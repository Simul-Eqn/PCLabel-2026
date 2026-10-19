"""Camera (numpy, row-major, OpenGL clip conventions: clip = P @ V @ p). z is up.

One camera serves every view. It orbits ``target`` at ``distance`` with ``yaw`` (about z) and ``pitch``
(elevation; > 0 looks down). The preset views (top, bottom, front, side) are camera angles of the same perspective
camera, so it is always clear what kind of view is on screen; orthographic projection is a separate switch. The
right vector is always horizontal, so pitch = +-90 (top, bottom) is not degenerate and keeps its heading.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

PERSPECTIVE, ORTHO = "perspective", "ortho"
# preset views: (yaw or None to keep the heading, pitch)
VIEW_ANGLES = {"top": (None, 90.0), "bottom": (None, -90.0), "front": (270.0, 0.0), "side": (0.0, 0.0),
               "perspective": (None, 35.0)}  # fmt: skip
VIEWS = tuple(VIEW_ANGLES)


def perspective(fovy_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / np.tan(np.deg2rad(fovy_deg) / 2)
    m = np.zeros((4, 4))
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2 * far * near / (near - far)
    m[3, 2] = -1.0
    return m


def orthographic(left, right, bottom, top, near, far) -> np.ndarray:
    m = np.eye(4)
    m[0, 0] = 2 / (right - left)
    m[1, 1] = 2 / (top - bottom)
    m[2, 2] = -2 / (far - near)
    m[0, 3] = -(right + left) / (right - left)
    m[1, 3] = -(top + bottom) / (top - bottom)
    m[2, 3] = -(far + near) / (far - near)
    return m


def look_at(eye, target, up=(0.0, 0.0, 1.0)) -> np.ndarray:
    eye, target, up = (np.asarray(v, dtype=np.float64) for v in (eye, target, up))
    f = target - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    if np.linalg.norm(s) < 1e-9:  # looking straight down or up
        s = np.cross(f, (0.0, 1.0, 0.0))
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


@dataclass
class OrbitCamera:
    target: np.ndarray = field(default_factory=lambda: np.zeros(3))
    distance: float = 50.0
    yaw: float = -60.0  # degrees
    pitch: float = 35.0
    fov: float = 50.0
    near: float = 0.05
    far: float = 2000.0
    projection: str = PERSPECTIVE

    # ---- frames ----

    def direction(self) -> np.ndarray:
        """Unit vector from the target towards the eye."""
        y, p = np.deg2rad(self.yaw), np.deg2rad(self.pitch)
        return np.array([np.cos(p) * np.cos(y), np.cos(p) * np.sin(y), np.sin(p)])

    def eye(self) -> np.ndarray:
        return self.target + self.distance * self.direction()

    def axes(self):
        """(right, up, forward) unit vectors in world coordinates."""
        f = -self.direction()
        y = np.deg2rad(self.yaw)
        r = np.array([-np.sin(y), np.cos(y), 0.0])
        u = np.cross(r, f)
        return r, u, f

    def view(self) -> np.ndarray:
        r, u, f = self.axes()
        m = np.eye(4)
        m[0, :3], m[1, :3], m[2, :3] = r, u, -f
        m[:3, 3] = -m[:3, :3] @ self.eye()
        return m

    def ortho_height(self) -> float:
        """World height of the view in orthographic mode (matches the perspective view at the target)."""
        return 2.0 * self.distance * np.tan(np.deg2rad(self.fov) / 2)

    def proj(self, aspect: float) -> np.ndarray:
        if self.projection == ORTHO:
            h = self.ortho_height() / 2
            return orthographic(-h * aspect, h * aspect, -h, h, self.near, self.far)
        return perspective(self.fov, aspect, self.near, self.far)

    def mvp(self, aspect: float) -> np.ndarray:
        return self.proj(aspect) @ self.view()

    def pixels_per_unit(self, viewport_h: float) -> float:
        """Perspective: pixels per world unit at eye depth 1. Ortho: pixels per world unit."""
        if self.projection == ORTHO:
            return viewport_h / self.ortho_height()
        return viewport_h / (2 * np.tan(np.deg2rad(self.fov) / 2))

    # ---- navigation ----

    def orbit(self, d_yaw: float, d_pitch: float):
        """Rotate the eye around the target (heading about z, elevation about the camera's right axis)."""
        self.yaw = (self.yaw + d_yaw) % 360.0
        self.pitch = float(np.clip(self.pitch + d_pitch, -90.0, 90.0))

    def zoom(self, factor: float, towards=None):
        """Scale the distance; with ``towards`` (a world point) the target moves so that point stays put."""
        factor = float(np.clip(factor, 1e-3, 1e3))
        new = float(np.clip(self.distance * factor, 0.05, 1e5))
        factor = new / self.distance
        if towards is not None:
            self.target = self.target + (np.asarray(towards, float) - self.target) * (1 - factor)
        self.distance = new

    def pan(self, dx: float, dy: float):
        """Move the target in the view plane by (dx, dy) in units of the view height."""
        r, u, _ = self.axes()
        h = self.ortho_height()
        self.target = self.target + h * (-dx * r + dy * u)

    def pivot_on_axis(self, p) -> bool:
        """Orbit around the point of the view axis at ``p``'s depth from now on. Eye and orientation stay
        exactly as they are, so the picture does not move (no jump when a drag starts). Perspective only: in
        orthographic views the distance sets the zoom, so the target stays. Returns whether the pivot moved."""
        if self.projection == ORTHO:
            return False
        eye = self.eye()
        f = self.axes()[2]
        d = float(np.dot(np.asarray(p, float) - eye, f))
        if d <= 1e-3:
            return False
        self.target = eye + f * d
        self.distance = d
        return True

    def reset(self, lo, hi):
        """Home: perspective, the default heading and elevation, framing the box (lo, hi)."""
        self.projection = PERSPECTIVE
        self.yaw, self.pitch = -60.0, 35.0
        self.fit(lo, hi)

    def set_pivot(self, p):
        """Orbit around world point ``p`` from now on, without moving the eye (re-aims the view at ``p``)."""
        eye = self.eye()
        d = eye - np.asarray(p, float)
        dist = float(np.linalg.norm(d))
        if dist < 1e-6:
            return
        self.target = np.asarray(p, float)
        self.distance = dist
        self.yaw = float(np.rad2deg(np.arctan2(d[1], d[0])))
        self.pitch = float(np.rad2deg(np.arcsin(np.clip(d[2] / dist, -1, 1))))

    def fly_look(self, d_yaw: float, d_pitch: float):
        """Turn the view around the eye (fly mode)."""
        eye = self.eye()
        self.yaw = (self.yaw + d_yaw) % 360.0
        self.pitch = float(np.clip(self.pitch + d_pitch, -89.0, 89.0))
        self.target = eye - self.distance * self.direction()

    def fly_move(self, right: float, up: float, forward: float):
        """Move eye and target together, in camera axes (forward is horizontal, up is world z)."""
        r, _, f = self.axes()
        fh = np.array([f[0], f[1], 0.0])
        n = np.linalg.norm(fh)
        fh = fh / n if n > 1e-9 else np.array([np.cos(np.deg2rad(self.yaw + 180)), np.sin(np.deg2rad(self.yaw + 180)), 0.0])
        self.target = self.target + right * r + forward * fh + up * np.array([0.0, 0.0, 1.0])

    def view_angles(self, name: str) -> tuple[float, float]:
        """(yaw, pitch) of a preset view: 'top' (looking down), 'bottom' (looking up), 'front' (looking +y),
        'side' (looking -x), 'perspective' (from 35 degrees above, heading kept)."""
        yaw, pitch = VIEW_ANGLES[name]
        return (self.yaw if yaw is None else yaw), pitch

    def set_view(self, name: str):
        """Turn to a preset view at once (the projection does not change)."""
        self.yaw, self.pitch = self.view_angles(name)

    def view_name(self) -> str:
        """Which preset the current angles match, else 'free'."""
        for name in ("top", "bottom"):
            if abs(self.pitch - VIEW_ANGLES[name][1]) < 0.05:
                return name
        for name in ("front", "side"):
            yaw, pitch = VIEW_ANGLES[name]
            if abs(self.pitch - pitch) < 0.05 and abs((self.yaw - yaw + 180) % 360 - 180) < 0.05:
                return name
        return "free"

    def fit(self, lo, hi):
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        self.target = (lo + hi) / 2
        r = 0.5 * np.linalg.norm(hi - lo)
        self.distance = r / np.sin(np.deg2rad(self.fov) / 2) * 0.9

    def update_clip(self, lo, hi):
        """Near/far from the scene box so depth precision suits the current distance."""
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        c = (lo + hi) / 2
        radius = 0.5 * np.linalg.norm(hi - lo)
        reach = np.linalg.norm(self.eye() - c) + radius
        if self.projection == ORTHO:
            # the eye may sit inside the scene in ortho views; pull the near plane far behind it
            self.near = -reach
            self.far = reach
        else:
            self.near = float(np.clip(self.distance * 0.01, 0.005, 1.0))
            self.far = max(reach * 1.05, self.near * 10)

    # ---- screen <-> world ----

    def project(self, p, viewport) -> np.ndarray:
        """World points (n, 3) -> (n, 3) pixel x, pixel y (top-left origin), window depth [0, 1]."""
        w, h = viewport
        p = np.atleast_2d(np.asarray(p, float))
        c = np.c_[p, np.ones(len(p))] @ self.mvp(w / h).T
        ndc = c[:, :3] / c[:, 3:4]
        return np.stack([(ndc[:, 0] + 1) * 0.5 * w, (1 - ndc[:, 1]) * 0.5 * h, (ndc[:, 2] + 1) * 0.5], 1)

    def unproject(self, px, py, depth, viewport) -> np.ndarray:
        """Pixel (top-left origin, centre of pixel at +0.5) and window depth -> world point."""
        w, h = viewport
        ndc = np.array([2 * px / w - 1, 1 - 2 * py / h, 2 * depth - 1, 1.0])
        q = np.linalg.solve(self.mvp(w / h), ndc)
        return q[:3] / q[3]


def frustum_planes(mvp: np.ndarray) -> np.ndarray:
    """Six planes (a, b, c, d) with inside = a x + b y + c z + d >= 0 (Gribb-Hartmann)."""
    m = mvp
    planes = np.array([m[3] + m[0], m[3] - m[0], m[3] + m[1], m[3] - m[1], m[3] + m[2], m[3] - m[2]])
    return planes / np.linalg.norm(planes[:, :3], axis=1, keepdims=True)


def boxes_visible(planes: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Conservative AABB-vs-frustum test for many boxes (lo, hi: (n, 3)). True = possibly visible."""
    vis = np.ones(lo.shape[0], bool)
    for a, b, c, d in planes:
        px = np.where(a >= 0, hi[:, 0], lo[:, 0])
        py = np.where(b >= 0, hi[:, 1], lo[:, 1])
        pz = np.where(c >= 0, hi[:, 2], lo[:, 2])
        vis &= a * px + b * py + c * pz + d >= 0
    return vis
