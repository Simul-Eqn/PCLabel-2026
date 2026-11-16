"""Joining pieces of line into continuous lines (the user's request of 2026-11-05: "a feature to clean up curb trace":
a directory of curb traces, made in several tries, that are supposed to be one line).

For lines of one label (e.g. every ``curb_top`` in a "curb trace" directory):
1. **Repeats:** the longest line is kept; a vertex of another line within ``overlap`` (across the height axis) of a line
   already kept is a repeat (a retry over the same stretch) and is dropped. What is left of a line splits into runs;
   runs shorter than ``min_length`` are left out.
2. **Joining:** the closest pair of free ends (of different lines) within ``join_gap`` is joined, if the joining piece
   carries on both lines (it turns no more than ``max_angle`` from either line's direction at its end), and again
   until no pair is left. Each join's length is recorded (a gap bridged: it was not traced).
3. **Closing:** a line whose two ends come within ``join_gap`` of each other (and is longer than four times that) is
   closed (a kerb around an island).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from scipy.spatial import cKDTree

from pclabel.analysis import curves


@dataclass(frozen=True)
class JoinParams:
    overlap: float = 0.10  # m: a vertex this close to a line already kept repeats it (a retry), and is dropped
    join_gap: float = 1.0  # m: ends closer than this are joined; longer gaps stay gaps
    max_angle: float = 60.0  # degrees: the joining piece turns no more than this from either line
    min_length: float = 0.5  # m: pieces shorter than this (after dropping repeats) are left out (slivers)


def _flat(v, n):
    return v - np.outer(v @ n, n)


def join_lines(lines, axis=(0.0, 0.0, 1.0), p: JoinParams = JoinParams()) -> list:
    """``lines``: [(m, 3) vertices]. Returns [{vertices, closed, sources (input indices), bridged (m each), dropped
    (vertices dropped as repeats, per input)}] sorted longest first."""
    n = np.asarray(axis, np.float64)
    n /= np.linalg.norm(n)
    lines = [np.asarray(v, np.float64).reshape(-1, 3) for v in lines]
    order = sorted(range(len(lines)), key=lambda i: -curves.length(lines[i]))
    kept_pts, segs, dropped = [], [], {}
    for i in order:
        v = lines[i]
        if len(v) < 2:
            continue
        if kept_pts:
            tree = cKDTree(_flat(np.vstack(kept_pts), n))
            dist, _ = tree.query(_flat(v, n))
            covered = dist < p.overlap
        else:
            covered = np.zeros(len(v), bool)
        dropped[i] = int(covered.sum())
        runs, cur = [], []
        for k in range(len(v)):
            if covered[k]:
                if cur:
                    runs.append(cur)
                cur = []
            else:
                cur.append(k)
        if cur:
            runs.append(cur)
        for r in runs:
            # reach back to the neighbouring covered vertices, so the run meets the line it repeats
            lo, hi = max(r[0] - 1, 0), min(r[-1] + 1, len(v) - 1)
            seg = v[lo : hi + 1]
            if len(seg) >= 2 and curves.length(seg) >= p.min_length:
                segs.append({"vertices": seg, "sources": [i], "bridged": []})
                kept_pts.append(curves.resample(seg, 0.02))
    # joining the closest free ends, while the join carries on both lines
    cos_max = np.cos(np.radians(p.max_angle))

    def end(seg, which):
        """(point, outward unit direction across the height axis) at an end ("a": the first vertex, "b": the last)."""
        v = seg["vertices"]
        if which == "a":
            q, r = v[0], v[min(2, len(v) - 1)]
        else:
            q, r = v[-1], v[max(-3, -len(v))]
        o = _flat((q - r)[None], n)[0]
        return q, o / (np.linalg.norm(o) or 1.0)

    while True:
        best = None
        for i in range(len(segs)):
            for j in range(len(segs)):
                if i == j:
                    continue
                for wi in "ab":
                    for wj in "ab":
                        qi, oi = end(segs[i], wi)
                        qj, oj = end(segs[j], wj)
                        gap = _flat((qj - qi)[None], n)[0]
                        g = float(np.linalg.norm(gap))
                        if g > p.join_gap:
                            continue
                        if g > p.overlap:  # a real gap: the joining piece carries on both lines
                            u = gap / g
                            if np.dot(u, oi) < cos_max or np.dot(-u, oj) < cos_max:
                                continue
                        elif np.dot(oi, -oj) < cos_max:  # ends that meet (a repeat's edge): the lines carry on
                            continue
                        if best is None or g < best[0]:
                            best = (g, i, wi, j, wj)
        if best is None:
            break
        g, i, wi, j, wj = best
        a = segs[i]["vertices"] if wi == "b" else segs[i]["vertices"][::-1]  # ends at the joining end
        b = segs[j]["vertices"] if wj == "a" else segs[j]["vertices"][::-1]  # starts at it
        if g <= p.overlap:
            b = b[1:]  # the ends meet: one vertex for both
        joined = {"vertices": np.vstack([a, b]), "sources": segs[i]["sources"] + segs[j]["sources"],
                  "bridged": segs[i]["bridged"] + segs[j]["bridged"] + ([g] if g > p.overlap else [])}  # fmt: skip
        segs = [s for k, s in enumerate(segs) if k not in (i, j)] + [joined]
    out = []
    for s in segs:
        v = s["vertices"]
        gap = float(np.linalg.norm(_flat((v[-1] - v[0])[None], n)[0]))
        closed = len(v) > 3 and gap <= p.join_gap and curves.length(v) > 4 * p.join_gap
        if closed and gap > 1e-6:
            s["bridged"].append(gap)
        if closed and gap <= 1e-6:
            v = v[:-1]
        out.append({"vertices": v, "closed": bool(closed), "sources": sorted(set(s["sources"])),
                    "bridged": [float(x) for x in s["bridged"]],
                    "dropped": {k: dropped.get(k, 0) for k in sorted(set(s["sources"]))}})  # fmt: skip
    return sorted(out, key=lambda o: -curves.length(o["vertices"]))


def record(p: JoinParams) -> dict:
    return asdict(p)
