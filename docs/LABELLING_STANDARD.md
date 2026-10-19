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
| 20 | shrub_hedge | proposed | shrubs, hedges, planted beds | R-011 |
| 21 / 22 | tree_trunk / tree_canopy | core (split proposed) | trunk and stems / leaves and twigs | R-011 |
| 30 | lamp_post | core | lamp pole with arm and head | R-012 |
| 31 | traffic_light_pole | proposed | signal pole with arms and heads | R-012 |
| 32 | other_pole | proposed | sign posts, bollards, camera poles | R-012 |
| 41, 42, 44 | fence_railing, roof_shelter, street_furniture | proposed | structures and furniture | — |
| 50–53 | vehicle, person, temporary_object, ghost | proposed | temporary and moving things | R-013 |
| 60 | noise | proposed | points on no real surface | R-014 |
| 61 | other | core | assessed, fits no class | — |

Flags: `ambiguous`, `marking` (R-006), `under_cover`, `exclude`, `reviewed`.

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

### R-006 Markings are a flag, not a class — proposed
- **Rule:** paint takes the class of the surface it is on and gets the `marking` flag.
- Marking type (lane, edge, double yellow, zig-zag, arrow, text) is not per point for now. Use instances or vector
  features later if the algorithms need it.

### R-007 Lowered kerbs — proposed
- **Rule:** kerb units at crossings and driveways stay curb (`curb_lowered`) as long as the unit can be told apart
  (material, joint, or a step of a few cm). The ramp surface behind it is pavement, including the tactile tiles.
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

## decision queue

The open questions, each with a provisional "meanwhile" labelling choice and what to watch for, are in
[OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) (Q-01 … Q-22). Until a rule here is `decided`, label as its question's
*meanwhile* line says. The user decides after labelling, from evidence.

## changelog

- 0.1 (2026-10-16): first draft written during planning. R-002 decided by the user; everything else proposed.
