class PV:
    """The solar panels. The available power comes as a time series from the input
    (column pv_kw): measured, or computed from weather data by inputs/zon.py. This asset
    only distributes it; the peak power follows from the panels and is not filled in
    separately anywhere.
    """

    def __init__(self, params):
        self.enabled = params.get("enabled", True)

    def operate(self, step_state, hp, bess, tess, grid, gb, pv):
        if not self.enabled:
            step_state["remaining_pv_available_kw"] = 0.0
            return step_state
        remaining_e_demand_kw = step_state["remaining_e_demand_kw"]
        remaining_pv_available_kw = step_state["remaining_pv_available_kw"]

        pv_supplied_kw = min(remaining_e_demand_kw, remaining_pv_available_kw)

        step_state["remaining_e_demand_kw"] -= pv_supplied_kw
        step_state["remaining_pv_available_kw"] -= pv_supplied_kw

        return step_state 

    def step_finalization(self, step_state):
        if not self.enabled:
            step_state["remaining_pv_available_kw"] = 0.0
            return step_state
        if step_state["remaining_pv_available_kw"] > 0:
            pv_surplus_kw = step_state["remaining_pv_available_kw"]
            step_state["e_surplus_kw"] += pv_surplus_kw
            step_state["remaining_pv_available_kw"] -= pv_surplus_kw

        return step_state

