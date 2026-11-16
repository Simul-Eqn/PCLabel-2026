"""Smoothing a kerb-top line onto the kerb (tools/kerbfit.py), on a small evenly scanned kerb: road at height 0 up to
y = 3.70, a 2 cm face, the kerb top from y = 3.72 at 0.15 m (a pavement rising behind it), lowered to 0.02 m at
x = 7..9 with 1 m ramps either side, and a drain pit in front of the kerb at x = 4..4.9."""

import numpy as np
import pytest

from pclabel.analysis import curves
from pclabel.synth import store_order
from pclabel.tools.kerbfit import KerbFitParams, fit


def kerb_height(x):
    h = np.full_like(x, 0.15)
    down = (x > 6) & (x < 7)
    h[down] = 0.15 - 0.13 * (x[down] - 6)
    h[(x >= 7) & (x <= 9)] = 0.02
    up = (x > 9) & (x < 10)
    h[up] = 0.02 + 0.13 * (x[up] - 9)
    return h


@pytest.fixture(scope="module")
def kerb():
    rng = np.random.default_rng(7)
    x, y = np.meshgrid(np.arange(-1.0, 16.0, 0.02), np.arange(2.5, 5.0, 0.02), indexing="ij")
    x, y = x.ravel(), y.ravel()
    k = kerb_height(x)
    z = np.where(y < 3.70, 0.0, np.where(y < 3.72, k * (y - 3.70) / 0.02, k + 0.02 * (y - 3.72)))
    pit = (x > 4.0) & (x < 4.9) & (y > 3.35) & (y < 3.70)
    z[pit] = -0.08
    xyz = np.c_[x, y, z + 0.003 * rng.standard_normal(len(x))].astype(np.float32)
    return store_order(xyz, 20_000)


def test_a_wavy_kerb_top_line_is_smoothed_onto_the_kerb_and_carried_over_the_lowered_kerb(kerb):
    xyz, tiles = kerb
    x = np.arange(0.0, 15.0, 0.25)
    wave = 0.05 * np.sin(x * 2.1) + 0.02 * np.random.default_rng(2).standard_normal(len(x))  # a hand-drawn wobble
    z = kerb_height(x) + 0.01 * np.random.default_rng(3).standard_normal(len(x))  # down the slope where it is lowered
    line = np.c_[x, 3.72 + wave, z]
    r = fit(xyz, tiles, line, (0, 0, 1), KerbFitParams(), False)
    X = r["vertices"]
    assert r["side"] == "left" and r["anchors"] > 30 and r["height_median"] == pytest.approx(0.15, abs=0.01)
    assert r["off_anchors_rms"] < 0.02 and r["off_anchors_max"] <= 0.05
    on_kerb = ((X[:, 0] > 0.5) & (X[:, 0] < 5.5)) | ((X[:, 0] > 10.5) & (X[:, 0] < 14.5))
    assert np.median(np.abs(X[on_kerb, 1] - 3.72)) < 0.02 and np.abs(X[on_kerb, 1] - 3.72).max() < 0.05
    assert np.abs(X[on_kerb, 2] - 0.15).max() < 0.02
    assert curves.wiggle(curves.resample(X, 0.1)) < 0.5 * curves.wiggle(curves.resample(line, 0.1))
    # the lowered kerb: carried across at the kerb top (from one side's top to the other's), not down the slope
    assert r["low_steps"] + r["no_edge"] >= 8
    assert any(a <= 7.0 and a + b >= 9.0 for a, b in r["bridged"]) and not r["kept"]
    mid = np.abs(X[:, 0] - 8.0) < 1.0
    assert np.all(X[mid, 2] > 0.12) and np.abs(X[mid, 1] - 3.72).max() < 0.06
    # in front of the drain the kerb top is measured on
    assert np.abs(X[np.abs(X[:, 0] - 4.45) < 0.4, 1] - 3.72).max() < 0.04


def test_long_stretches_without_kerb_stay_and_no_kerb_at_all(kerb):
    xyz, tiles = kerb
    # past the scanned kerb (x > 16) the line stays where it was
    x = np.arange(10.0, 30.0, 0.25)
    line = np.c_[x, np.full(len(x), 3.75), np.full(len(x), 0.15)]
    r = fit(xyz, tiles, line, (0, 0, 1))
    assert r["kept"] and r["kept"][-1][1] > 10
    far = r["vertices"][:, 0] > 22
    assert np.allclose(r["vertices"][far, 1], 3.75, atol=0.005)
    with pytest.raises(ValueError, match="no kerb top found"):
        fit(xyz, tiles, np.c_[np.arange(0, 6.0, 0.5), np.full(12, 3.0), np.zeros(12)], (0, 0, 1))
