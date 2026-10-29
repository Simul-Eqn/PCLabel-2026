"""Dock panels: display, clip box, point info, scan metadata, height axis, seed tools."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pclabel.render.shaders import MODE_NAMES


EXTRA_DECIMALS = 1  # tool panels take one more decimal than they show by default (finer thresholds, 2026-10-20)


def _spin(lo, hi, value, step, decimals=3, suffix=""):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setValue(value)
    s.setKeyboardTracking(False)
    if suffix:
        s.setSuffix(suffix)
    return s


class DisplayDock(QDockWidget):
    """Colour mode and its parameters, point size, EDL, background, point budget."""

    changed = Signal()
    budget_changed = Signal(int)
    auto_height = Signal()
    auto_intensity = Signal()

    def __init__(self, software: bool, point_budget: int, parent=None):
        super().__init__("Display", parent)
        self.setObjectName("display")
        w = QWidget()
        f = QFormLayout(w)
        self.mode = QComboBox()
        for k in sorted(MODE_NAMES):
            self.mode.addItem(MODE_NAMES[k], k)
        f.addRow("colour (C)", self.mode)
        ir = QHBoxLayout()
        self.imin, self.imax = _spin(0.0, 1.0, 0.0, 0.01, 3), _spin(0.0, 1.0, 1.0, 0.01, 3)
        self.iauto = QPushButton("auto")
        self.iauto.setToolTip("stretch the 1st to 99th percentile of intensity to black..white")
        for x in (self.imin, self.imax, self.iauto):
            ir.addWidget(x)
        f.addRow("intensity range", ir)
        self.gamma = _spin(0.1, 4.0, 0.6, 0.05, 2)
        f.addRow("intensity gamma", self.gamma)
        hr = QHBoxLayout()
        self.hmin, self.hmax = _spin(-1e5, 1e5, 0.0, 0.1, 3, " m"), _spin(-1e5, 1e5, 10.0, 0.1, 3, " m")
        self.hauto = QPushButton("auto")
        self.hauto.setToolTip("1st to 99th percentile of z")
        for x in (self.hmin, self.hmax, self.hauto):
            hr.addWidget(x)
        f.addRow("height range (z)", hr)
        self.period = _spin(0.2, 100.0, 3.0, 0.5, 2, " cm")
        f.addRow("wrap period ([ ])", self.period)
        self.h0 = _spin(-1e5, 1e5, 0.0, 0.5, 2, " cm")
        f.addRow("wrap offset ({ })", self.h0)
        self.size = _spin(1.0, 16.0, 2.0, 0.5, 1, " px")
        f.addRow("point size", self.size)
        self.adaptive = QCheckBox("adaptive (grows where the LOD is sparse)")
        self.adaptive.setChecked(True)
        f.addRow("", self.adaptive)
        self.max_size = _spin(1.0, 32.0, 6.0, 0.5, 1, " px")
        f.addRow("max adaptive size", self.max_size)
        self.round = QCheckBox("round points")
        f.addRow("", self.round)
        self.edl = QCheckBox("eye-dome lighting (Ctrl+E)")
        self.edl.setChecked(True)
        f.addRow("", self.edl)
        self.edl_strength = _spin(0.0, 5.0, 0.4, 0.05, 2)
        f.addRow("EDL strength", self.edl_strength)
        self.edl_radius = _spin(0.5, 6.0, 1.4, 0.1, 1, " px")
        f.addRow("EDL radius", self.edl_radius)
        self.bg = QPushButton()
        self.bg_color = QColor.fromRgbF(0.07, 0.07, 0.08)
        self._paint_bg()
        f.addRow("background", self.bg)
        self.budget = _spin(0.05, 20.0 if software else 200.0, point_budget / 1e6, 0.1 if software else 5.0, 2,
                            " M points")  # fmt: skip
        self.budget.setToolTip("points drawn per frame (the LOD shares them among visible tiles)")
        f.addRow("point budget", self.budget)
        self.setWidget(w)
        for s in (self.imin, self.imax, self.gamma, self.hmin, self.hmax, self.period, self.h0, self.size, self.max_size,
                  self.edl_strength, self.edl_radius):  # fmt: skip
            s.valueChanged.connect(self.changed)
        for c in (self.adaptive, self.round, self.edl):
            c.toggled.connect(self.changed)
        self.mode.currentIndexChanged.connect(self.changed)
        self.bg.clicked.connect(self._pick_bg)
        self.hauto.clicked.connect(self.auto_height)
        self.iauto.clicked.connect(self.auto_intensity)
        self.budget.valueChanged.connect(lambda v: self.budget_changed.emit(int(v * 1e6)))

    def _paint_bg(self):
        self.bg.setStyleSheet(f"background-color: {self.bg_color.name()}; min-height: 18px;")

    def _pick_bg(self):
        c = QColorDialog.getColor(self.bg_color, self, "Background")
        if c.isValid():
            self.bg_color = c
            self._paint_bg()
            self.changed.emit()

    def apply(self, d):
        """Copy the controls into a renderer Display."""
        d.mode = self.mode.currentData()
        d.gamma = self.gamma.value()
        d.intensity_range = (self.imin.value(), self.imax.value())
        d.height_range = (self.hmin.value(), self.hmax.value())
        d.period = self.period.value() / 100
        d.h0 = self.h0.value() / 100
        d.point_size = self.size.value()
        d.adaptive = self.adaptive.isChecked()
        d.max_size = self.max_size.value()
        d.round_points = self.round.isChecked()
        d.edl = self.edl.isChecked()
        d.edl_strength = self.edl_strength.value()
        d.edl_radius = self.edl_radius.value()
        d.background = (self.bg_color.redF(), self.bg_color.greenF(), self.bg_color.blueF())

    def set_mode(self, mode: int):
        self.mode.setCurrentIndex(self.mode.findData(mode))


class ClipDock(QDockWidget):
    """The clips: an axis-aligned box in world coordinates (scan frame), the 3D select volume and point sets."""

    changed = Signal()
    from_view = Signal()
    fit_scene = Signal()
    use_set = Signal()

    NO_SET = "no point set yet: select point sets or directories in the Annotations panel, then \"use the selected\""

    def __init__(self, parent=None):
        super().__init__("Clips", parent)
        self.setObjectName("clip")
        w = QWidget()
        v = QVBoxLayout(w)
        self.on = QCheckBox("clip to the box (K)")
        v.addWidget(self.on)
        self.volume_on = QCheckBox("clip to the 3D select volume")
        self.volume_on.setToolTip("set with \"clip to this\" (K) in 3D select: a box, cylinder or loop")
        self.volume_on.setEnabled(False)
        self.volume_label = QLabel("no volume yet: use \"clip to this\" in 3D select (U)")
        self.volume_label.setWordWrap(True)
        v.addWidget(self.volume_on)
        v.addWidget(self.volume_label)
        self.set_on = QCheckBox("clip to point sets (Ctrl+Shift+K)")
        self.set_on.setToolTip("show only the points of the chosen point sets; every tool reads and selects only them")
        self.set_on.setEnabled(False)
        self.set_label = QLabel(self.NO_SET)
        self.set_label.setWordWrap(True)
        self.set_use = QPushButton("use the selected point sets (Ctrl+K)")
        self.set_use.setToolTip("the point sets selected in the Annotations panel; a directory: all its point sets")
        v.addWidget(self.set_on)
        v.addWidget(self.set_label)
        v.addWidget(self.set_use)
        v.addWidget(QLabel("box (scan frame):"))
        g = QGridLayout()
        g.addWidget(QLabel("min"), 0, 1)
        g.addWidget(QLabel("max"), 0, 2)
        self.lo, self.hi = [], []
        for i, a in enumerate("xyz"):
            g.addWidget(QLabel(a), i + 1, 0)
            lo, hi = _spin(-1e7, 1e7, 0.0, 0.1, 4, " m"), _spin(-1e7, 1e7, 0.0, 0.1, 4, " m")
            g.addWidget(lo, i + 1, 1)
            g.addWidget(hi, i + 1, 2)
            self.lo.append(lo)
            self.hi.append(hi)
            lo.valueChanged.connect(self.changed)
            hi.valueChanged.connect(self.changed)
        v.addLayout(g)
        h = QHBoxLayout()
        b1, b2 = QPushButton("from view (Shift+K)"), QPushButton("whole scene")
        b1.setToolTip("BEV: the visible rectangle; other views: a box around the orbit point")
        h.addWidget(b1)
        h.addWidget(b2)
        v.addLayout(h)
        v.addStretch(1)
        self.setWidget(w)
        self.on.toggled.connect(self.changed)
        self.volume_on.toggled.connect(self.changed)
        self.set_on.toggled.connect(self.changed)
        self.set_use.clicked.connect(self.use_set)
        b1.clicked.connect(self.from_view)
        b2.clicked.connect(self.fit_scene)

    def set_box(self, lo, hi):
        for s, val in zip(self.lo + self.hi, list(lo) + list(hi)):
            s.blockSignals(True)
            s.setValue(float(val))
            s.blockSignals(False)
        self.changed.emit()

    def apply(self, d):
        d.clip_volume_on = self.volume_on.isChecked() and d.clip_volume is not None
        d.clip_set_on = self.set_on.isChecked() and self.set_on.isEnabled()
        d.clip_on = self.on.isChecked()
        d.clip_min = tuple(s.value() for s in self.lo)
        d.clip_max = tuple(s.value() for s in self.hi)


class PointInfoDock(QDockWidget):
    """Attributes of the picked point (click in the view)."""

    def __init__(self, parent=None):
        super().__init__("Point info", parent)
        self.setObjectName("point_info")
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["attribute", "value"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.setWidget(self.tree)
        self.show_rows([("", "click a point in the view")])

    def show_rows(self, rows):
        self.tree.clear()
        for k, v in rows:
            it = QTreeWidgetItem([k, v])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsSelectable)
            self.tree.addTopLevelItem(it)
        self.tree.resizeColumnToContents(0)


class ScanMetaDock(QDockWidget):
    """User-filled scan metadata, saved in project.json (intensity and RGB depend on it)."""

    def __init__(self, parent=None):
        super().__init__("Scan metadata", parent)
        self.setObjectName("scan_meta")
        w = QWidget()
        f = QFormLayout(w)
        self.fields = {}
        for key, label, hint in (
            ("scanner", "scanner", "e.g. Leica RTC360"),
            ("datetime", "date and time", "e.g. 2025-09-11 10:30"),
            ("weather", "weather", "e.g. overcast, after rain"),
            ("surface_wetness", "surface wetness", "dry / damp / wet / puddles"),
            ("settings", "scanner settings", "resolution, quality, HDR imaging"),
        ):
            e = QLineEdit()
            e.setPlaceholderText(hint)
            f.addRow(label, e)
            self.fields[key] = e
        self.capture = QComboBox()
        self.capture.addItems(["static", "mobile"])
        self.capture.setToolTip("mobile (SLAM) scans have no single scanner position: range is not shown")
        f.addRow("capture", self.capture)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("anything else about the capture")
        f.addRow("notes", self.notes)
        self.setWidget(w)
        self.project = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(600)
        self._timer.timeout.connect(self.save)
        for e in self.fields.values():
            e.textEdited.connect(lambda _=None: self._timer.start())
        self.capture.currentTextChanged.connect(lambda _=None: self._timer.start())
        self.notes.textChanged.connect(self._timer.start)
        self.setEnabled(False)

    def load(self, project):
        self.project = None
        meta = project.meta["scan_metadata"] if project else {}
        for k, e in self.fields.items():
            e.setText(meta.get(k, ""))
        self.capture.setCurrentText(meta.get("capture", "static") or "static")
        self.notes.setPlainText(meta.get("notes", ""))
        self.project = project
        self.setEnabled(project is not None)

    def save(self):
        if self.project is None:
            return
        vals = {k: e.text() for k, e in self.fields.items()}
        vals["capture"] = self.capture.currentText()
        vals["notes"] = self.notes.toPlainText()
        self.project.set_scan_metadata(**vals)


def fmt_point_rows(project, taxonomy, pick, scan_of_source) -> list[tuple[str, str]]:
    """Info-panel rows for a picked store index."""
    i = pick.index
    src_i = int(project.source_index[i])
    raw_i = int(project.raw_index()[src_i])
    p = np.asarray(project.xyz[i], np.float64) + project.offset
    names = {int(c["id"]): c["name"] for c in taxonomy.classes}
    lab = int(project.labels[i])
    fl = int(project.flags[i])
    flag_names = [f["name"] for f in taxonomy.flags if fl & (1 << int(f["bit"]))]
    rows = [
        ("x, y, z (scan frame)", f"{p[0]:.3f}, {p[1]:.3f}, {p[2]:.3f} m"),
        ("class", f"{lab} {names.get(lab, '?')}"),
        ("flags", ", ".join(flag_names) or "none"),
    ]
    if project.rgb is not None:
        r, g, b = (int(v) for v in project.rgb[i])
        rows.append(("RGB", f"{r}, {g}, {b}"))
    if project.intensity is not None:
        v = int(project.intensity[i])
        rows.append(("intensity", f"{v / 65535:.4f}  (raw {v})"))
    k = scan_of_source(src_i)
    if project.static_capture:
        o = project.scan_origins[k] + project.offset
        rows.append(("range from scanner", f"{np.linalg.norm(p - o):.3f} m (scan {k})"))
    else:
        rows.append(("range from scanner", "n/a (mobile capture)"))
    for r, c in pick.within.items():
        rows.append((f"points within {r * 100:.0f} cm", f"{c:,}"))
    rows += [
        ("store index", f"{i:,}"),
        ("source index (export order)", f"{src_i:,}"),
        ("raw record index", f"{raw_i:,}"),
        ("tile", f"{pick.tile}"),
        ("provenance", "no label operations yet (the journal arrives in M3)"),
    ]
    return rows


class FrameDock(QDockWidget):
    """Selection tools, the height axis (plane frames fitted to a selection) and the height slice."""

    slice_changed = Signal()
    frame_chosen = Signal(int)
    fit_requested = Signal()
    delete_requested = Signal()
    rename_requested = Signal()
    export_requested = Signal()
    clear_requested = Signal()
    tool_requested = Signal(object)  # "lasso" | "rect"
    depth_mode_chosen = Signal(str)  # "visible" | "through"

    def __init__(self, parent=None):
        super().__init__("Height axis and selection", parent)
        self.setObjectName("frames")
        w = QWidget()
        f = QFormLayout(w)
        row = QHBoxLayout()
        self.lasso, self.rect, self.clear = QPushButton("lasso (L)"), QPushButton("rectangle (R)"), QPushButton("clear (Esc)")
        for b in (self.lasso, self.rect):
            b.setCheckable(True)
        for b in (self.lasso, self.rect, self.clear):
            row.addWidget(b)
        f.addRow("select", row)
        self.depth_mode = QComboBox()
        self.depth_mode.addItem("visible: only what you see", "visible")
        self.depth_mode.addItem("through: hidden points too", "through")
        self.depth_mode.setToolTip("visible: points hidden behind others are left out (the drawn surface decides); "
                                   "through: every point inside the shape, at any depth (O switches)")  # fmt: skip
        self.depth_mode.activated.connect(lambda _: self.depth_mode_chosen.emit(self.depth_mode.currentData()))
        f.addRow("lasso / rect", self.depth_mode)
        self.sel_label = QLabel("nothing selected. Shift adds, Ctrl subtracts.")
        self.sel_label.setWordWrap(True)
        f.addRow("", self.sel_label)
        self.frame = QComboBox()
        f.addRow("height axis (H)", self.frame)
        self.fit = QPushButton("fit plane to selection (F)")
        self.fit.setToolTip("robust plane through the selected points; its normal becomes the height axis")
        self.delete = QPushButton("delete plane")
        self.delete.setToolTip("remove the plane shown above from the project")
        self.rename = QPushButton("rename...")
        self.rename.setToolTip("give the plane shown above another name")
        self.export = QPushButton("export plane...")
        self.export.setToolTip("save the plane shown above (its equation and fit) as JSON")
        prow = QHBoxLayout()
        prow.addWidget(self.fit)
        prow.addWidget(self.delete)
        prow.addWidget(self.rename)
        prow.addWidget(self.export)
        f.addRow("", prow)
        self.report = QLabel("")
        self.report.setWordWrap(True)
        self.report.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        f.addRow("", self.report)
        srow = QHBoxLayout()
        self.slice_on = QCheckBox("only")
        self.slice_a, self.slice_b = _spin(-1e4, 1e4, -10.0, 1.0, 2, " cm"), _spin(-1e4, 1e4, 30.0, 1.0, 2, " cm")
        for x in (self.slice_on, self.slice_a, QLabel("to"), self.slice_b):
            srow.addWidget(x)
        f.addRow("slice (Shift+H)", srow)
        self.setWidget(w)
        self.lasso.clicked.connect(lambda on: self.tool_requested.emit("lasso" if on else None))
        self.rect.clicked.connect(lambda on: self.tool_requested.emit("rect" if on else None))
        self.clear.clicked.connect(self.clear_requested)
        self.fit.clicked.connect(self.fit_requested)
        self.delete.clicked.connect(self.delete_requested)
        self.rename.clicked.connect(self.rename_requested)
        self.export.clicked.connect(self.export_requested)
        self.frame.activated.connect(self.frame_chosen)
        self.slice_on.toggled.connect(self.slice_changed)
        self.slice_a.valueChanged.connect(self.slice_changed)
        self.slice_b.valueChanged.connect(self.slice_changed)

    def show_tool(self, name):
        self.lasso.setChecked(name == "lasso")
        self.rect.setChecked(name == "rect")

    def set_frames(self, names: list[str], active: int):
        self.frame.blockSignals(True)
        self.frame.clear()
        self.frame.addItems(names)
        self.frame.setCurrentIndex(active)
        self.frame.blockSignals(False)

    def apply(self, d):
        d.band_on = self.slice_on.isChecked()
        d.band = (self.slice_a.value() / 100, self.slice_b.value() / 100)


def fmt_frame_report(frame) -> str:
    r = frame.report
    if not r:
        return "heights are world z (the scan frame)."
    warn = f"WARNING: {r['warning']}\n" if r.get("warning") else ""
    return warn + (f"{r['points_kept']:,} of {r['points_selected']:,} points kept "
            f"({r['points_fitted']:,} fitted)\n"
            f"tilt {r['tilt_deg']:.3f}° from vertical; RMS {r['rms_m'] * 1000:.1f} mm\n"
            f"grade {r['grade_percent']:+.2f} % along azimuth {r['along_azimuth_deg']:.1f}°\n"
            f"crossfall {r['crossfall_percent']:+.2f} % across\n"
            f"smallest useful wrap period about {r['suggested_min_period_m'] * 100:.1f} cm (4 x RMS)")  # fmt: skip


class SeedToolDock(QDockWidget):
    """A seed tool's panel: its thresholds (changed live), a legend of where it stopped, and what the last run
    found. Subclasses set the fields."""

    params_changed = Signal()
    TITLE = OBJECT = HINT = LEGEND = ""
    FIELDS = ()  # name, label, unit factor (shown / stored), lo, hi, step, decimals, suffix, tooltip
    CHECKS = ()  # name, label, tooltip (boolean parameters)
    SHOW_STOPS = True

    def __init__(self, defaults, parent=None):
        super().__init__(self.TITLE, parent)
        self.setObjectName(self.OBJECT)
        self._defaults = defaults
        w = QWidget()
        f = QFormLayout(w)
        self.form = f
        hint = QLabel(self.HINT)
        hint.setWordWrap(True)
        f.addRow(hint)
        self.spins = {}
        for name, label, k, lo, hi, step, dec, suffix, tip in self.FIELDS:
            sp = _spin(lo, hi, getattr(defaults, name) * k, step, dec + EXTRA_DECIMALS, suffix)
            sp.setToolTip(tip)
            sp.valueChanged.connect(self.params_changed)
            f.addRow(label, sp)
            self.spins[name] = (sp, k)
        self.checks = {}
        for name, label, tip in self.CHECKS:
            cb = QCheckBox(label)
            cb.setChecked(bool(getattr(defaults, name)))
            cb.setToolTip(tip)
            cb.toggled.connect(lambda _: self.params_changed.emit())
            f.addRow("", cb)
            self.checks[name] = cb
        reset = QPushButton("defaults")
        reset.clicked.connect(self.reset)
        f.addRow("", reset)
        self.show_stops = QCheckBox("show where it stopped")
        self.show_stops.setChecked(self.SHOW_STOPS)
        self.show_stops.setToolTip("coloured points where the tool refused to go on, by the first test that failed")
        f.addRow("", self.show_stops)
        self.legend = QLabel(self.LEGEND)
        f.addRow("", self.legend)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        f.addRow(self.result)
        self.setWidget(w)

    def params(self):
        from dataclasses import replace

        values = {n: sp.value() / k for n, (sp, k) in self.spins.items()}
        values.update({n: cb.isChecked() for n, cb in self.checks.items()})
        return replace(self._defaults, **values)

    def reset(self):
        for n, (sp, k) in self.spins.items():
            sp.blockSignals(True)
            sp.setValue(getattr(self._defaults, n) * k)
            sp.blockSignals(False)
        for n, cb in self.checks.items():
            cb.blockSignals(True)
            cb.setChecked(bool(getattr(self._defaults, n)))
            cb.blockSignals(False)
        self.params_changed.emit()


def _legend(*items) -> str:
    """HTML: a coloured square and a name per (hex colour, name), three per line."""
    parts = [f'<span style="color:{c}">&#9632;</span> {name}' for c, name in items]
    return "<br>".join(" &nbsp; ".join(parts[i : i + 3]) for i in range(0, len(parts), 3))


class GrowDock(SeedToolDock):
    """Grow select (G), in two modes: height and intensity (``tools.ground``, cells) or geometric (``tools.normals``,
    surface normals). Each mode shows its own thresholds."""

    TITLE, OBJECT = "Grow select", "grow"
    HINT = ("With grow select (G), click a surface: it grows over what is connected to it. Height and intensity: "
            "ground smooth in height and similar in intensity. Geometric: surfaces whose normals turn slowly "
            "(walls, kerb faces, poles too). Shift+click adds, Ctrl+click subtracts; a changed threshold or mode "
            "re-runs the last click.")  # fmt: skip
    NORMAL_FIELDS = (
        ("smoothness", "smoothness", 1, 0.1, 90, 0.5, 1, "°",
         "largest angle between the normals of neighbouring voxels (PCL's smoothness threshold)"),
        ("curvature", "curvature", 1, 0.0, 0.34, 0.005, 3, "",
         "voxels at most this curved carry the growth on (0 on a plane, 1/3 for scatter; PCL's curvature threshold)"),
        ("voxel", "voxel", 100, 1, 20, 0.5, 1, " cm", "the grid the normals are computed on"),
        ("normal_radius", "normal radius", 100, 2, 100, 1, 0, " cm", "voxels within this give a voxel's normal"),
        ("connect", "neighbours within", 100, 1, 50, 0.5, 1, " cm",
         "voxels this close are neighbours for the growth (1.5 voxels: touching ones)"),
        ("radius", "reach", 1, 1, 60, 1, 0, " m", "half-size of the cube around the click the growth may cover"),
    )  # fmt: skip
    LEGEND = _legend(("#ff3030", "height step"), ("#ff30ff", "roughness"), ("#20e0e0", "intensity step"),
                     ("#4070ff", "intensity vs road"), ("#30e030", "gap too wide"))  # fmt: skip
    FIELDS = (
        ("max_step", "height step", 100, 0.1, 50, 0.5, 1, " cm",
         "largest height change between neighbouring cells: kerbs are bigger, road roughness smaller"),
        ("intensity_step", "intensity step", 1, 0.0, 1.0, 0.005, 3, "",
         "largest intensity change between neighbouring cells (intensity 0..1, as in Point info)"),
        ("intensity_range", "intensity vs road", 1, 0.0, 1.0, 0.01, 3, "",
         "largest difference from the local road intensity (a running average along the grow); 1 switches it off"),
        ("intensity_memory", "road memory", 1, 0.1, 1000, 0.5, 1, " m",
         "how much grown road the running average remembers: short follows slow changes, long stays near the seed"),
        ("marking_width", "marking width", 100, 0, 300, 5, 0, " cm",
         "strips of other intensity up to this wide (road markings) are bridged; wider areas (walkways) stop the grow"),
        ("max_gap", "gap", 100, 0, 500, 10, 0, " cm",
         "empty cells (no points: far scan lines, pole shadows) the grow may step over"),
        ("max_slope", "slope across gaps", 100, 0, 50, 1, 0, " %",
         "extra height allowed per metre across a gap (road grade and crossfall); kerbs are steeper"),
        ("max_roughness", "roughness", 100, 0.1, 50, 0.5, 1, " cm",
         "cells rougher than this (kerb faces, vegetation, objects) are not ground"),
        ("surface_tol", "surface tolerance", 100, 0.5, 50, 0.5, 1, " cm",
         "points this close to their cell's surface are selected; drains below and objects above are not"),
        ("cell", "cell size", 100, 2, 100, 1, 0, " cm", "the grid the grow runs on"),
        ("radius", "reach", 1, 2, 300, 5, 0, " m", "how far from the seed the grow may go"),
    )  # fmt: skip

    def __init__(self, parent=None):
        from pclabel.tools.ground import GroundGrowParams
        from pclabel.tools.normals import NormalGrowParams

        super().__init__(GroundGrowParams(), parent)
        self._normal_defaults = NormalGrowParams()
        self.mode = QComboBox()
        self.mode.addItem("height and intensity", "height")
        self.mode.addItem("geometric: surface normals", "normals")
        self.mode.setToolTip("height and intensity: ground (cells, kerbs and walkways stop it); geometric: any "
                             "surface, by its normals (PCL's region growing)")  # fmt: skip
        self.form.insertRow(1, "mode", self.mode)
        self.normal_spins = {}
        row = 2 + len(self.FIELDS)
        for k, (name, label, f, lo, hi, step, dec, suffix, tip) in enumerate(self.NORMAL_FIELDS):
            sp = _spin(lo, hi, getattr(self._normal_defaults, name) * f, step, dec + EXTRA_DECIMALS, suffix)
            sp.setToolTip(tip)
            sp.valueChanged.connect(self.params_changed)
            self.form.insertRow(row + k, label, sp)
            self.normal_spins[name] = (sp, f)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self._mode_changed(emit=False)

    def current_mode(self) -> str:
        return self.mode.currentData()

    def _mode_changed(self, _=None, emit: bool = True):
        geometric = self.current_mode() == "normals"
        for sp, _ in self.spins.values():
            self.form.setRowVisible(sp, not geometric)
        for sp, _ in self.normal_spins.values():
            self.form.setRowVisible(sp, geometric)
        self.form.setRowVisible(self.show_stops, not geometric)
        self.form.setRowVisible(self.legend, not geometric)
        if emit:
            self.params_changed.emit()

    def params(self):
        from dataclasses import replace

        if self.current_mode() == "normals":
            return replace(self._normal_defaults, **{n: sp.value() / k for n, (sp, k) in self.normal_spins.items()})
        return super().params()

    def reset(self):
        for n, (sp, k) in self.normal_spins.items():
            sp.blockSignals(True)
            sp.setValue(getattr(self._normal_defaults, n) * k)
            sp.blockSignals(False)
        super().reset()


class MagicDock(SeedToolDock):
    """Magic select by intensity (I)."""

    TITLE, OBJECT = "Magic select", "magic"
    HINT = ("With magic select (I), click a road marking: it selects the connected ground of similar intensity, "
            "like the magic wand of an image editor, on a 2 cm intensity picture of the ground. Shift+click adds "
            "(the next dash), Ctrl+click subtracts.")  # fmt: skip
    LEGEND = _legend(("#4070ff", "intensity"), ("#ff3030", "height step"), ("#30e030", "gap too wide"))
    SHOW_STOPS = False  # the outline of every marking: only on request
    FIELDS = (
        ("tolerance", "tolerance", 1, 1.0, 100.0, 0.1, 2, " x",
         "a factor: intensities from the clicked one / tolerance to the clicked one x tolerance join (2: from half "
         "to twice). The spread of paint grows with its intensity, so a factor separates paint from road"),
        ("memory", "follow drift", 1, 0.0, 1000, 0.5, 1, " m",
         "0: compare with the clicked pixel, as an image editor does. More: compare with a running average over "
         "about this much of the fill, to follow a long line whose intensity fades with range"),
        ("max_gap", "gap", 100, 0, 200, 2, 0, " cm",
         "empty strips up to this wide are bridged: far from the scanner the points lie on scan lines several cm "
         "apart. Lower it if two markings close together merge"),
        ("max_step", "height step", 100, 0.1, 50, 0.5, 1, " cm",
         "largest height change between neighbouring pixels: keeps the fill off kerbs"),
        ("pixel", "pixel size", 100, 0.5, 10, 0.5, 1, " cm",
         "the intensity picture's resolution: selection edges are this precise; smaller needs denser points"),
        ("surface_tol", "surface tolerance", 100, 0.5, 50, 0.5, 1, " cm",
         "only points this close to the ground surface are in the picture (and can be selected)"),
        ("radius", "reach", 1, 1, 60, 1, 0, " m", "the fill stays within this distance of the click (a square)"),
    )  # fmt: skip
    CHECKS = (("contiguous", "contiguous",
               "off: every pixel of similar intensity on the surface clicked (reachable without a height step, e.g. "
               "the road between its kerbs), connected or not: all the dashes of a line, and anything as bright"),)  # fmt: skip

    def __init__(self, parent=None):
        from pclabel.tools.magic import MagicSelectParams

        super().__init__(MagicSelectParams(), parent)
