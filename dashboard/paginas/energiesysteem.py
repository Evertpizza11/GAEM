"""Energy system page: fill in the basis and the scenarios, per asset Enabled or Disabled.

Next to every input field stands the value from the basis (the existing system):
blue = equal to the basis, orange = changed. With the arrow you set the field
back to the basis value. Changes are only kept with 'Save'.
"""

import copy
import math
from html import escape

import streamlit as st

from config.asset_catalogus import CATALOGUS
from dashboard import gedeeld as g
from utils.projectinstellingen import (lees_scenario, schrijf_scenario, valideer_energiesysteem)
from utils.projectpaden import BASIS, bestaande_scenarios, controleer_naam, scenario_pad

ENABLED, DISABLED = "Enabled", "Disabled"

# The asset images and the header with picture and name are in dashboard/gedeeld.py,
# because the KPIs page uses them too. Two copies of the same table drift apart
# sooner or later.


def _formaat(ps: dict) -> str:
    if ps["type"] == "int":
        return "%d"
    decimalen = max(0, -math.floor(math.log10(ps["stap"]))) if ps["stap"] < 1 else 0
    return f"%.{max(2, decimalen)}f"


def _tekst(waarde, ps: dict) -> str:
    if waarde is None:
        return ps.get("leeg_betekent", "empty")
    if ps["type"] == "keuze":
        return ps["opties"].get(waarde, str(waarde))
    decimalen = 0 if float(waarde).is_integer() else 2
    eenheid = "" if ps["eenheid"] == "-" else ps["eenheid"]
    return f"{g.getal(waarde, decimalen)} {eenheid}".strip()


def _widgetwaarde(waarde, ps: dict):
    """Streamlit wants one number type per input field: int or float. A choice stays text."""
    if waarde is None or ps["type"] == "keuze":
        return waarde
    return int(waarde) if ps["type"] == "int" else float(waarde)


def _bewaarwaarde(waarde, ps: dict):
    """Store whole numbers such as 275 instead of 275.0; that reads more pleasantly in the JSON."""
    if waarde is None or ps["type"] in ("int", "keuze"):
        return waarde
    return int(waarde) if float(waarde).is_integer() else float(waarde)


def concept(project: str, scenario: str) -> dict:
    """Working version of the scenario. Stays kept when you go to another page."""
    concepten = st.session_state.setdefault("concepten", {})
    sleutel = f"{project}/{scenario}"
    if sleutel not in concepten:
        concepten[sleutel] = copy.deepcopy(lees_scenario(project, scenario)["energiesysteem"])
    return concepten[sleutel]


def vergeet_concept(project: str, scenario: str) -> None:
    st.session_state.get("concepten", {}).pop(f"{project}/{scenario}", None)
    g.vergeet(f"es|{project}|{scenario}|")


def zet_terug(sleutel: str, asset: dict, param: str, waarde, ps: dict | None) -> None:
    """Callback of the reset button: before the drawing, so the widget may be changed."""
    if ps is None:
        st.session_state[sleutel] = ENABLED if waarde else DISABLED
        asset["enabled"] = bool(waarde)
    else:
        st.session_state[sleutel] = _widgetwaarde(waarde, ps)
        asset[param] = waarde


def basislabel(tekst: str, gelijk: bool) -> str:
    return f":blue[basis: {tekst}]" if gelijk else f":orange[basis: {tekst} · changed]"


def toon_asset(naam: str, spec: dict, asset: dict, basis: dict | None, voorvoegsel: str) -> None:
    """One asset as a block, as on the KPI page: picture and name centred, the explanation
    below it, and the status and the input fields in a white box."""
    with g.blok(f"es_{naam}"):
        g.kop_asset(naam, spec["label"])
        st.markdown(f"<div style='text-align:center; color:#5b6472; font-size:0.9rem; "
                    f"max-width:60rem; margin:-6px auto 6px;'>{escape(spec['uitleg'])}</div>",
                    unsafe_allow_html=True)
        with g.vak(f"es_{naam}"):
            keuze, basiskolom, knop = st.columns([3, 3, 1], vertical_alignment="center")
            sleutel = f"{voorvoegsel}{naam}|enabled"
            if sleutel not in st.session_state:
                st.session_state[sleutel] = ENABLED if asset.get("enabled") else DISABLED
            asset["enabled"] = keuze.radio("Status", [ENABLED, DISABLED], key=sleutel, horizontal=True,
                                           label_visibility="collapsed") == ENABLED
            if basis is not None:
                b = basis.get(naam, {"enabled": False})
                gelijk = b.get("enabled", False) == asset["enabled"]
                basiskolom.markdown(basislabel("present" if b.get("enabled") else "not present", gelijk))
                knop.button("", icon=":material/undo:", key=f"{sleutel}|terug", disabled=gelijk,
                            help="Back to the basis", on_click=zet_terug,
                            args=(sleutel, asset, "enabled", b.get("enabled", False), None))

            groepen = spec.get("groepen") or []
            for groep in groepen:
                with st.container(border=True):
                    st.markdown(f"**{groep['label']}**")
                    toon_parameters(groep["parameters"], naam, spec, asset, basis, voorvoegsel)
            gegroepeerd = {p for groep in groepen for p in groep["parameters"]}
            toon_parameters([p for p in spec["parameters"] if p not in gegroepeerd],
                            naam, spec, asset, basis, voorvoegsel)


def toon_parameters(parameters: list[str], naam: str, spec: dict, asset: dict,
                    basis: dict | None, voorvoegsel: str) -> None:
    """Input fields in two columns, with the basis value and the reset button next to them."""
    raster = st.columns(2, gap="large")
    for i, param in enumerate(parameters):
        ps = spec["parameters"][param]
        sleutel = f"{voorvoegsel}{naam}|{param}"
        if sleutel not in st.session_state:
            st.session_state[sleutel] = _widgetwaarde(asset.get(param), ps)
        veld, basiskolom, knop = raster[i % 2].columns([6, 4, 1], vertical_alignment="bottom")
        # Within a group the little frame already carries the field number; then the short label.
        kop = ps.get("kort", ps["label"])
        label = f"{kop} ({ps['eenheid']})" if ps["eenheid"] not in ("", "-") else kop
        uitleg = ps["uitleg"] + (f" Leeg = {ps['leeg_betekent']}." if ps.get("optioneel") else "")
        # Some fields only have meaning with a certain choice elsewhere, such as the
        # source temperature that only exists with a water/water heat pump. Such a field
        # is in the catalogue with "actief_als"; it turns grey and empty when the
        # condition does not hold. The order in the catalogue makes sure the choice is
        # already filled in at the moment this field is drawn.
        actief = asset["enabled"] and all(
            asset.get(ander) == moet for ander, moet in ps.get("actief_als", {}).items())
        if ps["type"] == "keuze":
            opties = list(ps["opties"])
            if st.session_state.get(sleutel) not in opties:
                st.session_state[sleutel] = opties[0]
            waarde = veld.selectbox(label, opties, key=sleutel, help=uitleg,
                                    disabled=not actief,
                                    format_func=lambda k: ps["opties"][k])
        else:
            waarde = veld.number_input(label, key=sleutel, step=_widgetwaarde(ps["stap"], ps),
                                       format=_formaat(ps), help=uitleg, disabled=not actief,
                                       placeholder=ps.get("leeg_betekent", "not filled in"))
        # A field that does not apply should not end up in the scenario file either:
        # otherwise there is a source temperature with an air/water heat pump.
        asset[param] = _bewaarwaarde(waarde, ps) if actief or not ps.get("actief_als") else None
        if basis is not None and not basis.get(naam, {}).get("enabled"):
            basiskolom.markdown(":gray[basis: not present]")  # Nothing to compare with.
        elif basis is not None:
            bw = basis[naam].get(param)
            gelijk = bw == asset[param]
            basiskolom.markdown(basislabel(_tekst(bw, ps), gelijk))
            knop.button("", icon=":material/undo:", key=f"{sleutel}|terug", disabled=gelijk,
                        help="Back to the basis value", on_click=zet_terug,
                        args=(sleutel, asset, param, bw, ps))


def nieuw_scenario(project: str) -> None:
    with st.expander("New scenario", icon=":material/add:"):
        with st.form("nieuw_scenario", clear_on_submit=True, border=False):
            c1, c2 = st.columns(2)
            naam = c1.text_input("Name", placeholder="wp_en_buffer", help="Letters, digits, _ or -, without spaces.")
            bron = c2.selectbox("Start from", [BASIS] + bestaande_scenarios(project), format_func=g.scenarionaam)
            omschrijving = st.text_input("Description", placeholder="Heat pump 520 kWe and extra buffer 1000 m³")
            if st.form_submit_button("Create scenario", type="primary"):
                try:
                    naam = controleer_naam(naam, "scenario name")
                    if naam == BASIS:
                        raise ValueError("'basis' is reserved for the existing system.")
                    if scenario_pad(project, naam).exists():
                        raise ValueError(f"Scenario {naam} already exists.")
                    schrijf_scenario(project, naam, lees_scenario(project, bron)["energiesysteem"], omschrijving)
                except (ValueError, FileNotFoundError) as fout:
                    st.error(str(fout))
                else:
                    g.kies_volgende(scenario=naam)
                    st.rerun()


g.paginatitel("Energy system")
project, scenario = g.keuze()
if not project:
    st.info("First create a project on the General page.")
    st.stop()

nieuw_scenario(project)
bestand = lees_scenario(project, scenario)
st.header(g.scenarionaam(scenario))
if scenario == BASIS:
    st.info("You are editing the **existing system** of the greenhouse. Scenarios use this as reference: "
            "next to every field of a scenario stands the value from the basis.", icon=":material/info:")
    basis = None
else:
    basis = lees_scenario(project, BASIS)["energiesysteem"]
    st.caption("Next to every field stands the basis value: :blue[blue] = equal to the basis, "
               ":orange[orange] = changed. With the arrow you set a field back.")

# The input fields of the assets already survive a page switch through concept(); the
# description did not, because it read its value from the file and so lost a change that
# had not been saved yet. g.blijvend() holds on to it until Save or Discard.
omschrijving = st.text_input("Description",
                             key=g.blijvend(f"es|{project}|{scenario}|omschrijving",
                                            bestand.get("omschrijving", "")))
systeem = concept(project, scenario)
voorvoegsel = f"es|{project}|{scenario}|"
for naam, spec in CATALOGUS.items():
    systeem.setdefault(naam, {"enabled": False, **{p: None for p in spec["parameters"]}})
    toon_asset(naam, spec, systeem[naam], basis, voorvoegsel)

fouten = valideer_energiesysteem(systeem)
gewijzigd = systeem != bestand["energiesysteem"] or omschrijving != bestand.get("omschrijving", "")
with g.blok("es_opslaan"):
    g.blokkop("Check and save")
    if fouten:
        st.error("This energy system cannot be simulated yet:\n\n" + "\n".join(f"- {f}" for f in fouten),
                 icon=":material/error:")
    else:
        st.success("The energy system is complete and can be simulated.", icon=":material/check_circle:")
    if gewijzigd:
        st.warning("There are changes that have not been saved yet.", icon=":material/edit:")
    c1, c2, _ = st.columns([1, 2, 3])
    if c1.button("Save", type="primary", icon=":material/save:", disabled=not gewijzigd):
        schrijf_scenario(project, scenario, systeem, omschrijving)
        vergeet_concept(project, scenario)
        st.toast(f"{g.scenarionaam(scenario)} saved" + (" (not complete yet)" if fouten else ""))
        st.rerun()
    if c2.button("Discard changes", icon=":material/undo:", disabled=not gewijzigd):
        vergeet_concept(project, scenario)
        st.rerun()
