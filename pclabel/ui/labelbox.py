"""Label menus that show the taxonomy as a tree (the user's request of 2026-11-04: "during label selection we can
show the hierarchy"): each class indented under its parent, then the flags, then other labels in use.

An entry's text is the label itself (the indent is drawn, not typed), so an editable box's text is always the label.
A class kept to lines or markers (taxonomy ``only``) is greyed out where the items it would go on are not of that kind.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QStyledItemDelegate, QStyleOptionViewItem

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


def taxonomy_entries(taxonomy) -> list:
    """[(label, depth, tooltip, only)] for the enabled classes in tree order, then the flags (depth 0)."""
    out = []
    for c, depth in taxonomy.tree_order(enabled_only=True):
        only = c.get("only")
        tip = c.get("def", "") + (f" ({only}s only)" if only else "")
        out.append((c["name"], depth, tip, only))
    out += [(f["name"], 0, f"flag: {f.get('def', '')}", None) for f in taxonomy.flags]
    return out


def fill(box: QComboBox, entries, extra=(), kinds=None, blank: bool = False):
    """Fill a label box with ``entries`` ([(label, depth, tooltip, only)], e.g. ``taxonomy_entries``; plain strings
    are labels at depth 0), then the ``extra`` labels. With ``kinds`` (item kinds), a class kept to lines or markers
    is greyed out unless it fits them all. ``blank``: an empty first entry (no label). The text is kept."""
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
    box.setCurrentText(text)
    box.blockSignals(False)
