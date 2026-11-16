"""Semi-automatic road-marking tracing by intensity (the user's request of 2026-11-05, after LiDAR360MLS's "Detect
Lane": solid and dashed lines).

The labeller clicks a point on the paint, then a second point along it for the direction. The intensity threshold
is taken from the start (``threshold`` None): halfway between the paint's median intensity (points within half a
line width of the click) and the road's (points ``road_from``..``road_to`` to either side). Then the tracer steps
along (``step``): each step takes the points ``step`` long and ``search`` to either side of the line's centre, within
``band`` of the road's height (cars and curbs are left out), and keeps those at or above the threshold. Where there
are at least ``min_points`` of them and they are no wider than ``2 * line_width``, their median across is the line's
centre there; the direction follows the centres.

- **Solid** lines: gaps (worn paint, an object on it) are crossed up to ``stretch`` (LiDAR360MLS's default 2 m) and
  drawn over, and each bridge is recorded; a longer gap ends the line.
- **Dashed** lines: each dash is its own piece, from where its paint starts to where it ends (measured along the
  line in the first and last step); gaps up to ``dash_gap`` are crossed without drawing. Gaps up to ``dash_join``
  are inside a dash (sparse or worn paint) and bridged, as a solid line's are.
- The road's intensity for the threshold is the darker of the two sides (the other side may be a curb or another
  line).

Stops where the paint ends for longer, where it turns more than ``max_turn`` in a step, or after ``max_length``.
The parameters (and the threshold used) go with the result: they are the thresholds a marking algorithm would use.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

import numpy as np

from pclabel.analysis.geometry import direction_axes
from pclabel.tools.tracing import drive
from pclabel.tools.volume import VolumeParams, select_volume

KINDS = ("solid", "dashed")


@dataclass(frozen=True)
class LineTraceParams:
    kind: str = "solid"  # "solid" | "dashed"
    step: float = 0.25  # m along the line between sections
    line_width: float = 0.20  # m (LiDAR360MLS's default): a narrower one keeps a double line's two lines apart
    search: float = 0.25  # m to either side of the line's centre
    band: float = 0.10  # m above and below the road's height at the section
    threshold: float | None = None  # intensity (0..1) at or above which a point is paint; None: from the start
    road_from: float = 0.30  # m across from the click: where the road's intensity is sampled for the threshold
    road_to: float = 0.60
    start_length: float = 0.40  # m along the line: the box at the start that the threshold is taken from
    min_points: int = 5
    min_contrast: float = 3.0  # the paint's median must be this many road MADs (and at least 0.01) above the road's
    stretch: float = 2.0  # m: a solid line's gaps up to this are bridged (and recorded)
    dash_gap: float = 12.0  # m: a dashed line's gaps up to this are crossed to the next dash
    dash_join: float = 1.0  # m: a dashed line's shorter gaps are inside a dash (sparse or worn paint), bridged
    max_turn: float = 20.0  # degrees per step
    max_length: float = 300.0  # m


def _box(xyz, tiles, centre, n, d, length, width, band, filters):
    vp = VolumeParams(shape="box", length=length, width=width, bottom=-band, top=band)
    idx = select_volume(xyz, tiles, centre, n, d, vp, filters)
    q = np.asarray(xyz[idx], np.float64) - centre
    return idx, q @ d, q @ np.cross(n, d), q @ n


def start_threshold(xyz, intensity, tiles, start, axis, direction, p: LineTraceParams, filters=None) -> dict:
    """The paint's and the road's intensity at the start, and the threshold halfway between them."""
    n, d, _ = direction_axes(axis, direction)
    c = np.asarray(start, np.float64)
    idx, u, v, _ = _box(xyz, tiles, c, n, d, p.start_length, 2 * p.road_to, p.band, filters)
    i = np.asarray(intensity[idx], np.float64) / 65535.0
    paint = np.abs(v) <= p.line_width / 2
    sides = [(v >= p.road_from) & (v <= p.road_to), (v <= -p.road_from) & (v >= -p.road_to)]
    sides = [m for m in sides if m.sum() >= p.min_points]
    if paint.sum() < p.min_points or not sides:
        raise ValueError("too few points at the start to tell paint from road")
    road = min(sides, key=lambda m: float(np.median(i[m])))  # the darker side is road
    ip, ir = float(np.median(i[paint])), float(np.median(i[road]))
    mad = float(np.median(np.abs(i[road] - ir)))
    if ip - ir < max(p.min_contrast * mad, 0.01):
        raise ValueError(f"the start is no brighter than the road around it (paint {ip:.3f}, road {ir:.3f}, road MAD "
                         f"{mad:.3f}): click on the marking")  # fmt: skip
    return {"paint": ip, "road": ir, "road_mad": mad, "threshold": (ip + ir) / 2}


def trace(xyz, intensity, tiles, start, toward, axis, p: LineTraceParams = LineTraceParams(), filters=None):
    """Follow a marking from ``start`` (local, on the paint) towards ``toward``. A generator yielding each section
    with paint ({centre, u_lo, u_hi, points, piece, bridged}); returns {"stopped", "threshold"}."""
    if p.kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    n, d, _ = direction_axes(axis, np.asarray(toward, np.float64) - np.asarray(start, np.float64))
    thr = p.threshold
    if thr is None:
        thr = start_threshold(xyz, intensity, tiles, start, n, d, p, filters)["threshold"]
    c = np.asarray(start, np.float64)
    prev, gap, length, piece, gap_why = None, 0.0, 0.0, 0, ""
    max_gap = p.stretch if p.kind == "solid" else p.dash_gap
    while True:
        idx, u, v, h = _box(xyz, tiles, c, n, d, p.step, 2 * p.search, p.band, filters)
        i = np.asarray(intensity[idx], np.float64) / 65535.0 if idx.size else np.empty(0)
        bright = i >= thr
        ok = bright.sum() >= p.min_points
        if ok:
            vb = v[bright]
            lo, hi = np.percentile(vb, (5, 95))
            ok = hi - lo <= 2 * p.line_width
            gap_why = "" if ok else f"paint {hi - lo:.2f} m wide (more than {2 * p.line_width:.2f} m)"
        else:
            gap_why = "no paint"
        if not ok:
            if prev is None:
                return {"stopped": f"{gap_why} at the start", "threshold": thr}
            gap += p.step
            if gap > max_gap + 1e-9:
                return {"stopped": f"{gap_why} for more than {max_gap:.1f} m", "threshold": thr}
            c = c + p.step * d
            continue
        a = float(np.median(vb))
        centre = c + a * np.cross(n, d) + float(np.median(h[bright])) * n
        bridged = 0.0
        if gap > 0:
            if p.kind == "dashed" and gap > p.dash_join + 1e-9:
                piece += 1
            else:
                bridged = gap
        if prev is not None and gap == 0:
            step = centre - prev
            step -= np.dot(step, n) * n
            if np.linalg.norm(step) > 1e-6:
                new = step / np.linalg.norm(step)
                turn = np.degrees(np.arccos(np.clip(np.dot(new, d), -1, 1)))
                if turn > p.max_turn:
                    return {"stopped": f"turned {turn:.0f} degrees in one step (more than {p.max_turn:.0f})",
                            "threshold": thr}  # fmt: skip
                d = d + new
                d -= np.dot(d, n) * n
                d /= np.linalg.norm(d)
        if prev is not None:
            length += float(np.linalg.norm(centre - prev))
        ub = u[bright]
        yield {"centre": centre, "u_lo": float(ub.min()), "u_hi": float(ub.max()), "dir": d.copy(),
               "points": int(bright.sum()), "piece": piece, "bridged": bridged}  # fmt: skip
        prev, gap = centre, 0.0
        if length >= p.max_length:
            return {"stopped": f"{p.max_length:.0f} m traced (the most)", "threshold": thr}
        # the next section: centred on the line, one step on
        c = centre + p.step * d
        c = c - np.dot(c - centre, n) * n


def run_trace(xyz, intensity, tiles, start, toward, axis, p: LineTraceParams = LineTraceParams(), filters=None,
              progress=None) -> dict:  # fmt: skip
    """The whole trace as pieces (one for a solid line, one per dash for a dashed one): {pieces: [(m, 3) local],
    bridged: [m of each bridged gap], sections, stopped, threshold, params}."""
    secs, end = drive(trace(xyz, intensity, tiles, start, toward, axis, p, filters), progress,
                      {"stopped": "stopped by the labeller", "threshold": p.threshold})
    pieces = []
    for k in sorted({s["piece"] for s in secs}):
        ss = [s for s in secs if s["piece"] == k]
        pts = [s["centre"] for s in ss]
        if p.kind == "dashed":  # the dash's ends, where its paint starts and stops along the line
            pts = [ss[0]["centre"] + (ss[0]["u_lo"]) * ss[0]["dir"]] + pts + [ss[-1]["centre"] + ss[-1]["u_hi"] * ss[-1]["dir"]]
        pieces.append(np.array(pts))
    return {"pieces": pieces, "bridged": [s["bridged"] for s in secs if s["bridged"] > 0], "sections": len(secs),
            **end, "params": asdict(replace(p, threshold=end.get("threshold")))}  # fmt: skip
