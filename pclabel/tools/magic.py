"""Magic select (intensity): from a click on a road marking, select the connected ground area of similar intensity,
like the magic wand of an image editor, on an intensity picture of the ground (SPEC §11).

1. Picture. The ground surface comes from cells as in ground grow (``ground_cell``, 10 cm: the points within
   ``surface_band`` of a cell's 5th percentile height, their median height). Every point within ``surface_tol`` of
   its cell's surface is a *ground point*. Ground points are binned into square pixels (``pixel``, 2 cm) that hold
   their mean intensity and mean height. Vehicles, canopy and kerb faces above the ground and drain floors below it
   are not in the picture. Far from the scanner the points lie on scan lines several cm apart, so many pixels there
   are empty.
2. Gaps. An empty pixel within ``max_gap`` / 2 of a pixel with data takes that pixel's values (the nearest one), as
   if the points were drawn larger. Neighbouring scan lines up to ``max_gap`` apart then touch, and so do the pieces
   of a marking that each scan line crosses; wider empty areas (occlusion shadows) stay empty. (Stepping over empty
   pixels along the 8 directions, as ground grow does, missed pieces that lie at other angles.)
3. Fill, 8-connected from the clicked pixel (``tools.ground._grow``). A neighbour joins when
   - its intensity is within a factor ``tolerance`` of the reference (2: from half to twice). The spread of a
     marking's intensity grows with its intensity (single points far from the scanner: 0.13-0.55 on one dash; road
     0.03-0.10), so a factor separates paint from road where a fixed difference cannot. The reference is the
     clicked pixel's intensity, as in an image editor; with ``memory`` > 0 it is a running (geometric) average
     along the fill over about that distance, which follows a line whose intensity fades with range;
   - its height differs from the current pixel's by at most ``max_step``, so the fill does not climb a kerb onto
     bright kerb paint.
   Each refused pixel records why. With ``contiguous`` off, every pixel within ``tolerance`` of the clicked one is
   taken, connected to it or not, on the surface the click is on (the ground reachable from it without a height
   step, e.g. the road between its kerbs): all the dashes of a line, and anything else as bright there. The fill
   stays inside the square of half-size ``radius`` around the click.
4. Points: every ground point in a selected pixel.

The picture covers 1.5 x ``radius`` around the click that built it. Later clicks reuse it while the picture
settings stay the same and their own square fits inside it. Cells and pixels lie on fixed lattices (multiples of
their size from the local origin), so a result depends only on the click, the parameters and the clip box, not on
which picture was reused.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

import numba as nb
import numpy as np

from pclabel.tools import sets
from pclabel.tools.ground import (
    R_GAP,
    R_HEIGHT,
    R_IRANGE,
    THREADS,
    CellGrid,
    GroundGrowParams,
    _grow,
    _roi_tiles,
    cell_grid,
    tile_block,
)


@dataclass(frozen=True)
class MagicSelectParams:
    tolerance: float = 2.0  # factor: intensities from reference / tolerance to reference x tolerance join
    memory: float = 0.0  # m; 0: the reference is the clicked pixel; > 0: a running average along the fill
    contiguous: bool = True  # False: every pixel within tolerance on the clicked surface, connected or not
    max_step: float = 0.02  # m, height change between neighbouring pixels
    max_gap: float = 0.20  # m, empty strips (between far scan lines) up to this wide are bridged
    pixel: float = 0.02  # m
    radius: float = 15.0  # m, half-size of the square around the click the fill may cover
    surface_tol: float = 0.04  # m, points this close to their cell's ground surface are in the picture
    ground_cell: float = 0.10  # m, cells for the ground surface (as in ground grow)
    surface_band: float = 0.10  # m, above a cell's 5th percentile height: its ground layer
    cell_points: int = 48  # points kept per cell for the ground surface


PICTURE_SCALE = 1.5  # the picture's half-size, in reaches
MAX_PIXELS = 40_000_000  # about 1 GB of picture
I_FLOOR = 0.005  # intensities are compared as log(max(i, I_FLOOR))
REASONS = {R_HEIGHT: "height step", R_IRANGE: "intensity", R_GAP: "gap wider than max gap"}


@dataclass
class IntensityImage:
    x0: float
    y0: float
    pixel: float
    nx: int
    ny: int
    inten: np.ndarray  # (ny, nx) float64, mean intensity 0..1 of the ground points, NaN where none
    z: np.ndarray  # (ny, nx) float64, their mean height (local), NaN where none
    count: np.ndarray  # (ny, nx) int32, ground points per pixel
    grid: CellGrid  # the ground surface
    key: tuple  # what the picture depends on besides its position (see picture_key)
    points: int = 0  # ground points binned

    def covers(self, seed, radius: float) -> bool:
        x, y = float(seed[0]), float(seed[1])
        return (self.x0 <= x - radius and x + radius <= self.x0 + self.nx * self.pixel
                and self.y0 <= y - radius and y + radius <= self.y0 + self.ny * self.pixel)  # fmt: skip


@dataclass
class MagicResult:
    indices: np.ndarray  # sorted int64 store indices
    region: np.ndarray  # (ny, nx) bool, selected pixels of the picture
    image: IntensityImage
    info: dict


def picture_key(params: MagicSelectParams, clip=None, inside=None) -> tuple:
    p = params
    c = None if clip is None else (tuple(np.asarray(clip[0], float)), tuple(np.asarray(clip[1], float)))
    return (p.radius, p.pixel, p.surface_tol, p.ground_cell, p.surface_band, p.cell_points, c,
            None if inside is None else inside.token)  # fmt: skip


@nb.njit(nogil=True, cache=True)
def _bin_ground(xyz, inten, has_int, a, b, gx0, gy0, gcell, gnx, gny, surf, tol, x0, y0, pixel, ox, oy, w, h, clip_on,
                lo, hi, in_on, inside, s_i, s_z, cnt):  # fmt: skip
    """Sum the ground points of store points a..b (only those in ``inside`` when ``in_on``) into the block
    (ox, oy, w, h) of the picture at (x0, y0)."""
    for i in range(a, b):
        if in_on and not inside[i]:
            continue
        x, y, z = xyz[i, 0], xyz[i, 1], xyz[i, 2]
        if clip_on and (x < lo[0] or x > hi[0] or y < lo[1] or y > hi[1] or z < lo[2] or z > hi[2]):
            continue
        cx = int(np.floor((x - gx0) / gcell))
        cy = int(np.floor((y - gy0) / gcell))
        if cx < 0 or cx >= gnx or cy < 0 or cy >= gny:
            continue
        if not abs(z - surf[cy, cx]) <= tol:  # also false where the cell has no surface (NaN)
            continue
        px = int(np.floor((x - x0) / pixel)) - ox
        py = int(np.floor((y - y0) / pixel)) - oy
        if px < 0 or px >= w or py < 0 or py >= h:
            continue
        c = py * w + px
        s_i[c] += inten[i] / 65535.0 if has_int else 0.0
        s_z[c] += z
        cnt[c] += 1


@nb.njit(parallel=True, nogil=True, cache=True)
def _fill_gaps(z, inten, dy, dx, z_out, i_out):
    """Empty pixels (NaN) take the values of the nearest pixel with data among the offsets (dy, dx), which are
    sorted by distance; pixels with no data within them stay NaN."""
    ny, nx = z.shape
    for y in nb.prange(ny):
        for x in range(nx):
            if np.isfinite(z[y, x]):
                z_out[y, x] = z[y, x]
                i_out[y, x] = inten[y, x]
                continue
            z_out[y, x] = np.nan
            i_out[y, x] = np.nan
            for k in range(dy.shape[0]):
                yy, xx = y + dy[k], x + dx[k]
                if 0 <= yy < ny and 0 <= xx < nx and np.isfinite(z[yy, xx]):
                    z_out[y, x] = z[yy, xx]
                    i_out[y, x] = inten[yy, xx]
                    break


@nb.njit(parallel=True, nogil=True, cache=True)
def _classify(xyz, a, b, gx0, gy0, gcell, gnx, gny, surf, tol, x0, y0, pixel, nx, ny, region, out):
    for k in nb.prange(b - a):
        i = a + k
        x, y = xyz[i, 0], xyz[i, 1]
        ok = False
        px = int(np.floor((x - x0) / pixel))
        py = int(np.floor((y - y0) / pixel))
        if 0 <= px < nx and 0 <= py < ny and region[py, px]:
            cx = int(np.floor((x - gx0) / gcell))
            cy = int(np.floor((y - gy0) / gcell))
            if 0 <= cx < gnx and 0 <= cy < gny:
                ok = abs(xyz[i, 2] - surf[cy, cx]) <= tol
        out[k] = ok


def intensity_image(xyz, intensity, tiles, centre, params: MagicSelectParams, clip=None,
                    inside=None) -> IntensityImage:  # fmt: skip
    """The ground's intensity picture around ``centre`` (local coordinates), PICTURE_SCALE x the reach, from the
    points within the clip box and ``inside`` (a PointMask)."""
    p = params
    centre = np.asarray(centre, np.float64)
    half = PICTURE_SCALE * p.radius
    n = int(np.ceil(2 * half / p.pixel)) + 1
    if n * n > MAX_PIXELS:
        raise ValueError(f"a {p.radius:g} m reach at {p.pixel * 100:g} cm pixels is too large a picture: "
                         "lower the reach or use larger pixels")  # fmt: skip
    x0 = np.floor((centre[0] - half) / p.pixel) * p.pixel
    y0 = np.floor((centre[1] - half) / p.pixel) * p.pixel
    g = p.ground_cell
    gx0 = np.floor(x0 / g) * g
    gy0 = np.floor(y0 / g) * g
    gn = int(np.ceil((x0 + n * p.pixel - gx0) / g)) + 1
    gp = GroundGrowParams(cell=g, surface_band=p.surface_band, cell_points=p.cell_points)
    grid = cell_grid(xyz, intensity, tiles, centre, gp, clip, extent=(gx0, gy0, gn, gn), inside=inside)
    in_on, in_mask = sets.mask_args(inside)
    s_i = np.zeros((n, n))
    s_z = np.zeros((n, n))
    cnt = np.zeros((n, n), np.int32)
    arr = np.asarray(xyz)
    has_int = intensity is not None
    iarr = np.asarray(intensity) if has_int else np.zeros(1, np.uint16)
    lo, hi = (np.asarray(clip[0], np.float64), np.asarray(clip[1], np.float64)) if clip is not None else (
        np.zeros(3), np.zeros(3))  # fmt: skip
    mid = np.array([x0 + 0.5 * n * p.pixel, y0 + 0.5 * n * p.pixel])

    def block(t):
        ox, oy, w, h = tile_block(tiles, t, x0, y0, p.pixel, n, n)
        if w == 0 or h == 0:
            return None
        a, m = int(tiles["start"][t]), int(tiles["count"][t])
        b_i, b_z, b_n = np.zeros(w * h), np.zeros(w * h), np.zeros(w * h, np.int32)
        _bin_ground(arr, iarr, has_int, a, a + m, grid.x0, grid.y0, g, grid.nx, grid.ny, grid.surf, p.surface_tol,
                    x0, y0, p.pixel, ox, oy, w, h, clip is not None, lo, hi, in_on, in_mask, b_i, b_z, b_n)  # fmt: skip
        return ox, oy, w, h, b_i, b_z, b_n

    with ThreadPoolExecutor(THREADS) as ex:
        for b in ex.map(block, _roi_tiles(tiles, mid, 0.5 * n * p.pixel, clip, inside)):  # summed in tile order
            if b is not None:
                ox, oy, w, h, b_i, b_z, b_n = b
                s_i[oy : oy + h, ox : ox + w] += b_i.reshape(h, w)
                s_z[oy : oy + h, ox : ox + w] += b_z.reshape(h, w)
                cnt[oy : oy + h, ox : ox + w] += b_n.reshape(h, w)
    has = cnt > 0
    inten = np.full((n, n), np.nan)
    z = np.full((n, n), np.nan)
    inten[has] = s_i[has] / cnt[has]
    z[has] = s_z[has] / cnt[has]
    return IntensityImage(float(x0), float(y0), p.pixel, n, n, inten, z, cnt, grid, picture_key(p, clip, inside),
                          int(cnt.sum()))  # fmt: skip


def _disc_offsets(r: float):
    """Offsets within r pixels (not the centre), nearest first; ties in a fixed order."""
    k = int(np.floor(r))
    dy, dx = np.mgrid[-k : k + 1, -k : k + 1]
    dy, dx = dy.ravel(), dx.ravel()
    d2 = dy**2 + dx**2
    keep = (d2 > 0) & (d2 <= r * r + 1e-9)
    dy, dx, d2 = dy[keep], dx[keep], d2[keep]
    o = np.lexsort((dx, dy, d2))
    return dy[o].astype(np.int64), dx[o].astype(np.int64)


def filled_window(img: IntensityImage, wy: slice, wx: slice, max_gap: float):
    """(z, intensity) of a window of the picture with gaps up to max_gap bridged (module docstring, step 2)."""
    r = 0.5 * max_gap / img.pixel
    m = int(np.ceil(r))
    ya, yb = max(wy.start - m, 0), min(wy.stop + m, img.ny)  # a margin, so data just outside the window counts
    xa, xb = max(wx.start - m, 0), min(wx.stop + m, img.nx)
    z = np.ascontiguousarray(img.z[ya:yb, xa:xb])
    inten = np.ascontiguousarray(img.inten[ya:yb, xa:xb])
    z_f, i_f = np.empty_like(z), np.empty_like(inten)
    dy, dx = _disc_offsets(r)
    _fill_gaps(z, inten, dy, dx, z_f, i_f)
    crop = (slice(wy.start - ya, wy.stop - ya), slice(wx.start - xa, wx.stop - xa))
    return z_f[crop], i_f[crop]


def magic_region(img: IntensityImage, seed, params: MagicSelectParams):
    """(region, info): the selected pixels (picture shape) and figures about the fill."""
    p = params
    seed = np.asarray(seed, np.float64)
    if not img.covers(seed, p.radius):
        raise ValueError("the picture does not cover the reach around the click")
    if p.tolerance < 1.0:
        raise ValueError("the tolerance is a factor of at least 1")
    sx = int(np.floor((seed[0] - img.x0) / img.pixel))
    sy = int(np.floor((seed[1] - img.y0) / img.pixel))
    r = int(round(p.radius / img.pixel))
    wy = slice(max(sy - r, 0), min(sy + r + 1, img.ny))
    wx = slice(max(sx - r, 0), min(sx + r + 1, img.nx))
    z, inten = filled_window(img, wy, wx, p.max_gap)
    # the clicked pixel, or the nearest one within 3 pixels with ground at the click's height
    seed_px = None
    for dy, dx in zip(*_seed_offsets()):
        y, x = sy - wy.start + dy, sx - wx.start + dx
        if 0 <= y < z.shape[0] and 0 <= x < z.shape[1] and abs(z[y, x] - seed[2]) <= p.surface_band:
            seed_px = (int(y), int(x))
            break
    if seed_px is None:
        raise ValueError("no ground at the click: click on the ground (a marking), not on an object above it")
    ref = float(inten[seed_px])
    log_i = np.log(np.maximum(inten, I_FLOOR))  # NaN stays NaN
    log_tol = float(np.log(p.tolerance))
    if p.contiguous:
        alpha = min(1.0, img.pixel / p.memory) if p.memory > 0 else 0.0
        grown, reason = _grow(z, log_i, np.zeros_like(z), seed_px, float(log_i[seed_px]), p.max_step, 0.0, img.pixel,
                              np.inf, np.inf, log_tol, alpha, 0)  # fmt: skip
    else:  # the surface (a fill on height alone), then intensity
        surface, _ = _grow(z, np.zeros_like(z), np.zeros_like(z), seed_px, 0.0, p.max_step, 0.0, img.pixel, np.inf,
                           np.inf, np.inf, 0.0, 0)  # fmt: skip
        grown = surface & (np.abs(log_i - log_i[seed_px]) <= log_tol)
        reason = np.zeros(grown.shape, np.uint8)
    edge = bool(grown[0, :].any() or grown[-1, :].any() or grown[:, 0].any() or grown[:, -1].any())
    region = np.zeros((img.ny, img.nx), bool)
    region[wy, wx] = grown
    reason_full = np.zeros((img.ny, img.nx), np.uint8)
    reason_full[wy, wx] = reason
    ref_c = max(ref, I_FLOOR)
    info = {
        "stopped_by": {REASONS[c]: int((reason == c).sum()) for c in REASONS},
        "seed_pixel": (int(seed_px[0] + wy.start), int(seed_px[1] + wx.start)),
        "seed_intensity": ref,
        "accepted_intensity": (ref_c / p.tolerance, ref_c * p.tolerance),  # around the seed (memory 0)
        "seed_height_local": float(z[seed_px]),
        "pixels": int(grown.sum()),
        "area_m2": float(grown.sum() * img.pixel**2),
        "reached_edge": edge,
    }
    info["reason_grid"] = reason_full  # (ny, nx) uint8, see REASONS; not part of the parameters record
    return region, info


def _seed_offsets(k: int = 3):
    """The clicked pixel, then the others within k pixels, nearest first."""
    dy, dx = _disc_offsets(k)
    return [0, *dy.tolist()], [0, *dx.tolist()]


def select_region_points(xyz, tiles, img: IntensityImage, region: np.ndarray, params: MagicSelectParams,
                         clip=None, inside=None) -> np.ndarray:  # fmt: skip
    """Full-resolution store indices of the ground points in the selected pixels."""
    if not region.any():
        return np.empty(0, np.int64)
    ys, xs = np.nonzero(region)
    lo = np.array([img.x0 + xs.min() * img.pixel, img.y0 + ys.min() * img.pixel])
    hi = np.array([img.x0 + (xs.max() + 1) * img.pixel, img.y0 + (ys.max() + 1) * img.pixel])
    t = tiles
    cand = np.flatnonzero((t["x1"] >= lo[0]) & (t["x0"] <= hi[0]) & (t["y1"] >= lo[1]) & (t["y0"] <= hi[1]))
    if inside is not None:
        cand = inside.keep_tiles(t, cand)
    arr = np.asarray(xyz)
    g = img.grid
    parts = []
    for k in cand:
        a, b = int(t["start"][k]), int(t["start"][k] + t["count"][k])
        out = np.empty(b - a, np.bool_)
        _classify(arr, a, b, g.x0, g.y0, g.cell, g.nx, g.ny, g.surf, params.surface_tol, img.x0, img.y0, img.pixel,
                  img.nx, img.ny, region, out)  # fmt: skip
        idx = np.flatnonzero(out) + a
        if inside is not None and idx.size:
            idx = idx[inside.mask[idx]]
        if clip is not None and idx.size:
            q = arr[idx]
            idx = idx[np.all((q >= clip[0]) & (q <= clip[1]), axis=1)]
        parts.append(idx.astype(np.int64))
    return np.concatenate(parts) if parts else np.empty(0, np.int64)


def magic_select(xyz, intensity, tiles, seed, params: MagicSelectParams = MagicSelectParams(), *, image=None,
                 clip=None, inside=None) -> MagicResult:  # fmt: skip
    """Run the whole tool. Pass the ``image`` of an earlier run: it is reused when it was built with the same
    settings and covers this click's reach, so later clicks and threshold changes do not re-read the points."""
    seed = np.asarray(seed, np.float64)
    reused = image is not None and image.key == picture_key(params, clip, inside) and image.covers(seed, params.radius)
    if not reused:
        image = intensity_image(xyz, intensity, tiles, seed, params, clip, inside)
    region, info = magic_region(image, seed, params)
    idx = select_region_points(xyz, tiles, image, region, params, clip, inside)
    info["points"] = int(idx.size)
    info["picture_reused"] = bool(reused)
    info["picture_points"] = image.points
    info["params"] = asdict(params)
    return MagicResult(idx, region, image, info)
