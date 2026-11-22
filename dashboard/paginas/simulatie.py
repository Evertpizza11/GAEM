"""Simulation page: MILP settings of the project (projecten/<project>/simulatie.json)."""

import math

import streamlit as st

from config.simulatie_catalogus import SIMULATIE_CATALOGUS
from dashboard import gedeeld as g
from utils.projectinstellingen import lees_simulatie, schrijf_simulatie, standaard_simulatie, valideer_simulatie


def _formaat(ps: dict) -> str:
    if ps["type"] == "int":
        return "%d"
    decimalen = max(0, -math.floor(math.log10(ps["stap"]))) if ps["stap"] < 1 else 0
    return f"%.{max(2, decimalen)}f"


def _widgetwaarde(waarde, ps: dict):
    if waarde is None or ps["type"] == "keuze":
        return waarde
    return int(waarde) if ps["type"] == "int" else float(waarde)


def concept(project: str) -> dict:
    concepten = st.session_state.setdefault("sim_concepten", {})
    if project not in concepten:
        concepten[project] = lees_simulatie(project)
    return concepten[project]


def vergeet_concept(project: str) -> None:
    st.session_state.get("sim_concepten", {}).pop(project, None)
    for sleutel in [k for k in st.session_state if str(k).startswith(f"sim|{project}|")]:
        del st.session_state[sleutel]


def standaard_terug(project: str) -> None:
    st.session_state.setdefault("sim_concepten", {})[project] = standaard_simulatie()
    for param, ps in SIMULATIE_CATALOGUS.items():
        st.session_state[f"sim|{project}|{param}"] = _widgetwaarde(standaard_simulatie()[param], ps)


g.paginatitel("Simulation settings")
project, _ = g.keuze()
if not project:
    st.info("First create a project on the General page.")
    st.stop()

st.caption(f"These settings apply to every run of project **{project}** and are stored in "
           f"`projecten/{project}/simulatie.json`. You set up the energy system on the Energy system page.")
instellingen = concept(project)
standaard = standaard_simulatie()
with g.blok("milp_instellingen"):
    g.blokkop("MILP settings")
    with g.vak("milp_instellingen"):
        links, rechts = st.columns(2, gap="large")
        for i, (param, ps) in enumerate(SIMULATIE_CATALOGUS.items()):
            kolom = links if i % 2 == 0 else rechts
            sleutel = f"sim|{project}|{param}"
            if sleutel not in st.session_state:
                st.session_state[sleutel] = _widgetwaarde(instellingen.get(param), ps)
            label = f"{ps['label']} ({ps['eenheid']})" if ps["eenheid"] not in ("", "-") else ps["label"]
            uitleg = ps["uitleg"] + f" Standaard: {standaard[param] if standaard[param] is not None else 'empty'}."
            if ps["type"] == "keuze":
                instellingen[param] = kolom.selectbox(label, list(ps["opties"]), key=sleutel, help=uitleg,
                                                      format_func=ps["opties"].get)
            else:
                instellingen[param] = kolom.number_input(label, key=sleutel, step=_widgetwaarde(ps["stap"], ps),
                                                         format=_formaat(ps), help=uitleg,
                                                         placeholder=ps.get("leeg_betekent", "not filled in"))

    fouten = valideer_simulatie(instellingen)
    if not fouten:
        horizon_stappen = round(instellingen["horizon_hours"] * 12)
        interval = instellingen["control_interval_steps"]
        st.info(f"Every optimisation looks **{g.getal(instellingen['horizon_hours'], 1)} hours** ahead "
                f"({horizon_stappen} steps) and of those it executes **{interval} steps "
                f"({g.getal(interval / 12, 2)} hours)**. A run of 24 hours therefore counts "
                f"{math.ceil(288 / interval)} optimisation(s).", icon=":material/schedule:")

gewijzigd = instellingen != lees_simulatie(project)
with g.blok("sim_opslaan"):
    g.blokkop("Check and save")
    for fout in fouten:
        st.error(fout, icon=":material/error:")
    if gewijzigd:
        st.warning("There are changes that have not been saved yet.", icon=":material/edit:")
    c1, c2, c3, _ = st.columns([1, 2, 2, 1])
    if c1.button("Save", type="primary", icon=":material/save:", disabled=bool(fouten) or not gewijzigd):
        schrijf_simulatie(project, dict(instellingen))
        vergeet_concept(project)
        st.toast("Simulation settings saved")
        st.rerun()
    if c2.button("Discard changes", icon=":material/undo:", disabled=not gewijzigd):
        vergeet_concept(project)
        st.rerun()
    c3.button("Default values", icon=":material/restart_alt:", on_click=standaard_terug, args=(project,),
              help="Resets everything to the values from config/ems_config.py. After that still save.")
