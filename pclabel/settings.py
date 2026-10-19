"""Per-machine settings (not per project).

Stored in ``%APPDATA%\\PCLabel\\settings.json``; every key is optional. Environment variables override the file:
``PCLABEL_GL`` (hardware | software), ``PCLABEL_PROJECTS_DIR``, ``PCLABEL_MESA_DIR``.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from pclabel import DEFAULT_PROJECTS_DIR

GL_MODES = ("hardware", "software")
_ENV = {"PCLABEL_GL": "gl", "PCLABEL_PROJECTS_DIR": "projects_dir", "PCLABEL_MESA_DIR": "mesa_dir"}


@dataclass
class Settings:
    projects_dir: str = str(DEFAULT_PROJECTS_DIR)
    # "hardware": the GPU driver. "software": Mesa llvmpipe on the CPU (never touches the GPU).
    gl: str = "hardware"
    # folder holding Mesa's opengl32.dll and libgallium_wgl.dll; "" = newest %LOCALAPPDATA%\PCLabel\mesa-*
    mesa_dir: str = ""
    gpu_memory_gb: float = 8.0  # resident point buffers (hardware)
    software_memory_gb: float = 2.0  # resident point buffers (software)
    point_budget: int = 30_000_000  # drawn points per frame (hardware): 65.8 fps worst case (bench_render, M1)
    software_point_budget: int = 300_000  # llvmpipe: about 15 fps at 1920 x 1009 (bench_render --gl software)

    @property
    def software(self) -> bool:
        return self.gl == "software"

    @property
    def active_point_budget(self) -> int:
        return self.software_point_budget if self.software else self.point_budget

    @property
    def active_memory_bytes(self) -> int:
        return int((self.software_memory_gb if self.software else self.gpu_memory_gb) * 1024**3)


def settings_path() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "PCLabel" / "settings.json"


def load_settings(path: str | Path | None = None) -> Settings:
    path = Path(path) if path else settings_path()
    s = Settings()
    if path.exists():
        doc = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(Settings)}
        for k, v in doc.items():
            if k in known:
                setattr(s, k, v)
    for env, key in _ENV.items():
        if os.environ.get(env):
            setattr(s, key, os.environ[env])
    if s.gl not in GL_MODES:
        raise ValueError(f"settings: gl must be one of {GL_MODES}, not {s.gl!r}")
    return s


def save_settings(s: Settings, path: str | Path | None = None) -> Path:
    path = Path(path) if path else settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path
