"""The clip to point sets, tools side: a PointMask limits what every tool reads, not only what it returns.

The scene: two stretches of flat road (x 0..10 and 13..23) joined by a third (x 10..13). The clip keeps the two
outer stretches, so for the tools the middle one is not there: a 3 m gap no grow can step over.
"""

import numpy as np
import pytest

from pclabel.store.tiles import TilingParams, build_tiles
from pclabel.tools import sets
from pclabel.tools.cleanup import CleanParams, clean_selection
from pclabel.tools.ground import GroundGrowParams, ground_grow
from pclabel.tools.magic import MagicSelectParams, magic_select
from pclabel.tools.normals import NormalGrowParams, normal_grow
from pclabel.tools.pick import nearest_point
from pclabel.tools.selection import SelectFilters
from pclabel.tools.volume import VolumeParams, select_volume


def road(rng, x0, x1, density=2000, inten=0.05, marking=None):
    """A flat road 3 m wide from x0 to x1; ``marking``: the intensity of a 20 cm line along y = 1.5."""
    n = int((x1 - x0) * 3 * density)
    q = np.c_[rng.uniform(x0, x1, n), rng.uniform(0, 3, n), rng.normal(0, 0.001, n)]
    i = np.full(n, inten)
    if marking is not None:
        i[np.abs(q[:, 1] - 1.5) < 0.1] = marking
    return q, np.clip(i + rng.normal(0, 0.003, n), 0, 1)


def make_scene(marking=None):
    rng = np.random.default_rng(0)
    parts = [road(rng, x0, x1, marking=marking) for x0, x1 in ((0, 10), (10, 13), (13, 23))]
    xyz = np.vstack([p[0] for p in parts]).astype(np.float32)
    inten = (np.concatenate([p[1] for p in parts]) * 65535).astype(np.uint16)
    tiles, order = build_tiles(xyz, TilingParams(max_points=40_000))
    xyz, inten = np.ascontiguousarray(xyz[order]), inten[order]
    keep = np.flatnonzero((xyz[:, 0] < 10) | (xyz[:, 0] > 13))
    return xyz, inten, tiles, sets.PointMask(keep, len(xyz), "outer stretches")


@pytest.fixture(scope="module")
def scene():
    return make_scene()


def test_point_mask(scene):
    xyz, _, tiles, inside = scene
    assert inside.mask.sum() == inside.indices.size and inside.mask[inside.indices].all()
    counts = inside.tile_counts(tiles)
    assert counts.sum() == inside.indices.size
    assert np.array_equal(inside.keep_tiles(tiles, np.arange(len(tiles))), np.flatnonzero(counts > 0))
    a, n = int(tiles["start"][1]), int(tiles["count"][1])
    one = sets.PointMask(np.arange(a, a + n), len(xyz))  # the points of one tile: the others are skipped
    assert one.keep_tiles(tiles, np.arange(len(tiles))).tolist() == [1]
    assert sets.PointMask(inside.indices, len(xyz)).token != inside.token


def test_grow_select_sees_only_the_set(scene):
    xyz, inten, tiles, inside = scene
    seed, p = np.array([5.0, 1.5, 0.0]), GroundGrowParams(radius=25)
    far = xyz[:, 0] > 13
    free = ground_grow(xyz, inten, tiles, seed, p)
    assert (xyz[free.indices, 0] > 13).sum() > 0.95 * far.sum()  # over the middle stretch
    clipped = ground_grow(xyz, inten, tiles, seed, p, inside=inside)
    # not the free result trimmed: without the middle stretch the far one is never reached
    assert inside.mask[clipped.indices].all() and (xyz[clipped.indices, 0] > 13).sum() == 0
    assert (xyz[clipped.indices, 0] < 10).sum() > 0.95 * (xyz[:, 0] < 10).sum()
    g = clipped.grid  # built from the set's points only: no points over the middle stretch
    cols = slice(int((10.2 - g.x0) / g.cell), int((12.8 - g.x0) / g.cell))
    assert g.count[:, cols].sum() == 0 and free.grid.count[:, cols].sum() > 0


def test_geometric_grow_sees_only_the_set(scene):
    xyz, _, tiles, inside = scene
    seed = np.array([5.0, 1.5, 0.0])
    p = NormalGrowParams(voxel=0.05, normal_radius=0.15, connect=0.075, radius=25)
    free = normal_grow(xyz, tiles, seed, p)
    assert (xyz[free.indices, 0] > 13).sum() > 0.95 * (xyz[:, 0] > 13).sum()
    clipped = normal_grow(xyz, tiles, seed, p, inside=inside)
    assert inside.mask[clipped.indices].all() and (xyz[clipped.indices, 0] > 13).sum() == 0
    assert clipped.info["voxels_reused"] is False and clipped.voxels.key != free.voxels.key
    again = normal_grow(xyz, tiles, seed, p, voxels=clipped.voxels, inside=inside)
    assert again.info["voxels_reused"] and np.array_equal(again.indices, clipped.indices)


def test_magic_select_sees_only_the_set():
    xyz, inten, tiles, inside = make_scene(marking=0.4)
    seed, p = np.array([9.0, 1.5, 0.0]), MagicSelectParams(radius=16)
    line = np.abs(xyz[:, 1] - 1.5) < 0.09
    free = magic_select(xyz, inten, tiles, seed, p)
    assert (xyz[free.indices, 0] > 13).sum() > 0.9 * (line & (xyz[:, 0] > 13)).sum()  # along the whole line
    clipped = magic_select(xyz, inten, tiles, seed, p, image=free.image, inside=inside)
    assert not clipped.info["picture_reused"]  # a picture of other points
    assert inside.mask[clipped.indices].all() and (xyz[clipped.indices, 0] > 13).sum() == 0
    assert (line & (xyz[:, 0] < 9.5) & (xyz[:, 0] > 1))[clipped.indices].sum() > 0  # still the line before the gap


def test_pick_only_points_of_the_set(scene):
    xyz, _, tiles, inside = scene
    q = np.array([11.5, 1.5, 0.0])  # over the middle stretch
    assert nearest_point(xyz, tiles, q, search=2.0).distance < 0.05
    res = nearest_point(xyz, tiles, q, search=2.0, inside=inside)
    assert res is not None and inside.mask[res.index] and abs(res.distance - 1.5) < 0.05
    d = np.linalg.norm(xyz[inside.indices].astype(np.float64) - xyz[res.index], axis=1)
    assert res.within[0.10] == (d <= 0.10).sum()  # neighbours counted in the set only
    assert nearest_point(xyz, tiles, q, search=1.0, inside=inside) is None


def test_selections_keep_only_the_set(scene):
    xyz, _, tiles, inside = scene
    f = SelectFilters(inside=inside)
    idx = select_volume(xyz, tiles, np.array([11.5, 1.5, 0.0]), (0, 0, 1), (1, 0, 0),
                        VolumeParams(shape="box", length=6.0, width=2.0, bottom=-1.0, top=1.0), filters=f)  # fmt: skip
    box = (np.abs(xyz[:, 0] - 11.5) <= 3.0) & (np.abs(xyz[:, 1] - 1.5) <= 1.0)
    assert idx.size > 0 and np.array_equal(idx, np.flatnonzero(box & inside.mask))
    every = np.arange(len(xyz), dtype=np.int64)
    assert np.array_equal(f.apply(xyz, every), inside.indices)


def test_clean_up_reads_only_the_set(scene):
    xyz, _, tiles, inside = scene
    sel = inside.indices[xyz[inside.indices, 2] <= 0]  # about half the set's points
    args = (xyz, tiles, sel, (0, 0, 1), (1, 0, 0), np.zeros(3), CleanParams())
    _, _, free = clean_selection(*args)
    added, removed, info = clean_selection(*args, inside=inside)
    assert free["region_points"] == len(xyz) and info["region_points"] == inside.indices.size
    assert inside.mask[added].all() and inside.mask[removed].all() and info["added"] > 0
