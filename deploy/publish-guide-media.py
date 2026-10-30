"""Verify a rendered guide bundle and publish it into the mounted media volume.

Run on the deployment host after uploading the bundle, or locally for preview.
The manifest is replaced atomically only after all immutable assets are valid.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def publish(source: Path, destination: Path, *, dry_run: bool = False) -> dict:
    source = Path(source).resolve()
    destination = Path(destination)
    raw = (source / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    version = manifest.get('version', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', version):
        raise ValueError('Invalid media version')
    entries = manifest.get('files', [])
    if not entries or len(entries) > 100:
        raise ValueError('Invalid media file list')
    names = set()
    for entry in entries:
        name, expected = entry.get('name', ''), entry.get('sha256', '')
        if (not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,150}', name)
                or '..' in name or name in names or name == 'manifest.json'
                or Path(name).suffix not in {'.mp4', '.vtt', '.jpg', '.png', '.json'}
                or not re.fullmatch(r'[a-f0-9]{64}', expected)):
            raise ValueError('Invalid or duplicate asset name/hash')
        names.add(name)
        file = source / name
        if file.is_symlink() or not file.is_file() or digest(file) != expected:
            raise ValueError(f'Asset hash mismatch: {name}')
    prefix = f'/guide-media/{version}/'
    for clip_id in ('intro', 'walkthrough'):
        clip = manifest.get(clip_id, {})
        for field in ('src', 'poster'):
            path = clip.get(field, '')
            if not path.startswith(prefix) or path[len(prefix):] not in names:
                raise ValueError('Manifest references an unverified asset')
        if not clip['src'].endswith('.mp4') or clip['src'][len(prefix):].replace('.mp4', '.vtt') not in names or Path(clip['poster']).suffix not in {'.jpg', '.png'}:
            raise ValueError('Each video needs verified MP4 and VTT files')
        if not isinstance(clip.get('duration'), (int, float)) or not 0 < clip['duration'] < 3600:
            raise ValueError('Invalid video duration')
    chapters = manifest.get('chapters')
    if not isinstance(chapters, list) or not chapters:
        raise ValueError('Missing chapters')
    previous = -1
    ids = set()
    for chapter in chapters:
        start = chapter.get('start')
        if (not isinstance(start, (int, float)) or not 0 <= start < manifest['walkthrough']['duration'] or start < previous
                or not isinstance(chapter.get('id'), str) or not chapter['id'].strip()
                or not isinstance(chapter.get('title'), str) or not chapter['title'].strip()
                or chapter['id'] in ids):
            raise ValueError('Invalid chapter order or ID')
        ids.add(chapter.get('id')); previous = start
    target = destination / version
    for entry in entries:
        existing = target / entry['name']
        if existing.exists() and (existing.is_symlink() or digest(existing) != entry['sha256']):
            raise ValueError('An immutable media version already has different bytes')
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            final = target / entry['name']
            if final.exists():
                continue
            fd, temp = tempfile.mkstemp(prefix='.upload-', dir=target)
            try:
                with os.fdopen(fd, 'wb') as output, (source / entry['name']).open('rb') as data:
                    shutil.copyfileobj(data, output)
                    output.flush(); os.fsync(output.fileno())
                if digest(Path(temp)) != entry['sha256']:
                    raise ValueError('Published asset hash mismatch')
                os.chmod(temp, 0o644)
                os.replace(temp, final)
            finally:
                if os.path.exists(temp): os.unlink(temp)
        fd, temp = tempfile.mkstemp(prefix='.manifest-', dir=destination)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(raw); output.flush(); os.fsync(output.fileno())
            os.chmod(temp, 0o644)
            os.replace(temp, destination / 'manifest.json')
        finally:
            if os.path.exists(temp): os.unlink(temp)
    return {'version': version, 'files': len(entries), 'dry_run': dry_run}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    print(json.dumps(publish(args.source, args.destination, dry_run=args.dry_run)))
