"""Compare the CSV results of two run folders, number by number.

USAGE (from the root folder GAEM):
    python tools/vergelijk_runs.py <folder_A> <folder_B>
    python tools/vergelijk_runs.py <folder_A> <folder_B> --tolerantie 1e-6

For every CSV file that is in BOTH folders (also in subfolders such as
hierarchy_reference) the same columns and time stamps are compared.
Per file you see the largest difference and the column it is in. That difference is
relative (divided by the size of the value, at least 1): 1e-9 at 1000 kW is 1 microwatt.

    IDENTICAL     all values exactly equal
    EQUAL         differences smaller than the tolerance (rounding, another computer)
    DIFFERENT     larger than the tolerance: look at this file

Text columns (such as the solver status) are compared exactly. Computing times
(build_seconds, solve_seconds, controller_seconds) are skipped: those always differ,
even if the outcome is identical.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REKENTIJDEN = {"build_seconds", "solve_seconds", "controller_seconds"}


def lees(pad: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(pad)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    if "Date" in frame:
        frame = frame.set_index("Date")
    return frame


def vergelijk_bestand(a: Path, b: Path, tolerantie: float) -> tuple[str, str]:
    fa, fb = lees(a), lees(b)
    if fa.empty and fb.empty:
        return "IDENTICAL", "both empty"
    kolommen = [k for k in fa.columns if k in fb.columns and k not in REKENTIJDEN]
    alleen = sorted(set(fa.columns) ^ set(fb.columns))
    if len(fa) != len(fb) or not fa.index.equals(fb.index):
        return "DIFFERENT", f"other rows/time stamps ({len(fa)} against {len(fb)} rows)"
    grootste, waar, tekst_anders = 0.0, "", []
    for k in kolommen:
        x, y = fa[k], fb[k]
        if pd.api.types.is_numeric_dtype(x) and pd.api.types.is_numeric_dtype(y):
            xv, yv = x.to_numpy(dtype=float), y.to_numpy(dtype=float)
            beide_nan = np.isnan(xv) & np.isnan(yv)
            verschil = np.where(beide_nan, 0.0, np.abs(xv - yv))
            schaal = np.maximum(1.0, np.maximum(np.abs(xv), np.abs(yv)))
            relatief = np.nan_to_num(verschil / schaal, nan=np.inf)
            relatief = np.where(beide_nan, 0.0, relatief)
            if relatief.size and relatief.max() > grootste:
                grootste, waar = float(relatief.max()), k
        elif not x.astype(str).equals(y.astype(str)):
            tekst_anders.append(k)
    uitleg = f"largest difference {grootste:.3g} in {waar}" if waar else "no numerical differences"
    if alleen:
        uitleg += f"; columns in only one of the two: {', '.join(alleen)}"
    if tekst_anders:
        return "DIFFERENT", uitleg + f"; text differs in {', '.join(tekst_anders)}"
    if grootste == 0.0 and not alleen:
        return "IDENTICAL", uitleg
    return ("EQUAL" if grootste <= tolerantie else "DIFFERENT"), uitleg


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compare the CSV results of two run folders.")
    parser.add_argument("map_a", type=Path)
    parser.add_argument("map_b", type=Path)
    parser.add_argument("--tolerantie", type=float, default=1e-9,
                        help="Relative difference that still counts as equal (default 1e-9).")
    args = parser.parse_args(argv)
    for map_ in (args.map_a, args.map_b):
        if not map_.is_dir():
            parser.error(f"Folder not found: {map_}")
    a_bestanden = {p.relative_to(args.map_a) for p in args.map_a.rglob("*.csv")}
    b_bestanden = {p.relative_to(args.map_b) for p in args.map_b.rglob("*.csv")}
    gemeenschappelijk = sorted(a_bestanden & b_bestanden)
    if not gemeenschappelijk:
        parser.error("No common CSV files found.")
    slecht = 0
    for rel in gemeenschappelijk:
        oordeel, uitleg = vergelijk_bestand(args.map_a / rel, args.map_b / rel, args.tolerantie)
        slecht += oordeel == "DIFFERENT"
        print(f"{oordeel:<13} {rel.as_posix():<45} {uitleg}")
    for rel in sorted(a_bestanden ^ b_bestanden):
        print(f"{'ONLY IN ' + ('A' if rel in a_bestanden else 'B'):<13} {rel.as_posix()}")
    print(f"\n{len(gemeenschappelijk)} files compared, {slecht} different "
          f"(tolerance {args.tolerantie:g}).")
    return 1 if slecht else 0


if __name__ == "__main__":
    sys.exit(main())
