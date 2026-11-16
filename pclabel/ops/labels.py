"""Annotations resolved to one class and a flags byte per point (no Qt, no GL).

Labels live on annotation items and directories (D-007). A point gets the class of every point set that holds it,
resolved by the labelling standard's rules:
- along one branch of the taxonomy's tree the more specific class wins, whatever the order (a ``road_line`` point
  in the ``road`` set is ``road_line``; counted as road too, through the tree);
- across branches (``road`` and ``pavement``) it is a conflict: the smaller set wins, as in the labels view, and the
  conflicting points are counted;
- a label that is a taxonomy flag (``exclude``, ``marking``) sets its bit in the flags byte instead;
- any other label (free text) is reported and does not change the point;
- a point in no labelled set stays 0, unassessed. It is not a negative.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from pclabel.store.project import source_doc
from pclabel.store.util import write_json_atomic

LABELS_SCHEMA = "pclabel.labels/1"


@dataclass
class ResolvedLabels:
    classes: np.ndarray  # uint8 (n,), store order: taxonomy class id, 0 = unassessed
    flags: np.ndarray  # uint8 (n,), store order: taxonomy flag bits
    conflicts: int = 0  # points held by point sets of two classes on different branches of the tree
    conflict_pairs: dict = field(default_factory=dict)  # {"a / b": points}
    free_text: dict = field(default_factory=dict)  # {label: points}: labels that are neither classes nor flags
    sets: int = 0  # point sets resolved


def _relation_tables(taxonomy):
    """(below, related): 256 x 256 booleans. below[a, b]: class a is b or below b in the tree; related[a, b]: one is
    at or below the other (the same branch)."""
    below = np.zeros((256, 256), bool)
    for c in taxonomy.classes:
        a = int(c["id"])
        below[a, a] = True
        for anc in taxonomy.ancestors(c["name"]):
            below[a, taxonomy.class_id(anc)] = True
    return below, below | below.T


def resolve(model, taxonomy, n: int) -> ResolvedLabels:
    """Every point set of ``model`` (an ``Annotations``) by its own or inherited label, for a project of ``n``
    points. Larger sets are applied first, so where two classes on different branches overlap, the smaller set's
    class is the point's (as the labels view shows it)."""
    classes = np.zeros(n, np.uint8)
    flags = np.zeros(n, np.uint8)
    conflict = np.zeros(n, bool)
    below, related = _relation_tables(taxonomy)
    class_ids = {c["name"]: int(c["id"]) for c in taxonomy.classes}
    flag_bits = {f["name"]: int(f["bit"]) for f in taxonomy.flags}
    out = ResolvedLabels(classes, flags)
    for label, ps in model.label_layers():  # largest first
        idx = np.asarray(ps.indices, np.int64)
        if not label or not idx.size:
            continue
        out.sets += 1
        if label in flag_bits:
            flags[idx] |= np.uint8(1 << flag_bits[label])
            continue
        if label not in class_ids:
            out.free_text[label] = out.free_text.get(label, 0) + int(idx.size)
            continue
        code = class_ids[label]
        prev = classes[idx]
        clash = (prev != 0) & ~related[prev, code]
        if clash.any():
            conflict[idx[clash]] = True
            for p in np.unique(prev[clash]):
                pair = " / ".join(sorted((taxonomy_name(taxonomy, int(p)), label)))
                out.conflict_pairs[pair] = out.conflict_pairs.get(pair, 0) + int((prev[clash] == p).sum())
        keep = (prev != 0) & below[prev, code] & (prev != code)  # already more specific on this branch
        classes[idx[~keep]] = code
    out.conflicts = int(conflict.sum())
    return out


def taxonomy_name(taxonomy, class_id: int) -> str:
    for c in taxonomy.classes:
        if int(c["id"]) == class_id:
            return c["name"]
    return str(class_id)


def to_source_order(values: np.ndarray, source_index) -> np.ndarray:
    """A store-order array in source order, the order every export uses (``out[source_index] = values``)."""
    out = np.zeros_like(values)
    out[np.asarray(source_index)] = values
    return out


def export_labels(stem, project, model, taxonomy) -> dict:
    """Write ``<stem>.classes.npy`` and ``<stem>.flags.npy`` (uint8, source order, one value per valid point) and a
    sidecar ``<stem>.labels.json`` (schema ``pclabel.labels/1``: the scan, the taxonomy version and hash, counts per
    class, conflicts, free-text labels). ``stem`` must not be inside the project unless under ``auto/``. Returns the
    sidecar."""
    t0 = time.perf_counter()
    stem = Path(stem)
    proj = Path(project.path).resolve()
    if stem.resolve().is_relative_to(proj) and not stem.resolve().is_relative_to(proj / "auto"):
        raise ValueError(f"{stem}: label exports go outside the project, or under its auto/ directory")
    res = resolve(model, taxonomy, project.n)
    stem.parent.mkdir(parents=True, exist_ok=True)
    np.save(stem.with_name(stem.name + ".classes.npy"), to_source_order(res.classes, project.source_index))
    np.save(stem.with_name(stem.name + ".flags.npy"), to_source_order(res.flags, project.source_index))
    counts = np.bincount(res.classes, minlength=256)
    doc = {
        "schema": LABELS_SCHEMA,
        "order": "source order: the scan's valid points, scans in file order, records in stored order",
        "source": source_doc(project),
        "taxonomy": {"version": taxonomy.version, "sha256": taxonomy.sha256},
        "classes": {taxonomy_name(taxonomy, i): int(counts[i]) for i in np.flatnonzero(counts) if i != 0},
        "unassessed": int(counts[0]),
        "flags": {f["name"]: int(((res.flags >> int(f["bit"])) & 1).sum()) for f in taxonomy.flags},
        "conflicts": res.conflicts,
        "conflict_pairs": res.conflict_pairs,
        "free_text_labels": res.free_text,
        "point_sets": res.sets,
        "seconds": round(time.perf_counter() - t0, 3),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    write_json_atomic(stem.with_name(stem.name + ".labels.json"), doc)
    return doc
