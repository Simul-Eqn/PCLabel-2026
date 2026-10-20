"""Annotations: line segments, curves and point sets, organised in a tree of directories (SPEC §13; format in
ARCHITECTURE "annotations").

- **Items** (segments, curves, point sets) stand on their own: a new item goes to the top level with a colour of its
  own.
- **Directories** hold items and other directories. Everything inside a directory is drawn in the directory's
  colour; a directory without a colour of its own takes its parent's. So separate items can share a colour without
  being one item.
- Geometry is kept in scan (world) coordinates, float64, so the project offset is never lost. Point sets hold store
  indices in the project (``annotations/<id>.npy``) and source indices (the source file's valid-point order, as
  every export) in an exported file.

The project keeps ``annotations.json`` next to ``project.json``; it is rewritten after every change.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path

import numba as nb
import numpy as np

from pclabel.analysis import curves
from pclabel.store.importer import write_json_atomic

SCHEMA = "pclabel.annotations/1"
SET_OPS = {  # op -> (symbol, words)
    "union": ("∪", "in A or B"),
    "intersection": ("∩", "in both A and B"),
    "a_minus_b": ("\\", "in A but not in B"),
    "b_minus_a": ("/", "in B but not in A"),
    "xor": ("△", "in one of them only"),
}
LABELS_SCHEMA = "pclabel.annotation-labels/1"
PALETTE = ("#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4",
           "#469990", "#dcbeff", "#9a6324", "#fffac8", "#800000", "#aaffc3", "#808000", "#ffd8b1", "#000075")  # fmt: skip


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _pt(v) -> list:
    return [float(x) for x in np.asarray(v, np.float64)]


def jsonable(obj):
    """Plain JSON types from nested dicts, lists, tuples and numpy values."""
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return jsonable(obj.tolist())
    if isinstance(obj, np.generic):
        return obj.item()
    return obj


@dataclass(eq=False)  # nodes are compared by identity (their arrays have no single truth value)
class Segment:
    a: np.ndarray  # (3,) world
    b: np.ndarray
    picked: list = field(default_factory=lambda: [-1, -1])  # source indices of the clicked points (-1: unknown)
    name: str = ""
    color: str = PALETTE[0]  # used at the top level; inside a directory the directory's colour applies
    label: str = ""  # empty: the nearest enclosing directory's label
    id: str = field(default_factory=new_id)
    kind = "segment"

    def length(self) -> float:
        return float(np.linalg.norm(np.asarray(self.b) - np.asarray(self.a)))

    def describe(self) -> str:
        return f"segment, {self.length():.2f} m"


CURVE_MODES = {
    "ends": "through the start and end points, turned to fit the others",
    "axis": "perpendicular to the height axis, at the points' mean height",
    "fit": "fitted to all the points (least squares, any tilt)",
}


@dataclass(eq=False)  # nodes are compared by identity (their arrays have no single truth value)
class Curve:
    picked: np.ndarray  # (n, 3) world, the clicked points in order
    axis: np.ndarray  # (3,) unit height axis that was active when the curve was drawn (z, or a plane's normal)
    axis_origin: np.ndarray  # (3,) world point where height 0 is on that axis
    mode: str = "ends"  # how the curve's plane is chosen (CURVE_MODES)
    normal: np.ndarray | None = None  # (3,) the curve plane's unit normal
    point: np.ndarray | None = None  # (3,) world point on the plane
    nodes: np.ndarray | None = None  # (n, 3) world: the clicked points projected onto the plane
    h_in: np.ndarray | None = None  # (n, 3) handle offsets; NaN rows: automatic
    h_out: np.ndarray | None = None
    frame: str = "z (scan frame)"  # the height axis's name
    picked_source: list = field(default_factory=list)
    name: str = ""
    color: str = PALETTE[0]
    label: str = ""
    id: str = field(default_factory=new_id)
    closed: bool = False  # a loop: a last piece joins the last node back to the first
    kind = "curve"

    @classmethod
    def fit(cls, picked, axis, axis_origin, frame="z (scan frame)", picked_source=None, mode="ends",
            closed=False) -> "Curve":  # fmt: skip
        """Fit the curve's plane to the clicked points (``mode``, CURVE_MODES) and project them onto it."""
        picked = np.asarray(picked, np.float64)
        if closed and len(picked) < 3:
            raise ValueError("a closed loop needs at least three points")
        nan = np.full_like(picked, np.nan)
        c = cls(picked, np.asarray(axis, np.float64) / np.linalg.norm(axis), np.asarray(axis_origin, np.float64),
                mode, h_in=nan, h_out=nan.copy(), frame=frame,
                picked_source=list(picked_source) if picked_source is not None else [-1] * len(picked),
                closed=bool(closed))  # fmt: skip
        c.refit()
        return c

    def refit(self):
        """The plane from the clicked points, and the nodes on it.
        - "ends": through the first and last clicked points (which stay exactly where they were), turned about the
          line between them to fit the others in least squares;
        - "axis": perpendicular to the height axis, at the clicked points' mean height;
        - "fit": the least-squares plane through all the clicked points, at any tilt.
        Manual handles are offsets; they are brought into the plane if it turned."""
        if self.mode == "axis":
            h = curves.fit_height(self.picked, self.axis, self.axis_origin)
            self.normal, self.point = self.axis.copy(), self.axis_origin + h * self.axis
        elif self.mode == "fit":
            self.normal, self.point = curves.fit_plane_through(self.picked, self.axis)
        else:
            self.normal, self.point = curves.fit_through_ends(self.picked, self.axis), self.picked[0].copy()
        self.nodes = curves.project_onto(self.picked, self.normal, self.point)
        n = self.normal
        for hh in (self.h_in, self.h_out):
            hh -= (hh @ n)[:, None] * n  # NaN rows (automatic) stay NaN

    def set_mode(self, mode: str):
        if mode not in CURVE_MODES:
            raise ValueError(f"unknown curve mode {mode!r}")
        self.mode = mode
        self.refit()

    def set_closed(self, closed: bool):
        if closed and len(self.nodes) < 3:
            raise ValueError("a closed loop needs at least three points")
        self.closed = bool(closed)

    def height(self) -> float:
        """Height of the plane's point on the height axis (the plane's height in "axis" mode)."""
        return float(np.dot(self.point - self.axis_origin, self.axis))

    def tilt_deg(self) -> float:
        """Angle between the plane's normal and the height axis (0 in "axis" mode)."""
        return float(np.degrees(np.arccos(np.clip(abs(np.dot(self.normal, self.axis)), 0.0, 1.0))))

    def spread(self) -> float:
        """Largest distance of a clicked point from the plane."""
        return float(np.abs((self.picked - self.point) @ self.normal).max())

    def plane_point(self) -> np.ndarray:
        return self.point

    def handles(self) -> tuple[np.ndarray, np.ndarray]:
        return curves.resolve_handles(self.nodes, self.h_in, self.h_out, self.closed)

    def manual(self) -> np.ndarray:
        return curves.manual_nodes(self.h_in)

    def activate(self, i: int):
        """Give node i manual handles, starting from its automatic ones."""
        if not self.manual()[i]:
            a_in, a_out = self.handles()
            self.h_in[i], self.h_out[i] = a_in[i], a_out[i]

    def make_auto(self, i: int):
        self.h_in[i] = np.nan
        self.h_out[i] = np.nan

    def drag_handle(self, i: int, side: int, target_world):
        self.activate(i)
        self.h_in[i], self.h_out[i] = curves.set_handle(self.nodes[i], self.h_in[i], self.h_out[i], side,
                                                        target_world, self.normal)  # fmt: skip

    def insert(self, point_world, source_index: int = -1) -> int:
        """Add a clicked point. It goes between the two nodes of the piece the curve passes closest to it (or before
        the first / after the last node beyond the ends) with automatic handles; the plane is fitted again (a new
        first or last point is a new end of the curve)."""
        q = curves.project_onto(np.asarray(point_world, np.float64)[None], self.normal, self.point)[0]
        i = curves.insert_position(self.nodes, self.h_in, self.h_out, q, self.closed)
        nan = np.full(3, np.nan)
        self.picked = np.insert(self.picked, i, point_world, axis=0)
        self.h_in = np.insert(self.h_in, i, nan, axis=0)
        self.h_out = np.insert(self.h_out, i, nan, axis=0)
        self.picked_source.insert(i, int(source_index))
        self.refit()
        return i

    def remove(self, i: int):
        """Remove node i (a curve keeps at least two points, a loop three); the plane is fitted again."""
        if len(self.nodes) <= (3 if self.closed else 2):
            raise ValueError(f"a {'loop keeps at least three' if self.closed else 'curve keeps at least two'} points")
        self.picked = np.delete(self.picked, i, axis=0)
        self.h_in = np.delete(self.h_in, i, axis=0)
        self.h_out = np.delete(self.h_out, i, axis=0)
        del self.picked_source[i]
        self.refit()

    def polyline(self, step: float = 0.02) -> np.ndarray:
        return curves.bezier(self.nodes, self.h_in, self.h_out, step, self.closed)

    def length(self) -> float:
        return curves.length(self.polyline())

    def describe(self) -> str:
        return f"{'closed loop' if self.closed else 'curve'}, {len(self.nodes)} points, {self.length():.2f} m"


@dataclass(eq=False)  # nodes are compared by identity (their arrays have no single truth value)
class PointSet:
    indices: np.ndarray  # sorted int64 store indices
    provenance: list = field(default_factory=list)  # how the selection was made (tools, parameters, seeds)
    name: str = ""
    color: str = PALETTE[0]
    label: str = ""
    id: str = field(default_factory=new_id)
    kind = "points"

    def describe(self) -> str:
        return f"points, {len(self.indices):,}"


@dataclass(eq=False)  # nodes are compared by identity (their arrays have no single truth value)
class Directory:
    name: str
    color: str | None = None  # None: the parent's colour
    children: list = field(default_factory=list)
    label: str = ""  # applies to everything inside that has no label of its own
    id: str = field(default_factory=new_id)
    kind = "directory"

    def describe(self) -> str:
        n = len(self.children)
        return f"{n} item{'s' * (n != 1)}"


def clone_nodes(nodes: list) -> list:
    """A copy of nodes for undo snapshots: segment and curve arrays are copied (curves are edited in place); point
    set indices are shared (never changed in place, and possibly millions long)."""
    out = []
    for x in nodes:
        if x.kind == "directory":
            out.append(replace(x, children=clone_nodes(x.children)))
        elif x.kind == "segment":
            out.append(replace(x, a=x.a.copy(), b=x.b.copy(), picked=list(x.picked)))
        elif x.kind == "curve":
            out.append(replace(x, picked=x.picked.copy(), nodes=x.nodes.copy(), h_in=x.h_in.copy(),
                               h_out=x.h_out.copy(), normal=x.normal.copy(), point=x.point.copy(),
                               picked_source=list(x.picked_source)))  # fmt: skip
        else:
            out.append(replace(x, provenance=list(x.provenance)))
    return out


class Annotations:
    """The project's annotation tree, in display order."""

    def __init__(self):
        self.root: list = []
        self.counter = 0  # numbers new items and directories ("curve 7", "directory 8")
        self.locks: set = set()  # labels whose points cannot be selected
        self.unlocked: set = set()  # never-selected classes (taxonomy selectable: false) the user unlocked
        self.locks_on = True  # all locks at once; off: nothing is locked, the choices above are kept

    def locked_labels(self, never) -> set:
        """The labels whose points cannot be selected now: the locked ones and the never-selected classes
        (``never``) not unlocked, or none while the locks are off."""
        return (self.locks | (set(never) - self.unlocked)) if self.locks_on else set()

    def _next(self) -> tuple[int, str]:
        self.counter += 1
        return self.counter, PALETTE[(self.counter - 1) % len(PALETTE)]

    # ---- the tree -----------------------------------------------------------------------------------------------

    def add(self, item, name: str | None = None):
        """A new item at the top level, with its own name and colour."""
        n, color = self._next()
        item.name = name or f"{item.kind} {n}"
        item.color = color
        self.root.append(item)
        return item

    def new_directory(self, name: str | None = None, parent_id: str | None = None) -> Directory:
        """A new directory at the top level (with a colour of its own) or inside another one (taking its colour)."""
        n, color = self._next()
        d = Directory(name or f"directory {n}", color if parent_id is None else None)
        (self.root if parent_id is None else self.node(parent_id).children).append(d)
        return d

    def walk(self, nodes=None, parents=()):
        """(node, ancestors from the top) for every node, depth first in display order."""
        for x in self.root if nodes is None else nodes:
            yield x, parents
            if x.kind == "directory":
                yield from self.walk(x.children, parents + (x,))

    def node(self, node_id: str):
        for x, _ in self.walk():
            if x.id == node_id:
                return x
        raise KeyError(node_id)

    def parent(self, node_id: str) -> Directory | None:
        for x, anc in self.walk():
            if x.id == node_id:
                return anc[-1] if anc else None
        raise KeyError(node_id)

    def _siblings(self, node_id: str) -> list:
        p = self.parent(node_id)
        return self.root if p is None else p.children

    def items(self, nodes=None) -> list:
        """Every segment, curve and point set (inside ``nodes`` and their directories, or in the whole tree)."""
        return [x for x, _ in self.walk(nodes) if x.kind != "directory"]

    def color_of(self, node_id: str) -> str:
        """The colour a node is drawn in: the nearest enclosing directory's (one with a colour), else its own."""
        for x, anc in self.walk():
            if x.id == node_id:
                for d in reversed(anc):
                    if d.color:
                        return d.color
                return x.color or PALETTE[0]
        raise KeyError(node_id)

    def label_of(self, node_id: str) -> tuple[str, str | None]:
        """(label, id of the node that gives it): the node's own label, else the nearest enclosing directory's;
        ("", None) when none has one."""
        for x, anc in self.walk():
            if x.id == node_id:
                for d in (x, *reversed(anc)):
                    if d.label:
                        return d.label, d.id
                return "", None
        raise KeyError(node_id)

    def points_labelled(self, labels) -> np.ndarray:
        """Store indices (sorted, unique) of the points in point sets whose label (own or inherited) is one of
        ``labels``."""
        labels = set(labels)
        from pclabel.tools import sets as sets_

        return sets_.union_all([x.indices for x in self.items() if x.kind == "points"
                                and self.label_of(x.id)[0] in labels])  # fmt: skip

    def move(self, node_ids, target_id: str | None):
        """Move nodes into a directory (``target_id``) or to the top level (None), keeping their order. A directory
        is not moved into itself or its own contents; nodes whose directory moves along stay inside it."""
        moving = set(node_ids)
        target_chain = set()
        if target_id is not None:
            for x, anc in self.walk():
                if x.id == target_id:
                    target_chain = {target_id} | {d.id for d in anc}
        picked = []
        for x, anc in self.walk():
            if x.id in moving and not any(d.id in moving for d in anc) and x.id not in target_chain:
                picked.append(x)
        dest = self.root if target_id is None else self.node(target_id).children
        for x in picked:
            self._siblings(x.id).remove(x)
            dest.append(x)

    def remove(self, node_id: str):
        """Remove a node (a directory with everything in it)."""
        self._siblings(node_id).remove(self.node(node_id))

    def points_of(self, node_id: str) -> np.ndarray:
        """Store indices (sorted) of a point set, or of all the point sets in a directory."""
        from pclabel.tools import sets as sets_

        x = self.node(node_id)
        return sets_.union_all([m.indices for m in self.items([x]) if m.kind == "points"])

    def set_operation(self, a_id: str, op: str, b_id: str) -> tuple[np.ndarray, dict]:
        """(store indices, record) of A op B, for point sets or directories (all their point sets). ``op``: one of
        SET_OPS."""
        from pclabel.tools import sets as sets_

        a, b = self.points_of(a_id), self.points_of(b_id)
        fn = {"union": sets_.union, "intersection": sets_.intersection, "a_minus_b": sets_.difference,
              "b_minus_a": lambda x, y: sets_.difference(y, x), "xor": sets_.xor}[op]  # fmt: skip
        na, nb = self.node(a_id), self.node(b_id)
        record = {"tool": "set_operation", "op": op, "a": {"name": na.name, "points": int(a.size)},
                  "b": {"name": nb.name, "points": int(b.size)}}  # fmt: skip
        return fn(a, b), record

    def split_point_set(self, set_id: str, rough, record: dict) -> tuple["PointSet", "PointSet"]:
        """Replace a point set by two: its points where ``rough`` (a mask over its indices) is false ("<name> smooth")
        and true ("<name> rough"), in its place, with its label; ``record`` (how it was split) is added to their
        provenance."""
        ps = self.node(set_id)
        rough = np.asarray(rough, bool)
        parts = []
        for part, mask in (("smooth", ~rough), ("rough", rough)):
            _, color = self._next()
            parts.append(PointSet(ps.indices[mask], [*ps.provenance, dict(record, part=part)], f"{ps.name} {part}",
                                  color, ps.label))  # fmt: skip
        sib = self._siblings(ps.id)
        i = sib.index(ps)
        sib[i : i + 1] = parts
        return parts[0], parts[1]

    def join_curves(self, first_id: str, first_end: str, second_id: str, second_end: str, close: bool = False,
                    merge_within: float = 0.02) -> "Curve":  # fmt: skip
        """Join two open curves into one, ``first_end`` ("start" or "end") of the first to ``second_end`` of the
        second. The result takes the first curve's place in the tree, its name, colour, label, plane mode and height
        axis; its plane is fitted again to all the clicked points. Ends closer than ``merge_within`` metres become one
        node. ``close`` also joins the outer ends (a loop). Both curves are replaced."""
        a, b = self.node(first_id), self.node(second_id)
        if a is b or a.kind != "curve" or b.kind != "curve":
            raise ValueError("join needs two different curves")
        if a.closed or b.closed:
            raise ValueError("a closed loop has no ends to join: open it first")
        pa, sa, ia, oa = _oriented(a, first_end == "start")  # its joined end last
        pb, sb, ib, ob = _oriented(b, second_end == "end")  # its joined end first
        if np.linalg.norm(pa[-1] - pb[0]) < merge_within:
            pb, sb, ib, ob = pb[1:], sb[1:], ib[1:], ob[1:]
        picked, source = np.vstack([pa, pb]), sa + sb
        h_in, h_out = np.vstack([ia, ib]), np.vstack([oa, ob])
        if close and len(picked) > 3 and np.linalg.norm(picked[0] - picked[-1]) < merge_within:
            picked, source, h_in, h_out = picked[:-1], source[:-1], h_in[:-1], h_out[:-1]
        if close and len(picked) < 3:
            raise ValueError("a closed loop needs at least three points")
        c = Curve(picked, a.axis.copy(), a.axis_origin.copy(), a.mode, h_in=h_in, h_out=h_out, frame=a.frame,
                  picked_source=source, name=a.name, color=a.color, label=a.label, closed=bool(close))  # fmt: skip
        c.refit()
        sib = self._siblings(a.id)
        sib[sib.index(a)] = c
        self.remove(b.id)
        return c

    # ---- documents ----------------------------------------------------------------------------------------------

    def to_doc(self, project, *, points: str) -> dict:
        """The JSON document. ``points``: "file" (store indices in ``annotations/<id>.npy``, the project's copy) or
        "inline" (source indices in the document, for export)."""
        src = project.meta.get("source", {})
        return {
            "schema": SCHEMA,
            "coordinates": "scan frame (world), metres; store/local = world - offset",
            "source": {"file": Path(src.get("path", "")).name, "sha256": src.get("sha256"), "n_valid": project.n},
            "offset": _pt(project.offset),
            "counter": self.counter,
            "locks": sorted(self.locks),
            "unlocked": sorted(self.unlocked),
            "locks_on": self.locks_on,
            "tree": [_node_doc(x, project, points) for x in self.root],
        }

    @classmethod
    def from_doc(cls, doc: dict, project, base: Path | None = None) -> tuple["Annotations", list[str]]:
        """Annotations from a document, and warnings (e.g. point sets of another scan, which are left out)."""
        if doc.get("schema") != SCHEMA:
            raise ValueError(f"unsupported schema {doc.get('schema')!r} (expected {SCHEMA})")
        out, warnings = cls(), []
        out.counter = int(doc.get("counter", 0))
        out.locks = set(doc.get("locks", []))
        out.unlocked = set(doc.get("unlocked", []))
        out.locks_on = bool(doc.get("locks_on", True))
        same_scan = _same_scan(doc, project)
        for nd in doc.get("tree", []):
            x = _node_from_doc(nd, project, base, same_scan, warnings)
            if x is not None:
                out.root.append(x)
        out._name_unnamed()
        return out, warnings

    def _name_unnamed(self):
        for x, _ in self.walk():
            if not x.name:
                self.counter += 1
                x.name = f"{x.kind} {self.counter}"

    def save(self, project):
        """Write ``annotations.json`` and the point-set files into the project; remove files of deleted point sets."""
        d = Path(project.path) / "annotations"
        keep = set()
        for m in self.items():
            if m.kind == "points":
                f = d / f"{m.id}.npy"
                keep.add(f.name)
                if not f.exists():
                    d.mkdir(exist_ok=True)
                    tmp = d / f"{m.id}.tmp.npy"
                    np.save(tmp, np.asarray(m.indices, np.int64))
                    tmp.replace(f)
        write_json_atomic(Path(project.path) / "annotations.json", self.to_doc(project, points="file"))
        if d.exists():
            for f in d.glob("*.npy"):
                if f.name not in keep:
                    f.unlink()

    @classmethod
    def load(cls, project) -> tuple["Annotations", list[str]]:
        path = Path(project.path) / "annotations.json"
        if not path.exists():
            return cls(), []
        return cls.from_doc(json.loads(path.read_text(encoding="utf-8")), project, Path(project.path))

    def export(self, path, project, node_ids=None):
        """One self-contained JSON file (point sets as source indices): the whole tree, or only these nodes."""
        doc = self.to_doc(project, points="inline")
        if node_ids is not None:
            keep = set(node_ids)
            doc["tree"] = [_node_doc(x, project, "inline") for x, anc in self.walk()
                           if x.id in keep and not any(d.id in keep for d in anc)]  # fmt: skip
        write_json_atomic(Path(path), doc)

    def export_labels(self, path, project, class_ids: dict | None = None) -> dict:
        """One JSON file grouped by label instead of by directory: for each label its items (each with its
        directory path and where the label comes from), counts, the number of distinct points, and the points
        shared with other labels. ``class_ids`` maps taxonomy class names to ids, for labels that are classes.
        Returns a summary {label: (items, points)}."""
        class_ids = class_ids or {}
        by_label: dict = {}
        for x, anc in self.walk():
            if x.kind == "directory":
                continue
            label, giver = self.label_of(x.id)
            d = _node_doc(x, project, "inline")
            d["path"] = [a.name for a in anc]
            d["label_from"] = None if giver is None else self.node(giver).name
            by_label.setdefault(label, []).append((x, d))
        order = sorted((k for k in by_label if k), key=lambda k: (k not in class_ids, class_ids.get(k, 0), k))
        from pclabel.tools import sets as sets_

        unions = {k: sets_.union_all([x.indices for x, _ in v if x.kind == "points"]) for k, v in by_label.items()}
        overlaps = []
        for a_i, a in enumerate(order):
            for b in order[a_i + 1 :]:
                n = int(np.intersect1d(unions[a], unions[b], assume_unique=True).size)
                if n:
                    overlaps.append({"labels": [a, b], "points": n})

        def entry(k):
            items = by_label[k]
            counts = {t: sum(x.kind == t for x, _ in items) for t in ("segment", "curve", "points")}
            return {"label": k, "class_id": class_ids.get(k), "counts": counts, "points": int(unions[k].size),
                    "items": [d for _, d in items]}  # fmt: skip

        src = project.meta.get("source", {})
        doc = {
            "schema": LABELS_SCHEMA,
            "coordinates": "scan frame (world), metres; store/local = world - offset",
            "source": {"file": Path(src.get("path", "")).name, "sha256": src.get("sha256"), "n_valid": project.n},
            "offset": _pt(project.offset),
            "labels": [entry(k) for k in order],
            "unlabelled": entry("") if "" in by_label else None,
            "overlaps": overlaps,
        }
        write_json_atomic(Path(path), doc)
        return {k: (len(by_label[k]), int(unions[k].size)) for k in by_label}

    def import_file(self, path, project) -> list[str]:
        """Append the file's tree at the top level (new ids, so importing twice gives two copies). Warnings back."""
        other, warnings = Annotations.from_doc(json.loads(Path(path).read_text(encoding="utf-8")), project)
        for x, _ in other.walk():
            x.id = new_id()
        self.root.extend(other.root)
        self.counter = max(self.counter, other.counter)
        return warnings


def _oriented(c: "Curve", reverse: bool):
    """(picked, sources, in handles, out handles) of a curve, reversed if asked (a reversed node's handles swap)."""
    if not reverse:
        return c.picked.copy(), list(c.picked_source), c.h_in.copy(), c.h_out.copy()
    return c.picked[::-1].copy(), list(c.picked_source)[::-1], c.h_out[::-1].copy(), c.h_in[::-1].copy()


def _same_scan(doc, project) -> bool:
    s = doc.get("source", {})
    sha = project.meta.get("source", {}).get("sha256")
    return int(s.get("n_valid", -1)) == project.n and (s.get("sha256") == sha or not s.get("sha256") or not sha)


def _node_doc(x, project, points: str) -> dict:
    base = {"id": x.id, "type": x.kind, "name": x.name, "color": x.color, "label": x.label}
    if x.kind == "directory":
        return dict(base, children=[_node_doc(c, project, points) for c in x.children])
    if x.kind == "segment":
        return dict(base, a=_pt(x.a), b=_pt(x.b), length_m=x.length(), picked_source_indices=[int(i) for i in x.picked])
    if x.kind == "curve":
        h_in, h_out = x.handles()
        line = x.polyline()
        return dict(
            base,
            plane={"mode": x.mode, "normal": _pt(x.normal), "point": _pt(x.point), "tilt_deg": x.tilt_deg(),
                   "height_axis": {"name": x.frame, "normal": _pt(x.axis), "origin": _pt(x.axis_origin)},
                   **({"height": x.height()} if x.mode == "axis" else {})},  # fmt: skip
            nodes=[_pt(p) for p in x.nodes],
            handles_in=[_pt(p) for p in x.nodes + h_in],  # positions, as in a vector drawing program
            handles_out=[_pt(p) for p in x.nodes + h_out],
            manual=[bool(v) for v in x.manual()],
            closed=bool(x.closed),
            picked=[_pt(p) for p in x.picked],
            picked_source_indices=[int(i) for i in x.picked_source],
            length_m=curves.length(line),
            polyline=[_pt(p) for p in line],  # about 2 cm apart: the curve for readers without Bézier code
        )
    d = dict(base, count=int(len(x.indices)), provenance=jsonable(x.provenance))
    if points == "file":
        d["file"] = f"annotations/{x.id}.npy"
    else:
        d["source_indices"] = np.sort(np.asarray(project.source_index[x.indices], np.int64)).tolist()
    return d


def _node_from_doc(nd: dict, project, base, same_scan: bool, warnings: list):
    kind = nd.get("type")
    nid = nd.get("id") or new_id()
    name, color, label = nd.get("name", ""), nd.get("color"), nd.get("label", "")
    if kind == "directory":
        kids = [_node_from_doc(c, project, base, same_scan, warnings) for c in nd.get("children", [])]
        return Directory(name, color, [k for k in kids if k is not None], label, nid)
    color = color or PALETTE[0]
    if kind == "segment":
        return Segment(np.asarray(nd["a"], np.float64), np.asarray(nd["b"], np.float64),
                       list(nd.get("picked_source_indices", [-1, -1])), name, color, label, nid)  # fmt: skip
    if kind == "curve":
        pl = nd["plane"]
        nodes = np.asarray(nd["nodes"], np.float64)
        manual = np.asarray(nd.get("manual", [False] * len(nodes)), bool)
        h_in = np.asarray(nd["handles_in"], np.float64) - nodes
        h_out = np.asarray(nd["handles_out"], np.float64) - nodes
        h_in[~manual] = np.nan
        h_out[~manual] = np.nan
        if "mode" in pl:
            ha = pl["height_axis"]
            mode, axis, axis_origin, frame = pl["mode"], ha["normal"], ha["origin"], ha["name"]
        else:  # drawn before there were modes: perpendicular to the height axis
            mode, axis, frame = "axis", pl["normal"], pl.get("height_axis", "z (scan frame)")
            axis_origin = pl.get("origin", pl["point"])
        return Curve(np.asarray(nd.get("picked", nd["nodes"]), np.float64), np.asarray(axis, np.float64),
                     np.asarray(axis_origin, np.float64), mode, np.asarray(pl["normal"], np.float64),
                     np.asarray(pl["point"], np.float64), nodes, h_in, h_out, frame,
                     list(nd.get("picked_source_indices", [-1] * len(nodes))), name, color, label, nid,
                     bool(nd.get("closed", False)))  # fmt: skip
    if kind == "points":
        prov = list(nd.get("provenance", []))
        if "file" in nd:
            f = Path(base) / nd["file"] if base is not None else None
            if f is None or not f.exists():
                warnings.append(f"point set {name or nid}: file {nd['file']} not found")
                return None
            return PointSet(np.asarray(np.load(f), np.int64), prov, name, color, label, nid)
        if not same_scan:
            warnings.append(f"point set {name or nid} ({nd.get('count', '?')} points) is from another scan: left out")
            return None
        return PointSet(store_indices_of_sources(project, nd["source_indices"]), prov, name, color, label, nid)
    warnings.append(f"unknown item type {kind!r}: left out")
    return None


@nb.njit(parallel=True, nogil=True, cache=True)
def _match(source_index, wanted, out):
    for i in nb.prange(source_index.shape[0]):
        s = np.int64(source_index[i])
        k = np.searchsorted(wanted, s)
        out[i] = k < wanted.shape[0] and wanted[k] == s


def isin_sorted(values, wanted_sorted) -> np.ndarray:
    """Boolean mask: which ``values`` are in ``wanted_sorted`` (sorted, unique int64), in parallel."""
    out = np.empty(len(values), np.bool_)
    if len(values):
        _match(np.asarray(values), np.asarray(wanted_sorted, np.int64), out)
    return out


def store_indices_of_sources(project, source_indices) -> np.ndarray:
    """Store indices (sorted) of the points with these source indices: one parallel pass over the source index."""
    wanted = np.unique(np.asarray(source_indices, np.int64))
    if wanted.size == 0:
        return np.empty(0, np.int64)
    hit = np.empty(project.n, np.bool_)
    _match(np.asarray(project.source_index), wanted, hit)
    return np.flatnonzero(hit).astype(np.int64)
