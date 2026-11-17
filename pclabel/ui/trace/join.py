"""The Trace panel's "join traced pieces": the lines selected in Annotations (or in selected directories), label by
label (tools/linejoin.py): repeats from retries dropped, ends within reach joined, into one line each."""

from __future__ import annotations

from dataclasses import asdict

from PySide6.QtWidgets import QCheckBox, QFormLayout, QGroupBox, QPushButton

from pclabel.ops import trace as trace_ops
from pclabel.tools.linejoin import JoinParams, join_lines
from pclabel.ui.docks import _spin


class JoinBox(QGroupBox):
    def __init__(self):
        super().__init__("join traced pieces")
        self.setToolTip("the lines selected in Annotations (or in selected directories), label by label: "
                        "repeats from retries dropped, ends within reach joined, into one line each")  # fmt: skip
        jp = JoinParams()
        g = QFormLayout(self)
        self.gap = _spin(0.0, 20.0, jp.join_gap, 0.1, 2, " m")
        self.gap.setToolTip("ends closer than this are joined (the joining piece is recorded as bridged)")
        self.overlap = _spin(0.0, 1.0, jp.overlap, 0.01, 2, " m")
        self.overlap.setToolTip("a vertex this close to a longer line repeats it (a retry over the same stretch)")
        self.angle = _spin(0.0, 180.0, jp.max_angle, 5.0, 0, " deg")
        self.angle.setToolTip("a join turns no more than this from either line")
        self.min_length = _spin(0.0, 10.0, jp.min_length, 0.1, 2, " m")
        self.min_length.setToolTip("pieces shorter than this (after dropping repeats) are left out")
        self.keep = QCheckBox("keep the originals")
        self.run = QPushButton("join the selected lines")
        for label, wdg in (("join gaps up to", self.gap), ("repeats within", self.overlap),
                           ("turning at most", self.angle), ("drop pieces under", self.min_length)):  # fmt: skip
            g.addRow(label, wdg)
        g.addRow(self.keep)
        g.addRow(self.run)

    def params(self) -> JoinParams:
        return JoinParams(overlap=self.overlap.value(), join_gap=self.gap.value(), max_angle=self.angle.value(),
                          min_length=self.min_length.value())  # fmt: skip


class JoinTool:
    def __init__(self, trace):
        self.trace = trace  # the Trace panel's controller: its window, status line and how lines are saved
        self.box = JoinBox()
        self.box.run.clicked.connect(lambda: self.run())

    def run(self, keep: bool | None = None) -> list:
        """Join the lines selected in Annotations (or in selected directories), label by label (tools/linejoin.py):
        a directory "<name> joined" with a polyline per joined line, next to the first directory they came from; the
        originals removed (and directories left empty), unless "keep the originals". One undo step."""
        from pclabel.io.vector_export import assisted, geometry

        t = self.trace
        w = t.w
        a = w.annot
        m = a.model
        items = [x for x in a.selected_items() if x.kind in ("segment", "curve", "polyline")]
        if len(items) < 2:
            t.dock.status.setText("select the lines to join (or their directories) in Annotations: at least two")
            return []
        keep = self.box.keep.isChecked() if keep is None else keep
        params = self.box.params()
        f = w.frames[w.active_frame]
        by_label: dict = {}
        for x in items:
            by_label.setdefault(m.label_of(x.id)[0], []).append(x)
        results = []
        for lab, xs in by_label.items():
            out = join_lines([geometry(x)[0] for x in xs], f.normal, params)
            results.append((lab, xs, out))
        home = m.parent(items[0].id)
        stem = home.name if home is not None else "lines"
        a.checkpoint(f"join {len(items)} lines")
        d = m.new_directory(f"{stem} joined", m.parent(home.id).id if home is not None and m.parent(home.id) is not None else None)
        if home is not None:  # beside the directory the lines came from
            sib = m.siblings(home.id)
            sib.remove(d)
            sib.insert(sib.index(home) + 1, d)
            if d.color is None and home.color:
                d.color = home.color
        n_out, n_bridged, n_dropped = 0, 0, 0
        for lab, xs, out in results:
            for k, o in enumerate(out, 1):
                src = trace_ops.join_source(o, xs, asdict(params), f.name, assisted)
                pl = m.add(t.line_item(o["vertices"], o["closed"], src), f"{lab or 'line'} {k}")
                pl.label = lab
                m.move([pl.id], d.id)
                n_out += 1
                n_bridged += len(o["bridged"])
                n_dropped += sum(o["dropped"].values())
        if not keep:
            dirs = {m.parent(x.id).id for x in items if m.parent(x.id) is not None}
            for x in items:
                m.remove(x.id)
            for di in dirs:  # directories the lines left empty go too
                try:
                    dd = m.node(di)
                except KeyError:
                    continue
                if not dd.children and dd.id != d.id:
                    m.remove(di)
        a.changed(f"{d.name}: {len(items)} lines joined into {n_out} ({n_bridged} gap{'s' * (n_bridged != 1)} bridged, "
                  f"{n_dropped} repeated vertices dropped)" + ("; the originals kept" if keep else ""))  # fmt: skip
        t.dock.status.setText(a.dock.status.text())
        return d.children
