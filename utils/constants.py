"""
Physical constants used in the energy model
"""

# ---------------------------
# Water properties
# ---------------------------

# Density of water (kg/m³)
RHO_WATER = 1000

# Specific heat capacity of water (kJ/kg°C)
CP_WATER_KJ_PER_KG_K = 4.186

# Specific heat capacity in Wh/kg°C (useful for energy calcs)
CP_WATER_WH_PER_KG_K = 1.163


# ---------------------------
# Time constants
# ---------------------------

# 5-minute timestep in hours
TIMESTEP_5MIN_HOURS = 5 / 60


# ---------------------------
# Conversions
# ---------------------------

KJ_TO_KWH = 1 / 3600
WH_TO_KWH = 1 / 1000


# ---------------------------
# Natural gas
# ---------------------------

GAS_BOVENWAARDE_MJ_PER_M3 = 35.17   # caloric upper value, Dutch natural gas
GAS_ONDERWAARDE_MJ_PER_M3 = 31.65   # Caloric lower value

GAS_KWH_PER_M3 = GAS_BOVENWAARDE_MJ_PER_M3 / 3.6          # 9.769 kWh per m3
GAS_ONDER_OVER_FACTOR = GAS_ONDERWAARDE_MJ_PER_M3 / GAS_BOVENWAARDE_MJ_PER_M3   # 0.900
