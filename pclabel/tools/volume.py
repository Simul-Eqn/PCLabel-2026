"""3D select (SPEC §4): every point inside a box or a cylinder standing along the height axis, at full resolution.

The volume stands on a point (``base``, local coordinates). Its axis is the height axis (``axis``: z, or a fitted
plane's normal) and it reaches from ``bottom`` to ``top`` metres along it, measured from the base. Across the axis
it is
- a box, ``length`` along ``u`` by ``width`` along ``v``, where ``u`` is the frame's ``along`` direction turned by
  ``turn`` degrees about the axis and ``v`` is perpendicular to both;
- or a cylinder of ``radius``.
Only tiles whose bounds meet the volume's bounding box are scanned.

Extrude select (``select_loop``) sweeps a closed curve along its plane's normal instead: the loop is rasterised in
its plane (1 cm cells, larger for big loops), pulled in or pushed out evenly with an exact distance transform, and
each point is tested by looking up its cell.
"""

from __future__ import annotations

from dataclasses import dataclass

import numba as nb
import numpy as np


@dataclass(frozen=True)
class VolumeParams:
    shape: str = "box"  # "box" | "cylinder"
    length: float = 2.0  # m, box, along u
    width: float = 2.0  # m, box, along v
    radius: float = 0.5  # m, cylinder
    bottom: float = -0.2  # m along the height axis from the base (below the clicked surface)
    top: float = 2.0  # m
    turn: float = 0.0  # degrees about the axis (box), or about its centre (loop)
    scale: float = 1.0  # loop: about its centre
    inset: float = 0.0  # m, loop: pulled in evenly by this much (negative: pushed out)


def frame_axes(axis, along, turn_deg: float = 0.0):
    """(n, u, v): the unit height axis and two unit directions across it (u: ``along`` turned by ``turn_deg``)."""
    n = np.asarray(axis, np.float64)
    n = n / np.linalg.norm(n)
    a = np.asarray(along, np.float64)
    a = a - np.dot(a, n) * n
    if np.linalg.norm(a) < 1e-9:  # along is (nearly) the axis itself
        a = np.cross(n, [0.0, 1.0, 0.0] if abs(n[1]) < 0.9 else [1.0, 0.0, 0.0])
    a /= np.linalg.norm(a)
    b = np.cross(n, a)
    t = np.radians(turn_deg)
    u = np.cos(t) * a + np.sin(t) * b
    return n, u, np.cross(n, u)


def _half_extents(p: VolumeParams):
    return (0.5 * p.length, 0.5 * p.width) if p.shape == "box" else (p.radius, p.radius)


def corners(base, n, u, v, p: VolumeParams) -> np.ndarray:
    """(8, 3) corners of the box, or of the box around the cylinder."""
    hl, hw = _half_extents(p)
    base = np.asarray(base, np.float64)
    return np.array([base + h * n + x * u + y * v for h in (p.bottom, p.top) for x in (-hl, hl) for y in (-hw, hw)])


def evenly_along(ring2d, count: int) -> np.ndarray:
    """``count`` points (m, 2) spaced evenly by length along a closed ring (its first point first)."""
    r = np.asarray(ring2d, np.float64)
    if not np.allclose(r[0], r[-1]):
        r = np.vstack([r, r[:1]])
    seg = np.linalg.norm(np.diff(r, axis=0), axis=1)
    at = np.r_[0.0, np.cumsum(seg)]
    if at[-1] <= 0 or count <= 0:
        return r[:1].copy()
    want = np.arange(count) * at[-1] / count
    return np.c_[np.interp(want, at, r[:, 0]), np.interp(want, at, r[:, 1])]


def outline_ring(ring2d, origin, n, u, v, bottom: float, top: float, verticals: int = 24) -> list:
    """Polylines (local) that draw an extruded ring: bottom and top outlines and ``verticals`` upright lines spread
    evenly along it."""
    origin = np.asarray(origin, np.float64)
    r = np.asarray(ring2d, np.float64)
    flat = origin + r[:, :1] * u + r[:, 1:] * v
    lines = [flat + bottom * n, flat + top * n]
    for x, y in evenly_along(r, verticals):
        q = origin + x * u + y * v
        lines.append(np.array([q + bottom * n, q + top * n]))
    return lines


def ring_of(p: VolumeParams, segments: int = 96) -> np.ndarray:
    """The cross-section of a box or cylinder (closed ring, (m, 2), in u, v)."""
    if p.shape == "box":
        hl, hw = _half_extents(p)
        return np.array([(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw), (-hl, -hw)])
    t = np.linspace(0, 2 * np.pi, segments + 1)
    return p.radius * np.c_[np.cos(t), np.sin(t)]


def outline(base, n, u, v, p: VolumeParams, segments: int = 96, verticals: int = 24) -> list:
    """Polylines (local) that draw a box or cylinder: bottom and top outlines and ``verticals`` upright lines spread
    evenly around it (a box also gets its four corners)."""
    lines = outline_ring(ring_of(p, segments), base, n, u, v, p.bottom, p.top, verticals)
    if p.shape == "box":
        lines += outline_ring(ring_of(p), base, n, u, v, p.bottom, p.top, 4)[2:]
    return lines


@dataclass(frozen=True, eq=False)
class VolumeSpec:
    """A volume in local coordinates: what 3D select takes, and what a volume clip shows. ``kind`` is "box",
    "cylinder" or "loop"; the volume runs from ``bottom`` to ``top`` along ``n`` from ``origin``; across it, x is
    along ``u`` and y along ``v``. A box has half-sizes ``half``, a cylinder radius ``half[0]``; a loop is a mask
    (rows along y) on a grid (x0, y0, cell)."""

    kind: str
    origin: np.ndarray
    n: np.ndarray
    u: np.ndarray
    v: np.ndarray
    bottom: float
    top: float
    half: tuple = (0.0, 0.0)
    mask: np.ndarray | None = None
    grid: tuple = (0.0, 0.0, 1.0)
    label: str = ""  # for the panels, e.g. "box 6.00 x 3.00 m"
    ring: np.ndarray | None = None  # (m, 2) cross-section outline in u, v, for drawing

    def moved(self, origin) -> "VolumeSpec":
        """The same volume at another origin (a loop keeps its mask)."""
        from dataclasses import replace as _replace

        return _replace(self, origin=np.asarray(origin, np.float64))

    def outline(self, verticals: int = 24) -> list:
        return outline_ring(self.ring, self.origin, self.n, self.u, self.v, self.bottom, self.top, verticals)

    def contains(self, q) -> np.ndarray:
        """Which of the points q (m, 3), local, are inside."""
        q = np.ascontiguousarray(q)
        out = np.empty(len(q), np.bool_)
        if not len(q):
            return out
        if self.kind == "loop":
            x0, y0, cell = self.grid
            _in_loop(q, 0, len(q), self.origin, self.n, self.u, self.v, float(self.bottom), float(self.top),
                     float(x0), float(y0), float(cell), self.mask, out)  # fmt: skip
        else:
            _inside(q, 0, len(q), self.origin, self.n, self.u, self.v, float(self.bottom), float(self.top),
                    float(self.half[0]), float(self.half[1]), self.kind == "box", out)  # fmt: skip
        return out


def box_spec(base, axis, along, p: VolumeParams) -> VolumeSpec:
    n, u, v = frame_axes(axis, along, p.turn)
    hl, hw = _half_extents(p)
    size = f"{p.length:.2f} x {p.width:.2f} m" if p.shape == "box" else f"radius {p.radius:.2f} m"
    return VolumeSpec(p.shape, np.asarray(base, np.float64), n, u, v, float(p.bottom), float(p.top), (hl, hw),
                      label=f"{p.shape} {size}, {p.bottom:+.2f} to {p.top:+.2f} m", ring=ring_of(p))  # fmt: skip


def loop_spec(origin, normal, u, poly2d, bottom: float, top: float, inset: float = 0.0, name: str = "loop"):
    n = np.asarray(normal, np.float64) / np.linalg.norm(normal)
    u = np.asarray(u, np.float64) - np.dot(u, n) * n
    u /= np.linalg.norm(u)
    x0, y0, cell, mask = loop_mask(poly2d, inset)
    return VolumeSpec("loop", np.asarray(origin, np.float64), n, u, np.cross(n, u), float(bottom), float(top),
                      mask=mask, grid=(x0, y0, cell),
                      label=f"{name}, inset {inset * 100:+.0f} cm, {bottom:+.2f} to {top:+.2f} m",
                      ring=inset_outline(poly2d, inset))  # fmt: skip


@nb.njit(parallel=True, nogil=True, cache=True)
def _inside(xyz, a, b, base, n, u, v, bottom, top, hl, hw, is_box, out):
    r2 = hl * hl
    for k in nb.prange(b - a):
        i = a + k
        dx, dy, dz = xyz[i, 0] - base[0], xyz[i, 1] - base[1], xyz[i, 2] - base[2]
        h = dx * n[0] + dy * n[1] + dz * n[2]
        if h < bottom or h > top:
            out[k] = False
            continue
        x = dx * u[0] + dy * u[1] + dz * u[2]
        y = dx * v[0] + dy * v[1] + dz * v[2]
        out[k] = (abs(x) <= hl and abs(y) <= hw) if is_box else (x * x + y * y <= r2)


def select_volume(xyz, tiles, base, axis, along, params: VolumeParams = VolumeParams(), filters=None) -> np.ndarray:
    """Store indices (sorted, int64) of every point inside the volume. ``filters`` (tools.selection.SelectFilters)
    leave out what is hidden on screen (clip box, height slice)."""
    p = params
    n, u, v = frame_axes(axis, along, p.turn)
    base = np.asarray(base, np.float64)
    c = corners(base, n, u, v, p)
    lo, hi = c.min(0), c.max(0)
    t = tiles
    cand = np.flatnonzero((t["x1"] >= lo[0]) & (t["x0"] <= hi[0]) & (t["y1"] >= lo[1]) & (t["y0"] <= hi[1])
                          & (t["zmax"] >= lo[2]) & (t["zmin"] <= hi[2]))  # fmt: skip
    hl, hw = _half_extents(p)
    arr = np.asarray(xyz)
    parts = []
    for k in cand:
        a, m = int(t["start"][k]), int(t["count"][k])
        out = np.empty(m, np.bool_)
        _inside(arr, a, a + m, base, n, u, v, float(p.bottom), float(p.top), float(hl), float(hw), p.shape == "box",
                out)  # fmt: skip
        idx = np.flatnonzero(out).astype(np.int64) + a
        if filters is not None:
            idx = filters.apply(arr, idx)
        parts.append(idx)
    return np.concatenate(parts) if parts else np.empty(0, np.int64)


# ---- extrude select: a closed curve swept along its plane's normal -------------------------------------------------


def loop_centre(poly2d) -> np.ndarray:
    """The mean of a loop's evenly spaced points (a closed ring's repeated last point counted once)."""
    p = np.asarray(poly2d, np.float64)
    return (p[:-1] if len(p) > 2 and np.allclose(p[0], p[-1]) else p).mean(0)


def loop_transform(poly2d, scale: float = 1.0, turn_deg: float = 0.0, shift2d=(0.0, 0.0)) -> np.ndarray:
    """A loop (m, 2) scaled and turned about its centre (``loop_centre``), then shifted."""
    p = np.asarray(poly2d, np.float64)
    c = loop_centre(p)
    t = np.radians(turn_deg)
    rot = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
    return (p - c) * scale @ rot.T + c + np.asarray(shift2d, np.float64)


def inset_outline(poly2d, inset: float) -> np.ndarray:
    """The loop moved ``inset`` metres inward (negative: outward) along its normals: a close drawing of the inset
    region for smooth loops (the selection itself uses the exact distance, ``loop_mask``)."""
    p = np.asarray(poly2d, np.float64)
    if inset == 0 or len(p) < 3:
        return p.copy()
    closed_ring = np.allclose(p[0], p[-1])
    q = p[:-1] if closed_ring else p
    tangent = np.roll(q, -1, axis=0) - np.roll(q, 1, axis=0)
    normal = np.c_[-tangent[:, 1], tangent[:, 0]]
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-12)
    area = 0.5 * np.sum(q[:, 0] * np.roll(q[:, 1], -1) - np.roll(q[:, 0], -1) * q[:, 1])
    out = q + (inset if area > 0 else -inset) * normal  # left normals point inward on a counter-clockwise loop
    return np.vstack([out, out[:1]]) if closed_ring else out


def loop_mask(poly2d, inset: float = 0.0, max_cells: int = 16_000_000):
    """(x0, y0, cell, mask): the loop's inside on a grid in its plane (rows along y), shrunk by ``inset`` metres
    (grown if negative) with an exact distance transform. Cells are 1 cm or larger for big loops."""
    from scipy import ndimage

    from pclabel.tools.selection import rasterize_polygon

    p = np.asarray(poly2d, np.float64)
    lo, hi = p.min(0), p.max(0)
    cell = max(0.01, float(np.sqrt(np.prod(hi - lo + 0.1) / max_cells)))
    pad = max(0.0, -inset) + 3 * cell
    x0, y0 = lo - pad
    w, h = (int(np.ceil(v)) for v in (hi - lo + 2 * pad) / cell)
    mask = rasterize_polygon((p - (x0, y0)) / cell, w, h).astype(bool)
    if inset > 0:
        mask = ndimage.distance_transform_edt(mask) * cell > inset
    elif inset < 0:
        mask = mask | (ndimage.distance_transform_edt(~mask) * cell <= -inset)
    return float(x0), float(y0), cell, mask


@nb.njit(parallel=True, nogil=True, cache=True)
def _in_loop(xyz, a, b, origin, n, u, v, bottom, top, x0, y0, cell, mask, out):
    hgt, wid = mask.shape
    for k in nb.prange(b - a):
        i = a + k
        dx, dy, dz = xyz[i, 0] - origin[0], xyz[i, 1] - origin[1], xyz[i, 2] - origin[2]
        h = dx * n[0] + dy * n[1] + dz * n[2]
        ok = False
        if bottom <= h <= top:
            ix = int(np.floor((dx * u[0] + dy * u[1] + dz * u[2] - x0) / cell))
            iy = int(np.floor((dx * v[0] + dy * v[1] + dz * v[2] - y0) / cell))
            ok = 0 <= ix < wid and 0 <= iy < hgt and mask[iy, ix]
        out[k] = ok


def select_loop(xyz, tiles, origin, normal, u, poly2d, bottom: float, top: float, inset: float = 0.0,
                filters=None) -> np.ndarray:  # fmt: skip
    """Store indices (sorted, int64) of the points inside a closed loop swept along its plane's ``normal`` from
    ``bottom`` to ``top`` metres. The loop is (m, 2) in the plane through ``origin`` (local), with x along ``u`` and
    y along ``normal`` x ``u``; ``inset`` shrinks it evenly (metres; negative grows it)."""
    n = np.asarray(normal, np.float64) / np.linalg.norm(normal)
    u = np.asarray(u, np.float64) - np.dot(u, n) * n
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    origin = np.asarray(origin, np.float64)
    x0, y0, cell, mask = loop_mask(poly2d, inset)
    if not mask.any():
        return np.empty(0, np.int64)
    xs = np.array([x0, x0 + mask.shape[1] * cell])
    ys = np.array([y0, y0 + mask.shape[0] * cell])
    c = np.array([origin + x * u + y * v + h * n for x in xs for y in ys for h in (bottom, top)])
    lo, hi = c.min(0), c.max(0)
    t = tiles
    cand = np.flatnonzero((t["x1"] >= lo[0]) & (t["x0"] <= hi[0]) & (t["y1"] >= lo[1]) & (t["y0"] <= hi[1])
                          & (t["zmax"] >= lo[2]) & (t["zmin"] <= hi[2]))  # fmt: skip
    arr = np.asarray(xyz)
    parts = []
    for k in cand:
        a, m = int(t["start"][k]), int(t["count"][k])
        out = np.empty(m, np.bool_)
        _in_loop(arr, a, a + m, origin, n, u, v, float(bottom), float(top), x0, y0, cell, mask, out)
        idx = np.flatnonzero(out).astype(np.int64) + a
        if filters is not None:
            idx = filters.apply(arr, idx)
        parts.append(idx)
    return np.concatenate(parts) if parts else np.empty(0, np.int64)
