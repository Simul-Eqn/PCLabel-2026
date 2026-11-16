"""Curb tracing (tools/curbtrace.py) on the synthetic street: the north kerb faces the scanner, the south one away."""

import numpy as np
import pytest

from pclabel.tools.curbtrace import CurbTraceParams, run_trace, section


@pytest.fixture(scope="module")
def dense(dense_street):
    xyz, _, tiles, off, on_road = dense_street
    return xyz, tiles, off, on_road


def test_north_kerb_toe_lip_and_gaps_at_drains(dense):
    xyz, tiles, off, on_road = dense
    r = run_trace(xyz, tiles, on_road(-8, 3.4), on_road(-7, 3.4), (0, 0, 1), CurbTraceParams(side="left"))
    toe, lip = r["toe"] + off, r["lip"] + off
    assert len(toe) > 40 and toe[:, 0].max() > 7.5 and r["params"]["curb_height"] == 0.06
    assert np.median(np.abs(toe[:, 1] - 3.70)) < 0.03 and np.median(np.abs(lip[:, 1] - 3.72)) < 0.02
    assert np.median(r["height"]) == pytest.approx(0.15, abs=0.01)  # measured, not the threshold
    # the drain inlets at x = -5 and 5 break the line: three pieces, none across a drain
    assert r["piece"].max() >= 2  # (and a short piece where the lowered kerb's ramp still rises 6 cm)
    for k in range(int(r["piece"].max()) + 1):
        xs = toe[r["piece"] == k, 0]
        assert not ((xs.min() < -5) & (xs.max() > -4.2)) and not ((xs.min() < 5) & (xs.max() > 5.8))
    assert "stopped" in r and not r["closed"]


def test_south_kerb_faces_away_and_errors(dense):
    xyz, tiles, off, on_road = dense
    r = run_trace(xyz, tiles, on_road(-8, -3.4), on_road(-7, -3.4), (0, 0, 1), CurbTraceParams(side="right"))
    assert len(r["toe"]) > 30 and not r["seen"].any()  # the face is never seen: the toe is the last road seen
    lip = r["lip"] + off
    assert np.median(np.abs(lip[:, 1] + 3.70)) < 0.03 and np.median(r["height"]) == pytest.approx(0.15, abs=0.01)
    # the wrong side: no kerb there
    assert "no step" in section(xyz, tiles, on_road(-6, -3.4), (0, 0, 1), (1, 0, 0), CurbTraceParams(side="left"))["why"]
    # a higher threshold than the kerb: nothing traced
    r = run_trace(xyz, tiles, on_road(-6, 3.4), on_road(-5.5, 3.4), (0, 0, 1), CurbTraceParams(curb_height=0.2))
    assert len(r["toe"]) == 0 and "no step of 0.20 m" in r["stopped"]
    with pytest.raises(ValueError, match="on top of each other"):
        run_trace(xyz, tiles, on_road(0, 3.4), on_road(0, 3.4), (0, 0, 1))


def test_side_found_and_a_start_on_the_kerb_top(dense):
    xyz, tiles, off, on_road = dense
    # the kerb on the right of the direction (heading -x along the north kerb): found without being told
    r = run_trace(xyz, tiles, on_road(-2, 3.4), on_road(-3, 3.4), (0, 0, 1), CurbTraceParams(max_length=4))
    assert r["params"]["side"] == "right" and len(r["toe"]) > 8
    assert np.median(np.abs(r["toe"][:, 1] + off[1] - 3.70)) < 0.04
    # clicked on the kerb's top (or the pavement): moved onto the road below it
    for y in (3.78, 4.3):
        r = run_trace(xyz, tiles, on_road(-8, y, 0.1), on_road(-7, y, 0.1), (0, 0, 1), CurbTraceParams(max_length=4))
        assert len(r["toe"]) > 8 and np.median(np.abs(r["toe"][:, 1] + off[1] - 3.70)) < 0.05, y
    # the middle of the road: no kerb, and it says what it saw
    r = run_trace(xyz, tiles, on_road(-8, 0.5), on_road(-7, 0.5), (0, 0, 1))
    assert not len(r["toe"]) and "the most it rises" in r["stopped"]


def test_following_the_top_edge_only(dense):
    """follow="lip": the trace steps along the kerb's top edge; on the south kerb (its face and the road beside it
    hidden from the scanner) it follows the lip as well as the toe mode does."""
    xyz, tiles, off, on_road = dense
    r = run_trace(xyz, tiles, on_road(-8, -3.4), on_road(-7, -3.4), (0, 0, 1), CurbTraceParams(follow="lip"))
    lip = r["lip"] + off
    assert len(lip) > 30 and r["params"]["follow"] == "lip"
    assert np.median(np.abs(lip[:, 1] + 3.70)) < 0.03 and np.median(r["height"]) == pytest.approx(0.15, abs=0.02)
    with pytest.raises(ValueError, match="follow"):
        run_trace(xyz, tiles, on_road(-8, -3.4), on_road(-7, -3.4), (0, 0, 1), CurbTraceParams(follow="face"))
