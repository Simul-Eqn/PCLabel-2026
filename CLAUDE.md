# PCLabel: notes for Claude Code

PCLabel is a desktop app for labelling very large outdoor terrestrial laser scans (TLS: xyz + RGB + intensity) of
Singapore street scenes point by point, with classes such as road, curb, pavement, drain, grass, tree and lamp post.

**Status:** M0 (stack spike) and M1 (import and view) are done; all five scans are imported. M2 is in progress
(selection, ground grow, magic select by intensity, plane height axis and its export, slice, and SPEC §13's
segments, curves and annotation directories built). Labelling is done by labelling annotation directories and
items (D-007, the user's decision of 2026-10-20). See the results blocks in [docs/ROADMAP.md](docs/ROADMAP.md).

**Commands** (conda env `pclabel`; set up as described at the top of `requirements.txt`):
- `python -m pclabel ["streets 1"] [--gl software]` opens the app (optionally a project by name or path).
- `python -m pclabel import <file.e57> [--name N]` imports a scan; `python -m pclabel info <project>` summarises one.
  Projects go to `C:\Users\desktopms\Documents\PCLabel_projects\` (all five scans are imported there).
- `python -m pytest` runs the tests. GL tests run on Mesa llvmpipe (CPU), never on the GPU.
- `python bench/bench_read.py`, `bench_select.py` and `bench_render.py [--gl software]` run the benchmarks
  (`--json <out>` saves raw numbers). `python bench/check_open.py "streets 1" [--gl software]` is the M1 manual
  check. The GPU benchmarks refuse to measure while another process keeps the GPU busy.

**The GPU is shared with training runs** (e.g. the CurbNet Docker container). The user confirmed (2026-10-19) that
using the GPU for PCLabel alongside training is fine. However, fps measured while training runs is not
representative: the benchmarks warn and refuse unless `--force`, and results must say so. `--gl software` (Mesa
llvmpipe in `%LOCALAPPDATA%\PCLabel\mesa-26.2.4`, CPU only) remains available, and the tests always use it.

## why this exists (read before designing anything)

- The labels are **reference data for tuning and scoring the thresholds of algorithmic (non-ML) methods**. Those
  methods turn a scan into a vector road map (curb lines, markings, poles) for incident-scene sketch plans. The labels
  are not mainly ML training data. Background: [docs/CONTEXT.md](docs/CONTEXT.md).
- The project is at the stage of **setting the labelling standard**. The most valuable output of a labelling
  session is not the labels themselves. It is the **observations**, e.g. "the only points below the road surface
  are the drains that periodically interrupt the curbs", and the **decisions** they lead to, e.g. "a park
  connector counts as pavement". They live in [docs/OBSERVATIONS.md](docs/OBSERVATIONS.md) and
  [docs/LABELLING_STANDARD.md](docs/LABELLING_STANDARD.md).
- So the app has two jobs, in this order of design priority:
  1. make subtle scene facts **visible** (cm-scale height differences, material and intensity changes, occlusion,
     density) and make **recording** them almost free;
  2. make labelling fast, precise and reproducible on clouds of 600M+ points.
- For every view or tool you build, answer two questions: *what can the labeller notice now that they could not
  before, and how do they record it in two keystrokes or fewer?*

## hard constraints

- **Standalone.** The user does not want PCLabel based on pcview or on other code in the sibling folders (`..`:
  pcview, Roadrun, CurbNet-main, scene2sketch, …). Design it from scratch and do not import from those folders. The
  facts about the data and the domain collected in `docs/` are fine to use.
- **Source scans are read-only.** Never write into `../data/` and never modify a source file. Imported projects
  (large binaries) go into a projects directory outside the repo (default
  `C:\Users\desktopms\Documents\PCLabel_projects\`, configurable).
- **Labels are per point, at full resolution.** A selection applies to every point inside it, not only to the
  level-of-detail subset that is drawn. Exports map back to the source file's point order exactly
  ([docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#source-order)).
- **0 means unassessed**, never "not this class". "Assessed, and none of the classes" is its own class (`other`).
- **The taxonomy is data.** It lives in [config/taxonomy.yaml](config/taxonomy.yaml). No class id except 0 is
  hard-coded.
- **Every label change goes through the journal**: tool, parameters, selection definition, point count, time,
  standard version. Undo and redo use the journal. A crash loses at most the operation in progress.
- **Assisted tools record their parameters.** Those parameters are the thresholds being studied. The labeller's
  corrections after an assisted operation are the evidence for tuning them.
- **Coordinates:** local metres, z up, not georeferenced. Store float32 positions relative to a float64 project
  offset, and never drop the offset from outputs.

## machine

Windows 11 workstation: i9-14900KF (24 cores / 32 threads), 128 GB RAM, NVIDIA RTX A4000 (16 GB VRAM; Windows WMI
misreports it as 4 GB), NVMe SSD with about 570 GB free. Desktop only: no web server, no browser UI. One user.

## stack (planning decision; the M0 spike confirms or rejects it)

Python 3.12 · PySide6 (Qt 6 widgets, docks, shortcuts) · moderngl inside a `QOpenGLWidget` (OpenGL 4.3+ core:
custom point shaders, SSBOs, compute if needed) · numpy + numba (all per-point work; never Python loops over
points) · scipy (KD-trees, fitting) · pyqtgraph (histograms, the profile view) · pye57, laspy[lazrs], plyfile (I/O)
· pyyaml · pytest.

Why this stack:
- The standard feeds Python threshold algorithms, so tools, statistics and assisted methods must run in the same
  numpy world.
- Qt gives a real desktop UI with docks and keyboard shortcuts.
- moderngl gives full control over the shaders (height wrap, plane-relative height, eye-dome lighting).

If the M0 spike misses its targets, stop and report the numbers and the alternatives (e.g. VisPy, or a small
C++/Rust renderer module) before switching.

## planned layout

```
pclabel/              package
  io/                 E57 / LAS / PLY readers (chunked), exporters
  store/              project store: columns, tiles, labels, journal
  render/             GL context, shaders, LOD streaming, EDL, overlays
  tools/              selection, labelling, assisted tools (numpy/numba, no Qt or GL)
  analysis/           reference frames and surfaces, features, stats, queries, lint
  ui/                 Qt windows, docks, keymap
  synth/              synthetic street generator with ground truth
config/taxonomy.yaml  classes and flags (source of truth)
docs/                 context, standard, observations, spec, architecture, roadmap
tests/                pytest, synthetic data only
bench/                benchmark scripts (not run by pytest)
```

Keep algorithms (`tools/`, `analysis/`) free of Qt and GL. That way they can be unit-tested and later reused by the
threshold algorithms.

## conventions

- Tunable parameters live in dataclasses, one per tool. They are shown in the tool's panel and written into every
  journal entry and export sidecar.
- The UI thread never blocks for more than about 100 ms. Long jobs (import, feature computation, region growing on
  millions of points) run in worker threads with progress and cancel. numba kernels use `nogil=True`.
- Tests use `pclabel.synth` scenes with known ground truth. No test may need the real multi-GB scans. Real-data
  checks are manual and listed per milestone in [docs/ROADMAP.md](docs/ROADMAP.md), with their measured results
  written back there.
- Windows: always open text files with `encoding="utf-8"`. With the cp1252 default, a failed write can leave an
  empty file.
- When behaviour changes, update [docs/SPEC.md](docs/SPEC.md). When an on-disk or export format changes, update
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and bump its `schema` version.
- Use the session scratchpad for throwaway scripts and keep the repo clean.
- Ask before adding a dependency that is not in the stack above.

## working with the user

- The user is the labeller and owns the labelling standard. Never mark a rule `decided` in LABELLING_STANDARD.md,
  or change a decided rule, unless the user says so. Write it as `proposed` and ask.
- The user decides the open questions in [docs/OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md) after labelling, from
  evidence. Don't push for decisions. Until a question is decided, its *meanwhile* line is the working rule. Tools
  that need a definition (e.g. lint checks) use it, and must make it easy to change later.
- When the user mentions something they noticed in the data, offer to record it with `/observe`. When they make a
  labelling decision, offer `/decide`. Observations exported from the app are merged with `/ingest-observations`.
  The commands are in `.claude/commands/`.
- At the end of each milestone, give a short "try it" guide: which scan to open, which keys to press and what the
  labeller should look for. That is how the tool earns observations.
- Report performance as measured numbers on named files, not impressions.

## data

The source scans (read-only) are in `../data/`; sizes and fields are in
[docs/CONTEXT.md](docs/CONTEXT.md#the-scans).
- `newroad 2.e57` (8.4M points): quick checks.
- `ExchangeGreen/BLKARC/Fast/ExchangeGreenFast 1.e57` (29.7M points): the mid-size case.
- `streets 1.e57` (634M points, 12 GB): the stress test.
