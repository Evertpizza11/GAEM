"""Run the MILP control for a scenario of a project.

    python run_milp.py --project Voorbeeld_kas --scenario S1 --hours 24

Execution duration, planning horizon and re-optimization interval are independent.
Input data AFTER the requested run stays available for planning.

The energy system comes from projecten/<project>/scenarios/<scenario>.json
(--scenario basis uses basis.json). The MILP settings come from
config/ems_config.py, supplemented with projecten/<project>/simulatie.json, and
can be overridden per run with the options below.
Results go into runs/<date_time>_<project>_<scenario>_milp_<hours>/.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from utils.projectinstellingen import (BASIS, kies_scenario, lees_scenario, lees_simulatie,
                                       verschillen_met_basis)
from utils.projectpaden import PRIJZEN_CLEAN_BESTAND, ROOT, nieuwe_runmap, uren_label
from utils.weerjaren import WEERJAREN


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--project', required=True, help='Project folder in projecten/, for example Voorbeeld_kas.')
    parser.add_argument('--scenario', required=True, help="Scenario in scenarios/, of 'basis'.")
    parser.add_argument('--hours', type=float, default=1.0, help='Executed hours (default: 1); does not shorten forecast.')
    parser.add_argument('--all', action='store_true', help='Execute every available timestep; can take very long.')
    parser.add_argument('--start', help='YYYY-MM-DD HH:MM, exact timestamp; assets start at configured initial states.')
    parser.add_argument('--horizon-hours', type=float, help='Override planning horizon.')
    parser.add_argument('--control-interval-steps', type=int,
                        help='Execute this many distinct five-minute actions per solve; 1 = replan every step.')
    parser.add_argument('--time-limit', type=float, help='Seconds per solve, excluding building/checking.')
    parser.add_argument('--compare', action='store_true', help='Also run the hierarchy over the same executed interval.')
    weer = parser.add_mutually_exclusive_group()
    for label, (_, uitleg) in WEERJAREN.items():
        weer.add_argument(f'--{label}', dest='weerjaar', action='store_const', const=label,
                          help=f'Run this scenario in the {uitleg.lower()} ({label}).')
    parser.add_argument('--input', type=Path, help='Other input than the processed data of the project.')
    parser.add_argument('--prices', type=Path, default=PRIJZEN_CLEAN_BESTAND)
    parser.add_argument('--output', type=Path, help='Other output folder than a new folder in runs/.')
    args = parser.parse_args()

    # 1. Choose the scenario BEFORE the model is loaded (see utils/projectinstellingen.py).
    try:
        kies_scenario(args.project, args.scenario)
    except (ValueError, FileNotFoundError) as error:
        parser.error(str(error))

    # 2. Only now load the model: config.assets_config reads the chosen scenario.
    import pandas as pd
    from config.ems_config import MILP_SETTINGS
    from digital_twin.simulation import run_simulation
    from digital_twin.milp_control.milp_solver import MILPConfig
    from digital_twin.milp_control.rolling_horizon_simulation import attach_prices
    from inputs import elektra, warmte, warmtepomp, zon
    from inputs.verwerk_project import maak_processed
    from utils.constants import TIMESTEP_5MIN_HOURS as DT
    from utils.projectinstellingen import actieve_gasprijs_eur_per_kwh

    settings = dict(MILP_SETTINGS)
    if args.horizon_hours is not None:
        settings['horizon_hours'] = args.horizon_hours
    if args.control_interval_steps is not None:
        settings['control_interval_steps'] = args.control_interval_steps
    if args.time_limit is not None:
        settings['time_limit_seconds'] = args.time_limit
    try:
        config = MILPConfig(**settings)
    except ValueError as error:
        parser.error(str(error))

    try:
        input_path = args.input or maak_processed(args.project)
    except FileNotFoundError as error:
        parser.error(str(error))
    df = pd.read_csv(input_path, index_col='Date', parse_dates=True)
    # The measurement keeps its gaps; a simulation needs a complete series. That is why
    # the filling in happens here and not when reading in, so that it stays accountable
    # what was measured and what was filled in (see inputs/preprocess.vul_gaten).
    from inputs.preprocess import vul_gaten
    df = vul_gaten(df)
    # Which weather file this run uses. Looked up here and not only in the three
    # applications below, so that a missing or duplicate weather year gives a clean message
    # straight away and so that run_info.json can record what was really used.
    try:
        weerbestand_pad = zon.weerbestand_van_run(args.project, args.weerjaar)
    except zon.ZonFout as error:
        parser.error(str(error))
    # If this scenario computes the PV from weather data, this replaces the measured column pv_kw.
    df = zon.toepassen(df, args.project, weerjaar=args.weerjaar)
    # The heat demand always comes from the weather data; the measurement stays as a basis
    # for comparison in clean_data_combined.csv.
    try:
        df = warmte.toepassen(df, args.project, weerjaar=args.weerjaar)
    except warmte.WarmteFout as error:
        parser.error(str(error))
    # If project.json names a capping limit, a contract exceedance in the measured
    # electricity demand is taken off (assumption B8, see inputs/elektra.py).
    try:
        df = elektra.toepassen(df, args.project)
    except elektra.ElektraFout as error:
        parser.error(str(error))
    # The COP of the heat pump follows from the same weather data (assumption D6).
    try:
        df = warmtepomp.toepassen(df, args.project, weerjaar=args.weerjaar)
    except warmtepomp.WarmtepompFout as error:
        parser.error(str(error))
    if args.start:
        timestamp = pd.Timestamp(args.start)
        if timestamp not in df.index:
            parser.error('--start must match an available input timestamp exactly.')
        df = df.loc[timestamp:]
    df = attach_prices(df, args.prices)
    steps = len(df) if args.all else round(args.hours/DT)
    if not args.all and (steps < 1 or abs(steps*DT-args.hours) > 1e-8):
        parser.error('--hours must be a positive multiple of five minutes.')
    if steps > len(df):
        parser.error('Requested run extends beyond available input data.')

    # The weather year in the folder name, so that three weather years of the same scenario
    # can be told apart without opening run_info.json.
    soort = f"{args.weerjaar}_milp_" if args.weerjaar else 'milp_'
    output = args.output or nieuwe_runmap(args.project, args.scenario, soort + uren_label(steps*DT))
    if not output.is_absolute():
        output = ROOT / output
    output.mkdir(parents=True, exist_ok=True)
    scenario_data = lees_scenario(args.project, args.scenario)
    (output/'run_info.json').write_text(json.dumps({
        'project': args.project, 'scenario': args.scenario,
        'scenario_omschrijving': scenario_data.get('omschrijving', ''),
        'controller': 'milp', 'gestart': datetime.now().isoformat(timespec='seconds'),
        'invoer': str(input_path), 'prijzen': str(args.prices),
        # Which weather was in it. The outcome does not show that, and without this a run with
        # another weather year cannot be recognized afterwards.
        'weer': {'weerjaar': args.weerjaar, 'bestand': weerbestand_pad.name},
        'afwijkingen_van_basis': verschillen_met_basis(args.project, args.scenario) if args.scenario != BASIS else [],
        'simulatie_project': lees_simulatie(args.project), 'simulatie_deze_run': settings,
        'energiesysteem': scenario_data['energiesysteem'],
        'basis': lees_scenario(args.project, BASIS)['energiesysteem'],
        'commando': ' '.join(sys.argv),
    }, indent=2, ensure_ascii=False), encoding='utf-8')

    print(f'Project {args.project} | scenario {args.scenario}', flush=True)
    print(f'Execute {steps} steps ({steps*DT:g} h); forecast {config.horizon_hours:g} h; '
          f'{len(df)} available input rows. Assets start from the scenario.', flush=True)
    solves = (steps + config.control_interval_steps - 1) // config.control_interval_steps
    print(f'Re-optimize every {config.control_interval_steps} steps '
          f'({config.control_interval_steps*DT:g} h); {solves} planned solves.', flush=True)
    print(f'Output: {output}', flush=True)
    result, _, _ = run_simulation(df, controller='milp', milp_config=config,
                                 max_steps=steps, output_dir=output)
    print(f'Actual simulated energy cost: EUR {result.total_cost_eur.sum():.2f}')
    if args.compare:
        reference_dir = output/'hierarchy_reference'
        reference_dir.mkdir(parents=True, exist_ok=True)
        _, el, heat = run_simulation(df.copy(), max_steps=steps,
                                     save_electricity=reference_dir/'electricity_flow_profile.csv',
                                     save_heat=reference_dir/'heat_flow_profile.csv')
        grid = el.initial_grid_available_kw - el.remaining_grid_available_kw
        rows = []
        for label, e_cost, g_cost, unmet_e, unmet_h, b_end, t_end in (
            ('milp', result.electricity_cost_eur.sum(), result.gas_cost_eur.sum(), 0.,0.,
             result.bess_energy_kwh.iloc[-1], result.tess_energy_kwh.iloc[-1]),
            ('hierarchy', (grid*DT*df.price_eur_per_kwh.reindex(el.index)).sum(),
             (heat.gb_fuel_kwh*actieve_gasprijs_eur_per_kwh()).sum(),
             (el.remaining_e_demand_kw*DT).sum(), (heat.remaining_heat_demand_kw*DT).sum(),
             el.bess_energy_kwh.iloc[-1], heat.tess_energy_kwh.iloc[-1]),
        ):
            rows.append(dict(controller=label,electricity_cost_eur=e_cost,gas_cost_eur=g_cost,
                             total_cost_eur=e_cost+g_cost,unserved_electricity_kwh=unmet_e,
                             unserved_heat_kwh=unmet_h,bess_final_kwh=b_end,tess_final_kwh=t_end))
        comparison = pd.DataFrame(rows)
        comparison.to_csv(output/'comparison.csv',index=False)
        print(comparison.round(4).to_string(index=False))
        print('Compare service levels AND final inventory. A short-run cash difference is not a savings claim.')
    print(f'\nDone. Results are in {output}')


if __name__ == '__main__':
    main()
