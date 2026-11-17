import numpy as np

from pclabel.synth import make_street, tiled_copy
from pclabel.synth.street import is_material

MATERIAL_NAMES = ("road", "marking", "curb", "curb_lowered", "pavement", "drain", "grass", "tree_trunk", "tree_canopy",
                  "lamp_post")  # fmt: skip
STANDARD_CLASSES = ("road", "road_line", "pavement", "vegetation", "tree_trunk", "lamp_post")


def road_plane(p, x, y):
    """Analytic road surface (crowned), extended flat beyond the toe."""
    W = p.road_half_width
    return p.grade * x - p.crossfall * np.minimum(np.abs(y), W)


def test_deterministic(street_params, taxonomy):
    a = make_street(street_params, taxonomy)
    b = make_street(street_params, taxonomy)
    assert np.array_equal(a.xyz, b.xyz) and np.array_equal(a.cls, b.cls) and np.array_equal(a.material, b.material)


def test_size_close_to_target(street, street_params):
    assert abs(len(street) - street_params.n_target) < 0.05 * street_params.n_target


def test_classes_are_the_standards(street, taxonomy):
    """Every material is present; the class of its points is the labelling standard's: the curb is a line (its
    points have no class), the strip with its drains and the canopies are not labelled, grass is vegetation."""
    for name in MATERIAL_NAMES:
        assert is_material(street.material, name).any(), name
    present = set(np.unique(street.cls).tolist())
    assert present == {0} | {taxonomy.class_id(c) for c in STANDARD_CLASSES}
    for name in ("curb", "curb_lowered", "drain", "tree_canopy"):
        assert (street.cls[is_material(street.material, name)] == 0).all(), name
    assert (street.cls[is_material(street.material, "grass")] == taxonomy.class_id("vegetation")).all()
    assert not street.flags.any()


def test_markings_are_road_lines(street, taxonomy):
    marked = is_material(street.material, "marking")
    assert marked.sum() > 1000
    assert np.all(street.cls[marked] == taxonomy.class_id("road_line"))


def test_only_drains_below_the_road(street, street_params, taxonomy):
    """The O-0002 premise holds by construction: every point well below the road surface is a drain."""
    x, y, z = street.xyz.T.astype(np.float64)
    below = z < road_plane(street_params, x, y) - 0.02
    assert below.sum() > 100
    assert np.all(is_material(street.material[below], "drain"))


def test_no_point_strip_behind_near_curb(street, street_params, taxonomy):
    """The scanner stands on the south pavement: the curb lip shadows a strip of road north of the south toe."""
    p = street_params
    W = p.road_half_width
    x, y = street.xyz[:, 0], street.xyz[:, 1]
    a, b = p.lowered_x
    away = (np.abs(x) < 25) & ((x < a - p.lowered_ramp - 1) | (x > b + p.lowered_ramp + 1))
    ground = is_material(street.material, "road", "drain")
    south = away & ground & (y > -W + 0.01) & (y < -W + 0.10)
    north = away & ground & (y < W - 0.01) & (y > W - 0.10)
    assert south.sum() == 0
    assert north.sum() > 100


def test_back_facing_curb_face_has_no_points(street, street_params, taxonomy):
    p = street_params
    x, y, z = street.xyz.T.astype(np.float64)
    toe = road_plane(p, x, np.full_like(y, p.road_half_width))
    face = is_material(street.material, "curb") & (z - toe < p.curb_step - 0.03) & (z - toe > 0.03)
    assert face[y < 0].sum() == 0  # south face looks away from the scanner
    assert face[y > 0].sum() > 100  # north face looks at it


def test_blind_cone_and_density_falloff(street, street_params):
    o = street.origin
    r = np.hypot(street.xyz[:, 0] - o[0], street.xyz[:, 1] - o[1])
    ground = street.xyz[:, 2] < o[2] - 1.0
    blind = street_params.scanner_height / np.tan(np.deg2rad(-street_params.min_elevation_deg))
    assert (ground & (r < blind - 0.05)).sum() == 0
    near = (ground & (r > 2) & (r < 3)).sum() / (np.pi * (3**2 - 2**2))
    far = (ground & (r > 15) & (r < 16)).sum() / (np.pi * (16**2 - 15**2))
    assert near > 20 * far


def test_tiled_copy(street):
    t = tiled_copy(street, 4)
    assert len(t) == 4 * len(street)
    n = len(street)
    shift = t.xyz[n : 2 * n] - t.xyz[:n]
    assert np.allclose(shift[:, 0], shift[0, 0]) and shift[0, 0] > 0
    assert np.array_equal(t.cls[:n], t.cls[n : 2 * n]) and np.array_equal(t.material[:n], t.material[n : 2 * n])
