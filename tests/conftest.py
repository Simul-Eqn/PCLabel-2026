import os

import pytest

from pclabel.synth import StreetParams, make_street
from pclabel.taxonomy import load_taxonomy


@pytest.fixture
def gl(_gl_session):
    """moderngl context on Mesa llvmpipe (CPU only), so tests never touch the GPU. Made current for each test
    (a QOpenGLWidget in another test may have left its own context current).

    ``PCLABEL_TEST_GL=hardware`` runs the GL tests on the GPU driver instead. Skips if no Mesa build is found.
    """
    surf, qctx, ctx = _gl_session
    qctx.makeCurrent(surf)
    yield ctx


@pytest.fixture(scope="session")
def _gl_session():
    from pclabel.render import glsetup
    from pclabel.settings import load_settings

    try:
        glsetup.configure(load_settings(), os.environ.get("PCLABEL_TEST_GL", "software"))
    except RuntimeError as e:
        pytest.skip(str(e))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    surf, qctx, ctx = glsetup.offscreen_context()
    yield surf, qctx, ctx
    qctx.makeCurrent(surf)
    ctx.release()
    qctx.doneCurrent()
    del surf, qctx, app


@pytest.fixture(scope="session")
def taxonomy():
    return load_taxonomy()


@pytest.fixture(scope="session")
def street_params():
    return StreetParams(n_target=600_000)


@pytest.fixture(scope="session")
def street(street_params, taxonomy):
    return make_street(street_params, taxonomy)
