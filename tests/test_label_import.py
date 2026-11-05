"""Importing labels made elsewhere: the DXF reader (entities, object coordinate systems, bulges, meshes, units), the
profiles, and the import as annotations."""

import json

import numpy as np
import pytest

from pclabel.io.dxf import ocs_to_wcs, read_dxf
from pclabel.store.annotations import SCHEMA, Annotations, Marker, Polyline
from pclabel.store.importer import ImportParams, import_scan
from pclabel.store.label_import import aci_rgb, best_profile, coverage, load_profiles, read_source, to_directory
from pclabel.store.project import Project
from pclabel.store.tiles import TilingParams
from pclabel.synth import make_street, write_street_e57

TILT = np.array([0.1155026804983741, 0.4474006107019116, 0.8868437429114822])  # an extrusion from a real drawing


def dxf_text(units=6, x0=0.0, y0=0.0, z0=0.0) -> str:
    """A small ASCII DXF: one entity of each kind the reader knows, and a TEXT it skips."""
    p = []

    def add(*kv):
        for k, v in zip(kv[::2], kv[1::2]):
            p.extend([str(k), str(v)])

    add(0, "SECTION", 2, "HEADER", 9, "$ACADVER", 1, "AC1032", 9, "$INSUNITS", 70, units, 0, "ENDSEC")
    add(0, "SECTION", 2, "TABLES", 0, "TABLE", 2, "LAYER")
    for name, aci in (("TOP_OF_CURB", 1), ("PAINT_STRIPE", 4), ("LIGHT_POLE", 5), ("ODD", 30)):
        add(0, "LAYER", 2, name, 70, 0, 62, aci, 6, "Continuous")
    add(0, "ENDTAB", 0, "ENDSEC", 0, "SECTION", 2, "ENTITIES")
    # a closed 3D polyline (flag 1 + 8)
    add(0, "POLYLINE", 5, "A1", 8, "TOP_OF_CURB", 66, 1, 10, 0, 20, 0, 30, 0, 70, 9)
    for x, y, z in ((0, 0, 0.1), (4, 0, 0.2), (4, 3, 0.3)):
        add(0, "VERTEX", 5, "V", 8, "TOP_OF_CURB", 10, x0 + x, 20, y0 + y, 30, z0 + z, 70, 32)
    add(0, "SEQEND", 5, "S", 8, "TOP_OF_CURB")
    # a tilted LWPOLYLINE: OCS coordinates of world points, at their elevation
    world = np.array([[x0 + 1, y0 + 1, z0 + 0.0], [x0 + 2, y0 + 1.5, z0 + 0.01]])
    n = TILT
    ax = np.cross((0, 0, 1), n)
    ax /= np.linalg.norm(ax)
    ay = np.cross(n, ax)
    ocs = np.c_[world @ ax, world @ ay]
    elev = float(world[0] @ n)
    world[1] = ocs[1, 0] * ax + ocs[1, 1] * ay + elev * n  # both on one plane of the OCS
    add(0, "LWPOLYLINE", 5, "B2", 8, "PAINT_STRIPE", 90, 2, 70, 128, 38, repr(elev))
    for u, v in ocs:
        add(10, repr(float(u)), 20, repr(float(v)))
    add(210, repr(float(n[0])), 220, repr(float(n[1])), 230, repr(float(n[2])))
    # an LWPOLYLINE with a half-circle bulge, flat
    add(0, "LWPOLYLINE", 5, "B3", 8, "PAINT_STRIPE", 90, 2, 70, 0, 38, z0, 10, x0, 20, y0, 42, 1.0, 10, x0 + 2, 20, y0)
    add(0, "LINE", 5, "C4", 8, "PAINT_STRIPE", 10, x0, 20, y0, 30, z0, 11, x0 + 1, 21, y0, 31, z0)
    add(0, "3DFACE", 5, "D5", 8, "ODD", 10, x0, 20, y0, 30, z0, 11, x0 + 1, 21, y0, 31, z0, 12, x0, 22, y0 + 1, 32, z0,
        13, x0, 23, y0 + 1, 33, z0)  # a triangle (the 4th corner repeats the 3rd)
    add(0, "INSERT", 5, "E6", 8, "LIGHT_POLE", 2, "SYM003", 10, x0 + 3, 20, y0 + 1, 30, z0, 41, 0.2, 42, 0.2, 43, 0.2)
    add(0, "TEXT", 5, "F7", 8, "ODD", 10, x0, 20, y0, 30, z0, 40, 1, 1, "hello")
    # a polyface mesh: 4 positions, 2 triangles
    add(0, "POLYLINE", 5, "G8", 8, "ODD", 66, 1, 70, 64, 71, 4, 72, 2)
    for x, y in ((0, 0), (1, 0), (1, 1), (0, 1)):
        add(0, "VERTEX", 8, "ODD", 10, x0 + x, 20, y0 + y, 30, z0, 70, 192)
    for a, b, c in ((1, 2, 3), (1, 3, -4)):
        add(0, "VERTEX", 8, "ODD", 10, 0, 20, 0, 30, 0, 70, 128, 71, a, 72, b, 73, c)
    add(0, "SEQEND", 8, "ODD")
    add(0, "ENDSEC", 0, "EOF")
    return "\n".join(p) + "\n"


def test_ocs_to_wcs():
    assert np.allclose(ocs_to_wcs([[1, 2, 3]], (0, 0, 1)), [[1, 2, 3]])
    w = ocs_to_wcs([[5.0, -2.0, 7.0], [1.0, 1.0, 7.0]], TILT)
    assert np.allclose(w @ (TILT / np.linalg.norm(TILT)), 7.0)  # the elevation is the distance along the extrusion
    assert np.isclose(np.linalg.norm(w[0] - w[1]), np.hypot(4, 3))  # lengths kept
    flipped = ocs_to_wcs([[1.0, 2.0, 0.0]], (0, 0, -1))  # a mirrored 2D drawing: x flips
    assert np.allclose(flipped, [[-1, 2, 0]])


def test_read_dxf(tmp_path):
    f = tmp_path / "t.dxf"
    f.write_text(dxf_text(x0=586000.0, y0=4141000.0, z0=-20.0), encoding="utf-8")
    d = read_dxf(f)
    assert d.version == "AC1032" and d.units == "metres" and dict(d.skipped) == {"TEXT": 1}
    by = {(e.handle, e.extra.get("mesh_face")): e for e in d.entities}
    curb = by[("A1", None)]
    assert curb.closed and curb.type == "POLYLINE" and np.allclose(curb.vertices[2], [586004, 4141003, -19.7])
    tilted = by[("B2", None)].vertices
    assert np.allclose(tilted[0], [586001, 4141001, -20.0], atol=1e-6)  # back in the world, not kilometres away
    arc = by[("B3", None)].vertices  # bulge 1: a half circle about (x0 + 1, y0), clockwise of the chord's left
    assert np.allclose(np.hypot(arc[:, 0] - 586001, arc[:, 1] - 4141000), 1.0) and arc[:, 1].min() < 4141000 - 0.99
    assert len(by[("D5", None)].vertices) == 3 and by[("D5", None)].closed
    pole = by[("E6", None)]
    assert pole.vertices.shape == (1, 3) and pole.extra["block"] == "SYM003" and pole.extra["scale"] == [0.2] * 3
    faces = [e for e in d.entities if "mesh_face" in e.extra]
    assert len(faces) == 2 and all(e.closed for e in faces) and np.allclose(faces[1].vertices[2], [586000, 4141001, -20])
    assert d.layer_colors["PAINT_STRIPE"] == 4
    f.write_text(dxf_text(units=2), encoding="utf-8")  # feet
    feet = read_dxf(f)
    line = next(e for e in feet.entities if e.type == "LINE")
    assert feet.units == "feet" and np.isclose(line.vertices[1, 0], 0.3048)
    f.write_bytes(b"AutoCAD Binary DXF\r\n\x1a\x00")
    with pytest.raises(ValueError, match="binary"):
        read_dxf(f)


def test_aci_colours():
    assert aci_rgb(1) == "#ff0000" and aci_rgb(7) == "#ffffff" and aci_rgb(None) == "#ffffff"
    assert aci_rgb(30).startswith("#ff") and aci_rgb(140) != aci_rgb(150)  # 30: orange; hues step by 15 degrees


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    d = tmp_path_factory.mktemp("label_import")
    src = write_street_e57(make_street(n_target=30_000, seed=4), d / "street.e57")
    return Project(import_scan(src, projects_dir=d / "projects",
                               params=ImportParams(tiling=TilingParams(max_points=8_000))))  # fmt: skip


def test_profiles_and_the_import_as_annotations(project, tmp_path):
    profiles = load_profiles()
    rock = next(p for p in profiles if p.name == "ROCK planimetrics")
    assert rock.layers["TOP_OF_CURB"] == "curb_top" and rock.layers["PAINT_STRIPE"] == "road_line"
    assert rock.layers["GUTTER"] == "gutter" and rock.layers["MANHOLE"] == "cover" and rock.layers["RAILS"] == ""
    assert len(rock.layers) == 53  # every layer ROCK lists; 10 rural or US-only ones without a label
    assert sum(1 for v in rock.layers.values() if not v) == 10 and "Singapore" in rock.notes["RAILS"]
    from pclabel.taxonomy import load_taxonomy

    tax = load_taxonomy()
    known = {c["name"] for c in tax.classes} | {f["name"] for f in tax.flags} | {""}
    assert set(rock.layers.values()) <= known  # every label in the profile is a taxonomy class or flag
    # and fits what the layer holds: the curb's edges are lines, a manhole a marker
    assert tax.only("curb_top") == "line" and tax.only("cover") == "marker" and tax.only("lamp_post") is None
    c = project.offset + np.asarray(project.meta["bounds"]["min"]) + 1.0  # inside the scan
    f = tmp_path / "rock.dxf"
    f.write_text(dxf_text(x0=c[0], y0=c[1], z0=c[2]), encoding="utf-8")
    src = read_source(f)
    assert [lay.name for lay in src.layers] == ["TOP_OF_CURB", "PAINT_STRIPE", "LIGHT_POLE", "ODD"]
    assert best_profile(src, profiles) is rock
    cov = coverage(src, project)
    assert cov["vertices"] == sum(lay.n_vertices() for lay in src.layers) and cov["inside"] == cov["vertices"]
    from pclabel.store.label_import import apply_profile

    apply_profile(src, rock)
    assert [lay.label for lay in src.layers] == ["curb_top", "road_line", "lamp_post", ""]  # ODD is not in the profile
    top = to_directory(src, ["TOP_OF_CURB", "PAINT_STRIPE", "LIGHT_POLE"])
    assert top.name == "rock.dxf (imported)" and [d.name for d in top.children] == ["TOP_OF_CURB", "PAINT_STRIPE", "LIGHT_POLE"]
    t = Annotations()
    t.root.append(top)
    curb = top.children[0].children[0]
    assert isinstance(curb, Polyline) and curb.closed and len(curb.polyline()) == 4
    assert t.label_of(curb.id)[0] == "curb_top"
    assert curb.source["handle"] == "A1" and curb.source["layer"] == "TOP_OF_CURB" and curb.name == "POLYLINE A1"
    pole = top.children[2].children[0]  # a map symbol: a marker
    assert isinstance(pole, Marker) and pole.describe() == "marker, SYM003" and pole.name == "SYM003 E6"
    assert pole.position.shape == (3,) and pole.source["block"] == "SYM003"
    # kept by the project, exported and counted by label
    t.save(project)
    u, warnings = Annotations.load(project)
    assert not warnings and json.loads((project.path / "annotations.json").read_text(encoding="utf-8"))["schema"] == SCHEMA
    back = u.root[0].children[0].children[0]
    assert back.kind == "polyline" and np.array_equal(back.vertices, curb.vertices) and back.source == curb.source
    pole_back = u.root[0].children[2].children[0]
    assert pole_back.kind == "marker" and np.array_equal(pole_back.position, pole.position)
    assert pole_back.source == pole.source
    out = tmp_path / "labels.json"
    t.export_labels(out, project, {"curb": 2, "curb_top": 13, "road_line": 10, "lamp_post": 30},
                    parents={"curb_top": "curb", "lamp_post": "pole"})  # fmt: skip
    doc = json.loads(out.read_text(encoding="utf-8"))
    by = {e["label"]: e for e in doc["labels"]}
    assert by["road_line"]["counts"] == {"segment": 0, "curve": 0, "polyline": 3, "marker": 0, "points": 0}
    assert by["lamp_post"]["counts"]["marker"] == 1 and by["lamp_post"]["items"][0]["position"] == pole.position.tolist()
    assert by["curb_top"]["path"] == ["curb", "curb_top"] and by["curb_top"]["parent"] == "curb"
    roll = {e["label"]: e for e in doc["rollup"]}  # curb and pole are not used, but are above classes that are
    assert roll["curb"]["labels"] == ["curb_top"] and roll["curb"]["items"] == 1 and roll["pole"]["items"] == 1
    # schema 6 kept a map symbol as a one-vertex polyline: read as a marker
    d6 = json.loads((project.path / "annotations.json").read_text(encoding="utf-8"))
    d6["schema"] = "pclabel.annotations/6"
    sym = d6["tree"][0]["children"][2]["children"][0]
    pos = sym.pop("position")
    sym.update(type="polyline", vertices=[pos], closed=False, length_m=0.0)
    old, _ = Annotations.from_doc(d6, project)
    assert old.root[0].children[2].children[0].kind == "marker"
    assert old.root[0].children[2].children[0].position.tolist() == pos
