"""Ground grow on the synthetic street (crowned road, markings, drains, a lowered kerb with a pavement ramp)."""

from dataclasses import replace

import numpy as np
import pytest

from pclabel.store.tiles import TilingParams, build_tiles
from pclabel.synth import make_street
from pclabel.tools.ground import GroundGrowParams, ground_grow

# the synthetic scan is about 40x sparser than streets 1, so cells are 20 cm here (10 cm is the default)
P = GroundGrowParams(cell=0.2, max_gap=0.6, intensity_range=1.0)


@pytest.fixture(scope="module")
def scene(taxonomy):
    s = make_street(n_target=1_000_000, seed=3)
    tiles, order = build_tiles(s.xyz, TilingParams(max_points=50_000))
    xyz = np.ascontiguousarray(s.xyz[order])
    cls, flags = s.cls[order], s.flags[order]
    road = cls == taxonomy.class_id("road")
    d = np.where(road, np.hypot(xyz[:, 0] + 5, xyz[:, 1] + 1.5), np.inf)
    seed = xyz[np.argmin(d)].astype(np.float64)  # on the road, near the scanner
    return {"xyz": xyz, "inten": s.intensity[order], "tiles": tiles, "cls": cls, "flags": flags, "seed": seed,
            "params": s.params}  # fmt: skip


def selected(scene, params):
    r = ground_grow(scene["xyz"], scene["inten"], scene["tiles"], scene["seed"], params)
    sel = np.zeros(len(scene["xyz"]), bool)
    sel[r.indices] = True
    return sel, r


def test_grows_over_the_road_and_its_markings_only(scene, taxonomy):
    sel, r = selected(scene, P)
    cls = scene["cls"]
    road = cls == taxonomy.class_id("road")
    marking = (scene["flags"] & taxonomy.flag_mask("marking")) > 0
    assert (sel & ~road).sum() <= 0.005 * sel.sum()  # kerb and drain-lip points at road level, at most 0.5 %
    for name in ("pavement", "grass", "tree_trunk", "tree_canopy", "lamp_post"):
        assert not (sel & (cls == taxonomy.class_id(name))).any(), name
    assert sel[road].mean() > 0.95
    assert sel[marking].mean() > 0.9  # paint is bridged, not a boundary
    assert r.info["cells_bridged"] > 0 and np.all(np.diff(r.indices) > 0)


def test_intensity_is_what_stops_the_walkway(scene, taxonomy):
    """At the lowered kerb the pavement ramps down to road height: without the intensity checks the grow leaks
    onto it, with them it does not."""
    cls = scene["cls"]
    pavement = cls == taxonomy.class_id("pavement")
    leak, _ = selected(scene, replace(P, intensity_step=1.0, intensity_range=1.0, max_step=0.025))
    held, _ = selected(scene, replace(P, max_step=0.025))
    assert (leak & pavement).sum() > 10_000
    assert (held & pavement).sum() == 0


def test_markings_need_bridging(scene, taxonomy):
    marking = (scene["flags"] & taxonomy.flag_mask("marking")) > 0
    bridged, _ = selected(scene, P)
    plain, r = selected(scene, replace(P, marking_width=0.0))
    assert r.info["cells_bridged"] == 0
    assert plain[marking].mean() < bridged[marking].mean() - 0.2


def test_points_below_the_surface_are_not_selected(scene, taxonomy):
    """Drain floors (8 cm down) stay out: only points within surface_tol of the grown surface are taken."""
    sel, _ = selected(scene, P)
    p = scene["params"]
    x, y, z = scene["xyz"].T.astype(np.float64)
    plane = p.grade * x - p.crossfall * np.minimum(np.abs(y), p.road_half_width)
    deep = z < plane - 0.06  # the floor (8 cm down); wall points near the lip can sit within surface_tol
    assert deep.sum() > 50 and not (sel & deep).any()


def test_regrow_reuses_the_grid(scene):
    a = ground_grow(scene["xyz"], scene["inten"], scene["tiles"], scene["seed"], P)
    b = ground_grow(scene["xyz"], scene["inten"], scene["tiles"], scene["seed"], P, grid=a.grid)
    assert np.array_equal(a.indices, b.indices) and b.grid is a.grid
    c = ground_grow(scene["xyz"], scene["inten"], scene["tiles"], scene["seed"], replace(P, max_step=0.005),
                    grid=a.grid)  # fmt: skip
    assert c.indices.size < a.indices.size
    assert c.info["params"]["max_step"] == 0.005  # the thresholds travel with the result


def test_reach_limits_the_grow(scene):
    r = ground_grow(scene["xyz"], scene["inten"], scene["tiles"], scene["seed"], replace(P, radius=4.0))
    q = scene["xyz"][r.indices]
    assert r.info["reached_edge"] and np.all(np.abs(q[:, :2] - scene["seed"][:2]) <= 4.0 + 1e-6)


def test_seed_without_ground_data(scene):
    with pytest.raises(ValueError):
        ground_grow(scene["xyz"], scene["inten"], scene["tiles"], scene["seed"] + (0, 0, 0), P,
                    clip=(scene["seed"] + 100, scene["seed"] + 101))  # fmt: skip


def patch(parts, max_points=10_000_000):
    """Store-ordered arrays from [(xyz (n, 3), intensity 0..1 (n,)), ...]."""
    xyz = np.vstack([p[0] for p in parts]).astype(np.float32)
    inten = (np.concatenate([p[1] for p in parts]) * 65535).astype(np.uint16)
    tiles, order = build_tiles(xyz, TilingParams(max_points=max_points))
    return np.ascontiguousarray(xyz[order]), inten[order], tiles


def flat(rng, x0, x1, y0, y1, density, z=0.0, inten=0.05):
    n = int((x1 - x0) * (y1 - y0) * density)
    q = np.c_[rng.uniform(x0, x1, n), rng.uniform(y0, y1, n), np.full(n, z) + rng.normal(0, 0.001, n)]
    i = np.full(n, inten) if np.isscalar(inten) else inten(q)
    return q, np.clip(i + rng.normal(0, 0.003, n), 0, 1)


def test_dense_tile_does_not_starve_its_sparse_part():
    """Regression: sampling per tile left the sparse road inside a dense tile with too few points per cell."""
    rng = np.random.default_rng(0)
    road = flat(rng, 0, 20, 0, 2, 1000)
    blob = flat(rng, 0, 0.7, 0, 0.7, 10_000_000)  # 5M points in half a square metre (next to the scanner)
    xyz, inten, tiles = patch([road, blob])
    assert len(tiles) == 1  # one tile holds both
    r = ground_grow(xyz, inten, tiles, np.array([15.0, 1.0, 0.0]), GroundGrowParams(radius=22))
    assert r.info["area_m2"] > 0.95 * 20 * 2


def test_intensity_is_compared_with_the_local_road_not_the_seed():
    """Road intensity drifts 0.05 -> 0.15 over 30 m (range falloff); a walkway beside it at the same height is
    0.06 brighter than the road next to it. Local: the whole road and no walkway. Seed-fixed: a straight cut."""
    rng = np.random.default_rng(1)
    drift = lambda q: 0.05 + 0.10 * q[:, 0] / 30  # noqa: E731
    road = flat(rng, 0, 30, 0, 3, 2000, inten=drift)
    walk = flat(rng, 0, 30, 3, 5, 2000, inten=lambda q: drift(q) + 0.06)
    xyz, inten, tiles = patch([road, walk], max_points=200_000)
    seed = np.array([2.0, 1.5, 0.0])
    sel_road = lambda r: (xyz[r.indices, 1] < 3).sum() / (xyz[:, 1] < 3).sum()  # noqa: E731
    local = ground_grow(xyz, inten, tiles, seed, GroundGrowParams(radius=32))
    assert sel_road(local) > 0.95 and (xyz[local.indices, 1] > 3.05).sum() == 0
    fixed = ground_grow(xyz, inten, tiles, seed, GroundGrowParams(radius=32, intensity_memory=1e9))
    assert sel_road(fixed) < 0.6  # stops where the road has drifted 0.04 from the seed: a line across the road
    assert fixed.info["stopped_by"]["intensity vs local road"] >= 25  # a cut the width of the road (3 m / 10 cm)
    assert local.info["stopped_by"]["intensity vs local road"] < 5


def test_gaps_are_crossed_but_not_up_a_kerb():
    rng = np.random.default_rng(2)
    # a flat road with a 0.6 m no-point strip across it (a pole's shadow)
    a = flat(rng, 0, 10, 0, 3, 2000)
    b = flat(rng, 10.6, 20, 0, 3, 2000)
    xyz, inten, tiles = patch([a, b])
    r = ground_grow(xyz, inten, tiles, np.array([5.0, 1.5, 0.0]), GroundGrowParams(radius=25))
    assert (xyz[r.indices, 0] > 10.6).sum() > 0.95 * len(b[0])
    # a 15 cm kerb top behind a 0.6 m no-point strip (the strip behind a kerb that faces away)
    c = flat(rng, 0, 10, 0, 3, 2000)
    d = flat(rng, 10.6, 20, 0, 3, 2000, z=0.15)
    xyz, inten, tiles = patch([c, d])
    r = ground_grow(xyz, inten, tiles, np.array([5.0, 1.5, 0.0]), GroundGrowParams(radius=25))
    assert (xyz[r.indices, 0] > 10.6).sum() == 0 and r.info["stopped_by"]["height step"] > 0
