"""The window's menus, keys and toolbar (docs/MANUAL.md)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QToolBar


def _action(w, menu, text, keys, slot, checkable=False):
    a = QAction(text, w)
    if keys:
        a.setShortcuts([QKeySequence(k) for k in (keys if isinstance(keys, (list, tuple)) else [keys])])
        a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
    a.setCheckable(checkable)
    a.triggered.connect(slot)
    menu.addAction(a)
    w.addAction(a)
    return a


def build_menus(w):
    m = w.menuBar().addMenu("&File")
    _action(w, m, "Open project...", "Ctrl+O", w.open_dialog)
    _action(w, m, "Import scan...", "Ctrl+I", w.import_dialog)
    _action(w, m, "Import labels (DXF)...", "Ctrl+Shift+I", lambda: w.label_import.start())
    m.addSeparator()
    _action(w, m, "Quit", "Ctrl+Q", w.close)
    m = w.menuBar().addMenu("&Edit")
    w._undo_action = _action(w, m, "Undo", "Ctrl+Z", w.undo)
    w._redo_action = _action(w, m, "Redo", ["Ctrl+Y", "Ctrl+Shift+Z"], w.redo)
    w._update_undo_actions()
    m = w.menuBar().addMenu("&View")
    _action(w, m, "Next colour mode", "C", lambda: w.cycle_mode(1))
    _action(w, m, "Previous colour mode", "Shift+C", lambda: w.cycle_mode(-1))
    _action(w, m, "Height wrap on/off", "W", w.toggle_wrap)
    _action(w, m, "Wrap period / 1.5", "[", lambda: w.scale_period(1 / 1.5))
    _action(w, m, "Wrap period x 1.5", "]", lambda: w.scale_period(1.5))
    _action(w, m, "Wrap offset down", ["Shift+[", "{"], lambda: w.shift_h0(-1))
    _action(w, m, "Wrap offset up", ["Shift+]", "}"], lambda: w.shift_h0(1))
    m.addSeparator()
    top = _action(w, m, "Top view (looking down; again: back)", "T", lambda: w.set_view("top"))
    bottom = _action(w, m, "Bottom view (looking up; again: back)", "Shift+T", lambda: w.set_view("bottom"))
    front = _action(w, m, "Front view (looking +y)", "F7", lambda: w.set_view("front"))
    side = _action(w, m, "Side view (looking -x)", "F8", lambda: w.set_view("side"))
    home = _action(w, m, "Reset view (home)", "Home", w.viewport.reset_view)
    ortho = _action(w, m, "Orthographic projection", None, w.viewport.set_orthographic, checkable=True)
    w._ortho_action = ortho
    m.addSeparator()
    _action(w, m, "Height axis: z / fitted plane", "H", w.toggle_frame)
    _action(w, m, "Height slice on/off", "Shift+H", lambda: w.frames_dock.slice_on.toggle())
    _action(w, m, "Clip box on/off", "K", lambda: w.clip.on.toggle())
    _action(w, m, "Clip box from view", "Shift+K", w.clip_from_view)
    _action(w, m, "Clip to the point sets selected in Annotations", "Ctrl+K", lambda: w.clip_to_selected_sets())
    _action(w, m, "Point set clip on/off", "Ctrl+Shift+K", w.toggle_point_set_clip)
    _action(w, m, "Eye-dome lighting on/off", "Ctrl+E", lambda: w.display.edl.toggle())
    m.addSeparator()
    _action(w, m, "Labels view on/off (points, segments and curves in their labels' colours)", "B",
                 lambda: w.labels.set_view())  # fmt: skip
    _action(w, m, "Label map: the labels projected onto a plane, as PNG and JSON...", "Shift+B",
                 lambda: w.labels.export_map())  # fmt: skip
    m = w.menuBar().addMenu("&Select")
    lasso = _action(w, m, "Lasso", "L", lambda: w.toggle_tool("lasso"))
    rect = _action(w, m, "Rectangle", "R", lambda: w.toggle_tool("rect"))
    w._through_action = _action(w, m, "Lasso and rectangle select hidden points too", "O",
                                        w.set_select_through, checkable=True)  # fmt: skip
    grow = _action(w, m, "Grow select (click a surface)", "G", lambda: w.toggle_tool("grow"))
    magic = _action(w, m, "Magic select by intensity (click a marking)", "I", lambda: w.toggle_tool("magic"))
    volume = _action(w, m, "3D select: box or cylinder (click to place, Enter selects)", "U",
                          lambda: w.toggle_tool("volume"))  # fmt: skip
    _action(w, m, "Keep a piece of the selection (click it; Shift adds, Ctrl takes away)", "P",
                 lambda: w.toggle_tool("piece"))  # fmt: skip
    _action(w, m, "Tree or pole: click the ground at its foot, 3D select a band of it, save it", "Shift+U",
                 lambda: w.trees.begin())  # fmt: skip
    _action(w, m, "Trace a curb: click the road beside it, then further along", "Ctrl+T",
                 lambda: w.trace.begin("curb"))  # fmt: skip
    _action(w, m, "Trace a road marking: click the paint, then further along", "Ctrl+Shift+T",
                 lambda: w.trace.begin("marking"))  # fmt: skip
    fit = _action(w, m, "Fit plane to selection (height axis)", "F", w.fit_plane)
    m.addSeparator()
    seg = _action(w, m, "Line segment (click two points)", "V", lambda: w.toggle_tool("segment"))
    curve = _action(w, m, "Curve (click points in order, Enter)", "Shift+V", lambda: w.toggle_tool("curve"))
    group = _action(w, m, "Point set from the selection (in the directory selected in Annotations)", "Ctrl+G",
                         lambda: w.annot.points_from_selection())  # fmt: skip
    _action(w, m, "Point set from the selection, at the top level", "Ctrl+Shift+G",
                 lambda: w.annot.points_from_selection(top_level=True))  # fmt: skip
    marker = _action(w, m, "Marker where the selection is (in the directory selected in Annotations)", "Ctrl+M",
                          lambda: w.annot.marker_from_selection())  # fmt: skip
    _action(w, m, "Marker where the selection is, at the top level", "Ctrl+Shift+M",
                 lambda: w.annot.marker_from_selection(top_level=True))  # fmt: skip
    _action(w, m, "Markers by clicking (each click places one; circle centre snaps to a pole)", "Shift+M",
                 lambda: w.toggle_tool("marker"))  # fmt: skip
    _action(w, m, "Rename the directory or item selected in Annotations", "F2", lambda: w.annot.rename_selected())
    _action(w, m, "Duplicate the directories and items selected in Annotations", "Ctrl+D",
                 lambda: w.annot.duplicate_selected())  # fmt: skip
    _action(w, m, "Clean up the selection (footprint)...", None, lambda: w.cleanup.open())
    _action(w, m, "Density: the parts of a point set dense enough to work on...", None,
                 lambda: w.density.start())  # fmt: skip
    w._locks_action = _action(w, m, "Locks on (locked labels cannot be selected)", None,
                                      lambda on: w.annot.set_locks_on(on), checkable=True)  # fmt: skip
    w._locks_action.setChecked(True)
    m.addSeparator()
    _action(w, m, "Clear selection / tool / pick marker", "Esc", w.escape)
    m = w.menuBar().addMenu("&Window")
    for d in (w.frames_dock, w.annot.dock, w.grow, w.magic, w.volume.dock, w.split.dock,
              w.cleanup.dock, w.density.dock, w.pieces.dock, w.labels.dock, w.trace.dock,
              w.display, w.info,
              w.clip,
              w.meta):
        m.addAction(d.toggleViewAction())

    tb = QToolBar("View", w)
    tb.setObjectName("toolbar")
    w.addToolBar(tb)
    for a, label in ((home, "Home"), (top, "Top (T)"), (bottom, "Bottom (Shift+T)"), (front, "Front (F7)"),
                     (side, "Side (F8)")):  # fmt: skip
        b = tb.addAction(label, a.trigger)
        b.setToolTip(a.text())
    tb.addSeparator()
    for a, label in ((lasso, "Lasso (L)"), (rect, "Rectangle (R)"), (grow, "Grow select (G)"),
                     (magic, "Magic select (I)"), (volume, "3D select (U)"), (fit, "Fit plane (F)"),
                     (seg, "Segment (V)"),
                     (curve, "Curve (Shift+V)"), (group, "Points (Ctrl+G)"), (marker, "Marker (Ctrl+M)")):  # fmt: skip
        b = tb.addAction(label, a.trigger)
        b.setToolTip(a.text())
    tb.addAction("Slice", lambda: w.frames_dock.slice_on.toggle())
