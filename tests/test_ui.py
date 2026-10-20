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
