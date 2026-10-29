"""The labels view and the label map (the user's request of 2026-10-28): the Labels panel.

- **Labels view** (B, or the "labels" colour mode): every point set's points in its label's colour (its own label or
  its directory's, D-007), segments and curves too; points in no labelled point set are dim grey, or hidden with
  "only labelled points". Where point sets overlap, the smaller one shows (a ``marking`` over its ``road``).
- **Legend:** the labels in use with their colours, items and points. A label's colour is its taxonomy class colour,
  else one given once from a palette; double-click a colour to change it (saved with the annotations). Unticked
  labels are left out of the view, the map and the export.
- **Export annotations** (the user's request of 2026-10-29): the items with the ticked labels, in their directories,
  as an annotations file (``Annotations.export(labels=...)``).
- **Label map** (Shift+B): everything shown projected onto a plane (z, or a fitted plane) as a PNG with a legend
  strip, and a JSON sidecar with the pixel-to-world transform, the legend and the segments and curves as 2D lines
  in map metres (tools/labelmap.py). Runs in a worker thread.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pclabel.render.shaders import MODE_LABELS
from pclabel.store.annotations import PALETTE
from pclabel.store.importer import write_json_atomic
from pclabel.tools.labelmap import LABEL_MAP_SCHEMA, LabelMapParams, label_map
from pclabel.ui.docks import _spin

NO_LABEL = ""  # point sets, segments and curves without a label (own or inherited)
NO_LABEL_COLOR = "#9e9e9e"
LEGEND_WIDTH = 240  # pixels: the legend strip on the right of the map image


def _rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


class LabelsDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Labels", parent)
        self.setObjectName("labels")
        p = LabelMapParams()
        w = QWidget()
        v = QVBoxLayout(w)
        hint = QLabel("Every point set in its label's colour (own or its directory's), segments and curves too. Where "
                      "sets overlap the smaller one shows (a marking over its road). Double-click a colour to change "
                      "it; untick a label to leave it out of the view and the map.")  # fmt: skip
        hint.setWordWrap(True)
        v.addWidget(hint)
        row = QHBoxLayout()
        self.view = QPushButton("labels view (B)")
        self.view.setCheckable(True)
        self.view.setToolTip("colour the points by their annotation labels (the \"labels\" colour mode; B: back)")
        self.only = QCheckBox("only labelled points")
        self.only.setToolTip("hide the points in no (shown) labelled point set; otherwise they are dim grey")
        row.addWidget(self.view)
        row.addWidget(self.only)
        v.addLayout(row)
        self.legend = QTreeWidget()
        self.legend.setHeaderLabels(["label", "colour", "items", "points"])
        self.legend.setRootIsDecorated(False)
        self.legend.setColumnWidth(0, 130)
        self.legend.setColumnWidth(1, 60)
        self.legend.setColumnWidth(2, 45)
        self.legend.headerItem().setToolTip(3, "points in its point sets (a point in two of its sets counts twice)")
        v.addWidget(self.legend, 1)
        erow = QHBoxLayout()
        self.export_ann = QPushButton("export annotations...")
        self.export_ann.setToolTip("the point sets, segments and curves with the ticked labels, in their directories, "
                                   "as an annotations file (like \"export all\"; imports with the same labels)")  # fmt: skip
        self.tick_all = QPushButton("tick all")
        self.tick_none = QPushButton("tick none")
        erow.addWidget(self.export_ann)
        erow.addWidget(self.tick_all)
        erow.addWidget(self.tick_none)
        v.addLayout(erow)
        f = QFormLayout()
        self.plane = QComboBox()
        self.plane.setToolTip("the map lies on this plane: z (the scan frame, seen from above) or a fitted plane")
        f.addRow("map plane", self.plane)
        self.pixel = _spin(0.5, 100.0, p.pixel * 100, 0.5, 1, " cm")
        self.pixel.setToolTip("one pixel of the map")
        f.addRow("pixel", self.pixel)
        self.dot = _spin(0, 10, p.dot, 1, 0, " px")
        self.dot.setToolTip("extra pixels painted around each point (fills gaps where the scan is sparse)")
        f.addRow("points grow by", self.dot)
        self.line = _spin(0.5, 50.0, p.line_width, 0.5, 1, " px")
        f.addRow("line width", self.line)
        self.slice = QCheckBox("only within the height slice (relative to the map plane)")
        self.slice.setToolTip("only points between the slice's limits above the map plane (Height axis panel)")
        f.addRow("", self.slice)
        self.context = QCheckBox("every other point in light grey")
        self.context.setToolTip("draw the points in no shown label underneath, for context (reads the whole scan)")
        f.addRow("", self.context)
        self.legend_strip = QCheckBox("legend strip on the right")
        self.legend_strip.setChecked(True)
        f.addRow("", self.legend_strip)
        v.addLayout(f)
        mrow = QHBoxLayout()
        self.export = QPushButton("label map... (Shift+B)")
        self.export.setToolTip("save the map as PNG, with a JSON of its transform, legend and lines")
        self.cancel = QPushButton("cancel")
        self.cancel.setEnabled(False)
        mrow.addWidget(self.export)
        mrow.addWidget(self.cancel)
        v.addLayout(mrow)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.status)
        self.setWidget(w)

    def params(self, band=None) -> LabelMapParams:
        p = replace(LabelMapParams(), pixel=self.pixel.value() / 100, dot=int(self.dot.value()),
                    line_width=self.line.value())  # fmt: skip
        if band is not None:
            p = replace(p, h_min=float(band[0]), h_max=float(band[1]))
        return p


class _Done(QObject):
    done = Signal(object, object)  # result, error
    progress = Signal(float)


class LabelsController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.dock = LabelsDock(window)
        self.hidden: set = {NO_LABEL}  # labels left out of the view and the map (this session); unlabelled sets at first
        self._synced = None  # (annotations version, hidden, colours) last sent to the renderer
        self._legend_key = None  # the same, last shown in the legend
        self._prev_mode = None  # the colour mode before the labels view
        self._busy = False
        self._cancel = False
        self.last = None  # the last map: {"png", "json", "doc"}
        self._done = _Done()
        self._done.done.connect(self._finished)
        self._done.progress.connect(lambda f: self.dock.status.setText(f"working... {100 * f:.0f} %"))
        d = self.dock
        d.view.clicked.connect(lambda on: self.set_view(on))
        d.only.toggled.connect(lambda _: self.sync())
        d.legend.itemChanged.connect(self._on_item_changed)
        d.legend.itemDoubleClicked.connect(lambda it, col: self.pick_color(it.data(0, Qt.ItemDataRole.UserRole)))
        d.export.clicked.connect(lambda: self.export_map())
        d.export_ann.clicked.connect(lambda: self.export_annotations())
        d.tick_all.clicked.connect(lambda: self.set_shown(self.labels_in_use()))
        d.tick_none.clicked.connect(lambda: self.set_shown([]))
        d.cancel.clicked.connect(self.cancel)
        d.visibilityChanged.connect(lambda on: self.fill_planes() if on else None)

    # ---- labels and colours ---------------------------------------------------------------------------------

    def _model(self):
        return self.w.annot.model

    def labels_in_use(self) -> list:
        """The labels of the point sets, segments and curves: taxonomy classes in their order, then flags, then other
        labels, then "" (no label)."""
        m = self._model()
        used = {m.label_of(x.id)[0] for x in m.items()}
        tax = self.w.taxonomy
        order = [c["name"] for c in tax.classes] + [f["name"] for f in tax.flags]
        known = [k for k in order if k in used]
        return known + sorted(used - set(order) - {NO_LABEL}) + ([NO_LABEL] if NO_LABEL in used else [])

    def color(self, label: str) -> str:
        """The label's colour: chosen, else its class colour, else a palette colour given once (and saved)."""
        m = self._model()
        if label in m.label_colors:
            return m.label_colors[label]
        if label == NO_LABEL:
            return NO_LABEL_COLOR
        for c in self.w.taxonomy.classes:
            if c["name"] == label:
                return c["color"]
        taken = set(m.label_colors.values()) | {c["color"].lower() for c in self.w.taxonomy.classes}
        free = [p for p in PALETTE if p.lower() not in taken] or list(PALETTE)
        m.label_colors[label] = free[0]
        self.w.annot.save()
        return free[0]

    def codes(self) -> dict:
        """label -> code (1..254) for the shown labels."""
        return {k: i + 1 for i, k in enumerate(lab for lab in self.labels_in_use() if lab not in self.hidden)}

    def color_table(self, codes) -> np.ndarray:
        table = np.zeros((256, 4), np.uint8)
        for k, c in codes.items():
            table[c, :3] = _rgb(self.color(k))
            table[c, 3] = 255
        return table

    def pick_color(self, label, color: str | None = None):
        if label is None:
            return
        if color is None:
            c = QColorDialog.getColor(QColor(self.color(label)), self.dock, f"Colour of {label or '(no label)'}")
            if not c.isValid():
                return
            color = c.name()
        self._model().label_colors[label] = color
        self.w.annot.save()
        self.sync(force=True)
        self.w.annot.refresh()

    def _on_item_changed(self, it, col):
        if col != 0:
            return
        label = it.data(0, Qt.ItemDataRole.UserRole)
        shown = it.checkState(0) == Qt.CheckState.Checked
        if shown == (label not in self.hidden):
            return
        (self.hidden.discard if shown else self.hidden.add)(label)
        self.sync(force=True)
        self.w.annot.refresh()

    def set_shown(self, labels):
        """Tick exactly these labels in the legend (the others are left out of the view, the map and the export)."""
        self.hidden = set(self.labels_in_use()) - set(labels)
        self.sync(force=True)
        self.w.annot.refresh()

    def export_annotations(self, path=None):
        """The point sets, segments and curves with the ticked labels, in their directories, as an annotations file."""
        w = self.w
        if w.project is None:
            return None
        labels = [lab for lab in self.labels_in_use() if lab not in self.hidden]
        if not labels:
            self.dock.status.setText("tick the labels to export first")
            return None
        if path is None:
            name = labels[0] if len(labels) == 1 else f"{len(labels)} labels"
            path, _ = QFileDialog.getSaveFileName(w, "Export annotations with the ticked labels",
                                                  f"{w.project.name} annotations {name or 'no label'}.json",
                                                  "Annotations (*.json)")  # fmt: skip
            if not path:
                return None
        n = self._model().export(path, w.project, labels=labels)
        shown = ", ".join(lab or "(no label)" for lab in labels)
        self.dock.status.setText(f"exported {n} item{'' if n == 1 else 's'} ({shown}) to {path}")
        return path

    def fill_legend(self):
        m = self._model()
        items, points = {}, {}
        for x in m.items():
            lab = m.label_of(x.id)[0]
            items[lab] = items.get(lab, 0) + 1
            if x.kind == "points":
                points[lab] = points.get(lab, 0) + len(x.indices)
        t = self.dock.legend
        t.blockSignals(True)
        t.clear()
        for lab in self.labels_in_use():
            it = QTreeWidgetItem([lab or "(no label)", "", str(items.get(lab, 0)), f"{points.get(lab, 0):,}"])
            it.setData(0, Qt.ItemDataRole.UserRole, lab)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.CheckState.Unchecked if lab in self.hidden else Qt.CheckState.Checked)
            it.setBackground(1, QColor(self.color(lab)))
            it.setToolTip(1, f"{self.color(lab)} (double-click to change)")
            t.addTopLevelItem(it)
        t.blockSignals(False)

    # ---- the view -------------------------------------------------------------------------------------------

    def active(self) -> bool:
        return self.w.display.mode.currentData() == MODE_LABELS

    def set_view(self, on: bool | None = None):
        """The labels view on or off (None: switch); off goes back to the colour mode before it."""
        on = not self.active() if on is None else on
        if on and not self.active():
            self._prev_mode = self.w.display.mode.currentData()
            self.w.display.set_mode(MODE_LABELS)
        elif not on and self.active():
            self.w.display.set_mode(self._prev_mode if self._prev_mode not in (None, MODE_LABELS) else 0)
        self.dock.view.setChecked(self.active())

    def vector_rgba(self, item):
        """A segment's or curve's colour in the labels view (None: its label is not shown)."""
        lab = self._model().label_of(item.id)[0]
        if lab in self.hidden:
            return None
        return tuple(v / 255 for v in _rgb(self.color(lab))) + (1.0,)

    def sync(self, force: bool = False):
        """Bring the legend and, in the labels view, the renderer's label codes and colours up to date."""
        self.dock.view.setChecked(self.active())
        m = self._model()
        key = (self.w.annot.version, frozenset(self.hidden), tuple(sorted(m.label_colors.items())))
        if force or key != self._legend_key:
            self.fill_legend()
            self._legend_key = key
        r = self.w.viewport.renderer
        if r is None:
            return
        r.labels_only = self.dock.only.isChecked()
        if not self.active() or (not force and key == self._synced):
            self.w.viewport.update()
            return
        codes = self.codes()
        layers = [(ps.indices, codes[lab]) for lab, ps in m.label_layers() if lab in codes]
        vp = self.w.viewport
        vp.makeCurrent()
        try:
            r.set_label_colors(self.color_table(codes))
            r.set_label_layers(layers)
        finally:
            vp.doneCurrent()
        self._synced = key
        vp.update()

    # ---- the map --------------------------------------------------------------------------------------------

    def fill_planes(self):
        box = self.dock.plane
        keep = box.currentText()
        box.blockSignals(True)
        box.clear()
        for f in self.w.frames:
            box.addItem(f.name)
        i = box.findText(keep)
        box.setCurrentIndex(i if i >= 0 else self.w.active_frame)
        box.blockSignals(False)

    def _frame(self):
        self.fill_planes()
        return self.w.frames[max(self.dock.plane.currentIndex(), 0)]

    def export_map(self, path=None):
        """Save the label map (PNG and JSON) of everything shown, on the plane chosen in the panel."""
        w = self.w
        if w.project is None or self._busy:
            return None
        self.dock.show()
        self.dock.raise_()
        frame = self._frame()
        codes = self.codes()
        m = self._model()
        layers = [(ps.indices, codes[lab]) for lab, ps in m.label_layers() if lab in codes]
        vectors, vector_info = [], []
        for x in m.items():
            lab = m.label_of(x.id)[0]
            if x.kind == "points" or lab not in codes:
                continue
            poly = np.array([x.a, x.b]) if x.kind == "segment" else x.polyline()
            if x.kind == "curve" and x.closed:
                poly = np.vstack([poly, poly[:1]])
            vectors.append((poly, codes[lab]))
            vector_info.append({"name": x.name, "kind": x.kind, "label": lab, "closed": bool(getattr(x, "closed", False)),
                                "uncertain": bool(x.uncertain)})  # fmt: skip
        if not layers and not vectors:
            self.dock.status.setText("nothing to map: label some point sets, segments or curves (and tick them here)")
            return None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(w, "Label map", f"{w.project.name} label map {frame.name}.png",
                                                  "PNG image (*.png)")  # fmt: skip
            if not path:
                return None
        band = None
        if self.dock.slice.isChecked():
            fd = w.frames_dock
            band = (fd.slice_a.value() / 100, fd.slice_b.value() / 100)
        params = self.dock.params(band)
        context = w.project.n if self.dock.context.isChecked() else None
        table = self.color_table(codes)
        xyz, offset = w.project.xyz, w.project.offset
        self._busy, self._cancel = True, False
        self.dock.export.setEnabled(False)
        self.dock.cancel.setEnabled(True)
        self.dock.status.setText("working...")
        job = {"path": str(path), "frame": frame, "codes": codes, "params": params, "vectors": vector_info,
               "legend": self.dock.legend_strip.isChecked(), "context": context is not None}  # fmt: skip

        def progress(f):
            self._done.progress.emit(f)
            return not self._cancel

        def work():
            t0 = time.perf_counter()
            try:
                res = label_map(xyz, offset, frame.normal, frame.origin, frame.along, layers, vectors, table, params,
                                context=context, progress=progress)  # fmt: skip
                self._done.done.emit((job, res, time.perf_counter() - t0), None)
            except Exception as e:  # reported in the UI thread
                self._done.done.emit(None, e)

        self._thread = threading.Thread(target=work, daemon=True, name="label-map")
        self._thread.start()
        return path

    def cancel(self):
        self._cancel = True

    def _finished(self, out, err):
        self._busy = False
        self.dock.export.setEnabled(True)
        self.dock.cancel.setEnabled(False)
        if err is not None:
            self.dock.status.setText(f"no map: {err}")
            return
        job, res, seconds = out
        png = Path(job["path"])
        if png.suffix.lower() != ".png":
            png = png.with_suffix(".png")
        doc = self._document(job, res, seconds)
        image = self._image(res["rgba"], doc["legend"] if job["legend"] else None)
        if not image.save(str(png)):
            self.dock.status.setText(f"could not write {png}")
            return
        side = png.with_suffix(".json")
        write_json_atomic(side, doc)
        self.last = {"png": png, "json": side, "doc": doc}
        t = res["transform"]
        self.dock.status.setText(f"{t['width']:,} x {t['height']:,} px at {100 * t['pixel']:g} cm on {job['frame'].name}"
                                 f", {seconds:.1f} s: {png} (and {side.name})")  # fmt: skip

    def _document(self, job, res, seconds) -> dict:
        w = self.w
        src = w.project.meta.get("source", {})
        class_ids = {c["name"]: int(c["id"]) for c in w.taxonomy.classes}
        flags = {f["name"]: int(f["bit"]) for f in w.taxonomy.flags}
        legend = []
        for lab, code in job["codes"].items():
            legend.append({"label": lab, "color": self.color(lab), "code": code, "class_id": class_ids.get(lab),
                           "flag_bit": flags.get(lab), "pixels": res["pixels"].get(code, 0),
                           "points_drawn": res["points"].get(code, 0)})  # fmt: skip
        vectors = [dict(info, map_xy=np.round(uv, 4).tolist()) for info, uv in zip(job["vectors"], res["vectors_uv"])]
        return {
            "schema": LABEL_MAP_SCHEMA,
            "source": {"file": Path(src.get("path", "")).name, "sha256": src.get("sha256"), "n_valid": w.project.n},
            "offset": [float(v) for v in w.project.offset],
            "plane": job["frame"].to_json(),
            "transform": res["transform"],
            "image": {"png": Path(job["path"]).with_suffix(".png").name, "map_columns": res["transform"]["width"],
                      "legend_strip": LEGEND_WIDTH if job["legend"] else 0},  # fmt: skip
            "params": dict(res["params"], context=job["context"]),
            "overlaps": "smaller point sets are drawn over larger ones; segments and curves over the points",
            "legend": legend,
            "vectors": vectors,
            "seconds": round(seconds, 2),
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _image(self, rgba, legend) -> QImage:
        h, wd = rgba.shape[:2]
        rgba = np.ascontiguousarray(rgba)
        img = QImage(rgba.data, wd, h, 4 * wd, QImage.Format.Format_RGBA8888).copy()  # owns its pixels
        if not legend:
            return img
        rows = 28 * (len(legend) + 1) + 10
        out = QImage(wd + LEGEND_WIDTH, max(h, rows), QImage.Format.Format_RGBA8888)
        out.fill(QColor(0, 0, 0, 0))
        p = QPainter(out)
        p.drawImage(0, 0, img)
        p.fillRect(wd, 0, LEGEND_WIDTH, max(h, rows), QColor(255, 255, 255))
        f = QFont()
        f.setPixelSize(15)
        p.setFont(f)
        p.setPen(QColor(20, 20, 20))
        for i, e in enumerate(legend):
            y = 14 + 28 * i
            p.fillRect(wd + 14, y, 22, 18, QColor(e["color"]))
            p.drawText(wd + 46, y + 15, e["label"] or "(no label)")
        p.end()
        return out
