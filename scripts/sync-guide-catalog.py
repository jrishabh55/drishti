"""Copy canonical guide JSON for Python, or verify generated export with --check."""
from pathlib import Path
import argparse

root = Path(__file__).resolve().parents[1]
source = root / 'apps/web/src/guide/catalog.json'
target = root / 'apps/api/guide/catalog.json'
parser = argparse.ArgumentParser()
parser.add_argument('--check', action='store_true')
args = parser.parse_args()
if args.check:
    if not target.exists() or source.read_bytes() != target.read_bytes():
        raise SystemExit('Guide catalog export differs; run scripts/sync-guide-catalog.py')
else:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
print('Guide catalog export is in sync')
