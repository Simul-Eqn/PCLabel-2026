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

### O-0013 ROCK's planimetrics draw a kerb as three lines plus a gutter: lip 14.5 cm above and 7 cm out from the toe
- date / who: 2026-11-04, measured by Claude on the DXF at the user's request (recorded at the user's request)
- scan: `mobile-colorized-v2-1` (ROCK mobile mapping, R3 Pro); drawing `mobile-colorized-v2-1.dxf` (ROCK
  planimetrics) · where: the whole drawing (not recorded per kerb)
- tags: curb, curb_top, curb_bottom, curb_back, gutter, road edge, ROCK, Q-01, Q-02, Q-10
- seen (medians over the lines' vertices, each layer against the nearest line of the other, densified every 2 cm;
  from the DXF alone, not checked against the scan's points):
  - TOP_OF_CURB is 7 cm out from BOTTOM_OF_CURB (p10–p90 4.6–9.0 cm) and 14.5 cm above it (p10 10.5, p90 15.0 cm).
    90 % at or under 15.0 cm, which is 6 inches: possibly a template profile rather than measured heights.
  - BACK_OF_CURB is 8 cm behind TOP_OF_CURB (p10–p90 6–12 cm) at the same height to the millimetre (p10, p50 and
    p90 all 0.000 m): constructed from the top line, not measured.
  - GUTTER is 0.51 m out from BOTTOM_OF_CURB (p10–p90 0.31–0.62 m; 82 % of its vertices within 1.5 m of it) and
    2.7 cm higher: BOTTOM_OF_CURB is the gutter's flow line.
  - PAVED_ROAD runs along GUTTER (4.9 cm) where there is a gutter, else near BOTTOM_OF_CURB: ROCK's road stops at
    the gutter.
  - FOOT_PATH runs along BACK_OF_CURB (4.8 cm, where it is within 1.5 m of it: 54 % of its vertices); PAVED_PARKING
    along BOTTOM_OF_CURB (4.5 cm).
  - Each curb layer has 55 polylines of about 2.8 km in all.
- evidence: scratchpad script `rock_line_gaps.py` (this session); earlier (O-0011's session) the lowest scan
  points below TOP_OF_CURB were a median 14 cm lower, consistent with the 14.5 cm.
- seen elsewhere: not checked (ROCK's r3_pro drawing `planimetrics-dataset-with-r3-pro-try-2-3d-1-.dxf`; no
  Singapore scan has a ROCK drawing)
- implications:
  - **Rule:** ROCK's model is R-004's option B (a kerb unit from toe to back) with Q-02 answered "draw all three
    lines" and Q-10 answered "the gutter is not road". The taxonomy's line labels `curb_top`, `curb_bottom` and
    `curb_back` (2026-11-04, proposed) let the labeller draw the same three lines; R-004 proposed addition: label
    the kerb's edges as these lines where they are visible.
  - **Lint:** for a labelled kerb with all three lines, `curb_top` should lie above `curb_bottom` by the kerb's step
    and `curb_back` within the kerb-top width of `curb_top`; a pair of lines more than about 0.3 m apart is a
    mislabel or an unusual kerb (not a §9 point query: a check between lines).
  - **Measurement:** check ROCK's lines against the scan's points: the step at TOP_OF_CURB measured in cross
    sections (is it really 15 cm everywhere, or a template?), and whether BACK_OF_CURB sits on a visible joint.
  - **View:** draw two lines' separation along them (a profile of the gap and the height difference), so a
    template-like constant shows at a glance.
- status: open

### O-0014 The plain mean of a trunk band sits 5.6 cm from the trunk's axis, toward the scanner
- date / who: 2026-11-04, measured by Claude on the user's tree bands
- scan: `streets 1.e57` · where: the 127 tree bands saved with the tree tool (directories `bli_trees`,
  `bri_trees`, `middle_trees`, `top_trees`)
- tags: tree, tree_trunk, marker, position, instance, R-015, R-017, Q-16, Q-17
- seen (each band's points against the circle the tree tool fitted to it, across the height axis):
  - bands: radius p10/50/90 3.1 / 8.4 / 18.5 cm; 31 % / 50 % / 84 % of the circumference covered; 20 / 46 / 73 m from
    the scanner; 56 / 486 / 6,794 points.
  - the points' mean is 1.6 / 5.6 / 13.4 cm from the fitted centre (p10/50/90), up to 31.4 cm; 0.35 / 0.73 / 0.86
    radii. 83 % of the offsets point toward the scanner (taken at the scan's origin).
  - by coverage: under 50 % (55 bands) median 6.9 cm (0.80 radii); 50–75 % (48) 4.8 cm (0.71); 75 % and more (24)
    3.4 cm (0.50).
- evidence: scratchpad script `tree_mean_bias.py` (this session). In theory a circular arc of half-angle a has its
  points' mean r·sin(a)/a from the centre: 0.64 r for a half circle.
- seen elsewhere: not checked
- implications:
  - **Rule:** a pole's or trunk's position should not be its points' mean; the circle's centre of a band (R-015's
    method) is not pulled toward the scanner. Proposed R-017: one marker per tree or pole, at its band's circle
    centre at the ground.
  - **Tool:** markers (Ctrl+M) offer "circle centre" besides "mean", and say how far the mean was from the centre.
  - **Algorithm:** a detector that places poles at the centroid of their points is biased by about 0.7 radii toward
    the scanner; position errors should be scored against circle centres.
  - **Measurement:** whether the circle fit itself is biased at low coverage (31 % at p10): compare with trunks seen
    from two sides where a scan has them.
- status: open

### O-0015 ROCK's pole and tree symbols sit at the foot, within 5–13 cm of the pole's axis
- date / who: 2026-11-04, measured by Claude
- scan: `mobile-colorized-v2-1` with `mobile-colorized-v2-1.dxf` · where: every LIGHT_POLE (25), TRAFFIC_SIGNAL
  (16), SIGN_BOARD (19), SINGLE_TREE (12) and ELECTRIC_POLE (3) symbol
- tags: lamp_post, traffic_light_pole, other_pole, tree, marker, position, ROCK, R-017, Q-17
- seen (for each symbol, the densest 5 cm column of scan points within 1 m and 1.0–1.5 m above it, a circle fitted
  to it; p10/50/90):
  - symbol to the column's circle centre: light poles 1.5 / 6.0 / 15.7 cm; traffic signals 3.9 / 9.0 / 31.2 cm;
    sign boards 0.8 / 4.9 / 17.2 cm; trees 5.0 / 12.7 / 73.6 cm; electric poles (3) median 8.7 cm.
  - symbol height above the ground under it (5th percentile of points within the column's radius + 0.4 m): light
    poles 1.4 / 4.2 / 7.7 cm, traffic signals 2.7 / 5.0 / 13.4 cm, sign boards 1.8 / 2.6 / 14.6 cm, trees
    4.3 / 6.3 / 9.7 cm.
  - The mobile scan sees each pole from one side: the columns cover only 6–19 % of their circumference, so these
    circle centres are themselves uncertain by several cm. The columns' plain means were 6–9 cm (median) off them.
- evidence: scratchpad script `rock_symbols_vs_poles.py` (this session)
- seen elsewhere: not checked (no Singapore scan has a ROCK drawing)
- implications:
  - **Rule:** ROCK places a pole's or tree's symbol at its foot, on or near its axis: the same reference as R-017
    (proposed), so labelled markers and ROCK's symbols can be matched and their distances scored.
  - **Measurement:** with markers placed by the band method on this scan, ROCK's position errors could be measured
    properly; a scan that sees poles from more sides would bound the circle fit's own error.
  - **Tool:** matching markers between two sources (ROCK's and the labeller's) within a radius, reporting
    detections, misses and distances, is a possible tool.
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
