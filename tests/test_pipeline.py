"""Synthetic inputs exist only inside isolated tests; never in forecast outputs."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyRegressor

from windpilot.agent import run_forecast
from windpilot.common import ForecastError, read_config, target_hours, utc
from windpilot.data import prepare_one
from windpilot.model import weather_signature
from windpilot.weather import WeatherClient, validate_weather


def fake_weather(config, origin, horizon=48):
    origin = utc(origin)
    frames = []
    for tid in [1, 2]:
        frames.append(pd.DataFrame({"turbine_id": tid, "forecast_origin": origin,
            "valid_at": target_hours(origin, horizon), "wind_speed": np.linspace(3, 9, horizon),
            "temperature": 4.0, "weather_issued_at": origin - pd.Timedelta(hours=18),
            "weather_available_at": origin - pd.Timedelta(hours=6), "weather_source": "TEST_ONLY",
            "wind_variable": "wind_speed_100m", "latitude": 43.6, "longitude": 78.5}))
    return pd.concat(frames, ignore_index=True)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = read_config()
        self.cfg["weather"]["cache_dir"] = str(self.root / "cache")
        self.origin = utc("2026-01-31T23:00:00+05:00")

    def tearDown(self):
        self.temp.cleanup()

    def model(self):
        curve = DummyRegressor(strategy="constant", constant=0.4).fit(pd.DataFrame({"wind_speed": [1, 2], "temperature": [3, 4]}), [0.4, 0.4])
        residual = DummyRegressor(strategy="constant", constant=0).fit(np.ones((2, 6)), [0, 0])
        bundle = {"trained_until": self.origin.isoformat(), "weather_signature": weather_signature(self.cfg),
                  "feature_version": 1, "models": {i: {"curve": curve, "residual": residual} for i in [1, 2]}}
        path = self.root / "model.joblib"
        joblib.dump(bundle, path)
        return path

    def test_requires_explicit_timezone(self):
        with self.assertRaisesRegex(ForecastError, "timezone"):
            utc("2026-01-31 23:00")

    def test_complete_hours_only_and_no_zero_fill(self):
        frame = pd.DataFrame({"Статистическое время": pd.date_range("2025-12-01", periods=12, freq="10min").astype(str),
            "Средняя скорость ветра(m/s)": 5.0, "Нормализованная активная мощность": 0.4,
            "Средняя температура окружающей среды(°C)": 3.0})
        frame.loc[7, "Средняя скорость ветра(m/s)"] = np.nan
        path = self.root / "source.csv"
        frame.to_csv(path, index=False)
        hourly, report = prepare_one(path, 1, "Asia/Almaty")
        self.assertEqual(len(hourly), 1)
        self.assertEqual(report["invalid_numeric_rows"], 1)
        self.assertEqual(report["excluded_incomplete_hours"], 1)
        self.assertAlmostEqual(hourly.iloc[0].power_norm, 0.4)
        self.assertEqual(hourly.iloc[0].timestamp, utc("2025-11-30T19:00:00Z"))

    def test_weather_rejects_future_publication(self):
        weather = fake_weather(self.cfg, self.origin)
        weather.loc[0, "weather_available_at"] = self.origin + pd.Timedelta(hours=1)
        with self.assertRaisesRegex(ForecastError, "future leakage"):
            validate_weather(weather, self.origin, 48, [1, 2])

    def test_weather_rejects_missing_values_and_hours(self):
        weather = fake_weather(self.cfg, self.origin)
        with self.assertRaisesRegex(ForecastError, "48 unique"):
            validate_weather(weather.iloc[:-1], self.origin, 48, [1, 2])
        weather.loc[0, "temperature"] = np.nan
        with self.assertRaisesRegex(ForecastError, "non-finite"):
            validate_weather(weather, self.origin, 48, [1, 2])

    def test_offline_missing_cache_does_not_invent_weather(self):
        client = WeatherClient(self.cfg, offline=True)
        with self.assertRaisesRegex(ForecastError, "cache missing"):
            client.for_origin(self.origin, 48)

    def test_model_cannot_predict_before_training_cutoff(self):
        with self.assertRaisesRegex(ForecastError, "trained after"):
            run_forecast(self.cfg, self.origin - pd.Timedelta(hours=1), model_path=self.model(), out_dir=self.root / "runs", offline=True)

    def test_24_48_hours_reuse_and_changed_input_versioning(self):
        model = self.model()
        out = self.root / "runs"
        for horizon in [24, 48]:
            weather = fake_weather(self.cfg, self.origin, horizon)
            with patch("windpilot.agent.WeatherClient.for_origin", return_value=weather):
                first = run_forecast(self.cfg, self.origin, horizon, model, out, offline=True)
                second = run_forecast(self.cfg, self.origin, horizon, model, out, offline=True)
            self.assertEqual(first["run_id"], second["run_id"])
            self.assertEqual(second["status"], "reused")
            frame = pd.read_csv(Path(first["path"]) / "forecast.csv")
            self.assertEqual(frame.groupby("turbine_id").size().tolist(), [horizon, horizon])
            self.assertTrue(frame.power_pred.between(0, 1).all())
            weather.loc[0, "wind_speed"] += 1
            with patch("windpilot.agent.WeatherClient.for_origin", return_value=weather):
                changed = run_forecast(self.cfg, self.origin, horizon, model, out, offline=True)
            self.assertNotEqual(changed["run_id"], first["run_id"])
            self.assertTrue((Path(first["path"]) / "forecast.csv").exists())
            self.assertEqual(changed["analysis"]["previous_run_id"], first["run_id"])

    def test_source_config_change_requires_retraining(self):
        model = self.model()
        self.cfg["weather"]["wind_variable"] = "wind_speed_10m"
        with self.assertRaisesRegex(ForecastError, "Retrain"):
            run_forecast(self.cfg, self.origin, model_path=model, out_dir=self.root / "runs")

    def test_run_selection_accounts_for_publication_delay(self):
        client = WeatherClient(self.cfg)
        issue = client.issue_for(self.origin)
        self.assertLessEqual(issue + pd.Timedelta(hours=12), self.origin)
        self.assertIn(issue.hour, [0, 12])


if __name__ == "__main__":
    unittest.main()
