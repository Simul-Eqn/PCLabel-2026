"""Magic select by intensity on the synthetic street (dashed centre line, double yellow lines) and on small patches
(scan-line gaps, a fading line, painted kerbs)."""

from dataclasses import replace

import numpy as np
import pytest

from pclabel.store.tiles import TilingParams, build_tiles
from pclabel.synth import make_street
from pclabel.tools.magic import MagicSelectParams, magic_select

P = MagicSelectParams(radius=8.0)


@pytest.fixture(scope="module")
def scene(taxonomy):
    s = make_street(n_target=1_000_000, seed=3)
    tiles, order = build_tiles(s.xyz, TilingParams(max_points=50_000))
    xyz = np.ascontiguousarray(s.xyz[order])
    cls, flags = s.cls[order], s.flags[order]
    marking = (flags & taxonomy.flag_mask("marking")) > 0
    dash = marking & (np.abs(xyz[:, 1]) < 0.1) & (xyz[:, 0] > -3) & (xyz[:, 0] < 0)  # the centre dash x -3..0
    d = np.where(dash, np.hypot(xyz[:, 0] + 1.5, xyz[:, 1]), np.inf)
    return {"xyz": xyz, "inten": s.intensity[order], "tiles": tiles, "cls": cls, "marking": marking, "dash": dash,
            "seed": xyz[np.argmin(d)].astype(np.float64)}  # fmt: skip


def selected(scene, params, seed=None, **kw):
    r = magic_select(scene["xyz"], scene["inten"], scene["tiles"], scene["seed"] if seed is None else seed, params, **kw)
    sel = np.zeros(len(scene["xyz"]), bool)
    sel[r.indices] = True
    return sel, r


def test_selects_the_clicked_dash(scene, taxonomy):
    sel, r = selected(scene, P)
    assert sel[scene["dash"]].mean() > 0.98
    assert (sel & scene["marking"]).sum() > 0.97 * sel.sum()
    assert not (sel & scene["marking"] & ~scene["dash"]).any()  # contiguous: not the next dash, not the yellow lines
    assert not (sel & (scene["cls"] != taxonomy.class_id("road"))).any()
    assert np.all(np.diff(r.indices) > 0) and r.info["params"]["tolerance"] == 2.0
    lo, hi = r.info["accepted_intensity"]
    assert lo == pytest.approx(r.info["seed_intensity"] / 2) and hi == pytest.approx(r.info["seed_intensity"] * 2)


def test_not_contiguous_takes_the_other_markings_on_the_road(scene, taxonomy):
    sel, _ = selected(scene, replace(P, contiguous=False))
    cls = scene["cls"]
    assert (sel & scene["marking"] & ~scene["dash"]).sum() > 10_000  # other dashes and the yellow lines
    assert (sel & scene["marking"]).sum() > 0.85 * sel.sum()
    for name in ("pavement", "grass", "tree_trunk", "tree_canopy", "lamp_post"):  # as bright, but not on the road
        assert not (sel & (cls == taxonomy.class_id(name))).any(), name


def test_tolerance_is_a_factor(scene, taxonomy):
    tight, _ = selected(scene, replace(P, tolerance=1.05))  # the dash's own spread is about +-10 %
    assert tight[scene["dash"]].sum() < 0.5 * scene["dash"].sum()
    wide, r = selected(scene, replace(P, tolerance=10.0))  # takes the road too, but not up the kerbs
    road = scene["cls"] == taxonomy.class_id("road")
    assert (wide & road & ~scene["marking"]).sum() > 100_000
    assert not (wide & (scene["cls"] == taxonomy.class_id("pavement"))).any()
    assert r.info["stopped_by"]["height step"] > 0


def test_a_reused_picture_gives_the_same_result(scene):
    p = replace(P, radius=4.0)
    first = magic_select(scene["xyz"], scene["inten"], scene["tiles"], scene["seed"], p)
    seed2 = scene["seed"] + np.array([1.0, 0.02, 0.0])  # the same dash, 1 m on
    again = magic_select(scene["xyz"], scene["inten"], scene["tiles"], seed2, p, image=first.image)
    fresh = magic_select(scene["xyz"], scene["inten"], scene["tiles"], seed2, p)
    assert again.info["picture_reused"] and not fresh.info["picture_reused"]
    assert np.array_equal(again.indices, fresh.indices)
    moved = magic_select(scene["xyz"], scene["inten"], scene["tiles"], seed2, replace(p, radius=6.0),
                         image=first.image)  # fmt: skip
    assert not moved.info["picture_reused"]  # another reach needs another picture


def test_click_above_the_ground(scene):
    with pytest.raises(ValueError, match="no ground"):
        selected(scene, P, seed=scene["seed"] + np.array([0.0, 0.0, 1.0]))


def patch(parts, max_points=200_000):
    """Store-ordered arrays from [(xyz (n, 3), intensity 0..1 (n,)), ...]."""
    xyz = np.vstack([p[0] for p in parts]).astype(np.float32)
    inten = (np.clip(np.concatenate([p[1] for p in parts]), 0, 1) * 65535).astype(np.uint16)
    tiles, order = build_tiles(xyz, TilingParams(max_points=max_points))
    return np.ascontiguousarray(xyz[order]), inten[order], tiles


def flat(rng, x0, x1, y0, y1, density, inten, z=0.0):
    """Random points on a plane; ``inten`` is a function of the points (n, 3) -> (n,)."""
    n = int((x1 - x0) * (y1 - y0) * density)
    q = np.c_[rng.uniform(x0, x1, n), rng.uniform(y0, y1, n), np.full(n, z) + rng.normal(0, 0.001, n)]
    return q, inten(q) + rng.normal(0, 0.003, n)


def test_scan_line_gaps_are_bridged():
    """Far from the scanner the points lie on scan lines (here 8 cm apart, 1 cm along each): a marking crossing them
    at an angle is a row of separate pieces, which the gap fill joins."""
    xs, ys = np.meshgrid(np.arange(0, 6, 0.08), np.arange(0, 4, 0.01))
    q = np.c_[xs.ravel(), ys.ravel(), np.zeros(xs.size)]
    stripe = np.abs(-0.5 * q[:, 0] + 0.866 * q[:, 1] - 0.5) <= 0.06  # 12 cm wide, 30 degrees to x
    xyz, inten, tiles = patch([(q, np.where(stripe, 0.5, 0.06))])
    dist = np.abs(-0.5 * xyz[:, 0] + 0.866 * xyz[:, 1] - 0.5)
    on = dist <= 0.06
    seed = xyz[np.flatnonzero(on)[np.argmin(np.abs(xyz[on, 0] - 3.0))]].astype(np.float64)
    p = MagicSelectParams(radius=4.0)
    r = magic_select(xyz, inten, tiles, seed, p)
    assert np.isin(np.flatnonzero(on), r.indices).mean() > 0.98
    assert np.all(dist[r.indices] <= 0.06 + 2 * p.pixel)  # road points only in edge pixels (mixed with paint)
    r0 = magic_select(xyz, inten, tiles, seed, replace(p, max_gap=0.0))
    assert r0.indices.size < 0.1 * on.sum()


def test_follow_drift_along_a_fading_line():
    """A 12 m line fading from 0.6 to 0.15 (range falloff) on 0.04 road: compared with the click it stops half way;
    following the drift it takes the whole line and still no road."""
    rng = np.random.default_rng(4)
    road = flat(rng, 0, 12, 0, 3, 2000, lambda q: np.full(len(q), 0.04))
    line = flat(rng, 0, 12, 1.45, 1.6, 2000, lambda q: 0.6 - 0.45 * q[:, 0] / 12)
    keep = ~((road[0][:, 1] >= 1.45) & (road[0][:, 1] <= 1.6))
    xyz, inten, tiles = patch([(road[0][keep], road[1][keep]), line])
    on = (xyz[:, 1] >= 1.45) & (xyz[:, 1] <= 1.6)
    seed = np.array([0.5, 1.52, 0.0])
    p = MagicSelectParams(radius=12.5, pixel=0.04)
    fixed = magic_select(xyz, inten, tiles, seed, p)
    assert 0.4 < np.isin(np.flatnonzero(on), fixed.indices).mean() < 0.8
    follow = magic_select(xyz, inten, tiles, seed, replace(p, memory=2.0))
    assert np.isin(np.flatnonzero(on), follow.indices).mean() > 0.98
    y = xyz[follow.indices, 1]
    assert np.all((y >= 1.45 - p.pixel) & (y <= 1.6 + p.pixel))  # road points only in edge pixels


def test_does_not_climb_onto_kerb_paint():
    """A line along the kerb toe, and the kerb top (15 cm up) painted as bright: the height step keeps them apart."""
    rng = np.random.default_rng(5)
    road = flat(rng, 0, 6, 0, 1.9, 3000, lambda q: np.full(len(q), 0.05))
    line = flat(rng, 0, 6, 1.9, 2.0, 3000, lambda q: np.full(len(q), 0.45))
    kerb = flat(rng, 0, 6, 2.0, 2.3, 3000, lambda q: np.full(len(q), 0.45), z=0.15)
    xyz, inten, tiles = patch([road, line, kerb])
    seed = np.array([3.0, 1.95, 0.0])
    r = magic_select(xyz, inten, tiles, seed, MagicSelectParams(radius=4.0))
    q = xyz[r.indices]
    assert np.all(q[:, 2] < 0.1) and len(q) > 0.95 * len(line[0])
    assert r.info["stopped_by"]["height step"] > 0
    climbed = magic_select(xyz, inten, tiles, seed, MagicSelectParams(radius=4.0, max_step=0.3))
    assert (xyz[climbed.indices, 2] > 0.1).sum() > 0.9 * len(kerb[0])
