"""Set operations on point sets: A op B as a new point set or as the selection."""

from __future__ import annotations

from pclabel.store.annotations import SET_OPS, PointSet


class SetOpsMixin:
    """Part of AnnotationController."""

    # ---- set operations on point sets ----------------------------------------------------------------------

    def _ops_candidates(self) -> list:
        """(label, node id) of the point sets, and of the directories holding any."""
        out = []
        for x, anc in self.model.walk():
            if x.kind == "points" or (x.kind == "directory" and any(m.kind == "points" for m in self.model.items([x]))):
                path = "/".join([a.name for a in anc] + [x.name])
                out.append((f"{path}  ({'directory' if x.kind == 'directory' else 'points'})", x.id))
        return out

    def start_set_ops(self, a_id: str | None = None, b_id: str | None = None):
        """Open the set operations box, A and B from the selected entries (or these)."""
        cands = self._ops_candidates()
        if len(cands) < 1:
            self.dock.status.setText("no point sets yet")
            return
        picked = [i for i in self.selected_ids() if any(nid == i for _, nid in cands)]
        a_id = a_id or (picked[0] if picked else cands[0][1])
        b_id = b_id or (picked[1] if len(picked) > 1 else cands[min(1, len(cands) - 1)][1])
        d = self.dock
        self._fill_ops(cands, a_id, b_id)
        d.ops_box.show()
        d.show()
        d.raise_()
        self._ops_preview()

    def _fill_ops(self, cands, a_id, b_id):
        d = self.dock
        for c, nid in ((d.ops_a, a_id), (d.ops_b, b_id)):
            c.clear()
            for text, n in cands:
                c.addItem(text, n)
            c.setCurrentIndex(max(c.findData(nid), 0))

    def _refresh_ops(self):
        """After a change to the tree: the box's lists again, keeping A and B while they exist."""
        d = self.dock
        if d.ops_box.isHidden():
            return
        cands = self._ops_candidates()
        if not cands:
            d.ops_box.hide()
            return
        self._fill_ops(cands, d.ops_a.currentData(), d.ops_b.currentData())
        self._ops_preview()

    def _ops_choice(self):
        d = self.dock
        return d.ops_a.currentData(), d.ops_op.currentData(), d.ops_b.currentData()

    def _ops_preview(self):
        a, op, b = self._ops_choice()
        if a is None or b is None:
            return
        idx, rec = self.model.set_operation(a, op, b)
        self.dock.ops_result.setText(f"A {rec['a']['points']:,} points, B {rec['b']['points']:,}: "
                                     f"{SET_OPS[op][1]}: {idx.size:,} points")  # fmt: skip

    def set_op_result(self, where: str):
        """A op B as a new point set (``where`` "new") or as the selection ("select")."""
        a, op, b = self._ops_choice()
        if a is None or b is None:
            return None
        idx, rec = self.model.set_operation(a, op, b)
        name = f"{rec['a']['name']} {SET_OPS[op][0]} {rec['b']['name']}"
        if where == "select":
            self.w.set_selection(idx, {"tool": "set_operation", "op": "replace", "set_op": op, "name": name,
                                       "a": rec["a"], "b": rec["b"]})  # fmt: skip
            self.dock.ops_result.setText(f"{name}: {self.w.selection.size:,} points selected")
            return self.w.selection
        if idx.size == 0:
            self.dock.ops_result.setText(f"{name} is empty: nothing saved")
            return None
        self.checkpoint(f"new point set {name}")
        ps = self.model.add(PointSet(idx, [rec]), name=name)
        self.changed(f"{name}: {idx.size:,} points")
        self.dock.ops_result.setText(self.dock.status.text())
        return ps
