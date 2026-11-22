from digital_twin.assets import pv
from digital_twin.assets import bess
from digital_twin.assets import grid
from digital_twin.assets import heat_pump
from digital_twin.assets import gas_boiler
from digital_twin.assets import tess

HIERARCHY_HEAT = {
    "Option_1": [
        "tess",
        "heat_pump",
        "gas_boiler"
    ],

    "Option_2": [
        "heat_pump",
        "tess",
        "gas_boiler"
    ]
}

HIERARCHY_ELECTRICITY = {
    "Option_1": [
        "pv",
        "bess",
        "grid"
    ]
}

ACTIVE_HEAT_HIERARCHY = "Option_2"
ACTIVE_ELECTRICITY_HIERARCHY = "Option_1"