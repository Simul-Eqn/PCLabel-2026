"""Undo and redo for the session: the selection, the fitted planes and the annotations.

Before a change, the app records what it is about to change: a *checkpoint* holds the kind of state ("selection",
"frames", "annotations"), a label for the menu and a snapshot of that state. Undo takes the newest checkpoint,
records the current state of that kind on the redo stack and restores the snapshot; redo does the reverse. A new
change clears the redo stack.

Snapshots share what is never changed in place (selection index arrays, point-set indices), so most are small; the
stack keeps at most ``limit`` entries and ``max_bytes`` of such arrays, dropping the oldest. The history lasts for
the session; labels live on annotation items, so undoing an item undoes its labels.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QAbstractSpinBox, QApplication


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


class _UndoKeysToTheApp(QObject):
    """Ctrl+Z / Ctrl+Y in a number field undo the app, not the field. A spin box keeps the focus after a change, and
    Qt's own text undo then claims the keys: each press stepped the number back (e.g. 3D select's vertical lines)
    instead of undoing the app. Text fields keep their own undo while typing."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.ShortcutOverride and (
            ev.matches(QKeySequence.StandardKey.Undo) or ev.matches(QKeySequence.StandardKey.Redo)
            or (ev.key() == Qt.Key.Key_Y and ev.modifiers() == Qt.KeyboardModifier.ControlModifier)
        ):  # fmt: skip
            spin = isinstance(obj, QAbstractSpinBox) or isinstance(getattr(obj, "parent", lambda: None)(), QAbstractSpinBox)
            if spin:
                ev.ignore()  # not claimed: the window's Undo / Redo shortcut runs
                return True
        return False


class UndoMixin:
    """Part of MainWindow: checkpoints, undo and redo, and the Edit menu's text."""

    def _init_undo(self):
        self.history = UndoStack()  # Ctrl+Z / Ctrl+Y: the selection, planes and annotations (this session)
        self._undo_keys = _UndoKeysToTheApp(self)
        QApplication.instance().installEventFilter(self._undo_keys)

    def _capture(self, kind: str):
        """(snapshot, bytes held) of one kind of state."""
        if kind == "selection":
            return (self.selection, self.selection_def, list(self.selection_ops)), int(self.selection.nbytes)
        if kind == "frames":
            return (list(self.frames), self.active_frame, self._last_plane), 0
        if kind == "annotations":
            return self.annot.snapshot(), 0
        raise ValueError(kind)

    def _restore(self, kind: str, state):
        if kind == "selection":
            self.selection, self.selection_def, ops = state
            self.selection_ops = list(ops)
            self._grow_state = None  # threshold changes must not re-run a click that was undone
            self._set_overlay()
            self._show_selection_count()
        elif kind == "frames":
            frames, active, last = state
            self.frames = list(frames)
            self._last_plane = last
            self.activate_frame(min(active, len(self.frames) - 1))
        else:
            self.annot.restore(state)

    def checkpoint(self, kind: str, label: str, before=None):
        """Record the state of ``kind`` before a change (or ``before``, a capture taken earlier)."""
        state, nbytes = before if before is not None else self._capture(kind)
        self.history.push(kind, label, state, nbytes)
        self._update_undo_actions()

    def _busy(self) -> bool:
        if self._sel_busy or self._grow_busy:
            self.statusBar().showMessage("still working: try again in a moment", 3000)
            return True
        return False

    def undo(self):
        if self.annot.pending:  # while drawing a segment or curve: take back the last clicked point
            self.annot.back()
            return
        if self._busy():
            return
        e = self.history.undo(self._capture)
        if e is None:
            self.statusBar().showMessage("nothing to undo", 3000)
            return
        self._restore(e.kind, e.state)
        self._update_undo_actions()
        self.statusBar().showMessage(f"undone: {e.label}", 5000)

    def redo(self):
        if self._busy():
            return
        e = self.history.redo(self._capture)
        if e is None:
            self.statusBar().showMessage("nothing to redo", 3000)
            return
        self._restore(e.kind, e.state)
        self._update_undo_actions()
        self.statusBar().showMessage(f"redone: {e.label}", 5000)

    def _update_undo_actions(self):
        if not hasattr(self, "_undo_action"):
            return
        u, r = self.history.undo_entries, self.history.redo_entries
        self._undo_action.setText(f"Undo {u[-1].label}" if u else "Undo")
        self._redo_action.setText(f"Redo {r[-1].label}" if r else "Redo")
