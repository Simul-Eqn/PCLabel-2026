"""Cover outline templates (tools/templates.py) and a marker's attributes kept by the annotations."""

import numpy as np
import pytest

from pclabel.store.annotations import Annotations, Marker
from pclabel.tools.templates import OutlineParams, outline


def test_circle_and_rectangle_across_the_axis():
    c = np.array([10.0, 20.0, 1.0])
    ring = outline(c, (0, 0, 1), OutlineParams(diameter=0.6))
    assert ring.shape == (36, 3) and np.allclose(np.linalg.norm(ring - c, axis=1), 0.3) and np.allclose(ring[:, 2], 1.0)
    rect = outline(c, (0, 0, 1), OutlineParams(shape="rectangle", width=0.4, length=1.0, rotation=90))
    assert rect.shape == (4, 3)
    assert np.ptp(rect[:, 1]) == pytest.approx(1.0) and np.ptp(rect[:, 0]) == pytest.approx(0.4)  # turned 90 degrees
    t = np.radians(20)
    tilted = outline(c, (np.sin(t), 0, np.cos(t)), OutlineParams())
    assert np.allclose((tilted - c) @ np.array([np.sin(t), 0, np.cos(t)]), 0)  # in the plane across that axis
    with pytest.raises(ValueError, match="shape"):
        outline(c, (0, 0, 1), OutlineParams(shape="oval"))


def test_marker_attributes_round_trip():
    from types import SimpleNamespace

    t = Annotations()
    mk = t.add(Marker(np.array([1.0, 2.0, 3.0]), {"tool": "marker", "across": "mean", "points": 9}))
    mk.attributes = {"asset_id": "MH-0007", "type": "sewer"}
    assert mk.describe() == "marker (MH-0007), mean of 9 points"
    proj = SimpleNamespace(meta={"source": {"path": "a.e57", "sha256": "x"}}, n=0, offset=np.zeros(3))
    doc = t.to_doc(proj, points="inline")
    assert doc["schema"] == "pclabel.annotations/9" and doc["tree"][0]["attributes"] == mk.attributes
    back, _ = Annotations.from_doc(doc, proj)
    assert back.root[0].attributes == {"asset_id": "MH-0007", "type": "sewer"}
    from pclabel.store.annotations import clone_nodes

    copy = clone_nodes(t.root)[0]
    copy.attributes["type"] = "drain"
    assert mk.attributes["type"] == "sewer"  # an undo snapshot does not share them
