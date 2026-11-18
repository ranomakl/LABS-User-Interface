"""Stufe 3: Messwerte in die Kopie der Vorlage schreiben (app/experiments/versuchsplan_befuellen.py), ohne Flask.
Lauf: .venv/Scripts/python -m unittest tests.test_versuchsplan_befuellen -v"""
import io
import os
import sys
import unittest

import openpyxl

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from app.experiments import versuchsplan as vp  # noqa: E402
from app.experiments import versuchsplan_befuellen as vb  # noqa: E402
from tests.test_versuchsplan import vorlage_bauen  # noqa: E402

T0 = 1_700_000_000.0


def kolbe_lauf(dauer_min=219, takt_s=10):
    """lauf.json + values.json eines Elektrolyse-Laufs: pH faellt linear von 9,5 auf 8,5, Spannung 4,0 V,
    Gaszaehler 0,5 mLn/s ab Start (Zaehler stand vorher bei 100), Temperatur 23,5."""
    n = int(dauer_min * 60 / takt_s) + 1
    ts = [T0 + i * takt_s for i in range(n)]
    values = {
        "liquiline": {
            "ph_ch1_value": [[t, 9.5 - (t - T0) / (dauer_min * 60)] for t in ts],
            "conductivity_value": [[t, 12.0] for t in ts],
            "temperature_value": [[t, 23.5] for t in ts],
            "ph_ch1_status": [[t, "Good"] for t in ts],
            "state": [[T0, "Busy"]],
        },
        "psu1": {"voltage": [[t, 4.0] for t in ts], "current": [[t, 0.55] for t in ts]},
        "mfc1": {"counter": [[t, 100.0 + 0.5 * (t - T0)] for t in ts], "flow": [[t, 30.0] for t in ts]},
    }
    lauf = {"experiment_id": "D-Elektrolyse-7", "experiment_type": "kolbe_elektrolyse_gc",
            "parameters": {"current": {"value": 0.55, "unit": "A"}, "minutes": {"value": dauer_min, "unit": "min"}},
            "starting_time": T0, "finishing_time": T0 + dauer_min * 60, "final_state": "Finished"}
    return lauf, values


def ausgabe_block(routine):
    plan = vp.Versuchsplan.laden(vorlage_bauen(), "x.xlsx")
    return plan.vorlage["ausgabe"][routine]


class KolbeBefuellenTests(unittest.TestCase):
    def setUp(self):
        self.vorlage = vorlage_bauen().getvalue()
        self.lauf, self.values = kolbe_lauf()
        self.bytes, self.protokoll = vb.befuellen(self.vorlage, "kolbe_elektrolyse_gc", ausgabe_block("kolbe_elektrolyse_gc"),
                                                  self.lauf, self.values)
        self.wb = openpyxl.load_workbook(io.BytesIO(self.bytes))
        self.ws = self.wb["Exp_protocol"]

    def test_zeitraster(self):
        # Zeile 3 = 0 min, Zeile 9 = 60 min, Zeile 17 = 219 min
        self.assertAlmostEqual(self.ws["C3"].value, 9.5, places=2)
        self.assertAlmostEqual(self.ws["C9"].value, 9.5 - 60 / 219, places=2)
        self.assertAlmostEqual(self.ws["C17"].value, 8.5, places=2)
        self.assertAlmostEqual(self.ws["B3"].value, 0.0, places=1)          # Gaszaehler Differenz zu t0
        self.assertAlmostEqual(self.ws["B9"].value, 0.5 * 3600, places=1)   # 60 min x 0,5 mLn/s
        self.assertEqual(self.ws["F9"].value, 4.0)
        self.assertEqual(self.ws["D9"].value, 12.0)
        self.assertEqual(self.ws["G9"].value, 23.5)
        self.assertIsNone(self.ws["E9"].value)                              # H2: keine Zuordnung
        self.assertEqual(self.ws["A9"].value, 60)                           # Zeitspalte unveraendert

    def test_ende_und_zellen(self):
        self.assertAlmostEqual(self.ws["C18"].value, 8.5, places=2)         # After Electrolysis: letzter Wert
        self.assertEqual(self.ws["F18"].value, 4.0)
        self.assertIn("D-Elektrolyse-7", str(self.ws["H3"].value))          # Vermerk
        import datetime
        self.assertEqual(self.wb["Setup_parameter"]["B2"].value.date() if hasattr(self.wb["Setup_parameter"]["B2"].value, "date")
                         else self.wb["Setup_parameter"]["B2"].value, datetime.date.fromtimestamp(T0))
        self.assertTrue(any("Exp_protocol: " in s and "Messwerte" in s for s in self.protokoll), self.protokoll)

    def test_formeln_und_eingaben_bleiben(self):
        self.assertEqual(self.wb["Setup_parameter"]["B1"].value, 26174)
        self.assertEqual(self.wb["Exp_preparation"]["B5"].value, 50)
        self.assertEqual(self.ws["A18"].value, "After Electrolysis")

    def test_kuerzerer_lauf_laesst_spaete_zeitpunkte_leer(self):
        lauf, values = kolbe_lauf(dauer_min=100)
        b, protokoll = vb.befuellen(self.vorlage, "kolbe_elektrolyse_gc", ausgabe_block("kolbe_elektrolyse_gc"), lauf, values)
        ws = openpyxl.load_workbook(io.BytesIO(b))["Exp_protocol"]
        self.assertIsNotNone(ws["C11"].value)        # 100 min
        self.assertIsNone(ws["C12"].value)           # 120 min: nach Laufende
        self.assertTrue(any("nach dem Laufende" in s for s in protokoll), protokoll)

    def test_original_unveraendert(self):
        self.assertNotEqual(self.bytes, self.vorlage)
        self.assertIsNone(openpyxl.load_workbook(io.BytesIO(self.vorlage))["Exp_protocol"]["C3"].value)


class AnsatzBefuellenTests(unittest.TestCase):
    def test_naoh_und_end_ph(self):
        values = {"pump1": {"dosed_ml": [[T0, 0.0], [T0 + 100, 18.0], [T0 + 500, 29.4]]},
                  "liquiline": {"ph_value": [[T0, 3.2], [T0 + 500, 9.45]]}}
        lauf = {"experiment_id": "D-Ansatz-3", "starting_time": T0, "finishing_time": T0 + 600,
                "parameters": {"vol_carbonsaeure": {"value": 17.92, "unit": "mL"}, "vol_leitsalz": {"value": 0, "unit": "mL"}}}
        b, protokoll = vb.befuellen(vorlage_bauen().getvalue(), "ansatz", ausgabe_block("ansatz"), lauf, values)
        wb = openpyxl.load_workbook(io.BytesIO(b))
        self.assertAlmostEqual(wb["Exp_preparation"]["B12"].value, 29.4 - 17.92, places=3)   # NaOH = dosiert - feste Zugaben
        self.assertEqual(wb["Exp_preparation"]["B18"].value, 9.45)
        self.assertTrue(any("B12" in s and "minus" in s for s in protokoll), protokoll)

    def test_fehlende_messwerte_bleiben_leer(self):
        lauf = {"experiment_id": "x", "starting_time": T0, "finishing_time": T0 + 10, "parameters": {}}
        b, protokoll = vb.befuellen(vorlage_bauen().getvalue(), "ph_regelung", ausgabe_block("ph_regelung"), lauf, {})
        wb = openpyxl.load_workbook(io.BytesIO(b))
        self.assertIsNone(wb["Exp_preparation"]["B18"].value)
        self.assertTrue(any("bleibt leer" in s for s in protokoll), protokoll)


class FehlerTests(unittest.TestCase):
    def test_ohne_startzeit(self):
        with self.assertRaises(vb.BefuellenFehler):
            vb.befuellen(vorlage_bauen().getvalue(), "ansatz", ausgabe_block("ansatz"), {"experiment_id": "x"}, {})

    def test_ohne_ausgabe_block(self):
        with self.assertRaises(vb.BefuellenFehler):
            vb.befuellen(vorlage_bauen().getvalue(), "zugabe", None, {"starting_time": T0}, {})

    def test_iso_startzeit(self):
        lauf = {"experiment_id": "x", "starting_time": "2026-11-18 10:00:00", "finishing_time": "2026-11-18 10:10:00", "parameters": {}}
        b, _ = vb.befuellen(vorlage_bauen().getvalue(), "ph_regelung", ausgabe_block("ph_regelung"), lauf,
                            {"liquiline": {"ph_value": [[1_800_000_000.0, 9.0]]}})
        self.assertEqual(openpyxl.load_workbook(io.BytesIO(b))["Exp_preparation"]["B18"].value, 9.0)


if __name__ == "__main__":
    unittest.main()
