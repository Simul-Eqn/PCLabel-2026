"""Cleaning up a selection by its footprint: grass over a low cut comes in, a pole mostly does not, specks go."""

from dataclasses import replace

import numpy as np
import pytest

from pclabel.store.tiles import TilingParams, build_tiles
from pclabel.tools.cleanup import CleanParams, apply_clean, clean_selection


@pytest.fixture(scope="module")
def area():
    rng = np.random.default_rng(0)
    n = 300_000
    xy = rng.uniform([0, 0], [10, 6], (n, 2))
    z = rng.normal(0, 0.002, n)
    grass = (xy[:, 0] > 5) & (rng.random(n) < 0.3)
    z[grass] += rng.uniform(0.0, 0.08, grass.sum())  # blades up to 8 cm
    pole = np.c_[np.full(4000, 2.0) + rng.normal(0, 0.03, 4000), np.full(4000, 3.0) + rng.normal(0, 0.03, 4000),
                 rng.uniform(0, 3.0, 4000)]  # fmt: skip
    far = np.c_[rng.uniform(20, 20.1, 30), rng.uniform(0, 0.1, 30), np.zeros(30)]  # a speck, 10 m away
    xyz = np.vstack([np.c_[xy, z], pole, far]).astype(np.float32)
    tiles, order = build_tiles(xyz, TilingParams(max_points=40_000))
    xyz = np.ascontiguousarray(xyz[order])
    # the selection: a low cut (z <= 2 cm) over the area, plus the speck
    sel = np.flatnonzero((xyz[:, 2] <= 0.02) & (xyz[:, 0] < 10.5) | (xyz[:, 0] > 19))
    return xyz, tiles, sel


def test_grass_over_a_low_cut_joins_and_specks_go(area):
    xyz, tiles, sel = area
    added, removed, info = clean_selection(xyz, tiles, sel, (0, 0, 1), (1, 0, 0), np.zeros(3), CleanParams())
    blades = (xyz[:, 0] < 10.5) & (xyz[:, 2] > 0.02) & (xyz[:, 2] <= 0.09) & (np.hypot(xyz[:, 0] - 2, xyz[:, 1] - 3) > 0.2)
    assert np.isin(np.flatnonzero(blades), added).mean() > 0.97
    pole_high = (np.hypot(xyz[:, 0] - 2, xyz[:, 1] - 3) < 0.15) & (xyz[:, 2] > 0.15)
    assert not np.isin(np.flatnonzero(pole_high), added).any()  # only the foot of the pole
    assert np.all(xyz[removed, 0] > 19) and removed.size == (xyz[:, 0] > 19).sum()  # the speck
    new = apply_clean(sel, added, removed)
    assert np.all(np.diff(new) > 0) and new.size == sel.size + added.size - removed.size
    tight, _, _ = clean_selection(xyz, tiles, sel, (0, 0, 1), (1, 0, 0), np.zeros(3), replace(CleanParams(), above=0.03))
    assert tight.size < added.size


def test_only_allowed_points_are_touched(area):
    xyz, tiles, sel = area
    allowed = lambda idx: xyz[idx, 0] < 7.5  # noqa: E731  (e.g. a clip hides x >= 7.5)
    added, removed, _ = clean_selection(xyz, tiles, sel, (0, 0, 1), (1, 0, 0), np.zeros(3), CleanParams(), allowed)
    assert added.size and np.all(xyz[added, 0] < 7.5) and removed.size == 0  # the speck is hidden: it stays
