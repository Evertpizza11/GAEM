"""
Catalogue: all the assets that are known by the model and the parameters that it has. The dashboard builds the energy system tab from this script. 

"""

CATALOGUS = {
    "pv": {
        "label": "Solar panels (PV)",
        "soort": "elektrisch",
        "uitleg": (("Once you put a "
                    "number of panels on a plane, the model COMPUTES the yield from the weather "
                    "data, so you can scale up and change "
                    "orientation. Four planes.")),
        "groepen": [{"label": f"Plane {i}",
                     "parameters": [f"vlak{i}_panelen", f"vlak{i}_helling", f"vlak{i}_azimut"]}
                    for i in range(1, 5)],
        "parameters": {
            **{naam: waarde for i in range(1, 5) for naam, waarde in {
                f"vlak{i}_panelen": {
                    "stap": 10,
                    "label": f"Plane {i}: number of panels", "kort": "Number of panels",
                    "eenheid": "-", "type": "int", "min": 0,
                    "optioneel": True, "leeg_betekent": "no panels on this plane",
                    "uitleg": ("Number of modules on this plane."),
                    "neutraal": 0,
                },
                f"vlak{i}_helling": {
                    "stap": 5,
                    "label": f"Plane {i}: tilt", "kort": "Tilt",
                    "eenheid": "°", "type": "float",
                    "min": 0, "max": 90, "optioneel": True, "leeg_betekent": "0 (flat)",
                    "uitleg": "0° is flat, 90° is vertical.",
                    "neutraal": 0.0,
                },
                f"vlak{i}_azimut": {
                    "stap": 15,
                    "label": f"Plane {i}: orientation", "kort": "Orientation",
                    "eenheid": "°", "type": "float",
                    "min": 0, "max": 360, "optioneel": True, "leeg_betekent": "180 (south)",
                    "uitleg": "Where the plane faces: 0° north, 90° east, 180° south, 270° west.",
                    "neutraal": 180.0,
                },
            }.items()},
            "oppervlak_paneel_m2": {
                "stap": 0.1,
                "label": "Area per panel", "eenheid": "m²", "type": "float",
                "min": 0, "min_exclusief": True, "optioneel": True, "leeg_betekent": "1.9",
                "uitleg": "From the datasheet.",
                "neutraal": 1.95,
            },
            "eta_pv": {
                "stap": 0.01,
                "label": "Module efficiency", "eenheid": "-", "type": "float",
                "min": 0, "min_exclusief": True, "max": 1, "optioneel": True,
                "leeg_betekent": "0.20",
                "uitleg": ("Efficiency of the module at STC, from the datasheet. Modern: 0.20 to "
                           "0.22."),
                "neutraal": 0.20,
            },
            "eta_sys": {
                "stap": 0.01,
                "label": "System efficiency", "eenheid": "-", "type": "float",
                "min": 0, "min_exclusief": True, "max": 1, "optioneel": True,
                "leeg_betekent": "0.83",
                "uitleg": ("Everything between panel and meter: soiling, mismatch, cabling, "
                           "inverter, ageing."),
                "neutraal": 0.83,
            },
        },
    },
    "grid": {
        "label": "Grid connection",
        "soort": "elektrisch",
        "uitleg": "Maximum offtake from the grid.",
        "parameters": {
            "max_import_kw": {
                "stap": 10,
                "label": "Maximum grid offtake", "eenheid": "kW", "type": "float", "min": 0,
                "uitleg": "Contracted maximum of the connection.",
                "neutraal": 0.0,
            },
        },
    },
    "gas_boiler": {
        "label": "Gas boiler",
        "soort": "thermisch",
        "uitleg": "Modulating boiler. Capacities are supplied heat.",
        "parameters": {
            "min_capacity_kwth": {
                "stap": 10,
                "label": "Minimum thermal capacity", "eenheid": "kWth", "type": "float", "min": 0,
                "uitleg": "Lowest load while the boiler is on. Off (0) is always allowed.",
                "neutraal": 0.0,
            },
            "max_capacity_kwth": {
                "stap": 10,
                "label": "Maximum thermal capacity", "eenheid": "kWth", "type": "float", "min": 0,
                "uitleg": "Heat output; do not multiply by the efficiency.",
                "neutraal": 0.0,
            },
            "efficiency": {
                "stap": 0.01,
                "label": "Efficiency", "eenheid": "-", "type": "float", "min": 0, "min_exclusief": True,
                "max": 1,
                "uitleg": "Useful heat. For example 0.9.",
                "neutraal": 1.0,
            },
            "min_on_time_minutes": {
                "stap": 5,
                "label": "Minimum run time", "eenheid": "min", "type": "float", "min": 0,
                "uitleg": "Minimum time the gas boiler must be turned on, for reducing number of starts.",
                "neutraal": 0,
            },
        },
    },
    "tess": {
        "label": "Heat buffer (TESS)",
        "soort": "thermisch",
        "uitleg": (("10 m cylindrical  tank.")),
        "parameters": {
            "volume_m3": {
                "stap": 10,
                "label": "Volume", "eenheid": "m³", "type": "float", "min": 0,
                "uitleg": "Water volume of the buffer.", "neutraal": 0.0,
            },
            "min_temp_c": {
                "stap": 1,
                "label": "Minimum temperature", "eenheid": "°C", "type": "float",
                "uitleg": "Lowest temperature of the storage.", "neutraal": 0.0,
            },
            "max_temp_c": {
                "stap": 1,
                "label": "Maximum temperature", "eenheid": "°C", "type": "float",
                "uitleg": "Maximum temperature of the storage.", "neutraal": 1.0,
            },
            "starting_volume_%": {
                "stap": 5,
                "label": "Fill level at start", "eenheid": "%", "type": "float", "min": 0, "max": 100,
                "uitleg": "Share of the usable capacity that is filled at the start.", "neutraal": 0,
            },
            "capacity_in_kwth": {
                "stap": 10,
                "label": "Maximum charging capacity", "eenheid": "kWth", "type": "float", "min": 0,
                "optioneel": True, "leeg_betekent": "unlimited",
                "uitleg": ("Empty = unlimited, 0 = charging blocked. Enter the limit of the "
                           "piping or the exchanger."),
                "neutraal": None,
            },
            "capacity_out_kwth": {
                "stap": 10,
                "label": "Maximum discharging capacity", "eenheid": "kWth", "type": "float", "min": 0,
                "optioneel": True, "leeg_betekent": "unlimited",
                "uitleg": "Empty = unlimited, 0 = discharging blocked.",
                "neutraal": None,
            },
        },
    },
    "heat_pump": {
        "label": "Heat pump",
        "soort": "thermisch",
        "uitleg": (("Fixed stages between minimum and maximum electrical capacity. The heat "
                    "follows per time step from the COP, which is computed from the weather "
                    "data.")),
        "parameters": {
            "brontype": {
                "label": "Source", "eenheid": "-", "type": "keuze", "neutraal": "lucht_water",
                "opties": {"lucht_water": "Air/water (source = outdoor air)",
                           "water_water": "Water/water (source = constant temperature)"},
                "uitleg": (("Air/water uses the outdoor temperature from the weather data as its "
                            "source and gets a defrost correction below 2 C. Water/water uses a "
                            "fixed source temperature that you enter below, for example an "
                            "aquifer thermal energy store.")),
            },
            "doeltemperatuur_c": {
                "stap": 5.0,
                "label": "Supply temperature", "eenheid": "C", "type": "float",
                "min": 0, "min_exclusief": True, "max": 150, "neutraal": 45.0,
                "uitleg": (("The temperature at which the heat pump delivers its heat, should be equal to max temperature of storage tank.")),
            },
            "bron_temperatuur_c": {
                "stap": 1.0,
                "label": "Source temperature", "eenheid": "C", "type": "float",
                "min": -20, "max": 100, "optioneel": True,
                "leeg_betekent": "the outdoor air from the weather data",
                "actief_als": {"brontype": "water_water"},
                "uitleg": (("Only for water/water: the temperature of the source, constant all "
                            "year. For air/water the source is the outdoor air and this field "
                            "cannot be filled in.")),
                "neutraal": 12.0,
            },
            "min_capacity_kwe": {
                "stap": 10,
                "label": "Minimum electrical capacity", "eenheid": "kWe", "type": "float", "min": 0,
                "uitleg": "Lowest electrical draw while the heat pump runs.", "neutraal": 1.0,
            },
            "max_capacity_kwe": {
                "stap": 10,
                "label": "Maximum electrical capacity", "eenheid": "kWe", "type": "float", "min": 0,
                "uitleg": "Highest electrical draw.", "neutraal": 1.0,
            },
            "min_on_time_minutes": {
                "stap": 5,
                "label": "Minimum run time", "eenheid": "min", "type": "float", "min": 0,
                "uitleg": "After every start or step up.", "neutraal": 0,
            },
            "control_stages": {
                "stap": 1,
                "label": "Number of stages", "eenheid": "-", "type": "int", "min": 1,
                "uitleg": "Number of power levels between minimum and maximum.", "neutraal": 1,
            },
        },
    },
    "bess": {
        "label": "Battery (BESS)",
        "soort": "elektrisch",
        "uitleg": "Capacities on the alternating-current side; losses through the efficiencies.",
        "parameters": {
            "capacity_kwh": {
                "stap": 10,
                "label": "Storage capacity", "eenheid": "kWh", "type": "float", "min": 0,
                "uitleg": "Usable energy content.", "neutraal": 0.0,
            },
            "capacity_in_kw": {
                "stap": 10,
                "label": "Maximum charging power", "eenheid": "kW", "type": "float", "min": 0,
                "uitleg": "Electricity power while charging.", "neutraal": 0.0,
            },
            "capacity_out_kw": {
                "stap": 10,
                "label": "Maximum discharging power", "eenheid": "kW", "type": "float", "min": 0,
                "uitleg": "AC power while discharging.", "neutraal": 0.0,
            },
            "starting_soc_%": {
                "stap": 5,
                "label": "State of charge at start", "eenheid": "%", "type": "float", "min": 0, "max": 100,
                "uitleg": "Share of the capacity that is charged at the start.", "neutraal": 0,
            },
            "charge_efficiency": {
                "stap": 0.01,
                "label": "Charging efficiency", "eenheid": "-", "type": "float", "min": 0, "min_exclusief": True,
                "max": 1, "uitleg": "Electricity input -> stored energy.", "neutraal": 1.0,
            },
            "discharge_efficiency": {
                "stap": 0.01,
                "label": "Discharging efficiency", "eenheid": "-", "type": "float", "min": 0,
                "min_exclusief": True, "max": 1,
                "uitleg": ("Stored energy -> electricity output. Round-trip efficiency = charge x "
                           "discharge."),
                "neutraal": 1.0,
            },
            "cont_energy_loss_%": {
                "stap": 0.1,
                "label": "Self-discharge", "eenheid": "% per uur", "type": "float", "min": 0, "max": 100,
                "uitleg": "Percentage of the stored energy that is lost per hour.", "neutraal": 0.0,
            },
        },
    },
}

for _naam, _spec in CATALOGUS.items():
    for _groep in _spec.get("groepen", []):
        _onbekend = [p for p in _groep["parameters"] if p not in _spec["parameters"]]
        if _onbekend:
            raise ValueError(f"Asset {_naam!r}, group {_groep['label']!r}: the parameter(s) "
                             f"{', '.join(_onbekend)} are not in 'parameters'.")
