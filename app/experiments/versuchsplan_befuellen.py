"""Stufe 3: Messwerte eines fertigen Laufs in eine KOPIE des hochgeladenen Versuchsplans schreiben.

Eingang: die Originaldatei (Bytes), der Block `ausgabe/<routine>` aus der Zuordnungsdatei
(versuchsplan_vorlagen/*.yml), lauf.json und values.json des Laufs (vom Backend ueber /api/run_log) und
die Parameter des Laufs. Ausgang: die befuellte Kopie (Bytes) und ein Protokoll (Liste von Saetzen), das dem
Chemiker sagt, was geschrieben wurde und was nicht.

Das Original wird nie veraendert. openpyxl behaelt Formeln und Formate, verliert aber Diagramme und Bilder -
die Vorlage hat keine (Stand 10/2026).

Rechenarten (`art`):
  wert             naechster Messwert zum Zeitpunkt, hoechstens toleranz_s entfernt, sonst leer
  differenz_zu_t0  Messwert zum Zeitpunkt minus Messwert bei t = 0 (z.B. Gaszaehler)
  letzter          letzter Messwert des Laufs
  erster           erster Messwert des Laufs
Quellen fuer einzelne Zellen (`quelle`): startdatum, startzeit, endzeit, lauf (Lauf-ID), vermerk.
`minus_parameter: [a, b]` zieht die genannten Laufparameter vom Ergebnis ab (z.B. dosiertes NaOH =
alles dosierte Reagenz minus feste Zugaben).
"""
from __future__ import annotations

import datetime as _dt
import io
import re

import openpyxl

_ZELLE = re.compile(r"^(?P<blatt>[^!]+)!(?P<zelle>[A-Z]{1,3}[0-9]{1,6})$")


class BefuellenFehler(Exception):
    pass


def _zeitstempel(wert) -> float:
    """starting_time/finishing_time aus lauf.json: float (Epoche) oder ISO-Text."""
    if wert is None:
        raise BefuellenFehler("lauf.json hat keine Startzeit - der Lauf ist nicht gestartet oder nicht fertig")
    try:
        return float(wert)
    except (TypeError, ValueError):
        pass
    text = str(wert).strip().replace("Z", "+00:00")
    try:
        return _dt.datetime.fromisoformat(text).timestamp()
    except ValueError:
        raise BefuellenFehler(f"Startzeit {wert!r} in lauf.json ist weder Zahl noch ISO-Datum")


def _reihe(values: dict, name: str) -> list:
    """'geraet.observable' -> [(t, wert), ...] sortiert, ohne None/Text."""
    try:
        geraet, observable = name.split(".", 1)
    except ValueError:
        raise BefuellenFehler(f"Zuordnungsdatei: Observable {name!r} muss 'geraet.observable' heissen")
    punkte = (values.get(geraet) or {}).get(observable) or []
    out = []
    for p in punkte:
        try:
            t, w = float(p[0]), p[1]
        except (TypeError, ValueError, IndexError):
            continue
        if isinstance(w, bool) or not isinstance(w, (int, float)):
            continue
        out.append((t, float(w)))
    out.sort()
    return out


def _naechster(reihe: list, t: float, toleranz_s: float):
    """Wert mit dem kleinsten Zeitabstand zu t, wenn er hoechstens toleranz_s betraegt; sonst None."""
    best = None
    for zeit, wert in reihe:
        abstand = abs(zeit - t)
        if best is None or abstand < best[0]:
            best = (abstand, wert)
        if zeit > t + toleranz_s:
            break
    if best is None or best[0] > toleranz_s:
        return None
    return best[1]


def _runden(wert, stellen):
    if wert is None:
        return None
    if stellen is None:
        return round(wert, 6)
    return round(wert, int(stellen))


def befuellen(vorlage: bytes, routine: str, ausgabe: dict, lauf: dict, values: dict) -> tuple:
    """-> (Bytes der befuellten Kopie, Protokoll als Liste von Saetzen). ausgabe = Block ausgabe/<routine>."""
    if not ausgabe:
        raise BefuellenFehler(f"Fuer die Routine {routine!r} gibt es keinen Abschnitt 'ausgabe' in der Zuordnungsdatei")
    wb = openpyxl.load_workbook(io.BytesIO(vorlage))      # Formeln bleiben erhalten (kein data_only)
    protokoll = []
    t0 = _zeitstempel(lauf.get("starting_time"))
    t_ende = lauf.get("finishing_time")
    t_ende = _zeitstempel(t_ende) if t_ende else None
    lauf_id = lauf.get("experiment_id", "?")
    parameter = {name: (p.get("value") if isinstance(p, dict) else p) for name, p in (lauf.get("parameters") or {}).items()}

    def blatt(name):
        if name not in wb.sheetnames:
            raise BefuellenFehler(f"Blatt {name!r} fehlt in der Vorlage")
        return wb[name]

    def observable_wert(zu: dict, t=None):
        reihe = _reihe(values, zu["observable"])
        if not reihe:
            return None, f"keine Messwerte fuer {zu['observable']}"
        art = zu.get("art", "wert" if t is not None else "letzter")
        if art == "letzter":
            wert = reihe[-1][1]
        elif art == "erster":
            wert = reihe[0][1]
        elif art == "wert":
            wert = _naechster(reihe, t, float(zu.get("toleranz_s", 60)))
        elif art == "differenz_zu_t0":
            start = _naechster(reihe, t0, float(zu.get("toleranz_s", 60)))
            if start is None:
                start = reihe[0][1]
            jetzt = _naechster(reihe, t, float(zu.get("toleranz_s", 60)))
            wert = None if jetzt is None else jetzt - start
        else:
            raise BefuellenFehler(f"Zuordnungsdatei: unbekannte art {art!r}")
        if wert is None:
            return None, None
        for pname in zu.get("minus_parameter") or []:
            try:
                wert -= float(parameter.get(pname) or 0)
            except (TypeError, ValueError):
                pass
        return _runden(wert, zu.get("runden")), None

    # -- Zeitraster (Exp_protocol) ----------------------------------------------------------------------
    raster = ausgabe.get("zeitraster")
    if raster:
        ws = blatt(raster["blatt"])
        von, bis = (int(x) for x in str(raster.get("zeilen", "3-17")).split("-"))
        zeit_spalte = raster.get("zeit_spalte", "A")
        faktor = 60.0 if raster.get("einheit", "min") == "min" else 1.0
        toleranz = float(raster.get("toleranz_s", 60))
        geschrieben, leer = 0, 0
        for zeile in range(von, bis + 1):
            minuten = ws[f"{zeit_spalte}{zeile}"].value
            if not isinstance(minuten, (int, float)) or isinstance(minuten, bool):
                continue
            t = t0 + float(minuten) * faktor
            if t_ende is not None and t > t_ende + toleranz:
                leer += 1
                continue                                  # Zeitpunkt nach Laufende: bleibt leer
            for spalte, zu in (raster.get("spalten") or {}).items():
                zu = dict(zu)
                zu.setdefault("toleranz_s", toleranz)
                wert, _ = observable_wert(zu, t)
                if wert is not None:
                    ws[f"{spalte}{zeile}"] = wert
                    geschrieben += 1
        protokoll.append(f"{raster['blatt']}: {geschrieben} Messwerte im Zeitraster eingetragen"
                         + (f", {leer} Zeitpunkte liegen nach dem Laufende und bleiben leer" if leer else "") + ".")
        ende = raster.get("ende")
        if ende:
            n = 0
            for spalte, observable in (ende.get("spalten") or {}).items():
                wert, _ = observable_wert({"observable": observable, "art": "letzter", "runden": ende.get("runden")})
                if wert is not None:
                    ws[f"{spalte}{ende['zeile']}"] = wert
                    n += 1
            protokoll.append(f"{raster['blatt']} Zeile {ende['zeile']} (nach dem Lauf): {n} letzte Werte eingetragen.")

    # -- einzelne Zellen ---------------------------------------------------------------------------------
    for ref, zu in (ausgabe.get("zellen") or {}).items():
        m = _ZELLE.match(ref)
        if not m:
            raise BefuellenFehler(f"Zuordnungsdatei: {ref!r} ist keine Zelle der Form Blatt!B5")
        ws = blatt(m.group("blatt"))
        zelle = m.group("zelle")
        quelle = zu.get("quelle")
        if quelle:
            start = _dt.datetime.fromtimestamp(t0)
            if quelle == "startdatum":
                wert = start.date()
            elif quelle == "startzeit":
                wert = start.strftime("%Y-%m-%d %H:%M")
            elif quelle == "endzeit":
                wert = _dt.datetime.fromtimestamp(t_ende).strftime("%Y-%m-%d %H:%M") if t_ende else None
            elif quelle == "lauf":
                wert = lauf_id
            elif quelle == "vermerk":
                wert = f"aus LABS-Lauf {lauf_id} befuellt am {_dt.date.today().isoformat()}"
            else:
                raise BefuellenFehler(f"Zuordnungsdatei: unbekannte quelle {quelle!r}")
            if wert is not None:
                ws[zelle] = wert
                protokoll.append(f"{ref} = {wert} ({quelle}).")
            continue
        wert, grund = observable_wert(zu)
        if wert is None:
            protokoll.append(f"{ref} bleibt leer: {grund or 'kein Messwert'}.")
            continue
        ws[zelle] = wert
        text = f"{ref} = {wert} ({zu['observable']}, {zu.get('art', 'letzter')}"
        if zu.get("minus_parameter"):
            text += f" minus {', '.join(zu['minus_parameter'])}"
        text += ")" + (f" - {zu['hinweis']}" if zu.get("hinweis") else "") + "."
        protokoll.append(text)

    puffer = io.BytesIO()
    wb.save(puffer)
    return puffer.getvalue(), protokoll
