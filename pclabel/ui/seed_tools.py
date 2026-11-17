"""Clicks with the seed tools: grow select (G: height and intensity, or geometric) and magic select (I), run
again when a threshold changes, with where they stopped shown; the other click tools (pieces, tree, trace,
marker) are handed their click."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QTimer

from pclabel.ops.selection import combine
from pclabel.tools.ground import R_GAP, R_HEIGHT, R_IRANGE, R_ISTEP, R_ROUGH, ground_grow
from pclabel.tools.magic import magic_select
from pclabel.tools.normals import NormalGrowResult, normal_grow
from pclabel.ui.worker import run_in_worker


class SeedToolsMixin:
    """Part of MainWindow."""

    def _init_seed_tools(self):
        self._grow_state = None  # the last seed tool run: tool, seed, op, base selection, cached cell grid
        self._magic_image = None  # magic select's intensity picture, reused by later clicks
        self._normal_voxels = None  # grow select's geometric voxels and normals, reused by later clicks
        self._grow_busy = False
        self._grow_pending = False
        self._grow_timer = QTimer(self)
        self._grow_timer.setSingleShot(True)
        self._grow_timer.setInterval(350)
        self._grow_timer.timeout.connect(self.run_grow)

    def _on_seed(self, p_local, op):
        tool = self.viewport.tool
        if self.project is not None and tool == "piece":
            self.pieces.click(p_local, op)
            return
        if self.project is not None and tool == "tree":
            self.trees.place(p_local)
            return
        if self.project is not None and tool == "trace":
            self.trace.click(p_local)
            return
        if self.project is not None and tool == "marker":
            self.annot.click_marker(p_local)
            return
        if self.project is None or tool not in ("grow", "magic"):
            return
        base = self.selection.copy() if op in ("add", "subtract") else np.empty(0, np.int64)
        self._grow_state = {"tool": tool, "seed": np.asarray(p_local, np.float64), "op": op, "base": base,
                            "base_ops": list(self.selection_ops) if op != "replace" else [], "grid": None,
                            "grid_key": None, "before": self._capture("selection")}  # fmt: skip
        self.run_grow()

    def _seed_dock(self, tool):
        return self.magic if tool == "magic" else self.grow

    def _params_changed(self, tool):
        if self._grow_state is not None and self._grow_state["tool"] == tool:
            self._grow_timer.start()

    STOP_COLORS = {R_HEIGHT: (255, 48, 48), R_ROUGH: (255, 48, 255), R_ISTEP: (32, 224, 224),
                   R_IRANGE: (64, 112, 255), R_GAP: (48, 224, 48)}  # fmt: skip

    def _show_stops(self):
        """Coloured points where the last seed tool stopped, at the ground height of the cell or pixel."""
        vp, st = self.viewport, self._grow_state
        if vp.renderer is None:
            return
        pts = rgb = None
        if st is not None and st.get("stops") is not None and self._seed_dock(st["tool"]).show_stops.isChecked():
            x0, y0, cell, surf, reason = st["stops"]
            ys, xs = np.nonzero(reason)
            if ys.size:
                z = surf[ys, xs]
                if np.isnan(z).any():  # gap cells have no points: use the ground around them
                    from scipy import ndimage

                    ya, yb, xa, xb = max(ys.min() - 3, 0), ys.max() + 4, max(xs.min() - 3, 0), xs.max() + 4
                    sub = surf[ya:yb, xa:xb]
                    fin = np.isfinite(sub)
                    k = np.ones((7, 7))
                    num = ndimage.convolve(np.where(fin, sub, 0.0), k, mode="constant")
                    den = ndimage.convolve(fin.astype(float), k, mode="constant")
                    near = np.divide(num, den, out=np.full_like(num, st["seed"][2]), where=den > 0)
                    z = np.where(np.isfinite(z), z, near[ys - ya, xs - xa])
                pts = np.stack([x0 + (xs + 0.5) * cell, y0 + (ys + 0.5) * cell, z], 1)
                rgb = np.array([self.STOP_COLORS[int(r)] for r in reason[ys, xs]], np.uint8)
        with vp.current():
            vp.renderer.set_markers(pts, rgb)
        vp.update()

    def run_grow(self):
        """Run the active seed tool from the current seed in a worker (again when a threshold changes)."""
        st = self._grow_state
        if st is None or self.project is None or self.viewport.renderer is None:
            return
        if self._grow_busy:
            self._grow_pending = True
            return
        filters = self.filters()
        dock = self._seed_dock(st["tool"])
        params = dock.params()
        pr = self.project
        st["mode"] = "magic" if st["tool"] == "magic" else self.grow.current_mode()
        if st["tool"] == "magic":
            image = self._magic_image

            def run():
                return magic_select(pr.xyz, pr.intensity, pr.tiles, st["seed"], params, image=image, filters=filters)
        elif st["mode"] == "normals":
            voxels = self._normal_voxels

            def run():
                return normal_grow(pr.xyz, pr.tiles, st["seed"], params, voxels=voxels, filters=filters)
        else:
            key = (tuple(st["seed"]), params.cell, params.radius, params.surface_band, params.cell_points,
                   *filters.key())  # fmt: skip
            grid = st["grid"] if st["grid_key"] == key else None
            st["want_key"] = key

            def run():
                return ground_grow(pr.xyz, pr.intensity, pr.tiles, st["seed"], params, grid=grid, filters=filters)

        self._grow_busy = True
        dock.result.setText("working...")

        run_in_worker(run, lambda res, err, s: self._grow_done(st, res, s, err), st["tool"])

    def _grow_done(self, st, res, seconds, err):
        self._grow_busy = False
        if res is not None and st["tool"] == "magic":
            self._magic_image = res.image  # reusable by the next click, whatever happened since
        if isinstance(res, NormalGrowResult):
            self._normal_voxels = res.voxels
        if st is self._grow_state:  # otherwise a newer click or a cleared selection replaced it
            dock = self._seed_dock(st["tool"])
            if err is not None:
                dock.result.setText(f"nothing selected: {err}")
            else:
                if not st.get("recorded"):  # threshold changes re-run the same click: one undo step for all
                    name = "magic select" if st["tool"] == "magic" else "grow select"
                    self.checkpoint("selection", f"{name} ({st['op']})", before=st["before"])
                    st["recorded"] = True
                self.selection, dropped = combine(st["base"], self._visible(res.indices), st["op"],
                                                  self.locked_points())  # fmt: skip
                i = res.info
                seed_w = st["seed"] + self.project.offset
                tool = "magic_select" if st["tool"] == "magic" else "grow_select"
                self.selection_def = {"tool": tool, "op": st["op"], "seed": seed_w.tolist(), "params": i["params"],
                                      "result": {k: v for k, v in i.items() if k not in ("params", "reason_grid")}}  # fmt: skip
                if tool == "grow_select":
                    self.selection_def["mode"] = {"height": "height_intensity", "normals": "normals"}[st["mode"]]
                self.selection_ops = st["base_ops"] + [self.selection_def]
                stops = ", ".join(f"{k} {v:,}" for k, v in sorted(i.get("stopped_by", {}).items(), key=lambda kv: -kv[1])
                                  if v)  # fmt: skip
                edge = (f"\nreached the {i['params']['radius']:.0f} m reach: raise it to go further"
                        if i["reached_edge"] else "")  # fmt: skip
                if st["tool"] == "magic":
                    lo, hi = i["accepted_intensity"]
                    follow = f", following drift over {i['params']['memory']:g} m" if i["params"]["memory"] > 0 else ""
                    reused = "reused" if i["picture_reused"] else "built"
                    dock.result.setText(
                        f"clicked intensity {i['seed_intensity']:.3f}: accepts {lo:.3f} to {hi:.3f}{follow}\n"
                        f"{i['points']:,} points on {i['area_m2']:.2f} m² ({i['pixels']:,} pixels){edge}\n"
                        f"stopped by (pixels): {stops or 'nothing'}\n"
                        f"{seconds:.2f} s (picture {reused}: {i['picture_points']:,} ground points)")  # fmt: skip
                    img = res.image
                    st["stops"] = (img.x0, img.y0, img.pixel, img.z, i["reason_grid"])
                elif st["mode"] == "normals":
                    n = i["seed_normal"]
                    tilt = float(np.degrees(np.arccos(min(1.0, abs(n[2])))))
                    reused = "reused" if i["voxels_reused"] else "built"
                    dock.result.setText(
                        f"clicked surface: normal {tilt:.1f}° from vertical, curvature {i['seed_curvature']:.4f}\n"
                        f"{i['points']:,} points ({i['voxels_grown']:,} voxels grown; {i['refused_by_angle']:,} "
                        f"neighbours turned away more than {i['params']['smoothness']:g}°){edge}\n"
                        f"{seconds:.2f} s (voxels {reused}: {i['voxels_built']:,} from {i['voxel_points']:,} points)")  # fmt: skip
                    st["stops"] = None
                else:
                    st["grid"], st["grid_key"] = res.grid, st["want_key"]
                    dock.result.setText(
                        f"seed intensity {i['seed_intensity']:.3f}, height {i['seed_height_local'] + self.project.offset[2]:.3f} m\n"
                        f"{i['points']:,} points on {i['area_m2']:.1f} m² ({i['cells_grown']:,} cells grown, "
                        f"{i['cells_bridged']:,} bridged as markings){edge}\n"
                        f"stopped by (cells): {stops or 'nothing'}\n"
                        f"{seconds:.2f} s (cells from {res.grid.sample_points:,} points)")  # fmt: skip
                    g = res.grid
                    st["stops"] = (g.x0, g.y0, g.cell, g.surf, i["reason_grid"])
                self._set_overlay()
                self._show_stops()
                self._show_selection_count(seconds, dropped)
        if self._grow_pending:
            self._grow_pending = False
            self.run_grow()
