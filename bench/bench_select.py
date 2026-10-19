"""Selection spike: numba point-in-mask kernel over ~100M points (target >= 200M points/s, ARCHITECTURE).

    python bench/bench_select.py [--points 100e6] [--json out.json]

Points are a tiled synthetic street in store order (tiled + shuffled), the camera frames all of them, and the
lasso is a 40-vertex star covering about a quarter of a 1920 x 1080 viewport. Every point is tested (no tile
culling), so this is the worst case.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import Timer, dump, memory_gb, synthetic_store, system, table  # noqa: E402

from pclabel.render.camera import OrbitCamera  # noqa: E402
from pclabel.tools.selection import DepthTest, rasterize_polygon, select_in_mask  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", type=float, default=100e6)
    ap.add_argument("--repeats", type=int, default=7)
    ap.add_argument("--json")
    a = ap.parse_args()

    W, H = 1920, 1080
    store, prep = synthetic_store(a.points)
    xyz = store.xyz
    t = store.tiles
    cam = OrbitCamera()
    cam.fit([t["x0"].min(), t["y0"].min(), t["zmin"].min()], [t["x1"].max(), t["y1"].max(), t["zmax"].max()])
    mvp = cam.mvp(W / H)

    rng = np.random.default_rng(0)
    ang = np.sort(rng.uniform(0, 2 * np.pi, 40))
    rad = np.where(np.arange(40) % 2 == 0, 480, 260) * rng.uniform(0.9, 1.1, 40)
    poly = np.stack([W / 2 + rad * np.cos(ang), H / 2 + 0.8 * rad * np.sin(ang)], 1)
    rasterize_polygon(poly, W, H)  # JIT
    with Timer() as tr:
        mask = rasterize_polygon(poly, W, H)
    # depth buffer at the far plane: every point passes the test, so it costs the full test plus the write
    # (correctness is in tests/test_selection.py)
    depth = DepthTest(np.ones((H, W), np.float32), cam.near, cam.far)
    select_in_mask(xyz[:100_000], mvp, mask)  # JIT
    select_in_mask(xyz[:100_000], mvp, mask, depth)

    rows = []
    for label, n, dt in (
        ("mask", len(xyz), None),
        ("mask + depth test", len(xyz), depth),
        ("mask, 50M candidates", min(50_000_000, len(xyz)), None),
        ("mask + depth, 50M candidates", min(50_000_000, len(xyz)), depth),
    ):
        pts = xyz[:n]
        times = []
        for _ in range(a.repeats):
            with Timer() as tm:
                idx = select_in_mask(pts, mvp, mask, dt)
            times.append(tm.s)
        s = float(np.median(times))
        rows.append({"case": label, "candidates": n, "selected": int(idx.size), "median_s": s,
                     "min_s": float(min(times)), "Mpts_per_s": n / s / 1e6})  # fmt: skip

    print(f"mask {W}x{H}, coverage {mask.mean():.1%}, rasterise {tr.s * 1e3:.1f} ms")
    print(table(rows, ["case", "candidates", "selected", "median_s", "min_s", "Mpts_per_s"]))
    print(f"data prep: {prep}")
    out = {"system": system(), "prep": prep, "mask_coverage": float(mask.mean()), "rasterize_ms": tr.s * 1e3,
           "rows": rows, "memory": memory_gb()}  # fmt: skip
    dump(a.json, out)


if __name__ == "__main__":
    main()
