"""Execute MILP actions through EAM assets and independently check every step.

No hierarchy is rerun here: that would overwrite the optimized decisions.
Changes are transactional: an invalid action never mutates the live plant.
"""

from copy import deepcopy
from dataclasses import dataclass, asdict
import math

import numpy as np

from digital_twin.assets.bess import BESS
from digital_twin.assets.tess import TESS
from digital_twin.assets.heat_pump import HP
from digital_twin.assets.gas_boiler import GB
from digital_twin.assets.grid import GRID
from digital_twin.assets.pv import PV
from utils.constants import TIMESTEP_5MIN_HOURS

DT = TIMESTEP_5MIN_HOURS
TOL = 1e-5

# Why there are two tolerances (assumption F7).
#
# TOL is absolute and is used where the quantity itself is small: whether a whole number
# really is whole, whether a power is not negative, whether charging and discharging do
# not happen at the same time. For that, 1e-5 is more than enough.
#
# For the energy balance and for comparing predicted with executed flows an absolute
# limit does not work. HiGHS solves with a feasibility tolerance of 1e-7 PER ROW, so
# relative to the coefficients of that row; after that _execute() recomputes everything
# in Python floats, with efficiencies and dt in it. On flows of thousands of kW and a
# buffer of thousands of kWh, 1e-5 absolute is a relative requirement of about 3e-9 -
# tighter than the solver can deliver. Running for a year is 105,120 steps, and then such
# a step comes along: in May 2025 a year run stranded on a heat residual of 1.5e-05 kW.
#
# That is why the margin scales with the size of the terms: 1e-3 kW plus 1e-7 of the sum
# of the absolute terms. At flows of 3000 kW that is about 1 mW, or 0.08 mWh over a step
# of five minutes. The measured data have three decimals (0.001 kW), so the margin stays
# below the resolution of the measurement itself.
#
# This weakens no physical limit at all. The grid stays at max_import_kw, the buffer at
# its capacity; only the numerical play around it goes from 1e-5 to about 1e-3 kW. A REAL
# balance error is orders larger and is still found, and the residuals are written away
# per step as heat_balance_residual_kw and e_balance_residual_kw, so that you can go
# through them in the result.
MARGE_ABS = 1e-3          # kW or kWh
MARGE_REL = 1e-7          # of the sum of the absolute terms


def marge(*termen: float) -> float:
    """The permitted difference for a comparison between 'termen'."""
    return MARGE_ABS + MARGE_REL * sum(abs(float(t)) for t in termen)


@dataclass(frozen=True)
class Dispatch:
    hp_stage: int
    gb_on: int
    gb_heat_kw: float
    grid_import_kw: float
    pv_used_kw: float
    bess_charge_kw: float
    bess_discharge_kw: float
    tess_charge_kw: float
    tess_discharge_kw: float
    bess_end_kwh: float
    tess_end_kwh: float


class EAMPlant:
    def __init__(self, assets, gas_price_eur_per_kwh=None):
        # The gas price belongs to the COMPANY and not to the model: it comes from
        # project.json of the chosen project, in euro per m3, and is converted there
        # to euro per kWh of fuel. See utils/projectinstellingen.gasinstellingen().
        if gas_price_eur_per_kwh is None:
            from utils.projectinstellingen import actieve_gasprijs_eur_per_kwh
            gas_price_eur_per_kwh = actieve_gasprijs_eur_per_kwh()
        if not math.isfinite(gas_price_eur_per_kwh) or gas_price_eur_per_kwh < 0:
            raise ValueError('Gas price must be finite and nonnegative.')
        self.gas_price_eur_per_kwh = float(gas_price_eur_per_kwh)
        self.asset_settings = deepcopy(assets)
        self.hp = HP(assets['heat_pump'])
        self.gb = GB(assets['gas_boiler'])
        self.tess = TESS(assets['tess'])
        self.bess = BESS(assets['bess'])
        self.grid = GRID(assets['grid'])
        self.pv = PV(assets['pv'])
        # No COP in this list: that belongs to a timestep and not to the machine, and
        # is therefore not fixed here yet (assumption D6). The stages are electrical.
        hp_values = [self.hp.min_on_time_minutes,
                     self.hp.min_capacity_kwe, self.hp.max_capacity_kwe,
                     *self.hp.electric_stages]
        if not np.isfinite(hp_values).all() or self.hp.control_stages != int(self.hp.control_stages):
            raise ValueError('HP settings must be finite; control_stages must be an integer.')
        if not 0 <= self.hp.min_capacity_kwe <= self.hp.max_capacity_kwe:
            raise ValueError('HP needs 0 <= electrical minimum <= electrical maximum.')

    @property
    def grid_limit(self):
        return self.grid.max_import_kw if self.grid.enabled else 0.0

    def pv_available(self, profile_kw):
        """The PV power the optimization may use at this step, in kW.

        The profile comes from the input: either the measured production, or the yield
        computed from weather data (inputs/zon.py). It is passed through unchanged, because
        there is no AC limit anywhere in the model - inverters are assumed to grow with the
        array (assumption H4). An inverter limit would belong
        here, as min(profile_kw, limit); it is the only place PV enters the MILP.
        """
        if not self.pv.enabled:
            return 0.0
        return float(profile_kw)

    def execute(self, action, row):
        trial = deepcopy(self)
        record = trial._execute(action, row)
        self.__dict__.update(trial.__dict__)
        return record

    def _execute(self, a, row):
        values = asdict(a)
        if any(not math.isfinite(v) or v < -TOL for v in values.values()):
            raise ValueError('Dispatch contains nonfinite or negative values.')
        heat_demand, e_demand = float(row.heat_demand_kw), float(row.e_demand_kw)
        if (not all(math.isfinite(v) for v in (heat_demand,e_demand,row.pv_kw,row.price_eur_per_kwh))
                or min(heat_demand,e_demand,row.pv_kw) < 0):
            raise ValueError('Dispatch input must be finite; demand/PV must be nonnegative.')
        pv_available = self.pv_available(row.pv_kw)
        if (a.grid_import_kw > self.grid_limit + marge(self.grid_limit)
                or a.pv_used_kw > pv_available + marge(pv_available)):
            raise ValueError('Dispatch exceeds available grid/PV power.')
        for name in ('bess', 'tess'):
            charge, discharge = values[name + '_charge_kw'], values[name + '_discharge_kw']
            if charge > TOL and discharge > TOL:
                raise ValueError(f'{name}: simultaneous charge and discharge is forbidden.')
        if a.tess_discharge_kw > heat_demand + marge(heat_demand):
            raise ValueError('TESS may discharge only to greenhouse heat demand.')

        # The COP of this step comes from the input, just like the PV and the heat demand; it
        # is computed beforehand from the weather (inputs/warmtepomp.py, assumption D6).
        if self.hp.enabled:
            try:
                self.hp.set_cop(row.cop)
            except AttributeError:
                raise ValueError(
                    "The input has no 'cop' column while this scenario has a heat pump. "
                    'The COP is computed from the weather by inputs/warmtepomp.py; a run '
                    'must call warmtepomp.toepassen() on its input.') from None
        self.hp.apply_dispatch(a.hp_stage, DT)
        hp_heat, hp_el = self.hp.current_heat_stage_kw, self.hp.current_electricity_stage_kw
        warmte_termen = (hp_heat, a.gb_heat_kw, a.tess_discharge_kw, heat_demand, a.tess_charge_kw)
        stroom_termen = (a.grid_import_kw, a.pv_used_kw, a.bess_discharge_kw, e_demand,
                         hp_el, a.bess_charge_kw)
        heat_residual = hp_heat + a.gb_heat_kw + a.tess_discharge_kw - heat_demand - a.tess_charge_kw
        e_residual = a.grid_import_kw + a.pv_used_kw + a.bess_discharge_kw - e_demand - hp_el - a.bess_charge_kw
        if (abs(heat_residual) > marge(*warmte_termen)
                or abs(e_residual) > marge(*stroom_termen)):
            raise ValueError(
                f'Energy balance failed: heat {heat_residual} kW (allowed '
                f'{marge(*warmte_termen):.3g}), electricity {e_residual} kW (allowed '
                f'{marge(*stroom_termen):.3g}).')

        # Existing storage methods enforce real capacity, powers and efficiencies.
        # t_amb_c: the outdoor temperature of this step, for the standing loss of the
        # buffer (assumption D5). Comes from the input, just like the COP and the PV.
        th = {'dt': DT, 'remaining_heat_demand_kw': max(0.0, a.tess_discharge_kw),
              'heat_surplus_kw': max(0.0, a.tess_charge_kw),
              't_amb_c': float(getattr(row, 't_buiten_c', self.tess.min_temp)),
              'verlies_afkappen': False}
        el = {'dt': DT, 'remaining_e_demand_kw': max(0.0, a.bess_discharge_kw),
              'e_surplus_kw': max(0.0, a.bess_charge_kw)}
        self.tess.begin_step(th)
        self.bess.begin_step(el)
        self.tess.discharge(th)
        self.tess.charge(th)
        self.bess.discharge(el)
        self.bess.charge(el)
        for name, state in (('tess', th), ('bess', el)):
            for flow in ('charge_kw', 'discharge_kw'):
                key = name + '_' + flow
                if abs(state[key] - values[key]) > marge(state[key], values[key]):
                    raise ValueError(f'{key}: setpoint could not be executed by the asset.')
            opgeslagen = getattr(self, name).energy_kwh
            if abs(opgeslagen - values[name + '_end_kwh']) > marge(opgeslagen,
                                                                   values[name + '_end_kwh']):
                raise ValueError(f'{name}: predicted and executed stored energy differ.')

        hp_to_demand = min(hp_heat, max(0.0, heat_demand - a.tess_discharge_kw))
        gb_state = {'dt': DT, 'remaining_heat_demand_kw': max(0.0, heat_demand - a.tess_discharge_kw - hp_to_demand)}
        self.gb.apply_dispatch(gb_state, a.gb_heat_kw, a.gb_on)
        record = {k: v for state in (th, el, gb_state) for k, v in state.items()}
        record.update({
            'dt_hours': DT, 'initial_heat_demand_kw': heat_demand,
            'initial_e_demand_kw': e_demand, 'remaining_heat_demand_kw': 0.0,
            'remaining_e_demand_kw': 0.0, 'heat_surplus_kw': 0.0,
            'e_surplus_kw': max(0.0, pv_available - a.pv_used_kw),
            'cop': self.hp.cop if self.hp.enabled else 0.0,
            'initial_hp_heat_kw': hp_heat, 'initial_hp_el_kw': hp_el,
            'remaining_hp_heat_kw': 0.0, 'remaining_hp_el_kw': 0.0,
            'hp_heat_to_demand_kw': hp_to_demand, 'hp_heat_to_tess_kw': hp_heat - hp_to_demand,
            'hp_stage': self.hp.current_stage, 'hp_running_steps': self.hp.running_steps,
            'hp_steps_since_upshift': self.hp.steps_since_upshift,
            'initial_gasboiler_available_kw': self.gb.max_capacity_kwth if self.gb.enabled else 0.0,
            'remaining_gasboiler_available_kw': self.gb.remaining_capacity_kw,
            'gb_start_blocked': 0, 'initial_grid_available_kw': self.grid_limit,
            'remaining_grid_available_kw': max(0.0, self.grid_limit - a.grid_import_kw),
            'grid_import_kw': max(0.0, a.grid_import_kw),
            'initial_pv_available_kw': pv_available, 'remaining_pv_available_kw': 0.0,
            'pv_used_kw': max(0.0, a.pv_used_kw),
            'pv_curtailed_kw': max(0.0, pv_available - a.pv_used_kw),
            'heat_balance_residual_kw': heat_residual, 'e_balance_residual_kw': e_residual,
            'price_eur_per_kwh': float(row.price_eur_per_kwh),
            'electricity_cost_eur': max(0.0, a.grid_import_kw) * DT * float(row.price_eur_per_kwh),
            'gas_price_eur_per_kwh': self.gas_price_eur_per_kwh,
            'gas_cost_eur': gb_state['gb_fuel_kwh'] * self.gas_price_eur_per_kwh,
        })
        record['total_cost_eur'] = record['electricity_cost_eur'] + record['gas_cost_eur']
        return record
