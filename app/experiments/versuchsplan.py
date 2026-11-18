"""Versuchsplan der Chemiker (Excel-Vorlage mit festen Zellen) lesen und daraus die Parameter einer Routine
vorbelegen (Stufe 1 + 2 aus docs/konzept_excel_versuchsplan_10-11-2026.md im Backend-Repo).

Die Zuordnung Zelle -> Parameter steht NICHT im Code, sondern in YAML-Dateien unter versuchsplan_vorlagen/
(eine Datei je Vorlage, darin je Routine ein Block). Eine Vorlage wird an ihren Beschriftungen erkannt
(Abschnitt `erkennung`: Zelle -> erwarteter Text), nicht an einer Versionszelle - so bleibt die Vorlage der
Chemiker unveraendert, und eine verschobene Zeile faellt trotzdem auf (Rafael, 18.11.2026).

Grundsaetze:
- Es wird NIE ein Lauf angelegt oder gestartet. Das Ergebnis belegt nur das Formular "Add run" vor; der
  Chemiker prueft, aendert und drueckt selbst "Add" und spaeter "Submit Stage".
- Fehlt ein Wert in der Vorlage oder gibt es fuer einen Parameter keine Zuordnung, bleibt das Feld leer und
  wird als Hinweis gemeldet. Kein stiller Standardwert.
- Formelzellen liefern nur den von Excel zuletzt gespeicherten Wert (openpyxl data_only=True). Ist der leer,
  wurde die Datei nicht in Excel gespeichert -> Fehler mit Anleitung, keine halbe Vorbelegung.

Nur openpyxl und PyYAML, kein Flask: in tests/test_versuchsplan.py ohne App testbar.
"""
from __future__ import annotations

import datetime as _dt
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
import yaml

VORLAGEN_ORDNER = Path(__file__).parent / "versuchsplan_vorlagen"
_ZELLE = re.compile(r"^(?P<blatt>[^!]+)!(?P<zelle>[A-Z]{1,3}[0-9]{1,6})(?::(?P<bis>[A-Z]{1,3}[0-9]{1,6}))?$")


class VorlageFehler(Exception):
    """Datei ist keine bekannte Vorlage oder nicht auswertbar. Die Meldung ist fuer den Chemiker gedacht."""


@dataclass
class Zeile:
    """Eine Zeile der Anzeige: was aus welcher Zelle fuer welchen Parameter gelesen wurde."""
    parameter: str
    zelle: str
    bezeichnung: str
    roh: str
    wert: str
    einheit: str
    hinweis: str = ""


@dataclass
class Ergebnis:
    routine: str
    vorlage: str
    dateiname: str
    werte: dict = field(default_factory=dict)      # Parametername -> Text fuer das Formular (nur belegte)
    zeilen: list = field(default_factory=list)     # Zeile je Parameter der Zuordnung, auch leere
    hinweise: list = field(default_factory=list)   # Warnungen (Einheit, nur_wenn, Pruefungen)
    offen: list = field(default_factory=list)      # Parameter der Routine ohne Wert -> von Hand eintragen


def _zelle_parsen(ref: str):
    m = _ZELLE.match(str(ref).strip())
    if not m:
        raise VorlageFehler(f"Zuordnungsdatei: '{ref}' ist keine Zellangabe der Form Blatt!B5 oder Blatt!A3:A17")
    return m.group("blatt"), m.group("zelle"), m.group("bis")


def _als_text(value, runden=None, ganzzahl=False) -> str:
    """Zellwert -> Text fuer das Formular. Zahlen ohne nachlaufende Nullen, Datum als JJJJ-MM-TT."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        if ganzzahl:
            return str(int(round(value)))
        if runden is not None:
            value = round(float(value), int(runden))
        text = f"{float(value):.10g}"
        return text
    return str(value).strip()


class Versuchsplan:
    def __init__(self, wb_werte, wb_formeln, vorlage: dict, vorlagendatei: str, dateiname: str):
        self._werte = wb_werte
        self._formeln = wb_formeln
        self.vorlage = vorlage
        self.vorlagendatei = vorlagendatei
        self.dateiname = dateiname

    # -- Laden -----------------------------------------------------------------------------------------

    @staticmethod
    def vorlagen(ordner: Path = VORLAGEN_ORDNER) -> list:
        """[(Dateiname, Inhalt)] aller Zuordnungsdateien, alphabetisch."""
        out = []
        for pfad in sorted(Path(ordner).glob("*.yml")):
            with open(pfad, encoding="utf-8") as handle:
                inhalt = yaml.safe_load(handle) or {}
            if "vorlage" not in inhalt or "erkennung" not in inhalt["vorlage"]:
                raise VorlageFehler(f"Zuordnungsdatei {pfad.name}: Abschnitt vorlage/erkennung fehlt")
            out.append((pfad.name, inhalt))
        if not out:
            raise VorlageFehler(f"Keine Zuordnungsdatei (*.yml) in {ordner}")
        return out

    @classmethod
    def laden(cls, datei, dateiname: str = "", ordner: Path = VORLAGEN_ORDNER) -> "Versuchsplan":
        """datei: Pfad oder Dateiobjekt (z.B. Flask FileStorage.stream). Erkennt die Vorlage an den
        Beschriftungen; passt keine, VorlageFehler mit den Abweichungen der aehnlichsten Vorlage."""
        if hasattr(datei, "read"):
            daten = datei.read()
        else:
            dateiname = dateiname or Path(datei).name
            with open(datei, "rb") as handle:
                daten = handle.read()
        try:
            wb_werte = openpyxl.load_workbook(io.BytesIO(daten), data_only=True, read_only=True)
            wb_formeln = openpyxl.load_workbook(io.BytesIO(daten), data_only=False, read_only=True)
        except Exception as exc:  # noqa: BLE001 - openpyxl wirft verschiedene Typen
            raise VorlageFehler(f"'{dateiname}' laesst sich nicht als Excel-Datei (.xlsx) lesen: {exc}") from exc
        plan = cls(wb_werte, wb_formeln, {}, "", dateiname)
        beste, beste_fehler = None, None
        for name, vorlage in cls.vorlagen(ordner):
            fehler = plan._erkennung_pruefen(vorlage["vorlage"]["erkennung"])
            if not fehler:
                plan.vorlage, plan.vorlagendatei = vorlage, name
                return plan
            if beste_fehler is None or len(fehler) < len(beste_fehler):
                beste, beste_fehler = name, fehler
        raise VorlageFehler(
            f"'{dateiname}' passt zu keiner bekannten Vorlage. Naechste Vorlage {beste}, dort stimmen nicht: "
            + "; ".join(beste_fehler[:6]) + (" ..." if len(beste_fehler) > 6 else "")
            + ". Vorlage unveraendert verwenden oder die Zuordnungsdatei anpassen.")

    def _erkennung_pruefen(self, erkennung: dict) -> list:
        fehler = []
        for ref, erwartet in erkennung.items():
            try:
                gelesen = self._roh(ref, formel_pruefen=False)
            except VorlageFehler as exc:
                fehler.append(str(exc))
                continue
            if _als_text(gelesen).strip().lower() != str(erwartet).strip().lower():
                fehler.append(f"{ref} = {_als_text(gelesen)!r}, erwartet {str(erwartet)!r}")
        return fehler

    # -- Zellen lesen ----------------------------------------------------------------------------------

    def _roh(self, ref: str, formel_pruefen: bool = True):
        """Gespeicherter Wert einer Zelle. Bereich (A3:A17) -> Liste ohne leere Zellen."""
        blatt, zelle, bis = _zelle_parsen(ref)
        if blatt not in self._werte.sheetnames:
            raise VorlageFehler(f"Blatt '{blatt}' fehlt in der Datei (vorhanden: {', '.join(self._werte.sheetnames)})")
        ws = self._werte[blatt]
        if bis:
            return [c.value for row in ws[f"{zelle}:{bis}"] for c in row if c.value is not None]
        wert = ws[zelle].value
        if wert is None and formel_pruefen:
            formel = self._formeln[blatt][zelle].value
            if isinstance(formel, str) and formel.startswith("="):
                raise VorlageFehler(
                    f"Zelle {ref} enthaelt eine Formel ({formel[:40]}), aber keinen gespeicherten Wert. Die Datei in "
                    f"Excel oeffnen, speichern und erneut hochladen.")
        return wert

    def bezeichnung(self, ref: str) -> str:
        """Beschriftung links neben der Zelle (Spalte A derselben Zeile), fuer die Anzeige."""
        try:
            blatt, zelle, _ = _zelle_parsen(ref)
            zeile = re.sub(r"[A-Z]+", "", zelle)
            return _als_text(self._werte[blatt][f"A{zeile}"].value)
        except (VorlageFehler, KeyError):
            return ""

    # -- Routine vorbelegen ----------------------------------------------------------------------------

    def routinen(self) -> list:
        return list((self.vorlage.get("routinen") or {}).keys())

    def fuer_routine(self, routinenname: str, parameter: dict) -> Ergebnis:
        """parameter: {Parametername: Einheit} der Routine, wie im Frontend gespeichert (aus der Station).
        Liefert None-frei: fuer jeden Parameter der Routine entweder einen Wert oder einen Eintrag in `offen`."""
        block = (self.vorlage.get("routinen") or {}).get(routinenname)
        if block is None:
            raise VorlageFehler(
                f"Fuer die Routine '{routinenname}' gibt es in {self.vorlagendatei} keine Zuordnung. "
                f"Zugeordnet sind: {', '.join(self.routinen()) or 'keine'}.")
        ergebnis = Ergebnis(routine=routinenname, vorlage=self.vorlage["vorlage"].get("name", self.vorlagendatei),
                            dateiname=self.dateiname)
        for pruefung in (self.vorlage["vorlage"].get("pruefungen") or []) + (block.get("pruefungen") or []):
            gelesen = _als_text(self._roh(pruefung["zelle"], formel_pruefen=False))
            if gelesen.strip().lower() != str(pruefung["erwartet"]).strip().lower():
                ergebnis.hinweise.append(
                    f"{pruefung['zelle']} = {gelesen!r}, erwartet {pruefung['erwartet']!r}: {pruefung.get('meldung', '')}".strip())
        nur_wenn = block.get("nur_wenn")
        if nur_wenn:
            gelesen = _als_text(self._roh(nur_wenn["zelle"], formel_pruefen=False))
            if gelesen.strip().lower() != str(nur_wenn["wert"]).strip().lower():
                ergebnis.hinweise.append(
                    f"Die Vorlage sagt {nur_wenn['zelle']} = {gelesen!r}; die Routine {routinenname} ist fuer "
                    f"{nur_wenn['wert']!r} gedacht. Routine pruefen.")
        zuordnungen = block.get("parameter") or {}
        for name, einheit in parameter.items():
            zu = zuordnungen.get(name)
            if zu is None:
                ergebnis.offen.append(name)
                ergebnis.zeilen.append(Zeile(name, "-", "", "", "", einheit or "",
                                             "steht nicht in der Vorlage - von Hand eintragen"))
                continue
            if zu.get("einheit_routine") not in (None, "") and (einheit or "") != zu["einheit_routine"]:
                ergebnis.hinweise.append(
                    f"{name}: die Station meldet die Einheit {einheit!r}, die Zuordnung rechnet mit "
                    f"{zu['einheit_routine']!r} - Faktor pruefen.")
            zelle_text, roh_text, wert = self._parameter_lesen(zu)
            hinweis = zu.get("hinweis", "")
            if wert == "":
                ergebnis.offen.append(name)
                hinweis = (hinweis + " " if hinweis else "") + "Zelle leer - von Hand eintragen"
            else:
                ergebnis.werte[name] = wert
            ergebnis.zeilen.append(Zeile(name, zelle_text, self.bezeichnung(zelle_text.split(",")[0]) if "!" in zelle_text else "",
                                         roh_text, wert, einheit or zu.get("einheit_routine") or "", hinweis.strip()))
        for name in zuordnungen:
            if name not in parameter:
                ergebnis.hinweise.append(
                    f"Die Zuordnung kennt den Parameter {name!r}, die Routine {routinenname} an der Station aber nicht "
                    f"(Routine im Frontend neu laden?).")
        return ergebnis

    def _parameter_lesen(self, zu: dict):
        """-> (Zellangabe fuer die Anzeige, Rohwert als Text, fertiger Wert als Text oder '')."""
        faktor = zu.get("faktor")
        runden, ganzzahl = zu.get("runden"), bool(zu.get("ganzzahl"))
        if "summe" in zu:
            teile = [self._roh(ref) for ref in zu["summe"]]
            zahlen = [float(t) for t in teile if t not in (None, "")]
            if not zahlen:
                return ", ".join(zu["summe"]), "", ""
            summe = sum(zahlen)
            if faktor:
                summe *= float(faktor)
            return ", ".join(zu["summe"]), " + ".join(_als_text(t, 4) for t in teile), _als_text(summe, runden, ganzzahl)
        if "zellen" in zu:
            werte = self._roh(zu["zellen"])
            trenner = zu.get("trenner", ";")
            return zu["zellen"], trenner.join(_als_text(w) for w in werte), trenner.join(_als_text(w, runden, ganzzahl) for w in werte)
        ref = zu["zelle"]
        roh = self._roh(ref)
        if roh in (None, ""):
            return ref, "", ""
        if zu.get("text"):
            return ref, _als_text(roh), _als_text(roh)
        if faktor:
            try:
                roh_zahl = float(roh)
            except (TypeError, ValueError):
                raise VorlageFehler(f"Zelle {ref} = {roh!r} ist keine Zahl, soll aber mit {faktor} umgerechnet werden")
            return ref, _als_text(roh, 4), _als_text(roh_zahl * float(faktor), runden, ganzzahl)
        return ref, _als_text(roh, 4), _als_text(roh, runden, ganzzahl)
