import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from windpilot.api import create_app
from windpilot.common import read_config, target_hours
from windpilot.live import run_live_forecast
from windpilot.scheduler import ForecastScheduler


class LiveAgentTests(unittest.TestCase):
    def test_weather_change_recalculates_and_unchanged_input_reuses(self):
        changed = [False]
        def weather(config, origin, horizon):
            return pd.concat([pd.DataFrame({
                'turbine_id': tid, 'forecast_origin': origin,
                'valid_at': target_hours(origin, horizon), 'wind_speed': 6.5 if changed[0] else 6.,
                'temperature': 10., 'weather_source': 'TEST_ONLY', 'weather_issued_at': None,
                'weather_received_at': pd.Timestamp.now(tz='UTC').isoformat(),
            }) for tid in [1, 2]], ignore_index=True), []
        def predictor(bundle, frame, observer=None):
            observer('features_prepared', turbine_id=1)
            observer('turbine_model_started', turbine_id=1)
            return np.array(frame.wind_speed) / 10
        with tempfile.TemporaryDirectory() as directory, patch('windpilot.live.fetch_live_weather', side_effect=weather), patch('windpilot.live.predict', side_effect=predictor) as predict:
            first = run_live_forecast(read_config(), out_dir=directory)
            second = run_live_forecast(read_config(), out_dir=directory)
            self.assertEqual(predict.call_count, 1)
            self.assertEqual(second['status'], 'reused')
            self.assertEqual(first['run_id'], second['run_id'])
            self.assertEqual(first['forecast_origin'], second['forecast_origin'])
            self.assertEqual(first['forecast'], second['forecast'])
            changed[0] = True
            third = run_live_forecast(read_config(), out_dir=directory)
            self.assertEqual(predict.call_count, 2)
            self.assertNotEqual(first['run_id'], third['run_id'])
            self.assertEqual(third['analysis']['previous_run_id'], first['run_id'])
            self.assertAlmostEqual(third['analysis']['mean_absolute_change'], .05)
            names = [e['event'] for e in third['trace']]
            self.assertLess(names.index('forecast_formed'), names.index('analysis_completed'))

    def test_scheduler_reports_failures_and_recovers(self):
        outcomes = iter([RuntimeError('Weather unavailable'), {'run_id': 'test-id', 'status': 'reused', 'forecast_origin': '2026-02-15T18:00:00Z'}])
        def callback():
            result = next(outcomes)
            if isinstance(result, Exception):
                raise result
            return result
        scheduler = ForecastScheduler(callback, 900)
        scheduler.tick()
        self.assertEqual(scheduler.snapshot()['status'], 'error')
        self.assertIn('unavailable', scheduler.snapshot()['error'])
        scheduler.tick()
        self.assertEqual(scheduler.snapshot()['status'], 'waiting')
        self.assertIsNone(scheduler.snapshot()['error'])
        self.assertEqual(scheduler.snapshot()['last_result'], 'reused')
        self.assertIsNotNone(scheduler.snapshot()['next_check_at'])

    def test_disabled_scheduler_status_is_honest(self):
        with TestClient(create_app(auto_refresh_seconds=0)) as client:
            data = client.get('/agent/status').json()
            self.assertFalse(data['enabled'])
            self.assertEqual(data['status'], 'disabled')
            self.assertIsNone(data['last_run_id'])


if __name__ == '__main__':
    unittest.main()
