"""Undo and redo for the session (SPEC §4 "History"): the selection, the fitted planes and the annotations.

Before a change, the app records what it is about to change: a *checkpoint* holds the kind of state ("selection",
"frames", "annotations"), a label for the menu and a snapshot of that state. Undo takes the newest checkpoint,
records the current state of that kind on the redo stack and restores the snapshot; redo does the reverse. A new
change clears the redo stack.

Snapshots share what is never changed in place (selection index arrays, point-set indices), so most are small; the
stack keeps at most ``limit`` entries and ``max_bytes`` of such arrays, dropping the oldest. The history lasts for
the session. Point labels (M3) are undone through the journal instead.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Entry:
    kind: str
    label: str
    state: object
    nbytes: int = 0


class UndoStack:
    def __init__(self, limit: int = 200, max_bytes: int = 4_000_000_000):
        self.limit, self.max_bytes = limit, max_bytes
        self.undo_entries: list[Entry] = []
        self.redo_entries: list[Entry] = []

    def push(self, kind: str, label: str, state, nbytes: int = 0):
        """A checkpoint before a change; it clears what could be redone."""
        self.undo_entries.append(Entry(kind, label, state, nbytes))
        self.redo_entries.clear()
        self._trim()

    def undo(self, capture) -> Entry | None:
        """The newest checkpoint (to restore), after recording ``capture(kind)``, the current state, for redo."""
        return self._swap(self.undo_entries, self.redo_entries, capture)

    def redo(self, capture) -> Entry | None:
        return self._swap(self.redo_entries, self.undo_entries, capture)

    def clear(self):
        self.undo_entries.clear()
        self.redo_entries.clear()

    @staticmethod
    def _swap(src, dst, capture):
        if not src:
            return None
        e = src.pop()
        state, nbytes = capture(e.kind)
        dst.append(Entry(e.kind, e.label, state, nbytes))
        return e

    def _trim(self):
        while len(self.undo_entries) > self.limit or (
                len(self.undo_entries) > 1 and sum(e.nbytes for e in self.undo_entries) > self.max_bytes):  # fmt: skip
            self.undo_entries.pop(0)
