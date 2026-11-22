"""Financial page: what a simulated energy system costs and what it yields.

Two analyses of the same run:
    1. Payback time. The investment (the difference between the basis and the simulated
       system) against the saving on energy minus the extra fixed costs per year.
    2. Maximum investment. What may it cost in total to break even within the lifetime
       of the assets, and how much does that differ from the computed investment?

The computation is in analysis/financieel.py, the formulas in data/economic/. This page
only shows; that way the computation stays usable without a dashboard.

The analysis always covers the WHOLE run, not a period you choose: a payback time out
of three separate days says nothing.
"""

import pandas as pd
import streamlit as st

from analysis import financieel
from dashboard import gedeeld as g
from dashboard import grafieken
from utils import runbeheer
from utils.projectpaden import BASIS

MAX_LEVENSDUUR = 100.0  # limit of the input field; LEVENSDUUR_JAAR may be anything above 0
GEMETEN = "The measured original situation"
BASISRUN = "A simulated basis run"


def euro(waarde: float, decimalen: int = 0) -> str:
    if pd.isna(waarde):
        return "–"
    return f"EUR {g.getal(round(waarde, decimalen) + 0.0, decimalen)}"


def maat(paren: list[tuple[float, str]], aan: bool) -> str:
    """'500 m³' or '200 kWh / 150 kW'. A dash if the asset is not there, 'on' if it is
    there but has no size (PV without panels: then the model plays back the measured
    generation profile instead of computing the yield)."""
    if not aan:
        return "–"
    if not paren:
        return "on"
    return " / ".join(f"{g.getal(getal, 0 if float(getal).is_integer() else 1)} {eenheid}"
                      for getal, eenheid in paren)


def investeringstabel(regels: list[financieel.Regel]) -> None:
    kop = ("| Asset | Basis | Simulated | New price | Investment | OPEX per year | "
           "Extra OPEX per year |")
    rijen = [kop, "|:---|:---|:---|---:|---:|---:|---:|"]
    for r in regels:
        rijen.append(
            f"| {r.label} | {maat(r.maat_basis, r.aan_basis)} | {maat(r.maat_nieuw, r.aan_nieuw)} | "
            f"{euro(r.capex_nieuw)} | {euro(r.investering)} | {euro(r.opex_nieuw)} | "
            f"{euro(r.opex_extra)} |")
    rijen.append(f"| **Total** | | | | **{euro(financieel.investering(regels))}** | "
                 f"**{euro(sum(r.opex_nieuw for r in regels))}** | "
                 f"**{euro(financieel.opex_extra(regels))}** |")
    st.markdown("\n".join(rijen))

    uitleg = ["*New price* is what the asset would cost if you buy it completely new; "
              "*investment* is only the difference with the basis."]
    kleiner = [r.label for r in regels if r.kleiner]
    if kleiner:
        uitleg.append(f"{', '.join(kleiner)} becomes smaller than in the basis: that costs nothing and "
                      "yields nothing either.")
    pv = next((r for r in regels if r.asset == "pv"), None)
    if pv is not None and pv.aan_nieuw and not pv.maat_nieuw:
        uitleg.append("No panels are set for PV, so this scenario plays back the measured "
                      "generation profile. That generation comes from the panels that are already "
                      "there, and so they are still there: PV costs nothing here and yields "
                      "nothing either, and its maintenance stays equal to the basis.")
    st.caption(" ".join(uitleg))


def kosten_gemeten(df: pd.DataFrame, project: str) -> float | None:
    """The energy costs of the existing system from the measurement data, over the period of the run."""
    meting = g.meetdata(project)
    if meting is None:
        st.warning("There is no processed input data for this project yet. Process the raw data "
                   "on the General page.", icon=":material/database:")
        return None
    per_stap = grafieken.originele_kosten(grafieken.Reeks(df), meting)
    if per_stap is None:
        st.warning("There is no measured data at the moments of this run. Choose a basis run "
                   "as comparison, or run over a period for which measurement data exists.",
                   icon=":material/event_busy:")
        return None
    ontbreekt = int(meting.reindex(df.index).isna().any(axis=1).sum())
    if ontbreekt:
        st.warning(f"For {ontbreekt} of the {len(df)} steps the measured grid offtake or gas "
                   "import is missing. Those steps count as EUR 0 in the original situation, so "
                   "the saving below is too low.", icon=":material/warning:")
    return float(per_stap["Total (EUR)"].sum())


def kosten_basisrun(df: pd.DataFrame, runs: list[dict], project: str, huidig: str):
    """(costs basis run, costs of this run, days, period) over the moments that both
    runs share. None if there is nothing to compare."""
    anderen = {str(r["pad"]): r for r in runs if str(r["pad"]) != huidig}
    if not anderen:
        st.warning("There is only one run for this project; there is nothing to compare yet.",
                   icon=":material/info:")
        return None
    # Runs of the scenario 'basis' at the top: that is nearly always the one you want.
    paden = sorted(anderen, key=lambda p: anderen[p]["scenario"] != BASIS)
    gekozen = st.selectbox("Basis run", paden, format_func=lambda p: g.runlabel(anderen[p]),
                           key=g.blijvend(f"fin_basisrun|{project}", paden[0], lambda p: p in anderen),
                           help="A run of the scenario 'basis'. Only the moments that both "
                                "runs have in common are compared.")
    st.caption(g.runbeschrijving(anderen[gekozen]))
    if anderen[gekozen]["scenario"] != BASIS:
        st.warning(f"This is a run of scenario **{anderen[gekozen]['scenario']}**, not of the "
                   "basis. The saving is then measured against THAT system, while the "
                   "investment above is the difference with the basis. The payback time does "
                   "not belong together then.", icon=":material/warning:")
    df_basis = g.lees_dispatch(anderen[gekozen]["pad"])
    if df_basis is None or df_basis.empty:
        st.warning("That run has no results in dispatch.csv (yet).", icon=":material/warning:")
        return None
    samen = df.index.intersection(df_basis.index)
    if samen.empty:
        st.warning("These two runs have no single moment in common, so they cannot be "
                   "compared.", icon=":material/event_busy:")
        return None
    deel, deel_basis = df.loc[samen], df_basis.loc[samen]
    if len(samen) < max(len(df), len(df_basis)):
        st.caption("Only the moments that both runs have in common count.")
    return (float(deel_basis["total_cost_eur"].sum()), float(deel["total_cost_eur"].sum()),
            financieel.dagen(deel), (samen[0], samen[-1] + grafieken.STAP))


def kasstroom(vak, u: financieel.Uitkomst, levensduur: float) -> None:
    """The cash position year after year, in the place kept free in the Payback time section."""
    kruising = (f"paid back after {g.getal(u.terugverdientijd, 1)} years"
                if u.terugverdientijd is not None else "")
    fig = grafieken.kasstroom(u.investering, u.netto_jaar, levensduur, g.kleuren(),
                              terugverdientijd=u.terugverdientijd, label_kruising=kruising,
                              label_levensduur=f"lifetime {g.getal(levensduur)} years")
    with vak:
        st.plotly_chart(fig, width="stretch", theme=None, key="grafiek|kasstroom",
                        config={"displaylogo": False})
        uitleg = (f"What you are left with on balance. At the moment of investing the line dives "
                  f"to {euro(-u.investering)}; after that {euro(u.netto_jaar)} is added every "
                  "year, the saving on energy minus the extra fixed costs.")
        if u.terugverdientijd is not None:
            uitleg += (f" Where it crosses the zero line, after {g.getal(u.terugverdientijd, 1)} "
                       f"years, the investment is earned back. At the lifetime of "
                       f"{g.getal(levensduur)} years it stands at {euro(u.ruimte)}: the room below.")
        else:
            uitleg += " The line only drops further: the zero line is never reached."
        uitleg += " Without interest or inflation, so a euro in year nine counts equally (assumption G6)."
        st.caption(uitleg)


def tegels(paren: list[tuple[str, str, str | None]], sleutel: str) -> None:
    """Cards with a big number, like the KPIs. At most three next to each other: amounts
    of a million do not fit otherwise."""
    g.kerncijfers(paren, sleutel)


g.paginatitel("Financial")
project, _ = g.keuze()
if not project:
    st.info("First create a project on the General page.")
    st.stop()

runs = runbeheer.runs_van_project(project)
if not runs:
    st.info("There are no runs for this project yet. Start one on the General page.")
    st.stop()

run = g.kiesrun(runs, project, hulp="The same run as on the Results page.")
pad = run["pad"]

systemen = financieel.systemen(pad)
if systemen is None:
    st.warning("The `run_info.json` of this run does not hold the energy system and the basis, "
               "so the investment cannot be computed. That happens to runs from before this "
               "version. Using the scenario files of now would be possible, but those may have "
               "changed since that run; rather run it again with `run_milp.py`.", icon=":material/help:")
    st.stop()

df = g.lees_dispatch(pad)
if df is None or df.empty:
    st.warning("This run has no results in dispatch.csv (yet).")
    st.stop()
if run["status"] != "complete":
    st.info(f"This run is not finished (status: {g.RUNSTATUS.get(run['status'], run['status'])}). "
            "The analysis uses the part that was carried out.", icon=":material/hourglass:")

try:
    regels = financieel.regels(*systemen)
except financieel.FormuleFout as fout:
    st.error(f"{fout}\n\nAdjust the file and refresh this page; restarting is not needed.",
             icon=":material/function:")
    st.stop()

with g.blok("investering"):
    g.blokkop("Investment")
    with g.vak("investering"):
        st.caption("The difference between the basis and the simulated system, with the formulas "
                   "from `data/economic/asset_capex/` and `asset_opex/`. Scaling back yields "
                   "nothing. If you adjust a formula, that is visible here right away.")
        investeringstabel(regels)

with g.blok("besparing"):
    g.blokkop("Saving on energy")
    with g.vak("besparing"):
        vergelijk_met = st.radio("What do you compare with?", [GEMETEN, BASISRUN], horizontal=True,
                         key=g.blijvend("fin_vergelijking", GEMETEN),
                         help="The measured situation is what the company really paid. A basis run is "
                              "the same model chain with the existing system, and therefore a fairer "
                              "comparison of only the effect of the assets.")
        if vergelijk_met == GEMETEN:
            kosten_basis = kosten_gemeten(df, project)
            if kosten_basis is None:
                st.stop()
            kosten_nieuw, dagen = float(df["total_cost_eur"].sum()), financieel.dagen(df)
            periode = (df.index[0], df.index[-1] + grafieken.STAP)
            st.caption("The measured grid offtake at the same electricity price as the run, and the "
                       "measured gas import (`clean_data_gas.csv`) at the gas price of the run, without "
                       "efficiency. The model uses that same gas series as heat demand and burns "
                       "gas/efficiency for it (assumption B4), so a simulated basis run comes out on "
                       "gas about 11 % higher than these measured costs.")
        else:
            uitkomst = kosten_basisrun(df, runs, project, str(pad))
            if uitkomst is None:
                st.stop()
            kosten_basis, kosten_nieuw, dagen, periode = uitkomst
            st.caption("Two runs from the same model chain: the difference comes only from the "
                       "energy system and the control, not from assumption B4.")

levensduur_uit_assets = financieel.kortste_levensduur(regels)
# If nothing has been invested in, then the shortest lifetime there is at all: never a
# fixed number here, because then the page would say something else than LEVENSDUUR_JAAR.
standaard_levensduur = (levensduur_uit_assets[0] if levensduur_uit_assets
                        else min(r.levensduur for r in regels))

with g.blok("terugverdientijd"):
    g.blokkop("Payback time")
    u = financieel.Uitkomst(investering=financieel.investering(regels),
                            opex_extra_jaar=financieel.opex_extra(regels),
                            kosten_basis=kosten_basis, kosten_nieuw=kosten_nieuw, dagen=dagen,
                            levensduur=standaard_levensduur)
    tegels([
        ("Investment", euro(u.investering), "The difference between the basis and this system."),
        ("Saving per year", euro(u.besparing_jaar), f"Over the run: {euro(u.besparing_periode)}."),
        ("Extra OPEX per year", euro(u.opex_extra_jaar), "The fixed costs of this system minus "
                                                        "those of the basis."),
        ("Net per year", euro(u.netto_jaar), "Saving minus extra OPEX."),
        # 'never' and not 'does not pay itself back': a long text is cut off in the tile.
        ("Payback time", f"{g.getal(u.terugverdientijd, 1)} years"
         if u.terugverdientijd is not None else "never",
         "Investment / net per year. 'Never' if nothing is left over per year."),
    ], "terugverdientijd")
    regel = (f"The analysis covers **{g.getal(dagen, 1)} days** "
             f"({g.tijdstip(periode[0])} to {g.tijdstip(periode[1])}). "
             f"Energy costs: {euro(kosten_nieuw)} against {euro(kosten_basis)} in the basis.")
    if dagen < 350:
        regel += (f" The yearly amounts are scaled up from that linearly (x {g.getal(u.factor_jaar, 1)}). "
                  "**That is a rough estimate:** scaling a winter period up to a year overestimates "
                  "the heat saving, a summer period underestimates it. Run a whole year before you "
                  "put these numbers in your thesis.")
    st.caption(regel)
    if u.terugverdientijd is None and u.netto_jaar <= 0:
        st.warning("Nothing is left over per year: the extra fixed costs are higher than the saving "
                   "on energy. With these formulas this system therefore never pays itself back, "
                   "however cheap the investment is.", icon=":material/trending_down:")
    # The graph belongs here, but needs the lifetime that is only filled in below. That is
    # why an empty place is reserved now (a white box) and filled after the input field.
    kasstroomvak = g.vak("kasstroom")

with g.blok("maximale_investering"):
    g.blokkop("Maximum investment")
    st.caption("The other side of the question: what may the whole system cost to break even exactly "
               "within the lifetime? That is the net saving per year times the lifetime.")
    with g.vak("levensduur"):
        kolom, _ = st.columns([1, 3])
        levensduur = kolom.number_input(
            "Lifetime (years)", min_value=1.0, max_value=MAX_LEVENSDUUR, step=1.0, format="%.0f",
            key=g.blijvend(f"fin_levensduur|{pad.name}", min(standaard_levensduur, MAX_LEVENSDUUR),
                           lambda waarde: 1.0 <= waarde <= MAX_LEVENSDUUR),
            help="By default the shortest lifetime of the assets you invest in; that is in "
                 "LEVENSDUUR_JAAR in data/economic/asset_capex/capex.py.")
    u = financieel.Uitkomst(investering=u.investering, opex_extra_jaar=u.opex_extra_jaar,
                            kosten_basis=kosten_basis, kosten_nieuw=kosten_nieuw, dagen=dagen,
                            levensduur=float(levensduur))
    kasstroom(kasstroomvak, u, levensduur)
    tegels([
        ("May cost at most", euro(u.max_investering),
         f"Net saving per year x {g.getal(levensduur)} years."),
        ("Computed investment", euro(u.investering), "From the formulas above."),
        ("Room", euro(u.ruimte), "Positive: it pays itself back within the lifetime."),
    ], "maximale_investering")
    if levensduur_uit_assets:
        st.caption(f"The shortest lifetime in this system is {g.getal(levensduur_uit_assets[0])} years "
                   f"({levensduur_uit_assets[1]}).")
    if u.ruimte >= 0 and u.max_investering > 0:
        st.success(f"This system pays itself back within {g.getal(levensduur)} years: there is still "
                   f"{euro(u.ruimte)} room on top of the computed investment.",
                   icon=":material/check_circle:")
    elif u.max_investering > 0:
        deel = 100 * u.max_investering / u.investering if u.investering else float("nan")
        st.warning(f"To break even within {g.getal(levensduur)} years this system may cost "
                   f"{euro(u.max_investering)}, that is {g.getal(deel, 0)} % of the computed "
                   f"{euro(u.investering)}. It comes out {euro(-u.ruimte)} too expensive.",
                   icon=":material/trending_up:")
    st.caption("Without discounting, inflation or residual value, and without subsidy. An OPEX that "
               "is a percentage of the investment does not change along here: the extra OPEX stays "
               "that of the simulated system.")
