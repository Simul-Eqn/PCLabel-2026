"""Class and flag ids by name, from config/taxonomy.yaml (the source of truth).

Only id 0 (unassessed) is known to the code; every other id is looked up here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from pclabel import CONFIG_DIR

UNASSESSED = 0
DEFAULT_PATH = CONFIG_DIR / "taxonomy.yaml"


@dataclass(frozen=True)
class Taxonomy:
    path: Path
    sha256: str
    version: str
    classes: tuple[dict, ...]
    flags: tuple[dict, ...]

    def class_id(self, name: str) -> int:
        for c in self.classes:
            if c["name"] == name:
                return int(c["id"])
        raise KeyError(f"class {name!r} not in {self.path}")

    def flag_bit(self, name: str) -> int:
        for f in self.flags:
            if f["name"] == name:
                return int(f["bit"])
        raise KeyError(f"flag {name!r} not in {self.path}")

    def flag_mask(self, name: str) -> int:
        return 1 << self.flag_bit(name)

    def color_table(self) -> "list[tuple[int, int, int]]":
        """256 RGB entries indexed by class id (grey for unused ids)."""
        table = [(128, 128, 128)] * 256
        for c in self.classes:
            h = c["color"].lstrip("#")
            table[int(c["id"])] = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        return table


def load_taxonomy(path: str | Path | None = None) -> Taxonomy:
    path = Path(path) if path is not None else DEFAULT_PATH
    raw = path.read_bytes()
    doc = yaml.safe_load(raw.decode("utf-8"))
    classes = tuple(doc["classes"])
    ids = [int(c["id"]) for c in classes]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate class ids in {path}")
    if not all(0 <= i <= 255 for i in ids):
        raise ValueError(f"class ids must fit uint8 in {path}")
    if UNASSESSED not in ids:
        raise ValueError(f"{path} must define class 0 (unassessed)")
    return Taxonomy(
        path=path,
        sha256=hashlib.sha256(raw).hexdigest(),
        version=str(doc.get("version", "")),
        classes=classes,
        flags=tuple(doc.get("flags", ())),
    )
