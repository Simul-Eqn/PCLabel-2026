"""A 2D map of the labels on a plane (the user's request of 2026-10-28: "choose a plane and then make a 2d map of all
the labels (assign a colour for each label) projected onto the map; this includes curves/lines").

Everything is projected straight onto the plane along its normal: map x along the plane's ``along`` direction, map
y across it (for the scan frame z: x east, y north as stored). One pixel is ``pixel`` metres. Each point colours the
pixel it falls in (and ``dot`` pixels around); segments and curves are drawn on top as lines ``line_width`` pixels
wide.

Where labels overlap, the later layer wins. The caller orders the layers so that smaller point sets come later: a
``marking`` set (paint) shows on top of the ``road`` set it lies in, as in the 3D labels view.

Codes: 0 is empty (transparent), 1..254 are the caller's labels, CONTEXT (255) is grey context (points in no visible
label, when asked for).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numba as nb
import numpy as np

from pclabel.tools.volume import frame_axes

LABEL_MAP_SCHEMA = "pclabel.label-map/1"
CONTEXT = 255
CONTEXT_RGBA = (200, 200, 200, 255)
CHUNK = 4_000_000  # points read and projected at a time


@dataclass(frozen=True)
class LabelMapParams:
    pixel: float = 0.05  # metres per pixel
    dot: int = 0  # extra pixels around each point (0: just its pixel)
    line_width: float = 3.0  # pixels, segments and curves
    margin: float = 1.0  # metres around what is drawn
    h_min: float = -np.inf  # only points this far above the plane, along its normal (metres) ...
    h_max: float = np.inf  # ... up to this
    max_side: int = 30_000  # pixels: a larger map is refused (the pixel is too small for the area)


def map_axes(normal, along) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(n, x, y): the plane's unit normal, and the map's x (along) and y (across) directions on it."""
    return frame_axes(normal, along)


@nb.njit(parallel=True, nogil=True, cache=True)
def _project(xyz, idx, o, n, x, y, px, py, ph):
    for k in nb.prange(idx.shape[0]):
        i = idx[k]
        dx, dy, dz = xyz[i, 0] - o[0], xyz[i, 1] - o[1], xyz[i, 2] - o[2]
        px[k] = dx * x[0] + dy * x[1] + dz * x[2]
        py[k] = dx * y[0] + dy * y[1] + dz * y[2]
        ph[k] = dx * n[0] + dy * n[1] + dz * n[2]


def _size(src) -> int:
    return int(src) if isinstance(src, (int, np.integer)) else len(src)


def _chunks(src):
    """Store indices in chunks: of an index array, or of every point (``src`` an int: the point count)."""
    n = _size(src)
    for s in range(0, n, CHUNK):
        yield np.arange(s, min(s + CHUNK, n), dtype=np.int64) if isinstance(src, (int, np.integer)) else             np.asarray(src[s : s + CHUNK], np.int64)


def _projected(xyz, idx, o, n, x, y):
    m = idx.size
    px, py, ph = np.empty(m), np.empty(m), np.empty(m)
    _project(xyz, idx, o, n, x, y, px, py, ph)
    return px, py, ph


@nb.njit(nogil=True, cache=True)
def _splat(img, px, py, ph, h0, h1, x0, y1, pixel, dot, code):
    H, W = img.shape
    drawn = 0
    for k in range(px.shape[0]):
        if ph[k] < h0 or ph[k] > h1:
            continue
        c = int(np.floor((px[k] - x0) / pixel))
        r = int(np.floor((y1 - py[k]) / pixel))
        for rr in range(r - dot, r + dot + 1):
            if rr < 0 or rr >= H:
                continue
            for cc in range(c - dot, c + dot + 1):
                if 0 <= cc < W:
                    img[rr, cc] = code
        drawn += 1
    return drawn


@nb.njit(nogil=True, cache=True)
def _line(img, uv, x0, y1, pixel, radius, code):
    """A polyline (map metres) as discs of ``radius`` pixels every half pixel along it."""
    H, W = img.shape
    rad = int(np.ceil(radius))
    for s in range(uv.shape[0] - 1):
        c0, r0 = (uv[s, 0] - x0) / pixel, (y1 - uv[s, 1]) / pixel
        c1, r1 = (uv[s + 1, 0] - x0) / pixel, (y1 - uv[s + 1, 1]) / pixel
        steps = max(1, int(np.ceil(2.0 * np.hypot(c1 - c0, r1 - r0))))
        for t in range(steps + 1):
            f = t / steps
            cx, cy = c0 + f * (c1 - c0), r0 + f * (r1 - r0)
            for rr in range(int(np.floor(cy)) - rad, int(np.floor(cy)) + rad + 1):
                if rr < 0 or rr >= H:
                    continue
                for cc in range(int(np.floor(cx)) - rad, int(np.floor(cx)) + rad + 1):
                    if 0 <= cc < W and (cc + 0.5 - cx) ** 2 + (rr + 0.5 - cy) ** 2 <= radius * radius:
                        img[rr, cc] = code


def label_map(xyz_local, offset, normal, origin, along, layers, vectors, colors, params=LabelMapParams(),
              context=None, progress=None) -> dict:  # fmt: skip
    """The map. ``layers``: [(sorted store indices, code)] in draw order (later on top); ``vectors``: [(polyline
    (m, 3) world, code)] drawn on top of the points; ``colors``: (256, 4) uint8 RGBA per code; ``context``: store
    indices drawn first in grey (CONTEXT; an int: every point of that many), or None. ``origin`` is a world point on the plane; ``xyz_local`` are store
    coordinates (world = local + offset). ``progress(fraction)`` may return False to cancel (ValueError).

    Returns {"rgba": (H, W, 4) uint8, "codes": (H, W) uint8, "transform": ..., "pixels": {code: count},
    "points": {code: count drawn}, "vectors_uv": [(m, 2) map metres per vector]}."""
    n, x, y = map_axes(normal, along)
    o = np.asarray(origin, np.float64) - np.asarray(offset, np.float64)  # local
    p = params
    groups = ([(context, CONTEXT)] if context is not None and _size(context) else []) + [(i, c) for i, c in layers if len(i)]
    sized = [g for g in groups if g[1] != CONTEXT]  # the extent: the labels and lines (context only fills it) ...
    if not sized and not vectors:
        sized = groups  # ... or, with nothing else, the context
    total = sum(_size(i) for i, _ in sized) + sum(_size(i) for i, _ in groups) + 1
    done = [0]

    def tick(k):
        done[0] += k
        if progress is not None and progress(done[0] / total) is False:
            raise ValueError("cancelled")

    # pass 1: the extent of what is drawn
    lo, hi = np.full(2, np.inf), np.full(2, -np.inf)
    off = np.asarray(offset, np.float64)
    vectors_uv = []
    for poly, _ in vectors:
        d = np.asarray(poly, np.float64) - off - o
        uv = np.stack([d @ x, d @ y], 1)
        vectors_uv.append(uv)
        lo, hi = np.minimum(lo, uv.min(0)), np.maximum(hi, uv.max(0))
    for idx, _ in sized:
        for part in _chunks(idx):
            px, py, ph = _projected(xyz_local, part, o, n, x, y)
            keep = (ph >= p.h_min) & (ph <= p.h_max)
            if keep.any():
                lo = np.minimum(lo, [px[keep].min(), py[keep].min()])
                hi = np.maximum(hi, [px[keep].max(), py[keep].max()])
            tick(part.size)
    if not np.all(np.isfinite(lo)):
        raise ValueError("nothing to draw: no labelled points (in the height range) and no segments or curves")
    lo, hi = lo - p.margin, hi + p.margin
    W, H = max(1, int(np.ceil((hi[0] - lo[0]) / p.pixel))), max(1, int(np.ceil((hi[1] - lo[1]) / p.pixel)))
    if max(W, H) > p.max_side:
        raise ValueError(f"the map would be {W:,} x {H:,} pixels (at most {p.max_side:,} a side): use larger pixels")
    x0, y1 = float(lo[0]), float(lo[1] + H * p.pixel)  # the top-left corner, in map metres

    # pass 2: the points, then the lines
    codes = np.zeros((H, W), np.uint8)
    points = {}
    for idx, code in groups:
        for part in _chunks(idx):
            px, py, ph = _projected(xyz_local, part, o, n, x, y)
            k = _splat(codes, px, py, ph, p.h_min, p.h_max, x0, y1, p.pixel, int(p.dot), np.uint8(code))
            points[int(code)] = points.get(int(code), 0) + int(k)
            tick(part.size)
    for uv, (_, code) in zip(vectors_uv, vectors):
        if len(uv) >= 2:
            _line(codes, np.ascontiguousarray(uv), x0, y1, p.pixel, 0.5 * p.line_width, np.uint8(code))
    tick(1)
    table = np.asarray(colors, np.uint8).copy()
    table[0] = 0
    table[CONTEXT] = CONTEXT_RGBA
    counts = np.bincount(codes.ravel(), minlength=256)
    return {
        "rgba": table[codes],
        "codes": codes,
        "transform": {
            "plane_normal": n.tolist(), "plane_origin": [float(v) for v in origin], "map_x": x.tolist(),
            "map_y": y.tolist(), "pixel": float(p.pixel), "width": W, "height": H,
            "top_left": [x0, y1],
            "pixel_to_world": "world = plane_origin + (top_left[0] + (col + 0.5) * pixel) * map_x "
                              "+ (top_left[1] - (row + 0.5) * pixel) * map_y",
        },  # fmt: skip
        "pixels": {int(c): int(counts[c]) for c in np.flatnonzero(counts) if c != 0},
        "points": points,
        "vectors_uv": vectors_uv,
        "params": {k: (None if not np.isfinite(v) else v) if isinstance(v, float) else v
                   for k, v in asdict(p).items()},  # fmt: skip
    }
