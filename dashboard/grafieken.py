"""Plotly graphs for the Results and Financial pages.

Every function gets a list of Reeksen (one run, or two runs to compare), already
limited to the chosen period, and returns (figure, table). The table is the same data
as the graph: the dashboard shows it below the graph. At the bottom there is one graph
that is not about a period but about years: kasstroom(), for the Financial page.

Agreements:
- Powers apply over a step [t, t+5 min): drawn as a staircase (shape 'hv').
- State of charge and cumulative costs are END values of a step: drawn at t + 5 min.
  The cumulative costs add up from the start of the chosen period. The costs of the
  original situation can be added to that: dotted, computed from the measurement data of
  the company (see originele_kosten).
- Never two y axes in one graph: grid import and price are in two panels below each other.
- Every asset has the same colour everywhere (see dashboard/gedeeld.py). A comparison run
  (B) has the same colours as run A, but dashed lines; in the stacked graphs those
  are the edges of the stack of B.
- Every run is at its real moments: runs from different periods lie next to each other
  on the time axis, not over each other.
- If a run has more than 14 days of data in the chosen period, all graphs show hourly
  averages, otherwise the browser becomes slow. That is then in the caption. The CSV
  files stay per 5 minutes.
- Grey bands: the night (22:00-06:00). With a period of more than 62 days they are not
  drawn; then there are so many that they only give a grey haze.
- Dates on the axis and in the hover with English weekday, e.g. 'Mon 22-12-2025 14:00'.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

MAX_PUNTEN = 14 * 288  # 14 days of 5 minutes
STAP = pd.Timedelta(minutes=5)
LETTER = 'system-ui, -apple-system, "Segoe UI", sans-serif'
NACHT_VAN, NACHT_TOT = 22, 6  # hours
NACHT_MAX_DAGEN = 62
HOVERTIJD = "%a %d-%m-%Y %H:%M"

# Notation on the time axis, depending on the distance between two ticks (dtick, in ms).
# Plotly takes the first line that fits; %a and %b are English (Mon, Dec).
_DAG, _WEEK = 86_400_000, 604_800_000
TIJDAS = [
    dict(dtickrange=[None, _DAG - 1], value="%H:%M<br>%a %d-%m"),  # hours: 14:00 / Mon 22-12
    dict(dtickrange=[_DAG, _WEEK - 1], value="%a<br>%d-%m"),       # days: Mon / 22-12
    dict(dtickrange=[_WEEK, 20 * _DAG], value="%d-%m<br>%Y"),      # weeks
    dict(dtickrange=[None, None], value="%b<br>%Y"),               # months
]


@dataclass
class Reeks:
    """One run in the graphs."""
    df: pd.DataFrame                                   # dispatch.csv, limited to the chosen period
    instellingen: dict = field(default_factory=dict)   # run_settings.json of the run
    letter: str = ""                                   # "" without comparison, otherwise "A" or "B"

    @property
    def streep(self) -> str:
        return "dash" if self.letter == "B" else "solid"

    def naam(self, tekst: str) -> str:
        return f"{tekst} · {self.letter}" if self.letter else tekst


def per_uur_nodig(reeksen: list[Reeks]) -> bool:
    return any(len(r.df) > MAX_PUNTEN for r in reeksen)


def _verklein(tabel: pd.DataFrame, per_uur: bool, eindwaarde: bool = False) -> pd.DataFrame:
    if not per_uur:
        return tabel
    return tabel.resample("1h").last() if eindwaarde else tabel.resample("1h").mean()


def _tabel(delen: list[tuple[Reeks, pd.DataFrame]]) -> pd.DataFrame:
    """The data below the graph; with a comparison 'A · ' or 'B · ' is in front of every column."""
    return pd.concat([t.add_prefix(f"{r.letter} · ") if r.letter else t for r, t in delen], axis=1)


def _rgba(hexkleur: str, alfa: float) -> str:
    h = hexkleur.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alfa})"


def _nachten(van: pd.Timestamp, tot: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """All pieces of night (22:00-06:00) within [van, tot]."""
    banden = []
    for dag in pd.date_range(van.normalize() - pd.Timedelta(days=1), tot.normalize(), freq="D"):
        begin = max(dag + pd.Timedelta(hours=NACHT_VAN), van)
        eind = min(dag + pd.Timedelta(days=1, hours=NACHT_TOT), tot)
        if begin < eind:
            banden.append((begin, eind))
    return banden


def _opmaak(fig: go.Figure, k: dict, hoogte: int, venster: tuple, assen: tuple = (("x", "y"),)) -> go.Figure:
    van, tot = venster
    nacht = []
    if tot - van <= pd.Timedelta(days=NACHT_MAX_DAGEN):
        for i, (begin, eind) in enumerate(_nachten(van, tot)):
            for j, (x, y) in enumerate(assen):
                nacht.append(dict(type="rect", xref=x, yref=f"{y} domain", x0=begin, x1=eind, y0=0, y1=1,
                                  fillcolor=k["nacht"], line=dict(width=0), layer="below",
                                  name="Night (22:00-06:00)", legendgroup="nacht", showlegend=i == j == 0))
    fig.update_layout(
        # Generous margins: inside Streamlit they do not grow along with the axis labels.
        height=hoogte, margin=dict(l=72, r=16, t=48, b=56), separators=",.",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=LETTER, size=13, color=k["inkt2"]),
        hovermode="x unified", hoverlabel=dict(font=dict(family=LETTER, size=12)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    font=dict(color=k["inkt2"]), title=None, traceorder="normal"),
        shapes=nacht,
    )
    fig.update_xaxes(showgrid=False, linecolor=k["as"], ticks="outside", tickcolor=k["as"],
                     tickfont=dict(color=k["gedempt"]), showspikes=True, spikemode="across",
                     spikesnap="cursor", spikethickness=1, spikecolor=k["gedempt"], spikedash="solid",
                     range=[van, tot], tickformatstops=TIJDAS, hoverformat=HOVERTIJD)
    fig.update_yaxes(gridcolor=k["raster"], gridwidth=1, zeroline=True, zerolinecolor=k["as"],
                     zerolinewidth=1, tickfont=dict(color=k["gedempt"]), title_font=dict(color=k["inkt2"]),
                     rangemode="tozero")
    return fig


def _lijn(r: Reeks, x, y, naam: str, kleur: str, eenheid: str, breedte: float = 2, trap: bool = True,
          decimalen: int = 0, hover: str | None = None) -> go.Scatter:
    return go.Scatter(x=x, y=y, name=r.naam(naam), mode="lines",
                      line=dict(color=kleur, width=breedte, shape="hv" if trap else "linear", dash=r.streep),
                      hovertemplate=hover or f"%{{y:,.{decimalen}f}} {eenheid}")


def _limiet(r: Reeks) -> float | None:
    grid = r.instellingen.get("assets", {}).get("grid", {})
    return grid.get("max_import_kw") if grid.get("enabled") else None


def _deel_van(b: pd.Series, a: pd.Series) -> bool:
    """Is b a piece of a? E.g. the prices of B if B ran in the same period as A."""
    return bool(b.index.isin(a.index).all()) and np.allclose(
        b.to_numpy(float), a.reindex(b.index).to_numpy(float), equal_nan=True)


def netafname_en_prijs(reeksen: list[Reeks], k: dict, venster: tuple,
                       per_uur: bool) -> tuple[go.Figure, pd.DataFrame]:
    delen = [(r, _verklein(pd.DataFrame({"Grid import (kW)": r.df["grid_import_kw"],
                                         "Electricity price (EUR/MWh)": r.df["price_eur_per_kwh"] * 1000}),
                           per_uur)) for r in reeksen]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.62, 0.38], vertical_spacing=0.07)
    for r, tabel in delen:
        fig.add_trace(_lijn(r, tabel.index, tabel["Grid import (kW)"], "Grid import", k["net"], "kW"), row=1, col=1)
    # The maximum grid import belongs to the scenario: one line over the whole period (per run if they differ).
    limieten = [(r, _limiet(r)) for r, _ in delen]
    gelijk = len({limiet for _, limiet in limieten}) == 1
    for r, limiet in limieten:
        if limiet:
            fig.add_trace(go.Scatter(x=[venster[0], venster[1]], y=[limiet, limiet], mode="lines", hoverinfo="skip",
                                     name="Maximum grid import" if gelijk else r.naam("Maximum grid import"),
                                     line=dict(color=k["gedempt"], width=1,
                                               dash="dashdot" if r.letter == "B" else "dot")),
                          row=1, col=1)
        if gelijk:
            break
    # Did the runs (partly) run in the same period, then the price is the same there. If one price series fits
    # entirely in the other, then only the longest is in it, as one line for both runs.
    prijzen = [(r, tabel["Electricity price (EUR/MWh)"]) for r, tabel in delen]
    langste = max(prijzen, key=lambda rp: len(rp[1]))[1]
    if all(_deel_van(prijs, langste) for _, prijs in prijzen):
        prijzen = [(Reeks(pd.DataFrame()), langste)]  # without letter: solid, name without A or B
    for r, prijs in prijzen:
        fig.add_trace(go.Scatter(x=prijs.index, y=prijs, mode="lines", name=r.naam("Electricity price"),
                                 line=dict(color=k["inkt2"], width=1.5, shape="hv", dash=r.streep),
                                 hovertemplate="%{y:,.1f} EUR/MWh"), row=2, col=1)
    fig.update_yaxes(title_text="kW", row=1, col=1)
    fig.update_yaxes(title_text="EUR/MWh", row=2, col=1)
    return _opmaak(fig, k, 520, venster, assen=(("x", "y"), ("x2", "y2"))), _tabel(delen)


def _gestapeld(reeksen: list[Reeks], maak_tabel, kleuren: list[str], vraagnaam: str, k: dict, eenheid: str,
               venster: tuple, per_uur: bool) -> tuple[go.Figure, pd.DataFrame]:
    """Stacked supply plus the demand as a line. maak_tabel gives the supply columns first, the demand last."""
    fig = go.Figure()
    delen = []
    for r in reeksen:
        tabel = _verklein(maak_tabel(r.df), per_uur)
        *aanbod, vraag = tabel.columns
        for kolom, kleur in zip(aanbod, kleuren):
            naam = kolom.split(" (")[0]
            if r.letter == "B":  # Only the edges of the stack: areas of B would cover the stack of A.
                stijl = dict(fill="none", line=dict(color=kleur, width=2, shape="hv", dash="dash"))
            else:
                stijl = dict(line=dict(color=kleur, width=1, shape="hv"), fillcolor=_rgba(kleur, 0.45))
            fig.add_trace(go.Scatter(x=tabel.index, y=tabel[kolom], name=r.naam(naam), mode="lines",
                                     stackgroup=f"aanbod{r.letter}", hovertemplate=f"%{{y:,.0f}} {eenheid}", **stijl))
        fig.add_trace(_lijn(r, tabel.index, tabel[vraag], vraagnaam, k["inkt"], eenheid))
        delen.append((r, tabel))
    fig.update_yaxes(title_text=eenheid)
    return _opmaak(fig, k, 420, venster), _tabel(delen)


def warmte(reeksen: list[Reeks], k: dict, venster: tuple, per_uur: bool) -> tuple[go.Figure, pd.DataFrame]:
    def maak_tabel(df: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({
            "Heat pump (kWth)": df["initial_hp_heat_kw"],
            "Gas boiler (kWth)": df["gb_heat_kw"],
            "Thermal storage discharge (kWth)": df["tess_discharge_kw"],
            "Greenhouse heat demand (kWth)": df["initial_heat_demand_kw"],
        })
    return _gestapeld(reeksen, maak_tabel, [k["warmtepomp"], k["gasketel"], k["buffer"]], "Greenhouse heat demand",
                      k, "kWth", venster, per_uur)


def elektriciteit(reeksen: list[Reeks], k: dict, venster: tuple, per_uur: bool) -> tuple[go.Figure, pd.DataFrame]:
    def maak_tabel(df: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({
            "Grid (kW)": df["grid_import_kw"],
            "PV used (kW)": df["pv_used_kw"],
            "Battery discharge (kW)": df["bess_discharge_kw"],
            "Greenhouse + heat pump demand (kW)": df["initial_e_demand_kw"] + df["initial_hp_el_kw"],
        })
    return _gestapeld(reeksen, maak_tabel, [k["net"], k["pv"], k["batterij"]], "Greenhouse + heat pump demand",
                      k, "kW", venster, per_uur)


def pv_aan(r: Reeks) -> bool:
    """Does this run have PV? Without that key in run_settings.json: yes, as with the storage."""
    return bool(r.instellingen.get("assets", {}).get("pv", {}).get("enabled", True))


def pv_totalen(r: Reeks) -> tuple[float, float, float]:
    """Generation, used and curtailed in kWh over the whole chosen period.

    From the 5-minute rows, also when the graph shows hourly averages - just like the key figures.
    """
    opwek = float(r.df["initial_pv_available_kw"].sum()) / 12
    benut = float(r.df["pv_used_kw"].sum()) / 12
    return opwek, benut, float(r.df["pv_curtailed_kw"].sum()) / 12


def pv(reeksen: list[Reeks], k: dict, venster: tuple, per_uur: bool) -> tuple[go.Figure | None, pd.DataFrame]:
    """What the PV field delivered, and what of that was used and what was curtailed.

    The stack is the generation, split into the part the system has used and the part
    that was thrown away; the dark line above it is the generation itself and so coincides
    with the top of the stack. Curtailing costs nothing in this model (assumption D3) and
    there is no feed-in (D2), so everything that cannot be used right away on a sunny day or
    fit in the battery disappears here. That makes this graph the quickest readable argument
    for more storage or more demand midday.

    Whether the generation is the measurement or comes from weather data is in the run itself:
    if the scenario uses panels, this is the computed yield (B3, H1).
    """
    fig = go.Figure()
    delen = []
    for r in reeksen:
        if not pv_aan(r):
            continue
        tabel = _verklein(pd.DataFrame({
            "PV used (kW)": r.df["pv_used_kw"],
            "PV curtailed (kW)": r.df["pv_curtailed_kw"],
            "PV generation (kW)": r.df["initial_pv_available_kw"],
        }), per_uur)
        for kolom, kleur in (("PV used (kW)", k["pv"]), ("PV curtailed (kW)", k["gedempt"])):
            naam = kolom.split(" (")[0]
            if r.letter == "B":   # Only the edges; areas of B would cover the stack of A.
                stijl = dict(fill="none", line=dict(color=kleur, width=2, shape="hv", dash="dash"))
            else:
                stijl = dict(line=dict(color=kleur, width=1, shape="hv"), fillcolor=_rgba(kleur, 0.45))
            fig.add_trace(go.Scatter(x=tabel.index, y=tabel[kolom], name=r.naam(naam), mode="lines",
                                     stackgroup=f"pv{r.letter}", hovertemplate="%{y:,.0f} kW", **stijl))
        fig.add_trace(_lijn(r, tabel.index, tabel["PV generation (kW)"], "PV generation", k["inkt"], "kW"))
        delen.append((r, tabel))
    if not delen:
        return None, pd.DataFrame()
    fig.update_yaxes(title_text="kW")
    return _opmaak(fig, k, 420, venster), _tabel(delen)


def opslag(reeksen: list[Reeks], k: dict, venster: tuple, per_uur: bool) -> tuple[go.Figure | None, pd.DataFrame]:
    fig = go.Figure()
    delen = []
    for r in reeksen:
        assets = r.instellingen.get("assets", {})
        kolommen = {}
        if assets.get("tess", {}).get("enabled", True):
            kolommen["Thermal storage (%)"] = ("tess_soc", k["buffer"])
        if assets.get("bess", {}).get("enabled", True):
            kolommen["Battery (%)"] = ("bess_soc", k["batterij"])
        if not kolommen:
            continue
        tabel = pd.DataFrame({naam: r.df[kolom] * 100 for naam, (kolom, _) in kolommen.items()})
        tabel.index = tabel.index + STAP  # State of charge at the END of every step.
        tabel = _verklein(tabel, per_uur, eindwaarde=True)
        for naam, (_, kleur) in kolommen.items():
            fig.add_trace(_lijn(r, tabel.index, tabel[naam], naam.split(" (")[0], kleur, "%", trap=False,
                                decimalen=1))
        delen.append((r, tabel))
    if not delen:
        return None, pd.DataFrame()
    fig.update_yaxes(title_text="State of charge (%)", range=[-2, 102])
    return _opmaak(fig, k, 340, venster), _tabel(delen)


KOSTENLIJNEN = (("Electricity (EUR)", "net", 2), ("Gas (EUR)", "gasketel", 2), ("Total (EUR)", "inkt", 2.5))


def _cumulatief(per_stap: dict[str, pd.Series], per_uur: bool) -> pd.DataFrame:
    tabel = pd.DataFrame({naam: reeks.cumsum() for naam, reeks in per_stap.items()})
    tabel.index = tabel.index + STAP  # Cumulative up to and including the END of every step.
    return _verklein(tabel, per_uur, eindwaarde=True)


def originele_kosten(r: Reeks, meting: pd.DataFrame) -> dict[str, pd.Series] | None:
    """What the existing system cost in this period, per step.

    From the measurement data of the company (see gedeeld.meetdata): the measured grid offtake at
    the same electricity price as the run, and the measured gas import at the gas price of the run.
    No efficiency: the gas import is already the gas the company purchased. The model uses that same
    gas series as heat demand (assumption B4) and burns gas/efficiency for it, so a simulated
    basis run comes out on gas higher than these measured costs.
    """
    if not {"grid_import_kw", "gas_kwh_5min"} <= set(meting.columns):
        return None
    deel = meting.reindex(r.df.index)
    if deel["grid_import_kw"].isna().all():
        return None  # No measurement data at the moments of this run.
    elektriciteit = deel["grid_import_kw"].fillna(0) * r.df["dt"] * r.df["price_eur_per_kwh"]
    gas = deel["gas_kwh_5min"].fillna(0) * r.df["gas_price_eur_per_kwh"]
    return {"Electricity (EUR)": elektriciteit, "Gas (EUR)": gas, "Total (EUR)": elektriciteit + gas}


def _origineel(reeksen: list[Reeks], meting: pd.DataFrame,
               per_uur: bool) -> list[tuple[Reeks, pd.DataFrame]]:
    """The original situation: one series if the runs have the same moments, otherwise per run."""
    if len(reeksen) > 1 and all(r.df.index.equals(reeksen[0].df.index) for r in reeksen[1:]):
        reeksen = [Reeks(reeksen[0].df, reeksen[0].instellingen)]  # without letter: applies to both runs
    delen = []
    for r in reeksen:
        per_stap = originele_kosten(r, meting)
        if per_stap:
            delen.append((r, _cumulatief(per_stap, per_uur).add_prefix("Original: ")))
    return delen


def kosten(reeksen: list[Reeks], k: dict, venster: tuple, per_uur: bool,
           meting: pd.DataFrame | None = None) -> tuple[go.Figure, pd.DataFrame]:
    fig = go.Figure()
    delen = []
    for r in reeksen:
        tabel = _cumulatief({naam: r.df[kolom] for naam, kolom in
                             (("Electricity (EUR)", "electricity_cost_eur"), ("Gas (EUR)", "gas_cost_eur"),
                              ("Total (EUR)", "total_cost_eur"))}, per_uur)
        for naam, kleur, breedte in KOSTENLIJNEN:
            fig.add_trace(_lijn(r, tabel.index, tabel[naam], naam.split(" (")[0], k[kleur], "EUR", breedte=breedte,
                                trap=False, hover="EUR %{y:,.0f}"))
        delen.append((r, tabel))
    if meting is not None:
        # The original situation dotted, in the same colours: colour belongs to the cost item.
        for r, tabel in _origineel(reeksen, meting, per_uur):
            for naam, kleur, breedte in KOSTENLIJNEN:
                kolom = f"Original: {naam}"
                fig.add_trace(go.Scatter(x=tabel.index, y=tabel[kolom], mode="lines",
                                         name=r.naam(f"{naam.split(' (')[0]} original"),
                                         line=dict(color=k[kleur], width=breedte, dash="dot"),
                                         hovertemplate="EUR %{y:,.0f}"))
            delen.append((r, tabel))
    fig.update_yaxes(title_text="EUR, cumulative")
    return _opmaak(fig, k, 360, venster), _tabel(delen)


def kasstroom(investering: float, netto_per_jaar: float, levensduur: float, k: dict,
              terugverdientijd: float | None = None, label_kruising: str = "",
              label_levensduur: str = "") -> go.Figure:
    """What you are left with on balance, year after year: first the investment off, then the saving on.

    Belongs to the Financial page. One line, because there is one number: the cash position. That
    starts at zero, drops at the moment of investing to -investment and then runs straight up
    with the net saving per year. Where it crosses the zero line is the payback time; that
    point is marked separately because it is the question the page answers.

    A straight line and not a step per year: the saving runs through the year, and that way
    the line crosses the zero line exactly at investment / net per year. There is no
    discounting (assumption G6), so later years count as heavily as the first.
    """
    # A margin behind the furthest point, so that the dotted line of the lifetime does not coincide
    # with the edge of the graph and you see that the line simply continues after that.
    horizon = max(levensduur, (terugverdientijd or 0) * 1.1, 1.0) * 1.08
    jaren = np.arange(0.0, horizon + 0.025, 0.05)
    saldo = -investering + netto_per_jaar * jaren
    fig = go.Figure()
    fig.add_trace(go.Scatter(  # the drop at the moment of investing
        x=[0, 0], y=[0, -investering], mode="lines", showlegend=False, hoverinfo="skip",
        line=dict(color=k["inkt"], width=2)))
    fig.add_trace(go.Scatter(
        x=jaren, y=saldo, mode="lines", showlegend=False, fill="tozeroy", fillcolor=k["vlak"],
        line=dict(color=k["inkt"], width=2),
        hovertemplate="after %{x:,.1f} years<br>EUR %{y:,.0f}<extra></extra>"))
    if terugverdientijd is not None and terugverdientijd <= horizon:
        fig.add_trace(go.Scatter(
            x=[terugverdientijd], y=[0], mode="markers", showlegend=False,
            marker=dict(size=11, color=k["warmtepomp"]),
            hovertemplate=f"paid back after %{{x:,.1f}} years<extra></extra>"))
        # If the crossing point is on the right in the graph, then the label to the left of it: otherwise it falls away.
        rechts = terugverdientijd > 0.6 * horizon
        fig.add_annotation(x=terugverdientijd, y=0, text=label_kruising, showarrow=False, yshift=22,
                           xanchor="right" if rechts else "left", xshift=-8 if rechts else 8,
                           font=dict(family=LETTER, size=13, color=k["inkt"]))
    if levensduur <= horizon:
        fig.add_shape(type="line", x0=levensduur, x1=levensduur, y0=0, y1=1, yref="y domain",
                      line=dict(color=k["gedempt"], width=1, dash="dot"), layer="below")
        # At the bottom: the line is never there, because it runs from bottom left to top right.
        fig.add_annotation(x=levensduur, y=0, yref="y domain", text=label_levensduur, showarrow=False,
                           yshift=10, xanchor="right", xshift=-8,
                           font=dict(family=LETTER, size=12, color=k["gedempt"]))
    fig.update_layout(
        # Wide left margin: amounts of a million are long labels next to the axis title.
        height=340, margin=dict(l=116, r=24, t=28, b=56), separators=",.",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=LETTER, size=13, color=k["inkt2"]), showlegend=False,
        hovermode="x", hoverlabel=dict(font=dict(family=LETTER, size=12)))
    fig.update_xaxes(title_text="Years after the investment", range=[-0.035 * horizon, horizon],
                     showgrid=False, linecolor=k["as"], ticks="outside", tickcolor=k["as"],
                     dtick=1 if horizon <= 16 else 5, tickfont=dict(color=k["gedempt"]),
                     title_font=dict(color=k["inkt2"]), showspikes=True, spikemode="across",
                     spikesnap="cursor", spikethickness=1, spikecolor=k["gedempt"], spikedash="solid")
    fig.update_yaxes(title_text="Cumulative cash flow (EUR)", gridcolor=k["raster"], gridwidth=1,
                     zeroline=True, zerolinecolor=k["inkt2"], zerolinewidth=2,
                     tickformat=",.0f", tickfont=dict(color=k["gedempt"]),
                     title_font=dict(color=k["inkt2"]))
    return fig
