"""Clearance along a road (the user's request of 2026-11-05, after LiDAR360MLS's road clearance analysis): what enters
the space vehicles need above the road, e.g. branches hanging over the carriageway.

The envelope follows a reference line drawn on the road (a traced curb toe, a lane line, a curve along the road):
- across the height axis, up to ``half_width`` from the line (on ``side``: both, or the left or right of the line's
  direction);
- along the height axis, from ``bottom`` to ``height`` above the line's own height at the nearest point (below
  ``bottom`` are the road, curbs and the feet of things);
- between the line's ends.

Points inside it are hits. Hits closer than ``cluster`` (in the plane across the height axis) are one place; a place
with at least ``min_points`` is reported with its lowest point (the clearance left there: how high above the road
the lowest intruding point is), where it is along and across the line, and its points. Which points are checked is
the caller's choice (the points of vegetation labels, or all of them): the scan only shows what it captured, so an
unflagged stretch may still need looking at.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from pclabel.analysis import curves
from pclabel.store.tiles import tiles_meeting

SIDES = ("both", "left", "right")


@dataclass(frozen=True)
class ClearanceParams:
    half_width: float = 3.5  # m across from the reference line
    side: str = "both"  # "both" | "left" | "right" of the line's direction
    bottom: float = 0.5  # m above the line's height: lower points are the road and what stands on it
    height: float = 5.0  # m above the line's height: the clearance needed (set it for the road and its vehicles)
    cluster: float = 0.5  # m: hits closer than this (across the height axis) are one place
    min_points: int = 20  # a place needs this many points
    spacing: float = 0.1  # m: the line is sampled this finely to find each point's nearest place on it


def check(xyz, tiles, line, axis, p: ClearanceParams = ClearanceParams(), candidates=None, progress=None) -> dict:
    """Points of ``xyz`` (local) inside the envelope along ``line`` (local (m, 3)). ``candidates``: sorted store
    indices to check (e.g. the vegetation point sets), or None for every point near the line (by tile).
    Returns {hits (store indices), places: [{position (local), clearance, points, along, across}], length, params}."""
    if p.side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    n = np.asarray(axis, np.float64)
    n /= np.linalg.norm(n)
    samp, dirs, arc, length = curves.sample_along(line, p.spacing)
    flat = samp - np.outer(samp @ n, n)  # the line seen along the height axis
    tree = cKDTree(flat)
    reach = p.half_width + p.spacing
    lo = samp.min(0) - (reach + p.height)
    hi = samp.max(0) + (reach + p.height)
    if candidates is None:
        t = tiles
        cand = tiles_meeting(t, lo, hi)
        chunks = [np.arange(int(t["start"][k]), int(t["start"][k] + t["count"][k]), dtype=np.int64) for k in cand]
    else:
        c = np.asarray(candidates, np.int64)
        chunks = [c[i : i + 2_000_000] for i in range(0, len(c), 2_000_000)]
    hits, hh, aa, ss = [], [], [], []
    for ci, idx in enumerate(chunks):
        if progress is not None and progress(ci / max(len(chunks), 1)) is False:
            break
        if not len(idx):
            continue
        q = np.asarray(xyz[idx], np.float64)
        box = np.all((q >= lo) & (q <= hi), axis=1)
        idx, q = idx[box], q[box]
        if not len(idx):
            continue
        qf = q - np.outer(q @ n, n)
        dist, j = tree.query(qf, distance_upper_bound=reach)
        ok = np.isfinite(dist)
        idx, q, j = idx[ok], q[ok], j[ok]
        if not len(idx):
            continue
        rel = q - samp[j]
        along = np.einsum("ij,ij->i", rel, dirs[j])
        h = rel @ n
        across = np.einsum("ij,ij->i", rel, np.cross(n, dirs[j]))  # + left of the line
        inside = (np.abs(across) <= p.half_width) & (h >= p.bottom) & (h <= p.height)
        inside &= ~((j == 0) & (along < -1e-9)) & ~((j == len(samp) - 1) & (along > 1e-9))  # not past the ends
        if p.side == "left":
            inside &= across >= 0
        elif p.side == "right":
            inside &= across <= 0
        hits.append(idx[inside])
        hh.append(h[inside])
        aa.append(across[inside])
        ss.append(arc[j[inside]] + along[inside])
    hits = np.concatenate(hits) if hits else np.empty(0, np.int64)
    order = np.argsort(hits)
    hits = hits[order]
    h = np.concatenate(hh)[order] if hh else np.empty(0)
    across = np.concatenate(aa)[order] if aa else np.empty(0)
    s = np.concatenate(ss)[order] if ss else np.empty(0)
    places = []
    if len(hits):
        # places: hits joined through cells of `cluster` (across the height axis)
        cell = np.floor(np.c_[s, across] / p.cluster).astype(np.int64)
        c0 = cell.min(0)
        grid = np.zeros(tuple(cell.max(0) - c0 + 1), bool)
        grid[tuple((cell - c0).T)] = True
        lab, _ = ndimage.label(grid, structure=np.ones((3, 3)))
        pid = lab[tuple((cell - c0).T)]
        for k in np.unique(pid):
            m = pid == k
            if m.sum() < p.min_points:
                continue
            i = int(np.argmin(np.where(m, h, np.inf)))
            places.append({"position": np.asarray(xyz[hits[i]], np.float64), "clearance": float(h[i]),
                           "points": int(m.sum()), "along": float(s[i]), "across": float(across[i]),
                           "indices": hits[m]})  # fmt: skip
        places.sort(key=lambda d: d["along"])
    return {"hits": hits, "places": places, "length": float(length), "params": asdict(p)}
