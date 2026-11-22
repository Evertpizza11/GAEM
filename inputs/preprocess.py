import pandas as pd
import numpy as np


# The time notations that a raw file may use, tried in this order.
TIJDFORMATEN = (
    "%Y-%m-%d %H:%M:%S",  # 2025-10-26 02:55:00
    "%Y-%m-%d %H:%M",     # 2025-10-26 02:55
    "%d-%m-%Y %H:%M:%S",  # 26-10-2025 02:00:00
    "%d-%m-%Y %H:%M",     # 26-10-2025 02:00
)


def _parse_date(date_str):
    """Try multiple date formats in order until one succeeds."""
    for fmt in TIJDFORMATEN:
        try:
            return pd.to_datetime(date_str, format=fmt)
        except (ValueError, TypeError):
            continue
    return pd.NaT  # Return NaT if all formats fail


def lees_tijdkolom(reeks):
    """A whole column of time stamps, with the notation established once for the column.

    The notation is tried and not guessed. Left to itself pandas derives it from the first
    value and then applies it to every row, so a file that happens to start on a day below
    the thirteenth can be read month-first over its whole length: 01-05-2025 becomes the
    fifth of January, without any error. The first notation that reads EVERY value is the
    notation of this file. If none of them fits, the values are read one at a time, so that
    a file with mixed notations still works and only the unreadable stamps become NaT.
    """
    for fmt in TIJDFORMATEN:
        uit = pd.to_datetime(reeks, format=fmt, errors="coerce")
        if uit.notna().all():
            return uit
    return reeks.apply(_parse_date)


def preprocess_electricity(df, save_path=None):
    """Preparing the electricity measurement for the model, with UTC as the time basis.
    """
    df = df.dropna(how="all").copy()

    if "tijd_utc" in df.columns:
        df["Date"] = lees_tijdkolom(df["tijd_utc"])
        df = df.drop(columns=[c for c in ("tijd_utc", "tijd_lokaal", "dst")
                              if c in df.columns])
    else:
        # Old format: a Date column in local time, with " DST" as the marker.
        rauw = df["Date"].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
        zomertijd = rauw.str.endswith("DST")
        lokaal = lees_tijdkolom(rauw.str.replace(" DST", "", regex=False))
        if zomertijd.any():
            df["Date"] = lokaal - pd.to_timedelta(np.where(zomertijd, 2, 1), unit="h")
            print(f"Old format with a DST marker: {int(zomertijd.sum())} rows summer time, "
                  f"converted to UTC.")
        else:
            raise ValueError(
                "This measured series has no tijd_utc and no DST marker, so the summer "
                "time cannot be derived. It needs a column tijd_utc on UTC, or a Date "
                "column in local time with ' DST' on the summer time rows.")

    mislukt = int(df["Date"].isna().sum())
    if mislukt:
        print(f"Time stamps that could not be read: {mislukt}")
    df = df.dropna(subset=["Date"]).set_index("Date").sort_index()

    dubbel = int(df.index.duplicated().sum())
    if dubbel:
        raise ValueError(
            f"{dubbel} duplicate time stamps on UTC. That should not be possible; "
            "check the measured series.")

    df = df.dropna(axis=1, how="all")
    df = df.apply(pd.to_numeric, errors="coerce")

    # Feed-in is empty when nothing is fed back: that is a zero and not a missing
    # measurement.
    if "grid_export_kw" in df.columns:
        df["grid_export_kw"] = df["grid_export_kw"].fillna(0.0)

    leeg = df.isna().sum()
    if leeg.any():
        print("Missing measurements (stay empty):")
        for naam, aantal in leeg[leeg > 0].items():
            print(f"  {naam}: {aantal} of {len(df)} ({aantal/len(df):.2%})")

    print(f"Saving cleaned data with shape: {df.shape}")
    if save_path:
        df.to_csv(save_path)
        print(f"Electricity data saved to {save_path}")
    print(df.index.min(), df.index.max(), "(UTC)")
    print()
    return df


def vul_gaten(df, kolommen=("e_demand_kw", "appliance_demand_kw"), stil=False):
    """Filling in missing demand values with the same moment from neighbouring weeks.
    """
    df = df.copy()
    week = pd.Timedelta("7D")
    for kolom in kolommen:
        if kolom not in df.columns:
            continue
        begin = int(df[kolom].isna().sum())
        if not begin:
            continue
        for verschuiving in (week, -week, 2 * week, -2 * week, 3 * week, -3 * week):
            leeg = df[kolom].isna()
            if not leeg.any():
                break
            bron = df[kolom].reindex(df.index[leeg] - verschuiving)
            df.loc[leeg, kolom] = bron.to_numpy()
        rest = int(df[kolom].isna().sum())
        if not stil:
            print(f"  {kolom}: {begin - rest} of {begin} steps filled in "
                  f"from neighbouring weeks, {rest} still empty")
    return df


def preprocess_gas(df, save_path=None):
    """Spreading daily gas readings over steps of five minutes, on UTC.
    """
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["Date"]).sort_values("Date")
    df = df.rename(columns={"Gas": "gas_kwh"})

    # Midnight local time of every day, and of the day after, to UTC.
    lokaal = pd.DatetimeIndex(df["Date"]).tz_localize(
        "Europe/Amsterdam", ambiguous=True, nonexistent="shift_forward")
    begin_utc = lokaal.tz_convert("UTC").tz_localize(None)
    volgend = pd.DatetimeIndex(df["Date"] + pd.Timedelta("1D")).tz_localize(
        "Europe/Amsterdam", ambiguous=True, nonexistent="shift_forward")
    eind_utc = volgend.tz_convert("UTC").tz_localize(None)

    delen = []
    afwijkend = 0
    for start, eind, totaal in zip(begin_utc, eind_utc, df["gas_kwh"].to_numpy(float)):
        stappen = int(round((eind - start).total_seconds() / 300))
        if stappen != 288:
            afwijkend += 1
        x = np.arange(1, stappen + 1)
        gewichten = (1 / stappen) + ((0.083 - 0.008) / 24) * np.cos(
            (2 * np.pi / stappen) * (x - stappen / 8))
        gewichten = gewichten / gewichten.sum()
        delen.append(pd.DataFrame({
            "Date": pd.date_range(start, periods=stappen, freq="5min"),
            "gas_kwh_5min": totaal * gewichten,
        }))

    df_5min = pd.concat(delen).set_index("Date").sort_index()
    if afwijkend:
        print(f"Days with a deviating length because of summer time: {afwijkend}")

    verschil = abs(df_5min["gas_kwh_5min"].sum() - df["gas_kwh"].sum())
    print(f"Check: daily totals are preserved, deviation {verschil:.6f} kWh")

    if save_path:
        df_5min.to_csv(save_path)
        print(f"Gas data saved to {save_path}")
    print(df_5min.index.min(), df_5min.index.max(), "(UTC)")
    print()
    return df_5min
