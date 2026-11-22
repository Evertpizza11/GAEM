"""Make a new project folder with the right structure.

USAGE (from the root folder GAEM):
    python tools/nieuw_project.py Voorbeeld_kas
    python tools/nieuw_project.py Voorbeeld_kas --locatie Venlo --kopieer-basis-van Voorbeeld_kas

Result:
    projecten/Voorbeeld_kas/
        project.json        name, location, description
        basis.json          existing energy system: empty (everything off), or copied
        simulatie.json      default simulation settings from config/ems_config.py
        LEESMIJ.txt         what belongs where
        scenarios/          the possible energy systems go here
        input/raw/          put the data of the company here
        input/processed/    the model makes this itself
        output/             saved results

An existing project is not overwritten; missing parts are added.
A button for this will come in the dashboard later.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.projectpaden import ROOT, project_map, raw_map  # noqa: E402
from utils.projectinstellingen import maak_project  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Make a new project folder.")
    parser.add_argument("naam", help="Project name without spaces, for example Voorbeeld_kas")
    parser.add_argument("--locatie", default="")
    parser.add_argument("--omschrijving", default="")
    parser.add_argument("--kopieer-basis-van", metavar="PROJECT",
                        help="Start from the basis (existing system) of another project.")
    args = parser.parse_args(argv)
    try:
        bestond = project_map(args.naam).exists()
        map_ = maak_project(args.naam, args.locatie, args.omschrijving, basis_van=args.kopieer_basis_van)
    except (ValueError, FileNotFoundError) as fout:
        parser.error(str(fout))
    print(("Existing project completed: " if bestond else "New project created: ") + str(map_.relative_to(ROOT)))
    print("Next steps:")
    print("  1. Put the data of the company in", raw_map(args.naam).relative_to(ROOT))
    print("  2. Fill in the existing energy system:", (map_ / "basis.json").relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
