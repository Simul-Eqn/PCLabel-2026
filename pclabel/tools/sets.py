"""Set operations on sorted, unique int64 index arrays in one linear merge (numba).

numpy's union1d / setdiff1d / setxor1d sort their inputs again, which takes tens of seconds on the hundreds of
millions of indices a "through" selection can hold. These run in about a second for that size.
"""

from __future__ import annotations

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


def xor(a, b) -> np.ndarray:
    """In exactly one of ``a`` and ``b``."""
    if a.size == 0:
        return np.asarray(b, np.int64)
    if b.size == 0:
        return np.asarray(a, np.int64)
    return _op(a, b, True, True, False)
