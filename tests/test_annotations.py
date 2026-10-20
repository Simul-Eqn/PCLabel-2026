"""Annotations: segments, curves and point sets in a tree of directories; the project's copy, export and import."""

import json

import numpy as np
import pytest

from pclabel.store.annotations import SCHEMA, SET_OPS, Annotations, Curve, PointSet, Segment, store_indices_of_sources
from pclabel.store.importer import ImportParams, import_scan
from pclabel.store.project import Project
from pclabel.store.tiles import TilingParams
from pclabel.synth import make_street, write_street_e57


@pytest.fixture(scope="module")
def projects(tmp_path_factory):
    d = tmp_path_factory.mktemp("annotations")
    out = []
    for seed in (5, 6):
        src = write_street_e57(make_street(n_target=40_000, seed=seed), d / f"street{seed}.e57")
        out.append(import_scan(src, projects_dir=d / "projects", params=ImportParams(tiling=TilingParams(max_points=8_000))))
    return out


def sample(project):
    """One item of each type, in world coordinates."""
    xyz = np.asarray(project.xyz[:50], np.float64) + project.offset
    seg = Segment(xyz[0], xyz[1], [int(project.source_index[0]), int(project.source_index[1])])
    c = Curve.fit(xyz[2:6], (0, 0, 1), (0, 0, 0))
    c.drag_handle(1, 1, c.nodes[1] + (0.5, 0.2, 0))
    pts = PointSet(np.arange(100, 400, 3, dtype=np.int64), [{"tool": "magic_select", "op": "replace",
                                                               "params": {"tolerance": np.float64(2.0)}}])  # fmt: skip
    return seg, c, pts


def test_items_stand_alone_until_put_in_a_directory():
    t = Annotations()
    a = t.add(Segment(np.zeros(3), np.ones(3)))
    b = t.add(Segment(np.zeros(3), np.ones(3) * 2))
    c = t.add(PointSet(np.arange(5)))
    assert [x.name for x in t.root] == ["segment 1", "segment 2", "points 3"]
    assert len({t.color_of(x.id) for x in t.root}) == 3  # each its own colour at the top level
    lanes = t.new_directory("lane lines")
    t.move([a.id, c.id], lanes.id)
    assert [x.id for x in t.root] == [b.id, lanes.id] and lanes.children == [a, c]
    assert t.color_of(a.id) == t.color_of(c.id) == lanes.color != t.color_of(b.id)  # separate items, one colour
    lane3 = t.new_directory("lane 3", lanes.id)  # a directory inside takes its parent's colour...
    t.move([c.id], lane3.id)
    assert lane3.color is None and t.color_of(c.id) == lanes.color
    lane3.color = "#123456"  # ...unless given its own
    assert t.color_of(c.id) == "#123456" and t.color_of(a.id) == lanes.color
    t.move([lanes.id], lane3.id)  # not into its own contents
    assert t.parent(lanes.id) is None
    t.move([lanes.id, c.id], None)  # c is inside lanes, so it moves along with it (and stays in lane 3)
    assert t.parent(c.id) is lane3
    t.move([a.id, c.id], None)
    assert t.parent(c.id) is None and t.color_of(a.id) == a.color  # at the top level: their own colours again
    t.remove(lanes.id)  # with everything in it
    assert lane3.id not in {x.id for x, _ in t.walk()} and len(t.items()) == 3


def test_moving_and_deleting_items_after_others_of_their_kind():
    """Regression: list.remove() compared a node with those before it, and two curves (or segments, point sets)
    compared their numpy arrays: "The truth value of an array ... is ambiguous". Drag and delete stopped working."""
    t = Annotations()
    c1, c2 = (t.add(Curve.fit(np.array([[0.0, k, 0], [5.0, k, 0]]), (0, 0, 1), (0, 0, 0))) for k in (0.0, 1.0))
    s1, s2 = (t.add(Segment(np.zeros(3), np.ones(3) * k)) for k in (1.0, 2.0))
    p1, p2 = (t.add(PointSet(np.arange(k, k + 5))) for k in (0, 10))
    d = t.new_directory("lines")
    t.move([c2.id, s2.id, p2.id], d.id)
    assert d.children == [c2, s2, p2] and t.root == [c1, s1, p1, d]
    t.move([c1.id], d.id)
    t.remove(s1.id)
    t.remove(p2.id)
    assert d.children == [c2, s2, c1] and t.root == [p1, d]


def test_labels_are_inherited_and_exported_by_label(projects, tmp_path):
    p = Project(projects[0])
    xyz = np.asarray(p.xyz[:20], np.float64) + p.offset
    t = Annotations()
    kerb = t.new_directory("kerb")
    kerb.label = "curb"
    line = t.add(Curve.fit(xyz[:4], (0, 0, 1), (0, 0, 0)))
    seg = t.add(Segment(xyz[4], xyz[5]))
    road = t.add(PointSet(np.arange(0, 1000, dtype=np.int64)))
    road.label = "road"
    paint = t.add(PointSet(np.arange(900, 1200, dtype=np.int64), name="paint"))
    paint.label = "lane line"
    loose = t.add(Segment(xyz[6], xyz[7]))
    t.move([line.id, seg.id], kerb.id)
    seg.label = "curb lip"  # its own label wins over the directory's
    assert t.label_of(line.id) == ("curb", kerb.id) and t.label_of(seg.id) == ("curb lip", seg.id)
    assert t.label_of(loose.id) == ("", None)
    out = tmp_path / "labels.json"
    summary = t.export_labels(out, p, {"road": 1, "curb": 2})
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert [e["label"] for e in doc["labels"]] == ["road", "curb", "curb lip", "lane line"]  # classes first, by id
    by = {e["label"]: e for e in doc["labels"]}
    assert by["curb"]["class_id"] == 2 and by["lane line"]["class_id"] is None
    assert by["curb"]["items"][0]["path"] == ["kerb"] and by["curb"]["items"][0]["label_from"] == "kerb"
    assert by["curb"]["counts"] == {"segment": 0, "curve": 1, "points": 0}
    assert by["road"]["points"] == 1000 and by["road"]["items"][0]["source_indices"] == sorted(
        int(i) for i in p.source_index[np.arange(1000)])  # fmt: skip
    assert doc["overlaps"] == [{"labels": ["road", "lane line"], "points": 100}]
    assert doc["unlabelled"]["counts"]["segment"] == 1 and summary[""] == (1, 0)


def test_locked_labels_and_their_points():
    from pclabel.store.annotations import isin_sorted

    t = Annotations()
    road = t.add(PointSet(np.arange(0, 100, dtype=np.int64)))
    paint = t.add(PointSet(np.arange(90, 150, dtype=np.int64)))
    junk = t.add(PointSet(np.arange(500, 510, dtype=np.int64)))
    d = t.new_directory("roads")
    d.label = "road"
    t.move([road.id], d.id)
    paint.label, junk.label = "lane line", "other"
    assert np.array_equal(t.points_labelled({"road"}), np.arange(100))
    assert np.array_equal(t.points_labelled({"road", "lane line", "other"}),
                          np.r_[np.arange(150), np.arange(500, 510)])  # fmt: skip
    mask = isin_sorted(np.array([5, 120, 505, 700]), t.points_labelled({"road", "other"}))
    assert mask.tolist() == [True, False, True, False]


def test_the_project_keeps_its_annotations(projects):
    p = Project(projects[0])
    seg, c, pts = sample(p)
    t = Annotations()
    for x in (seg, c, pts):
        t.add(x)
    kerb = t.new_directory("kerb")
    inner = t.new_directory("south", kerb.id)
    t.move([seg.id], kerb.id)
    t.move([c.id], inner.id)
    kerb.label = "curb"
    t.locks = {"curb", "road"}
    t.unlocked, t.locks_on = {"other"}, False
    c.set_mode("axis")
    t.save(p)
    doc = json.loads((p.path / "annotations.json").read_text(encoding="utf-8"))
    assert doc["schema"] == SCHEMA and (p.path / "annotations" / f"{pts.id}.npy").exists()
    u, warnings = Annotations.load(p)
    assert not warnings and [x.name for x in u.root] == ["points 3", "kerb"]
    k = u.root[1]
    south, s2 = k.children
    c2 = south.children[0]
    assert south.name == "south" and south.color is None and u.color_of(c2.id) == kerb.color
    assert np.array_equal(s2.a, seg.a) and np.allclose(c2.polyline(), c.polyline())
    assert c2.mode == "axis" and np.allclose(c2.axis, c.axis) and u.label_of(c2.id) == ("curb", kerb.id)
    assert u.locks == {"curb", "road"} and u.unlocked == {"other"} and u.locks_on is False
    assert c2.manual().tolist() == c.manual().tolist()
    assert np.array_equal(u.root[0].indices, pts.indices) and u.root[0].provenance[0]["params"]["tolerance"] == 2.0
    u.remove(u.root[0].id)
    u.save(p)
    assert not (p.path / "annotations" / f"{pts.id}.npy").exists()  # a deleted point set's file goes too


def test_export_and_import(projects, tmp_path):
    p = Project(projects[0])
    seg, c, pts = sample(p)
    t = Annotations()
    for x in (seg, c, pts):
        t.add(x)
    d = t.new_directory("lines")
    t.move([seg.id, c.id], d.id)
    out = tmp_path / "all.json"
    t.export(out, p)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["offset"] == list(p.offset) and [n["type"] for n in doc["tree"]] == ["points", "directory"]
    s_doc, c_doc = doc["tree"][1]["children"]
    assert np.allclose(s_doc["a"], np.asarray(p.xyz[0], np.float64) + p.offset)  # world coordinates
    assert doc["tree"][0]["source_indices"] == sorted(int(i) for i in p.source_index[pts.indices])
    assert len(c_doc["polyline"]) > 10 and c_doc["manual"] == [False, True, False, False]

    same = Annotations()
    assert same.import_file(out, p) == []
    m = {x.kind: x for x in same.items()}
    assert np.array_equal(m["points"].indices, pts.indices) and m["points"].id != pts.id
    assert np.allclose(m["curve"].polyline(), c.polyline()) and same.parent(m["curve"].id).name == "lines"

    part = tmp_path / "part.json"
    t.export(part, p, [c.id, d.id])  # the directory includes the curve: exported once
    assert [n["name"] for n in json.loads(part.read_text(encoding="utf-8"))["tree"]] == ["lines"]

    other = Annotations()
    warnings = other.import_file(out, Project(projects[1]))  # another scan: geometry yes, point indices no
    assert sorted(x.kind for x in other.items()) == ["curve", "segment"] and "another scan" in warnings[0]


def test_source_indices_map_back_to_store_indices(projects):
    p = Project(projects[0])
    store = np.array([3, 17, 1000, len(p.source_index) - 1], np.int64)
    assert np.array_equal(store_indices_of_sources(p, p.source_index[store]), store)


@pytest.mark.parametrize("first_end,second_end", [("end", "start"), ("end", "end"), ("start", "start"), ("start", "end")])
def test_join_two_curves_at_the_chosen_ends(first_end, second_end):
    up = (0, 0, 1)
    a_pts = np.array([[0.0, 0, 0], [1.0, 0.2, 0], [2.0, 0, 0]])
    b_pts = np.array([[5.0, 0, 0], [6.0, 0.3, 0], [7.0, 0, 0]])
    t = Annotations()
    d = t.new_directory("kerb")
    a = t.add(Curve.fit(a_pts, up, (0, 0, 0)), name="south kerb")
    a.label = "curb"
    b = t.add(Curve.fit(b_pts, up, (0, 0, 0)))
    b.drag_handle(1, 1, b.nodes[1] + (0.5, 0.0, 0))  # a manual node: its handles must survive (swapped if reversed)
    keep_in, keep_out = b.h_in[1].copy(), b.h_out[1].copy()
    t.move([a.id], d.id)
    c = t.join_curves(a.id, first_end, b.id, second_end)
    a_seq = a_pts if first_end == "end" else a_pts[::-1]
    b_seq = b_pts if second_end == "start" else b_pts[::-1]
    assert np.allclose(c.picked, np.vstack([a_seq, b_seq]))
    assert c.name == "south kerb" and c.label == "curb" and t.parent(c.id) is d and len(t.items()) == 1
    j = 4  # b's middle node, wherever it went
    want_in, want_out = (keep_in, keep_out) if second_end == "start" else (keep_out, keep_in)
    assert np.allclose(c.h_in[j], want_in) and np.allclose(c.h_out[j], want_out)
    assert np.allclose(c.nodes[[0, -1]], c.picked[[0, -1]])  # "through the ends": the outer ends stay put


def test_join_merges_touching_ends_and_can_close_the_loop():
    up = (0, 0, 1)
    t = Annotations()
    a = t.add(Curve.fit(np.array([[0.0, 0, 0], [2.0, 0, 0], [4.0, 0, 0]]), up, (0, 0, 0)))
    b = t.add(Curve.fit(np.array([[4.005, 0, 0], [4.0, 3.0, 0], [0.0, 3.0, 0], [0.0, 0.01, 0]]), up, (0, 0, 0)))
    c = t.join_curves(a.id, "end", b.id, "start", close=True)
    assert len(c.nodes) == 5 and c.closed  # (4, 0) and (4.005, 0) became one node; (0, 0.01) merged into (0, 0)
    loop = t.add(Curve.fit(np.array([[0.0, 0, 0], [1.0, 0, 0], [1.0, 1, 0]]), up, (0, 0, 0), closed=True))
    other = t.add(Curve.fit(np.array([[5.0, 0, 0], [6.0, 0, 0]]), up, (0, 0, 0)))
    with pytest.raises(ValueError, match="closed loop"):
        t.join_curves(loop.id, "end", other.id, "start")


def test_set_operations():
    t = Annotations()
    d = t.new_directory("ground")
    a = t.add(PointSet(np.array([1, 5, 9], np.int64), [{"tool": "grow_select"}]), name="road a")
    b = t.add(PointSet(np.array([2, 5, 10], np.int64), [{"tool": "magic_select"}]), name="road b")
    c = t.add(PointSet(np.array([9, 20], np.int64)), name="kerb")
    t.move([a.id, b.id], d.id)
    expect = {"union": [1, 2, 5, 9, 10], "intersection": [5], "a_minus_b": [1, 9], "b_minus_a": [2, 10],
              "xor": [1, 2, 9, 10]}  # fmt: skip
    assert set(expect) == set(SET_OPS)
    for op, want in expect.items():
        idx, rec = t.set_operation(a.id, op, b.id)
        assert idx.tolist() == want and idx.dtype == np.int64, op
        assert rec["op"] == op and rec["a"] == {"name": "road a", "points": 3} and rec["b"]["name"] == "road b"
    # a directory stands for all the point sets inside it; the originals stay as they are
    idx, _ = t.set_operation(d.id, "a_minus_b", c.id)
    assert idx.tolist() == [1, 2, 5, 10] and a.indices.tolist() == [1, 5, 9] and len(d.children) == 2
    assert t.points_of(d.id).tolist() == [1, 2, 5, 9, 10]
    assert t.set_operation(c.id, "intersection", b.id)[0].size == 0


def test_lock_switches():
    t = Annotations()
    t.locks = {"road"}
    never = {"other"}
    assert t.locked_labels(never) == {"road", "other"}
    t.unlocked.add("other")  # the never-selected class unlocked
    assert t.locked_labels(never) == {"road"}
    t.locks_on = False  # all off, the choices kept
    assert t.locked_labels(never) == set() and t.locks == {"road"}
