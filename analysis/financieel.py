"""Investment, maintenance and payback time of a simulated energy system. (This script is not fully in use yet)

Post-processing: this runs after a run and changes nothing in the model. Everything that
is needed is already in the run folder: run_info.json 

The formulas are not here but in
    data/economic/asset_capex/capex.py   investment per asset + lifetime
    data/economic/asset_opex/opex.py     fixed annual costs per asset
so that you can change them without having to go into the code.

What is computed here:
    investment    per asset max(0, CAPEX(scenario) - CAPEX(basis)). Only the difference
                  with the existing system is an investment; scaling back yields nothing
                  and costs nothing. An asset that was not in the basis has a basis CAPEX of 0.
    OPEX          of the whole new system AND of the basis; the difference is what it costs
                  you extra per year.
    saving        the energy costs of the basis minus those of the run, over the period of
                  the run, then scaled linearly to a year.
    payback time  investment / (saving per year - extra OPEX per year).
    max. investment   what you would be allowed to invest at most to break even within the
                  lifetime: (saving - extra OPEX) x lifetime.

Without discounting, inflation or residual value: this is a first weighing, not a business case.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from config.asset_catalogus import CATALOGUS
from utils.projectinstellingen import lees_json
from utils.projectpaden import ROOT

DAGEN_PER_JAAR = 365
BESTAND_CAPEX = "data/economic/asset_capex/capex.py"
BESTAND_OPEX = "data/economic/asset_opex/opex.py"

# The order in which the assets appear in the table.
ASSETS = ("tess", "heat_pump", "bess", "pv", "grid", "gas_boiler")

# Which size of an asset ends up in the table. Only for display: the costs come from the
# formula files. Every function returns a list of (number, unit).
MAATGEVEND = {
    "tess": lambda a: [(a.get("volume_m3", 0.0), "m³")],
    # The heat pump is filled in electrically; the thermal capacity differs per hour with the
    # COP (D6). The table therefore shows the compressor size, the number that is fixed.
    "heat_pump": lambda a: [(a.get("max_capacity_kwe", 0.0), "kWe")],
    "bess": lambda a: [(a.get("capacity_kwh", 0.0), "kWh"),
                       (max(a.get("capacity_in_kw", 0.0), a.get("capacity_out_kw", 0.0)), "kW")],
    # PV: the peak power is not filled in separately anywhere, it follows from the panels. If
    # there are no panels, the list stays empty and the page shows 'on' (measured profile).
    "pv": lambda a: ([(sum(a.get(f"vlak{i}_panelen", 0.0) for i in range(1, 5)), "panelen"),
                      (sum(a.get(f"vlak{i}_panelen", 0.0) for i in range(1, 5))
                       * (a.get("oppervlak_paneel_m2") or 1.9) * (a.get("eta_pv") or 0.20), "kWp")]
                     if sum(a.get(f"vlak{i}_panelen", 0.0) for i in range(1, 5)) else []),
    "grid": lambda a: [(a.get("max_import_kw", 0.0), "kW")],
    "gas_boiler": lambda a: [(a.get("max_capacity_kwth", 0.0), "kWth")],
}


class FormuleFout(ValueError):
    """Something is wrong with one of the formula files in data/economic/."""


def _laad(naam: str) -> ModuleType:
    """Reading in a formula file and executing it, without importing it.
    
    Deliberately no 'import': Python reads a module only once and keeps the compiled version
    in __pycache__, so that the dashboard would keep showing the old numbers after a formula
    is changed until you restart Streamlit. Reading like this is always exactly what is in
    the file right now. An error in such a file becomes a FormuleFout here, so that a
    readable message appears on the Financial page instead of a crash.
    """
    pad = ROOT / naam
    try:
        bron = pad.read_text(encoding="utf-8")
    except OSError as fout:
        raise FormuleFout(f"{naam} cannot be read: {fout}") from fout
    module = ModuleType(pad.stem)
    module.__file__ = str(pad)
    try:
        exec(compile(bron, str(pad), "exec"), module.__dict__)  # noqa: S102 - own data file
    except Exception as fout:  # noqa: BLE001 - anything in that file can be broken
        raise FormuleFout(f"{naam} cannot be loaded: "
                          f"{type(fout).__name__}: {fout}") from fout
    return module


def formules() -> tuple[ModuleType, ModuleType]:
    """The two formula files from data/economic/, fresh from disk every time."""
    return _laad(BESTAND_CAPEX), _laad(BESTAND_OPEX)


def _parameters(instelling: dict) -> dict:
    """The parameters of an asset as the formulas get them: empty (null) becomes 0."""
    return {naam: (0.0 if waarde is None else waarde)
            for naam, waarde in instelling.items() if naam != "enabled"}


def _aan(instelling: dict | None) -> bool:
    return bool(instelling) and bool(instelling.get("enabled"))


def _getal(waarde, asset: str, bestand: str) -> float:
    if isinstance(waarde, bool) or not isinstance(waarde, (int, float)):
        raise FormuleFout(f"The formula for '{asset}' in {bestand} does not return a number "
                          f"but {type(waarde).__name__}.")
    if not math.isfinite(waarde) or waarde < 0:
        raise FormuleFout(f"The formula for '{asset}' in {bestand} returns {waarde}; "
                          "that has to be 0 or more.")
    return float(waarde)


def _roep(module, asset: str, bestand: str, *argumenten) -> float:
    functie = getattr(module, asset, None)
    if not callable(functie):
        raise FormuleFout(f"In {bestand} there is no function '{asset}'. Every asset needs its "
                          f"own function there; see the other assets in that file.")
    try:
        waarde = functie(*argumenten)
    except FormuleFout:
        raise
    except Exception as fout:  # noqa: BLE001 - this is a file that the user writes themselves
        raise FormuleFout(f"The formula for '{asset}' in {bestand} gave an error: "
                          f"{type(fout).__name__}: {fout}") from fout
    return _getal(waarde, asset, bestand)


def capex_asset(module, asset: str, instelling: dict | None) -> float:
    """The new price of this asset as it is set. 0 if the asset is off.
    
    Off means 0 and not 'the formula with zeros': a formula with a fixed term, such as the
    30,000 of the battery, should not apply to an asset that is not there.
    """
    if not _aan(instelling):
        return 0.0
    return _roep(module, asset, BESTAND_CAPEX, _parameters(instelling))


def opex_asset(module, asset: str, instelling: dict | None, capex: float) -> float:
    """The fixed annual costs of this asset. 0 if the asset is off."""
    if not _aan(instelling):
        return 0.0
    return _roep(module, asset, BESTAND_OPEX, _parameters(instelling), capex)


def levensduur_asset(module, asset: str) -> float:
    tabel = getattr(module, "LEVENSDUUR_JAAR", None)
    if not isinstance(tabel, dict) or asset not in tabel:
        raise FormuleFout(f"In {BESTAND_CAPEX} there is no lifetime for '{asset}' "
                          "in LEVENSDUUR_JAAR.")
    waarde = tabel[asset]
    if not isinstance(waarde, (int, float)) or isinstance(waarde, bool) or waarde <= 0:
        raise FormuleFout(f"The lifetime of '{asset}' in {BESTAND_CAPEX} is {waarde!r}; "
                          "that has to be a number greater than 0.")
    return float(waarde)


@dataclass(frozen=True)
class Regel:
    """One asset: what was there, what is coming, and what that costs."""
    asset: str
    label: str
    maat_basis: list[tuple[float, str]]
    maat_nieuw: list[tuple[float, str]]
    aan_basis: bool
    aan_nieuw: bool
    capex_basis: float
    capex_nieuw: float
    opex_basis: float
    opex_nieuw: float
    levensduur: float

    @property
    def investering(self) -> float:
        """Only the difference, and never negative: scaling back does not yield money."""
        return max(0.0, self.capex_nieuw - self.capex_basis)

    @property
    def opex_extra(self) -> float:
        return self.opex_nieuw - self.opex_basis

    @property
    def kleiner(self) -> bool:
        """This asset becomes smaller or disappears. Costs 0, but worth reporting."""
        return self.capex_nieuw < self.capex_basis - 0.005


def _pv_blijft_liggen(asset: str, basis: dict, nieuw: dict) -> bool:
    """Is the PV of this scenario on the measured profile while the basis has panels?
    
    With no panels on any plane the model does not compute the generation from the weather
    data but replays the measured profile (inputs/zon.py). That generation comes from the
    field that is already there, so that field is still there in this scenario: nothing is
    bought, but nothing is removed either. For the money the layout of the basis therefore
    counts. Without this exception the maintenance of those panels would be booked as an
    annual saving while they are still simply on the roof.
    
    This is the only asset with such a double meaning of 'empty'.
    """
    if asset != "pv" or not (_aan(basis) and _aan(nieuw)):
        return False
    from inputs.zon import vlakken_van
    return not vlakken_van(nieuw) and bool(vlakken_van(basis))


def regels(basis: dict, nieuw: dict) -> list[Regel]:
    """Per asset the investment and the OPEX, from the two formula files."""
    capexmodule, opexmodule = formules()
    uitkomst = []
    for asset in ASSETS:
        instelling_basis, instelling_nieuw = basis.get(asset) or {}, nieuw.get(asset) or {}
        # What the formulas get as 'new'. Only for PV can that be the basis.
        voor_geld = (instelling_basis
                     if _pv_blijft_liggen(asset, instelling_basis, instelling_nieuw)
                     else instelling_nieuw)
        capex_basis = capex_asset(capexmodule, asset, instelling_basis)
        capex_nieuw = capex_asset(capexmodule, asset, voor_geld)
        maat = MAATGEVEND.get(asset, lambda a: [])
        uitkomst.append(Regel(
            asset=asset,
            label=CATALOGUS.get(asset, {}).get("label", asset),
            maat_basis=maat(_parameters(instelling_basis)) if _aan(instelling_basis) else [],
            maat_nieuw=maat(_parameters(instelling_nieuw)) if _aan(instelling_nieuw) else [],
            aan_basis=_aan(instelling_basis),
            aan_nieuw=_aan(instelling_nieuw),
            capex_basis=capex_basis,
            capex_nieuw=capex_nieuw,
            opex_basis=opex_asset(opexmodule, asset, instelling_basis, capex_basis),
            opex_nieuw=opex_asset(opexmodule, asset, voor_geld, capex_nieuw),
            levensduur=levensduur_asset(capexmodule, asset),
        ))
    return uitkomst


def investering(regels_: list[Regel]) -> float:
    return sum(r.investering for r in regels_)


def opex_extra(regels_: list[Regel]) -> float:
    return sum(r.opex_extra for r in regels_)


def kortste_levensduur(regels_: list[Regel]) -> tuple[float, str] | None:
    """The shortest lifetime of the assets that are actually invested in.
    
    That determines how long your system as a whole can last without anything having to be
    replaced, and is therefore the fair term for 'breaking even within the lifetime'.
    """
    investeringen = [r for r in regels_ if r.investering > 0]
    if not investeringen:
        return None
    kortste = min(investeringen, key=lambda r: r.levensduur)
    return kortste.levensduur, kortste.label


def dagen(df) -> float:
    """The number of days of data in a dispatch table, from the step length itself."""
    if "dt" in df:
        return float(df["dt"].sum()) / 24
    return len(df) / 288


@dataclass(frozen=True)
class Uitkomst:
    """The financial weighing of one run. Amounts in euros, times in years."""
    investering: float
    opex_extra_jaar: float
    kosten_basis: float   # energy costs of the existing system over the period
    kosten_nieuw: float   # energy costs of the run over the same period
    dagen: float
    levensduur: float

    @property
    def besparing_periode(self) -> float:
        return self.kosten_basis - self.kosten_nieuw

    @property
    def factor_jaar(self) -> float:
        """The factor that scales the period up to a year. Linear: a run of one cold
        month thus becomes a year of cold months, so this overestimates the saving
        if the run does not cover a whole year."""
        return DAGEN_PER_JAAR / self.dagen if self.dagen > 0 else float("nan")

    @property
    def besparing_jaar(self) -> float:
        return self.besparing_periode * self.factor_jaar

    @property
    def netto_jaar(self) -> float:
        """What you have left over per year: the saving on energy minus the extra OPEX."""
        return self.besparing_jaar - self.opex_extra_jaar

    @property
    def terugverdientijd(self) -> float | None:
        """In years. None if nothing is left over per year: then there is no payback time."""
        if not math.isfinite(self.netto_jaar) or self.netto_jaar <= 0:
            return None
        return self.investering / self.netto_jaar

    @property
    def max_investering(self) -> float:
        """The maximum the investment may be in order to break even within the lifetime.

        The extra OPEX is that of the simulated system; it does not change along here.
        With an OPEX that is a percentage of the investment, a larger investment would
        also give more OPEX - that is a refinement for later.
        """
        return max(0.0, self.netto_jaar) * self.levensduur

    @property
    def ruimte(self) -> float:
        """Positive: it fits within the lifetime. Negative: this much too expensive."""
        return self.max_investering - self.investering


def systemen(runmap: Path | str) -> tuple[dict, dict] | None:
    """(basis, simulated system) as recorded in run_info.json of this run.
    
    None if those are not there: hierarchy runs and runs from before this version do not
    record the energy system, and then no reliable investment can be computed.
    """
    pad = Path(runmap) / "run_info.json"
    try:
        info = lees_json(pad)
    except (OSError, ValueError):
        return None
    basis, nieuw = info.get("basis"), info.get("energiesysteem")
    if not isinstance(basis, dict) or not isinstance(nieuw, dict):
        return None
    return basis, nieuw


# --------------------------------------------------------------------------
# Levelised cost of energy
# --------------------------------------------------------------------------
#
# The LCOE deliberately sits apart from Uitkomst above. That one computes without
# discounting, and for a payback time that is a defensible simplification. For an LCOE it
# is not: without a discount rate an LCOE is not an LCOE, but an average cost price.
#
# THREE CHOICES THAT HAVE BEEN MADE HERE, AND WHY
#
# 1. THE FULL CAPEX, NOT THE INVESTMENT. Uitkomst.investering is the DIFFERENCE with the
# basis; that is the right number for a payback time ("what do I have to put down"),
# but the wrong one for an LCOE. An LCOE divides the costs of the WHOLE system by the
# energy that that whole system delivers. If you compute with the difference only, a
# scenario that adds little gets an artificially low LCOE, while it leans on an
# existing boiler that is not in the numerator.
#
# 2. HEAT AND ELECTRICITY TOGETHER IN THE DENOMINATOR. Otherwise a system with and without
# a heat pump cannot be compared: the heat pump shifts energy from gas to electricity,
# and with a denominator of electricity only that would drive the LCOE up while nothing
# gets worse. The denominator is the DEMAND of the greenhouse, not what the assets
# produce: buffer and battery move energy and do not make any, so they would count twice.
#
# 3. NO NUMBER WITHOUT A DISCOUNT RATE AND A HORIZON. If one of the two is missing, this
# returns nan with the reason alongside. A silent default of for example 5 % would end
# up in the thesis as a choice that nobody made.

STANDAARD_DISCONTERINGSVOET: float | None = None   # for example 0.05 for 5 %; None = not chosen yet
STANDAARD_HORIZON_JAAR: float | None = None        # for example 15; None = not chosen yet


def kapitaalfactor(disconteringsvoet: float, horizon_jaar: float) -> float:
    r"""The annuity factor (capital recovery factor).
    
    $$\mathrm{CRF} = \frac{r\,(1+r)^{n}}{(1+r)^{n}-1}$$
    
    with $r$ the discount rate and $n$ the horizon in years. At $r = 0$ that expression
    runs into $0/0$; the limit is $1/n$, that is, the investment spread evenly over the
    years.
    """
    if horizon_jaar <= 0:
        raise ValueError("The horizon of an LCOE has to be greater than zero years.")
    if disconteringsvoet < 0:
        raise ValueError("A negative discount rate does not belong here.")
    if disconteringsvoet == 0:
        return 1.0 / horizon_jaar
    groei = (1 + disconteringsvoet) ** horizon_jaar
    return disconteringsvoet * groei / (groei - 1)


@dataclass(frozen=True)
class Lcoe:
    """The LCOE of a run, with its components alongside so that it can be checked."""
    waarde_eur_per_mwh: float
    reden: str                  # empty if there is a number; otherwise why not
    capex_totaal: float
    opex_totaal_jaar: float
    energiekosten_jaar: float
    energie_mwh_jaar: float
    disconteringsvoet: float | None
    horizon_jaar: float | None
    crf: float

    @property
    def bekend(self) -> bool:
        return math.isfinite(self.waarde_eur_per_mwh)


def geleverde_energie_mwh(df) -> float:
    """The energy that the greenhouse demanded in this run: heat plus electricity, in MWh.
    
    The DEMAND and not the production, see choice 2 above. Unserved demand does not count:
    it was not delivered. In a valid run it is zero.
    """
    dt = df["dt"] if "dt" in df else 5 / 60
    vraag = 0.0
    for kolom in ("initial_heat_demand_kw", "initial_e_demand_kw"):
        if kolom in df:
            vraag += float((df[kolom].fillna(0) * dt).sum())
    for kolom in ("heat_balance_residual_kw", "e_balance_residual_kw"):
        if kolom in df:
            vraag -= float((df[kolom].fillna(0).clip(lower=0) * dt).sum())
    return vraag / 1000


def lcoe(regels_: list[Regel], df, kosten_nieuw: float,
         disconteringsvoet: float | None = None,
         horizon_jaar: float | None = None) -> Lcoe:
    r"""The levelised cost of energy of the simulated system, in euro per MWh.
    
    $$\mathrm{LCOE} = \frac{\mathrm{CAPEX}\cdot\mathrm{CRF} + \mathrm{OPEX}_{\text{jaar}}
                             + K_{\text{energie,jaar}}}{E_{\text{geleverd,jaar}}}$$
    
    with CAPEX the full investment value of the system, OPEX the fixed annual costs,
    $K_{\text{energie}}$ the variable energy costs (gas and purchased electricity) and
    $E_{\text{geleverd}}$ the heat and electricity demand of the greenhouse, all per year.
    
    Without a discount rate or a horizon no number comes out, but nan with the reason alongside.
    """
    voet = disconteringsvoet if disconteringsvoet is not None else STANDAARD_DISCONTERINGSVOET
    horizon = horizon_jaar if horizon_jaar is not None else STANDAARD_HORIZON_JAAR

    capex = sum(r.capex_nieuw for r in regels_)
    opex = sum(r.opex_nieuw for r in regels_)
    dgn = dagen(df)
    schaal = DAGEN_PER_JAAR / dgn if dgn > 0 else float("nan")
    energie = geleverde_energie_mwh(df) * schaal
    variabel = kosten_nieuw * schaal

    ontbreekt = []
    if voet is None:
        ontbreekt.append("disconteringsvoet")
    if horizon is None:
        ontbreekt.append("horizon")
    if ontbreekt:
        return Lcoe(float("nan"), f"No {' and no '.join(ontbreekt)} chosen yet.",
                    capex, opex, variabel, energie, voet, horizon, float("nan"))
    if not math.isfinite(energie) or energie <= 0:
        return Lcoe(float("nan"), "This run delivers no energy, so there is nothing to "
                    "spread the costs over.", capex, opex, variabel, energie,
                    voet, horizon, float("nan"))

    crf = kapitaalfactor(float(voet), float(horizon))
    waarde = (capex * crf + opex + variabel) / energie
    return Lcoe(waarde, "", capex, opex, variabel, energie, float(voet), float(horizon), crf)


def voor_kpi(u: "Uitkomst | None", l: "Lcoe | None" = None) -> dict:
    """The economic KPIs in the flat format that analysis/kpi.py expects.
    
    That way kpi.py does not have to know anything about the inside of this file, and the
    KPI page does not break if something changes here.
    """
    if u is None:
        return {}
    terugverdientijd = u.terugverdientijd
    return {
        "investering": u.investering,
        "opex_extra": u.opex_extra_jaar,
        "besparing_per_jaar": u.besparing_jaar,
        # The saving as it actually occurred, without scaling to a year.
        # The KPI page shows this as soon as the run is shorter than a year.
        "besparing_periode": u.besparing_periode,
        "dagen": u.dagen,
        "terugverdientijd": float("nan") if terugverdientijd is None else terugverdientijd,
        "lcoe": l.waarde_eur_per_mwh if l is not None else float("nan"),
        "lcoe_reden": l.reden if l is not None else "LCOE not calculated.",
        "max_investering": u.max_investering,
    }
