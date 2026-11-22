"""Combining the electricity and gas measurement into one table per 5 minutes.
"""

import pandas as pd


def combine_data(df_el, df_gas=None, save_path=None):
    """Combining electricity and gas on their common moments in time.

    df_gas may be None: the gas measurement is optional. Then only the electricity series is
    written, without heat_kwh_5min and without heat_demand_kw. A run fills in the heat demand
    from the weather data anyway (see inputs/warmte.toepassen), so the simulation is the same;
    only the comparison with the measured situation cannot be drawn.
    """

    # ---------------------------
    # 1. Merge datasets
    # ---------------------------
    df = df_el.copy() if df_gas is None else df_el.join(df_gas, how="inner")

    # ---------------------------
    # 2. Rename columns (standardization)
    # ---------------------------
    df = df.rename(columns={
        "appliance_demand_kw": "e_demand_kw",
        "gas_kwh_5min": "heat_kwh_5min"
    })

    # ---------------------------
    # 3. Convert gas energy -> power
    # ---------------------------
    if "heat_kwh_5min" in df.columns:
        df["heat_demand_kw"] = df["heat_kwh_5min"] * 12  # kWh per 5 min -> kW

    # ---------------------------
    # 4. Save
    # ---------------------------
    if save_path:
        df.to_csv(save_path)
        print(f"Combined data saved to {save_path}")

    print("Combined dataset created:")
    print(df.head())
    print()
    print(f"COMBINED DATA CREATED. CLEAN DATA SAVED TO {save_path}")
    print()
    return df
