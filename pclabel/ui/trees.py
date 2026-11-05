"""Identify trees by a band of their trunk (``tools.trees``; R-015, proposed), and poles the same way (R-017,
proposed 2026-11-04): the "Tree or pole" box in the 3D select panel.

- Choose what it is (tree, lamp_post, traffic_light_pole, other_pole, or just pole), Shift+U (Select ▸ "Tree or
  pole"), then click the ground at the foot of the trunk or pole: its height along the height axis is the object's
  ground. A cylinder 1.00-1.10 m above it (the box's band, adjustable) is placed there and 3D select takes over: move
  it onto the trunk or pole with W A S D (or click it: in this mode a click only slides the cylinder across the axis,
  so the band keeps its height), size it with "radius", Enter selects the points inside.
- "Save" puts the selection into a new directory ``tree NN`` (``lamp_post NN``, ...) inside the directory chosen in
  "save into" (default ``trees`` or ``poles``, made when missing; the user's request of 2026-10-26): the band as a
  point set (labelled ``tree_trunk`` for a tree, else the pole's label) and a **marker** labelled with what it is, at
  the centre of a circle fitted to the band, at the ground height: the object's position (the user's request of
  2026-11-04 for markers). The fit (radius, girth, rms, coverage) is in both and shown in the box. The numbers count
  on from the objects already in that directory.
- "Add markers to earlier trees" gives every tree saved before markers existed its marker, from the position its
  band recorded (one undo step).
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QWidget

from pclabel.store.annotations import Marker, PointSet
from pclabel.tools.trees import TreeParams, fit_trunk, next_name
from pclabel.ui.docks import EXTRA_DECIMALS, _spin

LABEL = "tree_trunk"  # the label a saved tree's band gets (a taxonomy name, not an id)
TREE = "tree"  # what a saved object is by default; its marker's label
TREES = "trees"  # the default directory saved trees go into (made at the top level when missing)
POLES = "poles"  # ... and poles
POLE = "pole"  # the taxonomy class whose kinds the box offers besides trees
NEW = "new"  # the combo's data for that directory when it does not exist yet
POSITION = "position"  # the name of an object's marker


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
        box = QGroupBox("Tree or pole (Shift+U)")
        f = QFormLayout(box)
        hint = QLabel("Identify a tree or a pole by a band of it: press Shift+U and click the ground at its foot. A "
                      "cylinder appears this far above it; move it onto the trunk or pole (W A S D, or click it) and "
                      "set its radius, Enter selects, then save: the band, and a marker at the centre of a circle "
                      "fitted to it, at the ground.")  # fmt: skip
        hint.setWordWrap(True)
        f.addRow(hint)
        self.what = QComboBox()
        self.what.setToolTip("what it is: the marker's label (a tree's band is tree_trunk, a pole's band the pole's label)")
        f.addRow("what", self.what)
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
        self.save_button = QPushButton("save the selection")
        self.markers_button = QPushButton("add markers to earlier trees")
        self.markers_button.setToolTip("a marker for every tree saved before markers existed, from its band's "
                                       "recorded position")  # fmt: skip
        brow.addWidget(self.start)
        brow.addWidget(self.save_button)
        f.addRow(brow)
        f.addRow(self.markers_button)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        f.addRow(self.status)
        self.box = box
        self.vol.dock.widget().layout().addRow(box)
        self.vol.route_keys(box)  # W A S D ... typed in these fields move the cylinder too
        self.fill_what()
        self.fill_directories()
        self.ground = None  # local point clicked at the foot of the trunk
        self.ground_h = None  # its height along the height axis
        self.frame = None  # (axis, along, name) of the height axis when it was clicked
        self.start.clicked.connect(lambda on: self.begin() if on else
                                   self.w.viewport.set_tool(None) if self.w.viewport.tool == "tree" else None)  # fmt: skip
        self.save_button.clicked.connect(lambda: self.save())
        self.markers_button.clicked.connect(lambda: self.add_earlier_markers())
        self.what.activated.connect(lambda _: self.fill_directories(keep=NEW))
        window.viewport.tool_changed.connect(self.on_tool_changed)

    def fill_what(self):
        """Tree, then the pole classes (pole and its kinds), as the taxonomy has them."""
        tax = getattr(self.w, "taxonomy", None)
        names = [TREE]
        if tax is not None and tax.by_name(POLE) is not None:
            names += [POLE] + tax.descendants(POLE)
        keep = self.what.currentText()
        self.what.clear()
        self.what.addItems(names)
        if keep in names:
            self.what.setCurrentText(keep)

    def kind(self) -> str:
        return self.what.currentText() or TREE

    def _default_dir(self) -> str:
        return TREES if self.kind() == TREE else POLES

    def fill_directories(self, keep=None):
        """The directories trees can go into (by path), keeping the current choice (or ``keep``, an id)."""
        m = self.w.annot.model
        cur = keep if keep is not None else self.into.currentData()
        dirs = [(" / ".join([a.name for a in anc] + [x.name]), x.id) for x, anc in m.walk() if x.kind == "directory"]
        default = self._default_dir()
        top = next((x for x in m.root if x.kind == "directory" and x.name == default), None)
        self.into.blockSignals(True)
        self.into.clear()
        if top is None:
            self.into.addItem(f"{default} (new)", NEW)
        for path, i in dirs:
            self.into.addItem(path, i)
        k = self.into.findData(cur) if cur is not None else -1
        if k < 0:
            k = self.into.findData(top.id if top is not None else NEW)
        self.into.setCurrentIndex(max(k, 0))
        self.into.blockSignals(False)

    def _target(self):
        """The directory to save into: the chosen one, or ``trees`` (``poles``) at the top level (made if missing)."""
        m = self.w.annot.model
        i = self.into.currentData()
        if i not in (None, NEW):
            try:
                d = m.node(i)
                if d.kind == "directory":
                    return d
            except KeyError:
                pass
        default = self._default_dir()
        top = next((x for x in m.root if x.kind == "directory" and x.name == default), None)
        return top if top is not None else m.new_directory(default)

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
        """The selection becomes a tree (or a pole): ``trees/tree NN`` with a marker at its position and the band as a
        point set labelled tree_trunk (a pole: its label), both with the circle fit."""
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
        what = self.kind()
        record["object"] = what
        a = w.annot
        m = a.model
        a._checkpoint(f"save a {what}")
        trees = self._target()
        name = next_name([c.name for c in trees.children], what)
        tree = m.new_directory(name, trees.id)
        known = {c["name"] for c in w.taxonomy.classes}
        mk = m.add(Marker(np.asarray(record["position"], np.float64), _marker_source(record, fit)), POSITION)
        mk.label = what if what in known else ""
        band_label = LABEL if what == TREE else what
        part = "trunk" if what == TREE else "pole"
        ps = m.add(PointSet(sel.copy(), [dict(op) for op in w.selection_ops] + [record]), f"{part} {b0:.2f} to {b1:.2f} m")
        if band_label in known:
            ps.label = band_label
        m.move([mk.id, ps.id], tree.id)
        x, y, z = record["position"]
        text = (f"{name}: {sel.size:,} points; marker at ({x:.3f}, {y:.3f}, {z:.3f}); radius {fit['radius']:.3f} m, girth "
                f"{fit['girth']:.2f} m; fit {fit['rms'] * 100:.1f} cm rms, {fit['coverage']:.0%} of the circumference"
                + (f"; {fit['dropped']:,} points off the {part} left out of the fit" if fit["dropped"] else ""))  # fmt: skip
        a._changed(text)
        self.fill_directories(keep=trees.id)
        self.status.setText(text)
        return tree

    def add_earlier_markers(self) -> int:
        """A marker for every tree (or pole) saved before markers existed: one per directory holding a band with a
        recorded position and no marker yet, labelled with what the band recorded (a tree when it says nothing).
        One undo step; returns how many were added."""
        w = self.w
        if w.project is None:
            return 0
        a = w.annot
        m = a.model
        known = {c["name"] for c in w.taxonomy.classes}
        todo = []
        for x, anc in m.walk():
            if x.kind != "points":
                continue
            recs = [r for r in x.provenance if isinstance(r, dict) and r.get("tool") == "tree" and "position" in r]
            home = anc[-1] if anc else None
            if not recs or home is None or any(c.kind == "marker" for c in home.children):
                continue
            todo.append((home, recs[-1]))
        if not todo:
            self.status.setText("every saved tree has its marker")
            return 0
        a._checkpoint(f"add markers to {len(todo)} trees")
        for home, rec in todo:
            what = rec.get("object", TREE)
            fit = {"radius": rec.get("radius"), "girth": rec.get("girth"), "rms": rec.get("rms"),
                   "points": rec.get("points_fitted"), "dropped": rec.get("points_dropped"), "coverage": rec.get("coverage")}  # fmt: skip
            mk = m.add(Marker(np.asarray(rec["position"], np.float64), _marker_source(rec, fit)), POSITION)
            mk.label = what if what in known else ""
            m.move([mk.id], home.id)
            home.children.insert(0, home.children.pop())  # the marker first, as for a new tree
        text = f"added {len(todo)} marker{'s' * (len(todo) != 1)} to trees saved before, from their bands' positions"
        a._changed(text)
        self.status.setText(text)
        return len(todo)

    def cancel(self):
        self.ground = self.ground_h = self.frame = None
        self.vol.keep_height = False
        self.status.setText("")
        self.fill_directories(keep=NEW)


def _marker_source(record: dict, fit: dict) -> dict:
    """A tree's (or pole's) marker's source: placed by the tree tool at the band's circle centre, at the ground."""
    return {"tool": "tree", "object": record.get("object", TREE), "across": "circle", "height": "ground",
            "points": fit.get("points"), "points_dropped": fit.get("dropped"), "radius": fit.get("radius"),
            "girth": fit.get("girth"), "rms": fit.get("rms"), "coverage": fit.get("coverage"),
            "ground": record.get("ground"), "ground_h": record.get("ground_h"), "band": record.get("band"),
            "height_axis": record.get("height_axis"), "axis": record.get("axis")}  # fmt: skip
