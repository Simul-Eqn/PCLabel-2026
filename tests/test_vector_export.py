"""Vector export (io/vector_export.py): DXF layers per label that read back exactly, and GeoJSON with attributes."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from pclabel.io.dxf import read_dxf
from pclabel.io.vector_export import aci_of, layer_name, write_vectors
from pclabel.store.annotations import Annotations, Marker, PointSet, Polyline, Segment
from pclabel.taxonomy import load_taxonomy

OFF = np.array([586417.0, 4141263.0, -1.0])


def model():
    t = Annotations()
    d = t.new_directory("curbs")
    d.label = "curb_top"
    lip = t.add(Polyline(OFF + [[0, 0, 0.15], [1.25, 0.5, 0.151], [2.5, 0.75, 0.149]], source={"tool": "curb_trace",
                                                                                                 "assisted": True}))  # fmt: skip
    t.move([lip.id], d.id)
    ring = t.add(Polyline(OFF + [[5, 5, 0], [5.6, 5, 0], [5.6, 5.6, 0], [5, 5.6, 0]], closed=True), "cover outline")
    ring.label = "cover"
    seg = t.add(Segment(OFF + [0, 1, 0], OFF + [3, 1, 0.01]))
    seg.label = "road_line"
    mk = t.add(Marker(OFF + [7.0, 8.0, 0.02], {"tool": "marker", "across": "circle", "points": 120}), "LP 1")
    mk.label = "lamp_post"
    mk.uncertain = True
    mk.attributes = {"asset_id": "LP-0001", "needs": "check"}
    t.add(Polyline(OFF + [[9, 9, 0], [9, 10, 0]]))  # no label
    t.add(PointSet(np.arange(10)), "a set").label = "road"  # points: not a vector
    return t


def test_dxf_layers_read_back(tmp_path):
    t = model()
    f = tmp_path / "out.dxf"
    res = write_vectors(f, t, None, {"curb_top": "#FF8A80", "lamp_post": "#E040FB"})
    assert res["entities"] == 5 and res["layers"] == {"CURB_TOP": 1, "COVER": 1, "ROAD_LINE": 1, "LAMP_POST": 1,
                                                       "UNLABELLED": 1}  # fmt: skip
    d = read_dxf(f)
    assert d.version == "AC1009"
    by = {e.layer: e for e in d.entities}
    lip = t.root[0].children[0]
    assert by["CURB_TOP"].type == "POLYLINE" and np.array_equal(by["CURB_TOP"].vertices, lip.vertices)  # exact
    assert by["COVER"].closed and len(by["COVER"].vertices) == 4
    assert by["ROAD_LINE"].type == "LINE" and by["LAMP_POST"].type == "POINT"
    assert np.array_equal(by["LAMP_POST"].vertices[0], OFF + [7.0, 8.0, 0.02])
    assert d.layer_colors["LAMP_POST"] == aci_of("#E040FB") and d.layer_colors["UNLABELLED"] == 7
    assert layer_name("road line / x") == "ROAD_LINE___X" and layer_name("") == "UNLABELLED"
    with pytest.raises(ValueError, match="no segments"):
        write_vectors(f, t, None, {}, labels=["road"])  # only a point set has it


def test_geojson_with_attributes(tmp_path):
    t = model()
    proj = SimpleNamespace(meta={"source": {"path": "x/street.e57", "sha256": "ab"}}, n=10, offset=OFF)
    f = tmp_path / "out.geojson"
    res = write_vectors(f, t, proj, {}, labels=["curb_top", "lamp_post", "cover"], taxonomy=load_taxonomy())
    assert res == {"features": 3, "by_kind": {"polyline": 2, "marker": 1}}
    doc = json.loads(f.read_text(encoding="utf-8"))
    assert doc["pclabel"]["schema"] == "pclabel.vector-export/1" and "not longitude" in doc["pclabel"]["coordinates"]
    by = {ft["properties"]["label"]: ft for ft in doc["features"]}
    lp = by["lamp_post"]
    assert lp["geometry"] == {"type": "Point", "coordinates": (OFF + [7.0, 8.0, 0.02]).tolist()}
    assert lp["properties"]["attributes"]["asset_id"] == "LP-0001" and lp["properties"]["uncertain"]
    assert lp["properties"]["label_path"] == ["pole", "lamp_post"] and not lp["properties"]["assisted"]
    lip = by["curb_top"]["properties"]
    assert lip["assisted"] and lip["path"] == ["curbs"] and lip["length_m"] == pytest.approx(1.3463 + 1.2748, abs=1e-3)
    ring = by["cover"]["geometry"]["coordinates"]
    assert len(ring) == 5 and ring[0] == ring[-1] and by["cover"]["properties"]["closed"]
