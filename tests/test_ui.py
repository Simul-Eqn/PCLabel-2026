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
    assert n_box > 100 and window.viewport.renderer._sel is not None
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
    while len(frames_seen) < 2 and time.perf_counter() - t0 < 30:  # a frame drawn with the slice (its depth)
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
    assert n1 > 1000 and window.selection_def["tool"] == "ground_grow"
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
