"""General page: choose or create a project, check the input data and start the simulation."""

from datetime import datetime, time, timedelta
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard import gedeeld as g
from utils import runbeheer
from utils.projectinstellingen import (MAANDNAMEN, PROJECTVELDEN, lees_json, lees_scenario,
                                       lees_simulatie, maak_project, ontbrekende_projectvelden,
                                       schrijf_json, valideer_energiesysteem,
                                       valideer_simulatie, weerbestanden)
from utils.projectpaden import (PROJECT_BESTAND, RAW_GAS, ROOT, bestaande_projecten, project_map,
                                raw_map)
from utils.weerjaren import WEERJAREN

STAP = timedelta(minutes=5)


def _projectveld(naam: str, veld: dict, waarde, sleutel: str):
    """One input field from PROJECTVELDEN. Returns the filled-in value, or None.

    Empty is allowed: a required field that stays empty gives a warning later, but it
    must not hold up a project. You do not always know the enveloping surface of a
    greenhouse at the moment you create the project.
    """
    label = veld["label"] + (f" [{veld['eenheid']}]" if veld.get("eenheid") else "")
    if veld["verplicht"]:
        label += " \\*"
    if veld["type"] == "weerbestand":
        keuzes = [""] + weerbestanden()
        gekozen = st.selectbox(label, keuzes, key=sleutel, help=veld["help"],
                               index=keuzes.index(waarde) if waarde in keuzes else 0,
                               format_func=lambda n: n or "automatic")
        return gekozen or None
    if veld["type"] == "maanden":
        # Returned sorted, so that an unchanged choice is not seen as a change
        # because the order differs from what is in the file.
        try:
            huidig = sorted({int(m) for m in (waarde or ())})
        except (TypeError, ValueError):
            huidig = []
        gekozen = st.multiselect(label, list(MAANDNAMEN), key=sleutel, help=veld["help"],
                                 default=[m for m in huidig if m in MAANDNAMEN],
                                 format_func=lambda m: MAANDNAMEN[m],
                                 placeholder="none, cultivation all year")
        return sorted(gekozen) or None
    # Forced to float. In a json 275 is an int and 0.5 a float, and Streamlit
    # refuses a value whose type differs from that of min_value and step.
    return st.number_input(
        label, key=sleutel, help=veld["help"],
        value=None if waarde is None else float(waarde),
        min_value=veld.get("min"), max_value=veld.get("max"), step=veld.get("stap", 1.0),
        format=f"%.{veld.get('decimalen', 2)}f", placeholder="not filled in yet")


def _projectvelden(huidig: dict, voorvoegsel: str) -> dict:
    """The properties of a project as two columns of input fields."""
    st.caption("Fields with \\* are needed to be able to run a simulation.")
    uit = {}
    kolommen = st.columns(2)
    for i, (naam, veld) in enumerate(PROJECTVELDEN.items()):
        with kolommen[i % 2]:
            uit[naam] = _projectveld(naam, veld, huidig.get(naam, veld.get("standaard")),
                                     f"{voorvoegsel}_{naam}")
    return uit


def nieuw_project(open_: bool) -> None:
    with st.expander("New project", expanded=open_, icon=":material/add:"):
        with st.form("nieuw_project", clear_on_submit=True, border=False):
            naam = st.text_input("Project name", placeholder="Voorbeeld_kas",
                                 help="Letters, digits, _ or -, without spaces.")
            c1, c2 = st.columns(2)
            locatie = c1.text_input("Location", placeholder="Venlo")
            bron = c2.selectbox("Existing energy system (basis)",
                                ["Start empty"] + [f"Copy from {p}" for p in bestaande_projecten()])
            omschrijving = st.text_input("Description")
            st.divider()
            velden = _projectvelden({}, "nieuw")
            if st.form_submit_button("Create project", type="primary"):
                try:
                    if not naam.strip():
                        raise ValueError("Fill in a project name.")
                    if project_map(naam).exists():
                        raise ValueError(f"Project {naam} already exists.")
                    basis_van = None if bron == "Start empty" else bron.removeprefix("Copy from ")
                    maak_project(naam.strip(), locatie, omschrijving, basis_van=basis_van,
                                 velden=velden)
                except (ValueError, FileNotFoundError) as fout:
                    st.error(str(fout))
                else:
                    g.kies_volgende(project=naam.strip())
                    st.rerun()


def projectgegevens(project: str) -> None:
    pad = project_map(project) / PROJECT_BESTAND
    info = lees_json(pad)
    ontbreekt = ontbrekende_projectvelden(project)
    if ontbreekt:
        namen = ", ".join(PROJECTVELDEN[n]["label"] for n in ontbreekt)
        st.warning(f"This project is missing {namen}. Without that data a simulation stops. "
                   "Fill them in below.", icon=":material/warning:")
    with g.blok("projectgegevens"):
        g.blokkop("Project data")
        with g.vak("projectgegevens"):
            c1, c2 = st.columns([1, 2])
            # Through g.blijvend, otherwise a change that has not been saved yet is gone as soon
            # as you go to another page for a moment.
            locatie = c1.text_input("Location", key=g.blijvend(f"locatie|{project}",
                                                              info.get("locatie", "")))
            omschrijving = c2.text_input("Description", key=g.blijvend(f"omschrijving|{project}",
                                                                       info.get("omschrijving", "")))
            with st.expander("Properties of the greenhouse and the contract",
                             expanded=bool(ontbreekt), icon=":material/tune:"):
                velden = _projectvelden(info, f"veld|{project}")

        # Only write out filled-in fields. A field that is made empty disappears
        # from the file; that way 'the key is missing' stays the signal for 'not set',
        # and a null that could be read as a value never ends up in it.
        nieuw = {**{k: v for k, v in info.items() if k not in PROJECTVELDEN},
                 "locatie": locatie, "omschrijving": omschrijving,
                 **{k: v for k, v in velden.items() if v not in (None, "")}}
        if nieuw != info:
            if st.button("Save project data", icon=":material/save:", type="primary"):
                schrijf_json(pad, nieuw)
                st.rerun()


def invoerdata(project: str, scenario: str) -> dict:
    with g.blok("invoerdata"):
        g.blokkop("Input data")
        return _invoerdata(project)


def _invoerdata(project: str) -> dict:
    d = g.datastatus(project)
    raw_rel = raw_map(project).relative_to(ROOT).as_posix()
    if d["raw_ontbreekt"]:
        st.warning(f"In `{raw_rel}` the following is missing: {', '.join(d['raw_ontbreekt'])}. "
                   "Put the measured electricity demand there.", icon=":material/folder_open:")
        return d
    if not d["gas_aanwezig"]:
        st.caption(f"There is no `{RAW_GAS}` in `{raw_rel}`. That file is optional: the heat demand "
                   "always comes from the weather data. Without it the comparison with the measured "
                   "situation falls away on Results and KPIs; on Financial you can compare with a "
                   "simulated basis run instead.")
    if d["processed"]:
        # Smaller than the key figures on Results and Financial: a date with a time is
        # too long for that size in three columns.
        with g.vak("invoerdata_periode"):
            c1, c2, c3 = st.columns(3)
            c1.metric("First moment", d["begin"].strftime("%d-%m-%Y %H:%M"))
            c2.metric("Last moment", d["eind"].strftime("%d-%m-%Y %H:%M"))
            c3.metric("Period", f"{g.getal(d['stappen'] / 288, 1)} days")
    if not d["processed"] or not d.get("actueel", False):
        tekst = ("The raw data has not been processed yet." if not d["processed"]
                 else "The raw data is newer than the processed data.")
        st.info(tekst + " A run does this by itself; with the button you see the period right away.",
                icon=":material/sync:")
        if st.button("Process raw data", icon=":material/sync:"):
            with st.spinner("Processing raw data..."):
                uitkomst = runbeheer.verwerk_data(project)
            if uitkomst.returncode:
                st.error("Processing failed:\n\n" + uitkomst.stdout[-1500:] + uitkomst.stderr[-1500:])
            else:
                st.rerun()
    return d


def controleer_run(project: str, scenario: str, d: dict) -> list[str]:
    problemen = []
    try:
        problemen += valideer_energiesysteem(lees_scenario(project, scenario)["energiesysteem"])
    except (FileNotFoundError, ValueError) as fout:
        problemen.append(str(fout))
    try:
        problemen += [f"Simulation settings: {f}" for f in valideer_simulatie(lees_simulatie(project))]
    except ValueError as fout:
        problemen.append(str(fout))
    if d["raw_ontbreekt"]:
        problemen.append("There is no input data yet.")
    return problemen


def output_opties() -> None:
    st.markdown("**Output options** · which graphs the Results page shows")
    gekozen = st.session_state.setdefault("output_opties", list(g.GRAFIEKEN))
    kolommen = st.columns(3)
    nieuw = [sleutel for i, (sleutel, titel) in enumerate(g.GRAFIEKEN.items())
             if kolommen[i % 3].checkbox(titel, value=sleutel in gekozen, key=f"optie|{sleutel}")]
    st.session_state["output_opties"] = nieuw


def simulatie_starten(project: str, scenario: str, d: dict) -> None:
    with g.blok("simulatie_starten"):
        g.blokkop("Start simulation")
        st.markdown(f"<div style='text-align:center;'>Scenario: <b>{escape(g.scenarionaam(scenario))}</b>"
                    " · choose another scenario in the sidebar. The MILP settings are on the "
                    "Simulation page.</div>", unsafe_allow_html=True)
        _simulatie_starten(project, scenario, d)


def _simulatie_starten(project: str, scenario: str, d: dict) -> None:
    problemen = controleer_run(project, scenario, d)

    # g.blijvend: the choices stay when you go to another page and come back.
    alles, start, uren, weerjaar = False, None, 24.0, "TMY"
    with g.vak("startmoment"):
        c1, c2, c3 = st.columns(3)
        if d["processed"]:
            begin, eind = d["begin"].date(), d["eind"].date()
            datum = c1.date_input("Start date", min_value=begin, max_value=eind, format="DD-MM-YYYY",
                                  key=g.blijvend(f"startdatum|{project}", begin, lambda w: begin <= w <= eind))
            tijden = [time(u, m) for u in range(24) for m in range(0, 60, 5)]
            tijd = c2.selectbox("Start time", tijden, format_func=lambda t: t.strftime("%H:%M"),
                                key=g.blijvend(f"starttijd|{project}", tijden[0], lambda w: w in tijden))
            start = pd.Timestamp(datetime.combine(datum, tijd))
        else:
            c1.caption("You choose the start date as soon as the data is processed. Without a choice the run starts "
                       "at the beginning of the data.")
        uren = c3.number_input("Duration (hours)", min_value=0.25, step=24.0, format="%.2f",
                               key=g.blijvend(f"duur|{project}", 24.0), help="24 = 1 day, 168 = 1 week, 720 = 30 days.")
        # The weather year belongs to the question you ask, just like the start moment and the
        # duration, so it is here and not with the project settings or the MILP settings.
        c4, _ = st.columns([1, 2])
        weerjaar = c4.selectbox(
            "Weather year", list(WEERJAREN), key=g.blijvend(f"weerjaar|{project}", "TMY"),
            format_func=lambda label: f"{label} - {WEERJAREN[label][1]}",
            help="The heat demand, the outdoor temperature, the COP of the heat pump and the "
                 "PV yield all come from this weather file. The label will later be "
                 "in the name of the run folder and in run_info.json.")
        alles = st.checkbox("Simulate the whole dataset (can take very long)",
                            key=g.blijvend(f"alles|{project}", False))
        vergelijk = st.checkbox("Also run the hierarchical controller for comparison (--compare)",
                                key=g.blijvend(f"vergelijk|{project}", False),
                                help="Runs the priority lists over the same period and writes the "
                                     "result in the run folder, so the two controllers can be held "
                                     "against each other.")
        if alles:
            uren = None
        elif abs(uren * 12 - round(uren * 12)) > 1e-9:
            problemen.append("The duration must be a multiple of 5 minutes.")
        if start is not None and d["processed"]:
            if not d["begin"] <= start <= d["eind"]:
                problemen.append("The start time lies outside the data.")
            elif uren is not None and start + pd.Timedelta(hours=uren) > d["eind"] + STAP:
                beschikbaar = (d["eind"] + STAP - start) / pd.Timedelta(hours=1)
                problemen.append(f"From this start there is {g.getal(beschikbaar, 1)} hours of data left.")
            if alles:
                st.caption(f"From {start:%d-%m-%Y %H:%M} until the end of the data.")

    with g.vak("output_opties"):
        output_opties()

    bezig = "actieve_run" in st.session_state
    for probleem in problemen:
        st.error(probleem, icon=":material/error:")
    if st.button("Start simulation", type="primary", icon=":material/play_arrow:",
                 disabled=bool(problemen) or bezig):
        startnotatie = start.strftime("%Y-%m-%d %H:%M") if start is not None else None
        proces, runmap = runbeheer.start_milp(project, scenario, uren, startnotatie, vergelijk,
                                              weerjaar=weerjaar)
        st.session_state["actieve_run"] = {"proces": proces, "pad": str(runmap), "project": project}
        st.session_state.pop("laatste_run", None)
        st.rerun()


@st.fragment(run_every="2s")
def voortgang() -> None:
    actief = st.session_state.get("actieve_run")
    if not actief:
        return
    proces, pad = actief["proces"], Path(actief["pad"])
    v = runbeheer.voortgang(pad)
    loopt = proces.poll() is None
    with g.blok("voortgang"):
        g.blokkop("Simulation is running")
        with g.vak("voortgang"):
            st.markdown(f"`{pad.name}`")
            if v["fractie"] is not None:
                st.progress(v["fractie"], text=f"Step {v['stap']} of {v['totaal']} · "
                                               f"{v['optimalisaties']} optimisation(s)")
            else:
                st.progress(0.0, text="Preparing: reading data and building the first model...")
            st.code(v["logstaart"] or "(no output yet)", language=None)
        if loopt and st.button("Stop simulation", icon=":material/stop:"):
            runbeheer.stop(proces, pad)
            loopt = False
    if not loopt:
        st.session_state.pop("actieve_run", None)
        st.session_state["laatste_run"] = str(pad)
        st.rerun()


def laatste_run() -> None:
    pad = st.session_state.get("laatste_run")
    if not pad:
        return
    v = runbeheer.voortgang(Path(pad))
    if v["status"] == "complete":
        st.success(f"Simulation finished: `{Path(pad).name}`", icon=":material/check_circle:")
        if st.button("View results", type="primary", icon=":material/monitoring:"):
            st.session_state["resultaat_run"] = pad
            st.switch_page("paginas/resultaten.py")
    elif v["status"] == "gestopt":
        st.warning(f"Simulation stopped: `{Path(pad).name}`. What was already computed is in the folder.")
    else:
        st.error(f"Simulation did not succeed ({v['status']}): `{Path(pad).name}`")
        st.code(v["logstaart"], language=None)


g.paginatitel("General")
project, scenario = g.keuze()
if not project:
    st.write("Create a first project. After that you put the consumption data of the company in "
             "`projecten/<naam>/input/raw` and you fill in the existing system on the Energy system page.")
    nieuw_project(open_=True)
    st.stop()

st.header(project)
projectgegevens(project)
nieuw_project(open_=False)
data = invoerdata(project, scenario)
simulatie_starten(project, scenario, data)
if "actieve_run" in st.session_state:
    voortgang()  # Refreshes itself every 2 seconds as long as the run is going.
laatste_run()
