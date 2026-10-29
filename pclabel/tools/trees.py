"""Trees identified by a band of their trunk (R-015, proposed; the user's design of 2026-10-26).

The labeller clicks the ground at the foot of the trunk: its height along the height axis is the tree's ground
height. A cylinder 1.00-1.10 m above it (``TreeParams``) is moved onto the trunk and sized; the points inside are the
tree's identifying band. ``fit_trunk`` fits a circle to the band across the height axis: the circle's centre, put
at the ground height, is the tree's position; its radius gives the girth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from pclabel.tools.volume import frame_axes


@dataclass(frozen=True)
class TreeParams:
    band_bottom: float = 1.00  # m above the clicked ground: the identifying band of the trunk
    band_top: float = 1.10
    radius: float = 0.50  # m, the cylinder's radius when it is placed (then sized to the trunk)


def _circle(x, y):
    """Algebraic (Kasa) circle fit: centre x, centre y, radius."""
    A = np.c_[2 * x, 2 * y, np.ones(x.size)]
    (a, b, c), *_ = np.linalg.lstsq(A, x * x + y * y, rcond=None)
    return a, b, float(np.sqrt(max(c + a * a + b * b, 0.0)))


def fit_trunk(xyz, idx, ground, axis, along=(1.0, 0.0, 0.0), trim: float = 0.03) -> dict:
    """Circle fit across the height axis to the points ``idx`` (local ``xyz``), about the ground point ``ground``
    (local). Points more than ``trim`` m off the first circle (e.g. a shrub's) are dropped and the circle refitted.

    Returns the centre at the ground height (local), radius, girth, rms residual, the points used and the share of
    the circumference they cover (10° sectors)."""
    idx = np.asarray(idx, np.int64)
    if idx.size < 5:
        raise ValueError("too few points to fit the trunk (at least 5)")
    n, u, v = frame_axes(axis, along)
    g = np.asarray(ground, np.float64)
    d = np.asarray(xyz[idx], np.float64) - g
    x, y, h = d @ u, d @ v, d @ n
    keep = np.ones(idx.size, bool)
    for _ in range(3):
        a, b, r = _circle(x[keep], y[keep])
        res = np.hypot(x - a, y - b) - r
        new = np.abs(res) <= trim
        if new.sum() < 5 or np.array_equal(new, keep):
            break
        keep = new
    a, b, r = _circle(x[keep], y[keep])
    res = (np.hypot(x - a, y - b) - r)[keep]
    ang = np.arctan2(y[keep] - b, x[keep] - a)
    cover = np.unique(np.floor((ang + np.pi) / np.radians(10)).astype(int) % 36).size / 36
    return {"centre": g + a * u + b * v, "radius": r, "girth": 2 * np.pi * r, "rms": float(np.sqrt(np.mean(res**2))),
            "points": int(keep.sum()), "dropped": int((~keep).sum()), "coverage": float(cover),
            "band": (float(h.min()), float(h.max()))}  # fmt: skip


def next_tree_name(names) -> str:
    """"tree 01", "tree 02", ...: one more than the highest number among ``names``."""
    nums = [int(m.group(1)) for s in names if (m := re.fullmatch(r"tree (\d+)", s))]
    return f"tree {max(nums, default=0) + 1:02d}"
