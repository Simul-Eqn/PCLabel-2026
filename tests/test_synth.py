import numpy as np

from pclabel.synth import make_street, tiled_copy

ROAD_CLASSES = ("road", "curb", "curb_lowered", "pavement", "drain", "grass", "tree_trunk", "tree_canopy", "lamp_post")


def road_plane(p, x, y):
    """Analytic road surface (crowned), extended flat beyond the toe."""
    W = p.road_half_width
    return p.grade * x - p.crossfall * np.minimum(np.abs(y), W)


def test_deterministic(street_params, taxonomy):
    a = make_street(street_params, taxonomy)
    b = make_street(street_params, taxonomy)
    assert np.array_equal(a.xyz, b.xyz) and np.array_equal(a.cls, b.cls)


def test_size_close_to_target(street, street_params):
    assert abs(len(street) - street_params.n_target) < 0.05 * street_params.n_target


def test_classes_from_taxonomy(street, taxonomy):
    present = set(np.unique(street.cls).tolist())
    for name in ROAD_CLASSES:
        assert taxonomy.class_id(name) in present, name
    assert 0 not in present  # ground truth never uses "unassessed"


def test_markings_are_flags_on_road(street, taxonomy):
    marked = (street.flags & taxonomy.flag_mask("marking")) > 0
    assert marked.sum() > 1000
    assert np.all(street.cls[marked] == taxonomy.class_id("road"))


def test_only_drains_below_the_road(street, street_params, taxonomy):
    """The O-0002 premise holds by construction: every point well below the road surface is a drain."""
    x, y, z = street.xyz.T.astype(np.float64)
    below = z < road_plane(street_params, x, y) - 0.02
    assert below.sum() > 100
    assert np.all(street.cls[below] == taxonomy.class_id("drain"))


def test_no_point_strip_behind_near_kerb(street, street_params, taxonomy):
    """The scanner stands on the south pavement: the kerb lip shadows a strip of road north of the south toe."""
    p = street_params
    W = p.road_half_width
    x, y = street.xyz[:, 0], street.xyz[:, 1]
    a, b = p.lowered_x
    away = (np.abs(x) < 25) & ((x < a - p.lowered_ramp - 1) | (x > b + p.lowered_ramp + 1))
    ground = np.isin(street.cls, [taxonomy.class_id("road"), taxonomy.class_id("drain")])
    south = away & ground & (y > -W + 0.01) & (y < -W + 0.10)
    north = away & ground & (y < W - 0.01) & (y > W - 0.10)
    assert south.sum() == 0
    assert north.sum() > 100


def test_back_facing_kerb_face_has_no_points(street, street_params, taxonomy):
    p = street_params
    x, y, z = street.xyz.T.astype(np.float64)
    toe = road_plane(p, x, np.full_like(y, p.road_half_width))
    face = (street.cls == taxonomy.class_id("curb")) & (z - toe < p.curb_step - 0.03) & (z - toe > 0.03)
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
    assert np.array_equal(t.cls[:n], t.cls[n : 2 * n])
