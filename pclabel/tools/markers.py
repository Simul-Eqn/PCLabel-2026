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

from dataclasses import asdict, dataclass

import numpy as np

from pclabel.analysis.geometry import frame_axes
from pclabel.tools.trees import fit_trunk
from pclabel.tools.volume import VolumeParams, select_volume

ACROSS = {"mean": "the mean of the points", "circle": "the centre of a circle fitted to them (poles, trunks)"}
HEIGHTS = {"mean": "their mean height", "lowest": "their lowest point"}


@dataclass(frozen=True)
class MarkerParams:
    across: str = "mean"  # "mean" | "circle": where the marker goes across the height axis
    height: str = "mean"  # "mean" | "lowest": where it goes along the height axis
    trim: float = 0.03  # m, circle: points further than this off the circle are left out and the circle fitted again
    # markers by clicking (marker_at_click):
    snap_radius: float = 0.40  # m: a click snaps to a circle fitted to the points this far around it
    snap_band: float = 0.25  # m above and below the click: the points the circle is fitted to
    drop_reach: float = 0.30  # m beyond snap_radius: "lowest" looks for the ground this far around
    drop_depth: float = 6.0  # m below the click: and this far down
    ground_percentile: float = 2.0  # "lowest": the ground is this percentile of those points' heights


def marker_at_click(xyz, tiles, click, axis, params: MarkerParams = MarkerParams(), along=(1.0, 0.0, 0.0),
                    filters=None) -> dict:  # fmt: skip
    """Where a marker for a click on the cloud goes (``click``, local).

    - across the height axis: the click itself ("mean"), or the centre of a circle fitted to the points within
      ``snap_radius`` of it and ``snap_band`` above and below ("circle": click a pole or a trunk anywhere, the marker
      goes on its axis); if no circle fits, the click;
    - along it: the click's height ("mean"), or the ground below the click ("lowest": the ``ground_percentile`` of
      the points within ``snap_radius + drop_reach`` across and ``drop_depth`` below: the foot of what was clicked).
    Returns {"position", "record"}."""
    n, _, _ = frame_axes(axis, along)
    c = np.asarray(click, np.float64)
    pos = c.copy()
    p = params
    rec = {"tool": "marker_click", "across": "click", "height": "click", "points": 0, "params": asdict(p)}
    if p.across == "circle":
        vp = VolumeParams("cylinder", radius=p.snap_radius, bottom=-p.snap_band, top=p.snap_band)
        idx = select_volume(xyz, tiles, c, n, along, vp, filters)
        try:
            res = place_marker(xyz, idx, n, MarkerParams("circle", "mean", p.trim), along)
        except ValueError:
            rec["note"] = "no circle around the click: at the click"
        else:
            q = res["position"]
            pos = q - np.dot(q - c, n) * n  # across: the circle's centre; along: the click's height
            rec.update({k: v for k, v in res["record"].items() if k not in ("tool", "height", "spread", "height_range")})
            rec.update(across="circle", snap_radius=p.snap_radius)
    if p.height == "lowest":
        vp = VolumeParams("cylinder", radius=p.snap_radius + p.drop_reach, bottom=-p.drop_depth, top=0.0)
        idx = select_volume(xyz, tiles, pos, n, along, vp, filters)
        if idx.size:
            h = (np.asarray(xyz[idx], np.float64) - pos) @ n
            pos = pos + float(np.percentile(h, p.ground_percentile)) * n
            rec["height"] = "lowest"
        else:
            rec["note"] = (rec.get("note", "") + "; no points below: at the click's height").lstrip("; ")
    return {"position": pos, "record": rec}


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
