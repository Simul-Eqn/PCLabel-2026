"""Small file helpers for the store (no Qt, no GL)."""

from __future__ import annotations

import json
import os
from pathlib import Path


def write_json_atomic(path: Path, obj):
    """Write ``obj`` as JSON (utf-8) through a temporary file and a rename, so a failed write never leaves a
    half-written or empty file in place."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    os.replace(tmp, path)
