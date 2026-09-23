import copy
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests
from fastapi.testclient import TestClient

from windpilot.api import create_app
from windpilot.common import ForecastError, read_config
from windpilot.weather_overview import CURRENT_UNITS, DAILY_UNITS, normalize_overview


def weather_payload():
    now = pd.Timestamp.now(tz='Asia/Almaty')
    return {'timezone': 'Asia/Almaty', 'current_units': CURRENT_UNITS.copy(),
            'daily_units': DAILY_UNITS.copy(),
            'current': {'time': now.strftime('%Y-%m-%dT%H:%M'), 'temperature_2m': 20.,
                        'relative_humidity_2m': 40, 'weather_code': 0, 'wind_speed_10m': 4.,
                        'wind_direction_10m': 270, 'wind_gusts_10m': 7., 'wind_speed_100m': 6., 'is_day': 1},
            'daily': {'time': pd.date_range(now.date(), periods=7).strftime('%Y-%m-%d').tolist(),
                      'weather_code': [0]*7, 'temperature_2m_max': [23.]*7, 'temperature_2m_min': [12.]*7,
                      'precipitation_sum': [0.]*7, 'wind_speed_10m_max': [5.]*7,
                      'wind_gusts_10m_max': [8.]*7, 'wind_direction_10m_dominant': [270.]*7}}


class WeatherOverviewTests(unittest.TestCase):
    def test_cache_per_site_refresh_and_no_stale_success_after_error(self):
        response = Mock()
        response.json.side_effect = weather_payload
        with patch('windpilot.weather_overview.requests.get', return_value=response) as fetch, TestClient(create_app()) as client:
            first = client.get('/weather/overview?turbine_id=1').json()
            self.assertEqual(first['turbine_id'], 1)
            self.assertEqual(len(first['daily']), 7)
            self.assertTrue(first['current']['time'].endswith('+05:00'))
            self.assertFalse(first['cached'])
            self.assertTrue(client.get('/weather/overview?turbine_id=1').json()['cached'])
            self.assertEqual(fetch.call_count, 1)
            second = client.get('/weather/overview?turbine_id=2').json()
            self.assertEqual(second['turbine_id'], 2)
            self.assertNotEqual(first['latitude'], second['latitude'])
            client.get('/weather/overview?turbine_id=1&refresh=true').raise_for_status()
            self.assertEqual(fetch.call_count, 3)
            self.assertEqual(fetch.call_args.kwargs['params']['models'], 'ecmwf_ifs')
            fetch.side_effect = requests.ConnectionError('WinError 10013')
            failed = client.get('/weather/overview?turbine_id=1&refresh=true')
            self.assertEqual(failed.status_code, 503)
            self.assertNotIn('current', failed.json())
            self.assertEqual(client.get('/weather/overview?turbine_id=3').status_code, 422)

    def test_missing_optional_measurements_remain_null(self):
        payload = weather_payload()
        payload['current']['wind_gusts_10m'] = None
        payload['daily']['precipitation_sum'][0] = None
        cfg = read_config()
        data = normalize_overview(payload, cfg, cfg['turbines'][0])
        self.assertIsNone(data['current']['wind_gusts_10m'])
        self.assertIsNone(data['daily'][0]['precipitation_sum'])

    def test_invalid_or_old_weather_is_rejected(self):
        cfg = read_config()
        base = weather_payload()
        invalid = []
        p = copy.deepcopy(base); p['current_units']['wind_speed_10m'] = 'km/h'; invalid.append(p)
        p = copy.deepcopy(base); p['current']['time'] = '2026-02-01T00:00'; invalid.append(p)
        p = copy.deepcopy(base); p['daily']['time'][1] = p['daily']['time'][0]; invalid.append(p)
        p = copy.deepcopy(base); p['daily']['precipitation_sum'][0] = -1; invalid.append(p)
        p = copy.deepcopy(base); p['current']['wind_speed_10m'] = None; invalid.append(p)
        p = copy.deepcopy(base); p['daily']['temperature_2m_min'][0] = 30; invalid.append(p)
        p = copy.deepcopy(base); p['daily']['temperature_2m_max'][0] = float('nan'); invalid.append(p)
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ForecastError):
                normalize_overview(payload, cfg, cfg['turbines'][0])
