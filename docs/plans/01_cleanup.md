# Plan 01: bring the standard, the docs and the code into order

Written 2026-11-16 in a planning session, for the maintainer who will do the work. It is based on four read-only
audits of the repo at commit `29f6472` (243 tests in 33 files, 24,843 lines in `pclabel/`, `tests/` and `bench/`).
Line numbers below refer to that commit and will drift; find things by name.

Three parts, in this order: **A** the labelling standard, **B** the docs, **C** the code. Part A needs the
user's answers. Parts B and C do not, except where marked.

The user's instruction for this work: nothing in the existing code or docs is fixed, and the maintainer owns the
structure. Merge and delete in preference to adding beside. Git history is the archive, so stale material is
removed, not parked.

---

## A. The labelling standard

**Done 2026-11-16.** The user went through the table of differences between their notes and the
repo's standard (git history of this file has it) and decided:

- every row as proposed (the curb is one line, no gutter or drain class, the 5 cm pavement inset, one vegetation
  class, the uncertain mark on items only, the order of work);
- ramps stay a kind of road;
- the class is named `vegetation`; on the map it is outlines with a measured height (the threshold is proposed);
- covers are future work;
- lamp posts are split into `lta_lamp_post` (with its LTA number as the marker's `asset_id`) and `lamp_post`
  (privately owned, which every existing lamp post label is); `other_pole` is usually road signage.

`docs/LABELLING_STANDARD.md` (0.3) was rewritten from the user's notes, with the maintainer's R-, P- and Q- items
removed. `docs/OPEN_QUESTIONS.md` was deleted, and `config/taxonomy.yaml` is at 0.7, with no class an existing
project uses removed. `/decide` and `/observe` were updated to match.

The user's observations in `ignore/` are now O-0017 to O-0024 in `docs/OBSERVATIONS.md`. This plan also listed
"alternating paint on curbs", which is not in the notes: the alternating pattern there is the tilted road, O-0019.

`docs/SPEC.md` and `docs/ROADMAP.md` still cite the removed R- and Q- ids; part B rewrites them. Older observations
keep theirs, and a note at the top of that file explains them.

---

## B. The docs

**Done 2026-11-16, except the vendor drawing (asked).**

- `docs/SPEC.md` was replaced by `docs/MANUAL.md` (what is built, tool by tool, by parameter dataclass, with known
  failure cases; the keymap taken from `ui/main_window.py`).
- `docs/ROADMAP.md` was replaced by `docs/RESULTS.md` (measured numbers by topic, with scan, date and method;
  in-app numbers say no script was kept). `docs/plans/` is the roadmap.
- `docs/ARCHITECTURE.md` keeps the formats; the journal, per-point exports, LAS/PLY import and project files that
  were never built are gone. The decision log is one table (D-001 to D-019), with the user's decisions from SPEC.
- `docs/CONTEXT.md`: the three ROCK projects added; answered questions removed.
- `docs/OBSERVATIONS.md`: ids in order; the five entries whose scripts were lost say their numbers are not
  reproducible.
- `KICKOFF.md` and `.claude/commands/ingest-observations.md` deleted; `/observe` sends cited scripts to
  `bench/experiments/` and no longer mentions the query language.
- `requirements.txt`: `pyqtgraph`, `laspy`, `lazrs` and `plyfile` removed (nothing imports them; they stay
  installed in the env until it is rebuilt).
- No doc or code string mentions the journal or a milestone.

Left: `mobile-colorized-v2-1.dxf` (7.3 MB, a vendor's drawing, tracked at the repo root) belongs beside its scan in
`../review_commercial/ROCK_planimetrics/mobile_mapping/`. **Ask the user** before moving it, and update the path
in `docs/CONTEXT.md`.

---

## C. The code

**Progress, 2026-11-16.** `bench/check_projects.py` exists (it compares the committed code and the working tree on
the same project files, since the user labels while the code changes).
- C1, C2, C3 and C4: done. New homes: `analysis/geometry.py` (frame axes, the disk kernel, frustum culling),
  `analysis/curves.py` (length, resampling), `taxonomy.hex_rgb` and `ancestors_in`, `store/util.py`
  (`write_json_atomic`), `store/project.source_doc`, `tools/sets.isin_sorted`, `tools/tracing.drive`;
  `curbtrace.Section` / `NoStep` dataclasses; box sizes in the parameter dataclasses (`box_below`, `box_above`,
  `start_length`, `road_behind`).
- C5: done. `ops/labels.py` resolves annotations to a class and a flags byte per point, with a source-order
  export (`python -m pclabel labels`). `ops/trace.py` turns tracer runs, clearance checks, kerb fits, joins and cover
  outlines into annotations and writes the trace log; `ops/trees.py` saves trees and poles; the UI calls both. Tool
  labels come from the taxonomy's `tools` section; the curb tracer labels the lip `curb` and leaves the toe
  unlabelled. The unused `key` field is gone from the taxonomy. The synthetic street has a `material` array (the
  physical surface) beside `cls`, which is now the standard's classes (curb, drain and canopy points have none).
- C6: done. `store/tiles.py: tiles_meeting` is the one tile query (ten copies before); every tool takes
  `filters: SelectFilters` (the seed tools' `clip=` / `inside=` and the clean-up's `allowed` callback are gone; the
  clean-up takes `locked`); `tools/selection.restrict` is the one result filter of the seed tools. The numba
  kernels are unchanged. `bench/bench_select.py` before and after: 1,208 and 1,199M points/s (mask), 1,182 and
  1,168M (mask and depth test), within run-to-run noise.
- C12: done. `street_1m` (conftest) is the shared 1M-point scene; `synth.store_order` replaces the tile-and-reorder
  boilerplate. The UI tests keep a fresh window each (they change annotations; a shared one would make them depend
  on their order), so the suite still takes about 80 s.
- C14: done. New projects are schema `pclabel.project/2`, without label columns; schema 1 opens, its columns unread.
  Point info shows the point's class resolved from the annotations (`ops/labels.labels_at`), the sets holding it,
  and any conflict. The class colour mode is left out of the app (synthetic ground truth only).
- C7: done where it changes what is recorded: MarkerParams holds the click-snapping thresholds, TreeParams the fit's
  `trim` and is recorded with each band, pieces record their whole dataclass, shape selections their depth
  tolerance; the `record()` wrappers are gone (`asdict`). Kept as they are: `Annotations.smooth`, `convert`,
  `join_curves` and `Curve.from_line` take one or two numbers each and already write them into the line's source.
- C8: done. `tools/voxels.py` holds the hash (`hash_slot`, `find`, `grown`, `BITS`, `EMPTY`) and `local_normals`;
  the geometric grow, pieces and split import it instead of each other's private names. Each keeps its own short
  insert loop: a shared `claim` cost 50 % on pieces. `bench/bench_voxels.py` on streets 1, before / after: grow
  1.96 / 1.90-1.94 s, pieces 0.090 / 0.090-0.103 s, split 0.52 / 0.49-0.50 s, identical results.
- Next: C9 to C11, then C13.

Rules for every step:

- A step changes structure or behaviour, never both.
- Run the whole suite after each step: `C:\Users\desktopms\anaconda3\envs\pclabel\python.exe -m pytest`.
- The user's projects must load exactly as before. Before starting, write a read-only check in `bench/` that
  loads each real project's annotations and prints the item counts per kind and label (streets 1: 370 items,
  schema 7; ExchangeGreen 1: 111 items, schema 9; mobile-colorized-v2-1: 1,022 items, schema 6). Run it after
  every step that touches `store/`.
- The app has one user and no other callers, so internal names and signatures can change freely. Saved files
  cannot.

### Before the pipeline

These are what plan 02 builds on.

**C1. Delete dead code.** About 40 lines.
- Unused: `camera.VIEWS`, `camera.look_at`, `renderer.read_color`, `synth.ground_height`,
  `settings.save_settings`, `Taxonomy.subset_of`, the empty section header at `ui/trace.py:711`, the repeated
  import at `tools/markers.py:47`.
- Fix while there: `TraceController.back` (`ui/trace.py:512`) trims every result array except `top_seen`.

**C2. One home for geometry and colour helpers.** About 150 lines.
- `volume.frame_axes` is imported from the selection module by six tools, and the two tracers each carry their
  own copy (`curbtrace.py:56-65`, `linetrace.py:54-62`) that differs only in raising on a degenerate direction.
  Move it to `analysis/geometry.py` with a raising variant.
- Polyline resampling and length exist in `curves`, `linejoin` (`_dense`, `_length`), `kerbfit` (`_uniform`),
  `clearance` (`_sample`), `vector_export` and `ui/trace.py`. Make `curves` the one place.
- One disk kernel for `ground`, `density`, `cleanup` and `magic`.
- One hex-colour parser (four copies: `ui/annotate.py`, `ui/labels.py`, `taxonomy.py`, `io/vector_export.py`).

**C3. Store utilities.** About 80 lines.
- `write_json_atomic` out of the importer into `store/util.py`.
- `Project.source_doc()` for the `file`, `sha256`, `n_valid`, `offset` header, which is built in five places.
- `isin_sorted` from `store/annotations.py` into `tools/sets.py`, which removes the lazy imports that work around
  the cycle between `store` and `tools`.
- The frustum math in `render/camera.py:243-258` is plain numpy and is imported by `tools/selection.py`; move it
  out of the GL package.
- `Annotations.export_labels` uses its own `ancestors` instead of the taxonomy's.

**C4. Tracer core.** About 120 lines. Plan 02, P3 changes the curb tracer, so tidy it first.
- `kerbfit` reads `curbtrace.section`'s result through untyped dictionary keys. Make the section a dataclass.
- The two `run_trace` drivers are the same code. Share it.
- The section box sizes are hard-coded (`curbtrace.py:75`, `kerbfit.py:85`, `linetrace.py:76`). Move them into the
  parameter dataclasses, since they are thresholds like the others.

**C5. A Qt-free operations layer, first part.** Create `pclabel/ops/` and move into it, without changing it:
- turning a trace result into annotations, and the trace log (`ui/trace.py:435-465`, `547-624`);
- kerb fit, join, clearance and cover results into annotations (`ui/trace.py:677-948`);
- saving a tree or pole (`ui/trees.py:203-308`);
- resolving annotations to a per-point label array, which today exists only inside the renderer
  (`render/renderer.py:169-190`), with a source-order export.

The UI then calls these, and so does the pipeline. The label names that tools default to (`curb_top`,
`curb_bottom`, `road_line`, `tree`, `tree_trunk`, `pole`, `vegetation`, `cover`) are hard-coded in `ui/trace.py`
and `ui/trees.py`; move them into `config/taxonomy.yaml` as tool defaults. The taxonomy's `key` field (a hotkey per class) is
read by nothing: wire it to set the label of the selected items, or drop it. The standard (0.3) has one curb
line, `curb`: the curb tracer should write the lip as `curb` and stop writing the toe as `curb_bottom`. The
synthetic street (`synth/street.py`) still classes points as `curb`, `curb_lowered`, `drain`, `grass` and
`tree_canopy`; bring it to the standard's classes along with the tests that read them.

**C6. One tile query and one filter type.** About 200 lines.
- The tile bounding-box filter is written out ten times, and the loop "kernel per tile, collect indices, apply
  the inside mask, apply the clip box, concatenate" seven times (`volume`, `ground`, `magic`, `normals`,
  `cleanup`, `clearance`, `pick`, `selection`).
- Three conventions express the same restriction: `filters`, `clip=` with `inside=`, and an `allowed` callback.
  Keep one.
- Keep the order "inside mask, then clip box", and leave the numba kernels alone. `tests/test_clip_set.py`
  checks every tool against the mask. Time `bench/bench_select.py` before and after.

### Any time after

**C7. Parameter dataclasses everywhere.** Loose arguments that are thresholds: `Annotations.smooth`, `convert`
and `join_curves`; `Curve.from_line`; `select_loop`; `fit_trunk(trim)`; `marker_at_click(snap_radius)` and the
constants inside it; the depth tolerance of shape select. `TreeParams` and `PieceParams` exist but are not what
gets recorded. Remove the `record()` wrappers once `asdict` covers them. The contents of saved records change,
so note it in ARCHITECTURE.

**C8. One voxel hash.** It is implemented three times (`normals`, `pieces`, `split`), and two of the modules
import the third's private names. About 150 lines. Time it on a real scan by hand, since numba speed can shift.

**C9. The rest of the operations layer, and one way to change annotations.** Selection combining, locks and
filters (`ui/main_window.py:577-624`, `682-689`, `789-799`, `937-943`); markers (`ui/annotate.py:825-966`); label
colours, order and the map document (`ui/labels.py`). Other controllers call `AnnotationController`'s private
`_checkpoint` and `_changed` from 22 places and then change the model directly. Give the model one public way
to apply a change.

**C10. Qt boilerplate.** About 500 lines removed.
- One `run_in_worker` helper in place of about twelve hand-rolled threads with their own relay objects. Results
  must be delivered in the UI thread; a handler that ran in the worker thread once hung the import.
- A context manager for the 17 `makeCurrent()` / `doneCurrent()` pairs.
- `SeedToolDock` already builds a form from a field table. Generalise it to build a form from a parameter
  dataclass, and use it for the trace dock (about 35 hand-built spin boxes), clean-up, volume, density, split,
  pieces and trees.

**C11. Split the large modules**, after C9 and C10 have shrunk them: `ui/annotate.py` (1,590 lines, one class of
1,214), `ui/main_window.py` (1,241, one class of 1,151), `ui/trace.py` (991 lines, four unrelated tools in one
dock).

**C12. Tests.** Three test files build the identical scene; the tile-and-reorder boilerplate is in 14 files;
`test_ui.py` builds a fresh main window for each of its 46 tests. Move these into `tests/conftest.py`. Doing
this early makes every later step faster to verify.

**C13. Names.** Do this last, because it touches saved records.
- The user and the taxonomy say "curb". The code says both: `curbtrace` but `kerbfit`, `KerbFitParams`, the
  saved key `kerb_fit`, and about 195 uses of "kerb". Use "curb" everywhere, and keep reading the old key.
- "piece" means four things (connected components, runs between gaps, dashes, Bézier spans). Keep it for
  connected components.
- A tool's module name and the name in its saved record often differ (`ground` writes `grow_select`, `cleanup`
  writes `clean_up`). Give each tool module one constant for its record name.

**C14. Remove the dead label columns.** `labels/labels.u8` and `flags.u8` are created at import (1.3 GB on
streets 1), fed to the renderer and shown in the point info panel, and are always zero. Stop creating and
reading them; show the label resolved from the annotations instead (C5). Leave existing files on disk alone.
This changes the project format, so bump its schema and keep reading the old one.
