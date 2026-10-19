"""Ground grow: from a seed point, select the connected ground surface (SPEC §11 region grow, on a cell grid).

1. Cells. Points around the seed (a uniform subsample: tiles are shuffled, so their first points are one) are
   binned into square cells. Per cell, the *ground layer* is the points within ``surface_band`` of the cell's 5th
   percentile height, so canopy, cars and kerb tops above the ground do not count. For the ground layer the cell
   keeps a median height, a median intensity and a roughness (standard deviation of height).
2. Grow (8 directions, from the seed cell). Empty cells (no points: gaps between far scan lines, small shadows) are
   stepped over, up to ``max_gap``. The next cell with data in a direction joins when all of these hold:
   - its height differs from the current cell's by at most ``max_step`` per cell of distance: kerbs stop the grow;
   - its roughness is at most ``max_roughness``: kerb faces, vegetation and objects stop it;
   - its intensity differs from the current cell's by at most ``intensity_step``, and from the seed's by at most
     ``intensity_range``. A walkway at nearly the road's height but of another material stops the grow; slow
     changes of intensity with range do not.
3. Markings. Paint is road too, but its intensity differs. Strips up to ``marking_width`` wide inside or between
   grown areas are bridged (morphological closing), and enclosed holes (arrows, text) are filled, but only where
   the cell lies at the height of the grown surface around it (within 2 x ``max_step``).
4. Points. Every full-resolution point in a grown cell within ``surface_tol`` of the cell's surface is selected.
   Points below the surface (drains, multipath) and above it (vehicles, canopy) are not.

All parameters are thresholds under study; the result carries them, and labelling records them (M3).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numba as nb
import numpy as np


@dataclass(frozen=True)
class GroundGrowParams:
    max_step: float = 0.02  # m, between neighbouring cells (per cell of distance across gaps)
    intensity_step: float = 0.02  # between neighbouring cells (intensity 0..1 of the stored range)
    intensity_range: float = 0.04  # from the seed's intensity; 1.0 switches it off
    marking_width: float = 0.40  # m, narrower strips of other intensity are bridged
    max_roughness: float = 0.03  # m, standard deviation of a cell's ground layer
    max_gap: float = 0.30  # m, empty cells (no points) the grow may step over
    surface_tol: float = 0.04  # m, full-resolution points this close to the cell surface are selected
    cell: float = 0.10  # m
    radius: float = 30.0  # m, how far from the seed the grow may reach
    surface_band: float = 0.10  # m, above a cell's 5th percentile height: its ground layer
    sample_density: float = 4_000.0  # points per m^2 read for the cell statistics (dense tiles are subsampled)


@dataclass
class CellGrid:
    x0: float
    y0: float
    cell: float
    nx: int
    ny: int
    surf: np.ndarray  # (ny, nx) float64, median ground height (local z), NaN where no data
    inten: np.ndarray  # (ny, nx) float64, median ground intensity 0..1
    rough: np.ndarray  # (ny, nx) float64
    count: np.ndarray  # (ny, nx) int64, sample points per cell
    sample_points: int = 0


@dataclass
class GrowResult:
    indices: np.ndarray  # sorted int64 store indices
    region: np.ndarray  # (ny, nx) bool, grown cells (with bridged markings)
    grid: CellGrid
    info: dict


@nb.njit(parallel=True, nogil=True, cache=True)
def _cell_stats(z, inten, order, starts, counts, band, surf, med_i, rough):
    for c in nb.prange(starts.shape[0]):
        n = counts[c]
        if n < 3:
            continue
        s = starts[c]
        zz = np.empty(n)
        ii = np.empty(n)
        for k in range(n):
            j = order[s + k]
            zz[k] = z[j]
            ii[k] = inten[j]
        o = np.argsort(zz)
        zz = zz[o]
        ii = ii[o]
        low = zz[int(0.05 * n)]
        m = 0
        while m < n and zz[m] <= low + band:
            m += 1
        g = zz[:m]
        surf[c] = g[m // 2] if m % 2 == 1 else 0.5 * (g[m // 2 - 1] + g[m // 2])
        gi = np.sort(ii[:m])
        med_i[c] = gi[m // 2] if m % 2 == 1 else 0.5 * (gi[m // 2 - 1] + gi[m // 2])
        mean = g.mean()
        rough[c] = np.sqrt(((g - mean) ** 2).mean())


@nb.njit(nogil=True, cache=True)
def _grow(surf, inten, rough, seed, ref_i, max_step, max_rough, i_step, i_range, gap_cells):
    ny, nx = surf.shape
    region = np.zeros((ny, nx), np.bool_)
    queue = np.empty(ny * nx, np.int64)
    head, tail = 0, 0
    sy, sx = seed
    region[sy, sx] = True
    queue[tail] = sy * nx + sx
    tail += 1
    while head < tail:
        cur = queue[head]
        head += 1
        cy, cx = cur // nx, cur % nx
        h0 = surf[cy, cx]
        i0 = inten[cy, cx]
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                unit = np.sqrt(2.0) if dy != 0 and dx != 0 else 1.0
                for s in range(1, gap_cells + 2):  # the neighbour, or the next cell with data across a gap
                    y, x = cy + dy * s, cx + dx * s
                    if y < 0 or y >= ny or x < 0 or x >= nx or region[y, x]:
                        break
                    h = surf[y, x]
                    if not np.isfinite(h):
                        continue
                    if abs(h - h0) <= max_step * unit * s and rough[y, x] <= max_rough:
                        i = inten[y, x]
                        if abs(i - i0) <= i_step and abs(i - ref_i) <= i_range:
                            region[y, x] = True
                            queue[tail] = y * nx + x
                            tail += 1
                    break
    return region


@nb.njit(nogil=True, cache=True)
def _counting_order(cid, n_cells):
    counts = np.zeros(n_cells, np.int64)
    for i in range(cid.shape[0]):
        counts[cid[i]] += 1
    starts = np.zeros(n_cells, np.int64)
    for c in range(1, n_cells):
        starts[c] = starts[c - 1] + counts[c - 1]
    pos = starts.copy()
    order = np.empty(cid.shape[0], np.int64)
    for i in range(cid.shape[0]):
        order[pos[cid[i]]] = i
        pos[cid[i]] += 1
    return order, starts, counts


@nb.njit(parallel=True, nogil=True, cache=True)
def _classify(xyz, a, b, x0, y0, cell, nx, ny, region, surf, tol, out):
    for k in nb.prange(b - a):
        i = a + k
        cx = int(np.floor((xyz[i, 0] - x0) / cell))
        cy = int(np.floor((xyz[i, 1] - y0) / cell))
        ok = False
        if 0 <= cx < nx and 0 <= cy < ny and region[cy, cx]:
            ok = abs(xyz[i, 2] - surf[cy, cx]) <= tol
        out[k] = ok


def _roi_tiles(tiles, seed, radius, clip=None):
    t = tiles
    near = (t["x1"] >= seed[0] - radius) & (t["x0"] <= seed[0] + radius) & (t["y1"] >= seed[1] - radius) & (
        t["y0"] <= seed[1] + radius
    )
    if clip is not None:
        lo, hi = clip
        near &= (t["x1"] >= lo[0]) & (t["x0"] <= hi[0]) & (t["y1"] >= lo[1]) & (t["y0"] <= hi[1])
        near &= (t["zmax"] >= lo[2]) & (t["zmin"] <= hi[2])
    return np.flatnonzero(near)


def cell_grid(xyz, intensity, tiles, seed, params: GroundGrowParams, clip=None) -> CellGrid:
    """Cell statistics around ``seed`` (local coordinates). ``clip`` = (lo, hi) local box limits the points."""
    p = params
    seed = np.asarray(seed, np.float64)
    x0, y0 = seed[0] - p.radius, seed[1] - p.radius
    nx = ny = int(np.ceil(2 * p.radius / p.cell))
    xs, iv = [], []
    for t in _roi_tiles(tiles, seed, p.radius, clip):
        a, n = int(tiles["start"][t]), int(tiles["count"][t])
        area = max((tiles["x1"][t] - tiles["x0"][t]) * (tiles["y1"][t] - tiles["y0"][t]), 0.01)
        m = int(min(n, max(2000, area * p.sample_density)))
        q = np.asarray(xyz[a : a + m])
        keep = (q[:, 0] >= x0) & (q[:, 0] < x0 + nx * p.cell) & (q[:, 1] >= y0) & (q[:, 1] < y0 + ny * p.cell)
        if clip is not None:
            keep &= np.all((q >= clip[0]) & (q <= clip[1]), axis=1)
        xs.append(q[keep])
        iv.append(np.asarray(intensity[a : a + m])[keep] if intensity is not None else np.zeros(int(keep.sum())))
    q = np.concatenate(xs) if xs else np.empty((0, 3), np.float32)
    inten = (np.concatenate(iv).astype(np.float64) / 65535.0) if xs else np.empty(0)
    cx = np.floor((q[:, 0] - x0) / p.cell).astype(np.int64)
    cy = np.floor((q[:, 1] - y0) / p.cell).astype(np.int64)
    order, starts, counts = _counting_order(cy * nx + cx, nx * ny)
    surf = np.full(nx * ny, np.nan)
    med_i = np.full(nx * ny, np.nan)
    rough = np.full(nx * ny, np.nan)
    _cell_stats(q[:, 2].astype(np.float64), inten, order, starts, counts, p.surface_band, surf, med_i, rough)
    return CellGrid(x0, y0, p.cell, nx, ny, surf.reshape(ny, nx), med_i.reshape(ny, nx), rough.reshape(ny, nx),
                    counts.reshape(ny, nx), len(q))  # fmt: skip


def _disk(r: float) -> np.ndarray:
    k = int(np.ceil(r))
    yy, xx = np.mgrid[-k : k + 1, -k : k + 1]
    return (xx**2 + yy**2) <= r * r + 1e-9


def grow_cells(grid: CellGrid, seed, params: GroundGrowParams):
    """(region, info): grown cells with bridged markings, and figures about the grow."""
    from scipy import ndimage

    p = params
    sx = int(np.floor((seed[0] - grid.x0) / grid.cell))
    sy = int(np.floor((seed[1] - grid.y0) / grid.cell))
    valid = np.isfinite(grid.surf)
    if not (0 <= sx < grid.nx and 0 <= sy < grid.ny):
        raise ValueError("the seed is outside the grid")
    if not valid[sy, sx]:  # nearest cell with data, within 3 cells
        ys, xs = np.nonzero(valid[max(sy - 3, 0) : sy + 4, max(sx - 3, 0) : sx + 4])
        if ys.size == 0:
            raise ValueError("no ground data at the seed")
        k = np.argmin((ys + max(sy - 3, 0) - sy) ** 2 + (xs + max(sx - 3, 0) - sx) ** 2)
        sy, sx = int(ys[k] + max(sy - 3, 0)), int(xs[k] + max(sx - 3, 0))
    h_seed = grid.surf[sy, sx]
    win = (slice(max(sy - 2, 0), sy + 3), slice(max(sx - 2, 0), sx + 3))
    near = valid[win] & (np.abs(grid.surf[win] - h_seed) <= p.max_step)
    ref_i = float(np.median(grid.inten[win][near])) if near.any() else float(grid.inten[sy, sx])
    surf = np.where(valid, grid.surf, np.nan)
    rough = np.where(valid, grid.rough, np.inf)
    inten = np.where(valid, grid.inten, np.nan)
    grown = _grow(surf, inten, rough, (sy, sx), ref_i, p.max_step, p.max_roughness, p.intensity_step,
                  p.intensity_range, int(round(p.max_gap / grid.cell)))  # fmt: skip

    # markings: bridge narrow strips (closing) and fill enclosed holes, at the grown surface's height only
    r = p.marking_width / 2 / grid.cell
    added = np.zeros_like(grown)
    if r >= 0.5 and grown.any():
        disk = _disk(r)
        closed = ndimage.binary_closing(grown, structure=disk, border_value=0) | ndimage.binary_fill_holes(grown)
        cand = closed & ~grown & valid & (grid.rough <= p.max_roughness)
        if cand.any():
            big = _disk(max(r, 1.5 / grid.cell))  # local surface height from grown cells around (up to 1.5 m)
            s0 = np.where(grown, surf, 0.0)
            num = ndimage.convolve(s0, big.astype(float), mode="constant")
            den = ndimage.convolve(grown.astype(float), big.astype(float), mode="constant")
            local = np.divide(num, den, out=np.full_like(num, np.nan), where=den > 0)
            added = cand & (np.abs(surf - local) <= 2 * p.max_step)
    region = grown | added
    info = {
        "seed_cell": (sy, sx),
        "seed_height_local": float(h_seed),
        "seed_intensity": ref_i,
        "cells_grown": int(grown.sum()),
        "cells_bridged": int(added.sum()),
        "area_m2": float(region.sum() * grid.cell**2),
        "reached_edge": bool(region[0, :].any() or region[-1, :].any() or region[:, 0].any() or region[:, -1].any()),
    }
    return region, info


def select_region_points(xyz, tiles, grid: CellGrid, region: np.ndarray, params: GroundGrowParams,
                         clip=None) -> np.ndarray:  # fmt: skip
    """Full-resolution store indices of the points on the grown surface."""
    if not region.any():
        return np.empty(0, np.int64)
    ys, xs = np.nonzero(region)
    lo = np.array([grid.x0 + xs.min() * grid.cell, grid.y0 + ys.min() * grid.cell])
    hi = np.array([grid.x0 + (xs.max() + 1) * grid.cell, grid.y0 + (ys.max() + 1) * grid.cell])
    t = tiles
    cand = np.flatnonzero((t["x1"] >= lo[0]) & (t["x0"] <= hi[0]) & (t["y1"] >= lo[1]) & (t["y0"] <= hi[1]))
    arr = np.asarray(xyz)
    surf = np.where(region, grid.surf, 0.0)
    parts = []
    for k in cand:
        a, b = int(t["start"][k]), int(t["start"][k] + t["count"][k])
        out = np.empty(b - a, np.bool_)
        _classify(arr, a, b, grid.x0, grid.y0, grid.cell, grid.nx, grid.ny, region, surf, params.surface_tol, out)
        idx = np.flatnonzero(out) + a
        if clip is not None and idx.size:
            q = arr[idx]
            idx = idx[np.all((q >= clip[0]) & (q <= clip[1]), axis=1)]
        parts.append(idx.astype(np.int64))
    return np.concatenate(parts) if parts else np.empty(0, np.int64)


def ground_grow(xyz, intensity, tiles, seed, params: GroundGrowParams = GroundGrowParams(), *, grid=None,
                clip=None) -> GrowResult:  # fmt: skip
    """Run the whole tool. Pass ``grid`` from a previous run with the same seed area and cell size to re-grow
    with new thresholds without re-reading the points."""
    seed = np.asarray(seed, np.float64)
    if grid is None:
        grid = cell_grid(xyz, intensity, tiles, seed, params, clip)
    region, info = grow_cells(grid, seed, params)
    idx = select_region_points(xyz, tiles, grid, region, params, clip)
    info["points"] = int(idx.size)
    info["params"] = asdict(params)
    return GrowResult(idx, region, grid, info)
