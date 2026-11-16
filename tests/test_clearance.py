"""Clearance along a road (tools/clearance.py): branches inside the envelope are found and grouped into places."""

import numpy as np
import pytest

from pclabel.store.tiles import TilingParams, build_tiles
from pclabel.tools.clearance import ClearanceParams, check


@pytest.fixture(scope="module")
def scene():
    rng = np.random.default_rng(3)
    road = np.c_[rng.uniform(0, 40, 200_000), rng.uniform(-4, 4, 200_000), rng.normal(0, 0.003, 200_000)]
    # a branch hanging over the road at x 10..12, y 1..2, 3.8..4.2 m up; another above the envelope (6 m); one off
    # to the side (y 6..7, beyond 3.5 m); a person (1.0..1.7 m) on the road
    def blob(x, y, z, n):
        return np.c_[rng.uniform(*x, n), rng.uniform(*y, n), rng.uniform(*z, n)]

    low = blob((10, 12), (1, 2), (3.8, 4.2), 500)
    high = blob((20, 22), (0, 1), (6.0, 6.5), 500)
    side = blob((30, 31), (6, 7), (3.0, 3.5), 500)
    person = blob((25, 25.4), (-1, -0.6), (1.0, 1.7), 300)
    xyz = np.vstack([road, low, high, side, person]).astype(np.float32)
    tiles, order = build_tiles(xyz, TilingParams(max_points=20_000))
    xyz = np.ascontiguousarray(xyz[order])
    return xyz, tiles


def test_branches_in_the_envelope(scene):
    xyz, tiles = scene
    line = np.array([[0.0, 0.0, 0.0], [20.0, 0.0, 0.0], [38.0, 0.0, 0.0]])  # along the road's centre
    r = check(xyz, tiles, line, (0, 0, 1))
    places = r["places"]
    assert r["length"] == pytest.approx(38.0)
    assert len(places) == 2  # the low branch and the person; not the high branch nor the one off to the side
    branch, person = places
    assert 10 <= branch["along"] <= 12 and branch["clearance"] == pytest.approx(3.8, abs=0.02)
    assert branch["points"] == 500 and 1 <= branch["across"] <= 2
    assert person["clearance"] == pytest.approx(1.0, abs=0.03)
    # only the left of the line (+y): the person (on the right) is not in it; a lower envelope misses the branch
    left = check(xyz, tiles, line, (0, 0, 1), ClearanceParams(side="left"))
    assert [round(p["along"]) for p in left["places"]] == [round(branch["along"])]
    assert not check(xyz, tiles, line, (0, 0, 1), ClearanceParams(height=3.5, side="left"))["places"]
    # candidates only: the branch's points
    cand = np.flatnonzero((xyz[:, 2] > 3.7) & (xyz[:, 2] < 4.3))
    only = check(xyz, tiles, line, (0, 0, 1), candidates=cand)
    assert len(only["places"]) == 1 and np.array_equal(np.sort(only["hits"]), np.sort(branch["indices"]))
    # the ends: a line that stops at x = 9 sees nothing
    assert not check(xyz, tiles, np.array([[0.0, 0, 0], [9.0, 0, 0]]), (0, 0, 1))["places"]
    with pytest.raises(ValueError, match="no length"):
        check(xyz, tiles, np.zeros((2, 3)), (0, 0, 1))
