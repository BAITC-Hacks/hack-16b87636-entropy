"""Restore the checked-in runtime bundle in a clean checkout without local paths."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def restore(destination: Path):
    destination = destination.resolve()
    archive = ROOT / 'deployment/runtime.zip'
    manifest = json.loads((archive.parent / 'manifest.json').read_text(encoding='utf-8'))
    if sha(archive.read_bytes()) != manifest['archive_sha256']:
        raise ValueError('Runtime archive checksum mismatch.')
    with zipfile.ZipFile(archive) as bundle:
        if set(bundle.namelist()) != set(manifest['files']):
            raise ValueError('Runtime archive contents differ from manifest.')
        # Verify every entry before writing anything. Do not use extractall().
        for name, metadata in manifest['files'].items():
            parts = PurePosixPath(name)
            target = (destination / name).resolve()
            if parts.is_absolute() or '..' in parts.parts or not target.is_relative_to(destination):
                raise ValueError('Invalid archive path.')
            data = bundle.read(name)
            if len(data) != metadata['bytes'] or sha(data) != metadata['sha256']:
                raise ValueError(f'Runtime input checksum mismatch: {name}')
        written = 0
        for name, metadata in manifest['files'].items():
            target = destination / name
            if target.exists():
                if sha(target.read_bytes()) != metadata['sha256']:
                    raise ValueError(f'Refusing to overwrite changed local artifact: {name}')
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundle.read(name))
            written += 1
    print(f'Runtime verified: {len(manifest["files"])} files, {written} restored.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', type=Path, default=ROOT)
    restore(parser.parse_args().destination)
