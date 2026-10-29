"""The connected pieces of a selection (the user's request of 2026-10-21: a 3D select gives an object plus a few
random points elsewhere; keep only what is the same object).

The selected points are binned into voxels of ``gap`` (10 cm). Occupied voxels that touch (26 neighbours) belong to
the same piece, so points closer than ``gap`` always join and points up to about 3.5 x ``gap`` apart may. Pieces are
numbered by size: piece 0 is the largest. Then:
- keep the piece that was clicked (and add or take away more pieces with Shift / Ctrl);
- keep the largest piece;
- drop the pieces with fewer than ``min_points`` points (the random points).

Only the selection's points are looked at: a gap between two parts of an object that the selection left out splits
it, as it would for the eye.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numba as nb
import numpy as np

from pclabel.tools.normals import BITS, EMPTY, _find, _hash


@dataclass(frozen=True)
class PieceParams:
    gap: float = 0.10  # m, the voxel size: selected points in touching voxels are one piece
    min_points: int = 200  # "drop small pieces": pieces with fewer points go


@dataclass
class Pieces:
    """The pieces of one selection (``source``, kept by identity so a cache can tell whether it still applies)."""

    source: np.ndarray  # the selection's sorted store indices
    piece: np.ndarray  # int32 per point of the selection: its piece (0 = the largest)
    sizes: np.ndarray  # int64 points per piece, largest first
    keys: np.ndarray  # the voxel hash table
    slot_piece: np.ndarray  # int32 per table slot: the piece of that voxel (-1 if empty)
    lo: np.ndarray  # local corner of voxel (0, 0, 0)
    voxel: float
    n_side: int
    params: PieceParams

    @property
    def count(self) -> int:
        return int(self.sizes.size)

    def points(self, ids) -> np.ndarray:
        """Sorted store indices of the points in these pieces."""
        ids = np.asarray(sorted({int(i) for i in ids} & set(range(self.count))), np.int32)
        if ids.size == 0:
            return np.empty(0, np.int64)
        keep = np.zeros(self.count, bool)
        keep[ids] = True
        return self.source[keep[self.piece]]

    def small(self, min_points: int | None = None) -> np.ndarray:
        """Ids of the pieces with fewer than ``min_points`` points."""
        m = self.params.min_points if min_points is None else min_points
        return np.flatnonzero(self.sizes < m)

    def at(self, p, reach: float = 0.3) -> int:
        """The piece nearest to local point ``p`` (within ``reach`` metres), or -1."""
        r = max(1, int(np.ceil(reach / self.voxel)))
        return int(_nearest_piece(self.keys, self.slot_piece, np.asarray(p, np.float64), self.lo, self.voxel,
                                  self.n_side, r))  # fmt: skip

    def summary(self, min_points: int | None = None) -> dict:
        small = self.small(min_points)
        return {"pieces": self.count, "largest": [int(s) for s in self.sizes[:5]], "small_pieces": int(small.size),
                "small_points": int(self.sizes[small].sum()), "points": int(self.source.size), "params": asdict(self.params)}  # fmt: skip


@nb.njit(parallel=True, nogil=True, cache=True)
def _extent(xyz, idx, n_chunks, lo, hi):
    step = (idx.shape[0] + n_chunks - 1) // n_chunks
    for c in nb.prange(n_chunks):
        for k in range(c * step, min(idx.shape[0], (c + 1) * step)):
            i = idx[k]
            for a in range(3):
                v = xyz[i, a]
                if v < lo[c, a]:
                    lo[c, a] = v
                if v > hi[c, a]:
                    hi[c, a] = v


@nb.njit(parallel=True, nogil=True, cache=True)
def _keys(xyz, idx, lo, voxel, out):
    for k in nb.prange(idx.shape[0]):
        i = idx[k]
        ix = np.int64(np.floor((xyz[i, 0] - lo[0]) / voxel))
        iy = np.int64(np.floor((xyz[i, 1] - lo[1]) / voxel))
        iz = np.int64(np.floor((xyz[i, 2] - lo[2]) / voxel))
        out[k] = (ix << (2 * BITS)) | (iy << BITS) | iz


@nb.njit(nogil=True, cache=True)
def _insert_keys(pkeys, i0, keys, used, limit):
    """Put the point keys from i0 on into the table; stop (returning the next index) when ``limit`` slots are used."""
    mask = keys.shape[0] - 1
    for k in range(i0, pkeys.shape[0]):
        key = pkeys[k]
        h = _hash(key, mask)
        while keys[h] != EMPTY and keys[h] != key:
            h = (h + 1) & mask
        if keys[h] == EMPTY:
            if used >= limit:
                return k, used
            keys[h] = key
            used += 1
    return pkeys.shape[0], used


@nb.njit(nogil=True, cache=True)
def _rehash_keys(keys, nkeys):
    mask = nkeys.shape[0] - 1
    for s in range(keys.shape[0]):
        k = keys[s]
        if k == EMPTY:
            continue
        h = _hash(k, mask)
        while nkeys[h] != EMPTY:
            h = (h + 1) & mask
        nkeys[h] = k


@nb.njit(parallel=True, nogil=True, cache=True)
def _slots(keys, pkeys, out):
    for k in nb.prange(pkeys.shape[0]):
        out[k] = _find(keys, pkeys[k])


@nb.njit(nogil=True, cache=True)
def _root(parent, a):
    while parent[a] != a:
        parent[a] = parent[parent[a]]
        a = parent[a]
    return a


@nb.njit(nogil=True, cache=True)
def _union_neighbours(keys, n_side, parent):
    """Join every occupied voxel with its occupied neighbours (13 forward offsets cover all 26 pairs once)."""
    m = np.int64((1 << BITS) - 1)
    for s in range(keys.shape[0]):
        k = keys[s]
        if k == EMPTY:
            continue
        ix, iy, iz = k >> (2 * BITS), (k >> BITS) & m, k & m
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                for dz in range(-1, 2):
                    if not (dx > 0 or (dx == 0 and (dy > 0 or (dy == 0 and dz > 0)))):  # forward half only
                        continue
                    jx, jy, jz = ix + dx, iy + dy, iz + dz
                    if jx < 0 or jy < 0 or jz < 0 or jx >= n_side or jy >= n_side or jz >= n_side:
                        continue
                    t = _find(keys, (jx << (2 * BITS)) | (jy << BITS) | jz)
                    if t >= 0:
                        a, b = _root(parent, s), _root(parent, t)
                        if a != b:
                            if a < b:
                                parent[b] = a
                            else:
                                parent[a] = b


@nb.njit(nogil=True, cache=True)
def _roots(parent, keys, out):
    for s in range(keys.shape[0]):
        out[s] = _root(parent, s) if keys[s] != EMPTY else -1


@nb.njit(nogil=True, cache=True)
def _nearest_piece(keys, slot_piece, p, lo, voxel, n_side, r):
    cx = int(np.floor((p[0] - lo[0]) / voxel))
    cy = int(np.floor((p[1] - lo[1]) / voxel))
    cz = int(np.floor((p[2] - lo[2]) / voxel))
    best, best_d = -1, np.inf
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            for dz in range(-r, r + 1):
                jx, jy, jz = cx + dx, cy + dy, cz + dz
                if jx < 0 or jy < 0 or jz < 0 or jx >= n_side or jy >= n_side or jz >= n_side:
                    continue
                d = dx * dx + dy * dy + dz * dz
                if d >= best_d or d > r * r:
                    continue
                t = _find(keys, (np.int64(jx) << (2 * BITS)) | (np.int64(jy) << BITS) | np.int64(jz))
                if t >= 0:
                    best, best_d = slot_piece[t], d
    return best


def find_pieces(xyz, selection, params: PieceParams = PieceParams()) -> Pieces:
    """The connected pieces of a selection (sorted store indices)."""
    sel = selection  # kept as given: its identity tells callers which selection these pieces are of
    idx = np.asarray(sel, np.int64)
    arr = np.asarray(xyz)
    v = float(params.gap)
    if idx.size == 0:
        return Pieces(sel, np.empty(0, np.int32), np.empty(0, np.int64), np.full(2, EMPTY, np.int64),
                      np.full(2, -1, np.int32), np.zeros(3), v, 1, params)  # fmt: skip
    lo_c, hi_c = np.full((64, 3), np.inf), np.full((64, 3), -np.inf)
    _extent(arr, idx, 64, lo_c, hi_c)
    lo = np.floor(lo_c.min(0) / v) * v - v
    n_side = int(np.max(np.ceil((hi_c.max(0) - lo) / v))) + 2
    if n_side >= 1 << BITS:
        raise ValueError(f"the selection is too large for a {v * 100:g} cm gap: make the gap larger")
    pkeys = np.empty(idx.size, np.int64)
    _keys(arr, idx, lo, v, pkeys)
    size = 1 << 20
    keys = np.full(size, EMPTY, np.int64)
    used, i = 0, 0
    while i < pkeys.size:
        i, used = _insert_keys(pkeys, i, keys, used, size // 2)
        if i < pkeys.size:  # half full: double the table
            size *= 2
            nk = np.full(size, EMPTY, np.int64)
            _rehash_keys(keys, nk)
            keys = nk
    slots = np.empty(idx.size, np.int64)
    _slots(keys, pkeys, slots)
    parent = np.arange(size, dtype=np.int64)
    _union_neighbours(keys, n_side, parent)
    root = np.empty(size, np.int64)
    _roots(parent, keys, root)
    occupied = np.flatnonzero(root >= 0)
    uniq, comp = np.unique(root[occupied], return_inverse=True)  # one per piece (voxels: far fewer than points)
    slot_comp = np.full(size, -1, np.int64)
    slot_comp[occupied] = comp
    point_comp = slot_comp[slots]
    sizes = np.bincount(point_comp, minlength=uniq.size)
    order = np.argsort(-sizes, kind="stable")  # piece 0 is the largest
    rank = np.empty_like(order)
    rank[order] = np.arange(order.size)
    slot_piece = np.where(slot_comp >= 0, rank[np.maximum(slot_comp, 0)], -1).astype(np.int32)
    return Pieces(sel, rank[point_comp].astype(np.int32), sizes[order].astype(np.int64), keys, slot_piece, lo, v,
                  n_side, params)  # fmt: skip
