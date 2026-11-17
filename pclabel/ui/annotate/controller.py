"""The annotations controller: the model, the panel, undo, and what is drawn in the view."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject

from pclabel.store.annotations import Annotations, Curve, clone_nodes
from pclabel.ui.annotate.dock import HANDLE, PENDING, PICKED, PIN, ROLE, AnnotationDock, rgba
from pclabel.ui.annotate.drawing import DrawingMixin
from pclabel.ui.annotate.items import ItemsMixin
from pclabel.ui.annotate.markers import MarkersMixin
from pclabel.ui.annotate.set_ops import SetOpsMixin
from pclabel.ui.annotate.tree import TreeMixin


class AnnotationController(DrawingMixin, MarkersMixin, ItemsMixin, SetOpsMixin, TreeMixin, QObject):
    """The annotations in the app: the model, its panel and its overlays. Its parts are the mixins beside this module."""

    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.vp = window.viewport
        self.dock = AnnotationDock(window)
        self.model = Annotations()
        self.project = None
        self.pending: list = []  # [(local xyz (3,), source index)] clicked for the segment or curve being drawn
        self.hover = None  # local point under the cursor (rubber band)
        self.editing: Curve | None = None
        self.active: int | None = None  # node of the edited curve whose handles are shown
        self.highlight: set = set()  # item ids selected in the tree (also through their directories)
        self.collapsed: set = set()  # directory ids folded in the tree
        self._lines: dict = {}  # curve id -> polyline (world)
        self._dragging = False  # a handle drag is under way (one undo step for the whole drag)
        self.version = 0  # counts changes, so the window knows when to work out the locked points again
        self.joining: tuple | None = None  # the two curve ids offered for joining

        d = self.dock
        d.segment.clicked.connect(lambda: self.w.toggle_tool("segment"))
        d.curve.clicked.connect(lambda: self.w.toggle_tool("curve"))
        d.finish.clicked.connect(lambda: self.finish())
        d.remove_node.clicked.connect(lambda: self.remove_node())
        d.auto.clicked.connect(self.auto_gradient)
        d.done.clicked.connect(self.done)
        d.new_dir.clicked.connect(lambda: self.new_directory())
        d.points.clicked.connect(self.points_from_selection)
        d.marker.clicked.connect(lambda: self.marker_from_selection())
        d.click_markers.clicked.connect(lambda on: self.w.toggle_tool("marker") if on else self.vp.set_tool(None))
        d.marker_move.clicked.connect(lambda: self.move_markers())
        d.attributes.clicked.connect(lambda: self.edit_attributes())
        d.marker_axis.clicked.connect(lambda: self.set_marker_axis())
        d.color.clicked.connect(lambda: self.set_color())
        d.select.clicked.connect(self.select_points)
        d.edit.clicked.connect(self.edit_selected)
        d.to_curves.clicked.connect(lambda: self.convert_selected("curve"))
        d.to_polylines.clicked.connect(lambda: self.convert_selected("polyline"))
        d.smooth.clicked.connect(lambda: self.smooth_selected())
        d.delete.clicked.connect(lambda: self.delete_selected())
        d.duplicate.clicked.connect(lambda: self.duplicate_selected())
        d.rename.clicked.connect(lambda: self.rename_selected())
        d.export_sel.clicked.connect(lambda: self.export(selected=True))
        d.export_all.clicked.connect(lambda: self.export())
        d.import_.clicked.connect(lambda: self.import_())
        d.export_labels.clicked.connect(lambda: self.export_labels())
        d.join.clicked.connect(lambda: self.start_join())
        d.split.clicked.connect(lambda: self.w.split.start())
        d.clip_to.clicked.connect(lambda: self.w.clip_to_selected_sets())
        d.density.clicked.connect(lambda: self.w.density.start())
        d.set_ops.clicked.connect(lambda: self.start_set_ops())
        d.ops_new.clicked.connect(lambda: self.set_op_result("new"))
        d.ops_select.clicked.connect(lambda: self.set_op_result("select"))
        d.ops_close.clicked.connect(lambda: d.ops_box.hide())
        for c in (d.ops_a, d.ops_op, d.ops_b):
            c.activated.connect(lambda _: self._ops_preview())
        d.join_ok.clicked.connect(lambda: self.confirm_join())
        d.join_cancel.clicked.connect(lambda: self.cancel_join())
        d.join_first.activated.connect(lambda _: self._join_first_changed())
        for c in (d.join_first_end, d.join_second_end):
            c.activated.connect(lambda _: self.refresh())
        d.join_close.toggled.connect(lambda _: self.refresh())
        d.set_label.clicked.connect(lambda: self.set_label())
        d.uncertain.clicked.connect(lambda: self.set_uncertain())
        d.locks_menu.aboutToShow.connect(self._fill_locks)
        d.edit_mode.activated.connect(lambda _: self.set_curve_mode(d.edit_mode.currentData()))
        d.edit_closed.clicked.connect(lambda on: self.set_closed(on))
        d.show_picked.toggled.connect(lambda _: self.refresh())
        d.tree.dropped.connect(self.on_drop)
        d.tree.itemSelectionChanged.connect(self.on_tree_selection)
        d.tree.itemChanged.connect(self.on_item_changed)
        d.tree.itemDoubleClicked.connect(self.on_double_click)
        d.tree.itemCollapsed.connect(lambda it: self.collapsed.add(it.data(0, ROLE)[1]))
        d.tree.itemExpanded.connect(lambda it: self.collapsed.discard(it.data(0, ROLE)[1]))
        self.vp.finish_requested.connect(self.finish)
        self.vp.back_requested.connect(self.back)
        self.vp.edit_node_clicked.connect(self.node_clicked)
        self.vp.edit_node_removed.connect(self.remove_node)
        self.vp.edit_delete_requested.connect(lambda: self.remove_node())
        self.vp.edit_handle_dragged.connect(self.handle_dragged)
        self.vp.edit_drag_finished.connect(self.drag_finished)
        self.vp.tool_changed.connect(self.on_tool_changed)

    # ---- project ------------------------------------------------------------------------------------------------

    def set_project(self, project):
        self.project = project
        self.version += 1
        self.pending, self.editing, self.active, self.highlight, self._lines = [], None, None, set(), {}
        self.model, warnings = Annotations.load(project)
        self.dock.status.setText("\n".join(warnings))
        self.rebuild_tree()
        self.refresh()

    def save(self):
        if self.project is not None:
            self.model.save(self.project)

    def checkpoint(self, label: str):
        """Before changing the model: keep the tree as it is for undo (one step, named ``label``). Every change to the
        annotations, from any controller, is ``checkpoint``, then the change to ``model``, then ``changed``."""
        self.w.checkpoint("annotations", label)

    def snapshot(self):
        return clone_nodes(self.model.root), self.model.counter

    def restore(self, snap):
        """Undo or redo: put the tree back as it was; curve edit mode carries on if its curve is still there."""
        root, counter = snap
        self.model.root, self.model.counter = clone_nodes(root), counter
        self._lines.clear()
        if self.editing is not None:
            try:
                c = self.model.node(self.editing.id)
            except KeyError:
                c = None
            if c is None or c.kind not in ("curve", "polyline"):
                self.done()
            else:
                self.editing = c
                self.dock.edit_closed.setChecked(c.closed)
                if self.active is not None and self.active >= len(c.nodes):
                    self.active = None
        self.changed()

    def _frame(self):
        return self.w.frames[self.w.active_frame]

    def _world(self, local):
        return np.asarray(local, np.float64) + self.project.offset

    def forget_line(self, item_id: str):
        """A line was replaced or changed outside curve edit mode: draw it from its nodes again."""
        self._lines.pop(item_id, None)

    def changed(self, status: str | None = None):
        """After changing the model: save the annotations, redraw the tree and the view, and show ``status``."""
        self.version += 1
        self.save()
        self.rebuild_tree()
        self.refresh()
        if status is not None:
            self.dock.status.setText(status)

    # ---- overlays ---------------------------------------------------------------------------------------------

    def _polyline(self, c: Curve) -> np.ndarray:
        if c.id not in self._lines:
            self._lines[c.id] = c.polyline()
        return self._lines[c.id]

    def refresh(self):
        r = self.vp.renderer
        if r is None or self.project is None:
            return
        off = self.project.offset
        lines, points = [], []
        labels = getattr(self.w, "labels", None)
        by_label = labels is not None and labels.active()  # the labels view: segments and curves in label colours
        if labels is not None:
            labels.sync()
        for m in self.model.items():
            hl = m.id in self.highlight or m is self.editing
            col = rgba(self.model.color_of(m.id))
            if by_label and m.kind != "points":
                col = labels.vector_rgba(m)
                if col is None:  # its label is unticked in the Labels panel
                    continue
            width = 4.5 if hl else 2.5
            if m.kind == "segment":
                ends = np.array([m.a, m.b]) - off
                lines.append((ends, col, width))
                points.append((ends, col, 8.0))
            elif m.kind == "curve":
                lines.append((self._polyline(m) - off, col, width))
                if m is not self.editing:
                    points.append((m.nodes - off, col, 6.0))
            elif m.kind == "marker":  # a position: a dot with a pin up the height axis
                p = m.polyline() - off
                up = self._marker_axis(m)[0]  # its own height axis
                points.append((p, col, 16.0 if hl else 11.0))
                lines.append((np.vstack([p, p + PIN * up]), col, 3.5 if hl else 2.0))
            elif m.kind == "polyline":  # imported: exact vertices; a single one is a point feature (a map symbol)
                v = m.polyline() - off
                if len(v) == 1:
                    points.append((v, col, 14.0 if hl else 9.0))
                else:
                    lines.append((v, col, width))
                    if hl:
                        points.append((v, col, 6.0))
        tracer = getattr(self.w, "trace", None)
        if tracer is not None:  # the curb or marking trace waiting to be saved (local coordinates)
            t_lines, t_points = tracer.overlay(off)
            lines += t_lines
            points += t_points
        targets = None
        if self.joining is not None:
            try:
                p_a, p_b = self._join_ends()
                a, b = self._join_pair()
            except KeyError:  # one of them went (undo, delete)
                self.joining = None
                self.dock.join_box.hide()
            else:
                lines.append((np.array([p_a, p_b]) - off, PENDING, 2.5))
                points += [(p_a[None] - off, (0.2, 0.9, 0.3, 1.0), 15.0), (p_b[None] - off, (1.0, 0.5, 0.1, 1.0), 15.0)]
                if self.dock.join_close.isChecked():  # the outer ends too
                    outer_a = a.nodes[-1 if self.dock.join_first_end.currentData() == "start" else 0]
                    outer_b = b.nodes[-1 if self.dock.join_second_end.currentData() == "start" else 0]
                    lines.append((np.array([outer_a, outer_b]) - off, (0.7, 0.7, 0.7, 1.0), 1.5))
        if self.editing is not None:
            c = self.editing
            nodes = c.nodes - off
            points.append((nodes, rgba(self.model.color_of(c.id)), 11.0))
            handles = []
            if self.active is not None and self.active < len(nodes) and c.kind == "polyline":
                points.append((nodes[self.active : self.active + 1], PENDING, 14.0))
            elif self.active is not None and self.active < len(nodes):
                i = self.active
                h_in, h_out = c.handles()
                if i > 0 or c.closed:
                    handles.append((i, -1, nodes[i] + h_in[i]))
                if i < len(nodes) - 1 or c.closed:
                    handles.append((i, 1, nodes[i] + h_out[i]))
                for _, _, q in handles:
                    lines.append((np.array([nodes[i], q]), HANDLE, 1.5))
                points.append((nodes[i : i + 1], PENDING, 14.0))
                if handles:
                    points.append((np.array([q for _, _, q in handles]), HANDLE, 11.0))
            targets = {"nodes": nodes, "handles": handles, "plane": (c.drag_plane(0)[0], c.drag_plane(0)[1] - off),
                       "node_planes": [(c.drag_plane(i)[0], nodes[i]) for i in range(len(nodes))]}  # fmt: skip
            if c.kind == "curve" and self.dock.show_picked.isChecked():  # where the points were clicked, and where they went
                picked = c.picked - off
                for q, nd in zip(picked, nodes):
                    lines.append((np.array([q, nd]), PICKED, 1.0))
                points.append((picked, PICKED, 7.0))
        if self.pending:
            pts = np.array([p for p, _ in self.pending])
            band = np.vstack([pts, self.hover[None]]) if self.hover is not None else pts
            if len(band) >= 2:
                lines.append((band, PENDING, 2.0))
            points.append((pts, PENDING, 9.0))
        self.vp.edit_targets = targets
        with self.vp.current():
            r.set_vectors(lines, points)
        self.vp.update()
