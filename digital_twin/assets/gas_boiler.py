"""Modulating thermal boiler, with a start-based minimum on-time."""

from copy import deepcopy
import math


class GB:
    TOLERANCE_KW = 1e-5

    def __init__(self, params):
        self.enabled = params.get("enabled", True)
        self.min_capacity_kwth = float(params.get("min_capacity_kwth", 0.0))
        self.max_capacity_kwth = float(params["max_capacity_kwth"])
        self.efficiency = float(params.get("efficiency", 1.0))
        self.min_on_time_minutes = float(params.get("min_on_time_minutes", 0.0))
        powers = (self.min_capacity_kwth, self.max_capacity_kwth)
        if any(not math.isfinite(x) or x < 0 for x in powers) or powers[0] > powers[1]:
            raise ValueError("GB needs finite powers with 0 <= min_capacity_kwth <= max_capacity_kwth.")
        if not 0 < self.efficiency <= 1:
            raise ValueError("GB efficiency must be a fraction in (0, 1].")
        if not math.isfinite(self.min_on_time_minutes) or self.min_on_time_minutes < 0:
            raise ValueError("GB min_on_time_minutes must be finite and nonnegative.")
        self.running_steps = 0  # Completed steps; modulation does NOT reset this.
        self.is_on = False
        self.current_heat_kw = 0.0
        self.remaining_capacity_kw = self.max_capacity_kwth if self.enabled else 0.0

    def required_steps(self, dt):
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("GB dt must be positive and finite (hours).")
        # Ignore binary/CSV round-off (e.g. 12.000000000000004 steps).
        return max(1, math.ceil(self.min_on_time_minutes / (dt * 60) - 1e-10))

    def remaining_on_steps(self, dt):
        return max(0, self.required_steps(dt) - self.running_steps) if self.enabled and self.is_on else 0

    def mandatory_heat_kw(self, dt):
        return self.min_capacity_kwth if self.remaining_on_steps(dt) > 0 else 0.0

    def apply_dispatch(self, step_state, heat_kw, on):
        """Execute an EMS setpoint, retaining the physical boiler timer."""
        if on not in (0, 1, False, True):
            raise ValueError("GB on must be a boolean or 0/1.")
        if not math.isfinite(heat_kw) or heat_kw < -self.TOLERANCE_KW:
            raise ValueError("GB heat must be finite and nonnegative.")
        if on and (not self.enabled or self.max_capacity_kwth == 0):
            raise ValueError("Cannot dispatch an absent/zero-capacity boiler.")
        if not on and (abs(heat_kw) > self.TOLERANCE_KW or self.remaining_on_steps(step_state['dt'])):
            raise ValueError("GB stop violates minimum on-time or has nonzero output.")
        if on and not self.min_capacity_kwth - self.TOLERANCE_KW <= heat_kw <= self.max_capacity_kwth + self.TOLERANCE_KW:
            raise ValueError("GB dispatch violates thermal min/max power.")
        if on:
            heat_kw = min(self.max_capacity_kwth, max(self.min_capacity_kwth, heat_kw))
        return self._apply_output(step_state, heat_kw if on else 0.0, bool(on))

    def suggest_heat_kw(self, demand_kw, dt, allow_optional=True):
        """Pure proposal, also used by the HP's joint feasibility check."""
        if not self.enabled or self.max_capacity_kwth == 0:
            return 0.0
        locked = self.remaining_on_steps(dt) > 0
        if locked or (allow_optional and demand_kw > self.TOLERANCE_KW):
            return max(self.min_capacity_kwth, min(max(0.0, demand_kw), self.max_capacity_kwth))
        return 0.0

    def _apply_output(self, step_state, heat_kw, on):
        """Commit ONE step; callers must first establish feasibility."""
        was_on = self.is_on
        self.is_on = bool(on and self.enabled)
        self.running_steps = (self.running_steps + 1 if was_on else 1) if self.is_on else 0
        self.current_heat_kw = float(heat_kw) if self.is_on else 0.0
        supplied_kw = min(max(0.0, step_state["remaining_heat_demand_kw"]), self.current_heat_kw)
        surplus_kw = self.current_heat_kw - supplied_kw
        fuel_kw = self.current_heat_kw / self.efficiency
        step_state["remaining_heat_demand_kw"] -= supplied_kw
        step_state["heat_surplus_kw"] = step_state.get("heat_surplus_kw", 0.0) + surplus_kw
        self.remaining_capacity_kw = (self.max_capacity_kwth if self.enabled else 0.0) - self.current_heat_kw
        step_state.update({
            "remaining_gas_available_kw": self.remaining_capacity_kw,
            "gb_heat_kw": self.current_heat_kw,
            "gb_heat_to_demand_kw": supplied_kw,
            "gb_heat_surplus_kw": surplus_kw,
            "gb_fuel_kw": fuel_kw,
            "gb_fuel_kwh": fuel_kw * step_state["dt"],
            "gb_conversion_loss_kwh": (fuel_kw - self.current_heat_kw) * step_state["dt"],
            "gb_efficiency": self.efficiency,
            "gb_is_on": int(self.is_on),
            "gb_started": int(self.is_on and not was_on),
            "gb_running_steps": self.running_steps,
            "gb_reserved_heat_kw": 0.0,
        })
        return step_state

    def operate(self, step_state, hp, bess, tess, grid, gb, pv):
        dt = float(step_state["dt"])
        locked = self.remaining_on_steps(dt) > 0
        heat_kw = self.suggest_heat_kw(step_state["remaining_heat_demand_kw"], dt)
        on = locked or heat_kw > self.TOLERANCE_KW
        step_state["gb_start_blocked"] = 0
        if on and not self._output_is_feasible(heat_kw, step_state, hp, tess):
            if locked:
                raise RuntimeError(
                    f"GB at {step_state.get('timestep', 'unknown timestep')}: minimum on-time "
                    "is active but mandatory heat cannot be absorbed. Check forecasts and asset state."
                )
            # No illegal sub-minimum production, shortened new run or heat dump.
            step_state["gb_start_blocked"] = int(not self.is_on)
            heat_kw, on = 0.0, False
        return self._apply_output(step_state, heat_kw, on)

    def step_finalization(self, step_state):
        # Surplus was already booked in operate; never book it a second time.
        self.remaining_capacity_kw = self.max_capacity_kwth if self.enabled else 0.0
        return step_state

    def _output_is_feasible(self, heat_kw, step_state, hp, tess):
        """Check new/remaining commitment against demand and real TESS limits.
        """
        dt = float(step_state["dt"])
        commitment = self.required_steps(dt) if not self.is_on else max(1, self.remaining_on_steps(dt))
        hp_steps = hp.remaining_on_steps(dt) if hp is not None else 0
        hp_kw = hp.current_heat_stage_kw if hp_steps else 0.0
        horizon = max(commitment, 1 + hp_steps)
        future = step_state.get("future_heat_demand_kw", [])
        if horizon > 1 and len(future) < horizon:
            return False
        trial_tess = deepcopy(tess)
        for offset in range(horizon):
            if offset == 0:
                trial = {
                    "dt": dt,
                    "remaining_heat_demand_kw": max(0.0, step_state["remaining_heat_demand_kw"] - heat_kw),
                    "heat_surplus_kw": max(0.0, heat_kw - step_state["remaining_heat_demand_kw"])
                    + step_state.get("hp_heat_kw", 0.0) + step_state.get("heat_surplus_kw", 0.0),
                    "tess_charge_kw": step_state.get("tess_charge_kw", 0.0),
                    "tess_discharge_kw": step_state.get("tess_discharge_kw", 0.0),
                    "t_amb_c": step_state.get("t_amb_c", trial_tess.min_temp),
                }
            else:
                demand = float(future[offset])
                compulsory_hp_kw = hp_kw if offset <= hp_steps else 0.0
                boiler_kw = (
                    max(self.min_capacity_kwth, min(max(0.0, demand - compulsory_hp_kw), self.max_capacity_kwth))
                    if offset < commitment else 0.0
                )
                produced_kw = compulsory_hp_kw + boiler_kw
                trial = {
                    "dt": dt,
                    "remaining_heat_demand_kw": max(0.0, demand - produced_kw),
                    "heat_surplus_kw": max(0.0, produced_kw - demand),
                    # The outdoor temperature of NOW, also for later steps in this
                    # look-ahead: the step_state carries no temperature forecast.
                    # Over a look-ahead of a few hours at most, the standing loss
                    # changes too little because of that to affect the choice (D5).
                    "t_amb_c": step_state.get("t_amb_c", trial_tess.min_temp),
                }
                trial_tess.begin_step(trial)
                trial_tess.discharge(trial)
            trial_tess.step_finalization(trial)
            if trial["heat_surplus_kw"] > self.TOLERANCE_KW:
                return False
        return True
