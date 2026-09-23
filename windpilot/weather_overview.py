"""Current model conditions and daily weather for the configured turbine sites."""
import math
import time
from threading import Lock

import pandas as pd
import requests

from .common import ForecastError, digest
from .live import LIVE_ENDPOINT

CURRENT_UNITS = {
    'temperature_2m': '°C', 'relative_humidity_2m': '%', 'weather_code': 'wmo code',
    'wind_speed_10m': 'm/s', 'wind_direction_10m': '°', 'wind_gusts_10m': 'm/s',
    'wind_speed_100m': 'm/s', 'is_day': '',
}
DAILY_UNITS = {
    'weather_code': 'wmo code', 'temperature_2m_max': '°C', 'temperature_2m_min': '°C',
    'precipitation_sum': 'mm', 'wind_speed_10m_max': 'm/s',
    'wind_gusts_10m_max': 'm/s', 'wind_direction_10m_dominant': '°',
}


def normalize_overview(payload, config, turbine):
    """Keep unavailable optional values null; never turn missing weather into zero."""
    timezone = config['history_timezone']
    now = pd.Timestamp.now(tz='UTC')

    def value(number, field):
        if number is None:
            return None
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            raise ValueError(f'Invalid {field}')
        if (field.startswith(('wind_speed', 'wind_gusts', 'precipitation')) and number < 0
                or 'direction' in field and not 0 <= number <= 360
                or field == 'relative_humidity_2m' and not 0 <= number <= 100):
            raise ValueError(f'Invalid {field} range')
        return number

    try:
        if payload.get('timezone') != timezone:
            raise ValueError('Unexpected timezone')
        for group, units in [('current', CURRENT_UNITS), ('daily', DAILY_UNITS)]:
            if any(payload[group + '_units'].get(key) != unit for key, unit in units.items()):
                raise ValueError(f'Unexpected {group} units')
        current = {key: value(payload['current'][key], key) for key in CURRENT_UNITS}
        at = pd.Timestamp(payload['current']['time']).tz_localize(timezone)
        if abs((now - at).total_seconds()) > 3600:
            raise ValueError('Current weather timestamp is not current')
        current['time'] = at.isoformat()
        if current['temperature_2m'] is None or current['wind_speed_10m'] is None:
            raise ValueError('Current temperature or wind is missing')
        dates = payload['daily']['time']
        expected = pd.date_range(now.tz_convert(timezone).date(), periods=7).strftime('%Y-%m-%d').tolist()
        if dates != expected or any(len(payload['daily'][key]) != 7 for key in DAILY_UNITS):
            raise ValueError('Expected seven consecutive local forecast days')
        daily = [{'date': day, **{key: value(payload['daily'][key][i], key) for key in DAILY_UNITS}}
                 for i, day in enumerate(dates)]
        for row in daily:
            if row['temperature_2m_min'] is not None and row['temperature_2m_max'] is not None:
                if row['temperature_2m_min'] > row['temperature_2m_max']:
                    raise ValueError('Daily minimum exceeds maximum')
    except (KeyError, TypeError, ValueError) as exc:
        raise ForecastError(f'Неполные или некорректные погодные данные: {exc}') from exc
    return {'source': 'Open-Meteo', 'model': config['weather']['model'], 'timezone': timezone,
            'turbine_id': turbine['id'], 'latitude': turbine['latitude'], 'longitude': turbine['longitude'],
            'received_at': now.isoformat(), 'current': current, 'daily': daily,
            'data_kind': 'weather_model', 'refresh_seconds': 900}


class WeatherOverview:
    def __init__(self):
        self.cache = {}
        self.lock = Lock()

    def get(self, config, turbine_id, refresh=False):
        turbine = next((t for t in config['turbines'] if t['id'] == turbine_id), None)
        if turbine is None:
            raise ForecastError('Неизвестная установка.')
        params = {'latitude': turbine['latitude'], 'longitude': turbine['longitude'],
                  'models': config['weather']['model'], 'timezone': config['history_timezone'],
                  'current': ','.join(CURRENT_UNITS), 'daily': ','.join(DAILY_UNITS),
                  'forecast_days': 7, 'wind_speed_unit': 'ms'}
        key = digest({'turbine': turbine_id, 'params': params})
        with self.lock:
            saved = self.cache.get(key)
            today = str(pd.Timestamp.now(tz=config['history_timezone']).date())
            if saved and not refresh and time.monotonic() - saved[0] < 900 and saved[1]['daily'][0]['date'] == today:
                return {**saved[1], 'cached': True}
            try:
                response = requests.get(LIVE_ENDPOINT, params=params, timeout=(5, 20))
                response.raise_for_status()
                result = normalize_overview(response.json(), config, turbine)
            except requests.RequestException as exc:
                raise ForecastError('Не удалось подключиться к Open-Meteo. Проверьте доступ сервера к интернету и повторите обновление.') from exc
            except ValueError as exc:
                if isinstance(exc, ForecastError):
                    raise
                raise ForecastError('Open-Meteo вернул некорректный ответ.') from exc
            self.cache[key] = (time.monotonic(), result)
            return {**result, 'cached': False}
