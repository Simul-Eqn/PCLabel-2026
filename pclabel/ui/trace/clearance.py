"""The Trace panel's clearance check: select a line in Annotations (a traced toe, a lane line, a curve along the
road) and "check along the selected line": the points (of vegetation labels, or all) inside the envelope above it
(tools/clearance.py) become a directory "clearance NN" with a point set of the hits and a marker per place at its
lowest point, named with the clearance left there. Run in a worker thread; marked assisted."""

from __future__ import annotations

import numpy as np
from PySide6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QPushButton

from pclabel.ops import trace as trace_ops
from pclabel.tools.clearance import ClearanceParams, check
from pclabel.ui.docks import _spin
from pclabel.ui.worker import run_in_worker


class ClearanceBox(QGroupBox):
    def __init__(self):
        super().__init__("clearance along a line")
        self.setToolTip("what enters the space vehicles need above the road, e.g. branches")
        k = ClearanceParams()
        h = QFormLayout(self)
        self.half_width = _spin(0.1, 50.0, k.half_width, 0.25, 2, " m")
        self.half_width.setToolTip("how far across from the line the envelope reaches")
        self.side = QComboBox()
        for s_, words in (("both", "both sides"), ("left", "the left"), ("right", "the right")):
            self.side.addItem(words, s_)
        self.bottom = _spin(0.0, 10.0, k.bottom, 0.1, 2, " m")
        self.bottom.setToolTip("above the line's height: lower points are the road and what stands on it")
        self.top = _spin(0.5, 30.0, k.height, 0.1, 2, " m")
        self.top.setToolTip("the clearance needed above the road (set it for the road and its vehicles)")
        self.cluster = _spin(0.05, 5.0, k.cluster, 0.05, 2, " m")
        self.min_points = _spin(1, 100000, k.min_points, 1, 0)
        self.points = QComboBox()
        self.points.addItem("vegetation and tree labels", "vegetation")
        self.points.addItem("all points", "all")
        self.points.setToolTip("vegetation: the point sets labelled vegetation, tree or a kind of them; all: anything "
                               "there (vehicles and poles too)")  # fmt: skip
        self.run = QPushButton("check along the selected line")
        self.run.setToolTip("the line selected in Annotations: a segment, curve or polyline along the road")
        for label, wdg in (("across, up to", self.half_width), ("on", self.side), ("from", self.bottom),
                           ("to", self.top), ("one place within", self.cluster),
                           ("a place has at least", self.min_points), ("check", self.points)):  # fmt: skip
            h.addRow(label, wdg)
        h.addRow(self.run)

    def params(self) -> ClearanceParams:
        return ClearanceParams(half_width=self.half_width.value(), side=self.side.currentData(), bottom=self.bottom.value(),
                               height=self.top.value(), cluster=self.cluster.value(),
                               min_points=int(self.min_points.value()))  # fmt: skip


class ClearanceTool:
    def __init__(self, trace):
        self.trace = trace  # the Trace panel's controller: its window, status line and busy flag
        self.box = ClearanceBox()
        self.last = None  # the last check's result
        self.box.run.clicked.connect(lambda: self.run())

    def run(self, line_id: str | None = None, points: str | None = None):
        """Check the envelope along the line selected in Annotations (or ``line_id``), in a worker thread."""
        from pclabel.io.vector_export import geometry

        t = self.trace
        w = t.w
        if w.project is None or t.busy:
            return None
        a = w.annot
        m = a.model
        status = t.dock.status
        if line_id is None:
            lines = [x for x in a.selected_items() if x.kind in ("segment", "curve", "polyline")]
            if len(lines) != 1:
                status.setText("select one line in Annotations (a segment, curve or polyline along the road)")
                return None
            line = lines[0]
        else:
            line = m.node(line_id)
        v, _ = geometry(line)
        off = np.asarray(w.project.offset, np.float64)
        what = points or self.box.points.currentData()
        cand = None
        if what == "vegetation":
            labels = {k for top in w.taxonomy.tool_label("clearance") for k in (top, *w.taxonomy.descendants(top))}
            cand = m.points_labelled(labels)
            if not cand.size:
                status.setText("no point sets labelled vegetation (or a kind of it): label some, or check all points")
                return None
        f = w.frames[w.active_frame]
        axis, params = np.asarray(f.normal, np.float64), self.box.params()
        xyz, tiles = w.project.xyz, w.project.tiles
        t.busy, t.stopping = True, False
        status.setText(f"checking the clearance along {line.name}...")

        def progress(frac):
            return not t.stopping

        def done(res, err, seconds):
            if res is not None:
                res.update(seconds=seconds, line=line.name, line_id=line.id, points_checked=what, frame=f.name,
                           axis=axis)  # fmt: skip
            self._done(res, err)

        run_in_worker(lambda: check(xyz, tiles, v - off, axis, params, cand, progress), done, "clearance")
        return True

    def _done(self, res, err):
        t = self.trace
        t.busy = False
        if err is not None:
            t.dock.status.setText(f"no clearance check: {err}")
            return
        a = t.w.annot
        places = res["places"]
        self.last = res
        if not places:
            t.dock.status.setText(f"nothing inside the envelope along {res['line']} ({res['length']:.1f} m, "
                                  f"{res['seconds']:.1f} s); the scan shows only what it captured")  # fmt: skip
            return
        a.checkpoint("clearance check")
        d = trace_ops.add_clearance(a.model, res, t.w.project.offset)
        low = min(places, key=lambda p: p["clearance"])
        a.changed(f"{d.name}: {len(places)} place{'s' * (len(places) != 1)} inside the envelope along {res['line']} "
                  f"({res['length']:.1f} m); the lowest leaves {low['clearance']:.2f} m at {low['along']:.1f} m "
                  f"({res['seconds']:.1f} s)")  # fmt: skip
        t.dock.status.setText(a.dock.status.text())
