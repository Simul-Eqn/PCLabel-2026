"""ASCII DXF reading: the vector entities of a CAD drawing as 3D polylines in world coordinates.

Written for planimetrics deliveries (ROCK Robotic's, the user's request of 2026-11-04), without a CAD library: only
the entity section and a few header values are needed.

- ``LINE``, ``3DFACE`` (3 or 4 corners, a closed outline), ``POINT``.
- ``POLYLINE`` with its ``VERTEX`` records: 3D polylines as they are; 2D ones in their object coordinate system
  (below); polyface meshes (flag 64) as one closed outline per face.
- ``LWPOLYLINE``: 2D vertices in the object coordinate system at the entity's elevation; arcs (bulges) are
  tessellated.
- ``CIRCLE`` and ``ARC``: tessellated in their object coordinate system.
- ``INSERT`` (a block reference, i.e. a map symbol such as a pole or a manhole): its insertion point only, with the
  block name, scale and rotation. The block's drawing is a cartographic symbol, not geometry of the feature.
- Anything else (text, hatches, splines, ...) is counted as skipped.

**Object coordinate systems:** planar entities store 2D coordinates in a plane given by their extrusion direction
(codes 210/220/230) and an elevation. They are turned into world coordinates with the DXF "arbitrary axis algorithm".
Leaving this out puts a tilted entity kilometres away.

**Units:** ``$INSUNITS`` scales the coordinates to metres (0, unitless, is taken as metres).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# $INSUNITS -> metres per unit
UNITS = {0: 1.0, 1: 0.0254, 2: 0.3048, 3: 1609.344, 4: 0.001, 5: 0.01, 6: 1.0, 7: 1000.0, 10: 0.9144,
         14: 0.1, 21: 1200 / 3937}  # fmt: skip
UNIT_NAMES = {0: "unitless (taken as metres)", 1: "inches", 2: "feet", 3: "miles", 4: "millimetres",
              5: "centimetres", 6: "metres", 7: "kilometres", 10: "yards", 14: "decimetres", 21: "US survey feet"}  # fmt: skip
ARC_STEP_DEG = 5.0  # tessellation of arcs, circles and bulges


@dataclass
class DxfEntity:
    layer: str
    type: str  # the DXF entity type, e.g. POLYLINE
    handle: str  # the DXF handle (code 5): identifies the entity in the drawing
    vertices: np.ndarray  # (n, 3) float64 world, metres; n = 1 for a point or a block reference
    closed: bool = False
    extra: dict = field(default_factory=dict)  # e.g. {"block": "SYM004", "scale": [0.2, 0.2, 0.2], "rotation": 0}


@dataclass
class DxfDrawing:
    path: Path
    version: str  # $ACADVER, e.g. AC1032
    units: str
    scale: float  # metres per drawing unit (applied)
    entities: list
    skipped: Counter  # entity type -> count, of the entities not read
    layer_colors: dict  # layer -> ACI colour number (from the LAYER table)


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:  # DXF before R2007 uses the drawing's code page; ANSI_1252 is the usual one
        return raw.decode("cp1252", errors="replace")


def _pairs(text: str) -> list:
    lines = text.splitlines()
    if len(lines) % 2:
        lines = lines[:-1]
    return [(int(lines[i].strip()), lines[i + 1].strip()) for i in range(0, len(lines), 2)]


def ocs_to_wcs(xyz, extrusion) -> np.ndarray:
    """World coordinates of points in the object coordinate system of ``extrusion`` (the arbitrary axis algorithm)."""
    n = np.asarray(extrusion, np.float64)
    n = n / np.linalg.norm(n)
    xyz = np.asarray(xyz, np.float64).reshape(-1, 3)
    if abs(n[0]) < 1 / 64 and abs(n[1]) < 1 / 64:
        if n[2] > 0 and np.allclose(n, (0, 0, 1)):
            return xyz.copy()
        ax = np.cross((0.0, 1.0, 0.0), n)
    else:
        ax = np.cross((0.0, 0.0, 1.0), n)
    ax /= np.linalg.norm(ax)
    ay = np.cross(n, ax)
    ay /= np.linalg.norm(ay)
    return xyz[:, :1] * ax + xyz[:, 1:2] * ay + xyz[:, 2:3] * n


def _arc(cx, cy, r, a0_deg, a1_deg) -> np.ndarray:
    """(n, 2) points on a counter-clockwise arc from a0 to a1 (degrees)."""
    sweep = (a1_deg - a0_deg) % 360 or 360.0
    k = max(2, int(np.ceil(sweep / ARC_STEP_DEG)) + 1)
    t = np.radians(a0_deg + np.linspace(0, sweep, k))
    return np.c_[cx + r * np.cos(t), cy + r * np.sin(t)]


def _bulged(xy: np.ndarray, bulge: np.ndarray, closed: bool) -> np.ndarray:
    """2D vertices with the arcs of non-zero bulges (tan(angle/4), positive counter-clockwise) tessellated."""
    if not np.any(bulge):
        return xy
    out = []
    n = len(xy)
    for i in range(n if closed else n - 1):
        p, q, b = xy[i], xy[(i + 1) % n], bulge[i]
        out.append(p[None])
        if b == 0:
            continue
        theta = 4 * np.arctan(b)  # included angle, signed (positive: counter-clockwise)
        chord = np.linalg.norm(q - p)
        if chord == 0:
            continue
        left = np.array([-(q - p)[1], (q - p)[0]]) / chord
        c = (p + q) / 2 + left * (chord / 2) * (1 - b * b) / (2 * b)  # the centre
        r = np.linalg.norm(p - c)
        a0 = np.arctan2(p[1] - c[1], p[0] - c[0])
        k = max(2, int(np.ceil(np.degrees(abs(theta)) / ARC_STEP_DEG)) + 1)
        t = a0 + np.linspace(0, theta, k)[1:-1]
        out.append(np.c_[c[0] + r * np.cos(t), c[1] + r * np.sin(t)])
    if not closed:
        out.append(xy[-1:])
    return np.vstack(out)


def _records(pairs, start):
    """The entities of the ENTITIES section: [(type, [(code, value), ...])]."""
    out, cur = [], None
    for code, v in pairs[start:]:
        if code == 0:
            if cur is not None:
                out.append(cur)
            if v == "ENDSEC":
                break
            cur = (v, [])
        elif cur is not None:
            cur[1].append((code, v))
    return out


def _first(g, code, default=None):
    for c, v in g:
        if c == code:
            return v
    return default


def _f(g, code, default=0.0) -> float:
    v = _first(g, code)
    return float(v) if v is not None else default


def _extrusion(g):
    return (_f(g, 210, 0.0), _f(g, 220, 0.0), _f(g, 230, 1.0))


def _header(pairs) -> dict:
    out = {}
    name = None
    for code, v in pairs:
        if code == 0 and v == "ENDSEC":
            break
        if code == 9:
            name = v
            out[name] = []
        elif name is not None:
            out[name].append((code, v))
    return out


def _layer_colors(pairs) -> dict:
    out = {}
    for k, (code, v) in enumerate(pairs):
        if code == 0 and v == "LAYER":
            j, name, color = k + 1, None, None
            while j < len(pairs) and pairs[j][0] != 0:
                if pairs[j][0] == 2 and name is None:
                    name = pairs[j][1]
                elif pairs[j][0] == 62 and color is None:
                    color = abs(int(pairs[j][1]))
                j += 1
            if name is not None:
                out[name] = color
    return out


def read_dxf(path) -> DxfDrawing:
    """The drawing's vector entities in world coordinates (metres). Binary DXF is not read."""
    path = Path(path)
    raw = path.read_bytes()
    if raw.startswith(b"AutoCAD Binary DXF"):
        raise ValueError(f"{path.name} is a binary DXF; save it as ASCII DXF")
    pairs = _pairs(_decode(raw))
    sections = {pairs[k + 1][1]: k + 2 for k, p in enumerate(pairs[:-1]) if p == (0, "SECTION") and pairs[k + 1][0] == 2}
    if "ENTITIES" not in sections:
        raise ValueError(f"{path.name} has no ENTITIES section: not a DXF drawing?")
    hdr = _header(pairs[sections["HEADER"] :]) if "HEADER" in sections else {}
    version = _first(hdr.get("$ACADVER", []), 1, "")
    units = int(_first(hdr.get("$INSUNITS", []), 70, "0"))
    if units not in UNITS:
        raise ValueError(f"{path.name}: drawing units {units} ($INSUNITS) are not supported")
    scale = UNITS[units]
    ents, skipped = [], Counter()
    recs = _records(pairs, sections["ENTITIES"])
    i = 0
    while i < len(recs):
        t, g = recs[i]
        i += 1
        layer, handle = _first(g, 8, "0"), _first(g, 5, "")
        if t == "POLYLINE":
            verts = []
            while i < len(recs) and recs[i][0] == "VERTEX":
                verts.append(recs[i][1])
                i += 1
            if i < len(recs) and recs[i][0] == "SEQEND":
                i += 1
            ents += _polyline(layer, handle, g, verts)
        elif t == "SEQEND" or t == "VERTEX":
            continue
        else:
            e = _entity(t, layer, handle, g)
            if e is None:
                skipped[t] += 1
            else:
                ents.append(e)
    for e in ents:
        e.vertices = e.vertices * scale
    return DxfDrawing(path, version, UNIT_NAMES.get(units, str(units)), scale, ents, skipped, _layer_colors(pairs))


def _entity(t, layer, handle, g):
    if t == "LINE":
        v = np.array([[_f(g, 10), _f(g, 20), _f(g, 30)], [_f(g, 11), _f(g, 21), _f(g, 31)]])
        return DxfEntity(layer, t, handle, v)
    if t == "POINT":
        return DxfEntity(layer, t, handle, np.array([[_f(g, 10), _f(g, 20), _f(g, 30)]]))
    if t == "3DFACE":
        v = np.array([[_f(g, 10 + k), _f(g, 20 + k), _f(g, 30 + k)] for k in range(4)])
        if np.allclose(v[3], v[2]):  # a triangle repeats its third corner
            v = v[:3]
        return DxfEntity(layer, t, handle, v, closed=True)
    if t == "LWPOLYLINE":
        xy, bulge = [], []
        for c, v in g:
            if c == 10:
                xy.append([float(v), 0.0])
                bulge.append(0.0)
            elif c == 20 and xy:
                xy[-1][1] = float(v)
            elif c == 42 and xy:
                bulge[-1] = float(v)
        if not xy:
            return None
        closed = bool(int(_first(g, 70, "0")) & 1)
        xy = _bulged(np.array(xy), np.array(bulge), closed)
        ocs = np.c_[xy, np.full(len(xy), _f(g, 38))]
        return DxfEntity(layer, t, handle, ocs_to_wcs(ocs, _extrusion(g)), closed=closed)
    if t in ("CIRCLE", "ARC"):
        cx, cy, z, r = _f(g, 10), _f(g, 20), _f(g, 30), _f(g, 40)
        a0, a1 = (0.0, 360.0) if t == "CIRCLE" else (_f(g, 50), _f(g, 51))
        xy = _arc(cx, cy, r, a0, a1)
        if t == "CIRCLE":
            xy = xy[:-1]
        ocs = np.c_[xy, np.full(len(xy), z)]
        return DxfEntity(layer, t, handle, ocs_to_wcs(ocs, _extrusion(g)), closed=t == "CIRCLE", extra={"radius": r})
    if t == "INSERT":
        p = ocs_to_wcs([_f(g, 10), _f(g, 20), _f(g, 30)], _extrusion(g))
        extra = {"block": _first(g, 2, ""), "scale": [_f(g, 41, 1.0), _f(g, 42, 1.0), _f(g, 43, 1.0)],
                 "rotation_deg": _f(g, 50)}  # fmt: skip
        return DxfEntity(layer, t, handle, p, extra=extra)
    return None


def _polyline(layer, handle, g, verts) -> list:
    flags = int(_first(g, 70, "0"))
    closed = bool(flags & 1)
    if flags & 64:  # polyface mesh: position vertices, then faces that index them (1-based; negative: invisible edge)
        pos = [[_f(v, 10), _f(v, 20), _f(v, 30)] for v in verts if int(_first(v, 70, "0")) & 64]
        out = []
        for v in verts:
            if int(_first(v, 70, "0")) & 128 and not int(_first(v, 70, "0")) & 64:
                idx = [abs(int(_first(v, c, "0"))) for c in (71, 72, 73, 74)]
                idx = [k - 1 for k in idx if k]
                if len(idx) >= 3:
                    out.append(DxfEntity(layer, "POLYLINE", handle, np.array([pos[k] for k in idx]), closed=True,
                                         extra={"mesh_face": len(out)}))  # fmt: skip
        return out
    if flags & 16:  # polygon mesh (M x N grid): its vertices as one line; rare in planimetrics
        pts = np.array([[_f(v, 10), _f(v, 20), _f(v, 30)] for v in verts])
        return [DxfEntity(layer, "POLYLINE", handle, pts, extra={"polygon_mesh": True})] if len(pts) else []
    if flags & 8:  # 3D polyline: world coordinates
        pts = np.array([[_f(v, 10), _f(v, 20), _f(v, 30)] for v in verts])
        return [DxfEntity(layer, "POLYLINE", handle, pts, closed=closed)] if len(pts) else []
    # 2D polyline: OCS at the polyline's elevation (its 30), with bulges
    if not verts:
        return []
    xy = np.array([[_f(v, 10), _f(v, 20)] for v in verts])
    bulge = np.array([_f(v, 42) for v in verts])
    xy = _bulged(xy, bulge, closed)
    ocs = np.c_[xy, np.full(len(xy), _f(g, 30))]
    return [DxfEntity(layer, "POLYLINE", handle, ocs_to_wcs(ocs, _extrusion(g)), closed=closed)]
