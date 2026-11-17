"""The height axes: z, and the planes fitted to a selection (F), kept in the project; activated, renamed,
deleted and exported."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QFileDialog, QInputDialog

from pclabel.analysis.frames import Z_FRAME, HeightFrame, PlaneFitParams, frame_from_points, plane_document
from pclabel.store.project import source_doc
from pclabel.store.util import write_json_atomic
from pclabel.ui.docks import fmt_frame_report


class HeightAxesMixin:
    """Part of MainWindow."""

    def _load_frames(self):
        frames, active = self.project.load_frames()
        self.frames = [Z_FRAME] + [HeightFrame.from_json(f) for f in frames]
        names = [f.name for f in self.frames]
        self.active_frame = names.index(active) if active in names else 0
        self._last_plane = self.active_frame if self.active_frame > 0 else (len(self.frames) - 1 or None)
        self._show_frames()

    def _show_frames(self):
        self.frames_dock.set_frames([f.name for f in self.frames], self.active_frame)
        if hasattr(self, "labels"):
            self.labels.fill_planes()
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
        write_json_atomic(Path(path), plane_document(f, self.project.offset, source_doc(self.project)))
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

    def rename_frame(self, name: str | None = None):
        """Rename the plane shown in the Height axis panel (None: ask). Names stay unique; undoable."""
        i = self.active_frame
        if self.project is None or i == 0:
            self.statusBar().showMessage("choose a fitted plane first (z is the scan frame)", 4000)
            return
        f = self.frames[i]
        if name is None:
            name, ok = QInputDialog.getText(self, "Rename plane", "name:", text=f.name)
            if not ok:
                return
        name = name.strip()
        if not name or name == f.name:
            return
        if name in {g.name for g in self.frames}:
            self.statusBar().showMessage(f"there is already a {name!r}", 5000)
            return
        self.checkpoint("frames", f"rename {f.name}")
        self.frames[i] = replace(f, name=name)  # a new frame: the undo snapshot keeps the old one
        self._show_frames()
        self._save_frames()
        self.statusBar().showMessage(f"{f.name} renamed {name}", 4000)

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
