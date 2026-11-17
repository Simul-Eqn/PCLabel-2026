"""Tool results as annotations without the app (pclabel.ops.trace, pclabel.ops.trees): what the pipeline will call."""

import numpy as np

from pclabel.ops import trace as trace_ops
from pclabel.ops import trees as tree_ops
from pclabel.store.annotations import Annotations
from pclabel.taxonomy import load_taxonomy


def _curb_run(n=6):
    x = np.arange(n) * 0.3
    toe = np.c_[x, np.full(n, 0.3), np.zeros(n)]
    res = {"toe": toe, "lip": toe + [0, 0.02, 0.15], "height": np.full(n, 0.15), "face": np.full(n, 0.02),
           "seen": np.ones(n, bool), "top_seen": np.ones(n, bool), "piece": np.array([0, 0, 0, 1, 1, 1]),
           "stopped": "no step", "closed": False, "params": {"step": 0.3, "follow": "toe"}}  # fmt: skip
    return {"mode": "curb", "result": res, "start": np.zeros(3), "toward": np.array([1.0, 0, 0]), "frame": "z",
            "axis": np.array([0.0, 0, 1])}  # fmt: skip


def test_a_curb_run_saves_the_standards_lines():
    tax, off = load_taxonomy(), np.array([100.0, 200.0, 10.0])
    lines = trace_ops.run_lines([_curb_run()], off, tax, toe=True)
    assert [(n, lab) for n, _, lab, _ in lines] == [("toe 1", ""), ("lip 1", "curb"), ("toe 2", ""), ("lip 2", "curb")]
    assert np.allclose(lines[1][1][0], [100.0, 200.32, 10.15])  # world coordinates
    src = lines[1][3]
    assert src["tool"] == "curb_trace" and src["assisted"] and src["line"] == "lip" and src["heights"]["median"] == 0.15
    assert [n for n, *_ in trace_ops.run_lines([_curb_run()], off, tax, toe=False)] == ["lip 1", "lip 2"]
    m = Annotations()
    d = trace_ops.add_lines(m, lines, trace_ops.STEMS["curb"], None,
                            lambda v, s: trace_ops.line_item(v, False, s, "polyline", (0, 0, 1), "z", v[0], 0.01))  # fmt: skip
    assert d.name == "curb trace 01" and [c.label for c in d.children] == ["", "curb", "", "curb"]
    assert trace_ops.next_directory_name(m, "curb trace") == "curb trace 02"
    rec = trace_ops.log_record(_curb_run(), off, _curb_run()["result"])
    assert rec["steps"] == 6 and rec["pieces"] == 2 and rec["start"] == [100.0, 200.0, 10.0]


def test_a_tree_and_a_pole_are_saved_as_the_tree_tool_saves_them():
    tax = load_taxonomy()
    fit = {"centre": np.array([1.0, 2.0, 0.0]), "radius": 0.2, "girth": 1.26, "rms": 0.01, "points": 500,
           "dropped": 3, "coverage": 0.6}  # fmt: skip
    m = Annotations()
    into = m.new_directory("trees")
    rec = tree_ops.band_record(np.zeros(3), 0.0, "z", (0, 0, 1), (1.0, 1.1), fit, np.array([10.0, 0, 0]), "tree")
    d = tree_ops.add_object(m, into, rec, np.arange(5, dtype=np.int64), [{"tool": "volume"}], tax)
    mk, band = d.children
    assert d.name.startswith("tree") and mk.name == "position" and mk.label == "tree" and band.label == "tree_trunk"
    assert np.allclose(mk.position, [11.0, 2.0, 0.0]) and mk.source["radius"] == 0.2 and mk.source["points"] == 500
    assert band.name == "trunk 1.00 to 1.10 m" and band.provenance[-1]["object"] == "tree"
    pole = tree_ops.band_record(np.zeros(3), 0.0, "z", (0, 0, 1), (1.0, 1.1), fit, np.zeros(3), "lta_lamp_post")
    p = tree_ops.add_object(m, into, pole, np.arange(3, dtype=np.int64), [], tax)
    assert [c.label for c in p.children] == ["lta_lamp_post", "lta_lamp_post"] and p.children[1].name.startswith("pole")
    # an object saved before markers existed gets one, first in its directory
    m.remove(mk.id)
    todo = tree_ops.missing_markers(m)
    assert [h.id for h, _ in todo] == [d.id]
    tree_ops.add_marker(m, *todo[0], tax)
    assert d.children[0].kind == "marker" and np.allclose(d.children[0].position, [11.0, 2.0, 0.0])


def test_an_older_lines_kerb_fit_records_move_to_curb_fit():
    """Lines smoothed onto the curb top before 2026-11-16 keep their fits under ``kerb_fit``; the next fit moves them
    to ``curb_fit`` and adds its own."""
    keys = ("side", "sections", "anchors", "low_steps", "outliers", "no_edge", "top_unseen", "height_median",
            "off_anchors_rms", "off_anchors_max", "largest_move", "mean_move", "bridged", "kept", "seconds")  # fmt: skip
    result = {k: 0 for k in keys}
    old = {"tool": "curb_trace", "kerb_fit": [{"seconds": 1.0}], "edited": True, "curve_fit": {"nodes": 3}}
    src = trace_ops.curb_fit_source(old, result, {"step": 0.3}, "z", (0, 0, 1))
    assert "kerb_fit" not in src and len(src["curb_fit"]) == 2 and src["curb_fit"][0] == {"seconds": 1.0}
    assert src["assisted"] and "edited" not in src and "curve_fit" not in src and src["tool"] == "curb_trace"
