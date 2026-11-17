"""Marking tracing by intensity (tools/linetrace.py) on the synthetic street: a dashed centre line (3 m dashes, 6 m
gaps, from x = -30) and a double yellow line 0.35-0.45 m and 0.60-0.70 m from each curb (toe at |y| = 3.70)."""

import numpy as np
import pytest

from pclabel.tools.linetrace import LineTraceParams, run_trace, start_threshold


def test_dashed_centre_line_one_piece_per_dash(dense_street):
    xyz, inten, tiles, off, on_road = dense_street
    thr = start_threshold(xyz, inten, tiles, on_road(-2.5, 0), (0, 0, 1), (1, 0, 0), LineTraceParams())
    assert thr["paint"] > thr["road"] + 0.2 and thr["road"] < thr["threshold"] < thr["paint"]
    r = run_trace(xyz, inten, tiles, on_road(-2.5, 0), on_road(-2, 0), (0, 0, 1), LineTraceParams(kind="dashed"))
    pcs = [pc + off for pc in r["pieces"]]
    assert len(pcs) >= 2 and r["params"]["threshold"] == pytest.approx(thr["threshold"])
    assert pcs[0][-1, 0] == pytest.approx(0.0, abs=0.1)  # the first dash ends at x = 0
    assert pcs[1][0, 0] == pytest.approx(6.0, abs=0.1) and pcs[1][-1, 0] == pytest.approx(9.0, abs=0.1)
    assert all(np.abs(pc[:, 1]).max() < 0.05 for pc in pcs)  # on the centre line
    assert "no paint" in r["stopped"]


def test_double_yellow_lines_kept_apart(dense_street):
    xyz, inten, tiles, off, on_road = dense_street
    prm = LineTraceParams(line_width=0.10, search=0.10)
    ys = []
    for y in (3.05, 3.30):
        r = run_trace(xyz, inten, tiles, on_road(-6, y), on_road(-5, y), (0, 0, 1), prm)
        (pc,) = r["pieces"]  # solid: one piece, small gaps bridged and recorded
        pc = pc + off
        assert pc[-1, 0] - pc[0, 0] > 15 and all(b <= prm.stretch for b in r["bridged"])
        ys.append(np.median(pc[:, 1]))
    assert ys[0] == pytest.approx(3.05, abs=0.02) and ys[1] == pytest.approx(3.30, abs=0.02)
    with pytest.raises(ValueError, match="no brighter"):
        run_trace(xyz, inten, tiles, on_road(-6, 2.0), on_road(-5, 2.0), (0, 0, 1))  # plain road
