"""Trees identified by a band of trunk: the circle fit (position at the ground height, girth) and tree names."""

import numpy as np
import pytest

from pclabel.tools.trees import TreeParams, fit_trunk, next_tree_name
from pclabel.tools.volume import frame_axes


def _band(axis, rng):
    """Half a trunk (r 0.2 m, as one scan position sees it) 1.00-1.10 m above a ground point, plus shrub points in
    the same cylinder; returned in world coordinates for a height axis ``axis``."""
    n, u, v = frame_axes(axis, (1.0, 0.0, 0.0))
    ground = np.array([3.0, -2.0, 0.5])
    centre = (0.4, 0.3)  # across the axis, from the ground point
    a = rng.uniform(-np.pi / 2, np.pi / 2, 400)
    r = 0.2 + rng.normal(0, 0.003, a.size)
    x, y = centre[0] + r * np.cos(a), centre[1] + r * np.sin(a)
    h = rng.uniform(1.0, 1.1, a.size)
    shrub = rng.uniform(-0.45, 0.45, (40, 2)) + centre
    shrub = shrub[np.hypot(shrub[:, 0] - centre[0], shrub[:, 1] - centre[1]) > 0.3]  # leaves around, not on, the trunk
    x, y = np.r_[x, shrub[:, 0]], np.r_[y, shrub[:, 1]]
    h = np.r_[h, rng.uniform(1.0, 1.1, len(shrub))]
    xyz = ground + np.outer(x, u) + np.outer(y, v) + np.outer(h, n)
    return xyz.astype(np.float32), ground, ground + centre[0] * u + centre[1] * v, len(shrub)


@pytest.mark.parametrize("axis", [(0.0, 0.0, 1.0), (0.05, -0.03, 1.0)])  # z, and a fitted plane's normal
def test_fit_trunk_finds_the_position_and_girth(axis):
    xyz, ground, position, n_shrub = _band(axis, np.random.default_rng(1))
    fit = fit_trunk(xyz, np.arange(len(xyz)), ground, axis)
    assert np.linalg.norm(fit["centre"] - position) < 0.005  # at the ground height, under the trunk's centre
    assert fit["radius"] == pytest.approx(0.2, abs=0.003) and fit["girth"] == pytest.approx(2 * np.pi * 0.2, abs=0.02)
    assert fit["dropped"] == n_shrub and fit["points"] == 400 and fit["rms"] < 0.005
    assert 0.45 < fit["coverage"] < 0.6  # half the circumference
    assert fit["band"][0] == pytest.approx(1.0, abs=0.01) and fit["band"][1] == pytest.approx(1.1, abs=0.01)


def test_too_few_points_and_tree_names():
    with pytest.raises(ValueError):
        fit_trunk(np.zeros((3, 3), np.float32), np.arange(3), np.zeros(3), (0, 0, 1))
    assert next_tree_name([]) == "tree 01"
    assert next_tree_name(["tree 01", "tree 07", "trunk", "tree x"]) == "tree 08"
    assert TreeParams().band_bottom == 1.0 and TreeParams().band_top == 1.1
