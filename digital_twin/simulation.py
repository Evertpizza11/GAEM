import numpy as np
from config.assets_config import ASSETS
from digital_twin.assets.tess import TESS
from digital_twin.assets.pv import PV
from digital_twin.assets.grid import GRID
from digital_twin.assets.bess import BESS
from digital_twin.assets.heat_pump import HP
from digital_twin.assets.gas_boiler import GB
from digital_twin.hierarchy_control.heat_controller import get_heat_hierarchy
from digital_twin.hierarchy_control.electricity_controller import get_electricity_hierarchy

def run_simulation(df, save_electricity=None, save_heat=None, *, controller="hierarchy",
                   milp_config=None, assets=None, max_steps=None, output_dir=None):

    if controller == "milp":
        from digital_twin.milp_control.rolling_horizon_simulation import run_milp_simulation
        return run_milp_simulation(df, save_electricity, save_heat,
                                  config=milp_config, assets=assets,
                                  max_steps=max_steps, output_dir=output_dir)
    if controller != "hierarchy":
        raise ValueError("controller must be hierarchy or milp.")
    asset_params = ASSETS if assets is None else assets
    if max_steps is not None and (int(max_steps) != max_steps or not 1 <= max_steps <= len(df)):
        raise ValueError("max_steps must be an integer in 1..len(input).")
    simulation_steps = len(df) if max_steps is None else int(max_steps)

    if df.empty or not df.index.is_unique or not df.index.is_monotonic_increasing:
        raise ValueError("Simulation needs a nonempty, chronological, unique DatetimeIndex.")
    if not df.index.to_series().diff().dropna().dt.total_seconds().eq(300).all():
        raise ValueError("Simulation currently requires consecutive five-minute timesteps.")
    input_values = df[["heat_demand_kw", "e_demand_kw", "pv_kw"]].to_numpy(dtype=float)
    if not np.isfinite(input_values).all() or (input_values < 0).any():
        raise ValueError("Heat/electricity demand and PV must be finite and nonnegative.")

    bess = BESS(asset_params["bess"])         # Statefull asset
    pv = PV(asset_params["pv"])               # Historical PV profile
    grid = GRID(asset_params["grid"])
    gb = GB(asset_params["gas_boiler"])
    tess = TESS(asset_params["tess"])
    hp = HP(asset_params["heat_pump"])


    dt = 5 / 60  # timestep in hours
    required_steps_hp = hp.required_steps(dt)
    # A boiler start late in an HP commitment needs its own full lookahead.
    forecast_steps = required_steps_hp + gb.required_steps(dt)
    heat_forecast = input_values[:, 0].tolist()
    e_forecast = input_values[:, 1].tolist()
    pv_forecast = input_values[:, 2].tolist() if pv.enabled else [0.0] * len(df)

    th_assets = {"heat_pump": hp, "tess": tess, "gas_boiler": gb}
    e_assets = {"pv": pv, "bess": bess, "grid": grid}

    heat_storage_columns = [
        "tess_energy_start_kwh", "tess_charge_kw", "tess_discharge_kw",
        "tess_heat_loss_kwh", "tess_temp_c", "heat_surplus_kw",
    ]
    electricity_storage_columns = [
        "bess_energy_start_kwh", "bess_charge_kw", "bess_discharge_kw",
        "bess_charge_loss_kwh", "bess_discharge_loss_kwh",
        "bess_standing_loss_kwh", "e_surplus_kw",
    ]
    boiler_columns = [
        "gb_heat_kw", "gb_heat_to_demand_kw", "gb_heat_surplus_kw",
        "gb_fuel_kw", "gb_fuel_kwh", "gb_conversion_loss_kwh", "gb_efficiency",
        "gb_is_on", "gb_started", "gb_running_steps", "gb_start_blocked",
        "hp_heat_to_demand_kw",
    ]
    
    # Loop over timesteps
    for i in range(simulation_steps):
        t = df.index[i]
        # ---------------------------
        # Initialize current timestep values
        # ---------------------------

        # The COP of the heat pump differs per step and comes from the weather data
        # (inputs/warmtepomp.py, assumption D6). It has to be known before the assets
        # take their turn, because the stages are electrical and the heat follows from it.
        if hp.enabled:
            if "cop" not in df.columns:
                raise KeyError(
                    "The input has no 'cop' column while this scenario has a heat pump. "
                    "That column is created by inputs/warmtepomp.py; a run "
                    "should run warmtepomp.toepassen() over its input.")
            hp.set_cop(df.at[t, "cop"])

        step_state = {
            "timestep": t,
            "remaining_heat_demand_kw": df.at[t, "heat_demand_kw"],                                         # remaining heat demand for this timestep in kw
            "future_heat_demand_kw": heat_forecast[i:i+forecast_steps],
            "remaining_e_demand_kw": df.at[t, "e_demand_kw"],                                               # remaining electricity demand for this timestep in kw
            "future_e_demand_kw": e_forecast[i:i+forecast_steps],
            "remaining_pv_available_kw": pv_forecast[i],
            "future_pv_available_kw": pv_forecast[i:i+forecast_steps],
            "remaining_grid_available_kw": grid.remaining_import_kw,                                        # remaining grid e available for this timestep in kw
            "remaining_gas_available_kw": gb.remaining_capacity_kw,                                         # remaining gas boiler output for this timestep in kwth
            "bess_soc": bess.soc,                                                                           # BESS state of charge for this timestep in %
            "bess_energy_kwh": bess.energy_kwh,                                                             # BESS energy for this timestep in kwh
            "tess_soc": tess.soc,                                                                           # TESS state of charge for this timestep in %
            "tess_energy_kwh": tess.energy_kwh,                                                             # TESS energy for this timestep in kwh
            "tess_capacity_kwh": tess.capacity_kwh,
            # Outdoor temperature of this step, for the standing loss of the buffer (D5).
            "t_amb_c": float(df.at[t, "t_buiten_c"]) if "t_buiten_c" in df.columns
                       else tess.min_temp,                                                         # TESS capacity for this timestep in kwh        
            "hp_heat_kw": 0.0,                 # Pending HP surplus, NOT last step's output
            "hp_el_kw": 0.0,
            "hp_reserved_heat_kw": hp.mandatory_heat_kw(dt),
            "gb_reserved_heat_kw": gb.mandatory_heat_kw(dt),
            "heat_surplus_kw": 0,
            "e_surplus_kw": 0,
            "hp_stage": hp.current_stage,
            "hp_steps_since_upshift": hp.steps_since_upshift,
            "hp_running_steps": hp.running_steps,                                                           # Heat pump running steps for this timestep
            "hp_required_steps": required_steps_hp,                                                         # Heat pump required steps for this timestep
            "dt": dt,                                                                                       # Timestep in hours
            "i": i
        }

        # Store the initial step values in the df
        initial_grid_available_kw = grid.remaining_import_kw
        initial_gasboiler_available_kw = gb.remaining_capacity_kw


        # ---------------------------
        # What is the hierarchy that is suggested by the controller?
        # ---------------------------  
        th_hierarchy_operate = get_heat_hierarchy()                                 # Get the heat hierarchy for this timestep heat supply
        e_hierarchy_operate = get_electricity_hierarchy()                           # Get the electricity hierarchy for this timestep for e supply

        if tuple(th_hierarchy_operate) not in (("tess", "heat_pump", "gas_boiler"), ("heat_pump", "tess", "gas_boiler")):
            raise ValueError("Heat control requires HP/TESS or TESS/HP, with gas_boiler last; use enabled for absent assets.")
        if len(e_hierarchy_operate) != len(set(e_hierarchy_operate)) or any(name not in e_assets for name in e_hierarchy_operate):
            raise ValueError("Electricity hierarchy contains an unknown or duplicate asset.")

        step_state["electricity_hierarchy"] = tuple(e_hierarchy_operate)
        step_state["heat_hierarchy"] = tuple(th_hierarchy_operate)

        # Fixed finalization order
        th_hierarchy_finalization = [hp, gb, tess]                                  # Get the heat hierarchy for this timestep finalization
        e_hierarchy_finalization = [pv, grid, bess]                                 # Get the electricity hierarchy for this timestep finalization      

        # Standing losses act on opening storage, once, before any dispatch.
        tess.begin_step(step_state)
        bess.begin_step(step_state)

        # ---------------------------
        # HEAT ASSET CONTROL
        # ---------------------------
        for asset_name in th_hierarchy_operate:

            step_state = (th_assets[asset_name].operate(step_state, hp, bess, tess, grid, gb, pv))                          # Walks through all assets in the hierarchy and lets them operate on the step_state

        hp_heat_kw = hp.current_heat_stage_kw
        hp_el_kw = hp.current_electricity_stage_kw

        # ---------------------------
        # ELECTRICITY ASSET CONTROL
        # ---------------------------
        for asset_name in e_hierarchy_operate:
            step_state = (e_assets[asset_name].operate(step_state, hp, bess, tess, grid, gb, pv))                          # Walks through all assets in the hierarchy and lets them operate on the step_state

        # ---------------------------
        # FINALIZE STEP 
        # ---------------------------

        for asset in th_hierarchy_finalization:
            step_state = (asset.step_finalization(step_state))                # Walks through all assets in the hierarchy and lets them finalize on the step_state

        for asset in e_hierarchy_finalization:
            step_state = (asset.step_finalization(step_state))                # Walks through all assets in the hierarchy and lets them finalize on the step_state

        if step_state["heat_surplus_kw"] > 1e-5:
            raise RuntimeError(f"Unabsorbed heat at {t}; heat dumping is not an enabled disposal route.")

        # ---------------------------
        # Store results
        # ---------------------------
        # Heat results
        df.at[t, "initial_heat_demand_kw"] = df.at[t, "heat_demand_kw"]
        df.at[t, "remaining_heat_demand_kw"] = step_state["remaining_heat_demand_kw"]
        df.at[t, "initial_gasboiler_available_kw"] = initial_gasboiler_available_kw
        df.at[t, "remaining_gasboiler_available_kw"] = step_state["remaining_gas_available_kw"]
        df.at[t, "tess_soc"] = step_state["tess_soc"]
        df.at[t, "tess_energy_kwh"] = step_state["tess_energy_kwh"]
        df.at[t, "tess_temp_c"] = step_state["tess_temp_c"]
        df.at[t, "initial_hp_heat_kw"] = hp_heat_kw
        df.at[t, "remaining_hp_heat_kw"] = step_state["hp_heat_kw"]
        df.at[t, "hp_running_steps"] = step_state["hp_running_steps"]
        df.at[t, "hp_steps_since_upshift"] = step_state["hp_steps_since_upshift"]
        df.at[t, "hp_stage"] = step_state["hp_stage"]

        # Electricity results
        df.at[t, "initial_e_demand_kw"] = df.at[t, "e_demand_kw"]
        df.at[t, "remaining_e_demand_kw"] = step_state["remaining_e_demand_kw"]
        df.at[t, "initial_pv_available_kw"] = pv_forecast[i]
        df.at[t, "remaining_pv_available_kw"] = step_state["remaining_pv_available_kw"]
        df.at[t, "initial_grid_available_kw"] = initial_grid_available_kw
        df.at[t, "remaining_grid_available_kw"] = step_state["remaining_grid_available_kw"]
        df.at[t, "bess_soc"] = step_state["bess_soc"]
        df.at[t, "bess_energy_kwh"] = step_state["bess_energy_kwh"]
        df.at[t, "initial_hp_el_kw"] = hp_el_kw
        df.at[t, "remaining_hp_el_kw"] = step_state["hp_el_kw"]

        df.at[t, "dt_hours"] = dt
        for column in heat_storage_columns + electricity_storage_columns + boiler_columns:
            df.at[t, column] = step_state[column]

    df = df.iloc[:simulation_steps].copy()
    # ---------------------------
    # Create output datasets
    # ---------------------------
    heat_profile = df[[
        "initial_heat_demand_kw",
        "remaining_heat_demand_kw",
        "initial_gasboiler_available_kw", 
        "remaining_gasboiler_available_kw",
        "tess_soc",   
        "tess_energy_kwh",
        "initial_hp_heat_kw",
        "remaining_hp_heat_kw", 
        "hp_running_steps",
        "hp_steps_since_upshift",
        "hp_stage"
    ] + heat_storage_columns + boiler_columns + ["dt_hours"]].copy()

    electricity_profile = df[[
        "initial_e_demand_kw",
        "remaining_e_demand_kw",
        "initial_pv_available_kw",
        "remaining_pv_available_kw", 
        "initial_grid_available_kw",
        "remaining_grid_available_kw",
        "bess_soc", 
        "bess_energy_kwh",
        "initial_hp_el_kw",
        "remaining_hp_el_kw"
    ] + electricity_storage_columns + ["dt_hours"]].copy()


    # ---------------------------
    # Save results
    # ---------------------------
    if save_electricity:
        electricity_profile.to_csv(save_electricity)
        print(f"Electricity profile saved to {save_electricity}")

    if save_heat:
        heat_profile.to_csv(save_heat)
        print(f"Heat profile saved to {save_heat}")

    print()
    print("SIMULATION COMPLETE")
    unmet_heat = (heat_profile["remaining_heat_demand_kw"] * dt).sum()
    unmet_electricity = (electricity_profile["remaining_e_demand_kw"] * dt).sum()
    print(f"Unserved heat: {unmet_heat:.6f} kWhth; unserved electricity: {unmet_electricity:.6f} kWh")
    print()

    return df, electricity_profile, heat_profile
