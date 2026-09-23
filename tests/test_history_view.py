"""Chronological comparisons must never turn missing facts into predictions."""
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pandas as pd

from windpilot.history_view import history_data, install_history_routes


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data/processed").mkdir(parents=True)
        (self.root / "artifacts").mkdir()
        # A missing second turbine hour is deliberately not an observed zero.
        pd.DataFrame([
            {"timestamp": "2025-12-31T19:00:00+00:00", "turbine_id": 1,
             "power_norm": 0.2, "wind_speed": 4.5, "temperature": -5},
            {"timestamp": "2025-12-31T19:00:00+00:00", "turbine_id": 2,
             "power_norm": 0.0, "wind_speed": 0.0, "temperature": -4},
            {"timestamp": "2025-12-31T20:00:00+00:00", "turbine_id": 1,
             "power_norm": 0.4, "wind_speed": 6.0, "temperature": -4},
        ]).to_csv(self.root / "data/processed/hourly.csv", index=False)
        pd.DataFrame([
            self.prediction("2025-12-31T19:00:00+00:00", "2025-12-31T18:00:00+00:00", 0.3),
            # Overlapping preceding-day lead 25 must not enter the display.
            self.prediction("2025-12-31T19:00:00+00:00", "2025-12-30T18:00:00+00:00", 0.99),
            # An invalid origin after valid_at must not enter the display.
            self.prediction("2025-12-31T20:00:00+00:00", "2025-12-31T21:00:00+00:00", 0.9),
        ]).to_csv(self.root / "artifacts/validation_predictions.csv", index=False)
        app = FastAPI()
        install_history_routes(app, self.root)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    @staticmethod
    def prediction(valid, origin, value):
        return {"valid_at": valid, "forecast_origin": origin, "turbine_id": 1,
            "prediction": value, "power_norm": 0.88, "wind_speed": 99, "temperature": 50,
            "lead_hours": 1}  # Deliberately incorrect for the older run.

    def test_daily_grid_timezone_gaps_and_independent_observations(self):
        response = self.client.get("/history?start=2026-01-01&end=2026-01-01")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["timezone"], "Asia/Almaty")
        self.assertEqual(len(data["rows"]), 48)
        row = data["rows"][0]
        self.assertEqual(row["valid_at"], "2026-01-01T00:00:00+05:00")
        self.assertEqual(row["forecast_origin"], "2025-12-31T23:00:00+05:00")
        self.assertEqual(row["power_pred"], 0.3)
        self.assertEqual(row["power_actual"], 0.2)
        self.assertEqual(row["wind_speed_actual"], 4.5)
        self.assertEqual(row["wind_speed_forecast"], 99)
        self.assertEqual(row["temperature_forecast"], 50)
        self.assertEqual(row["temperature_actual"], -5)
        self.assertIsNone(row["model_version"])
        self.assertIsNone(row["weather_issued_at"])
        self.assertIsNone(row["weather_available_at"])
        self.assertIsNone(row["weather_selection_reason"])
        self.assertEqual(data["rows"][1]["power_actual"], 0.0)
        self.assertIsNone(data["rows"][3]["power_actual"])
        self.assertIsNone(data["rows"][2]["power_pred"])
        self.assertEqual(data["counts"]["paired"], 1)
        self.assertIn("archive_provenance", [n["code"] for n in data["notices"]])

    def test_future_month_has_neither_fabricated_actuals_nor_predictions(self):
        data = self.client.get("/history?start=2026-08-01&end=2026-08-31").json()
        self.assertEqual(len(data["rows"]), 31 * 24 * 2)
        self.assertTrue(all(r["power_actual"] is None and r["power_pred"] is None for r in data["rows"]))
        self.assertEqual(data["counts"]["paired"], 0)
        self.assertIn("missing_actual", [n["code"] for n in data["notices"]])
        self.assertIn("missing_prediction", [n["code"] for n in data["notices"]])

    def test_inclusive_range_validation(self):
        for query in ["start=2026-01-01&end=2026-02-01", "start=2026-02-01&end=2026-01-31",
                      "start=invalid&end=2026-01-01"]:
            self.assertEqual(self.client.get("/history?" + query).status_code, 422)
        data = self.client.get("/history?start=2026-01-31&end=2026-02-01").json()
        self.assertEqual(len(data["rows"]), 96)
        self.assertEqual(data["rows"][-1]["valid_at"], "2026-02-01T23:00:00+05:00")

    def test_august_is_optional_and_uses_its_own_model_metadata(self):
        first = self.client.get("/history/availability").json()
        self.assertEqual(len(first["forecast_periods"]), 1)
        folder = self.root / "artifacts/august_2025"
        folder.mkdir()
        pd.DataFrame([self.prediction("2025-07-31T19:00:00+00:00", "2025-07-31T18:00:00+00:00", 0.7)]).to_csv(folder / "validation_predictions.csv", index=False)
        (folder / "model_metadata.json").write_text(json.dumps({"model_version": "august-test-version",
            "trained_until": "2025-07-31T18:00:00+00:00"}), encoding="utf-8")
        data = self.client.get("/history?start=2025-08-01&end=2025-08-01").json()
        self.assertEqual(data["rows"][0]["forecast_kind"], "retrospective")
        self.assertEqual(data["rows"][0]["model_version"], "august-test-version")
        self.assertIsNone(data["rows"][0]["power_actual"])
        periods = self.client.get("/history/availability").json()["forecast_periods"]
        self.assertEqual(periods[0]["start"], "2025-08-01")
        self.assertEqual(periods[0]["forecast_kind"], "retrospective")

    def test_rejects_prediction_from_a_model_trained_after_origin(self):
        (self.root / "artifacts/metrics.json").write_text(json.dumps({"model_trained_until": "2026-01-02T00:00:00+00:00"}), encoding="utf-8")
        self.assertEqual(self.client.get("/history?start=2026-01-01&end=2026-01-01").status_code, 503)

    def test_older_weather_keeps_real_issue_and_explicit_reason(self):
        folder = self.root / "artifacts/august_2025"
        folder.mkdir()
        row = self.prediction("2025-08-05T19:00:00+00:00", "2025-08-05T18:00:00+00:00", 0.7)
        row.update(weather_issued_at="2025-08-04T12:00:00+00:00", weather_available_at="2025-08-05T00:00:00+00:00",
            weather_selection_reason="older_cycle_after_nominal_archive_unavailable")
        pd.DataFrame([row]).to_csv(folder / "validation_predictions.csv", index=False)
        data = self.client.get("/history?start=2025-08-06&end=2025-08-06").json()
        first = data["rows"][0]
        self.assertEqual(first["weather_issued_at"], "2025-08-04T17:00:00+05:00")
        self.assertEqual(first["weather_available_at"], "2025-08-05T05:00:00+05:00")
        self.assertEqual(first["weather_selection_reason"], "older_cycle_after_nominal_archive_unavailable")
        self.assertIn("older_weather", [notice["code"] for notice in data["notices"]])

    def test_weather_archive_gap_reason_is_scoped_to_selected_dates(self):
        folder = self.root / "artifacts/august_2025"
        folder.mkdir()
        (folder / "model_metadata.json").write_text(json.dumps({"weather_missing_origins": [
            "2025-08-06T18:00:00+00:00"]}), encoding="utf-8")
        missing = self.client.get("/history?start=2025-08-07&end=2025-08-07").json()
        codes = [notice["code"] for notice in missing["notices"]]
        self.assertIn("weather_archive_gaps", codes)
        self.assertIn("2025-08-07", missing["notices"][codes.index("weather_archive_gaps")]["message"])
        january = self.client.get("/history?start=2026-01-01&end=2026-01-01").json()
        self.assertNotIn("weather_archive_gaps", [notice["code"] for notice in january["notices"]])

    def test_weather_from_after_origin_and_malformed_actuals_fail_explicitly(self):
        path = self.root / "artifacts/validation_predictions.csv"
        predictions = pd.read_csv(path)
        predictions["weather_available_at"] = "2026-01-05T12:00:00+00:00"
        predictions.to_csv(path, index=False)
        self.assertEqual(self.client.get("/history?start=2026-01-01&end=2026-01-01").status_code, 503)
        path.unlink()
        actual_path = self.root / "data/processed/hourly.csv"
        actual = pd.read_csv(actual_path)
        for bad_value in [2.0, float("inf"), "unknown"]:
            with self.subTest(bad_value=bad_value):
                changed = actual.copy()
                changed["power_norm"] = bad_value
                changed.to_csv(actual_path, index=False)
                self.assertEqual(self.client.get("/history/availability").status_code, 503)

    def test_february_24_and_48_hour_saved_runs_are_deduplicated(self):
        for name in ("24", "48"):
            folder = self.root / "artifacts/dashboard_runs" / name
            folder.mkdir(parents=True)
            row = self.prediction("2026-01-31T19:00:00+00:00", "2026-01-31T18:00:00+00:00", 0.6)
            row["model_version"] = "final-version"
            pd.DataFrame([row]).to_csv(folder / "forecast.csv", index=False)
        data = self.client.get("/history?start=2026-02-01&end=2026-02-01").json()
        self.assertEqual(data["counts"]["predicted"], 1)
        self.assertEqual(data["rows"][0]["forecast_kind"], "historical_replay")
        self.assertIsNone(data["rows"][0]["power_actual"])

    def test_no_files_is_an_explicit_empty_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            result = history_data(date(2026, 1, 1), date(2026, 1, 1), folder)
        self.assertEqual(len(result["rows"]), 48)
        self.assertIsNone(result["observed_start"])
        self.assertEqual(result["forecast_periods"], [])

    def test_historical_timezone_offset_and_repeated_hour_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            before = history_data(date(2023, 8, 1), date(2023, 8, 1), folder)
            transition = history_data(date(2024, 2, 29), date(2024, 2, 29), folder)
        self.assertEqual(before["rows"][0]["valid_at"], "2023-08-01T00:00:00+06:00")
        # Kazakhstan moved from UTC+6 to UTC+5 at the end of this date.
        self.assertEqual(len(transition["rows"]), 25 * 2)
        stamps = [r["valid_at"] for r in transition["rows"] if r["turbine_id"] == 1]
        self.assertIn("2024-02-29T23:00:00+06:00", stamps)
        self.assertIn("2024-02-29T23:00:00+05:00", stamps)


if __name__ == "__main__":
    unittest.main()
