"""Curb and marking tracer results as annotations, and the trace log (no Qt, no GL).

A *run* is one tracer run as the app keeps it: {"mode": "curb" | "marking", "result": the tracer's ``run_trace``
result (local coordinates), "start", "toward" (local), "frame" (the height axis's name), "axis" (its normal)}. The
app and the pipeline save runs the same way: a directory of lines, each marked ``assisted`` with the tracer's
parameters, labelled by the taxonomy's ``tools`` section.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from pclabel.analysis import curves
from pclabel.store.annotations import Curve, Polyline

STEMS = {"curb": "curb trace", "marking": "marking trace"}


def log_record(run: dict, offset, res=None, err=None) -> dict:
    """One trace_log.jsonl line: where the run started, its settings, and what it found or why it stopped."""
    off = np.asarray(offset, np.float64)
    rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": run["mode"], "start": (run["start"] + off).tolist(),
           "toward": (run["toward"] + off).tolist(), "height_axis": run["frame"],
           "axis": [float(v) for v in run["axis"]]}  # fmt: skip
    if err is not None:
        rec["error"] = str(err)
        return rec
    rec["params"] = res.get("params")
    rec["stopped"] = res.get("stopped")
    rec["seconds"] = round(float(res.get("seconds", 0.0)), 3)
    if run["mode"] == "curb":
        rec["steps"] = int(len(res["toe"]))
        rec["pieces"] = int(res["piece"].max()) + 1 if len(res["piece"]) else 0
        if len(res["height"]):
            rec["height_median"] = float(np.median(res["height"]))
    else:
        rec["pieces"] = len(res["pieces"])
        rec["threshold"] = res.get("threshold")
    return rec


def append_log(project_path, rec: dict):
    """Append a record to the project's trace_log.jsonl (a failed write is not an error: the log is an aid)."""
    try:
        with open(Path(project_path) / "trace_log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def run_lines(runs: list, offset, taxonomy, toe: bool = True, lip: bool = True, line_label: str | None = None) -> list:
    """The lines a list of runs (all of one mode) saves: [(name, vertices in world coordinates, label, source)].
    Curb runs give a ``lip k`` line per piece and, with ``toe`` (and when the run followed the toe), a ``toe k``
    line; marking runs a ``line k`` or ``dash k`` per piece, labelled ``line_label`` or the taxonomy's default.
    Pieces of fewer than two points are left out."""
    off = np.asarray(offset, np.float64)
    mode = runs[0]["mode"]
    toe_label, lip_label = taxonomy.tool_label("curb_trace_toe"), taxonomy.tool_label("curb_trace_lip")
    label = line_label or taxonomy.tool_label("marking_trace")
    out = []
    for ri, run in enumerate(runs, 1):
        r = run["result"]
        base = {"tool": "curb_trace" if mode == "curb" else "line_trace", "assisted": True,
                "params": dict(r["params"]), "start": (run["start"] + off).tolist(),
                "toward": (run["toward"] + off).tolist(), "height_axis": run["frame"],
                "axis": [float(v) for v in run["axis"]], "stopped": r["stopped"], "run": ri}  # fmt: skip
        if mode == "curb":
            for k in range(int(r["piece"].max()) + 1 if len(r["piece"]) else 0):
                sel = r["piece"] == k
                if sel.sum() < 2:
                    continue
                h = r["height"][sel]
                src = dict(base, piece=k, heights={"median": float(np.median(h)), "min": float(h.min()),
                                                   "max": float(h.max())},
                           face_seen=float(r["seen"][sel].mean()))  # fmt: skip
                tag = f"{ri}.{k + 1}" if len(runs) > 1 else f"{k + 1}"
                if toe and r["params"].get("follow", "toe") == "toe":
                    out.append((f"toe {tag}", r["toe"][sel] + off, toe_label, dict(src, line="toe")))
                if lip:
                    out.append((f"lip {tag}", r["lip"][sel] + off, lip_label, dict(src, line="lip")))
        else:
            for k, pc in enumerate(r["pieces"]):
                if len(pc) < 2:
                    continue
                tag = f"{ri}.{k + 1}" if len(runs) > 1 else f"{k + 1}"
                what = "dash" if r["params"]["kind"] == "dashed" else "line"
                out.append((f"{what} {tag}", np.asarray(pc, np.float64) + off, label,
                            dict(base, piece=k, bridged=list(r["bridged"]))))  # fmt: skip
    return out


def line_item(vertices, closed: bool, source: dict, kind: str, axis, frame: str, origin, tolerance: float):
    """A saved line (world coordinates): a curve with no plane through as few vertices as stay within ``tolerance``
    (``kind`` "curve"; editable), or the polyline ("polyline", or fewer than two vertices)."""
    if kind != "curve" or len(vertices) < 2:
        return Polyline(vertices, closed, source)
    return Curve.from_line(vertices, np.asarray(axis, np.float64), np.asarray(origin, np.float64), frame, tolerance,
                           closed, source)  # fmt: skip


def next_directory_name(model, stem: str) -> str:
    """``<stem> NN``, numbered on from the directories of that name already in the tree."""
    nums = [int(x.name.rsplit(" ", 1)[1]) for x, _ in model.walk()
            if x.kind == "directory" and x.name.startswith(stem + " ") and x.name.rsplit(" ", 1)[1].isdigit()]  # fmt: skip
    return f"{stem} {max(nums, default=0) + 1:02d}"


def add_lines(model, lines: list, stem: str, into_id, make_item):
    """A new directory ``<stem> NN`` (inside ``into_id``, or at the top) holding one item per line;
    ``make_item(vertices, source)`` builds each (``line_item``). Returns the directory."""
    d = model.new_directory(next_directory_name(model, stem), into_id)
    for name, verts, label, src in lines:
        item = model.add(make_item(np.asarray(verts, np.float64), src), name)
        item.label = label
        model.move([item.id], d.id)
    return d


def simplify_polyline(vertices, closed: bool, tolerance: float = 0.002) -> np.ndarray:
    """The vertices a polyline keeps within ``tolerance`` of dense points."""
    return np.asarray(vertices, np.float64)[curves.simplify(vertices, tolerance, closed)]


def add_clearance(model, res: dict, offset):
    """A clearance check's result (``tools.clearance.check``, plus ``line``, ``line_id``, ``points_checked``,
    ``frame``, ``axis``) as a directory ``clearance NN``: a point set of every hit, and a marker at each place's lowest
    point. Returns the directory, or None when nothing entered the envelope."""
    from pclabel.store.annotations import Marker, PointSet

    off = np.asarray(offset, np.float64)
    if not res["places"]:
        return None
    base = {"tool": "clearance", "assisted": True, "params": res["params"], "line": res["line"],
            "line_id": res["line_id"], "points_checked": res["points_checked"], "height_axis": res["frame"],
            "axis": [float(x) for x in res["axis"]]}  # fmt: skip
    d = model.new_directory(next_directory_name(model, "clearance"))
    hits = model.add(PointSet(np.asarray(res["hits"], np.int64), [dict(base)]), "inside the envelope")
    model.move([hits.id], d.id)
    for pl in res["places"]:
        src = dict(base, clearance=pl["clearance"], points=pl["points"], along=pl["along"], across=pl["across"])
        mk = model.add(Marker(pl["position"] + off, src), f"clearance {pl['clearance']:.2f} m at {pl['along']:.1f} m")
        model.move([mk.id], d.id)
    return d


def curb_fit_source(source: dict, result: dict, params_record: dict, frame: str, axis) -> dict:
    """A line's source after "smooth onto the curb top" (``tools.curbfit.fit``'s ``result``): the fit appended to
    ``curb_fit`` (and the fits an older line kept under ``kerb_fit`` moved there), marked assisted, its ``curve_fit`` and any ``edited`` mark dropped (later hand edits mark it again)."""
    rec = {k: result[k] for k in ("side", "sections", "anchors", "low_steps", "outliers", "no_edge", "top_unseen",
                                  "height_median", "off_anchors_rms", "off_anchors_max", "largest_move",
                                  "mean_move", "bridged", "kept", "seconds")}  # fmt: skip
    rec.update(params=params_record, height_axis=frame)
    src = {k: v for k, v in (source or {}).items() if k not in ("curve_fit", "edited")}
    old = src.pop("kerb_fit", [])  # the key before 2026-11-16
    src["curb_fit"] = list(old) + list(src.get("curb_fit", [])) + [rec]
    src["assisted"] = True
    src.setdefault("axis", [float(c) for c in axis])
    src.setdefault("height_axis", frame)
    return src


def join_source(joined: dict, lines: list, params_record: dict, frame: str, assisted) -> dict:
    """The source of a line made by ``tools.linejoin.join_lines`` (``joined``) from these annotation ``lines``;
    ``assisted(item)``: whether a tool drew it."""
    return {"tool": "line_join", "assisted": any(assisted(lines[i]) for i in joined["sources"]),
            "params": params_record, "from": [lines[i].name for i in joined["sources"]],
            "from_ids": [lines[i].id for i in joined["sources"]], "bridged": joined["bridged"],
            "dropped": {lines[i].name: c for i, c in joined["dropped"].items()}, "height_axis": frame}  # fmt: skip


def add_cover(model, marker, ring, template_record: dict, axis, label: str):
    """A cover outline (``ring``, world) around ``marker``, both in a new directory ``cover NN`` beside where the
    marker was; the marker takes ``label`` if it has none. Returns the directory."""
    home = model.parent(marker.id)
    d = model.new_directory(next_directory_name(model, "cover"), home.id if home is not None else None)
    if not marker.label:
        marker.label = label
    src = {"tool": "cover_template", "assisted": True, "template": template_record, "marker": marker.id,
           "axis": [float(x) for x in axis]}  # fmt: skip
    pl = model.add(Polyline(ring, True, src), "outline")
    pl.label = label
    model.move([marker.id, pl.id], d.id)
    return d
