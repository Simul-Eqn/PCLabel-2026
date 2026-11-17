"""Geometry of drawn curves: a plane perpendicular to the height axis fitted to clicked points, the
clicked points projected onto it, and a smooth curve through them.

The curve is piecewise cubic Bézier, one piece between consecutive nodes; a closed curve (a loop) has one more piece,
from the last node back to the first. Each node has two handles (offsets from the node, in the plane), as in vector
drawing programs:
- **automatic** (the default): the handles point along the chord from the previous to the next node, a third of the
  adjacent piece long on each side, which gives a smooth curve through the nodes;
- **manual**: the labeller sets the gradient by dragging a handle. The two handles stay on one line through the
  node (the curve stays smooth there) and keep their own lengths.

All arrays are float64 (n, 3) in one coordinate frame (world or local, consistently).
"""

from __future__ import annotations

import numpy as np


def fit_height(points: np.ndarray, normal, origin) -> float:
    """The height along ``normal`` (measured from ``origin``) of the plane perpendicular to it that fits ``points``
    best in least squares: their mean height."""
    n = np.asarray(normal, np.float64)
    return float(np.mean((np.asarray(points, np.float64) - np.asarray(origin, np.float64)) @ n))


def project(points: np.ndarray, normal, origin, h: float) -> np.ndarray:
    """The closest points on the plane ``dot(p - origin, normal) = h``."""
    n = np.asarray(normal, np.float64)
    p = np.asarray(points, np.float64)
    d = (p - np.asarray(origin, np.float64)) @ n - h
    return p - d[:, None] * n


def _unit(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v, axis=-1, keepdims=True)
    return np.divide(v, norm, out=np.zeros_like(v), where=norm > 1e-12)


def auto_handles(nodes: np.ndarray, closed: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """(in, out) handle offsets that make a smooth curve through ``nodes``. On an open curve the first node has no
    in handle and the last no out handle (zeros); on a closed one the neighbours wrap round."""
    p = np.asarray(nodes, np.float64)
    n = len(p)
    h_in, h_out = np.zeros_like(p), np.zeros_like(p)
    if n < 2:
        return h_in, h_out
    if closed and n >= 3:
        nxt, prv = np.roll(p, -1, axis=0), np.roll(p, 1, axis=0)
        u = _unit(nxt - prv)
        return -u * (np.linalg.norm(p - prv, axis=1) / 3)[:, None], u * (np.linalg.norm(nxt - p, axis=1) / 3)[:, None]
    direction = np.empty_like(p)
    direction[1:-1] = p[2:] - p[:-2]
    direction[0] = p[1] - p[0]
    direction[-1] = p[-1] - p[-2]
    u = _unit(direction)
    seg = np.linalg.norm(np.diff(p, axis=0), axis=1)
    h_out[:-1] = u[:-1] * (seg / 3)[:, None]
    h_in[1:] = -u[1:] * (seg / 3)[:, None]
    return h_in, h_out


def resolve_handles(nodes, h_in, h_out, closed: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Handle offsets with automatic ones where a node's handles are NaN (not set by the labeller)."""
    a_in, a_out = auto_handles(nodes, closed)
    manual = manual_nodes(h_in)
    return np.where(manual[:, None], h_in, a_in), np.where(manual[:, None], h_out, a_out)


def manual_nodes(h_in) -> np.ndarray:
    return np.all(np.isfinite(np.asarray(h_in, np.float64)), axis=1)


def _pieces(n: int, closed: bool):
    """(start node, end node) of every piece."""
    return [(k, (k + 1) % n) for k in range(n if closed and n >= 3 else n - 1)]


def bezier(nodes, h_in, h_out, step: float = 0.02, closed: bool = False) -> np.ndarray:
    """Points along the curve, about ``step`` apart (at most 2000 per piece), through every node; a closed curve
    ends where it starts."""
    p = np.asarray(nodes, np.float64)
    if len(p) < 2:
        return p.copy()
    i_, o_ = resolve_handles(p, h_in, h_out, closed)
    out = []
    pieces = _pieces(len(p), closed)
    for j, (k, k1) in enumerate(pieces):
        c = np.array([p[k], p[k] + o_[k], p[k1] + i_[k1], p[k1]])
        span = np.linalg.norm(np.diff(c, axis=0), axis=1).sum()
        m = int(np.clip(np.ceil(span / step), 4, 2000))
        t = np.linspace(0.0, 1.0, m + 1)[:, None]
        if j < len(pieces) - 1:
            t = t[:-1]  # the next piece starts with this node
        s = 1.0 - t
        out.append(s**3 * c[0] + 3 * s**2 * t * c[1] + 3 * s * t**2 * c[2] + t**3 * c[3])
    return np.vstack(out)


def set_handle(node, h_in, h_out, side: int, target, normal) -> tuple[np.ndarray, np.ndarray]:
    """New (in, out) offsets of one node after dragging its out (``side`` +1) or in (-1) handle to ``target``. The
    handle stays in the plane (perpendicular to ``normal``); the other handle turns to stay on the same line and
    keeps its length."""
    n = np.asarray(normal, np.float64)
    v = np.asarray(target, np.float64) - np.asarray(node, np.float64)
    v = v - np.dot(v, n) * n
    h_in, h_out = np.asarray(h_in, np.float64).copy(), np.asarray(h_out, np.float64).copy()
    moved, other = (h_out, h_in) if side > 0 else (h_in, h_out)
    keep = float(np.linalg.norm(other)) if np.all(np.isfinite(other)) else 0.0
    moved[:] = v
    if np.linalg.norm(v) > 1e-12:
        other[:] = -v / np.linalg.norm(v) * (keep if keep > 0 else np.linalg.norm(v))
    return h_in, h_out


def _dp(v: np.ndarray, tol: float) -> list:
    """Douglas-Peucker: indices of the vertices kept so that the straight pieces stay within ``tol`` (3D)."""
    keep = {0, len(v) - 1}
    stack = [(0, len(v) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        ab = v[b] - v[a]
        L = float(np.linalg.norm(ab))
        w = v[a + 1 : b] - v[a]
        d = np.linalg.norm(np.cross(w, ab / L), axis=1) if L > 1e-12 else np.linalg.norm(w, axis=1)
        k = int(np.argmax(d))
        if d[k] > tol:
            keep.add(a + 1 + k)
            stack += [(a, a + 1 + k), (a + 1 + k, b)]
    return sorted(keep)


def resample(vertices, step: float, closed: bool = False) -> np.ndarray:
    """Points ``step`` apart (about) along a line through the vertices, its vertices among them; a closed line's
    first point is not repeated at the end."""
    v = np.asarray(vertices, np.float64).reshape(-1, 3)
    if closed and len(v) > 2:
        v = np.vstack([v, v[:1]])
    out = [v[:1]]
    for a, b in zip(v[:-1], v[1:]):
        k = max(1, int(np.ceil(np.linalg.norm(b - a) / step)))
        out.append(a + (np.arange(1, k + 1) / k)[:, None] * (b - a))
    p = np.vstack(out)
    return p[:-1] if closed and len(v) > 3 else p


def even_points(vertices, step: float, closed: bool = False) -> np.ndarray:
    """Points evenly spaced, about ``step`` apart, along the line by length. Unlike ``resample`` the vertices are not
    kept, so a densely sampled line gives points ``step`` apart too. A closed line's first point is not repeated."""
    v = np.asarray(vertices, np.float64).reshape(-1, 3)
    if closed and len(v) > 2:
        v = np.vstack([v, v[:1]])
    s = arc_length(v)
    k = max(2, int(round(s[-1] / step)) + 1)
    t = np.linspace(0.0, s[-1], k)
    if closed and len(vertices) > 2:
        t = t[:-1]
    return np.column_stack([np.interp(t, s, v[:, j]) for j in range(3)])


def sample_along(line, spacing: float):
    """An open line sampled every ``spacing`` along each piece (its vertices among the samples), with the unit
    direction and the distance along the line at each sample. Returns (points, directions, distances, length)."""
    v = np.asarray(line, np.float64)
    seg = np.diff(v, axis=0)
    L = np.linalg.norm(seg, axis=1)
    keep = L > 1e-9
    v = np.vstack([v[:1], v[1:][keep]])
    seg, L = seg[keep], L[keep]
    if not len(seg):
        raise ValueError("the reference line has no length")
    pts, dirs, s = [], [], []
    acc = 0.0
    for a, d, ln in zip(v[:-1], seg, L):
        k = max(1, int(np.ceil(ln / spacing)))
        t = np.arange(k) / k
        pts.append(a + t[:, None] * d)
        dirs.append(np.repeat((d / ln)[None], k, 0))
        s.append(acc + t * ln)
        acc += ln
    pts.append(v[-1:])
    dirs.append((seg[-1] / L[-1])[None])
    s.append(np.array([acc]))
    return np.vstack(pts), np.vstack(dirs), np.concatenate(s), acc


def smooth_line(vertices, scale: float = 1.0, max_shift: float = 0.05, closed: bool = False,
                step: float = 0.02) -> tuple[np.ndarray, float]:  # fmt: skip
    """(smoothed points ``step`` apart, largest shift): a Gaussian average along the line (sigma ``scale`` m of
    length), each point moved at most ``max_shift`` (so a real corner is rounded by no more than that). An open
    line's ends stay where they are (the line is mirrored through each end, so near an end it keeps its direction).
    Heights are smoothed with the rest."""
    p = resample(vertices, step, closed)
    n = len(p)
    if n < 3 or scale <= 0:
        return p, 0.0
    r = max(1, int(np.ceil(3 * scale / step)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) * step / scale) ** 2)
    k /= k.sum()
    if closed:
        reps = r // n + 1
        pad = np.vstack([p] * (2 * reps + 1))
        lo = reps * n - r
        ext = pad[lo : lo + n + 2 * r]
    else:
        m = min(r, n - 1)
        left = 2 * p[0] - p[m:0:-1]
        right = 2 * p[-1] - p[-2 : -m - 2 : -1]
        ext = np.vstack([left, p, right])
        if m < r:  # a line shorter than the kernel: repeat the mirrored ends
            ext = np.vstack([np.repeat(ext[:1], r - m, 0), ext, np.repeat(ext[-1:], r - m, 0)])
    s = np.stack([np.convolve(ext[:, j], k, mode="valid") for j in range(3)], axis=1)
    d = s - p
    dn = np.linalg.norm(d, axis=1)
    f = np.minimum(1.0, max_shift / np.maximum(dn, 1e-12))
    s = p + d * f[:, None]
    if not closed:
        s[0], s[-1] = p[0], p[-1]
    return s, float(np.minimum(dn, max_shift).max())


def wiggle(points, closed: bool = False) -> float:
    """Total turning across the line's direction (degrees, sum of |turn| between consecutive pieces of points about
    evenly spaced): how wavy a line is (a straight line 0, a circle 360)."""
    p = np.asarray(points, np.float64)
    if closed:
        p = np.vstack([p[-1:], p, p[:1]])
    d = np.diff(p[:, :2], axis=0)
    d = d[np.linalg.norm(d, axis=1) > 1e-9]
    a = np.arctan2(d[:, 1], d[:, 0])
    t = (np.diff(a) + np.pi) % (2 * np.pi) - np.pi
    return float(np.degrees(np.abs(t).sum()))


def simplify(vertices, tol: float, closed: bool = False) -> list:
    """Indices of the vertices kept so that straight pieces between them stay within ``tol`` of the others."""
    v = np.asarray(vertices, np.float64).reshape(-1, 3)
    if len(v) <= 2:
        return list(range(len(v)))
    if not closed:
        return _dp(v, tol)
    idx = _dp(np.vstack([v, v[:1]]), tol)[:-1]
    return idx if len(idx) >= 3 else sorted({0, len(v) // 3, 2 * len(v) // 3})


def nodes_within(vertices, tol: float = 0.01, closed: bool = False) -> tuple[list, float]:
    """(indices of ``vertices`` to make nodes of, largest distance of a vertex from the curve): the fewest nodes
    found (Douglas-Peucker at ``tol``, then the vertex furthest from the curve added as a node until every vertex is
    within ``tol``) so that the curve with automatic handles through them passes within ``tol`` of every vertex.
    For a traced line made into an editable curve."""
    from scipy.spatial import cKDTree

    v = np.asarray(vertices, np.float64).reshape(-1, 3)
    if len(v) <= 2:
        return list(range(len(v))), 0.0
    idx = _dp(np.vstack([v, v[:1]]), tol)[:-1] if closed else _dp(v, tol)
    if closed and len(idx) < 3:
        idx = sorted({0, len(v) // 3, 2 * len(v) // 3})
    while True:
        nodes = v[idx]
        nan = np.full_like(nodes, np.nan)
        d = cKDTree(bezier(nodes, nan, nan.copy(), min(0.01, tol / 2), closed)).query(v)[0]
        k = int(np.argmax(d))
        if d[k] <= tol or k in idx:
            return idx, float(d.max())
        idx = sorted(idx + [k])


def length(polyline, closed: bool = False) -> float:
    """The line's length; ``closed``: with the piece from the last vertex back to the first (more than 2 vertices)."""
    p = np.asarray(polyline, np.float64)
    out = float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum()) if len(p) > 1 else 0.0
    return out + (float(np.linalg.norm(p[-1] - p[0])) if closed and len(p) > 2 else 0.0)


def arc_length(polyline) -> np.ndarray:
    """(m,): the distance along the line to each vertex, from 0."""
    p = np.asarray(polyline, np.float64)
    return np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]


def closest(nodes, h_in, h_out, point, samples: int = 64, closed: bool = False) -> tuple[int, float]:
    """(piece, t): where the curve comes closest to ``point`` (piece k runs from node k to the next, t in 0..1)."""
    p = np.asarray(nodes, np.float64)
    i_, o_ = resolve_handles(p, h_in, h_out, closed)
    t = np.linspace(0.0, 1.0, samples + 1)[:, None]
    s = 1.0 - t
    best = (np.inf, 0, 0.0)
    for k, k1 in _pieces(len(p), closed):
        c = (p[k], p[k] + o_[k], p[k1] + i_[k1], p[k1])
        q = s**3 * c[0] + 3 * s**2 * t * c[1] + 3 * s * t**2 * c[2] + t**3 * c[3]
        d = np.linalg.norm(q - np.asarray(point, np.float64), axis=1)
        j = int(np.argmin(d))
        if d[j] < best[0]:
            best = (float(d[j]), k, float(t[j, 0]))
    return best[1], best[2]


def insert_position(nodes, h_in, h_out, point, closed: bool = False) -> int:
    """Where a new node at ``point`` goes in the node order: between the nodes of the piece the curve comes
    closest to it, or (open curves) before the first / after the last node when it lies beyond that end."""
    p = np.asarray(nodes, np.float64)
    q = np.asarray(point, np.float64)
    k, t = closest(p, h_in, h_out, q, closed=closed)
    if closed:
        return k + 1  # the closing piece (last -> first) takes it after the last node
    if k == 0 and t == 0.0 and np.dot(q - p[0], p[1] - p[0]) < 0:
        return 0
    if k == len(p) - 2 and t == 1.0 and np.dot(q - p[-1], p[-1] - p[-2]) > 0:
        return len(p)
    return k + 1


def fit_through_ends(points: np.ndarray, axis) -> np.ndarray:
    """Unit normal (pointing along ``axis``, the height axis) of the plane through the first and the last point that
    fits the points between them best (least squares of their distances to it).

    The plane contains the line through the ends, so only its turn about that line is free: the normal is the
    smallest-eigenvalue direction of the scatter of the inner points, measured from the first point,
    perpendicular to that line. With no inner points off the line, it is the plane through the ends that is closest
    to perpendicular to ``axis``.
    """
    p = np.asarray(points, np.float64)
    ax = np.asarray(axis, np.float64) / np.linalg.norm(axis)
    u = p[-1] - p[0]
    span = float(np.linalg.norm(u))
    if span < 1e-9:  # the ends coincide: no line to turn about
        return ax
    u /= span
    e1 = ax - np.dot(ax, u) * u
    if np.linalg.norm(e1) < 1e-9:  # the ends lie along the axis: any plane through them is as good
        e1 = np.cross(u, [1.0, 0.0, 0.0] if abs(u[0]) < 0.9 else [0.0, 1.0, 0.0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(u, e1)
    q = (p[1:-1] - p[0]) @ np.c_[e1, e2]  # inner points across the line, (m, 2)
    m = q.T @ q
    w, v = np.linalg.eigh(m)
    if len(q) == 0 or w[-1] < 1e-12 * span**2 * max(len(q), 1):
        n = e1
    else:
        n = v[0, 0] * e1 + v[1, 0] * e2
    n /= np.linalg.norm(n)
    return n if np.dot(n, ax) >= 0 else -n


def project_onto(points: np.ndarray, normal, point) -> np.ndarray:
    """The closest points on the plane through ``point`` with unit ``normal``."""
    n = np.asarray(normal, np.float64)
    p = np.asarray(points, np.float64)
    return p - ((p - np.asarray(point, np.float64)) @ n)[:, None] * n


def fit_plane_through(points: np.ndarray, axis) -> tuple[np.ndarray, np.ndarray]:
    """(unit normal pointing along ``axis``, centroid): the least-squares plane through all the points, any tilt
    (the normal is the smallest-variance direction of the points). When they lie on a line (or are fewer than
    three), it is the plane through that line closest to perpendicular to ``axis``."""
    p = np.asarray(points, np.float64)
    ax = np.asarray(axis, np.float64) / np.linalg.norm(axis)
    c = p.mean(0)
    q = p - c
    w, v = np.linalg.eigh(q.T @ q)
    if len(p) >= 3 and w[1] > 1e-9 * max(w[2], 1e-12):  # a real plane (two clear spreading directions)
        n = v[:, 0]
    else:  # a line: the plane through it that is as flat (on ``axis``) as possible
        d = v[:, 2]
        n = ax - np.dot(ax, d) * d
        if np.linalg.norm(n) < 1e-9:
            n = np.cross(d, [1.0, 0.0, 0.0] if abs(d[0]) < 0.9 else [0.0, 1.0, 0.0])
        n /= np.linalg.norm(n)
    return (n if np.dot(n, ax) >= 0 else -n), c
