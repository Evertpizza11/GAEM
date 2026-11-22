"""Receding horizon: forecast -> solve -> execute a block of actions -> carry state."""

from dataclasses import asdict
import json
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from config.assets_config import ASSETS
from config.ems_config import MILP_SETTINGS
from digital_twin.milp_control.milp_solver import MILPConfig, MILPController, MILPFailure, extract_dispatch
from digital_twin.milp_control.plant import EAMPlant, DT


def validate_input(df, require_prices=True):
    if not isinstance(df.index, pd.DatetimeIndex) or df.empty:
        raise ValueError('MILP input needs a nonempty DatetimeIndex.')
    if df.index.hasnans or not df.index.is_unique or not df.index.is_monotonic_increasing:
        raise ValueError('MILP timestamps must be valid, unique and increasing.')
    if not df.index.to_series().diff().dropna().eq(pd.Timedelta(minutes=5)).all():
        raise ValueError('MILP input must contain consecutive five-minute timesteps.')
    columns = ['heat_demand_kw', 'e_demand_kw', 'pv_kw']
    if require_prices:
        columns.append('price_eur_per_kwh')
    missing = set(columns) - set(df.columns)
    if missing:
        raise ValueError(f'Missing MILP columns: {sorted(missing)}.')
    v = df[columns].to_numpy(dtype=float)
    if not np.isfinite(v).all() or (v[:,:3] < 0).any():
        raise ValueError('Input must be finite; demand/PV must be nonnegative. Negative prices are allowed.')


def attach_prices(df, price_path):
    """Exact timestamp matching: never silently fill a missing day-ahead price."""
    validate_input(df, require_prices=False)
    prices = pd.read_csv(price_path, sep=';')
    prices['Date'] = pd.to_datetime(prices['Date'], format='%d-%m-%Y %H:%M', errors='raise')
    if prices.Date.duplicated().any() or prices.Date.isna().any():
        raise ValueError('Price timestamps must be unique and valid.')
    series = pd.to_numeric(prices.set_index('Date')['Day-ahead (EUR/MWh)'], errors='raise') / 1000.0
    out = df.copy()
    out['price_eur_per_kwh'] = series.reindex(out.index)
    if out.price_eur_per_kwh.isna().any():
        missing = out.index[out.price_eur_per_kwh.isna()]
        raise ValueError(f'Missing exact electricity prices: {len(missing)} timesteps, first {missing[0]}.')
    validate_input(out)
    return out


def _profiles(records, index):
    full = pd.DataFrame(records, index=pd.DatetimeIndex(index, name='Date'))
    common = ['dt_hours']
    heat_prefixes = ('tess_', 'gb_', 'hp_', 'initial_hp_heat', 'remaining_hp_heat',
                     'initial_heat', 'remaining_heat', 'initial_gasboiler', 'remaining_gasboiler',
                     'heat_', 'gas_')
    e_prefixes = ('bess_', 'pv_', 'grid_', 'initial_e_', 'remaining_e_', 'initial_pv',
                  'remaining_pv', 'initial_grid', 'remaining_grid', 'initial_hp_el', 'remaining_hp_el',
                  'e_', 'electricity_', 'price_')
    heat = full[[c for c in full if c in common or c.startswith(heat_prefixes)]].copy()
    electricity = full[[c for c in full if c in common or c.startswith(e_prefixes)]].copy()
    return full, electricity, heat


def write_outputs(records, index, logs, directory, append_from=0):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # One row per solve, not per executed timestep. The active block's executed
    # count can change at a checkpoint, so write this smaller log independently.
    pd.DataFrame(logs).to_csv(directory/'solver_log.csv', index=False)
    if append_from and append_from == len(records):
        return
    full, electricity, heat = _profiles(records[append_from:],index[append_from:])
    csv_options = {'mode':'a','header':False} if append_from else {}
    csv_options['date_format'] = '%Y-%m-%d %H:%M:%S'
    # Overwrite even an empty run, so a failed rerun cannot leave old dispatch
    # files looking like new successful results.
    full.to_csv(directory/'dispatch.csv', **csv_options)
    electricity.to_csv(directory/'electricity_flow_profile.csv', **csv_options)
    heat.to_csv(directory/'heat_flow_profile.csv', **csv_options)
    if records:
        full[['grid_import_kw','price_eur_per_kwh','electricity_cost_eur',
              'gb_fuel_kwh','gas_price_eur_per_kwh','gas_cost_eur','total_cost_eur']].to_csv(directory/'costs.csv', **csv_options)
    else:
        pd.DataFrame().to_csv(directory/'costs.csv', index=False)


def run_milp_simulation(df, save_electricity=None, save_heat=None, *,
                        config=None, assets=None, max_steps=None, output_dir=None):
    validate_input(df)
    config = config or MILPConfig(**MILP_SETTINGS)
    plant = EAMPlant(ASSETS if assets is None else assets)
    controller = MILPController(config)
    if max_steps is not None and (int(max_steps) != max_steps or not 1 <= max_steps <= len(df)):
        raise ValueError('max_steps must be an integer in 1..len(input).')
    count = len(df) if max_steps is None else int(max_steps)
    records, indices, logs = [], [], []
    written_steps = 0
    started = perf_counter()
    if output_dir:
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory/'run_status.json').write_text(json.dumps({
            'status':'running','completed_steps':0,'completed_optimizations':0}), encoding='utf-8')
        pd.DataFrame().to_csv(directory/'first_horizon_plan.csv', index=False)
        write_outputs([], [], [], directory)
        (directory/'run_settings.json').write_text(json.dumps({
            'controller': asdict(config), 'assets': plant.asset_settings,
            'gas_price_eur_per_kwh': plant.gas_price_eur_per_kwh,
            'start': str(df.index[0]), 'requested_steps': count,
            'data_end': str(df.index[-1]), 'timestep_hours': DT,
        }, indent=2), encoding='utf-8')
    try:
        for block_start in range(0, count, config.control_interval_steps):
            end = min(len(df), block_start + config.horizon_steps)
            forecast = df.iloc[block_start:end]
            elapsed_steps = logs[-1]['executed_steps'] if logs else 1
            first_action, meta = controller.solve(
                plant, forecast, at_data_end=(end == len(df)), elapsed_steps=elapsed_steps)
            block_steps = min(config.control_interval_steps, len(forecast), count-block_start)
            solve_log = {'Date': str(df.index[block_start]), 'optimization_id': len(logs)+1,
                         'simulation_start_step': block_start,
                         'planned_execution_steps': block_steps, 'executed_steps': 0, **meta}
            logs.append(solve_log)
            if output_dir and block_start == 0:
                plan = pd.DataFrame([asdict(extract_dispatch(controller.last_model,t)) for t in range(len(forecast))],
                                    index=pd.DatetimeIndex(forecast.index, name='Date'))
                plan.to_csv(directory/'first_horizon_plan.csv')
            if config.progress_every_steps:
                gap = meta['relative_gap']
                gap_text = f'{100*gap:.3f}%' if gap is not None else 'unknown'
                retry_text = ' | second attempt' if meta.get('warm_start_retry') else ''
                print(f'MILP solve {len(logs)} | {df.index[block_start]} | {meta["termination"]} | '
                      f'gap {gap_text} | {meta["controller_seconds"]:.2f}s{retry_text} | '
                      f'execute steps {block_start+1}-{block_start+block_steps}/{count}', flush=True)
            for plan_step in range(block_steps):
                i = block_start + plan_step
                action = first_action if plan_step == 0 else extract_dispatch(controller.last_model, plan_step)
                # Execute each distinct planned action through the normal physical
                # checks. Do not repeat the first action or jump asset timers.
                record = plant.execute(action, df.iloc[i])
                record.update(optimization_id=len(logs), plan_step=plan_step)
                records.append(record)
                indices.append(df.index[i])
                solve_log['executed_steps'] += 1
                if config.progress_every_steps and (
                        (i+1) % config.progress_every_steps == 0 or i == 0 or i+1 == count):
                    print(f'EXECUTE {i+1}/{count} | {df.index[i]} | plan {len(logs)} '
                          f'action {plan_step+1}/{block_steps} | HP {action.hp_stage} | '
                          f'BESS {plant.bess.energy_kwh:.1f} kWh | TESS {plant.tess.energy_kwh:.1f} kWh', flush=True)
                if output_dir and ((i+1) % 12 == 0 or plan_step+1 == block_steps):
                    write_outputs(records, indices, logs, directory, append_from=written_steps)
                    written_steps = len(records)
                    (directory/'run_status.json').write_text(json.dumps({
                        'status':'running', 'completed_steps':len(records),
                        'completed_optimizations':len(logs)}), encoding='utf-8')
    except (Exception, KeyboardInterrupt) as error:
        if output_dir:
            write_outputs(records, indices, logs, directory, append_from=written_steps)
            (directory/'run_status.json').write_text(json.dumps({
                'status': 'interrupted' if isinstance(error,KeyboardInterrupt) else 'failed',
                'completed_steps': len(records), 'error': str(error),
                'completed_optimizations': len(logs),
                'diagnostics': getattr(error,'diagnostics',{}),
            }, indent=2), encoding='utf-8')
        raise

    full, electricity, heat = _profiles(records, indices)
    # Replace preliminary input import indicators with actual optimized flows.
    result = df.loc[full.index].drop(columns=df.columns.intersection(full.columns)).join(full)
    # combine_data also carries historical/preliminary dispatch indicators.
    # The simulation return value must not present those as actual MILP flows.
    result['grid_export_kw'] = 0.0
    result['grid_import_max_kw'] = full.initial_grid_available_kw
    result['grid_rest_kw'] = full.remaining_grid_available_kw
    result['gas_boiler_max_kwth'] = full.initial_gasboiler_available_kw
    result['gas_used_kwth'] = full.gb_heat_kw
    result['gas_rest_kw'] = full.remaining_gasboiler_available_kw
    result['electricity_surplus_kw'] = full.pv_curtailed_kw
    result.attrs['solver_log'] = logs
    for profile, path in ((electricity,save_electricity), (heat,save_heat)):
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            profile.to_csv(path)
    if output_dir:
        write_outputs(records, indices, logs, directory, append_from=written_steps)
        summary = {
            'status': 'complete', 'completed_steps': count,
            'completed_optimizations': len(logs),
            'simulated_hours': count*DT, 'wall_seconds': perf_counter()-started,
            'electricity_cost_eur': full.electricity_cost_eur.sum(),
            'gas_cost_eur': full.gas_cost_eur.sum(), 'total_cost_eur': full.total_cost_eur.sum(),
            'unserved_electricity_kwh': 0.0, 'unserved_heat_kwh': 0.0,
            'bess_final_kwh': plant.bess.energy_kwh, 'tess_final_kwh': plant.tess.energy_kwh,
            'mean_controller_seconds': float(np.mean([x['controller_seconds'] for x in logs])),
            'time_limit_solves': sum(x['termination']=='maxTimeLimit' for x in logs),
            'time_limit_steps': sum(x['executed_steps'] for x in logs if x['termination']=='maxTimeLimit'),
            'warm_start_retries': sum(bool(x.get('warm_start_retry')) for x in logs),
        }
        (directory/'run_status.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return result, electricity, heat
