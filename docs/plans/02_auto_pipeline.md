# Plan 02: the automatic pipeline

Written 2026-11-16 in a planning session, for the maintainer who will build it. Facts about the code are from
commit `29f6472`; facts about the labelled projects are from the projects directory on that day. Where the code
contradicts this plan, trust the code and note the difference in your report.

Read [01_cleanup.md](01_cleanup.md) first. Its part A (the labelling standard) decides which labels the pipeline
writes, and its steps marked "before the pipeline" create the Qt-free layer this plan builds on.

## 1. Goal

One command takes a terrestrial laser scan of a Singapore street scene and writes a vector map of it, with no
clicks:

1. **curb lines** (highest priority),
2. **road markings** (highest priority),
3. **trees and poles** as a 2D map: one marker per object, plus the trunk cross-section for trees,
4. later: road, pavement and vegetation as point sets.

The user's route to this is the one they have followed so far: every assisted tool in the app needs a click
today, and the pipeline replaces the click with a computed proposal. The app stays. It makes the reference labels,
shows the pipeline's output beside them, and is where the user corrects that output. A corrected pipeline run is
also the fastest way to label a new scan, so each improvement pays twice.

## 2. What done looks like

```
python -m pclabel auto "streets 1"            # run every stage, write a run directory
python -m pclabel auto "streets 1" --stages ground,curbs
python -m pclabel score "streets 1" --run <run id>   # compare a run with the hand labels
python -m pclabel auto path\to\scan.e57       # import first if needed, then run, then export DXF + GeoJSON
```

A run writes `<project>/auto/<run id>/` and never touches the project's own `annotations.json`:

- `annotations.json`: the existing exchange format (`pclabel.annotations/9`, point sets as source indices), with
  one top directory per stage. The app's existing annotation import opens it beside the hand labels.
- `run.json`: every stage's parameter dataclass, timings, the git commit, the taxonomy version and the scan's
  `sha256` and `n_valid`.
- `score.json` once scored.
- `map.dxf` and `map.geojson` through the existing `io/vector_export.py`.

Scan-level rasters that several stages share are cached in `<project>/derived/`, keyed by a hash of their
parameters.

## 3. What exists to build on

### Reference labels

Only two scans carry the user's labels. Everything is in `annotations.json` and the point-set index files. The
per-point label columns are all zero and there is no journal.

| | streets 1 (634M points) | ExchangeGreen 1 (257M points) |
|---|---|---|
| curbs | 6 hand-clicked curves labelled `curb`, 778 m | 6 curves and 1 polyline labelled `curb_top`, 443 m, made by the curb tracer and then hand-edited; plus 2 hand curves labelled `curb`, 130 m |
| road markings | 23 `road_line` point sets, 9.87M points, 6 marked uncertain | 13 `road_line` point sets, 1.13M points |
| trees | 127 `tree` markers, 129 `tree_trunk` band sets, 17 uncertain | 29 markers, 29 band sets (the user says trees and poles are only partly done here) |
| poles | 7 `lamp_post`, 15 `other_pole`, 1 `pole` markers | 4 `lamp_post`, 4 `other_pole` markers |
| surfaces | `road` 152M points, `pavement` 18.9M | `road` 55.5M |

Things to know about this reference:

- **Curb lines follow the top outer edge of the upper curb** (the lip), by the user's standard. Hand lines labelled
  `curb` and `curb_top` both mean that.
- In streets 1, `top_curbs/top` and `top_curbs/bottom` are most likely the two curbs on either side of the *top
  road* (the user names things by position in the top view: top road, bottom road), not a lip and a toe. Check
  by measuring the step at both before using them.
- **ExchangeGreen's curb lines came from the tool you will be automating.** They are valid as a reference only
  because the user edited and approved them. streets 1's curbs are independent of the tracer. Report the two
  scans separately and treat streets 1 as the more honest curb number.
- Both projects contain large unlabelled point sets and unlabelled closed "copy" curves. These are working masks,
  not labels. Ignore every unlabelled item.
- Markings exist only as point sets. There are no marking polylines by the user, so marking vectors can be scored
  only through the points they cover.
- Nothing records which part of a scan the user assessed. Section 6, P1 deals with this.
- `mobile-colorized-v2-1` holds a vendor's (ROCK) drawing of a US mobile-mapping scene: about 2.8 km of each curb
  edge and 391 paint stripes. It is a different sensor and a different country. Use it as a sanity check that the
  pipeline is not fitted to two scans, never for tuning. Its curb edges were imported under an old profile that
  labelled all three edges `curb`; re-import with the current profile before using it.

### Tools

Every algorithm in `pclabel/tools/` and `pclabel/analysis/` is free of Qt and GL, works on full-resolution points
and has a parameter dataclass. The pieces the pipeline reuses:

| stage needs | existing function | what it still needs from a human |
|---|---|---|
| curb cross-section and trace | `curbtrace.section`, `curbtrace.run_trace` | a start point on the road and a second point for direction |
| snap a line to the curb top | `kerbfit.fit` | an existing line |
| stitch traced pieces | `linejoin.join_lines` | lines to join |
| smooth | `curves.smooth_line` | a line |
| local ground grid | `ground.cell_grid` (10 cm cells: surface height, intensity, roughness, count) | a seed, and it reaches only 30 m |
| ground intensity image | `magic.intensity_image`, `magic.magic_region` | a click on the paint |
| marking centreline | `linetrace.run_trace` | a click on the paint and a direction |
| trunk circle | `trees.fit_trunk`, `markers.marker_at_click` | a click at the foot and a cylinder moved onto the trunk |
| density | `density.density_grid` | a point set |
| connected components | `pieces.find_pieces` | a selection |
| rough/smooth split | `split.set_features`, `split.split_labels` | a point set |
| region read | `volume.select_volume`, `volume.select_loop` | nothing |

### What is missing

1. Any proposal of where to start: no curb seeds, no paint seeds, no trunk candidates.
2. A ground model for the whole scan. There is one global fitted plane at a time, plus the seed-bound grid above.
3. Handling of drains and bushes in the curb tracer (causes in P3).
4. An orchestrator, a writer and a scorer. Saving a trace result as annotations lives in `ui/trace.py`.

## 4. The user's observations, and what the pipeline does with each

These come from the user's labelling notes in `ignore/` and from the planning conversation. They are the main
design input. Where one is marked *hypothesis*, measure it before relying on it (P2).

| observation | consequence for the pipeline |
|---|---|
| Different roads slope differently, so one ground plane does not fit a scan (`ignore/streets_labelling_observations/images/roads_tilted.png`). | The ground is a raster surface for the whole scan, and every height is taken relative to the local surface. No stage uses a global plane. |
| Colouring by height modulo about 6 cm shows long, straight, regular stripes on roads and pavements, and noise on vegetation. | The stripes are the contour lines of the surface. Regular stripes mean a locally planar surface with a steady slope, and their spacing is the wrap period divided by the slope (6 cm at a 2.5 % crossfall gives 2.4 m). The computable equivalent is, per ground cell, a local plane fit: its gradient vector and its residual. Low residual and a slowly varying gradient is "paved". *Hypothesis:* road and pavement both fall toward the curb, so the gradient changes sign across a road edge even where the curb is lowered and there is no step. |
| Drains sit at the foot of curbs, and some of their points are below the road. They are the lowest points in the scan. Not every drain has such points. | Three uses. (a) The ground surface must be robust to pits, or every height near a drain is wrong. (b) A compact pit below the road surface is positive evidence that a curb runs beside it. (c) A gap in a traced curb that coincides with a pit is a drain, so the line is bridged across it and the bridge records why. |
| Curbs have two stages: a strip about 40 cm wide at road level (often concrete, where the drains are), then the raised curb. The strip is sometimes faint and sometimes absent. | The curb line is the lip of the raised stage. The road ends at the outer edge of the strip, so the road mask stops there. Where the strip is absent the road runs to the curb's toe. The strip is brighter than asphalt, so it must be excluded before looking for paint. |
| The curb's face is often missing: across the road from the scanner only the top is seen, with a band of no points between road and curb top. | Curb detection cannot require face points. It compares the road surface with the curb top across an empty band of up to about 0.3 m. |
| Curbs are often painted in alternating black and white stretches of roughly 1 m. | A periodic intensity or brightness signal along a line is evidence that the line is a curb and not a hedge edge, a wall or a car. It is confirmation only: many curbs are unpainted. |
| The curb tracer goes wrong at drains and sometimes at bushes. | Fix the causes in the tracer itself (P3), so the interactive tool improves too. |
| A walkway can look like a road in intensity (ExchangeGreen). What tells them apart is that no curb bounds it. | Road is defined from curbs (the low side of a curb line), not from intensity alone. |
| Paint is separable from asphalt by intensity, and one click selects a whole dash above about 2,000 points/m². Below about 500 points/m² it rarely works; the user's standard treats that as the limit for "uncertain". | Marking detection uses contrast against the local road surface, never a global threshold. Results in cells below 500 points/m² are marked uncertain. |
| Bright lines also occur near the road edge, and the area where a walkway drops to the road is bright. | Restrict paint detection to the road mask, inset from the curb. |
| Trees are defined by the trunk's cross-section 1.00 to 1.10 m above the foot, and located by the centre of a circle fitted to it, placed at ground height. | Trunk candidates come from a slab at that height above the ground raster. The existing circle fit is the measurement. |
| Bushes often surround a trunk and hide its foot. The grass rises toward a trunk. | Take the ground at a tree from the surface around it, not from the lowest point under it, and mark the tree uncertain if no ground is seen within about 0.5 m. |
| Saplings are often tied to one or more support poles. A tree shorter than 2 m counts as vegetation. | A tight group of thin vertical stems is one object. The object's height above ground decides tree or vegetation. |
| Lamp posts and traffic lights are vertical and straight, unlike trunks. | The same candidates, split by how straight and how constant in radius the stem is and by what is above it. |

The user said they have more observations they could not recall at the time. When one comes up, record it with
`/observe` and add a row here if it changes the design.

## 5. Design

```
pclabel/auto/          Qt-free; each stage is a function with its own parameter dataclass
  ground.py            scan-wide ground raster and its derived layers
  curbs.py             candidates -> seeds -> trace -> join -> fit -> checks
  road.py              road mask from curbs and ground
  markings.py          paint mask -> point sets -> centrelines and outlines
  trunks.py            trunk and pole candidates -> circle fits -> classes
  run.py               orchestrator, run directory, caching
pclabel/scoring/       reference selection, the three metrics, the report
```

Rules of the design:

- **One implementation per algorithm.** A stage calls the same function as the interactive tool. If the tracer
  needs to handle drains, change `curbtrace`, not a copy. The interactive tool can then offer the pipeline's
  proposal as its default.
- **Stages communicate through files** in the run directory and `derived/`, so any stage can be re-run alone and
  its input inspected.
- **Every stage records its parameters and its evidence.** A curb line carries, per sample, the measured step
  height, face width and the checks it passed. These thresholds are what the project exists to study.
- **A stage prefers leaving a gap to guessing.** Anything inferred (a bridge across a drain, a curb continued
  across a lowered section) is marked as inferred, and anything in sparse data as uncertain.
- **Heights are relative to the ground raster.** Raw z is used only to build it. The scans are levelled by the
  scanner, so z is close enough to vertical for a raster; say so in the report if a scan contradicts this.

## 6. Work packages

Do them in this order. Each ends with the tests passing, a short written result, and (from P3 on) a score on both
labelled scans. Stop and report instead of pressing on if a package misses its acceptance check and the cause is
not clear after a reasonable attempt.

### P1. Scoring and the headless run format

Build the scorer first, so that every later choice is measured.

Build:

- `python -m pclabel score <project> --run <id | path to an annotations export>`.
- Reference selection: hand items by label, leaving out unlabelled items, items whose `uncertain` is set, and
  items marked `assisted` that were never edited. Uncertain reference items are "don't care": matching one is
  neither a hit nor a false alarm.
- The assessed area. Until the user provides one (section 8), use the labelled `road` point sets rasterised at
  0.5 m and dilated by 3 m. Detections outside the assessed area are counted separately and are not false alarms.
- **Curb metric.** Resample both sets of lines every 5 cm. *Completeness* is the share of reference length with
  an automatic line within the tolerance; *correctness* is the share of automatic length, inside the assessed
  area, with a reference line within the tolerance. Report both at horizontal tolerances of 5, 10 and 20 cm.
  Also report the signed sideways offset (median and 95th percentile; a consistent sign shows a bias toward the
  road or away from it), the height offset, and the number of separate automatic pieces per 100 m of reference.
- **Marking metric.** Precision, recall and intersection over union of the automatic `road_line` points against
  the reference points, among points inside the assessed area. Also per item: the share of reference marking
  items with at least half their points found, because a few zebra crossings dominate the point count.
- **Tree and pole metric.** Match markers one to one, nearest first, within 0.5 m. Report precision, recall, the
  centre distance, the radius difference, and tree-versus-pole confusion among matched pairs.
- `score.json` with a `schema` field, and a one-screen text table.
- The run directory layout of section 2 and a writer that turns stage results into the exchange file.

Acceptance:

- Scoring a copy of the hand labels against themselves gives 100 % everywhere.
- Synthetic tests: a line shifted sideways by 7 cm passes at 10 cm and fails at 5 cm; a line with a third
  removed scores two thirds completeness; an extra tree lowers precision only.
- **A baseline for the tool as it is today.** `trace_log.jsonl` in ExchangeGreen 1 logs 40 curb tracer runs. If
  it records each run's start, direction and parameters, replay them headless and score the raw result against
  the final edited lines. That number is how much hand correction the semi-automatic tracer needs now, and it is
  the figure the pipeline has to beat without clicks.

### P2. Scan-wide ground raster, and the measurements that set the thresholds

Build `auto/ground.py`: one streaming pass over the tiles that produces, on a 10 cm grid over the whole scan,

- the point count per cell (density),
- the height of the lowest dense layer, with the layer's median intensity, median colour and roughness
  (generalise `ground.cell_grid`, which already computes these within 30 m of a seed),
- a reference surface: a smooth surface through the paved cells that ignores pits and bumps, with its gradient
  vector and fit residual per cell,
- depth below the reference surface (pits) and occupancy of a few height slabs above it (objects).

The result must not depend on how the scan is cut into blocks: an earlier per-tile sampling scheme left visible
seams (see the M2 results), so cap samples per cell, never per tile, and test for block independence.

Then measure, on both labelled scans, with scripts kept in `bench/experiments/` (not the scratchpad, so the
numbers can be reproduced):

1. Fit residual and roughness under labelled `road`, under labelled `pavement`, and under neither. Is there a
   threshold that separates paved from unpaved, and how well?
2. Step height, face width and the empty band at the hand curb lines, sampled every 0.3 m with
   `curbtrace.section`. Known so far: ExchangeGreen steps 8 to 10 cm, streets 1 steps 0.12 to 0.33 m (the latter
   unverified).
3. The gradient on each side of the hand curb lines. Does the sign change, including at lowered sections?
4. Intensity and brightness along the hand curb lines: how much of the length shows a periodic pattern, and with
   what period?
5. Depth, length and spacing of the pits beside the hand curb lines.
6. Paint-to-road intensity ratio for the hand `road_line` sets, against density and against distance from the
   nearest scanner position (`project.json` has the poses).
7. For the hand tree and pole markers: size, point count, fit error and coverage of the 1.00 to 1.10 m cluster,
   against all other clusters in that slab.

Acceptance:

- The raster for streets 1 builds in under 3 minutes and under 16 GB of memory (provisional).
- Under labelled road cells, the reference surface is within 2 cm of the lowest-layer height for at least 95 %
  of cells, including beside drains.
- A written result for each of the seven measurements, each recorded as an observation (`/observe`), with the
  threshold it suggests. Do not pick thresholds for P3 to P5 before these exist.

### P3. Curbs

Pipeline: step candidates from the raster → seeds → `curbtrace.run_trace` → `linejoin.join_lines` →
`kerbfit.fit` → smoothing → checks → `curb_top` lines.

Candidates: cells where the lowest-layer height rises by a curb-like amount (range from P2, measurement 2) within
about 0.3 m, allowing an empty band between, with a paved low side. Group them into chains, take the direction
from the chain, and seed the tracer on the low side. Repeat until every chain is either covered by a traced line
or rejected with a recorded reason.

Fix these in `curbtrace` (line numbers at commit `29f6472`). Add a synthetic test for each before fixing it:

- **Drains.** The "is this bin road" test has no lower bound (`:112`), so a pit passes as road and drags the road
  reference down; the road beyond the pit can then pass as a curb lip. Give the road test a lower bound, treat a
  pit as its own event, and record it on the result.
- The road reference is the median of the bins near the section centre (`:96-103`), which is wrong when the
  centre is over a grating. Take it from the ground raster.
- A gap ends the trace after 1.0 m (`:233`), which is shorter than some inlets. Let a gap explained by a pit run
  longer.
- The first section after a gap is accepted without the turn check (`:218`, `:238`), and the trace steps
  straight ahead while skipping (`:235`), so it drifts on bends.
- **Bushes.** The section box reaches 0.8 m above the centre (`:75`) and the road test uses the bin's 80th
  percentile, so overhanging foliage makes road bins fail. Nothing limits the curb's height or tests that its top
  is flat, so a hedge edge, a car side or a wall can pass as a lip. Add a maximum height and a test on the top's
  roughness, and bound the box from the raster.
- The synthetic street (`pclabel/synth/street.py`) has drains, a lowered curb, one tree and markings. Add what the
  tests above need: a hedge over the curb, an inlet longer than 1 m, a parked car at the curb, a curb seen only
  from above with an empty band, a two-stage curb with a strip, two roads with different slopes, and a painted
  curb.

Checks on a finished line, each recorded per sample: step height in range, top flat, supported by raster
candidates along most of its length, optional paint periodicity (P2, measurement 4). A line that fails is written
into a `rejected` directory with the reason, not dropped, so the user can see what was thrown away.

Leave lowered curbs for the end of this package. The user's standard draws the curb curve straight across a
lowered section. Bridging a gap is right at a lowered curb and wrong at a side road, where the curb turns away.
Use what P2 found (a residual step of a few centimetres, a material edge, a gradient sign change) and mark every
bridged span as inferred.

Acceptance (provisional targets; set the real ones with the user after the first full run):

- streets 1 and ExchangeGreen 1: completeness at least 85 % and correctness at least 90 % at 10 cm, with the
  median sideways offset under 3 cm.
- Better than the replayed-clicks baseline from P1 on ExchangeGreen 1.
- Named places from the user's notes come out right, checked by eye in the app (streets 1 coordinates as shown
  in the app; confirm whether they include the project offset):
  drain under a curb near (-5.542, 12.612, -1.454); two-stage curb near (-22.075, 19.725, -1.547); curb seen as
  a wall near (15.482, -24.425, -1.193); curb seen only from above near (20.780, -18.683, -0.803); walkway
  dropping to the road near (14.978, 5.914, -1.270).
- Curbs for streets 1 in under 5 minutes after the raster exists (provisional).

### P4. Road mask and road markings

Road mask: paved raster cells on the low side of the curb lines, filled inward from the curbs and stopped at the
outer edge of the strip where a strip exists (a change of intensity and texture about 40 cm from the lip; the
user's manual procedure uses a fixed 40 cm inset, which is the fallback). Score the mask against the labelled
`road` point sets as a by-product, but do not spend effort on it beyond what markings need.

Markings:

1. Build the 2 cm ground intensity image over the road mask in blocks (`magic.intensity_image` is limited to
   about 40M pixels per image).
2. Paint is where intensity exceeds the local road background by a ratio (from P2, measurement 6), with the
   background taken as a local median that excludes bright cells. This is the magic select rule applied
   everywhere at once.
3. Connected components become `road_line` point sets, with the full-resolution points selected as
   `magic_select` does. Mark a component uncertain where the density is under 500 points/m².
4. Vectors: long thin components get a centreline (seed `linetrace` from the component's main axis, or thin the
   mask); compact ones (arrows, text, zebra bars) get an outline. The line tracer rejects paint wider than
   0.40 m and stops at turns over 20°, so it suits lane lines only.

Typing the markings (solid, dashed, arrow, zebra, yellow versus white) is out of scope here. Record width,
length and mean colour on each item so that it can be done later.

Acceptance (provisional): per-item recall at least 90 % and point intersection over union at least 0.7 on both
scans; the arrow near (-16.085, 14.182, -1.513) and the dashes near (29.153, -14.370, -1.069) in streets 1 come
out whole.

### P5. Trees and poles

1. Take the points 1.00 to 1.10 m above the reference surface and cluster them (`pieces.find_pieces`).
2. Keep clusters of trunk-like size (range from P2, measurement 7; the median labelled trunk radius is about
   8 cm) and fit a circle with `trees.fit_trunk`.
3. Check the stem in several slabs between 0.5 and 2.5 m: a trunk or pole gives compact clusters whose centres
   line up. Allow a lean (one labelled tree leans 18.5°). Hedges, fences, walls, people and cars do not.
4. Merge thin stems within about 0.5 m of one another into one object (a sapling and its stakes).
5. Classify. Shorter than 2 m: vegetation, no marker. Straight, vertical, constant radius, nothing leafy above:
   pole. Otherwise tree. Start with the plain `pole` label and split lamp posts from other poles only if P2
   shows an easy cue.
6. Write, per tree, a directory with a `tree` marker at the fitted centre at ground height and a `tree_trunk`
   point set of the band, as the tree tool does. Poles get a marker only.

Acceptance (provisional): on streets 1, recall and precision both at least 85 % at 0.5 m with a median centre
distance under 5 cm. ExchangeGreen 1's trees are incomplete, so report recall only there. The tree in bushes near
(11.312, 25.932, -0.955) in streets 1 is found and marked uncertain if its foot is hidden.

### P6. Review in the app

Small UI work that makes the pipeline useful for labelling:

- Open a run beside the hand labels (the import exists; add a menu entry that lists the project's runs).
- Accept, edit or delete automatic items. Accepting moves the item into the hand tree; editing sets `edited`.
- A per-run summary: how many items were accepted unchanged, edited, deleted, and how much the user added.

That summary is the measure that matters in the end: how much work is left for a person after the pipeline has
run.

### P7. A scan the pipeline has never seen

Run the pipeline on `newroad 2` (8.4M points, never labelled). The user corrects the result in the app (P6).
Report the P6 summary. Do not tune on this scan before the first run.

## 7. Rules for this work

- **Tune on one scan, report on both, and never tune on the vendor scan or on `newroad 2`.** With two labelled
  scans, fitting thresholds to both leaves nothing to check them against.
- **A tool's own output is not evidence for that tool** unless the user confirmed or corrected it. That is why
  ExchangeGreen's curb lines count (all edited) and why unedited traced lines must be left out of the reference.
- **Unlabelled is not negative.** A point or item with no label has not been assessed. Count false alarms only
  inside the assessed area.
- **Report measured numbers on named scans**, with the run id, and say when a number comes from a crop.
- **Tests use synthetic scenes only.** Extend `pclabel.synth` when a real failure has no synthetic counterpart.
- **Never write into a source scan's folder or into the project's own `annotations.json`.** Runs go into
  `<project>/auto/`.
- For quick iteration, cut a crop of streets 1 that contains a drain, a lowered curb, a hedge at a curb and a
  junction, and record its bounds in the plan's results.

## 8. Decisions that are the user's

1. **The labelling standard.** [01_cleanup.md](01_cleanup.md) part A lists where the repo's standard differs
   from the user's own notes. The pipeline writes the user's version.
2. **The assessed area.** Scoring needs to know where the user looked. The cleanest answer is one closed curve
   per labelled scan, drawn in the app and labelled as the assessed area. Until then the scorer uses the fallback
   in P1.
3. **Targets.** The acceptance numbers above are proposals. The user sets what is good enough once the first
   full run shows where things stand.
4. **Whether a sapling's support poles belong to the tree**, and whether the tree's labels change when it grows
   past 2 m. Both are marked TODO in the user's standard; P5 assumes the poles belong to the tree.
5. **A third labelled scan.** Two scans from one city are a thin basis. P7 produces a third cheaply if the user
   is willing to correct one run.
