"""Class and flag ids by name, from config/taxonomy.yaml (the source of truth).

Only id 0 (unassessed) is known to the code; every other id is looked up here.

The classes form a tree (schema 2, the user's decision of 2026-11-04): each class may name a ``parent`` it is a kind
of. A label on a parent covers what no sub-label does, or what the labeller is not sure of; counting a class counts
its sub-classes too. A class may be ``only`` for lines (e.g. ``curb_top``) or only for markers (e.g. ``cover``).
Schema 1 files still load: their ``subset_of`` is the parent.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from pclabel import CONFIG_DIR

UNASSESSED = 0
DEFAULT_PATH = CONFIG_DIR / "taxonomy.yaml"
SCHEMAS = ("pclabel.taxonomy/1", "pclabel.taxonomy/2")
ONLY = ("line", "marker")  # the item kinds a class can be kept to
LINE_KINDS = ("segment", "curve", "polyline")  # items that are lines
# the labels tools give what they make, unless the taxonomy file's ``tools`` section says otherwise ("" = no label)
TOOL_LABELS = {
    "curb_trace_lip": "curb",  # the curb tracer's line along the curb's top edge: the standard's curb line
    "curb_trace_toe": "",  # its line along the foot of the curb: not in the standard, a helper line if saved
    "marking_trace": "road_line",
    "tree_band": "tree_trunk",  # the tree tool's band of a trunk (a pole's band takes the pole's class)
    "pole_kinds": "pole",  # the tree tool offers this class and its kinds besides trees
    "cover": "cover",
    "clearance": ["vegetation", "tree"],  # the clearance check's "vegetation" points: these classes and their kinds
}
NOT_FOR_NEW = {"import": "for imported labels only", "retired": "retired: not in the labelling standard"}


def ancestors_in(parents: dict, name: str) -> list:
    """Walk ``parents`` ({class: its parent}) up from ``name``: the parent, its parent, ... (nearest first)."""
    out = []
    while name in parents:
        name = parents[name]
        out.append(name)
    return out


def hex_rgb(hex_color: str) -> tuple:
    """``#rrggbb`` as (r, g, b), 0..255: the one colour parser (taxonomy colours, label colours, exports)."""
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def fits(only, kind: str) -> bool:
    """Whether a label kept to ``only`` ("line", "marker", a list of them, or None: anything) can go on an item of
    this kind ("directory": anything goes, its items take what fits them)."""
    if only is None or kind == "directory":
        return True
    if isinstance(only, (list, tuple)):
        return any(fits(o, kind) for o in only)
    return (only == "line" and kind in LINE_KINDS) or only == kind


def only_words(only) -> str:
    """"lines", "markers", "lines or markers"."""
    kinds = list(only) if isinstance(only, (list, tuple)) else [only]
    return " or ".join(f"{k}s" for k in kinds)


@dataclass(frozen=True)
class Taxonomy:
    path: Path
    sha256: str
    version: str
    classes: tuple[dict, ...]
    flags: tuple[dict, ...]
    tools: dict = None  # the file's ``tools`` section: {tool key: label}, over TOOL_LABELS

    def tool_label(self, key: str):
        """The label a tool gives what it makes (``TOOL_LABELS`` keys), from the file's ``tools`` section if set."""
        return (self.tools or {}).get(key, TOOL_LABELS[key])

    def class_id(self, name: str) -> int:
        for c in self.classes:
            if c["name"] == name:
                return int(c["id"])
        raise KeyError(f"class {name!r} not in {self.path}")

    def flag_bit(self, name: str) -> int:
        for f in self.flags:
            if f["name"] == name:
                return int(f["bit"])
        raise KeyError(f"flag {name!r} not in {self.path}")

    def flag_mask(self, name: str) -> int:
        return 1 << self.flag_bit(name)

    def by_name(self, name: str) -> dict | None:
        for c in self.classes:
            if c["name"] == name:
                return c
        return None

    def parents(self) -> dict:
        """{class: its parent class} for the classes that are a kind of another."""
        return {c["name"]: c["parent"] for c in self.classes if c.get("parent")}

    def ancestors(self, name: str) -> list:
        """The class's parent, its parent's parent, ... (nearest first); [] for a top-level class or another label."""
        return ancestors_in(self.parents(), name)

    def path_of(self, name: str) -> list:
        """The class with its ancestors, from the top: ``["ground", "road", "road_line"]``."""
        return [*reversed(self.ancestors(name)), name]

    def children(self, name: str | None) -> list:
        """The classes whose parent is ``name`` (None: the top-level classes), in file order."""
        return [c["name"] for c in self.classes if c.get("parent") == name and (name is not None or c["id"] != 0)]

    def descendants(self, name: str) -> list:
        """Every class below ``name``, depth first in file order."""
        out = []
        for k in self.children(name):
            out += [k, *self.descendants(k)]
        return out

    def depth(self, name: str) -> int:
        return len(self.ancestors(name))

    def only(self, name: str):
        """"line", "marker" or a list of them for a class only those items take; None for anything else (or a label
        not here)."""
        c = self.by_name(name)
        return None if c is None else c.get("only")

    def fits(self, name: str, kind: str) -> bool:
        """Whether this label can go on an item of this kind (labels that are not classes go anywhere)."""
        return fits(self.only(name), kind)

    def tree_order(self, enabled_only: bool = False, also: tuple = ()) -> list:
        """[(class dict, depth)] depth first: each class right after its parent (unassessed left out). A disabled
        class is left out with ``enabled_only``, and so is everything below it, unless its ``status`` is in
        ``also`` (e.g. ``("import",)`` for the DXF import's label boxes)."""
        out = []

        def add(name, depth):
            c = self.by_name(name)
            if enabled_only and not c.get("enabled", True) and c.get("status") not in also:
                return
            out.append((c, depth))
            for k in self.children(name):
                add(k, depth + 1)

        for k in self.children(None):
            add(k, 0)
        return out

    def not_for_new_labels(self, name: str) -> str | None:
        """Why a class should not be given to new labels ("for imported labels only", "retired"), or None."""
        c = self.by_name(name)
        if c is None:
            return None
        return NOT_FOR_NEW.get(c.get("status"))

    def color_table(self) -> "list[tuple[int, int, int]]":
        """256 RGB entries indexed by class id (grey for unused ids)."""
        table = [(128, 128, 128)] * 256
        for c in self.classes:
            table[int(c["id"])] = hex_rgb(c["color"])
        return table


def load_taxonomy(path: str | Path | None = None) -> Taxonomy:
    path = Path(path) if path is not None else DEFAULT_PATH
    raw = path.read_bytes()
    doc = yaml.safe_load(raw.decode("utf-8"))
    if doc.get("schema", SCHEMAS[0]) not in SCHEMAS:
        raise ValueError(f"{path}: unsupported taxonomy schema {doc.get('schema')!r}")
    classes = []
    for c in doc["classes"]:
        c = dict(c)
        if c.get("subset_of") and not c.get("parent"):  # schema 1: a subset is a kind of its parent
            c["parent"] = c.pop("subset_of")
        classes.append(c)
    classes = tuple(classes)
    ids = [int(c["id"]) for c in classes]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate class ids in {path}")
    if not all(0 <= i <= 255 for i in ids):
        raise ValueError(f"class ids must fit uint8 in {path}")
    if UNASSESSED not in ids:
        raise ValueError(f"{path} must define class 0 (unassessed)")
    names = [c["name"] for c in classes]
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate class names in {path}")
    parent = {c["name"]: c["parent"] for c in classes if c.get("parent")}
    for child, p in parent.items():
        if p not in names:
            raise ValueError(f"{path}: the parent of {child}, {p!r}, is not a class")
        if p == "unassessed" or child == "unassessed":
            raise ValueError(f"{path}: unassessed has no place in the tree")
        seen = {child}
        while p in parent:  # a chain of parents must end
            if p in seen:
                raise ValueError(f"{path}: the parents of {child} go round in a circle")
            seen.add(p)
            p = parent[p]
    unknown = sorted(set(doc.get("tools") or {}) - set(TOOL_LABELS))
    if unknown:
        raise ValueError(f"{path}: unknown tools {unknown} (known: {sorted(TOOL_LABELS)})")
    for c in classes:
        o = c.get("only")
        if o is not None and not all(k in ONLY for k in (o if isinstance(o, list) else [o])):
            raise ValueError(f"{path}: {c['name']}: only must be one of {ONLY} (or a list of them), not {o!r}")
    return Taxonomy(
        path=path,
        sha256=hashlib.sha256(raw).hexdigest(),
        version=str(doc.get("version", "")),
        classes=classes,
        flags=tuple(doc.get("flags", ())),
        tools=dict(doc.get("tools") or {}),
    )
