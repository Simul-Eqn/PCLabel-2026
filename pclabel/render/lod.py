"""Level of detail: how many points of each tile to draw (no GL; unit-tested).

Tiles are shuffled inside, so the first k points of a tile are a uniform subsample. A visible tile asks for
enough points to fill its projected screen area at ``spacing_px`` between points (never more than it holds);
if the requests exceed the point budget, all are scaled down by the same factor.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pclabel.render.camera import boxes_visible, frustum_planes
from pclabel.tools.selection import project_boxes


@dataclass(frozen=True)
class LodParams:
    point_budget: int = 30_000_000
    spacing_px: float = 1.0  # wanted on-screen spacing between drawn points


def tile_screen_area(lo: np.ndarray, hi: np.ndarray, mvp: np.ndarray, viewport) -> np.ndarray:
    """Pixel area of each box's projected bounding rectangle, clipped to the viewport.

    A box that reaches behind the eye counts as covering the whole viewport (it surrounds the camera).
    """
    x0, y0, x1, y1, _ = project_boxes(lo, hi, mvp, viewport)
    return (x1 - x0) * (y1 - y0)


def lod_counts(counts: np.ndarray, lo: np.ndarray, hi: np.ndarray, mvp: np.ndarray, viewport,
               params: LodParams) -> tuple[np.ndarray, np.ndarray]:  # fmt: skip
    """(k, area): points to draw per tile (int64) and the projected areas used to choose them."""
    vis = boxes_visible(frustum_planes(mvp), lo, hi)
    area = np.zeros(len(counts))
    if vis.any():
        area[vis] = tile_screen_area(lo[vis], hi[vis], mvp, viewport)
    want = np.where(vis, np.minimum(counts.astype(np.float64), np.maximum(area, 1.0) / params.spacing_px**2), 0.0)
    total = want.sum()
    if total > params.point_budget:
        want *= params.point_budget / total
    return np.floor(want).astype(np.int64), area
