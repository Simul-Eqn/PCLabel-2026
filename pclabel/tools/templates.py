"""Outline templates for covers and similar small objects (the user's request of 2026-11-05, after LiDAR360MLS's "Add
Manhole": place, rotate and resize a cover outline).

An outline is a closed ring in the plane across the height axis through a centre (a marker): a circle of
``diameter``, or a rectangle ``width`` x ``length`` turned ``rotation`` degrees from the frame's ``along`` direction.
The template's size is a starting point: check it against the scan (the outline is drawn over the points).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from pclabel.analysis.geometry import frame_axes

SHAPES = ("circle", "rectangle")


@dataclass(frozen=True)
class OutlineParams:
    shape: str = "circle"  # "circle" | "rectangle"
    diameter: float = 0.60  # m, circle
    width: float = 0.60  # m, rectangle, across ``along`` turned by ``rotation``
    length: float = 0.90  # m, rectangle, along it
    rotation: float = 0.0  # degrees about the height axis
    segments: int = 36  # vertices of a circle


def outline(centre, axis, p: OutlineParams = OutlineParams(), along=(1.0, 0.0, 0.0)) -> np.ndarray:
    """The ring's vertices (k, 3) around ``centre`` (the same coordinates as it), not closed (the polyline is)."""
    if p.shape not in SHAPES:
        raise ValueError(f"shape must be one of {SHAPES}")
    _, u, v = frame_axes(axis, along, p.rotation)
    c = np.asarray(centre, np.float64)
    if p.shape == "circle":
        t = np.arange(p.segments) * 2 * np.pi / p.segments
        r = p.diameter / 2
        return c + r * np.outer(np.cos(t), u) + r * np.outer(np.sin(t), v)
    hl, hw = p.length / 2, p.width / 2
    corners = [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw)]
    return np.array([c + a * u + b * v for a, b in corners])

