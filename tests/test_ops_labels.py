"""Annotations resolved to one class and a flags byte per point (pclabel.ops.labels)."""

from types import SimpleNamespace

import numpy as np
import pytest

from pclabel.ops.labels import export_labels, resolve, to_source_order
from pclabel.store.annotations import Annotations, PointSet
from pclabel.taxonomy import load_taxonomy


def _model():
    t = Annotations()
    road = t.add(PointSet(np.arange(0, 100, dtype=np.int64)))
    line = t.add(PointSet(np.arange(40, 50, dtype=np.int64)))  # inside the road: more specific on its branch
    pave = t.add(PointSet(np.arange(95, 130, dtype=np.int64)))  # overlaps the road's end: another branch
    excl = t.add(PointSet(np.arange(120, 125, dtype=np.int64)))
    note = t.add(PointSet(np.arange(200, 205, dtype=np.int64)))
    road.label, line.label, pave.label, excl.label, note.label = "road", "road_line", "pavement", "exclude", "lane?"
    return t


def test_more_specific_wins_on_a_branch_and_other_branches_conflict():
    tax = load_taxonomy()
    res = resolve(_model(), tax, 300)
    cid = tax.class_id
    assert (res.classes[40:50] == cid("road_line")).all()  # not overwritten by the larger road set
    assert (res.classes[:40] == cid("road")).all() and (res.classes[50:95] == cid("road")).all()
    assert (res.classes[95:130] == cid("pavement")).all()  # the smaller set wins a conflict
    assert res.conflicts == 5 and res.conflict_pairs == {"pavement / road": 5}
    assert (res.flags[120:125] == 1 << tax.flag_bit("exclude")).all() and res.flags[:120].sum() == 0
    assert res.free_text == {"lane?": 5} and (res.classes[200:205] == 0).all()
    assert (res.classes[130:200] == 0).all()  # in no set: unassessed, not a negative


def test_the_order_of_the_sets_does_not_matter_on_a_branch():
    tax = load_taxonomy()
    t = Annotations()
    small = t.add(PointSet(np.arange(10, 20, dtype=np.int64)))
    big = t.add(PointSet(np.arange(0, 20, dtype=np.int64)))
    small.label, big.label = "road", "road_line"  # the larger set is the more specific one
    res = resolve(t, tax, 30)
    assert (res.classes[:20] == tax.class_id("road_line")).all() and res.conflicts == 0


def test_export_in_source_order(tmp_path):
    tax = load_taxonomy()
    n = 300
    rng = np.random.default_rng(0)
    source_index = rng.permutation(n).astype(np.uint32)  # store position -> source index
    project = SimpleNamespace(path=tmp_path / "proj", n=n, source_index=source_index,
                              meta={"source": {"path": "x.e57", "sha256": "abc"}})  # fmt: skip
    (tmp_path / "proj").mkdir()
    doc = export_labels(tmp_path / "out" / "x", project, _model(), tax)
    classes = np.load(tmp_path / "out" / "x.classes.npy")
    store = resolve(_model(), tax, n).classes
    assert np.array_equal(classes[source_index], store)
    assert np.array_equal(to_source_order(store, source_index), classes)
    assert doc["classes"]["road_line"] == 10 and doc["conflicts"] == 5 and doc["source"]["sha256"] == "abc"
    assert doc["unassessed"] == int((store == 0).sum())
    with pytest.raises(ValueError, match="outside the project"):
        export_labels(tmp_path / "proj" / "labels", project, _model(), tax)
    export_labels(tmp_path / "proj" / "auto" / "run1" / "labels", project, _model(), tax)  # auto/ is allowed


def test_tool_labels_come_from_the_taxonomy(tmp_path):
    from pclabel.taxonomy import TOOL_LABELS

    tax = load_taxonomy()
    assert tax.tool_label("curb_trace_lip") == "curb" and tax.tool_label("curb_trace_toe") == ""
    for key in TOOL_LABELS:  # every tool label names a class (or none)
        for lab in np.atleast_1d(tax.tool_label(key)):
            assert lab == "" or tax.by_name(lab) is not None, (key, lab)
    f = tmp_path / "t.yaml"
    f.write_text("schema: pclabel.taxonomy/2\nclasses:\n  - {id: 0, name: unassessed, color: '#000000'}\n"
                 "tools: {curb_trace_lip: kerb_lip, nonsense: x}\n", encoding="utf-8")  # fmt: skip
    with pytest.raises(ValueError, match="unknown tools"):
        load_taxonomy(f)
    f.write_text("schema: pclabel.taxonomy/2\nclasses:\n  - {id: 0, name: unassessed, color: '#000000'}\n"
                 "tools: {curb_trace_lip: kerb_lip}\n", encoding="utf-8")  # fmt: skip
    t = load_taxonomy(f)
    assert t.tool_label("curb_trace_lip") == "kerb_lip" and t.tool_label("marking_trace") == "road_line"
