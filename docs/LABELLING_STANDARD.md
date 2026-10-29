# labelling standard

**Version 0.1 (draft), 2026-10-16.** This file and [../config/taxonomy.yaml](../config/taxonomy.yaml) together define
the standard. The app records a hash of both with every label operation, so labels can always be traced to the
rules they were made under.

How this file works:
- Each rule has an id `R-NNN` and a status:
  - `decided`: the user decided it;
  - `proposed`: a suggestion waiting for the user's decision;
  - `superseded`: kept for history, pointing to its replacement.
- Each rule links the observations (`O-NNNN`, in [OBSERVATIONS.md](OBSERVATIONS.md)) that motivated it.
- A rule may have a `check:`, a query in the app's query language ([SPEC.md](SPEC.md) §9).
  The label lint runs it to find points that break the rule. Each hit is either a mislabel or a counterexample worth
  an observation.
- Only the user moves a rule to `decided`. Record decisions with `/decide`.
- Never renumber or delete a rule; supersede it.

## principles (proposed)

- **P1 Function of the permanent surface.** Label a surface by who it is built for, not by what is on it at the
  moment. A park connector is for pedestrians and cyclists, so it is pavement. A parked car is `vehicle`; the road
  under it has no points.
- **P2 Zero is unassessed.** Inside an assessed region, every point gets a class; `other` exists for "none of
  these". Unlabelled points are never counted as negatives.
- **P3 Unsure means flag, not skip.** Label your best guess, set the `ambiguous` flag and log an observation
  tagged `decision-needed`. Do not leave unsure points at 0 inside an assessed region.
- **P4 No points, no label.** Do not invent surfaces that were not scanned. Occlusion is recorded as an observation
  or a region status, not as labels.
- **P5 Two indicators, zoomed in.** Before placing a boundary, check geometry (height in a flattened frame, a cross
  section) and appearance (RGB, intensity). Full-scene views mislead: earlier work here twice took a building edge
  and an embankment for a kerb. If the indicators disagree, log it.
- **P6 Boundary precision.** Where points exist, place class boundaries to within about 2 cm, the scale of the
  thresholds being tuned. Where the data does not allow that, set `ambiguous` rather than guessing a sharp edge.
- **P7 Rules over taste.** If a rule gives a label that looks wrong, follow the rule and log the case. The rule
  changes; individual points do not.

## classes (summary; the yaml is authoritative)

| id | class | status | short definition | rule |
|---|---|---|---|---|
| 0 | unassessed | core | not yet looked at | R-001 |
| 1 | road | core | carriageway surface for vehicles | R-005 |
| 2 | curb | core | raised kerb unit (extent pending) | R-004 |
| 3 | curb_lowered | proposed | kerb unit at a dropped/ramped section | R-007 |
| 4 | pavement | core | footpaths, park connectors, cycling/shared paths | R-002, R-009 |
| 5 | drain | proposed | inlets, gratings, channels, drain covers | R-003 |
| 6 | grass | core | turf, ground cover, bare soil in verges | R-011 |
| 7 | island | proposed, disabled | only if R-008 picks a separate class | R-008 |
| 8 | driveway | proposed, disabled | only if R-010 picks a separate class | R-010 |
| 9 | ramp | core (user, 2026-10-22) | sloped walking surface between pavement and road (extent pending) | R-007 |
| 10 | road_line | core (user, 2026-10-28) | painted lines and markings on the road; **part of road** (`subset_of: road`) | R-006 |
| 20 | shrub_hedge | proposed | shrubs, hedges, planted beds | R-011 |
| 21 / 22 | tree_trunk / tree_canopy | core (split proposed) | trunk and stems / leaves and twigs | R-011 |
| 30 | lamp_post | core | lamp pole with arm and head | R-012 |
| 31 | traffic_light_pole | proposed | signal pole with arms and heads | R-012 |
| 32 | other_pole | proposed | sign posts, bollards, camera poles | R-012 |
| 41, 42, 44 | fence_railing, roof_shelter, street_furniture | proposed | structures and furniture | — |
| 50–53 | vehicle, person, temporary_object, ghost | proposed | temporary and moving things | R-013 |
| 60 | noise | proposed | points on no real surface | R-014 |
| 61 | other | core | assessed, fits no class ("no class": its points are never selected, taxonomy `selectable: false`, user 2026-10-20) | — |

Flags: `ambiguous`, `marking` (R-006), `under_cover`, `exclude`, `reviewed`.

With labels on annotations (D-007), `ambiguous` is the **uncertain** mark on an item: a point set, segment or
curve, never a directory (added 2026-10-22 at the user's request; items only since 2026-10-26). The label is the
labeller's best guess, kept out of scoring by default (R-001). It is exported with the labels.

## rules

### R-001 Zero means unassessed — proposed
- **Rule:** label 0 means only "nobody has assessed this point". Assessed regions are drawn in the app. Scoring and
  threshold statistics use only points inside assessed regions, and exclude `ambiguous` and `exclude` points
  unless asked.
- **Why:** partial labelling is the plan; unlabelled must never mean negative.

### R-002 Park connectors are pavement — **decided** (user; recorded 2026-10-16)
- **Rule:** the paved surface of a Park Connector Network path is `pavement`, whatever its colour or material,
  including where it runs alongside the road.
- **Why:** it is built for pedestrians and cyclists, not vehicles (P1).
- **Examples:** O-0001.
- **Open edge cases:**
  - where a park connector crosses a road (the crossing is road?);
  - boardwalks and bridges;
  - its kerb against the road (curb, as anywhere else?).

### R-003 Below-road points at curb gaps are drain — proposed
- **Rule:** points clearly below the local road surface where the curb is interrupted are `drain`: inlet openings,
  gratings, channel bottoms. "Clearly below" gets a number once measured; start with 3 cm.
- **From:** O-0002 (the only points below the road are the drains that periodically interrupt the curbs).
- **Check:** `assessed and hs < -0.03 and class not in (drain, noise)`. This should find almost nothing; every
  cluster it finds is a mislabel or a counterexample (manhole, pothole, depression, puddle hollow).
- **Open:**
  - Is the kerb lintel spanning an inlet curb or drain?
  - Does the curb *line* continue across the inlet? For a sketch it probably should.
  - Inlet spacing: measure it (instance tool), since it may be regular enough to help an algorithm.

### R-004 Which points are curb — proposed, **decision needed**
Options:
- **A. Face only:** points on the near-vertical riser between toe and lip.
- **B. Kerb unit:** the face plus the top of the kerb unit, back to its joint with the pavement or verge where that
  is visible (material, RGB or intensity change, or a joint line). Where it is not visible, take a fixed width
  behind the lip (to be measured; start with 0.15 m).
- **C. Band:** a fixed-width band around the step, like the automatically extended labels of 3D-Curb.

Recommendation: **B**.
- It matches a physical object and should be stable across scanners.
- Algorithms can derive both the toe and the lip from it.
- A has almost no points wherever the face is occluded (the no-point strip).
- C mixes an algorithm parameter into the definition.

Also to decide: which line the vector map calls "the curb line" (toe, lip or face). Proposal: label points now,
and later draw toe and lip as separate vector features (ROADMAP M9). The sketch can then use either.

### R-005 Road extent — proposed
- **Rule:** `road` is all vehicle-trafficable surface between curbs or other edges:
  - lanes, shoulders, concrete bus bays;
  - road humps, rumble strips;
  - painted (chevron) islands, yellow boxes, zebra crossings;
  - manhole and utility covers flush with the road, patches.
- **Check:** `assessed and class == road and hs > 0.05` finds humps, vehicles labelled as road, and mislabelled
  curb tops.

### R-006 Road lines are a class within the road; other paint is a flag — proposed
- **Rule:** painted lines and markings on the road (lane, edge and centre lines, stop lines, arrows, text,
  zig-zags) are `road_line` (id 10), a class that is **part of `road`** (taxonomy `subset_of: road`).
  - The road's point set keeps its line points; the lines get point sets of their own labelled `road_line` (e.g.
    magic select a dash, `I`, then `Ctrl+G` inside a "road lines" directory labelled `road_line`).
  - A `road_line` point is a road point too. "Export labels" counts the road with its lines
    (`points_with_subsets`), marks road ∩ road_line as an expected overlap (`subset: true`), and gives the
    `road_line` points outside every road set (`outside_parent`): each is a line not yet inside the road's set, or
    a mislabel.
  - Paint on other surfaces (a painted kerb, markings on a pavement or a cycle path) keeps the class of its surface
    and the `marking` flag, as before.
- **History:** first proposed (2026-10-16) as a flag only. On 2026-10-21 the user asked for a "road lines" class
  and got `marking` point sets overlapping the road. On 2026-10-28 the user asked for a road lines class "which is
  going to be a subset of the road class": this version.
- **Open:** whether the blurred edge of a line (mixed returns at the paint's border) is line or road; whether worn
  or ghost lines count; whether studs and cat's eyes are road_line. Marking type stays Q-14.
- Marking type (lane, edge, double yellow, zig-zag, arrow, text) is not per point for now. Use instances or vector
  features later if the algorithms need it.

### R-007 Lowered kerbs — proposed
- **Rule:** kerb units at crossings and driveways stay curb (`curb_lowered`) as long as the unit can be told apart
  (material, joint, or a step of a few cm). The ramp surface behind it is pavement, including the tactile tiles.
- **Changed proposal (2026-10-22):** the user added a `ramp` class. The sloped surface behind a lowered kerb is then
  `ramp`, not pavement. Still open, for the user: where a ramp ends and the pavement begins (the end of the slope,
  or the end of the tactile tiles), whether the tactile tiles are ramp, and whether driveway slopes for vehicles
  count (R-010).
- **Open:** where a full-height kerb becomes lowered. Proposal: the first unit whose step is clearly below full
  height, with the measured heights recorded in an observation.

### R-008 Islands and medians — proposed, **decision needed**
Options:
- **(a)** Label the surface by its cover: paved → pavement, planted → grass / shrub_hedge / tree. Tag the assessed
  region as "island".
- **(b)** A separate `island` class (id 7, currently disabled).

Recommendation: **(a)**. It keeps the class set small, and the vector map can find islands as closed curbs around
non-road. Revisit if the algorithms need the distinction. Island noses with no step (O-0007) are the hard case.

### R-009 Cycling and shared paths are pavement — proposed
- Consistent with R-002. Red-coloured paths remain pavement; the colour is an observation for RGB thresholds.

### R-010 Driveways — proposed, **decision needed**
Options:
- road;
- pavement, with `curb_lowered` at its edge;
- a separate `driveway` class (id 8, disabled).

Collect at least 3 examples (O- entries) before deciding.

### R-011 Vegetation split — proposed
- `grass`: turf and low ground cover, including bare soil in verges.
- `shrub_hedge`: anything planted above ground cover that is not a tree.
- `tree_trunk`: the trunk and main stems.
- `tree_canopy`: leaves and small branches.
- No numeric heights yet; the heights and roughness that separate these are what the threshold explorer should
  measure.
- Where a trunk starts and ends, and how trees are split into instances: R-015.

### R-012 Poles include their attachments — proposed
- **Rule:** arms, lamp heads and signal heads belong to the pole they are mounted on. A sign on a lamp post is part
  of the lamp post.
- Each pole gets its own instance id.

### R-013 Temporary objects and ghosts — proposed
- Vehicles → `vehicle`, people → `person`, cones and barriers → `temporary_object`.
- Streaks from objects that moved during the scan → `ghost`, not `noise`, so they can be studied and filtered
  separately.

### R-014 Noise — proposed
- **Rule:** isolated points, mixed pixels floating between a foreground edge and the background, below-ground
  multipath and sky noise → `noise`.
- A mixed pixel that lies near a surface (within about 1 cm) takes that surface's class.

### R-015 Trees: one instance per tree, identified by a band of trunk — proposed
Proposed 2026-10-23 at the user's request; revised 2026-10-26 to the user's design. The user wants trees as
instances, located by their trunks, for incident sketch plans. The definition should be easy to select and
extensible. Evidence: O-0012 (`tree_example`, streets 1); also O-0008, Q-16.
- **Instance:** one tree = one annotation directory, `tree NN`, inside `trees` or a directory of the labeller's
  choice (e.g. one per street). Everything labelled in it belongs to that tree:
  the directory is the instance (the label export gives each item's directory path). Stems that leave the ground at
  one root collar are one tree; stems with separate collars are separate trees, even in the same pit.
- **Ground height G:** the height, along the height axis, of a ground point clicked at the foot of the trunk.
  - Where shrubs hide the foot, click visible ground nearby. The user notes that the ground around most trees is
    nearly flat (2026-10-23), so this costs little.
  - Where the ground does slope, click as close to the trunk as possible: `tree_example` stands on ground that
    falls 0.3 m within 2.5 m (O-0012).
- **The band (every tree; it identifies the tree):** the trunk's points from 1.00 to 1.10 m above G → `tree_trunk`,
  in an item named `trunk 1.00 to 1.10 m`.
  - 1 m is the height at which Singapore measures girth (the Parks and Trees Act, amended in 2017, S 660/2017).
  - At that height the band is usually above the flare, the buttresses, low shrubs and the first branches.
  - A 10 cm band is a single cylinder to select. On `tree_example` it holds 47,527 points and leaves 0.9 cm rms
    off a circle.
  - The band's height is a parameter, kept with each tree.
- **Position (derived, not labelled):** the centre of a circle fitted to the band across the height axis, placed at
  height G. The circle's radius gives the girth.
  - On a leaning trunk the position sits off the foot by 1.05 m × tan(lean). For `tree_example` (18.5°) that is
    0.35 m.
  - A band at 0.10–0.20 m would land 1 cm from the foot, but it holds flare and shrub points (10,778 of 75,752
    left off the circle there).
  - Where the foot matters (leaning trees), the optional `trunk` item gives it.
- **Optional items, in the same directory:**
  - `trunk`: the whole trunk from G to the first main fork, where the stem stops being one cylinder (a branch or a
    split) → `tree_trunk`. It overlaps the band, which is fine. On `tree_example` the fork is at 3.0 m.
    - It includes the root flare, buttresses and exposed roots above the ground surface.
    - It excludes the ground (the class of its cover, Q-16), shrubs (`shrub_hedge`), leafy growth on the trunk
      (`tree_canopy`), and things fixed to the tree (stakes, guards, signs, lights: their own class).
  - `crown`: everything above the fork → `tree_canopy`, main branches included for now (Q-16 a).
- **Extensible:**
  - Each tree is a directory, and new parts are new items in it, e.g. main branches apart from leaves, a crown
    outline curve, or the tree pit's outline.
  - Nothing labelled earlier is ever relabelled.
  - A tree with only its band is complete for identifying and locating it. The item names in the export show what
    else was labelled.
- **Size (derived):** girth at 1.0 m, Singapore's measure, comes from the band's circle. For comparison with tree
  inventories, the diameter at 1.3 m (DBH, the forestry standard) can be taken from `trunk`.
  - Both are best measured across the stem, at right angles to its axis. On `tree_example` the band's level circle
    gives a girth of 1.20 m; across the stem it is 1.25 m.
  - Size does not decide the class: any woody plant with a trunk standing on its own is a tree, saplings included
    (Q-16 d).
  - A stem that forks below 1 m is still one tree here (one root collar, one position). Forestry would count two
    trees. Select a band on each stem and save each into the same directory.
- **Shrubs reaching the band:** the circle fit leaves out points more than 3 cm off the trunk's circle, and says how
  many. To keep them out of the band itself, use Pieces (`P`) or a lasso before saving.
  - If the trunk cannot be told apart at the band, raise the band (the tree keeps its own height), mark the item
    uncertain, and log an observation.
- **Hidden trunk** (behind a parked car or a bus stop): label what is visible, mark the band uncertain, and log an
  observation.
- **In the app** (the tree tool, built 2026-10-26):
  1. Press `Shift+U` and click the ground at the foot of the trunk.
  2. A cylinder appears 1.00–1.10 m above that point. Move it onto the trunk with W A S D, or click the trunk: in
     tree mode a click only slides the cylinder sideways. Set its radius.
  3. Press Enter to select, then "save the selection as a tree". The position, girth and fit are kept in the
     point set's provenance.
- **check (for the lint, later):** for each tree:
  - it has a band;
  - the band's circle fit is within 2 cm rms and covers at least a quarter of the circumference;
  - no two trees' positions are closer than the sum of their radii.

## decision queue

The open questions, each with a provisional "meanwhile" labelling choice and what to watch for, are in
[OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) (Q-01 … Q-22). Until a rule here is `decided`, label as its question's
*meanwhile* line says. The user decides after labelling, from evidence.

## changelog

- 0.1 (2026-10-16): first draft written during planning. R-002 decided by the user; everything else proposed.
