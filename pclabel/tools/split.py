"""Split a point set in two by surface roughness (the user's request of 2026-10-20: "geometric segmentation").

1. **Features.** The set's points are binned into voxels (``voxel``, 3 cm, as in ``tools.normals``). From the voxels
   of the set within ``normal_radius``, each voxel gets the curvature (surface variation, 0 on a plane to 1/3 for
   scatter) and the roughness (the spread off the local plane, metres).
2. **Split.** Voxels whose feature (``feature``) is above ``threshold`` are rough, the others smooth; points follow
   their voxel. Voxels with too few neighbours for a feature count as smooth and unsure.
3. **Clean-up** (``clean``), so that a few rough voxels in the middle of smooth ground (and the other way round) do
   not stay:
   - majority: a voxel whose neighbours within ``radius`` (weighted by their points, itself included) are at least
     ``majority`` of the other group changes group; ``iterations`` rounds;
   - islands: a connected piece of one group (touching voxels) with fewer than ``min_island`` points joins the
     other group.
4. **Unsure:** points whose voxel changed group in the clean-up, or whose feature is within ``band`` (a fraction of
   the threshold) of it, are flagged for a look.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numba as nb
import numpy as np

from pclabel.tools.normals import BITS, EMPTY, _find, _hash, _normals, _rehash

FEATURES = {"curvature": "curvature (surface variation)", "roughness": "roughness (spread off the local plane)"}


@dataclass(frozen=True)
class SplitParams:
    feature: str = "curvature"  # "curvature" | "roughness"
    threshold: float = 0.05  # curvature, or metres of roughness
    voxel: float = 0.03  # m
    normal_radius: float = 0.09  # m
    clean: bool = True
    radius: float = 0.06  # m, the majority's neighbourhood
    majority: float = 0.7  # fraction of the neighbourhood's points in the other group that moves a voxel over
    iterations: int = 2
    min_island: int = 200  # points: smaller connected pieces of one group join the other
    band: float = 0.25  # unsure when the feature is within this fraction of the threshold


@dataclass
class SetFeatures:
    indices: np.ndarray  # (n,) the set's store indices, sorted
    slot: np.ndarray  # (n,) voxel slot of each point
    keys: np.ndarray  # hash table of voxel keys (see tools.normals)
    count: np.ndarray  # points of the set per slot
    centroid: np.ndarray
    curvature: np.ndarray
    rough: np.ndarray
    lo: np.ndarray
    voxel: float
    n_side: int
    key: tuple  # (voxel, normal_radius)

    def feature(self, name: str) -> np.ndarray:
        return self.curvature if name == "curvature" else self.rough

    def percentiles(self, name: str, q=(10, 50, 90)) -> list:
        """Percentiles of the feature over the set's points (voxels weighted by their points)."""
        f = self.feature(name)
        ok = (self.keys != EMPTY) & np.isfinite(f)
        order = np.argsort(f[ok])
        w = np.cumsum(self.count[ok][order]) / max(self.count[ok].sum(), 1)
        return [float(f[ok][order][min(np.searchsorted(w, x / 100), len(order) - 1)]) for x in q] if len(order) else []

    def otsu(self, name: str) -> float:
        """Otsu's threshold of the feature (on a log scale, voxels weighted by their points)."""
        f = self.feature(name)
        ok = (self.keys != EMPTY) & np.isfinite(f) & (f > 0)
        if ok.sum() < 2:
            return float("nan")
        x = np.log10(f[ok])
        hist, edges = np.histogram(x, bins=256, weights=self.count[ok].astype(float))
        mid = 0.5 * (edges[:-1] + edges[1:])
        w0 = np.cumsum(hist)
        w1 = w0[-1] - w0
        m0 = np.cumsum(hist * mid) / np.maximum(w0, 1e-12)
        m1 = (np.sum(hist * mid) - np.cumsum(hist * mid)) / np.maximum(w1, 1e-12)
        between = w0 * w1 * (m0 - m1) ** 2
        return float(10 ** edges[int(np.argmax(between[:-1])) + 1])


@nb.njit(nogil=True, cache=True)
def _insert_indices(xyz, idx, j0, lo, voxel, keys, sums, cnt, used, limit):
    mask = keys.shape[0] - 1
    for j in range(j0, idx.shape[0]):
        i = idx[j]
        x, y, z = xyz[i, 0], xyz[i, 1], xyz[i, 2]
        ix = np.int64(np.floor((x - lo[0]) / voxel))
        iy = np.int64(np.floor((y - lo[1]) / voxel))
        iz = np.int64(np.floor((z - lo[2]) / voxel))
        key = (ix << (2 * BITS)) | (iy << BITS) | iz
        h = _hash(key, mask)
        while keys[h] != EMPTY and keys[h] != key:
            h = (h + 1) & mask
        if keys[h] == EMPTY:
            if used >= limit:
                return j, used
            keys[h] = key
            used += 1
        sums[h, 0] += x
        sums[h, 1] += y
        sums[h, 2] += z
        cnt[h] += 1
    return idx.shape[0], used


@nb.njit(parallel=True, nogil=True, cache=True)
def _slots(xyz, idx, lo, voxel, keys, out):
    for j in nb.prange(idx.shape[0]):
        i = idx[j]
        ix = np.int64(np.floor((xyz[i, 0] - lo[0]) / voxel))
        iy = np.int64(np.floor((xyz[i, 1] - lo[1]) / voxel))
        iz = np.int64(np.floor((xyz[i, 2] - lo[2]) / voxel))
        out[j] = _find(keys, (ix << (2 * BITS)) | (iy << BITS) | iz)


def set_features(xyz, indices, voxel: float = 0.03, normal_radius: float = 0.09) -> SetFeatures:
    """Voxels of a point set (sorted store indices), with curvature and roughness."""
    idx = np.asarray(indices, np.int64)
    arr = np.asarray(xyz)
    q = np.asarray(arr[idx], np.float64)
    lo = np.floor(q.min(0) / voxel) * voxel - voxel
    n_side = int(np.ceil((q.max(0) - lo).max() / voxel)) + 2
    if n_side >= 1 << BITS:
        raise ValueError("the point set is too large for this voxel size")
    size = 1 << 16
    keys, sums, cnt = np.full(size, EMPTY, np.int64), np.zeros((size, 3)), np.zeros(size, np.int32)
    used, j = 0, 0
    while j < idx.size:
        j, used = _insert_indices(arr, idx, j, lo, voxel, keys, sums, cnt, used, size // 2)
        if j < idx.size:
            size *= 2
            nk, ns, nc = np.full(size, EMPTY, np.int64), np.zeros((size, 3)), np.zeros(size, np.int32)
            _rehash(keys, sums, cnt, nk, ns, nc)
            keys, sums, cnt = nk, ns, nc
    centroid = sums / np.maximum(cnt, 1)[:, None]
    normal = np.full((size, 3), np.nan)
    curvature, rough = np.full(size, np.nan), np.full(size, np.nan)
    _normals(keys, centroid, int(np.ceil(normal_radius / voxel)), normal_radius, normal, curvature, rough)
    slot = np.empty(idx.size, np.int64)
    _slots(arr, idx, lo, voxel, keys, slot)
    return SetFeatures(idx, slot, keys, cnt, centroid, curvature, rough, lo, voxel, n_side, (voxel, normal_radius))


@nb.njit(parallel=True, nogil=True, cache=True)
def _majority(keys, centroid, count, label, reach, radius, majority, out):
    mask = np.int64((1 << BITS) - 1)
    r2 = radius * radius
    for s in nb.prange(keys.shape[0]):
        k = keys[s]
        if k == EMPTY:
            continue
        ix, iy, iz = (k >> (2 * BITS)) & mask, (k >> BITS) & mask, k & mask
        same = 0.0
        other = 0.0
        c = centroid[s]
        for dx in range(-reach, reach + 1):
            for dy in range(-reach, reach + 1):
                for dz in range(-reach, reach + 1):
                    jx, jy, jz = ix + dx, iy + dy, iz + dz
                    if jx < 0 or jy < 0 or jz < 0:
                        continue
                    t = _find(keys, (jx << (2 * BITS)) | (jy << BITS) | jz)
                    if t < 0:
                        continue
                    q = centroid[t]
                    if (q[0] - c[0]) ** 2 + (q[1] - c[1]) ** 2 + (q[2] - c[2]) ** 2 > r2:
                        continue
                    if label[t] == label[s]:
                        same += count[t]
                    else:
                        other += count[t]
        out[s] = (not label[s]) if other >= majority * (same + other) and other > 0 else label[s]


@nb.njit(nogil=True, cache=True)
def _islands(keys, count, label, min_points, out):
    """Copy ``label`` to ``out``, flipping connected pieces (touching voxels, one label) with fewer points."""
    mask = np.int64((1 << BITS) - 1)
    n = keys.shape[0]
    seen = np.zeros(n, np.bool_)
    queue = np.empty(n, np.int64)
    for s0 in range(n):
        out[s0] = label[s0]
    for s0 in range(n):
        if keys[s0] == EMPTY or seen[s0]:
            continue
        head, tail, total = 0, 0, 0
        queue[tail] = s0
        tail += 1
        seen[s0] = True
        while head < tail:
            s = queue[head]
            head += 1
            total += count[s]
            k = keys[s]
            ix, iy, iz = (k >> (2 * BITS)) & mask, (k >> BITS) & mask, k & mask
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    for dz in range(-1, 2):
                        jx, jy, jz = ix + dx, iy + dy, iz + dz
                        if jx < 0 or jy < 0 or jz < 0:
                            continue
                        t = _find(keys, (jx << (2 * BITS)) | (jy << BITS) | jz)
                        if t >= 0 and not seen[t] and label[t] == label[s0]:
                            seen[t] = True
                            queue[tail] = t
                            tail += 1
        if total < min_points:
            for j in range(tail):
                out[queue[j]] = not label[s0]


def split_labels(feat: SetFeatures, p: SplitParams):
    """(rough (n,) bool per point, unsure (n,) bool per point, info)."""
    f = feat.feature(p.feature)
    occ = feat.keys != EMPTY
    known = occ & np.isfinite(f)
    raw = known & (f > p.threshold)
    label = raw.copy()
    if p.clean:
        reach = max(1, int(np.ceil(p.radius / feat.voxel)))
        for _ in range(int(p.iterations)):
            nxt = label.copy()
            _majority(feat.keys, feat.centroid, feat.count, label, reach, p.radius, p.majority, nxt)
            label = nxt
        if p.min_island > 0:
            out = np.empty_like(label)
            _islands(feat.keys, feat.count, label, int(p.min_island), out)
            label = out
    label &= occ
    flipped = occ & (label != raw)
    near = known & (np.abs(f - p.threshold) <= p.band * p.threshold)
    unsure_vox = flipped | near | (occ & ~np.isfinite(f))
    rough = label[feat.slot]
    unsure = unsure_vox[feat.slot]
    w = feat.count
    info = {
        "points": int(feat.indices.size),
        "rough": int(rough.sum()),
        "smooth": int((~rough).sum()),
        "changed_by_clean_up": int(w[flipped].sum()),
        "near_threshold": int(w[near].sum()),
        "no_feature": int(w[occ & ~np.isfinite(f)].sum()),
        "unsure": int(unsure.sum()),
        "voxels": int(occ.sum()),
        "params": asdict(p),
    }
    return rough, unsure, info
