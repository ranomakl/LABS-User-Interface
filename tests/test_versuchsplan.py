"""Versuchsplan-Leser (app/experiments/versuchsplan.py) ohne Flask und ohne Datenbank.
Lauf: .venv/Scripts/python -m unittest tests.test_versuchsplan -v   (im Ordner LABS-User-Interface)

Die Vorlage wird mit openpyxl nachgebaut (gleiche Beschriftungen und Zellen wie die Datei der Chemiker,
Werte statt Formeln). Liegt die echte Vorlage unter tests/data/*.xlsx, wird sie zusaetzlich geprueft."""
import glob
import io
import os
import sys
import unittest
from pathlib import Path

import openpyxl

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from app.experiments import versuchsplan as vp  # noqa: E402

HIER = Path(__file__).parent

# Zellen und Werte wie in Experiment_26174_flow_cell_opt_cond_max_1-chamber_rep_1.xlsx
SETUP = {
    "A1": "Experiment number", "B1": 26174,
    "A9": "one- or two-chamber", "B9": "One-Chamber",
    "A18": "flow rate", "B18": 100, "C18": "[mL min^-1]",
    "A23": "Carboxylic acid concentration", "B23": 1.5,
    "A26": "pH value", "B26": 9.5,
    "A27": "Pool volume", "B27": 100,
    "A29": "Measurement mode (potentiostatic / galvanostatic)", "B29": "galvanostatic",
    "A30": "voltage/current", "B30": 550, "C30": "[mA]",
    "A33": "Duration (regarding z = 1 & galvanostatic)", "B33": 219.2840909090909,
}
PREP = {
    "A5": "Water (part 1)", "B5": 50,
    "A6": "n-butanoic acid (MW: 88.1 g mol^-1; Density: 0.96 g mL^-1)", "B6": 3.4414062500000004,
    "A7": "n-Hexanoic acid (MW: 116.2 g mol^-1; Density: 0.93 g mL^-1)", "B7": 12.494623655913978,
    "A8": "n-Octanoic acid (MW: 144.2 g mol^-1; Density: 0.91 g mL^-1)", "B8": 1.9807692307692308,
    "A12": " + NaOH (10 M)",            # B12 leer: Handeintrag
    "A17": " + supporting electrolyte Na2SO4 (MW: 142.04 g mol^-1)", "B17": 3.551,
}
PROTOKOLL = {"A1": "cumul. Time [min]", "A2": "Before"}
for i, t in enumerate([0, 10, 20, 30, 40, 50, 60, 80, 100, 120, 140, 160, 180, 200, 219], start=3):
    PROTOKOLL[f"A{i}"] = t
ANALYTICS = {"A3": "microGC method", "B3": "MicroGC_standard_method_calibrated_0726",
             "A12": "N2 purging before electrolysis", "B12": 10}


def vorlage_bauen(**aenderungen) -> io.BytesIO:
    """Nachgebaute Vorlage als Datei im Speicher. aenderungen: {"Setup_parameter!B9": "Two-Chamber", ...}"""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, zellen in (("Setup_parameter", SETUP), ("Exp_preparation", PREP), ("Exp_protocol", PROTOKOLL),
                         ("Exp_postprocessing", {}), ("Analytics", ANALYTICS)):
        ws = wb.create_sheet(name)
        for zelle, wert in zellen.items():
            ws[zelle] = wert
    for ref, wert in aenderungen.items():
        blatt, zelle = ref.split("!")
        wb[blatt][zelle] = wert
    puffer = io.BytesIO()
    wb.save(puffer)
    puffer.seek(0)
    return puffer


ROUTINEN = {
    "kolbe_elektrolyse_gc": {"versuch": "", "gc_minutes": "", "current": "A", "max_voltage": "V", "minutes": "min", "rate": "mL/min"},
    "ansatz": {"vol_leitsalz": "mL", "vol_wasser": "mL", "vol_carbonsaeure": "mL", "vol_naoh": "mL", "ziel_ph": "pH",
               "toleranz": "pH", "vol_gross": "mL", "vol_klein": "mL", "wartezeit_ph_messung": "s", "zielvolumen": "mL",
               "pause_zugabe": "s"},
    "ph_regelung": {"ziel_ph": "pH", "toleranz": "pH", "vol_gross": "mL", "vol_klein": "mL", "wartezeit_ph_messung": "s",
                    "startvolumen": "mL", "zielvolumen": "mL"},
    "kolbe_n2_spuelung": {"rate": "mL/min", "minutes": "min"},
}


class ErkennungTests(unittest.TestCase):
    def test_vorlage_wird_erkannt(self):
        plan = vp.Versuchsplan.laden(vorlage_bauen(), "Experiment_26174.xlsx")
        self.assertEqual(plan.vorlagendatei, "kolbe_flow_v1.yml")
        self.assertIn("ansatz", plan.routinen())
        self.assertIn("kolbe_elektrolyse_gc", plan.routinen())

    def test_verschobene_zeile_wird_abgelehnt(self):
        datei = vorlage_bauen(**{"Setup_parameter!A27": "Reactor volume"})
        with self.assertRaises(vp.VorlageFehler) as ctx:
            vp.Versuchsplan.laden(datei, "x.xlsx")
        self.assertIn("Setup_parameter!A27", str(ctx.exception))
        self.assertIn("Pool volume", str(ctx.exception))

    def test_keine_excel_datei(self):
        with self.assertRaises(vp.VorlageFehler):
            vp.Versuchsplan.laden(io.BytesIO(b"kein excel"), "x.xlsx")

    def test_formel_ohne_gespeicherten_wert(self):
        wb = openpyxl.load_workbook(vorlage_bauen())
        wb["Setup_parameter"]["B33"] = "=(B23*B11/1000)*96485"      # Formel, kein Ergebnis gespeichert
        puffer = io.BytesIO()
        wb.save(puffer)
        puffer.seek(0)
        plan = vp.Versuchsplan.laden(puffer, "x.xlsx")
        with self.assertRaises(vp.VorlageFehler) as ctx:
            plan.fuer_routine("kolbe_elektrolyse_gc", ROUTINEN["kolbe_elektrolyse_gc"])
        self.assertIn("in Excel oeffnen", str(ctx.exception))


class KolbeTests(unittest.TestCase):
    def setUp(self):
        self.plan = vp.Versuchsplan.laden(vorlage_bauen(), "Experiment_26174.xlsx")

    def test_elektrolyse_gc(self):
        e = self.plan.fuer_routine("kolbe_elektrolyse_gc", ROUTINEN["kolbe_elektrolyse_gc"])
        self.assertEqual(e.werte, {"versuch": "26174", "rate": "100", "current": "0.55", "minutes": "219.3",
                                   "gc_minutes": "0;10;20;30;40;50;60;80;100;120;140;160;180;200;219"})
        self.assertEqual(e.offen, ["max_voltage"])
        self.assertEqual([h for h in e.hinweise if "Two-Chamber" in h], [])
        zeile = {z.parameter: z for z in e.zeilen}
        self.assertEqual(zeile["current"].roh, "550")
        self.assertEqual(zeile["current"].bezeichnung, "voltage/current")
        self.assertIn("von Hand", zeile["max_voltage"].hinweis)

    def test_zweikammer_warnung(self):
        plan = vp.Versuchsplan.laden(vorlage_bauen(**{"Setup_parameter!B9": "Two-Chamber"}), "x.xlsx")
        e = plan.fuer_routine("kolbe_elektrolyse_gc", ROUTINEN["kolbe_elektrolyse_gc"])
        self.assertTrue(any("Two-Chamber" in h and "One-Chamber" in h for h in e.hinweise), e.hinweise)

    def test_messmodus_pruefung(self):
        plan = vp.Versuchsplan.laden(vorlage_bauen(**{"Setup_parameter!B29": "potentiostatic"}), "x.xlsx")
        e = plan.fuer_routine("kolbe_n2_spuelung", ROUTINEN["kolbe_n2_spuelung"])
        self.assertTrue(any("galvanostatisch" in h for h in e.hinweise), e.hinweise)
        self.assertEqual(e.werte, {"rate": "100", "minutes": "10"})

    def test_einheit_der_station_anders_als_erwartet(self):
        e = self.plan.fuer_routine("kolbe_elektrolyse_gc", {**ROUTINEN["kolbe_elektrolyse_gc"], "current": "mA"})
        self.assertTrue(any("current" in h and "Faktor" in h for h in e.hinweise), e.hinweise)

    def test_routine_ohne_zuordnung(self):
        with self.assertRaises(vp.VorlageFehler) as ctx:
            self.plan.fuer_routine("test_pump1_flow", {"rate": "mL/min"})
        self.assertIn("keine Zuordnung", str(ctx.exception))

    def test_parameter_in_zuordnung_aber_nicht_an_der_station(self):
        e = self.plan.fuer_routine("kolbe_n2_spuelung", {"rate": "mL/min"})     # minutes fehlt an der Station
        self.assertTrue(any("minutes" in h and "neu laden" in h for h in e.hinweise), e.hinweise)


class AnsatzTests(unittest.TestCase):
    def setUp(self):
        self.plan = vp.Versuchsplan.laden(vorlage_bauen(), "Experiment_26174.xlsx")

    def test_ansatz(self):
        e = self.plan.fuer_routine("ansatz", ROUTINEN["ansatz"])
        self.assertEqual(e.werte, {"vol_wasser": "50", "vol_carbonsaeure": "17.92", "ziel_ph": "9.5", "zielvolumen": "100"})
        self.assertEqual(e.offen, ["vol_leitsalz", "vol_naoh", "toleranz", "vol_gross", "vol_klein",
                                   "wartezeit_ph_messung", "pause_zugabe"])
        zeile = {z.parameter: z for z in e.zeilen}
        self.assertEqual(zeile["vol_carbonsaeure"].roh, "3.4414 + 12.4946 + 1.9808")
        self.assertIn("Zelle leer", zeile["vol_naoh"].hinweis)
        self.assertIn("Port 6", zeile["vol_carbonsaeure"].hinweis)

    def test_naoh_eingetragen(self):
        plan = vp.Versuchsplan.laden(vorlage_bauen(**{"Exp_preparation!B12": 10}), "x.xlsx")
        e = plan.fuer_routine("ansatz", ROUTINEN["ansatz"])
        self.assertEqual(e.werte["vol_naoh"], "10")
        self.assertNotIn("vol_naoh", e.offen)

    def test_ph_regelung(self):
        e = self.plan.fuer_routine("ph_regelung", ROUTINEN["ph_regelung"])
        self.assertEqual(e.werte, {"ziel_ph": "9.5", "zielvolumen": "100"})
        self.assertEqual(len(e.offen), 5)


class EchteVorlageTests(unittest.TestCase):
    """Nur wenn eine echte Vorlage unter tests/data liegt (nicht im Repo)."""

    def test_echte_dateien(self):
        dateien = glob.glob(str(HIER / "data" / "*.xlsx"))
        if not dateien:
            self.skipTest("keine echte Vorlage unter tests/data")
        for pfad in dateien:
            plan = vp.Versuchsplan.laden(pfad)
            e = plan.fuer_routine("kolbe_elektrolyse_gc", ROUTINEN["kolbe_elektrolyse_gc"])
            self.assertIn("minutes", e.werte, pfad)
            e = plan.fuer_routine("ansatz", ROUTINEN["ansatz"])
            self.assertIn("vol_carbonsaeure", e.werte, pfad)


if __name__ == "__main__":
    unittest.main()
