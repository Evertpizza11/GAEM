"""The investment costs (CAPEX) per asset, and the lifetime. YOU MAY EDIT THIS FILE.

This is input data, not model code: the cost formulas that you choose and substantiate
yourself are here. analysis/financieel.py only computes with them.

Per asset there is one function below with the same name as the asset in the model
(pv, grid, gas_boiler, tess, heat_pump, bess). That function receives the parameters of
that one asset, exactly as they are in projecten/<project>/basis.json and
scenarios/<name>.json, and returns the investment in EUROS for a NEW device of that size.

    def bess(a):
        return 150 * a["capacity_kwh"] + ...

About 'a':
    - The keys are the parameter names from config/asset_catalogus.py.
    - An empty parameter (null in the JSON) arrives here as 0. Watch out for that with
      parameters where empty means 'unlimited', such as capacity_in_kwth of the buffer.
    - 'enabled' is not in it: a disabled asset always costs 0 and the formula is then
      not called.

What the model does with these numbers:
    The investment is the DIFFERENCE between the new system and the basis:
    max(0, CAPEX(scenario) - CAPEX(basis)) per asset. The formula is therefore used
    twice. An asset that already exists and gets bigger only costs the difference; an
    asset that gets smaller or disappears costs 0 and yields nothing. If an asset is not
    in the basis, its basis CAPEX is 0 and not f(0) - a fixed term such as the 30,000 of
    the battery is therefore included.

An error in a formula? Then a readable message appears on the Financial page with the
name of the asset in it. The dashboard reads this file again on every refresh, so a
changed number is visible immediately; restarting is not needed.
"""

# Lifetime per asset in YEARS. Used for the second analysis on the Financial page:
# what may the investment be at most to break even within the lifetime? The page takes
# the SHORTEST lifetime of the assets you invest in, and you can
# override it there.
LEVENSDUUR_JAAR = {
    "pv": 10,
    "grid": 10,
    "gas_boiler": 10,
    "tess": 10,
    "heat_pump": 10,
    "bess": 10,
}


def bess(a):
    """Battery: 150 EUR/kWh + 140 EUR/kW + 30,000 EUR fixed."""
    capaciteit_kwh = a["capacity_kwh"]
    vermogen_kw = max(a["capacity_in_kw"], a["capacity_out_kw"])
    return 150 * capaciteit_kwh + 140 * vermogen_kw + 30_000


def tess(a):
    """Heat buffer: 3055 EUR x volume^0.691. Economies of scale, so per m3 a large buffer
    gets cheaper.
    """
    return 3055 * a["volume_m3"] ** 0.691


def pv(a):
    """Solar panels: 480 EUR per kWp of installed power, over all planes together.
    
    The peak power is not filled in separately anywhere; it follows from the panels:
        kWp = number of panels x area per panel x module efficiency x 1 kW/m2
    If no plane has a number of panels on it, the model replays the measured generation
    profile and there is nothing to invest in; then 0 comes out here.
    """
    panelen = sum(a.get(f"vlak{i}_panelen", 0.0) for i in range(1, 5))
    kwp = panelen * (a.get("oppervlak_paneel_m2") or 1.9) * (a.get("eta_pv") or 0.20)
    return 480 * kwp


def heat_pump(a):
    """Heat pump: 2,250 EUR per kW of ELECTRICAL power (the top stage).
    
    Per kW electrical and not per kW thermal, because the thermal power is no longer a
    fixed number now that the COP comes from the weather data (assumption D6): it differs
    per hour with the weather and with the chosen supply temperature. The amount is the
    old 900 EUR/kWth converted at the COP of 2.5 with which the scenarios up to October
    2026 were filled in, so that a machine of the same size costs the same as before.
    """
    return 2250 * a["max_capacity_kwe"]


def grid(a):
    """Grid connection: kept outside the analysis for now."""
    return 0.0


def gas_boiler(a):
    """Gas boiler: kept outside the analysis for now."""
    return 0.0
