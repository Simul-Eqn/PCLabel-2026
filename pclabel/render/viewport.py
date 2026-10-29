"""The 3D viewport: a QOpenGLWidget around PointRenderer, with navigation, hover readout, picking and the selection
shapes.

Mouse, the same in every view:
- left or right drag rotates. In perspective the camera orbits the surface point at the centre of the view (found
  when the button goes down; the picture does not move until the mouse does, and where the drag starts does not
  matter). Orthographic views turn their heading.
- Shift + left drag, or middle drag, pans; the wheel zooms towards the cursor.
- right button held (perspective) and W A S D Q E pressed: fly for the rest of that hold. The mouse looks, W A S D
  move, Q E go down and up, Shift is faster, the wheel sets the speed.
- a click without dragging picks the nearest full-resolution point.
With a selection tool (lasso, rectangle) the left button draws the shape instead: plain replaces the selection,
Shift adds to it, Ctrl subtracts from it. The outline is white when only visible points will be selected and orange
when hidden points are selected too (``select_through``). With a seed tool (ground grow, magic select) a click (no drag) on the
ground sets the seed (Shift: add, Ctrl: subtract); dragging still rotates.
Vector tools (segment, curve) pick points with clicks as without a tool; Enter finishes a curve, Backspace takes back
its last point. In curve edit mode only the curve's nodes and handles react: a click on a node activates it, a drag
on a handle sets the gradient there (the handle follows the mouse on the curve's plane), Ctrl+click on a node removes
it, and Shift+click elsewhere picks a cloud point to add (Del removes the active node). Dragging elsewhere still
moves the camera.
With 3D select (volume) a click places the volume on the surface under the cursor, and W A S D Q E Z X and Enter go to
it (moving, turning, confirming) instead of to the window's shortcuts.
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QPointF, Qt, QTimer, Signal
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from pclabel.render import glsetup
from pclabel.render.camera import ORTHO, PERSPECTIVE, OrbitCamera
from pclabel.render.renderer import FrameStats, PointRenderer, PointSource
from pclabel.tools.pick import nearest_point

SEED_TOOLS = ("grow", "magic", "piece", "tree")  # a click sets a seed (piece: picks a piece of the selection; tree: the ground at a trunk)
VOLUME_KEYS = {Qt.Key.Key_W, Qt.Key.Key_A, Qt.Key.Key_S, Qt.Key.Key_D, Qt.Key.Key_Q, Qt.Key.Key_E, Qt.Key.Key_Z,
               Qt.Key.Key_X, Qt.Key.Key_Minus, Qt.Key.Key_Equal, Qt.Key.Key_Plus, Qt.Key.Key_Underscore,
               Qt.Key.Key_K, Qt.Key.Key_Return, Qt.Key.Key_Enter}  # fmt: skip
FLY_KEYS = {Qt.Key.Key_W, Qt.Key.Key_A, Qt.Key.Key_S, Qt.Key.Key_D, Qt.Key.Key_Q, Qt.Key.Key_E}
_CTRL_ALT = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier


def is_volume_key(e) -> bool:
    """A key 3D select takes in the view: VOLUME_KEYS, plain or with Shift; with Ctrl only Enter (subtract). Ctrl+Z,
    Ctrl+K, Ctrl+D ... stay the window's shortcuts (Ctrl+Z used to arrive as Z: fewer vertical lines, no undo)."""
    if e.key() not in VOLUME_KEYS:
        return False
    return not (e.modifiers() & _CTRL_ALT) or e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)


class Viewport(QOpenGLWidget):
    picked = Signal(object)  # (Pick, local point) or None
    hovered = Signal(object)  # world xyz (np.ndarray) or None
    frame = Signal(object)  # (FrameStats, wall seconds since the previous frame)
    view_changed = Signal(str)
    shape_done = Signal(object, str)  # (polygon (n, 2) in physical pixels, "replace" | "add" | "subtract")
    tool_changed = Signal(object)  # None | "lasso" | "rect" | "grow" | "magic" | "segment" | "curve" | "curve_edit"
    seed_picked = Signal(object, str)  # seed tools: (local point on the surface, "replace" | "add" | "subtract")
    renderer_ready = Signal()  # a renderer exists for the current source (apply display settings now)
    finish_requested = Signal(bool)  # Enter with a vector tool (True: Shift+Enter, finish as a closed loop)
    back_requested = Signal()  # Backspace with a vector tool
    edit_node_clicked = Signal(int)  # curve edit: a node was clicked
    edit_node_removed = Signal(int)  # curve edit: Ctrl+click on a node
    edit_delete_requested = Signal()  # curve edit: Del (remove the active node)
    edit_handle_dragged = Signal(int, int, object)  # curve edit: (node, side +1 out / -1 in, local point on the plane)
    edit_drag_finished = Signal()
    volume_placed = Signal(object)  # 3D select: local point on the surface clicked
    volume_key = Signal(int, object)  # 3D select: (key, modifiers)

    def __init__(self, memory_bytes: int, point_budget: int, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.memory_bytes, self.point_budget = memory_bytes, point_budget
        self.camera = OrbitCamera()
        self.ctx = None
        self.renderer: PointRenderer | None = None
        self.source: PointSource | None = None
        self._pending = None  # (source, class colours) waiting for the GL context
        self.gl_info: dict = {}
        self.continuous = False
        self.last_stats = FrameStats()
        self._t_prev = None
        self._press = None
        self._dragged = False
        self.tool = None  # None | "lasso" | "rect" | "grow" | "magic" | "segment" | "curve" | "curve_edit"
        # curve edit: {"nodes": (n, 3) local, "handles": [(node, side, (3,) local)], "plane": (normal, local point)}
        self.edit_targets = None
        self._edit_drag = None  # (node, side) of the handle being dragged
        self.select_through = False  # lasso / rectangle take hidden points too (the outline is orange)
        self.pick_inside = None  # tools.sets.PointMask: clicks pick only its points (the clip to a point set)
        self._shape = None  # widget-coordinate points of the shape being drawn
        self._shape_op = "replace"
        self._fly_keys: set = set()
        self._flying = False  # the right button is held and a fly key was pressed: the mouse looks
        self.fly_speed = 4.0  # m/s
        self._fly_timer = QTimer(self)
        self._fly_timer.setInterval(16)
        self._fly_timer.timeout.connect(self._fly_step)
        self._fly_t = None
        self._hover_pos = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(40)
        self._hover_timer.timeout.connect(self._do_hover)
        self._anim = None
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._anim_step)
        self._before_preset = None  # (yaw, pitch) before top/bottom, for toggling back
        self._log = None
        self.first_frame_s = None
        self.first_converged_s = None
        self._opened_at = None

    # ---- data -----------------------------------------------------------------------------------------------

    def set_source(self, source: PointSource | None, class_colors=None):
        """Show a new point source (None clears). The renderer is created in the GL context."""
        self._opened_at = time.perf_counter()
        self.first_frame_s = self.first_converged_s = None
        if self.ctx is None:
            self._pending = (source, class_colors)
            return
        self.makeCurrent()
        try:
            if self.renderer is not None:
                self.renderer.close()
                self.renderer = None
            self.source = source
            if source is not None:
                self.renderer = PointRenderer(self.ctx, source, class_colors, memory_bytes=self.memory_bytes,
                                              point_budget=self.point_budget)  # fmt: skip
                self.fit()
        finally:
            self.doneCurrent()
        if self.renderer is not None:
            self.renderer_ready.emit()
        self.update()

    def fit(self):
        """Frame the bulk of the points (1st..99th percentile box), not stray far-off returns."""
        if self.renderer is not None:
            self.camera.fit(*self.robust_bounds())
            self.update()

    def robust_bounds(self, per_tile: int = 4000):
        src = self.source
        t = src.tiles
        idx = np.concatenate([np.arange(int(s), int(s) + min(int(c), per_tile)) for s, c in zip(t["start"], t["count"])])
        sample = np.asarray(src.xyz[idx], np.float64)  # tiles are shuffled: their first points are a uniform sample
        return np.percentile(sample, 1, axis=0), np.percentile(sample, 99, axis=0)

    def reset_view(self):
        """Home: perspective from the default direction, framing the bulk of the points."""
        if self.renderer is not None:
            self._anim_timer.stop()
            self.camera.reset(*self.robust_bounds())
            self._before_preset = None
            self.view_changed.emit(self.view_label())
            self.update()

    @property
    def view_name(self) -> str:
        """'top', 'bottom', 'front', 'side' when the camera is at that preset angle, else 'free'."""
        return self.camera.view_name()

    def view_label(self) -> str:
        name = {"free": "free view"}.get(self.view_name, f"{self.view_name} view")
        return name + (", orthographic" if self.camera.projection == ORTHO else "")

    def set_orthographic(self, on: bool):
        self.camera.projection = ORTHO if on else PERSPECTIVE
        self.view_changed.emit(self.view_label())
        self.update()

    def set_tool(self, name: str | None):
        self.tool = name
        self._shape = None
        self._edit_drag = None
        if self.renderer is not None:
            self.renderer.screen_shape = None
        self.setCursor(Qt.CursorShape.CrossCursor if name else Qt.CursorShape.ArrowCursor)
        self.tool_changed.emit(name)
        self.update()

    def set_view(self, name: str, animate: bool = True):
        """Turn the camera to a preset view around the surface at the centre of the view. Asking for top or
        bottom while already there turns back to where the camera was before."""
        cam = self.camera
        if name in ("top", "bottom") and self.view_name == name and self._before_preset is not None:
            yaw, pitch = self._before_preset
            self._before_preset = None
        else:
            if name in ("top", "bottom") and self.view_name not in ("top", "bottom"):
                self._before_preset = (cam.yaw, cam.pitch)
            yaw, pitch = cam.view_angles(name)
        if self.renderer is not None and self.renderer.last_mvp is not None:
            c = self.world_at(QPointF(self.width() / 2, self.height() / 2), radius=max(8, min(self.width(), self.height()) // 12))
            if c is not None:
                cam.pivot_on_axis(c)
        if not animate:
            cam.yaw, cam.pitch = yaw % 360.0, pitch
            self.view_changed.emit(self.view_label())
            self.update()
            return
        d_yaw = (yaw - cam.yaw + 180.0) % 360.0 - 180.0  # the short way round
        self._anim = (time.perf_counter(), 0.25, cam.yaw, cam.pitch, d_yaw, pitch)
        self._anim_timer.start()

    def _anim_step(self):
        t0, dur, y0, p0, dy, p1 = self._anim
        f = min(1.0, (time.perf_counter() - t0) / dur)
        e = f * f * (3 - 2 * f)
        self.camera.yaw = (y0 + dy * e) % 360.0
        self.camera.pitch = p0 + (p1 - p0) * e
        if f >= 1.0:
            self.camera.yaw, self.camera.pitch = (y0 + dy) % 360.0, p1
            self._anim_timer.stop()
            self.view_changed.emit(self.view_label())
        self.update()

    def set_point_budget(self, n: int):
        self.point_budget = int(n)
        if self.renderer is not None:
            self.renderer.lod = self.renderer.lod.__class__(point_budget=self.point_budget,
                                                            spacing_px=self.renderer.lod.spacing_px)  # fmt: skip
        self.update()

    def start_frame_log(self, path: str | Path):
        new = not Path(path).exists()
        f = open(path, "a", newline="", encoding="utf-8")
        w = csv.writer(f)
        if new:
            w.writerow(["time", "wall_ms", "cpu_ms", "gpu_ms", "drawn", "visible_tiles", "uploaded", "upload_ms",
                        "resident_pages", "inflight", "converged"])  # fmt: skip
        self._log = (f, w)

    def close_frame_log(self):
        if self._log is not None:
            self._log[0].close()
            self._log = None

    # ---- GL -------------------------------------------------------------------------------------------------

    def initializeGL(self):
        self.ctx = glsetup.attach_moderngl()
        self.gl_info = {k: self.ctx.info[k] for k in ("GL_VENDOR", "GL_RENDERER", "GL_VERSION")}
        self.gl_info["mode"] = glsetup.current().mode
        if self._pending is not None:
            source, colors = self._pending
            self._pending = None
            self.source = source
            if source is not None:
                self.renderer = PointRenderer(self.ctx, source, colors, memory_bytes=self.memory_bytes,
                                              point_budget=self.point_budget)  # fmt: skip
                self.camera.fit(*self.robust_bounds())
                self.renderer_ready.emit()

    def physical_size(self) -> tuple[int, int]:
        dpr = self.devicePixelRatioF()
        return max(1, int(round(self.width() * dpr))), max(1, int(round(self.height() * dpr)))

    def paintGL(self):
        now = time.perf_counter()
        wall = now - self._t_prev if self._t_prev is not None else float("nan")
        self._t_prev = now
        fbo = self.ctx.detect_framebuffer(self.defaultFramebufferObject())
        if self.renderer is None:
            fbo.use()
            self.ctx.clear(0.07, 0.07, 0.08, 1.0)
            return
        stats = self.renderer.render(self.camera, self.physical_size(), fbo)
        self.last_stats = stats
        if self._opened_at is not None:
            if self.first_frame_s is None:
                self.first_frame_s = time.perf_counter() - self._opened_at
            if stats.converged and self.first_converged_s is None:
                self.first_converged_s = time.perf_counter() - self._opened_at
        if self._log is not None:
            self._log[1].writerow([f"{now:.4f}", f"{wall * 1e3:.2f}", f"{stats.cpu_ms:.2f}", f"{stats.gpu_ms:.2f}",
                                   stats.drawn, stats.visible_tiles, stats.uploaded, f"{stats.upload_ms:.2f}",
                                   stats.resident_pages, stats.inflight, int(stats.converged)])  # fmt: skip
        self.frame.emit((stats, wall))
        # keep repainting while pages stream in; stop if nothing can arrive (budget full, or a stall that would
        # otherwise spin the CPU forever)
        stalled = not stats.converged and stats.inflight == 0 and stats.uploaded == 0 and stats.ready == 0
        if self.continuous or not (stats.converged or stats.memory_full or stalled):
            self.update()

    # ---- screen <-> world -----------------------------------------------------------------------------------

    def world_at(self, pos: QPointF, radius: int = 4):
        """Local (store) coordinates of the drawn point nearest to a widget position, from the last frame."""
        if self.renderer is None or self.renderer.last_mvp is None:
            return None
        dpr = self.devicePixelRatioF()
        self.makeCurrent()
        try:
            hit = self.renderer.depth_near(pos.x() * dpr, pos.y() * dpr, radius)
        finally:
            self.doneCurrent()
        if hit is None:
            return None
        w, h = self.renderer.last_viewport
        x, y, d = hit
        ndc = np.array([2 * x / w - 1, 1 - 2 * y / h, 2 * d - 1, 1.0])
        q = np.linalg.solve(self.renderer.last_mvp, ndc)
        return q[:3] / q[3]

    def pick(self, pos: QPointF):
        p = self.world_at(pos, radius=6)
        if p is None or self.source is None:
            if self.renderer is not None:
                self.renderer.marker = None
            self.picked.emit(None)
            self.update()
            return
        # search a few pixel footprints around the depth hit: a pixel can cover decimetres when zoomed out
        if self.camera.projection == ORTHO:
            footprint = self.camera.ortho_height() / self.physical_size()[1]
        else:
            depth = float(np.dot(p - self.camera.eye(), self.camera.axes()[2]))
            footprint = max(depth, 0.0) / self.camera.pixels_per_unit(self.physical_size()[1])
        res = nearest_point(self.source.xyz, self.source.tiles, p, search=float(np.clip(3 * footprint, 0.05, 5.0)),
                            inside=self.pick_inside)  # fmt: skip
        if res is None:
            self.picked.emit(None)
            return
        self.renderer.marker = np.asarray(self.source.xyz[res.index], np.float64) + self.source.offset
        self.picked.emit((res, p))
        self.update()

    def _do_hover(self):
        if self._hover_pos is None or self.source is None:
            return
        p = self.world_at(self._hover_pos, radius=3)
        self.hovered.emit(None if p is None else p + self.source.offset)

    # ---- input ----------------------------------------------------------------------------------------------

    def _ndc(self, pts):
        w, h = max(self.width(), 1), max(self.height(), 1)
        return [(2 * p.x() / w - 1, 1 - 2 * p.y() / h) for p in pts]

    def _shape_points(self, cur):
        if self.tool == "rect":
            a = self._shape[0]
            return [a, QPointF(cur.x(), a.y()), cur, QPointF(a.x(), cur.y())]
        return self._shape + [cur]

    def screen_of(self, pts_local) -> np.ndarray:
        """Widget coordinates (n, 2) of local points in the last frame; NaN behind the camera."""
        p = np.asarray(pts_local, np.float64).reshape(-1, 3)
        if self.renderer is None or self.renderer.last_mvp is None or not len(p):
            return np.full((len(p), 2), np.nan)
        q = np.c_[p, np.ones(len(p))] @ self.renderer.last_mvp.T
        ok = q[:, 3] > 1e-9
        w = np.where(ok, q[:, 3], 1.0)
        xy = np.c_[(q[:, 0] / w + 1) / 2 * self.width(), (1 - q[:, 1] / w) / 2 * self.height()]
        return np.where(ok[:, None], xy, np.nan)

    def plane_point(self, pos: QPointF, normal, point):
        """Local point where the ray under a widget position meets a plane (normal, local point), or None."""
        if self.renderer is None or self.renderer.last_mvp is None:
            return None
        x = 2 * pos.x() / max(self.width(), 1) - 1
        y = 1 - 2 * pos.y() / max(self.height(), 1)
        inv = np.linalg.inv(self.renderer.last_mvp)
        a, b = inv @ np.array([x, y, -1.0, 1.0]), inv @ np.array([x, y, 1.0, 1.0])
        a, b = a[:3] / a[3], b[:3] / b[3]
        n = np.asarray(normal, np.float64)
        den = float(np.dot(n, b - a))
        if abs(den) < 1e-12:
            return None
        return a + float(np.dot(n, np.asarray(point, np.float64) - a)) / den * (b - a)

    def _edit_hit(self, pos: QPointF, radius: float = 10.0):
        """("handle", node, side) or ("node", node) under a widget position in curve edit mode, else None."""
        t = self.edit_targets
        if self.tool != "curve_edit" or t is None:
            return None
        here = np.array([pos.x(), pos.y()])
        if t.get("handles"):
            xy = self.screen_of([h[2] for h in t["handles"]])
            d = np.hypot(*(xy - here).T)
            if np.isfinite(d).any() and np.nanmin(d) <= radius:
                i, side, _ = t["handles"][int(np.nanargmin(d))]
                return ("handle", i, side)
        if t.get("nodes") is not None and len(t["nodes"]):
            d = np.hypot(*(self.screen_of(t["nodes"]) - here).T)
            if np.isfinite(d).any() and np.nanmin(d) <= radius:
                return ("node", int(np.nanargmin(d)))
        return None

    def mousePressEvent(self, e):
        self._press = (e.position(), e.buttons(), e.modifiers())
        self._dragged = False
        if e.button() == Qt.MouseButton.LeftButton:
            hit = self._edit_hit(e.position())
            if hit is not None and hit[0] == "handle":
                self._edit_drag = hit[1:]
                return
        mods = e.modifiers()
        self._shape_op = ("add" if mods & Qt.KeyboardModifier.ShiftModifier
                          else "subtract" if mods & Qt.KeyboardModifier.ControlModifier else "replace")  # fmt: skip
        if e.button() == Qt.MouseButton.LeftButton and self.tool in ("lasso", "rect"):
            self._shape = [e.position()]
            if self.renderer is not None:
                self.renderer.screen_shape_color = (1.0, 0.6, 0.1, 1.0) if self.select_through else (1.0, 1.0, 1.0, 1.0)
            return
        grow_click = self.tool in SEED_TOOLS and mods & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier)
        left_orbit = e.button() == Qt.MouseButton.LeftButton and not (mods & Qt.KeyboardModifier.ShiftModifier) \
            and not grow_click  # fmt: skip
        if left_orbit or e.button() == Qt.MouseButton.RightButton:
            # orbit the surface point at the centre of the view; the camera itself does not move
            c = QPointF(self.width() / 2, self.height() / 2)
            p = self.world_at(c, radius=max(8, min(self.width(), self.height()) // 12))
            if p is not None:
                self.camera.pivot_on_axis(p)
            if self.renderer is not None:
                self.renderer.pivot = self.camera.target + self.source.offset
        if e.button() == Qt.MouseButton.RightButton and self.camera.projection != ORTHO:
            self._flying = False  # until a fly key goes down, a right drag rotates
            self._fly_t = time.perf_counter()
            self._fly_timer.start()

    def mouseMoveEvent(self, e):
        if self._press is None:
            self._hover_pos = e.position()
            self._hover_timer.start()
            return
        p0, buttons, mods = self._press
        d = e.position() - p0
        if abs(d.x()) + abs(d.y()) > 3:
            self._dragged = True
        if self._edit_drag is not None:
            t = self.edit_targets
            q = None if t is None else self.plane_point(e.position(), *t["plane"])
            if q is not None:
                self.edit_handle_dragged.emit(self._edit_drag[0], self._edit_drag[1], q)
            self.update()
            return
        if self._shape is not None:
            if self.tool == "lasso" and (e.position() - self._shape[-1]).manhattanLength() >= 3:
                self._shape.append(e.position())
            if self.renderer is not None:
                self.renderer.screen_shape = self._ndc(self._shape_points(e.position()))
            self.update()
            return
        self._press = (e.position(), buttons, mods)
        h = max(self.height(), 1)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        ortho = self.camera.projection == ORTHO
        right = bool(buttons & Qt.MouseButton.RightButton)
        if right and self._flying and not ortho:
            self.camera.fly_look(-0.2 * d.x(), -0.2 * d.y())
        elif buttons & Qt.MouseButton.MiddleButton or (buttons & Qt.MouseButton.LeftButton and shift):
            if not (self.tool in SEED_TOOLS and shift):  # Shift+click with a seed tool adds a seed: no panning
                self.camera.pan(d.x() / h, d.y() / h)
        elif right or (buttons & Qt.MouseButton.LeftButton
                       and not (self.tool in SEED_TOOLS and mods & Qt.KeyboardModifier.ControlModifier)):  # fmt: skip
            self.camera.orbit(-0.3 * d.x(), 0.3 * d.y())
        self.update()

    def mouseReleaseEvent(self, e):
        if self._edit_drag is not None:
            self._edit_drag = None
            self._press = None
            self.edit_drag_finished.emit()
            self.update()
            return
        if e.button() == Qt.MouseButton.LeftButton and not self._dragged and self.tool == "curve_edit":
            hit = self._edit_hit(e.position())  # only the curve's nodes can be picked here
            mods = e.modifiers()
            if hit is not None and hit[0] == "node":
                if mods & Qt.KeyboardModifier.ControlModifier:
                    self.edit_node_removed.emit(hit[1])
                else:
                    self.edit_node_clicked.emit(hit[1])
            elif mods & Qt.KeyboardModifier.ShiftModifier:
                self.pick(e.position())  # a cloud point to add to the curve
        elif self._shape is not None and e.button() == Qt.MouseButton.LeftButton:
            pts = self._shape_points(e.position())
            self._shape = None
            if self.renderer is not None:
                self.renderer.screen_shape = None
            dpr = self.devicePixelRatioF()
            poly = np.array([(p.x() * dpr, p.y() * dpr) for p in pts], np.float64)
            if len(poly) >= 3 and np.ptp(poly[:, 0]) > 2 and np.ptp(poly[:, 1]) > 2:
                self.shape_done.emit(poly, self._shape_op)
        elif e.button() == Qt.MouseButton.LeftButton and not self._dragged and self.tool == "volume":
            p = self.world_at(e.position(), radius=6)
            if p is not None:
                self.volume_placed.emit(p)
        elif e.button() == Qt.MouseButton.LeftButton and not self._dragged and self.tool in SEED_TOOLS:
            p = self.world_at(e.position(), radius=6)
            if p is not None:
                self.seed_picked.emit(p, self._shape_op)
        elif e.button() == Qt.MouseButton.LeftButton and not self._dragged:
            self.pick(e.position())
        if e.button() == Qt.MouseButton.RightButton:
            self._fly_timer.stop()
            self._fly_keys.clear()
            self._flying = False
        if self.renderer is not None:
            self.renderer.pivot = None
        self._press = None
        self.update()

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120
        if self._press is not None and self._press[1] & Qt.MouseButton.RightButton:
            self.fly_speed = float(np.clip(self.fly_speed * 1.25**steps, 0.2, 200))
            return
        p = self.world_at(e.position())
        self.camera.zoom(0.85**steps, towards=p)
        self.update()

    def event(self, e):
        # while flying, W A S D Q E move the camera instead of triggering the window's shortcuts
        if e.type() == QEvent.Type.ShortcutOverride and (
                (self._fly_timer.isActive() and e.key() in FLY_KEYS) or (self.tool == "volume" and is_volume_key(e))):  # fmt: skip
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if self._fly_timer.isActive() and e.key() in FLY_KEYS and not e.isAutoRepeat():
            self._fly_keys.add(e.key())
            if not self._flying:  # from now until the right button goes up, the mouse looks instead of rotating
                self._flying = True
                if self.renderer is not None:
                    self.renderer.pivot = None
            return
        if self.tool == "volume" and is_volume_key(e):
            self.volume_key.emit(int(e.key()), e.modifiers())
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.tool in ("segment", "curve", "curve_edit"):
            self.finish_requested.emit(bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            return
        if e.key() == Qt.Key.Key_Backspace and self.tool in ("segment", "curve"):
            self.back_requested.emit()
            return
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.tool == "curve_edit":
            self.edit_delete_requested.emit()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() in self._fly_keys and not e.isAutoRepeat():
            self._fly_keys.discard(e.key())
            return
        super().keyReleaseEvent(e)

    def _fly_step(self):
        now = time.perf_counter()
        dt = now - (self._fly_t or now)
        self._fly_t = now
        if not self._fly_keys:
            return
        from PySide6.QtWidgets import QApplication

        v = self.fly_speed * dt * (4.0 if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0)
        k = self._fly_keys
        fwd = (Qt.Key.Key_W in k) - (Qt.Key.Key_S in k)
        right = (Qt.Key.Key_D in k) - (Qt.Key.Key_A in k)
        up = (Qt.Key.Key_E in k) - (Qt.Key.Key_Q in k)
        self.camera.fly_move(right * v, up * v, fwd * v)
        self.update()
