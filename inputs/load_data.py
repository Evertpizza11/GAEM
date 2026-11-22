import csv

import pandas as pd


def load_data(filepath):
    """Reading in a raw CSV file.

    The separator is detected instead of assumed: the measurement from the company comes
    with semicolons, other series with commas.
    """
    with open(filepath, encoding="utf-8-sig", newline="") as f:
        kop = f.readline()
    try:
        scheiding = csv.Sniffer().sniff(kop, delimiters=",;\t").delimiter
    except csv.Error:
        scheiding = ";" if kop.count(";") >= kop.count(",") else ","

    df = pd.read_csv(filepath, sep=scheiding, encoding="utf-8-sig")
    print(f"\nRAW DATA LOADED: {filepath}  ({len(df)} rows, separator {scheiding!r})")
    print(f"Columns: {list(df.columns)}")
    print()
    return df
