# open labelling questions

Decisions to make **after** labelling, from what the scans show. Nothing here needs deciding before you start.

How this works:
- **Meanwhile:** until a question is decided, label as its *meanwhile* line says and set `ambiguous` on the cases it
  names. The meanwhile choice is always the one that is cheapest to change later. Usually it labels the larger set,
  so a narrower definition can be carved out afterwards with a query, without relabelling by hand.
- **Evidence:** tag related observations with the question id (e.g. `Q-01`). `/observe` adds them to the
  question's *evidence* line.
- **Deciding:** use `/decide Q-01: <choice>`. It writes the rule in [LABELLING_STANDARD.md](LABELLING_STANDARD.md)
  and moves the question to *decided* at the bottom.
- **Ready when** is a suggestion, not a gate.

Priority: **high** = affects most labelled points or the curb line; **medium** = common but local; **low** = rare or
cosmetic.

## curbs

### Q-01 Which points are "curb"? · R-004 · high
- **Decide:** the extent of the `curb` class across a kerb.
- **Options:**
  - **A. Face only:** the near-vertical riser between toe and lip.
  - **B. Kerb unit:** the face plus the kerb's top, back to its joint with the pavement or verge.
  - **C. Band:** a fixed-width band around the step.
- **Meanwhile:** **B**. Where the back joint is not visible, stop 0.15 m behind the lip and set `ambiguous` on that
  back edge. A and C can later be derived from B by a query (verticality, distance to the step).
- **Watch for:**
  - Is the joint between kerb top and pavement visible, in RGB, intensity or a small step? Note how often.
  - Measured kerb-top widths.
  - How many face points exist near the scanner vs at 20–40 m.
  - Kerbs whose face is fully hidden (the no-point strip, O-0005).
- **Where:** streets 1 south kerb (x 15..35, y −30..−18); any kerb on newroad 2; the same kerb type in ExchangeGreen.
- **Ready when:** joint visibility and top width noted at 5 or more places in 2 or more scans.
- **Evidence:** —

### Q-02 Which line is "the curb line" in the vector map? · R-004 · high (for M9, not for point labels)
- **Decide:** toe (road level), lip (top edge), face, or both toe and lip as separate lines.
- **Meanwhile:** nothing to do in point labels. Note which edge is better observed at each kerb.
- **Watch for:**
  - Kerbs facing the scanner: toe and face are visible.
  - Kerbs facing away: often only the lip is visible.
  - Which case is more common?
- **Ask people:** which line the sketch users draw, and what LTA's kerb lines represent (see the end of this file).
- **Ready when:** the sketch users have answered, plus 5 or more kerbs noted.
- **Evidence:** —

### Q-03 Lowered kerbs: which class, and where do they start? · R-007 · medium
- **Decide:**
  - (a) Is a lowered kerb unit `curb_lowered`, plain `curb`, or part of the pavement/road?
  - (b) What marks the start of a lowered section: a step height below some value, or the first visibly lower
    unit?
- **Meanwhile:** `curb_lowered` from the first unit that is visibly lower than full height to the last. Set
  `ambiguous` on the transition units. The ramp behind it is pavement.
- **Watch for:**
  - Step-height profile along each crossing or driveway (patrol at 0.25 m).
  - Full-height vs lowered heights, and the transition length.
  - Tactile tiles in RGB.
  - Whether the units can still be told apart from the ramp.
- **Where:** streets 1 east zebra crossing (O-0004); the west crossing (ambiguous earlier).
- **Ready when:** 3 or more crossings or driveways profiled.
- **Evidence:** O-0004 (to verify)

### Q-04 Road edges with no step: is there a curb? · R-004 / R-008 · medium
- **Decide:** at flush edges (island noses, island backs, road meeting grass or paving with ≤ 2 cm step), whether
  any points are `curb`.
- **Meanwhile:** `curb` only if a kerb unit is visible by material or joint. Otherwise label the two surfaces with
  no curb between them, and set `ambiguous` on a 0.2 m strip either side of the edge.
- **Watch for:** material, intensity or roughness changes at the edge; how much step there really is; vegetation
  hiding a kerb.
- **Where:** streets 1 island nose (x 2..12, y 15..25) and island back (x −12..−2, y 10..20) (O-0007).
- **Ready when:** 3 or more flush edges examined.
- **Evidence:** O-0007 (to verify)

## drains

### Q-05 What counts as `drain`? · R-003 · high
- **Decide:** for each type, `drain` or the surface it sits in:
  - kerb inlet opening;
  - grating in the road;
  - grating or slab over a drain on the pavement;
  - open channel;
  - sump;
  - manhole cover (sewer or utility, not drainage).
- **Meanwhile:**
  - inlet openings, recessed gratings and open channels → `drain`;
  - flush covers and slabs you could walk or drive over → the surrounding surface class, with `ambiguous` set;
  - manholes → `road` (or `pavement`).
- **Watch for:**
  - Each type you meet: its depth below the surrounding surface, size, whether it is flush or recessed.
  - Spacing between inlets along a kerb.
- **Where:** along every labelled kerb; the diverging ±5 cm view shows them.
- **Ready when:** each type seen 2 or more times, or noted as absent from the scans.
- **Evidence:** O-0002, O-0010

### Q-06 How far below the road is "clearly below"? · R-003 · medium
- **Decide:** the `hs` threshold that separates drains from ordinary unevenness, or that no single threshold
  works.
- **Meanwhile:** 3 cm (used in the R-003 lint check).
- **Watch for:**
  - Road-surface noise (flatten RMS) per scanner.
  - Depths of drain inlets.
  - Depths of non-drain dips: potholes, ruts, ponding hollows, manholes.
- **Ready when:** 10 or more drain depths and 5 or more non-drain dip depths measured.
- **Evidence:** O-0010 (depth seen may depend on range)

### Q-07 Does the curb continue across a drain inlet? · R-003 · medium
- **Decide:**
  - (a) Is the kerb lintel above an opening `curb` or `drain`?
  - (b) Does the curb line (Q-02) run unbroken across the inlet?
- **Meanwhile:** lintel → `curb`; the opening and everything below road level → `drain`.
- **Watch for:** whether the lintel has points at all; inlet widths; whether the step continues over it.
- **Ready when:** 5 or more inlets examined.
- **Evidence:** —

## surfaces

### Q-08 Islands and medians · R-008 · medium
- **Decide:**
  - (a) Label by cover (paved → pavement, planted → grass/shrub/tree) and tag the region "island"; or
  - (b) use a separate `island` class (id 7).
- **Meanwhile:** (a).
- **Watch for:** how island tops differ from pavements (material, height, access); how many are paved vs planted;
  whether you ever wanted a separate class while labelling.
- **Where:** the streets 1 junction islands.
- **Ready when:** every island in one scan is labelled.
- **Evidence:** —

### Q-09 Driveways and vehicle crossings · R-010 · medium
- **Decide:** `road`, `pavement` (with `curb_lowered` at the edge), or a separate `driveway` class (id 8).
- **Meanwhile:** `pavement` with `curb_lowered` at the road edge, and `ambiguous` on the driveway surface.
- **Watch for:** whether the pavement surface continues across; the material (concrete vs asphalt); the step at
  the road edge; how often driveways occur.
- **Ready when:** 3 or more driveways seen.
- **Evidence:** —

### Q-10 Gutter or channel strip along the kerb · R-005 · medium
- **Decide:** whether a concrete channel or gutter band between the road and the kerb is `road`, `curb` or `drain`.
- **Meanwhile:** `road`, with `ambiguous` set.
- **Watch for:** the bright line pair 0.6–0.8 m from the streets 1 south kerb (O-0006): paint or a channel? Width,
  material, slope change.
- **Ready when:** O-0006 resolved, and 2 or more other kerbs checked.
- **Evidence:** O-0006 (to verify)

### Q-11 One pavement class, or subtypes? · R-002 / R-009 · low
- **Decide:** keep `pavement` as one class, or split it into footpath / park connector / cycling path / covered
  linkway / bus-stop platform.
- **Meanwhile:** one class; put the subtype in observation tags; tactile tiles are pavement.
- **Watch for:** whether subtypes differ in intensity, RGB or geometry enough that an algorithm would need to know.
- **Ready when:** the threshold explorer (M6) has compared them.
- **Evidence:** O-0001

### Q-12 Park connector edge cases · R-002 (decided) · low
- **Decide:**
  - Where a park connector crosses a road: `road`?
  - Boardwalks and bridges: `pavement`?
  - The park connector's kerb against the road: `curb` as usual?
- **Meanwhile:** road / pavement / curb respectively.
- **Ready when:** each case seen once.
- **Evidence:** O-0001

### Q-13 Is every carriageway variant `road`? · R-005 · low
- **Decide:** whether these are all `road`:
  - concrete bus bays, road humps, rumble strips;
  - painted chevron islands, yellow boxes, zebra crossings;
  - flush manhole covers, coloured surfacing.
- **Meanwhile:** all `road`.
- **Watch for:** any case where calling it road feels wrong, e.g. a hatched area used as a parking or loading bay.
- **Evidence:** —

### Q-14 Markings: one class, or types as well? · R-006 · low
- **Decide:** whether `road_line` (one class, part of road, since 2026-10-28) is enough, or each marking needs a
  type (lane, edge, double yellow, zig-zag, arrow, text) per point or as instances. A type could be another class
  with `subset_of: road_line`, or a directory per type.
- **Meanwhile:** one class, `road_line`; paint off the road keeps the `marking` flag.
- **Watch for:** whether paint is separable in intensity at 20–40 m; yellow vs white in RGB; which types the sketch
  needs (the current product exports lane and arrow markings).
- **Evidence:** —

## vegetation

### Q-15 Where does grass end and shrub begin? · R-011 · medium
- **Decide:** the boundary between `grass` and `shrub_hedge`: height, roughness, or "could you walk on it".
- **Meanwhile:** `grass` = continuous ground cover you could walk on; anything planted above that = `shrub_hedge`;
  `ambiguous` on in-between growth.
- **Watch for:** measured heights and roughness of both (threshold explorer, M6); hedges overhanging kerbs.
- **Ready when:** 5 or more verges or beds measured.
- **Evidence:** —

### Q-16 Trees: where does trunk end, and what are tree pits? · R-011, R-015 · medium
- **Decide:**
  - (a) The trunk/canopy boundary: up to the first main fork, or include main branches.
  - (b) Tree pits and grates in the pavement: `pavement`, `grass` or `drain`?
  - (c) Tree instances (R-015, proposed; the user's design of 2026-10-26): one directory per tree, identified by a
    band of trunk 1.00–1.10 m above the ground at its foot, with the band's circle centre as the tree's position.
    Is the band's height right, and is the band enough where trunks lean (0.35 m off the foot on `tree_example`)?
  - (d) Small trees and saplings: trees like any other, or set apart (e.g. by girth at 1 m, Singapore's measure)?
- **Meanwhile:**
  - label trees as R-015 proposes: a band on every tree (the tree tool, `Shift+U`), `trunk` and `crown` when
    wanted;
  - main stems up to the first main fork → `tree_trunk`, everything above → `tree_canopy`;
  - a tree grate → `pavement`, soil in a pit → `grass`.
- **Watch for:**
  - trunks that a pole detector would confuse (O-0008);
  - grates you can stand on;
  - trees that branch or carry leaves at the band's height;
  - multi-stem trees;
  - buttresses;
  - bases hidden by hedges or cars;
  - shrubs right against the trunk, which hide the ground height (the user, 2026-10-23);
  - whether the ground around trees is flat (the user: mostly, 2026-10-23).
- **Ready when:** 5 or more trees labelled with a band, including a leaning one and one in a pit or grate.
- **Evidence:** O-0008 (to verify), O-0012

## objects and artefacts

### Q-17 Pole types and attachments · R-012 · medium
- **Decide:**
  - (a) Are `lamp_post`, `traffic_light_pole` and `other_pole` enough, or should sign posts, bollards and cameras
    be separate?
  - (b) What is a pole carrying both a lamp and signal heads?
  - (c) Do signs mounted on a pole belong to the pole?
- **Meanwhile:**
  - the three classes;
  - signal heads present → `traffic_light_pole`;
  - all attachments belong to their pole;
  - one instance id per pole.
- **Watch for:** combined poles; which pole types the sketch needs (the current product exports lamp posts and
  traffic lights).
- **Evidence:** —

### Q-18 Ghosts and temporary objects · R-013 · low
- **Decide:** whether ghost streaks are `ghost` or `noise`; and whether partly scanned vehicles and people need any
  special handling.
- **Meanwhile:** as R-013 (ghost, vehicle, person, temporary_object).
- **Evidence:** —

### Q-19 Mixed pixels and noise · R-014 · low
- **Decide:** how far off a surface a point can be and still take that surface's class (proposed: 1 cm).
- **Meanwhile:** 1 cm; beyond that → `noise`.
- **Watch for:** how thick the halo of mixed pixels is at kerb lips and pole edges, and whether it differs between
  scanners.
- **Evidence:** —

## labelling policy

### Q-20 Unsure points: guess + flag, or leave unassessed? · P3 · high
- **Decide:** whether unsure points inside an assessed region get a best-guess class plus `ambiguous`, or stay 0.
- **Meanwhile:** best guess + `ambiguous` (P3).
- **Watch for:** whether `ambiguous` gets used so much that it stops meaning anything.
- **Evidence:** —

### Q-21 How precise do boundaries need to be? · P6 · medium
- **Decide:** the target accuracy for class boundaries (proposed: about 2 cm where points exist).
- **Meanwhile:** 2 cm.
- **Watch for:** time per metre of kerb at 2 cm vs looser; whether 2 cm is achievable at 20–40 m range.
- **Evidence:** —

### Q-22 Which proposed classes and flags to keep? · taxonomy · medium
- **Decide:** keep, enable, disable or merge each `proposed` class in `config/taxonomy.yaml`; keep or drop the
  `under_cover` flag.
- **Meanwhile:** as in the yaml.
- **Watch for:** classes never used after a few sessions; pairs you often confused; something you kept labelling
  `other`.
- **Ready when:** after 3–5 labelling sessions.
- **Evidence:** —

## questions for people, not data

Ask once; the answers feed the questions above. Record answers here with the date.

- **Sketch users / product owner:**
  - Which kerb edge do you draw: toe, lip or face (Q-02)?
  - Which features must a scan-based sketch have: driveways, drains, pole types, marking types (Q-09, Q-14, Q-17)?
  - What position accuracy do you need?
- **Whoever captured the scans:**
  - Scanner model and settings for `streets 1` and `newroad 2`.
  - Capture date, time and weather (wet roads change intensity).
  - Are the three ExchangeGreen captures co-registered?
  - Was the BLK ARC used while moving or at fixed setups?
- Answers: —

## decided

Moved here by `/decide`, with the date and the rule id.

- —
