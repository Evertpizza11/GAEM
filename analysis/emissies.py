"""CO2 emissions of a simulated energy system, and what it saves relative to the existing
situation.

Post-processing: this runs after a run and changes nothing in the model. Everything that
is needed is already in dispatch.csv 
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from utils.constants import GAS_KWH_PER_M3
from utils.projectpaden import ROOT

BESTAND_FACTOREN = "data/emissies/emissiefactoren.json"

# The three scopes that the model reports next to each other. 'verbranding' and 'keten'
# add up to 'totaal'; that is not an assumption but how the source list is built.
AFBAKENINGEN = ("verbranding", "keten", "totaal")


def kuub_uit_kwh(kwh: float | pd.Series) -> float | pd.Series:
    """Fuel in kWh to standard cubic metres of gas.
    
    """
    return kwh / GAS_KWH_PER_M3


# --------------------------------------------------------------------------
# De factoren
# --------------------------------------------------------------------------

@dataclass
class Factoren:
    """The emission factors as they are in the JSON file, with their origin alongside."""
    gas_per_m3: dict[str, float]
    elektriciteit_per_kwh: dict[str, float]
    bron_gas: str
    bron_elektriciteit: str
    lijst: str
    lijstjaar: int | None
    elektriciteit_keuze: str = ""
    elektriciteit_soort: str = ""
    reeks_bestand: Path | None = None
    reeks_kolom: str = "kg_co2_per_kwh"
    ruw: dict = field(default_factory=dict, repr=False)

    @property
    def herkomst(self) -> str:
        jaar = f", lijst {self.lijstjaar}" if self.lijstjaar else ""
        return f"{self.lijst}{jaar}"

    @property
    def variabele_reeks(self) -> bool:
        return self.reeks_bestand is not None


def factoren(pad: Path | str | None = None) -> Factoren:
    """Reading the emission factors fresh from disk.
    
    """
    pad = Path(pad) if pad else ROOT / BESTAND_FACTOREN
    if not pad.is_file():
        raise FileNotFoundError(
            f"The emission factors are missing: {pad} does not exist. Without that file the "
            "CO2 emissions cannot be computed, and an invented factor does not belong here.")
    gegevens = json.loads(pad.read_text(encoding="utf-8"))

    def neem(deel: dict, wat: str) -> dict[str, float]:
        ontbreekt = [sleutel for sleutel in AFBAKENINGEN if (deel or {}).get(sleutel) is None]
        if ontbreekt:
            raise ValueError(f"In {pad}, {wat} is missing the value(s) {ontbreekt}.")
        waarden = {sleutel: float(deel[sleutel]) for sleutel in AFBAKENINGEN}
        som = waarden["verbranding"] + waarden["keten"]
        if abs(som - waarden["totaal"]) > 0.01 * max(waarden["totaal"], 1e-9):
            print(f"NOTE: in {pad}, combustion and chain of {wat} add up to {som:g}, "
                  f"while total is at {waarden['totaal']:g}. Check the list.")
        return waarden

    # Electricity is a CHOICE out of several categories of the same list. That is not
    # roundabout but the point: which category you choose changes the outcome by almost a
    # factor of two, so that choice belongs visibly in the input and not implicitly in the
    # code. The category not chosen is kept for a sensitivity analysis.
    opties = gegevens.get("elektriciteit_opties") or {}
    keuze = gegevens.get("elektriciteit_keuze", "")
    if opties:
        if keuze not in opties:
            raise ValueError(
                f"In {pad} elektriciteit_keuze is set to {keuze!r}, but that is none of the "
                f"available categories {sorted(opties)}.")
        elektriciteit = opties[keuze]
    else:
        # Scheme 1: a single block 'elektriciteit' without a choice.
        elektriciteit = gegevens.get("elektriciteit") or {}
        keuze = "elektriciteit"

    reeks = gegevens.get("elektriciteit_reeks") or {}
    bestand = reeks.get("bestand")

    return Factoren(
        gas_per_m3=neem(gegevens.get("aardgas"), "aardgas"),
        elektriciteit_per_kwh=neem(elektriciteit, f"elektriciteit ({keuze})"),
        bron_gas=(gegevens.get("aardgas") or {}).get("bron", ""),
        bron_elektriciteit=elektriciteit.get("bron", ""),
        lijst=gegevens.get("lijst", "onbekend"),
        lijstjaar=gegevens.get("lijstjaar"),
        elektriciteit_keuze=keuze,
        elektriciteit_soort=elektriciteit.get("soort", ""),
        reeks_bestand=(ROOT / bestand) if bestand else None,
        reeks_kolom=reeks.get("kolom", "kg_co2_per_kwh"),
        ruw=gegevens,
    )


def _elektriciteitsfactor(f: Factoren, index: pd.DatetimeIndex,
                          afbakening: str) -> pd.Series | float:
    """The factor per kWh: a fixed number, or a series if a file has been given.
    
    """
    if not f.variabele_reeks:
        return f.elektriciteit_per_kwh[afbakening]
    if not f.reeks_bestand.is_file():
        raise FileNotFoundError(
            f"emissiefactoren.json points to {f.reeks_bestand} for a variable emission "
            "factor, but that file does not exist.")
    reeks = pd.read_csv(f.reeks_bestand, index_col="Date", parse_dates=True)[f.reeks_kolom]
    deel = reeks.reindex(index)
    leeg = int(deel.isna().sum())
    if leeg:
        print(f"NOTE: the variable emission factor is missing at {leeg} of the {len(index)} "
              "time steps. Those steps stay empty instead of zero.")
    return deel


# --------------------------------------------------------------------------
# Rekenen
# --------------------------------------------------------------------------

@dataclass
class Uitstoot:
    """The emissions over a period, split by carrier and scope.
    
    Everything in kg CO2-eq. 'dagen' is included so that the caller can scale it; scaling to
    a year deliberately does not happen here.
    """
    gas_kg: dict[str, float]
    elektriciteit_kg: dict[str, float]
    gas_m3: float
    elektriciteit_kwh: float
    dagen: float

    def totaal(self, afbakening: str = "totaal") -> float:
        return self.gas_kg[afbakening] + self.elektriciteit_kg[afbakening]

    def per_jaar(self, afbakening: str = "totaal", dagen_per_jaar: float = 365) -> float:
        if not self.dagen:
            return float("nan")
        return self.totaal(afbakening) * dagen_per_jaar / self.dagen


def _dagen(index: pd.DatetimeIndex, dt_uren: pd.Series | float) -> float:
    if isinstance(dt_uren, pd.Series):
        return float(dt_uren.sum()) / 24
    return len(index) * float(dt_uren) / 24


def van_run(df: pd.DataFrame, f: Factoren | None = None) -> Uitstoot:
    """The emissions of a simulated run, from dispatch.csv.
    """
    f = f or factoren()
    dt = df["dt"] if "dt" in df.columns else df.get("dt_hours", 5 / 60)

    gas_kwh = df["gb_fuel_kwh"] if "gb_fuel_kwh" in df.columns else pd.Series(0.0, index=df.index)
    gas_m3 = kuub_uit_kwh(gas_kwh)
    e_kwh = df["grid_import_kw"] * dt

    gas_kg, e_kg = {}, {}
    for afbakening in AFBAKENINGEN:
        gas_kg[afbakening] = float((gas_m3 * f.gas_per_m3[afbakening]).sum())
        e_kg[afbakening] = float((e_kwh * _elektriciteitsfactor(f, df.index, afbakening)).sum())

    return Uitstoot(gas_kg=gas_kg, elektriciteit_kg=e_kg, gas_m3=float(gas_m3.sum()),
                    elektriciteit_kwh=float(e_kwh.sum()), dagen=_dagen(df.index, dt))


def per_asset(df: pd.DataFrame, f: Factoren | None = None) -> dict[str, Uitstoot]:
    """The emissions attributed to the gas boiler, the grid and the heat pump.
    
    NOTE: these three do NOT add up to the system total, and that is not an error.
    
    - 'gas_boiler' gets all the burnt gas. Unambiguous: there is no other gas consumer.
    - 'grid' gets all offtaken electricity. Together with the boiler this is exactly van_run().
    - 'heat_pump' gets its SHARE of that grid offtake, and that share is therefore already in 'grid'.
    """
    f = f or factoren()
    dt = df["dt"] if "dt" in df.columns else df.get("dt_hours", 5 / 60)
    dagen = _dagen(df.index, dt)

    def kolom(naam: str) -> pd.Series:
        return df[naam] if naam in df.columns else pd.Series(0.0, index=df.index)

    gas_m3 = kuub_uit_kwh(kolom("gb_fuel_kwh"))
    net_kwh = kolom("grid_import_kw") * dt
    # The share of the heat pump in the grid offtake of that step. Where nobody uses
    # electricity there is nothing to divide either; there the share is zero instead of a
    # division by zero.
    verbruik = kolom("initial_hp_el_kw") + kolom("initial_e_demand_kw")
    hp_kwh = (net_kwh * kolom("initial_hp_el_kw") / verbruik.where(verbruik > 0)).fillna(0.0)

    def uitstoot(gas: pd.Series, elektriciteit: pd.Series) -> Uitstoot:
        gas_kg, e_kg = {}, {}
        for afbakening in AFBAKENINGEN:
            gas_kg[afbakening] = float((gas * f.gas_per_m3[afbakening]).sum())
            e_kg[afbakening] = float(
                (elektriciteit * _elektriciteitsfactor(f, df.index, afbakening)).sum())
        return Uitstoot(gas_kg=gas_kg, elektriciteit_kg=e_kg, gas_m3=float(gas.sum()),
                        elektriciteit_kwh=float(elektriciteit.sum()), dagen=dagen)

    leeg = pd.Series(0.0, index=df.index)
    return {
        "gas_boiler": uitstoot(gas_m3, leeg),
        "grid": uitstoot(leeg, net_kwh),
        "heat_pump": uitstoot(leeg, hp_kwh),
    }


def van_meting(meting: pd.DataFrame, df: pd.DataFrame,
               f: Factoren | None = None) -> Uitstoot | None:
    """The emissions of the existing situation, from the measured data of the company.
    
    """
    f = f or factoren()
    if not {"grid_import_kw", "gas_kwh_5min"} <= set(meting.columns):
        return None
    deel = meting.reindex(df.index)
    if deel["grid_import_kw"].isna().all():
        return None

    dt = df["dt"] if "dt" in df.columns else df.get("dt_hours", 5 / 60)
    gas_m3 = kuub_uit_kwh(deel["gas_kwh_5min"].fillna(0))
    e_kwh = deel["grid_import_kw"].fillna(0) * dt

    gas_kg, e_kg = {}, {}
    for afbakening in AFBAKENINGEN:
        gas_kg[afbakening] = float((gas_m3 * f.gas_per_m3[afbakening]).sum())
        e_kg[afbakening] = float((e_kwh * _elektriciteitsfactor(f, df.index, afbakening)).sum())

    return Uitstoot(gas_kg=gas_kg, elektriciteit_kg=e_kg, gas_m3=float(gas_m3.sum()),
                    elektriciteit_kwh=float(e_kwh.sum()), dagen=_dagen(df.index, dt))


def vermeden(nieuw: Uitstoot, referentie: Uitstoot | None,
             afbakening: str = "totaal") -> float:
    """Avoided emissions: reference minus new. Positive means fewer emissions than now."""
    if referentie is None:
        return float("nan")
    return referentie.totaal(afbakening) - nieuw.totaal(afbakening)
