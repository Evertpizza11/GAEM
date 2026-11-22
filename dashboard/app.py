"""GAEM dashboard. Start from the root folder GAEM:

    streamlit run dashboard/app.py

The pages are in two groups, and that split follows the order of work: first you put a
system together and have it computed, after that you look at what comes out. Every page is
its own file in dashboard/paginas/.

    SETUP
    General           choose or create a project, check data, start a simulation
    Energy system     fill in the basis and the scenarios (Enabled/Disabled per asset)
    Simulation        MILP settings of the project

    RESULTS
    Results           interactive graphs and the button 'Save in project'
    KPIs              the key figures per asset and for the system, A against B
    Financial         investment, payback time, maximum investment and LCOE

Why three separate pages under Results and not one page with tabs: st.tabs builds the
contents of all tabs at the same time. The graphs of the Results page would then also be
drawn while you are looking at the KPIs, and that is exactly where there is a switch per
graph to prevent that.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # So that 'utils', 'config' and 'dashboard' can be imported.

import streamlit as st  # noqa: E402

from dashboard import gedeeld  # noqa: E402

st.set_page_config(page_title="GAEM", page_icon="⚡", layout="wide")

MAP = Path(__file__).resolve().parent / "paginas"
bladen = st.navigation({
    "Setup": [
        st.Page(MAP / "algemeen.py", title="General", icon=":material/home:", default=True),
        st.Page(MAP / "energiesysteem.py", title="Energy system", icon=":material/bolt:"),
        st.Page(MAP / "simulatie.py", title="Simulation", icon=":material/tune:"),
    ],
    "Results": [
        st.Page(MAP / "resultaten.py", title="Results", icon=":material/monitoring:"),
        st.Page(MAP / "kpi.py", title="KPIs", icon=":material/scoreboard:"),
        st.Page(MAP / "financieel.py", title="Financial", icon=":material/euro:"),
    ],
})
gedeeld.zijbalk()
bladen.run()
