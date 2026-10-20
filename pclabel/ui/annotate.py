"""Vector annotations in the app (SPEC §13): the segment (V) and curve (Shift+V) tools, curve edit mode and the
Annotations panel. The model and its files are ``pclabel.store.annotations``; this module wires them to the viewport
and the panel.

- Segment: click two points of the cloud (the nearest full-resolution points, as with a plain click).
- Curve: click points in order; Enter fits the curve's plane, projects the points onto it and enters curve edit
  mode (Shift+Enter, or "closed loop" ticked: as a closed loop, the last point joined back to the first). The plane goes through the start and end points and turns about the line between them to fit the others
  (the default), or is perpendicular to the active height axis at the points' mean height. Backspace takes back the
  last point, Esc drops them.
- Curve edit mode: only the curve's nodes react. Click a node to give it a gradient (its handles appear), drag a
  handle to set it; "automatic" gives the node back its automatic handles. Shift+click the cloud adds a point where
  the curve passes closest; Ctrl+click a node (or Del with a node active) removes it. Enter, Esc or "done" ends it.
- The panel: a tree of directories and items. New items go to the top level; drag items or directories onto a
  directory to put them in it (onto empty space: back to the top level). Everything in a directory is drawn in its
  colour. Labels (taxonomy classes, or any text) go on directories or items; an item without one takes its
  directory's. "Export labels" writes the items grouped by label.
- Join: two open curves become one, an end of the first (start or end) joined to an end of the second; the
  closest pair of ends is offered first and drawn before joining. The result keeps the first curve's place, name,
  label and plane mode, and fits its plane to all the points again.
- Locks: the points of point sets with a locked label cannot be selected by any tool; classes marked
  ``selectable: false`` in the taxonomy (``other``, "no class") are always locked.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pclabel.store.annotations import SET_OPS, CURVE_MODES, Annotations, Curve, PointSet, Segment, clone_nodes
from pclabel.tools import sets as sets_

ROLE = Qt.ItemDataRole.UserRole
PENDING = (1.0, 1.0, 1.0, 1.0)
HANDLE = (1.0, 0.85, 0.1, 1.0)
PICKED = (1.0, 0.2, 0.9, 1.0)  # clicked points, shown on request in curve edit mode


def rgba(hex_color: str, alpha: float = 1.0) -> tuple:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)) + (alpha,)


class AnnotationTree(QTreeWidget):
    """Directories and items. A drop does not move items itself: it reports what was dropped where (a directory id,
    or None for the top level), and the tree is rebuilt from the model."""

    dropped = Signal(list, object)  # ids of the dragged nodes, id of the destination directory (None: top level)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["name", "label", "contents"])
        self.setColumnWidth(0, 160)
        self.setColumnWidth(1, 90)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed)

    def dropEvent(self, e):
        target = self.itemAt(e.position().toPoint())
        ids = [it.data(0, ROLE)[1] for it in self.selectedItems()]
        e.setDropAction(Qt.DropAction.IgnoreAction)
        e.ignore()
        dest = None
        if target is not None:
            kind, tid = target.data(0, ROLE)
            on = self.dropIndicatorPosition() == QAbstractItemView.DropIndicatorPosition.OnItem
            if kind == "directory" and on:
                dest = tid
            elif target.parent() is not None:  # beside an item: into the directory it is in
                dest = target.parent().data(0, ROLE)[1]
        self.dropped.emit(ids, dest)


class AnnotationDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Annotations", parent)
        self.setObjectName("annotations")
        w = QWidget()
        v = QVBoxLayout(w)
        hint = QLabel("Segment (V): two clicks. Curve (Shift+V): clicks in order, then Enter. Ctrl+G: the selection "
                      "as a point set. Drag onto a directory to put things in it. F2 renames.")  # fmt: skip
        hint.setWordWrap(True)
        v.addWidget(hint)
        row = QHBoxLayout()
        self.segment = QPushButton("segment (V)")
        self.curve = QPushButton("curve (Shift+V)")
        self.finish = QPushButton("finish (Enter)")
        for b in (self.segment, self.curve):
            b.setCheckable(True)
        for b in (self.segment, self.curve, self.finish):
            row.addWidget(b)
        v.addLayout(row)
        mrow = QHBoxLayout()
        mrow.addWidget(QLabel("new curves' plane:"))
        self.curve_mode = _mode_combo()
        mrow.addWidget(self.curve_mode, 1)
        self.new_closed = QCheckBox("closed loop")
        self.new_closed.setToolTip("new curves join their last point back to the first (Shift+Enter does it once)")
        mrow.addWidget(self.new_closed)
        v.addLayout(mrow)
        self.edit_box = QWidget()
        e = QVBoxLayout(self.edit_box)
        e.setContentsMargins(0, 0, 0, 0)
        self.edit_label = QLabel("")
        self.edit_label.setWordWrap(True)
        e.addWidget(self.edit_label)
        pr = QHBoxLayout()
        pr.addWidget(QLabel("plane:"))
        self.edit_mode = _mode_combo()
        self.edit_mode.setToolTip("how this curve's plane is chosen; changing it fits the plane again")
        pr.addWidget(self.edit_mode, 1)
        self.show_picked = QCheckBox("show clicked points")
        self.show_picked.setToolTip("where you clicked (magenta), with a line to each point's place on the curve's plane")
        pr.addWidget(self.show_picked)
        self.edit_closed = QCheckBox("closed loop")
        self.edit_closed.setToolTip("join the last point back to the first (a loop needs three points)")
        pr.addWidget(self.edit_closed)
        e.addLayout(pr)
        er = QHBoxLayout()
        self.remove_node = QPushButton("remove (Del)")
        self.auto = QPushButton("automatic")
        self.auto.setToolTip("give the active node back its automatic handles")
        self.done = QPushButton("done (Enter)")
        for b in (self.remove_node, self.auto, self.done):
            er.addWidget(b)
        e.addLayout(er)
        self.edit_box.hide()
        v.addWidget(self.edit_box)
        self.join_box = QWidget()
        jl = QGridLayout(self.join_box)
        jl.setContentsMargins(0, 0, 0, 0)
        self.join_first = QComboBox()
        self.join_first.setToolTip("the first curve: the joined curve keeps its place, name, label and plane mode")
        self.join_first_end = QComboBox()
        self.join_second_end = QComboBox()
        for c in (self.join_first_end, self.join_second_end):
            c.addItem("its start", "start")
            c.addItem("its end", "end")
        self.join_second = QLabel("")
        self.join_close = QCheckBox("and close the loop")
        self.join_close.setToolTip("join the two outer ends as well")
        self.join_ok = QPushButton("join")
        self.join_cancel = QPushButton("cancel")
        jl.addWidget(QLabel("join"), 0, 0)
        jl.addWidget(self.join_first, 0, 1)
        jl.addWidget(self.join_first_end, 0, 2)
        jl.addWidget(QLabel("to"), 1, 0)
        jl.addWidget(self.join_second, 1, 1)
        jl.addWidget(self.join_second_end, 1, 2)
        jl.addWidget(self.join_close, 2, 0, 1, 2)
        jl.addWidget(self.join_ok, 3, 1)
        jl.addWidget(self.join_cancel, 3, 2)
        self.join_box.hide()
        v.addWidget(self.join_box)
        self.tree = AnnotationTree()
        self.tree.setMinimumHeight(200)
        v.addWidget(self.tree, 1)
        self.ops_box = QWidget()
        ol = QGridLayout(self.ops_box)
        ol.setContentsMargins(0, 0, 0, 0)
        self.ops_a, self.ops_b = QComboBox(), QComboBox()
        self.ops_op = QComboBox()
        for op, (sym, words) in SET_OPS.items():
            self.ops_op.addItem(f"A {sym} B: {words}", op)
        self.ops_new = QPushButton("as a new point set")
        self.ops_select = QPushButton("as the selection")
        self.ops_close = QPushButton("close")
        self.ops_result = QLabel("")
        self.ops_result.setWordWrap(True)
        ol.addWidget(QLabel("A"), 0, 0)
        ol.addWidget(self.ops_a, 0, 1, 1, 3)
        ol.addWidget(QLabel("B"), 1, 0)
        ol.addWidget(self.ops_b, 1, 1, 1, 3)
        ol.addWidget(QLabel("result"), 2, 0)
        ol.addWidget(self.ops_op, 2, 1, 1, 3)
        ol.addWidget(self.ops_new, 3, 1)
        ol.addWidget(self.ops_select, 3, 2)
        ol.addWidget(self.ops_close, 3, 3)
        ol.addWidget(self.ops_result, 4, 0, 1, 4)
        self.ops_box.hide()
        v.addWidget(self.ops_box)
        lrow = QHBoxLayout()
        lrow.addWidget(QLabel("label:"))
        self.label = QComboBox()
        self.label.setEditable(True)
        self.label.setToolTip("a taxonomy class, or any text; empty clears the label")
        self.set_label = QPushButton("set label")
        self.set_label.setToolTip("give the selected directories or items this label (empty: remove theirs)")
        self.locks = QToolButton()
        self.locks.setText("locks")
        self.locks.setToolTip("labels whose points cannot be selected (tick to lock)")
        self.locks_menu = QMenu(self.locks)
        self.locks.setMenu(self.locks_menu)
        self.locks.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        lrow.addWidget(self.label, 1)
        lrow.addWidget(self.set_label)
        lrow.addWidget(self.locks)
        v.addLayout(lrow)
        grid = QGridLayout()
        self.new_dir = QPushButton("new directory")
        self.new_dir.setToolTip("at the top level, or inside the selected directory")
        self.points = QPushButton("points (Ctrl+G)")
        self.points.setToolTip("make the current selection a point set")
        self.color = QPushButton("colour")
        self.color.setToolTip("the colour of the selected directory (everything in it) or top-level item")
        self.select = QPushButton("select points")
        self.select.setToolTip("make the point sets among the selected items (and in selected directories) the selection")
        self.edit = QPushButton("edit curve")
        self.delete = QPushButton("delete")
        self.export_sel = QPushButton("export selected")
        self.export_all = QPushButton("export all")
        self.import_ = QPushButton("import")
        self.set_ops = QPushButton("set operations...")
        self.set_ops.setToolTip("A and B (point sets, or directories: all their point sets): union, intersection, "
                                "difference; the result as a new point set or as the selection")  # fmt: skip
        self.split = QPushButton("split...")
        self.split.setToolTip("split the selected point set in two by roughness (the Split panel)")
        self.join = QPushButton("join curves")
        self.join.setToolTip("join the two selected curves into one, choosing which ends meet")
        self.export_labels = QPushButton("export labels")
        self.export_labels.setToolTip("everything grouped by label (not by directory), as JSON")
        for i, b in enumerate((self.new_dir, self.points, self.color, self.select, self.edit, self.delete,
                               self.export_sel, self.export_all, self.import_, self.export_labels,
                               self.join, self.split, self.set_ops)):  # fmt: skip
            grid.addWidget(b, i // 4, i % 4)
        v.addLayout(grid)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.status)
        self.setWidget(w)


MODE_NAMES = {"ends": "through the ends", "axis": "flat on the height axis", "fit": "fit all points"}


def _mode_combo() -> QComboBox:
    c = QComboBox()
    for mode, text in CURVE_MODES.items():
        c.addItem(MODE_NAMES[mode], mode)
        c.setItemData(c.count() - 1, f"the plane goes {text}", Qt.ItemDataRole.ToolTipRole)
    return c


class AnnotationController(QObject):
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
        d.color.clicked.connect(lambda: self.set_color())
        d.select.clicked.connect(self.select_points)
        d.edit.clicked.connect(self.edit_selected)
        d.delete.clicked.connect(lambda: self.delete_selected())
        d.export_sel.clicked.connect(lambda: self.export(selected=True))
        d.export_all.clicked.connect(lambda: self.export())
        d.import_.clicked.connect(lambda: self.import_())
        d.export_labels.clicked.connect(lambda: self.export_labels())
        d.join.clicked.connect(lambda: self.start_join())
        d.split.clicked.connect(lambda: self.w.split.start())
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

    def _checkpoint(self, label: str):
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
            if c is None or c.kind != "curve":
                self.done()
            else:
                self.editing = c
                self.dock.edit_closed.setChecked(c.closed)
                if self.active is not None and self.active >= len(c.nodes):
                    self.active = None
        self._changed()

    def _frame(self):
        return self.w.frames[self.w.active_frame]

    def _world(self, local):
        return np.asarray(local, np.float64) + self.project.offset

    def _changed(self, status: str | None = None):
        self.version += 1
        self.save()
        self.rebuild_tree()
        self.refresh()
        if status is not None:
            self.dock.status.setText(status)

    # ---- drawing segments and curves ------------------------------------------------------------------------

    def add_vertex(self, store_index: int):
        """A clicked point (store index) for the segment or curve being drawn."""
        if self.project is None or self.vp.tool not in ("segment", "curve"):
            return
        p = np.asarray(self.project.xyz[store_index], np.float64)
        self.pending.append((p, int(self.project.source_index[store_index])))
        if self.vp.tool == "segment" and len(self.pending) == 2:
            (a, sa), (b, sb) = self.pending
            self._checkpoint("new segment")
            s = self.model.add(Segment(self._world(a), self._world(b), [sa, sb]))
            self.pending = []
            self._changed(f"{s.name}: {s.describe()}")
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
        self._checkpoint("new closed loop" if closed else "new curve")
        mode = self.dock.curve_mode.currentData()
        c = self.model.add(Curve.fit(pts, f.normal, f.origin, f.name, [s for _, s in self.pending], mode, closed))
        self.pending = []
        self._changed()
        self.edit(c)
        self.dock.status.setText(f"{c.name}: {self._plane_report(c)}")

    @staticmethod
    def _plane_report(c: Curve) -> str:
        if c.mode == "axis":
            where = f"plane at height {c.height():+.3f} m on {c.frame}"
        else:
            how = "through the ends" if c.mode == "ends" else "fitted to all points"
            where = f"plane {how}, {c.tilt_deg():.1f}° from perpendicular to {c.frame}"
        return f"{where}; the points are up to {c.spread() * 100:.1f} cm from it"

    # ---- curve edit mode --------------------------------------------------------------------------------------

    def edit(self, curve: Curve):
        self.editing, self.active = curve, None
        self.vp.set_tool("curve_edit")
        self.dock.edit_label.setText(f"editing {curve.name}: click a point for its handles and drag them; "
                                     "Shift+click the cloud adds a point, Ctrl+click a point removes it")  # fmt: skip
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
        self._checkpoint(f"{'close' if on else 'open'} {c.name}")
        c.set_closed(on)
        self._curve_changed()
        self._changed(f"{c.name}: {'closed loop' if on else 'open curve'}")

    def set_curve_mode(self, mode: str):
        c = self.editing
        if c is None or mode == c.mode:
            return
        self._checkpoint(f"plane of {c.name}")
        c.set_mode(mode)
        self._curve_changed()
        self._changed(f"{c.name}: {self._plane_report(c)}")

    def done(self):
        if self.editing is None:
            return
        self.editing, self.active = None, None
        self.vp.edit_targets = None
        self.dock.edit_box.hide()
        if self.vp.tool == "curve_edit":
            self.vp.set_tool(None)
        self._changed()

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
        if self.editing is None:
            return
        if not self._dragging:
            self._checkpoint(f"gradient of {self.editing.name}")
            self._dragging = True
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
        self._checkpoint(f"add a point to {c.name}")
        i = c.insert(p, int(self.project.source_index[store_index]))
        self.active = None
        self._curve_changed()
        self._changed(f"{c.name}: point {i + 1} of {len(c.nodes)} added; {self._plane_report(c)}")

    def remove_node(self, i: int | None = None):
        c = self.editing
        i = self.active if i is None else i
        if c is None or i is None:
            return
        if len(c.nodes) <= 2:
            self.dock.status.setText("a curve keeps at least two points")
            return
        self._checkpoint(f"remove a point from {c.name}")
        try:
            c.remove(i)
        except ValueError as e:
            self.dock.status.setText(str(e))
            return
        self.active = None
        self._curve_changed()
        self._changed(f"{c.name}: point {i + 1} removed, {len(c.nodes)} left")

    def auto_gradient(self):
        if self.editing is not None and self.active is not None:
            self._checkpoint(f"automatic gradient on {self.editing.name}")
            self.editing.make_auto(self.active)
            self._curve_changed()
            self.save()
            self.refresh()

    def on_tool_changed(self, name):
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
            curves_ = [m for m in self._selected_items() if m.kind == "curve"]
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
        self._checkpoint(f"join {names[0]} and {names[1]}")
        try:
            c = self.model.join_curves(a.id, ea, b.id, eb, self.dock.join_close.isChecked())
        except ValueError as e:
            self.dock.status.setText(str(e))
            return
        self.joining = None
        self.dock.join_box.hide()
        self._lines.clear()
        self._changed(f"{names[0]} ({ea}) joined to {names[1]} ({eb}) across {gap:.2f} m: {c.describe()}; "
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

    # ---- the tree -----------------------------------------------------------------------------------------------

    def points_from_selection(self):
        if self.project is None:
            return
        sel = self.w.selection
        if sel.size == 0:
            self.dock.status.setText("nothing selected")
            return
        self._checkpoint("new point set")
        ps = self.model.add(PointSet(sel.copy(), [dict(op) for op in self.w.selection_ops]))
        self._changed(f"{ps.name}: {sel.size:,} points")
        self.dock.show()
        self.dock.raise_()

    def selected_ids(self) -> list:
        return [it.data(0, ROLE)[1] for it in self.dock.tree.selectedItems()]

    def new_directory(self, name: str | None = None):
        sel = [self.model.node(i) for i in self.selected_ids()]
        parent = sel[0].id if len(sel) == 1 and sel[0].kind == "directory" else None
        self._checkpoint("new directory")
        d = self.model.new_directory(name, parent)
        self._changed(f"{d.name} created")
        item = self._item_of(d.id)
        if item is not None and name is None:
            self.dock.tree.editItem(item, 0)  # type its name
        return d

    def on_drop(self, ids, dest):
        if ids:
            self.move(ids, dest)

    def move(self, ids, dest):
        self._checkpoint(f"move {len(ids)} entr{'y' if len(ids) == 1 else 'ies'}")
        self.model.move(ids, dest)
        self._changed()

    def _selected_items(self) -> list:
        """Items selected in the tree, with the items inside selected directories."""
        nodes = [self.model.node(i) for i in self.selected_ids()]
        return list({m.id: m for m in self.model.items(nodes)}.values())

    def set_label(self, label: str | None = None):
        """Give the selected directories and items a label (None: the label box's text; empty removes it)."""
        ids = self.selected_ids()
        if not ids:
            self.dock.status.setText("select directories or items to label first")
            return
        label = (self.dock.label.currentText() if label is None else label).strip()
        self._checkpoint(f"label {label!r}" if label else "remove label")
        for i in ids:
            self.model.node(i).label = label
        self._changed(f"{len(ids)} entr{'y' if len(ids) == 1 else 'ies'} labelled {label!r}" if label else
                      f"label removed from {len(ids)} entr{'y' if len(ids) == 1 else 'ies'}")  # fmt: skip

    def _labels_in_order(self, labels) -> list:
        order = {c["name"]: int(c["id"]) for c in self.w.taxonomy.classes}
        return sorted(labels, key=lambda k: (k not in order, order.get(k, 0), k))

    def _fill_locks(self):
        """The locks menu: the switch for all locks, then every label that point sets carry and the classes
        never selected by default (``other``, "no class"), which can be unlocked too."""
        m = self.dock.locks_menu
        m.clear()
        on = m.addAction("locks on")
        on.setCheckable(True)
        on.setChecked(self.model.locks_on)
        on.toggled.connect(self.set_locks_on)
        m.addSeparator()
        never = self.w.never_selected()
        used = {self.model.label_of(x.id)[0] for x in self.model.items() if x.kind == "points"} - {""}
        for lab in self._labels_in_order(used | never):
            a = m.addAction(f"{lab}  (no class: locked unless unticked)" if lab in never else lab)
            a.setCheckable(True)
            a.setChecked(lab not in self.model.unlocked if lab in never else lab in self.model.locks)
            a.setEnabled(self.model.locks_on)
            a.toggled.connect(lambda on_, lab=lab: self.set_lock(lab, on_))
        if not used:
            m.addAction("(label point sets to lock them)").setEnabled(False)

    def set_lock(self, label: str, on: bool):
        """Lock a label (its points cannot be selected) or unlock it (also a never-selected class)."""
        if label in self.w.never_selected():
            (self.model.unlocked.discard if on else self.model.unlocked.add)(label)
        else:
            (self.model.locks.add if on else self.model.locks.discard)(label)
        self._changed(f"{label!r} {'locked: its points cannot be selected' if on else 'unlocked'}")

    def set_locks_on(self, on: bool):
        """All locks on or off (the choice of labels is kept)."""
        self.model.locks_on = bool(on)
        self._changed("locks on: locked labels cannot be selected" if on else "locks off: everything can be selected")

    def _class_ids(self) -> dict:
        return {c["name"]: int(c["id"]) for c in self.w.taxonomy.classes if int(c["id"]) != 0}

    def export_labels(self, path=None):
        if self.project is None:
            return None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self.w, "Export labels", f"{self.project.name} labels.json",
                                                  "Labels (*.json)")  # fmt: skip
            if not path:
                return None
        summary = self.model.export_labels(path, self.project, self._class_ids())
        parts = [f"{k or 'unlabelled'}: {n} item{'s' * (n != 1)}" + (f", {pts:,} points" if pts else "")
                 for k, (n, pts) in sorted(summary.items(), key=lambda kv: (kv[0] == "", kv[0]))]  # fmt: skip
        self.dock.status.setText(f"exported labels to {path}\n" + "; ".join(parts))
        return path

    def _fill_labels(self):
        """The label box: the enabled taxonomy classes, then labels in use that are not classes."""
        box = self.dock.label
        text = box.currentText()
        names = [c["name"] for c in self.w.taxonomy.classes if int(c["id"]) != 0 and c.get("enabled", True)]
        used = sorted({x.label for x, _ in self.model.walk() if x.label} - set(names))
        box.blockSignals(True)
        box.clear()
        box.addItems(names + used)
        box.setCurrentText(text)
        box.blockSignals(False)

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
        self._checkpoint(f"new point set {name}")
        ps = self.model.add(PointSet(idx, [rec]), name=name)
        self._changed(f"{name}: {idx.size:,} points")
        self.dock.ops_result.setText(self.dock.status.text())
        return ps

    def select_points(self):
        sets = [m for m in self._selected_items() if m.kind == "points"]
        if not sets:
            self.dock.status.setText("no point set among the selected items")
            return
        idx = sets_.union_all([m.indices for m in sets])  # sorted already: a merge, not a sort (24 s -> < 1 s)
        self.w.set_selection(idx, {"tool": "annotations", "op": "replace", "point_sets": [m.name for m in sets]})

    def edit_selected(self):
        curves_ = [m for m in self._selected_items() if m.kind == "curve"]
        if curves_:
            self.edit(curves_[0])

    def set_color(self, color: str | None = None):
        ids = self.selected_ids()
        if not ids:
            return
        if color is None:
            c = QColorDialog.getColor(QColor(self.model.color_of(ids[0])), self.w, "Colour")
            if not c.isValid():
                return
            color = c.name()
        self._checkpoint("colour")
        inside = 0
        for i in ids:
            x = self.model.node(i)
            x.color = color
            inside += x.kind != "directory" and self.model.parent(i) is not None
        self._changed(f"{inside} item(s) inside a directory keep its colour" if inside else None)

    def delete_selected(self, confirm: bool = True):
        ids = self.selected_ids()
        if not ids:
            return
        n_items = len(self._selected_items())
        if confirm and QMessageBox.question(
                self.w, "Delete", f"Delete {len(ids)} selected entr{'y' if len(ids) == 1 else 'ies'} "
                f"({n_items} item{'s' * (n_items != 1)} in all)?") != QMessageBox.StandardButton.Yes:  # fmt: skip
            return
        self._checkpoint(f"delete {len(ids)} entr{'y' if len(ids) == 1 else 'ies'}")
        for i in ids:
            try:
                self.model.remove(i)
            except (KeyError, ValueError):
                pass  # already gone with its directory
        if self.editing is not None and self.editing.id not in {m.id for m in self.model.items()}:
            self.done()
        self._changed()

    def export(self, path=None, selected: bool = False):
        if self.project is None:
            return
        ids = self.selected_ids() if selected else None
        if selected and not ids:
            self.dock.status.setText("select what to export first")
            return
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self.w, "Export annotations", f"{self.project.name} annotations.json",
                                                  "Annotations (*.json)")  # fmt: skip
            if not path:
                return
        self.model.export(path, self.project, ids)
        what = f"{len(ids)} selected entr{'y' if len(ids) == 1 else 'ies'}" if ids else "everything"
        self.dock.status.setText(f"exported {what} to {path}")

    def import_(self, path=None):
        if self.project is None:
            return
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self.w, "Import annotations", "", "Annotations (*.json)")
            if not path:
                return
        n0 = len(self.model.items())
        self._checkpoint("import")
        warnings = self.model.import_file(path, self.project)
        self._changed("\n".join([f"imported {len(self.model.items()) - n0} items", *warnings]))

    # ---- panel ------------------------------------------------------------------------------------------------

    def _item_of(self, node_id):
        t = self.dock.tree
        stack = [t.topLevelItem(i) for i in range(t.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it.data(0, ROLE)[1] == node_id:
                return it
            stack += [it.child(i) for i in range(it.childCount())]
        return None

    def rebuild_tree(self):
        t = self.dock.tree
        t.blockSignals(True)
        selected = set(self.selected_ids())
        never = self.w.never_selected()
        locked = self.model.locked_labels(never)
        t.clear()

        def add(x, parent_item):
            label, giver = self.model.label_of(x.id)
            it = QTreeWidgetItem([x.name, label, x.describe()])
            it.setData(0, ROLE, (x.kind, x.id))
            if label and giver != x.id:  # inherited from a directory
                it.setForeground(1, Qt.GlobalColor.gray)
                it.setToolTip(1, f"from {self.model.node(giver).name}")
            if label in locked:
                it.setForeground(1, QColor("#c0392b"))
                it.setToolTip(1, ("from " + self.model.node(giver).name + "; " if giver != x.id else "")
                              + "locked: cannot be selected")  # fmt: skip
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
            c = QColor(self.model.color_of(x.id))
            c.setAlpha(110 if x.kind == "directory" else 60)
            it.setBackground(0, c)
            it.setForeground(2, Qt.GlobalColor.gray)
            if x.kind != "directory":
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
            if parent_item is None:
                t.addTopLevelItem(it)
            else:
                parent_item.addChild(it)
            if x.kind == "directory":
                for c_ in x.children:
                    add(c_, it)
                it.setExpanded(x.id not in self.collapsed)
            it.setSelected(x.id in selected)

        for x in self.model.root:
            add(x, None)
        t.blockSignals(False)
        self._fill_labels()
        self._refresh_ops()
        self.dock.locks.setText("locks" if self.model.locks_on else "locks (off)")
        act = getattr(self.w, "_locks_action", None)
        if act is not None and act.isChecked() != self.model.locks_on:
            act.blockSignals(True)
            act.setChecked(self.model.locks_on)
            act.blockSignals(False)

    def on_item_changed(self, item, col):
        x = self.model.node(item.data(0, ROLE)[1])
        if col == 1:  # the label typed in the tree
            label = item.text(1).strip()
            if label != x.label:
                self._checkpoint(f"label {label!r}" if label else "remove label")
                x.label = label
                self._changed()
            return
        name = item.text(0).strip()
        if col == 0 and name and name != x.name:
            self._checkpoint(f"rename {x.name}")
            x.name = name
            self.save()

    def on_double_click(self, item, col):
        x = self.model.node(item.data(0, ROLE)[1])
        if col == 1:
            self.dock.tree.editItem(item, 1)
        elif x.kind == "curve":
            self.edit(x)
        else:
            self.dock.tree.editItem(item, 0)

    def on_tree_selection(self):
        self.highlight = {m.id for m in self._selected_items()}
        self.refresh()

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
        for m in self.model.items():
            hl = m.id in self.highlight or m is self.editing
            col = rgba(self.model.color_of(m.id))
            width = 4.5 if hl else 2.5
            if m.kind == "segment":
                ends = np.array([m.a, m.b]) - off
                lines.append((ends, col, width))
                points.append((ends, col, 8.0))
            elif m.kind == "curve":
                lines.append((self._polyline(m) - off, col, width))
                if m is not self.editing:
                    points.append((m.nodes - off, col, 6.0))
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
            if self.active is not None and self.active < len(nodes):
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
            targets = {"nodes": nodes, "handles": handles, "plane": (c.normal, c.plane_point() - off)}
            if self.dock.show_picked.isChecked():  # where the points were clicked, and where they went
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
        self.vp.makeCurrent()
        try:
            r.set_vectors(lines, points)
        finally:
            self.vp.doneCurrent()
        self.vp.update()
