"""Keep the connected pieces of the selection that matter (``tools.pieces``): the Pieces panel and the click tool (P).

- P (Select ▸ "Keep a piece of the selection"): the selection's pieces are found (a worker); the small ones (the
  random points) are tinted magenta. A click keeps the piece under it; Shift+click adds another piece, Ctrl+click
  takes one away. All clicks work on the selection the tool started with; each is one undo step.
- "Keep the largest piece" and "drop small pieces" work on the current selection.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QDockWidget, QFormLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox, QWidget

from pclabel.render.renderer import UNSURE_BIT
from pclabel.tools.pieces import PieceParams, find_pieces
from pclabel.ui.docks import _spin


class PiecesDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Pieces", parent)
        self.setObjectName("pieces")
        d = PieceParams()
        w = QWidget()
        f = QFormLayout(w)
        hint = QLabel("Narrow the selection to the pieces that matter: e.g. a 3D select that took an object and a few "
                      "random points elsewhere. Press P and click the object (Shift+click adds a piece, Ctrl+click "
                      "takes one away), or drop the small pieces. Magenta: the small pieces.")  # fmt: skip
        hint.setWordWrap(True)
        f.addRow(hint)
        self.gap = _spin(1, 200, d.gap * 100, 1, 1, " cm")
        self.gap.setToolTip("selected points closer than this are one piece (up to about 3.5 times this may join)")
        f.addRow("joined within", self.gap)
        self.min_points = QSpinBox()
        self.min_points.setRange(1, 100_000_000)
        self.min_points.setSingleStep(50)
        self.min_points.setValue(d.min_points)
        self.min_points.setToolTip("pieces with fewer points count as small (the random points)")
        f.addRow("small: under", self.min_points)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        f.addRow(self.result)
        row = QHBoxLayout()
        self.click = QPushButton("click a piece (P)")
        self.click.setCheckable(True)
        self.largest = QPushButton("keep the largest piece")
        self.drop = QPushButton("drop small pieces")
        for b in (self.click, self.largest, self.drop):
            row.addWidget(b)
        f.addRow(row)
        self.setWidget(w)

    def params(self) -> PieceParams:
        return replace(PieceParams(), gap=self.gap.value() / 100, min_points=self.min_points.value())


class _Done(QObject):
    done = Signal(object, object, object, object)  # pieces, seconds, error, what to do next


class PiecesController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.dock = PiecesDock(window)
        self.pieces = None  # of the selection in pieces.source
        self.base = None  # the click tool's selection (its clicks all work on this one)
        self.kept: set = set()  # the click tool's pieces kept so far
        self._busy = False
        self._done = _Done()
        self._done.done.connect(self._finished)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(lambda: self._ensure(self._current(), lambda: None, force=True))
        d = self.dock
        d.gap.valueChanged.connect(lambda _: self._timer.start())
        d.min_points.valueChanged.connect(lambda _: self._show())
        d.click.clicked.connect(lambda on: self.w.toggle_tool("piece") if on != (self.w.viewport.tool == "piece") else None)
        d.largest.clicked.connect(lambda: self.keep_largest())
        d.drop.clicked.connect(lambda: self.drop_small())
        window.viewport.tool_changed.connect(self.on_tool_changed)

    def _current(self):
        return self.base if self.base is not None else self.w.selection

    # ---- finding the pieces (a worker) ----------------------------------------------------------------------

    def _ensure(self, selection, then, force: bool = False):
        """Run ``then`` once the pieces of ``selection`` (by identity) with the panel's gap are known."""
        p = self.dock.params()
        if not force and self.pieces is not None and self.pieces.source is selection and self.pieces.params.gap == p.gap:
            then()
            return
        if selection.size == 0:
            self.dock.result.setText("select something first (e.g. a 3D select), then keep its pieces")
            return
        if self._busy:
            self.dock.result.setText("still finding the pieces...")
            return
        self._busy = True
        self.dock.result.setText(f"finding the pieces of {selection.size:,} points...")
        xyz = self.w.project.xyz

        def work():
            t0 = time.perf_counter()
            try:
                self._done.done.emit(find_pieces(xyz, selection, p), time.perf_counter() - t0, None, then)
            except Exception as e:  # reported in the UI thread
                self._done.done.emit(None, time.perf_counter() - t0, e, None)

        threading.Thread(target=work, daemon=True, name="pieces").start()

    def _finished(self, pieces, seconds, err, then):
        self._busy = False
        if err is not None:
            self.dock.result.setText(f"failed: {err}")
            return
        self.pieces = pieces
        self._seconds = seconds
        self._show()
        then()

    def _show(self):
        """The pieces in the panel; the small ones tinted magenta while they are pieces of the selection shown."""
        p = self.pieces
        vp = self.w.viewport
        current = p is not None and p.source is self.w.selection
        small = p.small(self.dock.min_points.value()) if p is not None else None
        if vp.renderer is not None:
            vp.makeCurrent()
            try:
                vp.renderer.set_marks(UNSURE_BIT, p.points(small) if current and small.size else None)
            finally:
                vp.doneCurrent()
            vp.update()
        if p is None:
            return
        s = p.summary(self.dock.min_points.value())
        big = ", ".join(f"{n:,}" for n in s["largest"])
        self.dock.result.setText(
            f"{s['pieces']:,} piece{'s' * (s['pieces'] != 1)} in {s['points']:,} points; the largest: {big}\n"
            f"{s['small_pieces']:,} small (under {self.dock.min_points.value():,} points): {s['small_points']:,} points"
            + (f"\n{self._seconds:.2f} s" if getattr(self, "_seconds", None) is not None else ""))  # fmt: skip

    def _apply(self, idx, definition):
        self.w.set_selection(idx, definition, filtered=False)
        self._show()

    def _record(self, op, ids, extra=None):
        p = self.pieces
        return {"tool": "pieces", "op": "replace", "action": op, "params": {"gap": p.params.gap,
                "min_points": self.dock.min_points.value()}, "pieces": int(p.count),
                "kept": [int(p.sizes[i]) for i in sorted(ids)][:20], **(extra or {})}  # fmt: skip

    # ---- the buttons: on the current selection --------------------------------------------------------------

    def keep_largest(self):
        sel = self.w.selection

        def go():
            if self.pieces.count:
                self._apply(self.pieces.points([0]), self._record("keep_largest", [0]))
                self.dock.result.setText(self.dock.result.text() + f"\nkept the largest piece: {self.w.selection.size:,} points")

        self._ensure(sel, go)

    def drop_small(self):
        sel = self.w.selection

        def go():
            p = self.pieces
            small = set(p.small(self.dock.min_points.value()).tolist())
            keep = [i for i in range(p.count) if i not in small]
            self._apply(p.points(keep), self._record("drop_small", keep, {"dropped_points": int(p.sizes[list(small)].sum()) if small else 0}))
            self.dock.result.setText(self.dock.result.text() + f"\ndropped {len(small):,} small pieces")

        self._ensure(sel, go)

    # ---- the click tool (P) ------------------------------------------------------------------------------

    def on_tool_changed(self, name):
        self.dock.click.setChecked(name == "piece")
        if name == "piece":
            self.base, self.kept = self.w.selection, set()
            self.dock.show()
            self.dock.raise_()
            self._ensure(self.base, lambda: None)
        elif self.base is not None:
            self.base, self.kept = None, set()
            self._show()

    def click(self, p_local, op: str):
        """Keep the piece under the click (``op`` "replace"), add it ("add") or take it away ("subtract")."""
        if self.base is None:
            return

        def go():
            p = self.pieces
            i = p.at(p_local)
            if i < 0:
                self.dock.result.setText("no selected point within 30 cm of the click")
                return
            if op == "add":
                self.kept |= {i}
            elif op == "subtract":
                self.kept = (self.kept or set(range(p.count))) - {i}
            else:
                self.kept = {i}
            self._apply(p.points(self.kept), self._record("click", self.kept, {"click_op": op}))
            self.dock.result.setText(f"piece {i + 1} of {p.count:,} ({int(p.sizes[i]):,} points); "
                                     f"{len(self.kept)} piece{'s' * (len(self.kept) != 1)} kept: {self.w.selection.size:,} points")  # fmt: skip

        self._ensure(self.base, go)

    def cancel(self):
        self.pieces, self.base, self.kept = None, None, set()
        vp = self.w.viewport
        if vp.renderer is not None:
            vp.makeCurrent()
            try:
                vp.renderer.set_marks(UNSURE_BIT, None)
            finally:
                vp.doneCurrent()
        self.dock.result.setText("")
