"""The Trace panel's cover outlines: select markers (e.g. placed with Ctrl+M on a cover's points) and "outline the
selected markers": each gets a directory "cover NN" with the marker and a closed outline (a circle or a rectangle,
tools/templates.py) labelled ``cover``. "Reshape" redraws the selected outlines with the box's size and rotation
(resize, rotate); the marker is where it stands (move it with "move", or place it again)."""

from __future__ import annotations

from dataclasses import asdict

import numpy as np
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QGroupBox, QPushButton

from pclabel.ops import trace as trace_ops
from pclabel.tools.templates import SHAPES, OutlineParams, outline
from pclabel.ui.docks import _spin


class CoverBox(QGroupBox):
    def __init__(self):
        super().__init__("cover outline")
        o = OutlineParams()
        g = QFormLayout(self)
        self.shape = QComboBox()
        for sh in SHAPES:
            self.shape.addItem(sh, sh)
        self.diameter = _spin(0.05, 5.0, o.diameter, 0.05, 3, " m")
        self.across = _spin(0.05, 5.0, o.width, 0.05, 3, " m")
        self.length = _spin(0.05, 5.0, o.length, 0.05, 3, " m")
        self.rotation = _spin(-360.0, 360.0, o.rotation, 5.0, 1, " deg")
        self.from_fit = QCheckBox("diameter from the marker's fit")
        self.from_fit.setToolTip("a circle's diameter from the marker's circle fit (Ctrl+M, circle centre), if it has one")
        self.from_fit.setChecked(True)
        self.place = QPushButton("outline the selected markers")
        self.reshape = QPushButton("reshape selected outlines")
        for label, wdg in (("shape", self.shape), ("diameter", self.diameter), ("width", self.across),
                           ("length", self.length), ("rotation", self.rotation)):  # fmt: skip
            g.addRow(label, wdg)
        g.addRow(self.from_fit)
        g.addRow(self.place)
        g.addRow(self.reshape)

    def params(self, diameter: float | None = None) -> OutlineParams:
        return OutlineParams(shape=self.shape.currentData(), diameter=diameter or self.diameter.value(),
                             width=self.across.value(), length=self.length.value(), rotation=self.rotation.value())  # fmt: skip


class CoverTool:
    def __init__(self, trace):
        self.trace = trace  # the Trace panel's controller: its window and status line
        self.box = CoverBox()
        self.box.place.clicked.connect(lambda: self.outline_selected())
        self.box.reshape.clicked.connect(lambda: self.reshape_selected())

    def _marker_frame(self, mk):
        """(axis, along) for a marker's outline: its own height axis if it has one, else the active one."""
        w = self.trace.w
        f = w.frames[w.active_frame]
        ax = mk.source.get("axis")
        axis = np.asarray(ax if ax is not None else f.normal, np.float64)
        return axis / np.linalg.norm(axis), np.asarray(f.along, np.float64)

    def _outline_for(self, mk):
        b = self.box
        dia = None
        if b.shape.currentData() == "circle" and b.from_fit.isChecked() and mk.source.get("radius"):
            dia = 2 * float(mk.source["radius"])
        p = b.params(dia)
        axis, along = self._marker_frame(mk)
        return outline(mk.position, axis, p, along), p, axis

    def outline_selected(self) -> list:
        """An outline (labelled cover) for each selected marker, in a directory "cover NN" with it; one undo step."""
        w = self.trace.w
        a = w.annot
        mks = [x for x in a.selected_items() if x.kind == "marker"]
        if not mks:
            self.trace.dock.status.setText("select markers in Annotations first (e.g. Ctrl+M on a cover's points)")
            return []
        a.checkpoint(f"cover outline{'s' * (len(mks) != 1)}")
        made = []
        for mk in mks:
            ring, p, axis = self._outline_for(mk)
            made.append(trace_ops.add_cover(a.model, mk, ring, asdict(p), axis, w.taxonomy.tool_label("cover")))
        a.changed(f"{len(made)} cover outline{'s' * (len(made) != 1)}: {made[0].name}" + (" ..." if len(made) > 1 else ""))
        return made

    def reshape_selected(self) -> int:
        """Redraw the selected outlines (or those in selected directories) with the box's shape, size and rotation,
        around their markers; one undo step."""
        a = self.trace.w.annot
        m = a.model
        outs = [x for x in a.selected_items() if x.kind == "polyline" and x.source.get("tool") == "cover_template"]
        if not outs:
            self.trace.dock.status.setText("select cover outlines (or their directories) first")
            return 0
        a.checkpoint(f"reshape {len(outs)} outline{'s' * (len(outs) != 1)}")
        n = 0
        for pl in outs:
            try:
                mk = m.node(pl.source["marker"])
            except KeyError:
                continue
            ring, p, axis = self._outline_for(mk)
            pl.vertices = ring
            pl.source = dict(pl.source, template=asdict(p), axis=[float(x) for x in axis])
            n += 1
        a.changed(f"{n} outline{'s' * (n != 1)} reshaped")
        return n
