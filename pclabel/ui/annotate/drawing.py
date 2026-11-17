"""Drawing segments and curves, curve edit mode and joining two curves (see ``pclabel.ui.annotate``)."""

from __future__ import annotations

import numpy as np

from pclabel.store.annotations import Curve, Segment


class DrawingMixin:
    """Part of AnnotationController."""

    # ---- drawing segments and curves ------------------------------------------------------------------------

    def add_vertex(self, store_index: int):
        """A clicked point (store index) for the segment or curve being drawn."""
        if self.project is None or self.vp.tool not in ("segment", "curve"):
            return
        p = np.asarray(self.project.xyz[store_index], np.float64)
        self.pending.append((p, int(self.project.source_index[store_index])))
        if self.vp.tool == "segment" and len(self.pending) == 2:
            (a, sa), (b, sb) = self.pending
            self.checkpoint("new segment")
            s = self.model.add(Segment(self._world(a), self._world(b), [sa, sb]))
            self.pending = []
            self.changed(f"{s.name}: {s.describe()}")
            return
        n = len(self.pending)
        self.dock.status.setText(f"{n} point{'s' * (n != 1)}: " + (
            "click the second end" if self.vp.tool == "segment" else "click the next point, Enter to finish"))
        self.refresh()

    def hover_at(self, p_world):
        if self.pending and self.vp.tool in ("segment", "curve"):
            self.hover = None if p_world is None else np.asarray(p_world, np.float64) - self.project.offset
            self.refresh()

    def back(self):
        if self.pending:
            self.pending.pop()
            self.refresh()

    def finish(self, closed: bool = False):
        """Enter: finish the curve being drawn (then edit it), or leave curve edit mode. ``closed`` (Shift+Enter) or
        "closed loop" ticked: join the last point back to the first."""
        if self.vp.tool == "curve_edit":
            self.done()
            return
        if self.vp.tool != "curve" or self.project is None:
            return
        closed = closed or self.dock.new_closed.isChecked()
        if len(self.pending) < (3 if closed else 2):
            self.dock.status.setText(f"a {'closed loop needs at least three' if closed else 'curve needs at least two'} "
                                     "points")  # fmt: skip
            return
        f = self._frame()
        pts = np.array([self._world(p) for p, _ in self.pending])
        self.checkpoint("new closed loop" if closed else "new curve")
        mode = self.dock.curve_mode.currentData()
        c = self.model.add(Curve.fit(pts, f.normal, f.origin, f.name, [s for _, s in self.pending], mode, closed))
        self.pending = []
        self.changed()
        self.edit(c)
        self.dock.status.setText(f"{c.name}: {self._plane_report(c)}")

    @staticmethod
    def _plane_report(c: Curve) -> str:
        if c.kind == "polyline":
            return f"{len(c.vertices)} points, {c.length():.2f} m"
        if c.mode == "free":
            lo, hi = c.heights()
            return f"no plane: the points stay where they are (heights {lo:+.3f} to {hi:+.3f} m on {c.frame})"
        if c.mode == "axis":
            where = f"plane at height {c.height():+.3f} m on {c.frame}"
        else:
            how = "through the ends" if c.mode == "ends" else "fitted to all points"
            where = f"plane {how}, {c.tilt_deg():.1f}° from perpendicular to {c.frame}"
        return f"{where}; the points are up to {c.spread() * 100:.1f} cm from it"

    # ---- curve edit mode --------------------------------------------------------------------------------------

    def edit(self, curve: Curve):
        """Curve edit mode, for a curve or a polyline (straight pieces: points only, no handles or plane)."""
        self.editing, self.active = curve, None
        self.vp.set_tool("curve_edit")
        is_curve = curve.kind == "curve"
        self.dock.edit_label.setText(f"editing {curve.name}: drag a point to move it"
                                     + ("; click one for its handles and drag them" if is_curve else " (level)")
                                     + "; Shift+click the cloud adds a point, Ctrl+click a point removes it")  # fmt: skip
        for wdg in (self.dock.edit_plane, self.dock.edit_mode, self.dock.show_picked, self.dock.auto):
            wdg.setVisible(is_curve)
        if is_curve:
            self.dock.edit_mode.setCurrentIndex(self.dock.edit_mode.findData(curve.mode))
        self.dock.edit_closed.setChecked(curve.closed)
        self.dock.edit_box.show()
        self.dock.show()
        self.dock.raise_()
        self.refresh()

    def set_closed(self, on: bool):
        c = self.editing
        if c is None or bool(on) == c.closed:
            return
        if on and len(c.nodes) < 3:
            self.dock.edit_closed.setChecked(False)
            self.dock.status.setText("a closed loop needs at least three points")
            return
        self.checkpoint(f"{'close' if on else 'open'} {c.name}")
        c.set_closed(on)
        self._curve_changed()
        self.changed(f"{c.name}: {'closed loop' if on else 'open curve'}")

    def set_curve_mode(self, mode: str):
        c = self.editing
        if c is None or mode == c.mode:
            return
        self.checkpoint(f"plane of {c.name}")
        c.set_mode(mode)
        self._curve_changed()
        self.changed(f"{c.name}: {self._plane_report(c)}")

    def done(self):
        if self.editing is None:
            return
        self.editing, self.active = None, None
        self.vp.edit_targets = None
        self.dock.edit_box.hide()
        if self.vp.tool == "curve_edit":
            self.vp.set_tool(None)
        self.changed()

    def _curve_changed(self):
        self._lines.pop(self.editing.id, None)

    def node_clicked(self, i: int):
        if self.editing is None:
            return
        self.editing.activate(i)
        self.active = i
        self._curve_changed()
        self.refresh()

    def handle_dragged(self, i: int, side: int, q_local):
        """A handle (side +1 out, -1 in) or, side 0, a node itself, dragged in its drag plane."""
        if self.editing is None:
            return
        if not self._dragging:
            self.checkpoint(f"{'move a point of' if side == 0 else 'gradient of'} {self.editing.name}")
            self._dragging = True
        self._mark_edited(self.editing)
        if side == 0:
            self.editing.move_node(i, self._world(q_local))
        else:
            self.editing.drag_handle(i, side, self._world(q_local))
        self.active = i
        self._curve_changed()
        self.refresh()

    def drag_finished(self):
        self._dragging = False
        self.save()

    def add_node(self, store_index: int):
        """Shift+click in curve edit mode: a cloud point becomes a node where the curve passes closest."""
        c = self.editing
        if c is None or self.project is None:
            return
        p = self._world(self.project.xyz[store_index])
        self.checkpoint(f"add a point to {c.name}")
        self._mark_edited(c)
        i = c.insert(p, int(self.project.source_index[store_index]))
        self.active = None
        self._curve_changed()
        self.changed(f"{c.name}: point {i + 1} of {len(c.nodes)} added; {self._plane_report(c)}")

    def remove_node(self, i: int | None = None):
        c = self.editing
        i = self.active if i is None else i
        if c is None or i is None:
            return
        if len(c.nodes) <= 2:
            self.dock.status.setText("a curve keeps at least two points")
            return
        self.checkpoint(f"remove a point from {c.name}")
        try:
            c.remove(i)
        except ValueError as e:
            self.dock.status.setText(str(e))
            return
        self._mark_edited(c)
        self.active = None
        self._curve_changed()
        self.changed(f"{c.name}: point {i + 1} removed, {len(c.nodes)} left")

    def auto_gradient(self):
        if self.editing is not None and self.active is not None:
            self.checkpoint(f"automatic gradient on {self.editing.name}")
            self.editing.make_auto(self.active)
            self._curve_changed()
            self.save()
            self.refresh()

    def on_tool_changed(self, name):
        self.dock.click_markers.setChecked(name == "marker")
        if name not in ("segment", "curve"):
            self.pending, self.hover = [], None
        if name != "curve_edit" and self.editing is not None:
            self.done()
        self.dock.segment.setChecked(name == "segment")
        self.dock.curve.setChecked(name == "curve")
        self.refresh()

    # ---- joining two curves ---------------------------------------------------------------------------------

    def start_join(self, first_id: str | None = None, second_id: str | None = None):
        """Offer to join the two selected open curves (or these two), with the closest pair of ends chosen."""
        if first_id is None:
            curves_ = [m for m in self.selected_items() if m.kind == "curve"]
            if len(curves_) != 2:
                self.dock.status.setText("select exactly two curves to join")
                return
            first_id, second_id = curves_[0].id, curves_[1].id
        a, b = self.model.node(first_id), self.model.node(second_id)
        if a.closed or b.closed:
            self.dock.status.setText("a closed loop has no ends to join: open it first (curve edit mode)")
            return
        self.joining = (a.id, b.id)
        d = self.dock
        d.join_first.clear()
        d.join_first.addItem(a.name, a.id)
        d.join_first.addItem(b.name, b.id)
        d.join_close.setChecked(False)
        self._join_first_changed(pick_ends=True)
        d.join_box.show()
        d.show()
        d.raise_()

    def _join_pair(self):
        """(first, second) curves as chosen in the join box."""
        first = self.model.node(self.dock.join_first.currentData())
        other = self.joining[1] if first.id == self.joining[0] else self.joining[0]
        return first, self.model.node(other)

    def _join_first_changed(self, pick_ends: bool = True):
        if self.joining is None:
            return
        a, b = self._join_pair()
        self.dock.join_second.setText(b.name)
        if pick_ends:  # the closest pair of ends
            ends = {(ea, eb): np.linalg.norm(a.nodes[0 if ea == "start" else -1] - b.nodes[0 if eb == "start" else -1])
                    for ea in ("start", "end") for eb in ("start", "end")}  # fmt: skip
            ea, eb = min(ends, key=ends.get)
            self.dock.join_first_end.setCurrentIndex(self.dock.join_first_end.findData(ea))
            self.dock.join_second_end.setCurrentIndex(self.dock.join_second_end.findData(eb))
        self.refresh()

    def _join_ends(self):
        """World points of the two ends to be joined."""
        a, b = self._join_pair()
        ea, eb = self.dock.join_first_end.currentData(), self.dock.join_second_end.currentData()
        return a.nodes[0 if ea == "start" else -1], b.nodes[0 if eb == "start" else -1]

    def confirm_join(self):
        if self.joining is None:
            return
        a, b = self._join_pair()
        ea, eb = self.dock.join_first_end.currentData(), self.dock.join_second_end.currentData()
        gap = float(np.linalg.norm(np.subtract(*self._join_ends())))
        names = (a.name, b.name)
        self.checkpoint(f"join {names[0]} and {names[1]}")
        try:
            c = self.model.join_curves(a.id, ea, b.id, eb, self.dock.join_close.isChecked())
        except ValueError as e:
            self.dock.status.setText(str(e))
            return
        self.joining = None
        self.dock.join_box.hide()
        self._lines.clear()
        self.changed(f"{names[0]} ({ea}) joined to {names[1]} ({eb}) across {gap:.2f} m: {c.describe()}; "
                      f"{self._plane_report(c)}")  # fmt: skip
        return c

    def cancel_join(self):
        self.joining = None
        self.dock.join_box.hide()
        self.refresh()

    def escape(self) -> bool:
        """Esc: leave curve edit mode, or drop the points clicked so far. False when there was nothing to do."""
        if self.joining is not None:
            self.cancel_join()
            return True
        if self.editing is not None:
            self.done()
            return True
        if self.pending:
            self.pending, self.hover = [], None
            self.dock.status.setText("")
            self.refresh()
            return True
        return False

    @staticmethod
    def _mark_edited(c):
        """A line a tool drew, changed by hand: its source says so (the labeller's corrections are the evidence
        for tuning the tool)."""
        if c.source:
            c.source["edited"] = True
