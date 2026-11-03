"""Import dialog: choose a scan, name the project, import in a worker thread with progress and cancel."""

from __future__ import annotations

import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QWidget,
)

from pclabel import REPO_ROOT
from pclabel.store.importer import ImportCancelled, import_scan, project_name_for


class ImportDialog(QDialog):
    def __init__(self, projects_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import scan")
        self.setMinimumWidth(640)
        f = QFormLayout(self)
        self.source = QLineEdit()
        browse = QPushButton("Browse...")
        row = QHBoxLayout()
        row.addWidget(self.source)
        row.addWidget(browse)
        holder = QWidget()
        holder.setLayout(row)
        f.addRow("scan (.e57, read-only)", holder)
        self.name = QLineEdit()
        f.addRow("project name", self.name)
        self.projects = QLineEdit(projects_dir)
        f.addRow("projects folder", self.projects)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        f.addRow(buttons)
        browse.clicked.connect(self._browse)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

    def _browse(self):
        start = str((REPO_ROOT.parent / "data") if (REPO_ROOT.parent / "data").exists() else Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Scan to import", start, "E57 scans (*.e57)")
        if path:
            self.source.setText(path)
            if not self.name.text():
                self.name.setText(project_name_for(path))

    def _accept(self):
        src = Path(self.source.text())
        if not src.is_file():
            QMessageBox.warning(self, "Import", f"not a file: {src}")
            return
        if not self.name.text():
            self.name.setText(project_name_for(src))
        if (Path(self.projects.text()) / self.name.text()).exists():
            QMessageBox.warning(self, "Import", "a project with this name already exists; choose another name")
            return
        self.accept()


class _Worker(QObject):
    progress = Signal(str, float)
    done = Signal(object, object)  # path, error

    def __init__(self, source, name, projects_dir, cancel):
        super().__init__()
        self.args = (source, name, projects_dir)
        self.cancel = cancel

    def run(self):
        try:
            src, name, pdir = self.args
            path = import_scan(src, name=name, projects_dir=pdir, cancel=self.cancel,
                               progress=lambda s, f: self.progress.emit(s, f))  # fmt: skip
            self.done.emit(path, None)
        except ImportCancelled:
            self.done.emit(None, "cancelled")
        except Exception as e:
            self.done.emit(None, f"{e}\n\n{traceback.format_exc(limit=3)}")


class _Relay(QObject):
    """Receives the worker's signals in the UI thread. A signal connected to a plain function (no receiver object)
    runs it in the sender's thread: the worker's. The progress dialog was then touched from the worker ("Timers
    cannot be started from another thread"), and ``thread.wait()`` ran in the thread it waited for: the import hung
    (the user's report of 2026-11-03)."""

    def __init__(self, source, dlg, thread, on_done, parent):
        super().__init__(parent)
        self.name, self.dlg, self.thread, self.on_done = Path(source).name, dlg, thread, on_done

    def progress(self, stage, frac):
        self.dlg.setValue(int(frac * 1000))
        self.dlg.setLabelText(f"Importing {self.name}: {stage} ({frac:.0%})")

    def finished(self, path, err):
        self.thread.quit()
        self.thread.wait()
        self.dlg.close()
        self.on_done(path, err)


def run_import(parent, source: str, name: str, projects_dir: str, on_done):
    """Start an import in a worker thread with a progress dialog; calls on_done(path or None, error or None) in the
    UI thread."""
    cancel = threading.Event()
    dlg = QProgressDialog(f"Importing {Path(source).name}", "Cancel", 0, 1000, parent)
    dlg.setWindowTitle("Import")
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    thread = QThread(parent)
    worker = _Worker(source, name, projects_dir, cancel)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    relay = _Relay(source, dlg, thread, on_done, parent)  # lives in the UI thread: its slots run there
    worker.progress.connect(relay.progress, Qt.ConnectionType.QueuedConnection)
    worker.done.connect(relay.finished, Qt.ConnectionType.QueuedConnection)
    dlg.canceled.connect(cancel.set)
    parent._import_job = (thread, worker, dlg, relay)  # keep references alive
    thread.start()
    return relay
