"""The fixed annual costs (OPEX) per asset. YOU MAY EDIT THIS FILE.

This is input data, not model code; see data/economic/asset_capex/capex.py for the same
explanation about the structure. Here there is one function per asset with the name of
the asset, which returns the costs in EUROS PER YEAR for a device of that size.

It concerns fixed costs: maintenance, insurance, service. Energy is NOT in it - gas and
electricity come out of the simulation itself.

Every function receives two things:
    a       the parameters of that asset (empty = 0), just as in capex.py
    capex   the CAPEX of that same asset according to capex.py, so that you can give the
            OPEX as a percentage of the investment

    def bess(a, capex):
        return 0.025 * capex

Important: 'capex' here is the full new price of the device as it stands, not the
difference with the basis. Maintenance hangs on the device and not on what you paid for
it. The Financial page therefore computes the OPEX of the new system AND of the basis,
and uses the difference for the payback time.
"""


def bess(a, capex):
    """Battery: 2.5 % of the investment per year."""
    return 0.025 * capex


def tess(a, capex):
    """Heat buffer: 0.3 % of the investment per year."""
    return 0.003 * capex


def pv(a, capex):
    """Solar panels: 13 EUR per kWp per year. Same peak power as in capex.py."""
    panelen = sum(a.get(f"vlak{i}_panelen", 0.0) for i in range(1, 5))
    kwp = panelen * (a.get("oppervlak_paneel_m2") or 1.9) * (a.get("eta_pv") or 0.20)
    return 13 * kwp


def heat_pump(a, capex):
    """Heat pump: 225 EUR per kW electrical per year. Same conversion as the investment;
    see the explanation at heat_pump() in asset_capex/capex.py.
    """
    return 225 * a["max_capacity_kwe"]


def grid(a, capex):
    """Grid connection: kept outside the analysis for now. The fixed costs of the connection
    (transport, capacity tariff) belong here later.
    """
    return 0.0


def gas_boiler(a, capex):
    """Gas boiler: kept outside the analysis for now."""
    return 0.0
