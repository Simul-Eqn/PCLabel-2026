"""Vector annotations in the app: the segment (V) and curve (Shift+V) tools, curve edit mode and the
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
- Markers (Ctrl+M, the user's request of 2026-11-04): a position from the selection, at the points' mean or at the
  centre of a circle fitted to them across the height axis (poles, trunks: the mean of a pole seen from one side sits
  toward the scanner), at their mean height or lowest point (tools/markers.py). Drawn as a dot with a short pin.
- The label box shows the taxonomy as a tree (each class indented under its parent); a class kept to lines or markers
  (``only``) is greyed out for other items, and refused on them.
"""

from pclabel.ui.annotate.controller import AnnotationController
from pclabel.ui.annotate.dock import rgba

__all__ = ["AnnotationController", "rgba"]
