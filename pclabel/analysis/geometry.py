"""Directions about a height axis, the disk kernel of the grid tools, and view-frustum culling (no Qt, no GL)."""

from __future__ import annotations

import numpy as np


def frame_axes(axis, along, turn_deg: float = 0.0):
    """(n, u, v): the unit height axis and two unit directions across it (u: ``along`` turned by ``turn_deg``; v is
    left of u, seen from above). When ``along`` is (nearly) the axis itself, some direction across it is used."""
    n = np.asarray(axis, np.float64)
    n = n / np.linalg.norm(n)
    a = np.asarray(along, np.float64)
    a = a - np.dot(a, n) * n
    if np.linalg.norm(a) < 1e-9:
        a = np.cross(n, [0.0, 1.0, 0.0] if abs(n[1]) < 0.9 else [1.0, 0.0, 0.0])
    a /= np.linalg.norm(a)
    b = np.cross(n, a)
    t = np.radians(turn_deg)
    u = np.cos(t) * a + np.sin(t) * b
    return n, u, np.cross(n, u)


def direction_axes(axis, direction):
    """(n, d, left) as ``frame_axes(axis, direction)``, but a direction along the height axis is an error: two clicked
    points on top of each other give no direction to trace in."""
    n = np.asarray(axis, np.float64)
    d = np.asarray(direction, np.float64)
    d = d - np.dot(d, n / np.linalg.norm(n)) * n / np.linalg.norm(n)
    if np.linalg.norm(d) < 1e-9:
        raise ValueError("the two points are on top of each other (seen along the height axis)")
    return frame_axes(axis, d)


def disk(r: float) -> np.ndarray:
    """A (2k+1, 2k+1) boolean kernel, k = ceil(r): the cells within ``r`` cells of the centre (centre included)."""
    k = int(np.ceil(r))
    yy, xx = np.mgrid[-k : k + 1, -k : k + 1]
    return (xx**2 + yy**2) <= r * r + 1e-9


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
