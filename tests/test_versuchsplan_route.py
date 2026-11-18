"""Route /upload_versuchsplan/<stage_id>: Versuchsplan hochladen -> Formular "Add run" vorbelegt, kein Lauf angelegt.
Lauf: .venv/Scripts/python -m unittest tests.test_versuchsplan_route -v   (im Ordner LABS-User-Interface)"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import openpyxl

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app import create_app, db  # noqa: E402
from app.auth.models import User  # noqa: E402
from app.experiments.models import (  # noqa: E402
    ExperimentalDesign, ExperimentalRoutines, ExperimentalRuns, ExperimentalStation, Parameters, Stage,
)
from tests.test_versuchsplan import ROUTINEN, vorlage_bauen  # noqa: E402
from tests.test_versuchsplan_befuellen import kolbe_lauf  # noqa: E402

app = create_app(environment="testing")


class UploadVersuchsplanTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.app_ctx = app.app_context()
        self.app_ctx.push()
        db.create_all()
        # Ablage der Versuchsplaene (instance/versuchsplaene) je Test in einen eigenen Ordner, sonst bleiben
        # Dateien aus frueheren Tests liegen (Stage-IDs beginnen nach drop_all wieder bei 1)
        self._instance_vorher = app.instance_path
        self._instance_tmp = tempfile.mkdtemp()
        app.instance_path = self._instance_tmp
        self.user = User(username="chem", email="chem@example.com", password="password", shortID="CHE")
        self.user.save()
        antwort = self.client.post("/login", data=dict(user_id="chem", password="password"), follow_redirects=True)
        assert b"Login successful" in antwort.data, antwort.data[:300]
        self.station = ExperimentalStation(name="Messstand II", address="127.0.0.1:11123", api_key="x", location="Labor")
        self.station.save()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_ctx.pop()
        app.instance_path = self._instance_vorher
        shutil.rmtree(self._instance_tmp, ignore_errors=True)

    def stage_mit_routine(self, name):
        with app.test_request_context():        # add_experimental_design/add_stage rufen flash()
            return self._stage_mit_routine(name)

    def _stage_mit_routine(self, name):
        routine = ExperimentalRoutines.add_routine(name)
        self.station.register_routine_in_station(routine)
        for pname, unit in ROUTINEN[name].items():
            param = Parameters(name=pname, unit=unit, note=None, data_type="float" if pname not in ("versuch", "gc_minutes") else "str",
                               static_param=False, default_value=0.0)
            param.save()
            routine.add_parameter(param)
        # add_experimental_design/add_stage geben nichts zurueck -> danach aus der DB holen
        ExperimentalDesign.add_experimental_design(name="Design", user_id=self.user.id, station_id=self.station.id)
        design = ExperimentalDesign.query.order_by(ExperimentalDesign.id.desc()).first()
        Stage.add_stage(name="Stage", user_id=self.user.id, experimental_Routine_id=routine.id,
                        experimental_design_id=design.id)
        return Stage.query.order_by(Stage.id.desc()).first()

    def test_ansatz_wird_vorbelegt_ohne_lauf(self):
        stage = self.stage_mit_routine("ansatz")
        antwort = self.client.post(f"/upload_versuchsplan/{stage.id}",
                                   data={"plan_file": (vorlage_bauen(), "Experiment_26174.xlsx")},
                                   content_type="multipart/form-data")
        self.assertEqual(antwort.status_code, 200)
        html = antwort.data.decode("utf-8")
        self.assertIn('value="17.92"', html)                  # vol_carbonsaeure = Summe der drei Saeuren
        self.assertIn('value="9.5"', html)                    # ziel_ph
        self.assertIn("Von Hand eintragen", html)
        self.assertIn("vol_leitsalz", html)
        self.assertIn("Aus dem Versuchsplan gelesen", html)
        self.assertEqual(ExperimentalRuns.query.count(), 0)   # kein Lauf angelegt

    def test_kolbe_mit_warnung(self):
        stage = self.stage_mit_routine("kolbe_elektrolyse_gc")
        datei = vorlage_bauen(**{"Setup_parameter!B9": "Two-Chamber"})
        antwort = self.client.post(f"/upload_versuchsplan/{stage.id}", data={"plan_file": (datei, "x.xlsx")},
                                   content_type="multipart/form-data")
        html = antwort.data.decode("utf-8")
        self.assertIn('value="0.55"', html)                   # 550 mA -> 0.55 A
        self.assertIn("Two-Chamber", html)                    # Warnung nur_wenn
        self.assertIn("max_voltage", html)

    def test_falsche_datei_wird_abgelehnt(self):
        stage = self.stage_mit_routine("ansatz")
        datei = vorlage_bauen(**{"Setup_parameter!A27": "Reactor volume"})
        antwort = self.client.post(f"/upload_versuchsplan/{stage.id}", data={"plan_file": (datei, "x.xlsx")},
                                   content_type="multipart/form-data", follow_redirects=True)
        html = antwort.data.decode("utf-8")
        self.assertIn("Versuchsplan nicht uebernommen", html)
        self.assertIn("Pool volume", html)
        self.assertEqual(ExperimentalRuns.query.count(), 0)

    def test_ohne_datei(self):
        stage = self.stage_mit_routine("ansatz")
        antwort = self.client.post(f"/upload_versuchsplan/{stage.id}", data={}, content_type="multipart/form-data",
                                   follow_redirects=True)
        self.assertIn("Versuchsplan-Datei", antwort.data.decode("utf-8"))


    # -- Stufe 3: Ergebnis-Excel -------------------------------------------------------------------------

    def upload(self, stage, datei=None):
        return self.client.post(f"/upload_versuchsplan/{stage.id}",
                                data={"plan_file": (datei or vorlage_bauen(), "Experiment_26174.xlsx")},
                                content_type="multipart/form-data")

    def test_download_ohne_abgelegte_datei(self):
        stage = self.stage_mit_routine("kolbe_elektrolyse_gc")
        antwort = self.client.get(f"/download_ergebnis/{stage.id}", follow_redirects=True)
        self.assertIn("kein Versuchsplan abgelegt", antwort.data.decode("utf-8"))

    def test_download_ohne_gesendeten_lauf(self):
        stage = self.stage_mit_routine("kolbe_elektrolyse_gc")
        self.upload(stage)
        antwort = self.client.get(f"/download_ergebnis/{stage.id}", follow_redirects=True)
        self.assertIn("Noch kein Lauf", antwort.data.decode("utf-8"))

    def test_download_befuellt_mit_messwerten(self):
        stage = self.stage_mit_routine("kolbe_elektrolyse_gc")
        html = self.upload(stage).data.decode("utf-8")
        self.assertIn("Ergebnis-Excel herunterladen", html)
        self.assertIn("Experiment_26174.xlsx", html)
        run = ExperimentalRuns.add_new_run(stage_id=stage.id, status=1)      # "an die Station geschickt"
        lauf, values = kolbe_lauf(dauer_min=60)
        lauf["experiment_id"] = f"Design-Stage-{run.id}"
        antwort_station = mock.Mock(status_code=200, text=json.dumps({"experiment_id": lauf["experiment_id"], "lauf": lauf, "values": values}))
        antwort_station.raise_for_status = mock.Mock()
        with mock.patch("app.experiments.views.requests.get", return_value=antwort_station) as get:
            antwort = self.client.get(f"/download_ergebnis/{stage.id}")
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("Experiment_26174_LABS.xlsx", antwort.headers.get("Content-Disposition", ""))
        self.assertIn("127.0.0.1:11123/api/run_log", get.call_args[0][0])
        self.assertEqual(json.loads(get.call_args[1]["params"]["experiment_id"]), f"Design-Stage-{run.id}")
        wb = openpyxl.load_workbook(io.BytesIO(antwort.data))
        ws = wb["Exp_protocol"]
        self.assertAlmostEqual(ws["C3"].value, 9.5, places=2)
        self.assertAlmostEqual(ws["C9"].value, 8.5, places=2)        # 60 min = Laufende
        self.assertIsNone(ws["C10"].value)                           # 80 min: nach Laufende
        self.assertEqual(wb["Setup_parameter"]["B1"].value, 26174)   # Original-Eingaben bleiben
        self.assertEqual(ExperimentalRuns.query.count(), 1)

    def test_download_station_meldet_fehler(self):
        stage = self.stage_mit_routine("ansatz")
        self.upload(stage)
        ExperimentalRuns.add_new_run(stage_id=stage.id, status=1)
        antwort_station = mock.Mock(status_code=200, text=json.dumps({"error": "kein Lauf 'x' unter logs"}))
        antwort_station.raise_for_status = mock.Mock()
        with mock.patch("app.experiments.views.requests.get", return_value=antwort_station):
            antwort = self.client.get(f"/download_ergebnis/{stage.id}", follow_redirects=True)
        self.assertIn("Kein fertiger Lauf", antwort.data.decode("utf-8"))
        self.assertIn("kein Lauf", antwort.data.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
