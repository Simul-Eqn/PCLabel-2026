import hashlib
import json
import threading

import numpy as np
import pytest

from pclabel.io.e57 import iter_chunks, quaternion_to_matrix, write_e57
from pclabel.store.importer import ImportCancelled, ImportParams, import_scan
from pclabel.store.project import Project
from pclabel.store.tiles import TilingParams
from pclabel.synth import make_street, write_street_e57

SMALL = ImportParams(chunk_size=7_777, tiling=TilingParams(max_points=20_000), gather_block=50_000)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def street_e57(tmp_path_factory):
    d = tmp_path_factory.mktemp("src")
    scan = make_street(n_target=120_000, seed=4)
    path = write_street_e57(scan, d / "street.e57", invalid_fraction=0.03, seed=4)
    return path, scan, np.load(path.with_name(path.name + ".gt.npz"))


@pytest.fixture(scope="module")
def imported(street_e57, tmp_path_factory):
    path, scan, gt = street_e57
    before = (sha256(path), path.stat().st_mtime_ns)
    projects = tmp_path_factory.mktemp("projects")
    stages = []
    proj = import_scan(path, projects_dir=projects, params=SMALL, progress=lambda s, f: stages.append((s, f)))
    return Project(proj), before, stages


def read_source_order(path, offset):
    chunks = list(iter_chunks(path, offset=offset, chunk_size=10_000))
    return (np.concatenate([c.xyz for c in chunks]), np.concatenate([c.rgb for c in chunks]),
            np.concatenate([c.intensity for c in chunks]))  # fmt: skip


def test_round_trip_store_to_source_order(street_e57, imported):
    path, scan, gt = street_e57
    p, _, _ = imported
    xyz, rgb, inten = read_source_order(path, p.offset)
    assert p.n == len(xyz) == len(scan)
    assert np.array_equal(p.to_source_order(np.asarray(p.xyz)), xyz)
    assert np.array_equal(p.to_source_order(np.asarray(p.rgb)), rgb)
    assert np.array_equal(p.to_source_order(np.asarray(p.intensity)), inten)
    assert np.abs(xyz.astype(np.float64) + p.offset - scan.xyz).max() < 2e-5  # offset never dropped


def test_tiles_cover_every_point_once(imported):
    p, _, _ = imported
    assert np.array_equal(np.sort(np.asarray(p.source_index)), np.arange(p.n))
    t = p.tiles
    assert t["start"][0] == 0 and np.array_equal(t["start"][1:], np.cumsum(t["count"])[:-1])
    assert t["count"].sum() == p.n and len(t) > 5
    for k in range(len(t)):
        a, b = p.tile_range(k)
        q = np.asarray(p.xyz[a:b])
        assert q[:, 0].min() >= t["x0"][k] and q[:, 0].max() <= t["x1"][k]
        assert q[:, 2].min() >= t["zmin"][k] and q[:, 2].max() <= t["zmax"][k]


def test_labels_map_back_to_source_order(street_e57, imported, taxonomy):
    """Label in store order (as the app will), export in source order, compare with the ground truth."""
    path, scan, gt = street_e57
    p, _, _ = imported
    # stand-in for a labelling pass: give each store point the class its source point has in the ground truth
    store_labels = gt["cls"][np.asarray(p.source_index)]
    assert np.array_equal(p.to_source_order(store_labels), gt["cls"])
    # and a geometric selection made on store-order coordinates lands on the same points in source order
    xyz_src, _, _ = read_source_order(path, p.offset)
    box = lambda q: (q[:, 0] > 2.0) & (q[:, 0] < 9.0) & (q[:, 1] > 3.0)  # noqa: E731
    out = p.to_source_order(box(np.asarray(p.xyz)).astype(np.uint8)).astype(bool)
    assert out.sum() > 1000 and np.array_equal(out, box(xyz_src))
    assert taxonomy.class_id("tree_trunk") in set(gt["cls"][out].tolist())
    # labels and flags start unassessed
    assert not np.asarray(p.labels).any() and not np.asarray(p.flags).any()


def test_raw_index_skips_invalid_records(street_e57, imported):
    path, scan, gt = street_e57
    p, _, _ = imported
    assert p.meta["n_raw"] == len(gt["invalid"]) > p.n
    assert np.array_equal(np.asarray(p.raw_index()), np.flatnonzero(gt["invalid"] == 0))


def test_project_json_and_source_untouched(street_e57, imported):
    path, _, _ = street_e57
    p, before, stages = imported
    m = p.meta
    assert m["schema"] == "pclabel.project/1"
    assert m["source"]["sha256"] == before[0] == sha256(path)
    assert path.stat().st_mtime_ns == before[1]
    assert m["source"]["size"] == path.stat().st_size
    assert sum(s["n_valid"] for s in m["scans"]) == p.n
    assert m["attributes"] == ["rgb", "intensity"]
    assert [s for s, _ in stages][0] == "read" and stages[-1][0] == "finish"
    fr = [f for _, f in stages]
    assert all(b >= a - 1e-9 for a, b in zip(fr, fr[1:])) and fr[-1] == pytest.approx(1.0)
    assert not any(c.name.startswith(".importing") for c in p.path.parent.iterdir())


def test_scan_metadata_round_trip(imported):
    p, _, _ = imported
    p.set_scan_metadata(scanner="Leica RTC360", surface_wetness="dry")
    again = json.loads((p.path / "project.json").read_text(encoding="utf-8"))
    assert again["scan_metadata"]["scanner"] == "Leica RTC360" and again["scan_metadata"]["capture"] == "static"
    with pytest.raises(KeyError):
        p.set_scan_metadata(colour="red")


def test_never_overwrites(street_e57, imported):
    path, _, _ = street_e57
    p, _, _ = imported
    stamp = (p.path / "project.json").stat().st_mtime_ns
    with pytest.raises(FileExistsError):
        import_scan(path, name=p.name, projects_dir=p.path.parent, params=SMALL)
    assert (p.path / "project.json").stat().st_mtime_ns == stamp


@pytest.mark.parametrize("stage", ["read", "tile", "reorder", "finish"])
def test_cancel_leaves_nothing(street_e57, tmp_path, stage):
    path, _, _ = street_e57
    cancel = threading.Event()

    def progress(s, f):
        if s == stage:
            cancel.set()

    with pytest.raises(ImportCancelled):
        import_scan(path, projects_dir=tmp_path, params=SMALL, progress=progress, cancel=cancel)
    assert list(tmp_path.iterdir()) == []


def test_multi_scan_poses(tmp_path):
    rng = np.random.default_rng(3)
    scans = []
    for k, (q, t) in enumerate([((1, 0, 0, 0), (1000.0, 2000.0, 30.0)),
                                ((np.cos(0.4), 0, 0, np.sin(0.4)), (1020.0, 1990.0, 31.0))]):  # fmt: skip
        n = 20_000
        scans.append({"xyz": rng.uniform(-15, 15, (n, 3)), "rgb": rng.integers(0, 256, (n, 3)),
                      "intensity": rng.random(n), "rotation": q, "translation": t})  # fmt: skip
    src = tmp_path / "two.e57"
    write_e57(src, scans)
    p = Project(import_scan(src, projects_dir=tmp_path / "projects", params=SMALL))
    assert np.allclose(p.offset, np.round(p.offset)) and np.all(np.abs(p.offset - (1010, 1995, 30)) < 30)
    world = np.vstack([np.round(s["xyz"] / 1e-5) * 1e-5 @ quaternion_to_matrix(s["rotation"]).T + s["translation"]
                       for s in scans])  # fmt: skip
    got = p.to_source_order(np.asarray(p.xyz)).astype(np.float64) + p.offset
    assert np.abs(got - world).max() < 2e-5
    assert np.allclose(p.scan_origins + p.offset, [s["translation"] for s in scans])
    assert [s["raw_start"] for s in p.meta["scans"]] == [0, 20_000]
    assert "raw_index" not in p.meta["columns"]  # nothing dropped: raw index = source index
