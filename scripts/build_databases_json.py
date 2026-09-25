#!/usr/bin/env python3
"""Regenerates databases.json for each terrain page from its *.sqlite files.

The repo root holds the square-grid databases (terrain-test.html) and
terrain-hex/ holds the H3 hex databases (terrain-hex/index.html); each folder
gets its own databases.json, and a database of the wrong grid type for its
folder is rejected.

Naming convention: X.sqlite is a terrain map, X_gaps.sqlite (optional) is its
gap-fill overlay, shown on the page while the grid is held down.

Labels already present in databases.json are kept, so they can be edited by
hand; new maps get their filename (without .sqlite) as the label. Exits with
status 1 if any database is malformed, so a bad file fails the workflow
instead of silently breaking the page.
"""
import json
import sqlite3
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAPS_SUFFIX = '_gaps'
REQUIRED_TABLES = {'meta', 'categories', 'cells'}
# Per grid type: numeric meta keys the page needs, and the keys a gaps file
# must share with its map so their cells line up.
GRIDS = {
    'square': {
        'required': ['origin_lon', 'origin_lat', 'meters_per_degree_lon', 'meters_per_degree_lat',
                     'cell_size_m', 'grid_width_cols', 'grid_height_rows'],
        'match': ['origin_lon', 'origin_lat', 'cell_size_m', 'grid_width_cols', 'grid_height_rows'],
    },
    'h3': {
        'required': ['h3_resolution'],
        'match': ['h3_resolution'],
    },
}
FOLDERS = {ROOT: 'square', ROOT / 'terrain-hex': 'h3'}


def read_meta(path, grid, errors):
    """Returns the meta dict, or None (with errors appended) if the file is unusable."""
    name = path.relative_to(ROOT)
    try:
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = REQUIRED_TABLES - tables
            if missing:
                errors.append(f'{name}: missing table(s) {", ".join(sorted(missing))}')
                return None
            meta = dict(con.execute('SELECT key, value FROM meta'))
        finally:
            con.close()
    except sqlite3.Error as e:
        errors.append(f'{name}: not a readable SQLite database ({e})')
        return None
    found = meta.get('grid', 'square')
    if found != grid:
        errors.append(f'{name}: this is a {found}-grid database, but this folder is for {grid} grids')
        return None
    bad = []
    for key in GRIDS[grid]['required']:
        try:
            float(meta[key])
        except (KeyError, TypeError, ValueError):
            bad.append(key)
    if bad:
        errors.append(f'{name}: meta key(s) {", ".join(bad)} missing or not a number')
        return None
    return meta


def build(folder, grid, errors):
    manifest_path = folder / 'databases.json'
    old_labels = {}
    old_order = []
    if manifest_path.exists():
        for entry in json.loads(manifest_path.read_text(encoding='utf-8')):
            old_labels[entry['file']] = entry.get('label')
            old_order.append(entry['file'])

    files = sorted(p.name for p in folder.glob('*.sqlite'))
    rel = lambda n: (folder / n).relative_to(ROOT)
    for name in files:
        if not name.isascii():
            print(f'warning: {rel(name)} has non-ASCII characters; ASCII filenames are safer in URLs')
        if unicodedata.normalize('NFC', name) != name:
            errors.append(f'{rel(name)}: filename is not NFC-normalised and will not load in the browser')

    metas = {name: read_meta(folder / name, grid, errors) for name in files}
    valid = [name for name in files if metas[name] is not None]
    # Broken files are reported and left out of the manifest entirely.
    gaps = {name for name in valid if name[:-len('.sqlite')].endswith(GAPS_SUFFIX)}
    maps = [name for name in valid if name not in gaps]

    for gap in sorted(gaps.copy()):
        main_name = gap[:-len(GAPS_SUFFIX + '.sqlite')] + '.sqlite'
        if main_name not in maps:
            errors.append(f'{rel(gap)}: no matching {main_name} for this gaps file')
            continue
        diff = [k for k in GRIDS[grid]['match'] if metas[main_name].get(k) != metas[gap].get(k)]
        if diff:
            errors.append(f'{rel(gap)}: grid does not match {main_name} ({", ".join(diff)} differ)')
            gaps.discard(gap)

    # Keep the existing order (first entry is the page's default), append new maps.
    ordered = [f for f in old_order if f in maps] + [f for f in maps if f not in old_order]
    manifest = []
    for name in ordered:
        entry = {'file': name, 'label': old_labels.get(name) or name[:-len('.sqlite')]}
        gap = name[:-len('.sqlite')] + GAPS_SUFFIX + '.sqlite'
        if gap in gaps:
            entry['gaps'] = gap
        manifest.append(entry)

    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'Wrote {manifest_path.relative_to(ROOT)} with {len(manifest)} map(s)')


def main():
    errors = []
    for folder, grid in FOLDERS.items():
        if folder.is_dir():
            build(folder, grid, errors)
    if errors:
        for e in errors:
            print(f'error: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
