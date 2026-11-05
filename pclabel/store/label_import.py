"""Importing labels made elsewhere (the user's request of 2026-11-04: "import different kinds of labels", first ROCK
Robotic's planimetrics DXF).

A source is read into **layers** of vector features (io/dxf.py for DXF). A **profile** (``config/label_imports/*.yaml``)
maps a layer to a PCLabel label (a taxonomy class or flag, or empty: no label). The import becomes one directory,
named after the file, with a directory per layer (its name, its colour in the source, the label from the profile) and
the features in it as polylines (store/annotations.py ``Polyline``) and, for a single point such as a map symbol, as
markers (``Marker``), so they show and export like any annotation: the labels view, the label map, "export labels".
Every feature keeps where it came from (file, layer, entity type, handle, and for a map symbol its block, scale and
rotation).
"""

from __future__ import annotations

import colorsys
import hashlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from pclabel import REPO_ROOT
from pclabel.store.annotations import Directory, Marker, Polyline

PROFILES_DIR = REPO_ROOT / "config" / "label_imports"
PROFILE_SCHEMA = "pclabel.label-import-profile/1"
FORMATS = {".dxf": "DXF drawing (CAD layers)"}  # suffix -> description; readers in read_source()


@dataclass
class Profile:
    name: str
    format: str  # a FORMATS suffix without the dot, e.g. "dxf"
    layers: dict  # source layer -> label ("" for none)
    notes: dict = field(default_factory=dict)  # source layer -> why it maps (or does not)
    path: Path | None = None


@dataclass
class Layer:
    name: str
    color: str  # "#rrggbb", the layer's colour in the source
    features: list  # DxfEntity-like: .type, .handle, .vertices (n, 3) world, .closed, .extra
    label: str = ""

    def counts(self) -> Counter:
        return Counter(f.type for f in self.features)

    def n_vertices(self) -> int:
        return int(sum(len(f.vertices) for f in self.features))


@dataclass
class Source:
    path: Path
    format: str
    layers: list  # [Layer] in the source's layer order
    info: dict  # e.g. {"version": "AC1032", "units": "metres"}
    skipped: Counter  # feature types not read
    sha256: str = ""


def load_profiles(directory=PROFILES_DIR) -> list:
    out = []
    for f in sorted(Path(directory).glob("*.yaml")):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if doc.get("schema") != PROFILE_SCHEMA:
            raise ValueError(f"{f}: schema must be {PROFILE_SCHEMA}")
        layers, notes = {}, {}
        for k, v in (doc.get("layers") or {}).items():
            if isinstance(v, dict):
                layers[str(k)] = str(v.get("label") or "")
                if v.get("note"):
                    notes[str(k)] = str(v["note"])
            else:
                layers[str(k)] = str(v or "")
        out.append(Profile(str(doc.get("name", f.stem)), str(doc.get("format", "")), layers, notes, f))
    return out


def best_profile(source: Source, profiles) -> Profile | None:
    """The profile of the source's format that knows the most of its layers (None: none knows any)."""
    names = {lay.name for lay in source.layers}
    scored = [(len(names & set(p.layers)), p) for p in profiles if p.format == source.format]
    scored = [s for s in scored if s[0]]
    return max(scored, key=lambda s: s[0])[1] if scored else None


def aci_rgb(aci: int | None) -> str:
    """An AutoCAD Color Index as "#rrggbb" (1-9 and the greys exactly; 10-249 by the index's hue and shade pattern)."""
    fixed = {1: "#ff0000", 2: "#ffff00", 3: "#00ff00", 4: "#00ffff", 5: "#0000ff", 6: "#ff00ff", 7: "#ffffff",
             8: "#808080", 9: "#c0c0c0", 250: "#333333", 251: "#505050", 252: "#696969", 253: "#828282",
             254: "#bebebe", 255: "#ffffff"}  # fmt: skip
    if aci is None or aci <= 0 or aci > 255:
        return "#ffffff"
    if aci in fixed:
        return fixed[aci]
    hue = ((aci - 10) // 10) * 15 / 360
    sub = aci % 10
    value = (1.0, 0.8, 0.6, 0.5, 0.3)[sub // 2]
    sat = 1.0 if sub % 2 == 0 else 0.5
    r, g, b = colorsys.hsv_to_rgb(hue, sat, value)
    return "#{:02x}{:02x}{:02x}".format(round(255 * r), round(255 * g), round(255 * b))


def read_source(path) -> Source:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".dxf":
        from pclabel.io.dxf import read_dxf

        d = read_dxf(path)
        by_layer: dict = {}
        for e in d.entities:
            by_layer.setdefault(e.layer, []).append(e)
        order = [k for k in d.layer_colors if k in by_layer] + sorted(set(by_layer) - set(d.layer_colors))
        layers = [Layer(k, aci_rgb(d.layer_colors.get(k)), by_layer[k]) for k in order]
        info = {"version": d.version, "units": d.units, "metres_per_unit": d.scale}
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        return Source(path, "dxf", layers, info, d.skipped, sha)
    raise ValueError(f"cannot import labels from {path.suffix or 'this'} files (known: {', '.join(FORMATS)})")


def apply_profile(source: Source, profile: Profile | None):
    for lay in source.layers:
        lay.label = profile.layers.get(lay.name, "") if profile is not None else ""


def coverage(source: Source, project) -> dict:
    """How the source sits on the scan: its vertices inside the scan's bounding box (world), and its extent."""
    v = np.vstack([f.vertices for lay in source.layers for f in lay.features]) if source.layers else np.empty((0, 3))
    if not len(v):
        return {"vertices": 0, "inside": 0, "min": None, "max": None}
    off = np.asarray(project.offset, np.float64)
    b = project.meta["bounds"]
    lo, hi = np.asarray(b["min"], np.float64) + off, np.asarray(b["max"], np.float64) + off
    inside = np.all((v >= lo) & (v <= hi), axis=1)
    return {"vertices": int(len(v)), "inside": int(inside.sum()), "min": v.min(0).tolist(), "max": v.max(0).tolist()}


def to_directory(source: Source, layers=None, top_name: str | None = None) -> Directory:
    """The import as a directory tree: one directory per layer (only ``layers``, names, if given), with the layer's
    colour and label, holding a polyline per feature (a marker for a single point: a map symbol)."""
    top = Directory(top_name or f"{source.path.name} (imported)", "#ffffff")
    for lay in source.layers:
        if layers is not None and lay.name not in layers:
            continue
        d = Directory(lay.name, lay.color, label=lay.label)
        for f in lay.features:
            src = {"format": source.format, "file": source.path.name, "sha256": source.sha256, "layer": lay.name,
                   "entity": f.type, "handle": f.handle, **f.extra}  # fmt: skip
            what = f.extra.get("block") or f.type
            name = f"{what} {f.handle}" + (f" face {f.extra['mesh_face'] + 1}" if "mesh_face" in f.extra else "")
            v = np.asarray(f.vertices, np.float64)
            if len(v) == 1:
                d.children.append(Marker(v[0].copy(), src, name, lay.color))
            else:
                d.children.append(Polyline(v, bool(f.closed), src, name, lay.color))
        top.children.append(d)
    return top
