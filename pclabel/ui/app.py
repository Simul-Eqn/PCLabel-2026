"""Application entry point: ``python -m pclabel [project] [--gl hardware|software] [--log-frames CSV]``."""

from __future__ import annotations

import faulthandler
import os
import sys
import time
from pathlib import Path

from pclabel.settings import load_settings


def resolve_project(name_or_path: str, projects_dir: str) -> Path:
    p = Path(name_or_path)
    if (p / "project.json").exists():
        return p
    q = Path(projects_dir) / name_or_path
    if (q / "project.json").exists():
        return q
    raise FileNotFoundError(f"no project at {p} or {q}")


_CRASH_LOG = None  # kept open for faulthandler


def crash_log() -> Path:
    """Where a hard crash (e.g. an access violation inside Qt) writes the Python stack of every thread."""
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PCLabel" / "_logs" / "crash.log"


def _enable_crash_log():
    global _CRASH_LOG
    try:
        f = crash_log()
        f.parent.mkdir(parents=True, exist_ok=True)
        _CRASH_LOG = open(f, "a", encoding="utf-8")
        _CRASH_LOG.write(f"\n=== PCLabel started {time.strftime('%Y-%m-%d %H:%M:%S')} (pid {os.getpid()}) ===\n")
        _CRASH_LOG.flush()
        faulthandler.enable(_CRASH_LOG, all_threads=True)
    except OSError:
        faulthandler.enable()  # to the console at least


def run(project: str | None = None, gl: str | None = None, log_frames: str | None = None) -> int:
    _enable_crash_log()
    settings = load_settings()
    if gl:
        settings.gl = gl
    from pclabel.render import glsetup

    glsetup.configure(settings, settings.gl)  # before the QApplication
    from PySide6.QtWidgets import QApplication

    from pclabel.taxonomy import load_taxonomy
    from pclabel.ui.main_window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("PCLabel")
    win = MainWindow(settings, load_taxonomy(), log_frames=log_frames)
    win.resize(1600, 1000)
    win.showMaximized()
    if project:
        win.open_project(resolve_project(project, settings.projects_dir))
    return app.exec()
