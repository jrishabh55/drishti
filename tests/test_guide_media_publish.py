"""Media publication verifies every byte before changing the current manifest."""
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

def module():
    path = Path(__file__).resolve().parents[1] / 'deploy/publish-guide-media.py'
    spec = importlib.util.spec_from_file_location('publish_guide_media', path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

def source(tmp_path):
    root = tmp_path / 'source'; root.mkdir()
    names = ['intro.mp4', 'walkthrough.mp4', 'intro.vtt', 'walkthrough.vtt', 'poster.jpg', 'chapters.json']
    files = []
    for name in names:
        data = name.encode(); (root / name).write_bytes(data)
        files.append({'name': name, 'sha256': hashlib.sha256(data).hexdigest()})
    manifest = {'version': 'v1', 'intro': {'src': '/guide-media/v1/intro.mp4', 'poster': '/guide-media/v1/poster.jpg', 'duration': 75}, 'walkthrough': {'src': '/guide-media/v1/walkthrough.mp4', 'poster': '/guide-media/v1/poster.jpg', 'duration': 360}, 'chapters': [{'id': 'paper', 'title': 'Paper', 'start': 0}], 'files': files}
    (root / 'manifest.json').write_text(json.dumps(manifest))
    return root

def test_publish_validates_before_manifest_and_is_idempotent(tmp_path):
    src = source(tmp_path); dest = tmp_path / 'published'; publish = module().publish
    publish(src, dest, dry_run=True)
    assert not dest.exists()
    publish(src, dest)
    assert (dest / 'v1/intro.mp4').read_bytes() == b'intro.mp4'
    before = (dest / 'manifest.json').read_bytes()
    (src / 'intro.mp4').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='hash'):
        publish(src, dest)
    assert (dest / 'manifest.json').read_bytes() == before

def test_publish_rejects_traversal(tmp_path):
    src = source(tmp_path); p = src / 'manifest.json'; manifest = json.loads(p.read_text()); manifest['version'] = '../escape'; p.write_text(json.dumps(manifest))
    with pytest.raises(ValueError): module().publish(src, tmp_path / 'published')

@pytest.mark.parametrize('field,value', [('id', None), ('title', 42), ('start', -0.5)])
def test_publish_rejects_unplayable_chapter_fields(tmp_path, field, value):
    src = source(tmp_path); p = src / 'manifest.json'; m = json.loads(p.read_text()); m['chapters'][0][field] = value; p.write_text(json.dumps(m))
    with pytest.raises(ValueError): module().publish(src, tmp_path / 'published')
