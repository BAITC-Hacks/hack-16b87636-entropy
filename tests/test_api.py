import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from windpilot.api import create_app
from windpilot.common import ForecastError


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(out_dir=self.temp.name))

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_invalid_parameters(self):
        for query in ["date=not-a-date", "date=2026-02-15&horizon=12", "horizon=48"]:
            with self.subTest(query=query):
                self.assertEqual(self.client.get("/forecast?" + query).status_code, 422)

    def test_actual_forecast_and_reuse(self):
        response = self.client.get("/forecast?date=2026-02-15&horizon=48&offline=true")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["row_count"], 96)
        self.assertEqual(data["forecast_origin"], "2026-02-14T23:00:00+05:00")
        self.assertTrue(all(0 <= row["power_pred"] <= 1 for row in data["forecast"]))
        again = self.client.get("/forecast?date=2026-02-15&horizon=48&offline=true").json()
        self.assertEqual(again["run_id"], data["run_id"])
        self.assertEqual(again["status"], "reused")

    def test_missing_weather_is_clear_503(self):
        with patch("windpilot.api.run_forecast", side_effect=ForecastError("Weather cache missing for this run")):
            response = self.client.get("/forecast?date=2026-02-15&offline=true")
        self.assertEqual(response.status_code, 503)
        self.assertIn("cache missing", response.json()["detail"])

    def test_january_selects_pre_january_model(self):
        response = self.client.get("/forecast?date=2026-01-15&horizon=24&offline=true")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["row_count"], 48)
        self.assertEqual(response.json()["analysis"]["model_trained_until"], "2025-12-31T18:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
