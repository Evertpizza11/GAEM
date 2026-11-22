"""Results page: interactive graphs of a run, comparing with a second run and
the button 'Save in project'.

A run is in runs/ first. Only when you click 'Save in project' is it copied
to projecten/<project>/output/.

Comparing: with 'Compare with' you choose a second run (B). That comes over run A in
every graph, in the same colours but dashed, at its real moments. The key figures then
become a table with A, B and the difference.

Period: the slider limits the key figures, graphs and tables to a part of the run(s).
If no run has more than 14 days of data in that part, the graphs show the values per 5
minutes instead of hourly averages.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard import gedeeld as g
from dashboard import grafieken
from utils import runbeheer
from utils.projectpaden import ROOT

GEEN = ""  # choice 'Do not compare'
DECIMALEN = {"Grid offtake (MWh)": 1, "Gas consumption (MWh)": 1}
DAG = pd.Timedelta(days=1)


def kengetallen(df: pd.DataFrame) -> dict[str, float]:
    dt = 5 / 60
    waarden = {
        "Total costs (EUR)": df["total_cost_eur"].sum(),
        "Electricity costs (EUR)": df["electricity_cost_eur"].sum(),
        "Gas costs (EUR)": df["gas_cost_eur"].sum(),
        "Grid offtake (MWh)": df["grid_import_kw"].sum() * dt / 1000,
        "Peak grid offtake (kW)": df["grid_import_kw"].max(),
        "Gas consumption (MWh)": df["gb_fuel_kwh"].sum() / 1000,
    }
    return {naam: float("nan") for naam in waarden} if df.empty else waarden


def tegels(df: pd.DataFrame) -> None:
    """The key figures as cards with a big number, three per row: long runs have
    long numbers."""
    paren = []
    for naam, waarde in kengetallen(df).items():
        titel, eenheid = naam.removesuffix(")").split(" (")
        tekst = g.getal(waarde, DECIMALEN.get(naam, 0))
        paren.append((titel, f"EUR {tekst}" if eenheid == "EUR" else f"{tekst} {eenheid}"))
    g.kerncijfers(paren, "resultaten")


def _tekst(waarde: float, decimalen: int, plusteken: bool = False) -> str:
    if pd.isna(waarde):
        return "–"
    waarde = round(waarde, decimalen) + 0.0  # + 0.0: no '-0' after rounding
    return ("+" if plusteken and waarde > 0 else "") + g.getal(waarde, decimalen)


def verschiltabel(dfa: pd.DataFrame, dfb: pd.DataFrame, a: dict, b: dict) -> None:
    """The key figures of A and B next to each other, with the difference B − A."""
    rijen = {"Days with data": (len(dfa) / 288, len(dfb) / 288, 1)}
    ka, kb = kengetallen(dfa), kengetallen(dfb)
    rijen.update({naam: (ka[naam], kb[naam], DECIMALEN.get(naam, 0)) for naam in ka})
    kop = [f"A · {a['scenario']}", f"B · {b['scenario']}"]
    regels = ["| | " + " | ".join(t.replace("|", "\\|") for t in kop) + " | B − A | B − A (%) |",
              "|:---|---:|---:|---:|---:|"]
    for naam, (wa, wb, decimalen) in rijen.items():
        procent = 100 * (wb - wa) / wa if wa else float("nan")
        regels.append(f"| {naam} | {_tekst(wa, decimalen)} | {_tekst(wb, decimalen)} | "
                      f"{_tekst(wb - wa, decimalen, True)} | {_tekst(procent, 1, True)} |")
    st.markdown("\n".join(regels))


def tweede_pogingen(pad: Path, wie: str = "") -> None:
    """Reports optimisations where the safety net (second attempt) was needed; also during a run."""
    bestand = pad / "solver_log.csv"
    try:
        log = pd.read_csv(bestand)
    except (OSError, ValueError, pd.errors.EmptyDataError):
        return
    if "warm_start_retry" not in log:
        return
    herstart = log[log["warm_start_retry"].astype(str).str.lower() == "true"]
    if herstart.empty:
        return
    momenten = ", ".join(
        g.tijdstip(datum, jaar=False) + (f" (gap {100 * gap:.0f} %)" if pd.notna(gap) else "")
        for datum, gap in zip(herstart["Date"], herstart["relative_gap"]))
    st.warning(f"{wie}With {len(herstart)} optimisation(s) the solver found no plan from the previous plan "
               f"within the time limit: {momenten}. There a second attempt was made from a controlled "
               "start plan. With a high gap that start plan is hardly improved and that day largely "
               "follows from it; a higher time limit helps. See solver_log.csv "
               "(warm_start_retry, warm_start_source).", icon=":material/replay:")


def waarschuwingen(pad: Path, status: dict, wie: str = "") -> None:
    """Time limits and second attempts: apply to the whole run, not only to the chosen period."""
    if status.get("time_limit_solves"):
        st.warning(f"{wie}{status['time_limit_solves']} optimisation(s) ran into the time limit. "
                   "Those outcomes depend on the speed of the computer; see solver_log.csv.",
                   icon=":material/timer:")
    tweede_pogingen(pad, wie)


def kies_periode(dfs: list[pd.DataFrame], sleutel: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Slider from the start to the end of the shown runs, per whole day (midnight)."""
    begin = min(df.index[0] for df in dfs)
    eind = max(df.index[-1] for df in dfs) + grafieken.STAP  # end of the last step
    middernachten = pd.date_range(begin.floor("D") + DAG, (eind - pd.Timedelta(1, "ns")).floor("D"), freq="D")
    grenzen = [begin, *middernachten, eind]
    # The chosen period stays when you go to another page and come back. It does belong
    # to these runs: if a kept period no longer fits within the data (another run chosen,
    # or a running run that has gone further by now), then it becomes the whole again.
    van, tot = st.select_slider(
        "Period", options=grenzen,
        key=g.blijvend(sleutel, (grenzen[0], grenzen[-1]),
                       lambda vt: isinstance(vt, tuple) and all(t in grenzen for t in vt)),
        format_func=lambda t: g.tijdstip(t, tijd=bool(t.hour or t.minute)),
        help="Limits the key figures, graphs and tables below to this period. With a period of "
             "14 days or shorter the graphs show the values per 5 minutes instead of hourly averages.")
    if van == tot:  # Both sliders in the same place: take the day after (or before).
        i = grenzen.index(van)
        van, tot = (van, grenzen[i + 1]) if i + 1 < len(grenzen) else (grenzen[i - 1], tot)
    st.caption(f"Key figures, graphs and tables below cover these {g.getal((tot - van) / DAG, 1)} days.")
    return van, tot


def origineel_erbij(reeksen: list, project: str) -> pd.DataFrame | None:
    """Checkbox at the cost graph: the measurement data of the company included, or None.

    The box is switched off when there is no measurement to draw. data_gas.csv is optional,
    so that is a normal state and not an error.
    """
    meting = g.meetdata(project)
    aan = st.checkbox("Also the original situation (measured consumption of the company)",
                      key=g.blijvend("origineel_erbij", False), disabled=meting is None,
                      help="What the existing system cost in this period: the measured grid offtake at "
                           "the same electricity price as the run, and the measured gas import "
                           "(clean_data_gas.csv) at the gas price of the run.")
    if meting is None:
        st.caption("There is no processed input data yet. Process the raw data on the General page."
                   if not g.datastatus(project)["processed"] else
                   "There is no measured gas for this project, so there is nothing to compare with. "
                   "On Financial you can compare with a simulated basis run instead.")
        return None
    if not aan:
        return None
    if not any(r.df.index.isin(meting.index).any() for r in reeksen):
        st.caption("There is no measured data at the moments of this run(s).")
        return None
    return meting


def origineel_totalen(reeksen: list, meting: pd.DataFrame) -> str:
    """One line with the total costs of the run(s) against those of the original situation."""
    regels = []
    for r in reeksen:
        per_stap = grafieken.originele_kosten(r, meting)
        if per_stap is None:
            continue
        origineel, run = per_stap["Total (EUR)"].sum(), r.df["total_cost_eur"].sum()
        procent = 100 * (run - origineel) / origineel if origineel else float("nan")
        regels.append(f"{'Run ' + r.letter if r.letter else 'This run'}: EUR {g.getal(run)} against "
                      f"EUR {g.getal(origineel)} original, a difference of {_tekst(run - origineel, 0, True)} EUR "
                      f"({_tekst(procent, 1, True)} %).")
    return " ".join(regels)


def pv_totalen(reeksen: list) -> str:
    """A line at the PV graph: how much was generated, used and curtailed in this period."""
    regels = []
    for r in reeksen:
        if not grafieken.pv_aan(r):
            continue
        opwek, benut, weg = grafieken.pv_totalen(r)
        deel = f" ({100 * weg / opwek:.1f} %)" if opwek > 0 else ""
        regels.append(f"{'Run ' + r.letter if r.letter else 'This run'}: {g.getal(opwek / 1000, 1)} MWh "
                      f"generated, of which {g.getal(benut / 1000, 1)} MWh used and "
                      f"{g.getal(weg / 1000, 1)} MWh curtailed{deel}.")
    return " ".join(regels)


def kop_met_schakelaar(sleutel: str) -> bool:
    """The title of a graph, with a toggle next to it to fold it in or out.

    If the toggle is off, the figure is NOT built. That is the point: a folded-in graph in
    an expander would still be drawn and sent to the browser, and then the page is not any
    faster for it. The position stays through g.blijvend(), so also after another period, another
    run or another page.
    """
    # The title centred like the blocks on the KPI page, the toggle to the right of it.
    # The empty column on the left is as wide as the one of the toggle, otherwise the title
    # is not in the middle.
    _, midden, rechts = st.columns([0.2, 1, 0.2], vertical_alignment="center")
    with midden:
        g.blokkop(g.GRAFIEKEN[sleutel])
    return rechts.toggle("Show", key=g.blijvend(f"toon|{sleutel}", True),
                         help="Off: this graph is not drawn. That makes the page faster.")


def klap_alles(opties: list[str]) -> None:
    """Two buttons to fold all graphs in or out in one go."""
    links, rechts, _ = st.columns([0.22, 0.22, 0.56])
    # The button takes care of a rerun itself; the toggles below are made after that and
    # take over the value from session_state.
    if links.button("Fold all in", icon=":material/unfold_less:", width="stretch"):
        for sleutel in opties:
            st.session_state[f"toon|{sleutel}"] = False
    if rechts.button("Fold all out", icon=":material/unfold_more:", width="stretch"):
        for sleutel in opties:
            st.session_state[f"toon|{sleutel}"] = True


def grafiek(sleutel: str, reeksen: list, k: dict, venster: tuple, per_uur: bool, project: str) -> None:
    args = (reeksen, k, venster, per_uur)
    b = " The dashed lines are the edges of the stack of run B." if len(reeksen) > 1 else ""
    if sleutel == "netafname":
        fig, tabel = grafieken.netafname_en_prijs(*args)
    elif sleutel == "warmte":
        fig, tabel = grafieken.warmte(*args)
        st.caption("Stacked: what the heat sources deliver. The line is the heat demand of the "
                   "greenhouse; if the stack goes above it, the difference is charged into the buffer." + b)
    elif sleutel == "elektriciteit":
        fig, tabel = grafieken.elektriciteit(*args)
        st.caption("Stacked: where the electricity comes from. If the stack goes above the line, "
                   "the difference is charged into the battery." + b)
    elif sleutel == "pv":
        fig, tabel = grafieken.pv(*args)
        if fig is None:
            st.caption("No PV in this scenario.")
            return
        st.caption("Stacked: the generation of the field, split into what the system has used and what was "
                   "curtailed. The dark line is the generation itself and so coincides with the top of the "
                   "stack. Curtailing costs nothing (assumption D3) and feed-in does not exist in this model "
                   "(D2), so everything that cannot be used midday or fit in the battery disappears here. "
                   + pv_totalen(reeksen) + b)
    elif sleutel == "opslag":
        fig, tabel = grafieken.opslag(*args)
        if fig is None:
            st.caption("No buffer or battery in this scenario.")
            return
        st.caption("State of charge at the end of every step. Equal percentages are not equal kWh "
                   "with different storage sizes.")
    else:
        meting = origineel_erbij(reeksen, project)
        fig, tabel = grafieken.kosten(*args, meting=meting)
        uitleg = ("Added up from the start of the chosen period; the end point is equal to the costs "
                  "in the key figures.")
        if meting is not None:
            uitleg += (" Dotted: the existing system with the measured consumption of the company, so the "
                       "measured grid offtake × the price of that moment and the measured gas import × the "
                       "gas price, without efficiency. " + origineel_totalen(reeksen, meting) +
                       " If you compare this with a simulated basis run: that burns gas/efficiency for the "
                       "same heat (assumption B4) and comes out on gas about 11 % higher.")
        st.caption(uitleg)
    st.plotly_chart(fig, width="stretch", theme=None, key=f"grafiek|{sleutel}",
                    config={"displaylogo": False})
    with st.expander("Table with this data"):
        st.dataframe(tabel, width="stretch", height=260)


def opslaan(run: dict, project: str) -> None:
    pad = run["pad"]
    if run["opgeslagen"]:
        info = runbeheer.lees_json(pad / runbeheer.OPSLAGBESTAND)
        tekst = f"Saved in `{pad.relative_to(ROOT).as_posix()}`"
        if info.get("opmerking"):
            tekst += f" · {info['opmerking']}"
        st.success(tekst, icon=":material/check_circle:")
        return
    with g.blok("opslaan"):
        g.blokkop("Save in project")
        with g.vak("opslaan"):
            st.markdown(f"Copies this run to `projecten/{project}/output/`. Runs that are not "
                        "saved stay in `runs/`.")
            opmerking = st.text_input("Remark (optional)", placeholder="E.g. first run with a larger buffer",
                                      key=g.blijvend(f"opmerking|{pad.name}", ""))
        klaar = run["status"] == "complete"
        if not klaar:
            st.caption("Only a finished run can be saved.")
        if st.button("Save in project", type="primary", icon=":material/save:", disabled=not klaar):
            try:
                doel = runbeheer.sla_op(pad, project, opmerking)
            except (ValueError, FileExistsError, OSError) as fout:
                st.error(str(fout))
                return
            st.session_state["resultaat_run"] = str(doel)
            st.rerun()


g.paginatitel("Results")
project, _ = g.keuze()
if not project:
    st.info("First create a project on the General page.")
    st.stop()

runs = runbeheer.runs_van_project(project)
if not runs:
    st.info("There are no runs for this project yet. Start one on the General page.")
    st.stop()

# Run A and run B; g.blijvend holds the choice when you go to another page and come back.
per_pad = {str(r["pad"]): r for r in runs}
paden = list(per_pad)
gewenst = st.session_state.pop("resultaat_run", None)  # after 'View results' or 'Save in project'
if gewenst in per_pad:
    st.session_state[f"run_a|{project}"] = gewenst
c1, c2 = st.columns(2)
gekozen = c1.selectbox("Run", paden, format_func=lambda p: g.runlabel(per_pad[p]),
                       key=g.blijvend(f"run_a|{project}", paden[0], lambda p: p in per_pad))
anderen = [GEEN] + [p for p in paden if p != gekozen]
tweede = c2.selectbox("Compare with", anderen,
                      format_func=lambda p: "Do not compare" if p == GEEN else g.runlabel(per_pad[p]),
                      key=g.blijvend(f"run_b|{project}", GEEN, lambda p: p in anderen),
                      help="A second run (B) over run A, in the same colours but dashed. You switch lines "
                           "on and off by clicking their name in the legend.")
run = per_pad[gekozen]
pad = run["pad"]
vergelijking = per_pad.get(tweede)
for kolom, r in ((c1, run), (c2, vergelijking)):
    tekst = g.runbeschrijving(r) if r else ""
    if tekst:
        kolom.caption(tekst)

status = runbeheer.lees_json(pad / "run_status.json")
instellingen = g.runinstellingen(pad)
if run["status"] in ("running", "gestart"):
    st.info("This run is still going. The graphs show what has been carried out so far.", icon=":material/hourglass:")
    st.button("Refresh", icon=":material/refresh:")
elif run["status"] == "failed" and status.get("error"):
    st.error(f"This run stopped after {status.get('completed_steps', '?')} carried-out steps with this error:\n\n"
             f"{status['error']}", icon=":material/error:")

df = g.lees_dispatch(pad)
if df is None or df.empty:
    st.warning("This run has no results in dispatch.csv (yet).")
    st.stop()

df_b, status_b = None, {}
if vergelijking:
    df_b = g.lees_dispatch(vergelijking["pad"])
    status_b = runbeheer.lees_json(vergelijking["pad"] / "run_status.json")
    if df_b is None or df_b.empty:
        c2.warning("This run has no results in dispatch.csv (yet).")
        vergelijking, df_b = None, None
    elif vergelijking["status"] in ("running", "gestart"):
        c2.caption("Run B is still going: you see what has been carried out so far.")
    elif vergelijking["status"] == "failed":
        c2.caption(f"Run B stopped after {status_b.get('completed_steps', '?')} steps with an error: "
                   "you only see the part that was carried out.")

waarschuwingen(pad, status, "Run A: " if vergelijking else "")
if vergelijking:
    waarschuwingen(vergelijking["pad"], status_b, "Run B: ")

van, tot = kies_periode([d for d in (df, df_b) if d is not None], f"periode|{gekozen}|{tweede}")
dfa = df[(df.index >= van) & (df.index < tot)]
with g.blok("kengetallen"):
    g.blokkop("Key figures")
    if vergelijking:
        dfb = df_b[(df_b.index >= van) & (df_b.index < tot)]
        with g.vak("kengetallen"):
            verschiltabel(dfa, dfb, run, vergelijking)
        reeksen = [grafieken.Reeks(dfa, instellingen, "A"),
                   grafieken.Reeks(dfb, g.runinstellingen(vergelijking["pad"]), "B")]
    else:
        tegels(dfa)
        reeksen = [grafieken.Reeks(dfa, instellingen)]
reeksen = [r for r in reeksen if not r.df.empty]

kleuren = g.kleuren()
# Which graphs this page shows. A graph that was added AFTER the start of this session is
# not in the kept choice, but should appear: Streamlit reloads the code on a change without
# clearing session_state. A graph whose checkbox on the General page has been drawn once is
# a real choice of the user.
_bewaard = st.session_state.get("output_opties")
opties = [sleutel for sleutel in g.GRAFIEKEN
          if _bewaard is None or sleutel in _bewaard or f"optie|{sleutel}" not in st.session_state]
if not opties:
    st.caption("No graphs have been chosen. Choose them at Output options on the General page.")
elif not reeksen:
    st.info("None of the runs has data in the chosen period.")
else:
    per_uur = grafieken.per_uur_nodig(reeksen)
    uitleg = ["Click a name in the legend to switch that line on or off, double-click to see only that "
              "line. After another period or run all lines are on again."]
    if vergelijking:
        uitleg.insert(0, "Solid lines: run A. Dashed lines: run B.")
    if tot - van <= pd.Timedelta(days=grafieken.NACHT_MAX_DAGEN):
        uitleg.append("Grey bands: night (22:00–06:00).")
    if per_uur:
        uitleg.append("A run has more than 14 days of data in this period: the graphs show hourly averages. "
                      "Choose a period of 14 days or shorter for the values per 5 minutes. "
                      "The CSV files and the key figures are per 5 minutes.")
    uitleg.append("Fold a graph in with the toggle next to it if you do not need it: it is then not "
                  "drawn and the page reacts faster.")
    st.caption(" ".join(uitleg))
    klap_alles(opties)
    for sleutel in opties:
        # Every graph as a block, with the figure in a white box: the figures have a
        # transparent background, so they take over the white of the box.
        with g.blok(f"grafiek_{sleutel}"):
            if kop_met_schakelaar(sleutel):
                with g.vak(f"grafiek_{sleutel}"):
                    grafiek(sleutel, reeksen, kleuren, (van, tot), per_uur, project)
            else:
                st.caption("Folded in.")

opslaan(run, project)
