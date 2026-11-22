# GAEM

**Greenhouse Asset Exploration Model** — a model that plans and evaluates the energy
system of a greenhouse. What does it cost today, and what would it cost with a heat pump,
a battery or a larger heat buffer? GAEM takes a year of measured data of one greenhouse,
represents its installation asset by asset, and runs it in steps of five minutes with
either a rule-based controller or a cost-optimising one.

Written for the master's thesis of Evert Janssen (Sustainable Energy Technology,
TU Eindhoven, 2026).

**New here? Open `USER_GUIDE.pdf` first.** That is the manual: how to get going, what the
dashboard shows and how to read the results. This README is the part before that — getting
the model installed and running on your computer.

## What it does

```text
input/raw            measured electricity of the greenhouse, optionally the gas
      ↓              clean up, put on UTC, combine into steps of 5 minutes
input/processed
      ↓              heat demand and PV yield from the weather data of the location
a complete series    demand, generation, prices and the COP of the heat pump
      ↓              the controller decides per step what runs
runs/<run>/          dispatch, costs, flows per asset, solver log
      ↓              the dashboard reads these
graphs, KPIs, payback time
```

You describe the existing installation once in `basis.json`. Every alternative you want to
test is a scenario: the same assets with other sizes, or an asset switched on that is not
there yet. The model runs a scenario and then shows what it costs against the basis.

## Installation

You need **Python 3.14** and **Git**. The exact version is in `.python-version`; the
results that come with this repository were produced on 3.14.4.

Open a terminal in the folder where you want the model (in VS Code: **Terminal → New
Terminal**) and run these commands one by one:

```powershell
git clone https://github.com/Evertpizza11/GAEM.git
cd GAEM
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements_dashboard.txt
```

On Linux or macOS the fourth line is `source .venv/bin/activate`.

If PowerShell refuses to activate the environment, allow it once for this session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

**You do not need to install a solver.** The MILP is solved by HiGHS, which comes along
with the `highspy` package.

**Reproducing the results instead of just running the model?** Then install
`requirements_lock.txt` rather than `requirements_dashboard.txt`. The two requirements
files name only what the code imports itself, so everything underneath them is whatever is
newest on the day you install. The lock file pins all 43 packages, including the date
handling and the time zone rules, on the versions the reference run was computed with.

## Quick start

With the environment active, from the root folder of the model:

```powershell
streamlit run dashboard/app.py
```

The dashboard opens at `http://localhost:8501`. Choose the project `Voorbeeld_kas`, go to
**General** and start a simulation of a few hours. `USER_GUIDE.pdf` walks through this
step by step.

Everything the dashboard does can also be done from the terminal:

```powershell
python run_milp.py --project Voorbeeld_kas --scenario S1 --TMY --hours 24
```

| Flag | What it does |
|---|---|
| `--project` | Folder in `projecten/`. Required |
| `--scenario` | File in `scenarios/`, or `basis` for the existing system. Required |
| `--hours` | How many hours are executed. Default 1 |
| `--all` | Execute the whole dataset. For a year this takes hours |
| `--start` | `"YYYY-MM-DD HH:MM"`, must match a time stamp in the data exactly |
| `--TMY` `--ECY` `--EWY` | Weather year: typical, extreme cold or extreme warm |
| `--time-limit` | Seconds the solver may spend per optimisation |
| `--compare` | Also run the rule-based controller over the same period |
| `--horizon-hours` `--control-interval-steps` | Override the MILP settings of the project |

A run always lands in a new folder in `runs/`. From the dashboard you can keep one
deliberately, which copies it to `projecten/<project>/output/`.

```powershell
python tools/nieuw_project.py <name> --kopieer-basis-van Voorbeeld_kas   # a new project
python tools/vergelijk_runs.py <folder A> <folder B>                     # two runs value by value
python -m inputs.verwerk_project --project <name>                        # only rebuild processed data
```

## What comes with it

**The project `Voorbeeld_kas`** — a greenhouse of 10 000 m² with a gas boiler, a heat
buffer and a grid connection of 250 kW, plus one scenario `S1` that adds an air/water heat
pump. The measured electricity series covers a full year in steps of five minutes:
**14 April 2025 22:00 to 14 April 2026 21:55 UTC**. That series is measured data of a
commercial greenhouse with noise on every value, published with their permission.

**Electricity prices** in `data/markt/e_prices/clean/` — day-ahead prices per five minutes
over exactly that same year.

**Weather data** in `data/weather/` — a typical year (TMY) from PVGIS plus an extreme cold
and an extreme warm year, for the location of the case study.

Two things to watch when you move outside this:

- **The prices cover one year.** Run outside 14 April 2025 – 14 April 2026 and there are no
  prices for those moments. Either stay inside that window, or put your own price series in
  `data/markt/e_prices/clean/e_prices_clean_utc.csv`.
- **The weather file belongs to one location.** The coordinates are read from the header of
  the TMY file itself, so the sun position always matches the irradiance. Have a greenhouse
  somewhere else? Download a TMY for your own location from PVGIS, put it in
  `data/weather/` and name it in `weerbestand` in `project.json`.

## How a project is organised

```text
projecten/<project>/
    project.json              name, greenhouse parameters, gas price, weather file
    basis.json                the EXISTING energy system — your reference
    scenarios/<name>.json     an alternative energy system to test
    simulatie.json            the MILP settings of this project
    LEESMIJ.txt               what belongs in this folder
    input/raw/                the measured data, exactly as you received it
    input/processed/          made by the model out of raw/, may be deleted
    output/                   runs you deliberately kept
```

`input/raw/` needs one file: **`meetreeks_elektra.csv`**, the measured electricity demand
in steps of five minutes with a column `tijd_utc`. **`data_gas.csv`** with daily gas
readings is optional — the heat demand is computed from the weather data either way, and
without it only the comparison with the measured situation falls away. `USER_GUIDE.pdf`
describes both formats.

**Your own data stays on your computer.** `.gitignore` keeps `projecten/*/input/`,
`projecten/*/output/` and `runs/` out of Git, so putting the measured data of a company in
a project folder does not put it in the repository. The one exception is the example
project, whose series is in there on purpose.

Two settings are not where you would look for them. The **gas price per kWh** is a constant
in `utils/constants.py`, not a scenario parameter, and the **priority lists** of the
rule-based controller are in `config/settings.py`. Both are therefore the same for every
project.

## What this is not

A model for one thesis, not a commercial design tool. It is a beta: the numbers that come
out depend on assumptions that are documented in the thesis, and nothing here is advice to
invest. Check the outcomes before you lean on them.

## Licence

MIT — see `LICENSE`. Use it, change it, build on it, for any purpose. The only condition
is that the copyright notice travels along with copies, and there is no warranty.

Three things in this repository come from elsewhere and keep their own licence:

- The asset icons in `dashboard/afbeeldingen/` are based on icons from Flaticon
  (www.flaticon.com) and were edited for this model.
- The electricity prices in `data/markt/e_prices/` come from the ENTSO-E Transparency
  Platform and are reused with attribution to ENTSO-E.
- The weather files in `data/weather/` come from PVGIS (European Commission, Joint
  Research Centre).

An exploratory modular tool for the preliminary design of greenhouse energy systems, Sustainable Energy Technology, TU Eindhoven, 2026.
