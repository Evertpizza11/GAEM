import pandas as pd
import numpy as np
import warnings
from config.assets_config import ASSETS
from utils.projectinstellingen import actieve_gasprijs_eur_per_kwh


def _add_gas_energy_columns(df):
    """Use logged fuel, not thermal output, as the billable energy.

    New profiles carry their own fuel and efficiency, independent of later
    changes to assets_config. Old profiles can only use the current efficiency.
    """
    dt = df.get("dt_hours", 5 / 60)
    if not np.isfinite(dt).all() or np.any(np.asarray(dt) <= 0):
        raise ValueError("Gas costs need positive, finite dt_hours.")
    heat_kw = df["gb_heat_kw"] if "gb_heat_kw" in df else (
        df["initial_gasboiler_available_kw"] - df["remaining_gasboiler_available_kw"]
    )
    df["gasboiler_heat_kw"] = heat_kw.clip(lower=0)
    df["gasboiler_heat_kwh"] = df["gasboiler_heat_kw"] * dt
    if "gb_efficiency" in df and not df["gb_efficiency"].between(0, 1, inclusive="right").all():
        raise ValueError("Logged gas efficiency must be in (0, 1].")
    if "gb_fuel_kwh" in df:
        fuel = df["gb_fuel_kwh"]
    else:
        if "gb_efficiency" in df:
            efficiency = df["gb_efficiency"]
        else:
            warnings.warn(
                "Legacy heat profile: deriving gas fuel using CURRENT assets_config efficiency. "
                "Rerun the simulation for self-contained fuel records.", RuntimeWarning, stacklevel=2
            )
            efficiency = float(ASSETS["gas_boiler"]["efficiency"])
        if not np.isfinite(efficiency).all() or np.any(np.asarray(efficiency) <= 0) or np.any(np.asarray(efficiency) > 1):
            raise ValueError("Gas efficiency must be in (0, 1].")
        fuel = df["gasboiler_heat_kwh"] / efficiency
    if not np.isfinite(fuel).all() or (fuel < 0).any():
        raise ValueError("Gas fuel records must be finite and nonnegative.")
    if "gb_efficiency" in df and not np.allclose(fuel * df["gb_efficiency"], df["gasboiler_heat_kwh"], atol=1e-6, rtol=1e-8):
        raise ValueError("Logged gas fuel, thermal production and efficiency are inconsistent.")
    df["gas_fuel_kwh"] = fuel
    df["gas_fuel_kw"] = fuel / dt
    gasprijs = actieve_gasprijs_eur_per_kwh()
    df["gas_price_eur_per_kwh"] = gasprijs
    df["gas_cost_eur"] = fuel * gasprijs
    return df

def calculate_electricity_costs(
    electricity_profile_path,
    price_path,
    output_path=None
):
    """
    Calculates yearly electricity costs from simulation results.
    """

    print("Calculating electricity costs...")

    # --------------------------------------------------
    # Load electricity flow profile
    # --------------------------------------------------

    df_flow = pd.read_csv(
        electricity_profile_path,
        parse_dates=["Date"]
    )

    # --------------------------------------------------
    # Load price data
    # --------------------------------------------------

    df_price = pd.read_csv(
        price_path,
        sep=";"
    )

    df_price["Date"] = pd.to_datetime(
        df_price["Date"],
        format="%d-%m-%Y %H:%M"
    )

    # --------------------------------------------------
    # Merge price data onto flow profile
    # --------------------------------------------------

    df = pd.merge_asof(
        df_flow.sort_values("Date"),
        df_price.sort_values("Date"),
        on="Date",
        direction="backward"
    )

    # --------------------------------------------------
    # Calculate grid import
    # --------------------------------------------------

# --------------------------------------------------
# Calculate grid import
# --------------------------------------------------

    df["grid_import_kw"] = (
        df["initial_grid_available_kw"]
        - df["remaining_grid_available_kw"]
    )

    # Prevent negative imports
    df["grid_import_kw"] = df["grid_import_kw"].clip(lower=0)

    # --------------------------------------------------
    # Convert power to energy
    # --------------------------------------------------

    df["grid_import_kwh"] = (
        df["grid_import_kw"]
        * df.get("dt_hours", 5 / 60)
    )

    # --------------------------------------------------
    # Price conversion
    # --------------------------------------------------

    df["price_eur_per_kwh"] = (
        df["Day-ahead (EUR/MWh)"]
        / 1000
    )

    # --------------------------------------------------
    # Electricity cost
    # --------------------------------------------------

    df["electricity_cost_eur"] = (
        df["grid_import_kwh"]
        * df["price_eur_per_kwh"]
    )

    total_cost = df["electricity_cost_eur"].sum()

    total_import = df["grid_import_kwh"].sum()

    print()
    print("===================================")
    print(f"Grid import : {total_import:,.0f} kWh")
    print(f"Total cost  : {total_cost:,.2f} Euro")
    print("===================================")



    if output_path:

        output_df = df[
            [
                "Date",
                "Day-ahead (EUR/MWh)",
                "grid_import_kw",
                "grid_import_kwh",
                "price_eur_per_kwh",
                "electricity_cost_eur"
            ]
        ]

        output_df = output_df.round({
            "Day-ahead (EUR/MWh)": 2,
            "grid_import_kw": 3,
            "grid_import_kwh": 3,
            "price_eur_per_kwh": 4,
            "electricity_cost_eur": 2
        })


        output_df.to_csv(
            output_path,
            index=False
        )

    return total_cost

def calculate_gas_costs(
        heat_profile_path,
        output_path=None
    ):
    """
    Calculates yearly gas costs from simulation results.
    """

    print("Calculating gas costs...")

    # --------------------------------------------------
    # Load heat flow profile
    # --------------------------------------------------

    df = pd.read_csv(
        heat_profile_path,
        parse_dates=["Date"]
    )

    # --------------------------------------------------
    # Calculate gas boiler production
    # --------------------------------------------------

    df = _add_gas_energy_columns(df)

    # --------------------------------------------------
    # Summaries
    # --------------------------------------------------

    total_gas_heat = (
        df["gasboiler_heat_kwh"]
        .sum()
    )

    total_cost = (
        df["gas_cost_eur"]
        .sum()
    )

    print()
    print("===================================")
    print(f"Boiler heat  : {total_gas_heat:,.0f} kWh_th")
    print(f"Gas consumed : {df['gas_fuel_kwh'].sum():,.0f} kWh_fuel")
    print(f"Total cost   : {total_cost:,.2f} Euro")
    print("===================================")

    # --------------------------------------------------
    # Save results
    # --------------------------------------------------

    if output_path:

        output_df = df[
            [
                "Date",
                "gasboiler_heat_kw",
                "gasboiler_heat_kwh",
                "gas_fuel_kw",
                "gas_fuel_kwh",
                "gas_price_eur_per_kwh",
                "gas_cost_eur"
            ]
        ]

        # Preserve per-step precision; round only printed/displayed totals.

        output_df.to_csv(
            output_path,
            index=False
        )

    return total_cost

def calculate_total_energy_costs(
    electricity_profile_path,
    price_path,
    heat_profile_path,
    output_path=None
    ):
    """
    Calculates total energy costs for every timestep.
    """

    print("Calculating total energy costs...")

    # --------------------------------------------------
    # Electricity costs
    # --------------------------------------------------

    df_el = pd.read_csv(
        electricity_profile_path,
        parse_dates=["Date"]
    )

    df_price = pd.read_csv(
        price_path,
        sep=";"
    )

    df_price["Date"] = pd.to_datetime(
        df_price["Date"],
        format="%d-%m-%Y %H:%M"
    )

    df_el = pd.merge_asof(
        df_el.sort_values("Date"),
        df_price.sort_values("Date"),
        on="Date",
        direction="backward"
    )

    df_el["grid_import_kw"] = (
        df_el["initial_grid_available_kw"]
        - df_el["remaining_grid_available_kw"]
    ).clip(lower=0)

    df_el["grid_import_kwh"] = (
        df_el["grid_import_kw"]
        * df_el.get("dt_hours", 5 / 60)
    )

    df_el["electricity_cost_eur"] = (
        df_el["grid_import_kwh"]
        * (df_el["Day-ahead (EUR/MWh)"] / 1000)
    )

    # --------------------------------------------------
    # Gas costs
    # --------------------------------------------------

    df_gas = pd.read_csv(
        heat_profile_path,
        parse_dates=["Date"]
    )

    df_gas = _add_gas_energy_columns(df_gas)

    # --------------------------------------------------
    # Merge results
    # --------------------------------------------------

    result_df = pd.merge(
        df_el[["Date", "electricity_cost_eur"]],
        df_gas[["Date", "gas_cost_eur"]],
        on="Date"
    )

    result_df["total_cost_eur"] = (
        result_df["electricity_cost_eur"]
        + result_df["gas_cost_eur"]
    )

    # Do not sum individually rounded cents: retain full timestep precision.

    # --------------------------------------------------
    # Summary
    # --------------------------------------------------

    total_electricity_cost = result_df[
        "electricity_cost_eur"
    ].sum()

    total_gas_cost = result_df[
        "gas_cost_eur"
    ].sum()

    total_energy_cost = result_df[
        "total_cost_eur"
    ].sum()

    print()
    print("===================================")
    print("ENERGY COST SUMMARY")
    print("===================================")
    print(f"Electricity cost : {total_electricity_cost:,.2f} Euro")
    print(f"Gas cost         : {total_gas_cost:,.2f} Euro")
    print(f"Total cost       : {total_energy_cost:,.2f} Euro")
    print("===================================")

    if output_path:
        result_df.to_csv(
            output_path,
            index=False
        )

    return total_energy_cost
