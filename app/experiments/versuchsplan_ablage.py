"""Ablage der hochgeladenen Versuchsplaene: eine Datei je Stage im Instance-Ordner des Frontends
(<LABS-User-Interface>/instance/versuchsplaene/stage_<id>.xlsx + .json mit Dateiname und Zeitpunkt).
Kein Datenbankschema noetig; der Ordner ist in .gitignore. Beim naechsten Upload wird die Datei ersetzt."""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from flask import current_app


def _ordner() -> Path:
    pfad = Path(current_app.instance_path) / "versuchsplaene"
    pfad.mkdir(parents=True, exist_ok=True)
    return pfad


def speichern(stage_id: int, dateiname: str, daten: bytes) -> dict:
    ordner = _ordner()
    (ordner / f"stage_{stage_id}.xlsx").write_bytes(daten)
    meta = {"dateiname": dateiname, "hochgeladen": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "bytes": len(daten)}
    (ordner / f"stage_{stage_id}.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta


def laden(stage_id: int):
    """-> (meta, daten) oder None, wenn fuer die Stage kein Versuchsplan abgelegt ist."""
    ordner = _ordner()
    xlsx, meta = ordner / f"stage_{stage_id}.xlsx", ordner / f"stage_{stage_id}.json"
    if not xlsx.exists():
        return None
    try:
        info = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
    except (OSError, ValueError):
        info = {}
    info.setdefault("dateiname", f"versuchsplan_stage_{stage_id}.xlsx")
    return info, xlsx.read_bytes()


def info(stage_id: int):
    """Nur die Meta-Angaben (fuer die Seite), None wenn nichts abgelegt."""
    geladen = laden(stage_id)
    return None if geladen is None else geladen[0]


def ergebnisname(dateiname: str) -> str:
    stamm = dateiname[:-5] if dateiname.lower().endswith(".xlsx") else dateiname
    return f"{stamm}_LABS.xlsx"
