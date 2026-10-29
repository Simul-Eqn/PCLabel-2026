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

### O-0011 Lane lines can be magic selected in one click down to about 2,000 points/m² of road
- date / who: 2026-10-21, measured by Claude from the user's lane-line clicks and a thinning experiment
- scan: `streets 1.e57` (one scan position) · where: the bottom road, point sets `bottom_road_cleaned` and
  `br_middle_laneline`
- tags: marking, density, range, magic select, R-006, Q-14
- seen:
  - The road's density falls from about 70,000 points/m² at 20 m to 2,040 at 40–45 m, 856 at 50–55 m and 124 at
    70–75 m. Beyond about 25 m it falls roughly with the cube of range (grazing angle).
  - Intensity contrast is not the limit: the lane line stays 4.5 to 19 times the road's intensity noise (MAD)
    above the road at every range out to 60 m.
  - The user's magic select clicks on the lane line, per dash: one click wherever the road around the dash had
    more than 1,936 points/m² (out to 39–46 m); one to three from about 1,250 down to 417; 12 clicks at 159
    points/m² (74 m).
  - Seven dashes at 14–33 m thinned at random, three times each, then one click (2 cm pixels, gap 0.28 m,
    tolerance 2.0): the click got the dash (recall and precision ≥ 90 % against full density) in 100 % of trials
    at ≥ 3,000 points/m², 90–95 % at 1,500–2,000, 81–86 % at 500–1,000, 38 % at 350, 19 % at 250, none at 150.
    Thinning is optimistic: it removes points but does not widen the laser footprint or steepen the angle.
- evidence: [docs/figures/density_threshold.png](figures/density_threshold.png) (the three measurements on one
  page; the same charts as the session's dashboard).
- seen elsewhere: not checked (newroad 2, ExchangeGreen)
- implications:
  - **Tool:** the Density panel keeps the parts of a point set at ≥ 2,000 points/m² by default: where one click
    gets a dash. 500 points/m² is the floor below which one click rarely does.
  - **Rule:** markings (R-006, Q-14) labelled where the road is sparser than about 2,000 points/m² take extra
    clicks and are less complete. Whether such stretches are labelled at all, or flagged, is for the user to
    decide.
  - **Algorithm:** a marking detector tuned on dense road will meet sparse road beyond about 45 m in a single
    scan. Its scores should be split by local density (or range).
- status: open

### O-0012 A street tree leans 18.5°: its trunk at 1.3 m is 0.42 m from where it stands
- date / who: 2026-10-23, measured by Claude on the user's point set
- scan: `streets 1.e57` · where: point set `tree_example` (27,958,604 points), trunk base at about x 15.92,
  y 18.30, z −22.74
- tags: tree_trunk, tree_canopy, instance, lean, ground contact, R-015, Q-16, O-0008
- seen (circles fitted to 10 cm slices of the trunk, followed upward from the base):
  - **Ground contact:** the ground ring 5–30 cm outside the trunk (225,170 points) has a median height G of
    z −22.741, with p10 and p90 within 5 cm of it. The ground falls away from the tree: 0.3 m lower 2.5 m out.
    The point set stops 3.5 cm above G, so the foot of the trunk was left out of the selection.
  - **Shape:**
    - 0–0.1 m above G: radius 0.27 m (the flare).
    - 0.1–1.3 m: 0.21 to 0.19 m. Measured at right angles to the stem: girth 1.33 m at 0.5 m, 1.25 m at 1.0 m
      above G (Singapore's measure since 2017), diameter 0.38 m at 1.3 m (DBH). Those circle fits leave 0.3–0.4 cm
      rms, against 0.9–1.1 cm for horizontal slices of the leaning trunk.
    - The tree tool's fit (2026-10-26) on level bands above G: 1.00–1.10 m: 47,527 points, girth 1.20 m, 0.9 cm
      rms, 75 % of the circumference, position 0.353 m from the foot. 0.60–0.70 m: 0.198 m from the foot.
      0.10–0.20 m: 0.011 m, but 10,778 of 75,752 points were off the circle (flare).
    - Up to 3.0 m above G: one clean cylinder, with residuals of 0.8–1.4 cm rms.
    - At 3.0 m it forks: the residuals rise to 3–9 cm.
    - Leaves (points 2 m or more from the axis) begin at about 4.2 m.
  - **Lean:** 18.5°, toward azimuth 38°. The centre at 0.1–0.5 m above G is 1 cm from the centre of the flare;
    the centre at 1.2–1.4 m is 0.42 m away from it.
  - **Coverage:** the slices cover 81–89 % of the trunk's circumference, so circle fits are well constrained.
- evidence: scratchpad scripts `tree_profile.py`, `tree_axis.py`, `tree_base.py`, `tree_girth.py`, `tree_band.py`
  (this session)
- seen elsewhere: not measured. The user notes (2026-10-23) that the ground around most trees does not slope
  much, so this tree's ground is probably an exception, and that shrubs are sometimes right against the trunk,
  which makes the ground height hard to tell.
- implications:
  - **Rule:** with lean, a position taken from a band of trunk sits off the foot by the band's height × tan(lean).
    R-015 (the user's design) identifies trees by a band at 1.00–1.10 m, which is 0.35 m off here, and keeps the
    whole trunk as an optional item for where the foot matters. The bare stem up to 3.0 m and the leaves above
    4.2 m make such bands easy to select without a trunk-or-branch call.
  - **Algorithm:** a vertical-cylinder pole detector would miss or split leaning trunks like this one. Lean is a
    trunk-vs-pole feature (O-0008), since poles stand within a degree or two of vertical.
  - **Tool:** the tree tool (`Shift+U`, built 2026-10-26) selects and saves the band. Following the trunk slice by
    slice to measure lean, and give the foot, is a possible extension.
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
