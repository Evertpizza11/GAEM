"""
MILP settings are chosen and introduced here. 

"""

from utils.projectinstellingen import laad_actieve_simulatie

STANDAARD_MILP_SETTINGS = {
    "horizon_hours": 48.0,
    "control_interval_steps": 288,  # Execute 96 distinct five-minute actions if you want 8 hours per solve, so 288 for a day
    "time_limit_seconds": 30.0,  # Time available per solve from MILP
    "mip_gap": 0.01,            # 1% relative MIP gap means 0.01
    "solver_threads": 1,
    "terminal_policy": "value",  # "value" or "none"

    "terminal_bess_eur_per_kwh": None,
    "terminal_tess_eur_per_kwh": None,
    "terminal_price_quantile": 0.25, # If you want 25% of cheapest costs. 
    "progress_every_steps": 1,
}
MILP_SETTINGS = laad_actieve_simulatie(STANDAARD_MILP_SETTINGS)
