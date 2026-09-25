#!/usr/bin/env python3
"""Converts a square-grid terrain database into an H3 hex database.

    python3 scripts/square_to_h3.py SRC.sqlite DEST.sqlite [--resolution 12]

How each hex gets its category depends on which cell is bigger:
- Hexes smaller than the squares (e.g. resolution 12, ~307 m2 vs 625 m2):
  the hex takes the square under its centre. Voting would leave holes,
  because many small hexes contain no square centre at all.
- Hexes bigger than the squares (e.g. resolution 11, ~2150 m2): the hex takes
  the category most of its squares have, voting over the squares whose
  centres fall inside it. Squares with no row vote "unmapped", and a tie is
  settled by the square under the hex's centre.
Either way the hex map ends where the square map ends. Works the same for
gaps databases, where only the gap cells have rows.

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


def sample_centres(square_at, origin_lat, origin_lon, m_lat, m_lon, cell, width, height, res):
    # Every hex whose centre lies inside the square grid's bounding box.
    top = origin_lat + height * cell / m_lat
    right = origin_lon + width * cell / m_lon
    box = h3.LatLngPoly([(origin_lat, origin_lon), (origin_lat, right), (top, right), (top, origin_lon)])
    hexes = {}
    for hex_id in h3.polygon_to_cells(box, res):
        cat = square_at(*h3.cell_to_latlng(hex_id))
        if cat is not UNMAPPED:
            hexes[hex_id] = cat
    return hexes


def vote(square, square_at, origin_lat, origin_lon, m_lat, m_lon, cell, width, height, res):
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
    return hexes


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('src')
    ap.add_argument('dest')
    ap.add_argument('--resolution', type=int, default=12)
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

    if h3.average_hexagon_area(res, 'm^2') < cell * cell:
        method = 'category of the square cell under each hex centre'
        hexes = sample_centres(square_at, origin_lat, origin_lon, m_lat, m_lon, cell, width, height, res)
    else:
        method = ('majority vote of square cell centres inside each hex; '
                  'empty squares vote unmapped; ties go to the square under the hex centre')
        hexes = vote(square, square_at, origin_lat, origin_lon, m_lat, m_lon, cell, width, height, res)

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
        'conversion': method,
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
