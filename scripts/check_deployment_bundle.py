"""Smoke-test the packaged runtime in an isolated checkout, without original CSVs."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    base = ROOT / 'artifacts/deployment_checks'
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as temporary:
        clean = Path(temporary)
        for name in ('windpilot', 'deployment'):
            shutil.copytree(ROOT / name, clean / name, ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('dashboard.html', 'config.json'):
            shutil.copyfile(ROOT / name, clean / name)
        (clean / 'scripts').mkdir()
        shutil.copyfile(ROOT / 'scripts/restore_runtime.py', clean / 'scripts/restore_runtime.py')
        subprocess.run([sys.executable, 'scripts/restore_runtime.py'], cwd=clean, check=True)
        subprocess.run([sys.executable, 'scripts/restore_runtime.py'], cwd=clean, check=True)
        code = '''
import hashlib, json
from pathlib import Path
import pandas as pd
from fastapi.testclient import TestClient
import windpilot
from windpilot.api import create_app
assert Path(windpilot.__file__).resolve().is_relative_to(Path.cwd())
html = Path('dashboard.html').read_text(encoding='utf-8')
saved = json.loads(html.split('/*DATA_START*/')[1].split('/*DATA_END*/')[0])
original_hash = hashlib.sha256(Path('artifacts/model.joblib').read_bytes()).hexdigest()
with TestClient(create_app()) as client:
    assert client.get('/health').json()['model_available']
    assert 'id="themeToggle"' in client.get('/').text
    assert set(client.get('/quality').json()['metrics_by_turbine']) == {'1', '2'}
    count = 0
    for day in pd.date_range('2026-01-31', '2026-02-28'):
        for horizon in (24, 48):
            date = (day + pd.Timedelta(days=1)).date().isoformat()
            response = client.get(f'/forecast?date={date}&horizon={horizon}&offline=true')
            assert response.status_code == 200, response.text
            run = response.json()
            assert len(run['forecast']) == 2 * horizon and run['trace']
            assert len(run['baseline']) == 2
            expected = saved['runs'][f'{day.date()}_{horizon}']['forecast']
            key = lambda r: (r['turbine_id'], r['valid_at'])
            assert all(abs(a['power_pred'] - b['power_pred']) < 1e-9 for a,b in zip(sorted(run['forecast'], key=key), sorted(expected, key=key)))
            assert all(0 <= r['power_pred'] <= 1 for r in run['forecast'])
            count += 1
    assert client.get('/forecast?date=2026-02-01&horizon=24&offline=true').json()['status'] == 'reused'
    assert client.get('/forecast?date=2026-01-15&horizon=48&offline=true').status_code == 200
assert original_hash == hashlib.sha256(Path('artifacts/model.joblib').read_bytes()).hexdigest()
print(f'PASS: {count} archived forecasts reproduced in isolated checkout, both horizons, boundaries, trace, baseline, January split and identical weights.')
'''
        subprocess.run([sys.executable, '-c', code], cwd=clean, check=True)
    (base / 'result.json').write_text(json.dumps({
        'status': 'passed', 'archived_runs_reproduced': 58, 'original_csvs_used': False,
        'public_deployment_verified': False, 'visual_browser_verified': False,
    }, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
