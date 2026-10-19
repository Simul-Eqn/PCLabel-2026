"""Command line: ``python -m pclabel [app|import|info] ...``."""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path


def cmd_import(a) -> int:
    from pclabel.store.importer import ImportCancelled, ImportParams, import_scan

    t0 = time.perf_counter()
    last = [0.0, ""]

    def progress(stage, frac):
        now = time.perf_counter()
        if stage != last[1] or now - last[0] > 2.0:
            print(f"  {stage:8s} {frac:6.1%}  {now - t0:7.1f} s", flush=True)
            last[0], last[1] = now, stage

    cancel = threading.Event()
    try:
        path = import_scan(a.file, name=a.name, projects_dir=a.projects_dir,
                           params=ImportParams(chunk_size=a.chunk_size), progress=progress, cancel=cancel)  # fmt: skip
    except KeyboardInterrupt:
        cancel.set()
        print("cancelled", file=sys.stderr)
        return 1
    except ImportCancelled:
        print("cancelled", file=sys.stderr)
        return 1
    print(f"imported -> {path}")
    return cmd_info(argparse.Namespace(project=str(path)))


def cmd_info(a) -> int:
    from pclabel.store.project import Project

    p = Project(a.project)
    m = p.meta
    imp = m.get("import", {})
    print(f"project   {p.path}")
    print(f"source    {m['source']['path']}  ({m['source']['size'] / 1e9:.2f} GB, sha256 {m['source']['sha256']})")
    print(f"points    {p.n:,} valid of {m['n_raw']:,} records, {len(m['scans'])} scan(s), attributes {m['attributes']}")
    print(f"offset    {p.offset.tolist()}  bounds {p.bounds[0].round(3).tolist()} .. {p.bounds[1].round(3).tolist()}")
    print(f"tiles     {len(p.tiles)} (max {int(p.tiles['count'].max()):,} points, T = {m['tiles']['max_points']:,})")
    if imp:
        stages = ", ".join(f"{k} {v:.1f} s" for k, v in imp.get("stage_seconds", {}).items())
        print(f"import    {imp['seconds']:.1f} s ({stages}); peak working set {imp.get('peak_working_set_gb', 0):.2f} GB, "
              f"peak commit {imp.get('peak_commit_gb', 0):.2f} GB")  # fmt: skip
    return 0


def cmd_app(a) -> int:
    from pclabel.ui.app import run

    return run(project=a.project, gl=a.gl, log_frames=a.log_frames)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pclabel")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("app", help="open the labelling app (default)")
    p.add_argument("project", nargs="?")
    p.add_argument("--gl", choices=["hardware", "software"], help="override settings: GPU driver or Mesa llvmpipe")
    p.add_argument("--log-frames", metavar="CSV", help="append per-frame times to a CSV file")
    p = sub.add_parser("import", help="import a scan into a new project")
    p.add_argument("file")
    p.add_argument("--name")
    p.add_argument("--projects-dir")
    p.add_argument("--chunk-size", type=int, default=4_000_000)
    p = sub.add_parser("info", help="summary of a project")
    p.add_argument("project")
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("app", "import", "info", "-h", "--help"):
        argv = ["app"] + argv
    a = ap.parse_args(argv)
    return {"app": cmd_app, "import": cmd_import, "info": cmd_info}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
