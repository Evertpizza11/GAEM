"""
Catalogue: for the MILP settings

"""

SIMULATIE_CATALOGUS = {
    "horizon_hours": {
        "label": "Planning horizon", "eenheid": "h", "type": "float", "min": 0, "min_exclusief": True,
        "stap": 1.0,
        "uitleg": "How far ahead the model looks at every optimization. Multiple of 5 minutes.",
    },
    "control_interval_steps": {
        "label": "Re-optimize after", "eenheid": "5-min steps", "type": "int", "min": 1, "stap": 12,
        "uitleg": (("This many steps of the plan are executed before the next optimization. 12 = "
                    "every hour, 96 = every 8 hours, 288 = every day. At most the horizon.")),
    },
    "time_limit_seconds": {
        "label": "Time limit per optimization", "eenheid": "s", "type": "float", "min": 0,
        "min_exclusief": True, "stap": 5.0,
        "uitleg": (("If the solver does not reach the MIP gap within this time, it uses the best "
                    "solution found so far. The outcome then depends on the speed of the "
                    "computer.")),
    },
    "mip_gap": {
        "label": "MIP gap", "eenheid": "fraction", "type": "float", "min": 0, "max_exclusief": 1, "stap": 0.005,
        "uitleg": ("Stop as soon as the solution is within this fraction of the optimum. 0.01 = "
                   "1 %."),
    },
    "solver_threads": {
        "label": "Solver threads", "eenheid": "-", "type": "int", "min": 1, "stap": 1,
        "uitleg": "Number of processor cores for HiGHS. 1 gives reproducible outcomes.",
    },
    "terminal_policy": {
        "label": "Terminal value of storage", "eenheid": "", "type": "keuze",
        "opties": {"value": "Count it (value)", "none": "Do not count it (none)"},
        "uitleg": (("Without a terminal value the model empties the buffers at the end of every "
                    "horizon, because energy is worth nothing after that.")),
    },
    "terminal_bess_eur_per_kwh": {
        "label": "Terminal value of the battery", "eenheid": "EUR/kWh", "type": "float", "min": 0, "stap": 0.01,
        "optioneel": True, "leeg_betekent": "automatic, from the prices",
        "uitleg": ("Value of a kWh left in the battery at the end of the horizon. Empty = "
                   "automatic."),
    },
    "terminal_tess_eur_per_kwh": {
        "label": "Terminal value of the heat buffer", "eenheid": "EUR/kWh", "type": "float", "min": 0, "stap": 0.01,
        "optioneel": True, "leeg_betekent": "automatic, from the prices",
        "uitleg": ("Value of a kWh of heat left in the buffer at the end of the horizon. Empty = "
                   "automatic."),
    },
    "terminal_price_quantile": {
        "label": "Price quantile for the terminal value", "eenheid": "-", "type": "float", "min": 0, "max": 1,
        "stap": 0.05,
        "uitleg": ("With an automatic terminal value: this quantile of the non-negative prices "
                   "in the horizon."),
    },
    "progress_every_steps": {
        "label": "Show progress every", "eenheid": "steps", "type": "int", "min": 0, "stap": 1,
        "uitleg": "0 = no progress lines; the dashboard can then not show a progress bar.",
    },
}
