"""Height frames: robust plane fit, slopes, heights (no GL)."""

import numpy as np
import pytest

from pclabel.analysis.frames import Z_FRAME, HeightFrame, PlaneFitParams, fit_plane, frame_from_points
from pclabel.synth import StreetParams, make_street


@pytest.fixture(scope="module")
def tilted_street(taxonomy):
    # one plane rising towards +y (no crown): z = 0.01 x + 0.025 y on the road
    p = StreetParams(n_target=500_000, crown=False, crossfall=0.025, grade=0.01, seed=7)
    return make_street(p, taxonomy), p


def road_patch(scan, taxonomy):
    x, y = scan.xyz[:, 0], scan.xyz[:, 1]
    road = (scan.cls == taxonomy.class_id("road")) & ((scan.flags & taxonomy.flag_mask("marking")) == 0)
    return road & (np.abs(x) < 15) & (np.abs(y) < 3.0)


def test_recovers_crossfall_and_grade(tilted_street, taxonomy):
    """SPEC §3: 2.5 % crossfall and 1 % grade recovered to within 0.05 %; RMS at the noise level."""
    scan, p = tilted_street
    pts = scan.xyz[road_patch(scan, taxonomy)].astype(np.float64) + scan.offset
    f = frame_from_points(pts, "road")
    r = f.report
    assert abs(r["along_azimuth_deg"]) < 5  # the 30 x 6 m patch runs along x
    assert r["grade_percent"] == pytest.approx(100 * p.grade, abs=0.05)
    assert r["crossfall_percent"] == pytest.approx(100 * p.crossfall, abs=0.05)
    assert 0 < r["rms_m"] <= p.range_noise
    assert r["tilt_deg"] == pytest.approx(np.degrees(np.arctan(np.hypot(p.grade, p.crossfall))), abs=0.01)
    assert np.abs(f.heights(scan.xyz[road_patch(scan, taxonomy)], scan.offset)).max() < 10 * p.range_noise


def test_robust_to_outliers(tilted_street, taxonomy):
    """Kerb tops, a tree and drains in the selection must not tilt the plane (MAD trimming)."""
    scan, p = tilted_street
    sel = road_patch(scan, taxonomy)
    x, y = scan.xyz[:, 0], scan.xyz[:, 1]
    junk = ~sel & (np.abs(x) < 15) & (np.abs(y) < 12)  # curbs, pavement, grass, tree, drains of the same area
    rng = np.random.default_rng(0)
    idx = np.r_[np.flatnonzero(sel), rng.choice(np.flatnonzero(junk), int(0.2 * sel.sum()), replace=False)]
    f = frame_from_points(scan.xyz[idx].astype(np.float64) + scan.offset, "road+junk")
    assert f.report["grade_percent"] == pytest.approx(100 * p.grade, abs=0.05)
    assert f.report["crossfall_percent"] == pytest.approx(100 * p.crossfall, abs=0.05)
    assert f.report["points_kept"] < len(idx)


def test_subsample_and_json_round_trip(tilted_street, taxonomy):
    scan, _ = tilted_street
    pts = scan.xyz[road_patch(scan, taxonomy)].astype(np.float64)
    a = fit_plane(pts, PlaneFitParams(max_points=5000))
    b = fit_plane(pts)
    assert a["n_fitted"] == 5000 and a["n_input"] == len(pts)
    assert np.degrees(np.arccos(np.clip(a["normal"] @ b["normal"], -1, 1))) < 0.05
    f = frame_from_points(pts, "x")
    g = HeightFrame.from_json(f.to_json())
    assert g == f


def test_z_frame_is_world_z():
    xyz = np.array([[1.0, 2.0, 3.0], [0.0, 0.0, -1.5]], np.float32)
    off = np.array([100.0, 200.0, 30.0])
    assert np.allclose(Z_FRAME.heights(xyz, off), xyz[:, 2] + 30.0)


def test_needs_three_points():
    with pytest.raises(ValueError):
        fit_plane(np.zeros((2, 3)))


def test_warns_when_the_fit_is_not_ground(tilted_street, taxonomy):
    scan, _ = tilted_street
    road = frame_from_points(scan.xyz[road_patch(scan, taxonomy)].astype(np.float64), "road")
    assert "warning" not in road.report
    canopy = scan.xyz[scan.cls == taxonomy.class_id("tree_canopy")].astype(np.float64)
    leaves = frame_from_points(canopy, "leaves")
    assert "does not look like ground" in leaves.report["warning"]
