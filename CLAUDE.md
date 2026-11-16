# PCLabel: notes for Claude Code

PCLabel turns terrestrial laser scans (xyz + RGB + intensity, hundreds of millions of points) of Singapore street
scenes into a vector map for incident-scene sketch plans. It has two parts on one code base:

- a desktop app in which the user labels scans with assisted tools (curb tracer, magic select by intensity, tree
  tool and others);
- an automatic pipeline, being built, that produces the same output with no clicks.

The app makes the reference labels, shows the pipeline's output beside them, and is where the user corrects it.

**Priorities:** curb lines and road markings first; then trees and poles as a 2D map (a marker each, plus the
trunk's cross-section for trees); then surfaces.

**Current work:** [docs/plans/01_cleanup.md](docs/plans/01_cleanup.md), then
[docs/plans/02_auto_pipeline.md](docs/plans/02_auto_pipeline.md). Background on the downstream use is in
[docs/CONTEXT.md](docs/CONTEXT.md).

## you maintain this code

You are the only maintainer, and the structure is yours. Nothing in the existing code or docs is fixed.

- Merge and delete in preference to adding beside. If a function nearly does what you need, change it.
- One implementation per algorithm. The pipeline calls the same function as the interactive tool.
- Docs describe what is built. Plans go in `docs/plans/`. Git history is the archive: delete stale text.
- Add a rule to this file only after a mistake that the rule would have prevented.

Three things are not yours: the labelling standard, the source scans, and the user's labelled projects.

## the labelling standard is the user's

- The user's own notes are the standard: `ignore/labelling_standards.md` and `ignore/labelling_procedure.md`
  (`ignore/` is gitignored and holds the user's notes and images). Where `docs/LABELLING_STANDARD.md` or
  `config/taxonomy.yaml` differ, the notes win and the repo's files are fixed to match.
- Never mark a rule `decided`, or change a decided rule, unless the user says so. Write it as `proposed` and ask.
- When the user mentions something they noticed in the data, offer `/observe`. When they decide a labelling
  question, offer `/decide`.

## mistakes that are costly here

**Data**
- Source scans and drawings are read-only, wherever they are (`../data/`, `../review_commercial/`). Open E57
  files only as `io/e57.py` does, with `libe57.ImageFile(path, "r")`. `pye57.E57(path)` can delete its input.
- The projects in `C:\Users\desktopms\Documents\PCLabel_projects\` hold weeks of hand labelling. No script or
  pipeline run writes to a project's `annotations.json` or `annotations/*.npy`; pipeline output goes to
  `<project>/auto/`.
- Saved projects must keep loading. When an on-disk or export format changes, bump its `schema`, keep reading
  the old one, and update [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Meaning of labels**
- Labels live on annotation items and directories. A point in no labelled set has not been assessed; it is not
  a negative. "Assessed, none of the classes" is the label `other`.
- A selection or label applies to every point inside it at full resolution, not to the subset that is drawn.
- Store order is not source order. Exports go through `source_index`.
- A tool's output is not evidence for that tool unless the user confirmed or edited it (`assisted`, `edited`).
- Every assisted or automatic result records its parameter dataclass. Those thresholds are what is being
  studied.
- The taxonomy is data ([config/taxonomy.yaml](config/taxonomy.yaml)). No class id or class name is hard-coded.

**Geometry**
- Positions are float32 relative to a float64 project offset. Tools return local coordinates; everything
  written out is in scan coordinates and carries the offset.
- Roads in one scan slope differently, so no single plane is the ground for a whole scan. The app's height
  axis is a plane the user fits for the area in view; automatic stages take heights from a local ground
  surface.

**Code**
- `tools/`, `analysis/`, `store/`, `io/`, `ops/`, and the planned `auto/` and `scoring/` import no Qt and no GL. What
  turns a tool's result into annotations lives in `ops/`, so the app and the pipeline save the same things.
- No Python loops over points: numpy and numba. On selections use the merges in `tools/sets.py`, not
  `np.union1d`, `setdiff1d` or `unique`, which took tens of seconds at 100M points.
- Qt: deliver a worker's result to its handler in the UI thread.
- Windows: open text files with `encoding="utf-8"`. With the cp1252 default a failed write can leave an empty
  file.
- The repo's text files have CRLF line endings. Python's `read_text` / `write_text` and `sed -i` produce LF or
  mixed endings: edit with the Edit tool, or read and write bytes keeping `\r\n`.
- Do not import code from the sibling folders (`../pcview`, `../Roadrun`, `../CurbNet-main`,
  `../scene2sketch`). The user wants PCLabel standalone.
- Ask before adding a dependency.

**Measuring**
- Tests use synthetic scenes from `pclabel.synth` and never the real scans. GL tests run on Mesa llvmpipe.
- The GPU is shared with training runs. The render benchmarks refuse to measure while it is busy; a forced
  result must say so.
- Report measured numbers on named scans. A script whose number is cited in the docs lives in `bench/`, not in
  the scratchpad.

## commands

Conda env `pclabel`; set-up is described at the top of `requirements.txt`. If `conda run -n pclabel` fails in
the tool shell, call `C:\Users\desktopms\anaconda3\envs\pclabel\python.exe` directly.

- `python -m pclabel ["streets 1"] [--gl software]` opens the app.
- `python -m pclabel import <file.e57> [--name N]` imports a scan; `python -m pclabel info <project>`
  summarises one.
- `python -m pytest` runs the tests.
- `python bench/bench_read.py`, `bench_select.py`, `bench_render.py [--gl software]` run the benchmarks.

## data

Source scans are in `../data/` and `../review_commercial/ROCK_planimetrics/`; sizes and fields are in
[docs/CONTEXT.md](docs/CONTEXT.md#the-scans). Imported projects:

| project | points | labels |
|---|---|---|
| `streets 1` | 634M | the user's: curbs, road, road lines, pavement, trees, poles |
| `ExchangeGreen 1` | 257M | the user's: curbs, road, road lines; trees and poles in part |
| `mobile-colorized-v2-1` | 596M | a vendor's drawing, imported; a US scene |
| `newroad 2` | 8.4M | none; the quick check |
| `ExchangeGreenFast 1`, `ExchangeGreenSlow 1`, two ROCK demo scans | 16M to 253M | none |

## working with the user

- End a feature with a short "try it": which scan to open, which keys to press, what to look for.
- The machine is one Windows 11 workstation (i9-14900KF, 128 GB RAM, RTX A4000 16 GB). Desktop only, one user.
