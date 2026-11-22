"""Try the existing hierarchy as a solver starting point, never as a fallback.

Only a complete plan passing ALL MILP constraints is supplied. The solver stays
free to change every action. Failure to construct this seed is not infeasibility.

- seed_from_hierarchy: the hierarchy plans the whole horizon (first solve of a run).
- seed_for_second_attempt: the same, for the second attempt in milp_solver.py. The
  hierarchy looks ahead no further than the horizon end, so in the last steps it can
  no longer start the boiler or heat pump: a new minimum run would not fit (the MILP
  forbids those starts as well). If the buffer runs empty there, every variant gets
  stuck. The plan is then kept up to shortly before that step and HiGHS plans the
  remaining steps on the same model, with the kept steps temporarily fixed ('+milp').
"""

from copy import deepcopy
import math

import pyomo.environ as pyo

from digital_twin.hierarchy_control.heat_controller import get_heat_hierarchy
from digital_twin.hierarchy_control.electricity_controller import get_electricity_hierarchy
from digital_twin.milp_control.plant import DT, TOL

VARIANTS = ('configured', 'hp_first', 'boiler_first')


def seed_from_hierarchy(m, plant, forecast, variant='configured'):
    """True: m holds a complete plan that passes check_solution. False: m holds no starting values."""
    if _hierarchy_plan(m, plant, forecast, variant) is None:
        return True
    _clear(m)
    return False


def seed_for_second_attempt(m, plant, forecast, config):
    """Complete, checked hierarchy plan in m; returns its source ('checked_...') or 'none'.

    Tries the variants as seed_from_hierarchy does. If every variant gets stuck, the plan
    of the one that got furthest is kept up to `window` steps before that point: twice
    the longest minimum run (HP or boiler), doubled once if that is not enough. HiGHS
    then plans the remaining steps under the same time limit and gap, with the kept
    steps fixed only during that step. Nothing of the plant or the problem changes.
    """
    from digital_twin.milp_control.milp_solver import check_solution
    furthest, best = -1, None
    for variant in VARIANTS:
        steps = _hierarchy_plan(m, plant, forecast, variant)
        if steps is None:
            return 'checked_' + variant
        if steps > furthest:
            furthest, best = steps, variant
    _hierarchy_plan(m, plant, forecast, best)  # Values of the variant that got furthest.
    window = 2 * max(plant.hp.required_steps(DT), plant.gb.required_steps(DT))
    for keep in (furthest - window, furthest - 2 * window):
        if keep < 1:
            break
        try:
            if _complete_with_milp(m, keep, config):
                check_solution(m)
                return f'checked_{best}+milp'
        except (ValueError, RuntimeError):
            pass
    _clear(m)
    return 'none'


def _hierarchy_plan(m, plant, forecast, variant):
    """Write the hierarchy plan into m, one step at a time.

    Returns None if the complete plan passes check_solution. Otherwise the number of
    leading steps that hold hierarchy values: the step where the hierarchy got stuck,
    every step if only the final check failed, or 0 if the variant does not apply.
    """
    trial = deepcopy(plant)
    hp, gb, th, b, grid, pv = trial.hp, trial.gb, trial.tess, trial.bess, trial.grid, trial.pv
    objects = {'heat_pump': hp, 'gas_boiler': gb, 'tess': th, 'bess': b, 'grid': grid, 'pv': pv}
    heat_order, e_order = tuple(get_heat_hierarchy()), tuple(get_electricity_hierarchy())
    if variant == 'hp_first':
        heat_order = ('heat_pump','tess','gas_boiler')
    elif variant == 'boiler_first':
        if hp.current_stage:
            return 0
        # Initial feasible proposal with HP off. This never alters installed
        # assets in the actual plant or the optimization problem.
        hp.enabled = False
        heat_order = ('gas_boiler','tess','heat_pump')
    heat = forecast.heat_demand_kw.to_list()
    electricity = forecast.e_demand_kw.to_list()
    solar = [trial.pv_available(v) for v in forecast.pv_kw]
    # Outdoor temperature per step, for the standing loss of the buffer (assumption D5).
    buiten = (forecast.t_buiten_c.to_list() if 't_buiten_c' in forecast.columns
              else [th.min_temp] * len(forecast))
    lookahead = hp.required_steps(DT) + gb.required_steps(DT)
    t = 0
    try:
        for t in range(len(forecast)):
            old_stage, old_on = hp.current_stage, gb.is_on
            s = dict(dt=DT, timestep=forecast.index[t],
                     remaining_heat_demand_kw=heat[t], remaining_e_demand_kw=electricity[t],
                     remaining_pv_available_kw=solar[t], remaining_grid_available_kw=trial.grid_limit,
                     remaining_gas_available_kw=gb.max_capacity_kwth if gb.enabled else 0.,
                     future_heat_demand_kw=heat[t:t+lookahead],
                     future_e_demand_kw=electricity[t:t+lookahead],
                     future_pv_available_kw=solar[t:t+lookahead],
                     hp_heat_kw=0., hp_el_kw=0., heat_surplus_kw=0., e_surplus_kw=0.,
                     hp_reserved_heat_kw=hp.mandatory_heat_kw(DT), gb_reserved_heat_kw=gb.mandatory_heat_kw(DT),
                     hp_required_steps=hp.required_steps(DT), heat_hierarchy=heat_order,
                     electricity_hierarchy=e_order, tess_capacity_kwh=th.capacity_kwh,
                     t_amb_c=buiten[t])
            th.begin_step(s)
            b.begin_step(s)
            for name in heat_order + e_order:
                objects[name].operate(s,hp,b,th,grid,gb,pv)
            for asset in (hp,gb,th,pv,grid,b):
                asset.step_finalization(s)
            if max(s['remaining_heat_demand_kw'],s['remaining_e_demand_kw'],s['heat_surplus_kw']) > TOL:
                raise ValueError('Hierarchy seed has unmet demand or heat dumping.')
            if s['bess_charge_kw'] > TOL and s['bess_discharge_kw'] > TOL:
                raise ValueError('Hierarchy seed cycles BESS within one step.')
            for k in m.K:
                if not m.hp_level[t,k].fixed:
                    m.hp_level[t,k].value = int(hp.current_stage >= k)
                if not m.hp_start[t,k].fixed:
                    m.hp_start[t,k].value = int(hp.current_stage >= k > old_stage)
            for component, value in (
                (m.gb_on,int(gb.is_on)), (m.gb_start,int(gb.is_on and not old_on)),
                (m.gb_heat,gb.current_heat_kw), (m.grid,trial.grid_limit-s['remaining_grid_available_kw']),
                (m.pv,solar[t]-s['e_surplus_kw']),
                (m.b_in,s['bess_charge_kw']), (m.b_out,s['bess_discharge_kw']),
                (m.b_charge_mode,int(s['bess_charge_kw']>TOL)),
                # TESS is lossless DURING flow conversion, so cancel same-step
                # pass-through while preserving both balances and final stock.
                (m.t_in,max(0.,s['tess_charge_kw']-s['tess_discharge_kw'])),
                (m.t_out,max(0.,s['tess_discharge_kw']-s['tess_charge_kw'])),
                (m.t_charge_mode,int(s['tess_charge_kw']>s['tess_discharge_kw'])),
            ):
                if not component[t].fixed:
                    component[t].set_value(value, skip_validation=True)
            m.b_energy[t+1].set_value(b.energy_kwh, skip_validation=True)
            m.t_energy[t+1].set_value(th.energy_kwh, skip_validation=True)
        # Deferred import avoids a module cycle.
        from digital_twin.milp_control.milp_solver import check_solution
        t = len(forecast)
        check_solution(m)
        return None
    except (ValueError, RuntimeError):
        return t


def _complete_with_milp(m, keep, config):
    """Fix the plan before step `keep`, let HiGHS plan the rest, unfix; True if it found a plan."""
    from pyomo.contrib.appsi.solvers import Highs
    from pyomo.contrib.appsi.base import TerminationCondition
    storage = ('b_energy', 't_energy')  # Follow from the fixed flows.
    kept = []
    for v in m.component_data_objects(pyo.Var):
        index = v.index()
        step = index[0] if isinstance(index, tuple) else index
        if not v.fixed and step < keep and v.parent_component().local_name not in storage:
            kept.append(v)
    if any(v.value is None for v in kept):
        return False
    solver = Highs()  # Separate instance: the controller's own solver is not touched.
    solver.config.load_solution = False
    solver.config.warmstart = False
    solver.config.time_limit = config.time_limit_seconds
    solver.config.mip_gap = config.mip_gap
    solver.highs_options.update(threads=config.solver_threads, random_seed=0,
                                mip_feasibility_tolerance=1e-7)
    for v in kept:
        v.fix()
    try:
        result = solver.solve(m)
        found = (result.termination_condition in (TerminationCondition.optimal,
                                                  TerminationCondition.maxTimeLimit,
                                                  TerminationCondition.maxIterations)
                 and result.best_feasible_objective is not None
                 and math.isfinite(result.best_feasible_objective))
        if found:
            solver.load_vars()
    finally:
        for v in kept:
            v.unfix()
    return found


def _clear(m):
    for v in m.component_data_objects(pyo.Var):
        if not v.fixed:
            v.set_value(None)
