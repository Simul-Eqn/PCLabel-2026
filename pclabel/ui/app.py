"""Application entry point: ``python -m pclabel [project] [--gl hardware|software] [--log-frames CSV]``."""

from __future__ import annotations

import sys
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


def run(project: str | None = None, gl: str | None = None, log_frames: str | None = None) -> int:
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
