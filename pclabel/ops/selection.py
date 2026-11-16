"""How a tool's result changes the selection (no Qt, no GL): the same rule for shapes, 3D select and seed tools."""

from __future__ import annotations

import numpy as np

from pclabel.tools import sets

OPS = ("replace", "add", "subtract")


def combine(current, found, op: str, locked=None) -> tuple[np.ndarray, int]:
    """(the new selection, how many locked points were left out): ``found`` (sorted store indices) replaces, is added
    to or is taken from ``current``. Locked points (sorted store indices) are never added, but taking points away
    ignores locks."""
    if op not in OPS:
        raise ValueError(f"op must be one of {OPS}, not {op!r}")
    found = np.asarray(found, np.int64)
    dropped = 0
    if op != "subtract" and locked is not None and len(locked) and found.size:
        keep = ~sets.isin_sorted(found, locked)
        dropped = int(found.size - keep.sum())
        found = found[keep]
    if op == "add":
        return sets.union(current, found), dropped
    if op == "subtract":
        return sets.difference(current, found), dropped
    return found, dropped
