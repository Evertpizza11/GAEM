"""The key performance indicators (KPIs) of a simulated energy system, per asset and for the
system as a whole.

Post-processing: this runs after a run and changes nothing in the model. Everything comes
from dispatch.csv, run_info.json and solver_log.csv, so it also works on runs that were
saved earlier.

The dashboard shows these numbers (KPI page); the computing is here, so that it can also be
used without the dashboard, for example from a script that makes a table for the thesis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from digital_twin.assets.tess import TESS

DAGEN_PER_JAAR = 365
STAP_UREN_STANDAARD = 5 / 60

# ----------------------------------------------------------------------------
# YEAR RUN OR NOT (assumption K2)
# ----------------------------------------------------------------------------
# A week in January times 52 is not a year, it is a year of January weeks. 
DAGEN_VOOR_JAARRUN = 360


def is_jaarrun(dgn: float) -> bool:
    """Does this run cover a whole year, so that yearly figures are meaningful?"""
    return bool(dgn) and math.isfinite(dgn) and dgn >= DAGEN_VOOR_JAARRUN

# Which assets there are and what they are called in the dashboard. The same order as in
# the thesis (table with asset-level KPIs).
ASSETS = (
    ("pv", "Photovoltaic system"),
    ("grid", "Grid connection"),
    ("bess", "Battery energy storage"),
    ("heat_pump", "Heat pump"),
    ("gas_boiler", "Gas boiler"),
    ("tess", "Thermal energy storage"),
)

# Assets for which no KPIs have been established yet. They do appear in the overview,
# with a line saying that they still have to come; that way it is visible in the dashboard
# that they are missing instead of being silently absent. At this moment all assets have
# KPIs; the arrangement stays in place for an asset that is added later.
NOG_GEEN_KPIS: tuple[str, ...] = ()

# Above which part of its maximum capacity the boiler runs 'at high load' (assumption
# K4). Strictly greater than, so a boiler that runs at exactly 90 % does not count.
KETEL_HOOG_VERMOGEN = 0.90


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------

@dataclass
class Kpi:
    """One key figure, with what is needed to judge and cite it."""
    naam: str
    eenheid: str
    waarde: float
    # A fixed key for KPIs whose NAME moves with the run length, so that the dashboard can
    # still match run A and run B to each other. Empty means: the name is the
    # key.
    sleutel: str = ""
    benchmark: float | None = None
    benchmark_tekst: str = ""
    richting: str = ""          # "hoger" = higher is better, "lager" = lower is better, "" = no judgement
    decimalen: int = 1
    toelichting: str = ""
    bron: str = ""
    marge: float | None = None  # below which a deviation from the benchmark is noise

    @property
    def haalt_benchmark(self) -> bool | None:
        """True, False or None. None as soon as there is no benchmark or no direction.
        """
        if self.benchmark is None or not self.richting or not math.isfinite(self.waarde):
            return None
        marge = self.marge if self.marge is not None else max(abs(self.benchmark), 1.0) * 1e-9
        if self.richting == "hoger":
            return self.waarde >= self.benchmark - marge
        return self.waarde <= self.benchmark + marge


@dataclass
class Groep:
    """The KPIs of one asset or of one theme at system level."""
    sleutel: str
    naam: str
    kpis: list[Kpi] = field(default_factory=list)
    aanwezig: bool = True
    melding: str = ""


def _eerste(df: pd.DataFrame, *namen: str) -> pd.Series:
    """The first column out of namen that is in df; otherwise a series of zeros.

    """
    for naam in namen:
        if naam in df.columns:
            return df[naam].fillna(0.0)
    return pd.Series(0.0, index=df.index)


def _som(df: pd.DataFrame, *namen: str) -> pd.Series:
    """The sum of all columns out of namen that are in df."""
    aanwezig = [df[naam].fillna(0.0) for naam in namen if naam in df.columns]
    return sum(aanwezig) if aanwezig else pd.Series(0.0, index=df.index)


def stap_uren(df: pd.DataFrame) -> pd.Series | float:
    for naam in ("dt", "dt_hours"):
        if naam in df.columns:
            return df[naam]
    return STAP_UREN_STANDAARD


def dagen(df: pd.DataFrame) -> float:
    dt = stap_uren(df)
    uren = float(dt.sum()) if isinstance(dt, pd.Series) else len(df) * float(dt)
    return uren / 24


def _per_jaar(waarde: float, dgn: float) -> float:
    """Scale linearly to a year. Only meaningful on a run of some length."""
    return waarde * DAGEN_PER_JAAR / dgn if dgn else float("nan")


def _deel(teller: float, noemer: float) -> float:
    return 100 * teller / noemer if noemer else float("nan")


def _aan(systeem: dict, asset: str) -> bool:
    return bool((systeem.get(asset) or {}).get("enabled"))


def _par(systeem: dict, asset: str, naam: str, standaard=None):
    waarde = (systeem.get(asset) or {}).get(naam)
    return standaard if waarde is None else waarde


def _starts(aan: pd.Series) -> int:
    """The number of times that an asset goes from off to on."""
    aan = aan.astype(bool)
    return int((aan & ~aan.shift(1, fill_value=False)).sum())


def _uren_waar(masker: pd.Series, dt: pd.Series | float) -> float:
    """The number of hours in which masker is true, also with a varying step length."""
    if isinstance(dt, pd.Series):
        return float((masker.astype(bool) * dt).sum())
    return float(masker.astype(bool).sum() * float(dt))


# --------------------------------------------------------------------------
# Energy flows: one place where the series from dispatch.csv are named
# --------------------------------------------------------------------------

@dataclass
class Stromen:
    """The energy flows of a run in kWh, over the whole period of that run."""
    dagen: float
    warmtevraag: float
    warmte_piek: float
    e_vraag: float
    e_piek: float
    pv_opwek: float
    pv_benut: float
    pv_afgeknepen: float
    pv_piek_afgeknepen: float
    netafname: float
    net_piek: float
    net_grens: float
    net_stappen_aan_grens: int
    stappen: int
    gb_warmte: float
    gb_brandstof: float
    hp_warmte: float
    hp_elektrisch: float
    hp_draaiuren: float
    hp_starts: int
    gb_draaiuren: float
    gb_uren_hoog: float
    gb_starts: int
    gb_maximaal: float
    tess_ontladen: float
    tess_geladen: float
    tess_verlies: float
    tess_inhoud_min: float
    tess_inhoud_max: float
    tess_inhoud_gem: float
    bess_ontladen: float
    bess_geladen: float
    gaskosten: float
    elektrakosten: float
    onbediende_warmte: float
    onbediende_elektriciteit: float


def stromen(df: pd.DataFrame) -> Stromen:
    """All energy flows that the KPIs need, in one go out of dispatch.csv.
    
    Everything in kWh (powers times the step length), except where the name says otherwise.
    """
    dt = stap_uren(df)

    pv_benut = _eerste(df, "pv_used_kw")
    pv_weg = _eerste(df, "pv_curtailed_kw")
    pv_opwek = _eerste(df, "initial_pv_available_kw")
    netafname = _eerste(df, "grid_import_kw")
    grens = _eerste(df, "initial_grid_available_kw")

    # The heat of the heat pump. The MILP sets initial_hp_heat_kw: that is the TOTAL heat,
    # and exactly equal to hp_heat_to_demand_kw + hp_heat_to_tess_kw (checked on a run of
    # 31 days: difference 0.000e+00 kWh). That is why that column comes first.
    if "initial_hp_heat_kw" in df.columns:
        hp_warmte_kw = _eerste(df, "initial_hp_heat_kw")
    elif "hp_heat_to_tess_kw" in df.columns:
        hp_warmte_kw = _som(df, "hp_heat_to_demand_kw", "hp_heat_to_tess_kw")
    else:
        hp_warmte_kw = _eerste(df, "hp_heat_to_demand_kw")
    hp_el_kw = _eerste(df, "initial_hp_el_kw", "hp_el_kw")

    # The electricity demand of the COMPANY: the equipment of the greenhouse plus the heat pump.
    # So not the grid offtake, because that also runs through the battery and leaves out the PV.
    e_vraag_kw = _eerste(df, "initial_e_demand_kw") + hp_el_kw
    warmtevraag_kw = _eerste(df, "initial_heat_demand_kw")

    # At the limit of the connection: the model never goes above it, so the question is not
    # whether it happens but how often the connection is full. A margin of 0.1 kW against
    # rounding differences of the solver.
    aan_grens = (grens > 0) & (netafname >= grens - 0.1)

    # The boiler. Whether it runs is in gb_is_on, the on/off binary that both controllers
    # write away; if that column is missing, the rule is: heat above a small tolerance. On the
    # year run of the MILP those two agree on all 105,120 steps. The maximum capacity is in
    # initial_gasboiler_available_kw per step; without that column 'high load' cannot be
    # determined and that KPI stays empty instead of every running step counting.
    gb_kw = _eerste(df, "gb_heat_kw")
    gb_aan = (df["gb_is_on"].fillna(0).astype(bool) if "gb_is_on" in df.columns
              else gb_kw > 1e-6)
    if "initial_gasboiler_available_kw" in df.columns:
        gb_max_kw = df["initial_gasboiler_available_kw"].fillna(0.0)
        gb_hoog = gb_aan & (gb_kw > KETEL_HOOG_VERMOGEN * gb_max_kw)
        gb_uren_hoog = _uren_waar(gb_hoog, dt)
        gb_maximaal = float(gb_max_kw.max()) if len(gb_max_kw) else float("nan")
    else:
        gb_uren_hoog = gb_maximaal = float("nan")

    # The contents of the buffer, in kWh above T_min: at the start of the run plus at the end
    # of every step. The start of step t is the end of step t-1, so the first starting
    # contents is the only one that still has to be added.
    tess_eind = _eerste(df, "tess_energy_kwh")
    tess_begin = _eerste(df, "tess_energy_start_kwh")
    tess_inhoud = pd.concat([tess_begin.iloc[:1], tess_eind]) if len(df) else tess_eind
    stap = dt if isinstance(dt, pd.Series) else pd.Series(float(dt), index=df.index)

    return Stromen(
        dagen=dagen(df),
        stappen=len(df),
        warmtevraag=float((warmtevraag_kw * dt).sum()),
        warmte_piek=float(warmtevraag_kw.max()) if len(df) else float("nan"),
        e_vraag=float((_eerste(df, "initial_e_demand_kw") * dt).sum()),
        e_piek=float(e_vraag_kw.max()) if len(df) else float("nan"),
        pv_opwek=float((pv_opwek * dt).sum()),
        pv_benut=float((pv_benut * dt).sum()),
        pv_afgeknepen=float((pv_weg * dt).sum()),
        pv_piek_afgeknepen=float(pv_weg.max()) if len(pv_weg) else float("nan"),
        netafname=float((netafname * dt).sum()),
        net_piek=float(netafname.max()) if len(netafname) else float("nan"),
        net_grens=float(grens.max()) if len(grens) else float("nan"),
        net_stappen_aan_grens=int(aan_grens.sum()),
        gb_warmte=float((_eerste(df, "gb_heat_kw") * dt).sum()),
        gb_brandstof=float(_eerste(df, "gb_fuel_kwh").sum()),
        hp_warmte=float((hp_warmte_kw * dt).sum()),
        hp_elektrisch=float((hp_el_kw * dt).sum()),
        hp_draaiuren=float(((hp_el_kw > 0) * dt).sum()) if isinstance(dt, pd.Series)
        else float((hp_el_kw > 0).sum() * dt),
        hp_starts=_starts(hp_el_kw > 0),
        gb_draaiuren=_uren_waar(gb_aan, dt),
        gb_uren_hoog=gb_uren_hoog,
        gb_starts=_starts(gb_aan),
        gb_maximaal=gb_maximaal,
        tess_ontladen=float((_eerste(df, "tess_discharge_kw") * dt).sum()),
        tess_geladen=float((_eerste(df, "tess_charge_kw") * dt).sum()),
        tess_verlies=float(_eerste(df, "tess_heat_loss_kwh").sum()),
        tess_inhoud_min=float(tess_inhoud.min()) if len(tess_inhoud) else float("nan"),
        tess_inhoud_max=float(tess_inhoud.max()) if len(tess_inhoud) else float("nan"),
        # Time weighted, so that a run with a varying step length does not count a step heavier
        # than its duration.
        tess_inhoud_gem=(float((tess_eind * stap).sum() / stap.sum())
                         if len(df) and stap.sum() else float("nan")),
        bess_ontladen=float((_eerste(df, "bess_discharge_kw") * dt).sum()),
        bess_geladen=float((_eerste(df, "bess_charge_kw") * dt).sum()),
        # The costs are already in euros per step, so not multiplied by dt again. They come from
        # the run itself and not from the financial analysis, so that they are there as well if
        # the latter could not run.
        gaskosten=float(_eerste(df, "gas_cost_eur").sum()),
        elektrakosten=float(_eerste(df, "electricity_cost_eur").sum()),
        onbediende_warmte=float((_eerste(df, "heat_balance_residual_kw").clip(lower=0) * dt).sum()),
        onbediende_elektriciteit=float((_eerste(df, "e_balance_residual_kw").clip(lower=0) * dt).sum()),
    )


# --------------------------------------------------------------------------
# KPIs per asset
# --------------------------------------------------------------------------

def _pv(s: Stromen, systeem: dict) -> list[Kpi]:
    return [
        Kpi("Curtailed energy", "%", _deel(s.pv_afgeknepen, s.pv_opwek),
            toelichting="The part of the generation that could not be used. Feed-in does not "
                        "exist in this model (assumption D2) and curtailing costs nothing (D3), "
                        "so everything that cannot be used or fit in the battery disappears."),
        Kpi("Peak curtailed", "kW", s.pv_piek_afgeknepen, decimalen=0,
            toelichting="The largest difference between generation and what the system could "
                        "absorb, at any moment in the run."),
        Kpi("PV share of electricity supply", "%", _deel(s.pv_benut, s.pv_benut + s.netafname),
            toelichting="The part of all electricity that comes onto the site that comes from "
                        "the own PV. Divided by the SUPPLY, so own PV plus grid offtake "
                        "together; the heat pump belongs to the consumption and is therefore "
                        "not treated separately. This number is equal to the KPI of the same "
                        "name under Energy, and together with the grid share there it is "
                        "exactly 100 %. The battery does not count: it generates nothing but "
                        "only shifts in time."),
    ]


def _grid(s: Stromen, systeem: dict) -> list[Kpi]:
    contract = _par(systeem, "grid", "max_import_kw")
    grens = float(contract) if contract is not None else s.net_grens
    jaar = is_jaarrun(s.dagen)
    invoer = _per_jaar(s.netafname, s.dagen) if jaar else s.netafname
    return [
        Kpi("Total import", "MWh yr⁻¹" if jaar else "MWh", invoer / 1000.0,
            sleutel="netafname", decimalen=0,
            toelichting="All electricity taken from the grid. This is what is on the invoice "
                        "and what the electricity costs are computed with. It is NOT the "
                        "consumption of the company: the own PV is not in it and the charging "
                        "losses of the battery are. See 'Electricity use' and 'Total "
                        "electricity demand' under Energy."),
        # Deliberately NO direction: the model cannot go above the contract limit, because that
        # is a hard bound in the optimization. A pass/fail judgement would therefore always be
        # 'passed' and would say nothing. The benchmark is given, as a reference value to read
        # the peak against.
        Kpi("Peak import", "kW", s.net_piek, decimalen=0,
            benchmark=grens if math.isfinite(grens) else None,
            benchmark_tekst=f"contract {grens:.0f} kW" if math.isfinite(grens) else "",
            bron="Case study",
            toelichting="The highest offtake in this run. The model never goes above the "
                        "contract limit: that is a hard bound in the optimisation. This KPI "
                        "therefore does not show whether the limit is exceeded, but how much "
                        "room is left."),
        Kpi("Reserve to contract limit", "kW",
            max(0.0, grens - s.net_piek) if math.isfinite(grens) else float("nan"),
            decimalen=0,
            toelichting="Contractwaarde min de hoogste afname. Nooit negatief, om dezelfde "
                        "reden."),
        Kpi("Share at limit", "%", _deel(s.net_stappen_aan_grens, s.stappen),
            decimalen=2,
            toelichting="The part of the time steps in which the connection is full. The "
                        "higher, the more often the contract limit decides the outcome instead "
                        "of the price."),
    ]


def _bess(s: Stromen, systeem: dict) -> list[Kpi]:
    capaciteit = _par(systeem, "bess", "capacity_kwh")
    uit_kw = _par(systeem, "bess", "capacity_out_kw")
    cycli = s.bess_ontladen / float(capaciteit) if capaciteit else float("nan")
    jaar = is_jaarrun(s.dagen)
    ontladen = _per_jaar(s.bess_ontladen, s.dagen) if jaar else s.bess_ontladen
    return [
        Kpi("Energy discharged", "MWh yr⁻¹" if jaar else "MWh", ontladen / 1000.0,
            sleutel="bess_ontladen", decimalen=1,
            toelichting="The electricity the battery delivered to the system. Discharged and "
                        "not charged: that is what really comes out, and it is also the "
                        "quantity the cycles below are counted with. The difference with what "
                        "went in are the efficiency losses. This is not extra supply: that "
                        "electricity was already counted when it came from the grid or from the "
                        "PV, and the battery only shifts it in time."),
        Kpi("Cycles per day", "d⁻¹", cycli / s.dagen if s.dagen else float("nan"),
            decimalen=2, benchmark=1.0, benchmark_tekst="~ 1", richting="",
            bron="NREL ATB / PNNL-33283",
            toelichting="Equivalent full cycles per day: discharged energy divided by the "
                        "capacity, divided by the number of days. The benchmark of about one "
                        "cycle per day is the assumption the lifetime and the costs from the "
                        "literature rest on. There is deliberately no judgement with it: both "
                        "much less and much more than one deviates from that assumption, so "
                        "higher is not better."),
        Kpi("Energy to power ratio", "h",
            float(capaciteit) / float(uit_kw) if capaciteit and uit_kw else float("nan"),
            decimalen=2,
            toelichting="Capacity divided by the discharge power: how long the battery can "
                        "deliver at full power. Follows from the configuration, not from the "
                        "simulation."),
    ]


def _heat_pump(s: Stromen, systeem: dict) -> list[Kpi]:
    nominaal_kwe = _par(systeem, "heat_pump", "max_capacity_kwe")
    scop = s.hp_warmte / s.hp_elektrisch if s.hp_elektrisch else float("nan")
    # Full load hours are NOT scaled to a year. It is already a sum over the run: the
    # electricity consumption divided by the nominal power gives the number of hours that the
    # machine would have run at full power directly. With a week run you should see the full
    # load hours of that week, not that week times 52.
    vollast = (s.hp_elektrisch / float(nominaal_kwe)) if nominaal_kwe else float("nan")
    jaar = is_jaarrun(s.dagen)
    return [
        Kpi("Full load hours", "h yr⁻¹" if jaar else "h", vollast,
            sleutel="full_load_hours", decimalen=0,
            benchmark=3500, benchmark_tekst="3 500 h yr⁻¹",
            richting="hoger" if jaar else "",
            bron="SDE++ (to be confirmed)",
            toelichting="Electrical consumption divided by the nominal electrical power: the "
                        "number of hours the heat pump would have run at full power. Over the "
                        "period of the run, so with a run shorter than a year not comparable "
                        "with the benchmark, which holds per year. Electrical and not thermal, "
                        "because the power of the heat pump is set electrically in this model "
                        "(min/max_capacity_kwe)."),
        Kpi("Seasonal COP" if jaar else "Average COP", "—", scop,
            sleutel="cop", decimalen=2,
            benchmark=2.5, benchmark_tekst="≥ 2,5", richting="hoger" if jaar else "",
            bron="SDE++ (to be confirmed)",
            toelichting="Total delivered heat divided by the total electrical consumption over "
                        "the run. Not the average of the COP per step: that weighs a step "
                        "without heat demand as heavily as a step at full power. Only with a "
                        "run of a whole year is this a seasonal performance; with a shorter run "
                        "it is the average over that period, and the benchmark does not hold."),
        # The heat share of the heat pump used to be here too, divided by the HEAT DEMAND.
        # It has been removed: next to 'Heat pump share of heat production' with the system KPIs
        # it produced two numbers that seemed to measure the same thing but had a different
        # denominator, and the share-of-demand gives the most distorted picture of the two because
        # the sources make more heat than the greenhouse asks for.
        Kpi("Starts per day", "d⁻¹", s.hp_starts / s.dagen if s.dagen else float("nan"),
            decimalen=2,
            toelichting="The number of times per day the heat pump goes from off to on. Many "
                        "starts means wear; the minimum run time in the configuration bounds "
                        "this."),
    ]


def _gas_boiler(s: Stromen, systeem: dict) -> list[Kpi]:
    # The maximum capacity from the configuration; if it is not there, from the run itself.
    nominaal = _par(systeem, "gas_boiler", "max_capacity_kwth")
    nominaal = float(nominaal) if nominaal else s.gb_maximaal
    geldig = bool(nominaal) and math.isfinite(nominaal)
    jaar = is_jaarrun(s.dagen)
    return [
        # Just as with the heat pump not scaled to a year (K2): it is already a
        # sum over the run.
        Kpi("Full load hours", "h yr⁻¹" if jaar else "h",
            s.gb_warmte / nominaal if geldig else float("nan"),
            sleutel="full_load_hours", decimalen=0,
            toelichting="Delivered heat divided by the maximum thermal power: the number of "
                        "hours the boiler would have run at full power. Thermal and not in "
                        "fuel, because the power of the boiler is set thermally in this model "
                        "(max_capacity_kwth). Over the period of the run; only with a run of a "
                        "year per year."),
        Kpi("Starts per day", "d⁻¹", s.gb_starts / s.dagen if s.dagen else float("nan"),
            decimalen=2,
            toelichting="The number of times per day the boiler goes from off to on. Many "
                        "starts means wear and start losses; the minimum run time in the "
                        "configuration bounds this."),
        Kpi("Average power when running", "kW",
            s.gb_warmte / s.gb_draaiuren if s.gb_draaiuren else float("nan"),
            decimalen=0,
            toelichting="Delivered heat divided by the number of hours the boiler was on. If "
                        "this is close to the minimum power, the boiler runs mostly in part "
                        "load and is generously sized for this demand."),
        Kpi("Share of running time at high power [> 0.9 Qmax]", "%",
            _deel(s.gb_uren_hoog, s.gb_draaiuren),
            decimalen=1,
            toelichting="The part of the running hours in which the boiler delivered above 90 % "
                        "of its maximum power. High means the boiler often sits against its "
                        "limit; near zero means the upper part of its power is rarely needed. "
                        "Weighted by time, not by the number of steps."),
    ]


def _tess_capaciteit(systeem: dict) -> float:
    """The usable capacity of the buffer in kWh, exactly as the model computes it.
    
    From the class TESS itself and not recomputed here: if the formula there ever changes, the
    KPIs compute with the new capacity by themselves.
    """
    try:
        return float(TESS(systeem.get("tess") or {}).capacity_kwh)
    except (KeyError, TypeError, ValueError):
        return float("nan")


def _tess(s: Stromen, systeem: dict) -> list[Kpi]:
    capaciteit = _tess_capaciteit(systeem)
    geldig = bool(capaciteit) and math.isfinite(capaciteit)
    cycli = s.tess_ontladen / capaciteit if geldig else float("nan")
    jaar = is_jaarrun(s.dagen)
    ontladen = _per_jaar(s.tess_ontladen, s.dagen) if jaar else s.tess_ontladen
    return [
        Kpi("Energy discharged", "MWh yr⁻¹" if jaar else "MWh", ontladen / 1000.0,
            sleutel="tess_ontladen", decimalen=1,
            toelichting="The heat the buffer delivered to the greenhouse. Discharged and not "
                        "charged: that is what really comes out, and it is also the quantity "
                        "the cycles below are counted with. The difference with what went in is "
                        "the standing loss. This is not extra heat production: that heat was "
                        "already counted when the boiler or the heat pump made it."),
        Kpi("Cycles per day", "d⁻¹", cycli / s.dagen if s.dagen else float("nan"),
            decimalen=2,
            toelichting="Equivalent full cycles per day: discharged heat divided by the "
                        "capacity, divided by the number of days. The same definition as with "
                        "the battery. The capacity is the usable heat between the minimum and "
                        "the maximum temperature."),
        Kpi("Utilised capacity", "%",
            _deel(s.tess_inhoud_max - s.tess_inhoud_min, capaciteit) if geldig
            else float("nan"),
            decimalen=1,
            toelichting="The difference between the highest and the lowest content in the run, "
                        "as a part of the capacity: how much of the vessel the system really "
                        "uses. If the buffer runs empty somewhere in the run, this equals the "
                        "highest filling. Low means a smaller vessel would have done the same."),
        Kpi("Average fill level", "%",
            _deel(s.tess_inhoud_gem, capaciteit) if geldig else float("nan"),
            decimalen=1,
            toelichting="The average content over the run, as a part of the capacity. 0 % means "
                        "the vessel is at the minimum temperature, not that it is empty: it "
                        "loses heat to its surroundings then too."),
        Kpi("Standing loss", "%", _deel(s.tess_verlies, s.tess_geladen),
            decimalen=1,
            toelichting="The standing loss over the run as a part of all the heat that went "
                        "into it. This is heat the sources did make but that never reaches the "
                        "greenhouse. It can only go above 100 % if the buffer starts filled and "
                        "is hardly charged after that."),
    ]


BEREKENING = {"pv": _pv, "grid": _grid, "bess": _bess, "heat_pump": _heat_pump,
              "gas_boiler": _gas_boiler, "tess": _tess}


def _asset_emissie(sleutel: str, per_asset: dict | None, afbakening: str,
                   jaar: bool) -> Kpi | None:
    """The emission KPI of one asset, or None if there is none for this asset.
    
    Only the boiler, the grid and the heat pump get one: they are the ones that burn gas or
    take electricity. The PV, the battery and the buffer emit nothing in this model; a line
    with 0.0 would only make that look like a measured zero.
    """
    uitstoot = (per_asset or {}).get(sleutel)
    if uitstoot is None:
        return None
    waarde = (uitstoot.per_jaar(afbakening) if jaar else uitstoot.totaal(afbakening)) / 1000
    uitleg = {
        "gas_boiler": "CO2 equivalent of the gas the boiler burned. Unambiguous: there is no "
                      "other gas consumer, so this is exactly the gas part of the system KPI "
                      "'Yearly emissions'.",
        "grid": "CO2 equivalent of all the electricity taken from the grid. This and the boiler "
                "together are exactly the system KPI 'Yearly emissions'.",
        "heat_pump": "The part of the grid offtake attributed to the heat pump, in proportion "
                     "to its consumption per time step (assumption K5). This number is ALREADY "
                     "in the one of the grid, so do not add them up. It is lower than the "
                     "consumption of the heat pump times the factor, because part of its "
                     "electricity came from the PV and that has no emissions in this model.",
    }[sleutel]
    return Kpi("Yearly emissions" if jaar else "Emissions", "t yr⁻¹" if jaar else "t", waarde,
               sleutel="emissies", decimalen=1,
               toelichting=uitleg + " You choose the scope (combustion, chain or total) above; "
                                    "it holds for all emission KPIs at once.")


def asset_kpis(df: pd.DataFrame, systeem: dict, s: Stromen | None = None,
               per_asset: dict | None = None, afbakening: str = "totaal") -> list[Groep]:
    """The KPIs per asset, for the energy system of this run.
    
    An asset that is off in this scenario gets an empty group with aanwezig=False, so that the
    dashboard can show it as 'not in this scenario' instead of with zeros.
    
    per_asset: the breakdown from analysis.emissies.per_asset(). Without that breakdown the
    emission KPIs are simply left out, so that this function also works without emission factors.
    """
    s = s or stromen(df)
    jaar = is_jaarrun(s.dagen)
    groepen = []
    for sleutel, naam in ASSETS:
        aan = _aan(systeem, sleutel)
        if sleutel in NOG_GEEN_KPIS:
            groepen.append(Groep(sleutel, naam, [], aan,
                                 melding="No KPIs have been established for this asset yet."))
        elif not aan:
            groepen.append(Groep(sleutel, naam, [], False,
                                 melding="Not in this scenario."))
        else:
            kpis = BEREKENING[sleutel](s, systeem)
            emissie = _asset_emissie(sleutel, per_asset, afbakening, jaar)
            if emissie is not None:
                kpis.append(emissie)
            groepen.append(Groep(sleutel, naam, kpis, True))
    return groepen


# --------------------------------------------------------------------------
# KPIs at system level
# --------------------------------------------------------------------------

def _economisch(fin: dict | None, s: Stromen, jaar: bool = True) -> list[Kpi]:
    """The two energy bills come from the run itself; the rest from analysis/financieel.py.
    
    """
    fin = fin or {}

    def haal(sleutel: str) -> float:
        waarde = fin.get(sleutel)
        return float("nan") if waarde is None else float(waarde)

    alleen_jaar = "Only with a run of a whole year: this formula sets a yearly amount against the investment, so a shorter run gives no meaningful number."
    gas = _per_jaar(s.gaskosten, s.dagen) if jaar else s.gaskosten
    elektra = _per_jaar(s.elektrakosten, s.dagen) if jaar else s.elektrakosten
    return [
        Kpi("Gas costs", "EUR yr⁻¹" if jaar else "EUR", gas,
            sleutel="gaskosten", decimalen=0,
            toelichting="What was settled for gas in this run: the fuel of the boiler times the "
                        "gas price. The price is a fixed amount per cubic metre from "
                        "project.json, converted to euro per kWh with the higher heating value. "
                        "No standing charge, no capacity tariff and no tax brackets."),
        Kpi("Electricity costs", "EUR yr⁻¹" if jaar else "EUR", elektra,
            sleutel="elektrakosten", decimalen=0,
            toelichting="What was settled for electricity in this run: the grid offtake times "
                        "the price of that hour. Only the bare energy price, so without "
                        "transport tariff, standing charge or energy tax. Own PV costs nothing "
                        "and therefore does not count here; a negative price gives a negative "
                        "contribution."),
        Kpi("Annual energy saving" if jaar else "Energy saving",
            "EUR yr⁻¹" if jaar else "EUR", haal("besparing_per_jaar" if jaar else "besparing_periode"),
            sleutel="besparing", decimalen=0,
            toelichting="Saving on energy against the chosen reference, over the period of the "
                        "run. See the Financial page for the choice of reference."),
        Kpi("Levelised cost of energy", "EUR MWh⁻¹",
            haal("lcoe") if jaar else float("nan"), decimalen=2,
            toelichting="Empty as long as no discount rate and horizon have been chosen. See "
                        "the Financial page; without those two an LCOE is not an LCOE. "
                        + alleen_jaar),
        Kpi("Capital expenditure", "EUR", haal("investering"), decimalen=0,
            toelichting="The difference between the basis and this system, added up per asset. "
                        "Does not depend on the length of the run."),
        Kpi("Operational expenditure", "EUR yr⁻¹", haal("opex_extra"), decimalen=0,
            toelichting="The extra fixed yearly costs against the basis. Comes from the cost "
                        "formulas and not from the run, so this is a real yearly amount "
                        "regardless of the length of the run."),
        Kpi("Payback period", "yr", haal("terugverdientijd") if jaar else float("nan"),
            decimalen=1,
            toelichting="Investment divided by the yearly saving minus the extra OPEX. Without "
                        "discounting, inflation or residual value. " + alleen_jaar),
    ]


# The breakdown per source under Emissions: key in per_asset -> (name, explanation).
UITSPLITSING = {
    "gas_boiler": ("Gas boiler emissions",
                   "The gas the boiler burned. There is no other gas consumer, so this is "
                   "exactly the gas part of the emissions above."),
    "grid": ("Grid emissions",
             "All the electricity taken from the grid. This and the boiler together are exactly "
             "the emissions above; the heat pump below is ALREADY in this."),
    "heat_pump": ("Heat pump emissions",
                  "The part of the grid offtake attributed to the heat pump, in proportion to "
                  "its consumption per time step (assumption K5). This number is already in the "
                  "one of the grid, so do not add them up. It is lower than the consumption of "
                  "the heat pump times the emission factor, because part of its electricity "
                  "came from the PV and that has no emissions in this model."),
}


def _emissies(uitstoot, referentie, afbakening: str = "totaal", jaar: bool = True,
              per_asset: dict | None = None) -> list[Kpi]:
    """The emission KPIs. uitstoot and referentie are analysis.emissies.Uitstoot or None.
    
    On a year run scaled to a year, otherwise the total over the run. The number is real in
    both cases; only the word 'yearly' and the unit per year are reserved for a run that
    really covers a year.
    
    per_asset is the breakdown from analysis.emissies.per_asset(). Those same numbers are also
    with the assets themselves; they are here once more so that you see the origin of the
    emissions at a glance without going through three pages. They do NOT add up to the total,
    because the heat pump is a part of the grid.
    """
    def ton(u):
        return (u.per_jaar(afbakening) if jaar else u.totaal(afbakening)) / 1000  # kg -> tonnes

    if uitstoot is None:
        emissie = vermeden = float("nan")
    else:
        emissie = ton(uitstoot)
        vermeden = float("nan") if referentie is None else ton(referentie) - emissie
    return [
        Kpi("Yearly emissions" if jaar else "Emissions", "t yr⁻¹" if jaar else "t", emissie,
            sleutel="emissies", decimalen=1,
            toelichting="CO2 equivalent of the grid offtake and the burned gas, over the period "
                        "of the run. Curtailed PV does not count: that electricity never "
                        "existed."),
        Kpi("Yearly emissions avoided" if jaar else "Emissions avoided",
            "t yr⁻¹" if jaar else "t", vermeden,
            sleutel="emissies_vermeden", decimalen=1, richting="hoger",
            toelichting="Against the measured existing situation over the same period. Positive "
                        "is less emission than now. NOTE the same skew as with the costs on the "
                        "Financial page: the model takes the measured gas series as HEAT DEMAND "
                        "(assumption B4) and burns gas/efficiency for it, while the measured "
                        "series is already purchased gas. The simulated side therefore burns "
                        "about 11 % more gas for the same heat than the measured side, and the "
                        "avoided emission comes out too low because of it. If you want to see "
                        "only the effect of the assets, put two runs from the same model chain "
                        "beside each other with run B above."),
    ] + [
        Kpi(naam, "t yr⁻¹" if jaar else "t",
            ton((per_asset or {})[sleutel]) if (per_asset or {}).get(sleutel) is not None
            else float("nan"),
            sleutel=f"emissies_{sleutel}", decimalen=1, toelichting=uitleg)
        for sleutel, (naam, uitleg) in UITSPLITSING.items()
    ]


def _energie(s: Stromen, uitstoot, oppervlak_m2: float | None,
             jaar: bool = True) -> list[Kpi]:
    # The gas volume comes from the fuel of the boiler and not from the emissions module, so
    # that these two KPIs are there as well if the emissions computation could not run. The
    # same conversion as there: the higher heating value from utils/constants.py.
    from analysis.emissies import kuub_uit_kwh
    gas_m3 = float(kuub_uit_kwh(s.gb_brandstof))
    gas = _per_jaar(gas_m3, s.dagen) if jaar else gas_m3
    per_m2 = (gas / oppervlak_m2) if oppervlak_m2 else float("nan")
    # SELF-SUFFICIENCY OVER THE INPUT, not over the demand (assumption K3).
    #
    # Denominator: everything that enters the site as energy, so own sun plus purchased
    # electricity plus purchased gas. Numerator: what of that was generated on site.
    #
    # The BATTERY IS DELIBERATELY NOT IN IT. It generates nothing, it only shifts in time. If
    # it were in the numerator, as used to be the case, then PV that runs through the battery
    # would be counted twice -- once as pv_benut and again as bess_ontladen -- and this KPI
    # would go up from a larger battery without a single kWh being added. With a generous
    # battery it could even come out above 100 %.
    #
    # The HEAT PUMP is in it on the input side only for the same reason: its electricity is in
    # the denominator, its heat is not. Otherwise the same energy counts twice, first as
    # electricity and then as heat.
    #
    # That way the denominator moreover stays one kind of quantity: purchased and self
    # generated kWh, without adding kWh of heat to kWh of electricity.
    eigen = s.pv_benut
    invoer = s.pv_benut + s.netafname + s.gb_brandstof
    bronnen = s.gb_warmte + s.hp_warmte      # everything that has been MADE in terms of heat

    # The electrical side gets the same treatment as the heat above: the shares are divided by
    # what COMES IN in terms of electricity, not by the demand. PV and grid together are
    # everything that enters the site, so the two shares add up to 100 %. The battery is
    # deliberately not among them; it delivers nothing, it only shifts in time, and if you
    # counted its discharge the same kWh would count twice.
    #
    # The input is therefore larger than the demand: the difference is the losses of the
    # battery. A share of the DEMAND would therefore not come out at 100 %.
    aanvoer = s.pv_benut + s.netafname
    e_vraag_totaal = s.e_vraag + s.hp_elektrisch
    aanvoer_totaal = _per_jaar(aanvoer, s.dagen) if jaar else aanvoer
    warmte = _per_jaar(s.warmtevraag, s.dagen) if jaar else s.warmtevraag
    elektra = _per_jaar(e_vraag_totaal, s.dagen) if jaar else e_vraag_totaal
    return [
        Kpi("Total heat demand", "MWh yr⁻¹" if jaar else "MWh", warmte / 1000.0,
            sleutel="warmtevraag", decimalen=0,
            toelichting="The heat demand of the greenhouse over the period of the run: what the "
                        "model computes from weather data and greenhouse parameters and imposes "
                        "on EVERY scenario of this case. Emphatically not the heat the boiler "
                        "and the heat pump made; that is higher, because the standing loss of "
                        "the buffer is paid out of it as well."),
        Kpi("Peak heat demand", "kW_th", s.warmte_piek, decimalen=0,
            toelichting="The highest heat demand at any moment in the run. Decisive for the "
                        "boiler and heat pump capacity, but note: the buffer can absorb a peak, "
                        "so the sources do not have to reach this power. A TMY moreover "
                        "contains no design winter; for the real peak a run on a cold year "
                        "(ECY) belongs beside it."),
        Kpi("Heat pump share of heat production", "%", _deel(s.hp_warmte, bronnen),
            toelichting="Heat from the heat pump as a part of everything the boiler and the "
                        "heat pump made together. In other words: which part of the heat supply "
                        "is electrified. The standing loss of the buffer is divided "
                        "proportionally over both sources; that is the only assumption in this, "
                        "and with a loss of around 1,5 % of the production it does not shift "
                        "the answer noticeably."),
        Kpi("Gas boiler share of heat production", "%", _deel(s.gb_warmte, bronnen),
            toelichting="Heat from the boiler as a part of everything the boiler and the heat "
                        "pump made together. This and the share of the heat pump add up to 100 "
                        "%."),
        Kpi("Total electricity demand", "MWh yr⁻¹" if jaar else "MWh", elektra / 1000.0,
            sleutel="e_vraag_totaal", decimalen=0,
            toelichting="What the DEVICES ask for in electricity: the installations of the "
                        "greenhouse plus the heat pump. This is the useful demand, so without "
                        "the losses of the battery. It is therefore slightly below 'Electricity "
                        "use' below, which counts what comes onto the site. The difference "
                        "between those two is exactly what charging and discharging the battery "
                        "cost; without a battery they are equal. Also not to be confused with "
                        "the grid offtake, because the own PV is in this one."),
        Kpi("Peak electricity demand", "kW_e", s.e_piek, decimalen=0,
            toelichting="The highest simultaneous consumption of devices and heat pump "
                        "together. This is what the system has to deliver INTERNALLY; what "
                        "comes off the grid is at the connection under 'Peak import' and is "
                        "lower as soon as PV or battery help out."),
        Kpi("PV share of electricity supply", "%", _deel(s.pv_benut, aanvoer),
            toelichting="The part of all electricity that comes onto the site that comes from "
                        "the own PV. NOTE: this is something other than 'PV share of "
                        "electricity demand' at the PV installation itself, which divides by "
                        "the demand of the devices. This one divides by the SUPPLY, so that "
                        "this and the grid share together are exactly 100 %."),
        Kpi("Grid share of electricity supply", "%", _deel(s.netafname, aanvoer),
            toelichting="The part of all electricity that comes onto the site that was "
                        "purchased. This and the PV share add up to 100 %. The battery counts "
                        "on neither side: it generates nothing but shifts in time."),
        Kpi("Gas use", "m³ yr⁻¹" if jaar else "m³", gas,
            sleutel="gas_m3", decimalen=0,
            toelichting="The burned gas in standard cubic metres, over the period of the run. "
                        "Comes from the fuel in kWh of the boiler, converted with the HIGHER "
                        "heating value (9,769 kWh/m³), because that is how Dutch natural gas is "
                        "settled and that is also how the model converts the gas price."),
        Kpi("Electricity use", "MWh yr⁻¹" if jaar else "MWh", aanvoer_totaal / 1000.0,
            sleutel="e_aanvoer", decimalen=0,
            toelichting="All electricity that comes onto the site: grid offtake plus the own PV "
                        "that was used. The electrical counterpart of 'Gas use' above, so the "
                        "energy that goes INTO the company, before anything is lost. It is "
                        "therefore slightly above 'Total electricity demand', and the "
                        "difference is what the battery costs in charging and discharging loss. "
                        "The battery itself does not count as supply: it generates nothing, it "
                        "only shifts in time."),
        Kpi("Gas use per area", "m³ m⁻² yr⁻¹" if jaar else "m³ m⁻²", per_m2,
            sleutel="gas_per_m2", decimalen=2,
            benchmark_tekst="30–40 m³ m⁻² yr⁻¹ (sector)",
            toelichting="Burned gas per square metre of greenhouse, over the period of the run. "
                        "With a shorter run therefore not comparable with the sector average, "
                        "which holds per year — a winter week is supposed to come out high "
                        "here, and that is information. NOTE: a system that electrifies heat "
                        "scores well here for the wrong reason; read this KPI together with "
                        "'Share of heat supply per source'."),
        # The shares are divided by what the SOURCES made together, not by the heat demand of the
        # greenhouse. That way they add up to exactly 100 % and no assumption is needed about
        # which part of the buffer comes from the boiler and which part from the heat pump: all
        # heat that the greenhouse ever gets was made by one of those two, and the buffer makes
        # nothing. Dividing by the demand would give three numbers that together come out above
        # 100 % -- the buffer then counts double, and the sources make more than the demand anyway
        # because the standing loss of the buffer is paid out of it too.
        Kpi("Electricity cycled through battery", "%",
            _deel(s.bess_ontladen, e_vraag_totaal),
            toelichting="The part of the electricity demand that was delivered through the "
                        "battery. The electrical counterpart of 'Heat cycled through storage' "
                        "below: discharged energy divided by the demand of devices and heat "
                        "pump together. It says how often the system shifts electricity in "
                        "time, not how much it generates; the battery adds nothing."),
        Kpi("Heat cycled through storage", "%", _deel(s.tess_ontladen, s.warmtevraag),
            toelichting="What the buffer gave off, divided by the heat demand of the "
                        "greenhouse: how much of the demand ran through the buffer. "
                        "Emphatically NOT a fourth heat source -- that heat was made earlier by "
                        "the boiler or the heat pump and is already counted there. It can go "
                        "above 100 % if the buffer is cycled more than once a day."),
        Kpi("Total self sufficiency", "%", _deel(eigen, invoer),
            toelichting="The part of all the energy the company uses that was generated on the "
                        "site: used PV divided by used PV plus grid offtake plus the gas of the "
                        "boiler, all in kWh. So over the WHOLE energy demand and not only the "
                        "electrical one, so that it is visible how self-sufficient the company "
                        "really is. The battery does not count as own generation: it shifts "
                        "energy in time and makes none. As long as only gas is burned this "
                        "number is low, because the gas dominates the denominator; electrifying "
                        "the heat makes it rise, and if heat is ever generated on site, that is "
                        "added to the numerator."),
    ]


def _modelgeldigheid(s: Stromen, solver: pd.DataFrame | None) -> list[Kpi]:
    """The KPIs that say whether you may believe the rest of the table.
    
    The solver log calls its status column 'termination' (values such as 'optimal' and
    'maxTimeLimit'), not 'status'. Both names are below, so that an older or newer log works
    too.
    """
    gap, niet_optimaal, solves = float("nan"), float("nan"), 0
    if solver is not None and len(solver):
        solves = len(solver)
        if "relative_gap" in solver.columns:
            gaps = pd.to_numeric(solver["relative_gap"], errors="coerce").dropna()
            if len(gaps):
                gap = float(gaps.max()) * 100
        for kolom in ("termination", "status"):
            if kolom in solver.columns:
                afwijkend = solver[kolom].astype(str).str.strip().str.lower() != "optimal"
                niet_optimaal = float(afwijkend.sum())
                break
    # NOT scaled to a year, not even on a year run. This is a validity check and not a
    # performance measure: the question is whether any demand was left unserved, and how much
    # in this run. A yearly average only makes that answer vaguer, and on short test runs this
    # is exactly the number you want to see.
    onbediend = s.onbediende_warmte + s.onbediende_elektriciteit
    return [
        Kpi("Worst MIP gap", "%", gap, decimalen=3, benchmark=0.0, richting="lager",
            benchmark_tekst="0 %", marge=1e-4,
            toelichting="The largest relative gap of all the optimisations in this run. Zero "
                        "means every optimisation was solved provably optimal; a gap greater "
                        "than zero means a better plan may exist that the solver did not find "
                        "within the time limit. This is the WORST of the run, not the average: "
                        "one day with a large gap makes the comparison with another run "
                        "unreliable at that point."),
        Kpi("Solves not optimal", f"of {solves}" if solves else "—", niet_optimaal,
            decimalen=0, benchmark=0.0, richting="lager", benchmark_tekst="0",
            toelichting="The number of optimisations that did not end in 'optimal', usually "
                        "because the time limit was reached. Those outcomes depend on the speed "
                        "of the computer and are not reproducible."),
        Kpi("Unserved energy demand", "kWh", onbediend, decimalen=0, benchmark=0.0,
            richting="lager", benchmark_tekst="0", marge=1e-3,
            bron="Modelvereiste",
            toelichting="Demand that could not be met at some moment, heat and electricity "
                        "together, as a total over the run. Should be zero: if it is not, the "
                        "system does not meet the demand and the other KPIs cannot be compared "
                        "with a system that does. Never scaled to a year, because the judgement "
                        "is zero or non-zero and that does not change with the length of the "
                        "run."),
    ]


def systeem_kpis(df: pd.DataFrame, systeem: dict, uitstoot=None, referentie=None,
                 fin: dict | None = None, solver: pd.DataFrame | None = None,
                 oppervlak_m2: float | None = None, afbakening: str = "totaal",
                 s: Stromen | None = None, per_asset: dict | None = None) -> list[Groep]:
    """The KPIs for the system as a whole, in four themes."""
    s = s or stromen(df)
    jaar = is_jaarrun(s.dagen)
    return [
        Groep("economisch", "Economic", _economisch(fin, s, jaar)),
        Groep("emissies", "Emissions",
              _emissies(uitstoot, referentie, afbakening, jaar, per_asset)),
        Groep("energie", "Energy", _energie(s, uitstoot, oppervlak_m2, jaar)),
        Groep("geldigheid", "Model validity", _modelgeldigheid(s, solver)),
    ]
