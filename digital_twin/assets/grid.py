import math

class GRID: 
    def __init__(self, params):
        self.enabled = params.get("enabled", True)
        self.max_import_kw = float(params["max_import_kw"])
        if not math.isfinite(self.max_import_kw) or self.max_import_kw < 0:
            raise ValueError("GRID max_import_kw must be finite and nonnegative.")
        self.remaining_import_kw = self.max_import_kw if self.enabled else 0.0

    def operate(self, step_state, hp, bess, tess, grid, gb, pv):
        if not self.enabled:
            self.remaining_import_kw = 0.0
            step_state["remaining_grid_available_kw"] = 0.0
            return step_state
        remaining_e_demand_kw = step_state["remaining_e_demand_kw"]
        remaining_grid_available_kw = step_state["remaining_grid_available_kw"]

        e_supplied_kw = min(remaining_e_demand_kw, remaining_grid_available_kw)

        step_state["remaining_e_demand_kw"] -= e_supplied_kw
        self.remaining_import_kw -= e_supplied_kw
        step_state["remaining_grid_available_kw"] = self.remaining_import_kw

        return step_state

    def step_finalization(self, step_state):
        self.remaining_import_kw = self.max_import_kw if self.enabled else 0.0

        return step_state
