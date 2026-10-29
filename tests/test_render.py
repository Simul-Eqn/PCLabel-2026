"""Renderer on Mesa llvmpipe (CPU): colour modes, clip box, EDL, streaming within a memory budget, picking."""

import numpy as np
import pytest

from pclabel.render.camera import OrbitCamera
from pclabel.render.colormaps import cyclic_oklab, sample_repeat_linear, sequential_oklab, wrap_color
from pclabel.render.renderer import (
    BYTES_PER_POINT,
    INSIDE_BIT,
    PAGE_MAX,
    PointRenderer,
    PointSource,
    label_bytes,
    page_range,
    pages_for,
)
from pclabel.render.shaders import (
    MODE_CLASS,
    MODE_FLAT,
    MODE_HEIGHT,
    MODE_INTENSITY,
    MODE_LABELS,
    MODE_RGB,
    MODE_WRAP,
)
from pclabel.store.tiles import TilingParams, build_tiles
from pclabel.tools.pick import nearest_point

W, H = 160, 120
OFFSET = np.array([100.0, 200.0, 10.0])


def grid_source(z_of_x, n_side=300, rgb=(200, 50, 25), inten=0.5, label=1, max_points=2_000_000):
    """A dense square of points over x, y in [-5, 5] (local), height z_of_x(x) (world z = local + 10)."""
    g = np.linspace(-5, 5, n_side)
    x, y = np.meshgrid(g, g)
    x, y = x.ravel(), y.ravel()
    xyz = np.stack([x, y, z_of_x(x) - OFFSET[2]], 1).astype(np.float32)
    tiles, order = build_tiles(xyz, TilingParams(max_points=max_points))
    n = len(xyz)
    return PointSource(
        tiles=tiles,
        xyz=np.ascontiguousarray(xyz[order]),
        rgb=np.tile(np.array(rgb, np.uint8), (n, 1)),
        intensity=np.full(n, int(inten * 65535), np.uint16),
        labels=np.full(n, label, np.uint8),
        flags=np.zeros(n, np.uint8),
        offset=OFFSET.copy(),
    )


@pytest.fixture
def target(gl):
    fbo = gl.simple_framebuffer((W, H), components=4)
    yield fbo
    fbo.release()


def make(gl, src, taxonomy, **kw):
    kw.setdefault("memory_bytes", 1 << 30)
    kw.setdefault("point_budget", 10**8)
    r = PointRenderer(gl, src, taxonomy.color_table(), **kw)
    r.display.edl = False
    r.display.adaptive = False
    r.display.point_size = 3.0
    r.display.background = (0.0, 0.0, 0.0)
    r.lod = r.lod.__class__(point_budget=kw["point_budget"], spacing_px=0.1)
    return r


def bev(distance=6.0):
    cam = OrbitCamera(target=np.zeros(3), distance=distance, yaw=-90)
    cam.set_view("top")
    cam.projection = "ortho"  # exact pixel geometry for these tests
    return cam


def centre(fbo):
    img = np.frombuffer(fbo.read(components=3), np.uint8).reshape(H, W, 3)[::-1]
    return img, img[H // 2 - 10 : H // 2 + 10, W // 2 - 10 : W // 2 + 10].reshape(-1, 3)


def test_colour_modes(gl, target, taxonomy):
    src = grid_source(lambda x: np.full_like(x, 11.0))  # world z = 11
    r = make(gl, src, taxonomy)
    road = np.array(taxonomy.color_table()[1])  # label 1 in this fixture
    d = r.display
    d.height_range, d.period, d.h0, d.gamma = (10.0, 12.0), 0.3, 0.0, 1.0
    ramp = sequential_oklab(256).astype(float)
    expect = {
        MODE_RGB: np.array([200, 50, 25]),
        MODE_INTENSITY: np.array([128, 128, 128]),
        MODE_FLAT: np.round(np.array(d.flat_color) * 255),
        MODE_CLASS: road,
        MODE_HEIGHT: (ramp[127] + ramp[128]) / 2,  # t = 0.5 -> between texels 127 and 128
        MODE_WRAP: wrap_color(np.array([11.0], np.float32), 0.3, 0.0, cyclic_oklab())[0] * 255,
    }
    for mode, want in expect.items():
        d.mode = mode
        r.render(bev(), (W, H), target, wait=True)
        _, px = centre(target)
        assert np.abs(px.astype(float) - want).max() <= 2.5, (mode, px[0], want)
    d.mode = MODE_INTENSITY  # intensity stretch: 0.5 within (0.4, 0.8) -> 0.25; within (0, 0.5) -> 1.0
    for rng, want in (((0.4, 0.8), 64), ((0.0, 0.5), 255)):
        d.intensity_range = rng
        r.render(bev(), (W, H), target, wait=True)
        _, px = centre(target)
        assert np.abs(px.astype(float) - want).max() <= 2.5, (rng, px[0])
    r.close()


def test_clip_box_in_world_coordinates(gl, target, taxonomy):
    src = grid_source(lambda x: np.full_like(x, 11.0))
    r = make(gl, src, taxonomy)
    r.display.clip_on = True
    r.display.clip_min = (OFFSET[0] + 0.0, -1e9, -1e9)  # keep world x >= 100, i.e. local x >= 0
    r.render(bev(), (W, H), target, wait=True)
    img, _ = centre(target)
    lit = img.sum(2) > 0
    assert lit[:, : W // 2 - 8].mean() < 0.01 and lit[:, W // 2 + 8 :].mean() > 0.95
    r.close()


def test_edl_darkens_the_low_side_of_a_step(gl, target, taxonomy):
    src = grid_source(lambda x: np.where(x < 0, 11.0, 11.3), n_side=400)
    r = make(gl, src, taxonomy)
    r.display.mode = MODE_FLAT
    cam = bev(6.0)  # top-down: flat ground has constant depth, the step is a depth jump
    cam.update_clip(r.scene_lo, r.scene_hi)
    r.render(cam, (W, H), target, wait=True)
    plain, _ = centre(target)
    r.display.edl, r.display.edl_strength, r.display.edl_radius = True, 1.0, 2.0
    r.render(cam, (W, H), target, wait=True)
    shaded, _ = centre(target)
    step_col = int(round(cam.project(np.array([[0.0, 0.0, 11.0 - OFFSET[2]]]), (W, H))[0, 0]))
    row = H // 2
    near_step = shaded[row - 5 : row + 5, step_col - 4 : step_col].astype(float).mean()
    far_away = shaded[row - 5 : row + 5, 10:30].astype(float).mean()
    flat = plain[row - 5 : row + 5, 10:30].astype(float).mean()
    assert far_away == pytest.approx(flat, abs=3)  # flat ground is not shaded
    assert near_step < flat - 20  # the low side next to the step is
    r.close()


def test_streaming_stays_in_budget_and_keeps_prefixes(gl, target, taxonomy):
    src = grid_source(lambda x: np.full_like(x, 11.0), n_side=1200, max_points=700_000)  # 1.44M points
    budget = 2 * PAGE_MAX * BYTES_PER_POINT
    assert src.n * BYTES_PER_POINT > 2 * budget
    r = make(gl, src, taxonomy, memory_bytes=budget, point_budget=10**8)
    s = r.render(bev(), (W, H), target, wait=True)
    assert s.resident_bytes <= budget and s.memory_full and not s.converged
    for pages in r.pages:  # pages are always a prefix of the tile: page j is resident only with 0..j-1
        for j, pg in enumerate(pages[:-1]):
            lo, hi = page_range(j)
            assert pg.n == hi - lo
    # zoom onto one corner: other tiles' pages are evicted to make room, still within budget
    cam = bev(1.0)
    cam.target = np.array([4.0, 4.0, 0.0])
    s2 = r.render(cam, (W, H), target, wait=True)
    assert s2.resident_bytes <= budget and s2.drawn > 0
    # a point budget caps what is drawn
    r.lod = r.lod.__class__(point_budget=50_000, spacing_px=0.1)
    assert r.render(bev(), (W, H), target, wait=True).drawn <= 50_000
    r.close()


def test_pick_through_depth_buffer(gl, target, taxonomy):
    src = grid_source(lambda x: 11.0 + 0.1 * x)
    r = make(gl, src, taxonomy)
    cam = OrbitCamera(target=np.zeros(3), distance=9.0, yaw=-70, pitch=40)
    r.render(cam, (W, H), target, wait=True)
    want = np.array([1.0, -2.0, 11.1 - OFFSET[2]])  # a point on the surface (local coordinates)
    px, py, _ = cam.project(want, (W, H))[0]
    hit = r.depth_near(px, py, radius=3)
    assert hit is not None
    p = cam.unproject(*hit, (W, H))
    pick = nearest_point(src.xyz, src.tiles, p, search=0.25)
    assert pick is not None and np.linalg.norm(src.xyz[pick.index] - want) < 0.1
    r.close()


def test_hidden_class_is_not_drawn(gl, target, taxonomy):
    src = grid_source(lambda x: np.full_like(x, 11.0), label=1)
    r = make(gl, src, taxonomy)
    vis = np.ones(256, bool)
    vis[1] = False
    r.set_class_colors(taxonomy.color_table(), vis)
    r.render(bev(), (W, H), target, wait=True)
    img, _ = centre(target)
    assert img.sum() == 0
    r.close()


def test_page_sizes_grow_geometrically():
    sizes = [page_range(j)[1] - page_range(j)[0] for j in range(8)]
    assert sizes == [16384, 16384, 32768, 65536, 131072, 262144, 262144, 262144]
    assert list(pages_for([0, 1, 16384, 16385, 32768, 32769, 2_000_000])) == [0, 1, 1, 2, 2, 3, 12]


def test_evicted_page_below_a_pending_one_is_requested_again(gl, taxonomy):
    """Regression: page 1 of a tile waiting in the ready queue while page 0 was evicted must not stall the tile."""
    from pclabel.render.renderer import pack_page

    src = grid_source(lambda x: np.full_like(x, 11.0), n_side=300)
    r = make(gl, src, taxonomy)
    t = int(np.argmax(src.tiles["count"]))
    a = int(src.tiles["start"][t])
    lo, hi = page_range(1)
    r._ready[(t, 1)] = pack_page(src, a + lo, a + min(hi, int(src.tiles["count"][t])))
    want = np.zeros(r.n_tiles, np.int64)
    want[t] = 2
    r._request(want, np.ones(r.n_tiles))
    assert (t, 0) in r._inflight and (t, 1) not in r._inflight
    r.close()


def test_streaming_converges_without_waiting_even_with_no_upload_time(gl, target, taxonomy):
    """Regression: with the upload time slice already spent, a frame must still upload a page, or a view whose
    pages are all packed but not uploaded would never converge."""
    src = grid_source(lambda x: np.full_like(x, 11.0), n_side=800, max_points=100_000)
    r = make(gl, src, taxonomy, upload_ms=0.0)
    cam = bev()
    for _ in range(2000):
        s = r.render(cam, (W, H), target)
        if s.converged:
            break
    assert s.converged and s.resident_pages > 1
    r.close()


def test_tilted_height_frame_wrap_and_slice(gl, target, taxonomy):
    """h along a tilted plane frame: the wrap colours match the numpy mirror, and the slice keeps the plane."""
    from pclabel.analysis.frames import HeightFrame

    slope = 0.05
    src = grid_source(lambda x: 11.0 + slope * x, n_side=300)
    r = make(gl, src, taxonomy)
    n = np.array([-slope, 0.0, 1.0]) / np.hypot(slope, 1.0)
    frame = HeightFrame("tilted", tuple(n), (OFFSET[0], OFFSET[1], 11.0))
    d = r.display
    d.mode, d.period, d.h0, d.point_size = MODE_WRAP, 0.3, 0.0, 3.0
    d.height_normal, d.height_origin = frame.normal, frame.origin
    r.render(bev(), (W, H), target, wait=True)
    _, px = centre(target)
    h = frame.heights(src.xyz, OFFSET)
    assert np.abs(h).max() < 1e-4  # every point lies on the plane
    want = wrap_color(np.zeros(1, np.float32), 0.3, 0.0, cyclic_oklab())[0] * 255
    assert np.abs(px.astype(float) - want).max() <= 2.5
    # slice of +-1 cm: the plane frame keeps everything, the z frame only a strip around world z = 11
    d.band_on, d.band = True, (-0.01, 0.01)
    r.render(bev(), (W, H), target, wait=True)
    img, _ = centre(target)
    assert (img.sum(2) > 0).mean() > 0.9
    d.height_normal, d.height_origin = (0.0, 0.0, 1.0), (0.0, 0.0, 0.0)  # z frame: h = world z
    d.band = (10.99, 11.01)
    r.render(bev(), (W, H), target, wait=True)
    img, _ = centre(target)
    lit = img.sum(2) > 0
    assert 0 < lit.mean() < 0.1 and lit[:, W // 2 - 3 : W // 2 + 3].mean() > 0.5  # the strip at x = 0
    r.close()


def test_selection_overlay_is_drawn(gl, target, taxonomy):
    src = grid_source(lambda x: np.full_like(x, 11.0))
    r = make(gl, src, taxonomy)
    r.display.mode = MODE_FLAT
    left = src.xyz[:, 0] < 0
    r.render(bev(), (W, H), target, wait=True)  # pages are resident before the selection: rewritten in place
    r.set_selection(np.flatnonzero(left))
    r.render(bev(), (W, H), target, wait=True)
    img, _ = centre(target)
    lc, rc = img[:, 10:40].reshape(-1, 3).mean(0), img[:, W - 40 : W - 10].reshape(-1, 3).mean(0)
    assert lc[2] < rc[2] - 40  # tinted yellow: less blue than the flat grey on the unselected side
    # every drawn selected point is tinted, none of the others (no subsampled overlay)
    assert (img[:, 10:40, 2] < 120).mean() > 0.95 and (img[:, W - 40 : W - 10, 2] > 150).mean() > 0.95
    r.set_selection(None)
    r.render(bev(), (W, H), target, wait=True)
    img, _ = centre(target)
    assert abs(img[:, 10:40].reshape(-1, 3).mean(0)[2] - rc[2]) < 5  # cleared
    r.close()


def test_pages_packed_later_carry_the_selection(gl, target, taxonomy):
    src = grid_source(lambda x: np.full_like(x, 11.0))
    r = make(gl, src, taxonomy)
    r.display.mode = MODE_FLAT
    r.set_selection(np.flatnonzero(src.xyz[:, 0] < 0))  # before any page exists
    r.render(bev(), (W, H), target, wait=True)
    img, _ = centre(target)
    assert (img[:, 10:40, 2] < 120).mean() > 0.95 and (img[:, W - 40 : W - 10, 2] > 150).mean() > 0.95
    r.close()


def test_labels_view(gl, target, taxonomy):
    """The labels mode: annotation label codes (fourth label byte) in their colours, later layers over earlier
    ones; the rest dim grey, or hidden with labels_only; codes set before and after the pages exist."""
    src = grid_source(lambda x: np.full_like(x, 11.0))
    r = make(gl, src, taxonomy)
    r.display.mode = MODE_LABELS
    table = np.zeros((256, 4), np.uint8)
    table[1] = (0, 0, 255, 255)
    table[2] = (255, 0, 0, 255)
    r.set_label_colors(table)
    left = np.flatnonzero(src.xyz[:, 0] < 0)
    r.set_label_layers([(left, 1)])  # before any page exists
    r.render(bev(), (W, H), target, wait=True)
    img, _ = centre(target)
    assert (img[:, 10:40, 2] > 200).mean() > 0.95 and (img[:, 10:40, 0] < 30).mean() > 0.95
    rest = img[:, W - 40 : W - 10].astype(int)
    assert (np.abs(rest[..., 0] - rest[..., 1]) < 3).mean() > 0.95 and (rest.sum(2) > 0).mean() > 0.95  # grey
    band = np.flatnonzero(np.abs(src.xyz[:, 0]) < 1.0)
    r.set_label_layers([(left, 1), (band, 2)])  # resident pages rewritten; the later layer over the earlier
    r.render(bev(), (W, H), target, wait=True)
    img, _ = centre(target)
    assert (img[H // 2, W // 2 - 4 : W // 2 + 4, 0] > 200).all() and (img[:, 10:40, 2] > 200).mean() > 0.95
    lab = label_bytes(src, 0, 2000, r.marks, r.label_layers)
    assert np.array_equal(lab[:, 3] == 2, np.abs(src.xyz[:2000, 0]) < 1.0)
    r.labels_only = True
    r.render(bev(), (W, H), target, wait=True)
    img, _ = centre(target)
    assert (img.sum(2) > 0)[:, W - 40 : W - 10].mean() < 0.01 and (img[:, 10:40, 2] > 200).mean() > 0.95
    table[1, 3] = 0  # a hidden code shows as unlabelled (hidden here)
    r.set_label_colors(table)
    r.render(bev(), (W, H), target, wait=True)
    img, _ = centre(target)
    assert (img.sum(2) > 0)[:, 10:40].mean() < 0.01
    r.close()


def test_clip_to_a_point_set(gl, target, taxonomy):
    src = grid_source(lambda x: np.full_like(x, 11.0))
    r = make(gl, src, taxonomy)
    r.render(bev(), (W, H), target, wait=True)  # pages resident first: their label bytes are rewritten in place
    right = np.flatnonzero(src.xyz[:, 0] >= 0)
    r.set_marks(INSIDE_BIT, right)
    r.display.clip_set_on = True
    r.render(bev(), (W, H), target, wait=True)
    img, _ = centre(target)
    lit = img.sum(2) > 0
    assert lit[:, : W // 2 - 8].mean() < 0.01 and lit[:, W // 2 + 8 :].mean() > 0.95
    r.display.clip_set_on = False  # off: everything again, the set kept
    r.render(bev(), (W, H), target, wait=True)
    img, _ = centre(target)
    assert (img.sum(2) > 0)[:, : W // 2 - 8].mean() > 0.95
    lab = label_bytes(src, 0, 1000, r.marks)  # the set's bit in the third byte, apart from the flags
    assert lab.shape == (1000, 4) and np.array_equal(lab[:, 2] == 1, src.xyz[:1000, 0] >= 0) and not lab[:, 1].any()
    r.close()
