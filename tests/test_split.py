"""Splitting a point set by roughness, with the clean-up and the points to look at."""

from dataclasses import replace

import numpy as np
import pytest

from pclabel.store.annotations import Annotations, PointSet
from pclabel.tools.split import SplitParams, set_features, split_labels


@pytest.fixture(scope="module")
def patch():
    """A flat 6 x 4 m plane (smooth) with a rough strip (vegetation-like, +-4 cm) along one side and three small rough
    specks (10 cm) inside the plane."""
    rng = np.random.default_rng(0)
    n = 400_000
    xy = rng.uniform([0, 0], [6, 4], (n, 2))
    z = rng.normal(0, 0.001, n)
    strip = xy[:, 1] > 3.0
    z[strip] += rng.uniform(-0.04, 0.04, strip.sum())
    specks = np.zeros(n, bool)
    for cx, cy in ((1.0, 1.0), (3.0, 1.5), (4.5, 0.8)):
        specks |= np.hypot(xy[:, 0] - cx, xy[:, 1] - cy) < 0.05
    z[specks] += rng.uniform(-0.04, 0.04, specks.sum())
    xyz = np.c_[xy, z].astype(np.float32)
    return {"xyz": xyz, "idx": np.arange(n, dtype=np.int64), "strip": strip, "specks": specks}


def test_split_finds_the_rough_strip_and_cleans_up_specks(patch):
    feat = set_features(patch["xyz"], patch["idx"])
    t = feat.otsu("curvature")
    assert 0 < t < 0.2
    raw, raw_unsure, raw_info = split_labels(feat, SplitParams(threshold=t, clean=False))
    plane = ~patch["strip"] & ~patch["specks"] & (patch["xyz"][:, 1] < 2.8)  # away from the strip's edge
    assert raw[patch["strip"] & (patch["xyz"][:, 1] > 3.2)].mean() > 0.9 and raw[plane].mean() < 0.02
    assert raw[patch["specks"]].mean() > 0.5  # the specks are rough before the clean-up
    clean, unsure, info = split_labels(feat, SplitParams(threshold=t, min_island=2000))
    assert clean[patch["specks"]].mean() < 0.1  # ... and join the smooth plane after it
    assert unsure[patch["specks"]].mean() > 0.9  # flagged to look at: the clean-up changed them
    assert info["changed_by_clean_up"] > 0 and info["rough"] + info["smooth"] == info["points"]
    rough_again, _, _ = split_labels(feat, SplitParams(feature="roughness", threshold=feat.otsu("roughness")))
    assert rough_again[patch["strip"] & (patch["xyz"][:, 1] > 3.2)].mean() > 0.9


def test_split_point_set_replaces_it_by_two():
    t = Annotations()
    d = t.new_directory("ground")
    ps = t.add(PointSet(np.arange(10, dtype=np.int64), [{"tool": "grow_select"}]), name="area")
    ps.label = "road"
    t.move([ps.id], d.id)
    rough = np.zeros(10, bool)
    rough[[2, 5]] = True
    smooth, rough_set = t.split_point_set(ps.id, rough, {"tool": "split", "threshold": 0.01})
    assert [x.name for x in d.children] == ["area smooth", "area rough"]
    assert rough_set.indices.tolist() == [2, 5] and smooth.indices.size == 8 and rough_set.label == "road"
    assert rough_set.provenance == [{"tool": "grow_select"}, {"tool": "split", "threshold": 0.01, "part": "rough"}]
