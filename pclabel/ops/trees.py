"""Trees and poles as annotations (no Qt, no GL): what the tree tool saves, so the pipeline saves the same.

An object is a directory (the instance, ``tree NN`` or ``lamp_post NN``) holding a marker named ``position`` (where it
stands: the centre of a circle fitted to its band, at the ground) and the band as a point set. Both keep the fit.
"""

from __future__ import annotations

from dataclasses import asdict

import numpy as np

from pclabel.store.annotations import Marker, PointSet
from pclabel.tools.trees import next_name

TREE = "tree"  # what a saved object is by default; its marker's label
POSITION = "position"  # the name of an object's marker


def band_record(ground, ground_h: float, frame: str, axis, band, fit: dict, offset, what: str,
                params=None) -> dict:  # fmt: skip
    """The tree tool's record (world coordinates) for a band whose circle fit is ``fit`` (``tools.trees.fit_trunk``,
    local), standing on ``ground`` (local) at height ``ground_h`` along ``axis``; ``params``: the TreeParams used."""
    off = np.asarray(offset, np.float64)
    return {"tool": "tree", "params": asdict(params) if params is not None else None, "ground": (np.asarray(ground, np.float64) + off).tolist(), "ground_h": ground_h,
            "height_axis": frame, "axis": [float(v) for v in axis], "band": [float(band[0]), float(band[1])],
            "position": (fit["centre"] + off).tolist(), "radius": fit["radius"], "girth": fit["girth"],
            "rms": fit["rms"], "points_fitted": fit["points"], "points_dropped": fit["dropped"],
            "coverage": fit["coverage"], "object": what}  # fmt: skip


def marker_source(record: dict) -> dict:
    """A tree's (or pole's) marker's source, from its band's record: placed by the tree tool at the band's circle
    centre, at the ground."""
    return {"tool": "tree", "object": record.get("object", TREE), "across": "circle", "height": "ground",
            "points": record.get("points_fitted"), "points_dropped": record.get("points_dropped"),
            "radius": record.get("radius"), "girth": record.get("girth"), "rms": record.get("rms"),
            "coverage": record.get("coverage"), "ground": record.get("ground"), "ground_h": record.get("ground_h"),
            "band": record.get("band"), "height_axis": record.get("height_axis"), "axis": record.get("axis")}  # fmt: skip


def add_object(model, into, record: dict, band_indices, provenance: list, taxonomy):
    """A new object directory inside ``into`` (a directory), named on from the objects there: its marker (labelled
    with what it is, if that is a class) and its band (labelled the taxonomy's ``tree_band`` for a tree, else what
    it is). ``provenance``: how the band was selected (the band's record is appended). Returns the directory."""
    what = record.get("object", TREE)
    known = {c["name"] for c in taxonomy.classes}
    d = model.new_directory(next_name([c.name for c in into.children], what), into.id)
    mk = model.add(Marker(np.asarray(record["position"], np.float64), marker_source(record)), POSITION)
    mk.label = what if what in known else ""
    band_label = taxonomy.tool_label("tree_band") if what == TREE else what
    b0, b1 = record["band"]
    part = "trunk" if what == TREE else "pole"
    ps = model.add(PointSet(np.asarray(band_indices).copy(), [dict(op) for op in provenance] + [record]),
                   f"{part} {b0:.2f} to {b1:.2f} m")  # fmt: skip
    if band_label in known:
        ps.label = band_label
    model.move([mk.id, ps.id], d.id)
    return d


def missing_markers(model) -> list:
    """[(directory, band record)] for objects saved before markers existed: a directory holding a band with a
    recorded position and no marker."""
    todo = []
    for x, anc in model.walk():
        if x.kind != "points":
            continue
        recs = [r for r in x.provenance if isinstance(r, dict) and r.get("tool") == "tree" and "position" in r]
        home = anc[-1] if anc else None
        if not recs or home is None or any(c.kind == "marker" for c in home.children):
            continue
        todo.append((home, recs[-1]))
    return todo


def add_marker(model, home, record: dict, taxonomy):
    """The marker of an object saved before markers existed, first in its directory."""
    what = record.get("object", TREE)
    mk = model.add(Marker(np.asarray(record["position"], np.float64), marker_source(record)), POSITION)
    mk.label = what if what in {c["name"] for c in taxonomy.classes} else ""
    model.move([mk.id], home.id)
    home.children.insert(0, home.children.pop())
    return mk
