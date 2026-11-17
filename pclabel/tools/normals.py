"""Grow select, geometric mode: region growing on surface normals, after PCL's region growing segmentation
(https://pointclouds.org/documentation/tutorials/region_growing_segmentation.html), on voxels around the click.

1. **Voxels.** Every point in a cube around the click is binned into ``voxel`` cells (3 cm; a hash table, so only
   occupied cells cost memory). A voxel keeps the centroid and the number of its points.
2. **Normals and curvature,** as PCL computes them: the centroids of the occupied voxels within ``normal_radius``
   give a covariance. Its smallest eigenvector is the normal, and lambda0 / (lambda0 + lambda1 + lambda2) the
   curvature (surface variation: 0 on a plane, 1/3 for scatter with no direction).
3. **Growth** from the clicked voxel, as in PCL. A neighbour (an occupied voxel within ``connect``) joins when its
   normal is within ``smoothness`` degrees of the current voxel's (the sign of a normal does not matter). It then
   carries the growth on only if its curvature is below ``curvature`` (PCL's seeds). So the region follows
   surfaces that bend slowly and stops at creases and edges (curb lips, wall corners) and on rough surfaces
   (vegetation). The growth stays inside the cube of half-size ``radius`` around the click.
4. **Points:** every point in a grown voxel.

The voxels and normals (the slow part) are built for 1.5 x ``radius`` around the click and reused for threshold
changes and for later clicks whose cube fits inside.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numba as nb
import numpy as np

from pclabel.store.tiles import tiles_meeting
from pclabel.tools import sets
from pclabel.tools.voxels import BITS, EMPTY, find, grown, hash_slot, local_normals
from pclabel.tools.selection import reach as filter_reach, restrict



@dataclass(frozen=True)
class NormalGrowParams:
    smoothness: float = 6.0  # degrees: largest angle between the normals of neighbouring voxels
    curvature: float = 0.05  # voxels at most this curved carry the growth on (0 plane .. 1/3 scatter)
    voxel: float = 0.03  # m
    normal_radius: float = 0.09  # m: voxels within this give a voxel's normal
    connect: float = 0.045  # m: voxels whose centres are this close are neighbours for the growth
    radius: float = 6.0  # m: half-size of the cube around the click the growth may cover


@dataclass
class VoxelNormals:
    lo: np.ndarray  # (3,) local corner of the cube the voxels were built for
    voxel: float
    n_side: int  # voxels along each side of the cube
    keys: np.ndarray  # hash table: (size,) int64 voxel keys, EMPTY where free
    centroid: np.ndarray  # (size, 3) float64, local
    count: np.ndarray  # (size,) int32, points per voxel
    normal: np.ndarray  # (size, 3) unit normals (NaN where too few neighbours)
    curvature: np.ndarray  # (size,) float64
    key: tuple  # what they were built with (see build_key)
    points: int = 0  # points binned

    def covers(self, seed, radius: float) -> bool:
        s = np.asarray(seed, np.float64)
        hi = self.lo + self.n_side * self.voxel
        return bool(np.all(self.lo <= s - radius) and np.all(s + radius <= hi))

    @property
    def occupied(self) -> int:
        return int((self.keys != EMPTY).sum())


@dataclass
class NormalGrowResult:
    indices: np.ndarray
    voxels: VoxelNormals
    info: dict


def build_key(params: NormalGrowParams, filters=None) -> tuple:
    f = filters.key() if filters is not None else (None, None)
    return (params.radius, params.voxel, params.normal_radius, *f)


@nb.njit(nogil=True, cache=True)
def _insert(xyz, i0, b, lo, voxel, n_side, clip_on, clo, chi, in_on, inside, keys, sums, cnt, used, limit):
    """Bin store points i0..b (only those in ``inside`` when ``in_on``) into the table; stop early (returning the
    next index) when ``limit`` slots are used."""
    mask = keys.shape[0] - 1
    for i in range(i0, b):
        if in_on and not inside[i]:
            continue
        x, y, z = xyz[i, 0], xyz[i, 1], xyz[i, 2]
        if clip_on and (x < clo[0] or x > chi[0] or y < clo[1] or y > chi[1] or z < clo[2] or z > chi[2]):
            continue
        ix = int(np.floor((x - lo[0]) / voxel))
        iy = int(np.floor((y - lo[1]) / voxel))
        iz = int(np.floor((z - lo[2]) / voxel))
        if ix < 0 or iy < 0 or iz < 0 or ix >= n_side or iy >= n_side or iz >= n_side:
            continue
        key = (np.int64(ix) << (2 * BITS)) | (np.int64(iy) << BITS) | np.int64(iz)
        h = hash_slot(key, mask)
        while keys[h] != EMPTY and keys[h] != key:
            h = (h + 1) & mask
        if keys[h] == EMPTY:
            if used >= limit:
                return i, used
            keys[h] = key
            used += 1
        sums[h, 0] += x
        sums[h, 1] += y
        sums[h, 2] += z
        cnt[h] += 1
    return b, used


@nb.njit(nogil=True, cache=True)
def _grow(keys, normal, curvature, seed, reach, lo_i, hi_i, cos_t, max_curv, region):
    """PCL region growing from voxel slot ``seed``: neighbours within ``reach`` voxels whose normal is within the
    angle join; those flatter than ``max_curv`` carry it on. Voxels outside lo_i..hi_i (voxel index box) are not
    entered. Returns (voxels grown, refused for the angle)."""
    mask = np.int64((1 << BITS) - 1)
    queue = np.empty(keys.shape[0], np.int64)
    head, tail = 0, 0
    region[seed] = True
    queue[tail] = seed
    tail += 1
    n_grown, n_angle = 1, 0
    while head < tail:
        s = queue[head]
        head += 1
        k = keys[s]
        ix, iy, iz = (k >> (2 * BITS)) & mask, (k >> BITS) & mask, k & mask
        n0 = normal[s]
        for dx in range(-reach, reach + 1):
            for dy in range(-reach, reach + 1):
                for dz in range(-reach, reach + 1):
                    jx, jy, jz = ix + dx, iy + dy, iz + dz
                    if jx < lo_i[0] or jy < lo_i[1] or jz < lo_i[2] or jx > hi_i[0] or jy > hi_i[1] or jz > hi_i[2]:
                        continue
                    t = find(keys, (jx << (2 * BITS)) | (jy << BITS) | jz)
                    if t < 0 or region[t]:
                        continue
                    n1 = normal[t]
                    if not np.isfinite(n1[0]):
                        continue
                    if abs(n0[0] * n1[0] + n0[1] * n1[1] + n0[2] * n1[2]) < cos_t:
                        n_angle += 1
                        continue
                    region[t] = True
                    n_grown += 1
                    if curvature[t] < max_curv:
                        queue[tail] = t
                        tail += 1
    return n_grown, n_angle


@nb.njit(parallel=True, nogil=True, cache=True)
def _classify(xyz, a, b, lo, voxel, n_side, keys, region, out):
    for k in nb.prange(b - a):
        i = a + k
        ix = int(np.floor((xyz[i, 0] - lo[0]) / voxel))
        iy = int(np.floor((xyz[i, 1] - lo[1]) / voxel))
        iz = int(np.floor((xyz[i, 2] - lo[2]) / voxel))
        ok = False
        if 0 <= ix < n_side and 0 <= iy < n_side and 0 <= iz < n_side:
            t = find(keys, (np.int64(ix) << (2 * BITS)) | (np.int64(iy) << BITS) | np.int64(iz))
            ok = t >= 0 and region[t]
        out[k] = ok


PICTURE_SCALE = 1.5  # the voxels' half-size, in reaches


def build_voxels(xyz, tiles, centre, params: NormalGrowParams, filters=None) -> VoxelNormals:
    """Voxels, centroids, normals and curvature in a cube of half-size PICTURE_SCALE x the reach around ``centre``,
    from the points ``filters`` reach (its clip box and point mask)."""
    clip, inside = filter_reach(filters)
    p = params
    half = PICTURE_SCALE * p.radius
    n_side = int(np.ceil(2 * half / p.voxel)) + 1
    if n_side >= 1 << BITS:
        raise ValueError("the reach is too large for the voxel size")
    centre = np.asarray(centre, np.float64)
    lo = np.floor((centre - half) / p.voxel) * p.voxel  # a fixed lattice
    hi = lo + n_side * p.voxel
    t = tiles
    cand = tiles_meeting(t, lo, hi, inside)
    in_on, in_mask = sets.mask_args(inside)
    size = 1 << 20
    keys = np.full(size, EMPTY, np.int64)
    sums = np.zeros((size, 3))
    cnt = np.zeros(size, np.int32)
    used = 0
    arr = np.asarray(xyz)
    clo, chi = (np.asarray(clip[0], np.float64), np.asarray(clip[1], np.float64)) if clip is not None else (
        np.zeros(3), np.zeros(3))  # fmt: skip
    for k in cand:
        a, b = int(t["start"][k]), int(t["start"][k] + t["count"][k])
        i = a
        while i < b:
            i, used = _insert(arr, i, b, lo, p.voxel, n_side, clip is not None, clo, chi, in_on, in_mask, keys, sums,
                              cnt, used, size // 2)  # fmt: skip
            if i < b:  # half full: double the table
                keys, sums, cnt = grown(keys, sums, cnt)
                size = keys.shape[0]
    centroid = sums / np.maximum(cnt, 1)[:, None]
    normal = np.full((size, 3), np.nan)
    curvature = np.full(size, np.nan)
    rough = np.full(size, np.nan)
    reach = int(np.ceil(p.normal_radius / p.voxel))
    local_normals(keys, centroid, reach, p.normal_radius, normal, curvature, rough)
    return VoxelNormals(lo, p.voxel, n_side, keys, centroid, cnt, normal, curvature, build_key(p, filters),
                        int(cnt.sum()))  # fmt: skip


def normal_grow(xyz, tiles, seed, params: NormalGrowParams = NormalGrowParams(), *, voxels=None,
                filters=None) -> NormalGrowResult:  # fmt: skip
    """Run the geometric grow from ``seed`` (local). Pass ``voxels`` from an earlier run to reuse them when they were
    built with the same settings and cover this click's reach."""
    p = params
    seed = np.asarray(seed, np.float64)
    clip, inside = filter_reach(filters)
    reused = voxels is not None and voxels.key == build_key(p, filters) and voxels.covers(seed, p.radius)
    if not reused:
        voxels = build_voxels(xyz, tiles, seed, p, filters)
    v = voxels
    vi = np.floor((seed - v.lo) / v.voxel).astype(np.int64)
    s = -1
    for r in range(0, 3):  # the clicked voxel, or the nearest occupied one within two voxels
        best = (np.inf, -1)
        for d in np.ndindex(2 * r + 1, 2 * r + 1, 2 * r + 1):
            j = vi + np.array(d) - r
            if np.any(j < 0):
                continue
            t = find(v.keys, (np.int64(j[0]) << (2 * BITS)) | (np.int64(j[1]) << BITS) | np.int64(j[2]))
            if t >= 0 and np.isfinite(v.normal[t, 0]):
                dist = float(np.linalg.norm(v.centroid[t] - seed))
                best = min(best, (dist, int(t)))
        if best[1] >= 0:
            s = best[1]
            break
    if s < 0:
        raise ValueError("no surface at the click")
    reach_i = int(np.round(p.radius / v.voxel))
    lo_i = np.maximum(vi - reach_i, 0)
    hi_i = np.minimum(vi + reach_i, v.n_side - 1)
    region = np.zeros(v.keys.shape[0], np.bool_)
    connect = max(1, int(np.floor(p.connect / v.voxel + 0.5)))
    n_grown, n_angle = _grow(v.keys, v.normal, v.curvature, s, connect, lo_i, hi_i,
                             float(np.cos(np.radians(p.smoothness))), p.curvature, region)  # fmt: skip
    lo_w = v.lo + lo_i * v.voxel
    hi_w = v.lo + (hi_i + 1) * v.voxel
    t = tiles
    cand = tiles_meeting(t, lo_w, hi_w, inside)
    arr = np.asarray(xyz)
    parts = []
    for k in cand:
        a, b = int(t["start"][k]), int(t["start"][k] + t["count"][k])
        out = np.empty(b - a, np.bool_)
        _classify(arr, a, b, v.lo, v.voxel, v.n_side, v.keys, region, out)
        idx = np.flatnonzero(out).astype(np.int64) + a
        idx = restrict(arr, idx, clip, inside)
        parts.append(idx)
    idx = np.concatenate(parts) if parts else np.empty(0, np.int64)
    reached = bool(np.any(region & _on_box_edge(v, lo_i, hi_i)))
    info = {
        "voxels_grown": int(n_grown),
        "refused_by_angle": int(n_angle),
        "seed_normal": [float(x) for x in v.normal[s]],
        "seed_curvature": float(v.curvature[s]),
        "voxels_built": v.occupied,
        "voxels_reused": bool(reused),
        "voxel_points": v.points,
        "points": int(idx.size),
        "reached_edge": reached,
        "params": asdict(p),
    }
    return NormalGrowResult(idx, v, info)


def _on_box_edge(v: VoxelNormals, lo_i, hi_i) -> np.ndarray:
    """Which table slots hold a voxel on the faces of the reach box."""
    mask = np.int64((1 << BITS) - 1)
    k = v.keys
    occ = k != EMPTY
    ix, iy, iz = (k >> (2 * BITS)) & mask, (k >> BITS) & mask, k & mask
    edge = (ix == lo_i[0]) | (ix == hi_i[0]) | (iy == lo_i[1]) | (iy == hi_i[1]) | (iz == lo_i[2]) | (iz == hi_i[2])
    return occ & edge
