"""Screen-space selection on full-resolution points (D-005).

A shape drawn on screen is rasterised once to a mask at viewport resolution. Every point (not only the drawn
LOD prefix) is projected with the camera matrix and kept if its pixel is in the mask and, optionally, it passes a
depth test against a copy of the depth buffer. Pixel convention: origin at the top-left, x right, y down; pixel
(i, j) covers [i, i+1) x [j, j+1) and is inside a polygon when its centre is (even-odd rule).
"""

from __future__ import annotations

from dataclasses import dataclass

import numba as nb
import numpy as np


@nb.njit(nogil=True, cache=True)
def rasterize_polygon(poly, width, height):
    """Even-odd scanline fill of a closed polygon (m, 2) in pixel coordinates -> uint8 mask (height, width)."""
    mask = np.zeros((height, width), np.uint8)
    m = poly.shape[0]
    xs = np.empty(m, np.float64)
    for j in range(height):
        yc = j + 0.5
        k = 0
        for e in range(m):
            x0 = poly[e, 0]
            y0 = poly[e, 1]
            x1 = poly[(e + 1) % m, 0]
            y1 = poly[(e + 1) % m, 1]
            if (y0 <= yc < y1) or (y1 <= yc < y0):
                xs[k] = x0 + (yc - y0) * (x1 - x0) / (y1 - y0)
                k += 1
        if k < 2:
            continue
        xk = np.sort(xs[:k])
        for p in range(0, k - 1, 2):
            i0 = max(int(np.ceil(xk[p] - 0.5)), 0)
            i1 = min(int(np.ceil(xk[p + 1] - 0.5)), width)
            for i in range(i0, i1):
                mask[j, i] = 1
    return mask


@dataclass(frozen=True)
class DepthTest:
    """Keep a point only if it is not hidden: eye depth <= buffer eye depth + tol + tol_per_m * eye depth."""

    depth: np.ndarray  # float32 (H, W), window-space depth in [0, 1], row 0 at the top
    near: float
    far: float
    ortho: bool = False
    tol: float = 0.03
    tol_per_m: float = 0.002


@nb.njit(parallel=True, nogil=True, cache=True, fastmath=True)
def _select(xyz, M, mask, depth, near, far, ortho, tol, tol_k, chunk, hits, counts):
    n = xyz.shape[0]
    H, W = mask.shape
    use_depth = depth.shape[0] > 0
    n_chunks = (n + chunk - 1) // chunk
    fw = np.float32(0.5 * W)
    fh = np.float32(0.5 * H)
    for c in nb.prange(n_chunks):
        s = c * chunk
        e = min(n, s + chunk)
        k = s
        for i in range(s, e):
            x = xyz[i, 0]
            y = xyz[i, 1]
            z = xyz[i, 2]
            cw = M[3, 0] * x + M[3, 1] * y + M[3, 2] * z + M[3, 3]
            if cw <= 0:
                continue
            cx = M[0, 0] * x + M[0, 1] * y + M[0, 2] * z + M[0, 3]
            cy = M[1, 0] * x + M[1, 1] * y + M[1, 2] * z + M[1, 3]
            if cx < -cw or cx > cw or cy < -cw or cy > cw:
                continue
            inv = np.float32(1.0) / cw
            px = int((cx * inv + 1) * fw)
            py = int((1 - cy * inv) * fh)
            if px >= W:
                px = W - 1
            if py >= H:
                py = H - 1
            if mask[py, px] == 0:
                continue
            if use_depth:
                d = depth[py, px]
                if ortho:
                    cz = M[2, 0] * x + M[2, 1] * y + M[2, 2] * z + M[2, 3]
                    ez = near + (cz * inv + 1) * 0.5 * (far - near)
                    eb = near + d * (far - near)
                else:
                    ez = cw  # perspective: w is the eye-space depth
                    eb = 2 * near * far / (far + near - (2 * d - 1) * (far - near))
                if ez > eb + tol + tol_k * ez:
                    continue
            hits[k] = i
            k += 1
        counts[c] = k - s


@nb.njit(parallel=True, nogil=True, cache=True)
def _compact(hits, counts, starts, chunk, out):
    for c in nb.prange(counts.shape[0]):
        s = c * chunk
        o = starts[c]
        for j in range(counts[c]):
            out[o + j] = hits[s + j]


def select_in_mask(
    xyz: np.ndarray,
    mvp: np.ndarray,
    mask: np.ndarray,
    depth: DepthTest | None = None,
    *,
    chunk: int = 1 << 16,
) -> np.ndarray:
    """Indices (sorted, uint32 or uint64) of the points of ``xyz`` (float32 (n, 3)) inside ``mask``.

    ``mvp`` is the 4x4 clip-from-model matrix (OpenGL convention, column vectors).
    """
    n = xyz.shape[0]
    idt = np.uint32 if n < 2**32 else np.uint64
    hits = np.empty(n, idt)
    n_chunks = (n + chunk - 1) // chunk
    counts = np.empty(n_chunks, np.int64)
    M = np.ascontiguousarray(mvp, dtype=np.float32)
    if depth is None:
        d, near, far, ortho, tol, tol_k = np.empty((0, 0), np.float32), 0.0, 1.0, False, 0.0, 0.0
    else:
        d, near, far = np.ascontiguousarray(depth.depth, np.float32), depth.near, depth.far
        ortho, tol, tol_k = depth.ortho, depth.tol, depth.tol_per_m
    _select(xyz, M, mask, d, near, far, ortho, tol, tol_k, chunk, hits, counts)
    starts = np.zeros(n_chunks, np.int64)
    np.cumsum(counts[:-1], out=starts[1:])
    out = np.empty(int(counts.sum()), idt)
    _compact(hits, counts, starts, chunk, out)
    return out


def project_boxes(lo: np.ndarray, hi: np.ndarray, mvp: np.ndarray, viewport):
    """Projected pixel rectangles of boxes (lo, hi: (n, 3)), clipped to the viewport: (x0, y0, x1, y1, behind).

    ``behind`` marks boxes that reach behind the eye (their rectangle is then the whole viewport).
    """
    w, h = viewport
    n = lo.shape[0]
    corners = np.empty((n, 8, 4))
    for k in range(8):
        corners[:, k, 0] = np.where(k & 1, hi[:, 0], lo[:, 0])
        corners[:, k, 1] = np.where(k & 2, hi[:, 1], lo[:, 1])
        corners[:, k, 2] = np.where(k & 4, hi[:, 2], lo[:, 2])
    corners[:, :, 3] = 1.0
    clip = corners @ mvp.T
    cw = clip[:, :, 3]
    behind = (cw <= 1e-6).any(1)
    cw = np.where(cw <= 1e-6, 1e-6, cw)
    x = (clip[:, :, 0] / cw + 1) * 0.5 * w
    y = (1 - clip[:, :, 1] / cw) * 0.5 * h
    x0, x1 = np.clip(x.min(1), 0, w), np.clip(x.max(1), 0, w)
    y0, y1 = np.clip(y.min(1), 0, h), np.clip(y.max(1), 0, h)
    x0, y0 = np.where(behind, 0, x0), np.where(behind, 0, y0)
    x1, y1 = np.where(behind, w, x1), np.where(behind, h, y1)
    return x0, y0, x1, y1, behind


@dataclass(frozen=True)
class SelectFilters:
    """What is hidden on screen is not selected: clip box, clip volume and height slice, in local (store)
    coordinates."""

    clip_min: tuple | None = None
    clip_max: tuple | None = None
    band_normal: tuple | None = None  # height axis
    band_origin: tuple | None = None  # local point where h = 0
    band: tuple | None = None  # (a, b) in metres
    volume: object = None  # tools.volume.VolumeSpec: only points inside it

    def apply(self, xyz: np.ndarray, idx: np.ndarray) -> np.ndarray:
        if idx.size == 0 or (self.clip_min is None and self.band is None and self.volume is None):
            return idx
        q = np.asarray(xyz[idx], np.float64)
        keep = np.ones(len(idx), bool)
        if self.clip_min is not None:
            keep &= np.all((q >= np.asarray(self.clip_min)) & (q <= np.asarray(self.clip_max)), axis=1)
        if self.band is not None:
            hh = (q - np.asarray(self.band_origin)) @ np.asarray(self.band_normal)
            keep &= (hh >= self.band[0]) & (hh <= self.band[1])
        if self.volume is not None:
            keep &= self.volume.contains(q)
        return idx[keep]


def select_visible(
    xyz: np.ndarray,
    tiles: np.ndarray,
    mvp: np.ndarray,
    viewport,
    polygon_px: np.ndarray,
    depth: DepthTest | None = None,
    filters: SelectFilters | None = None,
    dilate_px: int = 2,
) -> np.ndarray:
    """Store indices (sorted, int64) of every point inside a screen polygon (SPEC §4), at full resolution.

    Only tiles whose projected box meets the polygon's bounding box are scanned. With ``depth`` only points not
    hidden behind the drawn surface are kept; the depth buffer is min-filtered over ``dilate_px`` pixels first,
    so gaps between drawn (LOD) points do not let hidden surfaces through.
    """
    w, h = int(viewport[0]), int(viewport[1])
    poly = np.asarray(polygon_px, np.float64)
    mask = rasterize_polygon(poly, w, h)
    if not mask.any():
        return np.empty(0, np.int64)
    if depth is not None and dilate_px > 0:
        from scipy.ndimage import minimum_filter

        depth = DepthTest(minimum_filter(depth.depth, size=2 * dilate_px + 1), depth.near, depth.far, depth.ortho,
                          depth.tol, depth.tol_per_m)  # fmt: skip
    lo = np.stack([tiles["x0"], tiles["y0"], tiles["zmin"]], 1).astype(np.float64)
    hi = np.stack([tiles["x1"], tiles["y1"], tiles["zmax"]], 1).astype(np.float64)
    x0, y0, x1, y1, _ = project_boxes(lo, hi, mvp, (w, h))
    bx0, by0 = poly.min(0)
    bx1, by1 = poly.max(0)
    from pclabel.render.camera import boxes_visible, frustum_planes  # numpy only (no GL)

    cand = np.flatnonzero(boxes_visible(frustum_planes(mvp), lo, hi) & (x1 >= bx0) & (x0 <= bx1) & (y1 >= by0)
                          & (y0 <= by1))  # fmt: skip
    arr = np.asarray(xyz)
    parts = []
    for t in cand:
        a = int(tiles["start"][t])
        b = a + int(tiles["count"][t])
        idx = select_in_mask(arr[a:b], mvp, mask, depth).astype(np.int64) + a
        if filters is not None:
            idx = filters.apply(arr, idx)
        parts.append(idx)
    return np.concatenate(parts) if parts else np.empty(0, np.int64)
