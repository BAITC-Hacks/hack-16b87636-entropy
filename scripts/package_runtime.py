"""Package only the explicit, non-secret runtime inputs for the hosted MVP."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    files = [ROOT / name for name in (
        'artifacts/model.joblib', 'artifacts/validation_model.joblib',
        'artifacts/metrics.json', 'artifacts/model_metadata.json',
        'artifacts/validation_predictions.csv', 'data/processed/hourly.csv',
        'artifacts/august_2025/model.joblib', 'artifacts/august_2025/model_metadata.json',
        'artifacts/august_2025/validation_predictions.csv', 'artifacts/august_2025/metrics.json',
        'artifacts/august_2025/weather_selections.json',
    )]
    files += sorted((ROOT / 'data/weather_cache').glob('*.json'))
    for filename in ('forecast.csv', 'analysis.json', 'trace.json', 'weather.csv'):
        files += sorted((ROOT / 'artifacts/dashboard_runs').glob(f'*/{filename}'))
    if not all(p.is_file() for p in files) or not any('weather_cache' in p.parts for p in files):
        raise SystemExit('Missing model, metrics, processed history or archived weather; prepare them first.')
    target = ROOT / 'deployment/runtime.zip'
    target.parent.mkdir(exist_ok=True)
    manifest = {'format': 1, 'files': {}, 'notes': [
        'Existing weights, January validation split and normalized power remain unchanged.',
        'No raw CSVs, credentials, environment files or live user requests are packaged.',
        'Weather archive provenance and publication-lag assumptions remain unverified as documented.',
        'August 2025 uses a separate pre-August model; history comparison selects lead hours 1-24 only.',
    ]}
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(files):
            name = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            # Fixed zip timestamps make repeated packaging reproducible.
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, data, compresslevel=9)
            manifest['files'][name] = {'sha256': sha(data), 'bytes': len(data)}
    manifest['archive_sha256'] = sha(target.read_bytes())
    (target.parent / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Packaged {len(files)} runtime files, {target.stat().st_size:,} bytes. No model training performed.')


if __name__ == '__main__':
    main()
