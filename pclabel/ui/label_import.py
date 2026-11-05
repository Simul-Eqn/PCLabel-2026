"""File > Import labels... (Ctrl+Shift+I): labels made elsewhere (a DXF drawing's layers) as annotations.

The file is read in a worker thread; a dialog then lists its layers with their features, the label each gets (from
the best-matching profile in ``config/label_imports``, editable) and which to import, plus how the drawing sits on the
scan. The import is one undoable step: a directory named after the file, a directory per layer (store/label_import.py).
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from pclabel.store.label_import import FORMATS, apply_profile, best_profile, coverage, load_profiles, read_source, to_directory
from pclabel.ui import labelbox

COLS = ("import", "layer", "features", "vertices", "label", "why")


class LabelImportDialog(QDialog):
    def __init__(self, source, profiles, choices, cover, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Import labels: {source.path.name}")
        self.resize(980, 640)
        self.source = source
        self.profiles = [p for p in profiles if p.format == source.format]
        v = QVBoxLayout(self)
        inside = cover["inside"] / cover["vertices"] if cover["vertices"] else 0.0
        info = ", ".join(f"{k} {val}" for k, val in source.info.items())
        skipped = ", ".join(f"{n} {t}" for t, n in source.skipped.most_common()) or "nothing"
        n_feat = sum(len(lay.features) for lay in source.layers)
        head = self.head = QLabel(f"<b>{source.path.name}</b> ({source.format.upper()}, {info})<br>"
                      f"{n_feat:,} features in {len(source.layers)} layers; {cover['vertices']:,} vertices, "
                      f"<b>{inside:.1%}</b> inside the scan's bounding box; not read: {skipped}")  # fmt: skip
        head.setWordWrap(True)
        if cover["vertices"] and inside < 0.5:
            head.setStyleSheet("color: #d04020")
            head.setToolTip("most of the drawing lies outside the scan: another site, or another coordinate system?")
        v.addWidget(head)
        row = QHBoxLayout()
        row.addWidget(QLabel("labels from"))
        self.profile = QComboBox()
        self.profile.addItem("(no profile: no labels)", None)
        for p in self.profiles:
            self.profile.addItem(p.name, p)
        best = best_profile(source, self.profiles)
        if best is not None:
            self.profile.setCurrentIndex(self.profiles.index(best) + 1)
        self.profile.setToolTip("a profile in config/label_imports maps the source's layers to labels")
        row.addWidget(self.profile, 1)
        self.all = QPushButton("all")
        self.none = QPushButton("none")
        row.addWidget(self.all)
        row.addWidget(self.none)
        v.addLayout(row)
        t = self.table = QTableWidget(len(source.layers), len(COLS))
        t.setHorizontalHeaderLabels(COLS)
        t.verticalHeader().setVisible(False)
        self.boxes = []
        for i, lay in enumerate(source.layers):
            on = QTableWidgetItem()
            on.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            on.setCheckState(Qt.CheckState.Checked)
            t.setItem(i, 0, on)
            name = QTableWidgetItem(lay.name)
            name.setBackground(QColor(lay.color))
            name.setForeground(QColor("#000000") if QColor(lay.color).lightness() > 120 else QColor("#ffffff"))
            t.setItem(i, 1, name)
            t.setItem(i, 2, QTableWidgetItem(", ".join(f"{n} {k}" for k, n in lay.counts().most_common())))
            n = QTableWidgetItem(f"{lay.n_vertices():,}")
            n.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.setItem(i, 3, n)
            box = QComboBox()
            box.setEditable(True)
            labelbox.fill(box, choices, blank=True)  # the taxonomy as a tree
            self.boxes.append(box)
            t.setCellWidget(i, 4, box)
            t.setItem(i, 5, QTableWidgetItem(""))
        for item in (t.item(i, c) for i in range(t.rowCount()) for c in (1, 2, 3, 5)):
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        t.setColumnWidth(0, 52)
        t.setColumnWidth(1, 190)
        t.setColumnWidth(2, 220)
        t.setColumnWidth(3, 70)
        t.setColumnWidth(4, 150)
        t.horizontalHeader().setStretchLastSection(True)
        v.addWidget(t, 1)
        hint = QLabel("Each layer becomes a directory with its label (change it here or later on the directory); its "
                      "features become polylines (map symbols: markers) with their DXF layer, type and handle.")  # fmt: skip
        hint.setWordWrap(True)
        v.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.profile.currentIndexChanged.connect(lambda _: self._apply_profile())
        self.all.clicked.connect(lambda: self._tick(True))
        self.none.clicked.connect(lambda: self._tick(False))
        self._apply_profile()

    def _apply_profile(self):
        p = self.profile.currentData()
        apply_profile(self.source, p)
        for i, lay in enumerate(self.source.layers):
            self.boxes[i].setCurrentText(lay.label)
            note = p.notes.get(lay.name, "") if p is not None else ""
            unknown = p is not None and lay.name not in p.layers
            self.table.item(i, 5).setText("(not in the profile)" if unknown else note)

    def _tick(self, on: bool):
        for i in range(self.table.rowCount()):
            self.table.item(i, 0).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def chosen(self) -> list:
        """The names of the layers ticked for import; their labels (as edited) are written to the source's layers."""
        out = []
        for i, lay in enumerate(self.source.layers):
            lay.label = self.boxes[i].currentText().strip()
            if self.table.item(i, 0).checkState() == Qt.CheckState.Checked:
                out.append(lay.name)
        return out


class _Read(QObject):
    done = Signal(object, object, float)  # source, error, seconds


class LabelImportController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self._read = _Read(self)  # lives in the UI thread; ``_on_read`` runs there (a bound method of a QObject)
        self._read.done.connect(self._on_read)
        self._busy = False
        self.last_dialog = None

    def start(self, path=None):
        """Choose a file, read it in a worker thread, then show the dialog."""
        w = self.w
        if w.project is None or self._busy:
            return
        if path is None:
            filt = ";;".join(f"{d} (*{s})" for s, d in FORMATS.items())
            path, _ = QFileDialog.getOpenFileName(w, "Import labels", "", filt)
            if not path:
                return
        self._busy = True
        w.statusBar().showMessage(f"reading {path}...")

        def work():
            t0 = time.perf_counter()
            try:
                self._read.done.emit(read_source(path), None, time.perf_counter() - t0)
            except Exception as e:  # reported in the UI thread
                self._read.done.emit(None, e, time.perf_counter() - t0)

        threading.Thread(target=work, daemon=True, name="read-labels").start()

    def _on_read(self, source, err, seconds):
        self._busy = False
        if err is not None:
            self.w.statusBar().clearMessage()
            QMessageBox.critical(self.w, "Import labels", str(err))
            return
        self.w.statusBar().showMessage(f"read {source.path.name} in {seconds:.2f} s", 6000)
        dlg = self.dialog(source)
        if dlg.exec():
            self.add(source, dlg.chosen())

    def dialog(self, source) -> LabelImportDialog:
        choices = labelbox.taxonomy_entries(self.w.taxonomy)
        self.last_dialog = LabelImportDialog(source, load_profiles(), choices, coverage(source, self.w.project), self.w)
        return self.last_dialog

    def add(self, source, layers=None):
        """Add the import (the ``layers`` named, or all) to the annotations as one undoable step."""
        a = self.w.annot
        top = to_directory(source, layers)
        if not top.children:
            a.dock.status.setText("nothing imported: no layer ticked")
            return None
        n = sum(len(d.children) for d in top.children)
        a._checkpoint(f"import labels {source.path.name}")
        a.model.root.append(top)
        labelled = sum(len(d.children) for d in top.children if d.label)
        a._changed(f"imported {n:,} features in {len(top.children)} layers from {source.path.name} "
                   f"({labelled:,} with a label)")  # fmt: skip
        return top
