# observations

Things noticed in the scans while labelling. Observations are the main output of this stage: they turn into rules
([LABELLING_STANDARD.md](LABELLING_STANDARD.md)), lint checks, threshold experiments and tool features.

- **Ids:** `O-NNNN`, never reused.
- **Status:**
  - `open`: seen, not followed up;
  - `to verify`: from earlier work or hearsay, not yet checked in PCLabel;
  - `confirmed`: checked in at least two places or scans;
  - `became R-NNN`;
  - `rejected`: wrong, with the reason.
- **Adding entries:** `/observe <text>` adds one from chat. Observations captured in the app (the `N` key) are merged
  with `/ingest-observations`.
- **Location frame:** the scan's own coordinates (metres), unless a reference frame is named.

## template

```
### O-NNNN <one-line statement of what was seen>
- date / who: YYYY-MM-DD, <labeller>          · app id: <uuid if captured in the app>
- scan: <file>                                 · where: x, y, z or box [xmin, ymin, xmax, ymax]; view bookmark
- tags: <class names, feature words, decision-needed?>
- seen: <what exactly, in which colour mode / cross-section; counts and measurements with units>
- evidence: <screenshot path, measurement ids, query used>
- seen elsewhere: <other places/scans checked, with result; "not checked">
- implications: <rule (R-id, proposed change), lint check, threshold idea, tool/view feature>
- status: open | to verify | confirmed | became R-NNN | rejected (why)
```

## entries

### O-0001 Park connectors appear in the scenes and need a class
- date / who: before 2026-10-16, user
- scan / where: not recorded
- tags: pavement, park-connector, decision
- seen: Park Connector Network paths (pedestrians and cyclists) adjoin the road.
- implications: decided as `pavement` (R-002). Open: crossings over the road, boardwalks, the colour/material
  variety of these paths for RGB and intensity thresholds.
- status: became R-002

### O-0002 The only points below the road surface are drains that periodically interrupt the curbs
- date / who: before 2026-10-16, user
- scan / where: not recorded (add the scan and two or three example locations)
- tags: drain, curb, below-road, regularity
- seen: points below the road level occur only at drains, which interrupt the curb at intervals.
- implications:
  - **Rule:** R-003 (proposed); below-road points at curb gaps are `drain`.
  - **Lint:** `assessed and hs < -0.03 and class not in (drain, noise)` should come back empty; whatever it finds
    is a counterexample.
  - **Measurements to make:** depth range below the road; inlet length and spacing along a kerb (instances + the
    spacing statistic); whether the kerb lintel above the opening has points.
  - **Algorithm idea:** if the only below-road points are drains, a below-road detector is both a drain detector
    and a curb-gap explainer. A gap in a step-based curb line that coincides with below-road points is a drain,
    not a missing curb.
  - **Check in other scans:** newroad 2, ExchangeGreen (RTC360 and BLK ARC).
- status: open

### O-0010 Drain inlets only sometimes have points below the road level
- date / who: 2026-10-19, user
- scan: not recorded                           · where: not recorded
- tags: drain, curb, below-road, visibility, Q-05, Q-06
- seen: drains at the kerbs sometimes have points lower than the road surface, where the inlet goes down, but only
  sometimes. The labeller's explanation, not yet checked: the scanner has to be close enough to see into the inlet.
- evidence: not recorded
- seen elsewhere: not checked
- implications:
  - **Refines O-0002:** below-road points mean a drain (as far as seen), but a drain need not have below-road
    points. Whether its inside is seen may depend on the geometry (range and incidence from the scanner).
  - **Rule:** R-003 (proposed) labels drains by their below-road points. Proposed addition: an inlet with no
    points below the road is still `drain` where it can be identified otherwise (kerb gap, grating). As
    `proposed`; for the user to decide.
  - **Lint:** R-003's check still applies. A second check would list drain instances whose lowest `hs` is above
    −3 cm (drains seen without depth), with their range from the scanner.
  - **Measurement:** for each kerb inlet, its range from the scanner (Point info shows it) and the depth of its
    lowest points below the road. Plotting depth seen against range would show whether there is a range beyond
    which inlets look flat. Then check newroad 2 and ExchangeGreen.
  - **Algorithm:** a below-road detector would find only the drains seen from close by. Far drains need other cues
    (the kerb gap, grating intensity). Threshold studies on drains should be split by range.
  - **View:** the diverging ±5 cm view (M2, not built) would show below-road points. A "range from the scanner"
    colour mode would show where the depth stops being visible.
- status: open

### Prior observations from earlier work in this workspace (status `to verify`)

These come from `../Roadrun/docs/experiments.md` and `../Roadrun/docs/project_brief.md`, made on a 5 cm voxel
downsample of `streets 1.e57`. Coordinates are in that downsample's frame, which should equal the E57 frame (identity
pose); check on first open. Verifying them at full resolution is a cheap source of first observations.

### O-0003 Kerb steps on streets 1 are 0.12–0.33 m
- seen (earlier): 12 of 13 hand-picked kerb regions showed a real step of 0.12–0.33 m. Kerbs at the main-road
  junction mouth kept a full 0.13–0.23 m step. The south kerb at x 15..35, y −30..−18 was 0.17–0.21 m.
- implications: the step-height range for curb thresholds. A step of 0.33 m may not be a plain kerb (a drain edge?
  a planter?), so check what the tallest ones are.
- status: to verify

### O-0004 Lowered kerbs at the east zebra crossing of streets 1
- seen (earlier): both kerb ends drop from 0.12–0.20 m to 0.01–0.04 m along the stripes and step back up (23–40
  points per flank in the 5 cm data). Both ends of the west crossing were ambiguous.
- implications: examples for R-007 (`curb_lowered`) and for where a lowered section starts. Look for tactile tiles
  in RGB.
- status: to verify

### O-0005 No-point strip behind kerbs that face away from the scanner
- seen (earlier): at the north kerb of x 15..35, y −30..−18, the kerb face and toe fall inside a 0.4–0.8 m strip
  with no points (5 cm data). Toe and lip could not be told apart anywhere in that area.
- implications: whether the strip shrinks at full resolution. A density / range colour mode should make occlusion
  obvious. For R-004, option A (face only) fails here.
- status: to verify

### O-0006 Two bright lines 0.25 m apart, 0.6–0.8 m from the south kerb
- where: x 15..35, y −30..−18 (road direction about −26.6°)
- seen (earlier): an intensity-contrast raster showed two bright parallel lines; possibly paint or a gutter band.
- implications: check in RGB. Double yellow lines along the kerb would mean `marking` flags (R-006). A concrete
  channel would raise a road-surface question (is the gutter road?).
- status: to verify

### O-0007 Island noses and backs with little or no step
- where: island nose by the west crossing x 2..12, y 15..25; curved island kerb x −12..−2, y 10..20
- seen (earlier): the kerb was partly under shrubs; the nose had no vertical step; the island back changed only
  gradually by 0.01–0.04 m. About 69 % of one island-nose kerb had no step that a detector could find.
- implications: the hard cases for R-004, R-008 and R-011. Expect step-based thresholds to fail here; record what
  other indicators (roughness, RGB, intensity) separate island from road.
- status: to verify

### O-0008 A tree trunk can pass as a pole
- seen (earlier): of 6 automatic pole-like candidates on streets 1, about 4 were real poles and 1 was a tree (side
  view).
- implications: trunk vs pole features (straightness, a canopy above, radius, intensity) for the threshold explorer.
  Label both with instance ids.
- status: to verify

### O-0009 streets 1 has no real building facades
- seen (earlier): a RANSAC wall search found nothing. The scene includes an embankment, which a classifier without
  absolute height took for road.
- implications: `building_wall` stays disabled for now. The embankment is a good negative for road-surface
  thresholds (a flat-ish surface that is not road).
- status: to verify
