"""Picking: the full-resolution point nearest to a 3D position (e.g. unprojected from the depth buffer).

Brute force over the few tiles near the position (numba, parallel): a 2M-point tile takes about a millisecond,
and needs no KD-tree build. Also counts neighbours within two radii as a local density figure.
"""

from __future__ import annotations

from dataclasses import dataclass

import numba as nb
import numpy as np

from pclabel.tools import sets

_NCH = 64


@nb.njit(parallel=True, nogil=True, cache=True)
def _nearest(xyz, a, b, px, py, pz, r1, r2, in_on, inside):
    n = b - a
    step = (n + _NCH - 1) // _NCH
    best_i = np.full(_NCH, -1, np.int64)
    best_d = np.full(_NCH, np.inf)
    c1 = np.zeros(_NCH, np.int64)
    c2 = np.zeros(_NCH, np.int64)
    r1s, r2s = r1 * r1, r2 * r2
    for c in nb.prange(_NCH):
        for i in range(a + c * step, min(b, a + (c + 1) * step)):
            if in_on and not inside[i]:
                continue
            dx = xyz[i, 0] - px
            dy = xyz[i, 1] - py
            dz = xyz[i, 2] - pz
            d = dx * dx + dy * dy + dz * dz
            if d < best_d[c]:
                best_d[c] = d
                best_i[c] = i
            if d <= r2s:
                c2[c] += 1
                if d <= r1s:
                    c1[c] += 1
    k = np.argmin(best_d)
    return best_i[k], best_d[k], c1.sum(), c2.sum()


@dataclass(frozen=True)
class Pick:
    index: int  # store index
    tile: int
    distance: float  # from the query position, m
    within: dict  # radius (m) -> neighbour count, the point itself included


def nearest_point(xyz: np.ndarray, tiles: np.ndarray, p, search: float = 0.25,
                  radii: tuple[float, float] = (0.05, 0.10), inside=None) -> Pick | None:  # fmt: skip
    """Nearest point of ``xyz`` (store order, local coordinates) to ``p`` within ``search`` metres, or None.
    ``inside`` (a tools.sets.PointMask, e.g. the clip to a point set): only its points.

    ``within`` counts the neighbours of the picked point (not of ``p``) within each radius.
    """
    arr = np.asarray(xyz)
    best = None
    for t, (i, d2, _, _) in _scan(arr, tiles, np.asarray(p, np.float64), search, radii, inside):
        if i >= 0 and (best is None or d2 < best[1]):
            best = (int(i), float(d2), int(t))
    if best is None or best[1] > search * search:
        return None
    q = arr[best[0]].astype(np.float64)
    counts = np.zeros(2, np.int64)
    for _, (_, _, c1, c2) in _scan(arr, tiles, q, max(radii), radii, inside):
        counts += (c1, c2)
    return Pick(best[0], best[2], float(np.sqrt(best[1])), {radii[0]: int(counts[0]), radii[1]: int(counts[1])})


def _scan(arr, tiles, p, reach, radii, inside=None):
    near = ((tiles["x0"] - reach <= p[0]) & (p[0] <= tiles["x1"] + reach)
            & (tiles["y0"] - reach <= p[1]) & (p[1] <= tiles["y1"] + reach)
            & (tiles["zmin"] - reach <= p[2]) & (p[2] <= tiles["zmax"] + reach))  # fmt: skip
    cand = np.flatnonzero(near)
    if inside is not None:
        cand = inside.keep_tiles(tiles, cand)
    in_on, in_mask = sets.mask_args(inside)
    for t in cand:
        a = int(tiles["start"][t])
        yield t, _nearest(arr, a, a + int(tiles["count"][t]), p[0], p[1], p[2], radii[0], radii[1], in_on, in_mask)
