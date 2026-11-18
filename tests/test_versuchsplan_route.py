"""Route /upload_versuchsplan/<stage_id>: Versuchsplan hochladen -> Formular "Add run" vorbelegt, kein Lauf angelegt.
Lauf: .venv/Scripts/python -m unittest tests.test_versuchsplan_route -v   (im Ordner LABS-User-Interface)"""
import os
import sys
import unittest

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app import create_app, db  # noqa: E402
from app.auth.models import User  # noqa: E402
from app.experiments.models import (  # noqa: E402
    ExperimentalDesign, ExperimentalRoutines, ExperimentalRuns, ExperimentalStation, Parameters, Stage,
)
from tests.test_versuchsplan import ROUTINEN, vorlage_bauen  # noqa: E402

app = create_app(environment="testing")


class UploadVersuchsplanTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.app_ctx = app.app_context()
        self.app_ctx.push()
        db.create_all()
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


if __name__ == "__main__":
    unittest.main()
