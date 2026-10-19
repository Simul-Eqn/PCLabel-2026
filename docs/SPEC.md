# feature spec

What PCLabel does, feature by feature. Each section has a milestone tag ([ROADMAP.md](ROADMAP.md)). Data formats and
internals are in [ARCHITECTURE.md](ARCHITECTURE.md).

## 0. design principles: observation first

1. **Make every subtle signal visible.**
   - Height relative to the local road, in several encodings: wrap, contours, diverging, local relief.
   - Intensity contrast, RGB.
   - Roughness, verticality.
   - Density and occlusion, range and incidence.

   Switching between them is one key, and two of them can be shown side by side.
2. **Capturing an observation is nearly free.** One key drops a pin at the cursor and records the location, view
   state, measurements, local statistics and a screenshot. The labeller only types the sentence.
3. **Turn every disagreement into a candidate observation:**
   - rule checks (lint);
   - threshold-explorer errors;
   - corrections made after an assisted tool;
   - blind re-labelling of an area;
   - overlays of algorithm output.
4. **The standard is part of the product.** Rules have ids. Label operations can cite rules. Points can be
   selected by the rule they were labelled under. Unsure points are flagged and queued.
5. **Systematic sweeps beat wandering.** Patrol mode steps along a kerb station by station, and coverage maps show
   what has been looked at.
6. **Every number has its method.** Thresholds, fits and measurements always carry their parameters, point counts
   and residuals.

## 1. window, navigation, picking [M1]

- **Main window:** a central viewport with dockable panels: palette, tool options, layers / colour, frames,
  observations, statistics, profile, journal.
- **Status bar:** cursor xyz in scan and active frame, `h`, `hs`, class, flags, intensity, RGB, range, local
  density.
- **Views:**
  - perspective orbit (pivot at the point under the cursor);
  - fly (WASD);
  - **BEV orthographic** (top-down; the main labelling view, as in 3D-Curb's refinement);
  - front and side orthographic aligned to the active frame's axes.
- **Clip box** (x, y, z in the active frame) with handles, plus a quick **slab** (a thin horizontal band around
  `hs = 0`).
- **Point info on click:** all attributes plus **provenance**, i.e. the journal operations that last changed this
  point (tool, parameters, rules, time). Answers "why is this road?".
- **Display controls:**
  - point size (fixed / adaptive), round points;
  - **eye-dome lighting** (EDL; strength and radius), which shows shape even in flat colour;
  - background colour.
- **Scan metadata panel** (user-filled, stored in the project): scanner, date and time, weather and wetness,
  settings. Intensity and RGB depend on these, and observations show them.

**As built in M1** (`python -m pclabel [project] [--gl hardware|software]`):
- **Docks:** Display, Point info, Clip box and Scan metadata (the Window menu shows or hides them). The other
  docks arrive with their milestones.
- **Mouse, the same in every view** (changed 2026-10-19 after the first use: rotating around the point under the
  cursor made the view jump when a drag started, and the result depended on where the drag started):
  - left drag rotates. In perspective the camera orbits the surface point at the centre of the view. That point
    is found from the depth buffer when the button goes down, without moving the camera, and is shown as a cyan
    cross while rotating. Orthographic views turn their heading.
  - middle drag, or Shift + left drag, pans;
  - the wheel zooms towards the point under the cursor;
  - holding the right button in perspective flies: the mouse looks, W A S D move, Q E go down and up, Shift is 4×
    faster, and the wheel sets the speed. In orthographic views the right button pans.
- **Home** (key, View menu, toolbar) resets the view: perspective from the default direction, framing the bulk of
  the points. A toolbar holds Home, the four views, the selection tools, fit plane and the slice.
- **Views** (changed 2026-10-19 at the user's request: humans could not tell an orthographic top view from a
  perspective one):
  - Top, bottom, front and side are angles of the same perspective camera. They turn smoothly (0.25 s) around
    the surface at the centre of the view.
  - `T` looks straight down and `Shift+T` straight up (from below, e.g. to see drains under the road). Pressing
    either again turns back to where the camera was. Front looks along +y, side along −x.
  - The status bar names the view (top, bottom, front, side, or free).
  - Orthographic projection is a separate View-menu switch, named in the status bar when on.
- **Point info:** a click without dragging picks the nearest full-resolution point to the drawn point under the
  cursor and marks it. The panel shows:
  - world xyz (scan frame), class, flags, RGB, intensity (0–1 and raw);
  - range from the scanner (scan pose; hidden when the capture is `mobile`);
  - point counts within 5 and 10 cm (local density);
  - store index, source index (the export order), raw record index and tile.

  Provenance arrives with the journal (M3).
- **Status bar:** cursor world xyz and range while hovering, plus fps, drawn points, resident memory, streaming
  state and the GL mode. `h`, `hs`, class and density on hover come with M2's frames.
- **Clip box:** world-coordinate min/max fields with "from view" (the visible BEV rectangle, or a box around the
  orbit point) and "whole scene". The box is drawn as an outline. Dragging handles is not built yet.
- **Display:**
  - colour mode; intensity range (auto = 1st–99th percentile, applied on open, because every scan uses less than
    half of the stored range), intensity gamma; height range (auto = 1st–99th percentile of z); wrap period and
    offset;
  - point size, adaptive size with a maximum, round points;
  - EDL on/off, strength and radius;
  - background colour;
  - point budget.
- **Scan metadata** also records `capture` (static | mobile). It is saved to `project.json` as you type.
- **Software GL:** `--gl software` (or `gl: "software"` in `%APPDATA%\PCLabel\settings.json`) renders with Mesa
  llvmpipe on the CPU and never touches the GPU, for when the GPU is busy. Use a small point budget (300k
  default: about 15 fps).

## 2. colour modes and visual aids [M1 basic, M2 height and split view, M6 features]

All modes are computed in the shader from per-point attributes and uniforms. Changing a mode, period, frame or range
is instant: no CPU pass over points.

| mode | what it shows | for |
|---|---|---|
| RGB | camera colour | materials, paint colour, vegetation |
| intensity | return strength, gamma adjustable | paint, wet patches, materials |
| intensity contrast | intensity ÷ local median (radius R) | markings and material edges independent of range |
| RGB × intensity | both at once | general |
| height (z, h or hs) | linear ramp over a range | overview |
| **height wrap** | cyclic colour every P | cm-scale relief (below) |
| **multi-period wrap** | fine hue + coarse lightness | relief without wrap ambiguity |
| **contours** | iso-height lines every P | crossfall, bumps, kerb lines |
| **diverging** | blue below 0, white near 0, red above | "what is below the road" (drains) |
| **local relief** | h − smoothed h | depressions, ruts, small steps anywhere |
| class | label colours, per-class show/hide/solo | labelling |
| class over context | labels over a dimmed greyscale | labelling against context |
| flags | ambiguous / marking / … | review |
| density | points per area (cells) | occlusion shadows, no-point strips, scanner blind spot |
| range / incidence | distance and angle from the scanner origin | why a surface looks different; off for mobile scans |
| normal / verticality / roughness | local geometry (computed for a region) | curb faces, vegetation vs paving |
| overlay | an external per-point array, or a confusion map vs labels | comparing algorithms |

**Split view / swipe:** two viewports with synced cameras, or one viewport with a draggable swipe line, each side in
its own colour mode (e.g. RGB | wrap 3 cm). Correlations between appearance and geometry jump out.

**BEV raster overlays** at a chosen cell size (1–5 cm), computed with numba for the visible area and drawn as a
texture under or over the points:
- min / max / median height;
- **step map** (max height difference to neighbours within r);
- point count;
- median intensity.

They show curb lines and markings at a glance.

### 2.1 height wrap (detail) [M2]

Let `h` be a point's height in the active reference frame, or above the reference surface (`hs`), in metres.

- **Wrap.** `φ = fract((h − h₀) / P)`, colour `= C(φ)`, where C is a perceptually uniform **cyclic** colormap
  (e.g. colorcet CET-C6 or a twilight-like map) as a 256-entry texture.
  - P defaults to 0.03 m and ranges over 0.002–1 m, adjusted with `[` and `]` (×/÷ 1.5) or typed.
  - h₀ slides the band edges (`Shift+[` / `Shift+]`).
- **Multi-period wrap (the RoPE idea).** One period is ambiguous: at P = 3 cm, heights of 1 cm and 4 cm look
  identical.
  - Rotary position embeddings make positions unique by combining several frequencies; do the same here.
  - Hue comes from a fine period P₁ (3 cm). Lightness comes from a coarse period P₂ (default 10·P₁) as a triangle
    wave, so it has no seam.
  - Optionally a third, very coarse period P₃ drives saturation.
  - Within P₂ (or P₃) the height is then unambiguous, while differences below 3 cm still show as hue changes.
- **Contours.** Darken points within ε of a multiple of P, with `ε = k·P` (k ≈ 0.08, adjustable). Every 5th
  contour is bolder. Iso-height lines on the road reveal crossfall direction, humps, ponding hollows and the kerb
  line (where contours bunch up).
- **Diverging.** Colour by sign and size of h within ±R (default ±5 cm); white within ±δ (default 5 mm). Best for
  "below the road": drains, manholes, potholes.
- **Band.** Hide points outside [a, b] in any mode, e.g. keep −5 cm…+2 cm to isolate the road surface. Built
  (M2, part) as the **height slice**: `Shift+H`, default −10…+30 cm, along the active height axis (a fitted plane's
  normal, or z).
- **Shading.** Multiply wrap colour by EDL or by intensity (toggle), so shape and texture stay readable.
- **Noise guidance.** With range noise σ, a period below about 4σ shows mostly noise. The flatten tool reports σ
  (plane-fit RMS) and the UI suggests the smallest useful P. Optional display smoothing: h replaced by the median
  of the k nearest neighbours, for a region only.
- **Test:** a numpy mirror of the shader formula is unit-tested on synthetic heights, including band edges at
  multiples of P.

## 3. reference frames and surfaces: "make this area flat" [M2]

- **Flatten from selection** (`F`):
  1. **Input:** the current selection, or a lasso drawn with the tool active, optionally restricted to one class
     (e.g. road only).
  2. **Fit:** a robust plane. Least squares, then trim residuals beyond 2.5 × MAD, then refit (3 iterations); or
     RANSAC when clutter is heavy.
  3. **Report:**
     - normal and tilt;
     - **grade % along and crossfall % across** a chosen direction;
     - RMS residual (the noise estimate σ) and point count.

     The report can be attached to an observation.
  4. **Rotation:** the smallest rotation that takes the plane normal to +z (Rodrigues), then a yaw so +x runs
     along the road. The yaw comes from the selection's principal axis in the plane, from two picked points, or is
     left unchanged.
  5. **Translation:** the plane passes through z = 0 at the selection centroid.
- **Applied as a frame, never to the data.**
  - Named frames are saved in the project.
  - The **3D view renders in the active frame**: the whole scene rotates, the selected patch becomes level, and
    everything else shows its height relative to that plane.
  - The active frame drives height colouring, bands, the clip box, cross-sections, statistics and queries (`h`).
- **Reference surface** (`Shift+F`). A real road has camber and changing grade, so one plane is not enough
  everywhere. This generalises it:
  - **Fit:** a smooth surface fitted to the selected or road-labelled points. Use a grid of 0.25–0.5 m cells
    holding robust local heights (median after trimming, or a low percentile), fill holes by interpolation,
    smooth lightly.
  - **Output:** `hs = z − S(x, y)` for every point. Pavements and curb tops then show their **height above the
    extrapolated road**, so curb heights read directly off the colour.
  - **Storage and display:** saved as a small raster with its parameters; uploaded to the GPU as a texture for
    display.
- **Local relief:** `S` from all ground-like points with a large smoothing radius (e.g. 2–5 m). It shows cm-scale
  relief anywhere without choosing a road patch.
- One key cycles `z` → `h` (frame) → `hs` (surface).
- **Tests:** a synthetic road with 2.5 % crossfall and 1 % grade must be recovered to within 0.05 %. RMS must match
  the synthetic noise. A synthetic kerb gives `hs` = step height ± 5 mm.

**Built so far (M2, part, 2026-10-19, at the user's request):**
- `F` fits a robust plane to the selection (least squares, then 3 rounds of trimming at 2.5 × MAD) and makes its
  normal the **height axis**: `h = (p − origin) · normal`, with the origin at the centroid of the kept points.
- The height wrap, the height ramp, the slice, the status-bar readout and selection filters all use `h`.
- Switching to a plane resets the wrap offset to 0, so a band edge sits on the plane, and sets the height ramp to
  the slice range.
- The report (Height axis dock, status bar) gives:
  - points kept, selected and fitted;
  - tilt from vertical and RMS;
  - grade along the selection's main direction (with its azimuth) and crossfall across it;
  - the smallest useful wrap period (4 × RMS);
  - a warning when the fit does not look like ground (tilt > 10° or RMS > 5 cm).
- Frames are named `plane 1`, `plane 2`, …, saved in `frames.json` and listed in the Height axis dock. `H`
  switches between z and the last plane. `F` on the same selection again reuses that plane instead of adding a
  copy. "delete plane" removes the plane shown.
- Not yet:
  - the view does not rotate into the frame (heights are relative to the plane, but the camera still uses z up);
  - no yaw from two picked points; the frame's `along` is the selection's main axis;
  - no reference surface (`Shift+F`), no local relief, no `hs`;
  - no RANSAC option.

## 4. selection and labelling [M2 shapes and preview, M3 labelling]

- **Shapes:** lasso (freehand), polygon, rectangle and brush (screen circle; world radius in BEV). They work in BEV
  and perspective.
- **Depth modes** (perspective):
  - *visible surface*: only points not hidden behind others. Depth-tested against the depth buffer with a
    tolerance of about 3 cm, scaled with distance, so non-drawn LOD points are tested too.
  - *through*: everything inside the shape and the clip box.
- **Filters**, shown as chips in the tool bar and combinable:
  - clip box; height band in `h` or `hs`;
  - only unassessed; only class X (e.g. turn road into curb);
  - **locked classes** (never overwritten); visible classes only;
  - a query (section 9).
- **Combine:** add, subtract or intersect with the current selection; grow or shrink by distance; select connected
  within r.
- **Preview, then apply.** The selection is highlighted with its point count. A class key applies it; `Esc`
  cancels. `0` erases to unassessed.
- **Flags tool:** set or clear flags (`ambiguous`, `marking`, …) on the selection.
- **Full resolution:** applies to every point in the store inside the shape, not only the drawn points. The count
  and the time taken are shown.
- **Rule context chip:** while a rule chip (e.g. R-003) is active, every operation records that rule id. "Select
  by rule" later finds every point labelled under a rule that has changed.
- **History:** undo and redo (`Ctrl+Z` / `Ctrl+Y`), plus a journal panel listing operations. Click an operation to
  highlight its points.
- **Instances:** for countable classes, assign a new or existing instance id (connected components can split a
  selection, section 11).

**Built so far (M2, part, 2026-10-19):**
- Shapes: **lasso** (`L`) and **rectangle** (`R`), in every view. While a tool is active, the left button draws:
  plain replaces the selection, Shift adds to it, Ctrl subtracts from it. `Esc` drops the tool, then the
  selection.
- Depth mode *visible surface* only. Every full-resolution point inside the shape is tested against the last
  frame's depth buffer. The buffer is first min-filtered over 2 pixels so gaps between drawn LOD points do not
  let hidden surfaces through. Tolerance: 3 cm + 0.2 % of the distance.
- Filters: what is hidden is not selected. The clip box and the height slice apply.
- The selection runs in a worker thread. It is highlighted in yellow (up to 1M points drawn), and the count and
  time appear in the Height axis dock. Labelling the selection comes with M3.
- From the top, the visible surface under a tree is the canopy: to select the road beneath, turn the slice on
  first.

## 5. cross-sections and profiles [M4]

- **Draw a section line** in BEV, or press `X` at a point on a curb: the section is perpendicular to the local
  curb or road direction.
  - Slab width w (default 0.10 m) and length L (default 4 m).
  - A **profile dock** (pyqtgraph) shows distance along the section against height, with vertical exaggeration
    from 1× to 10×. Points use the current colour mode or label colours.
- **Label in the profile:** lasso in the profile view applies to those points. Best for toe, face and lip, drains
  and gutters.
- **Stepping:** with a polyline, step stations every Δs (default 0.25 m) with `,` and `.`. The 3D view and BEV
  follow.
- **Auto step measurement:**
  - Fit a low plane and a high plane on either side of the step.
  - Report step height, toe and lip positions, face slope, and the low- and high-side slopes.
  - Report the point count and residual of each fit.
  - Mark the result "insufficient" when either side has too few points. Never guess.
- **Profile strip export:** an image of stacked profiles along a polyline, for observations and reports.
- **Test:** a synthetic kerb of 0.15 m gives 0.150 ± 0.005 m. Occluded synthetic faces give "insufficient" or a
  flagged estimate.

## 6. measurement [M4]

- **Point to point:** total, horizontal and vertical components, in the scan frame and the active frame.
- **Point to plane / surface:** `h` and `hs` of a point.
- **Plane of selection:** grade, crossfall, RMS (same as the flatten report, without changing frame).
- **Step across a drawn line:** as in profiles, without opening the dock.
- **Instance spacing along a polyline:** e.g. distances between drain inlets along a kerb, as a list, mean and SD.
- Every measurement keeps its method, parameters, point counts and uncertainty, can be attached to an observation,
  and can go into a measurement report.

## 7. observations, bookmarks, decision workflow [M2 basic, M5 full]

- **Pin (`N`) at the cursor's 3D point** opens a non-modal dialog:
  - title, text;
  - tags, autocompleted from existing tags, class names and the open question ids (`Q-NN`, from
    [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md)), so evidence collects per question;
  - rule ids;
  - a **decision-needed** toggle.
- **Captured automatically:**
  - time; scan and project id;
  - the point in the scan frame, plus `h` and `hs`;
  - camera pose and the **full view state**: colour mode, period, frame, surface, clip box, filters, visible
    classes, split view;
  - a screenshot (PNG);
  - **local statistics** within 0.5 m: count, class histogram, `hs` and intensity percentiles;
  - any open measurement or profile.
- **Display:**
  - Pins appear in 3D and BEV.
  - An observations dock lists them, with search and filters (tag, status, decision-needed, scan).
  - Double-click restores the exact view state.
- **Bookmarks:** the same view state without a note (`Ctrl+B`). Bookmarks are listed with observations.
- **Storage:** `observations.jsonl` plus images in the project directory.
  - **Export for docs** writes one markdown block per observation in the [OBSERVATIONS.md](OBSERVATIONS.md)
    template.
  - `/ingest-observations` merges the export into the docs.
- **Decision queue view:** decision-needed observations grouped by tag or rule, with the number of examples each
  has.
- **Session log:** each session records duration, areas visited, operations and observations. Shown as a summary
  on close (and exportable), to help write up what was learned.

## 8. coverage: assessed regions and patrol mode [M5]

- **Assessed regions:**
  - polygons in BEV, with a z range and a status per class (complete / partial / not assessed);
  - shown as an overlay;
  - required by evaluation and statistics (R-001).
- **Patrol mode:**
  1. Define a polyline, e.g. along a kerb.
  2. The app steps through stations every Δs (default 0.5 m), showing BEV and the cross-section in sync.
  3. Per station, one key: OK / observation / ambiguous / unobservable.

  This produces a coverage record. Systematic stepping reveals periodic features (drain inlets) and transitions
  (lowered kerbs, where an island nose loses its step).
- **"Looked-at" heatmap** (optional): time spent per BEV cell during labelling.

## 9. queries and label lint [M5]

- **Query bar:** an expression over per-point fields that selects or highlights points.
  - **Fields:**
    - `class` (names allowed), the flag names, `instance`, `assessed` (inside an assessed region);
    - `x y z`, `h`, `hs`;
    - `intensity` (raw, and `i01` for 0–1), `r g b`, `hue sat val`;
    - `range`, `incidence`, `normal_z`, `roughness`, `step`, `density`.
  - **Operators:** comparisons, `and / or / not`, `in (...)`, `abs()`.
  - **Implementation:**
    - Parse with a whitelisted Python AST, evaluate with numpy or numba per tile.
    - Limit evaluation to a region (selection, assessed regions or the visible area).
    - Compute derived fields lazily and cache them.
- **Examples:**
  - `class == road and hs > 0.05`: humps, vehicles, mislabelled curb tops.
  - `hs < -0.03 and class != drain`: the R-003 check.
  - `class == curb and abs(hs) < 0.03`: lowered or flush candidates.
  - `class == pavement and intensity < 0.2`: dark pavement (wet? shaded? different material?).
- **Label lint:**
  - Runs every rule's `check:` from LABELLING_STANDARD.md (parsed from the markdown, or mirrored in a yaml file)
    over the assessed regions.
  - Hits are clustered into sites (connected components) and listed with counts. Each site has fly-to and two
    actions: **fix** (relabel) or **counterexample → new observation** (one key, pre-filled).

## 10. statistics and threshold explorer [M6]

- **Region:** a selection, assessed regions or the visible area.
- **Per class:**
  - count and density;
  - feature percentiles;
  - overlaid histograms (pyqtgraph, log toggle);
  - per-scanner comparison when several projects are open.
- **Threshold explorer:**
  - Pick a feature f and two classes (A vs B, or A vs the rest).
  - It shows the histograms, the threshold t and direction that minimise balanced error, and the error at a t you
    drag.
  - **"Show errors in 3D"** highlights points on the wrong side; inspecting them gives an observation or a relabel.
  - A 2-feature density scatter has an optional linear boundary.
- **Features:**
  - `hs`, `h`; intensity and intensity contrast; RGB and HSV;
  - normal_z and verticality; roughness (residual σ to a local plane in radius r);
  - step (max height difference within r); density;
  - range and incidence (need a single scanner origin);
  - column rank (height order within an xy cell).

  Every feature has explicit parameters (radius etc.), is computed per region with KD-trees on demand, and is
  cached.
- **Threshold report export** (JSON / CSV / markdown):
  - feature definition and parameters;
  - region, scans, standard version and hash;
  - class counts, chosen threshold, error rates.

  This report is what the threshold algorithms consume.

## 11. assisted tools [M7]

Each tool has a parameter panel, a live preview on a region, and an apply step that writes a journal entry with all
parameters.

- **Band select:** a height band in `h` or `hs` inside a lasso. The simplest threshold.
- **Region grow** from a seed click.
  - Accept neighbours within r if `|Δhs| ≤ t_h`, the normal angle is within `t_n`, `|Δintensity| ≤ t_i` and the
    colour difference (ΔE) is within `t_c`.
  - Stops at locked classes. The sliders update the preview live.
  - **Built early (2026-10-19, at the user's request) as Ground grow (`G`, `tools/ground.py`).** It uses
    intensity, not RGB, by the user's choice.
    1. Points around the seed (a uniform subsample) are binned into cells (10 cm). Per cell, the ground layer is
       the points within 10 cm of its 5th percentile height. It gives a median height, a median intensity and a
       roughness (standard deviation).
    2. The grow is 8-connected and steps over empty cells up to `max_gap` (30 cm), for sparse scan lines far
       from the scanner. A cell joins when all of these hold:
       - height change ≤ `max_step` (2 cm) per cell of distance: kerbs stop it;
       - roughness ≤ 3 cm: kerb faces, vegetation and objects stop it;
       - intensity change ≤ `intensity_step` (0.02) from the neighbour;
       - intensity within `intensity_range` (0.04, 1 = off) of the seed's.

       A walkway at road height but of another material therefore stops the grow.
    3. Markings: strips of other intensity up to `marking_width` (40 cm) are bridged, and enclosed holes are
       filled, but only at the grown surface's height.
    4. Selected: full-resolution points within `surface_tol` (4 cm) of their cell's surface. Drain floors below
       and objects above are not selected.
    5. The reach (30 m) limits it.
    - Shift+click adds, Ctrl+click subtracts. Changing a threshold re-grows from the same seed, reusing the
      cells.
    - The selection keeps its tool, seed and parameters for the journal (M3).
    - On the synthetic street (20 cm cells) it takes 98.5 % of road points and 97.6 % of markings, with 0.05 %
      non-road. With the intensity checks off it leaks over a lowered kerb onto the pavement.
- **Ground (GPF, Zermas 2017), adapted to TLS:**
  - Split xy into cells (the paper splits along the driving direction).
  - Per cell, seeds are the points within `Th_seeds` of the mean of the `N_LPR` lowest points.
  - Repeat `N_iter` times: fit a plane (PCA); ground = distance < `Th_dist`.
  - Parameters exposed: `N_LPR`, `Th_seeds`, `Th_dist`, `N_iter`, cell size. Output: a ground mask as a selection
    or filter.
- **Road boundary → curb candidates** (3D-Curb style):
  1. From road-labelled points, trace the road boundary in BEV (a raster at 2–5 cm with morphological clean-up).
  2. Propose curb points: within a band `w_out` outside the boundary, with `hs` in `[step_min, step_max]`, on or
     next to a step (`step ≥ s_min`).
  3. The labeller refines in BEV.
- **Connected components / instances:** split a selection by connectivity (radius r) and assign instance ids
  (drains, poles, trees, vehicles). The instance list shows count, size, position and height range, plus the
  spacing statistic along a polyline.
- **Pole helper:** click a pole to select a vertical cylinder (radius from a circle fit) over a height range, then
  refine.
- **Correction tracking** (the threshold-tuning evidence):
  - For every assisted operation, the app tracks later manual edits to the same area until the region is marked
    reviewed:
    - points taken out of the operation's class (false positives);
    - points of that class added within distance d of the operation's area (false negatives).
  - A report per tool and parameter set gives the precision and recall of the assisted tool against the
    labeller's final labels.

## 12. comparison and consistency [M8]

- **Overlays:** load a per-point array in source order (`.npy`, `.label`, LAS classification) from any algorithm or
  threshold.
  - Show it in class colours, or as a **confusion map** against the labels for a class mapping: TP green, FP red,
    FN blue, ignored grey.
  - Counts are given per assessed region.
- **Blind re-label:**
  1. Choose a region; its labels are hidden.
  2. Label it again.
  3. A disagreement map and a per-class confusion matrix follow. Both versions stay in the journal.

  Where you disagree with yourself shows where the definitions are vague, which feeds the decision queue.
- **Multi-scan:**
  - Open two projects side by side, e.g. RTC360 and BLK ARC at Exchange Green.
  - If they are aligned (check first; manual alignment or ICP comes later), transfer labels by nearest neighbour
    within d for comparison.

## 13. vector features [M9, later]

- **Feature types:**
  - polylines and arcs for curb toe, curb lip, road edges without a curb, and marking outlines;
  - points for poles, trees and drain inlets.
- **Attributes:** type, confidence, observed or inferred per segment.
- **Editing:** snap to points; draw in BEV with height taken from the points; derive candidates from point labels
  (boundary tracing) and refine.
- **Export:** local-frame JSON and DXF layers. This is the bridge to the sketch plan.

## 14. beyond labelling (speculative, low priority)

- **Measurement report:** the selected measurements with method, point counts and uncertainty.
- **Sight-line check** between two picked points at given eye and target heights (e.g. driver 1.1 m to pedestrian
  1.0 m). It counts points in a corridor along the line and lists the occluding classes (hedge, tree, vehicle,
  shelter).

## 15. suggested keymap

| key | action |
|---|---|
| `1`–`9`, `Shift+1`–`Shift+9` | apply class (from taxonomy.yaml) to the selection |
| `0` | erase to unassessed |
| `L` / `P` / `R` / `B` | lasso / polygon / rectangle / brush |
| `T` / `Shift+T` | top view (looking down) / bottom view (looking up); again: back (built) |
| `G` | ground grow tool: click the ground (built early, from §11) |
| `C` / `Shift+C` | next / previous colour mode |
| `W` | height wrap on/off; `[` `]` period; `Shift+[` `Shift+]` offset |
| `D` | diverging mode |
| `H` | cycle `z` → `h` → `hs` |
| `F` / `Shift+F` | flatten from selection / reference surface from selection |
| `X` | cross-section at the cursor; `,` `.` previous / next station |
| `N` | new observation pin; `Ctrl+B` bookmark |
| `M` | measure |
| `A` | toggle the ambiguous flag on the selection |
| `S` | split view / swipe |
| `Ctrl+Z` / `Ctrl+Y` | undo / redo |
| `Esc` | cancel the selection / tool (M1: clear the pick marker) |
| `F7` / `F8` | front / side orthographic view (M1) |
| `Home` | reset the view: perspective, default direction, framing the 1st–99th percentile box (M1) |
| `L` / `R` / `F` / `Esc` | lasso / rectangle / fit plane to the selection / drop tool, then selection (M2, built) |
| `H` / `Shift+H` | height axis z ↔ last fitted plane / height slice on and off (M2, built; `hs` comes later) |
| `K` / `Shift+K` | clip box on/off / clip box from the view (M1) |
| `Ctrl+E` | eye-dome lighting on/off (M1) |
| `Ctrl+O` / `Ctrl+I` | open project / import scan (M1) |
| right mouse + `W A S D Q E` | fly (M1; while the right button is held these keys move instead of their usual action) |

Built in M1: `T`, `C` / `Shift+C`, `W`, `[` `]`, `Shift+[` `Shift+]` and the rows marked M1.

## 16. ideas backlog

New ideas from labelling sessions go here, with the observation that prompted each and a suggested milestone.

- (empty)
