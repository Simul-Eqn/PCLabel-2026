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


def fits(only: str | None, kind: str) -> bool:
    """Whether a label kept to ``only`` (None: anything) can go on an item of this kind ("directory": anything goes,
    its items take what fits them)."""
    if only is None or kind == "directory":
        return True
    return (only == "line" and kind in LINE_KINDS) or only == kind


@dataclass(frozen=True)
class Taxonomy:
    path: Path
    sha256: str
    version: str
    classes: tuple[dict, ...]
    flags: tuple[dict, ...]

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
        p, out = self.parents(), []
        while name in p:
            name = p[name]
            out.append(name)
        return out

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

    def only(self, name: str) -> str | None:
        """"line" or "marker" for a class only those items take; None for anything else (or a label not here)."""
        c = self.by_name(name)
        return None if c is None else c.get("only")

    def fits(self, name: str, kind: str) -> bool:
        """Whether this label can go on an item of this kind (labels that are not classes go anywhere)."""
        return fits(self.only(name), kind)

    def tree_order(self, enabled_only: bool = False) -> list:
        """[(class dict, depth)] depth first: each class right after its parent (unassessed left out). A disabled
        class is left out with ``enabled_only``, and so is everything below it."""
        out = []

        def add(name, depth):
            c = self.by_name(name)
            if enabled_only and not c.get("enabled", True):
                return
            out.append((c, depth))
            for k in self.children(name):
                add(k, depth + 1)

        for k in self.children(None):
            add(k, 0)
        return out

    def subset_of(self) -> dict:
        """Kept for callers of schema 1: the same as ``parents``."""
        return self.parents()

    def color_table(self) -> "list[tuple[int, int, int]]":
        """256 RGB entries indexed by class id (grey for unused ids)."""
        table = [(128, 128, 128)] * 256
        for c in self.classes:
            h = c["color"].lstrip("#")
            table[int(c["id"])] = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
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
    for c in classes:
        if c.get("only") is not None and c["only"] not in ONLY:
            raise ValueError(f"{path}: {c['name']}: only must be one of {ONLY}, not {c['only']!r}")
    return Taxonomy(
        path=path,
        sha256=hashlib.sha256(raw).hexdigest(),
        version=str(doc.get("version", "")),
        classes=classes,
        flags=tuple(doc.get("flags", ())),
    )
