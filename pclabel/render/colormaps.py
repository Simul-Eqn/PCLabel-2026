"""Colormaps for the shaders, and numpy mirrors of the shader formulas (unit-tested; SPEC §2.1)."""

from __future__ import annotations

import numpy as np


def _oklab_to_linear_srgb(L, a, b):
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_**3, m_**3, s_**3
    return np.stack(
        [
            4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
        ],
        -1,
    )


def _encode_srgb(x):
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def cyclic_oklab(n: int = 256, lightness: float = 0.72, chroma: float = 0.12, hue0_deg: float = 0.0) -> np.ndarray:
    """A perceptually uniform cyclic map: a circle of constant lightness and chroma in OKLab. uint8 (n, 3).

    Constant lightness leaves lightness free for the coarse period of the multi-period wrap (M2).
    """
    h = np.deg2rad(hue0_deg) + 2 * np.pi * np.arange(n) / n
    lin = _oklab_to_linear_srgb(np.full(n, lightness), chroma * np.cos(h), chroma * np.sin(h))
    return (np.round(_encode_srgb(lin) * 255)).astype(np.uint8)


def wrap_phase(h, period: float, h0: float = 0.0):
    """Shader: ``phi = fract((h - h0) / P)``. GLSL fract(x) = x - floor(x), which equals numpy mod(x, 1)."""
    x = (np.asarray(h, dtype=np.float32) - np.float32(h0)) / np.float32(period)
    return x - np.floor(x)


def sample_repeat_linear(cmap: np.ndarray, u) -> np.ndarray:
    """GL ``texture()`` on an n x 1 texture with LINEAR filtering and REPEAT wrap, at coordinate u. float (.., 3)."""
    n = cmap.shape[0]
    t = np.asarray(u, dtype=np.float64) * n - 0.5
    i0 = np.floor(t).astype(np.int64)
    f = (t - i0)[..., None]
    c = cmap.astype(np.float64) / 255.0
    return c[i0 % n] * (1 - f) + c[(i0 + 1) % n] * f


def wrap_color(h, period: float, h0: float, cmap: np.ndarray) -> np.ndarray:
    """Height-wrap colour exactly as the point shader computes it (before shading)."""
    return sample_repeat_linear(cmap, wrap_phase(h, period, h0))


def sequential_oklab(n: int = 256) -> np.ndarray:
    """Perceptually ordered ramp for the height mode: lightness rises monotonically from dark blue to light
    yellow while the hue turns (a viridis-like path in OKLab). uint8 (n, 3)."""
    t = np.linspace(0.0, 1.0, n)
    L = 0.27 + 0.68 * t
    chroma = 0.10 + 0.06 * np.sin(np.pi * t)
    h = np.deg2rad(275.0 - 175.0 * t)
    lin = _oklab_to_linear_srgb(L, chroma * np.cos(h), chroma * np.sin(h))
    return (np.round(_encode_srgb(lin) * 255)).astype(np.uint8)
