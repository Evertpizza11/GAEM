from copy import deepcopy
import math

import numpy as np

from config.assets_config import ASSETS
from digital_twin.assets.pv import PV


class HP:
    """
    THE STAGES ARE ELECTRICAL (kWe). The compressor is the fixed part of a heat pump,
    so the stages are its electrical input; the heat that comes out is that input times
    the COP of this timestep, and therefore varies with the weather.
    """

    HEAT_DEMAND_TOLERANCE_KW = 1e-6
    BALANCE_TOLERANCE_KW = 1e-5

    def __init__(self, params):
        self.enabled = params.get("enabled", True)
        self.min_capacity_kwe = params["min_capacity_kwe"]
        self.max_capacity_kwe = params["max_capacity_kwe"]
        self.min_on_time_minutes = params.get("min_on_time_minutes", 0)
        self.control_stages = params["control_stages"]

        self.running_steps = 0
        self.steps_since_upshift = 0
        self.current_stage = 0  # 0 = OFF; 1..control_stages = active power level
        self.current_heat_stage_kw = 0.0
        self.current_electricity_stage_kw = 0.0
        self._cop = None        # set per timestep from the data; see set_cop()

        if self.control_stages < 1:
            raise ValueError("HP control_stages must be at least 1.")
        if self.min_on_time_minutes < 0:
            raise ValueError("HP min_on_time_minutes cannot be negative.")

        self.electric_stages = np.linspace(
            self.min_capacity_kwe, self.max_capacity_kwe, self.control_stages
        )
        if self.enabled and ((self.electric_stages <= 0).any()
                             or (self.control_stages > 1
                                 and (np.diff(self.electric_stages) <= 0).any())):
            raise ValueError("HP electrical stages must be positive and strictly increasing.")

    # ------------------------------------------------------------------
    # The COP of this timestep
    # ------------------------------------------------------------------

    @property
    def cop(self):
        """The COP of the current timestep.
        """
        if self._cop is None:
            raise ValueError(
                "HP has no COP for this timestep. The COP is computed from the weather "
                "(inputs/warmtepomp.py) and must be passed with set_cop() before the heat "
                "pump is used. Is the 'cop' column missing from the input?")
        return self._cop

    def set_cop(self, cop):
        """Setting the COP of this timestep. Called per step by the control."""
        cop = float(cop)
        if not math.isfinite(cop) or cop <= 0:
            raise ValueError(f"HP COP must be positive and finite, got {cop}.")
        self._cop = cop

    @property
    def thermal_stages(self):
        """The heat per stage at the COP of this moment: kWe * COP(t)."""
        return self.electric_stages * self.cop

    @property
    def max_capacity_kwth(self):
        """The highest thermal capacity at the COP of this moment."""
        return float(self.electric_stages[-1] * self.cop)

    @property
    def min_capacity_kwth(self):
        """The lowest thermal capacity at the COP of this moment."""
        return float(self.electric_stages[0] * self.cop)

    def required_steps(self, dt):
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("HP dt must be positive and finite (hours).")
        return max(1, math.ceil(self.min_on_time_minutes / (dt * 60) - 1e-10))

    def remaining_on_steps(self, dt):
        required = self.required_steps(dt)
        return max(0, required - self.steps_since_upshift) if self.enabled and self.current_stage else 0

    def mandatory_heat_kw(self, dt):
        return self.current_heat_stage_kw if self.remaining_on_steps(dt) else 0.0

    def apply_dispatch(self, stage, dt):
        """Execute a checked EMS stage; system feasibility is checked by the plant.

        Unlike operate(), this method permits deliberate production for storage.
        It enforces the same start/upshift timer, without rerunning the hierarchy.
        """
        if isinstance(stage, bool) or int(stage) != stage or not 0 <= stage <= self.control_stages:
            raise ValueError("HP dispatch stage must be an integer in 0..control_stages.")
        stage = int(stage)
        if stage and not self.enabled:
            raise ValueError("Cannot dispatch a disabled HP.")
        if stage < self.current_stage and self.remaining_on_steps(dt):
            raise ValueError("HP downshift/stop violates minimum run after start/upshift.")
        old_stage = self.current_stage
        # The stage IS the electrical power; the heat follows from the COP of this step.
        # The old check on the electrical limits has become superfluous because of that: a
        # stage lies by definition between min_capacity_kwe and max_capacity_kwe.
        electricity = float(self.electric_stages[stage - 1]) if stage else 0.0
        heat = electricity * self.cop if stage else 0.0
        self.current_stage = stage
        self.current_heat_stage_kw = heat
        self.current_electricity_stage_kw = electricity
        self.running_steps = (self.running_steps + 1 if old_stage else 1) if stage else 0
        self.steps_since_upshift = (1 if stage > old_stage else self.steps_since_upshift + 1) if stage else 0

    def operate(self, step_state, hp, bess, tess, grid, gas_boiler, pv):
        if not self.enabled:
            self.running_steps = self.steps_since_upshift = self.current_stage = 0
            self.current_heat_stage_kw = self.current_electricity_stage_kw = 0.0
            step_state.update(hp_running_steps=0, hp_steps_since_upshift=0, hp_stage=0)
            return self._deliver_heat(step_state, 0.0)
        required_steps = max(1, int(step_state["hp_required_steps"]))
        old_heat_kw = self.current_heat_stage_kw
        must_keep_running = (
            old_heat_kw > 0 and self.steps_since_upshift < required_steps
        )

        if abs(step_state["remaining_heat_demand_kw"]) <= self.HEAT_DEMAND_TOLERANCE_KW:
            step_state["remaining_heat_demand_kw"] = 0.0

        has_heat_demand = (
            step_state["remaining_heat_demand_kw"] - step_state.get("gb_reserved_heat_kw", 0.0)
            > self.HEAT_DEMAND_TOLERANCE_KW
        )

        selected_heat_kw = 0.0
        selected_el_kw = 0.0

        if has_heat_demand or must_keep_running:
            # A higher stage may start even before the previous minimum run ends.
            # It needs a NEW complete minimum-run horizon, measured from now.
            if has_heat_demand and old_heat_kw < self.thermal_stages[-1]:
                _, selected_heat_kw, selected_el_kw = self.get_sustainable_stage(
                    step_state, bess, tess, grid, higher_than_kw=old_heat_kw,
                    gas_boiler=gas_boiler, pv=pv
                )

            if selected_heat_kw == 0 and old_heat_kw > 0:
                if must_keep_running:
                    # A lock is not permission to invent electricity or dump heat.
                    if not self._stage_is_feasible(
                        old_heat_kw, self.remaining_on_steps(step_state["dt"]),
                        step_state, bess, tess, grid, gas_boiler=gas_boiler, pv=pv,
                        allow_boiler_starts=False
                    ):
                        timestamp = step_state.get("timestep", "unknown timestep")
                        raise RuntimeError(
                            f"HP at {timestamp}: stage {self.current_stage} is still "
                            f"locked ({self.steps_since_upshift}/{required_steps} steps), "
                            "but electricity or heat absorption is insufficient. "
                            "Check the input, forecasts and asset state."
                        )
                    selected_heat_kw = old_heat_kw
                    selected_el_kw = old_heat_kw / self.cop
                else:
                    # Keep the current stage if feasible; otherwise reduce to the
                    # highest feasible lower stage. No compressor starts on a downshift.
                    _, selected_heat_kw, selected_el_kw = self.check_for_1_step(
                        step_state, bess, tess, grid, gas_boiler=gas_boiler, pv=pv
                    )

        # One central update: feasibility checks NEVER increment real counters.
        if selected_heat_kw == 0:
            self.running_steps = 0
            self.steps_since_upshift = 0
            self.current_stage = 0
        else:
            self.running_steps = self.running_steps + 1 if old_heat_kw > 0 else 1
            if selected_heat_kw > old_heat_kw:
                self.steps_since_upshift = 1
            else:
                self.steps_since_upshift += 1
            self.current_stage = int(np.searchsorted(self.thermal_stages, selected_heat_kw)) + 1

        self.current_heat_stage_kw = selected_heat_kw
        self.current_electricity_stage_kw = selected_el_kw
        step_state["hp_running_steps"] = self.running_steps
        step_state["hp_steps_since_upshift"] = self.steps_since_upshift
        step_state["hp_stage"] = self.current_stage

        return self._deliver_heat(step_state, selected_heat_kw)

    def _deliver_heat(self, step_state, heat_kw):
        # Leave demand available for an already committed boiler minimum.
        available_demand = max(0.0, step_state["remaining_heat_demand_kw"] - step_state.get("gb_reserved_heat_kw", 0.0))
        heat_supplied_kw = min(available_demand, heat_kw)
        step_state["remaining_heat_demand_kw"] -= heat_supplied_kw
        # No heat means no electricity, whatever the COP. That looks superfluous
        # (0 / cop is zero after all), but self.cop is a property that raises an error as
        # long as set_cop() has not been called, and the control only calls it when the
        # heat pump is on. Without this check every scenario WITHOUT a heat pump got stuck
        # on the zero heat that operate() passes on here, including the scenario 'basis'.
        # Neutral for the result of a scenario in which the heat pump does run.
        step_state["remaining_e_demand_kw"] += (heat_kw / self.cop) if heat_kw else 0.0
        step_state["hp_heat_kw"] = heat_kw - heat_supplied_kw
        step_state["hp_heat_to_demand_kw"] = heat_supplied_kw
        step_state["hp_reserved_heat_kw"] = 0.0
        step_state["hp_el_kw"] = 0.0
        return step_state

    def step_finalization(self, step_state):
        if step_state["hp_heat_kw"] > 0:
            step_state["heat_surplus_kw"] += step_state["hp_heat_kw"]
            step_state["hp_heat_kw"] = 0.0
        return step_state

    def get_sustainable_stage(self, step_state, bess, tess, grid, higher_than_kw=0.0, *, gas_boiler=None, pv=None):
        """Return (1, heat_kw, electric_kw) for a feasible start/upshift, else zeros.

        The first value is an advisory first run step, retained for compatibility.
        It does not modify running_steps or steps_since_upshift.
        """
        required_steps = max(1, int(step_state["hp_required_steps"]))
        for heat_kw in reversed(self.thermal_stages):
            if heat_kw <= higher_than_kw:
                continue
            if self._stage_is_feasible(
                float(heat_kw), required_steps, step_state, bess, tess, grid,
                gas_boiler=gas_boiler, pv=pv
            ):
                return 1, float(heat_kw), float(heat_kw) / self.cop
        return 0, 0.0, 0.0

    def check_for_1_step(self, step_state, bess, tess, grid, *, gas_boiler=None, pv=None):
        """Highest feasible current/lower stage; caller must first release the lock.

        Pure check: returns a suggested total run counter, but changes no state.
        A lower stage retains the elapsed minimum-run time.
        """
        for heat_kw in reversed(self.thermal_stages):
            if heat_kw > self.current_heat_stage_kw:
                continue
            if self._stage_is_feasible(float(heat_kw), 1, step_state, bess, tess, grid, gas_boiler=gas_boiler, pv=pv):
                return self.running_steps + 1, float(heat_kw), float(heat_kw) / self.cop
        return 0, 0.0, 0.0

    def _stage_is_feasible(self, heat_kw, steps, step_state, bess, tess, grid, *, gas_boiler=None, pv=None, allow_boiler_starts=True):
        """Preview a constant stage using COP and COPIES of the EAM storage assets.
        """
        if not self.enabled:
            return False
        dt = float(step_state["dt"])
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("HP timestep duration must be positive and finite.")

        future_keys = (
            "future_heat_demand_kw", "future_e_demand_kw", "future_pv_available_kw"
        )
        if steps > 1 and any(len(step_state.get(key, [])) < steps for key in future_keys):
            return False  # No new starts/upshifts without the full required horizon.

        order = tuple(step_state.get("electricity_hierarchy", ("pv", "bess", "grid")))
        if len(order) != len(set(order)) or any(name not in ("pv", "bess", "grid") for name in order):
            raise ValueError("HP preview requires a unique electricity hierarchy using pv, bess and/or grid.")

        heat_order = tuple(step_state.get("heat_hierarchy", ("heat_pump", "tess", "gas_boiler")))
        if heat_order not in (
            ("heat_pump", "tess", "gas_boiler"),
            ("tess", "heat_pump", "gas_boiler"),
        ):
            raise ValueError("HP preview supports HP/TESS or TESS/HP, with gas_boiler last.")
        tess_first = heat_order[0] == "tess"

        trial_bess, trial_tess, trial_grid = deepcopy((bess, tess, grid))
        trial_pv = deepcopy(pv) if pv is not None else PV(ASSETS["pv"])
        trial_gb = deepcopy(gas_boiler)
        electricity_assets = {"pv": trial_pv, "bess": trial_bess, "grid": trial_grid}
        horizon = max(steps, trial_gb.remaining_on_steps(dt) if trial_gb is not None else 0)
        offset = 0
        while offset < horizon:
            if offset > 0 and any(len(step_state.get(key, [])) <= offset for key in future_keys):
                return False
            planned_heat_kw = heat_kw if offset < steps else 0.0
            if offset == 0:
                heat_demand = float(step_state["remaining_heat_demand_kw"])
                electricity_demand = float(step_state["remaining_e_demand_kw"])
                pv_available = float(step_state["remaining_pv_available_kw"])
            else:
                heat_demand = float(step_state["future_heat_demand_kw"][offset])
                electricity_demand = float(step_state["future_e_demand_kw"][offset])
                pv_available = float(step_state["future_pv_available_kw"][offset])

            values = (heat_demand, electricity_demand, pv_available)
            if any(not math.isfinite(value) or value < -self.BALANCE_TOLERANCE_KW for value in values):
                raise ValueError("HP preview needs finite, nonnegative demand and PV data.")
            heat_demand = max(0.0, heat_demand)
            electricity_demand = max(0.0, electricity_demand)
            pv_available = max(0.0, pv_available)

            trial = {
                "dt": dt,
                "remaining_heat_demand_kw": heat_demand,
                "heat_surplus_kw": float(step_state.get("heat_surplus_kw", 0.0)) if offset == 0 else 0.0,
                "remaining_e_demand_kw": electricity_demand + planned_heat_kw / self.cop,
                "remaining_pv_available_kw": pv_available if trial_pv.enabled else 0.0,
                "remaining_grid_available_kw": trial_grid.remaining_import_kw,
                "e_surplus_kw": 0.0,
                "hp_reserved_heat_kw": planned_heat_kw,
                "gb_reserved_heat_kw": trial_gb.mandatory_heat_kw(dt) if trial_gb is not None else 0.0,
                # The outdoor temperature of NOW, also for later steps in this look-ahead:
                # the step_state carries no temperature forecast. Over a few hours the standing
                # loss of the buffer changes too little because of that (D5).
                "t_amb_c": step_state.get("t_amb_c", trial_tess.min_temp),
            }

            if offset == 0:
                # Respect power already used earlier in the real current step.
                for key in ("tess_charge_kw", "tess_discharge_kw", "bess_charge_kw", "bess_discharge_kw"):
                    trial[key] = step_state.get(key, 0.0)
            else:
                trial_tess.begin_step(trial)
                trial_bess.begin_step(trial)
                if tess_first:
                    trial_tess.discharge(trial)

            available_demand = max(0.0, trial["remaining_heat_demand_kw"] - trial["gb_reserved_heat_kw"])
            heat_supplied_kw = min(available_demand, planned_heat_kw)
            trial["remaining_heat_demand_kw"] -= heat_supplied_kw
            trial["heat_surplus_kw"] += planned_heat_kw - heat_supplied_kw
            trial["hp_reserved_heat_kw"] = 0.0
            if not tess_first:
                trial_tess.discharge(trial)
            if trial_gb is not None:
                locked = trial_gb.remaining_on_steps(dt) > 0
                boiler_kw = trial_gb.suggest_heat_kw(
                    trial["remaining_heat_demand_kw"], dt,
                    allow_optional=allow_boiler_starts and offset < steps
                )
                boiler_on = locked or boiler_kw > trial_gb.TOLERANCE_KW
                if boiler_on and not trial_gb.is_on:
                    needed = offset + trial_gb.required_steps(dt)
                    if needed > 1 and any(len(step_state.get(key, [])) < needed for key in future_keys):
                        return False
                trial_gb._apply_output(trial, boiler_kw, boiler_on)
                horizon = max(horizon, offset + 1 + trial_gb.remaining_on_steps(dt))
            trial_tess.step_finalization(trial)
            if trial["heat_surplus_kw"] > self.BALANCE_TOLERANCE_KW:
                return False

            for name in order:
                electricity_assets[name].operate(
                    trial, None, trial_bess, trial_tess, trial_grid, None, trial_pv
                )
            if trial["remaining_e_demand_kw"] > self.BALANCE_TOLERANCE_KW:
                return False

            # Match simulation.py: PV surplus is stored in BESS; grid resets.
            for asset in (trial_pv, trial_grid, trial_bess):
                asset.step_finalization(trial)
            offset += 1

        return True
