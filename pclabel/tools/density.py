"""Point density of a point set across the height axis: the parts dense enough to work on (the user's request of
2026-10-21: "select the parts [of the road] that are dense enough that it has reasonable quality to e.g. magic select
road lines out").

The set's points are counted in columns of ``cell`` (25 cm) across the height axis (the active frame: z, or a
fitted plane). A column's density is the mean over the occupied columns within ``radius`` (50 cm): points per m² of
covered ground, so the road's edges and the empty columns of a car's shadow do not pull it down. Columns at or above
``min_density`` are dense. Dense pieces smaller than ``min_area`` are dropped (a dense speck in a sparse area is no
place to work).

The default threshold, MIN_DENSITY, comes from streets 1 (docs/OBSERVATIONS.md, O-0011). Seven real lane dashes
thinned at random: one magic select click got the whole dash (>= 90 % recall and precision) in >= 90 % of trials at
2,000 points/m² and above, 81-86 % at 500-1,000, 38 % at 350 and none at 150. The user's own clicks on the lane line
of that scan: every dash denser than 1,936 points/m² took one click; from about 1,200 down, one to three; below
500, always several (12 at 159 points/m²). Thinning is optimistic (it does not widen the laser footprint), so the
default sits where the real clicks put it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numba as nb
import numpy as np

from pclabel.tools.volume import frame_axes

MIN_DENSITY = 2000.0  # points per m² (points about 2.2 cm apart); see the module docstring


@dataclass(frozen=True)
class DensityParams:
    min_density: float = MIN_DENSITY  # points per m² across the height axis
    cell: float = 0.25  # m, columns
    radius: float = 0.5  # m, the density is averaged over the occupied columns within this
    min_area: float = 1.0  # m², smaller dense pieces are dropped


@dataclass
class DensityGrid:
    """Per-column counts of a set (computed once; thresholds are cheap to change)."""

    indices: np.ndarray  # the set's sorted store indices
    col: np.ndarray  # int32 per point: its column (row-major in (ny, nx))
    count: np.ndarray  # (ny, nx) int64 points per column
    density: np.ndarray  # (ny, nx) float64 points per m², averaged over the occupied columns around; NaN if empty
    x0: float
    y0: float
    cell: float
    radius: float


@nb.njit(parallel=True, nogil=True, cache=True)
def _project(xyz, idx, origin, u, v, x, y):
    for k in nb.prange(idx.shape[0]):
        i = idx[k]
        dx, dy, dz = xyz[i, 0] - origin[0], xyz[i, 1] - origin[1], xyz[i, 2] - origin[2]
        x[k] = dx * u[0] + dy * u[1] + dz * u[2]
        y[k] = dx * v[0] + dy * v[1] + dz * v[2]


@nb.njit(parallel=True, nogil=True, cache=True)
def _cells(x, y, x0, y0, cell, nx, col):
    for k in nb.prange(x.shape[0]):
        col[k] = np.int32(int((y[k] - y0) / cell) * nx + int((x[k] - x0) / cell))


def _disk(r: int) -> np.ndarray:
    yy, xx = np.mgrid[-r : r + 1, -r : r + 1]
    return ((xx**2 + yy**2) <= r * r).astype(float)


def density_grid(xyz, indices, axis, along, origin, cell: float = 0.25, radius: float = 0.5) -> DensityGrid:
    """Count a set's points (sorted store indices) in columns across the height axis ``axis`` (``along`` and
    ``origin``, local, fix the grid)."""
    from scipy import ndimage

    idx = np.asarray(indices, np.int64)
    n, u, v = frame_axes(axis, along, 0.0)
    x, y = np.empty(idx.size), np.empty(idx.size)
    _project(np.asarray(xyz), idx, np.asarray(origin, np.float64), u, v, x, y)
    if idx.size == 0:
        return DensityGrid(idx, np.empty(0, np.int32), np.zeros((1, 1), np.int64), np.full((1, 1), np.nan), 0.0, 0.0,
                           cell, radius)  # fmt: skip
    x0, y0 = float(x.min()), float(y.min())
    nx = int((x.max() - x0) / cell) + 1
    ny = int((y.max() - y0) / cell) + 1
    if nx * ny >= 2**31:
        raise ValueError("the set is too large for this column size")
    col = np.empty(idx.size, np.int32)
    _cells(x, y, x0, y0, cell, nx, col)
    count = np.bincount(col, minlength=nx * ny).reshape(ny, nx)
    occ = count > 0
    disk = _disk(max(1, int(round(radius / cell))))
    pts = ndimage.convolve(count.astype(float), disk, mode="constant")
    cols = ndimage.convolve(occ.astype(float), disk, mode="constant")
    density = np.where(occ, pts / np.maximum(cols, 1) / cell**2, np.nan)
    return DensityGrid(idx, col, count, density, x0, y0, cell, radius)


def dense_part(grid: DensityGrid, params: DensityParams = DensityParams()):
    """(dense, sparse, info): the set's points (sorted store indices) in dense columns and in the rest."""
    from scipy import ndimage

    p = params
    g = grid
    dense = np.nan_to_num(g.density, nan=0.0) >= p.min_density
    lab, n_lab = ndimage.label(dense, structure=np.ones((3, 3)))
    dropped = 0
    if n_lab:
        cells = np.bincount(lab.ravel(), minlength=n_lab + 1)
        small = cells * g.cell**2 < p.min_area
        small[0] = False
        dropped = int(small.sum())
        dense &= ~small[lab]
    keep = dense.ravel()[g.col] if g.col.size else np.zeros(0, bool)
    occ = g.count > 0
    a = g.cell**2
    d = g.density[occ]
    info = {
        "points": int(g.indices.size),
        "dense_points": int(keep.sum()),
        "area_m2": float(occ.sum() * a),
        "dense_area_m2": float((dense & occ).sum() * a),
        "density_percentiles": {str(q): float(v) for q, v in zip((5, 25, 50, 75, 95), np.percentile(d, (5, 25, 50, 75, 95)))}
        if d.size else {},  # fmt: skip
        "pieces_dropped": dropped,
        "params": asdict(p),
        "cell": g.cell,
        "radius": g.radius,
    }
    return g.indices[keep], g.indices[~keep], info
