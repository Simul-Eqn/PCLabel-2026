"""Clean up the selection by its footprint (``tools.cleanup``): the Clean up panel.

"Preview" works out what would be added (orange) and taken out (magenta) in a worker thread; changing a setting
previews again. "Apply" makes it the selection (one undo step). Only points visible through the clips and the slice,
and not locked, are considered.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QDockWidget, QFormLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox, QWidget

from pclabel.render.renderer import GROUP_BIT, UNSURE_BIT
from pclabel.tools.cleanup import CleanParams, apply_clean, clean_selection
from pclabel.ui.docks import _spin


class CleanDock(QDockWidget):
    FIELDS = (  # name, label, factor, lo, hi, step, decimals, suffix, tooltip
        ("above", "up to", 100, 0, 200, 0.5, 2, " cm above",
         "points up to this far over the selected surface around them join (grass, slightly raised pavement)"),
        ("below", "down to", 100, 0, 200, 0.5, 2, " cm below", "and down to this far under it"),
        ("radius", "around", 100, 1, 200, 1, 2, " cm", "the neighbourhood that gives the surface and the share selected"),
        ("cell", "columns", 100, 0.5, 50, 0.5, 2, " cm", "the footprint grid across the height axis"),
        ("majority", "fill holes when", 100, 0, 100, 5, 1, " % around",
         "a column with points joins the footprint when this share of the columns around it is selected"),
    )  # fmt: skip

    def __init__(self, parent=None):
        super().__init__("Clean up", parent)
        self.setObjectName("cleanup")
        d = CleanParams()
        w = QWidget()
        f = QFormLayout(w)
        hint = QLabel("Clean up the selection by its footprint across the height axis: take in the points just over "
                      "or under the selected surface (e.g. grass and raised pavement a low 3D select missed) and "
                      "drop stray specks. Orange: would be added; magenta: would be taken out.")  # fmt: skip
        hint.setWordWrap(True)
        f.addRow(hint)
        self.spins = {}
        for name, label, k, lo, hi, step, dec, suffix, tip in self.FIELDS:
            sp = _spin(lo, hi, getattr(d, name) * k, step, dec, suffix)
            sp.setToolTip(tip)
            f.addRow(label, sp)
            self.spins[name] = (sp, k)
        self.fill_holes = QCheckBox("fill holes in the footprint")
        self.fill_holes.setChecked(d.fill_holes)
        f.addRow("", self.fill_holes)
        self.remove_specks = QCheckBox("remove specks and small islands")
        self.remove_specks.setChecked(d.remove_specks)
        f.addRow("", self.remove_specks)
        self.min_island = QSpinBox()
        self.min_island.setRange(0, 100_000_000)
        self.min_island.setSingleStep(50)
        self.min_island.setValue(d.min_island)
        self.min_island.setToolTip("pieces of the footprint with fewer selected points are taken out")
        f.addRow("smallest island", self.min_island)
        row = QHBoxLayout()
        self.preview = QPushButton("preview")
        self.apply = QPushButton("apply")
        self.cancel = QPushButton("cancel")
        for b in (self.preview, self.apply, self.cancel):
            row.addWidget(b)
        f.addRow(row)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        f.addRow(self.result)
        self.setWidget(w)

    def params(self) -> CleanParams:
        return replace(CleanParams(), **{n: sp.value() / k for n, (sp, k) in self.spins.items()},
                       fill_holes=self.fill_holes.isChecked(), remove_specks=self.remove_specks.isChecked(),
                       min_island=self.min_island.value())  # fmt: skip


class _Done(QObject):
    done = Signal(object, object, object)  # selection it was for, result, error


class CleanController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.dock = CleanDock(window)
        self.last = None  # (selection, added, removed, info)
        self._busy = False
        self._again = False
        self._done = _Done()
        self._done.done.connect(self._finished)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self.preview)
        d = self.dock
        d.preview.clicked.connect(lambda: self.preview())
        d.apply.clicked.connect(lambda: self.apply())
        d.cancel.clicked.connect(lambda: self.cancel())
        for sp, _ in d.spins.values():
            sp.valueChanged.connect(lambda _: self._timer.start() if self.last is not None else None)
        for wdg in (d.fill_holes, d.remove_specks):
            wdg.toggled.connect(lambda _: self._timer.start() if self.last is not None else None)
        d.min_island.valueChanged.connect(lambda _: self._timer.start() if self.last is not None else None)

    def open(self):
        self.dock.show()
        self.dock.raise_()
        self.preview()

    def preview(self):
        w = self.w
        if w.project is None or w.selection.size == 0:
            self.dock.result.setText("select something first")
            return
        if self._busy:
            self._again = True
            return
        f = w.frames[w.active_frame]
        sel = w.selection
        p = self.dock.params()
        allowed = w.allowed_mask
        origin = np.asarray(f.origin, np.float64) - w.project.offset
        args = (w.project.xyz, w.project.tiles, sel, f.normal, f.along, origin, p, allowed)
        self._busy = True
        self.dock.result.setText("working...")

        def work():
            t0 = time.perf_counter()
            try:
                added, removed, info = clean_selection(*args)
                info["seconds"] = time.perf_counter() - t0
                self._done.done.emit(sel, (added, removed, info), None)
            except Exception as e:  # reported in the UI thread
                self._done.done.emit(sel, None, e)

        threading.Thread(target=work, daemon=True, name="clean-up").start()

    def _finished(self, sel, res, err):
        self._busy = False
        if err is not None:
            self.dock.result.setText(f"failed: {err}")
        elif sel is self.w.selection:  # still the selection it was worked out for
            added, removed, info = res
            self.last = (sel, added, removed, info)
            self._show(added, removed)
            self.dock.result.setText(f"would add {info['added']:,} points and take out {info['removed']:,} "
                                     f"({info['holes_filled']:,} footprint holes filled); {info['seconds']:.2f} s")  # fmt: skip
        if self._again:
            self._again = False
            self.preview()

    def _show(self, added, removed):
        vp = self.w.viewport
        if vp.renderer is None:
            return
        vp.makeCurrent()
        try:
            vp.renderer.set_marks(GROUP_BIT, added)
            vp.renderer.set_marks(UNSURE_BIT, removed)
        finally:
            vp.doneCurrent()
        vp.update()

    def cancel(self):
        self.last = None
        self._show(None, None)
        self.dock.result.setText("")

    def apply(self):
        if self.last is None or self.last[0] is not self.w.selection:
            self.dock.result.setText("preview first (the selection has changed since)" if self.last else "preview first")
            return None
        sel, added, removed, info = self.last
        new = apply_clean(sel, added, removed)
        self.w.set_selection(new, {"tool": "clean_up", "op": "replace", "added": info["added"],
                                   "removed": info["removed"], "params": info["params"]}, filtered=False)  # fmt: skip
        self.last = None
        self._show(None, None)
        self.dock.result.setText(f"added {info['added']:,} points, took out {info['removed']:,}")
        return new
