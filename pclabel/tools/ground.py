"""Ground grow: from a seed point, select the connected ground surface (SPEC §11 region grow, on a cell grid).

1. Cells. Every point around the seed is binned into square cells, and each cell keeps its first
   ``cell_points`` points (a uniform sample of the cell, because tiles are shuffled). Capping per cell, not per
   tile, matters: a tile near the scanner holds millions of points, and sampling it as a whole would leave its
   sparser road part with too few points per cell (holes, and straight cut lines along tile edges). Per cell,
   the *ground layer* is the points within ``surface_band`` of the cell's 5th percentile height, so canopy, cars
   and kerb tops above the ground do not count. For the ground layer the cell
   keeps a median height, a median intensity and a roughness (standard deviation of height).
2. Grow (8 directions, from the seed cell). Empty cells (no points: gaps between far scan lines, occlusion shadows
   of poles and kerbs) are stepped over, up to ``max_gap``. The next cell with data in a direction joins when all of
   these hold:
   - its height differs from the current cell's by at most ``max_step``, plus ``max_slope`` times the extra
     distance when it lies across a gap: kerbs stop the grow, also behind a no-point strip;
   - its roughness is at most ``max_roughness``: kerb faces, vegetation and objects stop it;
   - its intensity differs from the current cell's by at most ``intensity_step``, and from the *local road
     intensity* by at most ``intensity_range``. The local road intensity is a running average along the grow
     (memory about ``intensity_memory``), so intensity may drift slowly with range and incidence, but a walkway
     at nearly the road's height and of another material stops the grow.
   Each refused cell records why (``REASONS``), so the user can see which threshold stopped the grow.
3. Markings. Paint is road too, but its intensity differs. Strips up to ``marking_width`` wide inside or between
   grown areas are bridged (morphological closing), and enclosed holes (arrows, text) are filled, but only where
   the cell lies at the height of the grown surface around it (within 2 x ``max_step``).
4. Points. Every full-resolution point in a grown cell within ``surface_tol`` of the cell's surface is selected.
   Points below the surface (drains, multipath) and above it (vehicles, canopy) are not.

All parameters are thresholds under study; the result carries them, and labelling records them (M3).
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

import numba as nb
import numpy as np

from pclabel.tools import sets


@dataclass(frozen=True)
class GroundGrowParams:
    max_step: float = 0.02  # m, between neighbouring cells (per cell of distance across gaps)
    intensity_step: float = 0.02  # between neighbouring cells (intensity 0..1 of the stored range)
    intensity_range: float = 0.04  # from the local road intensity (running average); 1.0 switches it off
    intensity_memory: float = 2.0  # m, how much grown road the running average remembers
    marking_width: float = 0.40  # m, narrower strips of other intensity are bridged
    max_roughness: float = 0.03  # m, standard deviation of a cell's ground layer
    max_gap: float = 1.0  # m, empty cells (no points) the grow may step over
    max_slope: float = 0.08  # extra height allowed across a gap, per metre (road grade and crossfall)
    surface_tol: float = 0.04  # m, full-resolution points this close to the cell surface are selected
    cell: float = 0.10  # m
    radius: float = 30.0  # m, how far from the seed the grow may reach
    surface_band: float = 0.10  # m, above a cell's 5th percentile height: its ground layer
    cell_points: int = 48  # points kept per cell for its statistics (fewer if the grid is very large)


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
    count: np.ndarray  # (ny, nx) int64, all points per cell
    sample_points: int = 0


@dataclass
class GrowResult:
    indices: np.ndarray  # sorted int64 store indices
    region: np.ndarray  # (ny, nx) bool, grown cells (with bridged markings)
    grid: CellGrid
    info: dict


THREADS = max(1, min(32, os.cpu_count() or 1))  # tiles are processed in parallel (numba kernels release the GIL)


def tile_block(tiles, t, x0, y0, cell, nx, ny):
    """Tile ``t``'s bounds as a block (ox, oy, w, h) of a grid's cells, clipped to the grid (w or h is 0 outside).
    Every point of the tile that falls in the grid falls in the block."""
    ox = max(int(np.floor((tiles["x0"][t] - x0) / cell)), 0)
    oy = max(int(np.floor((tiles["y0"][t] - y0) / cell)), 0)
    ex = min(int(np.floor((tiles["x1"][t] - x0) / cell)) + 1, nx)
    ey = min(int(np.floor((tiles["y1"][t] - y0) / cell)) + 1, ny)
    return ox, oy, max(ex - ox, 0), max(ey - oy, 0)


@nb.njit(nogil=True, cache=True)
def _accumulate(xyz, inten, has_int, a, b, x0, y0, cell, ox, oy, w, h, clip_on, lo, hi, in_on, inside, cnt, bz, bi,
                seen):  # fmt: skip
    """Keep the first K points (K = bz.shape[1]) of every cell of the block (ox, oy, w, h) of the grid at (x0, y0)
    from store points a..b (only those in ``inside`` when ``in_on``)."""
    k_max = bz.shape[1]
    for i in range(a, b):
        if in_on and not inside[i]:
            continue
        x, y, z = xyz[i, 0], xyz[i, 1], xyz[i, 2]
        if clip_on and (x < lo[0] or x > hi[0] or y < lo[1] or y > hi[1] or z < lo[2] or z > hi[2]):
            continue
        cx = int(np.floor((x - x0) / cell)) - ox
        cy = int(np.floor((y - y0) / cell)) - oy
        if cx < 0 or cx >= w or cy < 0 or cy >= h:
            continue
        c = cy * w + cx
        seen[c] += 1
        k = cnt[c]
        if k < k_max:
            bz[c, k] = z
            bi[c, k] = inten[i] / 65535.0 if has_int else 0.0
            cnt[c] = k + 1


@nb.njit(nogil=True, cache=True)
def _merge_block(nx, ox, oy, w, h, cnt_t, bz_t, bi_t, seen_t, cnt, bz, bi, seen):
    """Append a block's kept points to the grid's, up to K per cell (blocks merged in tile order keep the same
    points as one pass over the tiles in that order)."""
    k_max = bz.shape[1]
    for r in range(h):
        for q in range(w):
            s = r * w + q
            if seen_t[s] == 0:
                continue
            c = (oy + r) * nx + ox + q
            seen[c] += seen_t[s]
            k = cnt[c]
            m = min(cnt_t[s], k_max - k)
            for j in range(m):
                bz[c, k + j] = bz_t[s, j]
                bi[c, k + j] = bi_t[s, j]
            cnt[c] = k + m


@nb.njit(parallel=True, nogil=True, cache=True)
def _cell_stats(bz, bi, cnt, band, surf, med_i, rough):
    for c in nb.prange(cnt.shape[0]):
        n = cnt[c]
        if n < 3:
            continue
        zz = bz[c, :n].astype(np.float64)
        ii = bi[c, :n].astype(np.float64)
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


# why a cell was refused (the first test it failed), for the panel and the debug picture
R_NONE, R_HEIGHT, R_ROUGH, R_ISTEP, R_IRANGE, R_GAP = range(6)
REASONS = {R_HEIGHT: "height step", R_ROUGH: "roughness", R_ISTEP: "intensity step",
           R_IRANGE: "intensity vs local road", R_GAP: "gap wider than max gap"}  # fmt: skip


@nb.njit(nogil=True, cache=True)
def _grow(surf, inten, rough, seed, ref_i, max_step, max_slope, cell, max_rough, i_step, i_range, alpha, gap_cells):
    ny, nx = surf.shape
    region = np.zeros((ny, nx), np.bool_)
    reason = np.zeros((ny, nx), np.uint8)
    ref = np.full((ny, nx), np.nan)  # running average of road intensity along the grow
    queue = np.empty(ny * nx, np.int64)
    head, tail = 0, 0
    sy, sx = seed
    region[sy, sx] = True
    ref[sy, sx] = ref_i
    queue[tail] = sy * nx + sx
    tail += 1
    while head < tail:
        cur = queue[head]
        head += 1
        cy, cx = cur // nx, cur % nx
        h0 = surf[cy, cx]
        i0 = inten[cy, cx]
        r0 = ref[cy, cx]
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                unit = np.sqrt(2.0) if dy != 0 and dx != 0 else 1.0
                found = False
                for s in range(1, gap_cells + 2):  # the neighbour, or the next cell with data across a gap
                    y, x = cy + dy * s, cx + dx * s
                    if y < 0 or y >= ny or x < 0 or x >= nx or region[y, x]:
                        found = True  # nothing to refuse
                        break
                    h = surf[y, x]
                    if not np.isfinite(h):
                        continue
                    found = True
                    extra = (s * unit - 1.0) * cell  # distance beyond an ordinary neighbour
                    code = R_NONE
                    i = inten[y, x]
                    if abs(h - h0) > max_step + max_slope * extra:
                        code = R_HEIGHT
                    elif rough[y, x] > max_rough:
                        code = R_ROUGH
                    elif abs(i - i0) > i_step:
                        code = R_ISTEP
                    elif abs(i - r0) > i_range:
                        code = R_IRANGE
                    if code == R_NONE:
                        region[y, x] = True
                        reason[y, x] = R_NONE
                        ref[y, x] = (1.0 - alpha) * r0 + alpha * i
                        queue[tail] = y * nx + x
                        tail += 1
                    elif reason[y, x] == R_NONE:
                        reason[y, x] = code
                    break
                if not found:  # only empty cells within max_gap in this direction
                    y, x = cy + dy, cx + dx
                    if reason[y, x] == R_NONE:
                        reason[y, x] = R_GAP
    for y in range(ny):
        for x in range(nx):
            if region[y, x]:
                reason[y, x] = R_NONE
    return region, reason


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


def _roi_tiles(tiles, seed, radius, clip=None, inside=None):
    t = tiles
    near = (t["x1"] >= seed[0] - radius) & (t["x0"] <= seed[0] + radius) & (t["y1"] >= seed[1] - radius) & (
        t["y0"] <= seed[1] + radius
    )
    if clip is not None:
        lo, hi = clip
        near &= (t["x1"] >= lo[0]) & (t["x0"] <= hi[0]) & (t["y1"] >= lo[1]) & (t["y0"] <= hi[1])
        near &= (t["zmax"] >= lo[2]) & (t["zmin"] <= hi[2])
    cand = np.flatnonzero(near)
    return cand if inside is None else inside.keep_tiles(tiles, cand)


def cell_grid(xyz, intensity, tiles, seed, params: GroundGrowParams, clip=None, *, extent=None,
              inside=None) -> CellGrid:  # fmt: skip
    """Cell statistics around ``seed`` (local coordinates). ``clip`` = (lo, hi) local box and ``inside`` (a
    tools.sets.PointMask: the clip to a point set) limit the points. ``extent`` = (x0, y0, nx, ny) places the grid
    explicitly instead (``params.radius`` is then unused)."""
    p = params
    seed = np.asarray(seed, np.float64)
    if extent is None:
        x0, y0 = seed[0] - p.radius, seed[1] - p.radius
        nx = ny = int(np.ceil(2 * p.radius / p.cell))
    else:
        x0, y0, nx, ny = float(extent[0]), float(extent[1]), int(extent[2]), int(extent[3])
    half = 0.5 * max(nx, ny) * p.cell
    centre = np.array([x0 + 0.5 * nx * p.cell, y0 + 0.5 * ny * p.cell])
    n_cells = nx * ny
    k = int(max(8, min(p.cell_points, 60_000_000 // n_cells)))  # at most about 0.5 GB of buffers
    cnt = np.zeros(n_cells, np.int64)
    seen = np.zeros(n_cells, np.int64)
    bz = np.empty((n_cells, k), np.float32)
    bi = np.empty((n_cells, k), np.float32)
    arr = np.asarray(xyz)
    has_int = intensity is not None
    iarr = np.asarray(intensity) if has_int else np.zeros(1, np.uint16)
    lo, hi = (np.asarray(clip[0], np.float64), np.asarray(clip[1], np.float64)) if clip is not None else (
        np.zeros(3), np.zeros(3))  # fmt: skip
    in_on, in_mask = sets.mask_args(inside)

    def block(t):
        ox, oy, w, h = tile_block(tiles, t, x0, y0, p.cell, nx, ny)
        if w == 0 or h == 0:
            return None
        a, n = int(tiles["start"][t]), int(tiles["count"][t])
        cnt_t, seen_t = np.zeros(w * h, np.int64), np.zeros(w * h, np.int64)
        bz_t, bi_t = np.empty((w * h, k), np.float32), np.empty((w * h, k), np.float32)
        _accumulate(arr, iarr, has_int, a, a + n, x0, y0, p.cell, ox, oy, w, h, clip is not None, lo, hi, in_on,
                    in_mask, cnt_t, bz_t, bi_t, seen_t)  # fmt: skip
        return ox, oy, w, h, cnt_t, bz_t, bi_t, seen_t

    with ThreadPoolExecutor(THREADS) as ex:
        for b in ex.map(block, _roi_tiles(tiles, centre, half, clip, inside)):  # in tile order
            if b is not None:
                _merge_block(nx, *b, cnt, bz, bi, seen)
    surf = np.full(n_cells, np.nan)
    med_i = np.full(n_cells, np.nan)
    rough = np.full(n_cells, np.nan)
    _cell_stats(bz, bi, cnt, p.surface_band, surf, med_i, rough)
    return CellGrid(x0, y0, p.cell, nx, ny, surf.reshape(ny, nx), med_i.reshape(ny, nx), rough.reshape(ny, nx),
                    seen.reshape(ny, nx), int(cnt.sum()))  # fmt: skip


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
    alpha = min(1.0, grid.cell / max(p.intensity_memory, grid.cell))
    grown, reason = _grow(surf, inten, rough, (sy, sx), ref_i, p.max_step, p.max_slope, grid.cell, p.max_roughness,
                          p.intensity_step, p.intensity_range, alpha, int(round(p.max_gap / grid.cell)))  # fmt: skip

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
    reason[region] = R_NONE
    stopped = {REASONS[k]: int((reason == k).sum()) for k in REASONS}
    info = {
        "stopped_by": stopped,
        "seed_cell": (sy, sx),
        "seed_height_local": float(h_seed),
        "seed_intensity": ref_i,
        "cells_grown": int(grown.sum()),
        "cells_bridged": int(added.sum()),
        "area_m2": float(region.sum() * grid.cell**2),
        "reached_edge": bool(region[0, :].any() or region[-1, :].any() or region[:, 0].any() or region[:, -1].any()),
    }
    info["reason_grid"] = reason  # (ny, nx) uint8, see REASONS; not part of the parameters record
    return region, info


def select_region_points(xyz, tiles, grid: CellGrid, region: np.ndarray, params: GroundGrowParams,
                         clip=None, inside=None) -> np.ndarray:  # fmt: skip
    """Full-resolution store indices of the points on the grown surface."""
    if not region.any():
        return np.empty(0, np.int64)
    ys, xs = np.nonzero(region)
    lo = np.array([grid.x0 + xs.min() * grid.cell, grid.y0 + ys.min() * grid.cell])
    hi = np.array([grid.x0 + (xs.max() + 1) * grid.cell, grid.y0 + (ys.max() + 1) * grid.cell])
    t = tiles
    cand = np.flatnonzero((t["x1"] >= lo[0]) & (t["x0"] <= hi[0]) & (t["y1"] >= lo[1]) & (t["y0"] <= hi[1]))
    if inside is not None:
        cand = inside.keep_tiles(t, cand)
    arr = np.asarray(xyz)
    surf = np.where(region, grid.surf, 0.0)
    parts = []
    for k in cand:
        a, b = int(t["start"][k]), int(t["start"][k] + t["count"][k])
        out = np.empty(b - a, np.bool_)
        _classify(arr, a, b, grid.x0, grid.y0, grid.cell, grid.nx, grid.ny, region, surf, params.surface_tol, out)
        idx = np.flatnonzero(out) + a
        if inside is not None and idx.size:
            idx = idx[inside.mask[idx]]
        if clip is not None and idx.size:
            q = arr[idx]
            idx = idx[np.all((q >= clip[0]) & (q <= clip[1]), axis=1)]
        parts.append(idx.astype(np.int64))
    return np.concatenate(parts) if parts else np.empty(0, np.int64)


def ground_grow(xyz, intensity, tiles, seed, params: GroundGrowParams = GroundGrowParams(), *, grid=None,
                clip=None, inside=None) -> GrowResult:  # fmt: skip
    """Run the whole tool. Pass ``grid`` from a previous run with the same seed area, cell size and clips to
    re-grow with new thresholds without re-reading the points. ``inside``: only these points (a PointMask)."""
    seed = np.asarray(seed, np.float64)
    if grid is None:
        grid = cell_grid(xyz, intensity, tiles, seed, params, clip, inside=inside)
    region, info = grow_cells(grid, seed, params)
    idx = select_region_points(xyz, tiles, grid, region, params, clip, inside)
    info["points"] = int(idx.size)
    info["params"] = asdict(params)
    return GrowResult(idx, region, grid, info)
