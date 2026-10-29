"""Connected pieces of a selection: keep the clicked object, the largest piece, or drop the random points."""

import numpy as np
import pytest

from pclabel.tools.pieces import PieceParams, find_pieces


@pytest.fixture(scope="module")
def scene():
    """A pole (r 10 cm, 0..3 m, with a 50 cm break from 1.5 to 2.0 m), a box 2 m away, and 6 stray clusters of
    3-20 points 3-10 m away. Store order is shuffled; the selection is all of it except some unrelated points."""
    rng = np.random.default_rng(0)
    a = rng.uniform(0, 2 * np.pi, 30000)
    z = rng.uniform(0, 2.5, 30000)
    z = np.where(z < 1.5, z, z + 0.5)  # the break: nothing between 1.5 and 2.0 m
    pole = np.c_[0.1 * np.cos(a), 0.1 * np.sin(a), z]
    box = rng.uniform([2, -0.5, 0], [3, 0.5, 1], (20000, 3))
    strays = [rng.normal(c, 0.02, (k, 3)) for c, k in zip(
        [(6, 2, 0.5), (-4, 3, 1.0), (8, -5, 0.2), (3, 7, 2.0), (-7, -6, 0.1), (5, 5, 3.0)], [3, 5, 8, 12, 20, 15])]
    other = rng.uniform([-20, -20, 0], [-15, -15, 1], (5000, 3))  # not selected
    xyz = np.vstack([pole, box, *strays, other]).astype(np.float32)
    kind = np.concatenate([np.full(len(pole), 0), np.full(len(box), 1), np.concatenate(
        [np.full(len(s), 2 + i) for i, s in enumerate(strays)]), np.full(len(other), -1)])  # fmt: skip
    order = rng.permutation(len(xyz))
    xyz, kind = np.ascontiguousarray(xyz[order]), kind[order]
    return xyz, kind, np.flatnonzero(kind >= 0)


def test_pieces_by_size_and_the_strays(scene):
    xyz, kind, sel = scene
    p = find_pieces(xyz, sel, PieceParams(gap=0.10))
    assert p.source is sel and p.piece.size == sel.size
    assert p.count == 2 + 1 + 6  # the pole in two (its break), the box, six strays
    assert np.all(np.diff(p.sizes) <= 0) and p.sizes.sum() == sel.size
    assert np.array_equal(p.points([0]), sel[kind[sel] == 1])  # the largest: the box (20,000)
    small = p.small(200)
    assert small.size == 6 and np.array_equal(np.sort(p.points(small)), sel[kind[sel] >= 2])
    keep = p.points(np.setdiff1d(np.arange(p.count), small))  # drop the random points
    assert np.array_equal(keep, sel[kind[sel] <= 1])
    s = p.summary()
    assert s["pieces"] == 9 and s["small_pieces"] == 6 and s["small_points"] == 63 and s["largest"][0] == 20000


def test_the_clicked_piece(scene):
    xyz, kind, sel = scene
    p = find_pieces(xyz, sel, PieceParams(gap=0.10))
    low = p.at((0.1, 0.0, 0.5))  # on the pole's lower part
    high = p.at((0.0, 0.1, 2.4))
    assert low >= 0 and high >= 0 and low != high
    lower = p.points([low])
    assert np.all(xyz[lower, 2] < 1.6) and np.all(kind[lower] == 0)
    assert p.at((0.3, 0.0, 0.5)) == low  # 20 cm off the surface: the nearest piece within reach
    assert p.at((1.2, 0.0, 0.5)) == -1  # nothing within 30 cm
    # a gap larger than the break joins the pole
    q = find_pieces(xyz, sel, PieceParams(gap=0.6))
    pole = q.points([q.at((0.1, 0.0, 0.5))])
    assert np.array_equal(pole, sel[kind[sel] == 0])


def test_empty_and_tiny_selections(scene):
    xyz, _, _ = scene
    p = find_pieces(xyz, np.empty(0, np.int64))
    assert p.count == 0 and p.points([0]).size == 0 and p.at((0, 0, 0)) == -1
    one = find_pieces(xyz, np.array([5], np.int64))
    assert one.count == 1 and one.points([0]).tolist() == [5]
