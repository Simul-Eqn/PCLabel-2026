"""Grow select, geometric mode: region growing on voxel normals (after PCL), on the synthetic street."""

from dataclasses import replace

import numpy as np
import pytest

from pclabel.store.tiles import TilingParams, build_tiles
from pclabel.synth import make_street
from pclabel.tools.normals import NormalGrowParams, build_voxels, normal_grow

P = NormalGrowParams(voxel=0.05, normal_radius=0.15, connect=0.075, radius=8.0)  # the synthetic scan is sparse


@pytest.fixture(scope="module")
def scene(taxonomy):
    s = make_street(n_target=1_000_000, seed=3)
    tiles, order = build_tiles(s.xyz, TilingParams(max_points=50_000))
    return {"xyz": np.ascontiguousarray(s.xyz[order]), "tiles": tiles, "cls": s.cls[order], "tax": taxonomy}


def pick(scene, name, near):
    cls = scene["cls"] == scene["tax"].class_id(name)
    d = np.where(cls, np.linalg.norm(scene["xyz"] - np.asarray(near, np.float32), axis=1), np.inf)
    return scene["xyz"][np.argmin(d)].astype(np.float64)


def test_normals_of_a_plane_are_its_normal(scene):
    rng = np.random.default_rng(0)
    n = np.array([0.2, -0.1, 1.0])
    n /= np.linalg.norm(n)
    a = np.cross(n, [1, 0, 0])
    a /= np.linalg.norm(a)
    b = np.cross(n, a)
    pts = (rng.uniform(-2, 2, (200_000, 1)) * a + rng.uniform(-2, 2, (200_000, 1)) * b).astype(np.float32)
    tiles, order = build_tiles(pts, TilingParams(max_points=50_000))
    v = build_voxels(np.ascontiguousarray(pts[order]), tiles, np.zeros(3), replace(P, radius=2.0))
    ok = (v.keys != -1) & np.isfinite(v.curvature)
    inner = ok & np.all(np.abs(v.centroid) < 1.7, axis=1)
    assert np.percentile(np.abs(v.normal[inner] @ n), 1) > 0.999 and np.percentile(v.curvature[inner], 99) < 1e-3


def test_road_grows_and_stops_at_the_kerb_faces(scene):
    tax, cls = scene["tax"], scene["cls"]
    seed = pick(scene, "road", (-2.0, -1.0, 0.0))
    r = normal_grow(scene["xyz"], scene["tiles"], seed, P)
    sel = np.zeros(len(cls), bool)
    sel[r.indices] = True
    road = cls == tax.class_id("road")
    near = np.all(np.abs(scene["xyz"][:, :2] - seed[:2]) < P.radius - 0.2, axis=1)
    assert sel[road & near].mean() > 0.9
    for name in ("tree_trunk", "tree_canopy", "lamp_post", "grass"):
        assert (sel & (cls == tax.class_id(name))).sum() < 0.002 * sel.sum(), name
    assert r.info["refused_by_angle"] > 0 and r.info["voxels_reused"] is False
    again = normal_grow(scene["xyz"], scene["tiles"], seed, replace(P, smoothness=1.0), voxels=r.voxels)
    assert again.info["voxels_reused"] and again.indices.size < r.indices.size  # stricter: less, same voxels


def test_a_trunk_is_a_surface_of_its_own(scene):
    tax, cls = scene["tax"], scene["cls"]
    seed = pick(scene, "tree_trunk", (6.0, 7.3, 1.0))
    r = normal_grow(scene["xyz"], scene["tiles"], seed, replace(P, smoothness=15.0))
    sel_cls = cls[r.indices]
    assert (sel_cls == tax.class_id("tree_trunk")).mean() > 0.8 and (sel_cls == tax.class_id("road")).sum() == 0
