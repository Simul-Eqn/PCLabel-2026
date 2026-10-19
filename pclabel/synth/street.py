"""Synthetic street scans with per-point ground truth (ARCHITECTURE.md "testing").

One static TLS setup is simulated by casting rays on an azimuth x elevation grid (constant angular step, column
by column like a real scanner) against an analytic street. Occlusion, back-facing kerb faces, no-point strips
behind kerbs, the blind cone under the tripod and density falling with range all follow from the geometry.

Layout (metres, z up). The road runs along x and is centred on y = 0. Across y, from the centre outwards:
road (|y| <= W), kerb unit (W..C), pavement (C..Pv), grass verge (Pv..Y). Both sides are alike. Additional parts:
- road: crossfall (crowned or one plane) and grade; a dashed centre line and a double yellow line near each kerb
  (3 mm thick, class road + flag ``marking``); drain pits below the road at the kerb toe at regular intervals;
- kerb: a step of ``curb_step`` with a lowered section (a crossing) where it drops to ``lowered_step``, with ramps
  along the kerb and a pavement ramp behind it;
- grass: value-noise roughness; a tree (trunk cylinder, porous ellipsoid canopy) on the north verge;
- a lamp post (pole, arm, head) on the south pavement;
- the scanner stands on the south pavement, so the south kerb face looks away from it (no points) and the road
  just north of the south toe is shadowed by the kerb lip (a no-point strip).

Ground-truth classes are looked up by name in config/taxonomy.yaml; no class id is hard-coded here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numba as nb
import numpy as np

from pclabel.taxonomy import Taxonomy, load_taxonomy

# material codes (internal to the simulator)
M_NONE, M_ROAD, M_MARK_W, M_MARK_Y, M_CURB, M_CURB_LOW, M_PAVEMENT, M_DRAIN, M_GRASS, M_TRUNK, M_CANOPY, M_LAMP = range(12)

# material -> (class name, flag name or None, base rgb, reflectance)
MATERIALS = {
    M_ROAD: ("road", None, (68, 68, 70), 0.20),
    M_MARK_W: ("road", "marking", (225, 225, 220), 0.80),
    M_MARK_Y: ("road", "marking", (215, 180, 40), 0.70),
    M_CURB: ("curb", None, (150, 150, 145), 0.45),
    M_CURB_LOW: ("curb_lowered", None, (150, 150, 145), 0.45),
    M_PAVEMENT: ("pavement", None, (170, 150, 135), 0.40),
    M_DRAIN: ("drain", None, (35, 35, 38), 0.10),
    M_GRASS: ("grass", None, (75, 120, 55), 0.33),
    M_TRUNK: ("tree_trunk", None, (95, 75, 55), 0.30),
    M_CANOPY: ("tree_canopy", None, (45, 95, 45), 0.28),
    M_LAMP: ("lamp_post", None, (120, 125, 130), 0.50),
}


@dataclass(frozen=True)
class StreetParams:
    # road
    length: float = 60.0
    road_half_width: float = 3.7
    crown: bool = True  # True: both halves fall towards the kerbs; False: one plane rising towards +y
    crossfall: float = 0.025
    grade: float = 0.01
    # kerb
    curb_width: float = 0.15
    curb_step: float = 0.15
    lowered_x: tuple[float, float] = (12.0, 16.0)
    lowered_step: float = 0.02
    lowered_ramp: float = 1.0  # along the kerb
    # pavement and verge
    pavement_width: float = 2.5
    pavement_slope: float = 0.02  # rises away from the kerb
    pavement_ramp: float = 1.2  # across the pavement, behind a lowered kerb
    verge_width: float = 5.0
    grass_drop: float = 0.03
    grass_roughness: float = 0.012
    # drains (pits below the road at the kerb toe)
    drain_spacing: float = 10.0
    drain_first: float = -25.0
    drain_length: float = 0.9
    drain_width: float = 0.35
    drain_depth: float = 0.08
    marking_height: float = 0.003
    # tree on the north verge, lamp post on the south pavement (x, |y|)
    tree_xy: tuple[float, float] = (6.0, 7.5)
    trunk_radius: float = 0.18
    trunk_height: float = 3.5
    canopy_radii: tuple[float, float, float] = (2.6, 2.6, 1.8)
    canopy_height: float = 5.2
    canopy_free_path: float = 0.35
    lamp_xy: tuple[float, float] = (-10.0, -4.2)
    lamp_height: float = 8.0
    lamp_radius: float = 0.09
    lamp_arm: float = 1.5
    # scanner
    scanner_xy: tuple[float, float] = (0.0, -5.2)
    scanner_height: float = 1.6
    min_elevation_deg: float = -60.0  # blind cone below
    max_elevation_deg: float = 75.0
    angular_step_deg: float | None = None  # None: chosen to give about n_target points
    n_target: int = 5_000_000
    range_noise: float = 0.002  # 1 sigma, metres, along the ray
    max_range: float = 80.0
    seed: int = 0


@dataclass
class SynthScan:
    """A synthetic scan in source order, with ground truth."""

    xyz: np.ndarray  # float32 (n, 3), relative to offset
    offset: np.ndarray  # float64 (3,)
    rgb: np.ndarray  # uint8 (n, 3)
    intensity: np.ndarray  # uint16 (n,)
    cls: np.ndarray  # uint8 (n,), taxonomy ids
    flags: np.ndarray  # uint8 (n,)
    instance: np.ndarray  # uint32 (n,), 0 = none
    row: np.ndarray  # uint16 (n,), elevation index
    col: np.ndarray  # uint16 (n,), azimuth index
    origin: np.ndarray  # float64 (3,), scanner position (relative to offset)
    params: StreetParams
    taxonomy_sha256: str
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return self.xyz.shape[0]


# ---- parameter vector for the numba kernels --------------------------------------------------------------------

(I_HALF_LEN, I_W, I_C, I_PV, I_Y, I_CROWN, I_XF, I_GRADE, I_STEP, I_LOW_A, I_LOW_B, I_LOW_STEP, I_LOW_RAMP,
 I_PAV_SLOPE, I_PAV_RAMP, I_GRASS_DROP, I_ROUGH, I_DR_SP, I_DR_FIRST, I_DR_LEN, I_DR_W, I_DR_DEPTH, I_MARK_H,
 I_SEED, I_BLO, I_BHI, I_SMAX, N_P) = range(28)

# objects: trunk, canopy, pole, arm, head
(J_TRUNK_X, J_TRUNK_Y, J_TRUNK_R, J_TRUNK_Z0, J_TRUNK_Z1,
 J_CAN_X, J_CAN_Y, J_CAN_Z, J_CAN_RX, J_CAN_RY, J_CAN_RZ, J_CAN_FP,
 J_POLE_X, J_POLE_Y, J_POLE_R, J_POLE_Z0, J_POLE_Z1,
 J_ARM_AX, J_ARM_AY, J_ARM_AZ, J_ARM_BX, J_ARM_BY, J_ARM_BZ, J_ARM_R,
 J_HEAD_X0, J_HEAD_Y0, J_HEAD_Z0, J_HEAD_X1, J_HEAD_Y1, J_HEAD_Z1, N_J) = range(31)


def _param_vector(p: StreetParams) -> np.ndarray:
    P = np.zeros(N_P)
    W = p.road_half_width
    C = W + p.curb_width
    Pv = C + p.pavement_width
    P[I_HALF_LEN] = p.length / 2
    P[I_W], P[I_C], P[I_PV], P[I_Y] = W, C, Pv, Pv + p.verge_width
    P[I_CROWN] = 1.0 if p.crown else 0.0
    P[I_XF], P[I_GRADE], P[I_STEP] = p.crossfall, p.grade, p.curb_step
    P[I_LOW_A], P[I_LOW_B] = p.lowered_x
    P[I_LOW_STEP], P[I_LOW_RAMP] = p.lowered_step, p.lowered_ramp
    P[I_PAV_SLOPE], P[I_PAV_RAMP] = p.pavement_slope, p.pavement_ramp
    P[I_GRASS_DROP], P[I_ROUGH] = p.grass_drop, p.grass_roughness
    P[I_DR_SP], P[I_DR_FIRST], P[I_DR_LEN] = p.drain_spacing, p.drain_first, p.drain_length
    P[I_DR_W], P[I_DR_DEPTH], P[I_MARK_H] = p.drain_width, p.drain_depth, p.marking_height
    P[I_SEED] = float(p.seed)
    P[I_SMAX] = abs(p.grade) + abs(p.crossfall) + 1e-3
    lo, hi = _relative_bounds(P, 0.02)
    P[I_BLO], P[I_BHI] = lo - 0.01, hi + 0.01
    return P


# ---- hashing and noise -----------------------------------------------------------------------------------------

_K1 = np.uint64(0x9E3779B97F4A7C15)
_K2 = np.uint64(0xC2B2AE3D27D4EB4F)
_K3 = np.uint64(0xFF51AFD7ED558CCD)
_K4 = np.uint64(0xC4CEB9FE1A85EC53)
_S33 = np.uint64(33)
_S11 = np.uint64(11)
_BIAS = 1 << 30


@nb.njit(cache=True)
def _mix(h):
    h ^= h >> _S33
    h *= _K3
    h ^= h >> _S33
    h *= _K4
    h ^= h >> _S33
    return h


@nb.njit(cache=True)
def _u01(a, b, seed):
    """Deterministic uniform in [0, 1) from two non-negative integers and a seed."""
    h = _mix(np.uint64(a) * _K1 ^ np.uint64(b) * _K2 ^ np.uint64(seed))
    return float(h >> _S11) * (1.0 / 9007199254740992.0)


@nb.njit(cache=True)
def _value_noise(x, y, seed):
    gx = x / 0.1 + _BIAS
    gy = y / 0.1 + _BIAS
    ix = int(np.floor(gx))
    iy = int(np.floor(gy))
    fx = gx - ix
    fy = gy - iy
    sx = fx * fx * (3.0 - 2.0 * fx)
    sy = fy * fy * (3.0 - 2.0 * fy)
    v00 = _u01(ix, iy, seed) * 2.0 - 1.0
    v10 = _u01(ix + 1, iy, seed) * 2.0 - 1.0
    v01 = _u01(ix, iy + 1, seed) * 2.0 - 1.0
    v11 = _u01(ix + 1, iy + 1, seed) * 2.0 - 1.0
    a = v00 + (v10 - v00) * sx
    b = v01 + (v11 - v01) * sx
    return a + (b - a) * sy


# ---- ground ----------------------------------------------------------------------------------------------------


@nb.njit(cache=True)
def _zbase(x, y, P):
    """Road surface, extended flat beyond the toe (the reference for the march bounds)."""
    W = P[I_W]
    if P[I_CROWN] > 0.5:
        return P[I_GRADE] * x - P[I_XF] * min(abs(y), W)
    return P[I_GRADE] * x + P[I_XF] * min(max(y, -W), W)


@nb.njit(cache=True)
def _kerb_step(x, P):
    a = P[I_LOW_A]
    b = P[I_LOW_B]
    d = 0.0
    if x < a:
        d = a - x
    elif x > b:
        d = x - b
    full = P[I_STEP]
    low = P[I_LOW_STEP]
    return low + (full - low) * min(1.0, d / P[I_LOW_RAMP])


@nb.njit(cache=True)
def _ground(x, y, P):
    """Ground height, material and sub-object id (drain number) at (x, y)."""
    W = P[I_W]
    ay = abs(y)
    side = 1 if y >= 0 else 0
    zb = _zbase(x, y, P)
    full = P[I_STEP]
    if ay <= W:
        if ay > W - P[I_DR_W]:
            k = int(np.floor((x - P[I_DR_FIRST]) / P[I_DR_SP] + 0.5))
            xc = P[I_DR_FIRST] + k * P[I_DR_SP]
            if k >= 0 and abs(x - xc) <= P[I_DR_LEN] / 2:
                return zb - P[I_DR_DEPTH], M_DRAIN, 2 * k + side
        e = W - ay
        if ay <= 0.075 and ((x + P[I_HALF_LEN]) % 9.0) < 3.0:
            return zb + P[I_MARK_H], M_MARK_W, 0
        if (0.35 <= e <= 0.45) or (0.60 <= e <= 0.70):
            return zb + P[I_MARK_H], M_MARK_Y, 0
        return zb, M_ROAD, 0
    s = _kerb_step(x, P)
    if ay <= P[I_C]:
        return zb + s, (M_CURB if s >= full - 1e-9 else M_CURB_LOW), 0
    if ay <= P[I_PV]:
        e = ay - P[I_C]
        return zb + full + P[I_PAV_SLOPE] * e - (full - s) * max(0.0, 1.0 - e / P[I_PAV_RAMP]), M_PAVEMENT, 0
    z = zb + full + P[I_PAV_SLOPE] * (P[I_PV] - P[I_C]) - P[I_GRASS_DROP]
    return z + P[I_ROUGH] * _value_noise(x, y, np.int64(P[I_SEED])), M_GRASS, 0


@nb.njit(cache=True)
def _relative_bounds(P, cell):
    nx = int(2 * P[I_HALF_LEN] / cell) + 1
    ny = int(2 * P[I_Y] / cell) + 1
    lo = 1e9
    hi = -1e9
    for i in range(nx):
        x = -P[I_HALF_LEN] + i * cell
        for j in range(ny):
            y = -P[I_Y] + j * cell
            g, _, _ = _ground(x, y, P)
            r = g - _zbase(x, y, P)
            lo = min(lo, r)
            hi = max(hi, r)
    return lo, hi


# ---- ray casting -----------------------------------------------------------------------------------------------


@nb.njit(cache=True)
def _vcyl(ox, oy, oz, dx, dy, dz, cx, cy, r, z0, z1):
    px = ox - cx
    py = oy - cy
    a = dx * dx + dy * dy
    if a < 1e-12:
        return np.inf
    b = 2.0 * (px * dx + py * dy)
    c = px * px + py * py - r * r
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return np.inf
    t = (-b - np.sqrt(disc)) / (2.0 * a)
    if t <= 1e-6:
        return np.inf
    z = oz + t * dz
    if z < z0 or z > z1:
        return np.inf
    return t


@nb.njit(cache=True)
def _cyl(ox, oy, oz, dx, dy, dz, ax_, ay_, az_, bx, by, bz, r):
    ux = bx - ax_
    uy = by - ay_
    uz = bz - az_
    L = np.sqrt(ux * ux + uy * uy + uz * uz)
    ux /= L
    uy /= L
    uz /= L
    wx = ox - ax_
    wy = oy - ay_
    wz = oz - az_
    dd = dx * ux + dy * uy + dz * uz
    wd = wx * ux + wy * uy + wz * uz
    px = dx - dd * ux
    py = dy - dd * uy
    pz = dz - dd * uz
    qx = wx - wd * ux
    qy = wy - wd * uy
    qz = wz - wd * uz
    a = px * px + py * py + pz * pz
    if a < 1e-12:
        return np.inf
    b = 2.0 * (px * qx + py * qy + pz * qz)
    c = qx * qx + qy * qy + qz * qz - r * r
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return np.inf
    t = (-b - np.sqrt(disc)) / (2.0 * a)
    if t <= 1e-6:
        return np.inf
    s = wd + t * dd
    if s < 0.0 or s > L:
        return np.inf
    return t


@nb.njit(cache=True)
def _box(ox, oy, oz, dx, dy, dz, x0, y0, z0, x1, y1, z1):
    tn = -np.inf
    tf = np.inf
    o = (ox, oy, oz)
    d = (dx, dy, dz)
    lo = (x0, y0, z0)
    hi = (x1, y1, z1)
    for k in range(3):
        if abs(d[k]) < 1e-12:
            if o[k] < lo[k] or o[k] > hi[k]:
                return np.inf
        else:
            ta = (lo[k] - o[k]) / d[k]
            tb = (hi[k] - o[k]) / d[k]
            if ta > tb:
                ta, tb = tb, ta
            tn = max(tn, ta)
            tf = min(tf, tb)
    if tn > tf or tn <= 1e-6:
        return np.inf
    return tn


@nb.njit(cache=True)
def _march(ox, oy, oz, dx, dy, dz, t_lim, P):
    """First ground hit along the ray before t_lim: (t, material, sub, cos incidence) or t = inf."""
    hs = np.sqrt(dx * dx + dy * dy)
    t = 0.0
    t_prev = -1.0
    while t < t_lim:
        x = ox + t * dx
        y = oy + t * dy
        z = oz + t * dz
        zb = _zbase(x, y, P)
        gap = z - (zb + P[I_BHI])
        if gap > 0.0:
            closing = -dz + hs * P[I_SMAX]
            if closing <= 1e-9:
                return np.inf, 0, 0, 0.0
            t += max(gap / closing, 1e-4)
            t_prev = -1.0
            continue
        g, m, s = _ground(x, y, P)
        if z - g <= 0.0:
            ta = t_prev if t_prev >= 0.0 else max(t - 1e-3, 0.0)
            tb = t
            for _ in range(40):
                tm = 0.5 * (ta + tb)
                gm, _, _ = _ground(ox + tm * dx, oy + tm * dy, P)
                if oz + tm * dz - gm > 0.0:
                    ta = tm
                else:
                    tb = tm
            th = 0.5 * (ta + tb)
            xa = ox + ta * dx
            ya = oy + ta * dy
            xb = ox + tb * dx
            yb = oy + tb * dy
            zh = oz + th * dz
            ga, ma, sa = _ground(xa, ya, P)
            gb, mb, sb = _ground(xb, yb, P)
            m, s = mb, sb
            if ma == M_DRAIN and zh < gb - 0.002:
                m, s = M_DRAIN, sa  # far wall of a drain pit, below the road surface
            if gb - ga > 0.002 and zh < gb - 0.001:
                cosi = hs  # vertical wall facing the ray
            else:
                e = 0.01
                gx = (_ground(xb + e, yb, P)[0] - _ground(xb - e, yb, P)[0]) / (2 * e)
                gy = (_ground(xb, yb + e, P)[0] - _ground(xb, yb - e, P)[0]) / (2 * e)
                gx = min(max(gx, -1.0), 1.0)
                gy = min(max(gy, -1.0), 1.0)
                cosi = abs(dz - dx * gx - dy * gy) / np.sqrt(1.0 + gx * gx + gy * gy)
            return th, m, s, cosi
        t_prev = t
        t += min(max(0.0015 * t, 0.003), 0.04)
    return np.inf, 0, 0, 0.0


@nb.njit(cache=True)
def _box_exit(ox, oy, dx, dy, P):
    t = np.inf
    hl = P[I_HALF_LEN]
    Y = P[I_Y]
    if dx > 1e-12:
        t = min(t, (hl - ox) / dx)
    elif dx < -1e-12:
        t = min(t, (-hl - ox) / dx)
    if dy > 1e-12:
        t = min(t, (Y - oy) / dy)
    elif dy < -1e-12:
        t = min(t, (-Y - oy) / dy)
    return t


@nb.njit(cache=True)
def _trace(ox, oy, oz, dx, dy, dz, P, J, max_range, ray_id, seed):
    best = np.inf
    mat = M_NONE
    cosi = 0.0
    hs = np.sqrt(dx * dx + dy * dy)

    t = _vcyl(ox, oy, oz, dx, dy, dz, J[J_TRUNK_X], J[J_TRUNK_Y], J[J_TRUNK_R], J[J_TRUNK_Z0], J[J_TRUNK_Z1])
    if t < best:
        best, mat = t, M_TRUNK
        nx = (ox + t * dx - J[J_TRUNK_X]) / J[J_TRUNK_R]
        ny = (oy + t * dy - J[J_TRUNK_Y]) / J[J_TRUNK_R]
        cosi = abs(nx * dx + ny * dy)
    t = _vcyl(ox, oy, oz, dx, dy, dz, J[J_POLE_X], J[J_POLE_Y], J[J_POLE_R], J[J_POLE_Z0], J[J_POLE_Z1])
    if t < best:
        best, mat = t, M_LAMP
        nx = (ox + t * dx - J[J_POLE_X]) / J[J_POLE_R]
        ny = (oy + t * dy - J[J_POLE_Y]) / J[J_POLE_R]
        cosi = abs(nx * dx + ny * dy)
    t = _cyl(ox, oy, oz, dx, dy, dz, J[J_ARM_AX], J[J_ARM_AY], J[J_ARM_AZ], J[J_ARM_BX], J[J_ARM_BY],
             J[J_ARM_BZ], J[J_ARM_R])
    if t < best:
        best, mat, cosi = t, M_LAMP, 0.7
    t = _box(ox, oy, oz, dx, dy, dz, J[J_HEAD_X0], J[J_HEAD_Y0], J[J_HEAD_Z0], J[J_HEAD_X1], J[J_HEAD_Y1],
             J[J_HEAD_Z1])
    if t < best:
        best, mat, cosi = t, M_LAMP, 0.8

    # porous canopy: exponential free path inside the ellipsoid
    qx = (ox - J[J_CAN_X]) / J[J_CAN_RX]
    qy = (oy - J[J_CAN_Y]) / J[J_CAN_RY]
    qz = (oz - J[J_CAN_Z]) / J[J_CAN_RZ]
    ex = dx / J[J_CAN_RX]
    ey = dy / J[J_CAN_RY]
    ez = dz / J[J_CAN_RZ]
    a = ex * ex + ey * ey + ez * ez
    b = 2.0 * (qx * ex + qy * ey + qz * ez)
    c = qx * qx + qy * qy + qz * qz - 1.0
    disc = b * b - 4.0 * a * c
    if disc > 0.0:
        sq = np.sqrt(disc)
        t_in = max((-b - sq) / (2.0 * a), 0.0)
        t_out = (-b + sq) / (2.0 * a)
        if t_out > 0.0:
            u = _u01(ray_id, 7, seed)
            t = t_in - np.log(1.0 - u) * J[J_CAN_FP]
            if t < t_out and t < best:
                best, mat, cosi = t, M_CANOPY, 0.6

    t_lim = min(best, _box_exit(ox, oy, dx, dy, P), max_range)
    tg, mg, sg, cg = _march(ox, oy, oz, dx, dy, dz, t_lim, P)
    if tg < t_lim:
        return tg, mg, sg, cg
    if best <= max_range:
        return best, mat, 0, cosi
    return np.inf, M_NONE, 0, 0.0


@nb.njit(parallel=True, nogil=True, cache=True)
def _cast(O, step, n_az, el0, n_el, P, J, max_range, seed, t_out, m_out, s_out, c_out):
    for ci in nb.prange(n_az):
        az = ci * step
        ca = np.cos(az)
        sa = np.sin(az)
        for ri in range(n_el):
            el = el0 + ri * step
            ce = np.cos(el)
            k = ci * n_el + ri
            t, m, s, c = _trace(O[0], O[1], O[2], ce * ca, ce * sa, np.sin(el), P, J, max_range, k, seed)
            t_out[k] = t
            m_out[k] = m
            s_out[k] = s
            c_out[k] = c


def _object_vector(p: StreetParams, P: np.ndarray) -> np.ndarray:
    J = np.zeros(N_J)
    tx, ty = p.tree_xy
    gt = _ground(tx, ty, P)[0]
    J[J_TRUNK_X], J[J_TRUNK_Y], J[J_TRUNK_R] = tx, ty, p.trunk_radius
    J[J_TRUNK_Z0], J[J_TRUNK_Z1] = gt - 0.2, gt + p.trunk_height
    J[J_CAN_X], J[J_CAN_Y], J[J_CAN_Z] = tx, ty, gt + p.canopy_height
    J[J_CAN_RX], J[J_CAN_RY], J[J_CAN_RZ] = p.canopy_radii
    J[J_CAN_FP] = p.canopy_free_path
    lx, ly = p.lamp_xy
    gl = _ground(lx, ly, P)[0]
    J[J_POLE_X], J[J_POLE_Y], J[J_POLE_R] = lx, ly, p.lamp_radius
    J[J_POLE_Z0], J[J_POLE_Z1] = gl - 0.2, gl + p.lamp_height
    toward = -np.sign(ly) if ly != 0 else 1.0  # the arm reaches over the road
    J[J_ARM_AX], J[J_ARM_AY], J[J_ARM_AZ] = lx, ly, gl + p.lamp_height - 0.3
    J[J_ARM_BX], J[J_ARM_BY], J[J_ARM_BZ] = lx, ly + toward * p.lamp_arm, gl + p.lamp_height
    J[J_ARM_R] = 0.05
    hy = ly + toward * (p.lamp_arm + 0.1)
    J[J_HEAD_X0], J[J_HEAD_Y0], J[J_HEAD_Z0] = lx - 0.3, hy - 0.15, gl + p.lamp_height - 0.12
    J[J_HEAD_X1], J[J_HEAD_Y1], J[J_HEAD_Z1] = lx + 0.3, hy + 0.15, gl + p.lamp_height
    return J


def scanner_origin(p: StreetParams) -> np.ndarray:
    P = _param_vector(p)
    sx, sy = p.scanner_xy
    return np.array([sx, sy, _ground(sx, sy, P)[0] + p.scanner_height])


def ground_height(p: StreetParams, x, y) -> tuple[np.ndarray, np.ndarray]:
    """Ground truth surface height and class name per (x, y) (for tests), vectorised over numpy arrays."""
    P = _param_vector(p)
    x = np.asarray(x, dtype=np.float64).ravel()
    y = np.asarray(y, dtype=np.float64).ravel()
    z = np.empty(x.size)
    m = np.empty(x.size, np.int64)
    for i in range(x.size):
        z[i], m[i], _ = _ground(x[i], y[i], P)
    return z, np.array([MATERIALS[k][0] for k in m])


def _run(p: StreetParams, step_rad: float):
    P = _param_vector(p)
    J = _object_vector(p, P)
    O = scanner_origin(p)
    n_az = int(round(2 * np.pi / step_rad))
    step = 2 * np.pi / n_az
    el0 = np.deg2rad(p.min_elevation_deg)
    n_el = int(np.floor((np.deg2rad(p.max_elevation_deg) - el0) / step)) + 1
    n = n_az * n_el
    t = np.empty(n, np.float64)
    m = np.empty(n, np.uint8)
    s = np.empty(n, np.int16)
    c = np.empty(n, np.float32)
    _cast(O, step, n_az, el0, n_el, P, J, p.max_range, p.seed, t, m, s, c)
    return O, step, n_az, el0, n_el, t, m, s, c


def make_street(params: StreetParams | None = None, taxonomy: Taxonomy | None = None, **overrides) -> SynthScan:
    """Simulate one TLS setup on a synthetic street. See the module docstring for the layout."""
    p = replace(params or StreetParams(), **overrides)
    tax = taxonomy or load_taxonomy()

    step = np.deg2rad(p.angular_step_deg) if p.angular_step_deg else None
    if step is None:
        # pilot run at a coarse step, then scale: hits grow as 1 / step^2
        pilot = 2.0 * np.sqrt(6.5 / p.n_target)
        t = _run(p, pilot)[5]
        hits = max(int(np.isfinite(t).sum()), 1)
        step = pilot * np.sqrt(hits / p.n_target)
    O, step, n_az, el0, n_el, t, m, s, c = _run(p, step)

    hit = np.flatnonzero(np.isfinite(t))
    col = (hit // n_el).astype(np.uint16)
    row = (hit % n_el).astype(np.uint16)
    az = col * step
    el = el0 + row * step
    rng = np.random.default_rng(p.seed)
    r = t[hit] + rng.normal(0.0, p.range_noise, hit.size)
    ce = np.cos(el)
    xyz = np.empty((hit.size, 3), np.float64)
    xyz[:, 0] = O[0] + r * ce * np.cos(az)
    xyz[:, 1] = O[1] + r * ce * np.sin(az)
    xyz[:, 2] = O[2] + r * np.sin(el)

    mat = m[hit]
    lut_cls = np.zeros(256, np.uint8)
    lut_flag = np.zeros(256, np.uint8)
    lut_rgb = np.zeros((256, 3), np.float32)
    lut_ref = np.zeros(256, np.float32)
    for code, (name, flag, rgb, refl) in MATERIALS.items():
        lut_cls[code] = tax.class_id(name)
        lut_flag[code] = tax.flag_mask(flag) if flag else 0
        lut_rgb[code] = rgb
        lut_ref[code] = refl
    cls = lut_cls[mat]
    flags = lut_flag[mat]

    instance = np.zeros(hit.size, np.uint32)
    instance[(mat == M_TRUNK) | (mat == M_CANOPY)] = 1
    instance[mat == M_LAMP] = 2
    dr = mat == M_DRAIN
    instance[dr] = 100 + s[hit][dr].astype(np.uint32)

    cosi = np.clip(c[hit], 0.0, 1.0)
    inten = lut_ref[mat] * (0.4 + 0.6 * cosi) * (1.0 - 0.004 * r) + rng.normal(0.0, 0.015, hit.size)
    intensity = (np.clip(inten, 0.0, 1.0) * 65535 + 0.5).astype(np.uint16)
    rgb = lut_rgb[mat] * (0.85 + 0.15 * cosi)[:, None] + rng.normal(0.0, 6.0, (hit.size, 3))
    rgb = np.clip(rgb + 0.5, 0, 255).astype(np.uint8)

    return SynthScan(
        xyz=xyz.astype(np.float32),
        offset=np.zeros(3),
        rgb=rgb,
        intensity=intensity,
        cls=cls,
        flags=flags,
        instance=instance,
        row=row,
        col=col,
        origin=O,
        params=p,
        taxonomy_sha256=tax.sha256,
        meta={"angular_step_deg": float(np.rad2deg(step)), "n_rays": int(t.size), "n_az": n_az, "n_el": n_el},
    )


def tiled_copy(scan: SynthScan, copies: int, gap: float = 2.0) -> SynthScan:
    """Repeat a scan on an x/y grid (about square overall) to reach benchmark sizes.

    Copy k is the original shifted in x and y. Instance ids stay per copy (not unique); row/col are kept.
    """
    p = scan.params
    w = p.length + gap
    h = 2 * (p.road_half_width + p.curb_width + p.pavement_width + p.verge_width) + gap
    nx = int(np.ceil(np.sqrt(copies * h / w)))
    n = len(scan)
    xyz = np.empty((n * copies, 3), np.float32)
    for k in range(copies):
        i, j = k % nx, k // nx
        sl = slice(k * n, (k + 1) * n)
        xyz[sl] = scan.xyz
        xyz[sl, 0] += np.float32(i * w)
        xyz[sl, 1] += np.float32(j * h)
    rep = lambda a: np.tile(a, (copies,) + (1,) * (a.ndim - 1))  # noqa: E731
    return SynthScan(
        xyz=xyz,
        offset=scan.offset.copy(),
        rgb=rep(scan.rgb),
        intensity=rep(scan.intensity),
        cls=rep(scan.cls),
        flags=rep(scan.flags),
        instance=rep(scan.instance),
        row=rep(scan.row),
        col=rep(scan.col),
        origin=scan.origin.copy(),
        params=p,
        taxonomy_sha256=scan.taxonomy_sha256,
        meta={**scan.meta, "copies": copies, "grid_nx": nx},
    )


def write_street_e57(scan: SynthScan, path: str | Path, *, invalid_fraction: float = 0.0, seed: int = 0) -> Path:
    """Write the scan as an E57 (scaled-integer xyz, like the real scans) plus ``<path>.gt.npz``.

    ``invalid_fraction`` inserts that share of extra records with ``cartesianInvalidState = 2`` at random
    positions; the ground-truth file holds the valid points in source order plus the raw ``invalid`` mask.
    """
    from pclabel.io.e57 import write_e57

    path = Path(path)
    n = len(scan)
    n_bad = int(round(n * invalid_fraction))
    rng = np.random.default_rng(seed)
    invalid = np.zeros(n + n_bad, np.int8)
    invalid[rng.choice(n + n_bad, n_bad, replace=False)] = 2
    good = invalid == 0
    xyz = np.zeros((n + n_bad, 3))
    xyz[good] = scan.xyz.astype(np.float64) + scan.offset
    xyz[~good] = rng.normal(0, 1e3, (n_bad, 3))  # garbage that must be dropped
    rgb = np.zeros((n + n_bad, 3), np.uint8)
    rgb[good] = scan.rgb
    inten = np.zeros(n + n_bad, np.float32)
    inten[good] = scan.intensity / 65535.0
    row = np.zeros(n + n_bad, np.uint16)
    col = np.zeros(n + n_bad, np.uint16)
    row[good] = scan.row
    col[good] = scan.col
    s = {"xyz": xyz, "rgb": rgb, "intensity": inten, "row": row, "col": col, "name": "synthetic street"}
    if n_bad:
        s["invalid"] = invalid
    write_e57(path, [s])
    np.savez_compressed(
        path.with_name(path.name + ".gt.npz"),
        cls=scan.cls,
        flags=scan.flags,
        instance=scan.instance,
        invalid=invalid,
        origin=scan.origin,
        params=np.array(repr(asdict(scan.params))),
        taxonomy_sha256=np.array(scan.taxonomy_sha256),
    )
    return path
