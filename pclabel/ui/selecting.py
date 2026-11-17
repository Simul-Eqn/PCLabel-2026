"""The window's selection: replacing and combining it, the locks that keep points out of it, what the clips and
the slice hide from it (``filters``), the clip to point sets, and lasso, rectangle, 3D and extrude select."""

from __future__ import annotations

import numpy as np
from PySide6.QtWidgets import QMessageBox

from pclabel.render.camera import ORTHO
from pclabel.render.renderer import INSIDE_BIT
from pclabel.ops.selection import combine
from pclabel.tools import sets
from pclabel.tools.selection import DepthTest, SelectFilters, select_visible
from pclabel.tools.volume import select_loop, select_volume
from pclabel.ui.worker import run_in_worker


class SelectionMixin:
    """Part of MainWindow."""

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
            indices = self.filters().apply(self.project.xyz, indices)
        self.selection, dropped = combine(indices, indices, "replace", self.locked_points()) if filtered else (indices, 0)
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

    def _set_overlay(self):
        """Tint exactly the drawn points that are selected (a GPU-side bit; no subsampling)."""
        vp = self.viewport
        if vp.renderer is None:
            return
        with vp.current():
            vp.renderer.set_selection(self.selection)
            if self._grow_state is None:
                vp.renderer.set_markers(None)
        vp.update()

    def filters(self) -> SelectFilters:
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
        if d.clip_set_on and self.clip_set is not None:
            kw.update(inside=self.clip_set)
        return SelectFilters(**kw)

    # ---- the clip to point sets: only their points are drawn, picked, read and selected ---------------------

    def _inside(self):
        """The clip to point sets while it is on (a tools.sets.PointMask), else None."""
        r = self.viewport.renderer
        return self.clip_set if r is not None and r.display.clip_set_on else None

    def set_point_set_clip(self, indices, label: str = "", on: bool = True):
        """Clip to these store indices (sorted): only they are drawn, picked and worked on. None: no point set."""
        r = self.viewport.renderer
        if r is None or self.project is None:
            return
        self.clip_set = None if indices is None else sets.PointMask(indices, self.project.n, label)
        vp = self.viewport
        with vp.current():
            r.set_marks(INSIDE_BIT, None if self.clip_set is None else self.clip_set.indices)
        self._show_set_clip(on)
        self._apply_display()

    def _show_set_clip(self, on: bool):
        c, s = self.clip, self.clip_set
        c.set_on.blockSignals(True)
        c.set_on.setEnabled(s is not None)
        c.set_on.setChecked(bool(on and s is not None))
        c.set_on.blockSignals(False)
        c.set_label.setText(c.NO_SET if s is None else f"{s.label}: {s.indices.size:,} points")

    def clip_to_selected_sets(self):
        """Ctrl+K: clip to the point sets selected in the Annotations panel (a directory: all its point sets)."""
        a = self.annot
        items = [m for m in a.selected_items() if m.kind == "points"]
        if not items or self.project is None:
            self.statusBar().showMessage("select point sets or directories in the Annotations panel first", 4000)
            return
        names = [a.model.node(i).name for i in a.selected_ids()]
        label = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
        idx = sets.union_all([m.indices for m in items])
        self.set_point_set_clip(idx, label)
        self.statusBar().showMessage(f"clipped to {label}: {idx.size:,} points", 4000)

    def toggle_point_set_clip(self):
        """Ctrl+Shift+K: the clip to point sets off or on again (the first time: the selected point sets)."""
        if self.clip_set is None:
            self.clip_to_selected_sets()
        else:
            self.clip.set_on.toggle()

    def _visible(self, idx):
        """What the clips (box and 3D select volume) and the slice hide is not selected."""
        if self.viewport.renderer is None or idx.size == 0:
            return idx
        return self.filters().apply(self.project.xyz, idx)

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
            with vp.current():
                depth = r.read_depth()
            cam = vp.camera
            test = DepthTest(depth, cam.near, cam.far, ortho=cam.projection == ORTHO)
        args = (self.project.xyz, self.project.tiles, r.last_mvp.copy(), r.last_viewport, polygon, test,
                self.filters())  # fmt: skip
        self._sel_busy = True
        self.frames_dock.sel_label.setText("selecting...")
        shape = "lasso" if self.viewport.tool == "lasso" else "rectangle"
        depth = "through" if self.viewport.select_through else "visible"
        self._run_selection(lambda: select_visible(*args), op,
                            {"tool": "select_shape", "shape": shape, "depth": depth, "op": op,
                             "depth_test": None if test is None else {"tol": test.tol, "tol_per_m": test.tol_per_m}})  # fmt: skip

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
                self.filters())  # fmt: skip
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
                bottom, top, inset, self.filters())  # fmt: skip
        self._sel_busy = True
        self.frames_dock.sel_label.setText("selecting...")
        self._run_selection(lambda: select_loop(*args), op, definition)

    def _run_selection(self, fn, op, definition):
        run_in_worker(fn, lambda res, err, s: self._selection_done(res, op, s, err, definition), "select")

    def _selection_done(self, idx, op, seconds, err, definition):
        self._sel_busy = False
        if err is not None:
            QMessageBox.critical(self, "Selection failed", str(err))
            return
        names = {"select_shape": f"{definition.get('shape', 'shape')} selection, {definition.get('depth', '')}",
                 "select_volume": "extrude select" if definition.get("shape") == "loop" else "3D select"}  # fmt: skip
        self.checkpoint("selection", f"{names.get(definition['tool'], definition['tool'])} ({op})")
        self.selection, dropped = combine(self.selection, idx, op, self.locked_points())
        self._grow_state = None  # a shape changed the selection: parameter changes no longer re-grow
        self.selection_def = dict(definition, points=int(idx.size) - dropped)  # the points it found, locked ones out
        self.selection_ops = ([] if op == "replace" else self.selection_ops) + [self.selection_def]
        self._set_overlay()
        self._show_selection_count(seconds, dropped)
