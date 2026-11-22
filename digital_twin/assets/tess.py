import math

from utils.constants import CP_WATER_WH_PER_KG_K, RHO_WATER

U_TESS_W_PER_M2K = 0.175
HOOGTE_TESS_M = 10.0


class TESS:
    """Usable heat above min_temp, with a standing loss from the geometry, and limits.

    Call begin_step once before dispatch, then operate and step_finalization.
    Charge and discharge flows are positive, external thermal powers in kWth.

    THE STANDING LOSS FOLLOWS THE TEMPERATURE DIFFERENCE with the surroundings, not a
    percentage of the stored energy. The tank is assumed to be mixed, so one effective
    temperature describes it, and that temperature rises linearly with the stored energy:

        T_eff(t) = T_min + (E(t) / E_max) (T_max - T_min)
        Q_loss(t) = U_te A_te (T_eff(t) - T_amb(t))

    Two consequences worth knowing. An empty buffer still loses heat, because it is still
    at T_min and that is warmer than a Dutch winter; the system has to make that up. And
    a full buffer loses roughly three times as much as an empty one, instead of the
    unlimited ratio that a percentage of the contents gives.
    """

    def __init__(self, params):
        self.enabled = params.get("enabled", True)
        self.volume_m3 = float(params["volume_m3"])
        self.min_temp = float(params["min_temp_c"])
        self.max_temp = float(params["max_temp_c"])
        self.max_charge_kw = self._power_limit(params.get("capacity_in_kwth"))
        self.max_discharge_kw = self._power_limit(params.get("capacity_out_kwth"))
        starting_soc = float(params["starting_volume_%"]) / 100

        if not math.isfinite(self.volume_m3) or self.volume_m3 < 0:
            raise ValueError("TESS volume_m3 must be finite and nonnegative.")
        if not all(math.isfinite(t) for t in (self.min_temp, self.max_temp)) or self.max_temp <= self.min_temp:
            raise ValueError("TESS needs finite temperatures with max_temp_c > min_temp_c.")
        if not 0 <= starting_soc <= 1:
            raise ValueError("TESS starting_volume_% must be between 0 and 100.")

        self.delta_T = self.max_temp - self.min_temp
        self.capacity_kwh = self.volume_m3 * RHO_WATER * CP_WATER_WH_PER_KG_K * self.delta_T / 1000
        if not math.isfinite(self.capacity_kwh):
            raise ValueError("TESS computed capacity must be finite.")
        self.energy_kwh = self.capacity_kwh * starting_soc if self.enabled else 0.0
        self.soc = starting_soc if self.enabled and self.capacity_kwh > 0 else 0.0

        # Geometry and insulation: see the explanation at the top of this module.
        self.hoogte_m = HOOGTE_TESS_M
        self.straal_m = math.sqrt(self.volume_m3 / (math.pi * self.hoogte_m)) if self.volume_m3 > 0 else 0.0
        self.oppervlak_m2 = (2 * math.pi * self.straal_m * self.hoogte_m
                             + math.pi * self.straal_m ** 2)
        self.ua_kw_per_k = U_TESS_W_PER_M2K * self.oppervlak_m2 / 1000

    # ------------------------------------------------------------------
    # The standing loss, affine in the stored energy
    # ------------------------------------------------------------------

    def verlies_coefficient_kw_per_kwh(self) -> float:
        """The part of the loss that scales with the contents, in kW per kWh."""
        if not self.enabled or self.capacity_kwh <= 0:
            return 0.0
        return self.ua_kw_per_k * self.delta_T / self.capacity_kwh

    def verlies_basis_kw(self, t_amb_c: float) -> float:
        """The loss with an EMPTY buffer, so at T_min, in kW.
        
        The ambient temperature is clipped at T_min. That only intervenes in the few hours
        per year that it is warmer outside than the lower bound of the buffer; without that
        limit the loss would become negative and the MILP could harvest free heat from the
        outdoor air on a summer day, which this model does not describe.
        """
        if not self.enabled:
            return 0.0
        return self.ua_kw_per_k * (self.min_temp - min(float(t_amb_c), self.min_temp))

    def verlies_kw(self, t_amb_c: float) -> float:
        """The standing loss at the current contents, in kW."""
        return (self.verlies_basis_kw(t_amb_c)
                + self.verlies_coefficient_kw_per_kwh() * self.energy_kwh)

    def effectieve_temperatuur_c(self) -> float:
        """The assumed average temperature of the tank at the current contents."""
        return self.min_temp + self.soc * self.delta_T

    @staticmethod
    def _power_limit(value):
        if value is None:
            return math.inf
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError("TESS power limits must be finite and nonnegative, or None.")
        return value

    @staticmethod
    def _timestep(value):
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("TESS dt must be positive and finite (hours).")
        return value

    def _update_state(self, step_state):
        self.soc = self.energy_kwh / self.capacity_kwh if self.capacity_kwh > 0 else 0.0
        step_state["tess_energy_kwh"] = self.energy_kwh
        step_state["tess_soc"] = self.soc
        return step_state

    def begin_step(self, step_state):
        step_state["tess_energy_start_kwh"] = self.energy_kwh
        step_state["tess_charge_kw"] = 0.0
        step_state["tess_discharge_kw"] = 0.0
        step_state["tess_heat_loss_kwh"] = self.apply_losses(
            step_state["dt"], step_state["t_amb_c"],
            afkappen=step_state.get("verlies_afkappen", True))
        step_state["tess_temp_c"] = self.effectieve_temperatuur_c()
        return self._update_state(step_state)

    def operate(self, step_state, hp, bess, tess, grid, gb, pv):
        if step_state["remaining_heat_demand_kw"] > 0:
            self.discharge(step_state)
        return step_state

    def step_finalization(self, step_state):
        if step_state["heat_surplus_kw"] > 0:
            self.charge(step_state)
        return self._update_state(step_state)

    def charge(self, step_state):
        if not self.enabled:
            return self._update_state(step_state)
        dt = self._timestep(step_state["dt"])
        surplus_kw = max(0.0, step_state["heat_surplus_kw"])
        used_kw = step_state.get("tess_charge_kw", 0.0)
        remaining_power_kw = max(0.0, self.max_charge_kw - used_kw)
        space_kwh = max(0.0, self.capacity_kwh - self.energy_kwh)
        charge_kw = min(surplus_kw, remaining_power_kw, space_kwh / dt)

        self.energy_kwh = min(self.capacity_kwh, self.energy_kwh + charge_kw * dt)
        step_state["tess_charge_kw"] = used_kw + charge_kw
        step_state["heat_surplus_kw"] = surplus_kw - charge_kw
        return self._update_state(step_state)

    def discharge(self, step_state):
        if not self.enabled:
            return self._update_state(step_state)
        dt = self._timestep(step_state["dt"])
        demand_kw = max(0.0, step_state["remaining_heat_demand_kw"])
        # Mandatory generator output takes precedence over optional discharge.
        reserved_kw = step_state.get("hp_reserved_heat_kw", 0.0) + step_state.get("gb_reserved_heat_kw", 0.0)
        dispatchable_demand_kw = max(0.0, demand_kw - reserved_kw)
        used_kw = step_state.get("tess_discharge_kw", 0.0)
        remaining_power_kw = max(0.0, self.max_discharge_kw - used_kw)
        # max(0, ...): with the MILP the contents can briefly be negative between the
        # loss and the charging; nothing may be discharged out of that.
        discharge_kw = min(dispatchable_demand_kw, remaining_power_kw,
                           max(0.0, self.energy_kwh) / dt)

        # No max(0, ...) here: the discharging is already limited above to what is in
        # there, so it cannot pull the buffer below zero. If it did clip here, then
        # contents that are briefly negative after the standing loss would silently be
        # wiped away, and that is exactly the loss that the MILP did plan for. The
        # execution would then deviate from the plan.
        self.energy_kwh = self.energy_kwh - discharge_kw * dt
        step_state["tess_discharge_kw"] = used_kw + discharge_kw
        step_state["remaining_heat_demand_kw"] = demand_kw - discharge_kw
        return self._update_state(step_state)

    def apply_losses(self, timestep_hours, t_amb_c, afkappen=True):
        """Subtract the standing loss over this step and return it in kWh.
        
        The loss is determined at the contents at the BEGINNING of the step and kept
        constant over the step after that. That is the same approximation as the MILP
        makes, and over five minutes the contents change too little to notice the
        difference.
        """
        dt = self._timestep(timestep_hours)
        if not self.enabled:
            return 0.0
        energy_loss = self.verlies_kw(t_amb_c) * dt
        if afkappen:
            energy_loss = min(energy_loss, self.energy_kwh)
        self.energy_kwh = self.energy_kwh - energy_loss
        self.soc = self.energy_kwh / self.capacity_kwh if self.capacity_kwh > 0 else 0.0
        return energy_loss
