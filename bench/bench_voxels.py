"""Times the three tools on the voxel hash (geometric grow, pieces, split features) on a real project, read-only.

    python bench/bench_voxels.py ["streets 1"] [--site X,Y] [--runs N]

The site (world x, y; default the magic-select site of streets 1) is on the road; the grow seeds there, and pieces
and split work on a 6 m cylinder of points around it (road, kerb, verge). Each case runs once to compile, then
``--runs`` times; the median is printed. Nothing is written to the project.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from pclabel.settings import load_settings
from pclabel.store.project import Project
from pclabel.tools.normals import NormalGrowParams, normal_grow
from pclabel.tools.pieces import PieceParams, find_pieces
from pclabel.tools.split import set_features
from pclabel.tools.volume import VolumeParams, select_volume


def timed(fn, runs):
    fn()  # compile and warm the page cache
    t = []
    for _ in range(runs):
        t0 = time.perf_counter()
        out = fn()
        t.append(time.perf_counter() - t0)
    return float(np.median(t)), out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", nargs="?", default="streets 1")
    ap.add_argument("--site", default="22.85,-11.09")
    ap.add_argument("--runs", type=int, default=5)
    a = ap.parse_args()
    path = Path(a.project)
    if not path.exists():
        path = Path(load_settings().projects_dir) / a.project
    p = Project(path)
    x, y = (float(v) for v in a.site.split(","))
    # the ground at the site: the lowest few cm of a 0.3 m cylinder, local coordinates
    c = np.array([x, y, 0.0]) - p.offset
    near = select_volume(p.xyz, p.tiles, c, (0, 0, 1), (1, 0, 0), VolumeParams("cylinder", radius=0.3, bottom=-50, top=50))
    z = np.asarray(p.xyz[near, 2], np.float64)
    seed = np.array([c[0], c[1], np.percentile(z, 5)])
    area = select_volume(p.xyz, p.tiles, seed, (0, 0, 1), (1, 0, 0),
                         VolumeParams("cylinder", radius=6.0, bottom=-0.5, top=3.0))  # fmt: skip
    print(f"{p.name}: site {x}, {y}; 6 m cylinder {area.size:,} points")
    t, r = timed(lambda: normal_grow(p.xyz, p.tiles, seed, NormalGrowParams()), a.runs)
    print(f"geometric grow (voxels built each run): {t:.3f} s, {r.indices.size:,} points, {r.voxels.occupied:,} voxels")
    t, pcs = timed(lambda: find_pieces(p.xyz, area, PieceParams()), a.runs)
    print(f"pieces of the cylinder: {t:.3f} s, {pcs.count:,} pieces")
    t, f = timed(lambda: set_features(p.xyz, area), a.runs)
    print(f"split features of the cylinder: {t:.3f} s, {len(f.count):,} voxels")


if __name__ == "__main__":
    main()
