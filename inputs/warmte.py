"""Computing the heat demand of the greenhouse from weather data.

The chain, per hour:

    1. loss through the envelope   U_gh * A_env * (T_in,set - T_out)
    2. solar gain                  tau * alfa * A_gh * G_h
    3. heat demand                 max(0, loss - solar gain)

"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from inputs import zon

# Model constants: the building physics of a modern glass greenhouse with a thermal screen. 
U_GH_W_PER_M2K = 5.2   
TRANSMISSIE = 0.34     
ABSORPTIE = 0.67       

# The fields in project.json, with their unit and a short explanation for the error message.
VELDEN = {
    "kas_oppervlak_m2": ("m2", "ground area of the greenhouse, A_gh"),
    "kas_omhullend_m2": ("m2", "envelope area (roof + walls), A_env"),
    "kas_setpoint_c": ("C", "indoor setpoint temperature, T_in,set"),
}

# Optional, with a default value. Not required, because a project that models the generic
# greenhouse does not need it.
STANDAARD_SCHAALFACTOR = 1.0

# The setpoint in a month without a crop. Five degrees is keeping it frost free
STANDAARD_SETPOINT_LEEG = 5.0

# Only to report month numbers readably, in error messages and in the line a run prints.
MAANDNAMEN = {1: "jan", 2: "feb", 3: "mar", 4: "apr", 5: "may", 6: "jun",
              7: "jul", 8: "aug", 9: "sep", 10: "oct", 11: "nov", 12: "dec"}

VOORBEELD = """{
  "kas_oppervlak_m2": 10000.0,
  "kas_omhullend_m2": 12500.0,
  "kas_setpoint_c": 20.0
}"""


class WarmteFout(ValueError):
    """Something is wrong with the greenhouse parameters or with the weather data."""


def kasinstellingen(project: str) -> dict:
    """The greenhouse parameters of a project: from project.json, plus the model constants.
    """
    from utils.projectinstellingen import lees_json
    from utils.projectpaden import PROJECT_BESTAND, project_map

    pad = project_map(project) / PROJECT_BESTAND
    gegevens = lees_json(pad) if pad.is_file() else {}

    ontbreekt = [naam for naam in VELDEN if gegevens.get(naam) is None]
    if ontbreekt:
        regels = "\n".join(f"    {n} ({VELDEN[n][0]}): {VELDEN[n][1]}" for n in ontbreekt)
        raise WarmteFout(
            f"In {pad} the greenhouse parameters are missing:\n{regels}\n\n"
            "The heat demand is computed from the weather data, so these are required. "
            f"Add them to project.json:\n{VOORBEELD}")

    kas = {}
    for naam in VELDEN:
        try:
            kas[naam] = float(gegevens[naam])
        except (TypeError, ValueError) as fout:
            raise WarmteFout(f"In {pad} {naam} = {gegevens[naam]!r}; that is not a "
                             f"number ({VELDEN[naam][0]}).") from fout

    kas["kas_u_waarde"] = U_GH_W_PER_M2K
    kas["kas_transmissie"] = TRANSMISSIE
    kas["kas_absorptie"] = ABSORPTIE

    ruw = gegevens.get("kas_schaalfactor")
    if ruw is None:
        kas["kas_schaalfactor"] = STANDAARD_SCHAALFACTOR
    else:
        try:
            kas["kas_schaalfactor"] = float(ruw)
        except (TypeError, ValueError) as fout:
            raise WarmteFout(f"In {pad} kas_schaalfactor = {ruw!r}; that is not a "
                             "number.") from fout

    kas["kas_teeltvrije_maanden"] = _maanden(gegevens.get("kas_teeltvrije_maanden"), pad)

    ruw = gegevens.get("kas_setpoint_leeg_c")
    if ruw is None:
        kas["kas_setpoint_leeg_c"] = STANDAARD_SETPOINT_LEEG
    else:
        try:
            kas["kas_setpoint_leeg_c"] = float(ruw)
        except (TypeError, ValueError) as fout:
            raise WarmteFout(f"In {pad} kas_setpoint_leeg_c = {ruw!r}; that is not a "
                             "number (C).") from fout

    _controleer(kas, pad)
    return kas


def _maanden(ruw, pad: Path) -> tuple[int, ...]:
    """The months without a crop from project.json: a list of month numbers, 1 up to 12.

    """
    if ruw is None:
        return ()
    if isinstance(ruw, (str, bytes)) or not hasattr(ruw, "__iter__"):
        raise WarmteFout(f"In {pad} kas_teeltvrije_maanden = {ruw!r}; a list of month "
                         "numbers is expected, for example [11, 12].")
    try:
        maanden = {int(m) for m in ruw}
    except (TypeError, ValueError) as fout:
        raise WarmteFout(f"In {pad} kas_teeltvrije_maanden contains a value that is not a "
                         f"month number: {ruw!r}. Expected is for example "
                         "[11, 12].") from fout
    buiten = sorted(m for m in maanden if not 1 <= m <= 12)
    if buiten:
        raise WarmteFout(f"In {pad} kas_teeltvrije_maanden contains {buiten}; a month number "
                         "runs from 1 (January) up to 12 (December).")
    return tuple(sorted(maanden))


def _controleer(kas: dict, pad: Path) -> None:
    """Checking the values for physically impossible input, before anything is computed."""
    for naam in ("kas_oppervlak_m2", "kas_omhullend_m2"):
        if kas[naam] <= 0:
            raise WarmteFout(f"In {pad} {naam} = {kas[naam]}; that has to be greater than zero.")
    if not -50 < kas["kas_setpoint_c"] < 60:
        raise WarmteFout(f"In {pad} kas_setpoint_c = {kas['kas_setpoint_c']} C; that is "
                         "not a plausible greenhouse temperature.")
    if not -50 < kas["kas_setpoint_leeg_c"] < 60:
        raise WarmteFout(f"In {pad} kas_setpoint_leeg_c = {kas['kas_setpoint_leeg_c']} C; "
                         "that is not a plausible greenhouse temperature.")
    if kas["kas_setpoint_leeg_c"] > kas["kas_setpoint_c"]:
        raise WarmteFout(
            f"In {pad} kas_setpoint_leeg_c ({kas['kas_setpoint_leeg_c']:.1f} C) is higher than "
            f"kas_setpoint_c ({kas['kas_setpoint_c']:.1f} C). A greenhouse without a crop is "
            "kept frost free and not heated above one with a crop. Swapped?")
    if len(kas["kas_teeltvrije_maanden"]) == 12:
        print("NOTE: all twelve months are listed as without a crop in "
              f"kas_teeltvrije_maanden. The greenhouse is then kept at "
              f"{kas['kas_setpoint_leeg_c']:.1f} C all year and nothing is grown anywhere; "
              "is that right?")
    if "kas_setpoint_leeg_c" in kas and not kas["kas_teeltvrije_maanden"] \
            and kas["kas_setpoint_leeg_c"] != STANDAARD_SETPOINT_LEEG:
        print(f"NOTE: kas_setpoint_leeg_c is set to {kas['kas_setpoint_leeg_c']:g} C, but "
              "kas_teeltvrije_maanden is empty. There is therefore no month in which that "
              "value is used.")
    if kas["kas_schaalfactor"] <= 0:
        raise WarmteFout(f"In {pad} kas_schaalfactor = {kas['kas_schaalfactor']}; that "
                         "has to be greater than zero.")
    if not 0.1 <= kas["kas_schaalfactor"] <= 3.0:
        print(f"NOTE: kas_schaalfactor is set to {kas['kas_schaalfactor']:g}. That is an "
              "unusual calibration; is it right?")
    if kas["kas_omhullend_m2"] < kas["kas_oppervlak_m2"]:
        raise WarmteFout(
            f"In {pad} kas_omhullend_m2 ({kas['kas_omhullend_m2']:.0f}) is smaller than "
            f"kas_oppervlak_m2 ({kas['kas_oppervlak_m2']:.0f}). The envelope area is the "
            "roof plus the walls and is therefore always larger than the ground area. Swapped?")


def setpoint_per_uur(kas: dict, maand=None):
    """The setpoint that applies per hour: the normal one, or the empty one in a month
    without a crop.
    """
    vol = float(kas["kas_setpoint_c"])
    maanden = kas.get("kas_teeltvrije_maanden") or ()
    if maand is None or not maanden:
        return vol
    leeg = float(kas.get("kas_setpoint_leeg_c", STANDAARD_SETPOINT_LEEG))
    return np.where(np.isin(np.asarray(maand, dtype=int), maanden), leeg, vol)


def warmtevraag_per_uur(temp, ghi, kas: dict, maand=None) -> np.ndarray:
    """Steps 1 up to 3: the heat demand in kW per hour. temp in C, ghi in W/m2.

    $$Q_{he} = \\max\\bigl(0,\\; U_{gh} A_{env} (T_{in,set} - T_{out}) -
                            \\tau \\alpha A_{gh} G_h\\bigr)$$
    """
    schaal = kas.get("kas_schaalfactor", STANDAARD_SCHAALFACTOR)
    verlies = kas["kas_u_waarde"] * (schaal * kas["kas_omhullend_m2"]) \
        * (setpoint_per_uur(kas, maand) - np.asarray(temp, dtype=float))
    winst = kas["kas_transmissie"] * kas["kas_absorptie"] * (schaal * kas["kas_oppervlak_m2"]) \
        * np.asarray(ghi, dtype=float)
    return np.maximum(0.0, verlies - winst) / 1000.0          # W -> kW


def buitentemperatuur_profiel(index: pd.DatetimeIndex, bestand: Path) -> pd.Series:
    """The outdoor temperature in C at the moments of 'index', from the same weather file.
    """
    weer, _ = zon.lees_tmy(bestand)
    return zon.naar_vijf_minuten(weer["temp"].to_numpy(dtype=float), weer, index,
                                 bestand.name, "t_buiten_c", ondergrens=None)


def warmte_profiel(index: pd.DatetimeIndex, kas: dict, bestand: Path) -> pd.Series:
    """The heat demand in kW at the moments of 'index', from the weather file.
    """
    weer, _ = zon.lees_tmy(bestand)
    per_uur = warmtevraag_per_uur(weer["temp"], weer["ghi"], kas, weer["maand"])
    return zon.naar_vijf_minuten(per_uur, weer, index,
                                 bestand.name, "heat_demand_kw")


# --------------------------------------------------------------------------
# Connection with a run
# --------------------------------------------------------------------------

def toepassen(df: pd.DataFrame, project: str, stil: bool = False,
              weerjaar: str | None = None) -> pd.DataFrame:
    """Replacing the column heat_demand_kw by the computed heat demand.
    """
    kas = kasinstellingen(project)
    bestand = zon.weerbestand_van_run(project, weerjaar)
    profiel = warmte_profiel(df.index, kas, bestand)

    df = df.copy()
    gemeten = df["heat_demand_kw"] if "heat_demand_kw" in df.columns else None
    df["heat_demand_kw"] = profiel
    # The outdoor temperature for the standing loss of the buffer (assumption D5).
    df["t_buiten_c"] = buitentemperatuur_profiel(df.index, bestand)
    if not stil:
        uren = len(profiel) / 12
        schaal = kas["kas_schaalfactor"]
        gekalibreerd = (f", scale factor {schaal:g} -> effectively "
                        f"{schaal * kas['kas_oppervlak_m2']:.0f} m2") if schaal != 1 else ""
        leeg = ""
        if kas["kas_teeltvrije_maanden"]:
            namen = ", ".join(MAANDNAMEN[m] for m in kas["kas_teeltvrije_maanden"])
            leeg = f" (no crop in {namen}: {kas['kas_setpoint_leeg_c']:.1f} C)"
        regel = (f"Heat demand from weather data: {kas['kas_oppervlak_m2']:.0f} m2 greenhouse"
                 f"{gekalibreerd}, "
                 f"U_gh {kas['kas_u_waarde']:.2f} W/m2K, setpoint "
                 f"{kas['kas_setpoint_c']:.1f} C{leeg}, {bestand.name} -> "
                 f"{profiel.sum() / 12 / 1000:.1f} MWh over the whole input period "
                 f"({uren / 24:.0f} days), peak {profiel.max():.0f} kW")
        if gemeten is not None:
            regel += f"; measured was {gemeten.sum() / 12 / 1000:.1f} MWh"
        print(regel)
    return df
