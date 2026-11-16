"""Semi-automatic curb tracing (the user's request of 2026-11-05, after LiDAR360MLS's "Detect Road Side Line").

The labeller clicks two points on the road beside a kerb: the first is where to start, the second gives the
direction. The tracer then steps along the kerb (``step``, 0.3 m): at each step it takes a cross-section across the
direction (``step`` long, from ``behind`` on the road side to ``search`` towards the kerb side), finds the road's
height near the start of the section, follows the road outward bin by bin (so a sloping gutter is still road), and
finds the step: the **toe** (the last road bin before the rise) and the **lip** (the first bin at least ``curb_height`` above the road, followed by more kerb top). Both are
measured, so the lip line is the kerb's real top edge, not the toe plus a fixed height. The next section is placed
``step`` further along the toe line, at the same offset from it; the direction follows the toes (smoothed).

Where a section has no kerb step (a drain inlet, a toe hidden from the scanner) it goes on straight for up to
``max_gap`` and leaves a gap in the lines: a gap is never drawn over. It stops where there is no step of at least
``curb_height`` for longer (a lowered kerb, a driveway, the end of the scan), where the face and the top are further apart than ``max_face`` (an occluded face is allowed up to that: the
toe is then the last road point seen, and the step is marked ``seen=False``), where every section for ``max_gap``
would turn it more than ``max_turn`` (a single such toe is an outlier, skipped as a gap), after ``max_length``, or when it comes back to its start (a closed kerb around an island).

All parameters go with the result: they are the thresholds a curb algorithm would use, so a traced line and the
labeller's corrections to it are evidence for tuning them. Heights are along the active height axis.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace

import numpy as np

from pclabel.analysis.geometry import direction_axes
from pclabel.tools.tracing import drive
from pclabel.tools.volume import VolumeParams, select_volume

SIDES = ("auto", "left", "right")
FOLLOW = ("toe", "lip")


@dataclass(frozen=True)
class CurbTraceParams:
    step: float = 0.30  # m along the kerb between sections (LiDAR360MLS's "arc step")
    curb_height: float = 0.06  # m: the least step that counts as a kerb (LiDAR360MLS uses 0.10; the kerbs of
    # ExchangeGreen 1 step 8-10 cm, so 0.10 missed them: lowered 2026-11-05)
    side: str = "auto"  # the kerb is on this side of the direction clicked (as seen from above, z up); "auto": found
    # at the start (either side; a start clicked on the kerb's top is moved down onto the road beside it)
    search: float = 1.0  # m across, towards the kerb side, to look for the step
    behind: float = 0.25  # m across on the road side of the section's centre (the road level is taken there)
    bin: float = 0.02  # m: the cross-section's bins
    min_points: int = 3  # points a bin needs to count
    road_tolerance: float = 0.03  # m: a bin is road when all of it is within this of the last road bin (the road is
    # followed outward bin by bin, so a sloping gutter is road)
    max_face: float = 0.40  # m: the most the toe and the lip may be apart across (a battered face, or one not seen)
    max_turn: float = 30.0  # degrees per step
    max_gap: float = 1.0  # m: sections without a kerb step are skipped up to this far (a drain inlet, a hidden
    # toe) and the lines are broken there; beyond it the trace stops
    max_length: float = 300.0  # m
    low_percentile: float = 20.0  # a bin's surface height: this percentile of its points' heights (below clutter)
    follow: str = "toe"  # "toe": step along the toe (both edges measured); "lip": along the kerb's top edge only,
    # which needs no visible road beside the kerb (a toe hidden from the scanner, a gutter full of leaves)
    box_below: float = 0.5  # m: a section's box reaches this far below its centre (the road beside the kerb)
    box_above: float = 0.8  # m: and at least this far above it (more for a high curb_height: 3 x it + 0.3)


@dataclass
class Section:
    """One cross-section's kerb step (local coordinates). ``a_toe``, ``a_lip`` and ``h_lip`` are across and up from
    the section's centre; ``profile`` is (bin centres across, each bin's surface height); ``across`` points from the
    road towards the kerb."""

    toe: np.ndarray
    lip: np.ndarray
    height: float
    face: float
    seen: bool  # every bin between toe and lip has points
    top_seen: bool  # False: only the face was scanned, and the lip is the top of the face seen
    road: float
    points: int
    a_toe: float
    a_lip: float
    h_lip: float
    across: np.ndarray
    profile: tuple
    piece: int = 0  # set by the trace: the piece of line it belongs to

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass
class NoStep:
    """A cross-section with no kerb step: why not, and the most the surface rose (if anything was seen)."""

    why: str
    rise: float | None = None


def section(xyz, tiles, centre, axis, direction, p: CurbTraceParams, filters=None) -> Section | NoStep:
    """One cross-section at ``centre`` (local, on the road beside the kerb): the toe and the lip, or why there is no
    kerb step there."""
    n, d, left = direction_axes(axis, direction)
    s = -left if p.side == "right" else left
    width = p.search + p.behind
    base = np.asarray(centre, np.float64) + s * (p.search - p.behind) / 2  # the box is centred on its base
    vp = VolumeParams(shape="box", length=p.step, width=width, bottom=-p.box_below,
                      top=max(p.box_above, 3 * p.curb_height + 0.3))
    # the box's v axis is cross(n, d) = left; its centre is shifted towards the kerb side, so it is symmetric
    idx = select_volume(xyz, tiles, base, n, d, vp, filters)
    if idx.size < p.min_points * 4:
        return NoStep("no points")
    q = np.asarray(xyz[idx], np.float64) - np.asarray(centre, np.float64)
    a = q @ s  # across, towards the kerb
    h = q @ n
    nb = int(np.ceil(width / p.bin))
    k = np.floor((a + p.behind) / p.bin).astype(np.int64)
    ok = (k >= 0) & (k < nb)
    k, h = k[ok], h[ok]
    order = np.argsort(k, kind="stable")
    k, h = k[order], h[order]
    starts = np.searchsorted(k, np.arange(nb + 1))
    surf = np.full(nb, np.nan)  # each bin's surface (a low percentile)
    high = np.full(nb, np.nan)  # and its top (a high one): a bin on the face spans the rise
    for j in range(nb):
        if starts[j + 1] - starts[j] >= p.min_points:
            surf[j], high[j] = np.percentile(h[starts[j] : starts[j + 1]], (p.low_percentile, 100 - p.low_percentile))
    centres = -p.behind + (np.arange(nb) + 0.5) * p.bin
    near = (centres <= 0.05) & ~np.isnan(surf)
    lip_only = p.follow == "lip"
    if near.sum() < 2:
        if not lip_only or np.all(np.isnan(surf)):
            return NoStep("no road beside the start")
        road = float(np.nanmin(surf))  # following the top edge: the lowest surface seen will do as the road
    else:
        road = float(np.median(surf[near]))
    # outward from the centre, follow the road bin by bin: a bin is road when all of it (its low and high
    # percentiles) is within road_tolerance of the last road bin, so a sloping gutter is followed, while a bin on
    # the face (spanning the rise) or with clutter on it is not road. The lip is the first bin curb_height above the
    # last road bin, with more kerb top after it.
    occ_all = np.flatnonzero(~np.isnan(surf))
    occ = occ_all[centres[occ_all] > -p.bin]
    last_h, last = road, None
    for i, j in enumerate(occ):
        if surf[j] <= last_h + p.road_tolerance and high[j] <= last_h + p.road_tolerance:
            last_h, last = float(surf[j]), j
            continue
        if surf[j] < last_h + p.curb_height:
            continue  # the face, or clutter
        top = occ[i : i + 4]
        top_seen = True
        if len(top) < 3 or np.any(surf[top[:3]] < last_h + p.curb_height - 0.02):
            after = occ[i + 1 :]
            if len(after) and centres[after[0]] - centres[j] <= 0.12:
                continue  # a single high bin: clutter, not a kerb top
            # nothing seen for 0.12 m behind the rise: a face whose top the scanner does not see; the lip is the
            # top of the face seen (its far edge, at the highest of its bins)
            top_seen = False
        if last is None:
            if not lip_only:
                return NoStep("no road before the step")
            before = occ_all[centres[occ_all] < centres[j]]
            if not before.size:
                return NoStep("no road before the step")
            last = int(before[np.argmin(surf[before])])  # the lowest surface before the rise stands for the toe
        t = last
        a_toe = centres[t] + p.bin / 2
        a_lip = centres[j] - p.bin / 2 if top_seen else centres[j] + p.bin / 2
        if a_lip - a_toe > p.max_face and not lip_only:
            return NoStep(f"the face is {a_lip - a_toe:.2f} m wide (more than {p.max_face:.2f} m)")
        seen = bool(np.all(np.isin(np.arange(t + 1, j), occ_all)))  # every bin between toe and lip has points
        h_toe = float(surf[t])
        h_lip = float(np.median(surf[top[:3]])) if top_seen else float(np.nanmax(high[t + 1 : j + 1]))
        c = np.asarray(centre, np.float64)
        return Section(c + a_toe * s + h_toe * n, c + a_lip * s + h_lip * n, h_lip - h_toe, a_lip - a_toe, seen,
                       top_seen, road, int(idx.size), a_toe, a_lip, h_lip, s, (centres, surf))
    rise = float(np.nanmax(surf[occ]) - road) if len(occ) else 0.0
    return NoStep(f"no step of {p.curb_height:.2f} m within {p.search:.2f} m", rise)


def find_start(xyz, tiles, start, axis, direction, p: CurbTraceParams, filters=None) -> dict:
    """Where and on which side the kerb is, from a start click: on the road beside the kerb (either side), or on the
    kerb's top (then the start moves onto the road below, ``moved``). Returns {side, start (local), section, moved},
    or {why} saying what was seen (the biggest rise on each side)."""
    n, d, left = direction_axes(axis, direction)
    c = np.asarray(start, np.float64)
    sides = SIDES[1:] if p.side == "auto" else (p.side,)
    found, rises = [], {}
    for side in sides:  # the start on the road
        sec = section(xyz, tiles, c, n, d, replace(p, side=side), filters)
        if isinstance(sec, NoStep):
            rises[side] = sec.rise
        else:
            s = -left if side == "right" else left
            found.append((float(np.dot(sec.toe - c, s)), side, c, sec, False))
    if not found and p.side == "auto":  # the start on the kerb's top: the road is down one side of it
        for side in SIDES[1:]:
            s = -left if side == "right" else left
            for off in (0.3, 0.6, 0.9):
                c2 = c + off * s - p.curb_height * 1.5 * n  # down on the road, if it is there
                other = "left" if side == "right" else "right"  # the kerb is back towards the click
                sec = section(xyz, tiles, c2, n, d, replace(p, side=other), filters)
                if isinstance(sec, Section) and -0.05 <= float(np.dot(sec.lip - c2, -s)) <= off + 0.05:  # back at the click
                    found.append((off, other, c2, sec, True))
                    break
            if found:
                break
    if not found:
        seen = ", ".join(f"{r:.2f} m on the {k}" for k, r in rises.items() if r is not None) or "no road"
        return {"why": f"no step of {p.curb_height:.2f} m within {p.search:.2f} m of the start (the most it rises: "
                       f"{seen}). Click on the road 0.1 to 0.5 m from the kerb, or lower the curb height"}  # fmt: skip
    _, side, c2, sec, moved = min(found, key=lambda f: abs(f[0]))
    if moved:  # on the road 0.3 m from the toe found, at its height, as if clicked there
        s = -left if side == "right" else left
        c2 = sec.toe - 0.3 * s
    return {"side": side, "start": c2, "section": sec, "moved": moved}


def trace(xyz, tiles, start, toward, axis, p: CurbTraceParams = CurbTraceParams(), filters=None):
    """Follow a kerb from ``start`` (local, on the road beside it) in the direction of ``toward``. A generator: yields
    each step's section ({toe, lip, height, face, seen, ...}); its return value (StopIteration.value) is
    {"stopped": reason, "closed": bool}."""
    if p.side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    if p.follow not in FOLLOW:
        raise ValueError(f"follow must be one of {FOLLOW}")
    key = p.follow  # the edge the trace steps along
    n, d, _ = direction_axes(axis, np.asarray(toward, np.float64) - np.asarray(start, np.float64))

    def signed(a, b):
        """The angle (degrees) from direction a to b about the height axis (+: to the left)."""
        return float(np.degrees(np.arctan2(np.dot(n, np.cross(a, b)), np.dot(a, b))))

    def turned(v, deg):
        t = np.radians(deg)
        return v * np.cos(t) + np.cross(n, v) * np.sin(t)

    first = find_start(xyz, tiles, start, n, d, p, filters)
    if "why" in first:
        return {"stopped": first["why"], "closed": False, "side": p.side}
    p = replace(p, side=first["side"])
    c = first["start"]
    first_toe, run, length, offset, missed, piece, rate, n_steps = None, [], 0.0, None, [], 0, 0.0, 0
    ahead = d  # the direction the next section was placed along
    halved = False  # the section in hand is a retry at half a step (a tight curve cuts across in a full one)
    while True:
        sec = section(xyz, tiles, c, n, ahead, p, filters)
        bad = isinstance(sec, NoStep)
        if not bad and run and not missed:
            st = getattr(sec, key) - run[-1]
            st -= np.dot(st, n) * n
            bad = np.linalg.norm(st) > 1e-6 and abs(signed(ahead, st / np.linalg.norm(st))) > p.max_turn
        if bad and run and not missed and not halved:  # once: half a step on, turned on at the curve's rate
            halved = True
            ahead = turned(ahead, rate)
            left_h = np.cross(n, ahead)
            s_h = -left_h if p.side == "right" else left_h
            c = run[-1] - offset * s_h + 0.5 * p.step * ahead
            c = c - np.dot(c - run[-1], n) * n
            continue
        halved = False
        if isinstance(sec, NoStep):
            missed.append(sec.why)
            if first_toe is None or len(missed) * p.step > p.max_gap + 1e-9:
                return {"stopped": missed[0], "closed": False, "side": p.side}
            c = c + p.step * ahead  # on over the gap, along the curve
            continue
        toe = getattr(sec, key)
        if run and not missed:  # an edge point that turns the line much more than its curve does is an outlier
            st = toe - run[-1]  # (clutter, a drain): a gap
            st -= np.dot(st, n) * n
            if np.linalg.norm(st) > 1e-6:
                turn = signed(ahead, st / np.linalg.norm(st))
                if abs(turn) > p.max_turn:
                    missed.append(f"turned {turn:.0f} degrees in one step (more than {p.max_turn:.0f} off its curve)")
                    c = c + p.step * ahead
                    continue
        if missed:  # after a gap: a new piece of line; the direction is kept, not taken from across the gap
            piece += 1
            missed = []
            run = []
        sec.piece = piece
        if run:
            length += float(np.linalg.norm(toe - run[-1]))
        run.append(toe)
        n_steps += 1
        if first_toe is None:
            first_toe = toe
        yield sec.to_dict()
        # the direction from a chord over the last 3 steps, and how fast it turns from the 3 before (noise in single
        # steps averages out; a tight curve is followed by turning on at that rate)
        if len(run) >= 4:
            ch = run[-1] - run[-4]
            ch -= np.dot(ch, n) * n
            if np.linalg.norm(ch) > 1e-6:
                new_d = ch / np.linalg.norm(ch)
                if len(run) >= 7:
                    ch0 = run[-4] - run[-7]
                    ch0 -= np.dot(ch0, n) * n
                    if np.linalg.norm(ch0) > 1e-6:
                        rate = float(np.clip(signed(ch0 / np.linalg.norm(ch0), new_d) / 3, -p.max_turn / 2, p.max_turn / 2))
                d = new_d
        if n_steps > 8 and np.linalg.norm((toe - first_toe) - np.dot(toe - first_toe, n) * n) < p.step * 0.75:
            return {"stopped": "back at the start: a closed kerb", "closed": True, "side": p.side}
        if length >= p.max_length:
            return {"stopped": f"{p.max_length:.0f} m traced (the most)", "closed": False, "side": p.side}
        left = np.cross(n, d)
        s = -left if p.side == "right" else left
        if offset is None:  # keep the start's distance from the edge, within reason
            offset = float(np.clip(np.dot(toe - c, s), 0.15, 0.5 if key == "toe" else 0.8))
        ahead = turned(d, 2 * rate) if len(run) >= 4 else d  # the chord lags the curve by 1.5 steps
        left = np.cross(n, ahead)
        s = -left if p.side == "right" else left
        c = toe - offset * s + p.step * ahead
        c = c - np.dot(c - toe, n) * n  # at the edge's height


def run_trace(xyz, tiles, start, toward, axis, p: CurbTraceParams = CurbTraceParams(), filters=None,
              progress=None) -> dict:  # fmt: skip
    """The whole trace: {toe (k, 3), lip (k, 3), height (k,), face (k,), seen (k,), piece (k,: the lines break
    between pieces, over gaps), stopped, closed, params}. ``progress(sections so far)`` may return False to stop
    (then ``stopped`` says so). ``top_seen`` (k,): False where the kerb's top was not seen (the lip is then the top
    of the face seen)."""
    secs, end = drive(trace(xyz, tiles, start, toward, axis, p, filters), progress,
                      {"stopped": "stopped by the labeller", "closed": False, "side": None})
    return {
        "toe": np.array([s["toe"] for s in secs]).reshape(-1, 3),
        "lip": np.array([s["lip"] for s in secs]).reshape(-1, 3),
        "height": np.array([s["height"] for s in secs]),
        "face": np.array([s["face"] for s in secs]),
        "seen": np.array([s["seen"] for s in secs], bool),
        "top_seen": np.array([s.get("top_seen", True) for s in secs], bool),
        "piece": np.array([s["piece"] for s in secs], np.int64),
        **end,
        "params": dict(asdict(p), side=end.get("side") or p.side),
    }
