"""The Trace panel's tracer (the user's request of 2026-11-05, after LiDAR360MLS's road tools): semi-automatic curb
and road-marking lines (tools/curbtrace.py, tools/linetrace.py). The panel's other tools are the modules beside this
one; they work on lines and markers selected in Annotations, and save lines the way the tracer does (``line_item``).

- Ctrl+T (curb) or Ctrl+Shift+T (marking), then two clicks: where to start (on the road beside the curb, or on the
  paint) and a point further along for the direction. The tracer runs in a worker thread and draws what it has found
  as it goes (curb: the toe orange, the lip yellow; marking: cyan).
- Space (or "stop") stops it; Backspace (or "back a step") takes back its last step; two more clicks trace another
  stretch into the same result (e.g. past a parked car: the gap stays a gap). Enter (or "save") saves the result, Esc
  leaves the tool (the result waits), "discard" drops it.
- Every run (saved or not) is appended to the project's ``trace_log.jsonl``: start, direction, settings, steps and
  why it stopped, so attempts that failed can be looked at and replayed.
- Saving makes a directory "curb trace NN" or "marking trace NN" (in the directory selected in Annotations, like
  Ctrl+G) with a line per piece, saved as a curve with no plane unless "polylines" is chosen: the curb's lip
  (labelled ``curb``) and, only if "toe" is ticked, its toe (no label), or the marking's label (``road_line`` by
  default). Each keeps ``{tool, assisted: true, params, start, toward, height_axis, stopped, ...}``:
  the parameters are the thresholds being studied, and the lines are marked as drawn by a tool.
"""

from __future__ import annotations

import threading
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
from pclabel.tools.curbtrace import CurbTraceParams
from pclabel.tools.curbtrace import run_trace as run_curb
from pclabel.tools.linetrace import LineTraceParams
from pclabel.tools.linetrace import run_trace as run_line
from pclabel.ui import labelbox
from pclabel.ui.docks import _spin
from pclabel.ui.trace.clearance import ClearanceTool
from pclabel.ui.trace.covers import CoverTool
from pclabel.ui.trace.curb_fit import CurbFitTool
from pclabel.ui.trace.join import JoinTool

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
        self.c_lip.setChecked(True)  # the standard's curb is one line, the lip; the toe is off unless asked for
        for b in (self.c_toe, self.c_lip):
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
        self._boxes = v  # the panel's other tools add their boxes here, below the tracer's
        v.addStretch(1)
        scroll = QScrollArea()  # the panel never widens the dock area (and so never narrows the view)
        scroll.setWidget(w)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setWidget(scroll)
        self.mode.currentIndexChanged.connect(lambda _: self._show_mode())
        self._show_mode()

    def _show_mode(self):
        curb = self.mode.currentData() == "curb"
        self.curb_box.setVisible(curb)
        self.line_box.setVisible(not curb)

    def add_box(self, box):
        self._boxes.insertWidget(self._boxes.count() - 1, box)  # above the stretch

    def curb_params(self) -> CurbTraceParams:
        return CurbTraceParams(step=self.c_step.value(), curb_height=self.c_height.value(), side=self.c_side.currentData(),
                               search=self.c_search.value(), max_face=self.c_face.value(), max_gap=self.c_gap.value(),
                               max_length=self.c_length.value(), follow=self.c_follow.currentData())  # fmt: skip

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
        self.busy = False
        self.stopping = False
        self._relay = _Relay(self)
        self._relay.progress.connect(self._on_progress)
        self._relay.done.connect(self._on_done)
        d = self.dock
        d.start.clicked.connect(lambda on: self.begin(d.mode.currentData()) if on else self.w.viewport.set_tool(None))
        d.stop.clicked.connect(self.stop)
        d.back.clicked.connect(self.back)
        d.save.clicked.connect(lambda: self.save())
        d.discard.clicked.connect(self.discard)
        window.viewport.tool_changed.connect(self._on_tool)
        # the panel's other tools, on the lines and markers selected in Annotations (they share the busy flag)
        self.clearance = ClearanceTool(self)
        self.covers = CoverTool(self)
        self.join = JoinTool(self)
        self.curb_fit = CurbFitTool(self)
        for tool in (self.clearance, self.covers, self.join, self.curb_fit):
            d.add_box(tool.box)

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
            self.redraw()

    def click(self, p_local):
        """A click with the trace tool: the start, then the direction (which starts the run)."""
        if self.busy:
            return
        self.clicks.append(np.asarray(p_local, np.float64))
        if len(self.clicks) == 1:
            self.dock.status.setText("now click a point further along, for the direction")
            self.redraw()
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
        filters = w.filters() if w.viewport.renderer is not None else None
        xyz, tiles, inten = p.xyz, p.tiles, p.intensity
        self.busy, self.stopping = True, False
        self.live = {"mode": mode, "result": None, "start": start, "toward": toward, "axis": axis, "frame": f.name,
                     "params": params}  # fmt: skip
        self.dock.status.setText("tracing... (Space stops)")
        last = [0.0]

        def progress(secs):
            t = time.perf_counter()
            if t - last[0] > 0.1:
                last[0] = t
                self._relay.progress.emit([dict(s) for s in secs])
            return not self.stopping

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
        self.redraw()

    def _log(self, live, res, err):
        """Every run goes into the project's trace_log.jsonl (start, direction, settings, what it found or why it
        stopped), so failed attempts can be looked at and replayed later."""
        if self.w.project is None or live is None:
            return
        trace_ops.append_log(self.w.project.path, trace_ops.log_record(live, self.w.project.offset, res, err))

    def _on_done(self, res, err):
        self.busy = False
        live, self.live = self.live, None
        self._log(live, res, err)
        if err is not None or live is None:
            self.dock.status.setText(f"no trace: {err}")
            self.redraw()
            return
        live["result"] = res
        n = len(res["toe"]) if live["mode"] == "curb" else sum(len(pc) for pc in res["pieces"])
        if n:
            self.runs.append(live)
        self.dock.status.setText(self._summary(live) + " Enter saves; two more clicks trace on; Backspace takes "
                                 "back a step.")  # fmt: skip
        self.redraw()

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
        if self.busy:
            self.stopping = True

    def back(self):
        """Backspace: take back the last step of the last run."""
        if self.busy or not self.runs:
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
        self.redraw()

    def discard(self):
        if self.busy:
            self.stopping = True
        self.runs, self.clicks, self.live = [], [], None
        self.dock.status.setText("discarded")
        self.redraw()

    def escape(self) -> bool:
        """Esc while tracing: stop the run. Returns whether it did anything."""
        if self.busy:
            self.stopping = True
            return True
        return False

    def key(self, name: str):
        """Keys from the viewport with the trace tool: "enter", "backspace", "space"."""
        {"enter": lambda: self.save(), "backspace": self.back, "space": self.stop}.get(name, lambda: None)()

    # ---- saving -----------------------------------------------------------------------------------------------

    def save(self):
        """The pending result as annotations: a directory of polylines, one undo step."""
        w = self.w
        if self.busy:
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
        into = a.directory_for_new()
        a.checkpoint(f"save a {mode} trace")
        run0 = self.runs[0]
        d = trace_ops.add_lines(a.model, lines, trace_ops.STEMS[mode], into.id if into is not None else None,
                                lambda v, src: self.line_item(v, False, src, run_axis=run0))  # fmt: skip
        self.runs = []
        a.changed(f"{d.name}: {len(lines)} line{'s' * (len(lines) != 1)} saved (drawn by the tracer: marked assisted)")
        self.dock.status.setText(f"saved {d.name}")
        self.redraw()
        return d

    def line_item(self, vertices, closed, src, run_axis=None, as_kind=None):
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

    def redraw(self):
        self.w.annot.refresh()

    def fill_labels(self):
        labelbox.fill(self.dock.l_label, labelbox.taxonomy_entries(self.w.taxonomy), kinds={"polyline"})
        if not self.dock.l_label.currentText():
            self.dock.l_label.setCurrentText(self.w.taxonomy.tool_label("marking_trace"))
