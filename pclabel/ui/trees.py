"""Identify trees by a band of their trunk (``tools.trees``; R-015, proposed): the Tree box in the 3D select panel.

- Shift+U (Select ▸ "Tree"), then click the ground at the foot of the trunk: its height along the height axis is the
  tree's ground. A cylinder 1.00-1.10 m above it (the box's band, adjustable) is placed there and 3D select takes
  over: move it onto the trunk with W A S D (or click the trunk: in tree mode a click only slides the cylinder across
  the axis, so the band keeps its height), size it with "radius", Enter selects the points inside.
- "Save the selection as a tree" puts the selection into a new directory ``tree NN`` as a point set labelled
  ``tree_trunk``, inside the directory chosen in "save into" (default ``trees``, made when missing; the user's
  request of 2026-10-26). The numbers count on from the trees already in that directory. A circle fitted to the
  band gives the tree's position (the circle's centre at the ground height) and girth; they are written into the
  point set's provenance and shown in the box.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QWidget

from pclabel.store.annotations import PointSet
from pclabel.tools.trees import TreeParams, fit_trunk, next_tree_name
from pclabel.ui.docks import EXTRA_DECIMALS, _spin

LABEL = "tree_trunk"  # the label a saved tree's band gets (a taxonomy name, not an id)
TREES = "trees"  # the default directory saved trees go into (made at the top level when missing)
NEW = "new"  # the combo's data for that directory when it does not exist yet


class _DirCombo(QComboBox):
    """A combo box that refills itself (``fill``) each time it opens, so new directories show up."""

    def __init__(self, fill):
        super().__init__()
        self._fill = fill

    def showPopup(self):
        self._fill()
        super().showPopup()


class TreeController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.vol = window.volume
        d = TreeParams()
        box = QGroupBox("Tree (Shift+U)")
        f = QFormLayout(box)
        hint = QLabel("Identify a tree by a band of its trunk: press Shift+U and click the ground at the foot of the "
                      "trunk. A cylinder appears this far above it; move it onto the trunk (W A S D, or click the trunk) "
                      "and set its radius, Enter selects, then save the selection as a tree.")  # fmt: skip
        hint.setWordWrap(True)
        f.addRow(hint)
        self.bottom = _spin(-5, 50, d.band_bottom, 0.05, 2 + EXTRA_DECIMALS, " m")
        self.top = _spin(-5, 50, d.band_top, 0.05, 2 + EXTRA_DECIMALS, " m")
        for sp in (self.bottom, self.top):
            sp.setToolTip("the band of trunk that identifies the tree, above the clicked ground (along the height axis)")
        row = QHBoxLayout()
        row.addWidget(self.bottom)
        row.addWidget(QLabel("to"))
        row.addWidget(self.top)
        band = QWidget()
        band.setLayout(row)
        f.addRow("band above the ground", band)
        self.radius = _spin(0.01, 20, d.radius, 0.05, 2 + EXTRA_DECIMALS, " m")
        self.radius.setToolTip("the cylinder's radius when it is placed; then size it with 3D select's radius")
        f.addRow("start radius", self.radius)
        self.into = _DirCombo(self.fill_directories)
        self.into.setToolTip("the directory each saved tree (a directory \"tree NN\") goes into")
        f.addRow("save into", self.into)
        brow = QHBoxLayout()
        self.start = QPushButton("click the ground at a trunk (Shift+U)")
        self.start.setCheckable(True)
        self.save_button = QPushButton("save the selection as a tree")
        brow.addWidget(self.start)
        brow.addWidget(self.save_button)
        f.addRow(brow)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        f.addRow(self.status)
        self.box = box
        self.vol.dock.widget().layout().addRow(box)
        self.vol.route_keys(box)  # W A S D ... typed in these fields move the cylinder too
        self.fill_directories()
        self.ground = None  # local point clicked at the foot of the trunk
        self.ground_h = None  # its height along the height axis
        self.frame = None  # (axis, along, name) of the height axis when it was clicked
        self.start.clicked.connect(lambda on: self.begin() if on else
                                   self.w.viewport.set_tool(None) if self.w.viewport.tool == "tree" else None)  # fmt: skip
        self.save_button.clicked.connect(lambda: self.save())
        window.viewport.tool_changed.connect(self.on_tool_changed)

    def fill_directories(self, keep=None):
        """The directories trees can go into (by path), keeping the current choice (or ``keep``, an id)."""
        m = self.w.annot.model
        cur = keep if keep is not None else self.into.currentData()
        dirs = [(" / ".join([a.name for a in anc] + [x.name]), x.id) for x, anc in m.walk() if x.kind == "directory"]
        top = next((x for x in m.root if x.kind == "directory" and x.name == TREES), None)
        self.into.blockSignals(True)
        self.into.clear()
        if top is None:
            self.into.addItem(f"{TREES} (new)", NEW)
        for path, i in dirs:
            self.into.addItem(path, i)
        k = self.into.findData(cur) if cur is not None else -1
        if k < 0:
            k = self.into.findData(top.id if top is not None else NEW)
        self.into.setCurrentIndex(max(k, 0))
        self.into.blockSignals(False)

    def _target(self):
        """The directory to save into: the chosen one, or ``trees`` at the top level (made if missing)."""
        m = self.w.annot.model
        i = self.into.currentData()
        if i not in (None, NEW):
            try:
                d = m.node(i)
                if d.kind == "directory":
                    return d
            except KeyError:
                pass
        top = next((x for x in m.root if x.kind == "directory" and x.name == TREES), None)
        return top if top is not None else m.new_directory(TREES)

    def params(self) -> TreeParams:
        return TreeParams(self.bottom.value(), self.top.value(), self.radius.value())

    def begin(self):
        """Shift+U: the next click on the cloud is the ground at the foot of a trunk."""
        if self.w.project is None:
            return
        self.w.viewport.set_tool(None if self.w.viewport.tool == "tree" else "tree")

    def on_tool_changed(self, name):
        self.start.setChecked(name == "tree")
        if name == "tree":
            self.fill_directories()
            self.vol.dock.show()
            self.vol.dock.raise_()
            self.status.setText("click the ground at the foot of the trunk")
        elif name != "volume":
            self.vol.keep_height = False  # a plain 3D select again next time

    def place(self, p_local):
        """The ground at the foot of the trunk was clicked: put the band's cylinder above it and hand over to 3D select."""
        w, vol, prm = self.w, self.vol, self.params()
        p = np.asarray(p_local, np.float64)
        f = w.frames[w.active_frame]
        self.ground, self.frame = p.copy(), vol.frame()
        self.ground_h = float(f.heights(p[None], w.project.offset)[0])
        d = vol.dock
        d.shape.setCurrentIndex(d.shape.findData("cylinder"))
        for name, value in (("radius", prm.radius), ("bottom", prm.band_bottom), ("top", prm.band_top)):
            d.spins[name][0].setValue(value)
        vol.base = p.copy()
        vol.keep_height = True
        w.viewport.set_tool("volume")
        vol.refresh()
        self.status.setText(f"ground at h = {self.ground_h:+.3f} m ({f.name}); band {prm.band_bottom:.2f} to "
                            f"{prm.band_top:.2f} m above it. Move the cylinder onto the trunk (W A S D, or click the "
                            f"trunk), size it, Enter selects, then save the selection as a tree.")  # fmt: skip

    def band(self) -> tuple[float, float]:
        """The cylinder's band now, above the clicked ground (Q E may have moved it)."""
        axis = self.frame[0]
        p = self.vol.dock.params()
        lift = float(np.dot(self.vol.base - self.ground, axis)) if self.vol.base is not None else 0.0
        return p.bottom + lift, p.top + lift

    def save(self):
        """The selection becomes a tree: ``trees/tree NN``, a point set labelled tree_trunk, with its circle fit."""
        w = self.w
        if w.project is None:
            return
        if self.ground is None:
            self.status.setText("click the ground at the foot of a trunk first (Shift+U)")
            return
        sel = w.selection
        axis, along, frame_name = self.frame
        try:
            fit = fit_trunk(w.project.xyz, sel, self.ground, axis, along)
        except ValueError as e:
            self.status.setText(f"select the band first (Enter): {e}")
            return
        off = np.asarray(w.project.offset, np.float64)
        b0, b1 = self.band()
        record = {"tool": "tree", "ground": (self.ground + off).tolist(), "ground_h": self.ground_h,
                  "height_axis": frame_name, "axis": [float(v) for v in axis], "band": [b0, b1],
                  "position": (fit["centre"] + off).tolist(), "radius": fit["radius"], "girth": fit["girth"],
                  "rms": fit["rms"], "points_fitted": fit["points"], "points_dropped": fit["dropped"],
                  "coverage": fit["coverage"]}  # fmt: skip
        a = w.annot
        m = a.model
        a._checkpoint("save a tree")
        trees = self._target()
        name = next_tree_name([c.name for c in trees.children])
        tree = m.new_directory(name, trees.id)
        ps = m.add(PointSet(sel.copy(), [dict(op) for op in w.selection_ops] + [record]), f"trunk {b0:.2f} to {b1:.2f} m")
        if any(c["name"] == LABEL for c in w.taxonomy.classes):
            ps.label = LABEL
        m.move([ps.id], tree.id)
        x, y, z = record["position"]
        text = (f"{name}: {sel.size:,} points; position ({x:.3f}, {y:.3f}, {z:.3f}); radius {fit['radius']:.3f} m, girth "
                f"{fit['girth']:.2f} m; fit {fit['rms'] * 100:.1f} cm rms, {fit['coverage']:.0%} of the circumference"
                + (f"; {fit['dropped']:,} points off the trunk left out of the fit" if fit["dropped"] else ""))  # fmt: skip
        a._changed(text)
        self.fill_directories(keep=trees.id)
        self.status.setText(text)
        return tree

    def cancel(self):
        self.ground = self.ground_h = self.frame = None
        self.vol.keep_height = False
        self.status.setText("")
        self.fill_directories(keep=NEW)
