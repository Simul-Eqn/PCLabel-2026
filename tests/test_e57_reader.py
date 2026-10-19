import numpy as np
import pye57
import pytest

from pclabel.io.e57 import default_offset, iter_chunks, quaternion_to_matrix, read_info, write_e57
from pclabel.synth import make_street, write_street_e57


def read_all(path, **kw):
    chunks = list(iter_chunks(path, **kw))
    cat = lambda name: np.concatenate([getattr(c, name) for c in chunks])  # noqa: E731
    return chunks, cat


@pytest.fixture(scope="module")
def two_scans(tmp_path_factory):
    rng = np.random.default_rng(5)
    scans = []
    for k, (q, t) in enumerate(
        [((1.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0)), ((np.cos(np.pi / 12), 0.0, 0.0, np.sin(np.pi / 12)), (100.0, 200.0, 5.0))]
    ):
        n = 20_000 + 7_000 * k
        invalid = np.where(rng.random(n) < 0.1, rng.integers(1, 3, n), 0).astype(np.int8)
        scans.append(
            {
                "xyz": rng.uniform(-40, 40, (n, 3)),
                "rgb": rng.integers(0, 256, (n, 3)).astype(np.uint8),
                "intensity": rng.random(n).astype(np.float32),
                "invalid": invalid,
                "rotation": q,
                "translation": t,
                "name": f"s{k}",
            }
        )
    path = tmp_path_factory.mktemp("e57") / "two.e57"
    write_e57(path, scans)
    return path, scans


def test_info(two_scans):
    path, scans = two_scans
    info = read_info(path)
    assert [i.n_raw for i in info] == [len(s["xyz"]) for s in scans]
    assert info[1].invalid_field == "cartesianInvalidState" and info[1].has_color and info[1].has_intensity
    assert np.allclose(info[1].rotation, quaternion_to_matrix(scans[1]["rotation"]))


def test_chunked_read_is_source_order_with_pose(two_scans):
    path, scans = two_scans
    chunks, cat = read_all(path, chunk_size=997)
    assert len(chunks) > 40  # many chunk boundaries, including across the scan boundary
    xyz = cat("xyz").astype(np.float64)
    expect = []
    for s in scans:
        R = quaternion_to_matrix(s["rotation"])
        v = s["invalid"] == 0
        world = np.round(s["xyz"][v] / 1e-5) * 1e-5 @ R.T + np.asarray(s["translation"])
        expect.append(world)
    expect = np.vstack(expect)
    off = default_offset(read_info(path))
    assert np.allclose(off, np.round(off))
    assert np.abs(xyz + off - expect).max() < 5e-5
    raw = np.concatenate([np.flatnonzero(s["invalid"] == 0) for s in scans])
    assert np.array_equal(cat("raw_index"), raw)
    rgb = np.vstack([s["rgb"][s["invalid"] == 0] for s in scans])
    assert np.array_equal(cat("rgb"), rgb)
    inten = np.concatenate([s["intensity"][s["invalid"] == 0] for s in scans])
    assert np.abs(cat("intensity").astype(np.int64) - np.round(inten * 65535)).max() <= 1


def test_matches_pye57_read_scan(two_scans):
    path, _ = two_scans
    _, cat = read_all(path, offset=np.zeros(3), chunk_size=4096)
    with pye57.E57(str(path)) as e:
        ref = np.vstack(
            [np.c_[d["cartesianX"], d["cartesianY"], d["cartesianZ"]] for d in (e.read_scan(i) for i in range(2))]
        )
    xyz = cat("xyz").astype(np.float64)
    assert xyz.shape == ref.shape  # N_valid matches pye57's read_scan, concatenated
    assert np.abs(xyz - ref).max() < 5e-5


def test_max_points_stops_early(two_scans):
    path, _ = two_scans
    chunks, _ = read_all(path, chunk_size=1000, max_points=4500)
    assert sum(c.n_raw for c in chunks) == 4500 and chunks[-1].scan == 0


def test_street_e57_round_trip(tmp_path):
    s = make_street(n_target=50_000)
    path = write_street_e57(s, tmp_path / "street.e57", invalid_fraction=0.05)
    gt = np.load(path.with_name(path.name + ".gt.npz"))
    assert (gt["invalid"] != 0).sum() > 0
    _, cat = read_all(path, offset=np.zeros(3))
    assert np.abs(cat("xyz") - s.xyz).max() < 2e-5
    assert np.array_equal(gt["cls"], s.cls)
    assert np.array_equal(cat("raw_index"), np.flatnonzero(gt["invalid"] == 0))


def test_existing_file_is_never_overwritten(two_scans):
    path, scans = two_scans
    with pytest.raises(FileExistsError):
        write_e57(path, scans)


@pytest.fixture(scope="module")
def odd_scans(tmp_path_factory):
    """Five scans that exercise the reader's edge cases, in file order."""
    rng = np.random.default_rng(9)
    n = 3000
    sph = np.c_[rng.uniform(1, 50, n), rng.uniform(-np.pi, np.pi, n), rng.uniform(-1.0, 1.0, n)]
    scans = [
        # 0: spherical coordinates with a pose, sphericalInvalidState, 16-bit colour, integer intensity
        {
            "spherical": sph,
            "invalid": np.where(rng.random(n) < 0.2, 1, 0).astype(np.int8),
            "rgb": rng.integers(0, 65536, (n, 3)),
            "rgb_max": 65535,
            "intensity": rng.integers(0, 2048, n),
            "intensity_max": 2047,
            "rotation": (np.cos(0.3), 0.0, np.sin(0.3), 0.0),
            "translation": (5.0, -3.0, 1.5),
        },
        # 1: every record invalid
        {"xyz": rng.uniform(-5, 5, (500, 3)), "invalid": np.full(500, 2, np.int8)},
        # 2: no colour, no intensity
        {"xyz": rng.uniform(-5, 5, (700, 3))},
        # 3: no records at all
        {"xyz": np.zeros((0, 3))},
        # 4: an ordinary scan after the odd ones
        {"xyz": rng.uniform(-5, 5, (900, 3)), "rgb": rng.integers(0, 256, (900, 3)), "intensity": rng.random(900)},
    ]
    path = tmp_path_factory.mktemp("e57odd") / "odd.e57"
    write_e57(path, scans)
    return path, scans


def test_odd_scans_info(odd_scans):
    path, scans = odd_scans
    info = read_info(path)
    assert [i.coords for i in info] == ["spherical", "cartesian", "cartesian", "cartesian", "cartesian"]
    assert info[0].invalid_field == "sphericalInvalidState" and info[0].color_max == 65535
    assert info[0].intensity_range == (0.0, 2047.0)
    assert not info[2].has_color and not info[2].has_intensity
    assert [i.n_raw for i in info] == [3000, 500, 700, 0, 900]


def test_odd_scans_read(odd_scans):
    path, scans = odd_scans
    chunks = list(iter_chunks(path, offset=np.zeros(3), chunk_size=1000))
    by_scan = {}
    for c in chunks:
        by_scan.setdefault(c.scan, []).append(c)
    assert sorted(by_scan) == [0, 1, 2, 4]  # the empty scan yields nothing
    assert sum(len(c.xyz) for c in by_scan[1]) == 0  # all invalid

    s0 = scans[0]
    v = s0["invalid"] == 0
    r, az, el = s0["spherical"][v].T
    local = np.stack([r * np.cos(el) * np.cos(az), r * np.cos(el) * np.sin(az), r * np.sin(el)], 1)
    expect = local @ quaternion_to_matrix(s0["rotation"]).T + np.asarray(s0["translation"])
    got = np.vstack([c.xyz for c in by_scan[0]]).astype(np.float64)
    assert np.abs(got - expect).max() < 1e-4
    rgb = np.vstack([c.rgb for c in by_scan[0]])
    assert np.abs(rgb.astype(int) - np.round(s0["rgb"][v] * 255 / 65535)).max() <= 1
    inten = np.concatenate([c.intensity for c in by_scan[0]])
    assert np.abs(inten.astype(int) - np.round(s0["intensity"][v] / 2047 * 65535)).max() <= 1
    assert np.array_equal(np.concatenate([c.raw_index for c in by_scan[0]]), np.flatnonzero(v))

    assert all(c.rgb is None and c.intensity is None for c in by_scan[2])
    assert sum(len(c.xyz) for c in by_scan[2]) == 700
    s4 = np.vstack([c.xyz for c in by_scan[4]]).astype(np.float64)
    assert np.abs(s4 - np.round(scans[4]["xyz"] / 1e-5) * 1e-5).max() < 1e-5
