"""Read-only check that the user's projects still load: for each project with annotations, the schema, the items
per kind and per (kind, label), the points in point sets, and a fingerprint of all of it.

    python bench/check_projects.py [--projects DIR] [--save FILE] [--compare FILE]

Nothing is written into the projects (the annotations are loaded, never saved). ``--save`` writes the summary as
JSON elsewhere; ``--compare`` loads such a file and says which projects differ. Run it before and after every
change to ``pclabel/store/``.

The user may be labelling while the code changes, so compare two versions of the code on the same files at the same
time: run this script from a worktree of the last commit (``git worktree add --detach <dir> HEAD``, copy this file
in, ``PYTHONPATH=<dir>``) with ``--save``, then from the working tree with ``--compare``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from pclabel.settings import load_settings
from pclabel.store.annotations import Annotations
from pclabel.store.project import Project


def summary(path: Path) -> dict:
    project = Project(path)
    doc = json.loads((path / "annotations.json").read_text(encoding="utf-8"))
    model, warnings = Annotations.load(project)
    kinds, labelled, points = Counter(), Counter(), 0
    for x, _ in model.walk():
        kinds[x.kind] += 1
        labelled[f"{x.kind}:{x.label}"] += 1
        if x.kind == "points":
            points += int(len(x.indices))
    out = {"schema": doc.get("schema"), "items": sum(v for k, v in kinds.items() if k != "directory"),
           "kinds": dict(sorted(kinds.items())), "labels": dict(sorted(labelled.items())), "points": points,
           "warnings": len(warnings)}  # fmt: skip
    out["fingerprint"] = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()[:16]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", default=load_settings().projects_dir)
    ap.add_argument("--save")
    ap.add_argument("--compare")
    a = ap.parse_args()
    result = {}
    for d in sorted(Path(a.projects).iterdir()):
        if (d / "annotations.json").exists() and (d / "project.json").exists():
            s = result[d.name] = summary(d)
            print(f"{d.name}: {s['schema']}, {s['items']} items {s['kinds']}, {s['points']:,} points in sets, "
                  f"{s['warnings']} warnings, fingerprint {s['fingerprint']}")  # fmt: skip
    if a.save:
        Path(a.save).write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
    if a.compare:
        before = json.loads(Path(a.compare).read_text(encoding="utf-8"))
        bad = [k for k in sorted(set(before) | set(result)) if before.get(k) != result.get(k)]
        print("same as before" if not bad else f"DIFFERENT: {', '.join(bad)}")
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
