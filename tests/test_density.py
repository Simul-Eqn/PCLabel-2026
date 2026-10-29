"""Density of a point set across the height axis: the dense part keeps its edges and car shadows, drops specks."""

import numpy as np
import pytest

from pclabel.tools.density import MIN_DENSITY, DensityParams, dense_part, density_grid


def flat(rng, x0, x1, y0, y1, density):
    n = int((x1 - x0) * (y1 - y0) * density)
    return np.c_[rng.uniform(x0, x1, n), rng.uniform(y0, y1, n), rng.normal(0, 0.002, n)]


@pytest.fixture(scope="module")
def road():
    """A road 3 m wide: x 0..10 at 4,000 points/m² with a car's shadow (no points) in it, x 10..20 at 400, and a dense
    speck (1 x 0.8 m) in the sparse part."""
    rng = np.random.default_rng(0)
    dense = flat(rng, 0, 10, 0, 3, 4000)
    shadow = (np.abs(dense[:, 0] - 5) < 0.75) & (np.abs(dense[:, 1] - 1.5) < 0.5)
    pts = np.vstack([dense[~shadow], flat(rng, 10, 20, 0, 3, 400), flat(rng, 15, 16, 1, 1.8, 4000)])
    return pts.astype(np.float32)


def test_dense_part_keeps_edges_and_shadows_and_drops_specks(road):
    xyz = road
    g = density_grid(xyz, np.arange(len(xyz)), (0, 0, 1), (1, 0, 0), np.zeros(3))
    dense, sparse, info = dense_part(g, DensityParams())
    assert MIN_DENSITY == 2000 and np.all(np.diff(dense) > 0) and dense.size + sparse.size == len(xyz)
    x = xyz[:, 0]
    inner = np.flatnonzero(x < 9.5)  # the dense stretch, its edges and the rim of the shadow included
    assert np.isin(inner, dense).mean() > 0.995
    assert not np.isin(np.flatnonzero(x > 10.5), dense).any()  # the sparse stretch and the speck in it
    assert info["pieces_dropped"] >= 1  # the speck
    med = info["density_percentiles"]["50"]
    assert 3000 < info["density_percentiles"]["95"] < 5000 and med > 0
    # a lower threshold takes the sparse stretch too, but still not the speck alone: it is part of it now
    all_, _, _ = dense_part(g, DensityParams(min_density=300))
    assert np.isin(np.flatnonzero(x > 10.5), all_).mean() > 0.99


def test_density_is_measured_across_the_height_axis(road):
    """The same road tilted 10° and measured across its own normal: the same dense part."""
    a = np.radians(10)
    rot = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    tilted = (road.astype(np.float64) @ rot.T).astype(np.float32)
    idx = np.arange(len(road))
    flat_dense, _, _ = dense_part(density_grid(road, idx, (0, 0, 1), (1, 0, 0), np.zeros(3)))
    tilt_dense, _, _ = dense_part(density_grid(tilted, idx, rot @ [0, 0, 1], (1, 0, 0), np.zeros(3)))
    assert np.setxor1d(flat_dense, tilt_dense).size < 0.002 * flat_dense.size
    # measured across z instead, the tilt squeezes y by cos 10°: denser, but still not the sparse stretch
    z_dense, _, _ = dense_part(density_grid(tilted, idx, (0, 0, 1), (1, 0, 0), np.zeros(3)))
    assert z_dense.size >= flat_dense.size


def test_a_subset_and_an_empty_set(road):
    idx = np.flatnonzero(road[:, 0] > 10)  # only the sparse stretch and the speck
    dense, sparse, info = dense_part(density_grid(road, idx, (0, 0, 1), (1, 0, 0), np.zeros(3)))
    assert dense.size == 0 and np.array_equal(sparse, idx)
    g = density_grid(road, np.empty(0, np.int64), (0, 0, 1), (1, 0, 0), np.zeros(3))
    dense, sparse, info = dense_part(g)
    assert dense.size == 0 and sparse.size == 0 and info["points"] == 0
