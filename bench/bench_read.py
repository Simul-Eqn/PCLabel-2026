"""Read spike: E57 throughput and peak RAM on the real scans (read-only).

    python bench/bench_read.py [--json out.json]           # all cases, each in a fresh process
    python bench/bench_read.py --case chunked --file F ...  # one case (used by the driver)

Cases:
- ``full``: one buffer for the whole scan (what pye57's read_scan_raw does), float64 xyz.
- ``chunked``: pclabel.io.e57.iter_chunks (fixed 4M-record buffers), converted to float32 xyz relative to the
  offset, uint8 rgb, uint16 intensity, collected into preallocated arrays (``--sink ram``) or only touched
  (``--sink none``, bounded memory whatever the file size).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import DATA_DIR, dump, memory_gb, table  # noqa: E402

from pclabel.io.e57 import COLORS, iter_chunks, read_info  # noqa: E402

FILES = {
    "newroad 2": DATA_DIR / "newroad 2.e57",
    "ExchangeGreenFast 1": DATA_DIR / "ExchangeGreen" / "BLKARC" / "Fast" / "ExchangeGreenFast 1.e57",
    "streets 1": DATA_DIR / "streets 1.e57",
}


def case_full(path: Path) -> dict:
    from pye57 import libe57

    from pclabel.io.e57 import _child, _node

    info = read_info(path)[0]
    n = info.n_raw
    t0 = time.perf_counter()
    imf = libe57.ImageFile(str(path), "r")  # read-only
    try:
        scan = _node(libe57.VectorNode(imf.root().get("data3D")).get(0))
        points = _child(scan, "points")
        bufs = {f: np.empty(n, np.float64) for f in ("cartesianX", "cartesianY", "cartesianZ")}
        bufs["intensity"] = np.empty(n, np.float32)
        for c in COLORS:
            bufs[c] = np.empty(n, np.uint8)
        sdb = libe57.VectorSourceDestBuffer()
        for f, a in bufs.items():
            sdb.append(libe57.SourceDestBuffer(imf, f, a, n, True, True))
        r = points.reader(sdb)
        got = r.read()
        r.close()
    finally:
        imf.close()
    s = time.perf_counter() - t0
    return {"records": int(got), "valid": int(got), "seconds": s}


def case_chunked(path: Path, sink: str, max_points: int | None, chunk: int) -> dict:
    info = read_info(path)
    n = sum(i.n_raw for i in info) if max_points is None else min(max_points, sum(i.n_raw for i in info))
    t0 = time.perf_counter()
    if sink == "ram":
        xyz = np.empty((n, 3), np.float32)
        rgb = np.empty((n, 3), np.uint8)
        inten = np.empty(n, np.uint16)
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    k = 0
    records = 0
    for c in iter_chunks(path, chunk_size=chunk, max_points=max_points):
        m = c.xyz.shape[0]
        if sink == "ram":
            xyz[k : k + m] = c.xyz
            rgb[k : k + m] = c.rgb
            inten[k : k + m] = c.intensity
        else:
            lo = np.minimum(lo, c.xyz.min(0))
            hi = np.maximum(hi, c.xyz.max(0))
        k += m
        records += c.n_raw
    s = time.perf_counter() - t0
    return {"records": records, "valid": k, "seconds": s}


def run_one(args) -> dict:
    path = FILES[args.file]
    if args.case == "full":
        r = case_full(path)
    else:
        r = case_chunked(path, args.sink, args.max_points, args.chunk)
    r.update(memory_gb())
    r["Mpts_per_s"] = r["records"] / r["seconds"] / 1e6
    return r


CASES = [
    ("full", "newroad 2", "ram", None),
    ("chunked", "newroad 2", "ram", None),
    ("full", "ExchangeGreenFast 1", "ram", None),
    ("chunked", "ExchangeGreenFast 1", "ram", None),
    ("chunked", "streets 1", "none", 50_000_000),
    ("chunked", "streets 1", "none", 50_000_000),  # second run: OS file cache warm
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", choices=["full", "chunked"])
    ap.add_argument("--file", choices=list(FILES))
    ap.add_argument("--sink", choices=["ram", "none"], default="ram")
    ap.add_argument("--max-points", type=int)
    ap.add_argument("--chunk", type=int, default=4_000_000)
    ap.add_argument("--json")
    a = ap.parse_args()
    if a.case:
        print(json.dumps(run_one(a)))
        return
    rows = []
    for case, f, sink, maxp in CASES:
        cmd = [sys.executable, __file__, "--case", case, "--file", f, "--sink", sink, "--chunk", str(a.chunk)]
        if maxp:
            cmd += ["--max-points", str(maxp)]
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if out.returncode != 0:
            print(out.stderr, file=sys.stderr)
            raise SystemExit(f"case failed: {case} {f}")
        r = json.loads(out.stdout.strip().splitlines()[-1])
        r.update({"case": case, "file": f, "sink": sink})
        rows.append(r)
        print(f"{case:8s} {f:20s} {r['records']:>12,} rec {r['seconds']:7.2f} s {r['Mpts_per_s']:6.2f} M/s "
              f"peak WS {r['peak_working_set_gb']:.2f} GB", flush=True)  # fmt: skip
    print(table(rows, ["case", "file", "sink", "records", "seconds", "Mpts_per_s", "peak_working_set_gb",
                       "peak_commit_gb"]))  # fmt: skip
    dump(a.json, rows)


if __name__ == "__main__":
    main()
