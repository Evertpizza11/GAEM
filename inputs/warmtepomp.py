"""Computing the COP of the heat pump from the weather data, instead of filling it in.

The formula, from oemof.thermal (oemof.solph):

    COP(t) = f_ice(t) * eta * T_high / (T_high - T_low(t))          [K]

    T_high      supply temperature of the heat pump, a design choice: doeltemperatuur_c
    T_low       source temperature. With air/water the outdoor air from the weather data,
                so a time series; with water/water a constant from bron_temperatuur_c.
    eta         quality grade, the part of Carnot that the machine actually reaches
    f_ice       defrost correction, air/water only and only below T_IJS

The quotient T_high/(T_high - T_low) is the Carnot COP, the thermodynamic upper bound.

WHAT THIS DOES TO THE MODEL. The COP becomes a time series, just like the PV yield and the
heat demand, and like those two it is computed from the weather data beforehand.

"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from inputs import zon

# The quality grade belongs to the type of machine 
KWALITEITSGRAAD = {"lucht_water": 0.40, "water_water": 0.50}

# Defrosting. With an air/water machine frost builds up on the evaporator as soon as the
# outdoor air gets near freezing point
F_IJS = 0.80
T_IJS_C = 2.0

# Lower bound on the lift, purely to prevent division by zero. 
MIN_LIFT_K = 1.0

NUL_CELSIUS_K = 273.15

VELDEN = {
    "brontype": "lucht_water or water_water",
    "doeltemperatuur_c": "supply temperature of the heat pump, T_high",
}


class WarmtepompFout(ValueError):
    """Something is wrong with the settings of the heat pump."""


def kwaliteitsgraad(brontype: str) -> float:
    try:
        return KWALITEITSGRAAD[brontype]
    except KeyError:
        raise WarmtepompFout(
            f"brontype {brontype!r} does not exist; choose "
            f"{' or '.join(repr(k) for k in KWALITEITSGRAAD)}.") from None


def instellingen(hp: dict) -> dict:
    """The heat pump fields from a scenario, checked. No default values."""
    brontype = hp.get("brontype")
    if brontype is None:
        raise WarmtepompFout(
            "The heat pump has no brontype. Choose 'lucht_water' (the source is the "
            "outdoor air from the weather data) or 'water_water' (the source is a constant "
            "temperature that you fill in yourself).")
    eta = kwaliteitsgraad(brontype)

    doel = hp.get("doeltemperatuur_c")
    if doel is None:
        raise WarmtepompFout(
            "The heat pump has no doeltemperatuur_c. That is the temperature at which it "
            "delivers its heat, usually equal to the maximum temperature of the buffer.")
    try:
        doel = float(doel)
    except (TypeError, ValueError):
        raise WarmtepompFout(f"doeltemperatuur_c = {doel!r}; that is not a number (C).") from None
    if not 0 < doel < 150:
        raise WarmtepompFout(f"doeltemperatuur_c = {doel} C; that is not a plausible "
                             "supply temperature.")

    bron = hp.get("bron_temperatuur_c")
    if brontype == "water_water":
        if bron is None:
            raise WarmtepompFout(
                "A water/water heat pump needs a bron_temperatuur_c: the temperature of "
                "the ground or surface water, constant all year.")
        try:
            bron = float(bron)
        except (TypeError, ValueError):
            raise WarmtepompFout(f"bron_temperatuur_c = {bron!r}; that is not a number (C).") from None
        if not -20 < bron < 100:
            raise WarmtepompFout(f"bron_temperatuur_c = {bron} C; that is not a plausible "
                                 "source temperature.")
        if bron >= doel:
            raise WarmtepompFout(
                f"bron_temperatuur_c ({bron} C) is not below doeltemperatuur_c "
                f"({doel} C). A heat pump lifts heat upwards; if the source is already "
                "warmer, you do not need a heat pump but a pump.")
    else:
        bron = None   # the outdoor air; bron_temperatuur_c is ignored

    return {"brontype": brontype, "eta": eta, "doeltemperatuur_c": doel,
            "bron_temperatuur_c": bron}


def cop_per_uur(t_bron_c, doeltemperatuur_c: float, brontype: str, eta: float) -> np.ndarray:
    """COP(t) from the source temperature in C. See the formula at the top of this module."""
    t_laag = np.asarray(t_bron_c, dtype=float) + NUL_CELSIUS_K
    t_hoog = float(doeltemperatuur_c) + NUL_CELSIUS_K
    lift = np.maximum(t_hoog - t_laag, MIN_LIFT_K)
    cop = eta * t_hoog / lift
    if brontype == "lucht_water":
        cop = np.where(np.asarray(t_bron_c, dtype=float) < T_IJS_C, F_IJS * cop, cop)
    return cop


def cop_profiel(index: pd.DatetimeIndex, hp: dict, bestand: Path) -> pd.Series:
    """The COP at the moments of 'index'.

    With water/water the source is constant and the COP is therefore a constant series;
    the weather file is then not read.
    """
    inst = instellingen(hp)
    if inst["brontype"] == "water_water":
        cop = cop_per_uur(inst["bron_temperatuur_c"], inst["doeltemperatuur_c"],
                          inst["brontype"], inst["eta"])
        return pd.Series(float(cop), index=index, name="cop")
    weer, _ = zon.lees_tmy(bestand)
    # The lift may not vanish anywhere. If it does, the COP runs to infinity and the MILP
    # could fill the buffer almost for free on the hottest day. That is not a modelling
    # choice but a computational error, so it stops here instead of being clipped.
    warmste = float(np.max(weer["temp"]))
    if warmste >= inst["doeltemperatuur_c"] - MIN_LIFT_K:
        raise WarmtepompFout(
            f"In {bestand.name} it gets {warmste:.1f} C, and that is not more than "
            f"{MIN_LIFT_K:g} K below the target temperature of {inst['doeltemperatuur_c']:.1f} C. "
            "The COP then runs to infinity and the model would make heat virtually for "
            "free in those hours. Choose a higher target temperature, or a water/water source.")
    per_uur = cop_per_uur(weer["temp"], inst["doeltemperatuur_c"], inst["brontype"],
                          inst["eta"])
    return zon.naar_vijf_minuten(per_uur, weer, index, bestand.name, "cop")


# --------------------------------------------------------------------------
# Connection with a run
# --------------------------------------------------------------------------

def toepassen(df: pd.DataFrame, project: str, stil: bool = False,
              weerjaar: str | None = None) -> pd.DataFrame:
    """Adding the column cop, if the scenario has a heat pump.

    Without a heat pump a column of zeros comes out: the MILP and the hierarchy do read
    it, but multiply it by a power that is fixed at zero.
    """
    from config.assets_config import ASSETS

    hp = ASSETS.get("heat_pump", {})
    df = df.copy()
    if not hp.get("enabled", False):
        df["cop"] = 0.0
        return df

    bestand = zon.weerbestand_van_run(project, weerjaar)
    profiel = cop_profiel(df.index, hp, bestand)
    df["cop"] = profiel
    if not stil:
        inst = instellingen(hp)
        bron = (f"constant source {inst['bron_temperatuur_c']:.1f} C"
                if inst["brontype"] == "water_water" else "outdoor air from the weather data")
        print(f"COP from weather data: {inst['brontype']}, supply "
              f"{inst['doeltemperatuur_c']:.1f} C, {bron}, quality grade {inst['eta']:g} -> "
              f"COP {profiel.min():.2f} to {profiel.max():.2f}, on average "
              f"{profiel.mean():.2f}")
    return df
