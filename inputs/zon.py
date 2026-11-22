"""Computing the PV yield from weather data, instead of replaying a measured profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from utils.projectpaden import WEER_MAP
from utils.weerjaren import WEERJAREN

VLAKKEN = 4  # number of adjustable planes; see config/asset_catalogus.py

# Fixed model constants. These are not project settings: they belong to the chosen
# models.
ALBEDO = 0.2              # ground reflection; value and formula from Cui et al.
EPSILON = 0.005           # 1/K, temperature loss of crystalline silicon (positive).
T_NOCT = 47.0             # °C, nominal operating temperature of the cells
                         
NOCT_INSTRALING = 0.8     # kW/m², measurement condition at which T_NOCT applies
NOCT_LUCHTTEMP = 20.0     # °C, idem
STC_CELTEMP = 25.0        # °C, cell temperature at which the module efficiency applies

STANDAARD_ETA_PV = 0.20       # module efficiency at STC
STANDAARD_OPPERVLAK = 1.95     # m² per panel
STANDAARD_ETA_SYS = 0.83      # soiling, mismatch, cables, inverter


class ZonFout(ValueError):
    """Something is wrong with the weather data or with the PV settings."""


@dataclass(frozen=True)
class Locatie:
    """Where the field is. Comes from the header of the TMY file, not from a list of place
    names: that way the coordinates are guaranteed to belong to this irradiance series."""
    breedtegraad: float
    lengtegraad: float
    hoogte_m: float

def weerbestand_voor_jaar(label: str) -> Path:
    """The weather file that belongs to TMY, ECY or EWY."""
    label = label.upper()
    if label not in WEERJAREN:
        raise ZonFout(f"Unknown weather year {label!r}. Choose from: {', '.join(WEERJAREN)}.")
    prefix, uitleg = WEERJAREN[label]
    kandidaten = sorted(WEER_MAP.glob(f"{prefix}*.csv")) if WEER_MAP.is_dir() else []
    if not kandidaten:
        raise ZonFout(f"For {label} ({uitleg}) there is no file {prefix}*.csv in "
                      f"{WEER_MAP}. Put one there, or choose another weather year.")
    if len(kandidaten) > 1:
        raise ZonFout(f"For {label} there are several files in {WEER_MAP} "
                      f"({', '.join(p.name for p in kandidaten)}), for example from two "
                      "locations. Remove what does not belong to this project, or point to "
                      "one with 'weerbestand' in project.json and leave out the flag.")
    return kandidaten[0]


def weerbestand(naam: str | None) -> Path:
    """The path of a weather file in data/weather/. Without a name: the only TMY file."""
    if naam:
        pad = WEER_MAP / naam
        if not pad.is_file():
            raise ZonFout(f"The weather file {naam!r} is not in {WEER_MAP}.")
        return pad
    kandidaten = sorted(WEER_MAP.glob("tmy_*.csv")) if WEER_MAP.is_dir() else []
    if not kandidaten:
        raise ZonFout(f"There is no TMY file (tmy_*.csv) in {WEER_MAP}. Download one from "
                      "PVGIS and put it there, or fill in 'weerbestand' in project.json.")
    if len(kandidaten) > 1:
        raise ZonFout(f"There are several TMY files in {WEER_MAP} "
                      f"({', '.join(p.name for p in kandidaten)}). Set in project.json under "
                      "'weerbestand' which one this project uses.")
    return kandidaten[0]


def lees_tmy(pad: Path) -> tuple[pd.DataFrame, Locatie]:
    """Reading in a TMY from PVGIS: 8760 hourly values plus the location from the header.
    """
    try:
        regels = pad.read_text(encoding="utf-8-sig").splitlines()
    except OSError as fout:
        raise ZonFout(f"{pad.name} cannot be read: {fout}") from fout

    locatie = {}
    for regel in regels[:10]:
        for sleutel, veld in (("Latitude", "breedtegraad"), ("Longitude", "lengtegraad"),
                              ("Elevation", "hoogte_m")):
            if regel.startswith(sleutel):
                try:
                    locatie[veld] = float(regel.split(":")[1].strip(" ;"))
                except (IndexError, ValueError) as fout:
                    raise ZonFout(f"{pad.name}: the line {regel!r} is not a valid "
                                  f"{sleutel.lower()}.") from fout
    ontbreekt = {"breedtegraad", "lengtegraad", "hoogte_m"} - set(locatie)
    if ontbreekt:
        raise ZonFout(f"The header of {pad.name} is missing {', '.join(sorted(ontbreekt))}. "
                      "A TMY as PVGIS delivers it is expected.")

    begin = next((i for i, r in enumerate(regels) if r.startswith("time(UTC)")), None)
    if begin is None:
        raise ZonFout(f"{pad.name} has no column header 'time(UTC)'; is this a PVGIS TMY?")
    kolommen = regels[begin].split(";")
    data = []
    for regel in regels[begin + 1:]:
        if not regel[:4].isdigit():
            break           # the tail of the file is explanatory text, not data
        data.append(regel.split(";"))
    if len(data) < 8760:
        raise ZonFout(f"{pad.name} contains only {len(data)} hourly values; 8760 are "
                      "expected. Download a complete TMY year from PVGIS.")
    if len(data) > 8784:
        raise ZonFout(f"{pad.name} contains {len(data)} hourly values; a TMY is a year of "
                      "8760 hours. Is this a series of several years?")

    ruw = pd.DataFrame(data, columns=kolommen)
    nodig = {"ghi": "G(h)", "dni": "Gb(n)", "dhi": "Gd(h)", "temp": "T2m"}
    mist = [k for k in nodig.values() if k not in ruw.columns]
    if mist:
        raise ZonFout(f"In {pad.name} the columns {', '.join(mist)} are missing.")
    weer = pd.DataFrame({naam: pd.to_numeric(ruw[kolom], errors="coerce")
                         for naam, kolom in nodig.items()})
    tijd = pd.to_datetime(ruw["time(UTC)"], format="%Y%m%d:%H%M", errors="coerce")
    if weer.isna().any().any() or tijd.isna().any():
        raise ZonFout(f"{pad.name} contains values that are not a number or not a moment in time.")
    weer["maand"], weer["dag"], weer["uur"] = tijd.dt.month, tijd.dt.day, tijd.dt.hour
    return weer, Locatie(**locatie)


# --------------------------------------------------------------------------
# The chain.
# --------------------------------------------------------------------------

def instraling_op_vlak(ghi, dni, dhi, zenit, azimut_zon, helling: float,
                       azimut: float) -> np.ndarray:
    """Step 2 and 3: the irradiance on a plane, in the same unit as ghi/dni/dhi.

    Three contributions, because light reaches a tilted panel along three paths:
        direct   the sun as a point source, through the angle of incidence
        diffuse  the whole sky dome, assumed uniformly bright here
        ground   what the surface below reflects back

    """
    b, g = np.radians(helling), np.radians(azimut)
    z, a = np.radians(np.asarray(zenit, dtype=float)), np.radians(np.asarray(azimut_zon, dtype=float))
    cos_theta = np.clip(np.cos(z) * np.cos(b) + np.sin(z) * np.sin(b) * np.cos(a - g), -1.0, 1.0)
    # Below the horizon or behind the panel: no direct contribution.
    direct = np.where((np.degrees(z) < 90) & (cos_theta > 0), np.asarray(dni, dtype=float) * cos_theta, 0.0)
    diffuus = np.asarray(dhi, dtype=float) * (1 + np.cos(b)) / 2
    grond = ALBEDO * np.asarray(ghi, dtype=float) * (1 - np.cos(b)) / 2
    return np.maximum(0.0, direct + diffuus + grond)


def celtemperatuur(luchttemp, instraling_kw) -> np.ndarray:
    """Step 4: the NOCT model. Irradiance in kW/m², temperatures in °C.
    T_NOCT is the cell temperature at 0.8 kW/m², 20 °C outdoor air and 1 m/s wind. In full
    sun the cells therefore sit well over 31 °C above the outdoor air.
    """
    helling = (T_NOCT - NOCT_LUCHTTEMP) / NOCT_INSTRALING
    return np.asarray(luchttemp, dtype=float) + helling * np.asarray(instraling_kw, dtype=float)


def vermogen_per_m2(instraling_kw, luchttemp) -> np.ndarray:
    """Step 5 without the efficiencies: kW per m² of panel at an efficiency of 1."""
    verlies = 1 - EPSILON * (celtemperatuur(luchttemp, instraling_kw) - STC_CELTEMP)
    return np.maximum(0.0, np.asarray(instraling_kw, dtype=float) * verlies)


# --------------------------------------------------------------------------
# The settings of a scenario
# --------------------------------------------------------------------------

def vlakken_van(pv: dict) -> list[tuple[int, float, float]]:
    """The planes with panels on them, as (number of panels, tilt, azimuth).
    """
    uit = []
    for i in range(1, VLAKKEN + 1):
        aantal = pv.get(f"vlak{i}_panelen") or 0
        if aantal > 0:
            uit.append((int(aantal), _getal(pv, f"vlak{i}_helling", 0.0),
                        _getal(pv, f"vlak{i}_azimut", 180.0)))
    return uit


def _getal(pv: dict, naam: str, standaard: float) -> float:
    waarde = pv.get(naam)
    return standaard if waarde is None else float(waarde)


def paneeloppervlak(pv: dict) -> float:
    return _getal(pv, "oppervlak_paneel_m2", STANDAARD_OPPERVLAK)


def oppervlak(pv: dict) -> float:
    """The total panel area of all planes together, in m²."""
    return sum(aantal for aantal, _, _ in vlakken_van(pv)) * paneeloppervlak(pv)


def opgesteld_vermogen(pv: dict) -> float:
    """The peak power in kWp: area x module efficiency x 1 kW/m².
    """
    return oppervlak(pv) * _getal(pv, "eta_pv", STANDAARD_ETA_PV)


def pv_profiel(index: pd.DatetimeIndex, pv: dict, bestand: Path) -> pd.Series:
    """The PV yield in kW at the moments of 'index', from the weather file.
    """
    import pvlib  

    vlakken = vlakken_van(pv)
    if not vlakken:
        return pd.Series(0.0, index=index, name="pv_kw")
    eta_pv = _getal(pv, "eta_pv", STANDAARD_ETA_PV)
    eta_sys = _getal(pv, "eta_sys", STANDAARD_ETA_SYS)
    paneel_m2 = paneeloppervlak(pv)
    if eta_pv <= 0 or paneel_m2 <= 0:
        raise ZonFout("This scenario has panels in it, but the module efficiency or the "
                      "panel area is zero. Fill in both on the Energy system page.")

    weer, locatie = lees_tmy(bestand)
    # An hourly value is the average over that hour, so the solar position of its middle.
    # The year is arbitrary (a TMY has none); 2004 is a leap year, so that a file with
    # 29 February in it does not trip up. Those hours are not looked up further on anyway,
    # because 29 February reads 28 February.
    klok = pd.to_datetime(dict(year=2004, month=weer["maand"], day=weer["dag"],
                               hour=weer["uur"])).dt.tz_localize("UTC") + pd.Timedelta("30min")
    zon = pvlib.solarposition.get_solarposition(
        klok, locatie.breedtegraad, locatie.lengtegraad, locatie.hoogte_m)
    zenit, azimut_zon = np.asarray(zon["apparent_zenith"]), np.asarray(zon["azimuth"])

    per_uur = np.zeros(len(weer), dtype=float)
    for aantal, helling, azimut in vlakken:
        op_vlak = instraling_op_vlak(weer["ghi"], weer["dni"], weer["dhi"], zenit, azimut_zon,
                                     helling, azimut) / 1000          # W/m² -> kW/m²
        per_uur += aantal * paneel_m2 * vermogen_per_m2(op_vlak, weer["temp"])
    per_uur *= eta_pv * eta_sys

    return naar_vijf_minuten(per_uur, weer, index, bestand.name, "pv_kw")


def naar_vijf_minuten(per_uur: np.ndarray, weer: pd.DataFrame, index: pd.DatetimeIndex,
                      bestandsnaam: str, naam: str,
                      ondergrens: float | None = 0.0) -> pd.Series:
    """Putting hourly values from a TMY onto the 5-minute steps of 'index'.
    """
    # Lookup table on (month, day, hour) in UTC.
    tabel = pd.Series(per_uur, index=pd.MultiIndex.from_arrays(
        [weer["maand"], weer["dag"], weer["uur"]], names=("maand", "dag", "uur")))
    tabel = tabel[~tabel.index.duplicated()]

    uren = pd.date_range(index[0].floor("h") - pd.Timedelta("1h"),
                         index[-1].ceil("h") + pd.Timedelta("1h"), freq="1h")
    maand, dag, uur = uren.month.to_numpy(), uren.day.to_numpy(), uren.hour.to_numpy()
    dag = np.where((maand == 2) & (dag == 29), 28, dag)   # leap day: repeat 28 February
    waarden = tabel.reindex(pd.MultiIndex.from_arrays([maand, dag, uur])).to_numpy(float)
    if np.isnan(waarden).any():
        ontbreekt = int(np.isnan(waarden).sum())
        raise ZonFout(f"For {ontbreekt} hour(s) there is no value in {bestandsnaam}; "
                      "is it a complete TMY year?")

    # The hourly average belongs to the middle of the hour; linear in between.
    midden = pd.Series(waarden, index=uren + pd.Timedelta("30min"))
    samen = midden.reindex(midden.index.union(index)).interpolate(method="time")
    samen = samen.reindex(index)
    if ondergrens is not None:
        samen = samen.clip(lower=ondergrens)
    return samen.fillna(0.0).rename(naam)


# --------------------------------------------------------------------------
# Connection with a run
# --------------------------------------------------------------------------

def weerinstellingen(project: str) -> dict:
    """The weather settings of a project from project.json, with their default values.
    """
    from utils.projectinstellingen import lees_json
    from utils.projectpaden import PROJECT_BESTAND, project_map

    pad = project_map(project) / PROJECT_BESTAND
    gegevens = lees_json(pad) if pad.is_file() else {}
    return {"weerbestand": gegevens.get("weerbestand") or None}


def weerbestand_van_run(project: str, weerjaar: str | None = None) -> Path:
    """The weather file of this run: the chosen weather year, otherwise that of the project.
    """
    if weerjaar:
        return weerbestand_voor_jaar(weerjaar)
    return weerbestand(weerinstellingen(project)["weerbestand"])


def toepassen(df: pd.DataFrame, project: str, stil: bool = False,
              weerjaar: str | None = None) -> pd.DataFrame:
    """Replacing the column pv_kw by the computed yield, if the scenario asks for that.
    """
    from config.assets_config import ASSETS

    pv = ASSETS.get("pv", {})
    if not pv.get("enabled", True):
        df = df.copy()
        df["pv_kw"] = 0.0
        if not stil:
            print("PV: asset is off, so no generation. The measured column pv_kw is "
                  "replaced by zeros.")
        return df
    if not vlakken_van(pv):
        _waarschuw_als_basis_wel_rekent(project, pv, stil)
        return df
    bestand = weerbestand_van_run(project, weerjaar)
    profiel = pv_profiel(df.index, pv, bestand)
    df = df.copy()
    df["pv_kw"] = profiel
    if not stil:
        aantal = sum(n for n, _, _ in vlakken_van(pv))
        print(f"PV from weather data: {aantal} panels on {len(vlakken_van(pv))} plane(s), "
              f"{oppervlak(pv):.0f} m2, {opgesteld_vermogen(pv):.1f} kWp, {bestand.name} -> "
              f"{profiel.sum() * 5 / 60 / 1000:.1f} MWh over the whole input period "
              f"({len(profiel) / 288:.0f} days)")
    return df


def _waarschuw_als_basis_wel_rekent(project: str, pv: dict, stil: bool = False) -> None:
    """This scenario replays the measured profile while the basis computes from weather data.
    """
    from utils.projectinstellingen import lees_scenario
    from utils.projectpaden import BASIS

    if stil:
        return
    try:
        basis_pv = lees_scenario(project, BASIS)["energiesysteem"].get("pv", {})
    except (OSError, ValueError, KeyError):
        return
    if vlakken_van(basis_pv):
        print("NOTE: this scenario has no panels on any plane, so it uses the MEASURED PV "
              "profile, while the basis computes the yield from the weather data. You are "
              "then comparing two different weather years. Copy the PV settings of the "
              "basis into this scenario.")
