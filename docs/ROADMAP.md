# roadmap

Milestones in build order. The order puts **seeing and noting** (M2) before labelling (M3) on purpose: the user can
start making observations with the height tools and pins while the labelling machinery is still being built.

For each milestone:
- implement the SPEC sections listed;
- write the automated tests;
- run the manual checks on the named scans and **write the measured results into the milestone's results block**;
- end with a "try it" guide for the user.

Do not start the next milestone without the user's go-ahead.

## M0: stack spike (decide D-001 and D-002)

Goal: prove that the planned stack can show and select at this scale, before building on it.

- `git init`, `.gitignore`, a `pclabel` conda env (Python 3.12) with a pinned `requirements.txt`.
- `pclabel.synth.make_street` (ARCHITECTURE §testing): about 5M points with ground truth, plus a "tiled copy"
  option to reach 50–100M points for benchmarks. It can write E57 via pye57.
- **Render spike:** a PySide6 window with a moderngl `QOpenGLWidget`.
  - Synthetic points in randomised tiles, prefix LOD, an orbit camera.
  - One shader with height wrap, to prove the shader path.
  - **Measure fps** at budgets of 10 / 20 / 40 / 80M drawn points, plus VRAM use.
- **Read spike:** measure throughput of
  - full reads of `newroad 2.e57` and `ExchangeGreenFast 1.e57`;
  - a **chunked** read of the first 50M points of `streets 1.e57` (proves the low-level reader with bounded
    memory). Report points/s and peak RAM.
- **Selection spike:** a numba point-in-mask kernel over 100M points. Report points/s.
- **Deliverable:** a short report in the results block (a table of numbers vs the ARCHITECTURE targets) and a
  recommendation: keep the stack and tiling, or switch (with the options).

Results (2026-10-16 and 2026-10-17; all three spikes measured):

- **Setup.** i9-14900KF (numba 32 threads), RTX A4000 (driver 582.16, OpenGL 4.3 core through `QOpenGLWidget`).
  Python 3.12.15, PySide6 6.12.0, moderngl 5.13.0, numba 0.68.0, numpy 2.5.4, pye57 0.4.19.
  Env: `requirements.txt`. Tests: 27 passed in 2.5 s (`pytest`, synthetic data only, including the shader-vs-numpy
  wrap mirror rendered on the GPU).
- **Synthetic data.** `make_street` ray-casts one TLS setup (0.062° grid, 12.5M rays). It gives 5.0M points with
  ground truth in 1.6–1.9 s, with occlusion, a no-point strip behind the near kerb, a back-facing kerb face without
  points, a blind cone, and drains as the only points below the road. A tiled copy to 99.97M takes 0.6 s.
  Quadtree tiling (T = 2M) of those 99.97M points takes 2.9 s and gives 220 tiles. Reordering the columns into
  store order takes 4.4 s (numpy fancy indexing; easy to parallelise in M1).
- **Read** (`bench/bench_read.py`; each case in a fresh process; peak RAM = peak working set):

  | file | reader | records | time | rate | peak RAM | target |
  |---|---|---|---|---|---|---|
  | newroad 2 | one buffer (= `read_scan_raw`) | 8.39M | 0.58 s | 14.5M/s | 0.31 GB | import ≤ 1 min |
  | newroad 2 | chunked 4M, into RAM | 8.39M | 1.04 s | 8.1M/s | 0.55 GB | |
  | ExchangeGreenFast 1 | one buffer | 29.7M | 2.46 s | 12.1M/s | 0.92 GB | |
  | ExchangeGreenFast 1 | chunked 4M, into RAM | 29.7M | 3.02 s | 9.8M/s | 0.87 GB | |
  | streets 1, first 50M | chunked 4M, bounded | 50.0M | 6.57 s (warm 6.38 s) | 7.6–7.8M/s | 0.49 GB | import ≤ 30 min, ≤ 48 GB |
  | streets 1, whole file (2026-10-17) | chunked 4M, bounded | 633.6M | 78.8 s | 8.0M/s | 0.49 GB | import ≤ 30 min, ≤ 48 GB |

  - libe57 decoding is the limit: 12–14M records/s on one thread. Conversion (validity, pose, float32 offset,
    rgb/intensity) runs at about 60–67M/s.
  - The whole of streets 1 reads in 78.8 s with the same peak RAM as the first 50M. That is about 4 % of the
    30 min import budget, and overlapping conversion with decoding in a worker should bring it to about 50 s.
  - All three files: one scan, identity pose; xyz as 32-bit scaled integers (10 µm); float intensity 0..1;
    8-bit RGB; no invalid-state field.
- **Selection** (`bench/bench_select.py`): numba point-in-mask over 99.97M store-order points; 1920 × 1080 lasso
  mask covering 14.6 % of the screen; 67.8M points selected; median of 7 runs. Target ≥ 200M points/s.

  | case | candidates | time | rate |
  |---|---|---|---|
  | mask | 99.97M | 0.090 s | 1,114M/s |
  | mask + depth test (all pass: worst case) | 99.97M | 0.107 s | 935M/s |
  | mask | 50M | 0.047 s | 1,062M/s |
  | mask + depth test | 50M | 0.053 s | 942M/s |

  That is 4.7–5.6× the target. Lasso preview on 50M candidates takes 0.05 s (target ≤ 0.5 s). Rasterising the
  polygon takes 0.7 ms.
- **Render** (`bench/bench_render.py`, 2026-10-17, GPU idle: 0 %, 399 MiB in use before the run).
  - Setup: 99.97M points in 220 tiles × 5 column buffers. The camera orbits the whole set, so every tile is
    visible and the drawn count equals the budget. 1920 × 1009 viewport, vsync off, 240 frames per row after 30
    warm-up frames. Target: ≥ 30 fps at the default budget.

  | budget | colour mode | point size | fps | wall ms median (p95, max) | GPU ms median (p95) |
  |---|---|---|---|---|---|
  | 10M | wrap | 2 px | 164 | 6.1 (6.4, 7.6) | 6.0 (6.3) |
  | 20M | wrap | 2 px | 77.8 | 12.8 (13.5, 13.9) | 12.8 (13.3) |
  | **30M** | wrap | 2 px | **50.7** | 19.7 (20.6, 24.9) | 19.7 (20.5) |
  | 40M | wrap | 2 px | 37.5 | 26.6 (27.9, 28.7) | 26.6 (27.7) |
  | 80M | wrap | 2 px | 18.5 | 53.9 (56.3, 57.7) | 53.9 (56.1) |
  | 40M | rgb | 2 px | 37.6 | 26.6 (27.8, 28.5) | 26.5 (27.6) |
  | 40M | wrap | 1 px | 65.5 | 15.3 (15.9, 16.4) | 15.2 (15.8) |
  | 40M | wrap | 3 px | 26.0 | 38.4 (40.0, 40.7) | 38.3 (40.0) |

  - **Throughput:** about 1.5G points/s at 2 px, linear in the budget. Wall time = GPU time + about 0.1 ms
    (Python, culling, 220 draw calls). The wrap shader costs the same as RGB.
  - **Fill rate dominates in this zoomed-out view** (many points per pixel): 40M at 1 px runs at 65 fps, at 3 px
    at 26 fps. Larger adaptive point sizes therefore need a smaller budget.
  - **VRAM:** 1.89 GiB for 99.97M points (20.3 B/point as designed). The 2.00 GB upload took 0.83 s (2.41 GB/s).
  - **Default budget: 30M** (50.7 fps worst case at 2 px, p95 20.6 ms).
  - Earlier run (2026-10-16), kept for the record: a Docker container (`curbnet-main-curbnet-1`, running
    `train_cylinder_asym_0.2.py`) held 15.4 GB of VRAM and the GPU at 100 %. Buffers beyond the about 0.4 GB left
    free were paged over PCIe (2–6 fps). The benchmark now refuses to run while another process holds the GPU.
- **Verdict:** every spike meets its target, so D-001 (stack) and D-002 (tiling) are kept. Development without
  the GPU uses Mesa llvmpipe (`gl: software` in settings; tests always use it). That was added 2026-10-17 because
  PCLabel shares the GPU with training runs.

## M1: import and view

SPEC §1, §2 (RGB, intensity, height, flat, class), ARCHITECTURE import / project / rendering.

- `python -m pclabel import <file> [--name] [--projects-dir]` and an import dialog in the app (progress, cancel).
- Project store, tile table, hashes, `project.json`, scan metadata panel.
- Viewer:
  - orbit / fly / BEV ortho / axis-aligned ortho; clip box;
  - EDL, adaptive point size;
  - point picking with an info panel;
  - async LOD streaming within the VRAM budget.
- Tests: reader edge cases, store round trip, tiles cover every point exactly once, source-order mapping.
- Manual:
  - import all five scans in `../data/`;
  - record import time and peak RAM;
  - record open time and fps on streets 1;
  - check no frame exceeds 100 ms while streaming (log frame times).

Try it: open streets 1 in BEV with EDL and intensity, find the junction, the zebra crossings and the islands
(OBSERVATIONS O-0004, O-0007), and drop nothing yet; just note what is hard to see.

Results (2026-10-17):

- **Built:**
  - `python -m pclabel import | info | [app]`;
  - the project store (`pclabel.project/1`, ARCHITECTURE);
  - the app: perspective, fly, BEV, front and side views; clip box; EDL; adaptive size; picking with an info
    panel; scan metadata; import dialog;
  - paged LOD streaming within a memory budget.

  Development without the GPU used `--gl software` (Mesa llvmpipe) at the user's request: the GPU was shared
  with a training run. The GPU checks below ran later, after confirming the GPU was idle (0 %, 430 MiB).
- **Tests:** 64 passed in 9.8 s (synthetic data only; GL tests on llvmpipe). They cover:
  - reader edge cases;
  - store round trip;
  - tiles cover every point exactly once;
  - source-order mapping with ground truth;
  - import cancel at every stage, no overwrite, source unchanged;
  - camera, LOD and picking;
  - colour modes, clip box, EDL, streaming in a budget;
  - UI wiring.
- **Import** (`python -m pclabel import`; one process each; peak RAM = peak working set, private commit in
  brackets):

  | scan | points | time | peak RAM | tiles | target |
  |---|---|---|---|---|---|
  | newroad 2 | 8.39M | 1.6 s | 0.57 GB (1.9) | 11 | ≤ 1 min |
  | ExchangeGreenFast 1 | 29.7M | 4.6 s | 1.18 GB (1.9) | 52 | |
  | ExchangeGreenSlow 1 | 59.8M | 10.1 s | 2.26 GB (2.1) | 77 | |
  | ExchangeGreen 1 (RTC360) | 257.3M | 40.4 s | 9.0 GB (3.6) | 351 | |
  | streets 1 | 633.6M | 110.4 s | 22.0 GB (6.4) | 754 | ≤ 30 min, ≤ 48 GB |

  - streets 1 stages: read 94.7 s, tile 6.0 s, reorder 9.7 s. The sha256 ran during the read, so finishing cost
    0 s.
  - The five projects take 22.8 GB in `C:\Users\desktopms\Documents\PCLabel_projects\`.
  - Every file's header `cartesianBounds` leave out some of its points (CONTEXT.md).
- **Open streets 1** (`bench/check_open.py`, 1920 × 1009, defaults):

  | GL | `project.json` | first frame | first points | every wanted page | target |
  |---|---|---|---|---|---|
  | GPU, 30M budget | 0.29 s | 0.29 s | 0.82 s | 1.46 s | ≤ 15 s to first frame |
  | llvmpipe, 300k budget | 0.24 s | 0.31 s | | 4.18 s | |

- **Navigation on streets 1** (GPU, default LOD; 1 px spacing draws 2–3.6M points, below the 30M budget). Each
  phase's first frame also includes the script's own work (a screenshot and a view switch: 124–306 ms) and is
  left out:

  | phase | frames (streaming) | wall ms median / p95 / max | over 100 ms | renderer CPU ms max |
  |---|---|---|---|---|
  | open | 381 (378) | 2.7 / 5.3 / 57.4 | 0 | 39.1 |
  | orbit 360° | 482 (42) | 3.6 / 4.8 / 7.5 | 0 | 5.6 |
  | BEV overview | 124 (0) | 3.6 / 4.5 / 7.2 | 0 | 3.9 |
  | zoom to the south kerb site | 240 (70) | 2.8 / 4.4 / 8.8 | 0 | 6.8 |
  | site in intensity + EDL | 124 (0) | 0.9 / 1.2 / 4.1 | 0 | 0.8 |

  **No frame over 100 ms while streaming.** On llvmpipe (300k budget) the medians are 33–35 ms (about 30 fps),
  p95 ≤ 58 ms, with one frame over 100 ms in each of the open and BEV phases while streaming (275–330 ms).
- **fps of the app renderer** (`bench/bench_render.py`, GPU, 99.97M synthetic points, every tile visible, the
  drawn count forced to the budget, EDL on, 2 px):

  | budget | 10M | 20M | **30M** | 40M | 80M | 30M, EDL off | 30M, adaptive size |
  |---|---|---|---|---|---|---|---|
  | fps | 216 | 100 | **65.8** | 48.5 | 24.2 | 65.4 | 83.1 |
  | wall p95 ms | 5.0 | 10.6 | 16.0 | 21.7 | 44.8 | 16.1 | 12.7 |

  - About 30 % faster than the M0 spike at every budget (geometry in one interleaved buffer per page). EDL costs
    nothing measurable.
  - Cold streaming: 99.97M points resident in 0.88 s; slowest frame 14.7 ms. On llvmpipe: 0.25M points at 16 fps,
    1M at 5.4 fps (hence the 300k software default).
- **Found and fixed during the checks:**
  - **Fixed-size pages:** one 256k-point page per visible tile meant 4 GB for the streets 1 overview. Pages now
    grow geometrically (16k, 16k, 32k, …, 256k).
  - **Stuck streaming:** a page evicted below a pending one was never requested again (regression test).
  - **Upload slice:** an already-spent upload slice uploaded nothing, so a frame could make no progress. Every
    frame now uploads at least one page (regression test).
  - **Settings not applied:** settings did not reach a renderer created after `python -m pclabel <project>`
    opened the project (regression test).
  - **Intensity barely visible:** every scan uses less than half of the stored intensity range (medians
    0.04–0.12, 99th percentiles 0.37–0.52), so the intensity mode got a range stretch (auto 1st–99th percentile).
- **Unexplained:** two early software-mode runs on streets 1 (before the paging fix) ended after about 21 s with
  exit status 127. There was no Python traceback and no Windows error event. Not seen in any later run.

## M2: see and note (height tools, observation pins)

SPEC §2.1, §3, the selection shapes of §4 (preview only, no labelling yet), §7 basic.

- Selection engine (full resolution, depth test, preview with counts).
- Flatten from selection (frames), reference surface, local relief.
- `z` / `h` / `hs` cycling; wrap, multi-period wrap, contours, diverging, band.
- Noise estimate from the flatten report, and the suggested period.
- Split view / swipe.
- Observation pins (`N`) with auto-captured context and screenshot; bookmarks; `observations.jsonl`; markdown
  export for `/ingest-observations`.
- Tests:
  - synthetic crossfall and grade recovered (±0.05 %);
  - synthetic kerb `hs` = step ± 5 mm;
  - the wrap formula mirror;
  - observation round trip (save → restore the exact view state).
- Manual: flatten a road patch on streets 1 and report its crossfall and grade; at P = 3 cm, check that drains
  (O-0002) and kerbs stand out.

Try it:
- Flatten a 10 m road stretch.
- Wrap at 3 cm, then 1 cm, then multi-period.
- Switch to diverging ±5 cm and look for everything below the road.
- Press `N` on each thing you cannot explain.

Results (in progress):
- **2026-10-19, at the user's request after first use of M1:**
  - **Built:** lasso and rectangle selection; robust plane fit (`F`) as the height axis; the height slice
    (`Shift+H`); `frames.json`.
  - **Navigation fixes:** rotation now pivots at the centre of the view without moving the camera, so a drag no
    longer jumps; Home resets the view; there is a toolbar.
  - SPEC §1, §2.1, §3, §4 and §15 mark what is built. 78 tests pass in 12 s.
- **Synthetic:** a one-plane road with 1.00 % grade and 2.50 % crossfall is recovered as 1.0009 % and 2.4994 %
  (target ±0.05 %), RMS 0.6 mm (range noise 2 mm along the ray). The result is unchanged with 20 % of kerb,
  pavement, grass, tree and drain points mixed in.
- **streets 1, south kerb site** (BEV, x 25, y −24; GPU while a training run used the card):
  - A 100 × 100 px rectangle on open road selected 3,195 points in 0.15 s. The fit: tilt 2.36°, RMS 1.0 mm, grade
    3.16 % along azimuth 11.6°, crossfall 2.65 % (3,149 kept).
  - Wrap at 3 cm in that frame turns the dense z bands on the road into a few broad ones. One sharp straight
    colour edge crosses the road, a height step (a resurfacing joint or patch edge?).
  - The −10…+30 cm slice removes the tree canopy and shows the road under it.
  - A first box placed at the view centre fell on the canopy edge: tilt 85°, RMS 0.46 m. Such fits now carry a
    "does not look like ground" warning.
- **2026-10-19, second round (user requests):**
  - **Views:** top, bottom (new), front and side are angles of the perspective camera, animated, and `T` /
    `Shift+T` toggle back. Orthographic is a separate switch.
  - **Ground grow (`G`):** SPEC §11's region grow, built early on a cell grid, on height and intensity.
  - **Planes:** `F` reuses an identical plane; planes can be deleted.
  - 89 tests pass in 16 s.
- **Ground grow, measured:**
  - synthetic street (20 cm cells): 98.5 % of road points and 97.6 % of marking points selected, 0.05 % of the
    selection not road (kerb and drain-lip points at road level);
  - with the intensity checks off it leaks over the lowered kerb onto 436k pavement and 261k grass points;
  - streets 1, south kerb site, seed on the road (GPU, app, defaults): 6.95M points on 279 m² (27,047 cells
    grown, 824 bridged as markings) in 1.4 s. It stops at both kerbs and includes the dashed lane lines;
  - a re-grow after changing the intensity step to 0.01 took 0.35 s on the cached cells (6.80M points);
  - before gap-stepping, 10 cm cells stalled on far road where scan lines are more than 10 cm apart.
- **Not built yet:** the remaining M2 items (rotating the view into the frame, reference surface and `hs`, local
  relief, multi-period wrap, contours, diverging, noise-guided period, split view, observation pins and
  bookmarks).

## M3: label

SPEC §4 (applying classes, filters, locks, flags, instances basic), §1 provenance, ARCHITECTURE journal / exports.

- Taxonomy loading (palette, hotkeys, enabled/disabled), class colour modes, show/hide/solo.
- Apply / erase / flags; filters and locks; rule context chip.
- Journal, undo/redo, snapshots, crash recovery; journal panel; per-point provenance.
- Assessed regions (draw, status per class), as required by R-001.
- Exports: `.npy` + sidecar, LAS, PLY, collapsed export.
- Tests:
  - label → export round trip against synthetic ground-truth indices;
  - crash recovery (kill between journal steps);
  - undo/redo invariants;
  - locks never overwritten.
- Manual: label a 20 m stretch of road, curb and pavement on newroad 2; time it; then do the same on streets 1 and
  check the lasso and undo timings against the targets.

Try it: label one stretch end to end and set `ambiguous` on anything unsure. Each `ambiguous` should get an
observation tagged `decision-needed`.

Results:
- (fill in)

## M4: cross-sections and measurement

SPEC §5, §6.

- Profile dock, section by line or by `X`, stepping along a polyline, labelling in the profile.
- Two-plane step measurement with an "insufficient" result; profile strip export.
- Point / plane / step / spacing measurements; attach to observations.
- Tests: synthetic kerb 0.150 ± 0.005 m; occluded faces give "insufficient".
- Manual: measure the kerb steps of O-0003 and the lowered kerbs of O-0004 at full resolution, and update those
  observations.

Try it: step along the south kerb at 0.25 m (x 15..35, y −30..−18) and note every station where the profile changes
shape.

Results:
- (fill in)

## M5: the standard workflow

SPEC §7 full, §8, §9.

- Observation browser (filters, restore view), decision queue view, session log.
- Patrol mode with per-station status; coverage records.
- Query bar (whitelisted AST); derived fields computed lazily.
- Label lint from the rules' `check:` lines; hit clusters with fix / counterexample actions.
- Tests: query parser safety and correctness; lint over a synthetic scene with planted violations.

Try it: run the lint after a labelling session; every hit is either a fix or a new observation.

Results:
- (fill in)

## M6: features, statistics, threshold explorer

SPEC §2 (feature colour modes, intensity contrast, BEV rasters), §10.

- Features with explicit parameters and caching: normals, roughness, step, density, intensity contrast, range and
  incidence.
- Class statistics, histograms, the threshold explorer with "show errors in 3D", the 2-feature scatter.
- Threshold report export.
- Tests: on synthetic data, the explorer finds the planted separating threshold.

Try it: road vs pavement on intensity, curb vs road on `hs`, grass vs shrub on roughness. Write up every overlap
you cannot explain.

Results:
- (fill in)

## M7: assisted tools

SPEC §11.

- Band select, region grow, GPF ground, road boundary → curb candidates, connected components / instances, pole
  helper.
- Correction tracking and the per-tool precision/recall report.
- Tests: each tool against synthetic ground truth with known parameter sensitivity.

Results:
- (fill in)

## M8: comparison and consistency

SPEC §12.

- External per-point overlays and confusion maps; blind re-label with a disagreement report; side-by-side projects
  (ExchangeGreen RTC360 vs BLK ARC).

Results:
- (fill in)

## M9: vector features (later)

SPEC §13 (curb toe/lip polylines, pole points, local JSON + DXF).

## M10: speculative extras

SPEC §14 (measurement report, sight-line check). Only if the user asks.
