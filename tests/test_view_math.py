"""Camera, LOD and picking (no GL)."""

import numpy as np
import pytest

from pclabel.render.camera import OrbitCamera
from pclabel.render.lod import LodParams, lod_counts
from pclabel.store.tiles import TilingParams, build_tiles
from pclabel.tools.pick import nearest_point

VP = (800, 600)


@pytest.mark.parametrize("view", ["perspective", "top", "bottom", "front", "side", "top ortho", "side ortho"])
def test_project_unproject_round_trip(view):
    cam = OrbitCamera(target=np.array([10.0, -5.0, 2.0]), distance=30, yaw=33, pitch=25)
    cam.set_view(view.split()[0])
    if view.endswith("ortho"):
        cam.projection = "ortho"
    cam.update_clip([-50, -50, -10], [50, 50, 30])
    rng = np.random.default_rng(0)
    p = cam.target + rng.uniform(-5, 5, (50, 3))
    s = cam.project(p, VP)
    back = np.array([cam.unproject(x, y, d, VP) for x, y, d in s])
    assert np.abs(back - p).max() < 1e-3


def test_preset_views_are_perspective_angles_along_their_axes():
    cam = OrbitCamera(target=np.array([1.0, 2.0, 3.0]), distance=20, yaw=10)
    cam.update_clip([-50, -50, -10], [50, 50, 30])
    for view, axis, sign in (("top", 2, -1), ("bottom", 2, 1), ("front", 1, 1), ("side", 0, -1)):
        cam.set_view(view)
        assert cam.projection == "perspective" and cam.view_name() == view
        f = cam.axes()[2]
        assert np.isclose(f[axis], sign), view  # looking down, up, along +y, along -x
        b = cam.target.copy()
        b[axis] += 3.0  # points on the viewing axis land on the screen centre
        s = cam.project(np.stack([cam.target, b]), VP)
        assert np.allclose(s[:, :2], (400, 300), atol=1e-6), view
    cam.orbit(5, -3)
    assert cam.view_name() == "free"


def test_top_keeps_heading_and_target_is_centred():
    cam = OrbitCamera(target=np.array([3.0, 4.0, 0.0]), distance=10, yaw=37)
    cam.set_view("top")
    cam.update_clip([-50, -50, -10], [50, 50, 30])
    assert cam.yaw == 37 and np.allclose(cam.project(cam.target, VP)[0, :2], (400, 300))
    r, u, f = cam.axes()
    assert np.allclose(f, (0, 0, -1)) and abs(r[2]) < 1e-12 and np.isclose(np.linalg.norm(r), 1)
    cam.orbit(0, -200)  # dragging past the bottom stops at looking straight up
    assert cam.pitch == -90.0 and cam.view_name() == "bottom"


def test_pivot_keeps_the_eye():
    cam = OrbitCamera(target=np.zeros(3), distance=20, yaw=40, pitch=30)
    eye = cam.eye()
    cam.set_pivot([3.0, -2.0, 1.0])
    assert np.allclose(cam.eye(), eye) and np.allclose(cam.target, (3, -2, 1))


def test_fly():
    cam = OrbitCamera(target=np.zeros(3), distance=5, yaw=0, pitch=20)
    eye = cam.eye()
    cam.fly_look(30, -5)
    assert np.allclose(cam.eye(), eye)
    e0 = cam.eye()
    cam.fly_move(0, 0, 2.0)  # forward, horizontally
    d = cam.eye() - e0
    assert np.isclose(np.linalg.norm(d), 2.0) and abs(d[2]) < 1e-12
    cam.fly_move(0, 1.0, 0)
    assert np.isclose(cam.eye()[2] - e0[2], 1.0)


def test_lod_budget_and_area():
    # 3 x 3 tiles of 1M points on a 30 m square, camera above the middle
    lo = np.array([[x, y, 0.0] for x in (0, 10, 20) for y in (0, 10, 20)])
    hi = lo + (10, 10, 1)
    counts = np.full(9, 1_000_000)
    cam = OrbitCamera(target=np.array([15.0, 15.0, 0.0]), distance=40, yaw=-90, pitch=50)
    cam.update_clip(lo.min(0), hi.max(0))
    mvp = cam.mvp(VP[0] / VP[1])
    k, area = lod_counts(counts, lo, hi, mvp, VP, LodParams(point_budget=2_000_000, spacing_px=1.0))
    assert k.sum() <= 2_000_000 and np.all(k <= counts) and np.all(k > 0)
    assert np.corrcoef(k, area)[0, 1] > 0.99  # proportional to screen area once scaled to the budget
    k2, area2 = lod_counts(counts, lo, hi, mvp, VP, LodParams(point_budget=10**9, spacing_px=2.0))
    assert np.all(k2 == np.minimum(counts, np.floor(np.maximum(area2, 1) / 4)))
    cam.yaw += 180  # tiles now behind the camera's back? move the target far away instead
    cam.target = np.array([500.0, 500.0, 0.0])
    cam.update_clip(lo.min(0), np.array([600, 600, 1.0]))
    k3, _ = lod_counts(counts, lo, hi, cam.mvp(VP[0] / VP[1]), VP, LodParams(point_budget=10**9))
    assert k3.sum() == 0


def test_pick_matches_brute_force(street):
    xyz = street.xyz
    tiles, order = build_tiles(xyz, TilingParams(max_points=30_000))
    store = np.ascontiguousarray(xyz[order])
    rng = np.random.default_rng(1)
    for i in rng.choice(len(store), 20, replace=False):
        q = store[i].astype(np.float64) + rng.normal(0, 0.01, 3)
        res = nearest_point(store, tiles, q, search=0.25)
        d = np.linalg.norm(store.astype(np.float64) - q, axis=1)
        assert res is not None and np.isclose(res.distance, d.min())
        dp = np.linalg.norm(store.astype(np.float64) - store[res.index].astype(np.float64), axis=1)
        assert res.within[0.05] == (dp <= 0.05).sum() and res.within[0.10] == (dp <= 0.10).sum()  # around the pick
        assert tiles["start"][res.tile] <= res.index < tiles["start"][res.tile] + tiles["count"][res.tile]
    assert nearest_point(store, tiles, [1e4, 1e4, 0]) is None


def test_pivot_on_axis_does_not_move_the_picture():
    cam = OrbitCamera(target=np.array([5.0, 2.0, 0.0]), distance=40, yaw=20, pitch=30)
    cam.update_clip([-50, -50, -10], [50, 50, 30])
    before = cam.mvp(VP[0] / VP[1])
    p = cam.target + np.array([3.0, -4.0, 1.0])  # any point; only its depth along the view axis matters
    assert cam.pivot_on_axis(p)
    assert np.allclose(cam.mvp(VP[0] / VP[1]), before, atol=1e-9)
    centre_world = cam.target.copy()
    cam.orbit(25, -10)  # the pivot (now the target) stays at the centre of the screen
    assert np.allclose(cam.project(centre_world, VP)[0, :2], (400, 300), atol=1e-6)


def test_reset_view():
    cam = OrbitCamera(yaw=123, pitch=-20)
    cam.set_view("side")
    cam.reset([0, 0, 0], [10, 10, 2])
    assert cam.projection == "perspective" and (cam.yaw, cam.pitch) == (-60.0, 35.0)
    assert np.allclose(cam.target, (5, 5, 1))
