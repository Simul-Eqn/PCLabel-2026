"""What is done to the directories and items selected in the tree: new point sets and directories, labels,
locks, uncertain, smoothing and converting lines, colour, delete, rename, duplicate, export and import."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QFileDialog,
    QMessageBox,
)

from pclabel.store.annotations import PointSet
from pclabel.tools import sets as sets_
from pclabel.ui import labelbox
from pclabel.ui.annotate.dock import ROLE


class ItemsMixin:
    """Part of AnnotationController."""

    # ---- the tree -----------------------------------------------------------------------------------------------

    def points_from_selection(self, top_level: bool = False):
        """The selection as a new point set (Ctrl+G): inside the directory selected in the tree, or in the directory of
        the one item selected there; at the top level when nothing (or more than one thing) is selected, or with
        ``top_level`` (Ctrl+Shift+G)."""
        if self.project is None:
            return
        sel = self.w.selection
        if sel.size == 0:
            self.dock.status.setText("nothing selected")
            return
        into = None if top_level else self.directory_for_new()
        self.checkpoint("new point set")
        ps = self.model.add(PointSet(sel.copy(), [dict(op) for op in self.w.selection_ops]))
        if into is not None:
            self.model.move([ps.id], into.id)
        self.changed(f"{ps.name}: {sel.size:,} points" + (f", in {into.name}" if into is not None else ", at the top level"))
        self.dock.show()
        self.dock.raise_()

    def _label_fits(self, label: str, nodes) -> str | None:
        """Why ``label`` cannot go on these nodes (a class kept to lines or markers), or None."""
        tax = self.w.taxonomy
        bad = [n for n in nodes if not tax.fits(label, n.kind)]
        if not bad:
            return None
        names = ", ".join(n.name for n in bad[:3]) + (f" and {len(bad) - 3} more" if len(bad) > 3 else "")
        from pclabel.taxonomy import only_words

        return f"{label!r} is for {only_words(tax.only(label))} only, not {names}"

    def directory_for_new(self):
        """Where a new point set goes: the one directory selected in the tree, or the directory of the one item
        selected (None: the top level)."""
        ids = self.selected_ids()
        if len(ids) != 1:
            return None
        x = self.model.node(ids[0])
        return x if x.kind == "directory" else self.model.parent(x.id)

    def selected_ids(self) -> list:
        return [it.data(0, ROLE)[1] for it in self.dock.tree.selectedItems()]

    def new_directory(self, name: str | None = None):
        sel = [self.model.node(i) for i in self.selected_ids()]
        parent = sel[0].id if len(sel) == 1 and sel[0].kind == "directory" else None
        self.checkpoint("new directory")
        d = self.model.new_directory(name, parent)
        self.changed(f"{d.name} created")
        item = self._item_of(d.id)
        if item is not None and name is None:
            self.dock.tree.editItem(item, 0)  # type its name
        return d

    def on_drop(self, ids, dest):
        if ids:
            self.move(ids, dest)

    def move(self, ids, dest):
        self.checkpoint(f"move {len(ids)} entr{'y' if len(ids) == 1 else 'ies'}")
        self.model.move(ids, dest)
        self.changed()

    def selected_items(self) -> list:
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
        if label == labelbox.HEADER:
            return
        why = self._label_fits(label, [self.model.node(i) for i in ids]) if label else None
        if why:
            self.dock.status.setText(why)
            return
        self.checkpoint(f"label {label!r}" if label else "remove label")
        for i in ids:
            self.model.node(i).label = label
        self.changed(f"{len(ids)} entr{'y' if len(ids) == 1 else 'ies'} labelled {label!r}" if label else
                      f"label removed from {len(ids)} entr{'y' if len(ids) == 1 else 'ies'}")  # fmt: skip
        note = self.w.taxonomy.not_for_new_labels(label) if label else None
        if note:
            self.dock.status.setText(self.dock.status.text() + f"\nnote: {label} is {note} (docs/LABELLING_STANDARD.md)")

    def _labels_in_order(self, labels) -> list:
        order = {c["name"]: i for i, c in enumerate(self.w.taxonomy.classes)}  # the taxonomy's tree order
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
        self.changed(f"{label!r} {'locked: its points cannot be selected' if on else 'unlocked'}")

    def set_locks_on(self, on: bool):
        """All locks on or off (the choice of labels is kept)."""
        self.model.locks_on = bool(on)
        self.changed("locks on: locked labels cannot be selected" if on else "locks off: everything can be selected")

    def set_uncertain(self, on: bool | None = None):
        """Mark the selected items uncertain (None: uncertain, or sure if all already are). Directories cannot be."""
        picked = [self.model.node(i) for i in self.selected_ids()]
        nodes = [n for n in picked if n.kind != "directory"]
        if not nodes:
            self.dock.status.setText("select point sets, segments or curves: directories cannot be uncertain"
                                     if picked else "select point sets, segments or curves first")  # fmt: skip
            return
        on = not all(n.uncertain for n in nodes) if on is None else bool(on)
        self.checkpoint("uncertain" if on else "sure")
        for n in nodes:
            n.uncertain = on
        names = ", ".join(n.name for n in nodes[:3]) + (f" and {len(nodes) - 3} more" if len(nodes) > 3 else "")
        self.changed(f"{names}: label {'uncertain' if on else 'sure'}")

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
        flags = {f["name"]: int(f["bit"]) for f in self.w.taxonomy.flags}
        summary = self.model.export_labels(path, self.project, self._class_ids(), flags, self.w.taxonomy.parents())
        parts = [f"{k or 'unlabelled'}: {n} item{'s' * (n != 1)}" + (f", {pts:,} points" if pts else "")
                 for k, (n, pts) in sorted(summary.items(), key=lambda kv: (kv[0] == "", kv[0]))]  # fmt: skip
        self.dock.status.setText(f"exported labels to {path}\n" + "; ".join(parts))
        return path

    def _fill_labels(self):
        """The label box: the enabled taxonomy classes as a tree (each indented under its parent; one kept to lines or
        markers greyed out unless the entries selected in the tree are such items), then the flags (e.g. ``marking``:
        paint off the road keeps the class of the surface it is on), then other labels in use."""
        entries = labelbox.taxonomy_entries(self.w.taxonomy)
        known = {e[0] for e in entries}
        in_use = sorted({x.label for x, _ in self.model.walk() if x.label} - known)
        old, free = labelbox.in_use_entries(self.w.taxonomy, in_use)  # old: classes not for new labels, greyed
        kinds = set()
        for i in self.selected_ids():
            try:
                kinds.add(self.model.node(i).kind)
            except KeyError:
                pass
        labelbox.fill(self.dock.label, entries, free, kinds or None, old=old)

    def select_points(self):
        sets = [m for m in self.selected_items() if m.kind == "points"]
        if not sets:
            self.dock.status.setText("no point set among the selected items")
            return
        idx = sets_.union_all([m.indices for m in sets])  # sorted already: a merge, not a sort (24 s -> < 1 s)
        self.w.set_selection(idx, {"tool": "annotations", "op": "replace", "point_sets": [m.name for m in sets]})

    def edit_selected(self):
        lines_ = [m for m in self.selected_items() if m.kind == "curve" or (m.kind == "polyline" and len(m.vertices) > 1)]
        if lines_:
            self.edit(lines_[0])

    def smooth_selected(self) -> list:
        """Smooth the selected curves and polylines (Annotations.smooth with the panel's settings); one undo step;
        curve edit mode carries on with the smoothed line."""
        items = [x for x in self.selected_items() if x.kind == "curve" or (x.kind == "polyline" and len(x.vertices) > 1)]
        if not items and self.editing is not None:
            items = [self.editing]
        if not items:
            self.dock.status.setText("select the curves or polylines to smooth (or their directories) in Annotations")
            return []
        scale, mx = self.dock.smooth_scale.value(), self.dock.smooth_max.value()
        tr = getattr(self.w, "trace", None)
        tol = tr.dock.fit_tol.value() if tr is not None else 0.01
        self.checkpoint(f"smooth {len(items)} line{'s' * (len(items) != 1)}")
        out = [self.model.smooth(x.id, scale, mx, tol) for x in items]
        for x in items:
            self._lines.pop(x.id, None)
        if self.editing is not None:
            for y in out:
                if y.id == self.editing.id:
                    self.editing, self.active = y, None
        recs = [y.source["smoothed"][-1] for y in out]
        w0, w1 = sum(r["wiggle_deg"][0] for r in recs), sum(r["wiggle_deg"][1] for r in recs)
        self.changed(f"{len(out)} line{'s' * (len(out) != 1)} smoothed over {scale:.2f} m: turning {w0:.0f}° -> "
                      f"{w1:.0f}°, points moved up to {max(r['largest_shift'] for r in recs) * 100:.1f} cm, "
                      f"{sum(r['points'] for r in recs)} points -> {sum(len(y.nodes) for y in out)}")  # fmt: skip
        return out

    def convert_selected(self, to: str, tolerance: float | None = None) -> list:
        """The selected polylines to curves (no plane; within the Trace panel's "within", 1 cm) or the selected
        curves to polylines (within 2 mm), each in its place; one undo step."""
        frm = "polyline" if to == "curve" else "curve"
        items = [x for x in self.selected_items() if x.kind == frm and (frm == "curve" or len(x.vertices) > 1)]
        if not items:
            self.dock.status.setText(f"no {frm}s among the selected items (or in selected directories)")
            return []
        if tolerance is None:
            tr = getattr(self.w, "trace", None)
            tolerance = tr.dock.fit_tol.value() if to == "curve" and tr is not None else 0.01 if to == "curve" else 0.002
        if self.editing is not None and self.editing.id in {x.id for x in items}:
            self.done()
        f = self._frame()
        self.checkpoint(f"{len(items)} {frm}s to {to}s")
        out = [self.model.convert(x.id, to, tolerance, f.normal, f.origin, f.name) for x in items]
        for x in items:
            self._lines.pop(x.id, None)
        n_before = sum(len(x.vertices) if frm == "polyline" else len(x.nodes) for x in items)
        n_after = sum(len(y.nodes) if to == "curve" else len(y.vertices) for y in out)
        self.changed(f"{len(out)} {frm}{'s' * (len(out) != 1)} to {to}s within {tolerance * 100:.1f} cm: "
                      f"{n_before} points became {n_after}")  # fmt: skip
        return out

    def set_color(self, color: str | None = None):
        ids = self.selected_ids()
        if not ids:
            return
        if color is None:
            c = QColorDialog.getColor(QColor(self.model.color_of(ids[0])), self.w, "Colour")
            if not c.isValid():
                return
            color = c.name()
        self.checkpoint("colour")
        inside = 0
        for i in ids:
            x = self.model.node(i)
            x.color = color
            inside += x.kind != "directory" and self.model.parent(i) is not None
        self.changed(f"{inside} item(s) inside a directory keep its colour" if inside else None)

    def delete_selected(self, confirm: bool = True):
        ids = self.selected_ids()
        if not ids:
            return
        n_items = len(self.selected_items())
        if confirm and QMessageBox.question(
                self.w, "Delete", f"Delete {len(ids)} selected entr{'y' if len(ids) == 1 else 'ies'} "
                f"({n_items} item{'s' * (n_items != 1)} in all)?") != QMessageBox.StandardButton.Yes:  # fmt: skip
            return
        self.checkpoint(f"delete {len(ids)} entr{'y' if len(ids) == 1 else 'ies'}")
        for i in ids:
            try:
                self.model.remove(i)
            except (KeyError, ValueError):
                pass  # already gone with its directory
        if self.editing is not None and self.editing.id not in {m.id for m in self.model.items()}:
            self.done()
        self.changed()

    def rename_selected(self):
        """Type a new name for the one entry selected in the tree (F2, from anywhere in the window): a directory or an
        item, at the top level or inside directories. Always the name column, whichever column was clicked."""
        ids = self.selected_ids()
        if len(ids) != 1:
            self.dock.status.setText("select one directory or item to rename")
            return
        self.dock.show()
        self.dock.raise_()  # it may be a tab behind another panel: edit once it is laid out

        def start():
            item = self._item_of(ids[0])
            if item is None:
                return
            t = self.dock.tree
            t.setCurrentItem(item, 0)
            t.scrollToItem(item)
            t.setFocus()
            t.editItem(item, 0)

        QTimer.singleShot(0, start)

    def duplicate_selected(self):
        """Copy the directories and items selected in the tree (Ctrl+D); the copies are selected afterwards."""
        ids = self.selected_ids()
        if not ids:
            self.dock.status.setText("select directories or items to duplicate first")
            return
        self.checkpoint(f"duplicate {len(ids)} entr{'y' if len(ids) == 1 else 'ies'}")
        copies = self.model.duplicate(ids)
        self.dock.tree.clearSelection()  # the copies are selected instead, e.g. to drag them elsewhere
        self.changed(", ".join(c.name for c in copies[:3]) + (f" and {len(copies) - 3} more" if len(copies) > 3 else ""))
        for c in copies:
            item = self._item_of(c.id)
            if item is not None:
                item.setSelected(True)
        return copies

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
        self.checkpoint("import")
        warnings = self.model.import_file(path, self.project)
        self.changed("\n".join([f"imported {len(self.model.items()) - n0} items", *warnings]))
