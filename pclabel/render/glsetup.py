"""Choosing the OpenGL implementation: the GPU driver ("hardware") or Mesa llvmpipe on the CPU ("software").

Software mode loads a current Mesa build through Qt's own switch (``AA_UseSoftwareOpenGL`` + ``QT_OPENGL_DLL``)
and pins Mesa to llvmpipe (``GALLIUM_DRIVER``; the Windows build could otherwise pick its D3D12 driver, which runs
on the GPU). moderngl then attaches to the context Qt made. :func:`configure` must run before the QApplication
is created.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path

import moderngl
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QOffscreenSurface, QOpenGLContext, QSurfaceFormat

from pclabel.settings import Settings

REQUIRE = 430


@dataclass(frozen=True)
class GLConfig:
    mode: str  # "hardware" | "software"
    libgl: str | None  # Mesa's opengl32.dll in software mode


def surface_format(vsync: bool = False) -> QSurfaceFormat:
    fmt = QSurfaceFormat()
    fmt.setVersion(4, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSwapInterval(1 if vsync else 0)
    return fmt


def find_mesa(settings: Settings) -> Path | None:
    if settings.mesa_dir:
        p = Path(settings.mesa_dir)
        return p if (p / "opengl32.dll").exists() else None
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PCLabel"
    cands = [p for p in base.glob("mesa-*") if (p / "opengl32.dll").exists() and (p / "libgallium_wgl.dll").exists()]

    def version(p):
        try:
            return tuple(int(x) for x in p.name.split("-", 1)[1].split("."))
        except ValueError:
            return (0,)

    return max(cands, key=version) if cands else None


_configured: GLConfig | None = None


def configure(settings: Settings, mode: str | None = None, vsync: bool = False) -> GLConfig:
    """Select the GL implementation for this process. Call once, before the QApplication exists."""
    global _configured
    mode = mode or settings.gl
    if _configured is not None:
        if _configured.mode != mode:
            raise RuntimeError(f"GL already configured as {_configured.mode}")
        return _configured
    if QCoreApplication.instance() is not None:
        raise RuntimeError("glsetup.configure() must run before the QApplication is created")
    libgl = None
    if mode == "software":
        mesa = find_mesa(settings)
        if mesa is None:
            raise RuntimeError(
                "software GL needs a current Mesa build (opengl32.dll + libgallium_wgl.dll) in "
                "%LOCALAPPDATA%\\PCLabel\\mesa-<version> or in settings.mesa_dir"
            )
        os.environ["PATH"] = str(mesa) + os.pathsep + os.environ.get("PATH", "")
        os.environ["QT_OPENGL_DLL"] = str(mesa / "opengl32.dll")
        os.environ["GALLIUM_DRIVER"] = "llvmpipe"
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL)
        libgl = str(mesa / "opengl32.dll")
    elif mode != "hardware":
        raise ValueError(mode)
    QSurfaceFormat.setDefaultFormat(surface_format(vsync))
    _configured = GLConfig(mode, libgl)
    return _configured


def current() -> GLConfig:
    if _configured is None:
        raise RuntimeError("glsetup.configure() has not run")
    return _configured


def attach_moderngl() -> moderngl.Context:
    """moderngl context on the GL context that is current (made by Qt). Checks the renderer matches the mode."""
    cfg = current()
    ctx = moderngl.create_context(require=REQUIRE, libgl=cfg.libgl) if cfg.libgl else moderngl.create_context(
        require=REQUIRE
    )
    renderer = ctx.info["GL_RENDERER"]
    if cfg.mode == "software" and "llvmpipe" not in renderer:
        raise RuntimeError(f"software GL requested but the renderer is {renderer!r}")
    return ctx


def offscreen_context():
    """(surface, Qt context, moderngl context) without a window; needs a Q(Gui)Application. For tests and tools."""
    surf = QOffscreenSurface()
    surf.setFormat(QSurfaceFormat.defaultFormat())
    surf.create()
    qctx = QOpenGLContext()
    qctx.setFormat(QSurfaceFormat.defaultFormat())
    if not qctx.create() or not qctx.makeCurrent(surf):
        raise RuntimeError("could not create an offscreen OpenGL context")
    return surf, qctx, attach_moderngl()


def nvx_memory_kb() -> dict | None:
    """VRAM figures in KiB from GL_NVX_gpu_memory_info (needs a current NVIDIA context), else None."""
    try:
        get = ctypes.WinDLL("opengl32").glGetIntegerv
        get.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_int)]
        out = {}
        for key, enum in (("total_available", 0x9048), ("current_available", 0x9049), ("evicted", 0x904B)):
            v = ctypes.c_int(-1)
            get(enum, ctypes.byref(v))
            out[key] = v.value
        return out if out["total_available"] > 0 else None
    except (OSError, AttributeError):
        return None
