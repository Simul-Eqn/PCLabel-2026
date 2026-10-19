import numpy as np
import pytest

from pclabel.store.tiles import TilingParams, build_tiles


@pytest.fixture(scope="module")
def tiled(street):
    params = TilingParams(max_points=40_000, cell=0.25, seed=3)
    tiles, order = build_tiles(street.xyz, params)
    return street.xyz, tiles, order, params


def test_every_point_exactly_once(tiled):
    xyz, tiles, order, _ = tiled
    assert order.dtype == np.uint32
    assert np.array_equal(np.sort(order), np.arange(xyz.shape[0]))
    assert tiles["count"].sum() == xyz.shape[0]
    assert np.array_equal(tiles["start"][1:], np.cumsum(tiles["count"])[:-1])


def test_tile_bounds_contain_their_points(tiled):
    xyz, tiles, order, _ = tiled
    for t in tiles:
        p = xyz[order[t["start"] : t["start"] + t["count"]]]
        assert p[:, 0].min() >= t["x0"] - 1e-4 and p[:, 0].max() <= t["x1"] + 1e-4
        assert p[:, 1].min() >= t["y0"] - 1e-4 and p[:, 1].max() <= t["y1"] + 1e-4
        assert p[:, 2].min() == t["zmin"] and p[:, 2].max() == t["zmax"]
        assert np.isclose(p[:, 0].min(), t["x0"]) and np.isclose(p[:, 1].max(), t["y1"])  # tight


def test_leaf_size(tiled):
    _, tiles, _, params = tiled
    single_cell = np.isclose(tiles["size"], params.cell)
    assert np.all((tiles["count"] <= params.max_points) | single_cell)
    assert len(tiles) > 10


def test_prefix_is_uniform_subsample(tiled):
    xyz, tiles, order, _ = tiled
    big = tiles[np.argmax(tiles["count"])]
    idx = order[big["start"] : big["start"] + big["count"]].astype(np.int64)
    k = len(idx) // 10
    full, prefix = xyz[idx, :2].mean(0), xyz[idx[:k], :2].mean(0)
    spread = xyz[idx, :2].std(0)
    assert np.all(np.abs(prefix - full) < 4 * spread / np.sqrt(k))
    # source order (scanner columns) must be broken up inside the tile
    assert abs(np.corrcoef(np.arange(len(idx)), idx)[0, 1]) < 0.05


def test_deterministic_for_seed(street):
    a = build_tiles(street.xyz, TilingParams(max_points=40_000, seed=1))[1]
    b = build_tiles(street.xyz, TilingParams(max_points=40_000, seed=1))[1]
    c = build_tiles(street.xyz, TilingParams(max_points=40_000, seed=2))[1]
    assert np.array_equal(a, b) and not np.array_equal(a, c)
