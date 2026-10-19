"""An imported project: memory-mapped columns in store order, the tile table and project.json."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from pclabel.store.importer import SCAN_METADATA_FIELDS, SCHEMA, write_json_atomic

FRAMES_SCHEMA = "pclabel.frames/1"


class Project:
    """Open a project directory. Point columns are read-only memmaps (labels change only via the journal, M3)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.meta = json.loads((self.path / "project.json").read_text(encoding="utf-8"))
        if self.meta.get("schema") != SCHEMA:
            raise ValueError(f"{self.path}: unsupported project schema {self.meta.get('schema')!r}")
        self.n = int(self.meta["n_valid"])
        self.offset = np.asarray(self.meta["offset"], dtype=np.float64)
        self.tiles = np.load(self.path / self.meta["tiles"]["file"])
        self.xyz = self._map("xyz")
        self.rgb = self._map("rgb")
        self.intensity = self._map("intensity")
        self.source_index = self._map("source_index")
        self.labels = self._map("labels")
        self.flags = self._map("flags")

    def _map(self, key: str, mode: str = "r"):
        col = self.meta["columns"].get(key)
        if col is None:
            return None
        return np.memmap(self.path / col["file"], np.dtype(col["dtype"]), mode, shape=tuple(col["shape"]))

    @property
    def name(self) -> str:
        return self.meta["name"]

    @property
    def bounds(self) -> np.ndarray:
        """(2, 3) bounds relative to the offset."""
        b = self.meta["bounds"]
        return np.array([b["min"], b["max"]])

    @property
    def scan_origins(self) -> np.ndarray:
        """Scanner positions relative to the offset (from each scan's pose), (n_scans, 3)."""
        return np.array([s["origin"] for s in self.meta["scans"]])

    @property
    def static_capture(self) -> bool:
        return self.meta["scan_metadata"].get("capture", "static") == "static"

    def raw_index(self) -> np.ndarray:
        """Raw record index (all scans, file order) per source index. Identity when no record was dropped."""
        col = self.meta["columns"].get("raw_index")
        if col is None:
            return np.arange(self.n, dtype=np.uint64)
        return self._map("raw_index")

    def to_source_order(self, store_values: np.ndarray) -> np.ndarray:
        """Per-point values in store order -> the source file's valid-point order (the export order)."""
        out = np.empty((self.n,) + store_values.shape[1:], store_values.dtype)
        out[self.source_index] = store_values
        return out

    def tile_range(self, t: int) -> tuple[int, int]:
        s = int(self.tiles["start"][t])
        return s, s + int(self.tiles["count"][t])

    def set_scan_metadata(self, **fields):
        unknown = set(fields) - set(SCAN_METADATA_FIELDS)
        if unknown:
            raise KeyError(f"unknown scan metadata fields: {sorted(unknown)}")
        self.meta["scan_metadata"].update({k: str(v) for k, v in fields.items()})
        write_json_atomic(self.path / "project.json", self.meta)

    def load_frames(self) -> tuple[list[dict], str | None]:
        """Saved height frames (``frames.json``, schema pclabel.frames/1) and the active one's name."""
        path = self.path / "frames.json"
        if not path.exists():
            return [], None
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("schema") != FRAMES_SCHEMA:
            raise ValueError(f"{path}: unsupported schema {doc.get('schema')!r}")
        return list(doc.get("frames", [])), doc.get("active")

    def save_frames(self, frames: list[dict], active: str | None):
        write_json_atomic(self.path / "frames.json", {"schema": FRAMES_SCHEMA, "active": active, "frames": frames})

    def close(self):
        for key in ("xyz", "rgb", "intensity", "source_index", "labels", "flags"):
            setattr(self, key, None)
