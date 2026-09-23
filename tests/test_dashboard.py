import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from windpilot.api import create_app
from windpilot.common import read_config, target_hours
from windpilot.dashboard_data import baseline_at
from windpilot.live import run_live_forecast


class DashboardTests(unittest.TestCase):
    def test_baseline_never_uses_unfinished_or_future_hour(self):
        origin = pd.Timestamp('2026-01-31T23:00:00+05:00')
        rows = baseline_at(origin)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertLessEqual(pd.Timestamp(row['available_at']), origin)
            self.assertEqual(pd.Timestamp(row['observed_at']).tz_convert('Asia/Almaty').hour, 22)
            self.assertTrue(0 <= row['value'] <= 1)

    def test_dashboard_and_quality_endpoints(self):
        with TestClient(create_app()) as client:
            response = client.get('/')
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/html', response.headers['content-type'])
            self.assertIn('id="daySlider"', response.text)
            q = client.get('/quality').json()
            self.assertEqual(set(q['metrics_by_turbine']), {'1', '2'})

    def test_agent_reports_actual_trace_and_baseline(self):
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(out_dir=directory)) as client:
            data = client.get('/forecast?date=2026-02-01&horizon=24&offline=true').json()
            self.assertEqual(data['row_count'], 48)
            self.assertGreater(data['request_duration_ms'], 0)
            events = [x['event'] for x in data['trace']]
            for name in ['weather_validated', 'features_prepared', 'turbine_model_started', 'analysis_completed']:
                self.assertIn(name, events)
            self.assertEqual(len(data['baseline']), 2)

    def test_live_mode_uses_current_time_and_no_invented_issuance(self):
        def weather(config, origin, horizon):
            self.assertLess(abs((pd.Timestamp.now(tz='UTC') - origin).total_seconds()), 30)
            parts = []
            for tid in [1, 2]:
                parts.append(pd.DataFrame({'turbine_id': tid, 'forecast_origin': origin,
                    'valid_at': target_hours(origin, horizon), 'wind_speed': 6., 'temperature': 10.,
                    'weather_source': 'open-meteo/live/ecmwf_ifs', 'weather_issued_at': None,
                    'weather_received_at': pd.Timestamp.now(tz='UTC').isoformat()}))
            return pd.concat(parts, ignore_index=True), []
        with tempfile.TemporaryDirectory() as directory, patch('windpilot.live.fetch_live_weather', side_effect=weather):
            result = run_live_forecast(read_config(), out_dir=directory)
        self.assertEqual(result['mode'], 'live_demo')
        self.assertEqual(result['row_count'], 96)
        self.assertEqual(result['baseline'], [])
        self.assertTrue(all(row['weather_issued_at'] is None for row in result['forecast']))
        self.assertTrue(all(0 <= row['power_pred'] <= 1 for row in result['forecast']))


if __name__ == '__main__':
    unittest.main()
