"""Converting raw project data (input/raw) into processed data (input/processed).
"""

from __future__ import annotations

from pathlib import Path

from utils.projectpaden import (PROCESSED_ELEKTRICITEIT, PROCESSED_GAS, PROCESSED_GECOMBINEERD,
                                RAW_ELEKTRICITEIT, RAW_GAS, processed_map, raw_map)


def ruwe_bestanden(project: str) -> tuple[Path, Path | None]:
    """The raw files of a project: the electricity measurement, and the gas measurement if
    there is one. Only the electricity measurement is required. Without a gas measurement
    the heat demand still comes from the weather data; what falls away is the comparison
    with the measured situation.
    """
    elektriciteit = raw_map(project) / RAW_ELEKTRICITEIT
    if not elektriciteit.is_file():
        raise FileNotFoundError(
            f"Missing in {raw_map(project)}: {elektriciteit.name}. "
            "Put the measured electricity demand there.")
    gas = raw_map(project) / RAW_GAS
    return elektriciteit, (gas if gas.is_file() else None)


def processed_is_actueel(project: str) -> bool:
    """True if the combined processed data exists and is newer than the raw data."""
    gecombineerd = processed_map(project) / PROCESSED_GECOMBINEERD
    if not gecombineerd.is_file():
        return False
    aanwezig = [p for p in ruwe_bestanden(project) if p is not None]
    nieuwste_raw = max(p.stat().st_mtime for p in aanwezig)
    return gecombineerd.stat().st_mtime >= nieuwste_raw


def verwerk_ruwe_data(project: str):
    """Rebuild the processed data out of raw and return the combined table.
    """
    from inputs.load_data import load_data
    from inputs.preprocess import preprocess_electricity, preprocess_gas
    from inputs.combine_data import combine_data

    elektriciteit_raw, gas_raw = ruwe_bestanden(project)
    processed_map(project).mkdir(parents=True, exist_ok=True)
    df_el = preprocess_electricity(load_data(elektriciteit_raw), processed_map(project) / PROCESSED_ELEKTRICITEIT)
    gas_processed = processed_map(project) / PROCESSED_GAS
    if gas_raw is None:
        # A gas series left over from an earlier processing would otherwise keep being read
        # as the measured situation, while the raw file it came from is gone.
        gas_processed.unlink(missing_ok=True)
        print(f"No {RAW_GAS}: processing the electricity measurement only. The heat demand "
              "comes from the weather data; the comparison with the measured situation "
              "falls away.")
        df_gas = None
    else:
        df_gas = preprocess_gas(load_data(gas_raw), gas_processed)
    return combine_data(df_el, df_gas, processed_map(project) / PROCESSED_GECOMBINEERD)


def maak_processed(project: str, forceer: bool = False) -> Path:
    """Build the processed data (if needed) and return the path of clean_data_combined.csv."""
    gecombineerd = processed_map(project) / PROCESSED_GECOMBINEERD
    if not forceer and processed_is_actueel(project):
        print(f"Processed data is up to date: {gecombineerd}")
        return gecombineerd
    verwerk_ruwe_data(project)
    return gecombineerd


if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser(description="Build processed data out of input/raw of a project.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--scenario", help=argparse.SUPPRESS)   # dropped, is ignored
    args = parser.parse_args()
    try:
        print(f"Done: {maak_processed(args.project, forceer=True)}")
    except (ValueError, FileNotFoundError) as fout:
        parser.error(str(fout))
