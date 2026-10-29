"""The label map (tools/labelmap.py): labels projected onto a plane, smaller sets over larger, lines on top."""

import numpy as np
import pytest

from pclabel.tools.labelmap import CONTEXT, LabelMapParams, label_map

OFFSET = np.array([1000.0, 2000.0, 30.0])
COLORS = np.zeros((256, 4), np.uint8)
COLORS[1] = (90, 90, 230, 255)  # road
COLORS[2] = (255, 255, 255, 255)  # marking
COLORS[3] = (255, 60, 50, 255)  # curb line


def street(tilt_deg=0.0, seed=0):
    """A 10 x 4 m road (z = 0 world, tilted about x), a 0.15 m wide marking down its middle, a pole 2 m tall."""
    rng = np.random.default_rng(seed)
    road = np.c_[rng.uniform(0, 10, 200_000), rng.uniform(0, 4, 200_000)]
    mark = np.c_[rng.uniform(1, 9, 20_000), rng.uniform(1.925, 2.075, 20_000)]
    pole = np.c_[np.full(2_000, 5.0), np.full(2_000, 3.5), np.linspace(0.1, 2.0, 2_000)]
    t = np.radians(tilt_deg)
    xy = np.vstack([road, mark])
    z = np.r_[np.zeros(len(xy))]
    pts = np.vstack([np.c_[xy, z], pole])
    rot = np.array([[1, 0, 0], [0, np.cos(t), -np.sin(t)], [0, np.sin(t), np.cos(t)]])
    world = pts @ rot.T + OFFSET
    normal = rot @ [0.0, 0.0, 1.0]
    return (world - OFFSET).astype(np.float32), rot, normal


@pytest.mark.parametrize("tilt", [0.0, 12.0])
def test_map_of_a_street(tilt):
    xyz, rot, normal = street(tilt)
    road, mark, pole = np.arange(200_000), np.arange(200_000, 220_000), np.arange(220_000, 222_000)
    kerb = (np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]]) @ rot.T) + OFFSET
    p = LabelMapParams(pixel=0.05, margin=0.5, line_width=3.0)
    res = label_map(xyz, OFFSET, normal, OFFSET, rot @ [1.0, 0, 0], [(road, 1), (mark, 2)], [(kerb, 3)], COLORS, p)
    t, codes = res["transform"], res["codes"]
    assert (t["width"], t["height"]) == (int(np.ceil(11 / 0.05)), int(np.ceil(5 / 0.05)))
    assert res["rgba"].shape == (t["height"], t["width"], 4) and res["rgba"][0, 0, 3] == 0  # outside: transparent

    def at(x, y):  # the code at map metres (x, y) from the plane origin
        return codes[int((t["top_left"][1] - y) / p.pixel), int((x - t["top_left"][0]) / p.pixel)]

    assert at(5.0, 2.0) == 2 and at(5.0, 1.0) == 1 and at(5.0, 3.0) == 1  # the marking shows over the road
    assert at(5.0, 0.0) == 3 and at(0.5, 0.02) == 3  # the kerb line on top, along the road's edge
    assert at(10.4, 2.0) == 0  # the margin
    # the transform takes a pixel back to the world: the pixel holding the marking's centre lies on the marking
    r, c = int((t["top_left"][1] - 2.0) / p.pixel), int((5.0 - t["top_left"][0]) / p.pixel)
    w = np.array(t["plane_origin"]) + (t["top_left"][0] + (c + 0.5) * p.pixel) * np.array(t["map_x"]) + \
        (t["top_left"][1] - (r + 0.5) * p.pixel) * np.array(t["map_y"])
    assert np.allclose(np.linalg.inv(rot) @ (w - OFFSET), [5.0, 2.0, 0.0], atol=p.pixel)
    assert res["points"] == {1: 200_000, 2: 20_000} and res["pixels"][2] < res["pixels"][1]
    assert np.allclose(res["vectors_uv"][0], [[0, 0], [10, 0]], atol=1e-9)


def test_height_range_dots_context_and_limits():
    xyz, rot, normal = street()
    pole = np.arange(220_000, 222_000)
    road = np.arange(200_000)
    # only the pole above 1 m: the road (h = 0) and the pole's foot are left out
    p = LabelMapParams(pixel=0.05, margin=0.2, h_min=1.0, h_max=10.0)
    res = label_map(xyz, OFFSET, normal, OFFSET, (1, 0, 0), [(road, 1), (pole, 3)], [], COLORS, p)
    assert res["points"] == {1: 0, 3: int((xyz[pole, 2].astype(np.float64) >= 1.0).sum())} and set(res["pixels"]) == {3}
    # points grow by a pixel: a 3 x 3 block
    p = LabelMapParams(pixel=0.05, margin=0.2, dot=1, h_min=1.0)
    assert label_map(xyz, OFFSET, normal, OFFSET, (1, 0, 0), [(pole, 3)], [], COLORS, p)["pixels"][3] == 9
    # context: every point (an int) in grey underneath, the extent still the labels'
    p = LabelMapParams(pixel=0.05, margin=0.0)
    res = label_map(xyz, OFFSET, normal, OFFSET, (1, 0, 0), [(pole, 3)], [], COLORS, p, context=len(xyz))
    assert res["codes"].shape == (1, 1) and res["pixels"] == {3: 1} and res["points"][CONTEXT] > 0
    with pytest.raises(ValueError, match="nothing to draw"):
        label_map(xyz, OFFSET, normal, OFFSET, (1, 0, 0), [(pole, 3)], [], COLORS, LabelMapParams(h_min=5.0))
    with pytest.raises(ValueError, match="larger pixels"):
        label_map(xyz, OFFSET, normal, OFFSET, (1, 0, 0), [(road, 1)], [], COLORS, LabelMapParams(pixel=0.0001))
    with pytest.raises(ValueError, match="cancelled"):
        label_map(xyz, OFFSET, normal, OFFSET, (1, 0, 0), [(road, 1)], [], COLORS, progress=lambda f: False)
