"""Set operations on sorted, unique int64 index arrays in one linear merge (numba).

numpy's union1d / setdiff1d / setxor1d sort their inputs again, which takes tens of seconds on the hundreds of
millions of indices a "through" selection can hold. These run in about a second for that size.
"""

from __future__ import annotations

import itertools

import numba as nb
import numpy as np


@nb.njit(nogil=True, cache=True)
def _merge(a, b, keep_a_only, keep_b_only, keep_both, out):
    i = j = k = 0
    na, nb_ = a.shape[0], b.shape[0]
    while i < na and j < nb_:
        x, y = a[i], b[j]
        if x < y:
            if keep_a_only:
                out[k] = x
                k += 1
            i += 1
        elif y < x:
            if keep_b_only:
                out[k] = y
                k += 1
            j += 1
        else:
            if keep_both:
                out[k] = x
                k += 1
            i += 1
            j += 1
    if keep_a_only:
        while i < na:
            out[k] = a[i]
            k += 1
            i += 1
    if keep_b_only:
        while j < nb_:
            out[k] = b[j]
            k += 1
            j += 1
    return k


def _op(a, b, a_only, b_only, both):
    a = np.asarray(a, np.int64)
    b = np.asarray(b, np.int64)
    out = np.empty(a.size + b.size, np.int64)
    k = _merge(a, b, a_only, b_only, both, out)
    return out[:k].copy() if k < out.size // 2 else out[:k]


def union(a, b) -> np.ndarray:
    if a.size == 0:
        return np.asarray(b, np.int64)
    if b.size == 0:
        return np.asarray(a, np.int64)
    return _op(a, b, True, True, True)


def union_all(arrays) -> np.ndarray:
    """Union of several sorted unique arrays, merged pairwise (smallest first)."""
    arrays = sorted((np.asarray(a, np.int64) for a in arrays), key=len)
    if not arrays:
        return np.empty(0, np.int64)
    out = arrays[0]
    for a in arrays[1:]:
        out = union(out, a)
    return out


def difference(a, b) -> np.ndarray:
    """``a`` without ``b``."""
    if a.size == 0 or b.size == 0:
        return np.asarray(a, np.int64)
    return _op(a, b, True, False, False)


def intersection(a, b) -> np.ndarray:
    """In both ``a`` and ``b``."""
    if a.size == 0 or b.size == 0:
        return np.empty(0, np.int64)
    return _op(a, b, False, False, True)


@nb.njit(parallel=True, nogil=True, cache=True)
def _scatter(idx, mask):
    for k in nb.prange(idx.shape[0]):
        mask[idx[k]] = True


_tokens = itertools.count(1)
_NO_MASK = np.zeros(1, np.bool_)


class PointMask:
    """A set of store points (e.g. the clip to a point set) as a dense mask, one byte per store point, for kernels
    that test every point they read. ``token`` tells masks apart in cache keys (unlike ``id``, never reused)."""

    def __init__(self, indices, n: int, label: str = ""):
        self.indices = np.asarray(indices, np.int64)
        self.mask = np.zeros(n, np.bool_)
        if self.indices.size:
            _scatter(self.indices, self.mask)
        self.label = label
        self.token = next(_tokens)

    def tile_counts(self, tiles) -> np.ndarray:
        """How many of the set's points each tile holds (tiles are consecutive store ranges)."""
        s = np.asarray(tiles["start"], np.int64)
        e = s + np.asarray(tiles["count"], np.int64)
        return np.searchsorted(self.indices, e) - np.searchsorted(self.indices, s)

    def keep_tiles(self, tiles, cand) -> np.ndarray:
        """The candidate tiles that hold any of the set's points."""
        cand = np.asarray(cand)
        return cand[self.tile_counts(tiles)[cand] > 0] if cand.size else cand


def mask_args(inside: PointMask | None):
    """(on, mask) for kernels: a PointMask's dense mask, or a placeholder when there is none."""
    return (False, _NO_MASK) if inside is None else (True, inside.mask)


def xor(a, b) -> np.ndarray:
    """In exactly one of ``a`` and ``b``."""
    if a.size == 0:
        return np.asarray(b, np.int64)
    if b.size == 0:
        return np.asarray(a, np.int64)
    return _op(a, b, True, True, False)
