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
- **2026-10-19, third round (user report: speckled selection, straight cut lines across the road):**
  - **Causes found:**
    - The cell grid sampled per tile. Dense tiles near the scanner were read at a few percent, leaving the
      sparse road inside them with too few points per cell: holes, and cut lines along the straight tile edges
      (diagonal across a diagonal road).
    - The highlight drew a random 1M of the selected points, which looked speckled.
    - The seed-relative intensity check could cut the road where intensity drifts with range.
    - The z axis was not a cause: neighbouring cells 10 cm apart differ by about 3 mm on a 3 % grade, against a
      2 cm step.
  - **Fixed:**
    - points are capped per cell (48);
    - selection is a GPU-side bit (exact tint);
    - intensity is compared with a running average of the grown road (2 m memory);
    - gaps up to 1 m are crossed with an 8 % slope allowance, so kerbs behind no-point strips still stop it;
    - the panel counts what stopped the grow and the view marks those cells by colour.
  - 94 tests pass (new: dense tile with a sparse part; drifting road beside a brighter walkway; pole shadow vs
    kerb behind a gap; exact tint; pages packed after the selection).
- **streets 1, south kerb site, same seed, GPU:**
  - 6.98M points on 309 m² (283 m² before the per-cell cap), 2.4 s for the first grow (grid 2.1 s, reading
    every nearby point).
  - Stopped by: height 3,355 cells, intensity step 504, gap 479, roughness 84, intensity vs road 21.
  - The kerb shows as a continuous red line of height stops. A shadow wedge beside the seed that had been
    sampled away is now included.
  - A re-grow with intensity step 0.01 took 0.36 s (6.82M points).
- **2026-10-19, fourth round (user request: an intensity magic select for road markings):**
  - **Built:** magic select (`I`, SPEC §11).
  - **Why a factor:** on streets 1 the intensity of paint spreads with its level. Over 2 cm pixels of the ground
    layer:
    - road is 0.03–0.10 everywhere;
    - a near dash is 0.15–0.25 (12 points per pixel);
    - a far dash is 0.13–0.55 (1–2 points per pixel);
    - the left dash is 0.10–0.75 (5 points per pixel).

    A fixed ±0.10 around the click took 17 pixels of the far dash. ×2 takes all of it and no road.
  - **Scan-line gaps:** stepping over empty pixels along 8 directions joined only 73 pixels of the far dash, whose
    pieces lie at other angles. A nearest-data fill up to half the gap joins all of them.
  - **Speed:** the cell grid and the picture now read tiles in parallel (32 threads, merged in tile order, so
    results are unchanged).
    - A 45 m picture of 183M points builds in 0.36 s (was 2.5 s).
    - The ground grow's first click at the south kerb site takes 0.56 s (was 2.4 s), with the same 6,976,938
      points.
  - 103 tests pass (new: a dash and only it; not contiguous takes the other markings on the road only; the
    tolerance factor; a reused picture gives the same result; scan-line gaps; a fading line; kerb paint not
    climbed; the app's click and Shift+click).
- **streets 1, magic select, GPU** (south kerb site; the app; defaults: ×2, gap 20 cm, 2 cm pixels, reach 15 m):
  - near dash (world 22.85, −11.09): 6,766 points on 0.22 m² (561 pixels; about 2.0 × 0.11 m) in 0.70 s,
    including building the picture from 176M ground points. Accepted intensity 0.10–0.40.
  - Shift+click a far dash (world 32.41, −29.03), about 10 cm between scan lines: 175 points on 0.20 m², the
    whole dash, in 0.40 s.
  - `contiguous` off from that dash: 52,894 points on 16.9 m² in 0.15 s (picture reused). It takes the centre
    dashes and the edge line pieces, and also bright patches under the trees beyond the kerb, which join the road
    without a height step.
  - A block of bright bars about 20 m away (between trees): one click took its two brightest bars, merged where
    the 20 cm gap fill bridged the sparse strip between them. The dimmer bars stayed out (×2 of 0.48 starts at
    0.24).
  - On the 300k-point UI test scan, a dash 9 m from the scanner has 50 points up to 25 cm apart. There the fill
    breaks it into pieces whatever the gap: such data needs a click per piece.
- **2026-10-19, fifth round (user request):** segments, curves with editable gradients, and groups of segments,
  curves and point sets, with export and import (SPEC §13, built early from M9).
  - 112 tests pass in 21 s. New tests:
    - the plane perpendicular to a tilted axis, at the mean height;
    - the curve through its nodes, straight for collinear nodes;
    - a dragged handle sets the gradient;
    - group editing; the project copy; export and import (also into another scan); source-to-store mapping;
    - in the app: segment, curve, a real mouse click on a node and a drag of its handle, Ctrl+G, merge, select
      points, export and import, Esc.
  - A UI test waited for two frames after switching the slice on, but the first frame is already converged and
    the view rightly stops there. It now waits for one (30 s faster).
- **streets 1, groups, GPU** (the app; at the magic-select site):
  - The near dash was magic-selected and grouped with Ctrl+G (6,766 points, with its magic-select record).
  - A 3.00 m segment across the lane.
  - A 5-point curve beside the dash. The active axis was a fitted plane ("plane 14"); the clicked points were up
    to 3.2 cm from the fitted curve plane. A drag of node 3's handle bent the curve, now 10.86 m long.
  - Merged into one group; it draws in one colour, thicker when selected.
  - Export: 250 kB in 0.01 s. Importing it back took 0.55 s and gave the same 6,766 store indices.
  - The test groups were removed from the project afterwards.
- **2026-10-19, sixth round (user requests):**
  - **Curves:** points can be added (Shift+click; placed where the curve passes closest) and removed (Ctrl+click,
    Del). The plane height is refitted after each.
  - **Directories:** groups became directories of items. Items stand alone; a directory colours everything in it.
    Schema `pclabel.annotations/1`, with no reading of the old groups format (the user did not need it).
  - **Plane export** (`pclabel.plane/1`).
  - 115 tests pass in 21 s. New tests: insert and remove order; the directory tree (colours, moves, no cycles);
    the project copy with nested directories; export of selected entries; the plane equation; and in the app,
    Shift+click and Ctrl+click on a curve, Ctrl+G, directories, plane export.
- **streets 1:**
  - The user's groups were converted once to `annotations.json`, with no loss:
    - segment 1 (165 m) and curve 5 (9 points) became items;
    - "points 2" became a directory of its two point sets, 17,150,184 and 21,527,293 points, identical to the
      originals.

    `groups.json` and `groups/` were left in place (the app no longer reads them).
  - Plane 15 exported for the user to `PCLabel_projects/streets 1 plane 15.json`:
    `0.009425881 x + 0.030288819 y + 0.999496743 z + 1.219019800 = 0`. The fit was on 1.89M of 38.7M selected
    points, RMS 13.5 mm, tilt 1.82°, grade 0.56 %, crossfall −3.12 %.
- **2026-10-19, later (user requests):**
  - A bug fix: dragging into directories and deleting failed once an item followed another of its kind
    (dataclass equality compared numpy arrays). Nodes now compare by identity, with a regression test.
  - Right drag rotates like left drag; fly needs a W A S D Q E key while the right button is held.
  - Undo and redo (`Ctrl+Z`, `Ctrl+Y`) for the selection, planes and annotations (SPEC §4).
  - 121 tests pass in 22 s.
- **2026-10-20 (user requests):**
  - Labelling by labels on annotation directories and items, and "export labels" grouped by label (D-007).
  - A second curve plane mode, now the default: through the start and end points, turned to fit the rest.
  - 124 tests pass in 25 s. New tests:
    - a tilted plane recovered through exact ends (3 mm noise);
    - the two-point case;
    - a new end added;
    - switching modes, also through undo in the app;
    - inherited labels and the by-label export with overlaps.
  - Later the same day:
    - 3D select (`U`): a box or cylinder along the height axis, moved with W A S D Q E, selected with Enter;
    - locks on labels;
    - `other` as "no class", never selected (taxonomy `selectable: false`, version 0.2).

    Then lasso and rectangle's second depth mode, *through* (`O`).
    - streets 1, GPU, a rectangle over the middle third of the overview: *visible* took 16.5M points in 1.2 s;
      *through* took 443.6M points (70 % of the scan) in 4.5 s.
    - At that size numpy's union1d / setdiff1d / setxor1d (they sort again) would hold the UI for tens of
      seconds. Selections now combine with one linear merge (`tools/sets.py`), and the renderer finds the tiles a
      change touches by tile, not by point. The UI thread is now held 1.2 s for that selection, 1.7 s to add a
      second one, and an undo takes 0.6 s.
    - 135 tests pass in 25 s.
    - Then closed loops (Shift+Enter, or the box in edit mode) and extrude select (3D select's "loop" shape, with
      an exact inset), which the user wants for taking pavement inside a kerb loop. On streets 1, a 10 m loop with
      a 20 cm inset took 31.7M points in 0.49 s. 144 tests pass in 25 s.
    - Then joining two curves at chosen ends (SPEC §13 step 5). 150 tests pass in 26 s.
    - Then a third curve plane mode, "fit all points", and "show clicked points" in curve edit mode. 152 tests
      pass in 28 s.
    - "Select points" on the user's 38.7M-point directory took 24 s, all of it numpy's unique over the
      concatenated sets. They are sorted already, so they are now merged (`sets.union_all`): 0.54 s.
    - Ground grow became Grow select, with a second, geometric mode (normals, after PCL's region growing): 7.39M
      road points in 2.3 s on streets 1, stopping at the kerb face. 156 tests pass in 29 s.
    - Split by roughness (SPEC §11): features, a threshold (Otsu's on request), a clean-up, and points to look at
      in magenta; then two point sets. A 4.83M-point area of streets 1 took 0.61 s. 159 tests pass in 32 s.
    - Clipping to any 3D select volume (box, cylinder, loop) on the GPU, with "clip live", and more vertical lines
      round the volume. A 4 m cylinder clip on streets 1: about 28 ms GPU per frame. 161 tests pass in 36 s.
    - Clean up the selection (footprint: points just over or under the selected surface, specks out). On the user's
      island case it would add 739k points and take out 15, in 3.3 s.
    - Also merging point sets, the clips respected by every selecting tool, and one more decimal in the panels.
      165 tests pass in 38 s. A loop's centre had counted the closed
      ring's repeated point twice (1 cm off on a 2 m circle); fixed, and tested.
    - Locks can be switched off as a whole (the choice kept), and `other` unlocked like any label. Set operations on
      point sets (∪, ∩, A \ B, B \ A, △; directories count as all their point sets) replace "merge points"; the
      result is a new point set or the selection. 166 tests pass.
    - Clip to point sets (`Ctrl+K`): only the set is drawn and picked, and every tool reads only its points. On
      streets 1 (GPU) clipping to `bti_pavement` (26.0M points) took 28 ms and left the frame at 1.5 ms GPU; grow
      select from a seed on it took 25.4M points (all in the set) in 0.58 s, against 190.6M in 3.33 s
      unclipped. 175 tests pass.
    - Density (2026-10-21): the parts of a point set dense enough to work on, default 2,000 points/m² from
      O-0011 (thinned lane dashes and the user's own clicks; figure `docs/figures/density_threshold.png`). On
      `bottom_road_cleaned` (121.1M points) counting took 1.09 s and each threshold change 0.61 s, in a worker:
      the dense part holds 99.5 % of the points but only 1,168 of 2,250 m² of the road. Also flags as labels
      (`marking` for road lines, R-006; label export schema 2) and Ctrl+Z in number fields undoing the app.
    - Pieces (`P`): keep the clicked connected piece of the selection, the largest, or drop the small ones (stray
      points of a 3D select). On streets 1: 9.8M points in 0.24 s, 121.1M in 2.01 s (327 small pieces in
      `bottom_road_cleaned`).
    - A `ramp` class (taxonomy 0.3) and uncertain labels (the tree's "unsure" box and an "uncertain" button;
      annotations schema 2, label export schema 3; schema 1 annotations still read). Since 2026-10-26 only items,
      not directories, can be uncertain (annotations schema 3, label export schema 4).
    - The tree tool (`Shift+U`, R-015 proposed): click the ground at a trunk; a cylinder 1.00–1.10 m above it;
      Enter; "save the selection as a tree" makes `tree NN` (in `trees`, or a chosen directory) with the band and
      its circle fit (position at
      the ground height, girth). 3D select's keys now also work while one of its number fields has the focus.
      On `tree_example` (streets 1) the band's fit: 47,527 points, girth 1.20 m, 0.9 cm rms.
    - Annotations (2026-10-26 to 28): Ctrl+G into the selected directory (Ctrl+Shift+G: the top level); duplicate
      (Ctrl+D); F2 renames anything wherever it is; planes can be renamed; Ctrl+Z, Ctrl+K, Ctrl+D no longer go to
      3D select's Z, K, D keys while it is the tool.
    - Labels view (`B`) and label map (`Shift+B`, 2026-10-28): point sets, segments and curves in their labels'
      colours, a legend with colours saved per project (annotations schema 4), and the labels projected onto a
      plane as PNG and JSON (`pclabel.label-map/1`). On streets 1 the map of the labelled sets (15M points, 6
      lines) took 0.97 s at 5 cm (4,860 x 5,242 px), 1.90 s at 2 cm, 8.1 s with every point as grey context.
      201 tests pass.
    - `road_line` class (2026-10-28, taxonomy 0.4, id 10): part of `road` through the new taxonomy key
      `subset_of`; label export schema 5 counts the road with its lines and reports line points outside the road.
      R-006 rewritten, still proposed.
    - Export annotations by label (2026-10-29, the Labels panel): the items with the ticked labels, in their
      directories, as an annotations file (schema 5 adds `labels_exported`). 203 tests pass.

    On streets 1 (GPU) at the magic-select site:
    - a 1 m cylinder took 107,891 points in 0.16 s;
    - a 6 × 3 m box turned 30° took 645,293 points in 0.05 s.
  - streets 1's current annotations (6 curves, 3 directories, 2 point sets) load unchanged. The older curves
    read as "perpendicular to the height axis", as drawn. Curve 12's clicked points lie up to 23 cm off its
    horizontal plane: a candidate for the new mode.
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

Built early (2026-10-19, see M2 results): segments; curves with gradients on a plane perpendicular to the height
axis, with points added and removed in edit mode; point sets; a tree of directories; their JSON export and import.
Still to do: snapping, attributes, DXF, candidates from point labels.

## M10: speculative extras

SPEC §14 (measurement report, sight-line check). Only if the user asks.
