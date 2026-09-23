"""Build one portable HTML from editable UI sources, preserving real snapshots."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, help='Optional existing real forecast snapshot JSON')
    args = parser.parse_args()
    path = ROOT / 'dashboard.html'
    if args.snapshot:
        data = json.loads(args.snapshot.read_text(encoding='utf-8'))
    else:
        old = path.read_text(encoding='utf-8')
        data = json.loads(old.split('/*DATA_START*/')[1].split('/*DATA_END*/')[0])
    if data.get('data_kind') != 'real_saved_forecasts' or len(data['runs']) != 58:
        raise ValueError('Expected the real 58-run snapshot, not mock data.')
    html = (ROOT / 'web/dashboard.template.html').read_text(encoding='utf-8')
    html = html.replace('/*CSS*/', (ROOT / 'web/dashboard.css').read_text(encoding='utf-8'))
    html = html.replace('/*APP*/', (ROOT / 'web/dashboard.js').read_text(encoding='utf-8') + '\n' +
                        (ROOT / 'web/weather.js').read_text(encoding='utf-8') + '\n' +
                        (ROOT / 'web/history.js').read_text(encoding='utf-8'))
    html = html.replace('/*DATA_START*/{}/*DATA_END*/', '/*DATA_START*/' + json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).replace('</', '<\\/') + '/*DATA_END*/')
    path.write_text(html, encoding='utf-8')
    print(f'Built {path.name}: {len(data["runs"])} real runs, inline CSS/JS; optional Windy view loads separately.')


if __name__ == '__main__':
    main()
