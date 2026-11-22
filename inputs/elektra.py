"""Capping a contract exceedance in the measured electricity demand.

What this module does. If e_demand_cap_kw is in project.json, the steps above that limit
are set back to the limit and the power taken away is distributed over the rest of the same
day, in proportion to the demand that was already there. 

"""

from __future__ import annotations

import numpy as np
import pandas as pd

KOLOM = "e_demand_kw"
VELD = "e_demand_cap_kw"

# This many rounds of water filling. Every round fills up proportionally and clips at the
# limit; in practice one round is enough as soon as there is room below the limit somewhere
# during the day.
MAX_RONDES = 50


class ElektraFout(ValueError):
    """Something is wrong with the capping of the electricity demand."""


def instellingen(project: str) -> dict:
    """The capping limit of a project from project.json.

    """
    from utils.projectinstellingen import lees_json
    from utils.projectpaden import PROJECT_BESTAND, project_map

    pad = project_map(project) / PROJECT_BESTAND
    gegevens = lees_json(pad) if pad.is_file() else {}
    ruw = gegevens.get(VELD)
    if ruw is None:
        return {"cap_kw": None}
    try:
        cap = float(ruw)
    except (TypeError, ValueError) as fout:
        raise ElektraFout(f"In {pad} {VELD} = {ruw!r}; that is not a number (kW).") from fout
    if cap <= 0:
        raise ElektraFout(f"In {pad} {VELD} = {cap:g} kW; that has to be greater than zero.")
    return {"cap_kw": cap}


def aftoppen(vraag: pd.Series, cap_kw: float) -> pd.Series:
    """Capping the demand at cap_kw and redistributing the surplus per calendar day.
    """
    waarden = vraag.to_numpy(dtype=float).copy()
    dagen = vraag.index.normalize()

    for dag in pd.unique(dagen[waarden > cap_kw]):
        masker = dagen == dag
        reeks = waarden[masker]

        boven = reeks > cap_kw
        overschot = float((reeks[boven] - cap_kw).sum())
        reeks[boven] = cap_kw

        for _ in range(MAX_RONDES):
            ruimte = cap_kw - reeks
            open_stap = ruimte > 1e-12
            gewicht = np.where(open_stap, reeks, 0.0)
            som = gewicht.sum()
            if overschot <= 1e-12 or not open_stap.any():
                break
            if som <= 0:                       # a day that is at zero everywhere: spread evenly
                gewicht = open_stap.astype(float)
                som = gewicht.sum()
            erbij = np.minimum(overschot * gewicht / som, ruimte)
            reeks += erbij
            overschot -= float(erbij.sum())

        waarden[masker] = reeks

    return pd.Series(waarden, index=vraag.index, name=vraag.name)


# --------------------------------------------------------------------------
# Connection with a run
# --------------------------------------------------------------------------

def toepassen(df: pd.DataFrame, project: str, stil: bool = False) -> pd.DataFrame:
    """Capping the column e_demand_kw, if project.json names a limit.
    """
    cap = instellingen(project)["cap_kw"]
    if cap is None or KOLOM not in df.columns:
        return df

    origineel = df[KOLOM]
    boven = int((origineel > cap).sum())
    if boven == 0:
        if not stil:
            print(f"Electricity demand: limit {cap:g} kW, not a single step above it; "
                  "nothing changed.")
        return df

    nieuw = aftoppen(origineel, cap)
    df = df.copy()
    df[KOLOM] = nieuw
    if not stil:
        verschoven = float((origineel - origineel.clip(upper=cap)).sum()) / 12
        verloren = (origineel.sum() - nieuw.sum()) / 12
        dagen = sorted({str(d.date()) for d in origineel.index[origineel > cap]})
        regel = (f"Electricity demand capped at {cap:g} kW (assumption B8): {boven} steps "
                 f"on {', '.join(dagen)}, peak {origineel.max():.1f} -> {nieuw.max():.1f} kW, "
                 f"{verschoven:.1f} kWh redistributed within the day out of "
                 f"{origineel.sum() / 12 / 1000:.0f} MWh in total")
        if abs(verloren) > 1e-6:
            regel += (f"\nNOTE: {verloren:.2f} kWh could not be redistributed; there was too "
                      "little room below the limit on that day. The daily total has therefore "
                      "not stayed exactly the same.")
        print(regel)
    return df
