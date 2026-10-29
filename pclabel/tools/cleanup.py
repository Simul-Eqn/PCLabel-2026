"""Clean up a selection by its footprint (the user's request of 2026-10-20: "denoising" as a tool of its own).

The selection is looked at across the height axis (the active frame: z, or a fitted plane), in columns of ``cell``
(5 cm). Typical use: a 3D select cut low (to keep lamp posts out) leaves the grass blades and slightly raised
pavement of its area unselected; this takes them in, and drops stray specks.

1. **Surface.** Each column with selected points gets their mean height. Columns without take the mean of the
   columns within ``radius`` that have one (the selected surface around them).
2. **Footprint.** Columns with selected points. With ``fill_holes``, also columns with points of which at least
   ``majority`` of the surrounding columns (within ``radius``, those with any points) have selected points.
3. **Added:** unselected points in the footprint from ``below`` metres under to ``above`` metres over the surface.
4. **Removed** (``remove_specks``): selected points in columns of which less than ``1 - majority`` of the
   surrounding columns are selected, and connected pieces of the footprint (touching columns) with fewer than
   ``min_island`` selected points.
Only the points the caller allows are considered (``allowed``: visible through the clips and the slice, not locked);
hidden selected points stay as they are. With ``inside`` (a clip to a point set) the points outside the set are not
read at all: they count neither for the surface nor for the footprint.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numba as nb
import numpy as np

from pclabel.tools import sets
from pclabel.tools.volume import frame_axes


@dataclass(frozen=True)
class CleanParams:
    cell: float = 0.05  # m, columns across the height axis
    radius: float = 0.15  # m, the neighbourhood of a column
    above: float = 0.10  # m: points up to this over the selected surface around them join
    below: float = 0.05  # m: and down to this under it
    fill_holes: bool = True
    majority: float = 0.6  # share of the surrounding columns (with points) that must be selected to fill a hole
    remove_specks: bool = True
    min_island: int = 200  # points: smaller pieces of the footprint are dropped


@nb.njit(parallel=True, nogil=True, cache=True)
def _columns(xyz, a, b, origin, n, u, v, x0, y0, cell, nx, ny, hlo, hhi, col, hgt):
    for k in nb.prange(b - a):
        i = a + k
        dx, dy, dz = xyz[i, 0] - origin[0], xyz[i, 1] - origin[1], xyz[i, 2] - origin[2]
        h = dx * n[0] + dy * n[1] + dz * n[2]
        cx = int(np.floor((dx * u[0] + dy * u[1] + dz * u[2] - x0) / cell))
        cy = int(np.floor((dx * v[0] + dy * v[1] + dz * v[2] - y0) / cell))
        if h < hlo or h > hhi or cx < 0 or cy < 0 or cx >= nx or cy >= ny:
            col[k] = -1
        else:
            col[k] = cy * nx + cx
        hgt[k] = h


def clean_selection(xyz, tiles, selection, axis, along, origin, params: CleanParams = CleanParams(), allowed=None,
                    inside=None):  # fmt: skip
    """(added, removed, info): sorted store indices to add to / take out of the selection. ``axis``, ``along`` and
    ``origin`` (local) give the height frame; ``allowed(store_indices) -> bool mask`` limits the points considered;
    ``inside`` (a tools.sets.PointMask) limits the points read."""
    from scipy import ndimage

    p = params
    sel = np.asarray(selection, np.int64)
    if sel.size == 0:
        return np.empty(0, np.int64), np.empty(0, np.int64), {"added": 0, "removed": 0, "params": asdict(p)}
    n, u, v = frame_axes(axis, along, 0.0)
    origin = np.asarray(origin, np.float64)
    arr = np.asarray(xyz)
    q = np.asarray(arr[sel], np.float64) - origin
    sx, sy, sh = q @ u, q @ v, q @ n
    pad = p.radius + 2 * p.cell
    x0, y0 = sx.min() - pad, sy.min() - pad
    nx = int(np.ceil((sx.max() + pad - x0) / p.cell)) + 1
    ny = int(np.ceil((sy.max() + pad - y0) / p.cell)) + 1
    hlo, hhi = sh.min() - p.below - 0.01, sh.max() + p.above + 0.01
    # the region's points (a box around it in local coordinates) and their columns
    corners = np.array([origin + x * u + y * v + h * n for x in (x0, x0 + nx * p.cell) for y in (y0, y0 + ny * p.cell)
                        for h in (hlo, hhi)])  # fmt: skip
    lo, hi = corners.min(0), corners.max(0)
    t = tiles
    cand = np.flatnonzero((t["x1"] >= lo[0]) & (t["x0"] <= hi[0]) & (t["y1"] >= lo[1]) & (t["y0"] <= hi[1])
                          & (t["zmax"] >= lo[2]) & (t["zmin"] <= hi[2]))  # fmt: skip
    if inside is not None:
        cand = inside.keep_tiles(t, cand)
    idx_parts, col_parts, h_parts = [], [], []
    for k in cand:
        a, m = int(t["start"][k]), int(t["count"][k])
        col, hgt = np.empty(m, np.int64), np.empty(m)
        _columns(arr, a, a + m, origin, n, u, v, x0, y0, p.cell, nx, ny, hlo, hhi, col, hgt)
        keep = np.flatnonzero((col >= 0) & (inside.mask[a : a + m] if inside is not None else True))
        idx_parts.append(keep + a)
        col_parts.append(col[keep])
        h_parts.append(hgt[keep])
    idx = np.concatenate(idx_parts) if idx_parts else np.empty(0, np.int64)
    col = np.concatenate(col_parts) if col_parts else np.empty(0, np.int64)
    hh = np.concatenate(h_parts) if h_parts else np.empty(0)
    from pclabel.store.annotations import isin_sorted

    is_sel = isin_sorted(idx, sel)
    ok = allowed(idx) if allowed is not None else np.ones(idx.size, bool)
    n_cells = nx * ny
    cnt_sel = np.bincount(col[is_sel], minlength=n_cells).reshape(ny, nx)
    sum_h = np.bincount(col[is_sel], weights=hh[is_sel], minlength=n_cells).reshape(ny, nx)
    cnt_all = np.bincount(col, minlength=n_cells).reshape(ny, nx)
    covered = cnt_sel > 0
    has = cnt_all > 0
    r = max(1, int(round(p.radius / p.cell)))
    yy, xx = np.mgrid[-r : r + 1, -r : r + 1]
    disk = ((xx**2 + yy**2) <= r * r).astype(float)
    near_cov = ndimage.convolve(covered.astype(float), disk, mode="constant")
    near_has = ndimage.convolve(has.astype(float), disk, mode="constant")
    frac = np.divide(near_cov, near_has, out=np.zeros_like(near_cov), where=near_has > 0)
    # the selected surface: own mean, else the mean of the columns around
    own = np.divide(sum_h, cnt_sel, out=np.full(sum_h.shape, np.nan), where=covered)
    around = np.divide(ndimage.convolve(np.where(covered, own, 0.0), disk, mode="constant"), near_cov,
                       out=np.full(sum_h.shape, np.nan), where=near_cov > 0)  # fmt: skip
    surface = np.where(covered, own, around)
    footprint = covered | (p.fill_holes & has & (frac >= p.majority) & np.isfinite(surface))
    # removals: specks and small islands
    drop = np.zeros_like(covered)
    if p.remove_specks:
        drop |= covered & (frac < 1.0 - p.majority)
        lab, n_lab = ndimage.label(covered & ~drop, structure=np.ones((3, 3)))
        if n_lab:
            sizes = np.bincount(lab.ravel(), weights=cnt_sel.ravel(), minlength=n_lab + 1)
            drop |= (lab > 0) & (sizes[lab] < p.min_island)
    footprint &= ~drop
    s_flat, f_flat, d_flat = surface.ravel(), footprint.ravel(), drop.ravel()
    rel = hh - s_flat[col]
    add = ~is_sel & ok & f_flat[col] & np.isfinite(rel) & (rel >= -p.below) & (rel <= p.above)
    remove = is_sel & ok & d_flat[col]
    added, removed = idx[add], idx[remove]
    added.sort()
    removed.sort()
    info = {"added": int(added.size), "removed": int(removed.size), "columns": int(covered.sum()),
            "holes_filled": int((footprint & ~covered).sum()), "region_points": int(idx.size), "params": asdict(p)}  # fmt: skip
    return added, removed, info


def apply_clean(selection, added, removed) -> np.ndarray:
    return sets.difference(sets.union(np.asarray(selection, np.int64), added), removed)
