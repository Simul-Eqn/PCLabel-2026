"""Import a scan into a project directory (ARCHITECTURE.md "import" and "project directory").

1. Pass A streams the valid points in source order into temporary columns (memmaps), with bounded memory.
2. Tiling (store/tiles.py) runs on those columns and gives ``order`` (store position -> source index).
3. Pass B gathers each column into store order, block by block.

Everything is written into a hidden temporary directory that is renamed to the project name only when complete,
so a cancelled or failed import leaves nothing behind. The source is opened read-only (libe57 "r" mode and a
plain binary read for the sha256, which runs in a background thread).
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import shutil
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numba as nb
import numpy as np

from pclabel import __version__
from pclabel.io.e57 import default_offset, iter_chunks, read_info
from pclabel.store.tiles import TilingParams, build_tiles
from pclabel.sysinfo import memory_gb

SCHEMA = "pclabel.project/1"
SCAN_METADATA_FIELDS = ("scanner", "capture", "datetime", "weather", "surface_wetness", "settings", "notes")
# share of the whole import, for one progress bar
STAGE_WEIGHTS = {"read": 0.60, "tile": 0.08, "reorder": 0.27, "finish": 0.05}


class ImportCancelled(Exception):
    pass


@dataclass(frozen=True)
class ImportParams:
    chunk_size: int = 4_000_000
    tiling: TilingParams = field(default_factory=TilingParams)
    gather_block: int = 16_000_000


def project_name_for(source: str | Path) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(source).stem).strip(" .")
    return name or "scan"


def _index_dtype(n: int):
    return np.uint32 if n < 2**32 else np.uint64


def _dtype_suffix(dt) -> str:
    return {np.dtype(np.float32): "f32", np.dtype(np.uint8): "u8", np.dtype(np.uint16): "u16",
            np.dtype(np.uint32): "u32", np.dtype(np.uint64): "u64"}[np.dtype(dt)]  # fmt: skip


@nb.njit(parallel=True, nogil=True, cache=True)
def _gather2(src, order, a, b, dst):
    k = src.shape[1]
    for i in nb.prange(a, b):
        j = order[i]
        for d in range(k):
            dst[i, d] = src[j, d]


@nb.njit(parallel=True, nogil=True, cache=True)
def _gather1(src, order, a, b, dst):
    for i in nb.prange(a, b):
        dst[i] = src[order[i]]


class _Hasher(threading.Thread):
    def __init__(self, path: Path, stop: threading.Event):
        super().__init__(daemon=True, name="sha256")
        self.path, self.stop, self.size = path, stop, path.stat().st_size
        self.done = 0
        self.digest: str | None = None
        self.error: BaseException | None = None

    def run(self):
        try:
            h = hashlib.sha256()
            with open(self.path, "rb") as f:  # read-only
                while not self.stop.is_set():
                    block = f.read(16 * 1024 * 1024)
                    if not block:
                        self.digest = h.hexdigest()
                        return
                    h.update(block)
                    self.done += len(block)
        except BaseException as e:  # reported by the importer
            self.error = e


class _Job:
    def __init__(self, progress, cancel):
        self.progress = progress or (lambda stage, frac: None)
        self.cancel = cancel or threading.Event()
        self.base = 0.0
        self.stage = None
        self.stage_seconds = {}
        self._t = None

    def enter(self, stage):
        now = time.perf_counter()
        if self.stage is not None:
            self.stage_seconds[self.stage] = now - self._t
            self.base += STAGE_WEIGHTS[self.stage]
        self.stage, self._t = stage, now
        self.report(0.0)

    def report(self, frac):
        if self.cancel.is_set():
            raise ImportCancelled()
        self.progress(self.stage, min(1.0, self.base + STAGE_WEIGHTS[self.stage] * min(max(frac, 0.0), 1.0)))

    def close(self):
        self.enter_end = time.perf_counter()
        if self.stage is not None:
            self.stage_seconds[self.stage] = self.enter_end - self._t


def import_scan(
    source: str | Path,
    *,
    name: str | None = None,
    projects_dir: str | Path | None = None,
    params: ImportParams = ImportParams(),
    progress: Callable[[str, float], None] | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Import ``source`` (E57) as project ``name`` under ``projects_dir``; returns the project directory.

    ``progress(stage, fraction)`` is called often with the overall fraction (0..1); setting ``cancel`` aborts
    with :class:`ImportCancelled`. Never overwrites: an existing project of that name raises FileExistsError.
    """
    from pclabel.settings import load_settings

    src = Path(source).resolve()
    if not src.is_file():
        raise FileNotFoundError(src)
    if src.suffix.lower() != ".e57":
        raise ValueError(f"only E57 is supported so far: {src.name}")
    projects_dir = Path(projects_dir) if projects_dir else Path(load_settings().projects_dir)
    projects_dir.mkdir(parents=True, exist_ok=True)
    name = name or project_name_for(src)
    if name != project_name_for(name + ".x"):
        raise ValueError(f"invalid project name {name!r}")
    final = projects_dir / name
    if final.exists():
        raise FileExistsError(f"project already exists: {final}")
    tmp = projects_dir / f".importing-{name}-{uuid.uuid4().hex[:8]}"
    tmp.mkdir()
    job = _Job(progress, cancel)
    stop_hash = threading.Event()
    hasher = _Hasher(src, stop_hash)
    try:
        hasher.start()
        _import_into(src, tmp, name, params, job, hasher)
        os.rename(tmp, final)
        return final
    except BaseException as e:
        stop_hash.set()
        traceback.clear_frames(e.__traceback__)  # drop frame locals that still hold memmaps (Windows file locks)
        gc.collect()
        _rmtree(tmp)
        raise
    finally:
        stop_hash.set()


def _rmtree(path: Path):
    for attempt in range(20):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            gc.collect()
            time.sleep(0.1 * (attempt + 1))
    shutil.rmtree(path)


def _import_into(src: Path, out: Path, name: str, params: ImportParams, job: _Job, hasher: _Hasher):
    t_start = time.perf_counter()
    st0 = src.stat()
    infos = read_info(src)
    n_raw = sum(i.n_raw for i in infos)
    if n_raw == 0:
        raise ValueError(f"{src.name} has no points")
    raw_start = np.cumsum([0] + [i.n_raw for i in infos])[:-1]
    has_rgb = any(i.has_color for i in infos)
    has_int = any(i.has_intensity for i in infos)

    offset = default_offset(infos)
    if offset is None:  # no header bounds: from the first chunk
        first = next(iter_chunks(src, offset=np.zeros(3), chunk_size=min(params.chunk_size, 1_000_000)), None)
        offset = np.round(first.xyz.astype(np.float64).mean(0)) if first is not None and len(first.xyz) else np.zeros(3)

    # ---- pass A: source order -----------------------------------------------------------------------------
    job.enter("read")
    tmpdir = out / "_source_order"
    tmpdir.mkdir()
    idt_raw = _index_dtype(n_raw)
    s_xyz = np.memmap(tmpdir / "xyz.f32", np.float32, "w+", shape=(n_raw, 3))
    s_rgb = np.memmap(tmpdir / "rgb.u8", np.uint8, "w+", shape=(n_raw, 3)) if has_rgb else None
    s_int = np.memmap(tmpdir / "intensity.u16", np.uint16, "w+", shape=(n_raw,)) if has_int else None
    s_raw = np.memmap(tmpdir / "raw_index", idt_raw, "w+", shape=(n_raw,))
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    n_valid_scan = np.zeros(len(infos), np.int64)
    k = 0
    seen = 0
    for c in iter_chunks(src, offset=offset, chunk_size=params.chunk_size):
        m = len(c.xyz)
        if m:
            s_xyz[k : k + m] = c.xyz
            if s_rgb is not None:
                s_rgb[k : k + m] = c.rgb if c.rgb is not None else 128
            if s_int is not None:
                s_int[k : k + m] = c.intensity if c.intensity is not None else 0
            s_raw[k : k + m] = c.raw_index + raw_start[c.scan]
            lo = np.minimum(lo, c.xyz.min(0))
            hi = np.maximum(hi, c.xyz.max(0))
        n_valid_scan[c.scan] += m
        k += m
        seen += c.n_raw
        job.report(seen / n_raw)
    n = k
    if n == 0:
        raise ValueError(f"{src.name} has no valid points")

    # ---- tiling -------------------------------------------------------------------------------------------
    job.enter("tile")
    tiles, order = build_tiles(s_xyz[:n], params.tiling)
    job.report(1.0)

    # ---- pass B: store order ------------------------------------------------------------------------------
    job.enter("reorder")
    (out / "points").mkdir()
    columns = {}
    work = [("xyz", s_xyz, (n, 3))]
    if s_rgb is not None:
        work.append(("rgb", s_rgb, (n, 3)))
    if s_int is not None:
        work.append(("intensity", s_int, (n,)))
    total = n * len(work)
    done = 0
    for key, srcmap, shape in work:
        fname = f"points/{key}.{_dtype_suffix(srcmap.dtype)}"
        dst = np.memmap(out / fname, srcmap.dtype, "w+", shape=shape)
        s_arr, d_arr = np.asarray(srcmap), np.asarray(dst)
        for a in range(0, n, params.gather_block):
            b = min(n, a + params.gather_block)
            (_gather2 if len(shape) == 2 else _gather1)(s_arr, order, a, b, d_arr)
            done += b - a
            job.report(done / total)
        dst.flush()
        del dst, d_arr, s_arr
        columns[key] = {"file": fname, "dtype": np.dtype(srcmap.dtype).str, "shape": list(shape), "order": "store"}

    sfile = f"points/source_index.{_dtype_suffix(order.dtype)}"
    order.tofile(out / sfile)
    columns["source_index"] = {"file": sfile, "dtype": order.dtype.str, "shape": [n], "order": "store",
                               "maps": "store position -> source index"}  # fmt: skip
    if n != n_raw:
        rfile = f"points/raw_index.{_dtype_suffix(idt_raw)}"
        np.asarray(s_raw[:n]).tofile(out / rfile)
        columns["raw_index"] = {"file": rfile, "dtype": np.dtype(idt_raw).str, "shape": [n], "order": "source",
                                "maps": "source index -> raw record index (all scans, file order)"}  # fmt: skip
    del order
    np.save(out / "tiles.npy", tiles)
    (out / "labels").mkdir()
    for key in ("labels", "flags"):
        f = f"labels/{key}.u8"
        with open(out / f, "wb") as fh:
            fh.truncate(n)  # zeros: 0 = unassessed, no flags
        columns[key] = {"file": f, "dtype": "|u1", "shape": [n], "order": "store"}

    # drop the source-order temporaries (close every map first: Windows keeps mapped files locked)
    del s_xyz, s_rgb, s_int, s_raw, work, srcmap
    gc.collect()
    _rmtree(tmpdir)

    # ---- finish: hash, check the source did not change, write project.json --------------------------------
    job.enter("finish")
    while hasher.is_alive():
        hasher.join(0.1)
        job.report(hasher.done / max(hasher.size, 1))
    if hasher.error is not None:
        raise hasher.error
    job.report(1.0)  # last chance to cancel before the project is written
    st1 = src.stat()
    if (st1.st_size, st1.st_mtime_ns) != (st0.st_size, st0.st_mtime_ns):
        raise RuntimeError(f"{src} changed during the import")
    job.close()

    scans = []
    for i, info in enumerate(infos):
        scans.append({
            "index": info.index, "name": info.name, "coords": info.coords,
            "n_raw": info.n_raw, "n_valid": int(n_valid_scan[i]), "raw_start": int(raw_start[i]),
            "rotation": info.rotation.tolist(), "translation": info.translation.tolist(),
            "origin": (info.translation - offset).tolist(),
            "fields": list(info.fields), "intensity_range": info.intensity_range, "color_max": info.color_max,
        })  # fmt: skip
    meta = {
        "schema": SCHEMA,
        "name": name,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app": {"name": "pclabel", "version": __version__},
        "source": {
            "path": str(src), "format": "e57", "size": st0.st_size, "mtime_ns": st0.st_mtime_ns,
            "mtime": datetime.fromtimestamp(st0.st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "sha256": hasher.digest,
        },  # fmt: skip
        "n_raw": int(n_raw),
        "n_valid": int(n),
        "offset": offset.tolist(),
        "bounds": {"min": lo.tolist(), "max": hi.tolist(), "frame": "relative to offset"},
        "attributes": [a for a, ok in (("rgb", has_rgb), ("intensity", has_int)) if ok],
        "scans": scans,
        "columns": columns,
        "tiles": {"file": "tiles.npy", "count": int(len(tiles)), **asdict(params.tiling)},
        "import": {
            "params": asdict(params),
            "seconds": time.perf_counter() - t_start,
            "stage_seconds": job.stage_seconds,
            **{k: round(v, 3) for k, v in memory_gb().items() if k.startswith("peak")},
        },
        "scan_metadata": {k: ("static" if k == "capture" else "") for k in SCAN_METADATA_FIELDS},
    }
    write_json_atomic(out / "project.json", meta)


def write_json_atomic(path: Path, obj):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    os.replace(tmp, path)
