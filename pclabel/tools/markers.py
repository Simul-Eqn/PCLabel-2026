"""Markers placed from selected points: where a thing is (no Qt, no GL).

- **Across the height axis:** the points' mean, or the centre of a circle fitted to them across the axis (a band of
  a pole or a trunk). A pole or trunk is scanned from one side, so its points' mean sits toward the scanner: on the
  127 tree bands of `streets 1` it was a median 5.6 cm (0.73 radii) from the fitted circle's centre, and up to 31 cm
  (O-0014). The circle's centre is not pulled that way.
- **Along the height axis:** the points' mean height, or their lowest point (e.g. a selection that reaches the
  ground at the foot of a pole).

``MarkerParams`` are written into the marker's ``source`` with what the fit found, so a marker can be traced back
to how it was placed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pclabel.tools.trees import fit_trunk
from pclabel.tools.volume import frame_axes

ACROSS = {"mean": "the mean of the points", "circle": "the centre of a circle fitted to them (poles, trunks)"}
HEIGHTS = {"mean": "their mean height", "lowest": "their lowest point"}


@dataclass(frozen=True)
class MarkerParams:
    across: str = "mean"  # "mean" | "circle": where the marker goes across the height axis
    height: str = "mean"  # "mean" | "lowest": where it goes along the height axis
    trim: float = 0.03  # m, circle: points further than this off the circle are left out and the circle fitted again


def place_marker(xyz, idx, axis, params: MarkerParams = MarkerParams(), along=(1.0, 0.0, 0.0)) -> dict:
    """Where a marker for the points ``idx`` (store indices into ``xyz``, local) goes. Returns ``position`` (local)
    and a record: the method, the number of points, how widely they spread across the axis, and for a circle its
    radius, girth, rms, the share of the circumference covered and how far the plain mean was from its centre."""
    if params.across not in ACROSS or params.height not in HEIGHTS:
        raise ValueError(f"unknown marker method {params.across!r} / {params.height!r}")
    idx = np.asarray(idx, np.int64)
    if idx.size == 0:
        raise ValueError("nothing selected")
    pts = np.asarray(xyz[idx], np.float64)
    n, _, _ = frame_axes(axis, along)
    mean = pts.mean(0)
    d = pts - mean
    h = d @ n
    across = d - np.outer(h, n)
    ref = mean + (float(h.min()) if params.height == "lowest" else 0.0) * n  # the mean, at the chosen height
    rec = {"tool": "marker", "across": params.across, "height": params.height, "points": int(idx.size),
           "spread": float(np.sqrt(np.mean(np.sum(across**2, 1)))), "height_range": float(np.ptp(h))}  # fmt: skip
    if params.across == "circle":
        fit = fit_trunk(xyz, idx, ref, n, along, trim=params.trim)
        pos = np.asarray(fit["centre"], np.float64)
        off = mean - pos
        off -= np.dot(off, n) * n
        rec.update(trim=params.trim, radius=fit["radius"], girth=fit["girth"], rms=fit["rms"],
                   points_fitted=fit["points"], points_dropped=fit["dropped"], coverage=fit["coverage"],
                   mean_off_centre=float(np.linalg.norm(off)))  # fmt: skip
    else:
        pos = ref
    return {"position": pos, "record": rec}
