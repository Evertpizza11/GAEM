"""Starting runs, follow, stop and save. Uses the dashboard. """

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from utils.projectpaden import ROOT, RUNS_MAP, controleer_naam, nieuwe_runmap, output_map, uren_label

LOGBESTAND = "console.log"
STARTBESTAND = "dashboard_run.json"
OPSLAGBESTAND = "opgeslagen.json"
_EXECUTE = re.compile(r"EXECUTE (\d+)/(\d+)")
_SOLVE = re.compile(r"MILP solve (\d+)")


def start_milp(project: str, scenario: str, uren: float | None, start: str | None = None,
               vergelijk: bool = False, weerjaar: str | None = None) -> tuple[subprocess.Popen, Path]:

    duur = "alles" if uren is None else uren_label(uren)
    soort = f"{weerjaar}_milp_{duur}" if weerjaar else f"milp_{duur}"
    runmap = nieuwe_runmap(project, scenario, soort)
    runmap.mkdir(parents=True)
    commando = [sys.executable, "-u", str(ROOT / "run_milp.py"), "--project", project,
                "--scenario", scenario, "--output", str(runmap)]
    commando += ["--all"] if uren is None else ["--hours", f"{uren:g}"]
    if weerjaar:
        commando.append(f"--{weerjaar}")
    if start:
        commando += ["--start", start]
    if vergelijk:
        commando.append("--compare")
    omgeving = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    log = (runmap / LOGBESTAND).open("w", encoding="utf-8")
    proces = subprocess.Popen(commando, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=omgeving, **extra)
    log.close()  
    (runmap / STARTBESTAND).write_text(json.dumps({
        "pid": proces.pid, "gestart": datetime.now().isoformat(timespec="seconds"),
        "commando": commando}, indent=2), encoding="utf-8")
    return proces, runmap


def lees_json(pad: Path) -> dict:
    try:
        return json.loads(pad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def staart(pad: Path, tekens: int = 20000) -> str:
    try:
        with pad.open("rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - tekens))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def voortgang(runmap: Path) -> dict:
    """Run status."""
    runmap = Path(runmap)
    status = lees_json(runmap / "run_status.json")
    log = staart(runmap / LOGBESTAND)
    stappen = _EXECUTE.findall(log)
    solves = _SOLVE.findall(log)
    klaar, totaal = (int(stappen[-1][0]), int(stappen[-1][1])) if stappen else (0, 0)
    return {
        "status": status.get("status", "gestart"),
        "stap": klaar, "totaal": totaal,
        "fractie": klaar / totaal if totaal else None,
        "optimalisaties": int(solves[-1]) if solves else 0,
        "fout": status.get("error"),
        "logstaart": "\n".join(log.splitlines()[-12:]),
        "run_status": status,
    }


def stop(proces: subprocess.Popen, runmap: Path) -> None:
    """Stops an ongoing run."""
    if proces.poll() is None:
        proces.terminate()
        try:
            proces.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proces.kill()
    pad = Path(runmap) / "run_status.json"
    status = lees_json(pad)
    if status.get("status") not in ("complete", "failed"):
        status.update(status="gestopt", gestopt_op=datetime.now().isoformat(timespec="seconds"))
        pad.write_text(json.dumps(status, indent=2), encoding="utf-8")


def _runinfo(pad: Path, opgeslagen: bool) -> dict:
    info = lees_json(pad / "run_info.json")
    status = lees_json(pad / "run_status.json")
    tijd = info.get("gestart") or datetime.fromtimestamp(pad.stat().st_mtime).isoformat(timespec="seconds")
    return {"pad": pad, "naam": pad.name, "opgeslagen": opgeslagen,
            "scenario": info.get("scenario", "?"), "status": status.get("status", "onbekend"), "gestart": tijd}


def runs_van_project(project: str) -> list[dict]:
    project = controleer_naam(project)
    gevonden = []
    if RUNS_MAP.is_dir():
        gevonden += [_runinfo(p, False) for p in RUNS_MAP.iterdir()
                     if p.is_dir() and f"_{project}_" in p.name and (p / "run_info.json").is_file()]
    uitvoer = output_map(project)
    if uitvoer.is_dir():
        for p in sorted(uitvoer.rglob("dispatch.csv")):
            if len(p.relative_to(uitvoer).parts) <= 3:  # output/<run> or output/<archief>/<run>
                gevonden.append(_runinfo(p.parent, True))
    # First the unsaved runs, then the saved ones; within each group, the newest ones appear at the top.
    return sorted(gevonden, key=lambda r: (not r["opgeslagen"], r["gestart"]), reverse=True)


def sla_op(runmap: Path, project: str, opmerking: str = "") -> Path:
    runmap = Path(runmap)
    status = lees_json(runmap / "run_status.json").get("status")
    if status != "complete":
        raise ValueError(f"Only finished runs can be saved (status: {status}).")
    doel = output_map(project) / runmap.name
    if doel.exists():
        raise FileExistsError(f"{doel.name} is already in the output of {project}.")
    shutil.copytree(runmap, doel)
    (doel / OPSLAGBESTAND).write_text(json.dumps({
        "opgeslagen_op": datetime.now().isoformat(timespec="seconds"),
        "bron": str(runmap), "opmerking": opmerking}, indent=2, ensure_ascii=False), encoding="utf-8")
    return doel


def verwerk_data(project: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "inputs.verwerk_project", "--project", project],
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
