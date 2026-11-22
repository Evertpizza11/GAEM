import math


class BESS:
    """Stored energy in kWh; all charge/discharge powers are AC.

    Call begin_step once before dispatch. Conversion losses are applied only
    when charging/discharging; standing losses act on the opening energy.
    """

    def __init__(self, params):
        self.enabled = params.get("enabled", True)
        self.capacity_kwh = float(params["capacity_kwh"])
        self.max_charge_kw = float(params["capacity_in_kw"])
        self.max_discharge_kw = float(params["capacity_out_kw"])
        # Legacy 'efficiency' was unused; accept it as charge-only if supplied.
        self.charge_efficiency = float(params.get("charge_efficiency", params.get("efficiency", 1.0)))
        self.discharge_efficiency = float(params.get("discharge_efficiency", 1.0))
        self.loss_percent_per_hour = float(params.get("cont_energy_loss_%", 0.0)) / 100
        self.efficiency = self.charge_efficiency
        starting_soc = float(params["starting_soc_%"]) / 100

        for value in (self.capacity_kwh, self.max_charge_kw, self.max_discharge_kw):
            if not math.isfinite(value) or value < 0:
                raise ValueError("BESS capacity and power limits must be finite and nonnegative.")
        for efficiency in (self.charge_efficiency, self.discharge_efficiency):
            if not 0 < efficiency <= 1:
                raise ValueError("BESS efficiencies must be fractions in (0, 1].")
        if not 0 <= self.loss_percent_per_hour <= 1:
            raise ValueError("BESS cont_energy_loss_% must be between 0 and 100 per hour.")
        if not 0 <= starting_soc <= 1:
            raise ValueError("BESS starting_soc_% must be between 0 and 100.")

        self.energy_kwh = self.capacity_kwh * starting_soc if self.enabled else 0.0
        self.soc = starting_soc if self.enabled and self.capacity_kwh > 0 else 0.0

    @staticmethod
    def _timestep(value):
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("BESS dt must be positive and finite (hours).")
        return value

    def _update_state(self, step_state):
        self.soc = self.energy_kwh / self.capacity_kwh if self.capacity_kwh > 0 else 0.0
        step_state["bess_energy_kwh"] = self.energy_kwh
        step_state["bess_soc"] = self.soc
        return step_state

    def begin_step(self, step_state):
        step_state["bess_energy_start_kwh"] = self.energy_kwh
        step_state["bess_charge_kw"] = 0.0
        step_state["bess_discharge_kw"] = 0.0
        step_state["bess_charge_loss_kwh"] = 0.0
        step_state["bess_discharge_loss_kwh"] = 0.0
        step_state["bess_standing_loss_kwh"] = self.apply_losses(step_state["dt"])
        return self._update_state(step_state)

    def operate(self, step_state, hp, bess, tess, grid, gb, pv):
        if step_state["remaining_e_demand_kw"] > 0:
            self.discharge(step_state)
        return step_state

    def step_finalization(self, step_state):
        if step_state["e_surplus_kw"] > 0:
            self.charge(step_state)
        return self._update_state(step_state)

    def charge(self, step_state):
        if not self.enabled:
            return self._update_state(step_state)
        dt = self._timestep(step_state["dt"])
        surplus_kw = max(0.0, step_state["e_surplus_kw"])
        used_kw = step_state.get("bess_charge_kw", 0.0)
        remaining_power_kw = max(0.0, self.max_charge_kw - used_kw)
        space_kwh = max(0.0, self.capacity_kwh - self.energy_kwh)
        charge_kw = min(surplus_kw, remaining_power_kw, space_kwh / (self.charge_efficiency * dt))
        input_kwh = charge_kw * dt
        stored_kwh = input_kwh * self.charge_efficiency

        self.energy_kwh = min(self.capacity_kwh, self.energy_kwh + stored_kwh)
        step_state["bess_charge_kw"] = used_kw + charge_kw
        step_state["bess_charge_loss_kwh"] = step_state.get("bess_charge_loss_kwh", 0.0) + input_kwh - stored_kwh
        step_state["e_surplus_kw"] = surplus_kw - charge_kw
        return self._update_state(step_state)

    def discharge(self, step_state):
        if not self.enabled:
            return self._update_state(step_state)
        dt = self._timestep(step_state["dt"])
        demand_kw = max(0.0, step_state["remaining_e_demand_kw"])
        used_kw = step_state.get("bess_discharge_kw", 0.0)
        remaining_power_kw = max(0.0, self.max_discharge_kw - used_kw)
        discharge_kw = min(demand_kw, remaining_power_kw, self.energy_kwh * self.discharge_efficiency / dt)
        output_kwh = discharge_kw * dt
        removed_kwh = output_kwh / self.discharge_efficiency

        self.energy_kwh = max(0.0, self.energy_kwh - removed_kwh)
        step_state["bess_discharge_kw"] = used_kw + discharge_kw
        step_state["bess_discharge_loss_kwh"] = step_state.get("bess_discharge_loss_kwh", 0.0) + removed_kwh - output_kwh
        step_state["remaining_e_demand_kw"] = demand_kw - discharge_kw
        return self._update_state(step_state)

    def apply_losses(self, timestep_hours):
        dt = self._timestep(timestep_hours)
        if not self.enabled:
            return 0.0
        retained_energy = self.energy_kwh * (1 - self.loss_percent_per_hour) ** dt
        energy_loss = self.energy_kwh - retained_energy
        self.energy_kwh = retained_energy
        self.soc = self.energy_kwh / self.capacity_kwh if self.capacity_kwh > 0 else 0.0
        return energy_loss
