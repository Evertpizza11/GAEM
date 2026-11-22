"""What all pages of the dashboard share: project choice, colours and reading data.

The dashboard never loads the MODEL itself (config.assets_config etc.). A run
always starts as a separate process through utils/runbeheer.py. That way an old scenario
can never stay hanging in the memory of the dashboard.
"""

from __future__ import annotations

import base64
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from config.asset_catalogus import CATALOGUS
from utils import runbeheer
from utils.projectinstellingen import lees_json
from utils.projectpaden import (BASIS, PROCESSED_GAS, PROCESSED_GECOMBINEERD, RAW_ELEKTRICITEIT, RAW_GAS,
                                bestaande_projecten, bestaande_scenarios, processed_map, raw_map)

# Set project/scenario choice for the next run of the script. A page
# may not change the choice list in the sidebar directly after it has been drawn.
VOLGEND_PROJECT, VOLGEND_SCENARIO = "_volgend_project", "_volgend_scenario"

GRAFIEKEN = {
    "netafname": "Grid import and electricity price",
    "warmte": "Heat: production and demand",
    "elektriciteit": "Electricity: supply and demand",
    "pv": "PV: generated, used and curtailed",
    "opslag": "State of charge of the storage",
    "kosten": "Cumulative cost",
}

# --------------------------------------------------------------------------
# Asset images
# --------------------------------------------------------------------------
# Are here and not on a page, because both Energy system and KPIs use them. Two
# copies of the same table drift apart sooner or later.
AFBEELDINGEN_MAP = Path(__file__).resolve().parent / "afbeeldingen"
ASSET_AFBEELDINGEN = {
    "pv": "solar-panel.png",
    "grid": "power-grid.png",
    "gas_boiler": "Gas boiler.png",
    "tess": "water-tank.png",
    "heat_pump": "heat pump.png",
    "bess": "battery.png",
}


def afbeeldingspad(naam: str) -> Path | None:
    """The path of the image of an asset, regardless of capitals.

    On disk there is 'Heat pump.png' with a small p, in the table above with a
    capital. Windows does not care about that, Linux does. This function therefore keeps
    looking if the exact name does not exist, so that the dashboard does not only work on a
    Windows machine.
    """
    bestandsnaam = ASSET_AFBEELDINGEN.get(naam)
    if bestandsnaam is None or not AFBEELDINGEN_MAP.is_dir():
        return None
    pad = AFBEELDINGEN_MAP / bestandsnaam
    if pad.is_file():
        return pad
    doel = bestandsnaam.lower()
    return next((p for p in AFBEELDINGEN_MAP.iterdir() if p.name.lower() == doel), None)


def _tegel(pad: Path, label: str, grootte: int, marge: str = "0") -> str:
    """The HTML of one image on a white area with a small border."""
    afbeelding = base64.b64encode(pad.read_bytes()).decode("ascii")
    return (f'<div style="background:#fff; border:1px solid #e5e7eb; border-radius:14px; '
            f'width:{grootte}px; max-width:100%; aspect-ratio:1; display:flex; '
            f'align-items:center; justify-content:center; box-sizing:border-box; '
            f'padding:10px; margin:{marge};">'
            f'<img src="data:image/png;base64,{afbeelding}" alt="{escape(label, quote=True)}" '
            'style="display:block; width:100%; height:100%; object-fit:contain;" />'
            '</div>')


def toon_afbeelding(naam: str, label: str, grootte: int = 88) -> None:
    """Show the asset on a white area with a small border."""
    pad = afbeeldingspad(naam)
    if pad is None:
        return
    st.markdown(_tegel(pad, label, grootte), unsafe_allow_html=True)


def kop_asset(naam: str, label: str, grootte: int = 110,
              links: str = "", rechts: str = "") -> None:
    """The header of an asset block: the image with the name NEXT TO it, centred together.

    In one HTML block and not with st.columns, because columns can align the picture
    but cannot centre it together with the title as a whole.

    LINKS and RECHTS are optional bits of HTML next to the whole, on the KPI page the
    set-up of run A and run B (see opstelling_html). They both get the same
    flexible width, also when only one is filled: that keeps picture and name
    exactly in the middle, with or without a comparison run.
    """
    pad = afbeeldingspad(naam)
    tegel = _tegel(pad, label, grootte) if pad else ""
    midden = (f'<div style="display:flex; align-items:center; gap:18px; flex:0 0 auto;">'
              f'{tegel}'
              f'<div style="font-size:1.75rem; font-weight:700; line-height:1.2;">'
              f'{escape(label)}</div></div>')
    if links or rechts:
        zijkant = 'flex:1 1 0; min-width:220px; display:flex;'
        inhoud = (f'<div style="{zijkant} justify-content:flex-end;">{links}</div>'
                  f'{midden}'
                  f'<div style="{zijkant} justify-content:flex-start;">{rechts}</div>')
    else:
        inhoud = midden
    st.markdown(
        '<div style="display:flex; align-items:center; justify-content:center; '
        f'gap:28px; margin:4px 0 18px; flex-wrap:wrap;">{inhoud}</div>',
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# The set-up of an asset, short, next to its header on the KPI page
# --------------------------------------------------------------------------
# Only the settings that determine WHAT is there, not how it behaves: efficiencies,
# run times and start values are left out. Label and unit come from the catalogue, so
# that they have the same name here as on the Energy system page.
OPSTELLING = {
    "grid": ("max_import_kw",),
    "gas_boiler": ("min_capacity_kwth", "max_capacity_kwth"),
    "tess": ("volume_m3",),
    "heat_pump": ("brontype", "min_capacity_kwe", "max_capacity_kwe", "control_stages"),
    "bess": ("capacity_kwh", "capacity_in_kw", "capacity_out_kw"),
}
PV_VLAKKEN = range(1, 5)
_WINDSTREKEN = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _kort(waarde) -> str:
    """A setting as a number: whole without decimals, otherwise with one."""
    getal_ = float(waarde)
    return getal(getal_, 0 if getal_.is_integer() else 1)


def _eenheid_html(eenheid: str) -> str:
    """kWth and kWe with a subscript, as in the thesis; '-' means no unit."""
    if not eenheid or eenheid == "-":
        return ""
    for staart in ("th", "e"):
        if eenheid.startswith("kW") and eenheid.endswith(staart):
            return f"kW<sub>{staart}</sub>"
    return escape(eenheid)


def _windstreek(azimut: float) -> str:
    """0° north, 90° east, 180° south, 270° west (see the explanation in the catalogue)."""
    return _WINDSTREKEN[int(((azimut % 360) + 22.5) // 45) % 8]


def opstelling(asset: str, instellingen: dict | None) -> list[tuple[str, str]] | str:
    """The set-up of one asset in one run: lines (label, value as HTML).

    A text instead of lines if there is nothing to list: the asset is off, or the
    run has no energy system saved in run_info.json.
    """
    if instellingen is None:
        return "Set-up not saved in this run"
    if not instellingen.get("enabled"):
        return "Not in this scenario"
    spec = CATALOGUS.get(asset, {}).get("parameters", {})

    if asset == "pv":
        regels = []
        for i in PV_VLAKKEN:
            panelen = instellingen.get(f"vlak{i}_panelen")
            if not panelen:
                continue
            # Empty means according to the catalogue: flat, and facing south.
            helling = instellingen.get(f"vlak{i}_helling")
            helling = spec[f"vlak{i}_helling"]["neutraal"] if helling is None else helling
            azimut = instellingen.get(f"vlak{i}_azimut")
            azimut = spec[f"vlak{i}_azimut"]["neutraal"] if azimut is None else azimut
            regels.append((f"Field {i}",
                           f"{_kort(panelen)} panels · tilt {_kort(helling)}° · "
                           f"orientation {_kort(azimut)}° ({_windstreek(float(azimut))})"))
        # Without panels the model plays back the measured profile (see the catalogue).
        return regels or "Measured generation profile, no fields set"

    regels = []
    for naam in OPSTELLING.get(asset, ()):
        p = spec.get(naam, {})
        waarde = instellingen.get(naam)
        if waarde is None:
            tekst = "–"
        elif p.get("type") == "keuze":
            tekst = p.get("opties", {}).get(waarde, str(waarde))
            tekst = escape(tekst.split(" (")[0])      # 'Lucht/water (bron = ...)' -> 'Lucht/water'
            bron = instellingen.get("bron_temperatuur_c")
            if naam == "brontype" and waarde == "water_water" and bron is not None:
                tekst += f", source {_kort(bron)} °C"
        else:
            eenheid = _eenheid_html(p.get("eenheid", ""))
            tekst = f"{_kort(waarde)} {eenheid}".strip()
        regels.append((escape(p.get("label", naam)), tekst))
    return regels


def opstelling_html(asset: str, instellingen: dict | None, runlabel: str,
                    uitlijning: str = "left") -> str:
    """The set-up as a small block: the run label above it, below that label and value.

    A grid of two columns, so that the values start below each other. Run A is left
    of the picture and aligns towards the picture, run B right and so the other way around.
    """
    inhoud = opstelling(asset, instellingen)
    kop = (f'<div style="grid-column:1 / -1; font-size:0.8rem; font-weight:600; '
           f'color:#6b7280; letter-spacing:0.02em; margin-bottom:2px; '
           f'text-align:{uitlijning};">{escape(runlabel)}</div>')
    if isinstance(inhoud, str):
        rijen = (f'<div style="grid-column:1 / -1; color:#6b7280; font-style:italic; '
                 f'text-align:{uitlijning};">{escape(inhoud)}</div>')
    else:
        rijen = "".join(f'<div style="color:#4b5563;">{label}</div>'
                        f'<div style="font-weight:600;">{waarde}</div>'
                        for label, waarde in inhoud)
    return (f'<div style="display:grid; grid-template-columns:auto auto; column-gap:14px; '
            f'row-gap:2px; font-size:0.95rem; line-height:1.45;">{kop}{rijen}</div>')


# --------------------------------------------------------------------------
# House style: the styling of the KPI page, for all pages
# --------------------------------------------------------------------------
# Every page is built up in the same way:
#
#     paginatitel()         large centred title, with the styling below included
#     choices and texts     on the page itself (project choice, run choice, period)
#     blok()                light grey-blue area per part, with blokkop() or kop_asset()
#         vak()             white area inside it, for input fields, tables and graphs
#         kerncijfers()     white cards with a big number, like the KPIs
#
# Input fields belong in a white VAK and not directly on a blok: Streamlit gives an
# input field the second background colour of the theme, and that is exactly the colour of
# the blok. Directly on a blok the field therefore disappears into its background.
#
# Two things cannot be arranged with the theme or with a parameter: a bordered
# container gets no fill colour from Streamlit, only a border, and the font size of
# st.metric is fixed.
#
# The fill colour hooks onto the KEY of a container. If you give your st.container a key,
# Streamlit puts a class 'st-key-<key>' on it, and that is a documented hook
# that is meant exactly for this. So not the internal class names of Streamlit, which
# can change with every version. That is why the keys start with a fixed prefix:
# groepblok_ (blok), witvak_ (vak), kerncijfer_ and kpiblok_ (white cards with a number).
#
# The size of st.metric does hang on a data-testid. That is the only fragile bit:
# should Streamlit ever change that name, then the numbers become standard size again and
# the rest simply keeps working.
VULLING_BLOK = "#e8edf5"   # blok: light grey-blue
VULLING_KPI = "#ffffff"    # vak and card inside it: white, so that it stands out
TITELMAAT = "4rem"         # paginatitel; a normal h1 is 2.75rem
BLOKKOPMAAT = "1.75rem"    # name of a blok or asset
GETALMAAT = "2.9rem"       # the number of a KPI or key figure


def huisstijl() -> None:
    """The styling of all pages. Calls paginatitel(); calling it yourself is not needed."""
    st.markdown(f"""
        <style>
        [class*="st-key-groepblok"] {{
            background:{VULLING_BLOK};
            border-radius:16px;
            padding:10px 18px 20px;
        }}
        [class*="st-key-kpiblok"],
        [class*="st-key-kerncijfer"],
        [class*="st-key-witvak"] {{
            background:{VULLING_KPI};
            border-radius:12px;
        }}
        /* Also the innermost element, with !important: Streamlit puts its own font size on
           the value of a metric, and otherwise that wins over inheritance. */
        [class*="st-key-kpiblok"] [data-testid="stMetricValue"],
        [class*="st-key-kpiblok"] [data-testid="stMetricValue"] * {{
            font-size:{GETALMAAT} !important;
            line-height:1.15 !important;
        }}
        /* A key figure is often an amount of a million in a card of one third of the
           width. On a narrower screen that got cut off ('EUR 1.06...'). That is why
           the number shrinks along with the width of the card (cqi = percent of that
           width), up to at most the size of the KPIs. All cards in a row are equally
           wide, so their numbers stay equally large. */
        [class*="st-key-kerncijfer"] {{
            container-type:inline-size;
        }}
        [class*="st-key-kerncijfer"] [data-testid="stMetricValue"],
        [class*="st-key-kerncijfer"] [data-testid="stMetricValue"] * {{
            font-size:min({GETALMAAT}, 15cqi) !important;
            line-height:1.15 !important;
        }}
        [class*="st-key-kpiblok"] [data-testid="stMetricDelta"],
        [class*="st-key-kpiblok"] [data-testid="stMetricDelta"] *,
        [class*="st-key-kerncijfer"] [data-testid="stMetricDelta"],
        [class*="st-key-kerncijfer"] [data-testid="stMetricDelta"] * {{
            font-size:1.15rem !important;
        }}
        /* ALIGNMENT on the KPI page. A has no difference under it and B does. With
           centred columns the number of A therefore drops a few pixels; the columns
           are therefore on 'top' and then both numbers start at the same height. The name
           on the left and the badge on the right would then end up next to the small label
           of the metric instead of next to the number, which is why those two columns get
           a little push downwards. */
        [class*="st-key-kpiblok"] [data-testid="stColumn"]:first-child,
        [class*="st-key-kpiblok"] [data-testid="stColumn"]:last-child {{
            padding-top:1.55rem;
        }}
        </style>
        """, unsafe_allow_html=True)


def paginatitel(tekst: str) -> None:
    """The title of a page, large and centred, with the house style included.

    No CSS on Streamlit class names, only an own element with a style on it; that
    does not break with a next version.
    """
    st.markdown(f"<h1 style='text-align:center; font-size:{TITELMAAT}; line-height:1.1; "
                f"margin-bottom:0.2em;'>{escape(tekst)}</h1>", unsafe_allow_html=True)
    huisstijl()


def blokkop(tekst: str) -> None:
    """The name of a blok: large, bold and centred, like the assets on the KPI page."""
    st.markdown(f"<div style='text-align:center; font-size:{BLOKKOPMAAT}; font-weight:700; "
                f"margin:10px 0 18px;'>{escape(tekst)}</div>", unsafe_allow_html=True)


def blok(sleutel: str):
    """A light grey-blue area for one part of a page. Use: with g.blok(...):"""
    return st.container(border=True, key=f"groepblok_{sleutel}")


def vak(sleutel: str):
    """A white area within a blok, for input fields, tables and graphs."""
    return st.container(border=True, key=f"witvak_{sleutel}")


def kerncijfers(paren: list[tuple], sleutel: str, per_rij: int = 3) -> None:
    """Key figures as white cards with a big number, at most per_rij next to each other.

    paren: (title, value) or (title, value, explanation). Fewer than per_rij figures get
    the whole width together; more are spread over rows of per_rij, because amounts of
    a million do not fit otherwise.
    """
    breedte = min(per_rij, len(paren)) or 1
    for begin in range(0, len(paren), breedte):
        kolommen = st.columns(breedte)
        for i, (kolom, paar) in enumerate(zip(kolommen, paren[begin:begin + breedte])):
            titel, waarde, *rest = paar
            with kolom, st.container(border=True, key=f"kerncijfer_{sleutel}_{begin + i}"):
                st.metric(titel, waarde, help=rest[0] if rest else None)


def kies_volgende(project: str | None = None, scenario: str | None = None) -> None:
    """Let the sidebar show another project/scenario at the next reload."""
    if project:
        st.session_state[VOLGEND_PROJECT] = project
    if scenario:
        st.session_state[VOLGEND_SCENARIO] = scenario


def zijbalk() -> tuple[str | None, str | None]:
    """Project and scenario choice in the sidebar; applies to all pages."""
    projecten = bestaande_projecten()
    if VOLGEND_PROJECT in st.session_state:
        st.session_state["project"] = st.session_state.pop(VOLGEND_PROJECT)
    if st.session_state.get("project") not in projecten:
        st.session_state.pop("project", None)
    with st.sidebar:
        st.markdown("### Greenhouse Asset Exploration Model")
        if not projecten:
            st.info("No project yet. Create one on the General page.")
            return None, None
        project = st.selectbox("Project", projecten, key="project")
        scenarios = [BASIS] + bestaande_scenarios(project)
        if VOLGEND_SCENARIO in st.session_state:
            st.session_state["scenario"] = st.session_state.pop(VOLGEND_SCENARIO)
        if st.session_state.get("scenario") not in scenarios:
            # By default the first real scenario; if there are none yet, then the basis.
            st.session_state["scenario"] = scenarios[1] if len(scenarios) > 1 else BASIS
        scenario = st.selectbox("Scenario", scenarios, key="scenario",
                                format_func=lambda s: "basis (existing system)" if s == BASIS else s)
        actief = st.session_state.get("actieve_run")
        if actief:
            st.caption(f"A simulation is running: {Path(actief['pad']).name}")
    return project, scenario


# --------------------------------------------------------------------------
# Colours: fixed colour per asset, the same in every page and every graph.
# Checked for colour blindness per graph (dataviz validator, light and dark).
# --------------------------------------------------------------------------

# 'nacht' is the background band for 22:00-06:00 and 'vlak' the fill under a single line:
# both a light shade of grey, not a series colour.
_KLEUREN = {
    "licht": {"net": "#2a78d6", "gasketel": "#eb6834", "warmtepomp": "#1baf7a", "buffer": "#4a3aa7",
              "pv": "#e87ba4", "batterij": "#008300",
              "inkt": "#0b0b0b", "inkt2": "#52514e", "gedempt": "#898781", "raster": "#e1e0d9", "as": "#c3c2b7",
              "nacht": "rgba(82,81,78,0.08)", "vlak": "rgba(11,11,11,0.07)"},
    "donker": {"net": "#3987e5", "gasketel": "#d95926", "warmtepomp": "#199e70", "buffer": "#9085e9",
               "pv": "#d55181", "batterij": "#008300",
               "inkt": "#ffffff", "inkt2": "#c3c2b7", "gedempt": "#898781", "raster": "#2c2c2a", "as": "#383835",
               "nacht": "rgba(255,255,255,0.06)", "vlak": "rgba(255,255,255,0.09)"},
}


def kleuren() -> dict:
    thema = getattr(getattr(st.context, "theme", None), "type", None)
    return _KLEUREN["donker" if thema == "dark" else "licht"]


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

def _mtime(pad: Path) -> float:
    return pad.stat().st_mtime if pad.exists() else 0.0


@st.cache_data(show_spinner=False)
def _datumbereik(pad: str, mtime: float) -> tuple[pd.Timestamp, pd.Timestamp, int]:
    datums = pd.read_csv(pad, usecols=["Date"], parse_dates=["Date"])["Date"]
    return datums.iloc[0], datums.iloc[-1], len(datums)


def datastatus(project: str) -> dict:
    """Which input is there for this project, and which period does it cover?

    Only the electricity measurement counts as missing: data_gas.csv is optional, so its
    absence is reported separately under 'gas_aanwezig' and not as a problem.
    """
    elektra = raw_map(project) / RAW_ELEKTRICITEIT
    gas = raw_map(project) / RAW_GAS
    gecombineerd = processed_map(project) / PROCESSED_GECOMBINEERD
    status = {"raw_ontbreekt": [] if elektra.is_file() else [elektra.name],
              "gas_aanwezig": gas.is_file(), "processed": gecombineerd.is_file(),
              "begin": None, "eind": None, "stappen": 0}
    if status["processed"]:
        # Only over the files that are there: _mtime of a file that does not exist raises.
        aanwezig = [p for p in (elektra, gas) if p.is_file()]
        status["actueel"] = (not status["raw_ontbreekt"]
                             and _mtime(gecombineerd) >= max(_mtime(p) for p in aanwezig))
        status["begin"], status["eind"], status["stappen"] = _datumbereik(str(gecombineerd), _mtime(gecombineerd))
    return status


@st.cache_data(show_spinner=False, max_entries=6)
def _lees_csv(pad: str, mtime: float) -> pd.DataFrame:
    return pd.read_csv(pad, index_col="Date", parse_dates=True)


def lees_dispatch(runmap: Path) -> pd.DataFrame | None:
    pad = Path(runmap) / "dispatch.csv"
    if not pad.is_file() or pad.stat().st_size < 10:
        return None
    try:
        return _lees_csv(str(pad), _mtime(pad))
    except (ValueError, pd.errors.EmptyDataError):
        return None


def meetdata(project: str) -> pd.DataFrame | None:
    """The measured consumption of the existing system per 5 minutes: grid offtake and gas import.

    The gas import comes from clean_data_gas.csv: the daily consumption from the raw data, spread
    over the day; per day that adds up to exactly the daily consumption. Deliberately not the column
    heat_kwh_5min from the combined file: that is the same series, but there it is called heat demand.
    """
    paden = [processed_map(project) / naam for naam in (PROCESSED_GECOMBINEERD, PROCESSED_GAS)]
    if not all(pad.is_file() for pad in paden):
        return None
    try:
        gecombineerd, gas = (_lees_csv(str(pad), _mtime(pad)) for pad in paden)
    except (ValueError, pd.errors.EmptyDataError):
        return None
    if "grid_import_kw" not in gecombineerd or "gas_kwh_5min" not in gas:
        return None
    return gecombineerd[["grid_import_kw"]].join(gas[["gas_kwh_5min"]], how="left")


def runinstellingen(runmap: Path) -> dict:
    return lees_json(Path(runmap) / "run_settings.json") if (Path(runmap) / "run_settings.json").is_file() else {}


def getal(waarde: float, decimalen: int = 0) -> str:
    """Dutch notation: 1.234,5"""
    tekst = f"{waarde:,.{decimalen}f}"
    return tekst.replace(",", "_").replace(".", ",").replace("_", ".")


_WEEKDAGEN = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def tijdstip(moment, jaar: bool = True, tijd: bool = True) -> str:
    """'Mon 22-12-2025 14:00': the weekday in English, just as in the graphs."""
    t = pd.Timestamp(moment)
    tekst = f"{_WEEKDAGEN[t.weekday()]} {t:%d-%m}" + (f"-{t:%Y}" if jaar else "")
    return tekst + (f" {t:%H:%M}" if tijd else "")


def blijvend(sleutel: str, standaard, toegestaan=None) -> str:
    """Key for a widget that keeps its value when you go to another page.

    Streamlit clears the value of a widget as soon as a page is shown on which that widget
    is not present. That is why there is a copy under its own key, which is put back here.
    Do not give the widget itself a value or index: the value comes from st.session_state.
    'toegestaan' checks a kept value (e.g. a date that still falls within the data);
    if it no longer fits, it becomes the default value.
    """
    kopie = f"_bewaard|{sleutel}"
    if sleutel not in st.session_state:
        st.session_state[sleutel] = st.session_state.get(kopie, standaard)
    if toegestaan is not None and not toegestaan(st.session_state[sleutel]):
        st.session_state[sleutel] = standaard
    st.session_state[kopie] = st.session_state[sleutel]
    return sleutel


def vergeet(voorvoegsel: str) -> None:
    """Clear all widget values with this prefix, including the copy of blijvend().

    Needed with 'Save' and 'Discard changes': if you only clear the widget key, then
    blijvend() simply puts the discarded value from the copy back at the next drawing.
    """
    kopievoorvoegsel = f"_bewaard|{voorvoegsel}"
    for sleutel in [k for k in st.session_state
                    if str(k).startswith(voorvoegsel) or str(k).startswith(kopievoorvoegsel)]:
        del st.session_state[sleutel]


def keuze() -> tuple[str | None, str | None]:
    """The project and scenario that have been chosen in the sidebar."""
    return st.session_state.get("project"), st.session_state.get("scenario")


# --------------------------------------------------------------------------
# Runs: the same choice list on the Results and Financial pages.
# --------------------------------------------------------------------------

RUNSTATUS = {"complete": "finished", "running": "running", "failed": "failed", "interrupted": "interrupted",
             "gestopt": "stopped", "gestart": "started", "onbekend": "unknown"}


def runlabel(run: dict) -> str:
    """The line in the choice list: name, saved or not, and the status."""
    teken = "✓ saved" if run["opgeslagen"] else "● not saved"
    return f"{run['naam']}  ·  {teken}  ·  {RUNSTATUS.get(run['status'], run['status'])}"


def runbeschrijving(run: dict) -> str:
    """One line below the choice list: scenario, start time and the number of deviations."""
    info = runbeheer.lees_json(run["pad"] / "run_info.json")
    if not info:
        return ""
    return (f"Scenario **{info.get('scenario', '?')}** · started {info.get('gestart', '?').replace('T', ' ')} "
            f"· {len(info.get('afwijkingen_van_basis', []))} deviation(s) from the basis")


def kiesrun(runs: list[dict], project: str, label: str = "Run", hulp: str | None = None) -> dict:
    """Choice list with the runs of a project. The same key as on the Results page,
    so that the chosen run comes along when you switch between those pages."""
    per_pad = {str(r["pad"]): r for r in runs}
    paden = list(per_pad)
    gekozen = st.selectbox(label, paden, format_func=lambda p: runlabel(per_pad[p]), help=hulp,
                           key=blijvend(f"run_a|{project}", paden[0], lambda p: p in per_pad))
    tekst = runbeschrijving(per_pad[gekozen])
    if tekst:
        st.caption(tekst)
    return per_pad[gekozen]


def scenarionaam(scenario: str) -> str:
    return "basis (existing system)" if scenario == BASIS else scenario
