"""Main window: viewport, docks, menus, keymap (SPEC §1, §15) and status bar."""

from __future__ import annotations

import threading
import time

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QFileDialog, QLabel, QMainWindow, QMessageBox, QToolBar

from pclabel.analysis.frames import Z_FRAME, HeightFrame, PlaneFitParams, frame_from_points
from pclabel.render.camera import ORTHO
from pclabel.render.renderer import PointSource
from pclabel.render.shaders import MODE_INTENSITY, MODE_NAMES, MODE_WRAP, MODE_WRAP_SHADED
from pclabel.render.viewport import Viewport
from pclabel.settings import Settings
from pclabel.store.project import Project
from pclabel.tools.ground import ground_grow
from pclabel.tools.selection import DepthTest, SelectFilters, select_visible
from pclabel.ui.docks import (
    ClipDock,
    DisplayDock,
    FrameDock,
    GrowDock,
    PointInfoDock,
    ScanMetaDock,
    fmt_frame_report,
    fmt_point_rows,
)
from pclabel.ui.import_dialog import ImportDialog, run_import

MODES = sorted(MODE_NAMES)
OVERLAY_MAX = 1_000_000  # selected points drawn as the highlight (a random subset of a larger selection)


class _SelectionResult(QObject):
    done = Signal(object, str, float, object)  # indices, op, seconds, error


class _GrowResult(QObject):
    done = Signal(object, float, object)  # GrowResult, seconds, error


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, taxonomy, log_frames: str | None = None):
        super().__init__()
        self.settings = settings
        self.taxonomy = taxonomy
        self.project: Project | None = None
        self._open_t = None
        self._open_reported = False
        self._last_status = 0.0
        self._prev_mode = MODE_INTENSITY
        self.viewport = Viewport(settings.active_memory_bytes, settings.active_point_budget)
        self.setCentralWidget(self.viewport)
        if log_frames:
            self.viewport.start_frame_log(log_frames)

        self.display = DisplayDock(settings.software, settings.active_point_budget, self)
        self.frames_dock = FrameDock(self)
        self.grow = GrowDock(self)
        self.clip = ClipDock(self)
        self.info = PointInfoDock(self)
        self.meta = ScanMetaDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.frames_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.display)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.info)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.clip)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.meta)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.grow)
        self.tabifyDockWidget(self.display, self.clip)
        self.tabifyDockWidget(self.display, self.grow)
        self.tabifyDockWidget(self.info, self.meta)
        self.display.raise_()
        self.info.raise_()

        self.frames: list[HeightFrame] = [Z_FRAME]
        self.active_frame = 0
        self._last_plane = None
        self.selection = np.empty(0, np.int64)
        self._sel_busy = False
        self._sel_result = _SelectionResult()
        self._sel_result.done.connect(self._selection_done)
        self._grow_state = None  # seed, op, base selection, cached cell grid of the last grow
        self._grow_busy = False
        self._grow_pending = False
        self._grow_result = _GrowResult()
        self._grow_result.done.connect(self._grow_done)
        self._grow_timer = QTimer(self)
        self._grow_timer.setSingleShot(True)
        self._grow_timer.setInterval(350)
        self._grow_timer.timeout.connect(self.run_grow)
        self.selection_def = None  # how the current selection was made (tool, parameters): recorded by M3

        self.s_cursor, self.s_range, self.s_frame, self.s_gl = QLabel(), QLabel(), QLabel(), QLabel()
        self.s_view = QLabel()
        for w in (self.s_cursor, self.s_range):
            self.statusBar().addWidget(w)
        self.statusBar().addPermanentWidget(self.s_view)
        self.statusBar().addPermanentWidget(self.s_frame)
        self.statusBar().addPermanentWidget(self.s_gl)

        self.display.changed.connect(self._apply_display)
        self.display.budget_changed.connect(self.viewport.set_point_budget)
        self.display.auto_height.connect(self.auto_height)
        self.display.auto_intensity.connect(self.auto_intensity)
        self.clip.changed.connect(self._apply_display)
        self.clip.from_view.connect(self.clip_from_view)
        self.clip.fit_scene.connect(self.clip_scene)
        self.viewport.picked.connect(self._on_pick)
        self.viewport.hovered.connect(self._on_hover)
        self.viewport.frame.connect(self._on_frame)
        self.viewport.renderer_ready.connect(self._on_renderer_ready)
        self.viewport.shape_done.connect(self._on_shape)
        self.viewport.tool_changed.connect(self.frames_dock.show_tool)
        self.frames_dock.slice_changed.connect(self._apply_display)
        self.frames_dock.frame_chosen.connect(self.activate_frame)
        self.frames_dock.fit_requested.connect(self.fit_plane)
        self.frames_dock.clear_requested.connect(self.clear_selection)
        self.frames_dock.tool_requested.connect(self.viewport.set_tool)
        self.frames_dock.delete_requested.connect(self.delete_frame)
        self.viewport.seed_picked.connect(self._on_seed)
        self.viewport.view_changed.connect(self.s_view.setText)
        self.grow.params_changed.connect(lambda: self._grow_state is not None and self._grow_timer.start())
        self._build_menus()
        self._update_title()

    # ---- menus and keys ---------------------------------------------------------------------------------

    def _action(self, menu, text, keys, slot, checkable=False):
        a = QAction(text, self)
        if keys:
            a.setShortcuts([QKeySequence(k) for k in (keys if isinstance(keys, (list, tuple)) else [keys])])
            a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        menu.addAction(a)
        self.addAction(a)
        return a

    def _build_menus(self):
        m = self.menuBar().addMenu("&File")
        self._action(m, "Open project...", "Ctrl+O", self.open_dialog)
        self._action(m, "Import scan...", "Ctrl+I", self.import_dialog)
        m.addSeparator()
        self._action(m, "Quit", "Ctrl+Q", self.close)
        m = self.menuBar().addMenu("&View")
        self._action(m, "Next colour mode", "C", lambda: self.cycle_mode(1))
        self._action(m, "Previous colour mode", "Shift+C", lambda: self.cycle_mode(-1))
        self._action(m, "Height wrap on/off", "W", self.toggle_wrap)
        self._action(m, "Wrap period / 1.5", "[", lambda: self.scale_period(1 / 1.5))
        self._action(m, "Wrap period x 1.5", "]", lambda: self.scale_period(1.5))
        self._action(m, "Wrap offset down", ["Shift+[", "{"], lambda: self.shift_h0(-1))
        self._action(m, "Wrap offset up", ["Shift+]", "}"], lambda: self.shift_h0(1))
        m.addSeparator()
        top = self._action(m, "Top view (looking down; again: back)", "T", lambda: self.set_view("top"))
        bottom = self._action(m, "Bottom view (looking up; again: back)", "Shift+T", lambda: self.set_view("bottom"))
        front = self._action(m, "Front view (looking +y)", "F7", lambda: self.set_view("front"))
        side = self._action(m, "Side view (looking -x)", "F8", lambda: self.set_view("side"))
        home = self._action(m, "Reset view (home)", "Home", self.viewport.reset_view)
        ortho = self._action(m, "Orthographic projection", None, self.viewport.set_orthographic, checkable=True)
        self._ortho_action = ortho
        m.addSeparator()
        self._action(m, "Height axis: z / fitted plane", "H", self.toggle_frame)
        self._action(m, "Height slice on/off", "Shift+H", lambda: self.frames_dock.slice_on.toggle())
        self._action(m, "Clip box on/off", "K", lambda: self.clip.on.toggle())
        self._action(m, "Clip box from view", "Shift+K", self.clip_from_view)
        self._action(m, "Eye-dome lighting on/off", "Ctrl+E", lambda: self.display.edl.toggle())
        m = self.menuBar().addMenu("&Select")
        lasso = self._action(m, "Lasso", "L", lambda: self.toggle_tool("lasso"))
        rect = self._action(m, "Rectangle", "R", lambda: self.toggle_tool("rect"))
        grow = self._action(m, "Ground grow (click the ground)", "G", lambda: self.toggle_tool("grow"))
        fit = self._action(m, "Fit plane to selection (height axis)", "F", self.fit_plane)
        self._action(m, "Clear selection / tool / pick marker", "Esc", self.escape)
        m = self.menuBar().addMenu("&Window")
        for d in (self.frames_dock, self.grow, self.display, self.info, self.clip, self.meta):
            m.addAction(d.toggleViewAction())

        tb = QToolBar("View", self)
        tb.setObjectName("toolbar")
        self.addToolBar(tb)
        for a, label in ((home, "Home"), (top, "Top (T)"), (bottom, "Bottom (Shift+T)"), (front, "Front (F7)"),
                         (side, "Side (F8)")):  # fmt: skip
            b = tb.addAction(label, a.trigger)
            b.setToolTip(a.text())
        tb.addSeparator()
        for a, label in ((lasso, "Lasso (L)"), (rect, "Rectangle (R)"), (grow, "Ground grow (G)"),
                         (fit, "Fit plane (F)")):  # fmt: skip
            b = tb.addAction(label, a.trigger)
            b.setToolTip(a.text())
        tb.addAction("Slice", lambda: self.frames_dock.slice_on.toggle())

    # ---- project ----------------------------------------------------------------------------------------

    def open_dialog(self):
        path = QFileDialog.getExistingDirectory(self, "Open project", self.settings.projects_dir)
        if path:
            self.open_project(path)

    def import_dialog(self):
        dlg = ImportDialog(self.settings.projects_dir, self)
        if dlg.exec():
            run_import(self, dlg.source.text(), dlg.name.text(), dlg.projects.text(), self._import_done)

    def _import_done(self, path, err):
        if err == "cancelled":
            self.statusBar().showMessage("import cancelled; nothing was written", 8000)
        elif err:
            QMessageBox.critical(self, "Import failed", str(err))
        else:
            self.open_project(path)

    def open_project(self, path):
        try:
            p = Project(path)
        except Exception as e:
            QMessageBox.critical(self, "Open project", f"{path}\n\n{e}")
            return
        if self.project is not None:
            self.project.close()
        self.project = p
        self._open_t = time.perf_counter()
        self._open_reported = False
        self._scan_ends = np.cumsum([s["n_valid"] for s in p.meta["scans"]])
        self.meta.load(p)
        self._load_frames()
        self.viewport.set_tool(None)
        self.selection = np.empty(0, np.int64)
        self._show_selection_count()
        self.viewport.set_source(PointSource.from_project(p), self.taxonomy.color_table())
        self.auto_height()
        self.auto_intensity()
        self.clip_scene()
        self.info.show_rows([("", "click a point in the view")])
        self._apply_display()
        self._update_title()

    def _update_title(self):
        gl = "software GL (llvmpipe, CPU)" if self.settings.software else "GPU"
        name = self.project.name if self.project else "no project (File > Open or Import)"
        self.setWindowTitle(f"PCLabel - {name} - {gl}")
        self.s_gl.setText(gl)

    # ---- display ----------------------------------------------------------------------------------------

    def _on_renderer_ready(self):
        self.clip_scene()
        self._apply_display()

    def _apply_display(self):
        r = self.viewport.renderer
        if r is None:
            return
        self.display.apply(r.display)
        self.clip.apply(r.display)
        self.frames_dock.apply(r.display)
        f = self.frames[self.active_frame]
        r.display.height_normal, r.display.height_origin = f.normal, f.origin
        self.viewport.update()

    def cycle_mode(self, step):
        i = MODES.index(self.display.mode.currentData())
        self.display.set_mode(MODES[(i + step) % len(MODES)])

    def toggle_wrap(self):
        cur = self.display.mode.currentData()
        if cur in (MODE_WRAP, MODE_WRAP_SHADED):
            self.display.set_mode(self._prev_mode)
        else:
            self._prev_mode = cur
            self.display.set_mode(MODE_WRAP)

    def scale_period(self, f):
        self.display.period.setValue(self.display.period.value() * f)

    def shift_h0(self, sign):
        self.display.h0.setValue(self.display.h0.value() + sign * self.display.period.value() / 8)

    def _sample(self, per_tile: int = 4000) -> np.ndarray:
        """Store indices of a uniform sample: each tile's first points (tiles are shuffled inside)."""
        t = self.project.tiles
        return np.concatenate([np.arange(int(s), int(s) + min(int(c), per_tile)) for s, c in zip(t["start"], t["count"])])

    def auto_height(self):
        """Height ramp over the 1st..99th percentile of z."""
        p = self.project
        if p is None:
            return
        z = np.asarray(p.xyz[self._sample(), 2], np.float64) + p.offset[2]
        lo, hi = np.percentile(z, [1, 99])
        self.display.hmin.setValue(lo)
        self.display.hmax.setValue(hi)

    def auto_intensity(self):
        """Stretch the 1st..99th percentile of intensity to black..white (the scans use well under half the
        stored range)."""
        p = self.project
        if p is None or p.intensity is None:
            return
        v = np.asarray(p.intensity[self._sample()], np.float64) / 65535
        lo, hi = np.percentile(v, [1, 99])
        self.display.imin.setValue(lo)
        self.display.imax.setValue(max(hi, lo + 1e-3))

    def set_view(self, name):
        self.viewport.set_view(name)

    def clip_scene(self):
        r = self.viewport.renderer
        if r is None:
            return
        off = r.src.offset
        self.clip.set_box(r.scene_lo + off - 0.01, r.scene_hi + off + 0.01)

    def clip_from_view(self):
        r = self.viewport.renderer
        if r is None:
            return
        cam, off = self.viewport.camera, r.src.offset
        if self.viewport.view_name in ("top", "bottom"):
            w, h = self.viewport.physical_size()
            half = np.array([cam.ortho_height() * w / h, cam.ortho_height()]) / 2
            corners = []
            rr, uu, _ = cam.axes()
            for sx in (-1, 1):
                for sy in (-1, 1):
                    corners.append(cam.target[:2] + sx * half[0] * rr[:2] + sy * half[1] * uu[:2])
            corners = np.array(corners)
            lo = np.r_[corners.min(0), r.scene_lo[2]]
            hi = np.r_[corners.max(0), r.scene_hi[2]]
        else:
            s = max(cam.distance / 2, 1.0)
            lo, hi = cam.target - s, cam.target + s
        self.clip.set_box(lo + off, hi + off)
        self.clip.on.setChecked(True)

    # ---- viewport signals -------------------------------------------------------------------------------

    def clear_pick(self):
        if self.viewport.renderer is not None:
            self.viewport.renderer.marker = None
            self.viewport.update()

    # ---- selection and height frames --------------------------------------------------------------------

    def toggle_tool(self, name):
        self.viewport.set_tool(None if self.viewport.tool == name else name)
        if self.viewport.tool == "grow":
            self.grow.show()
            self.grow.raise_()

    def escape(self):
        """Esc: drop the tool, then the selection, then the pick marker."""
        if self.viewport.tool is not None:
            self.viewport.set_tool(None)
        elif self.selection.size:
            self.clear_selection()
        else:
            self.clear_pick()

    def clear_selection(self):
        self.selection = np.empty(0, np.int64)
        self._grow_state = None
        self.selection_def = None
        self._set_overlay()
        self._show_selection_count()

    def _show_selection_count(self, seconds=None):
        n = self.selection.size
        t = f" ({seconds:.2f} s)" if seconds is not None else ""
        self.frames_dock.sel_label.setText(f"{n:,} points selected{t}" if n else
                                           "nothing selected. Shift adds, Ctrl subtracts.")  # fmt: skip

    def _set_overlay(self):
        vp = self.viewport
        if vp.renderer is None:
            return
        sel = self.selection
        if sel.size > OVERLAY_MAX:
            sel = np.sort(np.random.default_rng(0).choice(sel, OVERLAY_MAX, replace=False))
        vp.makeCurrent()
        try:
            vp.renderer.set_selection(np.asarray(self.project.xyz[sel]) if sel.size else None)
        finally:
            vp.doneCurrent()
        vp.update()

    def _filters(self) -> SelectFilters:
        """What is hidden is not selected: the clip box and the height slice, in local coordinates."""
        d, off = self.viewport.renderer.display, self.project.offset
        kw = {}
        if d.clip_on:
            kw.update(clip_min=tuple(np.asarray(d.clip_min) - off), clip_max=tuple(np.asarray(d.clip_max) - off))
        if d.band_on:
            kw.update(band_normal=tuple(d.height_normal), band_origin=tuple(np.asarray(d.height_origin) - off),
                      band=tuple(d.band))  # fmt: skip
        return SelectFilters(**kw)

    def _on_shape(self, polygon, op):
        vp, r = self.viewport, self.viewport.renderer
        if r is None or r.last_mvp is None or self.project is None:
            return
        if self._sel_busy:
            self.statusBar().showMessage("still selecting...", 3000)
            return
        vp.makeCurrent()
        try:
            depth = r.read_depth()
        finally:
            vp.doneCurrent()
        cam = vp.camera
        test = DepthTest(depth, cam.near, cam.far, ortho=cam.projection == ORTHO)
        args = (self.project.xyz, self.project.tiles, r.last_mvp.copy(), r.last_viewport, polygon, test,
                self._filters())  # fmt: skip
        self._sel_busy = True
        self.frames_dock.sel_label.setText("selecting...")

        def work():
            t0 = time.perf_counter()
            try:
                idx = select_visible(*args)
                self._sel_result.done.emit(idx, op, time.perf_counter() - t0, None)
            except Exception as e:  # reported in the UI thread
                self._sel_result.done.emit(None, op, time.perf_counter() - t0, e)

        threading.Thread(target=work, daemon=True, name="select").start()

    def _selection_done(self, idx, op, seconds, err):
        self._sel_busy = False
        if err is not None:
            QMessageBox.critical(self, "Selection failed", str(err))
            return
        if op == "add":
            self.selection = np.union1d(self.selection, idx)
        elif op == "subtract":
            self.selection = np.setdiff1d(self.selection, idx, assume_unique=True)
        else:
            self.selection = idx
        self._grow_state = None  # a shape changed the selection: parameter changes no longer re-grow
        self.selection_def = {"tool": "select_visible", "op": op}
        self._set_overlay()
        self._show_selection_count(seconds)

    # ---- ground grow ----------------------------------------------------------------------------------

    def _on_seed(self, p_local, op):
        if self.project is None:
            return
        base = self.selection.copy() if op in ("add", "subtract") else np.empty(0, np.int64)
        self._grow_state = {"seed": np.asarray(p_local, np.float64), "op": op, "base": base, "grid": None,
                            "grid_key": None}  # fmt: skip
        self.run_grow()

    def _clip_local(self):
        d, off = self.viewport.renderer.display, self.project.offset
        if not d.clip_on:
            return None
        return np.asarray(d.clip_min) - off, np.asarray(d.clip_max) - off

    def run_grow(self):
        st = self._grow_state
        if st is None or self.project is None or self.viewport.renderer is None:
            return
        if self._grow_busy:
            self._grow_pending = True
            return
        params = self.grow.params()
        clip = self._clip_local()
        key = (tuple(st["seed"]), params.cell, params.radius, params.surface_band, params.sample_density,
               None if clip is None else (tuple(clip[0]), tuple(clip[1])))  # fmt: skip
        grid = st["grid"] if st["grid_key"] == key else None
        st["want_key"] = key
        self._grow_busy = True
        self.grow.result.setText("growing...")
        args = (self.project.xyz, self.project.intensity, self.project.tiles, st["seed"], params)

        def work():
            t0 = time.perf_counter()
            try:
                res = ground_grow(*args, grid=grid, clip=clip)
                self._grow_result.done.emit(res, time.perf_counter() - t0, None)
            except Exception as e:  # reported in the UI thread
                self._grow_result.done.emit(None, time.perf_counter() - t0, e)

        threading.Thread(target=work, daemon=True, name="grow").start()

    def _grow_done(self, res, seconds, err):
        self._grow_busy = False
        st = self._grow_state
        if st is None:
            return
        if err is not None:
            self.grow.result.setText(f"no grow: {err}")
        else:
            st["grid"], st["grid_key"] = res.grid, st["want_key"]
            if st["op"] == "add":
                self.selection = np.union1d(st["base"], res.indices)
            elif st["op"] == "subtract":
                self.selection = np.setdiff1d(st["base"], res.indices, assume_unique=True)
            else:
                self.selection = res.indices
            i = res.info
            seed_w = st["seed"] + self.project.offset
            self.selection_def = {"tool": "ground_grow", "op": st["op"], "seed": seed_w.tolist(), "params": i["params"],
                                  "result": {k: v for k, v in i.items() if k != "params"}}  # fmt: skip
            edge = f"\nreached the {i['params']['radius']:.0f} m reach: raise it to grow further" if i["reached_edge"] else ""
            self.grow.result.setText(
                f"seed intensity {i['seed_intensity']:.3f}, height {i['seed_height_local'] + self.project.offset[2]:.3f} m\n"
                f"{i['points']:,} points on {i['area_m2']:.1f} m² ({i['cells_grown']:,} cells grown, "
                f"{i['cells_bridged']:,} bridged as markings){edge}\n"
                f"{seconds:.2f} s (cells from {res.grid.sample_points:,} sampled points)")  # fmt: skip
            self._set_overlay()
            self._show_selection_count(seconds)
        if self._grow_pending:
            self._grow_pending = False
            self.run_grow()

    def _load_frames(self):
        frames, active = self.project.load_frames()
        self.frames = [Z_FRAME] + [HeightFrame.from_json(f) for f in frames]
        names = [f.name for f in self.frames]
        self.active_frame = names.index(active) if active in names else 0
        self._last_plane = self.active_frame if self.active_frame > 0 else (len(self.frames) - 1 or None)
        self._show_frames()

    def _show_frames(self):
        self.frames_dock.set_frames([f.name for f in self.frames], self.active_frame)
        self.frames_dock.report.setText(fmt_frame_report(self.frames[self.active_frame]))

    def _save_frames(self):
        if self.project is not None:
            active = self.frames[self.active_frame].name if self.active_frame > 0 else None
            self.project.save_frames([f.to_json() for f in self.frames[1:]], active)

    def activate_frame(self, i: int):
        self.active_frame = int(i)
        if i > 0:
            self._last_plane = i
            # bands start at the plane; the height ramp spans the slice range
            self.display.h0.setValue(0.0)
            self.display.hmin.setValue(self.frames_dock.slice_a.value() / 100)
            self.display.hmax.setValue(self.frames_dock.slice_b.value() / 100)
        else:
            self.auto_height()
        self._show_frames()
        self._save_frames()
        self._apply_display()

    def delete_frame(self):
        i = self.active_frame
        if i == 0:
            self.statusBar().showMessage("z (the scan frame) cannot be deleted", 4000)
            return
        name = self.frames[i].name
        del self.frames[i]
        self._last_plane = (len(self.frames) - 1) or None
        self.activate_frame(0)
        self.statusBar().showMessage(f"deleted {name}", 4000)

    def toggle_frame(self):
        if self.active_frame > 0:
            self.activate_frame(0)
        elif self._last_plane:
            self.activate_frame(self._last_plane)
        else:
            self.statusBar().showMessage("no fitted plane yet: select a patch (L or R), then F", 5000)

    def fit_plane(self):
        if self.project is None:
            return
        if self.selection.size < 3:
            self.statusBar().showMessage("select a patch first (L lasso or R rectangle), then F", 5000)
            return
        params = PlaneFitParams()
        sel = self.selection
        if sel.size > params.max_points:
            sel = np.sort(np.random.default_rng(params.seed).choice(sel, params.max_points, replace=False))
        pts = np.asarray(self.project.xyz[sel], np.float64) + self.project.offset
        names = {f.name for f in self.frames}
        k = 1
        while f"plane {k}" in names:
            k += 1
        frame = frame_from_points(pts, f"plane {k}", params)
        for i, f in enumerate(self.frames[1:], start=1):  # the same selection again: reuse its plane
            if np.allclose(f.normal, frame.normal, atol=1e-12) and np.allclose(f.origin, frame.origin, atol=1e-9):
                self.activate_frame(i)
                self.statusBar().showMessage(f"same plane as {f.name}", 5000)
                return
        frame.report["points_selected"] = int(self.selection.size)
        frame.report["created"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.frames.append(frame)
        self.activate_frame(len(self.frames) - 1)
        self.statusBar().showMessage(f"{frame.name}: " + fmt_frame_report(frame).replace("\n", "; "), 15000)

    def _scan_of_source(self, i: int) -> int:
        return int(np.searchsorted(self._scan_ends, i, side="right"))

    def _on_pick(self, res):
        if res is None or self.project is None:
            self.info.show_rows([("", "no point under the cursor")])
            return
        pick, _ = res
        self.info.show_rows(fmt_point_rows(self.project, self.taxonomy, pick, self._scan_of_source))
        self.info.raise_()

    def _on_hover(self, p):
        if p is None or self.project is None:
            self.s_cursor.setText("")
            self.s_range.setText("")
            return
        text = f"x {p[0]:.3f}  y {p[1]:.3f}  z {p[2]:.3f} m"
        if self.active_frame > 0:
            f = self.frames[self.active_frame]
            text += f"   h {float(np.dot(p - np.asarray(f.origin), f.normal)):+.3f} m ({f.name})"
        self.s_cursor.setText(text)
        if self.project.static_capture:
            o = self.project.scan_origins + self.project.offset
            d = np.linalg.norm(o - p, axis=1).min()
            self.s_range.setText(f"range {d:.2f} m")

    def _on_frame(self, item):
        stats, wall = item
        vp = self.viewport
        if not self._open_reported and self._open_t is not None and vp.first_converged_s is not None:
            self._open_reported = True
            msg = (f"opened {self.project.name}: first frame {vp.first_frame_s:.2f} s, "
                   f"all wanted pages {vp.first_converged_s:.2f} s after the viewport got the data")  # fmt: skip
            print(msg, flush=True)
            self.statusBar().showMessage(msg, 10000)
        now = time.perf_counter()
        if now - self._last_status < 0.25:
            return
        self._last_status = now
        self.s_view.setText(vp.view_label())
        fps = f"{1 / wall:5.1f} fps" if wall == wall and wall > 0 else "     fps"
        stream = "" if stats.converged else f"  streaming {stats.inflight} pages"
        self.s_frame.setText(f"{fps}  drawn {stats.drawn / 1e6:.2f}M  resident {stats.resident_bytes / 2**20:,.0f} MiB"
                             f"{stream}")  # fmt: skip

    def closeEvent(self, e):
        self.meta.save()
        self.viewport.close_frame_log()
        super().closeEvent(e)
