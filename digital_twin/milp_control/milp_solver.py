"""Pyomo/HiGHS formulation. Units: kW, kWh and EUR; dt is in hours.

HP levels use ordered binary thresholds: [1,1,0] means stage 2.
A newly activated threshold must stay active for a full minimum run.
Ordering consequently prevents any downshift during the latest upshift lock.

Solver starting points (warm starts) never change the plant state or the model;
they only tell HiGHS where to begin searching. Every executed action is a MILP
solution that passes check_solution() and the plant's physical checks.
- First solve of a run: a complete, checked plan from the hierarchy (warm_start.py).
- Later solves: the unexecuted tail of the previous plan, shifted. The last part
  of the new horizon has no old values, so HiGHS must complete that start itself.
- Second attempt: ONLY if that shifted start produced no feasible plan at all
  within the time limit, the solve is repeated once on a freshly built model with
  a complete, checked hierarchy start, as for a first solve. Where the hierarchy
  gets stuck near the horizon end, HiGHS completes its last steps (warm_start.py).
  Logged as warm_start_retry, with the start in warm_start_source.
"""

from dataclasses import dataclass
import math
from numbers import Integral
from time import perf_counter

import numpy as np
import pyomo.environ as pyo
from pyomo.contrib.appsi.solvers import Highs
from pyomo.contrib.appsi.base import TerminationCondition

from digital_twin.milp_control.plant import Dispatch, DT, TOL, marge


@dataclass(frozen=True)
class MILPConfig:
    horizon_hours: float = 48.0
    time_limit_seconds: float = 30.0
    mip_gap: float = 0.01
    solver_threads: int = 1
    terminal_policy: str = 'value'
    terminal_bess_eur_per_kwh: float | None = None
    terminal_tess_eur_per_kwh: float | None = None
    terminal_price_quantile: float = 0.25
    progress_every_steps: int = 1
    control_interval_steps: int = 1

    def __post_init__(self):
        for name in ('horizon_hours', 'time_limit_seconds'):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f'{name} must be positive and finite.')
        if abs(self.horizon_hours / DT - round(self.horizon_hours / DT)) > 1e-8:
            raise ValueError('horizon_hours must be a multiple of five minutes.')
        if (isinstance(self.control_interval_steps, bool)
                or not isinstance(self.control_interval_steps, Integral)
                or not 1 <= self.control_interval_steps <= self.horizon_steps):
            raise ValueError('control_interval_steps must be an integer in 1..horizon_steps.')
        if not 0 <= self.mip_gap < 1 or not 0 <= self.terminal_price_quantile <= 1:
            raise ValueError('Invalid mip_gap or terminal_price_quantile.')
        for name in ('solver_threads', 'progress_every_steps'):
            v = getattr(self, name)
            if int(v) != v or v < (1 if name == 'solver_threads' else 0):
                raise ValueError(f'Invalid {name}.')
        if self.terminal_policy not in ('none', 'value'):
            raise ValueError('terminal_policy must be none or value.')
        for v in (self.terminal_bess_eur_per_kwh, self.terminal_tess_eur_per_kwh):
            if v is not None and (not math.isfinite(v) or v < 0):
                raise ValueError('Terminal values must be nonnegative and finite, or None.')

    @property
    def horizon_steps(self):
        return round(self.horizon_hours / DT)


class MILPFailure(RuntimeError):
    """No certified feasible schedule; do not execute an invented fallback."""

    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


def terminal_values(plant, forecast, config, at_data_end):
    if config.terminal_policy == 'none' or at_data_end:
        return 0.0, 0.0
    price = float(np.quantile(np.maximum(forecast.price_eur_per_kwh.to_numpy(), 0.0),
                              config.terminal_price_quantile))
    bess_value = config.terminal_bess_eur_per_kwh
    if bess_value is None:
        bess_value = price * plant.bess.discharge_efficiency
    heat_options = []
    if plant.hp.enabled:
        # The COP differs per step, so for this valuation of the buffer we take the
        # average over the horizon. It is a planning value and not a cost item (C7);
        # an exact COP per hour would add nothing here.
        heat_options.append(price / float(np.mean(forecast.cop.to_numpy(dtype=float))))
    if plant.gb.enabled and plant.gb.max_capacity_kwth > 0:
        heat_options.append(plant.gas_price_eur_per_kwh / plant.gb.efficiency)
    tess_value = config.terminal_tess_eur_per_kwh
    if tess_value is None:
        tess_value = min(heat_options) if heat_options else 0.0
    return (float(bess_value) if plant.bess.enabled else 0.0,
            float(tess_value) if plant.tess.enabled else 0.0)


def build_model(plant, forecast, config, at_data_end=False):
    n = len(forecast)
    if n < 1 or n > config.horizon_steps:
        raise ValueError('Invalid MILP horizon length.')
    hp, gb, b, th = plant.hp, plant.gb, plant.bess, plant.tess
    if hp.remaining_on_steps(DT) > n or gb.remaining_on_steps(DT) > n:
        raise MILPFailure('Available horizon is shorter than an existing minimum-run commitment.')
    heat = forecast.heat_demand_kw.to_numpy(dtype=float)
    electricity = forecast.e_demand_kw.to_numpy(dtype=float)
    pv = np.array([plant.pv_available(v) for v in forecast.pv_kw])
    prices = forecast.price_eur_per_kwh.to_numpy(dtype=float)
    bcap = b.capacity_kwh if b.enabled else 0.0
    tcap = th.capacity_kwh if th.enabled else 0.0
    b_in = min(b.max_charge_kw, bcap / (DT * b.charge_efficiency)) if b.enabled else 0.0
    b_out = min(b.max_discharge_kw, bcap * b.discharge_efficiency / DT) if b.enabled else 0.0
    # The COP per timestep, computed beforehand from the weather data (inputs/warmtepomp.py).
    # It is a PARAMETER and not a variable, so the model stays linear: the heat of the
    # heat pump is a variable times a known number.
    cop = forecast.cop.to_numpy(dtype=float) if hp.enabled else np.zeros(n)
    if hp.enabled and (not np.isfinite(cop).all() or (cop <= 0).any()):
        raise MILPFailure('The COP of the heat pump must be positive and finite in every step.')
    gb_max = gb.max_capacity_kwth if gb.enabled else 0.0
    # The highest thermal capacity over this horizon: the top stage at the most favourable
    # COP. This only limits how fast the buffer can be filled.
    hp_max = float(hp.electric_stages[-1] * cop.max()) if hp.enabled else 0.0
    t_in = min(th.max_charge_kw, hp_max + gb_max, tcap / DT) if th.enabled else 0.0
    t_out = np.minimum(heat, min(th.max_discharge_kw, tcap / DT)) if th.enabled else np.zeros(n)
    rb = (1 - b.loss_percent_per_hour) ** DT
    # The standing loss of the buffer is affine in the contents (assumption D5): a fixed part
    # that depends only on the outdoor temperature, plus a part that scales with the contents.
    # That keeps the energy balance a linear equation.
    t_amb = forecast.t_buiten_c.to_numpy(dtype=float) if th.enabled else np.zeros(n)
    rt = 1 - DT * th.verlies_coefficient_kw_per_kwh()
    t_verlies_basis = np.array([th.verlies_basis_kw(temp) for temp in t_amb])
    # The stages are electrical; the heat follows per step from the COP (assumption D6).
    increments = np.diff(np.r_[0.0, hp.electric_stages])
    bv, tv = terminal_values(plant, forecast, config, at_data_end)

    m = pyo.ConcreteModel('EAM joint heat/electricity dispatch')
    m.T = pyo.RangeSet(0, n - 1)
    m.E = pyo.RangeSet(0, n)
    m.K = pyo.RangeSet(1, len(increments))
    m.hp_level = pyo.Var(m.T, m.K, domain=pyo.Binary)
    m.hp_start = pyo.Var(m.T, m.K, domain=pyo.Binary)
    m.gb_on = pyo.Var(m.T, domain=pyo.Binary)
    m.gb_start = pyo.Var(m.T, domain=pyo.Binary)
    m.b_charge_mode = pyo.Var(m.T, domain=pyo.Binary)
    m.t_charge_mode = pyo.Var(m.T, domain=pyo.Binary)
    m.hp_el = pyo.Expression(m.T, rule=lambda m,t: sum(increments[k-1] * m.hp_level[t,k] for k in m.K))
    m.hp_heat = pyo.Expression(m.T, rule=lambda m,t: m.hp_el[t] * cop[t])
    m.gb_heat = pyo.Var(m.T, bounds=(0, gb_max))
    m.grid = pyo.Var(m.T, bounds=(0, plant.grid_limit))
    m.pv = pyo.Var(m.T, bounds=lambda m,t: (0, pv[t]))
    m.b_in = pyo.Var(m.T, bounds=(0, b_in))
    m.b_out = pyo.Var(m.T, bounds=(0, b_out))
    m.t_in = pyo.Var(m.T, bounds=(0, t_in))
    m.t_out = pyo.Var(m.T, bounds=lambda m,t: (0, float(t_out[t])))
    m.b_energy = pyo.Var(m.E, bounds=(0, bcap))
    m.t_energy = pyo.Var(m.E, bounds=(0, tcap))
    m.b_energy[0].fix(b.energy_kwh)
    m.t_energy[0].fix(th.energy_kwh)
    m.c = pyo.ConstraintList()
    hu, gu = hp.required_steps(DT), gb.required_steps(DT)

    for t in range(n):
        for k in m.K:
            level, start = m.hp_level[t,k], m.hp_start[t,k]
            previous = m.hp_level[t-1,k] if t else int(hp.current_stage >= k)
            if k > 1:
                m.c.add(level <= m.hp_level[t,k-1])
            m.c.add(start >= level - previous)
            m.c.add(start <= level)
            m.c.add(start <= 1 - previous)
            m.c.add(sum(m.hp_start[j,k] for j in range(max(0,t-hu+1),t+1)) <= level)
            if t < hp.remaining_on_steps(DT) and k <= hp.current_stage:
                level.fix(1)
            # No minimum-runtime debt beyond the modeled horizon.
            if t + hu > n:
                start.fix(0)
            if not hp.enabled:
                level.fix(0)
                start.fix(0)
        m.c.add(m.hp_el[t] >= hp.min_capacity_kwe * m.hp_level[t,1])
        m.c.add(m.hp_el[t] <= hp.max_capacity_kwe * m.hp_level[t,1])
        previous = m.gb_on[t-1] if t else int(gb.is_on)
        m.c.add(m.gb_start[t] >= m.gb_on[t] - previous)
        m.c.add(m.gb_start[t] <= m.gb_on[t])
        m.c.add(m.gb_start[t] <= 1 - previous)
        m.c.add(sum(m.gb_start[j] for j in range(max(0,t-gu+1),t+1)) <= m.gb_on[t])
        if t < gb.remaining_on_steps(DT):
            m.gb_on[t].fix(1)
        if t + gu > n:
            m.gb_start[t].fix(0)
        if gb_max == 0:
            m.gb_on[t].fix(0)
            m.gb_start[t].fix(0)
        m.c.add(m.gb_heat[t] >= gb.min_capacity_kwth * m.gb_on[t])
        m.c.add(m.gb_heat[t] <= gb_max * m.gb_on[t])

        m.c.add(m.b_in[t] <= b_in * m.b_charge_mode[t])
        m.c.add(m.b_out[t] <= b_out * (1 - m.b_charge_mode[t]))
        m.c.add(m.t_in[t] <= t_in * m.t_charge_mode[t])
        m.c.add(m.t_out[t] <= float(t_out[t]) * (1 - m.t_charge_mode[t]))
        if b_in == 0 and b_out == 0:
            m.b_charge_mode[t].fix(0)
        if t_in == 0 and float(t_out[t]) == 0:
            m.t_charge_mode[t].fix(0)
        m.c.add(m.b_energy[t+1] == rb * m.b_energy[t]
                + DT * (b.charge_efficiency * m.b_in[t] - m.b_out[t] / b.discharge_efficiency))
        m.c.add(m.t_energy[t+1] == rt * m.t_energy[t]
                + DT * (m.t_in[t] - m.t_out[t] - t_verlies_basis[t]))
        m.c.add(m.hp_heat[t] + m.gb_heat[t] + m.t_out[t] == heat[t] + m.t_in[t])
        m.c.add(m.grid[t] + m.pv[t] + m.b_out[t] == electricity[t] + m.hp_el[t] + m.b_in[t])

    m.energy_cost = pyo.Expression(expr=sum(DT * (prices[t]*m.grid[t]
                 + plant.gas_price_eur_per_kwh*m.gb_heat[t]/gb.efficiency) for t in m.T))
    m.terminal_credit = pyo.Expression(expr=bv*m.b_energy[n] + tv*m.t_energy[n])
    m.objective = pyo.Objective(expr=m.energy_cost - m.terminal_credit, sense=pyo.minimize)
    m._terminal_values = (bv,tv)
    return m


def check_solution(model):
    """Check all variable domains, bounds and constraints, including the tail.

    Bounds and constraints are checked with the scaled margin from plant.py, not with the
    absolute TOL: HiGHS guarantees feasibility to 1e-7 PER ROW, so on a row with powers of
    thousands of kW its own answer may be further off than 1e-5 absolute (see the comment
    on the tolerances in plant.py, assumption F7). Integrality keeps TOL, because an
    integer is of order one and there is nothing to scale with.
    """
    for v in model.component_data_objects(pyo.Var):
        x = pyo.value(v, exception=False)
        if x is None or not math.isfinite(x):
            raise MILPFailure(f'Missing/nonfinite solver variable: {v.name}.')
        if v.is_integer() and abs(x-round(x)) > TOL:
            raise MILPFailure(f'Nonintegral solver variable: {v.name}.')
        if ((v.lb is not None and x < v.lb - marge(x, v.lb))
                or (v.ub is not None and x > v.ub + marge(x, v.ub))):
            raise MILPFailure(f'Solver variable outside bounds: {v.name}.')
    for c in model.component_data_objects(pyo.Constraint, active=True):
        x = pyo.value(c.body)
        if c.lower is not None:
            onder = pyo.value(c.lower)
            if x < onder - marge(x, onder):
                raise MILPFailure(f'Solver constraint violation: {c.name}.')
        if c.upper is not None:
            boven = pyo.value(c.upper)
            if x > boven + marge(x, boven):
                raise MILPFailure(f'Solver constraint violation: {c.name}.')


def _opslagstromen(val, modus, laden, ontladen):
    """(charge, discharge) of a storage, where the binary decides which one is zero.
    
    Charging and discharging at the same time is forbidden with a binary z and two
    coupling constraints: charge <= upper_in * z and discharge <= upper_out * (1 - z).
    HiGHS may return z next to 0 or 1 up to its tolerance, and check_solution() allows
    that too (TOL). Multiplied by an upper bound of hundreds of kW, a z of 1 - 9e-6
    then yields a permitted discharge of a few thousandths of a kW. And because
    discharging is free heat, the objective pushes that discharge right up against
    that limit instead of leaving it at zero. The plant tests simultaneity absolutely
    at 1e-5 kW and then refuses such a step, rightly: the tolerance on a DIMENSIONLESS
    binary and the one on a POWER are not the same quantity, and the big-M sits two to
    three orders in between (assumption F7).
    
    That is why we let the binary decide here. That never changes a real dispatch: it
    only removes a flow of which the model itself says that it should be zero. The
    energy balance and the final stock are tested with a relative margin, so a few
    milliwatts less falls well within that.
    """
    return (val(laden), 0.0) if round(pyo.value(modus)) == 1 else (0.0, val(ontladen))


def extract_dispatch(m, t):
    val = lambda x: max(0.0, float(pyo.value(x)))
    b_in, b_out = _opslagstromen(val, m.b_charge_mode[t], m.b_in[t], m.b_out[t])
    t_in, t_out = _opslagstromen(val, m.t_charge_mode[t], m.t_in[t], m.t_out[t])
    return Dispatch(
        hp_stage=sum(round(pyo.value(m.hp_level[t,k])) for k in m.K),
        gb_on=round(pyo.value(m.gb_on[t])), gb_heat_kw=val(m.gb_heat[t]),
        grid_import_kw=val(m.grid[t]), pv_used_kw=val(m.pv[t]),
        bess_charge_kw=b_in, bess_discharge_kw=b_out,
        tess_charge_kw=t_in, tess_discharge_kw=t_out,
        bess_end_kwh=val(m.b_energy[t+1]), tess_end_kwh=val(m.t_energy[t+1]),
    )


class MILPController:
    def __init__(self, config=None):
        self.config = config or MILPConfig()
        self.solver = Highs()
        if not self.solver.available():
            raise RuntimeError('HiGHS unavailable. Run: python -m pip install -r requirements_milp.txt')
        self.solver.config.load_solution = False
        self.solver.config.time_limit = self.config.time_limit_seconds
        self.solver.config.mip_gap = self.config.mip_gap
        self.solver.config.warmstart = True
        self.solver.highs_options.update(threads=self.config.solver_threads, random_seed=0,
                                         mip_feasibility_tolerance=1e-7)
        self.previous_solution = {}
        self.last_model = None

    def solve(self, plant, forecast, at_data_end=False, *, elapsed_steps=1):
        """Plan from the live plant; elapsed_steps counts actions used from the last plan."""
        if (isinstance(elapsed_steps, bool) or not isinstance(elapsed_steps, Integral)
                or elapsed_steps < 1):
            raise ValueError('elapsed_steps must be a positive integer.')
        started = perf_counter()
        m = build_model(plant, forecast, self.config, at_data_end)
        has_overlap = bool(self.previous_solution) and elapsed_steps < len(self.previous_solution['grid'])
        warm_start_source = 'previous_horizon' if has_overlap else 'none'
        if not has_overlap:
            from digital_twin.milp_control.warm_start import seed_from_hierarchy
            for variant in ('configured', 'hp_first', 'boiler_first'):
                if seed_from_hierarchy(m, plant, forecast, variant):
                    warm_start_source = 'checked_' + variant
                    break
        # Align all time-indexed variables with the number of executed actions.
        # The new tail has no old values; HiGHS still has to complete this start.
        if has_overlap:
            for component in m.component_objects(pyo.Var):
                old = self.previous_solution.get(component.local_name, {})
                for idx, var in component.items():
                    prior = (idx[0]+elapsed_steps, *idx[1:]) if isinstance(idx, tuple) else idx+elapsed_steps
                    if not var.fixed and prior in old:
                        var.set_value(old[prior], skip_validation=True)
        build_seconds = perf_counter()-started
        solver_started = perf_counter()
        result = self.solver.solve(m)
        solve_seconds = perf_counter()-solver_started
        termination = result.termination_condition
        bound, incumbent = result.best_objective_bound, result.best_feasible_objective
        usable = lambda value: value is not None and math.isfinite(value)
        limits = (TerminationCondition.maxTimeLimit, TerminationCondition.maxIterations)
        retry = {'warm_start_retry': False, 'first_attempt_termination': None,
                 'first_attempt_solve_seconds': None}
        retry_note = ''
        if has_overlap and termination in limits and not usable(incumbent):
            # The shifted previous plan gave no feasible plan within the limit. Try once
            # more like a first solve: fresh model, same live plant state, and a complete
            # checked starting plan. Nothing about the plant or the problem changes.
            from digital_twin.milp_control.warm_start import seed_for_second_attempt
            retry_started = perf_counter()
            fresh = build_model(plant, forecast, self.config, at_data_end)
            source = seed_for_second_attempt(fresh, plant, forecast, self.config)
            build_seconds += perf_counter()-retry_started
            if source == 'none':
                retry_note = ' No checked starting plan could be built for a second attempt.'
            else:
                print(f'MILP at {forecast.index[0]}: no feasible plan from the previous-plan start within '
                      f'{self.config.time_limit_seconds:g} s; second attempt from a checked '
                      f'starting plan ({source}).', flush=True)
                retry.update(warm_start_retry=True, first_attempt_termination=termination.name,
                             first_attempt_solve_seconds=solve_seconds)
                retry_note = ' A second attempt from a checked starting plan found none either.'
                solver_started = perf_counter()
                result = self.solver.solve(fresh)
                solve_seconds += perf_counter()-solver_started
                m, warm_start_source = fresh, source
                termination = result.termination_condition
                bound, incumbent = result.best_objective_bound, result.best_feasible_objective
        metadata = {
            'termination': termination.name, 'horizon_steps': len(forecast),
            'build_seconds': build_seconds, 'solve_seconds': solve_seconds,
            'warm_start_source': warm_start_source,
            'warm_start_shift_steps': int(elapsed_steps) if warm_start_source == 'previous_horizon' else 0,
            'objective_bound_eur': float(bound) if usable(bound) else None,
            'objective_eur': float(incumbent) if usable(incumbent) else None,
            'terminal_bess_eur_per_kwh': m._terminal_values[0],
            'terminal_tess_eur_per_kwh': m._terminal_values[1],
            **retry,
        }
        acceptable = (TerminationCondition.optimal, *limits)
        if termination not in acceptable or not usable(incumbent):
            raise MILPFailure(
                f'MILP at {forecast.index[0]}: {termination.name}; no usable feasible plan.{retry_note} '
                'No action executed. Check demand, enabled assets, capacities, starting inventory, '
                'minimum run times and available data; a time limit can also expire before a plan is found.', metadata)
        self.solver.load_vars()
        check_solution(m)
        self.previous_solution = {
            c.local_name: {idx: pyo.value(v) for idx,v in c.items()}
            for c in m.component_objects(pyo.Var)
        }
        self.last_model = m
        metadata.update({
            'relative_gap': (max(0.0, float(incumbent-bound))/max(abs(incumbent), 1e-10)
                             if bound is not None and math.isfinite(bound) else None),
            'planned_energy_cost_eur': pyo.value(m.energy_cost),
            'terminal_credit_eur': pyo.value(m.terminal_credit),
            'controller_seconds': perf_counter()-started,
        })
        return extract_dispatch(m, 0), metadata
