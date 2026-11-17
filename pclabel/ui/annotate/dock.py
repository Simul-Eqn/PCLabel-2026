"""The Annotations panel: its tree of directories and items, and its buttons and boxes."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from pclabel.store.annotations import SET_OPS, CURVE_MODES
from pclabel.taxonomy import hex_rgb
from pclabel.ui.docks import _spin


MARKER_ACROSS = {"mean": "mean of the points", "circle": "circle centre (poles, trunks)"}
MARKER_HEIGHTS = {"mean": "mean height", "lowest": "lowest point"}
PIN = 0.8  # m: a marker's pin, along the height axis, so it stands out among the points

ROLE = Qt.ItemDataRole.UserRole
PENDING = (1.0, 1.0, 1.0, 1.0)
HANDLE = (1.0, 0.85, 0.1, 1.0)
PICKED = (1.0, 0.2, 0.9, 1.0)  # clicked points, shown on request in curve edit mode


def rgba(hex_color: str, alpha: float = 1.0) -> tuple:
    return tuple(v / 255 for v in hex_rgb(hex_color)) + (alpha,)


class AnnotationTree(QTreeWidget):
    """Directories and items. A drop does not move items itself: it reports what was dropped where (a directory id,
    or None for the top level), and the tree is rebuilt from the model."""

    dropped = Signal(list, object)  # ids of the dragged nodes, id of the destination directory (None: top level)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["name", "label", "unsure", "contents"])
        self.setColumnWidth(0, 160)
        self.setColumnWidth(1, 90)
        self.setColumnWidth(2, 52)
        self.headerItem().setToolTip(2, "the item's label is uncertain (tick it here, or \"uncertain\" below); only "
                                        "point sets, segments and curves, not directories")  # fmt: skip
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
                      "as a point set, in the selected directory (or beside the selected item); Ctrl+Shift+G: at the "
                      "top level. Ctrl+M: a marker where the selection is. Drag onto a directory to put things in "
                      "it. F2 renames the selected entry, inside a directory too.")  # fmt: skip
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
        self.edit_plane = QLabel("plane:")
        pr.addWidget(self.edit_plane)
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
        self.uncertain = QPushButton("uncertain")
        self.uncertain.setToolTip("mark the labels of the selected point sets, segments and curves as uncertain (again: "
                                  "sure); directories cannot be; exported with the labels")  # fmt: skip
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
        lrow.addWidget(self.uncertain)
        lrow.addWidget(self.locks)
        v.addLayout(lrow)
        krow = QHBoxLayout()
        krow.addWidget(QLabel("marker at:"))
        self.marker_across = QComboBox()
        for k, words in MARKER_ACROSS.items():
            self.marker_across.addItem(words, k)
        self.marker_across.setToolTip("across the height axis: the selected points' mean, or the centre of a circle "
                                      "fitted to them (a band of a pole or a trunk: seen from one side, its points' "
                                      "mean sits toward the scanner)")  # fmt: skip
        self.marker_height = QComboBox()
        for k, words in MARKER_HEIGHTS.items():
            self.marker_height.addItem(words, k)
        self.marker_height.setToolTip("along the height axis: the points' mean height, or their lowest point (e.g. "
                                      "a selection that reaches the ground at a pole's foot)")  # fmt: skip
        self.marker = QPushButton("marker (Ctrl+M)")
        self.marker.setToolTip("a marker where the selection is: in the directory selected here, or beside the item "
                               "selected here (Ctrl+Shift+M: at the top level)")  # fmt: skip
        krow.addWidget(self.marker_across, 1)
        krow.addWidget(QLabel("at"))
        krow.addWidget(self.marker_height)
        krow.addWidget(self.marker)
        self.attributes = QPushButton("attributes...")
        self.attributes.setToolTip("the selected marker's fields, one \"key: value\" per line (e.g. asset_id: LP-0012); "
                                   "exported with it (GeoJSON, export labels)")  # fmt: skip
        krow.addWidget(self.attributes)
        v.addLayout(krow)
        crow = QHBoxLayout()
        self.click_markers = QPushButton("click to place markers (Shift+M)")
        self.click_markers.setCheckable(True)
        self.click_markers.setToolTip("each click on the cloud places a marker, by \"marker at\": circle centre snaps "
                                      "to a pole or trunk around the click, lowest point drops it to the ground")  # fmt: skip
        self.click_label = QCheckBox("label them")
        self.click_label.setChecked(True)
        self.click_label.setToolTip("give each clicked marker the label in the label box (if any)")
        crow.addWidget(self.click_markers, 1)
        crow.addWidget(self.click_label)
        v.addLayout(crow)
        hrow = QHBoxLayout()
        hrow.addWidget(QLabel("move selected markers up by:"))
        self.marker_dz = _spin(-100.0, 100.0, 0.10, 0.01, 3, " m")
        self.marker_dz.setToolTip("along the active height axis; negative moves them down")
        self.marker_move = QPushButton("move")
        self.marker_move.setToolTip("move the selected markers (and those in selected directories) by this much")
        hrow.addWidget(self.marker_dz, 1)
        hrow.addWidget(self.marker_move)
        self.marker_axis = QPushButton("use the active height axis")
        self.marker_axis.setToolTip("make the active height axis (z or a fitted plane) the selected markers' own: their "
                                    "pin and \"move\" follow it; their positions stay where they are")  # fmt: skip
        hrow.addWidget(self.marker_axis)
        v.addLayout(hrow)
        grid = QGridLayout()
        self.new_dir = QPushButton("new directory")
        self.new_dir.setToolTip("at the top level, or inside the selected directory")
        self.points = QPushButton("points (Ctrl+G)")
        self.points.setToolTip("make the current selection a point set: inside the directory selected here, or beside "
                               "the item selected here; at the top level if nothing is (Ctrl+Shift+G: always at the top "
                               "level)")  # fmt: skip
        self.color = QPushButton("colour")
        self.color.setToolTip("the colour of the selected directory (everything in it) or top-level item")
        self.select = QPushButton("select points")
        self.select.setToolTip("make the point sets among the selected items (and in selected directories) the selection")
        self.edit = QPushButton("edit line")
        self.edit.setToolTip("edit the selected curve or polyline (or double-click it): drag its points, Shift+click "
                             "the cloud to add one, Ctrl+click one to remove it")  # fmt: skip
        self.to_curves = QPushButton("to curves")
        self.to_curves.setToolTip("the selected polylines (and those in selected directories) become curves with no "
                                  "plane, through as few of their vertices as keep within 1 cm (the Trace panel's "
                                  "\"within\"); undoable")  # fmt: skip
        self.smooth = QPushButton("smooth")
        self.smooth.setToolTip("smooth the selected curves and polylines (and those in selected directories): each "
                               "point averaged along the line over the distance below, moved at most the limit; an "
                               "open line's ends stay; undoable, and again to smooth more")  # fmt: skip
        self.smooth_scale = _spin(0.05, 20.0, 1.0, 0.1, 2, " m")
        self.smooth_scale.setToolTip("smooth over: the length along the line points are averaged over (sigma); longer "
                                     "takes out longer waves")  # fmt: skip
        self.smooth_max = _spin(0.001, 1.0, 0.05, 0.005, 3, " m")
        self.smooth_max.setToolTip("no point moves further than this (a real corner is rounded by no more)")
        self.to_polylines = QPushButton("to polylines")
        self.to_polylines.setToolTip("the selected curves (and those in selected directories) become polylines whose "
                                     "straight pieces stay within 2 mm of the curve; undoable")  # fmt: skip
        self.delete = QPushButton("delete")
        self.rename = QPushButton("rename (F2)")
        self.rename.setToolTip("type a new name for the selected directory, point set, segment or curve (wherever it is)")
        self.duplicate = QPushButton("duplicate (Ctrl+D)")
        self.duplicate.setToolTip("copy the selected directories (with everything in them) and items; each copy goes "
                                  "right after its original, named \"... copy\"")  # fmt: skip
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
        self.density = QPushButton("density...")
        self.density.setToolTip("the parts of the selected point set dense enough to work on, e.g. to magic select "
                                "road lines (the Density panel)")  # fmt: skip
        self.clip_to = QPushButton("clip to")
        self.clip_to.setToolTip("show and work on only the selected point sets (and those in selected directories); "
                                "Ctrl+Shift+K turns it off and on")  # fmt: skip
        for i, b in enumerate((self.new_dir, self.points, self.color, self.select, self.edit, self.delete,
                               self.rename, self.duplicate, self.export_sel, self.export_all, self.import_, self.export_labels,
                               self.join, self.split, self.set_ops, self.clip_to, self.density, self.to_curves,
                               self.to_polylines)):  # fmt: skip
            grid.addWidget(b, i // 4, i % 4)
        v.addLayout(grid)
        srow = QHBoxLayout()
        srow.addWidget(self.smooth)
        srow.addWidget(QLabel("over"))
        srow.addWidget(self.smooth_scale)
        srow.addWidget(QLabel("moving ≤"))
        srow.addWidget(self.smooth_max)
        srow.addStretch(1)
        v.addLayout(srow)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.status)
        self.setWidget(w)


MODE_NAMES = {"ends": "through the ends", "axis": "flat on the height axis", "fit": "fit all points",
              "free": "no plane (3D)"}


def _mode_combo() -> QComboBox:
    c = QComboBox()
    for mode, text in CURVE_MODES.items():
        c.addItem(MODE_NAMES[mode], mode)
        c.setItemData(c.count() - 1, f"the plane goes {text}", Qt.ItemDataRole.ToolTipRole)
    return c
