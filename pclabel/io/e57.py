"""E57 reading in fixed-size chunks (bounded memory), plus a small writer for synthetic scans.

Source files are opened with ``libe57.ImageFile(path, "r")`` only. ``pye57.E57`` is deliberately not used for
reading: its constructor has an error path that deletes the file it was given.

Order and validity follow ARCHITECTURE.md "source order": scans in file order, records in stored order, records
with a non-zero invalid state or non-finite coordinates dropped. Each scan's pose is applied.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numba as nb
import numpy as np
from pye57 import libe57
from pye57.libe57 import NodeType

_CAST = {
    NodeType.E57_BLOB: libe57.BlobNode,
    NodeType.E57_COMPRESSED_VECTOR: libe57.CompressedVectorNode,
    NodeType.E57_FLOAT: libe57.FloatNode,
    NodeType.E57_INTEGER: libe57.IntegerNode,
    NodeType.E57_SCALED_INTEGER: libe57.ScaledIntegerNode,
    NodeType.E57_STRING: libe57.StringNode,
    NodeType.E57_STRUCTURE: libe57.StructureNode,
    NodeType.E57_VECTOR: libe57.VectorNode,
}

CARTESIAN = ("cartesianX", "cartesianY", "cartesianZ")
SPHERICAL = ("sphericalRange", "sphericalAzimuth", "sphericalElevation")
COLORS = ("colorRed", "colorGreen", "colorBlue")


def _node(n):
    return _CAST[n.type()](n)


def _child(node, name):
    return _node(node.get(name))


def _numeric_range(node) -> tuple[float, float] | None:
    if isinstance(node, libe57.ScaledIntegerNode):
        return float(node.scaledMinimum()), float(node.scaledMaximum())
    if isinstance(node, (libe57.FloatNode, libe57.IntegerNode)):
        return float(node.minimum()), float(node.maximum())
    return None


def quaternion_to_matrix(q) -> np.ndarray:
    """Rotation matrix from an E57 quaternion (w, x, y, z)."""
    w, x, y, z = np.asarray(q, dtype=np.float64) / np.linalg.norm(q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


@dataclass(frozen=True)
class ScanInfo:
    index: int
    name: str
    n_raw: int
    fields: tuple[str, ...]
    coords: str  # "cartesian" or "spherical"
    rotation: np.ndarray  # (3, 3)
    translation: np.ndarray  # (3,)
    bounds: np.ndarray | None  # (2, 3) cartesianBounds in the scan's own frame, if present
    intensity_range: tuple[float, float] | None
    color_max: int | None

    @property
    def has_color(self) -> bool:
        return all(c in self.fields for c in COLORS)

    @property
    def has_intensity(self) -> bool:
        return "intensity" in self.fields

    @property
    def invalid_field(self) -> str | None:
        f = "cartesianInvalidState" if self.coords == "cartesian" else "sphericalInvalidState"
        return f if f in self.fields else None

    def world_bounds(self) -> np.ndarray | None:
        """Pose-applied bounds (axis-aligned box around the 8 transformed corners)."""
        if self.bounds is None:
            return None
        lo, hi = self.bounds
        corners = np.array([[a, b, c] for a in (lo[0], hi[0]) for b in (lo[1], hi[1]) for c in (lo[2], hi[2])])
        w = corners @ self.rotation.T + self.translation
        return np.stack([w.min(0), w.max(0)])


def _scan_info(index: int, scan) -> ScanInfo:
    points = _child(scan, "points")
    proto = libe57.StructureNode(points.prototype())
    fields = tuple(proto.get(i).elementName() for i in range(proto.childCount()))
    if all(f in fields for f in CARTESIAN):
        coords = "cartesian"
    elif all(f in fields for f in SPHERICAL):
        coords = "spherical"
    else:
        raise ValueError(f"scan {index}: no cartesian or spherical coordinates in {fields}")

    rot, trans = np.eye(3), np.zeros(3)
    if scan.isDefined("pose"):
        pose = _child(scan, "pose")
        if pose.isDefined("rotation"):
            r = _child(pose, "rotation")
            rot = quaternion_to_matrix([_child(r, k).value() for k in ("w", "x", "y", "z")])
        if pose.isDefined("translation"):
            t = _child(pose, "translation")
            trans = np.array([_child(t, k).value() for k in ("x", "y", "z")], dtype=np.float64)

    bounds = None
    if scan.isDefined("cartesianBounds"):
        b = _child(scan, "cartesianBounds")
        bounds = np.array(
            [[_child(b, f"{a}Minimum").value() for a in "xyz"], [_child(b, f"{a}Maximum").value() for a in "xyz"]],
            dtype=np.float64,
        )

    irange = None
    if "intensity" in fields:
        if scan.isDefined("intensityLimits"):
            lim = _child(scan, "intensityLimits")
            irange = (float(_child(lim, "intensityMinimum").value()), float(_child(lim, "intensityMaximum").value()))
        else:
            irange = _numeric_range(_child(proto, "intensity"))
        if irange is not None and not irange[1] > irange[0]:
            irange = None

    cmax = None
    if all(c in fields for c in COLORS):
        cmax = 255
        if scan.isDefined("colorLimits"):
            lim = _child(scan, "colorLimits")
            cmax = int(max(_child(lim, f"{c}Maximum").value() for c in COLORS))
        else:
            r = _numeric_range(_child(proto, "colorRed"))
            if r is not None:
                cmax = int(r[1])

    name = _child(scan, "name").value() if scan.isDefined("name") else f"scan {index}"
    return ScanInfo(index, name, int(points.childCount()), fields, coords, rot, trans, bounds, irange, cmax)


def read_info(path: str | Path) -> list[ScanInfo]:
    imf = libe57.ImageFile(str(path), "r")
    try:
        data3d = libe57.VectorNode(imf.root().get("data3D"))
        return [_scan_info(i, _node(data3d.get(i))) for i in range(data3d.childCount())]
    finally:
        imf.close()


def default_offset(infos: list[ScanInfo]) -> np.ndarray | None:
    """Project offset from the header bounds: centre of all pose-applied bounds, rounded to whole metres."""
    boxes = [b for b in (i.world_bounds() for i in infos) if b is not None]
    if not boxes:
        return None
    lo = np.min([b[0] for b in boxes], axis=0)
    hi = np.max([b[1] for b in boxes], axis=0)
    return np.round((lo + hi) / 2.0)


@dataclass
class Chunk:
    """Valid points of one block of records, in source order."""

    scan: int
    raw_start: int  # index (within the scan) of the first record of the block
    n_raw: int  # records in the block, valid or not
    raw_index: np.ndarray  # int64 (n,), record index within the scan of each valid point
    xyz: np.ndarray  # float32 (n, 3), world position minus the offset
    rgb: np.ndarray | None  # uint8 (n, 3)
    intensity: np.ndarray | None  # uint16 (n,), scaled from the scan's intensity range to 0..65535


@nb.njit(parallel=True, nogil=True, cache=True)
def _valid_mask(a, b, c, inv, n, out):
    has_inv = inv.shape[0] > 0
    for i in nb.prange(n):
        ok = np.isfinite(a[i]) and np.isfinite(b[i]) and np.isfinite(c[i])
        if has_inv and inv[i] != 0:
            ok = False
        out[i] = ok


@nb.njit(parallel=True, nogil=True, cache=True)
def _transform(a, b, c, idx, spherical, R, t, out):
    # world = R @ p + t, where t already has the project offset subtracted
    for j in nb.prange(idx.shape[0]):
        i = idx[j]
        if spherical:
            r, az, el = a[i], b[i], c[i]
            x = r * np.cos(el) * np.cos(az)
            y = r * np.cos(el) * np.sin(az)
            z = r * np.sin(el)
        else:
            x, y, z = a[i], b[i], c[i]
        out[j, 0] = R[0, 0] * x + R[0, 1] * y + R[0, 2] * z + t[0]
        out[j, 1] = R[1, 0] * x + R[1, 1] * y + R[1, 2] * z + t[1]
        out[j, 2] = R[2, 0] * x + R[2, 1] * y + R[2, 2] * z + t[2]


def iter_chunks(
    path: str | Path,
    *,
    offset=None,
    chunk_size: int = 4_000_000,
    scans: list[int] | None = None,
    max_points: int | None = None,
    attributes: bool = True,
) -> Iterator[Chunk]:
    """Yield the valid points of the file block by block with fixed-size read buffers.

    Peak memory is about ``chunk_size * 50`` bytes whatever the file size. ``offset`` defaults to
    :func:`default_offset` (0 if the file has no bounds). ``max_points`` stops after that many raw records
    (counted over all scans). ``attributes=False`` reads coordinates only.
    """
    path = str(path)
    infos = read_info(path)
    if offset is None:
        offset = default_offset(infos)
    offset = np.zeros(3) if offset is None else np.asarray(offset, dtype=np.float64)
    remaining = max_points
    imf = libe57.ImageFile(path, "r")
    try:
        data3d = libe57.VectorNode(imf.root().get("data3D"))
        for info in infos:
            if scans is not None and info.index not in scans:
                continue
            if remaining is not None and remaining <= 0:
                break
            if info.n_raw == 0:
                continue  # libe57 cannot open a reader on a scan without records
            scan = _node(data3d.get(info.index))
            points = _child(scan, "points")
            cap = int(max(1, min(chunk_size, info.n_raw)))
            names = CARTESIAN if info.coords == "cartesian" else SPHERICAL
            bufs = {f: np.empty(cap, np.float64) for f in names}
            if info.invalid_field:
                bufs[info.invalid_field] = np.empty(cap, np.int8)
            if attributes and info.has_color:
                cdt = np.uint8 if (info.color_max or 255) <= 255 else np.uint16
                for f in COLORS:
                    bufs[f] = np.empty(cap, cdt)
            if attributes and info.has_intensity:
                bufs["intensity"] = np.empty(cap, np.float32)
            sdb = libe57.VectorSourceDestBuffer()
            for f, arr in bufs.items():
                sdb.append(libe57.SourceDestBuffer(imf, f, arr, cap, True, True))
            reader = points.reader(sdb)
            t_local = info.translation - offset
            inv = bufs.get(info.invalid_field, np.empty(0, np.int8)) if info.invalid_field else np.empty(0, np.int8)
            ok = np.empty(cap, np.bool_)
            raw_start = 0
            try:
                while True:
                    n = reader.read()
                    if n == 0:
                        break
                    if remaining is not None:
                        n = min(n, remaining)
                    a, b, c = (bufs[f] for f in names)
                    _valid_mask(a, b, c, inv, n, ok)
                    idx = np.flatnonzero(ok[:n])
                    xyz = np.empty((idx.size, 3), np.float32)
                    _transform(a, b, c, idx, info.coords == "spherical", info.rotation, t_local, xyz)
                    rgb = None
                    if "colorRed" in bufs:
                        rgb = np.empty((idx.size, 3), np.uint8)
                        for k, f in enumerate(COLORS):
                            v = bufs[f][idx]
                            if v.dtype != np.uint8:
                                v = (v.astype(np.float32) * (255.0 / info.color_max) + 0.5).astype(np.uint8)
                            rgb[:, k] = v
                    inten = None
                    if "intensity" in bufs:
                        lo, hi = info.intensity_range or (0.0, 1.0)
                        v = (bufs["intensity"][idx] - np.float32(lo)) * np.float32(65535.0 / (hi - lo))
                        inten = (np.clip(v, 0, 65535) + 0.5).astype(np.uint16)
                    yield Chunk(info.index, raw_start, n, idx.astype(np.int64) + raw_start, xyz, rgb, inten)
                    raw_start += n
                    if remaining is not None:
                        remaining -= n
                        if remaining <= 0:
                            break
            finally:
                reader.close()
    finally:
        imf.close()


def write_e57(path: str | Path, scans: list[dict], *, xyz_scale: float | None = 1e-5, chunk_size: int = 1_000_000):
    """Write scans to a new E57 file (for synthetic data and tests; never used on source files).

    Each scan is a dict with either ``xyz`` (n, 3) float64 in the scan's own frame or ``spherical`` (n, 3)
    (range, azimuth, elevation; radians), and optionally:
    - ``rgb`` (n, 3) with ``rgb_max`` (default 255; e.g. 65535 for 16-bit colour);
    - ``intensity`` (n,): float in [0, 1], or integers with ``intensity_max`` (written as an IntegerNode);
    - ``invalid`` (n,) int8 (cartesian/sphericalInvalidState); ``row``/``col`` (n,) integer;
    - ``rotation`` (w, x, y, z), ``translation`` (3,), ``name``.

    ``xyz_scale`` writes cartesian coordinates as 32-bit scaled integers like the real scans (``None``: float64).
    """
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    imf = libe57.ImageFile(str(path), "w")
    try:
        root = imf.root()
        imf.extensionsAdd("", libe57.E57_V1_0_URI)
        root.set("formatName", libe57.StringNode(imf, "ASTM E57 3D Imaging Data File"))
        root.set("guid", libe57.StringNode(imf, "{%s}" % uuid.uuid4()))
        root.set("versionMajor", libe57.IntegerNode(imf, libe57.E57_FORMAT_MAJOR))
        root.set("versionMinor", libe57.IntegerNode(imf, libe57.E57_FORMAT_MINOR))
        root.set("e57LibraryVersion", libe57.StringNode(imf, libe57.E57_LIBRARY_ID))
        root.set("coordinateMetadata", libe57.StringNode(imf, ""))
        data3d = libe57.VectorNode(imf, True)
        root.set("data3D", data3d)
        root.set("images2D", libe57.VectorNode(imf, True))
        for k, s in enumerate(scans):
            _write_scan(imf, data3d, k, s, xyz_scale, chunk_size)
    finally:
        imf.close()


def _write_scan(imf, data3d, k, s, xyz_scale, chunk_size):
    spherical = "spherical" in s
    coords = np.asarray(s["spherical"] if spherical else s["xyz"], dtype=np.float64).reshape(-1, 3)
    n = coords.shape[0]
    if spherical:
        r, az, el = coords.T
        xyz = np.stack([r * np.cos(el) * np.cos(az), r * np.cos(el) * np.sin(az), r * np.sin(el)], 1)
    else:
        xyz = coords
    node = libe57.StructureNode(imf)
    node.set("guid", libe57.StringNode(imf, "{%s}" % uuid.uuid4()))
    node.set("name", libe57.StringNode(imf, s.get("name", f"scan {k}")))
    node.set("description", libe57.StringNode(imf, "pclabel synthetic"))
    if "rotation" in s or "translation" in s:
        pose = libe57.StructureNode(imf)
        rot = libe57.StructureNode(imf)
        for name, v in zip("wxyz", s.get("rotation", (1.0, 0.0, 0.0, 0.0))):
            rot.set(name, libe57.FloatNode(imf, float(v)))
        tr = libe57.StructureNode(imf)
        for name, v in zip("xyz", s.get("translation", (0.0, 0.0, 0.0))):
            tr.set(name, libe57.FloatNode(imf, float(v)))
        pose.set("rotation", rot)
        pose.set("translation", tr)
        node.set("pose", pose)
    valid = np.ones(n, bool) if "invalid" not in s else np.asarray(s["invalid"]) == 0
    if valid.any():
        lo, hi = xyz[valid].min(0), xyz[valid].max(0)
        box = libe57.StructureNode(imf)
        for i, a in enumerate("xyz"):
            box.set(f"{a}Minimum", libe57.FloatNode(imf, float(lo[i])))
            box.set(f"{a}Maximum", libe57.FloatNode(imf, float(hi[i])))
        node.set("cartesianBounds", box)

    proto = libe57.StructureNode(imf)
    arrays = {}
    if spherical:
        for i, f in enumerate(SPHERICAL):
            proto.set(f, libe57.FloatNode(imf, 0.0, libe57.E57_DOUBLE))
            arrays[f] = (coords[:, i], np.float64)
    else:
        for i, f in enumerate(CARTESIAN):
            if xyz_scale is None:
                proto.set(f, libe57.FloatNode(imf, 0.0, libe57.E57_DOUBLE))
            else:
                lim = 2147483647
                proto.set(f, libe57.ScaledIntegerNode(imf, 0, -lim, lim, float(xyz_scale), 0.0))
            arrays[f] = (coords[:, i], np.float64)
    if "intensity" in s:
        lim = libe57.StructureNode(imf)
        if "intensity_max" in s:
            imax = int(s["intensity_max"])
            proto.set("intensity", libe57.IntegerNode(imf, 0, 0, imax))
            lim.set("intensityMinimum", libe57.IntegerNode(imf, 0))
            lim.set("intensityMaximum", libe57.IntegerNode(imf, imax))
            arrays["intensity"] = (np.asarray(s["intensity"], np.int64), np.int32)
        else:
            proto.set("intensity", libe57.FloatNode(imf, 0.0, libe57.E57_SINGLE, 0.0, 1.0))
            lim.set("intensityMinimum", libe57.FloatNode(imf, 0.0))
            lim.set("intensityMaximum", libe57.FloatNode(imf, 1.0))
            arrays["intensity"] = (np.asarray(s["intensity"], np.float32), np.float32)
        node.set("intensityLimits", lim)
    if "rgb" in s:
        cmax = int(s.get("rgb_max", 255))
        cdt = np.uint8 if cmax <= 255 else np.uint16
        rgb = np.asarray(s["rgb"]).astype(cdt)
        lim = libe57.StructureNode(imf)
        for i, f in enumerate(COLORS):
            proto.set(f, libe57.IntegerNode(imf, 0, 0, cmax))
            lim.set(f"{f}Minimum", libe57.IntegerNode(imf, 0))
            lim.set(f"{f}Maximum", libe57.IntegerNode(imf, cmax))
            arrays[f] = (rgb[:, i], cdt)
        node.set("colorLimits", lim)
    if "row" in s:
        for f, key in (("rowIndex", "row"), ("columnIndex", "col")):
            v = np.asarray(s[key], np.int64)
            proto.set(f, libe57.IntegerNode(imf, 0, 0, int(max(v.max(), 1))))
            arrays[f] = (v.astype(np.uint16) if v.max() < 65536 else v.astype(np.uint32), None)
    if "invalid" in s:
        f = "sphericalInvalidState" if spherical else "cartesianInvalidState"
        proto.set(f, libe57.IntegerNode(imf, 0, 0, 2))
        arrays[f] = (np.asarray(s["invalid"], np.int8), np.int8)

    points = libe57.CompressedVectorNode(imf, proto, libe57.VectorNode(imf, True))
    node.set("points", points)
    data3d.append(node)

    cap = max(1, min(chunk_size, n))
    bufs, sdb = {}, libe57.VectorSourceDestBuffer()
    for f, (src, dt) in arrays.items():
        bufs[f] = np.empty(cap, dt or src.dtype)
        sdb.append(libe57.SourceDestBuffer(imf, f, bufs[f], cap, True, True))
    writer = points.writer(sdb)
    try:
        for start in range(0, n, cap):
            m = min(cap, n - start)
            for f, (src, _) in arrays.items():
                bufs[f][:m] = src[start : start + m]
            writer.write(m)
    finally:
        writer.close()
