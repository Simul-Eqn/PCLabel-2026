"""The panel's tree: rebuilt from the model after every change; names, labels and the uncertain box edited in it."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QTreeWidgetItem,
)

from pclabel.ui.annotate.dock import ROLE


class TreeMixin:
    """Part of AnnotationController."""

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
            it = QTreeWidgetItem([x.name, label, "", x.describe()])
            it.setData(0, ROLE, (x.kind, x.id))
            if label and giver != x.id:  # inherited from a directory
                it.setForeground(1, Qt.GlobalColor.gray)
                it.setToolTip(1, f"from {self.model.node(giver).name}")
            if label in locked:
                it.setForeground(1, QColor("#c0392b"))
                it.setToolTip(1, ("from " + self.model.node(giver).name + "; " if giver != x.id else "")
                              + "locked: cannot be selected")  # fmt: skip
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
            if x.kind != "directory":  # only items can be uncertain
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(2, Qt.CheckState.Checked if x.uncertain else Qt.CheckState.Unchecked)
            else:
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)  # Qt's default has it: no box at all
            if x.kind != "directory" and x.uncertain:
                f = it.font(1)
                f.setItalic(True)
                it.setFont(1, f)
                it.setToolTip(1, (it.toolTip(1) + "; " if it.toolTip(1) else "") + "uncertain")
            c = QColor(self.model.color_of(x.id))
            c.setAlpha(110 if x.kind == "directory" else 60)
            it.setBackground(0, c)
            it.setForeground(3, Qt.GlobalColor.gray)
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
        if col == 2:  # the uncertain box (items only)
            if x.kind == "directory":
                return
            on = item.checkState(2) == Qt.CheckState.Checked
            if on != x.uncertain:
                self.checkpoint(f"{x.name}: {'uncertain' if on else 'sure'}")
                x.uncertain = on
                # rebuilt once Qt is done with this item (rebuilding now would delete it under the signal)
                QTimer.singleShot(0, lambda: self.changed(f"{x.name}: label {'uncertain' if on else 'sure'}"))
            return
        if col == 1:  # the label typed in the tree
            label = item.text(1).strip()
            why = self._label_fits(label, [x]) if label else None
            if why:
                self.dock.status.setText(why)
                QTimer.singleShot(0, self.rebuild_tree)  # put the label shown back
                return
            if label != x.label:
                self.checkpoint(f"label {label!r}" if label else "remove label")
                x.label = label
                QTimer.singleShot(0, self._changed)  # rebuilding the tree now would delete the item under the editor
            return
        name = item.text(0).strip()
        if col == 0 and name and name != x.name:
            self.checkpoint(f"rename {x.name}")
            x.name = name
            self.save()

    def on_double_click(self, item, col):
        x = self.model.node(item.data(0, ROLE)[1])
        if col == 1:
            self.dock.tree.editItem(item, 1)
        elif x.kind == "curve" or (x.kind == "polyline" and len(x.vertices) > 1):
            self.edit(x)
        else:
            self.dock.tree.editItem(item, 0)

    def on_tree_selection(self):
        self.highlight = {m.id for m in self.selected_items()}
        self._fill_labels()
        self.refresh()
