"""The app's wiring on llvmpipe: open a project, stream, pick, views, clip box, scan metadata."""

import json
import time

import numpy as np
import pytest

from pclabel.store.importer import ImportParams, import_scan
from pclabel.store.tiles import TilingParams
from pclabel.synth import make_street, write_street_e57


@pytest.fixture(scope="module")
def project_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("ui")
    src = write_street_e57(make_street(n_target=300_000, seed=2), d / "street.e57")
    return import_scan(src, projects_dir=d / "projects", params=ImportParams(tiling=TilingParams(max_points=60_000)))


@pytest.fixture
def window(gl, taxonomy, project_dir):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from pclabel.settings import Settings
    from pclabel.ui.main_window import MainWindow

    s = Settings(gl="software", projects_dir=str(project_dir.parent), software_point_budget=300_000)
    win = MainWindow(s, taxonomy)
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    win.resize(900, 600)
    win.show()
    win.open_project(project_dir)
    app = QApplication.instance()

    def settle(timeout=60):
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            app.processEvents()
            vp = win.viewport
            if vp.renderer is not None and vp.first_frame_s is not None and vp.last_stats.converged:
                app.processEvents()
                return True
            time.sleep(0.005)
        return False

    win.settle = settle
    yield win
    win.close()
    win.deleteLater()
    app.processEvents()


def test_open_stream_and_pick(window):
    assert window.settle()
    vp = window.viewport
    assert vp.gl_info["mode"] == "software" and "llvmpipe" in vp.gl_info["GL_RENDERER"]
    assert vp.last_stats.drawn > 0 and vp.last_stats.converged
    window.viewport.set_view("top", animate=False)  # the street from above: the centre is on the ground
    assert window.settle()
    from PySide6.QtCore import QPointF

    vp.pick(QPointF(vp.width() / 2, vp.height() / 2))
    rows = dict((window.info.tree.topLevelItem(i).text(0), window.info.tree.topLevelItem(i).text(1))
                for i in range(window.info.tree.topLevelItemCount()))  # fmt: skip
    assert "x, y, z (scan frame)" in rows and rows["class"].startswith("0 ")  # nothing labelled yet
    assert int(rows["points within 10 cm"].replace(",", "")) >= 1
    src_i = int(rows["source index (export order)"].replace(",", ""))
    store_i = int(rows["store index"].replace(",", ""))
    assert int(window.project.source_index[store_i]) == src_i
    assert vp.renderer.marker is not None


def test_keys_views_and_clip(window, project_dir):
    assert window.settle()
    vp = window.viewport
    mode0 = window.display.mode.currentData()
    window.cycle_mode(1)
    assert window.display.mode.currentData() != mode0
    from PySide6.QtWidgets import QApplication

    yaw0, pitch0 = vp.camera.yaw, vp.camera.pitch
    window.set_view("top")  # animated: a quarter of a second
    t0 = time.perf_counter()
    while vp._anim_timer.isActive() and time.perf_counter() - t0 < 5:
        QApplication.instance().processEvents()
    assert vp.view_name == "top" and vp.camera.projection == "perspective"
    assert window.s_view.text() == "top view"
    vp.set_view("top", animate=False)  # again: back to where it was
    assert np.isclose(vp.camera.pitch, pitch0) and np.isclose((vp.camera.yaw - yaw0 + 180) % 360 - 180, 0)
    vp.set_view("bottom", animate=False)
    assert vp.view_name == "bottom" and vp.camera.axes()[2][2] > 0.999  # looking up
    window._ortho_action.trigger()
    assert vp.camera.projection == "ortho" and "orthographic" in vp.view_label()
    window._ortho_action.trigger()
    assert vp.camera.projection == "perspective"
    vp.set_view("top", animate=False)
    window.clip_from_view()
    assert window.clip.on.isChecked() and vp.renderer.display.clip_on
    assert window.settle()
    lo, hi = np.array(vp.renderer.display.clip_min), np.array(vp.renderer.display.clip_max)
    assert np.all(hi > lo)


def test_scan_metadata_is_saved(window, project_dir):
    window.meta.fields["scanner"].setText("Leica RTC360")
    window.meta.capture.setCurrentText("mobile")
    window.meta.save()
    meta = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))["scan_metadata"]
    assert meta["scanner"] == "Leica RTC360" and meta["capture"] == "mobile"
    assert not window.project.static_capture


def test_settings_reach_a_renderer_created_later(gl, taxonomy, project_dir):
    """Opening a project before the GL context exists (python -m pclabel <project>) must still apply the docks."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from pclabel.settings import Settings
    from pclabel.ui.main_window import MainWindow

    win = MainWindow(Settings(gl="software", software_point_budget=200_000), taxonomy)
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    win.open_project(project_dir)  # no GL context yet: the renderer is created in initializeGL
    assert win.viewport.renderer is None
    win.resize(800, 500)
    win.show()
    app = QApplication.instance()
    t0 = time.perf_counter()
    while win.viewport.renderer is None and time.perf_counter() - t0 < 30:
        app.processEvents()
    d = win.viewport.renderer.display
    assert d.height_range == (win.display.hmin.value(), win.display.hmax.value())
    assert d.height_range != (0.0, 10.0)  # the auto range from the data, not the renderer's default
    assert d.clip_min == tuple(s.value() for s in win.clip.lo)
    win.close()
    win.deleteLater()
    app.processEvents()


def _mouse(vp, kind, x, y, button, buttons, mods=None):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    t = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
         "release": QEvent.Type.MouseButtonRelease}[kind]  # fmt: skip
    pos = QPointF(x, y)
    ev = QMouseEvent(t, pos, vp.mapToGlobal(pos), button, buttons, mods or Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(vp, ev)


def test_rotation_starts_without_a_jump_wherever_the_drag_starts(window):
    from PySide6.QtCore import Qt

    assert window.settle()
    vp = window.viewport
    L, NB = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
    before = vp.camera.mvp(1.5)
    _mouse(vp, "press", 15, 12, L, L)  # near a corner, far from the centre
    assert np.allclose(vp.camera.mvp(1.5), before, atol=1e-9)  # pressing does not move the picture
    pivot = vp.camera.target.copy()
    _mouse(vp, "move", 60, 12, NB, L)
    _mouse(vp, "release", 60, 12, L, NB)
    assert not np.allclose(vp.camera.mvp(1.5), before)  # dragging rotates
    w, h = vp.physical_size()
    assert np.allclose(vp.camera.project(pivot, (w, h))[0, :2], (w / 2, h / 2), atol=1e-6)  # around the centre
    vp.reset_view()
    assert vp.view_name == "free" and vp.camera.projection == "perspective"
    assert (vp.camera.yaw, vp.camera.pitch) == (-60.0, 35.0)


def test_right_drag_rotates_like_left_drag_and_a_key_turns_it_into_flying(window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    assert window.settle()
    vp, cam = window.viewport, window.viewport.camera
    L, R, NB = Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton, Qt.MouseButton.NoButton
    start = (cam.yaw, cam.pitch, cam.target.copy(), cam.distance)

    def drag(button):
        cam.yaw, cam.pitch, cam.target, cam.distance = start[0], start[1], start[2].copy(), start[3]
        _mouse(vp, "press", 100, 80, button, button)
        _mouse(vp, "move", 160, 110, NB, button)
        _mouse(vp, "release", 160, 110, button, NB)
        return cam.mvp(1.5), cam.eye()

    eye_before = cam.eye().copy()
    m_left, eye_left = drag(L)
    m_right, eye_right = drag(R)
    assert np.linalg.norm(eye_left - eye_before) > 0.1  # it rotated (the camera went round the pivot)
    assert np.allclose(m_left, m_right) and np.allclose(eye_left, eye_right)  # the same rotation either way

    _mouse(vp, "press", 100, 80, R, R)  # hold the right button, press W: the mouse looks, W flies forward
    QTest.keyPress(vp, Qt.Key.Key_W)
    eye0 = cam.eye().copy()
    _mouse(vp, "move", 130, 80, NB, R)
    assert np.allclose(cam.eye(), eye0, atol=1e-6)  # looking turns the camera where it stands
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 0.2:
        QApplication.instance().processEvents()
    assert np.linalg.norm(cam.eye() - eye0) > 0.05  # and W moves it
    QTest.keyRelease(vp, Qt.Key.Key_W)
    _mouse(vp, "release", 130, 80, R, NB)
    assert not vp._flying and not vp._fly_timer.isActive()


def test_select_fit_plane_and_slice(window, project_dir):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    assert window.settle()
    vp = window.viewport
    vp.set_view("top", animate=False)
    assert window.settle()
    w, h = vp.physical_size()

    def select(poly, op="replace"):
        vp.shape_done.emit(np.asarray(poly, float), op)
        t0 = time.perf_counter()
        while window._sel_busy and time.perf_counter() - t0 < 60:
            app.processEvents()

    box = [(w * 0.45, h * 0.45), (w * 0.55, h * 0.45), (w * 0.55, h * 0.55), (w * 0.45, h * 0.55)]
    select(box)
    n_box = window.selection.size
    assert n_box > 100 and np.array_equal(window.viewport.renderer.selection, window.selection)
    window.fit_plane()
    assert window.active_frame == 1 and window.frames[1].report["points_selected"] == n_box
    d = vp.renderer.display
    assert np.allclose(d.height_normal, window.frames[1].normal) and d.h0 == 0.0
    window.toggle_frame()
    assert window.active_frame == 0 and d.height_normal == (0.0, 0.0, 1.0)
    window.toggle_frame()
    assert window.active_frame == 1
    # a slice of +-2 cm around the plane: a new selection only takes points inside it
    window.frames_dock.slice_a.setValue(-2.0)
    window.frames_dock.slice_b.setValue(2.0)
    window.frames_dock.slice_on.setChecked(True)
    assert d.band_on and d.band == (-0.02, 0.02)
    frames_seen = []
    vp.frame.connect(frames_seen.append)
    t0 = time.perf_counter()
    while not frames_seen and time.perf_counter() - t0 < 30:  # a frame drawn with the slice (its depth buffer)
        app.processEvents()
    vp.frame.disconnect(frames_seen.append)
    select(box)
    hh = window.frames[1].heights(np.asarray(window.project.xyz[window.selection]), window.project.offset)
    assert window.selection.size > 0 and np.all(np.abs(hh) <= 0.02 + 1e-6)
    select(box, "subtract")
    assert window.selection.size == 0
    # frames are saved with the project and come back on reopening
    frames = json.loads((project_dir / "frames.json").read_text(encoding="utf-8"))
    assert frames["schema"] == "pclabel.frames/1" and frames["active"] == "plane 1"
    window.open_project(project_dir)
    assert window.active_frame == 1 and window.frames[1].name == "plane 1"
    window.escape()  # nothing selected after reopening: clears the pick marker, no error


def test_ground_grow_tool(window, project_dir):
    """Seed click -> grow in a worker -> selection; changing a threshold re-grows from the same seed on the cached
    cell grid; the selection remembers how it was made."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    assert window.settle()
    vp = window.viewport
    window.toggle_tool("grow")
    assert vp.tool == "grow" and window.frames_dock.lasso.isChecked() is False

    def wait():
        t0 = time.perf_counter()
        while (window._grow_busy or window._grow_timer.isActive() or window._grow_pending) \
                and time.perf_counter() - t0 < 60:  # fmt: skip
            app.processEvents()

    p = window.project
    road = np.flatnonzero(np.abs(np.asarray(p.xyz[:, 1]) + p.offset[1] + 1.5) < 0.05)
    seed = np.asarray(p.xyz[road[0]], np.float64)
    window.grow.spins["cell"][0].setValue(20.0)  # the synthetic scan is sparse
    window.grow.spins["max_gap"][0].setValue(60.0)
    window.grow.spins["intensity_range"][0].setValue(1.0)
    vp.seed_picked.emit(seed, "replace")
    wait()
    n1 = window.selection.size
    assert n1 > 1000 and window.selection_def["tool"] == "grow_select" and window.selection_def["mode"] == "height_intensity"
    assert window.selection_def["params"]["cell"] == 0.2 and "points" in window.selection_def["result"]
    grid = window._grow_state["grid"]
    window.grow.spins["max_step"][0].setValue(0.3)  # 3 mm: the grow stops much sooner
    wait()
    assert window._grow_state["grid"] is grid and window.selection.size < n1
    assert "points on" in window.grow.result.text()
    window.escape()  # drops the tool
    assert vp.tool is None and window.selection.size > 0
    window.escape()  # then the selection
    assert window.selection.size == 0 and window._grow_state is None


def test_grow_reports_and_shows_where_it_stopped(window):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    assert window.settle()
    window.toggle_tool("grow")
    for name, v in (("cell", 20.0), ("max_gap", 60.0)):
        window.grow.spins[name][0].setValue(v)
    p = window.project
    road = np.flatnonzero(np.abs(np.asarray(p.xyz[:, 1]) + p.offset[1] + 1.5) < 0.05)
    window.viewport.seed_picked.emit(np.asarray(p.xyz[road[0]], np.float64), "replace")
    t0 = time.perf_counter()
    while (window._grow_busy or window._grow_pending) and time.perf_counter() - t0 < 60:
        app.processEvents()
    stops = window.selection_def["result"]["stopped_by"]
    assert stops["height step"] > 0  # the kerbs
    assert "stopped by (cells):" in window.grow.result.text() and "height step" in window.grow.result.text()
    assert window.viewport.renderer._marks is not None and window.viewport.renderer._marks[2] == sum(stops.values())
    window.grow.show_stops.setChecked(False)
    assert window.viewport.renderer._marks is None
    window.escape()
    window.escape()


def test_magic_select_takes_a_dash_and_shift_adds_a_line(window):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    assert window.settle()
    window.toggle_tool("magic")
    assert window.viewport.tool == "magic"
    p = window.project
    w = np.asarray(p.xyz, np.float64) + p.offset
    bright = np.asarray(p.intensity) / 65535 > 0.25

    def click(y0, y1, op):
        on = (w[:, 1] > y0) & (w[:, 1] < y1) & (w[:, 0] > -2.0) & (w[:, 0] < -1.0) & bright
        window.viewport.seed_picked.emit(np.asarray(p.xyz[np.flatnonzero(on)[0]], np.float64), op)
        t0 = time.perf_counter()
        while (window._grow_busy or window._grow_pending) and time.perf_counter() - t0 < 60:
            app.processEvents()

    click(-0.04, 0.04, "replace")  # the centre dash at x -3..0
    first = w[window.selection]
    assert len(first) > 50 and np.all(np.abs(first[:, 1]) < 0.15) and np.all((first[:, 0] > -3.1) & (first[:, 0] < 0.1))
    d = window.selection_def
    assert d["tool"] == "magic_select" and d["params"]["tolerance"] == 2.0 and "accepts" in window.magic.result.text()
    assert window.viewport.renderer._marks is None  # where it stopped: off by default (the outline of every marking)
    click(-3.33, -3.27, "add")  # the south double yellow line, 3.3 m away
    both = w[window.selection]
    assert (both[:, 1] < -3).sum() > 200 and np.abs(both[:, 1]).max() < 3.6  # not the kerb (3.7 m)
    assert window.selection_def["result"]["picture_reused"]
    window.magic.show_stops.setChecked(True)
    assert window.viewport.renderer._marks is not None
    window.escape()
    window.escape()
    assert window.selection.size == 0 and window._grow_state is None


def _mouse_at(vp, kind, xy, buttons, mods=None):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    t = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
         "release": QEvent.Type.MouseButtonRelease}[kind]  # fmt: skip
    pos = QPointF(float(xy[0]), float(xy[1]))
    button = Qt.MouseButton.NoButton if kind == "move" else Qt.MouseButton.LeftButton
    mods = Qt.KeyboardModifier.NoModifier if mods is None else mods
    QApplication.sendEvent(vp, QMouseEvent(t, pos, vp.mapToGlobal(pos), button, buttons, mods))


def _click_at(vp, xy, mods=None):
    from PySide6.QtCore import Qt

    _mouse_at(vp, "press", xy, Qt.MouseButton.LeftButton, mods)
    _mouse_at(vp, "release", xy, Qt.MouseButton.NoButton, mods)


def test_segments_curves_and_directories(window, tmp_path):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest

    assert window.settle()
    a, vp, p = window.annot, window.viewport, window.project
    w = np.asarray(p.xyz, np.float64) + p.offset

    window.toggle_tool("segment")
    assert vp.tool == "segment" and a.dock.segment.isChecked()
    a.add_vertex(10)
    a.add_vertex(5000)
    seg = a.model.root[-1]
    assert seg.kind == "segment" and np.allclose(seg.a, w[10]) and np.allclose(seg.b, w[5000])
    assert (p.path / "annotations.json").exists() and vp.renderer._vlines is not None

    window.toggle_tool("curve")  # four road points across x, then Backspace and redo the last
    road = np.flatnonzero(np.abs(w[:, 1] + 1.5) < 0.05)
    picks = [int(road[np.argmin(np.abs(w[road, 0] - x))]) for x in (-4.0, -2.0, 0.0, 2.0)]
    for i in picks:
        a.add_vertex(i)
    a.back()
    assert len(a.pending) == 3
    a.add_vertex(picks[-1])
    QTest.keyClick(vp, Qt.Key.Key_Return)  # finish: fit the plane, edit the curve
    c = a.editing
    assert vp.tool == "curve_edit" and c is not None and len(c.nodes) == 4
    assert c.mode == "ends" and np.allclose(c.nodes[[0, -1]], w[[picks[0], picks[-1]]])  # the ends stay put
    f = window.frames[window.active_frame]  # the active height axis (a plane fitted by an earlier test, or z)
    a.dock.edit_mode.setCurrentIndex(a.dock.edit_mode.findData("axis"))
    a.set_curve_mode(a.dock.edit_mode.currentData())  # the other mode: perpendicular to the height axis
    assert np.allclose(c.normal, f.normal) and c.height() == pytest.approx(np.mean((w[picks] - f.origin) @ f.normal))
    assert np.allclose((c.nodes - f.origin) @ f.normal, c.height())
    window.undo()  # back to the plane through the ends
    c = a.editing
    assert c.mode == "ends" and a.dock.edit_mode.currentData() in ("ends", "axis")

    vp.camera.target = c.nodes[1] - p.offset
    vp.camera.distance = 8.0
    vp.set_view("top", animate=False)
    vp.camera.target = c.nodes[1] - p.offset  # the preset view re-centres on the surface: back onto node 1
    assert window.settle()
    node = vp.screen_of(c.nodes[1:2] - p.offset)[0]
    _click_at(vp, node)  # a click on node 1 activates its gradient
    assert a.active == 1 and c.manual()[1] and len(vp.edit_targets["handles"]) == 2
    out = [h for h in vp.edit_targets["handles"] if h[1] == 1][0][2]
    start = vp.screen_of(out[None])[0]
    before = c.polyline()
    _mouse_at(vp, "press", start, Qt.MouseButton.LeftButton)  # drag the out handle 60 px up the screen
    for k in range(1, 7):
        _mouse_at(vp, "move", start + (0, -10 * k), Qt.MouseButton.LeftButton)
    _mouse_at(vp, "release", start + (0, -60), Qt.MouseButton.NoButton)
    h = c.h_out[1]
    assert abs(h @ c.normal) < 1e-9 and np.linalg.norm(h - (out + p.offset - c.nodes[1])) > 0.1  # moved, in plane
    after = c.polyline()
    assert after.shape != before.shape or not np.allclose(after, before)
    end = vp.plane_point(QPointF(*(start + (0, -60))), c.normal, c.plane_point() - p.offset)
    assert np.allclose(c.nodes[1] + h, end + p.offset, atol=1e-6)  # the handle followed the mouse on the plane

    # Shift+click on the cloud between nodes 2 and 3 adds a point there; Ctrl+click on node 0 removes it
    def look_at(q_local):
        vp.camera.target = np.asarray(q_local, np.float64)
        vp.update()
        assert window.settle()
        return vp.screen_of(np.asarray(q_local)[None])[0]

    mid = 0.5 * (c.nodes[2] + c.nodes[3]) - p.offset
    _click_at(vp, look_at(mid), Qt.KeyboardModifier.ShiftModifier)
    assert len(c.nodes) == 5 and abs(c.nodes[3, 0] - p.offset[0] - mid[0]) < 0.5  # the new node is 4th of 5
    assert np.all(np.diff(c.nodes[:, 0]) > 0)  # still in order along the road
    _click_at(vp, look_at(c.nodes[0] - p.offset), Qt.KeyboardModifier.ControlModifier)
    assert len(c.nodes) == 4 and c.nodes[0, 0] > w[picks[0], 0] + 1
    assert len(json.loads((p.path / "annotations.json").read_text(encoding="utf-8"))["tree"][-1]["nodes"]) == 4
    QTest.keyClick(vp, Qt.Key.Key_Return)  # done
    assert vp.tool is None and a.editing is None and vp.edit_targets is None

    window.set_selection(np.arange(1000, 1100), {"tool": "test", "op": "replace"})
    next(x for x in window.actions() if x.shortcut().toString() == "Ctrl+G").trigger()  # a point set from it
    ps = a.model.root[-1]
    assert ps.kind == "points" and ps.provenance == [{"tool": "test", "op": "replace"}]
    d = a.new_directory("lines")
    a.move([seg.id, c.id], d.id)  # what a drop onto the directory does
    assert [x.kind for x in a.model.root] == ["points", "directory"] and a.model.color_of(c.id) == d.color
    t = a.dock.tree
    assert t.topLevelItemCount() == 2 and t.topLevelItem(1).childCount() == 2
    t.topLevelItem(0).setSelected(True)
    window.clear_selection()
    a.select_points()
    assert np.array_equal(window.selection, np.arange(1000, 1100))

    path = tmp_path / "all.json"
    a.export(path)
    a.import_(path)
    assert [x.kind for x in a.model.root] == ["points", "directory"] * 2
    window.toggle_tool("curve")
    a.add_vertex(picks[0])
    window.escape()  # drops the unfinished curve, keeps the tool
    assert a.pending == [] and vp.tool == "curve"
    window.escape()
    assert vp.tool is None


def test_export_a_plane(window, tmp_path):
    assert window.settle()
    window.activate_frame(0)
    assert window.export_plane(tmp_path / "z.json") is None  # z is not a fitted plane
    window.toggle_tool("rect")
    vp = window.viewport
    vp.set_view("top", animate=False)
    assert window.settle()
    w, h = vp.physical_size()
    vp.shape_done.emit(np.array([(w * 0.45, h * 0.45), (w * 0.55, h * 0.45), (w * 0.55, h * 0.55), (w * 0.45, h * 0.55)]),
                       "replace")  # fmt: skip
    from PySide6.QtWidgets import QApplication

    t0 = time.perf_counter()
    while window._sel_busy and time.perf_counter() - t0 < 60:
        QApplication.instance().processEvents()
    window.fit_plane()
    f = window.frames[window.active_frame]
    path = window.export_plane(tmp_path / "plane.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    e = doc["equation"]
    assert doc["name"] == f.name and np.allclose([e["a"], e["b"], e["c"]], f.normal)
    assert e["d"] == pytest.approx(-np.dot(f.normal, f.origin)) and doc["source"]["n_valid"] == window.project.n


def test_undo_and_redo(window):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    assert window.settle()
    a, vp, p = window.annot, window.viewport, window.project
    w = np.asarray(p.xyz, np.float64) + p.offset
    undo = next(x for x in window.actions() if x.shortcut().toString() == "Ctrl+Z")

    def magic(op, x0):
        on = (np.abs(w[:, 1]) < 0.04) & (w[:, 0] > x0) & (w[:, 0] < x0 + 1) & (np.asarray(p.intensity) / 65535 > 0.25)
        vp.seed_picked.emit(np.asarray(p.xyz[np.flatnonzero(on)[0]], np.float64), op)
        t0 = time.perf_counter()
        while (window._grow_busy or window._grow_pending) and time.perf_counter() - t0 < 60:
            app.processEvents()

    # the selection: a magic select, a threshold change (same step), a clear
    window.toggle_tool("magic")
    magic("replace", -2.5)
    first = window.selection.copy()
    window.magic.spins["tolerance"][0].setValue(3.0)  # re-runs the same click
    t0 = time.perf_counter()
    while (window._grow_timer.isActive() or window._grow_busy or window._grow_pending) and time.perf_counter() - t0 < 60:
        app.processEvents()
    widened = window.selection.copy()
    window.clear_selection()
    assert undo.text() == "Undo clear the selection"
    undo.trigger()
    assert np.array_equal(window.selection, widened)
    undo.trigger()  # the click and its threshold change are one step
    assert window.selection.size == 0 and first.size > 0
    window.redo()
    assert np.array_equal(window.selection, widened) and window._grow_state is None
    window.toggle_tool(None)

    # annotations: a segment, a curve with a gradient and a new point, a directory and a move
    window.toggle_tool("segment")
    a.add_vertex(10)
    a.add_vertex(5000)
    n_items = len(a.model.items())
    window.undo()
    assert len(a.model.items()) == n_items - 1
    window.redo()
    assert len(a.model.items()) == n_items
    seg = a.model.root[-1]

    window.toggle_tool("curve")
    road = np.flatnonzero(np.abs(w[:, 1] + 1.5) < 0.05)
    picks = [int(road[np.argmin(np.abs(w[road, 0] - x))]) for x in (-4.0, -2.0, 0.0, 2.0)]
    for i in picks[:3]:
        a.add_vertex(i)
    window.undo()  # while drawing, undo takes back the last clicked point
    assert len(a.pending) == 2
    a.add_vertex(picks[2])
    a.add_vertex(picks[3])
    a.finish()
    c = a.editing
    a.node_clicked(1)
    plain = c.polyline()
    for k in range(1, 4):  # one drag of node 1's out handle
        a.handle_dragged(1, 1, c.nodes[1] - p.offset + (0.3 * k, 0.4 * k, 0))
    a.drag_finished()
    dragged = a.editing.polyline()
    a.add_node(int(road[np.argmin(np.abs(w[road, 0] - 1.0))]))
    assert len(a.editing.nodes) == 5
    window.undo()  # the new point
    assert len(a.editing.nodes) == 4 and vp.tool == "curve_edit"  # still editing the same curve
    window.undo()  # the whole drag
    assert np.allclose(a.editing.polyline(), plain) and a.editing.id == c.id
    window.redo()  # the drag again
    assert np.allclose(a.editing.polyline(), dragged)
    a.done()

    d = a.new_directory("lines")
    a.move([seg.id], d.id)
    assert a.model.parent(seg.id).id == d.id
    window.undo()
    assert a.model.parent(seg.id) is None
    window.undo()
    assert d.id not in {x.id for x, _ in a.model.walk()}
    doc = json.loads((p.path / "annotations.json").read_text(encoding="utf-8"))
    assert d.id not in {n["id"] for n in doc["tree"]}  # saved after an undo too

    # planes
    window.set_selection(np.flatnonzero(np.abs(w[:, 1] + 1.5) < 0.5)[:5000], {"tool": "test", "op": "replace"})
    n_frames = len(window.frames)
    window.fit_plane()
    assert len(window.frames) == n_frames + 1
    window.undo()
    assert len(window.frames) == n_frames
    window.redo()
    assert len(window.frames) == n_frames + 1 and window.active_frame == n_frames


def test_3d_select_with_keys_and_locks(window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from pclabel.tools.volume import VolumeParams, frame_axes

    app = QApplication.instance()
    assert window.settle()
    vp, vol, a, p = window.viewport, window.volume, window.annot, window.project
    window.activate_frame(0)  # z
    xyz = np.asarray(p.xyz, np.float64)

    def wait():
        t0 = time.perf_counter()
        while window._sel_busy and time.perf_counter() - t0 < 60:
            app.processEvents()

    def inside(base, prm):
        n, u, v = frame_axes((0, 0, 1), (1, 0, 0), prm.turn)
        d = xyz - base
        h, x, y = d @ n, d @ u, d @ v
        ok = (h >= prm.bottom) & (h <= prm.top)
        return np.flatnonzero(ok & ((np.abs(x) <= prm.length / 2) & (np.abs(y) <= prm.width / 2) if prm.shape == "box"
                                    else x**2 + y**2 <= prm.radius**2))  # fmt: skip

    window.toggle_tool("volume")
    assert vp.tool == "volume"
    road = np.flatnonzero(np.abs(xyz[:, 1] + p.offset[1] + 1.5) < 0.05)
    base = xyz[road[0]].copy()
    vp.volume_placed.emit(base)
    assert "volume" in vp.renderer._vectors
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    claim = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_W, Qt.KeyboardModifier.NoModifier)
    vp.event(claim)
    assert claim.isAccepted()  # W goes to the box, not to the window's height-wrap shortcut
    step = vol.dock.step.value() / 100
    QTest.keyClick(vp, Qt.Key.Key_E)  # up one step along the height axis
    assert np.allclose(vol.base, base + (0, 0, step))
    QTest.keyClick(vp, Qt.Key.Key_Q, Qt.KeyboardModifier.ShiftModifier)  # down five steps
    assert np.allclose(vol.base, base + (0, 0, -4 * step))
    before = vol.base.copy()
    QTest.keyClick(vp, Qt.Key.Key_W)  # across the axis, away from the viewer
    moved = vol.base - before
    assert abs(moved[2]) < 1e-12 and np.linalg.norm(moved) == pytest.approx(step)
    QTest.keyClick(vp, Qt.Key.Key_X)
    assert vol.dock.spins["turn"][0].value() == 5.0

    sel_base = vol.base.copy()
    QTest.keyClick(vp, Qt.Key.Key_Return)  # select what is inside
    wait()
    prm = vol.dock.params()
    assert window.selection.size > 20 and np.array_equal(window.selection, inside(vol.base, prm))
    assert window.selection_def["tool"] == "select_volume" and window.selection_def["params"]["turn"] == 5.0
    first = window.selection.copy()
    vol.dock.shape.setCurrentIndex(vol.dock.shape.findData("cylinder"))
    QTest.keyClick(vp, Qt.Key.Key_D, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClick(vp, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)  # add a cylinder beside it
    wait()
    cyl = inside(vol.base, vol.dock.params())
    assert np.array_equal(window.selection, np.union1d(first, cyl))
    QTest.keyClick(vp, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)  # and take it out again
    wait()
    assert np.array_equal(window.selection, np.setdiff1d(first, cyl))

    # locks: a labelled point set's points are left out once its label is locked; "other" never gets selected
    window.set_selection(first[: first.size // 2], {"tool": "test", "op": "replace"})
    a.points_from_selection()
    ps = a.model.root[-1]
    ps.label = "road"
    a._changed()
    vol.dock.shape.setCurrentIndex(vol.dock.shape.findData("box"))
    vol.place(sel_base)  # the box that made the first selection
    a.set_lock("road", True)
    assert a.model.locks == {"road"}
    vol.confirm("replace")
    wait()
    assert not np.isin(ps.indices, window.selection).any() and "locked points left out" in window.frames_dock.sel_label.text()
    a.set_lock("road", False)
    vol.confirm("replace")
    wait()
    assert np.isin(ps.indices, window.selection).all()
    ps.label = "other"  # no class: never selected, without any lock
    a._changed()
    vol.confirm("replace")
    wait()
    assert not np.isin(ps.indices, window.selection).any()
    a._fill_locks()
    other = [x for x in a.dock.locks_menu.actions() if x.text().startswith("other")][0]
    assert other.isChecked() and other.isEnabled()
    other.setChecked(False)  # "no class" unlocked: selected like anything else
    assert a.model.unlocked == {"other"}
    vol.confirm("replace")
    wait()
    assert np.isin(ps.indices, window.selection).all()
    a.set_lock("other", True)
    a.set_lock("road", True)
    a.set_locks_on(False)  # all locks off, the choices kept
    assert window._locks_action.isChecked() is False and a.dock.locks.text() == "locks (off)"
    vol.confirm("replace")
    wait()
    assert np.isin(ps.indices, window.selection).all() and a.model.locks == {"road"}
    window._locks_action.trigger()  # on again from the menu
    assert a.model.locks_on
    vol.confirm("replace")
    wait()
    assert not np.isin(ps.indices, window.selection).any()
    a.set_lock("road", False)
    window.toggle_tool(None)
    assert "volume" not in vp.renderer._vectors
    claim = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_W, Qt.KeyboardModifier.NoModifier)
    claim.ignore()
    vp.event(claim)
    assert not claim.isAccepted()  # without the tool, W is the window's again


def test_lasso_and_rectangle_select_visible_points_or_everything_through(window, taxonomy):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    assert window.settle()
    vp, p = window.viewport, window.project
    window.activate_frame(0)
    vp.camera.target = np.array([6.0, 7.5, 2.0]) - p.offset  # above the tree on the north verge
    vp.camera.distance = 18.0
    vp.set_view("top", animate=False)
    vp.camera.target = np.array([6.0, 7.5, 2.0]) - p.offset
    assert window.settle()
    w, h = vp.physical_size()
    box = np.array([(w * 0.42, h * 0.42), (w * 0.58, h * 0.42), (w * 0.58, h * 0.58), (w * 0.42, h * 0.58)])

    def select():
        vp.shape_done.emit(box, "replace")
        t0 = time.perf_counter()
        while window._sel_busy and time.perf_counter() - t0 < 60:
            app.processEvents()
        return window.selection.copy()

    window.toggle_tool("rect")
    window.set_select_through(False)
    visible = select()
    assert window.selection_def["depth"] == "visible"
    window.set_select_through(True)
    assert vp.select_through and window._through_action.isChecked()
    assert window.frames_dock.depth_mode.currentData() == "through"
    through = select()
    assert window.selection_def == {"tool": "select_shape", "shape": "rectangle", "depth": "through", "op": "replace",
                                    "points": through.size}  # fmt: skip
    assert np.isin(visible, through).all() and through.size > 1.5 * visible.size  # the ground under the canopy too
    z = np.asarray(p.xyz[through, 2], np.float64) + p.offset[2]
    assert z.min() < 0.5 < 3.0 < z.max()  # from the ground up into the canopy
    from PySide6.QtCore import Qt

    L, NB = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
    _mouse(vp, "press", 10, 10, L, L)  # starting a rectangle: the outline shows the mode
    assert vp.renderer.screen_shape_color[:3] == (1.0, 0.6, 0.1)
    _mouse(vp, "release", 10, 10, L, NB)  # too small to select anything
    window.set_select_through(False)
    _mouse(vp, "press", 10, 10, L, L)
    assert vp.renderer.screen_shape_color[:3] == (1.0, 1.0, 1.0)
    _mouse(vp, "release", 10, 10, L, NB)
    assert np.array_equal(window.selection, through)
    window.toggle_tool(None)


def test_closed_loops_and_extrude_select(window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from pclabel.tools.volume import select_loop

    app = QApplication.instance()
    assert window.settle()
    a, vp, vol, p = window.annot, window.viewport, window.volume, window.project
    window.activate_frame(0)
    w = np.asarray(p.xyz, np.float64) + p.offset
    road = np.flatnonzero((np.abs(w[:, 1] + 2.0) < 1.6) & (np.abs(w[:, 0]) < 4))

    def near(x, y):
        return int(road[np.argmin(np.hypot(w[road, 0] - x, w[road, 1] - y))])

    window.toggle_tool("curve")
    for x, y in ((-3, -1), (3, -1), (3, -3), (-3, -3)):
        a.add_vertex(near(x, y))
    vp.finish_requested.emit(True)  # Shift+Enter: finish as a closed loop
    c = a.editing
    assert c.closed and len(c.nodes) == 4 and a.dock.edit_closed.isChecked()
    a.dock.edit_closed.click()  # open it in edit mode
    assert not a.editing.closed
    window.undo()  # and closed again
    c = a.editing  # undo restores a copy of the tree: look the curve up again
    assert c.closed and a.dock.edit_closed.isChecked()
    a.done()

    t = a.dock.tree
    t.clearSelection()
    item = a._item_of(c.id)
    item.setSelected(True)
    window.toggle_tool("volume")
    vol.dock.shape.setCurrentIndex(vol.dock.shape.findData("loop"))  # takes the selected closed curve
    assert vol.loop_id == c.id and vol.dock.curve_label.text() == c.name
    for name, value in (("inset", 20.0), ("bottom", -0.3), ("top", 0.5)):
        vol.dock.spins[name][0].setValue(value)
    QTest.keyClick(vp, Qt.Key.Key_Minus)  # 1 cm further in
    assert vol.dock.spins["inset"][0].value() == 21.0
    QTest.keyClick(vp, Qt.Key.Key_W)  # moved across its normal
    assert np.linalg.norm(vol.move) == pytest.approx(vol.dock.step.value() / 100) and abs(vol.move @ c.normal) < 1e-12
    assert "volume" in vp.renderer._vectors
    QTest.keyClick(vp, Qt.Key.Key_Return)
    t0 = time.perf_counter()
    while window._sel_busy and time.perf_counter() - t0 < 60:
        app.processEvents()
    origin, n, u, poly, _ = vol.loop_geometry()
    want = select_loop(p.xyz, p.tiles, origin, n, u, poly, -0.3, 0.5, 0.21)
    assert want.size > 50 and np.array_equal(window.selection, want)
    d = window.selection_def
    assert d["shape"] == "loop" and d["curve_id"] == c.id and d["inset"] == pytest.approx(0.21)
    sel = w[window.selection, :2]
    line = c.polyline()[:, :2] + vol.move[:2]  # the smooth loop bulges past the clicked corners: use its own extent
    assert np.all((sel >= line.min(0) + 0.21 - 0.02) & (sel <= line.max(0) - 0.21 + 0.02))  # inside, off its line
    QTest.keyClick(vp, Qt.Key.Key_K)  # clip to the loop: its mask goes to the GPU
    spec = vp.renderer.display.clip_volume
    assert spec.kind == "loop" and np.array_equal(np.flatnonzero(spec.contains(np.asarray(p.xyz))), want)
    window.toggle_tool(None)
    assert window.settle()
    assert vp.renderer._vol_tex is not None and vp.renderer._vol_tex_for is spec.mask
    window.set_volume_clip(None)


def test_join_two_curves_in_the_app(window):
    assert window.settle()
    a, p = window.annot, window.project
    window.activate_frame(0)
    w = np.asarray(p.xyz, np.float64) + p.offset
    road = np.flatnonzero(np.abs(w[:, 1] + 1.5) < 0.05)

    def curve(xs):
        window.toggle_tool("curve")
        for x in xs:
            a.add_vertex(int(road[np.argmin(np.abs(w[road, 0] - x))]))
        a.finish()
        c = a.editing
        a.done()
        return c

    first = curve((-6.0, -5.0, -4.0))
    second = curve((2.0, 1.0, 0.0))  # drawn the other way: its end (0) is nearest the first's end (-4)
    t = a.dock.tree
    t.clearSelection()
    for c in (first, second):
        a._item_of(c.id).setSelected(True)
    n_items = len(a.model.items())
    a.start_join()
    assert a.dock.join_box.isVisibleTo(a.dock) and a.dock.join_first_end.currentData() == "end"
    assert a.dock.join_second_end.currentData() == "end"  # the closest pair of ends, offered first
    assert "annotations" in window.viewport.renderer._vectors
    first_name = a.dock.join_first.currentText()
    c = a.confirm_join()
    assert len(a.model.items()) == n_items - 1 and len(c.nodes) == 6 and c.name == first_name
    assert np.all(np.diff(c.nodes[:, 0]) > 0)  # one curve running along the road
    window.undo()
    assert len(a.model.items()) == n_items
    a.start_join(first.id, second.id)
    assert window.annot.escape() and a.joining is None and not a.dock.join_box.isVisibleTo(a.dock)


def test_fit_all_points_mode_and_showing_the_clicked_points(window):
    assert window.settle()
    a, vp, p = window.annot, window.viewport, window.project
    w = np.asarray(p.xyz, np.float64) + p.offset
    road = np.flatnonzero(np.abs(w[:, 1] + 1.5) < 0.3)
    window.toggle_tool("curve")
    for x in (-3.0, -1.0, 1.0, 3.0):
        a.add_vertex(int(road[np.argmin(np.hypot(w[road, 0] - x, w[road, 1] + 1.5 + 0.2 * x))]))
    a.finish()
    c = a.editing
    combo = a.dock.edit_mode
    assert combo.findData("fit") >= 0
    combo.setCurrentIndex(combo.findData("fit"))
    a.set_curve_mode("fit")
    assert c.mode == "fit" and np.allclose(c.point, c.picked.mean(0)) and "fitted to all points" in a.dock.status.text()

    def drawn():
        layer = vp.renderer._vectors["annotations"]
        return layer[0][2], layer[1][2]  # line vertices, points

    lines0, points0 = drawn()
    a.dock.show_picked.setChecked(True)
    lines1, points1 = drawn()
    assert points1 - points0 == 4 and lines1 - lines0 == 2 * 4  # four clicked points, a line from each to its node
    a.dock.show_picked.setChecked(False)
    assert drawn() == (lines0, points0)
    a.done()


def test_grow_select_geometric_mode(window):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    assert window.settle()
    g, p = window.grow, window.project
    window.toggle_tool("grow")
    assert g.windowTitle() == "Grow select"
    g.mode.setCurrentIndex(g.mode.findData("normals"))
    assert g.normal_spins["smoothness"][0].isVisibleTo(g) and not g.spins["max_step"][0].isVisibleTo(g)
    for name, value in (("voxel", 5.0), ("normal_radius", 15.0), ("connect", 7.5)):  # cm, for this sparse scan
        g.normal_spins[name][0].setValue(value)

    def wait():
        t0 = time.perf_counter()
        while (window._grow_busy or window._grow_pending or window._grow_timer.isActive()) and time.perf_counter() - t0 < 60:
            app.processEvents()

    w = np.asarray(p.xyz, np.float64) + p.offset
    road = np.flatnonzero(np.abs(w[:, 1] + 1.5) < 0.05)
    seed = road[np.argmin(np.abs(w[road, 0]))]  # near the scanner: far out, this small scan is too thin for normals
    window.viewport.seed_picked.emit(np.asarray(p.xyz[seed], np.float64), "replace")
    wait()
    d = window.selection_def
    assert d["tool"] == "grow_select" and d["mode"] == "normals" and d["params"]["smoothness"] == 6.0
    assert window.selection.size > 1000 and "voxels grown" in g.result.text()
    geometric = window.selection.copy()
    g.mode.setCurrentIndex(g.mode.findData("height"))  # the other mode re-runs the same click
    wait()
    assert window.selection_def["mode"] == "height_intensity" and not np.array_equal(window.selection, geometric)
    window.undo()  # the click and both runs are one step
    assert window.selection.size == 0
    window.toggle_tool(None)


def test_split_a_point_set_in_the_app(window):
    from PySide6.QtWidgets import QApplication

    from pclabel.render.renderer import GROUP_BIT, UNSURE_BIT

    app = QApplication.instance()
    assert window.settle()
    a, sp, r = window.annot, window.split, window.viewport.renderer
    p = window.project
    w = np.asarray(p.xyz, np.float64) + p.offset
    area = np.flatnonzero((np.abs(w[:, 0]) < 3) & (w[:, 1] > -3.5) & (w[:, 1] < 9))  # road, kerb, pavement, grass, tree
    window.set_selection(area, {"tool": "test", "op": "replace"})
    a.points_from_selection()
    ps = a.model.root[-1]
    a.dock.tree.clearSelection()
    a._item_of(ps.id).setSelected(True)

    def wait():
        t0 = time.perf_counter()
        while (sp._busy or sp._timer.isActive()) and time.perf_counter() - t0 < 120:
            app.processEvents()

    for name, value in (("radius", 15.0),):
        getattr(sp.dock, name).setValue(value)
    sp.start()
    wait()
    assert sp.features is not None and sp.last is not None
    assert np.array_equal(window.selection, ps.indices)  # the set shows yellow
    rough, unsure, info = sp.last
    assert 0 < info["rough"] < info["points"]
    assert np.array_equal(r.marks[GROUP_BIT], ps.indices[rough]) and np.array_equal(r.marks[UNSURE_BIT], ps.indices[unsure])
    sp.dock.highlight.setChecked(False)
    wait()
    assert r.marks[UNSURE_BIT].size == 0
    smooth, rough_set = sp.split()
    assert smooth.indices.size + rough_set.indices.size == area.size and r.marks[GROUP_BIT].size == 0
    assert [x.name for x in a.model.root[-2:]] == [f"{ps.name} smooth", f"{ps.name} rough"]
    window.undo()
    assert a.model.root[-1].name == ps.name and a.model.root[-1].indices.size == area.size


def test_clip_to_a_3d_select_volume(window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    assert window.settle()
    vp, vol, p = window.viewport, window.volume, window.project
    window.activate_frame(0)
    xyz = np.asarray(p.xyz, np.float64)
    w = xyz + p.offset
    road = np.flatnonzero(np.abs(w[:, 1] + 1.5) < 0.05)
    base = xyz[road[np.argmin(np.abs(w[road, 0]))]]
    vp.camera.target = base.copy()
    vp.camera.distance = 25.0
    vp.set_view("top", animate=False)
    vp.camera.target = base.copy()
    assert window.settle()

    window.toggle_tool("volume")
    vol.dock.shape.setCurrentIndex(vol.dock.shape.findData("box"))
    for name, value in (("length", 3.0), ("width", 2.0), ("bottom", -0.5), ("top", 1.0), ("turn", 0.0)):
        vol.dock.spins[name][0].setValue(value)
    vol.dock.verticals.setValue(40)
    vol.place(base)
    QTest.keyClick(vp, Qt.Key.Key_K)  # clip to the box
    d = vp.renderer.display
    assert d.clip_volume_on and d.clip_volume.kind == "box" and window.clip.volume_on.isChecked()
    window.toggle_tool(None)
    assert "clip" in vp.renderer._vectors  # its outline stays drawn
    assert window.settle()

    # nothing is drawn outside the box's footprint (the GPU clip)
    vp.makeCurrent()
    try:
        depth = vp.renderer.read_depth()
    finally:
        vp.doneCurrent()
    corners = base + np.array([(x, y, 0.0) for x in (-1.5, 1.5) for y in (-1.0, 1.0)])
    sx = vp.screen_of(corners) * vp.devicePixelRatioF()
    lo, hi = np.floor(sx.min(0)) - 3, np.ceil(sx.max(0)) + 3
    ys, xs = np.nonzero(depth < 1.0)
    assert len(xs) > 50 and np.all((xs >= lo[0]) & (xs <= hi[0]) & (ys >= lo[1]) & (ys <= hi[1]))

    # what the clip hides is not selected: a rectangle over the whole view, through
    window.toggle_tool("rect")
    window.set_select_through(True)
    W, H = vp.physical_size()
    vp.shape_done.emit(np.array([(0, 0), (W, 0), (W, H), (0, H)], float), "replace")
    t0 = time.perf_counter()
    while window._sel_busy and time.perf_counter() - t0 < 60:
        app.processEvents()
    inside = d.clip_volume.contains(xyz)
    assert window.selection.size > 0 and inside[window.selection].all()
    window.set_select_through(False)

    # clip live follows the volume; a loop clips by its mask; unclip
    window.toggle_tool("volume")
    vol.dock.live.setChecked(True)
    QTest.keyClick(vp, Qt.Key.Key_D)
    assert np.allclose(d.clip_volume.origin, vol.base)
    vol.dock.live.setChecked(False)
    vol.dock.unclip.click()
    assert not d.clip_volume_on and d.clip_volume is None and not window.clip.volume_on.isEnabled()
    window.toggle_tool(None)
    assert window.settle()


def test_clean_up_merge_and_clips(window):
    from PySide6.QtWidgets import QApplication

    from pclabel.render.renderer import GROUP_BIT
    from pclabel.tools.volume import VolumeParams, box_spec

    app = QApplication.instance()
    assert window.settle()
    a, cl, p = window.annot, window.cleanup, window.project
    window.activate_frame(0)
    xyz = np.asarray(p.xyz, np.float64)
    w = xyz + p.offset
    area = np.flatnonzero((np.abs(w[:, 0]) < 3) & (w[:, 1] > -3.5) & (w[:, 1] < -0.5))  # road
    low = area[w[area, 2] < np.percentile(w[area, 2], 40)]  # a low cut of it
    window.set_selection(low, {"tool": "test", "op": "replace"})

    def wait():
        t0 = time.perf_counter()
        while (cl._busy or cl._timer.isActive()) and time.perf_counter() - t0 < 60:
            app.processEvents()

    cl.open()
    wait()
    sel0, added, removed, info = cl.last
    assert info["added"] > 0 and np.array_equal(window.viewport.renderer.marks[GROUP_BIT], added)
    cl.dock.spins["above"][0].setValue(0.0)  # no margin over or under: previews again, (almost) nothing joins
    cl.dock.spins["below"][0].setValue(0.0)
    wait()
    assert cl.last[3]["added"] < info["added"]
    new = cl.apply()
    assert np.array_equal(window.selection, new) and window.viewport.renderer.marks[GROUP_BIT].size == 0
    window.undo()
    assert np.array_equal(window.selection, low)

    # set operations on two point sets: A and B from the tree selection; the result as a new point set or selected
    window.set_selection(area[:500], {"tool": "test", "op": "replace"})
    a.points_from_selection()
    window.set_selection(area[400:900], {"tool": "test", "op": "replace"})
    a.points_from_selection()
    pa, pb = a.model.root[-2:]
    t = a.dock.tree
    t.clearSelection()
    for x in (pa, pb):
        a._item_of(x.id).setSelected(True)
    a.start_set_ops()
    d = a.dock
    assert d.ops_box.isVisibleTo(d) and d.ops_a.currentData() == pa.id and d.ops_b.currentData() == pb.id
    d.ops_op.setCurrentIndex(d.ops_op.findData("a_minus_b"))
    a._ops_preview()
    assert "400 points" in d.ops_result.text()
    sel = a.set_op_result("select")
    assert np.array_equal(sel, area[:400]) and window.selection_def["set_op"] == "a_minus_b"
    d.ops_op.setCurrentIndex(d.ops_op.findData("union"))
    n_before = len(a.model.root)
    merged = a.set_op_result("new")
    assert np.array_equal(merged.indices, area[:900]) and a.model.root[-1] is merged
    assert len(a.model.root) == n_before + 1 and merged.name == f"{pa.name} ∪ {pb.name}"
    assert merged.provenance[0]["tool"] == "set_operation" and pa.indices.size == 500  # A and B kept
    window.undo()
    assert len(a.model.root) == n_before
    window.redo()
    merged = a.model.root[-1]
    a._checkpoint("delete B")
    a.model.remove(pb.id)  # B deleted while the box is open: the lists follow
    a._changed()
    assert d.ops_b.findData(pb.id) == -1 and d.ops_a.currentData() == pa.id
    window.undo()
    merged = a.model.node(merged.id)
    d.ops_close.click()

    # "select points" respects a clip volume
    spec = box_spec(xyz[area[0]], (0, 0, 1), (1, 0, 0), VolumeParams(length=2, width=2, bottom=-1, top=1))
    window.set_volume_clip(spec)
    t.clearSelection()
    a._item_of(merged.id).setSelected(True)
    a.select_points()
    inside = spec.contains(xyz[merged.indices])
    assert np.array_equal(window.selection, merged.indices[inside]) and 0 < inside.sum() < merged.indices.size
    window.set_volume_clip(None)


def test_clip_to_point_sets(window):
    from PySide6.QtWidgets import QApplication

    from pclabel.render.renderer import INSIDE_BIT

    app = QApplication.instance()
    assert window.settle()
    a, vp, p = window.annot, window.viewport, window.project
    window.activate_frame(0)
    xyz = np.asarray(p.xyz, np.float64)
    w = xyz + p.offset
    area = np.flatnonzero((np.abs(w[:, 0]) < 3) & (w[:, 1] > -3.5) & (w[:, 1] < -0.5))  # a piece of the road
    window.set_selection(area, {"tool": "test", "op": "replace"})
    a.points_from_selection()
    ps = a.model.root[-1]
    window.clear_selection()
    a.dock.tree.clearSelection()
    window.clip_to_selected_sets()  # nothing chosen in the tree: nothing happens
    assert window.clip_set is None
    a._item_of(ps.id).setSelected(True)
    a.dock.clip_to.click()
    d = vp.renderer.display
    assert window.clip_set is not None and d.clip_set_on and window.clip.set_on.isChecked()
    assert np.array_equal(vp.renderer.marks[INSIDE_BIT], ps.indices) and vp.pick_inside is window.clip_set
    assert ps.name in window.clip.set_label.text() and f"{ps.indices.size:,} points" in window.clip.set_label.text()

    # only the set is drawn
    vp.camera.target = xyz[area].mean(0)
    vp.camera.distance = 30.0
    vp.set_view("top", animate=False)
    vp.camera.target = xyz[area].mean(0)
    assert window.settle()
    vp.makeCurrent()
    try:
        depth = vp.renderer.read_depth()
    finally:
        vp.doneCurrent()
    lo, hi = xyz[area].min(0), xyz[area].max(0)
    corners = np.array([(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    sx = vp.screen_of(corners) * vp.devicePixelRatioF()
    s_lo, s_hi = np.floor(sx.min(0)) - 3, np.ceil(sx.max(0)) + 3
    ys, xs = np.nonzero(depth < 1.0)
    assert len(xs) > 50 and np.all((xs >= s_lo[0]) & (xs <= s_hi[0]) & (ys >= s_lo[1]) & (ys <= s_hi[1]))

    # a rectangle over the whole view, through: only the set's points
    window.toggle_tool("rect")
    window.set_select_through(True)
    W, H = vp.physical_size()
    vp.shape_done.emit(np.array([(0, 0), (W, 0), (W, H), (0, H)], float), "replace")
    t0 = time.perf_counter()
    while window._sel_busy and time.perf_counter() - t0 < 60:
        app.processEvents()
    assert window.selection.size > 0 and np.isin(window.selection, ps.indices).all()
    window.set_select_through(False)

    # grow select from a seed in the set stays in it (the road goes on beyond it)
    window.toggle_tool("grow")
    seed = area[np.argmin(np.hypot(w[area, 0], w[area, 1] + 2.0))]

    def grown():
        t0 = time.perf_counter()
        while (window._grow_busy or window._grow_pending or window._grow_timer.isActive()) and time.perf_counter() - t0 < 60:
            app.processEvents()
        return window.selection.copy()

    vp.seed_picked.emit(xyz[seed], "replace")
    inside = grown()
    assert inside.size > 0 and np.isin(inside, ps.indices).all()
    window.toggle_point_set_clip()  # Ctrl+Shift+K: off (the set kept)
    assert not d.clip_set_on and vp.pick_inside is None and window._filters().inside is None
    vp.seed_picked.emit(xyz[seed], "replace")
    assert not np.isin(grown(), ps.indices).all()  # unclipped, the grow goes on along the road
    window.toggle_point_set_clip()  # and on again
    assert d.clip_set_on and vp.pick_inside is window.clip_set
    window.toggle_tool(None)
    window.set_point_set_clip(None)
    assert not d.clip_set_on and not window.clip.set_on.isEnabled() and vp.renderer.marks[INSIDE_BIT].size == 0


def test_undo_keys_in_a_number_field_undo_the_app(window):
    """Regression: after changing a spin box (e.g. 3D select's vertical lines) it kept the focus, and Ctrl+Z went to
    Qt's own text undo of that field: each press stepped the number back instead of undoing the app."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    assert window.settle()
    spin = window.volume.dock.verticals
    edit = spin.lineEdit()
    for key, mods in ((Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier), (Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier),
                      (Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)):  # fmt: skip
        for target in (spin, edit):
            claim = QKeyEvent(QEvent.Type.ShortcutOverride, key, mods)
            claim.ignore()
            QApplication.sendEvent(target, claim)
            assert not claim.isAccepted(), (key, target)  # left to the window's Undo / Redo
    claim = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    claim.ignore()
    QApplication.sendEvent(edit, claim)
    assert claim.isAccepted()  # other editing keys stay the field's (select all)
    name = window.annot.dock.label.lineEdit()  # a text field keeps its own undo while typing
    claim = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    claim.ignore()
    QApplication.sendEvent(name, claim)
    assert claim.isAccepted()


def test_ctrl_keys_in_3d_select_stay_the_window_shortcuts(window):
    """Regression: with 3D select the tool and the view focused, Ctrl+Z arrived as Z (fewer vertical lines) instead
    of undoing; Ctrl+Enter (subtract) and the plain keys stay 3D select's."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    assert window.settle()
    vp = window.viewport
    window.toggle_tool("volume")
    ctrl, shift = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier
    for key, mods, ours in ((Qt.Key.Key_Z, ctrl, False), (Qt.Key.Key_Z, ctrl | shift, False), (Qt.Key.Key_K, ctrl, False),
                            (Qt.Key.Key_D, ctrl, False), (Qt.Key.Key_Z, Qt.KeyboardModifier.NoModifier, True),
                            (Qt.Key.Key_Q, shift, True), (Qt.Key.Key_Return, ctrl, True)):  # fmt: skip
        claim = QKeyEvent(QEvent.Type.ShortcutOverride, key, mods)
        claim.ignore()
        QApplication.sendEvent(vp, claim)
        assert claim.isAccepted() == ours, (key, mods)
    v0 = window.volume.dock.verticals.value()
    got = []
    vp.volume_key.connect(lambda k, m: got.append(k))
    QApplication.sendEvent(vp, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Z, ctrl))
    QTest.keyRelease(vp, Qt.Key.Key_Control)  # Qt keeps the last modifiers app-wide: let go of Ctrl for later tests
    assert not got and window.volume.dock.verticals.value() == v0
    window.toggle_tool(None)


def test_rename_plane(window):
    window.set_selection(np.arange(0, 5000, dtype=np.int64), {"tool": "test", "op": "replace"})
    window.fit_plane()
    i = window.active_frame
    old = window.frames[i].name
    window.rename_frame("crown of the road")
    assert window.frames[i].name == "crown of the road" and window.frames_dock.frame.currentText() == "crown of the road"
    assert "crown of the road" in [f["name"] for f in window.project.load_frames()[0]]
    window.rename_frame("z (scan frame)")  # names stay unique
    assert window.frames[i].name == "crown of the road"
    window.undo()
    assert window.frames[i].name == old and window.frames_dock.frame.currentText() == old


def test_labels_view_and_label_map(window, tmp_path):
    """B: points, segments and curves in their labels' colours (a smaller set over a larger one); the legend; a
    colour changed and saved; a label unticked; Shift+B: the map on a plane, PNG and JSON."""
    import json

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from pclabel.render.shaders import MODE_LABELS
    from pclabel.store.annotations import PointSet, Segment

    assert window.settle()
    a, lc = window.annot, window.labels
    act = {x.shortcut().toString(): x for x in window.actions()}
    xyz = window.project.xyz
    a.model.root.clear()  # the project is shared by these tests: start from an empty tree
    a.model.label_colors.clear()
    road = np.flatnonzero(xyz[:, 2] < np.percentile(xyz[:, 2], 30))
    mark = road[:: 50]
    ps_road = a.model.add(PointSet(road), "road set")
    ps_road.label = "road"
    d = a.new_directory("lines")
    d.label = "marking"
    ps_mark = a.model.add(PointSet(mark), "dash")
    a.model.move([ps_mark.id], d.id)
    p0 = xyz[road[0]].astype(np.float64) + window.project.offset
    seg = a.model.add(Segment(p0, p0 + [3.0, 0.0, 0.0]))
    seg.label = "curb"
    a._changed()
    act["B"].trigger()
    assert window.display.mode.currentData() == MODE_LABELS and lc.dock.view.isChecked()
    r = window.viewport.renderer
    codes = lc.codes()
    assert list(codes) == ["road", "curb", "marking"]  # taxonomy classes in order, then flags
    assert [c for _, c in r.label_layers] == [codes["road"], codes["marking"]]  # the smaller set drawn later
    legend = [lc.dock.legend.topLevelItem(i).text(0) for i in range(lc.dock.legend.topLevelItemCount())]
    assert legend == ["road", "curb", "marking"]
    road_rgb = tuple(int(window.taxonomy.classes[[c["name"] for c in window.taxonomy.classes].index("road")]["color"][i : i + 2], 16) for i in (1, 3, 5))
    page = r.pages[int(np.flatnonzero([len(p) for p in r.pages])[0])][0]
    window.viewport.makeCurrent()
    try:
        lab = np.frombuffer(page.lab.read(), np.uint8).reshape(-1, 4)
    finally:
        window.viewport.doneCurrent()
    in_mark = np.isin(np.arange(page.a, page.a + page.n), mark)
    assert (lab[in_mark, 3] == codes["marking"]).all()
    assert lc.vector_rgba(seg)[:3] == tuple(v / 255 for v in lc.color_table(codes)[codes["curb"], :3])
    assert tuple(lc.color_table(codes)[codes["road"], :3]) == road_rgb
    marking_color = lc.color("marking")  # not a class: a palette colour, given once and saved
    assert a.model.label_colors["marking"] == marking_color
    lc.pick_color("marking", "#00ff88")
    saved = json.loads((window.project.path / "annotations.json").read_text(encoding="utf-8"))
    assert saved["schema"] == "pclabel.annotations/5" and saved["label_colors"]["marking"] == "#00ff88"
    assert tuple(lc.color_table(lc.codes())[lc.codes()["marking"], :3]) == (0, 255, 136)
    it = lc.dock.legend.topLevelItem(0)
    it.setCheckState(0, Qt.CheckState.Unchecked)  # road left out of the view and the map
    assert "road" in lc.hidden and [c for _, c in r.label_layers] == [lc.codes()["marking"]]
    # the annotations with the ticked labels (curb, marking), in their directories
    ann = tmp_path / "ticked.json"
    assert lc.export_annotations(ann) == ann
    exported = json.loads(ann.read_text(encoding="utf-8"))
    assert exported["labels_exported"] == ["curb", "marking"]
    assert [n["name"] for n in exported["tree"]] == ["lines", seg.name] and exported["tree"][0]["label"] == "marking"
    lc.set_shown([])
    assert lc.export_annotations(ann) is None and "tick" in lc.dock.status.text()
    lc.set_shown(["curb", "marking"])
    # the map, on z; road left out, then back in
    out = tmp_path / "map.png"
    lc.dock.pixel.setValue(10.0)
    lc.fill_planes()
    lc.dock.plane.setCurrentIndex(0)  # z (other tests may have left a fitted plane as the height axis)
    assert lc.export_map(out) == out
    t0 = time.perf_counter()
    while lc._busy and time.perf_counter() - t0 < 60:
        QApplication.instance().processEvents()
        time.sleep(0.01)
    doc = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert doc["schema"] == "pclabel.label-map/1" and [e["label"] for e in doc["legend"]] == ["curb", "marking"]
    assert doc["legend"][1]["points_drawn"] == mark.size and doc["vectors"][0]["label"] == "curb"
    assert np.allclose(doc["vectors"][0]["map_xy"][1][0] - doc["vectors"][0]["map_xy"][0][0], 3.0)
    img = QImage(str(out))
    assert img.width() == doc["transform"]["width"] + 240 and img.height() >= doc["transform"]["height"]
    it = lc.dock.legend.topLevelItem(0)
    it.setCheckState(0, Qt.CheckState.Checked)
    act["B"].trigger()  # back to the colour mode before
    assert window.display.mode.currentData() != MODE_LABELS and not lc.dock.view.isChecked()
    assert lc.vector_rgba(seg) is not None


def test_flags_are_offered_as_labels(window):
    """Road lines (R-006): the label box offers the taxonomy's flags after its classes, `marking` among them."""
    window.annot.rebuild_tree()
    box = window.annot.dock.label
    items = [box.itemText(i) for i in range(box.count())]
    flags = [f["name"] for f in window.taxonomy.flags]
    assert "marking" in items and items.index("road") < items.index("marking")
    assert items[items.index(flags[0]) : items.index(flags[0]) + len(flags)] == flags


def test_density_panel(window):
    from PySide6.QtWidgets import QApplication

    from pclabel.render.renderer import UNSURE_BIT

    app = QApplication.instance()
    assert window.settle()
    a, ds, p = window.annot, window.density, window.project
    window.activate_frame(0)
    w = np.asarray(p.xyz, np.float64) + p.offset
    road = np.flatnonzero((np.abs(w[:, 1] + 1.5) < 1.5) & (np.abs(w[:, 0]) < 25))  # the road near and far
    window.set_selection(road, {"tool": "test", "op": "replace"})
    a.points_from_selection()
    ps = a.model.root[-1]
    kept = ps.indices.copy()
    a.dock.tree.clearSelection()
    a._item_of(ps.id).setSelected(True)

    def wait():
        t0 = time.perf_counter()
        while (ds._busy or ds._timer.isActive() or ds._again is not None) and time.perf_counter() - t0 < 60:
            app.processEvents()

    a.dock.density.click()
    wait()
    assert np.array_equal(window.selection, ps.indices) and ds.last is not None
    dense, sparse, info = ds.last
    assert info["params"]["min_density"] == 2000 and "density over the set" in ds.dock.result.text()
    assert np.array_equal(window.viewport.renderer.marks[UNSURE_BIT], sparse)
    # this small synthetic scan is sparse: set the threshold at its median density instead
    ds.dock.min_density.setValue(info["density_percentiles"]["50"])
    wait()
    dense, sparse, info = ds.last
    assert dense.size > 0 and sparse.size > 0 and dense.size + sparse.size == ps.indices.size
    # near the scanner it is denser: the dense part lies nearer than the sparse part
    org = np.asarray(p.scan_origins[0], np.float64)
    rng = lambda i: np.hypot(*(np.asarray(p.xyz[i], np.float64)[:, :2] - org[:2]).T)  # noqa: E731
    assert np.median(rng(dense)) < np.median(rng(sparse))
    ds.select_dense()
    assert np.array_equal(window.selection, dense) and window.selection_def["tool"] == "density"
    assert window.viewport.renderer.marks[UNSURE_BIT].size == 0
    n = len(a.model.root)
    ds.preview()
    wait()
    saved = ds.save()
    assert len(a.model.root) == n + 1 and np.array_equal(saved.indices, dense) and saved.name.startswith(ps.name)
    assert saved.provenance[0]["tool"] == "density" and np.array_equal(ps.indices, kept)  # the set kept
    window.undo()
    assert len(a.model.root) == n
    ds.cancel()


def test_keep_pieces_of_the_selection(window):
    """A selection of a road patch plus a few stray points far from it: click the patch (P) to keep it alone, Shift+click
    a stray to add it back, drop the small pieces, keep the largest; each one undo step."""
    from PySide6.QtWidgets import QApplication

    from pclabel.render.renderer import UNSURE_BIT

    app = QApplication.instance()
    assert window.settle()
    pc, p, vp = window.pieces, window.project, window.viewport
    xyz = np.asarray(p.xyz, np.float64)
    w = xyz + p.offset
    patch = np.flatnonzero((np.abs(w[:, 0]) < 2) & (np.abs(w[:, 1] + 1.5) < 1.0))
    far = np.flatnonzero(np.hypot(w[:, 0] - 15, w[:, 1] + 1.5) < 4)
    strays = np.random.default_rng(0).choice(far, 4, replace=False)  # four lone points 10+ m away
    base = np.union1d(patch, strays)
    window.set_selection(base, {"tool": "test", "op": "replace"})
    base = window.selection
    pc.dock.min_points.setValue(10)

    def wait():
        t0 = time.perf_counter()
        while (pc._busy or pc._timer.isActive()) and time.perf_counter() - t0 < 60:
            app.processEvents()

    window.toggle_tool("piece")
    assert pc.dock.click.isChecked() and pc.base is base
    wait()
    assert pc.pieces.source is base and pc.pieces.count >= 2
    assert np.isin(strays, vp.renderer.marks[UNSURE_BIT]).all()  # the lone points: small pieces, magenta
    centre = xyz[patch[np.argmin(np.hypot(w[patch, 0], w[patch, 1] + 1.5))]]
    vp.seed_picked.emit(centre, "replace")  # a click on the patch
    wait()
    kept = window.selection.copy()
    assert window.selection_def["tool"] == "pieces" and not np.isin(strays, kept).any()
    assert np.isin(kept, patch).all() and kept.size > 0.9 * patch.size
    vp.seed_picked.emit(xyz[strays[0]], "add")  # Shift+click: add one stray back
    wait()
    assert np.array_equal(window.selection, np.union1d(kept, strays[:1]))
    vp.seed_picked.emit(xyz[strays[0]], "subtract")  # Ctrl+click: and take it away again
    wait()
    assert np.array_equal(window.selection, kept)
    window.undo()
    window.undo()
    window.undo()
    assert np.array_equal(window.selection, base)  # one undo step per click
    window.toggle_tool(None)
    assert pc.base is None and not pc.dock.click.isChecked()

    # the buttons, on the current selection
    pc.dock.drop.click()
    wait()
    assert not np.isin(strays, window.selection).any() and np.isin(window.selection, patch).all()
    window.undo()
    pc.dock.largest.click()
    wait()
    assert np.isin(window.selection, patch).all() and window.selection.size > 0.9 * patch.size


def test_mark_labels_uncertain(window):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    a = window.annot
    assert window.settle()
    window.set_selection(np.arange(100, dtype=np.int64), {"tool": "test", "op": "replace"})
    a.points_from_selection()
    ps = a.model.root[-1]
    ps.label = "ramp"
    a._changed()
    assert "ramp" in [a.dock.label.itemText(i) for i in range(a.dock.label.count())]  # the new class
    item = a._item_of(ps.id)
    assert item.checkState(2) == Qt.CheckState.Unchecked
    item.setCheckState(2, Qt.CheckState.Checked)  # the tree's box
    QApplication.instance().processEvents()
    assert ps.uncertain and a._item_of(ps.id).font(1).italic()
    window.undo()
    ps = a.model.node(ps.id)
    assert not ps.uncertain
    a.dock.tree.clearSelection()
    a._item_of(ps.id).setSelected(True)
    a.dock.uncertain.click()  # the button: uncertain, again: sure
    assert a.model.node(ps.id).uncertain and "uncertain" in a.dock.status.text()
    a.dock.uncertain.click()
    assert not a.model.node(ps.id).uncertain
    d = a.new_directory("unsure dir")  # directories cannot be uncertain: no box, and the button leaves them alone
    assert not (a._item_of(d.id).flags() & Qt.ItemFlag.ItemIsUserCheckable)
    a.dock.tree.clearSelection()
    a._item_of(d.id).setSelected(True)
    a.dock.uncertain.click()
    assert "directories cannot be uncertain" in a.dock.status.text() and not hasattr(d, "uncertain")


def test_ctrl_g_puts_the_point_set_in_the_selected_directory(window):
    """Ctrl+G: inside the directory selected in the tree, or beside the one item selected; with nothing selected, or
    with Ctrl+Shift+G, at the top level."""
    assert window.settle()
    a = window.annot
    act = {x.shortcut().toString(): x for x in window.actions()}
    window.set_selection(np.arange(200, 300, dtype=np.int64), {"tool": "test", "op": "replace"})
    d = a.new_directory("markings")
    a.dock.tree.clearSelection()
    a._item_of(d.id).setSelected(True)
    act["Ctrl+G"].trigger()
    first = d.children[-1]
    assert first.kind == "points" and np.array_equal(first.indices, window.selection) and "in markings" in a.dock.status.text()
    a.dock.tree.clearSelection()
    a._item_of(first.id).setSelected(True)  # an item selected: the new one goes beside it
    act["Ctrl+G"].trigger()
    assert len(d.children) == 2
    n_root = len(a.model.root)
    act["Ctrl+Shift+G"].trigger()  # outside everything, whatever is selected
    assert len(a.model.root) == n_root + 1 and a.model.root[-1].kind == "points" and len(d.children) == 2
    a.dock.tree.clearSelection()
    act["Ctrl+G"].trigger()  # nothing selected in the tree: the top level
    assert len(a.model.root) == n_root + 2
    window.undo()
    window.undo()
    assert len(a.model.root) == n_root and len(d.children) == 2


def test_duplicate(window):
    """Ctrl+D: copies of the selected directories (with their contents) and items, each after its original, with new
    ids and the labels and marks kept; the copies end up selected; undo removes them."""
    assert window.settle()
    a = window.annot
    act = {x.shortcut().toString(): x for x in window.actions()}
    window.set_selection(np.arange(200, 300, dtype=np.int64), {"tool": "test", "op": "replace"})
    d = a.new_directory("dup marks")
    d.label = "marking"
    a.dock.tree.clearSelection()
    a._item_of(d.id).setSelected(True)
    act["Ctrl+G"].trigger()
    ps = d.children[0]
    ps.uncertain = True
    from pclabel.store.annotations import PointSet

    loose = a.model.add(PointSet(np.arange(5, dtype=np.int64)))
    a._changed()
    n_root = len(a.model.root)
    a.dock.tree.clearSelection()
    for x in (d, ps, loose):  # ps is inside d: copied once, with it
        a._item_of(x.id).setSelected(True)
    act["Ctrl+D"].trigger()
    copies = [a.model.node(i) for i in a.selected_ids()]
    names = [x.name for x in a.model.root]
    assert len(a.model.root) == n_root + 2 and names.index("dup marks copy") == names.index("dup marks") + 1
    dc = a.model.root[names.index("dup marks copy")]
    (pc,) = dc.children
    assert dc.label == "marking" and dc.id != d.id and pc.id != ps.id and pc.name == ps.name  # inside a copied directory, names stay
    assert np.array_equal(pc.indices, ps.indices) and pc.uncertain and pc.provenance[-1]["tool"] == "duplicate"
    assert {c.id for c in copies} == {dc.id, a.model.root[names.index(f"{loose.name} copy")].id}
    assert len(d.children) == 1  # the original is untouched
    window.undo()
    assert len(a.model.root) == n_root


def test_rename_inside_a_directory(window):
    """F2 renames a point set, segment or curve inside a directory: always the name, whichever column was clicked,
    also while the viewport has the keyboard."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLineEdit

    from pclabel.store.annotations import Segment

    assert window.settle()
    a, t = window.annot, window.annot.dock.tree
    act = {x.shortcut().toString(): x for x in window.actions()}
    d = a.new_directory("rename dir")
    inner = a.new_directory("rename inner")
    seg = a.model.add(Segment(np.zeros(3), np.ones(3)))
    window.set_selection(np.arange(10, 20, dtype=np.int64), {"tool": "test", "op": "replace"})
    a._changed()
    a.dock.tree.clearSelection()
    a._item_of(inner.id).setSelected(True)
    act["Ctrl+G"].trigger()
    a.move([seg.id, inner.id], d.id)
    ps = inner.children[0]
    for x, new in ((seg, "kerb top"), (ps, "dash 3"), (inner, "east side")):
        t.clearSelection()
        it = a._item_of(x.id)
        it.setSelected(True)
        t.setCurrentItem(it, 3)  # the contents column was clicked last
        window.viewport.setFocus()
        act["F2"].trigger()
        QApplication.instance().processEvents()
        assert t.state() == t.State.EditingState
        ed = [e for e in t.findChildren(QLineEdit) if e.isVisible()][-1]  # the name editor
        ed.selectAll()
        QTest.keyClicks(ed, new)
        QTest.keyClick(ed, Qt.Key.Key_Return)
        QApplication.instance().processEvents()
        assert a.model.node(x.id).name == new and a._item_of(x.id).text(3) == x.describe()
    assert sorted(c.name for c in d.children) == ["east side", "kerb top"] and inner.children[0].name == "dash 3"
    window.undo()
    assert a.model.node(inner.id).name == "rename inner"  # undo takes back the last rename
    t.clearSelection()
    act["F2"].trigger()
    assert "select one" in a.dock.status.text()


def test_tree_tool_band_fit_and_keys_in_the_fields(window):
    """Shift+U: click the ground at the trunk; a cylinder 1.00-1.10 m above it; a click on the trunk slides it across
    the axis only; W A S D typed in a number field move it; Enter selects; save makes trees/tree NN with the fit."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    assert window.settle()
    vp, vol, tr, a, p = window.viewport, window.volume, window.trees, window.annot, window.project
    window.activate_frame(0)  # z
    xyz = np.asarray(p.xyz, np.float64)
    w = xyz + p.offset
    r = np.hypot(w[:, 0] - 6.0, w[:, 1] - 7.5)  # the synthetic tree's trunk (r 0.18 m) on the north verge
    ground = xyz[np.flatnonzero((r > 0.4) & (r < 1.0) & (w[:, 2] < 0.3))[0]]
    trunk = xyz[np.flatnonzero((r < 0.25) & (w[:, 2] > 1.8) & (w[:, 2] < 2.2))[0]]

    def wait():
        t0 = time.perf_counter()
        while window._sel_busy and time.perf_counter() - t0 < 60:
            app.processEvents()

    window.trees.begin()
    assert vp.tool == "tree" and tr.start.isChecked()
    vp.seed_picked.emit(ground, "replace")  # the click on the ground
    prm = vol.dock.params()
    assert vp.tool == "volume" and prm.shape == "cylinder" and (prm.bottom, prm.top, prm.radius) == (1.0, 1.1, 0.5)
    assert np.allclose(vol.base, ground) and tr.ground_h == pytest.approx(ground[2] + p.offset[2])
    vp.volume_placed.emit(trunk)  # a click on the trunk, 2 m up: the cylinder slides there, its height stays
    assert np.allclose(vol.base[:2], trunk[:2]) and vol.base[2] == pytest.approx(ground[2])

    # keys typed in a number field (after setting the radius) move the cylinder; Enter there takes the value only
    spin = vol.dock.spins["radius"][0]
    spin.setFocus()
    claim = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_W, Qt.KeyboardModifier.NoModifier)
    claim.ignore()
    QApplication.sendEvent(spin.lineEdit(), claim)
    assert claim.isAccepted()  # W: the volume's, not the window's height wrap
    before = vol.base.copy()
    QTest.keyClick(spin.lineEdit(), Qt.Key.Key_D)
    step = vol.dock.step.value() / 100
    assert np.linalg.norm(vol.base - before) == pytest.approx(step) and vol.base[2] == pytest.approx(before[2])
    QTest.keyClick(spin.lineEdit(), Qt.Key.Key_A)
    assert np.allclose(vol.base, before)
    spin.lineEdit().selectAll()
    QTest.keyClicks(spin.lineEdit(), "0.3")
    sel_before = window.selection.copy()
    QTest.keyClick(spin.lineEdit(), Qt.Key.Key_Return)
    app.processEvents()
    assert vol.dock.params().radius == pytest.approx(0.3) and np.array_equal(window.selection, sel_before)

    vol.confirm("replace")  # Enter in the view
    wait()
    band = window.selection
    hb = w[band, 2] - (ground[2] + p.offset[2])
    assert band.size >= 5 and np.all((hb >= 1.0 - 1e-6) & (hb <= 1.1 + 1e-6)) and np.all(r[band] < 0.3)
    tree = tr.save()
    assert tree is not None and tree.name == "tree 01"
    trees = [x for x in a.model.root if x.kind == "directory" and x.name == "trees"][0]
    ps = tree.children[0]
    assert tree in trees.children and ps.kind == "points" and ps.label == "tree_trunk" and np.array_equal(ps.indices, band)
    rec = ps.provenance[-1]
    assert rec["tool"] == "tree" and rec["band"] == pytest.approx([1.0, 1.1])
    assert np.hypot(rec["position"][0] - 6.0, rec["position"][1] - 7.5) < 0.05
    assert rec["position"][2] == pytest.approx(ground[2] + p.offset[2]) and rec["radius"] == pytest.approx(0.18, abs=0.04)
    assert ps.provenance[-2]["tool"] == "select_volume"
    assert tr.save().name == "tree 02"
    window.undo()
    assert [c.name for c in [x for x in a.model.root if x.name == "trees"][0].children] == ["tree 01"]
    # save into another directory: chosen in "save into"; numbering starts again there
    verge = a.new_directory("north verge")
    tr.into.showPopup()  # opening the list refills it
    tr.into.hidePopup()
    tr.into.setCurrentIndex(tr.into.findData(verge.id))
    t3 = tr.save()
    assert t3.name == "tree 01" and t3 in a.model.node(verge.id).children
    assert tr.into.currentData() == verge.id  # the choice stays for the next tree
    assert tr.save() in a.model.node(verge.id).children
    window.toggle_tool(None)
    assert not vol.keep_height and not tr.start.isChecked()
