"""The Trace panel's "smooth onto the curb top", for curb_top lines: the curb's top edge measured every 0.3 m along
the selected lines (tools/curbfit.py), the line made smooth but kept within a distance of it; each line replaced in
place (one undo step), its source recording the fit."""

from __future__ import annotations

import time
from dataclasses import asdict

import numpy as np
from PySide6.QtWidgets import QFormLayout, QGroupBox, QPushButton

from pclabel.ops import trace as trace_ops
from pclabel.tools.curbfit import CurbFitParams
from pclabel.tools.curbfit import fit as curb_fit
from pclabel.ui.docks import _spin
from pclabel.ui.worker import run_in_worker


class CurbFitBox(QGroupBox):
    def __init__(self):
        super().__init__("smooth onto the curb top")
        self.setToolTip("for curb_top lines: the curb's top edge measured every 0.3 m along the selected lines "
                        "(where the curb top was scanned), the line made smooth but kept within the distance "
                        "of it; where the curb slopes down to the road it is carried across at the top")  # fmt: skip
        kp = CurbFitParams()
        g = QFormLayout(self)
        self.smooth = _spin(0.1, 20.0, kp.smooth, 0.1, 2, " m")
        self.smooth.setToolTip("the length along the line points are averaged over")
        self.shift = _spin(0.0, 0.5, kp.max_shift, 0.005, 3, " m")
        self.shift.setToolTip("the line stays this close to the measured curb top (its scatter taken out)")
        self.search = _spin(0.02, 1.0, kp.window, 0.02, 2, " m")
        self.search.setToolTip("a curb top measured further across from the line than this is not this curb's")
        self.bridge = _spin(0.0, 100.0, kp.max_bridge, 0.5, 1, " m")
        self.bridge.setToolTip("a stretch with no full-height curb up to this long (a lowered curb at a driveway or "
                               "crossing) is carried across at the curb top's height; a longer one stays where it was")  # fmt: skip
        self.ratio = _spin(0.0, 1.0, kp.min_height_ratio, 0.05, 2, "")
        self.ratio.setToolTip("a curb lower than this part of the line's usual height is sloping down: not followed")
        self.run = QPushButton("smooth the selected lines")
        for label, wdg in (("smooth over", self.smooth), ("within", self.shift), ("search across", self.search),
                           ("carry across up to", self.bridge), ("full height ≥", self.ratio)):  # fmt: skip
            g.addRow(label, wdg)
        g.addRow(self.run)

    def params(self) -> CurbFitParams:
        return CurbFitParams(smooth=self.smooth.value(), max_shift=self.shift.value(), window=self.search.value(),
                             max_bridge=self.bridge.value(), min_height_ratio=self.ratio.value())  # fmt: skip


class CurbFitTool:
    def __init__(self, trace):
        self.trace = trace  # the Trace panel's controller: its window, status line, busy flag and how lines are saved
        self.box = CurbFitBox()
        self.box.run.clicked.connect(lambda: self.run())

    def run(self):
        """Smooth the selected lines onto the curb's top edge, in a worker thread; each replaced in place when done
        (one undo step)."""
        from pclabel.io.vector_export import geometry

        t = self.trace
        w = t.w
        if w.project is None or t.busy:
            return None
        a = w.annot
        items = [x for x in a.selected_items() if x.kind == "curve" or (x.kind == "polyline" and len(x.vertices) > 1)]
        if not items and a.editing is not None:
            items = [a.editing]
        if not items:
            t.dock.status.setText("select the curb_top lines to smooth (or their directories) in Annotations")
            return None
        off = np.asarray(w.project.offset, np.float64)
        f = w.frames[w.active_frame]
        axis, params = np.asarray(f.normal, np.float64), self.box.params()
        jobs = []
        for x in items:
            v, closed = geometry(x)
            jobs.append((x.id, x.name, v - off, closed))
        xyz, tiles = w.project.xyz, w.project.tiles
        filters = w.filters() if w.viewport.renderer is not None else None
        t.busy, t.stopping = True, False
        t.dock.status.setText(f"measuring the curb top along {len(items)} line{'s' * (len(items) != 1)}...")

        def work():
            out = []
            for iid, name, v, closed in jobs:
                if t.stopping:
                    break
                t0 = time.perf_counter()
                try:
                    r = curb_fit(xyz, tiles, v, axis, params, closed, filters, lambda fr: not t.stopping)
                    r["seconds"] = time.perf_counter() - t0
                    out.append((iid, r))
                except (ValueError, InterruptedError) as e:
                    out.append((iid, e))
            return {"results": out, "frame": f.name, "axis": axis, "params": params}

        run_in_worker(work, lambda res, err, s: self._done(res, err), "curb_fit")
        return True

    def _done(self, res, err):
        t = self.trace
        t.busy = False
        if err is not None:
            t.dock.status.setText(f"no fit: {err}")
            return
        w = t.w
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
            t.dock.status.setText("; ".join(msgs) or "nothing fitted")
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
            new = t.line_item(r["vertices"] + off, x.closed, src, as_kind=x.kind)
            if x.kind == "curve" and new.kind == "curve":
                new.frame, new.axis, new.axis_origin = x.frame, x.axis.copy(), x.axis_origin.copy()
            m.replace_node(x, new)
            a.forget_line(iid)
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
        t.dock.status.setText(a.dock.status.text())
        t.redraw()
