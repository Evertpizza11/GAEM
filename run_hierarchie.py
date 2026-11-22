"""Hierarchical reference run for a scenario of a project.

    python run_hierarchie.py --project Voorbeeld_kas --scenario S1
    python run_hierarchie.py --project Voorbeeld_kas --scenario S1 --hours 168

First rebuilds the processed data out of input/raw, then runs the hierarchical
controller and computes the electricity and gas costs.
Results go into runs/<date_time>_<project>_<scenario>_hierarchie_<hours>/.
"""

import argparse

from utils.projectinstellingen import kies_scenario
from utils.projectpaden import PRIJZEN_CLEAN_BESTAND, nieuwe_runmap, uren_label
from utils.weerjaren import WEERJAREN


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, help="Project folder in projecten/, for example Voorbeeld_kas.")
    parser.add_argument("--scenario", required=True, help="Scenario in scenarios/, of 'basis'.")
    parser.add_argument("--hours", type=float, help="Only the first this many hours (default: all data).")
    weer = parser.add_mutually_exclusive_group()
    for label, (_, uitleg) in WEERJAREN.items():
        weer.add_argument(f"--{label}", dest="weerjaar", action="store_const", const=label,
                          help=f"Run this scenario in the {uitleg.lower()} ({label}).")
    args = parser.parse_args()

    # Choose the scenario BEFORE the model is loaded (see utils/projectinstellingen.py).
    try:
        kies_scenario(args.project, args.scenario)
    except (ValueError, FileNotFoundError) as error:
        parser.error(str(error))

    from digital_twin.simulation import run_simulation
    from inputs import warmte, zon
    from inputs.verwerk_project import verwerk_ruwe_data
    from analysis.energy_import_costs import calculate_electricity_costs, calculate_gas_costs
    from utils.constants import TIMESTEP_5MIN_HOURS as DT

    # As before: always rebuild the processed data out of raw.
    try:
        df_combined = verwerk_ruwe_data(args.project)
        # The measurement keeps its gaps; a simulation needs a complete series. That is why
        # the filling in happens here and not when reading in, so that it stays accountable
        # what was measured and what was filled in (see inputs/preprocess.vul_gaten).
        from inputs.preprocess import vul_gaten
        df_combined = vul_gaten(df_combined)
        # Replaces the measured columns pv_kw and heat_demand_kw by what follows from the
        # weather data; the measurement stays in clean_data_combined.csv as a basis for comparison.
        df_combined = zon.toepassen(df_combined, args.project, weerjaar=args.weerjaar)
        df_combined = warmte.toepassen(df_combined, args.project, weerjaar=args.weerjaar)
    except (FileNotFoundError, warmte.WarmteFout, zon.ZonFout) as error:
        parser.error(str(error))

    steps = None
    if args.hours is not None:
        steps = round(args.hours / DT)
        if steps < 1 or abs(steps * DT - args.hours) > 1e-8 or steps > len(df_combined):
            parser.error("--hours has to be a positive multiple of 5 minutes, within the available data.")
    duur = uren_label((steps or len(df_combined)) * DT)
    # The weather year in the folder name, just as with run_milp.py.
    soort = f"{args.weerjaar}_hierarchie_" if args.weerjaar else "hierarchie_"
    output = nieuwe_runmap(args.project, args.scenario, soort + duur)
    output.mkdir(parents=True)

    elec_out = output / "electricity_flow_profile.csv"
    heat_out = output / "heat_flow_profile.csv"
    run_simulation(df_combined, save_electricity=elec_out, save_heat=heat_out, max_steps=steps)

    calculate_electricity_costs(
        electricity_profile_path=elec_out,
        price_path=PRIJZEN_CLEAN_BESTAND,
        output_path=output / "electricity_costs.csv",
    )
    calculate_gas_costs(
        heat_profile_path=heat_out,
        output_path=output / "gas_costs.csv",
    )
    print(f"\nDone. Results are in {output}")


if __name__ == "__main__":
    main()
