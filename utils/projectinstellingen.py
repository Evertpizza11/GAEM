"""Project settings: base, scenario, and simulation settings.

Project contains: 
Basis.json                  Existing energy system of the current greenhouse
scenarios/<name>.json       Potential energy system to be tested
simulatie.json              MILP settings for simulation

"""

from __future__ import annotations

import ast
import json
import math
import os
from datetime import datetime
from pathlib import Path

from config.asset_catalogus import CATALOGUS
from config.simulatie_catalogus import SIMULATIE_CATALOGUS
from utils.projectpaden import (BASIS, BASIS_BESTAND, PROJECT_BESTAND, ROOT, SIMULATIE_BESTAND,
                                controleer_naam, project_map, project_mappen, scenario_pad)

SCHEMA_VERSIE = 1
PROJECT_VAR = "GAEM_PROJECT"
SCENARIO_VAR = "GAEM_SCENARIO"


class GeenScenarioGekozen(RuntimeError):
    pass


class OngeldigScenario(ValueError):
    def __init__(self, fouten: list[str], bron: str = ""):
        self.fouten = list(fouten)
        kop = f"{bron} is not valid:" if bron else "The energy system is not valid:"
        super().__init__(kop + "".join(f"\n  - {f}" for f in self.fouten))


# --------------------------------------------------------------------------
# Reading and writing files
# --------------------------------------------------------------------------

def lees_json(pad: Path) -> dict:
    try:
        data = json.loads(Path(pad).read_text(encoding="utf-8"))
    except json.JSONDecodeError as fout:
        raise ValueError(f"{pad} is not valid JSON (line {fout.lineno}): {fout.msg}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{pad} must be a JSON object.")
    return data


def schrijf_json(pad: Path, data: dict) -> None:
    pad = Path(pad)
    pad.parent.mkdir(parents=True, exist_ok=True)
    tijdelijk = pad.with_suffix(pad.suffix + ".tmp")
    tijdelijk.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tijdelijk.replace(pad)  


def _lees_waarden(bestand: Path, namen: list[str]) -> dict:
    """Read literal assignments (NAME = {...}) from a .py file, without executing it."""
    boom = ast.parse(bestand.read_text(encoding="utf-8"), filename=str(bestand))
    gevonden = {}
    for knoop in boom.body:
        if isinstance(knoop, ast.Assign) and len(knoop.targets) == 1 and isinstance(knoop.targets[0], ast.Name):
            if knoop.targets[0].id in namen:
                gevonden[knoop.targets[0].id] = ast.literal_eval(knoop.value)
    ontbrekend = [n for n in namen if n not in gevonden]
    if ontbrekend:
        raise ValueError(f"{bestand.name}: {', '.join(ontbrekend)} not found, or not a fixed value.")
    return gevonden


def standaard_simulatie() -> dict:
    """Default MILP settings from config/ems_config.py."""
    return _lees_waarden(ROOT / "config" / "ems_config.py", ["STANDAARD_MILP_SETTINGS"])["STANDAARD_MILP_SETTINGS"]


def leeg_energiesysteem() -> dict:
    """All assets from the catalog, disabled and left blank."""
    return {naam: {"enabled": False, **{p: None for p in spec["parameters"]}}
            for naam, spec in CATALOGUS.items()}


def lees_scenario(project: str, scenario: str) -> dict:
    pad = scenario_pad(project, scenario)
    if not project_map(project).is_dir():
        raise FileNotFoundError(f"Project {project!r} does not exist in {project_map(project).parent}.")
    if not pad.is_file():
        if scenario == BASIS:
            raise FileNotFoundError(f"Project {project} does not have a {BASIS_BESTAND} yet.")
        raise FileNotFoundError(f"Scenario {scenario!r} does not exist in project {project} ({pad}).")
    data = lees_json(pad)
    if "energiesysteem" not in data:
        raise ValueError(f"{pad} is missing the 'energiesysteem' section.")
    return data


def schrijf_scenario(project: str, scenario: str, energiesysteem: dict, omschrijving: str = "") -> Path:
    """Write a scenario (or the basis for one). Invalid input IS saved: 
       this ensures that partially entered values are not lost. The 
       model only rejects the input when it is actually run."""
    pad = scenario_pad(project, scenario)
    bestaand = lees_json(pad) if pad.is_file() else {}
    schrijf_json(pad, {
        "schema_versie": SCHEMA_VERSIE,
        "naam": scenario,
        "omschrijving": omschrijving or bestaand.get("omschrijving", ""),
        "gewijzigd": datetime.now().isoformat(timespec="seconds"),
        "energiesysteem": energiesysteem,
    })
    return pad


def lees_simulatie(project: str, standaard: dict | None = None) -> dict:
    """MILP settings for the project: default, supplemented with simulation.json."""
    instellingen = dict(standaard_simulatie() if standaard is None else standaard)
    pad = project_map(project) / SIMULATIE_BESTAND
    if pad.is_file():
        eigen = lees_json(pad).get("milp", {})
        onbekend = sorted(set(eigen) - set(instellingen))
        if onbekend:
            raise ValueError(f"{pad}: unknown setting(s) {', '.join(onbekend)}.")
        instellingen.update(eigen)
    return instellingen


def schrijf_simulatie(project: str, milp: dict) -> Path:
    pad = project_map(project) / SIMULATIE_BESTAND
    schrijf_json(pad, {"schema_versie": SCHEMA_VERSIE, "milp": milp})
    return pad


# --------------------------------------------------------------------------
# Check and change to what model expects 
# --------------------------------------------------------------------------

def _getal(waarde) -> bool:
    return isinstance(waarde, (int, float)) and not isinstance(waarde, bool) and math.isfinite(waarde)


def _controleer_parameter(label: str, ps: dict, waarde) -> str | None:
    naam = f"{label}: '{ps['label']}'" if label else f"'{ps['label']}'"
    if waarde is None:
        return None if ps.get("optioneel") else f"{naam} ({ps['eenheid']}) is not filled in."
    if ps["type"] == "keuze":
        return None if waarde in ps["opties"] else f"{naam} must be one of {', '.join(ps['opties'])}."
    if not _getal(waarde):
        return f"{naam} must be a number, not {waarde!r}."
    if ps["type"] == "int" and waarde != int(waarde):
        return f"{naam} must be a whole number."
    if "min" in ps:
        if ps.get("min_exclusief") and waarde <= ps["min"]:
            return f"{naam} must be greater than {ps['min']:g} {ps['eenheid']}."
        if waarde < ps["min"]:
            return f"{naam} must be at least {ps['min']:g} {ps['eenheid']}."
    if "max" in ps and waarde > ps["max"]:
        return f"{naam} may be at most {ps['max']:g} {ps['eenheid']}."
    if "max_exclusief" in ps and waarde >= ps["max_exclusief"]:
        return f"{naam} must be smaller than {ps['max_exclusief']:g}."
    return None


def _kruiscontroles(naam: str, a: dict) -> list[str]:
    """Interdependence between parameters applies only if each individual number is valid in its own right."""
    fouten = []
    if naam == "gas_boiler" and a["min_capacity_kwth"] > a["max_capacity_kwth"]:
        fouten.append("Gas boiler: the minimum thermal capacity is greater than the maximum.")
    if naam == "tess" and a["max_temp_c"] <= a["min_temp_c"]:
        fouten.append("Heat buffer: the maximum temperature must be higher than the minimum.")
    if naam == "heat_pump":
        # Stages are electrical power, thermal power follows from COP
        if a["min_capacity_kwe"] > a["max_capacity_kwe"]:
            fouten.append("Heat pump: the minimum electrical capacity is greater than the maximum.")
        if a["control_stages"] > 1 and a["max_capacity_kwe"] <= a["min_capacity_kwe"]:
            fouten.append("Heat pump: with more than 1 stage the maximum electrical "
                          "capacity must be greater than the minimum.")
        if a.get("brontype") == "water_water":
            bron = a.get("bron_temperatuur_c")
            if bron is None:
                fouten.append("Heat pump: a water/water source needs a source "
                              "temperature.")
            elif a.get("doeltemperatuur_c") is not None and bron >= a["doeltemperatuur_c"]:
                fouten.append(
                    f"Heat pump: the source temperature ({bron:g} C) is not below the "
                    f"supply temperature ({a['doeltemperatuur_c']:g} C). A heat pump lifts "
                    "heat upwards; if the source is already warmer, you do not need one.")
    return fouten


def valideer_energiesysteem(systeem: dict) -> list[str]:
    """List of errors in plain language; empty = all clear."""
    if not isinstance(systeem, dict):
        return ["The energy system must be an object with assets."]
    fouten = [f"Unknown asset {naam!r}; the known assets are: {', '.join(CATALOGUS)}."
              for naam in systeem if naam not in CATALOGUS]
    for naam, spec in CATALOGUS.items():
        asset = systeem.get(naam, {"enabled": False}) 
        label = spec["label"]
        if not isinstance(asset, dict):
            fouten.append(f"{label}: expected an object with settings.")
            continue
        if not isinstance(asset.get("enabled"), bool):
            fouten.append(f"{label}: 'enabled' must be true or false.")
            continue
        onbekend = sorted(set(asset) - {"enabled"} - set(spec["parameters"]))
        if onbekend:
            fouten.append(f"{label}: unknown parameter(s) {', '.join(onbekend)}.")
        if not asset["enabled"]:
            continue
        eigen = [f for p, ps in spec["parameters"].items()
                 if (f := _controleer_parameter(label, ps, asset.get(p)))]
        fouten += eigen
        if not eigen:
            fouten += _kruiscontroles(naam, asset)
    if not any(isinstance(a, dict) and a.get("enabled") is True for a in systeem.values()):
        fouten.append("No asset is switched on yet. Fill in the energy system first.")
    return fouten


def valideer_simulatie(instellingen: dict) -> list[str]:
    """Check MILP settings using the same rules as MILPConfig."""
    fouten = [f"Unknown setting {k!r}." for k in instellingen if k not in SIMULATIE_CATALOGUS]
    fouten += [f for k, ps in SIMULATIE_CATALOGUS.items()
               if (f := _controleer_parameter("", ps, instellingen.get(k)))]
    if fouten:
        return fouten
    stappen = instellingen["horizon_hours"] * 12  # 12 steps of 5 min per hour
    if abs(stappen - round(stappen)) > 1e-8:
        fouten.append("'Planning horizon' must be a multiple of 5 minutes (for example 48 or 12.5 h).")
    elif instellingen["control_interval_steps"] > round(stappen):
        fouten.append(f"'Re-optimize after' may not be longer than the horizon ({round(stappen)} steps).")
    return fouten


def model_assets(systeem: dict, bron: str = "") -> dict:
    """Convert a controlled energy system into the model's ASSETS format.

    An asset that is switched off is assigned the neutral values from the catalog: it is not included, but the model code can still instantiate it.
    """
    fouten = valideer_energiesysteem(systeem)
    if fouten:
        raise OngeldigScenario(fouten, bron)
    assets = {}
    for naam, spec in CATALOGUS.items():
        asset = systeem.get(naam, {"enabled": False})
        if asset["enabled"]:
            assets[naam] = {"enabled": True, **{p: asset.get(p) for p in spec["parameters"]}}
        else:
            assets[naam] = {"enabled": False, **{p: ps["neutraal"] for p, ps in spec["parameters"].items()}}
    return assets


def verschillen_met_basis(project: str, scenario: str) -> list[dict]:
    """How does a scenario differ from the baseline? Regarding run information and the dashboard."""
    basis = lees_scenario(project, BASIS)["energiesysteem"]
    eigen = lees_scenario(project, scenario)["energiesysteem"]
    verschillen = []
    for naam, spec in CATALOGUS.items():
        b, s = basis.get(naam, {"enabled": False}), eigen.get(naam, {"enabled": False})
        if b.get("enabled") != s.get("enabled"):
            # Asset deleted or added is one change not per parameter but asset.
            verschillen.append({"asset": naam, "parameter": "enabled",
                                "basis": b.get("enabled"), "scenario": s.get("enabled")})
            continue
        if not s.get("enabled"):
            continue
        for p in spec["parameters"]:
            if b.get(p) != s.get(p):
                verschillen.append({"asset": naam, "parameter": p, "basis": b.get(p), "scenario": s.get(p)})
    return verschillen


# --------------------------------------------------------------------------
# The active scenario (for config/assets_config.py and config/ems_config.py)
# --------------------------------------------------------------------------

def kies_scenario(project: str, scenario: str) -> dict:
    """Select a project + scenario for this run. Call this BEFORE the model is loaded.

    Immediately checks whether the scenario exists and is valid, and returns the model ASSETS.
    """
    project, scenario = controleer_naam(project, "project name"), controleer_naam(scenario, "scenario name")
    if not project_map(project).is_dir():
        raise FileNotFoundError(f"Project {project!r} does not exist in {project_map(project).parent}.")
    assets = model_assets(lees_scenario(project, scenario)["energiesysteem"],
                          f"Scenario {scenario!r} of project {project}")
    fouten = valideer_simulatie(lees_simulatie(project))  # Fail now, not halfway the run
    if fouten:
        raise OngeldigScenario(fouten, f"simulatie.json of project {project}")
    os.environ[PROJECT_VAR], os.environ[SCENARIO_VAR] = project, scenario
    return assets


def actieve_keuze() -> tuple[str, str]:
    project, scenario = os.environ.get(PROJECT_VAR), os.environ.get(SCENARIO_VAR)
    if not project or not scenario:
        raise GeenScenarioGekozen(
            "No project and scenario have been chosen, so the model does not know which "
            "energy system to compute with. Start for example with:\n"
            "    python run_milp.py --project Voorbeeld_kas --scenario S1\n"
            "or use the dashboard.")
    return project, scenario


def laad_actieve_assets() -> dict:
    project, scenario = actieve_keuze()
    return model_assets(lees_scenario(project, scenario)["energiesysteem"],
                        f"Scenario {scenario!r} of project {project}")


def laad_actieve_simulatie(standaard: dict) -> dict:
    """Default values, supplemented by the `simulation.json` file from the selected project (if present)."""
    project = os.environ.get(PROJECT_VAR)
    return lees_simulatie(project, standaard) if project else dict(standaard)


# --------------------------------------------------------------------------
# Gas price
# --------------------------------------------------------------------------

def gasinstellingen(project: str) -> dict:
    """The gas price for a project from project.json, in euros per m³ AND per kWh.

    In the input, the price is specified per cubic meter (m³), as this is the unit used 
    in the invoice and the contract. Since the model performs calculations based on the 
    fuel's energy content in kWh, the value is converted here using the gross calorific 
    value (upper heating value) of Dutch natural gas (35.17 MJ/m³ = 9.769 kWh/m³).
    """
    from utils.constants import GAS_KWH_PER_M3

    pad = project_map(project) / PROJECT_BESTAND
    gegevens = lees_json(pad) if pad.is_file() else {}

    ruw = gegevens.get("gasprijs_eur_per_m3")
    if ruw is None:
        raise ValueError(
            f"{pad} has no gasprijs_eur_per_m3: the gas price of this company in euros "
            "per cubic metre, as on the invoice. Add it to project.json, for example:\n"
            '    "gasprijs_eur_per_m3": 0.45')
    try:
        per_m3 = float(ruw)
    except (TypeError, ValueError) as fout:
        raise ValueError(f"In {pad} gasprijs_eur_per_m3 = {ruw!r}; that is not a "
                         "number (euro/m3).") from fout
    if not math.isfinite(per_m3) or per_m3 < 0:
        raise ValueError(f"In {pad} gasprijs_eur_per_m3 = {per_m3}; that has to be a finite "
                         "number of zero or higher.")
    if per_m3 > 5:
        print(f"NOTE: gasprijs_eur_per_m3 is set to {per_m3:g} euro per cubic metre. That is "
              "unusually high; is it perhaps per GJ or per MWh?")

    return {"gasprijs_eur_per_m3": per_m3,
            "gasprijs_eur_per_kwh": per_m3 / GAS_KWH_PER_M3}


def actieve_gasprijs_eur_per_kwh() -> float:
    """The gas price per kWh of fuel for the SELECTED project."""
    project, _ = actieve_keuze()
    return gasinstellingen(project)["gasprijs_eur_per_kwh"]


# --------------------------------------------------------------------------
# New project
# --------------------------------------------------------------------------

PROJECT_LEESMIJ = """PROJECT FOLDER {naam}
{streep}

input/raw/        Put the data you received from the company here, exactly as you
                  received it. Never edit it by hand: this is your source. The model
                  needs meetreeks_elektra.csv: the measured electricity demand in
                  steps of 5 minutes, with a column tijd_utc. data_gas.csv with the
                  daily gas readings is optional. The heat demand is computed from
                  the weather data either way, so without it only the comparison
                  with the measured situation falls away.

input/processed/  Made by the model itself out of raw/. Deleting it is always
                  allowed: it is rebuilt on the next run.

basis.json        The EXISTING energy system of the greenhouse. This is your
                  reference: the dashboard shows it beside every input field.

scenarios/        Every file is a possible energy system, for example
                  wp_en_buffer.json. A new scenario starts as a copy of the
                  basis; after that you may change anything.

simulatie.json    Simulation settings (horizon, interval, time limit, ...).

output/           Only results you deliberately kept. Every run first lands in
                  the folder runs/ in the root folder of the model.

project.json      Name, location and description of the project.

Shared market data (electricity prices) is NOT here but in data/markt/.
"""


# --------------------------------------------------------------------------
# The properties of a project
# --------------------------------------------------------------------------

WEER_MAP_NAAM = "data/weather"


MAANDNAMEN = {1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
              7: "July", 8: "August", 9: "September", 10: "October", 11: "November",
              12: "December"}

PROJECTVELDEN = {
    "kas_oppervlak_m2": {
        "label": "Greenhouse ground area", "eenheid": "m²", "type": "float", "verplicht": True,
        "min": 0.0, "stap": 100.0, "standaard": None,
        "help": ("A_gh: the ground area of the greenhouse. Enters the solar term of the heat "
                 "demand.")},
    "kas_omhullend_m2": {
        "label": "Envelope area", "eenheid": "m²", "type": "float", "verplicht": True,
        "min": 0.0, "stap": 100.0, "standaard": None,
        "help": ("A_env: roof plus walls. Enters the loss term of the heat demand. If a wall "
                 "borders another heated space, leave that wall out.")},
    "kas_setpoint_c": {
        "label": "Indoor temperature setpoint", "eenheid": "°C", "type": "float",
        "verplicht": True, "min": -50.0, "max": 60.0, "stap": 0.5, "standaard": 20.0,
        "help": "T_in,set: the temperature the greenhouse holds while a crop is being grown."},
    "kas_teeltvrije_maanden": {
        "label": "Months without a crop", "type": "maanden", "verplicht": False,
        "standaard": None,
        "help": ("The months in which nothing is grown. In those months the model uses the "
                 "setpoint below instead of the normal setpoint. Choosing nothing means a crop "
                 "all year round.")},
    "kas_setpoint_leeg_c": {
        "label": "Setpoint without a crop", "eenheid": "°C", "type": "float",
        "verplicht": False, "min": -50.0, "max": 60.0, "stap": 0.5, "standaard": 5.0,
        "decimalen": 1,
        "help": ("The temperature an empty greenhouse holds: keeping it frost free, not heating "
                 "for a crop. May not be higher than the normal setpoint. Without months free of "
                 "a crop this field does nothing.")},
    "gasprijs_eur_per_m3": {
        "label": "Gas price", "eenheid": "EUR/m³", "type": "float", "verplicht": True,
        "min": 0.0, "stap": 0.01, "standaard": None, "decimalen": 4,
        "help": ("The price per CUBIC METRE, as it appears on the invoice. The model converts it "
                 "to euros per kWh itself, on the higher heating value. NOTE that the efficiency "
                 "of your gas boiler then has to be on the higher heating value as well.")},
    "weerbestand": {
        "label": "Weather file", "type": "weerbestand", "verplicht": False, "standaard": None,
        "help": ("If there is only one TMY file in data/weather/, the model picks that one by "
                 "itself and this may stay empty.")},
    "klok_offset_uren": {
        "label": "Clock offset from UTC", "eenheid": "h", "type": "float",
        "verplicht": False, "min": -12.0, "max": 14.0, "stap": 1.0, "standaard": 1.0,
        "help": ("The difference between the local clock of the measured data and UTC. The TMY "
                 "is in UTC.")},
    "kas_schaalfactor": {
        "label": "Greenhouse scale factor", "type": "float", "verplicht": False,
        "min": 0.0, "stap": 0.01, "standaard": None, "decimalen": 4,
        "help": ("Optional calibration: multiplies both areas and therefore the whole heat "
                 "demand. Leaving it empty means no calibration.")},
    "e_demand_cap_kw": {
        "label": "Cap on electricity demand", "eenheid": "kW", "type": "float",
        "verplicht": False, "min": 0.0, "stap": 5.0, "standaard": None,
        "help": ("Optional: steps above this value are capped and the surplus is spread over the "
                 "rest of the same day, so that the daily total stays the same. Leaving it empty "
                 "leaves the measured series untouched.")},
}


def weerbestanden() -> list[str]:
    """The weather files located in data/weather/, in alphabetical order."""
    map_ = ROOT / WEER_MAP_NAAM
    return sorted(p.name for p in map_.glob("*.csv")) if map_.is_dir() else []


def ontbrekende_projectvelden(project: str) -> list[str]:
    """The mandatory fields that are not yet present in project.json.

    This allows the dashboard to issue a warning before a run fails, rather than after.
    """
    gegevens = lees_json(project_map(project) / PROJECT_BESTAND) or {}
    return [naam for naam, veld in PROJECTVELDEN.items()
            if veld["verplicht"] and gegevens.get(naam) is None]


def nieuw_projectbestand(naam: str, locatie: str = "", omschrijving: str = "",
                         velden: dict | None = None) -> dict:
    """The content of a new project.json.

    Fields with no value are OMITTED rather than being written as null: the code
    that reads them checks whether the key exists, and a null value could potentially 
    be interpreted as an actual, provided value.
    """
    bestand = {"naam": naam, "locatie": locatie, "omschrijving": omschrijving,
               "aangemaakt": datetime.now().isoformat(timespec="seconds")}
    for sleutel in PROJECTVELDEN:
        waarde = (velden or {}).get(sleutel)
        if waarde not in (None, ""):
            bestand[sleutel] = waarde
    return bestand


def schrijf_leesmij(naam: str, overschrijven: bool = False) -> Path:
    pad = project_map(naam) / "LEESMIJ.txt"
    if overschrijven or not pad.exists():
        titel = f"PROJECT FOLDER {naam}"
        pad.write_text(PROJECT_LEESMIJ.format(naam=naam, streep="=" * len(titel)), encoding="utf-8")
    return pad


def maak_project(naam: str, locatie: str = "", omschrijving: str = "",
                 basis_van: str | None = None, velden: dict | None = None) -> Path:
    """Create a project folder with subfolders and starter files. Never overwrite anything.
    """
    map_ = project_map(naam)
    if basis_van is not None:
        basis = lees_scenario(basis_van, BASIS)["energiesysteem"]  
    for sub in project_mappen(naam):
        sub.mkdir(parents=True, exist_ok=True)
    if not (map_ / PROJECT_BESTAND).exists():
        schrijf_json(map_ / PROJECT_BESTAND,
                     nieuw_projectbestand(naam, locatie, omschrijving, velden))
    if not (map_ / BASIS_BESTAND).exists():
        if basis_van is None:
            schrijf_scenario(naam, BASIS, leeg_energiesysteem(), "Existing system; still to be filled in.")
        else:
            schrijf_scenario(naam, BASIS, basis, f"Copied from the basis of {basis_van}; please check.")
    if not (map_ / SIMULATIE_BESTAND).exists():
        schrijf_simulatie(naam, standaard_simulatie())
    schrijf_leesmij(naam)
    return map_
