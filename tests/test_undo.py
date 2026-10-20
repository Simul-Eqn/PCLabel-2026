"""The undo stack, and the annotation snapshots it keeps."""

import numpy as np

from pclabel.store.annotations import Annotations, Curve, PointSet, clone_nodes
from pclabel.ui.undo import UndoStack


def test_undo_and_redo_restore_in_order():
    state = {"selection": "A"}
    stack = UndoStack()

    def change(value, label):
        stack.push("selection", label, state["selection"])
        state["selection"] = value

    def capture(kind):
        return state[kind], 0

    change("B", "to B")
    change("C", "to C")
    e = stack.undo(capture)
    state["selection"] = e.state
    assert (e.label, state["selection"]) == ("to C", "B")
    e = stack.undo(capture)
    state["selection"] = e.state
    assert state["selection"] == "A" and stack.undo(capture) is None
    e = stack.redo(capture)
    state["selection"] = e.state
    assert (e.label, state["selection"]) == ("to B", "B")
    change("D", "to D")  # a new change: what was undone can no longer be redone
    assert stack.redo(capture) is None and [x.label for x in stack.undo_entries] == ["to B", "to D"]


def test_the_stack_drops_its_oldest_entries():
    stack = UndoStack(limit=3, max_bytes=250)
    for k in range(5):
        stack.push("selection", f"step {k}", k, nbytes=100)
    assert [e.label for e in stack.undo_entries] == ["step 3", "step 4"]  # 300 bytes would be over 250


def test_snapshots_do_not_change_with_the_tree():
    t = Annotations()
    c = t.add(Curve.fit(np.array([[0.0, 0, 0], [2.0, 0, 0], [4.0, 0, 0]]), (0, 0, 1), (0, 0, 0)))
    p = t.add(PointSet(np.arange(1_000_000)))
    d = t.new_directory("lines")
    t.move([c.id], d.id)
    snap = clone_nodes(t.root)
    c.drag_handle(1, 1, c.nodes[1] + (1.0, 1.0, 0))  # edits in place
    c.insert(np.array([3.0, 0.5, 0.0]))
    d.name = "renamed"
    c_snap = snap[1].children[0]
    assert len(c_snap.nodes) == 3 and not c_snap.manual().any() and snap[1].name == "lines"
    assert c_snap.id == c.id and c_snap is not c
    assert snap[0].indices is p.indices  # point indices are shared, not copied
