"""Render benchmark: fps of the app's viewport (renderer, LOD, paged streaming, EDL) at several point budgets.

    python bench/bench_render.py [--gl hardware|software] [--points 100e6] [--budgets 10,20,30,40,80] [--json out]
    python bench/bench_render.py --interactive [--gl software] [--points 5e6]

A tiled synthetic street streams in from cold (reported: time until every wanted page is resident, and the
slowest frame meanwhile). Then the camera orbits the whole set for ``--frames`` frames per configuration, vsync
off. The LOD spacing is set tiny so the drawn count equals the budget (the worst case, as in M0). Hardware mode
refuses to measure while another process keeps the GPU busy or holds the VRAM the buffers need.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import dump, gpu_smi, memory_gb, synthetic_store, system, table  # noqa: E402

from pclabel.render import glsetup  # noqa: E402
from pclabel.render.renderer import BYTES_PER_POINT  # noqa: E402
from pclabel.settings import load_settings  # noqa: E402


def gpu_busy(samples: int = 5) -> float:
    vals = []
    for _ in range(samples):
        out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True).stdout.strip()  # fmt: skip
        if out.isdigit():
            vals.append(int(out))
        time.sleep(0.3)
    return float(np.median(vals)) if vals else float("nan")


def configs(budgets, software):
    out = [{"budget": int(b * 1e6), "edl": True, "adaptive": False, "size": 2.0} for b in budgets]
    mid = int(budgets[min(2, len(budgets) - 1)] * 1e6)
    out += [{"budget": mid, "edl": False, "adaptive": False, "size": 2.0},
            {"budget": mid, "edl": True, "adaptive": True, "size": 1.0}]  # fmt: skip
    return out


class Driver:
    def __init__(self, app, vp, cfgs, warmup, frames, need_bytes, check_vram):
        self.app, self.vp, self.cfgs = app, vp, cfgs
        self.warmup, self.frames = warmup, frames
        self.need_bytes, self.check_vram = need_bytes, check_vram
        self.phase, self.i, self.k = "stream", 0, 0
        self.t0 = time.perf_counter()
        self.stream_walls = []
        self.stream = {}
        self.rows = []
        self.wall, self.gpu, self.cpu = [], [], []
        self.invalid = None
        self.nvx_before = None

    def _apply(self):
        c = self.cfgs[self.i]
        r = self.vp.renderer
        r.lod = r.lod.__class__(point_budget=c["budget"], spacing_px=0.02)
        r.display.edl, r.display.adaptive, r.display.point_size = c["edl"], c["adaptive"], c["size"]
        self.k = 0
        self.wall, self.gpu, self.cpu = [], [], []

    def on_frame(self, item):
        stats, wall = item
        vp, r = self.vp, self.vp.renderer
        if self.phase == "stream":
            if self.nvx_before is None:
                self.nvx_before = glsetup.nvx_memory_kb() if self.check_vram else {}
                b = self.nvx_before
                if b and b["current_available"] * 1024 < 1.1 * self.need_bytes:
                    self.invalid = (f"only {b['current_available'] / 2**20:.2f} GiB of VRAM free for "
                                    f"{self.need_bytes / 2**30:.2f} GiB of point buffers")  # fmt: skip
                    print(f"NOT VALID: {self.invalid}", flush=True)
                    self.app.quit()
                    return
                self._apply()
            if wall == wall:
                self.stream_walls.append(wall)
            if stats.converged:
                w = np.array(self.stream_walls) * 1e3
                self.stream = {"seconds_to_resident": time.perf_counter() - self.t0, "frames": len(w),
                               "wall_ms_max": float(w.max()) if len(w) else float("nan"),
                               "wall_ms_p95": float(np.percentile(w, 95)) if len(w) else float("nan"),
                               "frames_over_100ms": int((w > 100).sum()), "resident_bytes": stats.resident_bytes}  # fmt: skip
                print(f"streamed {stats.resident_bytes / 2**30:.2f} GiB in {self.stream['seconds_to_resident']:.2f} s, "
                      f"{len(w)} frames, slowest {self.stream['wall_ms_max']:.1f} ms, "
                      f"{self.stream['frames_over_100ms']} over 100 ms", flush=True)  # fmt: skip
                self.phase = "measure"
                self._apply()
            vp.camera.orbit(360.0 / self.frames, 0.0)
            return
        self.k += 1
        if self.k > self.warmup:
            self.wall.append(wall)
            self.gpu.append(stats.gpu_ms)
            self.cpu.append(stats.cpu_ms)
        vp.camera.orbit(360.0 / self.frames, 0.0)
        if len(self.wall) < self.frames:
            return
        c = self.cfgs[self.i]
        w = np.array(self.wall) * 1e3
        g = np.array(self.gpu)
        g = g[np.isfinite(g)]
        row = {"budget_M": c["budget"] / 1e6, "drawn_M": stats.drawn / 1e6, "edl": c["edl"],
               "size": "adaptive" if c["adaptive"] else f"{c['size']:.0f} px", "fps": 1e3 / np.median(w),
               "wall_ms_median": float(np.median(w)), "wall_ms_p95": float(np.percentile(w, 95)),
               "wall_ms_max": float(w.max()), "gpu_ms_median": float(np.median(g)) if g.size else float("nan"),
               "cpu_ms_median": float(np.median(self.cpu))}  # fmt: skip
        self.rows.append(row)
        print(f"budget {row['budget_M']:6.2f}M edl={c['edl']!s:5} {row['size']:>8}: {row['fps']:6.1f} fps, "
              f"wall {row['wall_ms_median']:6.1f} ms (p95 {row['wall_ms_p95']:6.1f}), gpu {row['gpu_ms_median']:6.1f} ms",
              flush=True)  # fmt: skip
        self.i += 1
        if self.i == len(self.cfgs):
            vp.continuous = False
            self.app.quit()
        else:
            self._apply()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gl", choices=["hardware", "software"])
    ap.add_argument("--points", type=float)
    ap.add_argument("--budgets")
    ap.add_argument("--frames", type=int)
    ap.add_argument("--warmup", type=int, default=20)
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--force", action="store_true", help="measure even if the GPU is busy or VRAM is short")
    ap.add_argument("--json")
    a = ap.parse_args()
    settings = load_settings()
    mode = a.gl or settings.gl
    software = mode == "software"
    points = a.points or (2e6 if software else 100e6)
    budgets = [float(b) for b in (a.budgets or ("0.25,0.5,1" if software else "10,20,30,40,80")).split(",")]
    frames = a.frames or (30 if software else 240)

    busy = float("nan")
    if not software and not a.interactive:
        busy = gpu_busy()
        if busy > 20:
            msg = f"the GPU is {busy:.0f}% busy with other work; fps would measure the contention"
            if not a.force:
                raise SystemExit(f"NOT RUN: {msg} (free the GPU, use --gl software, or pass --force)")
            print(f"WARNING: {msg}", flush=True)

    glsetup.configure(settings, mode)
    from PySide6.QtWidgets import QApplication, QMainWindow

    from pclabel.render.viewport import Viewport
    from pclabel.taxonomy import load_taxonomy

    app = QApplication(sys.argv[:1])
    smi_before = {} if software else gpu_smi()
    src, prep = synthetic_store(points)
    print(f"data: {prep}", flush=True)
    need = src.n * BYTES_PER_POINT
    mem = max(settings.active_memory_bytes, int(need * 1.05))
    win = QMainWindow()
    vp = Viewport(mem, int(budgets[-1] * 1e6))
    win.setCentralWidget(vp)
    win.setWindowTitle(f"PCLabel render benchmark ({mode})")
    vp.set_source(src, load_taxonomy().color_table())

    if a.interactive:
        vp.continuous = True

        def title(item):
            stats, wall = item
            if wall == wall and wall > 0:
                win.setWindowTitle(f"PCLabel bench | {mode} | {1 / wall:5.1f} fps | drawn {stats.drawn / 1e6:.2f}M | "
                                   f"resident {stats.resident_bytes / 2**20:.0f} MiB")  # fmt: skip

        vp.frame.connect(title)
        win.showMaximized()
        app.exec()
        return

    driver = Driver(app, vp, configs(budgets, software), a.warmup, frames, need, check_vram=not software)
    vp.frame.connect(driver.on_frame)
    vp.continuous = True
    win.showMaximized()
    app.exec()
    if driver.invalid and not a.force:
        raise SystemExit(f"NOT RUN: {driver.invalid}")
    w, h = vp.physical_size()
    print(f"mode {mode}, viewport {w}x{h}, GL: {vp.gl_info}")
    nb, na = driver.nvx_before, glsetup.nvx_memory_kb() if not software else None
    if nb and na:
        print(f"NVX: free before {nb['current_available'] / 2**20:.2f} GiB, after {na['current_available'] / 2**20:.2f} GiB")
    print(table(driver.rows, ["budget_M", "drawn_M", "edl", "size", "fps", "wall_ms_median", "wall_ms_p95",
                              "wall_ms_max", "gpu_ms_median", "cpu_ms_median"]))  # fmt: skip
    dump(a.json, {"system": system(), "mode": mode, "prep": prep, "viewport": (w, h), "gl": vp.gl_info,
                  "stream": driver.stream, "rows": driver.rows, "gpu_busy_before_pct": busy,
                  "smi_before": smi_before, "invalid_reason": driver.invalid, "memory": memory_gb()})  # fmt: skip


if __name__ == "__main__":
    main()
