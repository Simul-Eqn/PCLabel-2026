import numpy as np
import pytest

from pclabel.render.colormaps import cyclic_oklab, sample_repeat_linear, wrap_color, wrap_phase


def test_phase_band_edges_and_period():
    P, h0 = 0.25, 0.5  # exactly representable, so band edges land exactly on 0
    k = np.arange(-8, 9)
    assert np.all(wrap_phase(h0 + k * P, P, h0) == 0.0)
    h = np.linspace(-1, 1, 1001)
    a, b = wrap_phase(h, 0.03), wrap_phase(h + 0.03, 0.03)
    d = np.abs(a - b)
    assert np.all((d < 1e-4) | (d > 1 - 1e-4))
    assert np.all((wrap_phase(h, 0.03) >= 0) & (wrap_phase(h, 0.03) < 1))
    assert wrap_phase(np.float32(-0.01), 0.03) == pytest.approx(2 / 3, abs=1e-5)


def test_cyclic_map_is_seamless():
    c = cyclic_oklab(256).astype(np.float64)
    steps = np.linalg.norm(np.diff(np.vstack([c, c[:1]]), axis=0), axis=1)
    assert steps.max() < 3 * np.median(steps)  # the seam is no bigger than any other step
    assert len({tuple(v) for v in c.astype(int)}) > 200


def test_texture_sampling_mirror():
    c = cyclic_oklab(256)
    i = np.arange(256)
    assert np.allclose(sample_repeat_linear(c, (i + 0.5) / 256), c / 255.0)
    assert np.allclose(sample_repeat_linear(c, 0.0), (c[0] / 255.0 + c[-1] / 255.0) / 2)


def test_shader_matches_numpy_mirror(gl, taxonomy):
    """Render 4096 points at random heights (one per pixel, BEV) through the real renderer; each pixel must equal
    the numpy mirror of the wrap formula."""
    from pclabel.render.camera import OrbitCamera
    from pclabel.render.renderer import PointRenderer, PointSource
    from pclabel.render.shaders import MODE_WRAP
    from pclabel.store.tiles import TilingParams, build_tiles

    W = H = 64
    step = 0.1
    i, j = np.meshgrid(np.arange(W), np.arange(H))
    x = -W * step / 2 + step * (i.ravel() + 0.5)
    y = H * step / 2 - step * (j.ravel() + 0.5)
    rng = np.random.default_rng(0)
    h = rng.uniform(-0.2, 0.2, W * H)
    offset = np.array([0.0, 0.0, 30.0])
    xyz = np.stack([x, y, h - offset[2]], 1).astype(np.float32)
    tiles, order = build_tiles(xyz, TilingParams())
    n = len(xyz)
    src = PointSource(tiles, np.ascontiguousarray(xyz[order]), np.zeros((n, 3), np.uint8), np.zeros(n, np.uint16),
                      np.zeros(n, np.uint8), np.zeros(n, np.uint8), offset)  # fmt: skip
    r = PointRenderer(gl, src, taxonomy.color_table(), memory_bytes=1 << 28, point_budget=10**7)
    r.lod = r.lod.__class__(point_budget=10**7, spacing_px=0.01)
    d = r.display
    d.mode, d.period, d.h0, d.edl, d.adaptive, d.point_size = MODE_WRAP, 0.03, 0.004, False, False, 1.0
    cam = OrbitCamera(target=np.zeros(3), distance=W * step / 2 / np.tan(np.deg2rad(25)), yaw=-90)
    cam.set_view("top")
    cam.projection = "ortho"
    fbo = gl.simple_framebuffer((W, H), components=4)
    r.render(cam, (W, H), fbo, wait=True)
    img = np.frombuffer(fbo.read(components=3), np.uint8).reshape(H, W, 3)[::-1].reshape(-1, 3) / 255.0
    world_h = xyz[:, 2] + np.float32(offset[2])  # what the shader sees: store z + u_zoffset (float32)
    expect = wrap_color(world_h, 0.03, 0.004, cyclic_oklab())
    # texture filtering uses 8-bit weights; allow 2 levels
    assert np.abs(img - expect).max() < 2.5 / 255
    r.close()
    fbo.release()
