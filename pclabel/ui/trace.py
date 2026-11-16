"""The Trace panel (the user's request of 2026-11-05, after LiDAR360MLS's road tools): semi-automatic curb and
road-marking lines (tools/curbtrace.py, tools/linetrace.py).

- Ctrl+T (curb) or Ctrl+Shift+T (marking), then two clicks: where to start (on the road beside the curb, or on the
  paint) and a point further along for the direction. The tracer runs in a worker thread and draws what it has found
  as it goes (curb: the toe orange, the lip yellow; marking: cyan).
- Space (or "stop") stops it; Backspace (or "back a step") takes back its last step; two more clicks trace another
  stretch into the same result (e.g. past a parked car: the gap stays a gap). Enter (or "save") saves the result, Esc
  leaves the tool (the result waits), "discard" drops it.
- **Clearance** (the panel's lower box): select a line in Annotations (a traced toe, a lane line, a curve along the
  road) and "check along the selected line": the points (of vegetation labels, or all) inside the envelope above it
  (tools/clearance.py) become a directory "clearance NN" with a point set of the hits and a marker per place at its
  lowest point, named with the clearance left there. Run in a worker thread; marked assisted.
- **Cover outlines** (the panel's last box): select markers (e.g. placed with Ctrl+M on a cover's points) and
  "outline at the selected markers": each gets a directory "cover NN" with the marker and a closed outline (a circle
  or a rectangle, tools/templates.py) labelled ``cover``. "Reshape" redraws the selected outlines with the box's
  size and rotation (resize, rotate); the marker is where it stands (move it with "move", or place it again).
- Every run (saved or not) is appended to the project's ``trace_log.jsonl``: start, direction, settings, steps and
  why it stopped, so attempts that failed can be looked at and replayed.
- Saving makes a directory "curb trace NN" or "marking trace NN" (in the directory selected in Annotations, like
  Ctrl+G) with a polyline per piece: the curb's ``curb_bottom`` (toe) and ``curb_top`` (lip), or the marking's label
  (``road_line`` by default). Each keeps ``{tool, assisted: true, params, start, toward, height_axis, stopped, ...}``:
  the parameters are the thresholds being studied, and the lines are marked as drawn by a tool.
"""

from __future__ import annotations

import threading
from dataclasses import asdict
import time

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pclabel.analysis import curves
from pclabel.ops import trace as trace_ops
from pclabel.tools.clearance import ClearanceParams, check
from pclabel.tools.curbfit import CurbFitParams
from pclabel.tools.curbfit import fit as curb_fit
from pclabel.tools.linejoin import JoinParams, join_lines
from pclabel.tools.templates import SHAPES, OutlineParams, outline
from pclabel.tools.curbtrace import CurbTraceParams
from pclabel.tools.curbtrace import run_trace as run_curb
from pclabel.tools.linetrace import LineTraceParams
from pclabel.tools.linetrace import run_trace as run_line
from pclabel.ui import labelbox
from pclabel.ui.worker import run_in_worker
from pclabel.ui.docks import _spin

TOE_COLOR = (1.0, 0.55, 0.1, 1.0)
LIP_COLOR = (1.0, 0.9, 0.2, 1.0)
LINE_COLOR = (0.2, 0.95, 1.0, 1.0)
CLICK_COLOR = (1.0, 1.0, 1.0, 1.0)


class TraceDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Trace", parent)
        self.setObjectName("trace")
        w = QWidget()
        v = QVBoxLayout(w)
        hint = QLabel("Ctrl+T traces a curb, Ctrl+Shift+T a road marking: click where to start (on the road beside the "
                      "curb, or on the paint), then a point further along. Space stops, Backspace takes back a step, "
                      "two more clicks trace on, Enter saves.")  # fmt: skip
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.mode = QComboBox()
        self.mode.addItem("curb (Ctrl+T)", "curb")
        self.mode.addItem("road marking (Ctrl+Shift+T)", "marking")
        v.addWidget(self.mode)
        c, m = CurbTraceParams(), LineTraceParams()
        self.curb_box = QGroupBox("curb")
        f = QFormLayout(self.curb_box)
        self.c_step = _spin(0.05, 2.0, c.step, 0.05, 2, " m")
        self.c_height = _spin(0.02, 0.5, c.curb_height, 0.01, 3, " m")
        self.c_height.setToolTip("the least step that counts as a curb: lower curbs (lowered, driveways) end the trace")
        self.c_side = QComboBox()
        self.c_side.addItem("found automatically", "auto")
        self.c_side.addItem("on the left", "left")
        self.c_side.addItem("on the right", "right")
        self.c_side.setToolTip("which side of the direction you clicked the curb is on (seen from above); found "
                               "automatically: either side, and a start clicked on the curb's top moves onto the road")
        self.c_search = _spin(0.2, 5.0, c.search, 0.1, 2, " m")
        self.c_search.setToolTip("how far across, towards the curb, to look for the step")
        self.c_face = _spin(0.02, 1.0, c.max_face, 0.02, 2, " m")
        self.c_face.setToolTip("the most the toe and the lip may be apart across (a face not seen from the scanner)")
        self.c_gap = _spin(0.0, 10.0, c.max_gap, 0.1, 2, " m")
        self.c_gap.setToolTip("stretches without a curb step up to this long (a drain inlet) are skipped, and the "
                              "lines broken there")  # fmt: skip
        self.c_length = _spin(1.0, 5000.0, c.max_length, 10.0, 0, " m")
        self.c_follow = QComboBox()
        self.c_follow.addItem("the toe (both edges)", "toe")
        self.c_follow.addItem("the top edge only", "lip")
        self.c_follow.setToolTip("the top edge only: steps along the curb's lip and needs no road seen beside the curb "
                                 "(a toe hidden from the scanner, a gutter); saves only the lip")  # fmt: skip
        lines = QHBoxLayout()
        self.c_toe = QCheckBox("toe (a helper line, no label)")
        self.c_lip = QCheckBox("lip (the curb line)")
        for b in (self.c_toe, self.c_lip):
            b.setChecked(True)
            lines.addWidget(b)
        for label, wdg in (("follow", self.c_follow), ("step", self.c_step), ("curb height", self.c_height), ("curb is", self.c_side),
                           ("search across", self.c_search), ("face up to", self.c_face), ("skip gaps up to", self.c_gap),
                           ("at most", self.c_length)):  # fmt: skip
            f.addRow(label, wdg)
        lw = QWidget()
        lw.setLayout(lines)
        f.addRow("save", lw)
        v.addWidget(self.curb_box)
        self.line_box = QGroupBox("road marking")
        g = QFormLayout(self.line_box)
        self.l_kind = QComboBox()
        self.l_kind.addItem("solid", "solid")
        self.l_kind.addItem("dashed", "dashed")
        self.l_step = _spin(0.05, 2.0, m.step, 0.05, 2, " m")
        self.l_width = _spin(0.03, 1.0, m.line_width, 0.01, 2, " m")
        self.l_width.setToolTip("the line's width: a narrower one keeps a double line's two lines apart")
        self.l_search = _spin(0.05, 2.0, m.search, 0.05, 2, " m")
        self.l_auto = QCheckBox("threshold from the start")
        self.l_auto.setChecked(True)
        self.l_thr = _spin(0.0, 1.0, 0.5, 0.01, 3)
        self.l_thr.setToolTip("intensity (0..1, as in Point info) at or above which a point is paint")
        self.l_stretch = _spin(0.0, 20.0, m.stretch, 0.25, 2, " m")
        self.l_stretch.setToolTip("solid: gaps up to this are bridged (and recorded)")
        self.l_dash = _spin(0.5, 50.0, m.dash_gap, 0.5, 1, " m")
        self.l_dash.setToolTip("dashed: gaps up to this are crossed to the next dash")
        self.l_length = _spin(1.0, 5000.0, m.max_length, 10.0, 0, " m")
        self.l_label = QComboBox()
        self.l_label.setEditable(True)
        thr = QHBoxLayout()
        thr.addWidget(self.l_auto)
        thr.addWidget(self.l_thr)
        tw = QWidget()
        tw.setLayout(thr)
        for label, wdg in (("line", self.l_kind), ("step", self.l_step), ("line width", self.l_width),
                           ("search either side", self.l_search), ("paint", tw), ("bridge gaps up to", self.l_stretch),
                           ("next dash within", self.l_dash), ("at most", self.l_length), ("label", self.l_label)):  # fmt: skip
            g.addRow(label, wdg)
        v.addWidget(self.line_box)
        keep = QHBoxLayout()
        self.save_as = QComboBox()
        self.save_as.addItem("curves", "curve")
        self.save_as.addItem("polylines", "polyline")
        self.save_as.setToolTip("curves: editable (drag points and gradients), with no plane, through as few of the "
                                "traced vertices as keep within the distance; polylines: every vertex exactly")  # fmt: skip
        self.fit_tol = _spin(0.002, 0.10, 0.01, 0.005, 3, " m")
        self.fit_tol.setToolTip("a saved curve passes this close to every traced vertex")
        keep.addWidget(QLabel("save as"))
        keep.addWidget(self.save_as, 1)
        keep.addWidget(QLabel("within"))
        keep.addWidget(self.fit_tol)
        v.addLayout(keep)
        self.save_as.currentIndexChanged.connect(lambda _: self.fit_tol.setEnabled(self.save_as.currentData() == "curve"))
        row = QGridLayout()  # two rows: a wide panel would narrow the view
        self.start = QPushButton("start: click twice")
        self.start.setCheckable(True)
        self.stop = QPushButton("stop (Space)")
        self.back = QPushButton("back (Backspace)")
        self.save = QPushButton("save (Enter)")
        self.discard = QPushButton("discard")
        for i, b in enumerate((self.start, self.stop, self.back, self.save, self.discard)):
            row.addWidget(b, i // 3, i % 3)
        v.addLayout(row)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.status)
        k = ClearanceParams()
        self.clear_box = QGroupBox("clearance along a line")
        self.clear_box.setToolTip("what enters the space vehicles need above the road, e.g. branches")
        h = QFormLayout(self.clear_box)
        self.k_width = _spin(0.1, 50.0, k.half_width, 0.25, 2, " m")
        self.k_width.setToolTip("how far across from the line the envelope reaches")
        self.k_side = QComboBox()
        for s_, words in (("both", "both sides"), ("left", "the left"), ("right", "the right")):
            self.k_side.addItem(words, s_)
        self.k_bottom = _spin(0.0, 10.0, k.bottom, 0.1, 2, " m")
        self.k_bottom.setToolTip("above the line's height: lower points are the road and what stands on it")
        self.k_height = _spin(0.5, 30.0, k.height, 0.1, 2, " m")
        self.k_height.setToolTip("the clearance needed above the road (set it for the road and its vehicles)")
        self.k_cluster = _spin(0.05, 5.0, k.cluster, 0.05, 2, " m")
        self.k_min = _spin(1, 100000, k.min_points, 1, 0)
        self.k_points = QComboBox()
        self.k_points.addItem("vegetation and tree labels", "vegetation")
        self.k_points.addItem("all points", "all")
        self.k_points.setToolTip("vegetation: the point sets labelled vegetation, tree or a kind of them; all: anything there "
                                 "(vehicles and poles too)")  # fmt: skip
        self.k_run = QPushButton("check along the selected line")
        self.k_run.setToolTip("the line selected in Annotations: a segment, curve or polyline along the road")
        for label, wdg in (("across, up to", self.k_width), ("on", self.k_side), ("from", self.k_bottom),
                           ("to", self.k_height), ("one place within", self.k_cluster), ("a place has at least", self.k_min),
                           ("check", self.k_points)):  # fmt: skip
            h.addRow(label, wdg)
        h.addRow(self.k_run)
        v.addWidget(self.clear_box)
        o = OutlineParams()
        self.cover_box = QGroupBox("cover outline")
        g2 = QFormLayout(self.cover_box)
        self.o_shape = QComboBox()
        for sh in SHAPES:
            self.o_shape.addItem(sh, sh)
        self.o_diam = _spin(0.05, 5.0, o.diameter, 0.05, 3, " m")
        self.o_width = _spin(0.05, 5.0, o.width, 0.05, 3, " m")
        self.o_length = _spin(0.05, 5.0, o.length, 0.05, 3, " m")
        self.o_rot = _spin(-360.0, 360.0, o.rotation, 5.0, 1, " deg")
        self.o_fit = QCheckBox("diameter from the marker's fit")
        self.o_fit.setToolTip("a circle's diameter from the marker's circle fit (Ctrl+M, circle centre), if it has one")
        self.o_fit.setChecked(True)
        self.o_place = QPushButton("outline the selected markers")
        self.o_reshape = QPushButton("reshape selected outlines")
        for label, wdg in (("shape", self.o_shape), ("diameter", self.o_diam), ("width", self.o_width),
                           ("length", self.o_length), ("rotation", self.o_rot)):  # fmt: skip
            g2.addRow(label, wdg)
        g2.addRow(self.o_fit)
        g2.addRow(self.o_place)
        g2.addRow(self.o_reshape)
        v.addWidget(self.cover_box)
        jp = JoinParams()
        self.join_box = QGroupBox("join traced pieces")
        self.join_box.setToolTip("the lines selected in Annotations (or in selected directories), label by label: "
                                 "repeats from retries dropped, ends within reach joined, into one line each")  # fmt: skip
        g3 = QFormLayout(self.join_box)
        self.j_gap = _spin(0.0, 20.0, jp.join_gap, 0.1, 2, " m")
        self.j_gap.setToolTip("ends closer than this are joined (the joining piece is recorded as bridged)")
        self.j_overlap = _spin(0.0, 1.0, jp.overlap, 0.01, 2, " m")
        self.j_overlap.setToolTip("a vertex this close to a longer line repeats it (a retry over the same stretch)")
        self.j_angle = _spin(0.0, 180.0, jp.max_angle, 5.0, 0, " deg")
        self.j_angle.setToolTip("a join turns no more than this from either line")
        self.j_min = _spin(0.0, 10.0, jp.min_length, 0.1, 2, " m")
        self.j_min.setToolTip("pieces shorter than this (after dropping repeats) are left out")
        self.j_keep = QCheckBox("keep the originals")
        self.j_run = QPushButton("join the selected lines")
        for label, wdg in (("join gaps up to", self.j_gap), ("repeats within", self.j_overlap),
                           ("turning at most", self.j_angle), ("drop pieces under", self.j_min)):  # fmt: skip
            g3.addRow(label, wdg)
        g3.addRow(self.j_keep)
        g3.addRow(self.j_run)
        v.addWidget(self.join_box)
        kp = CurbFitParams()
        self.fit_box = QGroupBox("smooth onto the curb top")
        self.fit_box.setToolTip("for curb_top lines: the curb's top edge measured every 0.3 m along the selected lines "
                                "(where the curb top was scanned), the line made smooth but kept within the distance "
                                "of it; where the curb slopes down to the road it is carried across at the top")  # fmt: skip
        g4 = QFormLayout(self.fit_box)
        self.f_smooth = _spin(0.1, 20.0, kp.smooth, 0.1, 2, " m")
        self.f_smooth.setToolTip("the length along the line points are averaged over")
        self.f_shift = _spin(0.0, 0.5, kp.max_shift, 0.005, 3, " m")
        self.f_shift.setToolTip("the line stays this close to the measured curb top (its scatter taken out)")
        self.f_window = _spin(0.02, 1.0, kp.window, 0.02, 2, " m")
        self.f_window.setToolTip("a curb top measured further across from the line than this is not this curb's")
        self.f_bridge = _spin(0.0, 100.0, kp.max_bridge, 0.5, 1, " m")
        self.f_bridge.setToolTip("a stretch with no full-height curb up to this long (a lowered curb at a driveway or "
                                 "crossing) is carried across at the curb top's height; a longer one stays where it was")  # fmt: skip
        self.f_ratio = _spin(0.0, 1.0, kp.min_height_ratio, 0.05, 2, "")
        self.f_ratio.setToolTip("a curb lower than this part of the line's usual height is sloping down: not followed")
        self.f_run = QPushButton("smooth the selected lines")
        for label, wdg in (("smooth over", self.f_smooth), ("within", self.f_shift), ("search across", self.f_window),
                           ("carry across up to", self.f_bridge), ("full height ≥", self.f_ratio)):  # fmt: skip
            g4.addRow(label, wdg)
        g4.addRow(self.f_run)
        v.addWidget(self.fit_box)
        v.addStretch(1)
        scroll = QScrollArea()  # the panel never widens the dock area (and so never narrows the view)
        scroll.setWidget(w)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setWidget(scroll)
        self.mode.currentIndexChanged.connect(lambda _: self._show_mode())
        self._show_mode()

    def outline_params(self, diameter: float | None = None) -> OutlineParams:
        return OutlineParams(shape=self.o_shape.currentData(), diameter=diameter or self.o_diam.value(),
                             width=self.o_width.value(), length=self.o_length.value(), rotation=self.o_rot.value())  # fmt: skip

    def clearance_params(self) -> ClearanceParams:
        return ClearanceParams(half_width=self.k_width.value(), side=self.k_side.currentData(), bottom=self.k_bottom.value(),
                               height=self.k_height.value(), cluster=self.k_cluster.value(), min_points=int(self.k_min.value()))  # fmt: skip

    def _show_mode(self):
        curb = self.mode.currentData() == "curb"
        self.curb_box.setVisible(curb)
        self.line_box.setVisible(not curb)

    def curb_params(self) -> CurbTraceParams:
        return CurbTraceParams(step=self.c_step.value(), curb_height=self.c_height.value(), side=self.c_side.currentData(),
                               search=self.c_search.value(), max_face=self.c_face.value(), max_gap=self.c_gap.value(),
                               max_length=self.c_length.value(), follow=self.c_follow.currentData())  # fmt: skip

    def fit_params(self) -> CurbFitParams:
        return CurbFitParams(smooth=self.f_smooth.value(), max_shift=self.f_shift.value(), window=self.f_window.value(),
                             max_bridge=self.f_bridge.value(), min_height_ratio=self.f_ratio.value())  # fmt: skip

    def join_params(self) -> JoinParams:
        return JoinParams(overlap=self.j_overlap.value(), join_gap=self.j_gap.value(), max_angle=self.j_angle.value(),
                          min_length=self.j_min.value())  # fmt: skip

    def line_params(self) -> LineTraceParams:
        return LineTraceParams(kind=self.l_kind.currentData(), step=self.l_step.value(), line_width=self.l_width.value(),
                               search=self.l_search.value(), threshold=None if self.l_auto.isChecked() else self.l_thr.value(),
                               stretch=self.l_stretch.value(), dash_gap=self.l_dash.value(), max_length=self.l_length.value())  # fmt: skip


class _Relay(QObject):
    progress = Signal(object)  # a snapshot of the run so far
    done = Signal(object, object)  # result, error


class TraceController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.dock = TraceDock(window)
        self.clicks: list = []  # local points clicked for the next run
        self.runs: list = []  # finished runs of the pending result: {mode, result, start, toward}
        self.live = None  # the run in progress, as far as it got (a snapshot)
        self._busy = False
        self._stop = False
        self._relay = _Relay(self)
        self._relay.progress.connect(self._on_progress)
        self._relay.done.connect(self._on_done)
        self.last_clearance = None
        d = self.dock
        d.start.clicked.connect(lambda on: self.begin(d.mode.currentData()) if on else self.w.viewport.set_tool(None))
        d.stop.clicked.connect(self.stop)
        d.back.clicked.connect(self.back)
        d.save.clicked.connect(lambda: self.save())
        d.discard.clicked.connect(self.discard)
        d.k_run.clicked.connect(lambda: self.clearance())
        d.j_run.clicked.connect(lambda: self.join_selected())
        d.f_run.clicked.connect(lambda: self.fit_selected())
        d.o_place.clicked.connect(lambda: self.cover_outlines())
        d.o_reshape.clicked.connect(lambda: self.reshape_outlines())
        window.viewport.tool_changed.connect(self._on_tool)

    # ---- the tool ---------------------------------------------------------------------------------------------

    def mode(self) -> str:
        return self.dock.mode.currentData()

    def begin(self, mode: str = "curb"):
        """Ctrl+T / Ctrl+Shift+T: the next two clicks start a trace."""
        if self.w.project is None:
            return
        if self.runs and self.runs[-1]["mode"] != mode:
            self.dock.status.setText("save or discard the other trace first")
            self.dock.start.setChecked(False)
            return
        self.dock.mode.setCurrentIndex(self.dock.mode.findData(mode))
        self.clicks = []
        self.dock.show()
        self.dock.raise_()
        self.w.viewport.set_tool("trace")
        what = "on the road beside the curb" if mode == "curb" else "on the paint"
        self.dock.status.setText(f"click where to start, {what}")

    def _on_tool(self, name):
        self.dock.start.setChecked(name == "trace")
        if name != "trace":
            self.clicks = []
            self._redraw()

    def click(self, p_local):
        """A click with the trace tool: the start, then the direction (which starts the run)."""
        if self._busy:
            return
        self.clicks.append(np.asarray(p_local, np.float64))
        if len(self.clicks) == 1:
            self.dock.status.setText("now click a point further along, for the direction")
            self._redraw()
            return
        start, toward = self.clicks[:2]
        self.clicks = []
        self._run(start, toward)

    def _run(self, start, toward):
        w, mode = self.w, self.mode()
        p = w.project
        f = w.frames[w.active_frame]
        axis = np.asarray(f.normal, np.float64)
        params = self.dock.curb_params() if mode == "curb" else self.dock.line_params()
        filters = w._filters() if w.viewport.renderer is not None else None
        xyz, tiles, inten = p.xyz, p.tiles, p.intensity
        self._busy, self._stop = True, False
        self.live = {"mode": mode, "result": None, "start": start, "toward": toward, "axis": axis, "frame": f.name,
                     "params": params}  # fmt: skip
        self.dock.status.setText("tracing... (Space stops)")
        last = [0.0]

        def progress(secs):
            t = time.perf_counter()
            if t - last[0] > 0.1:
                last[0] = t
                self._relay.progress.emit([dict(s) for s in secs])
            return not self._stop

        def work():
            t0 = time.perf_counter()
            try:
                if mode == "curb":
                    res = run_curb(xyz, tiles, start, toward, axis, params, filters, progress)
                else:
                    res = run_line(xyz, inten, tiles, start, toward, axis, params, filters, progress)
                res["seconds"] = time.perf_counter() - t0
                self._relay.done.emit(res, None)
            except Exception as e:  # reported in the UI thread
                self._relay.done.emit(None, e)

        self._thread = threading.Thread(target=work, daemon=True, name="trace")
        self._thread.start()

    def _on_progress(self, secs):
        if self.live is None:
            return
        self.live["partial"] = secs
        self._redraw()

    def _log(self, live, res, err):
        """Every run goes into the project's trace_log.jsonl (start, direction, settings, what it found or why it
        stopped), so failed attempts can be looked at and replayed later."""
        if self.w.project is None or live is None:
            return
        trace_ops.append_log(self.w.project.path, trace_ops.log_record(live, self.w.project.offset, res, err))

    def _on_done(self, res, err):
        self._busy = False
        live, self.live = self.live, None
        self._log(live, res, err)
        if err is not None or live is None:
            self.dock.status.setText(f"no trace: {err}")
            self._redraw()
            return
        live["result"] = res
        n = len(res["toe"]) if live["mode"] == "curb" else sum(len(pc) for pc in res["pieces"])
        if n:
            self.runs.append(live)
        self.dock.status.setText(self._summary(live) + " Enter saves; two more clicks trace on; Backspace takes "
                                 "back a step.")  # fmt: skip
        self._redraw()

    def _summary(self, run) -> str:
        r = run["result"]
        if run["mode"] == "curb":
            k = len(r["toe"])
            if not k:
                return f"no curb found: {r['stopped']}."
            h = r["height"]
            return (f"{k} steps ({int(r['piece'].max()) + 1} pieces), curb {np.median(h):.3f} m high (median; "
                    f"{h.min():.3f}..{h.max():.3f}), face seen in {r['seen'].mean():.0%}; stopped: {r['stopped']} "
                    f"({r['seconds']:.1f} s).")  # fmt: skip
        pcs = r["pieces"]
        if not pcs:
            return f"no marking found: {r['stopped']}."
        length = sum(curves.length(pc) for pc in pcs)
        bridged = f", {len(r['bridged'])} gaps bridged" if r["bridged"] else ""
        return (f"{len(pcs)} piece{'s' * (len(pcs) != 1)}, {length:.1f} m, threshold {r['threshold']:.3f}{bridged}; "
                f"stopped: {r['stopped']} ({r['seconds']:.1f} s).")

    def stop(self):
        """Space: stop the run in progress (what it found so far stays)."""
        if self._busy:
            self._stop = True

    def back(self):
        """Backspace: take back the last step of the last run."""
        if self._busy or not self.runs:
            return
        r = self.runs[-1]["result"]
        if self.runs[-1]["mode"] == "curb":
            for k in ("toe", "lip", "height", "face", "seen", "top_seen", "piece"):
                if k in r:
                    r[k] = r[k][:-1]
            empty = len(r["toe"]) == 0
        else:
            pcs = r["pieces"]
            if len(pcs[-1]) > 2:
                pcs[-1] = pcs[-1][:-1]
            else:
                pcs.pop()
            empty = not pcs
        if empty:
            self.runs.pop()
        self.dock.status.setText("took back a step")
        self._redraw()

    def discard(self):
        if self._busy:
            self._stop = True
        self.runs, self.clicks, self.live = [], [], None
        self.dock.status.setText("discarded")
        self._redraw()

    def escape(self) -> bool:
        """Esc while tracing: stop the run. Returns whether it did anything."""
        if self._busy:
            self._stop = True
            return True
        return False

    def key(self, name: str):
        """Keys from the viewport with the trace tool: "enter", "backspace", "space"."""
        {"enter": lambda: self.save(), "backspace": self.back, "space": self.stop}.get(name, lambda: None)()

    # ---- saving -----------------------------------------------------------------------------------------------

    def save(self):
        """The pending result as annotations: a directory of polylines, one undo step."""
        w = self.w
        if self._busy:
            self.dock.status.setText("stop the trace first (Space)")
            return None
        if not self.runs:
            self.dock.status.setText("nothing traced to save")
            return None
        a = w.annot
        mode = self.runs[0]["mode"]
        lines = trace_ops.run_lines(self.runs, w.project.offset, w.taxonomy, toe=self.dock.c_toe.isChecked(),
                                    lip=self.dock.c_lip.isChecked(), line_label=self.dock.l_label.currentText().strip())
        if not lines:
            self.dock.status.setText("nothing to save: the pieces are too short")
            return None
        into = a._directory_for_new()
        a.checkpoint(f"save a {mode} trace")
        run0 = self.runs[0]
        d = trace_ops.add_lines(a.model, lines, trace_ops.STEMS[mode], into.id if into is not None else None,
                                lambda v, src: self._line_item(v, False, src, run_axis=run0))  # fmt: skip
        self.runs = []
        a.changed(f"{d.name}: {len(lines)} line{'s' * (len(lines) != 1)} saved (drawn by the tracer: marked assisted)")
        self.dock.status.setText(f"saved {d.name}")
        self._redraw()
        return d

    def _line_item(self, vertices, closed, src, run_axis=None, as_kind=None):
        """A saved line: a "free" curve within the panel's distance of the vertices (editable), or the polyline
        (``as_kind`` overrides the panel's "save as"; a polyline from dense points keeps them within 2 mm)."""
        kind = as_kind or self.dock.save_as.currentData()
        if kind != "curve" and as_kind == "polyline":
            vertices = trace_ops.simplify_polyline(vertices, closed)
        f = self.w.frames[self.w.active_frame]
        if run_axis is not None:  # the height axis the trace ran on
            axis, frame = np.asarray(run_axis["axis"], np.float64), run_axis["frame"]
        else:
            axis, frame = np.asarray(f.normal, np.float64), f.name
        origin = np.asarray(f.origin, np.float64) if frame == f.name else np.asarray(vertices[0], np.float64)
        return trace_ops.line_item(vertices, closed, src, kind, axis, frame, origin, self.dock.fit_tol.value())

    # ---- clearance --------------------------------------------------------------------------------------------

    def clearance(self, line_id: str | None = None, points: str | None = None):
        """Check the envelope along the line selected in Annotations (or ``line_id``), in a worker thread."""
        from pclabel.io.vector_export import geometry

        w = self.w
        if w.project is None or self._busy:
            return None
        a = w.annot
        m = a.model
        if line_id is None:
            lines = [x for x in a._selected_items() if x.kind in ("segment", "curve", "polyline")]
            if len(lines) != 1:
                self.dock.status.setText("select one line in Annotations (a segment, curve or polyline along the road)")
                return None
            line = lines[0]
        else:
            line = m.node(line_id)
        v, _ = geometry(line)
        off = np.asarray(w.project.offset, np.float64)
        what = points or self.dock.k_points.currentData()
        cand = None
        if what == "vegetation":
            labels = {k for top in w.taxonomy.tool_label("clearance") for k in (top, *w.taxonomy.descendants(top))}
            cand = m.points_labelled(labels)
            if not cand.size:
                self.dock.status.setText("no point sets labelled vegetation (or a kind of it): label some, or check all points")
                return None
        f = w.frames[w.active_frame]
        axis, params = np.asarray(f.normal, np.float64), self.dock.clearance_params()
        xyz, tiles = w.project.xyz, w.project.tiles
        self._busy, self._stop = True, False
        self.dock.status.setText(f"checking the clearance along {line.name}...")

        def progress(frac):
            return not self._stop

        def done(res, err, seconds):
            if res is not None:
                res.update(seconds=seconds, line=line.name, line_id=line.id, points_checked=what, frame=f.name,
                           axis=axis)  # fmt: skip
            self._on_clearance(res, err)

        run_in_worker(lambda: check(xyz, tiles, v - off, axis, params, cand, progress), done, "clearance")
        return True

    def _on_clearance(self, res, err):
        self._busy = False
        if err is not None:
            self.dock.status.setText(f"no clearance check: {err}")
            return
        a = self.w.annot
        places = res["places"]
        self.last_clearance = res
        if not places:
            self.dock.status.setText(f"nothing inside the envelope along {res['line']} ({res['length']:.1f} m, "
                                     f"{res['seconds']:.1f} s); the scan shows only what it captured")  # fmt: skip
            return
        a.checkpoint("clearance check")
        d = trace_ops.add_clearance(a.model, res, self.w.project.offset)
        low = min(places, key=lambda p: p["clearance"])
        a.changed(f"{d.name}: {len(places)} place{'s' * (len(places) != 1)} inside the envelope along {res['line']} "
                   f"({res['length']:.1f} m); the lowest leaves {low['clearance']:.2f} m at {low['along']:.1f} m "
                   f"({res['seconds']:.1f} s)")  # fmt: skip
        self.dock.status.setText(a.dock.status.text())

    # ---- smoothing onto the curb top ----------------------------------------------------------------------------

    def fit_selected(self):
        """Smooth the selected lines onto the curb's top edge (tools/curbfit.py), in a worker thread; each replaced
        in place when done (one undo step)."""
        from pclabel.io.vector_export import geometry

        w = self.w
        if w.project is None or self._busy:
            return None
        a = w.annot
        items = [x for x in a._selected_items() if x.kind == "curve" or (x.kind == "polyline" and len(x.vertices) > 1)]
        if not items and a.editing is not None:
            items = [a.editing]
        if not items:
            self.dock.status.setText("select the curb_top lines to smooth (or their directories) in Annotations")
            return None
        off = np.asarray(w.project.offset, np.float64)
        f = w.frames[w.active_frame]
        axis, params = np.asarray(f.normal, np.float64), self.dock.fit_params()
        jobs = []
        for x in items:
            v, closed = geometry(x)
            jobs.append((x.id, x.name, v - off, closed))
        xyz, tiles = w.project.xyz, w.project.tiles
        filters = w._filters() if w.viewport.renderer is not None else None
        self._busy, self._stop = True, False
        self.dock.status.setText(f"measuring the curb top along {len(items)} line{'s' * (len(items) != 1)}...")

        def work():
            out = []
            for iid, name, v, closed in jobs:
                if self._stop:
                    break
                t0 = time.perf_counter()
                try:
                    r = curb_fit(xyz, tiles, v, axis, params, closed, filters, lambda fr: not self._stop)
                    r["seconds"] = time.perf_counter() - t0
                    out.append((iid, r))
                except (ValueError, InterruptedError) as e:
                    out.append((iid, e))
            return {"results": out, "frame": f.name, "axis": axis, "params": params}

        run_in_worker(work, lambda res, err, s: self._on_fit(res, err), "curb_fit")
        return True

    def _on_fit(self, res, err):
        self._busy = False
        if err is not None:
            self.dock.status.setText(f"no fit: {err}")
            return
        w = self.w
        a = w.annot
        m = a.model
        off = np.asarray(w.project.offset, np.float64)
        done = [(iid, r) for iid, r in res["results"] if isinstance(r, dict)]
        failed = [(iid, r) for iid, r in res["results"] if not isinstance(r, dict)]
        msgs = []
        for iid, e in failed:
            try:
                msgs.append(f"{m.node(iid).name}: {e}")
            except KeyError:
                pass
        if not done:
            self.dock.status.setText("; ".join(msgs) or "nothing fitted")
            return
        a.checkpoint(f"smooth {len(done)} line{'s' * (len(done) != 1)} onto the curb top")
        stats = []
        for iid, r in done:
            try:
                x = m.node(iid)
            except KeyError:  # removed meanwhile
                continue
            src = trace_ops.curb_fit_source(getattr(x, "source", {}), r, asdict(res["params"]), res["frame"],
                                            res["axis"])  # fmt: skip
            new = self._line_item(r["vertices"] + off, x.closed, src, as_kind=x.kind)
            if x.kind == "curve" and new.kind == "curve":
                new.frame, new.axis, new.axis_origin = x.frame, x.axis.copy(), x.axis_origin.copy()
            m._replace(x, new)
            a._lines.pop(iid, None)
            if a.editing is not None and a.editing.id == iid:
                a.editing, a.active = new, None
            stats.append(r)
        n_b = sum(len(r["bridged"]) for r in stats)
        a.changed(f"{len(stats)} line{'s' * (len(stats) != 1)} smoothed onto the curb top: "
                   f"{sum(r['anchors'] for r in stats)} curb tops measured, the line within "
                   f"{max(r['off_anchors_rms'] for r in stats) * 100:.1f} cm (rms) of them, moved up to "
                   f"{max(r['largest_move'] for r in stats) * 100:.0f} cm; {n_b} stretch{'es' * (n_b != 1)} carried "
                   f"across ({sum(b for r in stats for _, b in r['bridged']):.1f} m)"
                   + (f"; {'; '.join(msgs)}" if msgs else ""))  # fmt: skip
        self.dock.status.setText(a.dock.status.text())
        self._redraw()

    def join_selected(self, keep: bool | None = None) -> list:
        """Join the lines selected in Annotations (or in selected directories), label by label (tools/linejoin.py):
        a directory "<name> joined" with a polyline per joined line, next to the first directory they came from; the
        originals removed (and directories left empty), unless "keep the originals". One undo step."""
        from pclabel.io.vector_export import assisted, geometry

        w = self.w
        a = w.annot
        m = a.model
        items = [x for x in a._selected_items() if x.kind in ("segment", "curve", "polyline")]
        if len(items) < 2:
            self.dock.status.setText("select the lines to join (or their directories) in Annotations: at least two")
            return []
        keep = self.dock.j_keep.isChecked() if keep is None else keep
        params = self.dock.join_params()
        f = w.frames[w.active_frame]
        by_label: dict = {}
        for x in items:
            by_label.setdefault(m.label_of(x.id)[0], []).append(x)
        results = []
        for lab, xs in by_label.items():
            out = join_lines([geometry(x)[0] for x in xs], f.normal, params)
            results.append((lab, xs, out))
        home = m.parent(items[0].id)
        stem = home.name if home is not None else "lines"
        a.checkpoint(f"join {len(items)} lines")
        d = m.new_directory(f"{stem} joined", m.parent(home.id).id if home is not None and m.parent(home.id) is not None else None)
        if home is not None:  # beside the directory the lines came from
            sib = a.model._siblings(home.id)
            sib.remove(d)
            sib.insert(sib.index(home) + 1, d)
            if d.color is None and home.color:
                d.color = home.color
        n_out, n_bridged, n_dropped = 0, 0, 0
        for lab, xs, out in results:
            for k, o in enumerate(out, 1):
                src = trace_ops.join_source(o, xs, asdict(params), f.name, assisted)
                pl = m.add(self._line_item(o["vertices"], o["closed"], src), f"{lab or 'line'} {k}")
                pl.label = lab
                m.move([pl.id], d.id)
                n_out += 1
                n_bridged += len(o["bridged"])
                n_dropped += sum(o["dropped"].values())
        if not keep:
            dirs = {m.parent(x.id).id for x in items if m.parent(x.id) is not None}
            for x in items:
                m.remove(x.id)
            for di in dirs:  # directories the lines left empty go too
                try:
                    dd = m.node(di)
                except KeyError:
                    continue
                if not dd.children and dd.id != d.id:
                    m.remove(di)
        a.changed(f"{d.name}: {len(items)} lines joined into {n_out} ({n_bridged} gap{'s' * (n_bridged != 1)} bridged, "
                   f"{n_dropped} repeated vertices dropped)" + ("; the originals kept" if keep else ""))  # fmt: skip
        self.dock.status.setText(a.dock.status.text())
        return d.children

    # ---- cover outlines ---------------------------------------------------------------------------------------

    def _marker_frame(self, mk):
        """(axis, along) for a marker's outline: its own height axis if it has one, else the active one."""
        f = self.w.frames[self.w.active_frame]
        ax = mk.source.get("axis")
        axis = np.asarray(ax if ax is not None else f.normal, np.float64)
        return axis / np.linalg.norm(axis), np.asarray(f.along, np.float64)

    def _outline_for(self, mk):
        d = self.dock
        dia = None
        if d.o_shape.currentData() == "circle" and d.o_fit.isChecked() and mk.source.get("radius"):
            dia = 2 * float(mk.source["radius"])
        p = d.outline_params(dia)
        axis, along = self._marker_frame(mk)
        return outline(mk.position, axis, p, along), p, axis

    def cover_outlines(self) -> list:
        """An outline (labelled cover) for each selected marker, in a directory "cover NN" with it; one undo step."""
        a = self.w.annot
        m = a.model
        mks = [x for x in a._selected_items() if x.kind == "marker"]
        if not mks:
            self.dock.status.setText("select markers in Annotations first (e.g. Ctrl+M on a cover's points)")
            return []
        a.checkpoint(f"cover outline{'s' * (len(mks) != 1)}")
        made = []
        for mk in mks:
            ring, p, axis = self._outline_for(mk)
            made.append(trace_ops.add_cover(m, mk, ring, asdict(p), axis, self.w.taxonomy.tool_label("cover")))
        a.changed(f"{len(made)} cover outline{'s' * (len(made) != 1)}: {made[0].name}" + (" ..." if len(made) > 1 else ""))
        return made

    def reshape_outlines(self) -> int:
        """Redraw the selected outlines (or those in selected directories) with the box's shape, size and rotation,
        around their markers; one undo step."""
        a = self.w.annot
        m = a.model
        outs = [x for x in a._selected_items() if x.kind == "polyline" and x.source.get("tool") == "cover_template"]
        if not outs:
            self.dock.status.setText("select cover outlines (or their directories) first")
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

    # ---- drawing ----------------------------------------------------------------------------------------------

    def overlay(self, off) -> tuple[list, list]:
        """(lines, points) for the viewport, local: the pending runs, the run in progress, the clicks."""
        lines, points = [], []
        runs = list(self.runs)
        if self.live is not None and self.live.get("partial"):
            secs = self.live["partial"]
            if self.live["mode"] == "curb":
                part = {"toe": np.array([s["toe"] for s in secs]), "lip": np.array([s["lip"] for s in secs]),
                        "piece": np.array([s["piece"] for s in secs])}  # fmt: skip
            else:
                part = {"pieces": [np.array([s["centre"] for s in secs if s["piece"] == k])
                                   for k in sorted({s["piece"] for s in secs})]}  # fmt: skip
            runs.append({"mode": self.live["mode"], "result": part})
        for run in runs:
            r = run["result"]
            if run["mode"] == "curb":
                for k in np.unique(r["piece"]) if len(r["piece"]) else []:
                    sel = r["piece"] == k
                    for key, col in (("toe", TOE_COLOR), ("lip", LIP_COLOR)):
                        v = r[key][sel]
                        if len(v) > 1:
                            lines.append((v, col, 3.0))
                        points.append((v, col, 5.0))
            else:
                for pc in r["pieces"]:
                    if len(pc) > 1:
                        lines.append((pc, LINE_COLOR, 3.0))
                    if len(pc):
                        points.append((pc[[0, -1]], LINE_COLOR, 7.0))
        if self.clicks:
            points.append((np.array(self.clicks), CLICK_COLOR, 12.0))
        return lines, points

    def _redraw(self):
        self.w.annot.refresh()

    def fill_labels(self):
        labelbox.fill(self.dock.l_label, labelbox.taxonomy_entries(self.w.taxonomy), kinds={"polyline"})
        if not self.dock.l_label.currentText():
            self.dock.l_label.setCurrentText(self.w.taxonomy.tool_label("marking_trace"))
