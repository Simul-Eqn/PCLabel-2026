"""3D select: boxes and cylinders along the height axis, against brute force."""

from dataclasses import replace

import numpy as np
import pytest

from pclabel.store.tiles import TilingParams, build_tiles
from pclabel.tools.selection import SelectFilters
from pclabel.tools.volume import VolumeParams, frame_axes, outline, select_volume


@pytest.fixture(scope="module")
def cloud():
    rng = np.random.default_rng(0)
    xyz = np.c_[rng.uniform(-10, 10, 400_000), rng.uniform(-10, 10, 400_000), rng.uniform(-1, 5, 400_000)]
    tiles, order = build_tiles(xyz.astype(np.float32), TilingParams(max_points=20_000))
    return np.ascontiguousarray(xyz.astype(np.float32)[order]), tiles


def brute(xyz, base, axis, along, p):
    n, u, v = frame_axes(axis, along, p.turn)
    d = xyz.astype(np.float64) - base
    h, x, y = d @ n, d @ u, d @ v
    inside = (h >= p.bottom) & (h <= p.top)
    if p.shape == "box":
        inside &= (np.abs(x) <= p.length / 2) & (np.abs(y) <= p.width / 2)
    else:
        inside &= x**2 + y**2 <= p.radius**2
    return np.flatnonzero(inside)


@pytest.mark.parametrize("p", [VolumeParams(), VolumeParams(length=6, width=1.5, turn=30, bottom=0.5, top=3),
                               VolumeParams(shape="cylinder", radius=1.2, bottom=-1, top=1.5)])  # fmt: skip
@pytest.mark.parametrize("axis", [(0, 0, 1), (0.05, -0.08, 1.0)])
def test_matches_brute_force(cloud, p, axis):
    xyz, tiles = cloud
    base = np.array([1.5, -2.0, 0.3])
    along = (1.0, 0.0, 0.0)
    got = select_volume(xyz, tiles, base, axis, along, p)
    want = brute(xyz, base, np.asarray(axis, float), along, p)
    assert want.size > 1000 and np.array_equal(got, want)
    assert np.all(np.diff(got) > 0)


def test_filters_and_outline(cloud):
    xyz, tiles = cloud
    base = np.array([0.0, 0.0, 0.0])
    p = VolumeParams(length=4, width=4)
    clip = SelectFilters(clip_min=(-10, 0, -10), clip_max=(10, 10, 10))  # the half with y >= 0
    got = select_volume(xyz, tiles, base, (0, 0, 1), (1, 0, 0), p, clip)
    assert got.size and np.all(xyz[got, 1] >= 0)
    n, u, v = frame_axes((0, 0, 1), (1, 0, 0), 0)
    box = outline(base, n, u, v, p)
    assert len(box) == 2 + 24 + 4 and np.allclose(box[0][:, 2], p.bottom) and np.allclose(box[1][:, 2], p.top)
    cyl = outline(base, n, u, v, replace(p, shape="cylinder", radius=2), verticals=64)
    assert np.allclose(np.hypot(cyl[0][:, 0], cyl[0][:, 1]), 2) and len(cyl) == 2 + 64
    feet = np.array([line[0, :2] for line in cyl[2:]])
    gaps = np.linalg.norm(np.diff(np.vstack([feet, feet[:1]]), axis=0), axis=1)
    assert np.allclose(gaps, gaps.mean(), rtol=0.02)  # spread evenly round it


def square_distance(x, y, half):
    """Distance from points to a square [-half, half]^2 (0 inside)."""
    dx = np.maximum(np.abs(x) - half, 0)
    dy = np.maximum(np.abs(y) - half, 0)
    return np.hypot(dx, dy)


@pytest.mark.parametrize("inset", [0.0, 0.5, -0.5])
@pytest.mark.parametrize("normal", [(0, 0, 1), (0.2, -0.1, 1.0)])
def test_extrude_select_matches_brute_force(cloud, inset, normal):
    from pclabel.tools.volume import select_loop

    xyz, tiles = cloud
    n = np.asarray(normal, float) / np.linalg.norm(normal)
    n_, u, v = frame_axes(n, (1, 0, 0), 0)
    origin = np.array([1.0, -1.0, 1.5])
    t = np.linspace(0, 1, 101)[:-1]
    side = np.r_[-2 + 4 * t]
    square = np.vstack([np.c_[side, np.full_like(side, -2)], np.c_[np.full_like(side, 2), side],
                        np.c_[-side, np.full_like(side, 2)], np.c_[np.full_like(side, -2), -side]])  # fmt: skip
    got = np.zeros(len(xyz), bool)
    got[select_loop(xyz, tiles, origin, n, u, square, -0.5, 1.0, inset)] = True
    d = xyz.astype(np.float64) - origin
    h, x, y = d @ n_, d @ u, d @ v
    in_h = (h >= -0.5) & (h <= 1.0)
    if inset >= 0:
        inside_core = (np.abs(x) <= 2 - inset) & (np.abs(y) <= 2 - inset)
        margin = np.minimum(np.abs(np.abs(x) - (2 - inset)), np.abs(np.abs(y) - (2 - inset)))
    else:
        dist = square_distance(x, y, 2.0)
        inside_core = dist <= -inset
        margin = np.abs(dist - (-inset))
    want = in_h & inside_core
    unsure = in_h & (margin < 0.025)  # within about two cells of the boundary
    assert want.sum() > 1000 and np.array_equal(got & ~unsure, want & ~unsure)
    assert (got != want).sum() <= unsure.sum()


def test_loop_transform_and_inset_outline():
    from pclabel.tools.volume import inset_outline, loop_transform

    t = np.linspace(0, 2 * np.pi, 201)
    circle = np.c_[np.cos(t), np.sin(t)] * 2 + (5, 1)
    small = loop_transform(circle, 0.5, 90, (1, 0))
    assert np.allclose(np.hypot(*(small - (6, 1)).T), 1.0, atol=1e-6)  # half the size, same centre, moved
    inner = inset_outline(circle, 0.3)
    assert np.allclose(np.hypot(*(inner - (5, 1)).T), 1.7, atol=1e-3)  # 30 cm in, whichever way round it was drawn
    assert np.allclose(np.hypot(*(inset_outline(circle[::-1], 0.3) - (5, 1)).T), 1.7, atol=1e-3)


def test_volume_spec_contains_what_select_takes(cloud):
    from pclabel.tools.volume import box_spec, loop_spec, select_loop

    xyz, tiles = cloud
    base = np.array([1.5, -2.0, 0.3])
    for prm in (VolumeParams(length=5, width=2, turn=20), VolumeParams(shape="cylinder", radius=1.5)):
        spec = box_spec(base, (0.05, 0, 1), (1, 0, 0), prm)
        want = select_volume(xyz, tiles, base, (0.05, 0, 1), (1, 0, 0), prm)
        assert np.array_equal(np.flatnonzero(spec.contains(xyz)), want)
    t = np.linspace(0, 2 * np.pi, 400)
    poly = np.c_[np.cos(t), np.sin(t)] * 2
    spec = loop_spec(base, (0, 0, 1), (1, 0, 0), poly, -0.5, 1.0, 0.2)
    assert np.array_equal(np.flatnonzero(spec.contains(xyz)), select_loop(xyz, tiles, base, (0, 0, 1), (1, 0, 0), poly,
                                                                          -0.5, 1.0, 0.2))  # fmt: skip
    f = SelectFilters(volume=spec)
    idx = np.arange(len(xyz))
    assert np.array_equal(f.apply(xyz, idx), np.flatnonzero(spec.contains(xyz)))  # hidden by the clip: not selected
