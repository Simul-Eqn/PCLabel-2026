"""Read-only: a saved curb trace against a reference curb line of the same project, and both against the cloud.

    python bench/experiments/trace_vs_curve.py "ExchangeGreen 1" "tmi_curb/curb_top 1" "curb trace 01/lip 1"
    python bench/experiments/trace_vs_curve.py "ExchangeGreen 1" "tli_curb/tli_right" "tli_curb/curb trace 02/lip 1"

Prints (1) the trace replayed headless from its saved start and settings, section by section, against the
reference: the sideways offset (+: further onto the curb) and the height offset; (2) the tracer's cross-section
placed every 0.3 m along the whole reference line: where it finds the lip, and where it finds none and why.
Nothing is written. The numbers of plan 02's two reference examples come from this script.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import fields, replace
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from pclabel.analysis import curves
from pclabel.settings import load_settings
from pclabel.store.project import Project
from pclabel.tools.curbtrace import CurbTraceParams, NoStep, run_trace, section


def find(doc: dict, path: str) -> dict:
    nodes, node = doc["tree"], None
    for name in path.split("/"):
        node = next(x for x in nodes if x["name"] == name)
        nodes = node.get("children", [])
    return node


def shares(a) -> str:
    return ", ".join(f"{100 * float((a <= t).mean()):.0f} % within {100 * t:.0f} cm" for t in (0.02, 0.03, 0.05, 0.10))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("reference", help="path of the reference line in the annotation tree, e.g. tli_curb/tli_right")
    ap.add_argument("trace", help="path of a line saved by the curb tracer")
    ap.add_argument("--projects", default=load_settings().projects_dir)
    a = ap.parse_args()
    path = Path(a.projects) / a.project
    proj = Project(path)
    doc = json.loads((path / "annotations.json").read_text(encoding="utf-8"))
    off = np.asarray(doc["offset"], np.float64)
    ref, line = find(doc, a.reference), find(doc, a.trace)
    src = line["source"]
    ax = np.asarray(src["axis"], np.float64)
    ax /= np.linalg.norm(ax)
    names = {f.name for f in fields(CurbTraceParams)}
    p = CurbTraceParams(**{k: v for k, v in src["params"].items() if k in names})

    T = curves.resample(np.asarray(ref["polyline"], np.float64) - off, 0.01, False)
    arc = curves.arc_length(T)
    tree = cKDTree(T)
    t_tan = np.gradient(T, axis=0)
    t_tan -= np.outer(t_tan @ ax, ax)
    t_tan /= np.linalg.norm(t_tan, axis=1, keepdims=True)

    # 1. the trace replayed
    res = run_trace(proj.xyz, proj.tiles, np.asarray(src["start"]) - off, np.asarray(src["toward"]) - off, ax,
                    replace(p, side="auto"))  # fmt: skip
    lip, side = res["lip"], res["params"]["side"]
    _, i = tree.query(lip)
    along = np.sign(np.median(np.diff(i)))  # the trace runs up or down the reference's arc length
    left = np.cross(ax, t_tan[i] * along)
    s = -left if side == "right" else left
    v = lip - T[i]
    up = v @ ax
    o = np.einsum("ij,ij->i", v - np.outer(up, ax), s)
    print(f"{a.project}: {a.trace} against {a.reference} ({arc[-1]:.1f} m)")
    print(f"replay: {len(lip)} sections, reference arc {arc[i[0]]:.1f} to {arc[i[-1]]:.1f} m, curb on the {side}, "
          f"stopped: {res['stopped']}")  # fmt: skip
    print(f"  across, lip - reference: median {100 * np.median(o):+.1f} cm, worst {100 * np.abs(o).max():.1f} cm; "
          f"{shares(np.abs(o))}")  # fmt: skip
    print(f"  height, lip - reference: median {100 * np.median(up):+.1f} cm, 10th and 90th percentile "
          f"{100 * np.percentile(up, 10):+.1f} and {100 * np.percentile(up, 90):+.1f} cm")  # fmt: skip
    print(f"  step {100 * np.median(res['height']):.1f} cm (median); face seen in {100 * res['seen'].mean():.0f} % "
          f"of sections, top seen in {100 * res['top_seen'].mean():.0f} %")  # fmt: skip
    for k in np.flatnonzero((np.abs(o) > 0.03) | ~res["top_seen"]):
        print(f"    arc {arc[i[k]]:6.1f} m: across {100 * o[k]:+5.1f} cm, height {100 * up[k]:+5.1f} cm, step "
              f"{100 * res['height'][k]:4.1f} cm, face {100 * res['face'][k]:4.1f} cm, top seen {bool(res['top_seen'][k])}")  # fmt: skip

    # 2. the tracer's section along the whole reference
    pts = curves.even_points(T, 0.3, False)
    _, j = tree.query(pts)
    tan = t_tan[j] * along
    left = np.cross(ax, tan)
    sd = -left if side == "right" else left
    pp = replace(p, side=side)
    found, none = [], []
    for c, t, s_, jj in zip(pts, tan, sd, j):
        r = section(proj.xyz, proj.tiles, c - 0.3 * s_ - 0.09 * ax, ax, t, pp)
        if isinstance(r, NoStep):
            none.append((arc[jj], r.why))
        else:
            found.append(float((r.lip - c) @ s_))
    f = np.abs(np.array(found))
    print(f"sections along the whole reference: a lip in {len(found)} of {len(pts)}; {shares(f)}")
    k = 0
    while k < len(none):  # stretches with no lip, by reason
        m = k
        while m + 1 < len(none) and none[m + 1][1] == none[k][1] and none[m + 1][0] - none[m][0] < 0.45:
            m += 1
        print(f"    arc {none[k][0]:6.1f} to {none[m][0]:6.1f} m: {none[k][1]}")
        k = m + 1


if __name__ == "__main__":
    main()
