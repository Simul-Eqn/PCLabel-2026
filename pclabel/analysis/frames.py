"""Height frames: the axis heights are measured along (SPEC §3, "flatten from selection").

A frame is a plane: ``h(p) = (p - origin) . normal``. The scan frame uses z. A plane frame comes from a robust plane
fit to selected points (least squares, then drop residuals beyond ``trim_mad`` x MAD and refit, ``iterations``
times), so a road patch with crossfall and grade becomes level and the height wrap, height ramp and slice show
relief relative to it. Frames are view/analysis transforms only; stored coordinates never change (D-006).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np


GROUND_TILT_DEG = 10.0  # a fit steeper or rougher than this gets a warning in its report
GROUND_RMS_M = 0.05


@dataclass(frozen=True)
class PlaneFitParams:
    trim_mad: float = 2.5  # drop |residual - median| > trim_mad * MAD (MAD scaled to sigma)
    iterations: int = 3
    max_points: int = 2_000_000  # random subsample above this
    seed: int = 0


@dataclass
class HeightFrame:
    name: str
    normal: tuple  # unit vector, world, pointing up (z >= 0)
    origin: tuple  # world point where h = 0
    along: tuple = (1.0, 0.0, 0.0)  # horizontal unit vector: the selection's main direction (grade is along it)
    report: dict = field(default_factory=dict)

    def heights(self, xyz_local: np.ndarray, offset) -> np.ndarray:
        """h of store points (local coordinates), float64. Mirrors the point shader."""
        o = np.asarray(self.origin, np.float64) - np.asarray(offset, np.float64)
        return (np.asarray(xyz_local, np.float64) - o) @ np.asarray(self.normal, np.float64)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "HeightFrame":
        return cls(d["name"], tuple(d["normal"]), tuple(d["origin"]), tuple(d.get("along", (1.0, 0.0, 0.0))),
                   dict(d.get("report", {})))  # fmt: skip


Z_FRAME = HeightFrame("z (scan frame)", (0.0, 0.0, 1.0), (0.0, 0.0, 0.0))


def slope_percent(normal, direction) -> float:
    """Slope of the plane (rise per run, %) along a horizontal direction."""
    n = np.asarray(normal, np.float64)
    a = np.asarray(direction, np.float64)
    return float(-(n[0] * a[0] + n[1] * a[1]) / n[2] * 100.0)


def fit_plane(points: np.ndarray, params: PlaneFitParams = PlaneFitParams()) -> dict:
    """Robust plane through points (n, 3) float64. Returns normal (up), centroid, along, rms, counts."""
    p = np.asarray(points, np.float64)
    n_input = len(p)
    if n_input < 3:
        raise ValueError("a plane needs at least 3 points")
    if n_input > params.max_points:
        p = p[np.random.default_rng(params.seed).choice(n_input, params.max_points, replace=False)]
    keep = np.ones(len(p), bool)
    for _ in range(params.iterations + 1):
        q = p[keep]
        c = q.mean(0)
        _, sv, vt = np.linalg.svd(q - c, full_matrices=False)
        normal = vt[2] if vt[2][2] >= 0 else -vt[2]
        r = (p - c) @ normal
        med = np.median(r[keep])
        mad = 1.4826 * np.median(np.abs(r[keep] - med))
        new_keep = np.abs(r - med) <= params.trim_mad * mad if mad > 0 else np.ones(len(p), bool)
        if new_keep.sum() < 3 or np.array_equal(new_keep, keep):
            break
        keep = new_keep
    q = p[keep]
    c = q.mean(0)
    _, sv, vt = np.linalg.svd(q - c, full_matrices=False)
    normal = vt[2] if vt[2][2] >= 0 else -vt[2]
    r = (q - c) @ normal
    along = np.array([vt[0][0], vt[0][1], 0.0])
    if np.linalg.norm(along) < 1e-9:
        along = np.array([1.0, 0.0, 0.0])
    along /= np.linalg.norm(along)
    if along[0] < 0 or (along[0] == 0 and along[1] < 0):
        along = -along  # deterministic sign: towards +x
    return {
        "normal": normal,
        "centroid": c,
        "along": along,
        "rms": float(np.sqrt(np.mean(r**2))),
        "n_input": int(n_input),
        "n_fitted": int(len(p)),
        "n_kept": int(keep.sum()),
    }


def frame_from_points(points_world: np.ndarray, name: str, params: PlaneFitParams = PlaneFitParams()) -> HeightFrame:
    """Plane frame fitted to world points, with the fit report (method, parameters, counts, slopes, noise)."""
    f = fit_plane(points_world, params)
    n, a = f["normal"], f["along"]
    across = np.array([-a[1], a[0], 0.0])
    report = {
        "method": "least-squares plane, MAD trimming",
        "params": asdict(params),
        "points_selected": f["n_input"],
        "points_fitted": f["n_fitted"],
        "points_kept": f["n_kept"],
        "rms_m": f["rms"],
        "tilt_deg": float(np.degrees(np.arccos(np.clip(n[2], -1, 1)))),
        "along_azimuth_deg": float(np.degrees(np.arctan2(a[1], a[0]))),
        "grade_percent": slope_percent(n, a),
        "crossfall_percent": slope_percent(n, across),
        "suggested_min_period_m": 4.0 * f["rms"],  # below about 4 sigma a wrap shows mostly noise (SPEC §2.1)
    }
    tilt, rms = report["tilt_deg"], report["rms_m"]
    if tilt > GROUND_TILT_DEG or rms > GROUND_RMS_M:
        report["warning"] = (f"does not look like ground (tilt {tilt:.1f}°, RMS {rms * 100:.1f} cm): the selection "
                             "may include vegetation, kerbs or objects")  # fmt: skip
    return HeightFrame(name, tuple(map(float, n)), tuple(map(float, f["centroid"])), tuple(map(float, a)), report)
