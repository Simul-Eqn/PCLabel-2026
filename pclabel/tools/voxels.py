"""The voxel hash table shared by the geometric grow, pieces and split (numba; no Qt, no GL).

A voxel is keyed by its integer coordinates packed into one int64 (``BITS`` bits each). The table is an array of
keys, a power of two long, with open addressing (linear probing from ``hash_slot``); ``EMPTY`` marks a free slot.
Callers keep any per-voxel data (sums, counts) in arrays of the same length, indexed by slot, and double the table
with ``grown`` when it is half full. Each tool inserts with its own probe loop (the same few lines): a shared
``claim`` returning (slot, used) per point cost 50 % on pieces and 3-8 % on the geometric grow on streets 1.
"""

from __future__ import annotations

import numba as nb
import numpy as np

BITS = 21  # bits per axis in a key: up to 2,097,152 voxels along each
EMPTY = np.int64(-1)


@nb.njit(nogil=True, cache=True, inline="always")
def hash_slot(key, mask):
    h = np.uint64(key) * np.uint64(0x9E3779B97F4A7C15)
    return np.int64((h >> np.uint64(29)) & np.uint64(mask))


@nb.njit(nogil=True, cache=True)
def find(keys, key):
    """The slot holding ``key``, or -1."""
    mask = keys.shape[0] - 1
    h = hash_slot(key, mask)
    while True:
        k = keys[h]
        if k == key:
            return h
        if k == EMPTY:
            return -1
        h = (h + 1) & mask


@nb.njit(nogil=True, cache=True)
def rehash(keys, nkeys, moved):
    """Move every key into the larger table ``nkeys`` (all EMPTY); ``moved[old slot]`` = its new slot (-1: none).
    The caller moves its per-slot data the same way."""
    mask = nkeys.shape[0] - 1
    for s in range(keys.shape[0]):
        k = keys[s]
        moved[s] = -1
        if k == EMPTY:
            continue
        h = hash_slot(k, mask)
        while nkeys[h] != EMPTY:
            h = (h + 1) & mask
        nkeys[h] = k
        moved[s] = h


def grown(keys, *data):
    """A table twice the size, with the keys and each per-slot array of ``data`` moved into it:
    (new keys, *new data)."""
    nkeys = np.full(keys.shape[0] * 2, EMPTY, np.int64)
    moved = np.empty(keys.shape[0], np.int64)
    rehash(keys, nkeys, moved)
    occ = moved >= 0
    out = []
    for d in data:
        nd = np.zeros((nkeys.shape[0], *d.shape[1:]), d.dtype)
        nd[moved[occ]] = d[occ]
        out.append(nd)
    return (nkeys, *out)


@nb.njit(parallel=True, nogil=True, cache=True)
def local_normals(keys, centroid, reach, radius, normal, curvature, rough):
    """PCA normals, surface variation and roughness (the spread off the local plane, sqrt of the smallest
    eigenvalue, metres) of every voxel from the voxels within ``radius`` (``reach`` voxels)."""
    mask = np.int64((1 << BITS) - 1)
    r2 = radius * radius
    for s in nb.prange(keys.shape[0]):
        k = keys[s]
        if k == EMPTY:
            continue
        ix, iy, iz = (k >> (2 * BITS)) & mask, (k >> BITS) & mask, k & mask
        c = centroid[s]
        m = 0
        mean = np.zeros(3)
        cov = np.zeros((3, 3))
        for dx in range(-reach, reach + 1):
            for dy in range(-reach, reach + 1):
                for dz in range(-reach, reach + 1):
                    jx, jy, jz = ix + dx, iy + dy, iz + dz
                    if jx < 0 or jy < 0 or jz < 0:
                        continue
                    t = find(keys, (jx << (2 * BITS)) | (jy << BITS) | jz)
                    if t < 0:
                        continue
                    q = centroid[t]
                    d0, d1, d2 = q[0] - c[0], q[1] - c[1], q[2] - c[2]
                    if d0 * d0 + d1 * d1 + d2 * d2 > r2:
                        continue
                    m += 1
                    for a in range(3):
                        mean[a] += q[a]
                        for b in range(3):
                            cov[a, b] += q[a] * q[b]
        if m < 3:
            normal[s, 0] = np.nan
            curvature[s] = np.nan
            rough[s] = np.nan
            continue
        mean /= m
        for a in range(3):
            for b in range(3):
                cov[a, b] = cov[a, b] / m - mean[a] * mean[b]
        w, v = np.linalg.eigh(cov)
        normal[s] = v[:, 0]
        tot = w[0] + w[1] + w[2]
        curvature[s] = max(w[0], 0.0) / tot if tot > 0 else 0.0
        rough[s] = np.sqrt(max(w[0], 0.0))
