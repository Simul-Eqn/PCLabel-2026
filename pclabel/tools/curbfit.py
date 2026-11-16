"""Smoothing a curb's top line while keeping it on the curb (the user's request of 2026-11-06: "a specialized smooth
thing that also tries to keep to the ground truth as much as possible and not lose it", and "there is a real case
when the curb boundary should be drawn at an area where the curb slopes down to meet the road, so we should not
follow that and keep it to the top of the curb"; for the top edge only, the user's word of the same day).

For a line along the top edge of a curb (a traced or drawn ``curb_top``):
1. **Measured edge:** every ``step`` along the line a cross-section of the cloud is taken across it (the curb
   tracer's, tools/curbtrace.py ``section``, following the top edge, so the road beside the curb need not be seen),
   from the road side, and the top of the curb's face found there (only where the curb's top was scanned: where
   only its face was, the face's highest point scanned is no top edge, and on ExchangeGreen 1 such spots scattered
   0-20 cm across): the first bin within ``top_tolerance`` of the
   curb top's own height (the median surface ``top_band`` beyond the first bin ``curb_height`` above the road; on a
   sloped or rounded face that first bin can be anywhere up the face). One within ``window`` across of the line is an *anchor*:
   where the curb's top edge really is. The curb's side is found once for the line (where more sections find it).
2. **Full-height curb only:** an anchor whose step is lower than ``min_height_ratio`` of the line's median step is
   left out, and so is a section with no step at all. Where the curb slopes down to meet the road (a lowered curb at
   a crossing or a driveway) the line is therefore not drawn down the slope: a stretch without anchors up to
   ``max_bridge`` long is carried across from the full-height curb on either side, its height going straight from
   the curb top on one side to the curb top on the other, its position across following the old line's shape
   moved by the corrections measured at the two sides (*bridged*). In a longer stretch (no curb measured: not this
   scan's, or not a curb) the line stays where it was, the corrections fading out over half of ``max_bridge``
   (*kept*).
3. **Clutter:** an anchor more than ``outlier`` from the curve through the anchors around it (a quadratic along the
   line through those within ``neighbourhood`` either side, itself left out) is dropped: a real corner is followed by
   its neighbours, an isolated wrong edge (a car tyre, a leaf pile) is not.
4. **Smoothing, held to the measurements:** the line through the anchors (and the bridged and kept stretches) is
   averaged along its length (Gaussian, sigma ``smooth``, analysis/curves.py ``smooth_line``) but no point moves
   more than ``max_shift`` from the same line de-noised (a cubic fitted along it ``denoise`` either way, a
   Savitzky-Golay filter: it takes out the measurements' own scatter, 1-3 cm, without pulling a corner in): the result is never further than that from what was measured,
   so a corner is rounded by at most that much and the measured curb is not lost.

All parameters and the counts (anchors, low steps left out, outliers, bridged and kept stretches, how far the line
moved, how far it is from the anchors) go with the result: the line becomes an assisted one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from scipy.spatial import cKDTree

from pclabel.analysis import curves
from pclabel.tools.curbtrace import CurbTraceParams, Section, section


@dataclass(frozen=True)
class CurbFitParams:
    smooth: float = 1.0  # m: the length along the line points are averaged over (sigma)
    max_shift: float = 0.02  # m: no point moves further than this from the (de-noised) line through the measurements
    denoise: float = 0.9  # m either way: the measurements' own scatter is taken out first by a cubic fitted along
    # the line over this (a Savitzky-Golay filter: unlike an average it does not pull a corner in)
    window: float = 0.20  # m across the line either way: a measured edge further than this is not this curb's
    step: float = 0.30  # m along the line between sections
    curb_height: float = 0.06  # m: the least step that counts as a curb (as the curb tracer's)
    min_height_ratio: float = 0.80  # an anchor's step must be at least this part of the line's median step (lower:
    # the curb sloping down to the road, carried over at the top instead)
    outlier: float = 0.05  # m: an anchor this far off the curve through its neighbours is clutter
    neighbourhood: float = 0.9  # m along the line either way: the neighbours that test an anchor
    road_offset: float = 0.35  # m: the section starts this far on the road side of the line
    road_behind: float = 0.25  # m: and looks this much further onto the road (the curb tracer's ``behind``)
    top_band: float = 0.20  # m beyond the first high bin: the curb top's height is the median surface there
    top_tolerance: float = 0.02  # m: the top of the face is the first bin this close to the curb top's height
    max_bridge: float = 8.0  # m: a stretch with no full-height curb up to this long is carried across from the curb
    # on either side (a lowered curb at a driveway or crossing); in a longer one the line keeps to where it was


def _sections(xyz, tiles, pts, tangents, axis, sign, p: CurbFitParams, filters):
    """(lip points (n, 3), NaN where none; step heights (n,)) for the curb on one side (``sign`` +1: left)."""
    n = np.asarray(axis, np.float64) / np.linalg.norm(axis)
    cp = CurbTraceParams(step=p.step, curb_height=p.curb_height, side="left" if sign > 0 else "right",
                         search=p.road_offset + p.window, behind=p.road_behind, follow="lip")  # fmt: skip
    out = np.full((len(pts), 3), np.nan)
    hts = np.full(len(pts), np.nan)
    unseen = np.zeros(len(pts), bool)
    for i, (q, d) in enumerate(zip(pts, tangents)):
        left = np.cross(n, d)
        ln = np.linalg.norm(left)
        if ln < 1e-9:
            continue
        s = sign * left / ln
        r = section(xyz, tiles, q - p.road_offset * s, n, d, cp, filters)
        if not isinstance(r, Section):
            continue
        if not r.top_seen:  # only the face scanned: its highest point is not the top edge
            unseen[i] = True
            continue
        lip, h = _top_of_face(r, n, p)
        if abs(np.dot(lip - q, s)) <= p.window:
            out[i], hts[i] = lip, h
    return out, hts, unseen


def _top_of_face(r: Section, n, p: CurbFitParams):
    """(point, step height) where the face ends and the curb's top begins: the first bin past the toe within
    ``top_tolerance`` of the top's height (the median surface ``top_band`` beyond the tracer's lip). On a sloped or
    rounded face the first bin ``curb_height`` above the road (the tracer's lip) can be anywhere up the face."""
    centres, surf = r.profile
    band = (centres > r.a_lip) & (centres <= r.a_lip + p.top_band) & ~np.isnan(surf)
    if band.sum() < 2:
        return r.lip, r.height
    top = float(np.median(surf[band]))
    cand = np.flatnonzero((centres >= r.a_toe) & ~np.isnan(surf) & (surf >= top - p.top_tolerance))
    if not cand.size:
        return r.lip, r.height
    a_top = float(centres[cand[0]]) - (centres[1] - centres[0]) / 2
    dh = top - r.h_lip
    return r.lip + (a_top - r.a_lip) * r.across + dh * n, r.height + dh


def _outliers(s, e, use, p: CurbFitParams, total, closed) -> np.ndarray:
    """Anchors more than ``p.outlier`` off a quadratic (along the line) through their neighbours."""
    idx = np.flatnonzero(use)
    sa, ea = s[idx], e[idx]
    bad = np.zeros(len(s), bool)
    for k, i in enumerate(idx):
        ds = sa - sa[k]
        if closed:
            ds = (ds + total / 2) % total - total / 2
        nb = (np.abs(ds) <= p.neighbourhood) & (np.arange(len(idx)) != k)
        if nb.sum() < 4:
            continue
        x = ds[nb]
        V = np.c_[np.ones_like(x), x, x * x]
        coef, *_ = np.linalg.lstsq(V, ea[nb], rcond=None)
        if np.linalg.norm(coef[0] - ea[k]) > p.outlier:
            bad[i] = True
    return bad


def _through(pts, e, use, s, total, ax, p: CurbFitParams, closed):
    """The line through the anchors: (points, per-sample state 0 anchored / 1 bridged / 2 kept)."""
    m = len(pts)
    h_of = lambda v: v @ ax  # noqa: E731
    T = pts.copy()
    state = np.zeros(m, np.int8)
    idx = np.flatnonzero(use)
    T[idx] = e[idx]
    corr = e - pts  # the measured correction at each anchor
    half = p.max_bridge / 2

    def fill(ks, s_rel, a, b, span):
        """Samples ``ks`` between anchors a and b (either may be None: a line's end) ``span`` apart."""
        ks = np.asarray(ks)
        if a is not None and b is not None and span <= p.max_bridge:  # bridged: carried across
            t = s_rel / span
            c = (1 - t)[:, None] * corr[a] + t[:, None] * corr[b]
            hh = (1 - t) * h_of(e[a]) + t * h_of(e[b])  # the curb top's height, straight across
            q = pts[ks] + c
            T[ks] = q + (hh - h_of(q))[:, None] * ax
            state[ks] = 1
            return
        # kept: the corrections fade out from each side over half of max_bridge
        q = pts[ks].copy()
        if a is not None:
            q += np.clip(1 - s_rel / half, 0, 1)[:, None] * corr[a]
        if b is not None:
            q += np.clip(1 - (span - s_rel) / half, 0, 1)[:, None] * corr[b]
        T[ks] = q
        state[ks] = 2

    if closed:
        order = list(idx) + [idx[0]]
        for a, b in zip(order[:-1], order[1:]):
            ks = (np.arange(a + 1, b + (m if b <= a else 0))) % m
            if not len(ks):
                continue
            span = (s[b] - s[a]) % total or total
            fill(ks, (s[ks] - s[a]) % total, a, b, span)
    else:
        if idx[0] > 0:
            ks = np.arange(0, idx[0])
            fill(ks, s[idx[0]] - s[ks], idx[0], None, np.inf)  # measured from the first anchor, backwards
        for a, b in zip(idx[:-1], idx[1:]):
            if b > a + 1:
                ks = np.arange(a + 1, b)
                fill(ks, s[ks] - s[a], a, b, s[b] - s[a])
        if idx[-1] < m - 1:
            ks = np.arange(idx[-1] + 1, m)
            fill(ks, s[ks] - s[idx[-1]], idx[-1], None, np.inf)
    return T, state


def _denoise(v, half: float, closed: bool) -> np.ndarray:
    """A cubic fitted along evenly spaced points, ``half`` points either way (Savitzky-Golay)."""
    from scipy.signal import savgol_filter

    w = 2 * int(round(half)) + 1
    w = min(w, len(v) - (1 - len(v) % 2))  # odd, no longer than the line
    if w < 5:
        return v.copy()
    out = savgol_filter(v, w, 3, axis=0, mode="wrap" if closed else "interp")
    if not closed:
        out[0], out[-1] = v[0], v[-1]
    return out


def fit(xyz, tiles, vertices, axis, p: CurbFitParams = CurbFitParams(), closed: bool = False, filters=None,
        progress=None) -> dict:  # fmt: skip
    """Fit a line (local (m, 3) vertices) to the top edge of the curb under it. Returns {vertices (local, about
    2 cm apart), samples (the section points), state (per sample: 0 anchored, 1 bridged, 2 kept), bridged and kept
    [(from m, length m)], side, sections, anchors, low_steps, outliers, no_edge, height_median, off_anchors_rms,
    off_anchors_max (m, the result from the anchors), largest_move, mean_move (m, from the old line), params}."""
    ax = np.asarray(axis, np.float64) / np.linalg.norm(axis)
    pts = curves.even_points(vertices, p.step, closed)
    m = len(pts)
    if m < 3:
        raise ValueError("the line is too short to fit")
    nxt = np.roll(pts, -1, 0) if closed else np.vstack([pts[1:], 2 * pts[-1:] - pts[-2:-1]])
    prv = np.roll(pts, 1, 0) if closed else np.vstack([2 * pts[:1] - pts[1:2], pts[:-1]])
    tan = nxt - prv
    tan -= np.outer(tan @ ax, ax)
    tan /= np.maximum(np.linalg.norm(tan, axis=1, keepdims=True), 1e-12)
    probe = np.unique(np.linspace(0, m - 1, min(m, 15)).round().astype(int))
    found = {}
    for sgn in (1, -1):  # the curb's side: where more of a few sections find it
        e, _, _ = _sections(xyz, tiles, pts[probe], tan[probe], ax, sgn, p, filters)
        ok = ~np.isnan(e[:, 0])
        found[sgn] = (int(ok.sum()), -float(np.median(np.linalg.norm((e - pts[probe])[ok], axis=1))) if ok.any() else 0.0)
    if progress is not None and progress(0.1) is False:
        raise InterruptedError("stopped")
    sgn = max(found, key=lambda k: found[k])
    if found[sgn][0] == 0:
        raise ValueError(f"no curb top found within {p.window:.2f} m of the line on either side")
    edge, hts, unseen = _sections(xyz, tiles, pts, tan, ax, sgn, p, filters)
    if progress is not None and progress(0.9) is False:
        raise InterruptedError("stopped")
    has = ~np.isnan(edge[:, 0])
    med = float(np.median(hts[has]))
    low = has & (hts < p.min_height_ratio * med)
    use = has & ~low
    s = curves.arc_length(pts)
    total = s[-1] + (float(np.linalg.norm(pts[0] - pts[-1])) if closed else 0.0)
    outliers = 0
    for _ in range(2):
        bad = _outliers(s, edge, use, p, total, closed)
        if not bad.any():
            break
        outliers += int(bad.sum())
        use &= ~bad
    if use.sum() < 2:
        raise ValueError("fewer than two measured curb tops along the line")
    T, state = _through(pts, edge, use, s, total, ax, p, closed)
    # the measurements de-noised a little (over denoise: a corner of the curb survives that), and the smooth line
    # held within max_shift of it
    Td = curves.resample(T, 0.02, closed)
    R = _denoise(Td, p.denoise / 0.02, closed)
    Y, _ = curves.smooth_line(Td, p.smooth, np.inf, closed)
    d = Y - R
    dn = np.linalg.norm(d, axis=1)
    X = R + d * np.minimum(1.0, p.max_shift / np.maximum(dn, 1e-12))[:, None]
    tree = cKDTree(X)
    off = tree.query(edge[use])[0]
    move = tree.query(pts)[0]
    spans = {1: [], 2: []}
    k = 0
    while k < m:
        if state[k] == 0:
            k += 1
            continue
        j = k
        while j < m and state[j] == state[k]:
            j += 1
        a0, a1 = s[max(k - 1, 0)], s[min(j, m - 1)]
        spans[int(state[k])].append((float(a0), float(a1 - a0)))
        k = j
    return {"vertices": X, "samples": pts, "state": state, "bridged": spans[1], "kept": spans[2],
            "side": "left" if sgn > 0 else "right", "sections": m, "anchors": int(use.sum()),
            "low_steps": int(low.sum()), "outliers": outliers, "no_edge": int((~has).sum()),
            "top_unseen": int(unseen.sum()), "height_median": med,
            "off_anchors_rms": float(np.sqrt(np.mean(off**2))), "off_anchors_max": float(off.max()),
            "largest_move": float(move.max()), "mean_move": float(move.mean()), "params": asdict(p)}  # fmt: skip

