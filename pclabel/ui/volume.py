"""3D select in the app (U): a box or a cylinder standing along the height axis, or a closed curve (loop) extruded
along its plane's normal (``tools.volume``).

- Box and cylinder: click the cloud to place it on the surface under the cursor; clicking again moves it there.
- Loop ("extrude select"): pick a closed curve in the Annotations panel and press "use selected curve" (or switch
  the shape to loop with it selected). The loop is swept along its plane's normal from bottom to top. "inset" pulls
  it in evenly by that many cm (negative: pushes it out), "scale" and "turn" act about its centre, and a click
  moves its centre to the clicked point.
- W A S D move the volume across its axis (W away from you, as the view faces), Q E down and up along it, Z X turn
  a box or loop, - and = pull a loop's outline in and push it out by 1 cm. Shift: 5 times as far. The step is set
  in the panel.
- Enter selects the points inside; Shift+Enter adds them to the selection, Ctrl+Enter takes them out. The panel's
  buttons do the same.
- K (or "clip to this") clips the view to the volume instead: only what is inside stays visible (and selectable).
  With "clip live" the clip follows the volume as it moves. The Clip box panel turns it off and on.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from pclabel.tools.volume import (
    VolumeParams,
    box_spec,
    frame_axes,
    inset_outline,
    loop_centre,
    loop_spec,
    loop_transform,
    outline,
    outline_ring,
)
from pclabel.ui.docks import EXTRA_DECIMALS, _spin

COLOR = (0.2, 0.9, 1.0, 1.0)
CLIP_COLOR = (0.1, 0.45, 0.55, 1.0)  # the active clip volume, when the tool is off


class VolumeDock(QDockWidget):
    FIELDS = (  # name, label, factor (shown / stored), lo, hi, step, decimals, suffix
        ("length", "length", 1, 0.01, 200, 0.1, 2, " m"),
        ("width", "width", 1, 0.01, 200, 0.1, 2, " m"),
        ("radius", "radius", 1, 0.01, 100, 0.05, 2, " m"),
        ("inset", "inset", 100, -500, 500, 1, 1, " cm"),
        ("scale", "scale", 100, 10, 1000, 1, 1, " %"),
        ("bottom", "bottom", 1, -50, 50, 0.05, 2, " m"),
        ("top", "top", 1, -50, 100, 0.1, 2, " m"),
        ("turn", "turn", 1, -360, 360, 5, 1, "°"),
    )

    def __init__(self, parent=None):
        super().__init__("3D select", parent)
        self.setObjectName("volume")
        d = VolumeParams()
        w = QWidget()
        f = QFormLayout(w)
        hint = QLabel("With 3D select (U), click the cloud to place a box or cylinder, or use a closed curve as a loop. "
                      "W A S D move it, Q E lower and raise it, Z X turn it, - = pull a loop in or push it out (Shift: "
                      "5x). Enter selects what is inside; Shift+Enter adds, Ctrl+Enter subtracts.")  # fmt: skip
        hint.setWordWrap(True)
        f.addRow(hint)
        self.shape = QComboBox()
        self.shape.addItem("box", "box")
        self.shape.addItem("cylinder", "cylinder")
        self.shape.addItem("loop: a closed curve, extruded", "loop")
        f.addRow("shape", self.shape)
        lrow = QHBoxLayout()
        self.use_curve = QPushButton("use selected curve")
        self.use_curve.setToolTip("the closed curve selected in the Annotations panel (or being edited)")
        self.curve_label = QLabel("")
        lrow.addWidget(self.use_curve)
        lrow.addWidget(self.curve_label, 1)
        self.curve_row = QWidget()
        self.curve_row.setLayout(lrow)
        f.addRow("loop", self.curve_row)
        self.spins = {}
        for name, label, k, lo, hi, step, dec, suffix in self.FIELDS:
            sp = _spin(lo, hi, getattr(d, name) * k, step, dec + EXTRA_DECIMALS, suffix)
            f.addRow(label, sp)
            self.spins[name] = (sp, k)
        self.spins["inset"][0].setToolTip("pull the loop in evenly by this much (the same distance everywhere, e.g. off "
                                          "the kerb line); negative pushes it out")  # fmt: skip
        self.spins["bottom"][0].setToolTip("from the clicked point (box, cylinder) or the curve's plane (loop), along "
                                           "the height axis or the curve's plane normal")  # fmt: skip
        self.step = _spin(0.1, 500, 10.0, 1.0, 2, " cm")
        self.step.setToolTip("how far W A S D Q E move the volume (Shift: 5x); Z X turn by 5° (Shift: 25°)")
        f.addRow("step", self.step)
        self.verticals = QSpinBox()
        self.verticals.setRange(0, 512)
        self.verticals.setValue(24)
        self.verticals.setToolTip("upright lines drawn round the volume, evenly spaced, to see what it cuts")
        f.addRow("vertical lines", self.verticals)
        crow = QHBoxLayout()
        self.clip = QPushButton("clip to this (K)")
        self.clip.setToolTip("show only what is inside the volume (and select only that); the Clip box panel turns "
                             "it off")  # fmt: skip
        self.unclip = QPushButton("unclip")
        self.live = QCheckBox("clip live")
        self.live.setToolTip("the clip follows the volume while you move and size it")
        crow.addWidget(self.clip)
        crow.addWidget(self.unclip)
        crow.addWidget(self.live)
        f.addRow(crow)
        row = QHBoxLayout()
        self.select = QPushButton("select (Enter)")
        self.add = QPushButton("add (Shift+Enter)")
        self.subtract = QPushButton("subtract (Ctrl+Enter)")
        for b in (self.select, self.add, self.subtract):
            row.addWidget(b)
        f.addRow(row)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        f.addRow(self.status)
        self.setWidget(w)
        self.shape.currentIndexChanged.connect(self._show_fields)
        self._show_fields()

    def _show_fields(self):
        shape = self.shape.currentData()
        f = self.widget().layout()
        for name, visible in (("length", shape == "box"), ("width", shape == "box"), ("turn", shape != "cylinder"),
                              ("radius", shape == "cylinder"), ("inset", shape == "loop"), ("scale", shape == "loop")):
            f.setRowVisible(self.spins[name][0], visible)  # fmt: skip
        f.setRowVisible(self.curve_row, shape == "loop")

    def params(self) -> VolumeParams:
        return replace(VolumeParams(), shape=self.shape.currentData(),
                       **{n: sp.value() / k for n, (sp, k) in self.spins.items()})  # fmt: skip


class VolumeController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.vp = window.viewport
        self.dock = VolumeDock(window)
        self.base = None  # local point a box or cylinder stands on
        self.loop_id = None  # the closed curve used as the loop
        self.move = np.zeros(3)  # how far the loop has been moved from the curve (local)
        self._mask_key, self._mask_spec = None, None  # the loop's last mask, reused while it only moves
        d = self.dock
        d.shape.currentIndexChanged.connect(self._shape_changed)
        for sp, _ in d.spins.values():
            sp.valueChanged.connect(lambda _: self.refresh())
        d.use_curve.clicked.connect(lambda: self.use_selected_curve())
        d.select.clicked.connect(lambda: self.confirm("replace"))
        d.add.clicked.connect(lambda: self.confirm("add"))
        d.subtract.clicked.connect(lambda: self.confirm("subtract"))
        d.clip.clicked.connect(lambda: self.clip_to_this())
        d.unclip.clicked.connect(lambda: self.w.set_volume_clip(None))
        d.live.toggled.connect(lambda on: self.clip_to_this() if on else None)
        d.verticals.valueChanged.connect(lambda _: self.refresh())
        self.vp.volume_placed.connect(self.place)
        self.vp.volume_key.connect(self.key)
        self.vp.tool_changed.connect(lambda _: self.refresh())

    def frame(self):
        """(axis, along, name) of the active height axis, world."""
        f = self.w.frames[self.w.active_frame]
        return np.asarray(f.normal, np.float64), np.asarray(f.along, np.float64), f.name

    def set_project(self):
        self.base, self.loop_id, self.move = None, None, np.zeros(3)
        self.dock.status.setText("")
        self.dock.curve_label.setText("")

    def _shape(self) -> str:
        return self.dock.shape.currentData()

    def _shape_changed(self):
        if self._shape() == "loop" and self._loop() is None:
            self.use_selected_curve(quiet=True)
        self.refresh()

    # ---- the loop -------------------------------------------------------------------------------------------

    def _loop(self):
        if self.loop_id is None:
            return None
        try:
            c = self.w.annot.model.node(self.loop_id)
        except KeyError:
            return None
        return c if c.kind == "curve" and c.closed else None

    def use_selected_curve(self, quiet: bool = False):
        """The closed curve being edited or selected in the Annotations panel becomes the loop."""
        a = self.w.annot
        cands = ([a.editing] if a.editing is not None else []) + a._selected_items()
        c = next((x for x in cands if x.kind == "curve" and x.closed), None)
        if c is None:
            if not quiet:
                self.dock.status.setText("select a closed curve in the Annotations panel first (tick \"closed loop\" "
                                         "on a curve to close it)")  # fmt: skip
            return None
        self.loop_id, self.move = c.id, np.zeros(3)
        self.dock.curve_label.setText(c.name)
        self.refresh()
        return c

    def loop_geometry(self):
        """(origin (local), normal, u, loop (m, 2) as selected, outline (m, 2) as drawn) of the moved, scaled, turned
        loop; None without a loop."""
        c = self._loop()
        if c is None:
            return None
        p = self.dock.params()
        off = self.w.project.offset
        n, u, v = frame_axes(c.normal, (1.0, 0.0, 0.0))
        origin = c.point - off + self.move
        q = c.polyline() - c.point
        poly = loop_transform(np.c_[q @ u, q @ v], p.scale, p.turn)
        return origin, n, u, poly, inset_outline(poly, p.inset)

    # ---- input --------------------------------------------------------------------------------------------------

    def place(self, p_local):
        if self._shape() == "loop":
            g = self.loop_geometry()
            if g is None:
                self.dock.status.setText("no closed curve to use as the loop")
                return
            origin, n, u, poly, _ = g
            centre = origin + loop_centre(poly) @ np.array([u, np.cross(n, u)])
            d = np.asarray(p_local, np.float64) - centre
            self.move = self.move + d - np.dot(d, n) * n  # across the loop's normal only
        else:
            self.base = np.asarray(p_local, np.float64)
        self.refresh()

    def _axis(self):
        if self._shape() == "loop" and self._loop() is not None:
            return self._loop().normal
        return self.frame()[0]

    def key(self, key: int, mods):
        """W A S D Q E move, Z X turn, - = inset, Enter confirms (from the viewport while the tool is active)."""
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirm("add" if shift else "subtract" if mods & Qt.KeyboardModifier.ControlModifier else "replace")
            return
        if key == Qt.Key.Key_K:
            self.clip_to_this()
            return
        loop = self._shape() == "loop"
        if (loop and self._loop() is None) or (not loop and self.base is None):
            self.dock.status.setText("use a closed curve first" if loop else "click the cloud to place the volume first")
            return
        factor = 5.0 if shift else 1.0
        if key in (Qt.Key.Key_Z, Qt.Key.Key_X):
            sp = self.dock.spins["turn"][0]
            sp.setValue(sp.value() + (5.0 if key == Qt.Key.Key_X else -5.0) * factor)  # refreshes
            return
        if key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore, Qt.Key.Key_Equal, Qt.Key.Key_Plus):
            if loop:
                sp = self.dock.spins["inset"][0]  # cm
                sp.setValue(sp.value() + (1.0 if key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore) else -1.0) * factor)
            return
        n = self._axis()
        r, up, fwd = self.vp.camera.axes()
        ahead = (fwd - np.dot(fwd, n) * n) + (up - np.dot(up, n) * n)  # works looking down and looking ahead
        ahead /= max(np.linalg.norm(ahead), 1e-12)
        right = np.cross(ahead, n)
        step = self.dock.step.value() / 100 * factor
        move = {Qt.Key.Key_W: ahead, Qt.Key.Key_S: -ahead, Qt.Key.Key_D: right, Qt.Key.Key_A: -right,
                Qt.Key.Key_E: n, Qt.Key.Key_Q: -n}.get(key)  # fmt: skip
        if move is not None:
            if loop:
                self.move = self.move + step * move
            else:
                self.base = self.base + step * move
            self.refresh()

    def spec(self):
        """The current volume as a tools.volume.VolumeSpec (local), or None if there is none yet."""
        p = self.dock.params()
        if self._shape() == "loop":
            g = self.loop_geometry()
            if g is None:
                return None
            origin, n, u, poly, _ = g
            key = (self.loop_id, self._loop().polyline().tobytes(), p.scale, p.turn, p.inset)
            if self._mask_key == key:  # only moved or heightened: keep the mask (no new texture upload)
                return replace(self._mask_spec, origin=origin, bottom=p.bottom, top=p.top,
                               label=f"{self._loop().name}, inset {p.inset * 100:+.0f} cm, {p.bottom:+.2f} to "
                                     f"{p.top:+.2f} m")  # fmt: skip
            spec = loop_spec(origin, n, u, poly, p.bottom, p.top, p.inset, self._loop().name)
            self._mask_key, self._mask_spec = key, spec
            return spec
        if self.base is None:
            return None
        axis, along, _ = self.frame()
        return box_spec(self.base, axis, along, p)

    def clip_to_this(self):
        spec = self.spec()
        if spec is None:
            self.dock.status.setText("place the volume (or pick a closed curve) first")
            return
        self.w.set_volume_clip(spec)

    def draw_clip(self):
        """The active clip volume's outline, while the tool is off (the tool draws its own volume)."""
        r = self.vp.renderer
        if r is None:
            return
        d = r.display
        lines = None
        if self.vp.tool != "volume" and d.clip_volume_on and d.clip_volume is not None:
            lines = [(q, CLIP_COLOR, 1.2) for q in d.clip_volume.outline(min(self.dock.verticals.value(), 16))]
        self.vp.makeCurrent()
        try:
            r.set_vectors(lines, None, layer="clip")
        finally:
            self.vp.doneCurrent()
        self.vp.update()

    def confirm(self, op: str):
        p = self.dock.params()
        if self._shape() == "loop":
            g = self.loop_geometry()
            if g is None:
                self.dock.status.setText("select a closed curve in the Annotations panel and press \"use selected curve\"")
                return
            origin, n, u, poly, _ = g
            c = self._loop()
            definition = {"tool": "select_volume", "shape": "loop", "op": op, "curve": c.name, "curve_id": c.id,
                          "move": self.move.tolist(), "scale": p.scale, "turn": p.turn, "inset": p.inset,
                          "bottom": p.bottom, "top": p.top}  # fmt: skip
            self.w.select_loop(origin, n, u, poly, p.bottom, p.top, p.inset, op, definition)
            return
        if self.base is None:
            self.dock.status.setText("click the cloud to place the volume first")
            return
        axis, along, name = self.frame()
        self.w.select_volume(self.base, axis, along, p, op, name)

    # ---- drawing ------------------------------------------------------------------------------------------------

    def refresh(self):
        r = self.vp.renderer
        if r is None:
            return
        lines = points = None
        if self.vp.tool == "volume":
            p = self.dock.params()
            if self._shape() == "loop":
                g = self.loop_geometry()
                if g is not None:
                    origin, n, u, poly, drawn = g
                    v = np.cross(n, u)
                    lines = [(q, COLOR, 2.0 if k < 2 else 1.5) for k, q in enumerate(
                        outline_ring(drawn, origin, n, u, v, p.bottom, p.top, self.dock.verticals.value()))]  # fmt: skip
                    centre = origin + loop_centre(poly) @ np.array([u, v])
                    points = [(centre[None], COLOR, 9.0)]
                    self.dock.status.setText(f"loop {self._loop().name}: inset {p.inset * 100:+.0f} cm, scale "
                                             f"{p.scale * 100:.0f} %, turn {p.turn:.0f}°, {p.bottom:+.2f} to "
                                             f"{p.top:+.2f} m along its normal")  # fmt: skip
            elif self.base is not None:
                axis, along, _ = self.frame()
                n, u, v = frame_axes(axis, along, p.turn)
                lines = [(q, COLOR, 2.0 if k < 2 else 1.5) for k, q in
                         enumerate(outline(self.base, n, u, v, p, verticals=self.dock.verticals.value()))]
                points = [(self.base[None], COLOR, 9.0)]
                size = f"{p.length:.2f} x {p.width:.2f} m" if p.shape == "box" else f"radius {p.radius:.2f} m"
                self.dock.status.setText(f"{p.shape} {size}, {p.bottom:+.2f} to {p.top:+.2f} m along the height axis; "
                                         f"base at {np.round(self.base + self.w.project.offset, 3).tolist()}")  # fmt: skip
        self.vp.makeCurrent()
        try:
            r.set_vectors(lines, points, layer="volume")
        finally:
            self.vp.doneCurrent()
        if self.vp.tool == "volume" and self.dock.live.isChecked() and lines is not None:
            self.clip_to_this()  # the clip follows the volume
        else:
            self.draw_clip()
        self.vp.update()
