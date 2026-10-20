"""Split a point set by roughness in the app (``tools.split``): the Split panel.

1. Select a point set in the Annotations panel and press "split..." there (or "use selected point set" here). The
   set's voxel features are computed (a worker thread) and the set becomes the selection (yellow).
2. Choose the feature (curvature or roughness) and its threshold ("auto": Otsu's). The rough group is tinted orange;
   the smooth group stays yellow.
3. Clean-up (majority within a radius, small islands) moves isolated voxels to the group around them. Points that
   may be misclassified (changed by the clean-up, or near the threshold) are tinted magenta.
4. "Split" replaces the set by two point sets, "<name> smooth" and "<name> rough" (undoable).
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace

from PySide6.QtCore import QObject, Qt, QTimer, Signal
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

from pclabel.render.renderer import GROUP_BIT, UNSURE_BIT
from pclabel.tools.split import FEATURES, SplitParams, set_features, split_labels
from pclabel.ui.docks import _spin

# threshold display: curvature as is, roughness in mm
UNITS = {"curvature": (1.0, 6, 0.0005, ""), "roughness": (1000.0, 3, 0.1, " mm")}


class SplitDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Split", parent)
        self.setObjectName("split")
        d = SplitParams()
        w = QWidget()
        f = QFormLayout(w)
        hint = QLabel("Split a point set in two by roughness: select it in the Annotations panel and press \"split...\". "
                      "Orange: the rough group; yellow: the smooth group; magenta: points to look at (changed by the "
                      "clean-up, or near the threshold).")  # fmt: skip
        hint.setWordWrap(True)
        f.addRow(hint)
        row = QHBoxLayout()
        self.use = QPushButton("use selected point set")
        self.source = QLabel("")
        row.addWidget(self.use)
        row.addWidget(self.source, 1)
        f.addRow("set", row)
        self.feature = QComboBox()
        for k, text in FEATURES.items():
            self.feature.addItem(text, k)
        f.addRow("feature", self.feature)
        trow = QHBoxLayout()
        self.threshold = _spin(0, 1e6, d.threshold, 0.0005, 5, "")
        self.auto = QPushButton("auto")
        self.auto.setToolTip("Otsu's threshold of the feature over the set (on a log scale)")
        trow.addWidget(self.threshold, 1)
        trow.addWidget(self.auto)
        f.addRow("rough above", trow)
        self.stats = QLabel("")
        self.stats.setWordWrap(True)
        f.addRow("", self.stats)
        self.clean = QCheckBox("clean up")
        self.clean.setChecked(d.clean)
        self.clean.setToolTip("move isolated voxels to the group around them")
        f.addRow("", self.clean)
        self.radius = _spin(1, 100, d.radius * 100, 1, 2, " cm")
        self.radius.setToolTip("the neighbourhood for the majority")
        f.addRow("majority within", self.radius)
        self.majority = _spin(50, 100, d.majority * 100, 5, 1, " %")
        self.majority.setToolTip("a voxel changes group when at least this share of its neighbourhood's points is in the "
                                 "other group")  # fmt: skip
        f.addRow("majority", self.majority)
        self.iterations = QSpinBox()
        self.iterations.setRange(0, 10)
        self.iterations.setValue(d.iterations)
        f.addRow("rounds", self.iterations)
        self.min_island = QSpinBox()
        self.min_island.setRange(0, 10_000_000)
        self.min_island.setSingleStep(50)
        self.min_island.setValue(d.min_island)
        self.min_island.setToolTip("connected pieces of one group with fewer points join the other group")
        f.addRow("smallest island", self.min_island)
        self.highlight = QCheckBox("highlight points to look at")
        self.highlight.setChecked(True)
        f.addRow("", self.highlight)
        self.band = _spin(0, 100, d.band * 100, 5, 1, " %")
        self.band.setToolTip("points whose feature is this close to the threshold (a share of it) count as unsure")
        f.addRow("near the threshold", self.band)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        f.addRow(self.result)
        brow = QHBoxLayout()
        self.split = QPushButton("split into two point sets")
        self.cancel = QPushButton("cancel")
        brow.addWidget(self.split)
        brow.addWidget(self.cancel)
        f.addRow(brow)
        self.setWidget(w)

    def set_units(self, feature: str, value: float):
        k, dec, step, suffix = UNITS[feature]
        self.threshold.blockSignals(True)
        self.threshold.setDecimals(dec)
        self.threshold.setSingleStep(step)
        self.threshold.setSuffix(suffix)
        self.threshold.setValue(value * k)
        self.threshold.blockSignals(False)

    def params(self) -> SplitParams:
        feature = self.feature.currentData()
        return replace(SplitParams(), feature=feature, threshold=self.threshold.value() / UNITS[feature][0],
                       clean=self.clean.isChecked(), radius=self.radius.value() / 100,
                       majority=self.majority.value() / 100, iterations=self.iterations.value(),
                       min_island=self.min_island.value(), band=self.band.value() / 100)  # fmt: skip


class _Done(QObject):
    done = Signal(object, object, object)  # kind, result, error


class SplitController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.dock = SplitDock(window)
        self.source_id = None
        self.features = None
        self.last = None  # (rough, unsure, info) of the last preview
        self._busy = False
        self._again = False
        self._done = _Done()
        self._done.done.connect(self._finished)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.preview)
        d = self.dock
        d.use.clicked.connect(lambda: self.start())
        d.auto.clicked.connect(self.auto)
        d.feature.currentIndexChanged.connect(lambda _: self._feature_changed())
        for wdg in (d.threshold, d.radius, d.majority, d.band):
            wdg.valueChanged.connect(lambda _: self._timer.start())
        for wdg in (d.iterations, d.min_island):
            wdg.valueChanged.connect(lambda _: self._timer.start())
        for cb in (d.clean, d.highlight):
            cb.toggled.connect(lambda _: self._timer.start())
        d.split.clicked.connect(lambda: self.split())
        d.cancel.clicked.connect(self.cancel)

    def _source(self):
        if self.source_id is None:
            return None
        try:
            x = self.w.annot.model.node(self.source_id)
        except KeyError:
            return None
        return x if x.kind == "points" else None

    def start(self, point_set_id: str | None = None):
        """Work on this point set (or the one selected in the Annotations panel): compute its features."""
        a = self.w.annot
        if point_set_id is None:
            sets_ = [m for m in a._selected_items() if m.kind == "points"]
            if not sets_:
                self.dock.result.setText("select a point set in the Annotations panel first")
                return
            point_set_id = sets_[0].id
        ps = a.model.node(point_set_id)
        self.source_id, self.features, self.last = ps.id, None, None
        self.dock.source.setText(f"{ps.name} ({ps.indices.size:,} points)")
        self.dock.show()
        self.dock.raise_()
        self.w.set_selection(ps.indices, {"tool": "split", "op": "replace", "point_set": ps.name})
        self._run("features", lambda: set_features(self.w.project.xyz, ps.indices))

    def _run(self, kind, fn):
        if self._busy:
            self._again = True
            return
        self._busy = True
        self.dock.result.setText("computing the features..." if kind == "features" else "splitting...")

        def work():
            t0 = time.perf_counter()
            try:
                self._done.done.emit(kind, (fn(), time.perf_counter() - t0), None)
            except Exception as e:  # reported in the UI thread
                self._done.done.emit(kind, None, e)

        threading.Thread(target=work, daemon=True, name="split").start()

    def _finished(self, kind, res, err):
        self._busy = False
        if err is not None:
            self.dock.result.setText(f"failed: {err}")
            return
        out, seconds = res
        if kind == "features":
            self.features = out
            self.dock.result.setText(f"features of {(out.keys != -1).sum():,} voxels in {seconds:.1f} s")
            self._feature_changed(keep=False)
        else:
            self.last = out
            self._show(seconds)
        if self._again:
            self._again = False
            self.preview()

    def _feature_changed(self, keep: bool = False):
        f = self.features
        name = self.dock.feature.currentData()
        if f is None:
            return
        k, _, _, suffix = UNITS[name]
        pct = f.percentiles(name)
        if pct:
            self.dock.stats.setText(f"over the set: 10 % {pct[0] * k:.5g}{suffix}, half {pct[1] * k:.5g}{suffix}, "
                                    f"90 % {pct[2] * k:.5g}{suffix}")  # fmt: skip
        if not keep:
            self.dock.set_units(name, f.otsu(name))
        self.preview()

    def auto(self):
        if self.features is not None:
            name = self.dock.feature.currentData()
            self.dock.set_units(name, self.features.otsu(name))
            self.preview()

    def preview(self):
        if self.features is None:
            return
        feat, p = self.features, self.dock.params()
        self._run("split", lambda: split_labels(feat, p))

    def _show(self, seconds: float):
        rough, unsure, info = self.last
        idx = self.features.indices
        vp = self.w.viewport
        if vp.renderer is not None:
            vp.makeCurrent()
            try:
                vp.renderer.set_marks(GROUP_BIT, idx[rough])
                vp.renderer.set_marks(UNSURE_BIT, idx[unsure] if self.dock.highlight.isChecked() else None)
            finally:
                vp.doneCurrent()
            vp.update()
        n = max(info["points"], 1)
        self.dock.result.setText(
            f"rough {info['rough']:,} ({100 * info['rough'] / n:.1f} %), smooth {info['smooth']:,}\n"
            f"to look at: {info['unsure']:,} (changed by the clean-up {info['changed_by_clean_up']:,}, near the "
            f"threshold {info['near_threshold']:,}, too few neighbours {info['no_feature']:,})\n"
            f"{seconds:.2f} s")  # fmt: skip

    def _clear(self):
        vp = self.w.viewport
        if vp.renderer is not None:
            vp.makeCurrent()
            try:
                vp.renderer.set_marks(GROUP_BIT, None)
                vp.renderer.set_marks(UNSURE_BIT, None)
            finally:
                vp.doneCurrent()
            vp.update()

    def cancel(self):
        self._clear()
        self.source_id, self.features, self.last = None, None, None
        self.dock.source.setText("")
        self.dock.result.setText("")

    def split(self):
        """Replace the set by its smooth and rough parts (two point sets)."""
        ps = self._source()
        if ps is None or self.last is None or self._busy:
            self.dock.result.setText("nothing to split yet")
            return None
        rough, _, info = self.last
        a = self.w.annot
        a._checkpoint(f"split {ps.name}")
        smooth, rough_set = a.model.split_point_set(ps.id, rough, {"tool": "split", **info["params"]})
        self._clear()
        self.source_id, self.features, self.last = None, None, None
        self.dock.source.setText("")
        a._changed(f"{ps.name} split: {smooth.name} ({smooth.indices.size:,}), {rough_set.name} "
                   f"({rough_set.indices.size:,})")  # fmt: skip
        self.dock.result.setText(a.dock.status.text())
        return smooth, rough_set
