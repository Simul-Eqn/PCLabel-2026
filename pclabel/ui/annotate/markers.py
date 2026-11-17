"""Markers: from the selection (Ctrl+M), by clicking (Shift+M), moved, put on a height axis, and their
attributes."""

from __future__ import annotations

import numpy as np

from pclabel.store.annotations import Marker
from pclabel.tools.markers import MarkerParams, marker_at_click, place_marker
from pclabel.ui.annotate.dock import MARKER_ACROSS, MARKER_HEIGHTS


class MarkersMixin:
    """Part of AnnotationController."""

    def marker_from_selection(self, top_level: bool = False, across: str | None = None, height: str | None = None):
        """A marker where the selection is (Ctrl+M): across the height axis at the points' mean or at the centre of
        a circle fitted to them, along it at their mean height or lowest point (the panel's choices, or ``across`` and
        ``height``); in the directory selected in the tree, like Ctrl+G (at the top level with ``top_level``)."""
        if self.project is None:
            return None
        sel = self.w.selection
        if sel.size == 0:
            self.dock.status.setText("nothing selected: select the points to place a marker at (e.g. a band of a pole)")
            return None
        d = self.dock
        params = MarkerParams(across or d.marker_across.currentData(), height or d.marker_height.currentData())
        f = self._frame()
        try:
            res = place_marker(self.project.xyz, sel, f.normal, params, f.along)
        except ValueError as e:
            self.dock.status.setText(f"no marker: {e}")
            return None
        rec = dict(res["record"], height_axis=f.name, axis=[float(v) for v in f.normal],
                   selection=[dict(op) for op in self.w.selection_ops])  # fmt: skip
        into = None if top_level else self.directory_for_new()
        self.checkpoint("new marker")
        mk = self.model.add(Marker(self._world(res["position"]), rec))
        if into is not None:
            self.model.move([mk.id], into.id)
        x, y, z = mk.position
        text = (f"{mk.name}: {MARKER_ACROSS[params.across]} of {sel.size:,} points, at their "
                f"{MARKER_HEIGHTS[params.height]}: ({x:.3f}, {y:.3f}, {z:.3f})")  # fmt: skip
        if params.across == "circle":
            text += (f"; radius {rec['radius']:.3f} m, fit {rec['rms'] * 100:.1f} cm rms, {rec['coverage']:.0%} of the "
                     f"circumference; the plain mean was {rec['mean_off_centre'] * 100:.1f} cm off its centre")  # fmt: skip
            if rec["points_dropped"]:
                text += f"; {rec['points_dropped']:,} points off the circle left out"
        self.changed(text + (f", in {into.name}" if into is not None else ", at the top level"))
        self.dock.show()
        self.dock.raise_()
        return mk

    def _marker_axis(self, m) -> tuple:
        """(unit normal, name) of a marker's own height axis (``source.axis``), else the active one."""
        ax = m.source.get("axis")
        if ax is not None and np.linalg.norm(ax) > 0:
            a = np.asarray(ax, np.float64)
            return a / np.linalg.norm(a), m.source.get("height_axis", "its axis")
        f = self._frame()
        return np.asarray(f.normal, np.float64), f.name

    def edit_attributes(self, text: str | None = None) -> dict | None:
        """The selected marker's attributes, edited as "key: value" lines (``text``, or typed in a dialog); an empty
        value removes its key. One undo step."""
        mks = [self.model.node(i) for i in self.selected_ids()]
        mks = [m for m in mks if m.kind == "marker"]
        if len(mks) != 1:
            self.dock.status.setText("select one marker to edit its attributes")
            return None
        mk = mks[0]
        if text is None:
            from PySide6.QtWidgets import QInputDialog

            now = "\n".join(f"{k}: {v}" for k, v in mk.attributes.items()) or "asset_id: "
            text, ok = QInputDialog.getMultiLineText(self.w, f"Attributes of {mk.name}",
                                                     "one \"key: value\" per line (an empty value removes it)", now)  # fmt: skip
            if not ok:
                return None
        attrs = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            k, v = (s.strip() for s in line.split(":", 1))
            if k and v:
                attrs[k] = v
        self.checkpoint(f"attributes of {mk.name}")
        mk.attributes = attrs
        self.changed(f"{mk.name}: " + (", ".join(f"{k} {v}" for k, v in attrs.items()) or "no attributes"))
        return attrs

    def move_markers(self, dh: float | None = None) -> int:
        """Move the selected markers (and those in selected directories) by ``dh`` metres (None: the panel's value;
        negative: down) along each marker's own height axis (the active one for a marker without one, e.g. imported).
        One undo step; each marker's source records the move."""
        mks = [m for m in self.selected_items() if m.kind == "marker"]
        if not mks:
            self.dock.status.setText("select markers (or directories holding them) to move first")
            return 0
        dh = float(self.dock.marker_dz.value() if dh is None else dh)
        self.checkpoint(f"move {len(mks)} marker{'s' * (len(mks) != 1)}")
        names = set()
        for m in mks:
            n, name = self._marker_axis(m)
            names.add(name)
            m.position = np.asarray(m.position, np.float64) + dh * n
            m.source = dict(m.source, moved=[*m.source.get("moved", []), {"along": name, "by": dh}])
        self.changed(f"{len(mks)} marker{'s' * (len(mks) != 1)} moved {dh:+.3f} m along {', '.join(sorted(names))}")
        return len(mks)

    def set_marker_axis(self) -> int:
        """Make the active height axis the selected markers' own (``source.height_axis`` and ``axis``); their
        positions stay. One undo step; each change is added to ``source.axis_changes``."""
        mks = [m for m in self.selected_items() if m.kind == "marker"]
        if not mks:
            self.dock.status.setText("select markers (or directories holding them) first")
            return 0
        f = self._frame()
        n = [float(v) for v in f.normal]
        self.checkpoint(f"{len(mks)} marker{'s' * (len(mks) != 1)} on {f.name}")
        for m in mks:
            old = m.source.get("height_axis")
            m.source = dict(m.source, height_axis=f.name, axis=n,
                            axis_changes=[*m.source.get("axis_changes", []), {"from": old, "to": f.name}])  # fmt: skip
        self.changed(f"{len(mks)} marker{'s' * (len(mks) != 1)} now on the height axis {f.name} (positions kept)")
        return len(mks)

    def click_marker(self, p_local):
        """The marker tool (Shift+M): a marker where the cloud was clicked, by the panel's "marker at" (circle centre:
        snapped to the pole or trunk around the click; lowest point: dropped to the ground), labelled with the label
        box's label if "label them" is ticked; in the directory selected in the tree. One undo step per click."""
        if self.project is None:
            return None
        d = self.dock
        f = self._frame()
        params = MarkerParams(d.marker_across.currentData(), d.marker_height.currentData())
        filters = self.w.filters() if self.vp.renderer is not None else None
        res = marker_at_click(self.project.xyz, self.project.tiles, p_local, f.normal, params, f.along, filters)
        rec = dict(res["record"], click=self._world(p_local).tolist(), height_axis=f.name,
                   axis=[float(v) for v in f.normal])  # fmt: skip
        label = d.label.currentText().strip() if d.click_label.isChecked() else ""
        if label and not self.w.taxonomy.fits(label, "marker"):
            label = ""  # e.g. a line-only label in the box
        into = self.directory_for_new()
        self.checkpoint("new marker (click)")
        mk = self.model.add(Marker(self._world(res["position"]), rec))
        mk.label = label
        if into is not None:
            self.model.move([mk.id], into.id)
        x, y, z = mk.position
        how = "on the circle around the click" if rec["across"] == "circle" else "at the click"
        text = f"{mk.name} {how}" + (", on the ground" if rec["height"] == "lowest" else "") + f": ({x:.3f}, {y:.3f}, {z:.3f})"
        if rec["across"] == "circle":
            text += f", radius {rec['radius']:.3f} m, {rec['coverage']:.0%} of the circumference"
        text += (f", labelled {label}" if label else "") + (f"; {rec['note']}" if rec.get("note") else "")
        self.changed(text + (f", in {into.name}" if into is not None else ""))
        return mk
