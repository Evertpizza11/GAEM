"""
Folder structure from GAEM. 
The whole code gets the folderstructure from here. 


    GAEM/
    ├─ projecten/
    │   └─ <projectnaam>/              Project name
    │       ├─ project.json            name, location, description
    │       ├─ basis.json              Current energy system in the greenhouse
    │       ├─ simulatie.json          simulation settings(rolling horizon, solver time, etc)
    │       ├─ scenarios/
    │       │   └─ <scenario>.json     A potential energy system
    │       ├─ input/
    │       │   ├─ raw/                
    │       │   └─ processed/          
    │       └─ output/                 
    ├─ data/markt/                     
    │   └─ e_prices/
    │       ├─ raw/
    │       └─ clean/
    └─ runs/                           All finished simulations end up in this folder
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

# Main folder of the model including run_milp.
ROOT = Path(__file__).resolve().parent.parent

PROJECTEN_MAP = ROOT / "projecten"
MARKT_MAP = ROOT / "data" / "markt"
WEER_MAP = ROOT / "data" / "weather"
RUNS_MAP = ROOT / "runs"

PRIJZEN_RAW_MAP = MARKT_MAP / "e_prices" / "raw"
PRIJZEN_CLEAN_MAP = MARKT_MAP / "e_prices" / "clean"
PRIJZEN_CLEAN_BESTAND = PRIJZEN_CLEAN_MAP / "e_prices_clean_utc.csv"

PROJECT_BESTAND = "project.json"
BASIS_BESTAND = "basis.json"
SIMULATIE_BESTAND = "simulatie.json"
SCENARIOS_SUBMAP = "scenarios"
BASIS = "basis"  # Safe scenarion name, normal system is in basis.json

# Raw in put the model expects and what it makes from it
# Measured data with summer time. 
RAW_ELEKTRICITEIT = "meetreeks_elektra.csv"
RAW_GAS = "data_gas.csv"
PROCESSED_ELEKTRICITEIT = "clean_data_electricity.csv"
PROCESSED_GAS = "clean_data_gas.csv"
PROCESSED_GECOMBINEERD = "clean_data_combined.csv"

# Character, numbers, _ and -, no space bar.
_GELDIGE_NAAM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def controleer_naam(naam: str, soort: str = "project name") -> str:
    naam = str(naam).strip()
    if not _GELDIGE_NAAM.fullmatch(naam):
        raise ValueError(
            f"Invalid {soort} {naam!r}. Use letters, digits, _ or -, "
            "without spaces, for example Voorbeeld_kas or wp_en_buffer."
        )
    return naam


def controleer_projectnaam(naam: str) -> str:
    return controleer_naam(naam, "project name")


def project_map(naam: str) -> Path:
    return PROJECTEN_MAP / controleer_projectnaam(naam)


def raw_map(naam: str) -> Path:
    return project_map(naam) / "input" / "raw"


def processed_map(naam: str) -> Path:
    return project_map(naam) / "input" / "processed"


def output_map(naam: str) -> Path:
    return project_map(naam) / "output"


def scenarios_map(naam: str) -> Path:
    return project_map(naam) / SCENARIOS_SUBMAP


def scenario_pad(project: str, scenario: str) -> Path:
    """Path of a scenario. 'basis' refers to the existing system in basis.json."""
    scenario = controleer_naam(scenario, "scenario name")
    if scenario == BASIS:
        return project_map(project) / BASIS_BESTAND
    return scenarios_map(project) / f"{scenario}.json"


def project_mappen(naam: str) -> list[Path]:
    """All the folders that a project must have at a minimum."""
    return [raw_map(naam), processed_map(naam), output_map(naam), scenarios_map(naam)]


def bestaande_projecten() -> list[str]:
    """Names of project folders that contain a project.json file, in alphabetical order."""
    if not PROJECTEN_MAP.is_dir():
        return []
    return sorted(p.name for p in PROJECTEN_MAP.iterdir()
                  if p.is_dir() and (p / PROJECT_BESTAND).is_file())


def bestaande_scenarios(project: str) -> list[str]:
    """Project scenarios, in alphabetical order. 'Basis' is not included."""
    map_ = scenarios_map(project)
    if not map_.is_dir():
        return []
    return sorted(p.stem for p in map_.glob("*.json") if p.is_file())


def nieuwe_runmap(project: str, scenario: str, soort: str) -> Path:
    """Unique folder in runs/, e.g., runs/20260921_110312_Test_1_testsystem_milp_24u."""
    stempel = datetime.now().strftime("%Y%m%d_%H%M%S")
    basis = RUNS_MAP / f"{stempel}_{controleer_projectnaam(project)}_{scenario}_{soort}"
    kandidaat, teller = basis, 2
    while kandidaat.exists():
        kandidaat = basis.with_name(f"{basis.name}_{teller}")
        teller += 1
    return kandidaat


def uren_label(uren: float) -> str:
    return f"{uren:g}u".replace(".", "p")


def nieuwste_run(met_bestand: str = "costs.csv") -> Path | None:
    """Most recent folder in runs/ that contains the specified file ('None' if none exists)."""
    if not RUNS_MAP.is_dir():
        return None
    kandidaten = [p for p in RUNS_MAP.iterdir() if p.is_dir() and (p / met_bestand).is_file()]
    return max(kandidaten, key=lambda p: p.name) if kandidaten else None
