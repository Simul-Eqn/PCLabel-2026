"""Main window: viewport, docks, menus, keymap (docs/MANUAL.md) and status bar."""

from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
)

from pclabel.analysis.frames import Z_FRAME, HeightFrame
from pclabel.render.renderer import PointSource
from pclabel.render.shaders import MODE_CLASS, MODE_INTENSITY, MODE_NAMES, MODE_WRAP, MODE_WRAP_SHADED
from pclabel.render.viewport import Viewport
from pclabel.settings import Settings
from pclabel.store.project import Project
from pclabel.ui.docks import (
    ClipDock,
    DisplayDock,
    FrameDock,
    GrowDock,
    MagicDock,
    PointInfoDock,
    ScanMetaDock,
    fmt_point_rows,
)
from pclabel.ui.annotate import AnnotationController
from pclabel.ui.cleanup import CleanController
from pclabel.ui.density import DensityController
from pclabel.ui.labels import LabelsController
from pclabel.ui.pieces import PiecesController
from pclabel.ui.trees import TreeController
from pclabel.ui.split import SplitController
from pclabel.ui.volume import VolumeController
from pclabel.ui.import_dialog import ImportDialog, run_import
from pclabel.ui.trace import TraceController
from pclabel.ui.label_import import LabelImportController
from pclabel.ui.height_axes import HeightAxesMixin
from pclabel.ui.menus import build_menus
from pclabel.ui.seed_tools import SeedToolsMixin
from pclabel.ui.selecting import SelectionMixin
from pclabel.ui.undo import UndoMixin

MODES = [k for k in sorted(MODE_NAMES) if k != MODE_CLASS]  # the class mode is for synthetic ground truth


class MainWindow(SelectionMixin, SeedToolsMixin, HeightAxesMixin, UndoMixin, QMainWindow):
    """The window. Its parts: ``selecting`` (the selection, locks and clips), ``seed_tools`` (clicks with grow, magic
    and the other click tools), ``height_axes`` (the fitted planes), ``undo`` and ``menus``."""

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
        self.trees = TreeController(self)  # identify trees by a band of their trunk (in the 3D select panel)
        self.split = SplitController(self)  # split a point set by roughness
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.split.dock)
        self.tabifyDockWidget(self.display, self.split.dock)
        self.cleanup = CleanController(self)  # clean up the selection by its footprint
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.cleanup.dock)
        self.tabifyDockWidget(self.display, self.cleanup.dock)
        self.density = DensityController(self)  # the parts of a point set dense enough to work on
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.density.dock)
        self.tabifyDockWidget(self.display, self.density.dock)
        self.pieces = PiecesController(self)  # keep the connected pieces of the selection that matter
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.pieces.dock)
        self.tabifyDockWidget(self.display, self.pieces.dock)
        self.labels = LabelsController(self)  # the labels view and the label map
        self.label_import = LabelImportController(self)  # File > Import labels (e.g. a DXF's layers)
        self.trace = TraceController(self)  # semi-automatic curb and road-marking lines
        self.trace.fill_labels()
        self.viewport.trace_key.connect(self.trace.key)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.labels.dock)
        self.tabifyDockWidget(self.display, self.labels.dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.trace.dock)
        self.tabifyDockWidget(self.display, self.trace.dock)
        self._locked = (None, np.empty(0, np.int64))  # (annotations version, store indices that cannot be selected)
        self.clip_set = None  # tools.sets.PointMask: the clip to point sets (drawn, picked and worked on alone)
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
        self._init_seed_tools()
        self.selection_def = None  # how the current selection was made (tool, parameters); see selection_ops
        self.selection_ops: list[dict] = []  # every operation since the last plain replace (for groups)
        self._init_undo()

        self.s_cursor, self.s_range, self.s_frame, self.s_gl = QLabel(), QLabel(), QLabel(), QLabel()
        self.s_view = QLabel()
        for w in (self.s_cursor, self.s_range):
            self.statusBar().addWidget(w)
        self.statusBar().addPermanentWidget(self.s_view)
        self.statusBar().addPermanentWidget(self.s_frame)
        self.statusBar().addPermanentWidget(self.s_gl)

        self.display.changed.connect(self._apply_display)
        self.display.changed.connect(self._labels_view_changed)
        self.display.budget_changed.connect(self.viewport.set_point_budget)
        self.display.auto_height.connect(self.auto_height)
        self.display.auto_intensity.connect(self.auto_intensity)
        self.clip.changed.connect(self._apply_display)
        self.clip.changed.connect(lambda: self.volume.draw_clip())
        self.clip.from_view.connect(self.clip_from_view)
        self.clip.fit_scene.connect(self.clip_scene)
        self.clip.use_set.connect(lambda: self.clip_to_selected_sets())
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
        self.frames_dock.rename_requested.connect(lambda: self.rename_frame())
        self.frames_dock.export_requested.connect(lambda: self.export_plane())
        self.viewport.seed_picked.connect(self._on_seed)
        self.viewport.view_changed.connect(self.s_view.setText)
        self.grow.params_changed.connect(lambda: self._params_changed("grow"))
        self.magic.params_changed.connect(lambda: self._params_changed("magic"))
        self.grow.show_stops.toggled.connect(lambda _: self._show_stops())
        self.magic.show_stops.toggled.connect(lambda _: self._show_stops())
        build_menus(self)
        self._update_title()

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
        self.clip_set = None
        self._show_set_clip(False)
        self.history.clear()
        self._update_undo_actions()
        self._show_selection_count()
        self.viewport.set_source(PointSource.from_project(p), self.taxonomy.color_table())
        self.selection_ops = []
        self.annot.set_project(p)
        self.volume.set_project()
        self.trees.cancel()
        self.split.cancel()
        self.cleanup.cancel()
        self.density.cancel()
        self.pieces.cancel()
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

    def _labels_view_changed(self):
        """The colour mode changed: the labels' codes and colours, and when the labels view comes or goes, the
        segments, curves, polylines and markers drawn again in their labels' colours (or their directories')."""
        self.labels.sync()
        on = self.labels.active()
        if on != getattr(self, "_labels_on", False):
            self._labels_on = on
            self.annot.refresh()

    def _on_renderer_ready(self):
        self.clip_scene()
        self.annot.refresh()
        self._apply_display()
        self.labels.sync(force=True)

    def _apply_display(self):
        r = self.viewport.renderer
        if r is None:
            return
        self.display.apply(r.display)
        self.clip.apply(r.display)
        self.viewport.pick_inside = self._inside()
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

    # ---- the view ---------------------------------------------------------------------------------------

    def clear_pick(self):
        if self.viewport.renderer is not None:
            self.viewport.renderer.marker = None
            self.viewport.update()

    # ---- tools ------------------------------------------------------------------------------------------

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
        if self.annot.escape() or self.trace.escape():
            return
        if self.viewport.tool is not None:
            self.viewport.set_tool(None)
        elif self.selection.size:
            self.clear_selection()
        else:
            self.clear_pick()

    def _scan_of_source(self, i: int) -> int:
        return int(np.searchsorted(self._scan_ends, i, side="right"))

    def _on_pick(self, res):
        if res is None or self.project is None:
            self.info.show_rows([("", "no point under the cursor")])
            return
        pick, _ = res
        self.info.show_rows(fmt_point_rows(self.project, self.taxonomy, pick, self._scan_of_source, self.annot.model))
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
