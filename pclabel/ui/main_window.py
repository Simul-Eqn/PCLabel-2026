"""Main window: viewport, docks, menus, keymap (SPEC §1, §15) and status bar."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QFileDialog, QLabel, QMainWindow, QMessageBox, QToolBar

from pclabel.analysis.frames import Z_FRAME, HeightFrame, PlaneFitParams, frame_from_points, plane_document
from pclabel.render.camera import ORTHO
from pclabel.render.renderer import PointSource
from pclabel.render.shaders import MODE_INTENSITY, MODE_NAMES, MODE_WRAP, MODE_WRAP_SHADED
from pclabel.render.viewport import Viewport
from pclabel.settings import Settings
from pclabel.store.importer import write_json_atomic
from pclabel.store.project import Project
from pclabel.tools.ground import R_GAP, R_HEIGHT, R_IRANGE, R_ISTEP, R_ROUGH, ground_grow
from pclabel.tools.magic import magic_select
from pclabel.tools.normals import NormalGrowResult, normal_grow
from pclabel.store.annotations import isin_sorted
from pclabel.tools import sets
from pclabel.tools.selection import DepthTest, SelectFilters, select_visible
from pclabel.tools.volume import select_loop, select_volume
from pclabel.ui.docks import (
    ClipDock,
    DisplayDock,
    FrameDock,
    GrowDock,
    MagicDock,
    PointInfoDock,
    ScanMetaDock,
    fmt_frame_report,
    fmt_point_rows,
)
from pclabel.ui.annotate import AnnotationController
from pclabel.ui.cleanup import CleanController
from pclabel.ui.split import SplitController
from pclabel.ui.volume import VolumeController
from pclabel.ui.import_dialog import ImportDialog, run_import
from pclabel.ui.undo import UndoStack

MODES = sorted(MODE_NAMES)


class _SelectionResult(QObject):
    done = Signal(object, str, float, object, object)  # indices, op, seconds, error, definition (tool, parameters)


class _GrowResult(QObject):
    done = Signal(object, object, float, object)  # seed tool state, result, seconds, error


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
        self.magic = MagicDock(self)
        self.clip = ClipDock(self)
        self.info = PointInfoDock(self)
        self.meta = ScanMetaDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.frames_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.display)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.info)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.clip)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.meta)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.grow)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.magic)
        self.tabifyDockWidget(self.display, self.clip)
        self.tabifyDockWidget(self.display, self.grow)
        self.tabifyDockWidget(self.display, self.magic)
        self.tabifyDockWidget(self.info, self.meta)
        self.annot = AnnotationController(self)  # segments, curves, groups (needs the viewport)
        self.volume = VolumeController(self)  # 3D select
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.volume.dock)
        self.tabifyDockWidget(self.display, self.volume.dock)
        self.split = SplitController(self)  # split a point set by roughness
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.split.dock)
        self.tabifyDockWidget(self.display, self.split.dock)
        self.cleanup = CleanController(self)  # clean up the selection by its footprint
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.cleanup.dock)
        self.tabifyDockWidget(self.display, self.cleanup.dock)
        self._locked = (None, np.empty(0, np.int64))  # (annotations version, store indices that cannot be selected)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.annot.dock)
        self.tabifyDockWidget(self.frames_dock, self.annot.dock)
        self.frames_dock.raise_()
        self.display.raise_()
        self.info.raise_()

        self.frames: list[HeightFrame] = [Z_FRAME]
        self.active_frame = 0
        self._last_plane = None
        self.selection = np.empty(0, np.int64)
        self._sel_busy = False
        self._sel_result = _SelectionResult()
        self._sel_result.done.connect(self._selection_done)
        self._grow_state = None  # the last seed tool run: tool, seed, op, base selection, cached cell grid
        self._magic_image = None  # magic select's intensity picture, reused by later clicks
        self._normal_voxels = None  # grow select's geometric voxels and normals, reused by later clicks
        self._grow_busy = False
        self._grow_pending = False
        self._grow_result = _GrowResult()
        self._grow_result.done.connect(self._grow_done)
        self._grow_timer = QTimer(self)
        self._grow_timer.setSingleShot(True)
        self._grow_timer.setInterval(350)
        self._grow_timer.timeout.connect(self.run_grow)
        self.selection_def = None  # how the current selection was made (tool, parameters): recorded by M3
        self.selection_ops: list[dict] = []  # every operation since the last plain replace (for groups)
        self.history = UndoStack()  # Ctrl+Z / Ctrl+Y: the selection, planes and annotations (this session)

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
        self.clip.changed.connect(lambda: self.volume.draw_clip())
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
        self.frames_dock.depth_mode_chosen.connect(lambda m: self.set_select_through(m == "through"))
        self.frames_dock.delete_requested.connect(self.delete_frame)
        self.frames_dock.export_requested.connect(lambda: self.export_plane())
        self.viewport.seed_picked.connect(self._on_seed)
        self.viewport.view_changed.connect(self.s_view.setText)
        self.grow.params_changed.connect(lambda: self._params_changed("grow"))
        self.magic.params_changed.connect(lambda: self._params_changed("magic"))
        self.grow.show_stops.toggled.connect(lambda _: self._show_stops())
        self.magic.show_stops.toggled.connect(lambda _: self._show_stops())
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
        m = self.menuBar().addMenu("&Edit")
        self._undo_action = self._action(m, "Undo", "Ctrl+Z", self.undo)
        self._redo_action = self._action(m, "Redo", ["Ctrl+Y", "Ctrl+Shift+Z"], self.redo)
        self._update_undo_actions()
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
        self._through_action = self._action(m, "Lasso and rectangle select hidden points too", "O",
                                            self.set_select_through, checkable=True)  # fmt: skip
        grow = self._action(m, "Grow select (click a surface)", "G", lambda: self.toggle_tool("grow"))
        magic = self._action(m, "Magic select by intensity (click a marking)", "I", lambda: self.toggle_tool("magic"))
        volume = self._action(m, "3D select: box or cylinder (click to place, Enter selects)", "U",
                              lambda: self.toggle_tool("volume"))  # fmt: skip
        fit = self._action(m, "Fit plane to selection (height axis)", "F", self.fit_plane)
        m.addSeparator()
        seg = self._action(m, "Line segment (click two points)", "V", lambda: self.toggle_tool("segment"))
        curve = self._action(m, "Curve (click points in order, Enter)", "Shift+V", lambda: self.toggle_tool("curve"))
        group = self._action(m, "Point set from the selection", "Ctrl+G", lambda: self.annot.points_from_selection())
        self._action(m, "Clean up the selection (footprint)...", None, lambda: self.cleanup.open())
        self._locks_action = self._action(m, "Locks on (locked labels cannot be selected)", None,
                                          lambda on: self.annot.set_locks_on(on), checkable=True)  # fmt: skip
        self._locks_action.setChecked(True)
        m.addSeparator()
        self._action(m, "Clear selection / tool / pick marker", "Esc", self.escape)
        m = self.menuBar().addMenu("&Window")
        for d in (self.frames_dock, self.annot.dock, self.grow, self.magic, self.volume.dock, self.split.dock,
                  self.cleanup.dock,
                  self.display, self.info,
                  self.clip,
                  self.meta):
            m.addAction(d.toggleViewAction())

        tb = QToolBar("View", self)
        tb.setObjectName("toolbar")
        self.addToolBar(tb)
        for a, label in ((home, "Home"), (top, "Top (T)"), (bottom, "Bottom (Shift+T)"), (front, "Front (F7)"),
                         (side, "Side (F8)")):  # fmt: skip
            b = tb.addAction(label, a.trigger)
            b.setToolTip(a.text())
        tb.addSeparator()
        for a, label in ((lasso, "Lasso (L)"), (rect, "Rectangle (R)"), (grow, "Grow select (G)"),
                         (magic, "Magic select (I)"), (volume, "3D select (U)"), (fit, "Fit plane (F)"),
                         (seg, "Segment (V)"),
                         (curve, "Curve (Shift+V)"), (group, "Points (Ctrl+G)")):  # fmt: skip
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
        self._grow_state = None
        self._magic_image = None
        self._normal_voxels = None
        self.history.clear()
        self._update_undo_actions()
        self._show_selection_count()
        self.viewport.set_source(PointSource.from_project(p), self.taxonomy.color_table())
        self.selection_ops = []
        self.annot.set_project(p)
        self.volume.set_project()
        self.split.cancel()
        self.cleanup.cancel()
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
        self.annot.refresh()
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
        if self.viewport.tool in ("grow", "magic", "segment", "curve", "volume"):
            t = self.viewport.tool
            dock = self.annot.dock if t in ("segment", "curve") else self.volume.dock if t == "volume" else \
                self._seed_dock(t)  # fmt: skip
            dock.show()
            dock.raise_()

    def escape(self):
        """Esc: end curve editing or drop the points of an unfinished segment or curve, else drop the tool, then
        the selection, then the pick marker."""
        if self.annot.escape():
            return
        if self.viewport.tool is not None:
            self.viewport.set_tool(None)
        elif self.selection.size:
            self.clear_selection()
        else:
            self.clear_pick()

    def clear_selection(self):
        if self.selection.size:
            self.checkpoint("selection", "clear the selection")
        self.selection = np.empty(0, np.int64)
        self._grow_state = None
        self.selection_def = None
        self.selection_ops = []
        self._set_overlay()
        self._show_selection_count()

    def set_selection(self, indices, definition: dict, filtered: bool = True):
        """Replace the selection with these sorted store indices (e.g. a group's point sets). ``filtered``: what the
        clips and the slice hide, and locked points, are left out."""
        self.checkpoint("selection", f"select ({definition.get('tool', '?')})")
        indices = np.asarray(indices, np.int64)
        if filtered and self.viewport.renderer is not None and indices.size:
            indices = self._filters().apply(self.project.xyz, indices)
        self.selection, dropped = self._unlocked(indices) if filtered else (indices, 0)
        self._grow_state = None
        self.selection_def = definition
        self.selection_ops = [definition]
        self._set_overlay()
        self._show_selection_count(locked_out=dropped)

    def _show_selection_count(self, seconds=None, locked_out: int = 0):
        n = self.selection.size
        t = f" ({seconds:.2f} s)" if seconds is not None else ""
        lk = f"; {locked_out:,} locked points left out" if locked_out else ""
        self.frames_dock.sel_label.setText(f"{n:,} points selected{t}{lk}" if n else
                                           f"nothing selected{lk}. Shift adds, Ctrl subtracts.")  # fmt: skip

    # ---- locks: labels whose points cannot be selected -------------------------------------------------------

    def never_selected(self) -> set:
        """Taxonomy classes marked ``selectable: false`` (``other``, "no class"): always locked."""
        return {c["name"] for c in self.taxonomy.classes if c.get("selectable", True) is False}

    def locked_points(self) -> np.ndarray:
        """Store indices (sorted) of points that cannot be selected: in point sets whose label is locked or never
        selected. Cached until the annotations or the locks change."""
        if self._locked[0] != self.annot.version:
            m = self.annot.model
            self._locked = (self.annot.version, m.points_labelled(m.locked_labels(self.never_selected())))
        return self._locked[1]

    def _unlocked(self, idx):
        """(idx without locked points, how many were left out)."""
        lk = self.locked_points()
        if lk.size == 0 or idx.size == 0:
            return idx, 0
        keep = ~isin_sorted(idx, lk)
        return idx[keep], int(idx.size - keep.sum())

    def _set_overlay(self):
        """Tint exactly the drawn points that are selected (a GPU-side bit; no subsampling)."""
        vp = self.viewport
        if vp.renderer is None:
            return
        vp.makeCurrent()
        try:
            vp.renderer.set_selection(self.selection)
            if self._grow_state is None:
                vp.renderer.set_markers(None)
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
        if d.clip_volume_on and d.clip_volume is not None:
            kw.update(volume=d.clip_volume)
        return SelectFilters(**kw)

    def _visible(self, idx):
        """What the clips (box and 3D select volume) and the slice hide is not selected."""
        if self.viewport.renderer is None or idx.size == 0:
            return idx
        return self._filters().apply(self.project.xyz, idx)

    def allowed_mask(self, idx) -> np.ndarray:
        """Which of these store indices a tool may select: visible (clips, slice) and not locked."""
        idx = np.asarray(idx, np.int64)
        ok = isin_sorted(idx, self._visible(idx)) if idx.size else np.zeros(0, bool)
        lk = self.locked_points()
        if lk.size and idx.size:
            ok &= ~isin_sorted(idx, lk)
        return ok

    def set_volume_clip(self, spec, on: bool = True):
        """Clip to a 3D select volume (a tools.volume.VolumeSpec, local), or turn that clip off (spec None)."""
        r = self.viewport.renderer
        if r is None:
            return
        r.display.clip_volume = spec
        c = self.clip
        c.volume_on.blockSignals(True)
        c.volume_on.setEnabled(spec is not None)
        c.volume_on.setChecked(bool(on and spec is not None))
        c.volume_on.blockSignals(False)
        c.volume_label.setText(spec.label if spec is not None else "no volume")
        self._apply_display()
        self.volume.draw_clip()

    def _on_shape(self, polygon, op):
        vp, r = self.viewport, self.viewport.renderer
        if r is None or r.last_mvp is None or self.project is None:
            return
        if self._sel_busy:
            self.statusBar().showMessage("still selecting...", 3000)
            return
        test = None  # through: every point inside the shape
        if not vp.select_through:  # visible: test against the drawn surface
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
        shape = "lasso" if self.viewport.tool == "lasso" else "rectangle"
        depth = "through" if self.viewport.select_through else "visible"
        self._run_selection(lambda: select_visible(*args), op,
                            {"tool": "select_shape", "shape": shape, "depth": depth, "op": op})  # fmt: skip

    def set_select_through(self, on: bool):
        """Lasso and rectangle: every point inside the shape (through), or only the visible ones."""
        self.viewport.select_through = bool(on)
        self._through_action.setChecked(bool(on))
        d = self.frames_dock.depth_mode
        d.setCurrentIndex(d.findData("through" if on else "visible"))
        self.statusBar().showMessage("lasso / rectangle: " + ("hidden points too (through)" if on else
                                                              "only visible points"), 4000)  # fmt: skip

    def select_volume(self, base_local, axis, along, params, op: str, frame_name: str):
        """3D select: the points inside a box or cylinder along the height axis (in a worker)."""
        if self.project is None:
            return
        if self._sel_busy:
            self.statusBar().showMessage("still selecting...", 3000)
            return
        from dataclasses import asdict

        args = (self.project.xyz, self.project.tiles, np.asarray(base_local, np.float64), axis, along, params,
                self._filters())  # fmt: skip
        definition = {"tool": "select_volume", "op": op, "base": (np.asarray(base_local) + self.project.offset).tolist(),
                      "height_axis": frame_name, "axis": [float(v) for v in axis], "params": asdict(params)}  # fmt: skip
        self._sel_busy = True
        self.frames_dock.sel_label.setText("selecting...")
        self._run_selection(lambda: select_volume(*args), op, definition)

    def select_loop(self, origin_local, normal, u, poly2d, bottom, top, inset, op: str, definition: dict):
        """Extrude select: the points inside a closed curve swept along its plane's normal (in a worker)."""
        if self.project is None:
            return
        if self._sel_busy:
            self.statusBar().showMessage("still selecting...", 3000)
            return
        args = (self.project.xyz, self.project.tiles, np.asarray(origin_local, np.float64), normal, u, poly2d,
                bottom, top, inset, self._filters())  # fmt: skip
        self._sel_busy = True
        self.frames_dock.sel_label.setText("selecting...")
        self._run_selection(lambda: select_loop(*args), op, definition)

    def _run_selection(self, fn, op, definition):
        def work():
            t0 = time.perf_counter()
            try:
                self._sel_result.done.emit(fn(), op, time.perf_counter() - t0, None, definition)
            except Exception as e:  # reported in the UI thread
                self._sel_result.done.emit(None, op, time.perf_counter() - t0, e, definition)

        threading.Thread(target=work, daemon=True, name="select").start()

    def _selection_done(self, idx, op, seconds, err, definition):
        self._sel_busy = False
        if err is not None:
            QMessageBox.critical(self, "Selection failed", str(err))
            return
        names = {"select_shape": f"{definition.get('shape', 'shape')} selection, {definition.get('depth', '')}",
                 "select_volume": "extrude select" if definition.get("shape") == "loop" else "3D select"}  # fmt: skip
        self.checkpoint("selection", f"{names.get(definition['tool'], definition['tool'])} ({op})")
        dropped = 0
        if op != "subtract":
            idx, dropped = self._unlocked(idx)
        if op == "add":
            self.selection = sets.union(self.selection, idx)
        elif op == "subtract":
            self.selection = sets.difference(self.selection, idx)
        else:
            self.selection = idx
        self._grow_state = None  # a shape changed the selection: parameter changes no longer re-grow
        self.selection_def = dict(definition, points=int(idx.size))
        self.selection_ops = ([] if op == "replace" else self.selection_ops) + [self.selection_def]
        self._set_overlay()
        self._show_selection_count(seconds, dropped)

    # ---- seed tools: grow select (G: height and intensity, or geometric), magic select (I) ------------------

    def _on_seed(self, p_local, op):
        tool = self.viewport.tool
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
        vp.makeCurrent()
        try:
            vp.renderer.set_markers(pts, rgb)
        finally:
            vp.doneCurrent()
        vp.update()

    def _clip_local(self):
        d, off = self.viewport.renderer.display, self.project.offset
        if not d.clip_on:
            return None
        return np.asarray(d.clip_min) - off, np.asarray(d.clip_max) - off

    def run_grow(self):
        """Run the active seed tool from the current seed in a worker (again when a threshold changes)."""
        st = self._grow_state
        if st is None or self.project is None or self.viewport.renderer is None:
            return
        if self._grow_busy:
            self._grow_pending = True
            return
        clip = self._clip_local()
        dock = self._seed_dock(st["tool"])
        params = dock.params()
        pr = self.project
        st["mode"] = "magic" if st["tool"] == "magic" else self.grow.current_mode()
        if st["tool"] == "magic":
            image = self._magic_image

            def run():
                return magic_select(pr.xyz, pr.intensity, pr.tiles, st["seed"], params, image=image, clip=clip)
        elif st["mode"] == "normals":
            voxels = self._normal_voxels

            def run():
                return normal_grow(pr.xyz, pr.tiles, st["seed"], params, voxels=voxels, clip=clip)
        else:
            key = (tuple(st["seed"]), params.cell, params.radius, params.surface_band, params.cell_points,
                   None if clip is None else (tuple(clip[0]), tuple(clip[1])))  # fmt: skip
            grid = st["grid"] if st["grid_key"] == key else None
            st["want_key"] = key

            def run():
                return ground_grow(pr.xyz, pr.intensity, pr.tiles, st["seed"], params, grid=grid, clip=clip)

        self._grow_busy = True
        dock.result.setText("working...")

        def work():
            t0 = time.perf_counter()
            try:
                self._grow_result.done.emit(st, run(), time.perf_counter() - t0, None)
            except Exception as e:  # reported in the UI thread
                self._grow_result.done.emit(st, None, time.perf_counter() - t0, e)

        threading.Thread(target=work, daemon=True, name=st["tool"]).start()

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
                found = self._visible(res.indices)
                found, dropped = (found, 0) if st["op"] == "subtract" else self._unlocked(found)
                if st["op"] == "add":
                    self.selection = sets.union(st["base"], found)
                elif st["op"] == "subtract":
                    self.selection = sets.difference(st["base"], found)
                else:
                    self.selection = found
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

    def export_plane(self, path=None):
        """Save the plane shown in the Height axis panel (the active one) as JSON: its equation and fit."""
        i = self.active_frame
        if self.project is None or i == 0:
            self.statusBar().showMessage("choose a fitted plane first (z is the scan frame)", 4000)
            return None
        f = self.frames[i]
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Export plane", f"{self.project.name} {f.name}.json",
                                                  "Plane (*.json)")  # fmt: skip
            if not path:
                return None
        src = self.project.meta.get("source", {})
        source = {"file": Path(src.get("path", "")).name, "sha256": src.get("sha256"), "n_valid": self.project.n}
        write_json_atomic(Path(path), plane_document(f, self.project.offset, source))
        self.statusBar().showMessage(f"exported {f.name} to {path}", 6000)
        return path

    def delete_frame(self):
        i = self.active_frame
        if i == 0:
            self.statusBar().showMessage("z (the scan frame) cannot be deleted", 4000)
            return
        name = self.frames[i].name
        self.checkpoint("frames", f"delete {name}")
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
        self.checkpoint("frames", f"fit {frame.name}")
        self.frames.append(frame)
        self.activate_frame(len(self.frames) - 1)
        self.statusBar().showMessage(f"{frame.name}: " + fmt_frame_report(frame).replace("\n", "; "), 15000)

    # ---- undo and redo --------------------------------------------------------------------------------------

    def _capture(self, kind: str):
        """(snapshot, bytes held) of one kind of state."""
        if kind == "selection":
            return (self.selection, self.selection_def, list(self.selection_ops)), int(self.selection.nbytes)
        if kind == "frames":
            return (list(self.frames), self.active_frame, self._last_plane), 0
        if kind == "annotations":
            return self.annot.snapshot(), 0
        raise ValueError(kind)

    def _restore(self, kind: str, state):
        if kind == "selection":
            self.selection, self.selection_def, ops = state
            self.selection_ops = list(ops)
            self._grow_state = None  # threshold changes must not re-run a click that was undone
            self._set_overlay()
            self._show_selection_count()
        elif kind == "frames":
            frames, active, last = state
            self.frames = list(frames)
            self._last_plane = last
            self.activate_frame(min(active, len(self.frames) - 1))
        else:
            self.annot.restore(state)

    def checkpoint(self, kind: str, label: str, before=None):
        """Record the state of ``kind`` before a change (or ``before``, a capture taken earlier)."""
        state, nbytes = before if before is not None else self._capture(kind)
        self.history.push(kind, label, state, nbytes)
        self._update_undo_actions()

    def _busy(self) -> bool:
        if self._sel_busy or self._grow_busy:
            self.statusBar().showMessage("still working: try again in a moment", 3000)
            return True
        return False

    def undo(self):
        if self.annot.pending:  # while drawing a segment or curve: take back the last clicked point
            self.annot.back()
            return
        if self._busy():
            return
        e = self.history.undo(self._capture)
        if e is None:
            self.statusBar().showMessage("nothing to undo", 3000)
            return
        self._restore(e.kind, e.state)
        self._update_undo_actions()
        self.statusBar().showMessage(f"undone: {e.label}", 5000)

    def redo(self):
        if self._busy():
            return
        e = self.history.redo(self._capture)
        if e is None:
            self.statusBar().showMessage("nothing to redo", 3000)
            return
        self._restore(e.kind, e.state)
        self._update_undo_actions()
        self.statusBar().showMessage(f"redone: {e.label}", 5000)

    def _update_undo_actions(self):
        if not hasattr(self, "_undo_action"):
            return
        u, r = self.history.undo_entries, self.history.redo_entries
        self._undo_action.setText(f"Undo {u[-1].label}" if u else "Undo")
        self._redo_action.setText(f"Redo {r[-1].label}" if r else "Redo")

    def _scan_of_source(self, i: int) -> int:
        return int(np.searchsorted(self._scan_ends, i, side="right"))

    def _on_pick(self, res):
        if res is None or self.project is None:
            self.info.show_rows([("", "no point under the cursor")])
            return
        pick, _ = res
        self.info.show_rows(fmt_point_rows(self.project, self.taxonomy, pick, self._scan_of_source))
        if self.viewport.tool in ("segment", "curve"):
            self.annot.add_vertex(pick.index)
        elif self.viewport.tool == "curve_edit":
            self.annot.add_node(pick.index)
        else:
            self.info.raise_()

    def _on_hover(self, p):
        self.annot.hover_at(p)
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
