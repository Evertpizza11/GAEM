"""KPIs page: the key figures of a run, per asset and for the system as a whole.

The layout follows the thesis: first the asset-level KPIs (table per asset), then the
system-level KPIs in four themes. The computation is in analysis/kpi.py,
analysis/emissies.py and analysis/financieel.py; this page only shows.

COMPARING. As on the Results page you choose a second run (B) with 'Compare with'.
That comes next to it as an extra column, with the difference included. The run choice
shares its key with the Results page, so it comes along when you switch between pages.

NO JUDGEMENT WITHOUT A BENCHMARK. A KPI without a benchmark gets a dash and no colour.
Making a number green or red while there is no norm for it suggests a judgement that is
not there. If there is a benchmark but no direction (such as with the cycles per day of
the battery, where both much less and much more than one deviates from the assumption
from the literature), then the benchmark is there as a reference without a tick.

REFERENCE FOR AVOIDED EMISSIONS. The measured existing situation of the company, over
exactly the moments of the run. If you want to set it against a simulated basis run
instead, that choice is on the Financial page.
"""

import math
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from analysis import emissies as em
from analysis import financieel
from analysis import kpi as kpimod
from dashboard import gedeeld as g
from dashboard import grafieken
from utils import runbeheer
from utils.projectpaden import PROJECT_BESTAND, project_map

GEEN = ""
AFBAKENINGEN = {
    "verbranding": "Combustion only",
    "totaal": "Including upstream chain",
}


# --------------------------------------------------------------------------
# Display
# --------------------------------------------------------------------------

def _waarde(k: kpimod.Kpi) -> str:
    if not math.isfinite(k.waarde):
        return "–"
    return g.getal(round(k.waarde, k.decimalen) + 0.0, k.decimalen)


def _verschil(a: kpimod.Kpi, b: kpimod.Kpi) -> str:
    """The difference between two runs, or a dash if that has no meaning.

    UNITS MUST BE EQUAL. If run A covers a year and run B a week, then A shows a yearly
    figure and B a run total. Subtracting those gives a number that means nothing, and
    that cannot be seen from the table. So rather nothing then.
    """
    if b is None or a.eenheid != b.eenheid:
        return "–"
    if not (math.isfinite(a.waarde) and math.isfinite(b.waarde)):
        return "–"
    delta = round(b.waarde - a.waarde, a.decimalen) + 0.0
    teken = "+" if delta > 0 else ""
    return teken + g.getal(delta, a.decimalen)


def _benchmark(k: kpimod.Kpi) -> str:
    """The benchmark with the judgement included, or a dash."""
    tekst = k.benchmark_tekst or ("" if k.benchmark is None else g.getal(k.benchmark, k.decimalen))
    if not tekst:
        return "—"
    oordeel = k.haalt_benchmark
    if oordeel is None:
        return tekst
    return f"{tekst} ✓" if oordeel else f"{tekst} ✗"


def _deltakleur(k: kpimod.Kpi) -> str:
    """How st.metric should colour the difference.

    Streamlit colours up green and down red by default. That is only right if higher is
    better. With a KPI such as 'Curtailed energy' down is in fact good, and without a
    direction there is nothing to judge and the difference should stay grey.
    """
    return {"hoger": "normal", "lager": "inverse"}.get(k.richting, "off")


def _deltatekst(a: kpimod.Kpi, b: kpimod.Kpi | None) -> str | None:
    """The difference as text for st.metric, or None if it has no meaning.

    The sign is in front of it as a plain + or -, because that is where Streamlit reads the
    direction of the little arrow from; the number behind it is in Dutch notation.
    """
    if b is None or a.eenheid != b.eenheid:
        return None
    if not (math.isfinite(a.waarde) and math.isfinite(b.waarde)):
        return None
    delta = round(b.waarde - a.waarde, a.decimalen) + 0.0
    return f"{'+' if delta >= 0 else '-'}{g.getal(abs(delta), a.decimalen)}"


def kpi_blok(a: kpimod.Kpi, b: kpimod.Kpi | None, label_a: str, label_b: str,
             voorvoegsel: str = "") -> None:
    """One KPI as a bordered block: name on the left, the numbers large, benchmark on the right.

    The key starts with 'kpiblok' so that the house style in gedeeld.huisstijl() can fill it
    and can enlarge the numbers. Streamlit refuses two containers with the same key, so the
    group is in it: should the boiler ever also get 'Full load hours', they do not collide.
    """
    eigen = f"{voorvoegsel}_{a.sleutel or a.naam}".replace(" ", "_")
    with st.container(border=True, key=f"kpiblok_{eigen}"):
        breedtes = [3, 2, 2, 2] if label_b else [4, 3, 2]
        kolommen = st.columns(breedtes, vertical_alignment="top")
        # The unit after the name between square brackets. Not with a dimensionless KPI
        # such as the COP, and not with 'Solves not optimal', where the 'unit' is a count
        # ("of 48") and between brackets would start to read as a unit.
        toon_eenheid = a.eenheid and a.eenheid != "—" and not a.eenheid.startswith("of ")
        kop = f"{a.naam} [{a.eenheid}]" if toon_eenheid else a.naam
        # With its own size on it instead of through a style rule for all paragraphs:
        # such a general rule also hits the labels inside st.metric.
        kolommen[0].markdown(
            f"<div style='font-size:1.2rem; font-weight:700; line-height:1.3;'>"
            f"{escape(kop)}</div>", unsafe_allow_html=True)

        kolommen[1].metric(label_a, _waarde(a), label_visibility="visible")
        if label_b:
            # The unit after the value of B if it differs: with a year run next to a week
            # run there is a yearly figure on the left and a run total on the right (assumption K2).
            waarde_b = "–" if b is None else _waarde(b)
            if b is not None and b.eenheid != a.eenheid:
                waarde_b = f"{waarde_b} {b.eenheid}"
            kolommen[2].metric(label_b, waarde_b, delta=_deltatekst(a, b),
                               delta_color=_deltakleur(a))

        oordeel = a.haalt_benchmark
        tekst = _benchmark(a).removesuffix(" ✓").removesuffix(" ✗")
        if tekst == "—":
            kolommen[-1].caption("no benchmark")
        else:
            kleur = {True: "green", False: "red", None: "gray"}[oordeel]
            teken = {True: ":material/check:", False: ":material/close:", None: None}[oordeel]
            kolommen[-1].badge(tekst, color=kleur, icon=teken)


def tabel(kpis_a: list[kpimod.Kpi], kpis_b: list[kpimod.Kpi] | None,
          label_a: str, label_b: str, voorvoegsel: str = "") -> None:
    """A group of KPIs, each in its own block.

    Coupling on the KEY and not on the name: a KPI that is called 'Yearly emissions' in a
    year run and 'Emissions' in a shorter run (assumption K2) would otherwise not find each
    other, and then run B appeared as a dash while there certainly is a value.
    """
    if not kpis_a:
        return
    per_sleutel = {(k.sleutel or k.naam): k for k in (kpis_b or [])}
    for k in kpis_a:
        kpi_blok(k, per_sleutel.get(k.sleutel or k.naam) if kpis_b is not None else None,
                 label_a, label_b if kpis_b is not None else "", voorvoegsel)


def toelichtingen(kpis: list[kpimod.Kpi]) -> None:
    met_tekst = [k for k in kpis if k.toelichting]
    if not met_tekst:
        return
    with st.expander("What these numbers mean"):
        for k in met_tekst:
            bron = f" · source: {k.bron}" if k.bron else ""
            st.markdown(f"**{k.naam}** ({k.eenheid}){bron}  \n{k.toelichting}")


def naar_csv(groepen: list[kpimod.Groep], label: str) -> pd.DataFrame:
    """The KPIs as a table to download; handy as a basis for a table in the thesis."""
    rijen = []
    for groep in groepen:
        for k in groep.kpis:
            rijen.append({"groep": groep.naam, "indicator": k.naam, "eenheid": k.eenheid,
                          "waarde": k.waarde, "benchmark": k.benchmark,
                          "benchmark_tekst": k.benchmark_tekst,
                          "haalt_benchmark": k.haalt_benchmark, "bron": k.bron,
                          "run": label})
    return pd.DataFrame(rijen)


# --------------------------------------------------------------------------
# Computing for one run
# --------------------------------------------------------------------------

def economisch(pad: Path, df: pd.DataFrame, project: str, voet, horizon) -> dict:
    """The economic KPIs of a run, with the measured situation as reference.

    If it does not work out (no energy system in run_info.json, an error in a cost formula, no
    measurement data at these moments), then an empty dict: the KPIs then become dashes and the
    rest of the page stays.
    """
    systemen = financieel.systemen(pad)
    if systemen is None:
        return {"_melding": "The `run_info.json` of this run does not hold the energy system "
                            "and the basis, so the investment cannot be computed."}
    try:
        regels = financieel.regels(*systemen)
    except financieel.FormuleFout as fout:
        return {"_melding": f"Error in a cost formula: {fout}"}

    meting = g.meetdata(project)
    kosten_basis = None
    if meting is not None:
        per_stap = grafieken.originele_kosten(grafieken.Reeks(df), meting)
        if per_stap is not None:
            kosten_basis = float(per_stap["Total (EUR)"].sum())

    kosten_nieuw = float(df["total_cost_eur"].sum())
    levensduur = financieel.kortste_levensduur(regels)
    u = financieel.Uitkomst(
        investering=financieel.investering(regels),
        opex_extra_jaar=financieel.opex_extra(regels),
        kosten_basis=kosten_basis if kosten_basis is not None else float("nan"),
        kosten_nieuw=kosten_nieuw,
        dagen=financieel.dagen(df),
        levensduur=levensduur[0] if levensduur else min(r.levensduur for r in regels))
    l = financieel.lcoe(regels, df, kosten_nieuw, voet, horizon)
    uit = financieel.voor_kpi(u, l)
    if kosten_basis is None:
        uit["_melding"] = ("There is no measured data at the moments of this run, so the "
                           "saving and the payback time cannot be determined.")
    return uit


def energiesysteem(pad: Path) -> dict | None:
    """The energy system this run was run with, or None if it is not in there."""
    info = runbeheer.lees_json(Path(pad) / "run_info.json") or {}
    return info.get("energiesysteem") or None


def alles(pad: Path, df: pd.DataFrame, project: str, oppervlak, factoren,
          afbakening: str, voet, horizon) -> tuple[list, list, str]:
    """(asset groups, system groups, message) for one run."""
    systeem = energiesysteem(pad) or {}
    s = kpimod.stromen(df)

    uitstoot = referentie = per_asset = None
    if factoren is not None:
        uitstoot = em.van_run(df, factoren)
        # The breakdown into boiler, grid and heat pump. See em.per_asset: those three
        # deliberately do not add up to the system total.
        per_asset = em.per_asset(df, factoren)
        meting = g.meetdata(project)
        if meting is not None:
            referentie = em.van_meting(meting, df, factoren)

    solver = None
    logpad = Path(pad) / "solver_log.csv"
    if logpad.is_file():
        try:
            solver = pd.read_csv(logpad)
        except (OSError, ValueError, pd.errors.EmptyDataError):
            solver = None

    fin = economisch(pad, df, project, voet, horizon)
    return (kpimod.asset_kpis(df, systeem, s, per_asset=per_asset, afbakening=afbakening),
            kpimod.systeem_kpis(df, systeem, uitstoot=uitstoot, referentie=referentie,
                                fin=fin, solver=solver, oppervlak_m2=oppervlak,
                                afbakening=afbakening, s=s, per_asset=per_asset),
            fin.get("_melding", ""))


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------

# Title and styling as on every page: see the house style in dashboard/gedeeld.py.
g.paginatitel("KPIs")
project, _ = g.keuze()
if not project:
    st.info("First create a project on the General page.")
    st.stop()

runs = runbeheer.runs_van_project(project)
if not runs:
    st.info("There are no runs for this project yet. Start one on the General page.")
    st.stop()

per_pad = {str(r["pad"]): r for r in runs}
paden = list(per_pad)
c1, c2 = st.columns(2)
gekozen = c1.selectbox("Run", paden, format_func=lambda p: g.runlabel(per_pad[p]),
                       key=g.blijvend(f"run_a|{project}", paden[0], lambda p: p in per_pad),
                       help="The same run as on the Results and Financial pages.")
anderen = [GEEN] + [p for p in paden if p != gekozen]
tweede = c2.selectbox("Compare with", anderen,
                      format_func=lambda p: "Do not compare" if p == GEEN else g.runlabel(per_pad[p]),
                      key=g.blijvend(f"run_b|{project}", GEEN, lambda p: p in anderen),
                      help="A second run (B) as an extra column, with the difference B − A.")
run, vergelijking = per_pad[gekozen], per_pad.get(tweede)
for kolom, r in ((c1, run), (c2, vergelijking)):
    tekst = g.runbeschrijving(r) if r else ""
    if tekst:
        kolom.caption(tekst)

df = g.lees_dispatch(run["pad"])
if df is None or df.empty:
    st.warning("This run has no results in dispatch.csv (yet).")
    st.stop()
df_b = g.lees_dispatch(vergelijking["pad"]) if vergelijking else None
if vergelijking and (df_b is None or df_b.empty):
    c2.warning("That run has no results in dispatch.csv (yet).")
    vergelijking, df_b = None, None

st.caption("The KPIs cover the WHOLE run, not a part that you choose: a full-load hour count "
           "or a seasonal COP out of three separate days says nothing. If you want to look at a "
           "part, use the slider on the Results page.")

# --- settings ---------------------------------------------------------------
with st.expander("Settings for emissions and LCOE"):
    links, rechts = st.columns(2)
    afbakening = links.radio(
        "Which emissions count?", list(AFBAKENINGEN),
        format_func=lambda s: AFBAKENINGEN[s],
        key=g.blijvend("kpi_afbakening", "verbranding"),
        help="'Combustion only' counts what is released at the burning itself. 'Including "
             "upstream chain' adds extraction, transport and conversion. NOTE the difference in "
             "scope between the two carriers: with gas the combustion is a scope 1 emission "
             "(the company burns it itself), with electricity a scope 2 emission (the power "
             "station burns elsewhere). A footprint according to the GHG protocol usually "
             "counts electricity on the total.")
    rechts.markdown("**Levelised cost of energy**")
    voet = rechts.number_input(
        "Discount rate (%)", min_value=0.0, max_value=25.0, step=0.5, value=None,
        placeholder="not chosen yet", key=g.blijvend("kpi_voet", None),
        help="Without a discount rate an LCOE is not an LCOE but an average cost price. "
             "Leave it empty until you have chosen a value and can justify it; the KPI then "
             "shows a dash.")
    horizon = rechts.number_input(
        "Horizon (years)", min_value=1.0, max_value=50.0, step=1.0, value=None,
        placeholder="not chosen yet", key=g.blijvend("kpi_horizon", None),
        help="Over how many years the investment is spread out. An obvious choice is the "
             "shortest lifetime of the assets that are invested in; that one is on the "
             "Financial page.")

try:
    factoren = em.factoren()
except (OSError, ValueError) as fout:
    st.error(f"The emission factors could not be read: {fout}", icon=":material/eco:")
    factoren = None
else:
    # The name the source list itself uses for this item, so that it can be cited in the
    # thesis without having to go back to the JSON file. And the CHOSEN electricity
    # category, because that choice changes the outcome by almost a factor of two and
    # therefore belongs in sight and not tucked away in a data file.
    etiket = (factoren.ruw.get("aardgas", {}).get("bron_label", {}) or {}).get(afbakening, "")
    st.caption(
        f"Emission factors: **{factoren.herkomst}**. "
        f"Natural gas {g.getal(factoren.gas_per_m3[afbakening], 3)} kg CO₂-eq/Nm³"
        f"{f' ({etiket})' if etiket else ''}, "
        f"electricity {g.getal(factoren.elektriciteit_per_kwh[afbakening], 3)} kg CO₂-eq/kWh "
        f"— category **{factoren.elektriciteit_soort or factoren.elektriciteit_keuze}**. "
        "To be changed in `data/emissies/emissiefactoren.json`, without touching the code; "
        "that also says which other categories from the same list are available.")

projectgegevens = runbeheer.lees_json(project_map(project) / PROJECT_BESTAND)
oppervlak = (projectgegevens or {}).get("kas_oppervlak_m2")

voet_fractie = None if voet is None else float(voet) / 100

# --- computing --------------------------------------------------------------
assets_a, systeem_a, melding_a = alles(run["pad"], df, project, oppervlak, factoren,
                                       afbakening, voet_fractie, horizon)
assets_b = systeem_b = None
if vergelijking:
    assets_b, systeem_b, melding_b = alles(vergelijking["pad"], df_b, project, oppervlak,
                                           factoren, afbakening, voet_fractie, horizon)
    if melding_b:
        st.warning(f"Run B: {melding_b}", icon=":material/warning:")
if melding_a:
    st.warning((f"Run A: {melding_a}" if vergelijking else melding_a), icon=":material/warning:")

label_a = f"A · {run['scenario']}"
label_b = f"B · {vergelijking['scenario']}" if vergelijking else ""

# --- showing ----------------------------------------------------------------
st.header("Asset level")
per_sleutel_b = {grp.sleutel: grp for grp in (assets_b or [])}
# The set-up of each run next to the header of the asset: run A left of the picture, run B
# right of it. That way the numbers also show what had been installed.
systeem_run_a = energiesysteem(run["pad"])
systeem_run_b = energiesysteem(vergelijking["pad"]) if vergelijking else None
for groep in assets_a:
    # Each asset in its own bordered block, so that PV and grid stand apart from each other.
    with g.blok(groep.sleutel):
        links = g.opstelling_html(
            groep.sleutel,
            None if systeem_run_a is None else systeem_run_a.get(groep.sleutel, {}),
            label_a, uitlijning="right")
        rechts = (g.opstelling_html(
            groep.sleutel,
            None if systeem_run_b is None else systeem_run_b.get(groep.sleutel, {}),
            label_b, uitlijning="left") if vergelijking else "")
        g.kop_asset(groep.sleutel, groep.naam, links=links, rechts=rechts)
        if groep.melding:
            st.caption(groep.melding)
        b = per_sleutel_b.get(groep.sleutel)
        tabel(groep.kpis, b.kpis if (b and vergelijking) else None, label_a, label_b,
              groep.sleutel)
        if vergelijking and b and groep.aanwezig != b.aanwezig:
            st.caption(f"This asset is {'present' if groep.aanwezig else 'absent'} in run A and "
                       f"{'present' if b.aanwezig else 'absent'} in run B.")
        toelichtingen(groep.kpis)

st.header("System level")
per_sleutel_b = {grp.sleutel: grp for grp in (systeem_b or [])}
for groep in systeem_a:
    with g.blok(groep.sleutel):
        # Also centred, so that the system blocks have the same styling as the assets.
        g.blokkop(groep.naam)
        b = per_sleutel_b.get(groep.sleutel)
        tabel(groep.kpis, b.kpis if (b and vergelijking) else None, label_a, label_b,
              groep.sleutel)
        toelichtingen(groep.kpis)

# --- taking into the thesis -------------------------------------------------
uitvoer = [naar_csv(assets_a + systeem_a, label_a or run["scenario"])]
if vergelijking:
    uitvoer.append(naar_csv(assets_b + systeem_b, label_b))
st.download_button("Download KPIs as CSV", pd.concat(uitvoer).to_csv(index=False),
                   file_name=f"kpi_{project}_{Path(gekozen).name}.csv", mime="text/csv",
                   icon=":material/download:",
                   help="The same numbers as above, as a table. Meant as a basis for a table "
                        "in the thesis, so that you do not have to retype them.")
