"""M1 manual check: open a project in the app, then drive a fixed camera path and log every frame.

    python bench/check_open.py "streets 1" [--gl software|hardware] [--budget N] [--out DIR]

Reports: time to read project.json, to the first frame and until every wanted page is resident (open time);
per-phase frame times (median, p95, max, frames over 100 ms, and how many of those happened while pages were
still streaming). Saves a frame log (CSV) and a screenshot per phase into --out. Hardware mode refuses to run
while another process keeps the GPU busy.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import memory_gb  # noqa: E402

from pclabel.render import glsetup  # noqa: E402
from pclabel.settings import load_settings  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--gl", choices=["hardware", "software"])
    ap.add_argument("--budget", type=int)
    ap.add_argument("--site", default="15,35,-30,-18", help="world xmin,xmax,ymin,ymax to zoom into")
    ap.add_argument("--frames", type=int, default=120)
    ap.add_argument("--out", default=".")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    if a.gl:
        settings.gl = a.gl
    if a.budget:
        if settings.software:
            settings.software_point_budget = a.budget
        else:
            settings.point_budget = a.budget
    if not settings.software:
        from bench_render import gpu_busy

        busy = gpu_busy()
        if busy > 20:
            raise SystemExit(f"NOT RUN: the GPU is {busy:.0f}% busy with other work (use --gl software)")
    glsetup.configure(settings, settings.gl)
    from PySide6.QtWidgets import QApplication

    from pclabel.render.shaders import MODE_INTENSITY, MODE_RGB
    from pclabel.taxonomy import load_taxonomy
    from pclabel.ui.app import resolve_project
    from pclabel.ui.main_window import MainWindow

    app = QApplication(sys.argv[:1])
    win = MainWindow(settings, load_taxonomy(), log_frames=str(out / "frames.csv"))
    win.resize(1920, 1080)
    win.showMaximized()
    for _ in range(20):
        app.processEvents()
    vp = win.viewport
    frames = []
    phase = ["open"]
    vp.frame.connect(lambda item: frames.append((phase[0], time.perf_counter(), item[1], item[0])))

    t0 = time.perf_counter()
    win.open_project(resolve_project(a.project, settings.projects_dir))
    t_project = time.perf_counter() - t0

    def run_until(cond, timeout):
        t = time.perf_counter()
        while time.perf_counter() - t < timeout and not cond():
            app.processEvents()
        app.processEvents()

    run_until(lambda: vp.first_converged_s is not None, 600)
    first_points = next((x[1] - t0 for x in frames if x[3].drawn > 0), None)
    opened = {"project_json_s": t_project, "first_frame_s": vp.first_frame_s, "first_points_s": first_points,
              "all_resident_s": vp.first_converged_s, "total_s": time.perf_counter() - t0}  # fmt: skip
    print(f"open: {opened}", flush=True)
    vp.grabFramebuffer().save(str(out / "0_open.png"))

    def drive(name, n, step):
        phase[0] = name
        vp.continuous = True
        k0 = len(frames)
        vp.update()  # an idle, converged view does not repaint by itself
        run_until(lambda: len(frames) - k0 >= 1, 60)
        for _ in range(n):
            step()
            k = len(frames)
            vp.update()
            run_until(lambda: len(frames) > k, 60)
        vp.continuous = False
        run_until(lambda: vp.last_stats.converged or vp.last_stats.memory_full, 600)
        vp.grabFramebuffer().save(str(out / f"{name}.png"))

    win.display.set_mode(MODE_RGB)
    drive("1_orbit", a.frames, lambda: vp.camera.orbit(360.0 / a.frames, 0.0))
    win.set_view("bev")
    vp.fit()
    drive("2_bev_overview", a.frames // 4, lambda: None)
    x0, x1, y0, y1 = (float(v) for v in a.site.split(","))
    goal = np.array([(x0 + x1) / 2, (y0 + y1) / 2, 0.0]) - vp.source.offset
    goal[2] = vp.camera.target[2]
    start, d0 = vp.camera.target.copy(), vp.camera.distance
    d1 = (y1 - y0) / 2 / np.tan(np.deg2rad(vp.camera.fov) / 2) * 1.2
    n = a.frames // 2
    path = iter(range(1, n + 1))

    def zoom_step():
        i = next(path, n) / n
        vp.camera.target = start + (goal - start) * i
        vp.camera.distance = d0 * (d1 / d0) ** i

    drive("3_zoom_to_site", n, zoom_step)
    win.display.set_mode(MODE_INTENSITY)
    win.display.edl.setChecked(True)
    drive("4_site_intensity_edl", a.frames // 4, lambda: None)

    # wall = time since the previous frame. A phase's first frame also contains the script's own work between
    # phases (screenshot, view switch), so it is reported apart; cpu = the renderer's time inside paintGL.
    rows = {}
    for name in dict.fromkeys(f[0] for f in frames):
        f = [x for x in frames if x[0] == name and x[2] == x[2]]
        if len(f) < 2:
            continue
        w = np.array([x[2] for x in f[1:]]) * 1e3
        cpu = np.array([x[3].cpu_ms for x in f])
        streaming = np.array([not x[3].converged for x in f[1:]])
        rows[name] = {"frames": len(f), "first_frame_wall_ms": f[0][2] * 1e3, "wall_ms_median": float(np.median(w)),
                      "wall_ms_p95": float(np.percentile(w, 95)), "wall_ms_max": float(w.max()),
                      "over_100ms": int((w > 100).sum()), "streaming_frames": int(streaming.sum()),
                      "over_100ms_while_streaming": int(((w > 100) & streaming).sum()),
                      "renderer_cpu_ms_median": float(np.median(cpu)), "renderer_cpu_ms_max": float(cpu.max()),
                      "drawn_median_M": float(np.median([x[3].drawn for x in f])) / 1e6}  # fmt: skip
        print(f"{name:22s} " + ", ".join(f"{k} {v:.4g}" if isinstance(v, float) else f"{k} {v}"
                                          for k, v in rows[name].items()), flush=True)  # fmt: skip
    res = {"project": a.project, "gl": vp.gl_info, "budget": settings.active_point_budget, "open": opened,
           "phases": rows, "viewport": vp.physical_size(), "memory": memory_gb()}  # fmt: skip
    (out / "check_open.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    win.close()


if __name__ == "__main__":
    main()
