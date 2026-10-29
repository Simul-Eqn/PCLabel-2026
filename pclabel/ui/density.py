"""The parts of a point set dense enough to work on (``tools.density``): the Density panel.

1. Select a point set (or directories) in the Annotations panel and press "density..." there, or "use the
   selected point sets" / "use the selection" here. The set becomes the selection (yellow) and its columns are
   counted in a worker thread.
2. The sparse part (below the threshold) is tinted magenta; changing a setting previews again.
3. "Select the dense part" makes it the selection; "save as a point set" adds it as a new point set (the set itself
   stays as it is). Both record the parameters.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QDockWidget, QFormLayout, QHBoxLayout, QLabel, QPushButton, QWidget

from pclabel.render.renderer import UNSURE_BIT
from pclabel.store.annotations import PointSet
from pclabel.tools import sets
from pclabel.tools.density import DensityParams, dense_part, density_grid
from pclabel.ui.docks import _spin


class DensityDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Density", parent)
        self.setObjectName("density")
        d = DensityParams()
        w = QWidget()
        f = QFormLayout(w)
        hint = QLabel("The parts of a point set dense enough to work on, e.g. a road set for magic selecting its "
                      "lines. Points per m² across the height axis; magenta: too sparse.")  # fmt: skip
        hint.setWordWrap(True)
        f.addRow(hint)
        row = QHBoxLayout()
        self.use = QPushButton("use the selected point sets")
        self.use_sel = QPushButton("use the selection")
        row.addWidget(self.use)
        row.addWidget(self.use_sel)
        f.addRow(row)
        self.source = QLabel("")
        self.source.setWordWrap(True)
        f.addRow("set", self.source)
        self.min_density = _spin(0, 1e7, d.min_density, 100, 0, " /m²")
        self.min_density.setToolTip(
            "2,000 points/m² (2.2 cm apart): on streets 1 one magic select click got a whole lane dash at this "
            "density and above (90 % or more of thinned trials; every real dash you clicked); below 500, one click "
            "rarely did (OBSERVATIONS O-0011)")  # fmt: skip
        f.addRow("at least", self.min_density)
        self.spacing = QLabel("")
        f.addRow("", self.spacing)
        self.cell = _spin(5, 500, d.cell * 100, 5, 1, " cm")
        self.cell.setToolTip("the columns across the height axis")
        f.addRow("columns", self.cell)
        self.radius = _spin(5, 1000, d.radius * 100, 5, 1, " cm")
        self.radius.setToolTip("a column's density is the mean over the occupied columns within this")
        f.addRow("averaged within", self.radius)
        self.min_area = _spin(0, 10000, d.min_area, 0.5, 2, " m²")
        self.min_area.setToolTip("dense pieces smaller than this are left out")
        f.addRow("smallest piece", self.min_area)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        f.addRow(self.result)
        brow = QHBoxLayout()
        self.select = QPushButton("select the dense part")
        self.save = QPushButton("save as a point set")
        self.cancel = QPushButton("cancel")
        for b in (self.select, self.save, self.cancel):
            brow.addWidget(b)
        f.addRow(brow)
        self.setWidget(w)
        self.min_density.valueChanged.connect(self._show_spacing)
        self._show_spacing()

    def _show_spacing(self):
        v = self.min_density.value()
        self.spacing.setText(f"points about {100 / np.sqrt(v):.1f} cm apart or closer" if v > 0 else "everything")

    def params(self) -> DensityParams:
        return replace(DensityParams(), min_density=self.min_density.value(), cell=self.cell.value() / 100,
                       radius=self.radius.value() / 100, min_area=self.min_area.value())  # fmt: skip


class _Done(QObject):
    done = Signal(object, object, object)  # kind, result, error


class DensityController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.dock = DensityDock(window)
        self.name = None  # what the set is called (point set names, or "selection")
        self.grid = None
        self.last = None  # (dense, sparse, info)
        self._busy = False
        self._again = None
        self._done = _Done()
        self._done.done.connect(self._finished)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.preview)
        d = self.dock
        d.use.clicked.connect(lambda: self.start())
        d.use_sel.clicked.connect(lambda: self.start_selection())
        d.min_density.valueChanged.connect(lambda _: self._timer.start())
        d.min_area.valueChanged.connect(lambda _: self._timer.start())
        d.cell.valueChanged.connect(lambda _: self._regrid())
        d.radius.valueChanged.connect(lambda _: self._regrid())
        d.select.clicked.connect(lambda: self.select_dense())
        d.save.clicked.connect(lambda: self.save())
        d.cancel.clicked.connect(self.cancel)

    def start(self):
        """Work on the point sets selected in the Annotations panel (a directory: all its point sets)."""
        a = self.w.annot
        items = [m for m in a._selected_items() if m.kind == "points"]
        if not items:
            self.dock.result.setText("select a point set (or a directory) in the Annotations panel first")
            self._open()
            return
        names = [a.model.node(i).name for i in a.selected_ids()]
        self._begin(sets.union_all([m.indices for m in items]), ", ".join(names))

    def start_selection(self):
        if self.w.selection.size == 0:
            self.dock.result.setText("select something first")
            self._open()
            return
        self._begin(self.w.selection, "the selection")

    def _open(self):
        self.dock.show()
        self.dock.raise_()

    def _begin(self, indices, name: str):
        w = self.w
        if w.project is None:
            return
        self.name, self.grid, self.last = name, None, None
        self._open()
        w.set_selection(indices, {"tool": "density", "op": "replace", "set": name})
        idx = w.selection  # without what the clips hide and locked points
        self.dock.source.setText(f"{name} ({idx.size:,} points)")
        self._grid_job(idx)

    def _grid_job(self, idx):
        w = self.w
        f = w.frames[w.active_frame]
        origin = np.asarray(f.origin, np.float64) - w.project.offset
        p = self.dock.params()
        xyz = w.project.xyz
        self._run("grid", lambda: density_grid(xyz, idx, f.normal, f.along, origin, p.cell, p.radius))

    def _regrid(self):
        if self.grid is not None:
            self._grid_job(self.grid.indices)

    def _run(self, kind, fn):
        if self._busy:
            self._again = (kind, fn)
            return
        self._busy = True
        self.dock.result.setText("counting the points per column..." if kind == "grid" else "working...")

        def work():
            t0 = time.perf_counter()
            try:
                self._done.done.emit(kind, (fn(), time.perf_counter() - t0), None)
            except Exception as e:  # reported in the UI thread
                self._done.done.emit(kind, None, e)

        threading.Thread(target=work, daemon=True, name="density").start()

    def _finished(self, kind, res, err):
        self._busy = False
        if err is not None:
            self.dock.result.setText(f"failed: {err}")
        elif kind == "grid":
            self.grid = res[0]
            self.preview()
        else:
            self.last = res[0]
            self._show(res[1])
        if self._again is not None:
            kind, fn = self._again
            self._again = None
            self._run(kind, fn)

    def preview(self):
        if self.grid is None:
            return
        g, p = self.grid, self.dock.params()
        self._run("split", lambda: dense_part(g, p))

    def _show(self, seconds: float):
        dense, sparse, info = self.last
        vp = self.w.viewport
        if vp.renderer is not None:
            vp.makeCurrent()
            try:
                vp.renderer.set_marks(UNSURE_BIT, sparse)
            finally:
                vp.doneCurrent()
            vp.update()
        pc = info["density_percentiles"]
        pct = ", ".join(f"{k} % {v:,.0f}" for k, v in pc.items())
        share = 100 * info["dense_points"] / max(info["points"], 1)
        self.dock.result.setText(
            f"dense: {info['dense_points']:,} points ({share:.1f} %) on {info['dense_area_m2']:.1f} of "
            f"{info['area_m2']:.1f} m²\ndensity over the set (points/m²): {pct}\n"
            f"{info['pieces_dropped']} small dense pieces left out; {seconds:.2f} s")  # fmt: skip

    def _clear(self):
        vp = self.w.viewport
        if vp.renderer is not None:
            vp.makeCurrent()
            try:
                vp.renderer.set_marks(UNSURE_BIT, None)
            finally:
                vp.doneCurrent()
            vp.update()

    def cancel(self):
        self._clear()
        self.name, self.grid, self.last = None, None, None
        self.dock.source.setText("")
        self.dock.result.setText("")

    def _record(self, info):
        return {"tool": "density", "set": self.name, "params": info["params"],
                "result": {k: v for k, v in info.items() if k != "params"}}  # fmt: skip

    def select_dense(self):
        if self.last is None or self._busy:
            self.dock.result.setText("nothing to select yet")
            return None
        dense, _, info = self.last
        self._clear()
        self.w.set_selection(dense, dict(self._record(info), op="replace"), filtered=False)
        self.dock.result.setText(f"selected the dense part: {dense.size:,} points")
        return dense

    def save(self):
        """The dense part as a new point set (the set itself stays as it is)."""
        if self.last is None or self._busy:
            self.dock.result.setText("nothing to save yet")
            return None
        dense, _, info = self.last
        if dense.size == 0:
            self.dock.result.setText("the dense part is empty: nothing saved")
            return None
        a = self.w.annot
        name = f"{self.name} dense ≥{info['params']['min_density']:g}/m²"
        a._checkpoint(f"new point set {name}")
        ps = a.model.add(PointSet(dense, [self._record(info)]), name=name)
        a._changed(f"{name}: {dense.size:,} points")
        self.dock.result.setText(a.dock.status.text())
        return ps
