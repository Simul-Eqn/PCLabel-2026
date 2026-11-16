# labelling standard

**Version 0.3, 2026-11-16.** Written from the user's notes, [`ignore/labelling_standards.md`](../ignore/labelling_standards.md)
and [`ignore/labelling_procedure.md`](../ignore/labelling_procedure.md), which hold the step-by-step methods and
their screenshots. Where this file and the notes differ, the notes win and this file is fixed. Class ids, names and
colours are in [`config/taxonomy.yaml`](../config/taxonomy.yaml) (version 0.7); this file says when to use them.

Every rule here is **decided** by the user unless it is marked **proposed**. Only the user decides a rule; record a
decision with `/decide`. Open questions are in [undecided](#undecided) at the end.

## general rules

- **Unassessed is not negative.** A point in no labelled set has not been looked at. "Looked at, none of the
  classes" is the label `other`, whose points no tool selects.
- **Labels form a tree** (2026-11-04). A class is a kind of its parent: `road_line`, `ramp` and `driveway` are kinds
  of `road`. Use the parent when no sub-label fits, when unsure which, or to label quickly. Counting a class counts
  its kinds.
- **A label covers every point in its set**, at full resolution, not only the points drawn.
- **Uncertain** is a mark on an item (a point set, a curve or a marker), never on a directory. The guideline: where
  the point density is under 500 points/m² (the density tool), an item may be marked uncertain; above it, it is not.
  Features you are sure of need no mark at any density. A class may add its own guideline. The 500 comes from where
  intensity select starts to fail on streets 1 (the procedure's road lines section).
- **Exclude.** Objects in the way (artefacts, things on the pavement) may be saved as a point set labelled `exclude`
  and locked, so later selections skip them.

## order of work

Curb, ramp, road, road lines, pavement, trees, vegetation, poles.

## curb

- The curb is a **line, not points**: a curve along the **top, outermost edge** of the raised curb, label `curb`.
  The curb's sides and the strip beside it are not selected.
- Draw the top, not where the curb meets the ground: the scanner often misses the foot because the curb hides it.
- Where the walkway drops gradually to the road (a crossing, a driveway), the curve runs straight across it.
- One curb per area is one curve. Pavement and vegetation are selected from that curve.
- Older labels and the curb tracer write the same line as `curb_top`; it counts as `curb`. `curb_bottom` and
  `curb_back` exist only so vendor drawings import.

**The two stages.** A curb often has two stages: a lower strip at road level beside the road (where the drains are),
then the raised curb. The strip is sometimes faint and sometimes absent. It has **no label**: it is neither road nor
curb.

## ramp

- A ramp is a **kind of road** (2026-11-16): its points are road, and ramp says which part.
- The ramp is the part more than **0.1 m above a plane fitted to the road around it** (3D select from 0.1 m up on that
  plane).
- **Proposed:** the method has not been tried yet.

## road

- Road is **everything bounded by the lower stage of the curbs**.
- It does **not** include the strip beside the road.
- It **includes** features on the road that are flat, can be driven over and would not reasonably be expected to cause
  accidents, such as a flat drain cover or tiles in the road.
- It includes ramps (above) and road lines (below).
- Method: extrude-select from the curb curve with an inset of about −40 cm, matching the lower stage, or draw a curve
  along the lower stage. Then remove what stands on the road (ground select, 3D select from a fitted plane, clean-up).
  Check that no part of the lower stage is selected.
- Driveways, the vehicle crossings over the pavement, are a kind of road (2026-11-04).

## road lines

- Painted lines and markings on the road, label `road_line`. They are also road: the **only points with two
  classes**.
- Method: magic select by intensity, clipped to the road set, coloured by intensity; or the marking tracer
  (`Ctrl+Shift+T`).

## pavement

- The part **pedestrians can walk on**, including the yellow tactile paving and the part that slopes down to the
  road, **up to a 5 cm inset from the curb curve**.
- Other surfaces people can walk on (a manhole cover in the pavement) are pavement. Things people cannot walk on (a
  fire hydrant and the raised square around it) are not.
- Park connectors are pavement, whatever their colour or material (2026-10-16). Pavement has no sub-labels.
- Method: clip with the curb at a 5 cm inset, label objects in the way `exclude` and lock them, then select the
  pavement with a curve. Fitting a plane to the road helps: in the wrap-height view the flat pavement shows a regular
  pattern.

## tree

- A tree is one directory (the instance) holding:
  - its **band**: the trunk's points **1.00 to 1.10 m above the base of the tree**, label `tree_trunk`. Singapore
    measures girth 1 m above the ground (Parks and Trees regulations, amended 2017), and at that height bushes rarely
    block the trunk;
  - its **marker**, label `tree`: the centre of a circle fitted to the band projected on the height plane, at the
    height of the base. A circle still finds the centre when only one side of the trunk was scanned.
- Canopies are not labelled.
- Method: the tree tool, `Shift+U`. Click a point at the height of the tree's base, move the cylinder onto the trunk,
  select, and save into the right directory.
- Where leaves, early branching or sparse points spoil the band (a short tree), mark it uncertain. In worse cases,
  do not label it as a tree: label it `vegetation` if it is joined to vegetation, otherwise leave it unlabelled.

## vegetation

- One class, `vegetation`, **without instances**: grass and small plants together, any plant without a trunk at
  least 1 m long (the forestry standard), and tree saplings shorter than 2 m.
- Bushes can hide how tall a sapling is; curb points can help judge the height.
- `grass` and `shrub_hedge` are optional finer labels; they are never required.
- Method: select pavement and vegetation together inside the curb curve (5 cm inset), then split them with the split
  tool and tidy with 3D select. Keep one and take the other as the set difference.
- **Proposed, for the map:** vegetation goes on the sketch plan as outlines, each with its height above the local
  ground (a high percentile of its points). Plants tall enough to block a driver's view come from a threshold on that
  height, not from a label. The threshold is to be measured.

## lamp posts, traffic lights and other poles

- They stand straight up and are easy to tell from trees.
- Each is a marker at the **height of the ground around it**, at the **middle of the shape**. Either use the tree
  tool (`Shift+U`; the marker it saves is what matters), or 3D select points near the ground around it (bottom
  0.05 m) and use the marker tool.
- Labels (2026-11-16):
  - `lta_lamp_post`: a lamp post registered with LTA. Its marker's `asset_id` attribute holds its LTA lamp post
    number (the marker's "attributes..." button);
  - `lamp_post`: a privately owned lamp post. Every lamp post labelled before 2026-11-16 is one;
  - `traffic_light_pole`;
  - `other_pole`: usually road signage; also bollards, camera poles and utility poles;
  - `pole` when unsure which.

## undecided

- **Petrol kiosk roads:** are they road?
- **Dirt paths:** are they pavement?
- **Sapling support poles:** are the poles around a young tree part of the tree?
- **Growing saplings:** a sapling under 2 m is vegetation, so its label changes when it grows. Is that acceptable?
- **Ramps:** the 0.1 m method has not been tried.
- **Vegetation on the map:** the height threshold for plants that block the view (proposed above).
- **Covers** (manholes, utility covers): future work, not labelled for now. The cover tool stays.
- **Classes not covered yet:** `structure`, `temporary` and `noise` and their kinds are in the taxonomy as
  proposed. The standard says nothing about them yet.

## changelog

- 0.3 (2026-11-16): rewritten from the user's notes; the maintainer's proposals (R- rules, P- principles and the
  Q- questions in `OPEN_QUESTIONS.md`) removed, as git history keeps them. Ramp is a kind of road; `lta_lamp_post`
  added; taxonomy 0.7.
- 0.2 (2026-11-04): the labels form a tree.
- 0.1 (2026-10-16): first draft; park connectors are pavement.
