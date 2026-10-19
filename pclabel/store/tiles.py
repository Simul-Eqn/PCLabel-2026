"""Spatial tiling (D-002, planning): a 2D quadtree on xy, with points randomly permuted inside each tile.

Any prefix of a tile is then a uniform random subsample, which is what the LOD draws. The quadtree is built from
a fine count histogram (cells of ``cell`` metres), so it costs one pass over the points whatever their number.
A node becomes a leaf when it holds at most ``max_points`` points or when it is a single cell.
"""

from __future__ import annotations

from dataclasses import dataclass

import numba as nb
import numpy as np

# tiles.npy. x0..y1, zmin, zmax: tight bounds of the tile's points (relative to the project offset);
# size: edge of the quadtree node (m).
TILE_DTYPE = np.dtype(
    [
        ("id", "<u4"),
        ("depth", "<u1"),
        ("x0", "<f8"), ("y0", "<f8"), ("x1", "<f8"), ("y1", "<f8"),
        ("zmin", "<f4"), ("zmax", "<f4"),
        ("size", "<f8"),
        ("start", "<u8"), ("count", "<u8"),
    ]
)  # fmt: skip


@dataclass(frozen=True)
class TilingParams:
    max_points: int = 2_000_000  # T
    cell: float = 0.25  # histogram cell and minimum tile size, metres
    seed: int = 0  # in-tile permutation


@nb.njit(parallel=True, nogil=True, cache=True)
def _cell_index(xyz, x0, y0, cell, nx, ny, out):
    for i in nb.prange(xyz.shape[0]):
        cx = int((xyz[i, 0] - x0) / cell)
        cy = int((xyz[i, 1] - y0) / cell)
        cx = min(max(cx, 0), nx - 1)
        cy = min(max(cy, 0), ny - 1)
        out[i] = cy * nx + cx


@nb.njit(nogil=True, cache=True)
def _bincount(idx, n):
    out = np.zeros(n, np.int64)
    for i in range(idx.shape[0]):
        out[idx[i]] += 1
    return out


@nb.njit(parallel=True, nogil=True, cache=True)
def _leaf_of(cell_idx, cell_leaf, out):
    for i in nb.prange(cell_idx.shape[0]):
        out[i] = cell_leaf[cell_idx[i]]


@nb.njit(nogil=True, cache=True)
def _scatter(leaf, starts, out):
    """Stable counting sort: out[pos] = source index, grouped by leaf."""
    pos = starts.copy()
    for i in range(leaf.shape[0]):
        t = leaf[i]
        out[pos[t]] = i
        pos[t] += 1


@nb.njit(cache=True)
def _splitmix(x):
    x = (x + np.uint64(0x9E3779B97F4A7C15))
    z = x
    z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return x, z ^ (z >> np.uint64(31))


@nb.njit(parallel=True, nogil=True, cache=True)
def _shuffle_tiles(order, starts, counts, seed):
    """Fisher-Yates inside each tile, with a per-tile counter-based RNG (deterministic for a seed)."""
    for t in nb.prange(starts.shape[0]):
        s = starts[t]
        n = counts[t]
        state = np.uint64(seed) * np.uint64(0x9E3779B97F4A7C15) + np.uint64(t)
        for k in range(n - 1, 0, -1):
            state, r = _splitmix(state)
            j = np.int64(r % np.uint64(k + 1))
            a = order[s + k]
            order[s + k] = order[s + j]
            order[s + j] = a


@nb.njit(parallel=True, nogil=True, cache=True)
def _tile_bounds(xyz, order, starts, counts, out):
    for t in nb.prange(starts.shape[0]):
        lo = np.full(3, np.inf)
        hi = np.full(3, -np.inf)
        for k in range(starts[t], starts[t] + counts[t]):
            i = order[k]
            for d in range(3):
                v = xyz[i, d]
                lo[d] = min(lo[d], v)
                hi[d] = max(hi[d], v)
        out[t, :3] = lo
        out[t, 3:] = hi


@nb.njit(parallel=True, nogil=True, cache=True)
def _xy_bounds(xyz):
    n = xyz.shape[0]
    nch = 256
    lo = np.full((nch, 2), np.inf)
    hi = np.full((nch, 2), -np.inf)
    step = (n + nch - 1) // nch
    for c in nb.prange(nch):
        for i in range(c * step, min(n, (c + 1) * step)):
            for d in range(2):
                v = xyz[i, d]
                lo[c, d] = min(lo[c, d], v)
                hi[c, d] = max(hi[c, d], v)
    out = np.empty(4)
    for d in range(2):
        out[d] = lo[:, d].min()
        out[2 + d] = hi[:, d].max()
    return out


def build_tiles(xyz: np.ndarray, params: TilingParams = TilingParams()) -> tuple[np.ndarray, np.ndarray]:
    """Tile points (float32 (n, 3), any order; a memmap is fine) into an xy quadtree.

    Returns ``(tiles, order)``: ``tiles`` is a TILE_DTYPE array; ``order`` (uint32 if n < 2**32, else uint64)
    maps store position -> input index, i.e. the store holds ``xyz[order]``. Tile ``t`` occupies store
    positions ``start .. start + count`` and is randomly permuted inside. Transient memory: about 12 bytes/point.
    """
    n = xyz.shape[0]
    if n == 0:
        raise ValueError("no points to tile")
    xyz = np.asarray(xyz)
    cell = float(params.cell)
    b = _xy_bounds(xyz)
    lo, hi = b[:2], b[2:]
    side = max(hi - lo) + cell
    levels = max(0, int(np.ceil(np.log2(side / cell))))
    g = 1 << levels  # root covers g x g cells
    x0, y0 = lo[0], lo[1]
    nx = int(np.floor((hi[0] - x0) / cell)) + 1
    ny = int(np.floor((hi[1] - y0) / cell)) + 1

    if nx * ny >= 2**31:
        raise ValueError(f"extent too large for a {cell} m histogram ({nx} x {ny} cells)")
    cell_idx = np.empty(n, np.int32)
    _cell_index(xyz, x0, y0, cell, nx, ny, cell_idx)
    hist = _bincount(cell_idx, nx * ny).reshape(ny, nx)
    sat = np.zeros((g + 1, g + 1), np.int64)  # summed-area table over the padded g x g grid
    sat[1 : ny + 1, 1 : nx + 1] = hist.cumsum(0).cumsum(1)
    sat[ny + 1 :, 1 : nx + 1] = sat[ny, 1 : nx + 1]
    sat[:, nx + 1 :] = sat[:, nx : nx + 1]

    def count(cx0, cy0, size):
        cx1, cy1 = cx0 + size, cy0 + size
        return sat[cy1, cx1] - sat[cy0, cx1] - sat[cy1, cx0] + sat[cy0, cx0]

    leaves = []  # (cx0, cy0, size, depth, count)
    stack = [(0, 0, g, 0)]
    while stack:
        cx0, cy0, size, depth = stack.pop()
        c = count(cx0, cy0, size)
        if c == 0:
            continue
        if c <= params.max_points or size == 1:
            leaves.append((cx0, cy0, size, depth, c))
            continue
        h = size // 2
        # push in reverse so tiles come out in Morton-like order (good locality for streaming)
        for ox, oy in ((1, 1), (0, 1), (1, 0), (0, 0)):
            stack.append((cx0 + ox * h, cy0 + oy * h, h, depth + 1))

    cell_leaf = np.full((ny, nx), -1, np.int32)
    for k, (cx0, cy0, size, _, _) in enumerate(leaves):
        cell_leaf[cy0 : min(cy0 + size, ny), cx0 : min(cx0 + size, nx)] = k
    leaf = np.empty(n, np.int32)
    _leaf_of(cell_idx, cell_leaf.ravel(), leaf)
    del cell_idx

    counts = np.array([c for *_, c in leaves], np.int64)
    starts = np.zeros_like(counts)
    np.cumsum(counts[:-1], out=starts[1:])
    order = np.empty(n, np.uint32 if n < 2**32 else np.uint64)
    _scatter(leaf, starts, order)
    del leaf
    _shuffle_tiles(order, starts, counts, params.seed)

    bounds = np.empty((len(leaves), 6))
    _tile_bounds(xyz, order, starts, counts, bounds)

    tiles = np.zeros(len(leaves), TILE_DTYPE)
    tiles["id"] = np.arange(len(leaves))
    tiles["depth"] = [d for _, _, _, d, _ in leaves]
    tiles["x0"], tiles["y0"], tiles["zmin"] = bounds[:, 0], bounds[:, 1], bounds[:, 2]
    tiles["x1"], tiles["y1"], tiles["zmax"] = bounds[:, 3], bounds[:, 4], bounds[:, 5]
    tiles["size"] = [size * cell for _, _, size, _, _ in leaves]
    tiles["start"], tiles["count"] = starts, counts
    return tiles, order
