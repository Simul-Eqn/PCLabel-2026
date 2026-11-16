# results

Measured numbers, by topic. Each gives the scan, the date and how it was measured. "In the app" means a one-off
measurement in a session with no script kept; such a number holds for the code of its date and is re-measured
before it is relied on. Observations with measurements are in [OBSERVATIONS.md](OBSERVATIONS.md).

Machine: i9-14900KF (numba 32 threads), 128 GB RAM, RTX A4000 16 GB (driver 582.16, OpenGL 4.3 core). Python
3.12.15, PySide6 6.12.0, moderngl 5.13.0, numba 0.68.0, numpy 2.5.4. The GPU is shared with training runs: render
numbers are from runs with the GPU idle unless marked.

## reading and importing scans

`bench/bench_read.py`, 2026-10-16/17, each case in a fresh process; peak RAM = peak working set.

| file | reader | records | time | rate | peak RAM |
|---|---|---|---|---|---|
| newroad 2 | one buffer | 8.39M | 0.58 s | 14.5M/s | 0.31 GB |
| newroad 2 | chunked 4M | 8.39M | 1.04 s | 8.1M/s | 0.55 GB |
| ExchangeGreenFast 1 | one buffer | 29.7M | 2.46 s | 12.1M/s | 0.92 GB |
| ExchangeGreenFast 1 | chunked 4M | 29.7M | 3.02 s | 9.8M/s | 0.87 GB |
| streets 1, first 50M | chunked 4M | 50.0M | 6.57 s | 7.6M/s | 0.49 GB |
| streets 1 | chunked 4M | 633.6M | 78.8 s | 8.0M/s | 0.49 GB |

libe57 decoding is the limit (12–14M records/s on one thread); conversion runs at 60–67M/s. All files: one scan,
identity pose, xyz as 10 µm scaled integers, float intensity 0..1, 8-bit RGB.

`python -m pclabel import`, 2026-10-17, one process each (private commit in brackets):

| scan | points | time | peak RAM | tiles |
|---|---|---|---|---|
| newroad 2 | 8.39M | 1.6 s | 0.57 GB (1.9) | 11 |
| ExchangeGreenFast 1 | 29.7M | 4.6 s | 1.18 GB (1.9) | 52 |
| ExchangeGreenSlow 1 | 59.8M | 10.1 s | 2.26 GB (2.1) | 77 |
| ExchangeGreen 1 | 257.3M | 40.4 s | 9.0 GB (3.6) | 351 |
| streets 1 | 633.6M | 110.4 s | 22.0 GB (6.4) | 754 |

streets 1: read 94.7 s, tile 6.0 s, reorder 9.7 s; the sha256 runs during the read. The five projects take 22.8 GB.

## opening and navigating

`bench/check_open.py`, streets 1, 2026-10-17, 1920 × 1009, defaults:

| GL | `project.json` | first frame | first points | every wanted page |
|---|---|---|---|---|
| GPU, 30M budget | 0.29 s | 0.29 s | 0.82 s | 1.46 s |
| llvmpipe, 300k budget | 0.24 s | 0.31 s | | 4.18 s |

Navigation on streets 1, GPU, 2026-10-17 (in the app, frame times logged): median wall time 0.9–3.6 ms per frame
over open, a 360° orbit, the overview and a zoom; no frame over 100 ms while streaming. On llvmpipe (300k): medians
33–35 ms, one frame of 275–330 ms in each of the open and overview phases.

## rendering

`bench/bench_render.py`, 2026-10-17, 99.97M synthetic points in 220 tiles, every tile visible, drawn count forced to
the budget, EDL on, 2 px:

| budget | 10M | 20M | **30M** | 40M | 80M | 30M, EDL off | 30M, adaptive size |
|---|---|---|---|---|---|---|---|
| fps | 216 | 100 | **65.8** | 48.5 | 24.2 | 65.4 | 83.1 |
| wall p95 ms | 5.0 | 10.6 | 16.0 | 21.7 | 44.8 | 16.1 | 12.7 |

- 30M is the default budget. EDL costs nothing measurable; fill rate dominates when zoomed out (the first render spike: 40M
  at 1 px 65 fps, at 3 px 26 fps).
- VRAM: 20.3 B/point (1.89 GiB for 99.97M); cold streaming of 99.97M in 0.88 s, slowest frame 14.7 ms.
- llvmpipe: 0.25M points at 16 fps, 1M at 5.4 fps (hence the 300k software default).
- With another process holding the GPU at 100 % and 15.4 GB of VRAM, buffers were paged over PCIe (2–6 fps): the
  benchmark refuses to measure while the GPU is busy.

## selection engine

`bench/bench_select.py`, 2026-10-16, numba point-in-mask over 99.97M points, a 1920 × 1080 lasso covering 14.6 %
of the screen, median of 7 runs: 0.090 s (1,114M points/s); with the depth test 0.107 s (935M/s). Rasterising the
polygon: 0.7 ms.

In the app, streets 1, GPU, 2026-10-20:
- a rectangle over the middle third of the overview: *visible* 16.5M points in 1.2 s; *through* 443.6M points (70 %
  of the scan) in 4.5 s. The UI thread is held 1.2 s for that selection, 1.7 s to add a second, 0.6 s to undo.
- "select points" on a 38.7M-point directory: 0.54 s with `sets.union_all` (24 s with numpy's `unique`).

## selection tools

In the app on streets 1, GPU, unless named otherwise.

| tool | case | result | time | date |
|---|---|---|---|---|
| grow, height and intensity | synthetic street, 20 cm cells | 98.5 % of road and 97.6 % of marking points; 0.05 % of the selection not road | | 2026-10-19 |
| grow, height and intensity | synthetic street, intensity tests off | leaks over the lowered kerb: 436k pavement and 261k grass points | | 2026-10-19 |
| grow, height and intensity | south kerb site, seed on the road | 6.98M points on 309 m², stops at both kerbs, lane lines included; stopped by height 3,355 cells, intensity step 504, gap 479, roughness 84, intensity vs road 21 | 0.56 s first click; 0.36 s re-grow | 2026-10-19 |
| grow, geometric | road at the magic-select site | 7.39M points (1.05M voxels from 47M points), stops at the kerb face | 2.3 s; re-run 0.65 s | 2026-10-20 |
| magic select | synthetic street (1M points), a centre dash | all of the dash, 99.4 % of the selection marking; `contiguous` off: every dash and the yellow lines, 88 % marking, nothing from pavement or grass | | 2026-10-19 |
| magic select | near dash (22.85, −11.09) | 6,766 points on 0.22 m², accepted intensity 0.10–0.40 | 0.70 s incl. a picture of 176M ground points | 2026-10-19 |
| magic select | far dash (32.41, −29.03), scan lines ~10 cm apart | 175 points, the whole dash | 0.40 s; re-runs 0.06–0.15 s | 2026-10-19 |
| magic select | `contiguous` off from the far dash | 52,894 points on 16.9 m², incl. bright patches under trees beyond the kerb | 0.15 s | 2026-10-19 |
| 3D select | 1 m cylinder / 6 × 3 m box turned 30° | 107,891 / 645,293 points | 0.16 s / 0.05 s | 2026-10-20 |
| extrude select | 10 m loop, 20 cm inset / 40 m loop | 31.7M / 212M points | 0.49 s / 2.8 s | 2026-10-20 |
| clip to a volume | 4 m cylinder | 23.6M points drawn | ~28 ms GPU per frame | 2026-10-20 |
| clip to point sets | `bti_pavement` (26.0M points) | frame cost 1.5 ms GPU; grow from a seed on it 25.4M points, all in the set (unclipped 190.6M, 13 % in the set); geometric grow 20.1M (unclipped 36.5M, 54 % in the set) | clip 28 ms; grow 0.58 s (unclipped 3.33 s); geometric 0.70 s (2.53 s) | 2026-10-20 |
| clean up | curve 11 as a loop, inset 5 cm, −0.10 to +0.05 m (27.3M points) | would add 739k and remove 15 specks | 3.3 s | 2026-10-20 |
| pieces | `bti_pavement` (9.8M) / `bli_outermask` (34.5M) / `bottom_road_cleaned` (121.1M) | 2 pieces / 3,043 (2,961 small, 34,637 points) / 327 small (6,946 points) | 0.24 / 0.67 / 2.01 s | 2026-10-21 |
| density | `bottom_road_cleaned` (121.1M) | the dense part (≥ 2,000/m²) holds 99.5 % of the points on 1,168 of 2,250 m²; 2,000/m² is reached about 43 m from the scanner on the bottom road | 1.09 s; 0.61 s per change | 2026-10-21 |
| split | 6 m cylinder, 4.83M points | verge rough, road smooth; points to look at along the kerb edge and marking edges | 0.61 s; ~0.1 s per change | 2026-10-20 |

## voxel tools

`python bench/bench_voxels.py "streets 1"`, 2026-11-16, at the magic-select site (22.85, −11.09), median of 9 runs:
geometric grow from the road (voxels built each run) 1.90–1.94 s, 7.39M points, 1.05M voxels; pieces of a 6 m
cylinder (5.78M points, 10 cm gap) 0.090–0.103 s, 36 pieces; split features of that cylinder 0.49–0.50 s.

## planes

- Synthetic road with 1.00 % grade and 2.50 % crossfall recovered as 1.0009 % and 2.4994 %, RMS 0.6 mm; unchanged
  with 20 % of other classes mixed in (`tests/test_frames.py`).
- streets 1, south kerb site, a 100 × 100 px rectangle (3,195 points), 2026-10-19: tilt 2.36°, RMS 1.0 mm, grade
  3.16 % along azimuth 11.6°, crossfall 2.65 %.
- streets 1 plane 15 (exported to `PCLabel_projects/streets 1 plane 15.json`), 2026-10-19: fitted on 1.89M of 38.7M
  points, RMS 13.5 mm, tilt 1.82°, grade 0.56 %, crossfall −3.12 %.

## annotations, maps and imports

- Labels per point of streets 1 (`python -m pclabel labels "streets 1" <out>`, 2026-11-16; 196 point sets, 866M
  point references): 13.0 s. 172.0M points have a class (road 142.2M, pavement 18.9M, road_line 9.9M, tree_trunk
  517k, other 145k, other_pole 49k, lamp_post 39k), 1.84M carry `exclude`, 462.0M are unassessed. 4,156 points lie
  in both an `other` set and an `other_pole` set (a conflict across branches).
- Label map of streets 1's labelled sets (15M points, 6 lines), 2026-10-28, `tools/labelmap.py` without writing the
  PNG: 4,860 × 5,242 px at 5 cm in 0.97 s; 12,150 × 13,104 px at 2 cm in 1.90 s; 8.1 s with every other point as
  grey context. With the 32 unlabelled sets (848M point references) ticked, 22 s.
- Annotations export of a 6,766-point set and two lines: 250 kB in 0.01 s; import 0.55 s (2026-10-19).
- DXF import of `mobile-colorized-v2-1.dxf` (7.3 MB, 1,021 entities, 40,326 vertices, 31 layers) onto its project,
  2026-11-04: read in 0.38 s, tree built in 0.04 s, 2.1 MB of annotations; every vertex inside the scan; 1,015 of
  1,021 features labelled by the ROCK profile. 413 of 422 LWPOLYLINEs are tilted (read as plain x, y they land
  kilometres away). Where scan points lie within 5 cm (xy) of a vertex (36 % of vertices), the nearest point's
  height is within 1 cm for 90 % of the ground layers' vertices; below TOP_OF_CURB the lowest nearby points are a
  median 14 cm lower.

## road tools

In the app unless named otherwise; lines compared are the user's, read only.

- **Curb tracer, synthetic street** (2M points, `tests/`): the north kerb traced 16 m in 0.13 s in three pieces
  broken at the two drains, toe within 3 cm, lip within 2 cm, height 0.149 m (true 0.15); the south kerb (facing
  away): toe the last road point seen, lip within 3 cm, height 0.147 m.
- **Curb tracer, streets 1** (2026-11-05): 20 starts 0.2–0.5 m from the user's curb lines: 9 reached the 30 m test
  limit, the others stopped at lowered kerbs (3 found no step: at most 4 cm of rise); lip within 1–3 cm of the
  user's lines; kerb heights 0.12–0.19 m.
- **Curb tracer, ExchangeGreen 1** (2026-11-05): with the 0.10 m curb height no kerb was found (the kerbs step
  8–10 cm, O-0016); with 0.06 m every start where the kerb was scanned traced to the 10 m test limit. Replaying the
  user's 21 logged runs from `trace_log.jsonl`: 334 steps (90 m) as they ran, 404 (109 m) after the tight-curve
  changes, 544 (148 m) following the top edge only.
- **Curb tracer, `mobile-colorized-v2-1`** (595M points, 2026-11-05), started 0.3 m beside 30 of ROCK's
  BOTTOM_OF_CURB lines: 182 m in 1.6 s; toe a median 6.5 cm (xy) and 3 mm (height) from ROCK's bottom line, lip
  5.0 cm and 4 mm from its top line; kerb 0.142 m (p10–p90 0.131–0.152); about 10 % of steps more than 4 m from any
  ROCK line (edges ROCK did not draw).
- **Marking tracer, synthetic street**: dash ends within 0.1 m; a double line's two lines (0.10 m wide, 0.15 m
  apart) traced separately at y 3.04 and 3.29 (true 3.05, 3.30).
- **Join, ExchangeGreen 1** "curb trace 01" and its wraparound (2026-11-05): 22 lip pieces (91 m) into 10 lines, 22
  toe pieces into 9, bridging 10 gaps of 0.1–0.7 m (mostly 0.6 m drains).
- **Curves for traced lines, ExchangeGreen 1** (2026-11-06): the joined kerb lines are 3–35 cm off any one plane (a
  25.5 m kerb top rises 1.18 m); within 1 cm a curve takes 36–72 nodes for 19–27 m (64–89 traced vertices); within
  2 cm, 21–45.
- **Smooth, ExchangeGreen 1** (22.8 m `curb_bottom`, 19.4 m `curb_top`, 2026-11-06): 1 m / 5 cm took the turning
  from 683° to 512° and 435° to 384°; 1 m / 10 cm to 353° and 324°; 0.5 m / 3 cm only to 630° and 397°; ~20 ms a
  line.
- **Smooth onto the kerb top, ExchangeGreen 1** (2026-11-06): `tli_right` (103 m, hand-drawn) 263 kerb tops
  measured, the line within 1.2 cm rms of them, moved 2.6 cm on average (5.5 at most), 10 stretches carried across
  (longest 6.3 m); `curb_top 1` (46.6 m, traced) 134 measured, 1.4 cm, moved at most 7.5 cm, turning 538° to 260°;
  `bi_curb/curb_top 1` (200 m) 371 measured, 1.0 cm, 26 stretches carried across, at most 42 cm moved where the
  drawn line spiked 30 cm up; 0.5–2.5 s a line. On `lip 1`, 112 of 301 sections saw only the kerb's face.

## trees and markers

See O-0012 (a leaning tree), O-0014 (the mean of a trunk band sits toward the scanner) and O-0015 (ROCK's symbols
sit at the foot). The tree tool on `tree_example` (streets 1, 2026-10-26): band of 47,527 points, girth 1.20 m,
0.9 cm rms.
