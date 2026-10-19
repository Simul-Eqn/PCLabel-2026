"""Helpers for the benchmark scripts (not part of the package; not run by pytest)."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from pathlib import Path

import numpy as np

from pclabel.sysinfo import memory_gb  # noqa: F401  (re-exported for the bench scripts)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def gpu_smi() -> dict:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()  # fmt: skip
        name, total, used, driver = [s.strip() for s in out.split(",")]
        return {"gpu": name, "vram_total_mib": int(total), "vram_used_mib": int(used), "driver": driver}
    except Exception:
        return {}


def system() -> dict:
    import numba

    return {
        "python": platform.python_version(),
        "cpu": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "numba_threads": numba.config.NUMBA_NUM_THREADS,
        "numpy": np.__version__,
        "numba": numba.__version__,
        **gpu_smi(),
    }


class Timer:
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.s = time.perf_counter() - self.t0


def table(rows: list[dict], cols: list[str]) -> str:
    def fmt(v):
        if isinstance(v, float):
            return f"{v:.3g}" if abs(v) < 1000 else f"{v:,.0f}"
        if isinstance(v, int):
            return f"{v:,}"
        return str(v)

    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(fmt(r.get(c, "")) for c in cols) + " |" for r in rows]
    return "\n".join(lines)


def dump(path: str | None, obj):
    if path:
        Path(path).write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def synthetic_store(n_points: float, seed: int = 0, max_tile_points: int = 2_000_000):
    """make_street (5M) tiled up to about n_points, then tiled into store order. Returns (PointSource, timings)."""
    from pclabel.render.renderer import PointSource
    from pclabel.store.tiles import TilingParams, build_tiles
    from pclabel.synth import make_street, tiled_copy

    times = {}
    with Timer() as t:
        base = make_street(n_target=min(5_000_000, max(int(n_points), 100_000)), seed=seed)
    times["make_street_s"] = t.s
    copies = max(1, int(round(n_points / len(base))))
    with Timer() as t:
        scan = tiled_copy(base, copies) if copies > 1 else base
    times["tiled_copy_s"] = t.s
    with Timer() as t:
        tiles, order = build_tiles(scan.xyz, TilingParams(max_points=max_tile_points, seed=seed))
    times["build_tiles_s"] = t.s
    with Timer() as t:
        src = PointSource(tiles=tiles, xyz=scan.xyz[order], rgb=scan.rgb[order], intensity=scan.intensity[order],
                          labels=np.zeros(len(scan), np.uint8), flags=np.zeros(len(scan), np.uint8),
                          offset=scan.offset)  # fmt: skip
    times["reorder_s"] = t.s
    times["points"] = len(scan)
    times["tiles"] = len(tiles)
    return src, times
