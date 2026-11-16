"""Curve geometry: the plane perpendicular to the height axis, projection, the Bézier curve and its handles."""

import numpy as np
import pytest

from pclabel.analysis import curves
from pclabel.store.annotations import Curve

UP = np.array([0.0, 0.0, 1.0])


def tilted():
    n = np.array([0.03, -0.02, 1.0])
    return n / np.linalg.norm(n)


@pytest.mark.parametrize("normal", [UP, tilted()])
def test_plane_is_perpendicular_to_the_height_axis_at_the_mean_height(normal):
    rng = np.random.default_rng(0)
    pts = np.c_[rng.uniform(0, 10, 6), rng.uniform(0, 5, 6), rng.normal(1.0, 0.05, 6)]
    origin = np.array([1.0, 2.0, 0.5])
    c = Curve.fit(pts, normal * 3, origin, mode="axis")  # the axis need not be unit length
    assert np.allclose(c.normal, normal) and c.tilt_deg() == pytest.approx(0, abs=1e-6)
    assert c.height() == pytest.approx(np.mean((pts - origin) @ normal))
    assert np.allclose((c.nodes - origin) @ normal, c.height())  # every node on the plane
    assert np.allclose(np.cross(c.nodes - pts, normal), 0, atol=1e-12)  # moved along the axis only (closest point)
    assert np.array_equal(c.picked, pts)
    assert np.allclose((c.polyline() - origin) @ normal, c.height())  # the curve stays in the plane


def test_curve_passes_through_its_nodes_and_is_straight_where_they_are():
    nodes = np.array([[0.0, 0, 0], [1.0, 1, 0], [3.0, 3, 0], [3.5, 3.5, 0]])
    nan = np.full_like(nodes, np.nan)
    line = curves.bezier(nodes, nan, nan, step=0.02)
    for p in nodes:
        assert np.min(np.linalg.norm(line - p, axis=1)) < 1e-12
    assert np.allclose(line[:, 0], line[:, 1])  # collinear nodes: a straight line
    assert np.all(np.diff(line[:, 0]) > 0)  # no loops or overshoot
    assert np.linalg.norm(np.diff(line, axis=0), axis=1).max() <= 0.02 + 1e-9
    two = curves.bezier(nodes[:2], nan[:2], nan[:2])
    assert curves.length(two) == pytest.approx(np.sqrt(2))


def test_a_dragged_handle_sets_the_gradient_at_its_node():
    c = Curve.fit(np.array([[0.0, 0, 0], [2.0, 0, 0], [4.0, 0, 0]]), UP, np.zeros(3))
    c.activate(1)
    assert c.manual().tolist() == [False, True, False]
    c.drag_handle(1, +1, c.nodes[1] + np.array([1.0, 1.0, 0.7]))  # the height part is dropped: handles stay flat
    h_in, h_out = c.handles()
    assert np.allclose(h_out[1], [1.0, 1.0, 0.0])
    assert np.allclose(np.cross(h_in[1], h_out[1]), 0) and np.dot(h_in[1], h_out[1]) < 0  # one line through the node
    assert np.linalg.norm(h_in[1]) == pytest.approx(2 / 3)  # the other handle keeps its length
    line = c.polyline(0.001)
    k = np.argmin(np.linalg.norm(line - c.nodes[1], axis=1))
    d = line[k + 1] - line[k - 1]
    assert np.allclose(d / np.linalg.norm(d), [np.sqrt(0.5), np.sqrt(0.5), 0], atol=1e-3)  # the curve's gradient
    c.make_auto(1)
    assert not c.manual().any() and np.allclose(c.polyline()[:, 1], 0)


def test_points_are_added_where_the_curve_passes_closest_and_removed():
    pts = np.array([[0.0, 0, 1.0], [4.0, 0, 1.1], [8.0, 0, 0.9]])
    c = Curve.fit(pts, UP, np.zeros(3), mode="axis")
    c.drag_handle(2, -1, c.nodes[2] + (-1.0, 0.5, 0))  # a manual node: it keeps its handles
    keep = c.h_in[2].copy()
    assert c.insert(np.array([2.1, 0.3, 1.4])) == 1  # between the first two
    assert c.insert(np.array([-1.5, 0.1, 1.0])) == 0  # beyond the start
    assert c.insert(np.array([9.5, -0.2, 1.0])) == 5  # beyond the end
    assert c.insert(np.array([6.0, -0.1, 1.0])) == 4  # between the old 2nd and 3rd
    assert np.allclose(c.nodes[:, 0], [-1.5, 0.0, 2.1, 4.0, 6.0, 8.0, 9.5])
    assert c.height() == pytest.approx(np.mean(c.picked[:, 2])) and np.allclose(c.nodes[:, 2], c.height())  # refit
    assert c.manual().tolist() == [False] * 5 + [True, False] and np.allclose(c.h_in[5], keep)
    assert len(c.picked_source) == 7
    c.remove(2)
    assert np.allclose(c.nodes[:, 0], [-1.5, 0.0, 4.0, 6.0, 8.0, 9.5])
    assert c.height() == pytest.approx(np.mean(c.picked[:, 2]))
    for _ in range(4):
        c.remove(0)
    with pytest.raises(ValueError):
        c.remove(0)  # at least two stay


def test_by_default_the_plane_goes_through_both_ends_and_turns_to_fit_the_rest():
    """Points on a plane tilted 20 degrees about the line through the ends (with 3 mm noise): the fit keeps the ends
    exactly and finds the tilt."""
    rng = np.random.default_rng(1)
    a, b = np.array([0.0, 0.0, 0.0]), np.array([10.0, 0.0, 0.5])
    u = (b - a) / np.linalg.norm(b - a)
    t = np.radians(20)
    across = np.cos(t) * np.array([0.0, 1.0, 0.0]) + np.sin(t) * np.array([0.0, 0.0, 1.0])  # in the plane, across
    inner = a + rng.uniform(0, 10, (8, 1)) * u + rng.uniform(-3, 3, (8, 1)) * across + rng.normal(0, 0.003, (8, 3))
    pts = np.vstack([a, inner, b])
    c = Curve.fit(pts, UP, np.zeros(3))
    assert c.mode == "ends"
    assert np.allclose(c.nodes[0], a, atol=1e-12) and np.allclose(c.nodes[-1], b, atol=1e-12)  # the ends stay put
    true_n = np.cross(u, across)
    true_n /= np.linalg.norm(true_n)  # across is not quite perpendicular to u (the ends differ in height)
    assert abs(abs(np.dot(c.normal, true_n)) - 1) < 1e-4 and np.dot(c.normal, UP) > 0  # the tilt, pointing up
    assert c.spread() < 0.012
    line = c.polyline()
    assert np.allclose(line[0], a) and np.allclose(line[-1], b)

    c.set_mode("axis")  # the other mode: perpendicular to the height axis; the ends move onto that plane
    assert np.allclose(c.normal, UP) and not np.allclose(c.nodes[0], a)
    c.set_mode("ends")
    assert np.allclose(c.nodes[0], a, atol=1e-12)


def test_ends_mode_without_inner_points_and_with_a_new_end():
    two = Curve.fit(np.array([[0.0, 0, 0], [4.0, 3.0, 1.0]]), UP, np.zeros(3))  # just the ends
    d = np.array([4.0, 3.0, 1.0]) / np.linalg.norm([4.0, 3.0, 1.0])
    assert abs(np.dot(two.normal, d)) < 1e-12  # contains the line through the ends ...
    assert np.dot(two.normal, UP) == pytest.approx(np.sqrt(1 - d[2] ** 2))  # ... and is as flat as that allows
    c = Curve.fit(np.array([[0.0, 0, 0], [2.0, 0.1, 0.05], [4.0, 0, 0]]), UP, np.zeros(3))
    c.drag_handle(1, 1, c.nodes[1] + (0.5, 0.5, 0.3))
    assert c.insert(np.array([6.0, 0.5, 0.4])) == 3  # beyond the end: the new last point
    assert np.allclose(c.nodes[-1], [6.0, 0.5, 0.4], atol=1e-12) and np.allclose(c.nodes[0], 0, atol=1e-12)
    assert abs(c.h_out[1] @ c.normal) < 1e-12 and abs(c.h_in[1] @ c.normal) < 1e-12  # handles kept in the plane


def test_a_closed_loop_joins_its_last_point_back_to_the_first():
    pts = np.array([[0.0, 0, 0], [4.0, 0, 0], [4.0, 3.0, 0], [0.0, 3.0, 0]])
    c = Curve.fit(pts, UP, np.zeros(3), mode="axis", closed=True)
    line = c.polyline(0.01)
    assert np.allclose(line[0], line[-1]) and np.allclose(line[0], pts[0])  # it ends where it starts
    for p in pts:
        assert np.min(np.linalg.norm(line - p, axis=1)) < 1e-12
    h_in, h_out = c.handles()
    assert np.allclose(np.cross(h_in[0], h_out[0]), 0) and np.dot(h_in[0], h_out[0]) < 0  # smooth at the join too
    start, end = line[1] - line[0], line[-1] - line[-2]
    assert np.dot(start, end) / np.linalg.norm(start) / np.linalg.norm(end) > 0.999
    open_len = Curve.fit(pts, UP, np.zeros(3), mode="axis").length()
    assert c.length() > open_len + 2.5  # the closing piece (about 3 m)
    assert c.insert(np.array([-0.3, 1.5, 0.0])) == 4  # on the closing piece: after the last point
    c.remove(0)
    c.remove(0)
    with pytest.raises(ValueError):
        c.remove(0)  # a loop keeps three
    c.set_closed(False)
    assert not np.allclose(c.polyline()[0], c.polyline()[-1])
    with pytest.raises(ValueError):
        Curve.fit(pts[:2], UP, np.zeros(3), closed=True)


def test_fit_all_points_mode():
    rng = np.random.default_rng(3)
    n_true = np.array([0.1, -0.25, 1.0])
    n_true /= np.linalg.norm(n_true)
    a = np.cross(n_true, [1.0, 0, 0])
    a /= np.linalg.norm(a)
    b = np.cross(n_true, a)
    pts = np.array([2.0, 1.0, 0.5]) + rng.uniform(-4, 4, (12, 1)) * a + rng.uniform(-4, 4, (12, 1)) * b
    pts += rng.normal(0, 0.003, pts.shape)
    c = Curve.fit(pts, UP, np.zeros(3), mode="fit")
    assert abs(abs(np.dot(c.normal, n_true)) - 1) < 1e-4 and np.dot(c.normal, UP) > 0
    assert np.allclose(c.point, pts.mean(0)) and c.spread() < 0.012
    assert np.isclose(c.tilt_deg(), np.degrees(np.arccos(n_true[2])), atol=0.1)
    line = Curve.fit(np.array([[0.0, 0, 0], [1.0, 1.0, 0.2], [2.0, 2.0, 0.4]]), UP, np.zeros(3), mode="fit")
    d = np.array([1.0, 1.0, 0.2]) / np.linalg.norm([1.0, 1.0, 0.2])
    assert abs(np.dot(line.normal, d)) < 1e-9 and np.dot(line.normal, UP) == pytest.approx(np.sqrt(1 - d[2] ** 2))
    assert np.allclose(line.nodes, line.picked)  # points on a line stay where they were clicked


def test_a_free_curve_has_no_plane_and_follows_a_slope():
    """Mode "free" (no plane): the nodes stay where they are, up a 1 m rise; a point added keeps its height; a node
    dragged moves level (in its drag plane across the height axis), its handles with it; read back from a document."""
    x = np.linspace(0, 10, 6)
    pts = np.c_[x, np.sin(x / 3), x / 10]  # rising 1 m over 10 m, and curving
    c = Curve.fit(pts, [0, 0, 1], [0, 0, 0], mode="free")
    assert np.allclose(c.nodes, pts) and c.spread() == 0.0 and c.heights() == pytest.approx((0.0, 1.0))
    line = c.polyline()
    assert line[:, 2].min() == pytest.approx(0) and line[:, 2].max() == pytest.approx(1.0)
    i = c.insert([5.0, np.sin(5 / 3), 0.5])
    assert c.nodes[i] == pytest.approx([5.0, np.sin(5 / 3), 0.5])
    c.activate(i)
    h0 = c.h_out[i].copy()
    c.move_node(i, [5.0, 3.0, 9.0])  # level: the height stays
    assert c.nodes[i] == pytest.approx([5.0, 3.0, 0.5]) and np.allclose(c.h_out[i], h0)
    c.drag_handle(i, 1, c.nodes[i] + [1.0, 0.0, 2.0])
    assert c.h_out[i][2] == pytest.approx(0.0)  # handles are dragged level too


def test_a_planar_curve_node_moves_in_its_plane():
    pts = np.array([[0, 0, 0], [1, 1, 0.0], [2, 0, 0]])
    c = Curve.fit(pts, [0, 0, 1], [0, 0, 0], mode="axis")
    c.move_node(1, [1, 2, 5.0])
    assert c.nodes[1] == pytest.approx([1, 2, 0.0])


def test_a_traced_line_as_a_curve_within_a_tolerance():
    """Curve.from_line: as few of the vertices as keep the curve within the tolerance of every one, no plane."""
    from scipy.spatial import cKDTree

    s = np.arange(0, 30, 0.3)
    rng = np.random.default_rng(1)
    v = np.c_[s, 4 * np.sin(s / 6), 0.04 * s + 0.003 * rng.standard_normal(len(s))]  # a kerb up a slope, with noise
    for tol in (0.01, 0.02):
        c = Curve.from_line(v, [0, 0, 1], [0, 0, 0], tolerance=tol, source={"tool": "curb_trace"})
        fit = c.source["curve_fit"]
        assert c.mode == "free" and c.source["tool"] == "curb_trace" and fit["vertices"] == len(v)
        assert fit["nodes"] == len(c.nodes) < len(v) and fit["largest_off"] <= tol
        assert cKDTree(c.polyline(0.005)).query(v)[0].max() <= tol + 0.003
        assert np.allclose(c.nodes[[0, -1]], v[[0, -1]])
    # a loop stays a loop
    t = np.linspace(0, 2 * np.pi, 80, endpoint=False)
    ring = np.c_[3 * np.cos(t), 3 * np.sin(t), 0.2 * np.sin(t)]
    c = Curve.from_line(ring, [0, 0, 1], [0, 0, 0], tolerance=0.01, closed=True)
    assert c.closed and c.source["curve_fit"]["largest_off"] <= 0.01 and len(c.nodes) < 80


def test_a_free_curve_and_its_source_are_saved_and_read_back():
    from pclabel.store.annotations import _node_doc, _node_from_doc_body, clone_nodes

    pts = np.array([[0, 0, 0], [1, 1, 0.3], [2, 0, 0.8], [3, -1, 0.5]])
    c = Curve.fit(pts, [0, 0, 1], [0, 0, 0], mode="free")
    c.source = {"tool": "curb_trace", "assisted": True}
    c.activate(1)
    c.drag_handle(1, 1, c.nodes[1] + [0.5, 0.2, 0])
    doc = _node_doc(c, None, "file")
    assert doc["plane"]["mode"] == "free" and doc["source"]["tool"] == "curb_trace"
    d = _node_from_doc_body(doc, None, None, True, [])
    assert d.mode == "free" and d.source == c.source and np.allclose(d.nodes, pts)
    assert np.allclose(d.polyline(), c.polyline())
    k = clone_nodes([c])[0]
    k.source["x"] = 1
    assert "x" not in c.source


def test_smoothing_a_line():
    """curves.smooth_line: noise along a straight line averaged out, no point moved more than the limit, the ends
    kept; a loop stays closed; Annotations.smooth keeps the item's place and mode and records it."""
    from pclabel.store.annotations import Annotations, Polyline

    rng = np.random.default_rng(3)
    x = np.arange(0, 20, 0.3)
    v = np.c_[x, 0.02 * rng.standard_normal(len(x)), 0.1 + 0.01 * rng.standard_normal(len(x))]
    s, shift = curves.smooth_line(v, 1.0, 0.05)
    assert np.allclose(s[[0, -1]], v[[0, -1]]) and shift <= 0.05 + 1e-9
    p = curves.resample(v, 0.02)
    assert np.linalg.norm(s - p, axis=1).max() <= 0.05 + 1e-9
    inner = (s[:, 0] > 2) & (s[:, 0] < 18)
    assert np.abs(s[inner, 1]).max() < 0.4 * np.abs(v[:, 1]).max() and np.abs(s[inner, 2] - 0.1).max() < 0.01
    assert curves.wiggle(curves.resample(s, 0.1)) < 0.3 * curves.wiggle(curves.resample(v, 0.1))
    # a real corner is rounded by no more than the limit
    corner = np.r_[np.c_[np.arange(0, 5, 0.1), np.zeros(50)], np.c_[np.full(50, 5.0), np.arange(0, 5, 0.1)]]
    corner = np.c_[corner, np.zeros(len(corner))]
    s, shift = curves.smooth_line(corner, 1.0, 0.03)
    assert shift == pytest.approx(0.03) and np.hypot(s[:, 0] - 5, s[:, 1]).min() <= 0.031
    # a loop
    t = np.linspace(0, 2 * np.pi, 120, endpoint=False)
    ring = np.c_[(3 + 0.03 * rng.standard_normal(120)) * np.cos(t), 3 * np.sin(t), np.zeros(120)]
    s, _ = curves.smooth_line(ring, 0.5, 0.05, closed=True)
    assert np.abs(np.hypot(s[:, 0], s[:, 1]) - 3).max() < 0.05
    # in the tree
    m = Annotations()
    c = m.add(Curve.from_line(v, [0, 0, 1], [0, 0, 0], tolerance=0.01, source={"tool": "curb_trace"}), "lip")
    pl = m.add(Polyline(v, False, {}), "pl")
    c2 = m.smooth(c.id)
    assert c2.id == c.id and m.root[0] is c2 and c2.mode == "free" and c2.source["tool"] == "curb_trace"
    r = c2.source["smoothed"][0]
    assert r["scale"] == 1.0 and r["wiggle_deg"][1] < r["wiggle_deg"][0] and len(c2.nodes) < len(c.nodes)
    p2 = m.smooth(pl.id, 1.0, 0.05)
    assert p2.kind == "polyline" and m.root[1] is p2 and len(p2.source["smoothed"]) == 1
    assert len(m.smooth(c2.id).source["smoothed"]) == 2  # again: smoothed more
