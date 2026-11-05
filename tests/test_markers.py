"""Markers from selected points (tools/markers.py): the mean, a circle's centre, the mean height or lowest point."""

import numpy as np
import pytest

from pclabel.tools.markers import MarkerParams, place_marker


def pole_seen_from_one_side(radius=0.15, centre=(2.0, -1.0), z0=-0.4, seen_deg=120.0, n=4000, noise=0.002, seed=1):
    """Points on a vertical pole from z0 to z0 + 1.5, only on the arc facing a scanner at -x (as a single scan sees
    it), plus a few ground points around its foot."""
    rng = np.random.default_rng(seed)
    a = np.radians(180 + rng.uniform(-seen_deg / 2, seen_deg / 2, n))
    r = radius + rng.normal(0, noise, n)
    pts = np.c_[centre[0] + r * np.cos(a), centre[1] + r * np.sin(a), z0 + rng.uniform(0, 1.5, n)]
    return pts


def test_mean_and_circle_on_a_pole_seen_from_one_side():
    xyz = pole_seen_from_one_side()
    idx = np.arange(len(xyz))
    z = (0.0, 0.0, 1.0)
    mean = place_marker(xyz, idx, z)
    assert mean["record"]["across"] == "mean" and mean["record"]["points"] == len(xyz)
    # the mean sits toward the scanner (-x): for a 120 degree arc, r sin(60)/(pi/3) = 0.83 r from the axis
    assert mean["position"][0] == pytest.approx(2.0 - 0.15 * np.sin(np.radians(60)) / np.radians(60), abs=0.005)
    assert mean["position"][2] == pytest.approx(xyz[:, 2].mean())
    circ = place_marker(xyz, idx, z, MarkerParams(across="circle"))
    assert np.hypot(circ["position"][0] - 2.0, circ["position"][1] + 1.0) < 0.005  # on the axis
    rec = circ["record"]
    assert rec["radius"] == pytest.approx(0.15, abs=0.005) and rec["coverage"] == pytest.approx(1 / 3, abs=0.06)
    assert rec["mean_off_centre"] == pytest.approx(0.124, abs=0.006) and rec["rms"] < 0.005
    low = place_marker(xyz, idx, z, MarkerParams(across="circle", height="lowest"))
    assert low["position"][2] == pytest.approx(xyz[:, 2].min()) and np.allclose(low["position"][:2], circ["position"][:2])


def test_tilted_height_axis_and_errors():
    xyz = pole_seen_from_one_side(seen_deg=360.0)
    t = np.radians(10)
    axis = np.array([np.sin(t), 0.0, np.cos(t)])  # a fitted plane's normal, tilted 10 degrees
    res = place_marker(xyz, np.arange(len(xyz)), axis, MarkerParams(height="lowest"))
    h = (xyz - res["position"]) @ axis
    assert h.min() == pytest.approx(0.0, abs=1e-9)  # the lowest point along that axis
    with pytest.raises(ValueError, match="nothing selected"):
        place_marker(xyz, [], (0, 0, 1))
    with pytest.raises(ValueError, match="too few"):
        place_marker(xyz, [0, 1, 2], (0, 0, 1), MarkerParams(across="circle"))
    with pytest.raises(ValueError, match="unknown"):
        place_marker(xyz, [0], (0, 0, 1), MarkerParams(across="median"))
