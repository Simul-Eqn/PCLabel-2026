"""Label menus that show the taxonomy as a tree (the user's request of 2026-11-04: "during label selection we can
show the hierarchy"): each class indented under its parent, then the flags, then other labels in use.

An entry's text is the label itself (the indent is drawn, not typed), so an editable box's text is always the label.
A class kept to lines or markers (taxonomy ``only``) is greyed out where the items it would go on are not of that kind.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QStyledItemDelegate, QStyleOptionViewItem

from pclabel.taxonomy import only_words

DEPTH = Qt.ItemDataRole.UserRole + 7  # an entry's depth in the tree
INDENT = 16  # pixels per level


class IndentDelegate(QStyledItemDelegate):
    """Draws each entry of a combo box's list indented by its depth."""

    def paint(self, painter, option, index):
        d = index.data(DEPTH) or 0
        if d:
            option = QStyleOptionViewItem(option)
            option.rect.adjust(INDENT * d, 0, 0, 0)
        super().paint(painter, option, index)

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        s.setWidth(s.width() + INDENT * (index.data(DEPTH) or 0))
        return s


HEADER = "not for new labels:"  # the heading above in-use labels the standard does not use (never a label)


def class_tip(taxonomy, c) -> str:
    """A class's tooltip: its definition, what items it goes on, and why not to use it for new labels."""
    only = c.get("only")
    note = taxonomy.not_for_new_labels(c["name"])
    return (f"{note.upper()}. " if note else "") + c.get("def", "") + (f" ({only_words(only)} only)" if only else "")


def taxonomy_entries(taxonomy, imports: bool = False) -> list:
    """[(label, depth, tooltip, only)] for the enabled classes in tree order, then the flags (depth 0). ``imports``:
    also the classes kept for imported labels (taxonomy status ``import``), for the DXF import's boxes."""
    out = []
    for c, depth in taxonomy.tree_order(enabled_only=True, also=("import",) if imports else ()):
        out.append((c["name"], depth, class_tip(taxonomy, c), c.get("only")))
    out += [(f["name"], 0, f"flag: {f.get('def', '')}", None) for f in taxonomy.flags]
    return out


def in_use_entries(taxonomy, labels) -> tuple:
    """Labels in use that the menus do not list, split into ([(label, 0, tooltip, only)] of taxonomy classes the
    standard does not use for new labels, [other labels: free text])."""
    old, free = [], []
    for name in labels:
        c = taxonomy.by_name(name)
        if c is None:
            free.append(name)
        else:
            old.append((name, 0, class_tip(taxonomy, c), c.get("only")))
    return old, free


def fill(box: QComboBox, entries, extra=(), kinds=None, blank: bool = False, old=()):
    """Fill a label box with ``entries`` ([(label, depth, tooltip, only)], e.g. ``taxonomy_entries``; plain strings
    are labels at depth 0), then the ``extra`` labels, then ``old`` (entries as ``entries``, e.g. in-use classes the
    standard no longer uses) in grey italics under the heading ``HEADER``, which cannot be chosen. With ``kinds``
    (item kinds), a class kept to lines or markers is greyed out unless it fits them all. ``blank``: an empty first
    entry (no label). The text is kept."""
    from PySide6.QtGui import QBrush, QColor, QFont

    from pclabel.taxonomy import fits

    if not isinstance(box.itemDelegate(), IndentDelegate):
        box.setItemDelegate(IndentDelegate(box))
    text = box.currentText()
    box.blockSignals(True)
    box.clear()
    if blank:
        box.addItem("")
    for e in entries:
        name, depth, tip, only = (e, 0, "", None) if isinstance(e, str) else e
        box.addItem(name)
        i = box.count() - 1
        box.setItemData(i, depth, DEPTH)
        if tip:
            box.setItemData(i, tip, Qt.ItemDataRole.ToolTipRole)
        if kinds and not all(fits(only, k) for k in kinds):
            item = box.model().item(i)
            if item is not None:
                item.setEnabled(False)
    for name in extra:
        box.addItem(name)
    if old:
        box.addItem(HEADER)
        head = box.model().item(box.count() - 1)
        if head is not None:
            head.setEnabled(False)
            head.setSelectable(False)
        italic = QFont()
        italic.setItalic(True)
        for name, _depth, tip, only in old:
            box.addItem(name)
            i = box.count() - 1
            box.setItemData(i, 1, DEPTH)
            box.setItemData(i, tip, Qt.ItemDataRole.ToolTipRole)
            box.setItemData(i, italic, Qt.ItemDataRole.FontRole)
            box.setItemData(i, QBrush(QColor("#8a8a8a")), Qt.ItemDataRole.ForegroundRole)
            if kinds and not all(fits(only, k) for k in kinds):
                item = box.model().item(i)
                if item is not None:
                    item.setEnabled(False)
    box.setCurrentText(text)
    box.blockSignals(False)
