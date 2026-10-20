import numpy as np

from pclabel.render.camera import OrbitCamera
from pclabel.tools.selection import DepthTest, rasterize_polygon, select_in_mask


def star(cx, cy, r0, r1, n, rng):
    a = np.sort(rng.uniform(0, 2 * np.pi, n))
    r = np.where(np.arange(n) % 2 == 0, r0, r1) * rng.uniform(0.8, 1.2, n)
    return np.stack([cx + r * np.cos(a), cy + r * np.sin(a)], 1)


def even_odd(poly, px, py):
    inside = np.zeros(px.shape, bool)
    m = len(poly)
    for e in range(m):
        x0, y0 = poly[e]
        x1, y1 = poly[(e + 1) % m]
        crosses = ((y0 <= py) & (py < y1)) | ((y1 <= py) & (py < y0))
        with np.errstate(divide="ignore", invalid="ignore"):
            xi = x0 + (py - y0) * (x1 - x0) / (y1 - y0)
        inside ^= crosses & (px < xi)
    return inside


def test_rasterize_matches_even_odd_reference():
    rng = np.random.default_rng(0)
    W, H = 320, 200
    py, px = np.mgrid[0:H, 0:W] + 0.5
    for poly in (star(160, 100, 90, 30, 40, rng), rng.uniform(-20, 340, (12, 2))):  # second self-intersects
        mask = rasterize_polygon(poly, W, H)
        assert np.array_equal(mask.astype(bool), even_odd(poly, px, py))


def project(xyz, mvp, W, H):
    p = np.c_[xyz.astype(np.float64), np.ones(len(xyz))] @ mvp.T
    w = p[:, 3]
    with np.errstate(divide="ignore", invalid="ignore"):
        nx, ny = p[:, 0] / w, p[:, 1] / w
    fx, fy = (nx + 1) * 0.5 * W, (1 - ny) * 0.5 * H
    ok = (w > 0) & (np.abs(nx) <= 1) & (np.abs(ny) <= 1)
    return ok, fx, fy, w


def test_select_matches_reference():
    rng = np.random.default_rng(1)
    W, H = 400, 300
    xyz = rng.uniform(-30, 30, (300_000, 3)).astype(np.float32)
    cam = OrbitCamera(distance=60, yaw=20, pitch=30)
    mvp = cam.mvp(W / H)
    mask = rasterize_polygon(star(200, 150, 140, 50, 30, rng), W, H)
    got = select_in_mask(xyz, mvp, mask, chunk=4096)
    assert np.all(np.diff(got.astype(np.int64)) > 0)

    ok, fx, fy, _ = project(xyz, mvp, W, H)
    ix = np.clip(fx.astype(np.int64), 0, W - 1)
    iy = np.clip(fy.astype(np.int64), 0, H - 1)
    ref = np.flatnonzero(ok & (mask[iy, ix] > 0))
    diff = np.setxor1d(got, ref)
    # float32 kernel vs float64 reference may only disagree for points on a pixel or frustum edge
    edge = (np.abs(fx[diff] - np.round(fx[diff])) < 1e-3) | (np.abs(fy[diff] - np.round(fy[diff])) < 1e-3)
    assert np.all(edge)
    assert len(ref) > 10_000


def test_depth_test_keeps_front_drops_hidden():
    W, H = 200, 150
    cam = OrbitCamera(target=np.zeros(3), distance=30, yaw=0, pitch=89.9, near=0.5, far=100)
    mvp = cam.mvp(W / H)
    rng = np.random.default_rng(2)
    back = np.c_[rng.uniform(-8, 8, (200_000, 2)), np.zeros(200_000)]
    front = np.c_[rng.uniform(-2, 2, (50_000, 2)), np.full(50_000, 10.0)]  # 10 m closer to the camera
    xyz = np.vstack([back, front]).astype(np.float32)
    ok, fx, fy, w = project(xyz, mvp, W, H)
    ix = np.clip(fx.astype(np.int64), 0, W - 1)
    iy = np.clip(fy.astype(np.int64), 0, H - 1)
    # depth buffer = nearest point per pixel (what a 1 px point render would leave), as window depth
    n, f = cam.near, cam.far
    zw = (f + n) / (f - n) - 2 * f * n / ((f - n) * w)  # ndc z
    depth = np.ones((H, W))
    np.minimum.at(depth, (iy[ok], ix[ok]), (zw[ok] + 1) / 2)
    mask = np.ones((H, W), np.uint8)
    got = select_in_mask(xyz, mvp, mask, DepthTest(depth.astype(np.float32), n, f))
    sel = np.zeros(len(xyz), bool)
    sel[got] = True
    covered = np.zeros((H, W), bool)
    covered[iy[200_000:], ix[200_000:]] = True
    is_back = np.arange(len(xyz)) < 200_000
    assert np.all(sel[~is_back])  # every front point is visible
    assert not np.any(sel[is_back & covered[iy, ix]])  # back points under the front plane are hidden
    assert sel[is_back & ~covered[iy, ix] & ok].mean() > 0.99


def test_select_visible_matches_kernel_and_filters(street):
    """Tile culling must not lose points: select_visible == the kernel over every point; filters drop the rest."""
    from pclabel.store.tiles import TilingParams, build_tiles
    from pclabel.tools.selection import SelectFilters, select_visible

    tiles, order = build_tiles(street.xyz, TilingParams(max_points=20_000))
    xyz = np.ascontiguousarray(street.xyz[order])
    W, H = 640, 480
    cam = OrbitCamera(target=np.array([0.0, 0.0, 0.0]), distance=25, yaw=-80, pitch=50)
    cam.update_clip(xyz.min(0), xyz.max(0))
    mvp = cam.mvp(W / H)
    rng = np.random.default_rng(3)
    poly = star(320, 240, 150, 60, 24, rng)
    got = select_visible(xyz, tiles, mvp, (W, H), poly)
    ref = select_in_mask(xyz, mvp, rasterize_polygon(poly, W, H)).astype(np.int64)
    assert len(got) > 1000 and np.array_equal(got, ref)
    # a height slice along a tilted axis, and a clip box
    n = np.array([0.02, -0.01, 1.0])
    n /= np.linalg.norm(n)
    f = SelectFilters(clip_min=(-5.0, -20.0, -5.0), clip_max=(20.0, 20.0, 5.0), band_normal=tuple(n),
                      band_origin=(0.0, 0.0, 0.0), band=(-0.05, 0.1))  # fmt: skip
    got_f = select_visible(xyz, tiles, mvp, (W, H), poly, filters=f)
    q = xyz[ref].astype(np.float64)
    h = q @ n
    keep = (h >= -0.05) & (h <= 0.1) & np.all((q >= (-5, -20, -5)) & (q <= (20, 20, 5)), axis=1)
    assert np.array_equal(got_f, ref[keep]) and 0 < len(got_f) < len(ref)


def test_depth_dilation_closes_gaps_between_drawn_points():
    """Hidden points must not leak through gaps in a sparsely drawn front surface."""
    from pclabel.store.tiles import TilingParams, build_tiles
    from pclabel.tools.selection import select_visible

    W, H = 200, 150
    cam = OrbitCamera(target=np.zeros(3), distance=30, yaw=0, pitch=89.9, near=0.5, far=100)
    mvp = cam.mvp(W / H)
    rng = np.random.default_rng(4)
    back = np.c_[rng.uniform(-8, 8, (200_000, 2)), np.zeros(200_000)]
    front = np.c_[rng.uniform(-2, 2, (60_000, 2)), np.full(60_000, 10.0)]
    xyz = np.vstack([back, front]).astype(np.float32)
    # the depth buffer holds only a sparse LOD of the front plane (1 point in 200), as when zoomed out
    drawn = front[::200]
    ok, fx, fy, w = project(drawn.astype(np.float32), mvp, W, H)
    n, f = cam.near, cam.far
    zw = ((f + n) / (f - n) - 2 * f * n / ((f - n) * w) + 1) / 2
    depth = np.ones((H, W))
    np.minimum.at(depth, (np.clip(fy.astype(int), 0, H - 1)[ok], np.clip(fx.astype(int), 0, W - 1)[ok]), zw[ok])
    tiles, order = build_tiles(xyz, TilingParams(max_points=50_000))
    store = np.ascontiguousarray(xyz[order])
    # the front square (4 m at 20 m from the eye, 8 px/m) covers about x 84..116, y 59..91: stay inside it
    poly = np.array([[90.0, 65.0], [110.0, 65.0], [110.0, 85.0], [90.0, 85.0]])
    test = DepthTest(depth.astype(np.float32), n, f)
    leaky = select_visible(store, tiles, mvp, (W, H), poly, test, dilate_px=0)
    tight = select_visible(store, tiles, mvp, (W, H), poly, test, dilate_px=3)
    is_back = order[tight] < 200_000
    assert (order[leaky] < 200_000).sum() > 100  # without dilation, back points show through the gaps
    assert is_back.sum() == 0 and len(tight) > 1000


def test_sorted_set_operations_match_numpy():
    from pclabel.tools import sets

    rng = np.random.default_rng(7)
    for na, nb in ((0, 5), (5, 0), (1000, 800), (50_000, 70_000)):
        a = np.unique(rng.integers(0, 3 * max(na, 1), na))
        b = np.unique(rng.integers(0, 3 * max(nb, 1), nb))
        assert np.array_equal(sets.union(a, b), np.union1d(a, b))
        assert np.array_equal(sets.difference(a, b), np.setdiff1d(a, b))
        assert np.array_equal(sets.xor(a, b), np.setxor1d(a, b))
        assert np.array_equal(sets.intersection(a, b), np.intersect1d(a, b))
