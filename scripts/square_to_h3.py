#!/usr/bin/env python3
"""Converts a square-grid terrain database into an H3 hex database.

    python3 scripts/square_to_h3.py SRC.sqlite DEST.sqlite [--resolution 11]

Each hex gets the category most of its square cells have, voting over the
squares whose centres fall inside the hex. Squares with no row count as a
vote for "unmapped", so the hex map ends where the square map ends. A tie is
settled by the square under the hex's own centre. Works the same for gaps
databases, where only the gap cells have rows.

The source file is only read. The output has the same `categories` table, a
`cells(h3 TEXT PRIMARY KEY, category)` table and a `meta` table that records
the H3 resolution and where the data came from. Needs the `h3` package
(pip install h3).
"""
import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import h3

UNMAPPED = None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('src')
    ap.add_argument('dest')
    ap.add_argument('--resolution', type=int, default=11)
    args = ap.parse_args()
    src, dest, res = Path(args.src), Path(args.dest), args.resolution
    if dest.exists():
        sys.exit(f'{dest} already exists; delete it first')

    con = sqlite3.connect(f'file:{src}?mode=ro', uri=True)
    meta = dict(con.execute('SELECT key, value FROM meta'))
    categories = list(con.execute('SELECT id, name FROM categories'))
    square = dict(con.execute('SELECT cell_id, category FROM cells'))
    con.close()

    origin_lon = float(meta['origin_lon'])
    origin_lat = float(meta['origin_lat'])
    m_lon = float(meta['meters_per_degree_lon'])
    m_lat = float(meta['meters_per_degree_lat'])
    cell = float(meta['cell_size_m'])
    width = int(meta['grid_width_cols'])
    height = int(meta['grid_height_rows'])

    def square_at(lat, lon):
        col = int((lon - origin_lon) * m_lon // cell)
        row = int((lat - origin_lat) * m_lat // cell)
        if 0 <= row < height and 0 <= col < width:
            return square.get(row * width + col, UNMAPPED)
        return UNMAPPED

    votes = defaultdict(Counter)
    for row in range(height):
        lat = origin_lat + (row + 0.5) * cell / m_lat
        for col in range(width):
            lon = origin_lon + (col + 0.5) * cell / m_lon
            votes[h3.latlng_to_cell(lat, lon, res)][square.get(row * width + col, UNMAPPED)] += 1

    hexes = {}
    for hex_id, counter in votes.items():
        ranked = counter.most_common()
        best = ranked[0][1]
        tied = {cat for cat, n in ranked if n == best}
        if len(tied) > 1:
            center = square_at(*h3.cell_to_latlng(hex_id))
            winner = center if center in tied else ranked[0][0]
        else:
            winner = ranked[0][0]
        if winner is not UNMAPPED:
            hexes[hex_id] = winner

    out = sqlite3.connect(dest)
    out.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)')
    out.execute('CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT)')
    out.execute('CREATE TABLE cells (h3 TEXT PRIMARY KEY, category INTEGER) WITHOUT ROWID')
    out.executemany('INSERT INTO categories VALUES (?, ?)', categories)
    out.executemany('INSERT INTO cells VALUES (?, ?)', sorted(hexes.items()))
    new_meta = {
        'grid': 'h3',
        'h3_resolution': str(res),
        'converted_from': src.name,
        'conversion': 'majority vote of square cell centres inside each hex; '
                      'empty squares vote unmapped; ties go to the square under the hex centre',
        'generated': date.today().isoformat(),
    }
    for key in ('source', 'coverage', 'generated'):
        if key in meta:
            new_meta['source_' + key] = meta[key]
    out.executemany('INSERT INTO meta VALUES (?, ?)', new_meta.items())
    out.commit()
    out.execute('VACUUM')
    out.close()
    print(f'{src.name}: {len(square)} squares -> {len(hexes)} hexes at resolution {res} -> {dest}')


if __name__ == '__main__':
    main()
