"""Joining pieces of line (tools/linejoin.py): repeats dropped, gaps within reach joined, loops closed."""

import numpy as np
import pytest

from pclabel.tools.linejoin import JoinParams, join_lines


def line(x0, x1, y=0.0, step=0.3):
    x = np.arange(x0, x1 + 1e-9, step)
    return np.c_[x, np.full(len(x), y), np.zeros(len(x))]


def test_pieces_and_a_retry_become_one_line():
    a = line(0, 6)  # the first try
    b = line(4.5, 12, y=0.02)[::-1]  # a retry over 4.5..6 (2 cm off), drawn the other way, on to 12
    c = line(12.6, 20)  # after a 0.6 m gap (a drain)
    far = line(0, 5, y=3.0)  # another kerb across the road
    out = join_lines([a, b, c, far])
    assert len(out) == 2
    main = out[0]
    assert main["sources"] == [0, 1, 2] and not main["closed"]
    xs = main["vertices"][:, 0]
    assert xs.min() == pytest.approx(0) and xs.max() == pytest.approx(19.8, abs=0.31)
    assert np.all(np.diff(xs) > 0) or np.all(np.diff(xs) < 0)  # in one direction, no doubling back
    assert len(main["bridged"]) == 1 and main["bridged"][0] == pytest.approx(0.6, abs=0.31)
    assert main["dropped"][0] >= 4  # the overlap 4.5..6 is dropped from the shorter try (the longer is kept whole)
    # a gap longer than join_gap stays a gap; so does a join that would turn back
    assert len(join_lines([a, line(8, 12)], p=JoinParams(join_gap=1.0))) == 2
    side = np.c_[np.full(10, 6.3), np.arange(10) * 0.3 + 0.3, np.zeros(10)]  # starts beside a's end, going sideways
    assert len(join_lines([a, side], p=JoinParams(max_angle=30))) == 2


def test_a_loop_is_closed():
    t = np.linspace(0, 2 * np.pi, 60, endpoint=False)
    ring = np.c_[3 * np.cos(t), 3 * np.sin(t), np.zeros(60)]
    out = join_lines([ring[:35], ring[33:]])  # two tries that overlap a little
    assert len(out) == 1 and out[0]["closed"] and len(out[0]["vertices"]) >= 55
