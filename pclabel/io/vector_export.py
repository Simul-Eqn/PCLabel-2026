"""Vector export of the annotations for CAD and GIS (the user's request of 2026-11-05, after LiDAR360MLS's handover:
"feature types in separate, clearly named layers; identifiers and attributes survive export").

- **DXF** (ASCII, AutoCAD R12 / AC1009, which FARO Zone, AutoCAD and Leica software all read): one layer per label
  (its name upper-cased; "UNLABELLED" for none) in the label's colour (the nearest AutoCAD colour index). Segments
  are LINEs, curves and polylines 3D POLYLINEs (closed where they are), markers POINTs. Coordinates are the scan's
  own (scan frame, metres, the project offset added back), so the drawing lies on the scan as an imported one does.
- **GeoJSON** (RFC 7946 geometry, 3D coordinates): a LineString or Point per item with its id, name, kind, label,
  directory path, uncertain mark, closed, length, whether a tool drew it (``assisted``), its source or provenance and
  a marker's attributes. The coordinates are the scan's local metres, not longitude and latitude: the file says so
  (``pclabel.coordinates``), so a GIS should treat it as an unknown local CRS.

Point sets are not vectors and are left out (their labels go out with "export labels").
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from pclabel.store.annotations import jsonable
from pclabel.store.importer import write_json_atomic

SCHEMA = "pclabel.vector-export/1"
UNLABELLED = "UNLABELLED"
VECTOR_KINDS = ("segment", "curve", "polyline", "marker")
ASSISTED_TOOLS = ("curb_trace", "line_trace", "clearance", "cover_template")  # tools that draw lines or markers


def layer_name(label: str) -> str:
    """A DXF layer name for a label: upper case, letters, digits, _ - $ only, at most 31 characters (R12)."""
    if not label:
        return UNLABELLED
    return re.sub(r"[^A-Z0-9_$-]", "_", label.upper())[:31] or UNLABELLED


def aci_of(hex_color: str) -> int:
    """The AutoCAD colour index (1-255) nearest to ``#rrggbb``."""
    from pclabel.store.label_import import aci_rgb

    h = hex_color.lstrip("#")
    c = np.array([int(h[i : i + 2], 16) for i in (0, 2, 4)], float)
    best, best_d = 7, 1e18
    for k in range(1, 256):
        g = aci_rgb(k).lstrip("#")
        d = float(np.sum((np.array([int(g[i : i + 2], 16) for i in (0, 2, 4)], float) - c) ** 2))
        if d < best_d:
            best, best_d = k, d
    return best


def vector_items(model, labels=None) -> list:
    """[(item, label, directory path)] for the segments, curves, polylines and markers (whose label, own or inherited,
    is one of ``labels``, if given), in tree order."""
    out = []
    for x, anc in model.walk():
        if x.kind not in VECTOR_KINDS:
            continue
        lab = model.label_of(x.id)[0]
        if labels is not None and lab not in labels:
            continue
        out.append((x, lab, [a.name for a in anc]))
    return out


def geometry(x) -> tuple[np.ndarray, bool]:
    """(world vertices (n, 3), closed) of a vector item; a closed line's first vertex is not repeated."""
    if x.kind == "segment":
        return np.array([x.a, x.b], np.float64), False
    if x.kind == "marker":
        return np.asarray(x.position, np.float64).reshape(1, 3), False
    if x.kind == "curve":
        return np.asarray(x.polyline(), np.float64), bool(x.closed)
    return np.asarray(x.vertices, np.float64), bool(x.closed)


def assisted(x) -> bool:
    """Whether a tool drew it (a tracer, the clearance check, a template), not the labeller's own clicks."""
    src = getattr(x, "source", None) or {}
    return bool(src.get("assisted")) or src.get("tool") in ASSISTED_TOOLS


def _f(v: float) -> str:
    return repr(float(v))


def write_dxf(path, items, colors: dict) -> dict:
    """Write the items as an R12 DXF, one layer per label (``colors``: label -> "#rrggbb"). Returns a summary
    {layers: {layer: n}, entities}."""
    layers: dict = {}
    for _, lab, _ in items:
        layers.setdefault(layer_name(lab), colors.get(lab, "#ffffff"))
    out = ["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009", "0", "ENDSEC",
           "0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER", "70", str(len(layers) + 1),
           "0", "LAYER", "2", "0", "70", "0", "62", "7", "6", "CONTINUOUS"]  # fmt: skip
    for name, col in layers.items():
        out += ["0", "LAYER", "2", name, "70", "0", "62", str(aci_of(col)), "6", "CONTINUOUS"]
    out += ["0", "ENDTAB", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES",
            "999", "PCLabel vector export: scan frame coordinates, metres"]  # fmt: skip
    counts: dict = {}
    for x, lab, _ in items:
        layer = layer_name(lab)
        v, closed = geometry(x)
        counts[layer] = counts.get(layer, 0) + 1
        if x.kind == "marker":
            out += ["0", "POINT", "8", layer, "10", _f(v[0, 0]), "20", _f(v[0, 1]), "30", _f(v[0, 2])]
        elif x.kind == "segment":
            out += ["0", "LINE", "8", layer, "10", _f(v[0, 0]), "20", _f(v[0, 1]), "30", _f(v[0, 2]),
                    "11", _f(v[1, 0]), "21", _f(v[1, 1]), "31", _f(v[1, 2])]  # fmt: skip
        else:
            if closed and len(v) > 2 and np.linalg.norm(v[0] - v[-1]) < 1e-9:
                v = v[:-1]
            out += ["0", "POLYLINE", "8", layer, "66", "1", "10", "0.0", "20", "0.0", "30", "0.0",
                    "70", str(8 | (1 if closed else 0))]  # fmt: skip
            for p in v:
                out += ["0", "VERTEX", "8", layer, "10", _f(p[0]), "20", _f(p[1]), "30", _f(p[2]), "70", "32"]
            out += ["0", "SEQEND", "8", layer]
    out += ["0", "ENDSEC", "0", "EOF"]
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")
    return {"layers": counts, "entities": sum(counts.values())}


def write_geojson(path, items, project, taxonomy=None) -> dict:
    """Write the items as a GeoJSON FeatureCollection in the scan's coordinates. Returns {features, by_kind}."""
    feats, by_kind = [], {}
    for x, lab, dirs in items:
        v, closed = geometry(x)
        if x.kind == "marker":
            geom = {"type": "Point", "coordinates": v[0].tolist()}
        else:
            pts = np.vstack([v, v[:1]]) if closed and len(v) > 2 and not np.linalg.norm(v[0] - v[-1]) < 1e-9 else v
            geom = {"type": "LineString", "coordinates": pts.tolist()}
        props = {"id": x.id, "name": x.name, "kind": x.kind, "label": lab,
                 "label_path": taxonomy.path_of(lab) if (taxonomy is not None and lab) else ([lab] if lab else []),
                 "layer": layer_name(lab), "path": dirs, "uncertain": bool(x.uncertain), "closed": closed,
                 "length_m": float(np.linalg.norm(np.diff(v, axis=0), axis=1).sum()) + (
                     float(np.linalg.norm(v[-1] - v[0])) if closed and len(v) > 2 else 0.0),
                 "assisted": assisted(x)}  # fmt: skip
        if getattr(x, "source", None):
            props["source"] = jsonable(x.source)
        if getattr(x, "attributes", None):
            props["attributes"] = jsonable(x.attributes)
        feats.append({"type": "Feature", "id": x.id, "geometry": geom, "properties": props})
        by_kind[x.kind] = by_kind.get(x.kind, 0) + 1
    src = project.meta.get("source", {})
    doc = {
        "type": "FeatureCollection",
        "pclabel": {"schema": SCHEMA,
                    "coordinates": "scan frame (local metres, z up), not longitude/latitude: an unknown local CRS",
                    "source": {"file": Path(src.get("path", "")).name, "sha256": src.get("sha256"), "n_valid": project.n},
                    "offset": [float(v) for v in project.offset]},  # fmt: skip
        "features": feats,
    }
    write_json_atomic(Path(path), doc)
    return {"features": len(feats), "by_kind": by_kind}


def write_vectors(path, model, project, colors: dict, labels=None, taxonomy=None) -> dict:
    """DXF or GeoJSON by the file's suffix (.dxf; .geojson or .json)."""
    items = vector_items(model, labels)
    if not items:
        raise ValueError("no segments, curves, polylines or markers to export" + (" with these labels" if labels else ""))
    if Path(path).suffix.lower() == ".dxf":
        return write_dxf(path, items, colors)
    return write_geojson(path, items, project, taxonomy)
