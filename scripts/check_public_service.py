"""Exercise a deployed API without mistaking local/file fallback for deployment."""
import argparse
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests


def check(base):
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.hostname in ('localhost', '127.0.0.1'):
        raise ValueError('Provide the actual public HTTPS service URL.')
    base = base.rstrip('/')

    def get(path):
        response = requests.get(base + path, timeout=(15, 120))
        response.raise_for_status()
        return response

    assert get('/health').json()['model_available']
    assert 'id="themeToggle"' in get('/').text
    assert set(get('/quality').json()['metrics_by_turbine']) == {'1', '2'}
    for date, horizon in [('2026-02-01', 24), ('2026-03-01', 48)]:
        run = get(f'/forecast?date={date}&horizon={horizon}&offline=true').json()
        assert len(run['forecast']) == horizon * 2
        assert run['trace'] and all(0 <= row['power_pred'] <= 1 for row in run['forecast'])
    live = get('/forecast/live?horizon=48').json()
    assert live['mode'] == 'live_demo' and len(live['forecast']) == 96
    assert not live['baseline']
    assert abs((datetime.now(timezone.utc) - datetime.fromisoformat(live['checked_at'])).total_seconds()) < 180
    # Unchanged current inputs may reuse a run made earlier in this forecast hour.
    assert abs((datetime.now(timezone.utc) - datetime.fromisoformat(live['forecast_origin'])).total_seconds()) < 3700
    assert get('/agent/status').json()['enabled']
    assert all(0 <= row['power_pred'] <= 1 and row['weather_issued_at'] is None for row in live['forecast'])
    print('PASS: public HTTP, models, history boundaries, trace, metrics and real live weather. Browser QA still required.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('base_url')
    check(parser.parse_args().base_url)
