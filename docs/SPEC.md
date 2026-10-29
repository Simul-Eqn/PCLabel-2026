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
- **Docks:** Display, Point info, Clips (the clip box, the 3D select clip and the clip to point sets; it was "Clip
  box" in M1) and Scan metadata (the Window menu shows or hides them). The other docks arrive with their
  milestones.
- **Mouse, the same in every view** (changed 2026-10-19 after the first use: rotating around the point under the
  cursor made the view jump when a drag started, and the result depended on where the drag started):
  - left drag rotates, and so does right drag (changed 2026-10-19 at the user's request). In perspective the
    camera orbits the surface point at the centre of the view. That point is found from the depth buffer when the
    button goes down, without moving the camera, and is shown as a cyan cross while rotating. Orthographic views
    turn their heading.
  - middle drag, or Shift + left drag, pans;
  - the wheel zooms towards the point under the cursor;
  - fly: hold the right button (perspective) and press W A S D or Q E. From then until the button goes up the
    mouse looks instead of rotating: W A S D move, Q E go down and up, Shift is 4× faster, and the wheel sets the
    speed. (Before, holding the right button always flew, and in orthographic views it panned.)
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
| **labels** (built 2026-10-28) | annotation labels (D-007): every point set in its label's colour, the rest dim grey or hidden; segments and curves in their labels' colours (below) | seeing what is labelled, and how |
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

### labels view and label map (built 2026-10-28, the user's request)

- **Labels view:** `B` (or the "labels" colour mode, or the Labels panel's button) colours every point set's points
  by its label, its own or its directory's (D-007); segments and curves take their labels' colours too. Points in
  no shown labelled set are dim grey, or hidden with "only labelled points". `B` again goes back to the colour mode
  before.
  - Where point sets overlap, the smaller set shows: a `marking` set over the `road` set it lies in (R-006).
  - Done on the GPU: each point's label code is a fourth byte in the label buffer, so switching modes costs
    nothing; an annotation change rewrites the resident pages of the tiles its sets touch.
- **Legend** (the Labels panel): the labels in use (taxonomy classes in order, then flags, then other labels, then
  "(no label)"), with colour, items and points.
  - A label's colour is its taxonomy class colour; a flag or free-text label gets a palette colour once. Double-click
    a colour to change it. Colours are saved with the annotations (`label_colors`).
  - Untick a label to leave it out of the view and the map. "(no label)" starts unticked: on streets 1, the 32
    unlabelled point sets hold 848M point references and would make a map slow (22 s instead of 1 s). "tick all" and
    "tick none" set every label at once.
- **Export annotations by label** ("export annotations...", 2026-10-29, the user's request): the point sets,
  segments and curves whose labels are ticked, as an annotations file (the "export all" format, schema 5 with
  `labels_exported`).
  - Each item stays in its directories, which keep their names and labels but only the items exported, so the file
    imports with the same labels (an item that takes its label from its directory still has it).
  - "(no label)" ticked exports the unlabelled items too; an item with its own label in a directory of another
    label goes by its own.
- **Label map** (`Shift+B`, "label map..."): everything shown, projected straight onto a plane (the "map plane": z,
  seen from above, or a fitted plane), as a PNG with a legend strip on the right and a JSON sidecar
  (`pclabel.label-map/1`, ARCHITECTURE).
  - Map x runs along the plane's `along` direction, y across it (for z: x and y as stored). Parameters: pixel
    (5 cm), points grow by (0 px), line width (3 px); optionally only points within the height slice above the map
    plane, and every other point in light grey underneath for context.
  - Smaller point sets are drawn over larger ones, and segments and curves over the points (closed loops closed).
  - The JSON has the pixel-to-world transform, the legend (colour, class id or flag bit, pixels and points drawn per
    label) and the segments and curves as 2D lines in map metres. It runs in a worker thread (cancel stops it).
  - Measured on streets 1 (634M points; your labelled sets: 15M points, plus 6 labelled lines): 4,860 x 5,242 px
    at 5 cm in 0.97 s, 12,150 x 13,104 px at 2 cm in 1.90 s, and 8.1 s with every other point as grey context
    (`tools/labelmap.py` alone, without writing the PNG).

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
  copy. "delete plane" removes the plane shown; "rename..." renames it (names stay unique; undoable; records made
  with the plane keep the name it had then). "export plane" saves it as JSON (`pclabel.plane/1`): the
  equation `a x + b y + c z + d = 0` in scan coordinates with (a, b, c) the unit normal (so the left side is the
  height above the plane), the same as `z = p x + q y + r`, and the frame and its fit report.
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
- Two depth modes (both built; *through* added 2026-10-20 at the user's request), chosen in the Height axis
  dock or with `O`:
  - **visible**: only points not hidden by others. Every full-resolution point inside the shape is tested
    against the last frame's depth buffer. The buffer is first min-filtered over 2 pixels so gaps between drawn
    LOD points do not let hidden surfaces through. Tolerance: 3 cm + 0.2 % of the distance.
  - **through**: every point inside the shape at any depth, e.g. the ground under a canopy.

  The shape's outline is white for visible and orange for through. The selection records `depth`. The names
  follow the spec; the user had suggested "cloth" and "dense", but "cloth" is easily read as the cloth-simulation
  ground filter (CSF).
- Filters: what is hidden is not selected. The clip box and the height slice apply.
- The selection runs in a worker thread. Selected points are tinted yellow through a GPU-side bit, so exactly
  the drawn points that are selected are tinted (earlier, a random 1M of them drew as a separate overlay and
  looked speckled). The count and time appear in the Height axis dock. Labelling the selection comes with M3.
- From the top, the visible surface under a tree is the canopy: to select the road beneath, turn the slice on
  first.
- **3D select** (`U`, built 2026-10-20 at the user's request; `tools/volume.py`, `ui/volume.py`): a box or a
  cylinder standing along the height axis (z, or the active plane's normal). Every point inside is selected, at
  full resolution, not only what is visible.
  - **Place it:** click the cloud. It stands on the surface under the cursor; clicking again moves it there.
  - **Adjust it in the panel:** shape; length, width and turn (box) or radius (cylinder); bottom and top heights
    from the clicked point (default −0.2 to +2 m).
  - **Keys:** W A S D move it across the height axis (W away from the viewer, whether looking down or ahead), Q E
    lower and raise it, Z X turn a box by 5°. Shift: 5×. The step is set in the panel (10 cm).
  - **Enter** selects what is inside; Shift+Enter adds it, Ctrl+Enter subtracts it (also as buttons). While the
    tool is on, these keys go to the volume instead of the window's shortcuts (W is otherwise the height wrap).
  - **Loop, "extrude select"** (2026-10-20, at the user's request; a closed curve swept along its plane's
    normal). The user's case: draw the kerb line around a pavement area as a loop, then take everything inside.
    - Choose the shape "loop" with a closed curve selected in the Annotations panel (or press "use selected
      curve"). The volume runs from bottom to top along the curve plane's normal.
    - **inset** (cm) pulls the outline in evenly, the same distance everywhere, so the selection can stay clear of
      the kerb line. Negative values push it out. `-` and `=` change it by 1 cm (Shift: 5 cm). The selection uses
      the exact distance (a distance transform of the loop rasterised at 1 cm); the drawing offsets the outline
      along its normals.
    - Scaling about the loop's centre was asked for too and is there (scale %). For a long, thin area, though, it
      moves the ends far more than the sides, so inset is usually what keeps clear of a kerb.
    - turn (Z X) acts about the loop's centre. W A S D move the loop across its normal and Q E along it; a click
      moves its centre to the clicked point.
    - Named after CAD's "extrude": "prism" usually means straight sides. Locks help here: lock `curb` and kerb
      points stay out even with a small inset.
    - On streets 1, a 10 m loop with a 20 cm inset took 31.7M points in 0.49 s, and a 40 m one 212M points in
      2.8 s.
  - The clip box, the slice and locks apply. The selection records the shape, its base, the height axis and the
    parameters.
  - **Clip to the volume** (`K` in the tool, or "clip to this"; 2026-10-20, at the user's request): only what is
    inside the box, cylinder or loop stays visible. The point shader culls the rest per point: analytically for
    a box or cylinder, through the loop's mask for a loop. What it hides is not selected either, by shapes, grow,
    magic or 3D select.
    - **"Clip live":** the clip follows the volume while it is moved or sized, to see exactly what it cuts.
    - Outside the tool its outline stays drawn, dimmer. "Unclip", or the box in the Clip box panel, turns it
      off.
    - The axis-aligned clip box (`K` outside the tool, `Shift+K`) still exists; both clips can be on together.
    - Annotation lines are not clipped.
  - **"Vertical lines"** sets how many upright lines are drawn round the volume, spread evenly (24 by default;
    a box also gets its corners), to see what it cuts.
  - On streets 1 (GPU), clipping to a 4 m cylinder kept the frame at about 28 ms of GPU time for 23.6M points.
- **Locks** (built 2026-10-20 at the user's request): with labelling done on annotations (D-007), a point "has a
  class" when a point set holding it carries that label (its own or its directory's).
  - The "locks" menu in the Annotations panel lists the labels that point sets carry. A ticked label is locked:
    its points cannot be selected by any tool (shapes, ground grow, magic select, 3D select, "select points").
  - Taking points out of the selection is not affected.
  - The selection count says how many locked points were left out.
  - Locked labels are saved with the annotations and shown in red in the tree.
  - **"No class":** taxonomy classes marked `selectable: false` start locked. That is `other` ("assessed, fits
    no class"), the user's choice of 2026-10-20. Label a point set `other` and its points are not selected
    again, unless `other` is unticked in the menu like any other label.
  - **Locks on/off** (2026-10-20, the user's request): "locks on" at the top of the menu (also Select ▸ "Locks on")
    switches all locks off and on again. The choice of labels is kept. While off the button reads "locks (off)"
    and nothing is shown in red. Saved with the annotations.
- **Undo and redo** (built 2026-10-19 at the user's request, before labelling): `Ctrl+Z` undoes, `Ctrl+Y` or
  `Ctrl+Shift+Z` redoes. The Edit menu names the step ("Undo magic select (add)").
  - **Covered:** selection changes (shapes, ground grow, magic select, clearing, "select points"), fitting and
    deleting planes, and every annotation change. Annotation changes are new segments, curves, point sets and
    directories, moves, colours, renames, deletes and imports, and curve edits (gradient drags, added or removed
    points, "automatic").
  - **One step per action:** a seed-tool click and all its threshold changes are one step, and so is a whole
    handle drag.
  - **While drawing a segment or curve,** `Ctrl+Z` takes back the last clicked point. In curve edit mode, undo
    keeps editing the curve if it still exists.
  - **Limits:** the history lasts for the session (it is cleared when a project is opened). Selection snapshots
    share their index arrays, so a long history stays cheap: at most 200 steps, and at most 4 GB of selection
    arrays.
  - Point labels (M3) will be undone through the journal.

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
  - **Built early (2026-10-19, at the user's request) as Ground grow (`G`, `tools/ground.py`); renamed Grow
    select on 2026-10-20, where it is the "height and intensity" mode (the geometric mode is below).** It uses
    intensity, not RGB, by the user's choice.
    1. Every point within the reach is binned into 10 cm cells, keeping up to 48 per cell.
       - Capping per cell, not per tile, was a fix: per-tile sampling left the sparse road inside dense tiles
         near the scanner with too few points per cell. That showed as unselected speckle and straight cut lines
         along tile edges.
       - Per cell, the ground layer is the points within 10 cm of its 5th percentile height. It gives a median
         height, a median intensity and a roughness (standard deviation).
    2. The grow is 8-connected and steps over empty cells (far scan lines, pole shadows) up to `max_gap` (1 m).
       A cell joins when all of these hold:
       - height change ≤ `max_step` (2 cm), plus `max_slope` (8 %) × the extra distance across a gap: kerbs
         stop it, also behind a no-point strip;
       - roughness ≤ 3 cm;
       - intensity change ≤ `intensity_step` (0.02) from the neighbour;
       - intensity within `intensity_range` (0.04, 1 = off) of the **local road intensity**.

       The local road intensity is a running average along the grow, remembering about `intensity_memory`
       (2 m). Comparing with the seed instead cut the road along a line where intensity had drifted with range.
    3. Markings: strips of other intensity up to `marking_width` (40 cm) are bridged, and enclosed holes are
       filled, but only at the grown surface's height.
    4. Selected: full-resolution points within `surface_tol` (4 cm) of their cell's surface. Drain floors below
       and objects above are not selected.
    5. The reach (30 m) limits it.
    - **Where it stopped:** every refused cell records the first test it failed. The panel counts them, and the
      view marks them: red height, magenta roughness, cyan intensity step, blue intensity vs road, green gap.
    - Shift+click adds, Ctrl+click subtracts. Changing a threshold re-grows from the same seed, reusing the
      cells.
    - The selection keeps its tool, seed and parameters for the journal (M3).
    - On the synthetic street (20 cm cells) it takes 98.5 % of road points and 97.6 % of markings, with 0.05 %
      non-road. With the intensity checks off it leaks over a lowered kerb onto the pavement.
- **Grow select, geometric mode** (`G`, the mode box "geometric: surface normals"; `tools/normals.py`; built
  2026-10-20 at the user's request). It follows PCL's region growing segmentation
  (pointclouds.org/documentation/tutorials/region_growing_segmentation.html), on voxels:
  1. Points in a cube around the click are binned into 3 cm voxels (a hash table, so only occupied voxels cost
     memory). Each voxel keeps its centroid.
  2. Each voxel's normal and curvature come from PCA of the centroids within `normal_radius` (9 cm), as PCL
     computes them. The curvature is the surface variation λ0 / (λ0 + λ1 + λ2): 0 on a plane, 1/3 for scatter.
  3. Growth from the clicked voxel, as in PCL. A neighbour (within `connect`, 4.5 cm: the touching voxels) joins
     when its normal is within `smoothness` (6°) of the current voxel's, the sign ignored. It carries the growth
     on only if its curvature is below `curvature` (0.05). So the region follows surfaces that bend slowly and
     stops at creases, edges and rough surfaces. It works on any surface (walls, kerb faces, poles), not only
     ground.
  4. Every point in a grown voxel is selected. The voxels are built for 1.5 × the reach (6 m) and reused for
     threshold changes and nearby clicks. Switching the mode re-runs the last click; the click and its re-runs
     are one undo step.
  - On streets 1 (GPU), from the road at the magic-select site: 7.39M points of road in 2.3 s, building 1.05M
    voxels from 47M points. It stops at the kerb face; the straight edges are the 6 m reach. A re-run with 3°
    took 0.65 s.
- **Clean up the selection** (Select menu, "Clean up the selection..."; the Clean up panel; `tools/cleanup.py`;
  built 2026-10-20 at the user's request as "denoising" on its own). The case it was built for: a 3D select cut
  low, to keep lamp posts out, leaves grass blades and slightly raised pavement unselected.
  1. **Columns:** the selection is seen across the active height axis, in 5 cm columns. A column's surface is
     the mean height of its selected points; a column without any takes the mean of the columns around it
     (within 15 cm).
  2. **Footprint:** the columns with selected points. With "fill holes", also columns with points where at
     least 60 % of the columns around are selected.
  3. **Added:** unselected points in the footprint from 5 cm under to 10 cm over the surface around them.
  4. **Taken out:** selected points in columns where little around is selected (specks), and footprint
     islands of fewer than 200 points.
  5. **Preview and apply:** "preview" tints additions orange and removals magenta, and changing a setting
     previews again. "Apply" makes it the selection, as one undo step.
  - Only points visible through the clips and the slice, and not locked, are touched.
  - On streets 1 the user's case was curve 11 as a loop, inset 5 cm, −0.10 to +0.05 m, giving 27.3M points. The
    preview would add 739k (the grass at the island's tip, and the feet of people and poles) and take out 15
    specks, in 3.3 s in a worker.
- **Tree tool** (2026-10-26, the user's design; R-015, proposed): `Shift+U` (Select ▸ "Tree"), then click the
  ground at the foot of a trunk.
  - The clicked point's height along the height axis is the tree's ground height. A cylinder appears 1.00–1.10 m
    above it: the "Tree" box in the 3D select panel sets the band and the starting radius (0.5 m).
  - 3D select takes over: W A S D move the cylinder onto the trunk, and the radius field sizes it. A click on the
    trunk only slides the cylinder sideways in tree mode, so the band keeps its height. Enter selects.
  - "Save the selection as a tree" makes a directory `tree NN` inside the directory chosen in "save into" (default
    `trees` at the top level, made when missing; the choice stays for the next tree; the user's request of
    2026-10-26), numbered on from the trees already there. In it goes a point set `trunk 1.00 to 1.10 m`
    labelled `tree_trunk`. It fits a circle to the band across the height axis, leaving out points more than
    3 cm off it.
  - The circle's centre at the ground height is the tree's position. The position, radius, girth, rms, the share
    of the circumference covered and the ground point go into the point set's provenance (`"tool": "tree"`) and
    are shown in the box. Undoable.
- **3D select's keys in its number fields** (2026-10-26, the user's request): after typing a radius (or any field
  of the 3D select panel), W A S D Q E Z X and K still move, turn and clip the volume. Enter in a field takes its
  value and gives the keys back to the view, so the next Enter selects.
- **Uncertain labels** (2026-10-22, the user's request): the "unsure" box in the Annotations tree, or the
  "uncertain" button beside "set label" (on the selected items; again: sure), marks a label as uncertain: the
  taxonomy's `ambiguous` flag (R-001: kept out of scoring by default).
  - Only items (point sets, segments, curves) can be uncertain, not directories (the user, 2026-10-26).
    Directories have no box, and the button leaves selected directories alone.
  - Uncertain labels are in italics in the tree. Undoable; saved; "export labels" gives each item's mark and each
    label's uncertain points.
- **`ramp` class** (2026-10-22, the user's request): taxonomy 0.3, id 9, the sloped walking surface between the
  pavement and the road (R-007, proposed change; its extent is open).
- **`road_line` class** (2026-10-28, the user's request: "a road lines class, which is going to be a subset of the
  road class"): taxonomy 0.4, id 10, with the new taxonomy key `subset_of: road` (R-006, proposed).
  - Label the road's set `road` with its lines in it, and the lines' own sets `road_line`.
  - "Export labels" (schema 5) gives the parent its `subsets` and `points_with_subsets`, the subset its
    `subset_of` and `outside_parent` (its points in no set of the parent: to check), and marks parent ∩ subset
    overlaps `subset: true`.
  - The labels view and the label map draw the smaller set on top, so the lines show over the road.
  - The taxonomy loader refuses a `subset_of` that names no class or goes round in a circle.
- **Pieces: keep part of the selection** (2026-10-21, the user's request: a 3D select gives an object plus a few
  random points elsewhere, "contiguous subselect the object"): the selection falls into connected pieces, points
  joined through selected points closer than the gap (10 cm voxels, 26 neighbours: closer than 10 cm always join,
  up to about 35 cm may).
  - `P`: the pieces are found (a worker) and the small ones (under 200 points, the panel's setting) are tinted
    magenta. A click keeps the piece under it (the nearest within 30 cm); Shift+click adds a piece, Ctrl+click
    takes one away. All clicks work on the selection the tool started with; each is one undo step.
  - The Pieces panel: "keep the largest piece" and "drop small pieces", on the current selection.
  - Only selected points count: a part of an object the selection left out can split it in two.
  - Recorded: `{tool: pieces, action, params {gap, min_points}, pieces, kept (sizes)}`.
  - On streets 1: `bti_pavement` (9.8M points) took 0.24 s, 2 pieces (one lone point); `bli_outermask` (34.5M)
    0.67 s, 3,043 pieces, of which 2,961 small (34,637 points); `bottom_road_cleaned` (121.1M) 2.01 s, 327 small
    pieces (6,946 points).
- **Density** (2026-10-21, the user's request: "select the parts that are dense enough that it has reasonable
  quality to e.g. magic select road lines out"): "density..." in the Annotations panel (or Select ▸ Density) on a
  point set, the sets in a directory, or the selection.
  - Points per m² across the height axis, in 25 cm columns averaged over the occupied columns within 50 cm (road
    edges and car shadows do not pull a column down). Dense pieces under 1 m² are dropped.
  - The set becomes the selection; the sparse part is tinted magenta and follows the settings live (a worker).
    "Select the dense part" or "save as a point set" (`<name> dense ≥2000/m²`, the set kept); both record the
    parameters.
  - **Default 2,000 points/m²** (points about 2.2 cm apart), from O-0011: on streets 1 one magic select click got a
    whole lane dash at this density and above, in 90 % or more of thinned trials and for every real dash the user
    clicked; below 500, one click rarely did. The figure is `docs/figures/density_threshold.png`.
  - On streets 1 that is about 43 m from the scanner on the bottom road.
- **Flags as labels** (2026-10-21): the label box offers the taxonomy's flags after its classes. A `marking` point
  set overlapping the road set gave the road lines (R-006; since 2026-10-28 they are the `road_line` class, and
  `marking` is for paint off the road); "export labels" marks each label's `kind` (class, flag or text) and lists
  the overlap.
- **Undo keys in number fields** (2026-10-21, a bug the user found in 3D select): Ctrl+Z / Ctrl+Y in a spin box
  undo the app. Before, a spin box that kept the focus took them for its own text undo, stepping its number back
  (the vertical lines changed and nothing was undone). Text fields keep their own undo while typing.
- **Set operations on point sets** (2026-10-20, the user's request; replaced "merge points"): "set operations..." in
  the Annotations panel opens a box with A, B and the result:
  - A ∪ B (in A or B), A ∩ B (in both), A \ B (in A but not in B), B \ A, and A △ B (in one of them only).
  - A and B are point sets, or directories standing for all the point sets inside them. They start as the first
    two entries selected in the tree.
  - The box shows the points in A, in B and in the result as the choice changes.
  - "As a new point set" adds the result at the top level, named e.g. "points 4 ∪ points 5", with provenance
    `{tool: set_operation, op, a: {name, points}, b: {name, points}}`. A and B stay as they are; undoable.
    Merging is a union saved this way (delete A and B afterwards if they are no longer wanted).
  - "As the selection" makes the result the selection instead (clips, slice and locks apply, as for "select
    points").
- **The clips everywhere** (2026-10-20): what the clip box, the 3D select clip, the clip to point sets and the slice
  hide is not selected. This applies to shapes, grow select (both modes), magic select, 3D select, "select points"
  and set operations from the Annotations panel, and the clean-up.
- **Clip to point sets** (2026-10-20, the user's request: "only show points inside the set and algorithms only run
  within these points, nothing outside"):
  - Select point sets or directories in the Annotations panel and press "clip to" there, or `Ctrl+K`, or "use the
    selected point sets" in the Clips panel. A directory stands for all the point sets in it.
  - Only those points are drawn. Clicks pick only them, so seeds and curve points land on the set.
  - The tools read only the set's points, not just trim their results:
    - grow select (both modes) and magic select build their grids, pictures and voxels from the set alone. A grow
      therefore cannot leave the set and come back in through points outside it;
    - the clean-up's surface and footprint come from the set alone;
    - shapes, 3D select, "select points" and set operations "as the selection" keep only the set's points.
  - `Ctrl+Shift+K` (or the checkbox in the Clips panel) turns the clip off and on again; the set is kept.
  - The clip holds the points of the chosen sets when it was made: later edits to those point sets do not change
    it until "clip to" is pressed again. It is not saved with the project, like the other clips.
  - On streets 1 (GPU), clipping to `bti_pavement` (26.0M points) took 28 ms, and the frame cost stayed at 1.5 ms
    GPU. From a seed on that pavement, grow select took 25.4M points, all in the set, in 0.58 s; unclipped it
    took 190.6M points (13 % in the set) in 3.33 s. Geometric grow: 20.1M points in 0.70 s, against 36.5M (54 %
    in the set) in 2.53 s.
- **Inputs** take one more decimal than before in the tool panels (grow select, magic select, 3D select, split,
  slice, clip box).
- **Split by roughness** ("split..." in the Annotations panel, the Split panel; `tools/split.py`; built
  2026-10-20 at the user's request as "geometric segmentation"). It splits a saved point set in two:
  1. **Features:** the set's points are binned into 3 cm voxels. From the set's voxels within 9 cm, each gets the
     curvature (surface variation) and the roughness (spread off the local plane, shown in mm). The set becomes
     the selection (yellow).
  2. **Split:** voxels above the threshold of the chosen feature are rough (tinted orange); points follow their
     voxel. "Auto" sets Otsu's threshold (log scale, weighted by points). The panel shows the feature's 10 %,
     half and 90 % values over the set.
  3. **Clean-up,** so stray rough points in the middle of smooth ground (and the other way round) do not stay:
     - **majority:** a voxel whose neighbourhood within 6 cm (by points) is at least 70 % the other group
       changes group, for 2 rounds;
     - **islands:** connected pieces of one group with fewer than 200 points join the other group.
  4. **Points to look at** (tinted magenta, on by default): voxels the clean-up changed, voxels within ±25 % of
     the threshold, and voxels with too few neighbours for a feature.
  5. **"Split into two point sets"** replaces the set by "<name> smooth" and "<name> rough", in its place, with
     its label. How it was split is added to their provenance. Undoable.
  - The preview tints come from two more GPU-only bits beside the selection (flags bits 6 and 5), so every point
    shows exactly. Priority: magenta over orange over yellow.
  - On streets 1, a 6 m cylinder of 4.83M points (road, kerb, verge, lamp post) took 0.61 s for features and
    preview, and each change about 0.1 s. The grass verge came out rough and the road smooth. Points to look at
    lay along the kerb edge and on the edges of road markings.
- **Magic select by intensity** (`I`, `tools/magic.py`). Built at the user's request (2026-10-19) for labelling
  road markings (lane dashes, arrows, text): like the magic wand of an image editor, on intensity, with a
  tolerance. It works on an intensity picture of the ground.
  1. **Picture:** the ground surface comes from 10 cm cells, as in ground grow. Points within `surface_tol` (4 cm)
     of it are binned into 2 cm pixels (`pixel`), holding mean intensity and mean height. Vehicles, canopy, kerb
     faces and drain floors are not in it.
  2. **Gaps:** far from the scanner the points lie on scan lines several cm apart, so a dash is a row of separate
     pieces. Empty pixels within `max_gap` / 2 (20 cm gap) of data take the nearest pixel's values, as if the
     points were drawn larger, so the pieces touch. Stepping over gaps along 8 directions (as ground grow does)
     missed pieces at other angles.
  3. **Fill**, 8-connected from the clicked pixel. A neighbour joins when:
     - its intensity is within a **factor** `tolerance` (2: half to twice) of the clicked pixel's. A factor, not
       a difference, because the spread of paint grows with its intensity. On streets 1, road is 0.03–0.10
       everywhere, a near dash 0.15–0.25 (12 points per pixel), a far dash 0.13–0.55 (single points). Any fixed
       difference wide enough for the far dash takes road near the scanner.
     - its height differs by at most `max_step` (2 cm), so it does not climb a kerb onto kerb paint.
  4. **Options:**
     - `follow drift` (m, 0 = off) compares with a running geometric average along the fill instead of the
       click, for a long line that fades with range.
     - `contiguous` off takes every pixel within tolerance on the surface the click is on (the ground reachable
       without a height step, e.g. the road between its kerbs), connected or not: all the dashes of a line.
       Where that ground joins pavement without a step (driveways, under trees), bright patches there come
       too. The clip box limits it.
  5. **Selected:** every ground point in a selected pixel. Edge pixels mix paint and road, so the edge is precise
     to one pixel.
  6. **Reach:** a square of ±`radius` (15 m) around the click. The picture covers 1.5 × the reach and is reused
     by later clicks whose square fits inside it. Cells and pixels lie on fixed lattices, so a result does not
     depend on which picture was reused.
  - Shift+click adds (the next dash), Ctrl+click subtracts, and a changed threshold re-runs the last click.
  - The panel shows the clicked intensity, the accepted range and the counts. "Show where it stopped" (off by
    default) marks the refused pixels: blue intensity, red height step, green gap.
  - The selection keeps tool, seed and parameters for the journal.
  - **Measured** (details in ROADMAP M2):
    - On the synthetic street (1M points) a click on a centre dash takes all of that dash (99.4 % of the
      selection is marking), no other marking and no non-road.
    - With `contiguous` off it also takes the other dashes and the yellow lines (88 % marking: edge pixels).
      Nothing comes from the pavement or grass, which are as bright.
    - streets 1, GPU:
      - near dash: 6,766 points on 0.22 m² in 0.70 s, including building the picture (176M ground points);
      - far dash: 175 points on scan lines about 10 cm apart, complete, in 0.40 s;
      - a re-run on a reused picture takes 0.06–0.15 s.
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

**Built early (2026-10-19, at the user's request): segments, curves and annotation directories**
(`ui/annotate.py`, `store/annotations.py`, `analysis/curves.py`).
- **Segment** (`V`): click two points of the cloud, near or far apart (navigate between the clicks). Each click takes
  the nearest full-resolution point, as a plain click does. The segment joins the two points in 3D.
- **Curve** (`Shift+V`): click points in order (Backspace takes the last one back, Esc drops them), then Enter.
  1. A plane is fitted to the points, in one of two modes (chosen for new curves in the panel, and per curve in
     edit mode; changed 2026-10-20 at the user's request):
     - **through the ends** (the default): the plane contains the first and the last clicked point, so the curve
       starts and ends exactly there. Its turn about the line between them is fitted to the other points (least
       squares of their distances). With no other points off that line, it is the plane through the ends closest
       to perpendicular to the height axis;
     - **perpendicular to the height axis** (z, or a fitted plane): its height is the points' mean height, the
       least-squares fit for a fixed normal. This was the only mode before.
     - **fit all points** (added 2026-10-20 at the user's request): the least-squares plane through all the
       clicked points at any tilt (normal: their smallest-variance direction), through their centroid. Unlike
       "through the ends", the ends may move a little. Points on a line get the plane through it that is
       flattest on the height axis.
  2. Each clicked point is replaced by its closest point on the plane (a *node*). The panel reports the plane (its
     height, or its tilt from perpendicular to the height axis) and how far the points were from it.
  3. The curve runs through the nodes in that plane: cubic Bézier pieces between consecutive nodes. By default
     each node's handles point along the chord from its previous to its next node, a third of the adjacent piece
     long, which gives a smooth curve.
  4. **Closed loops** (2026-10-20, at the user's request): Shift+Enter finishes the curve as a loop, and so does
     Enter with "closed loop" ticked for new curves. One more piece joins the last node back to the first, and
     the automatic handles wrap round, so the loop is smooth at the join too. A loop needs at least three points.
     In curve edit mode the "closed loop" box opens or closes the edited curve (undoable). Every node of a loop
     has both handles, and a point added on the closing piece goes after the last node.
  5. **Join** (2026-10-20, at the user's request): select two open curves and press "join curves".
     - Choose which is first and which end of each meets the other. The closest pair of ends is offered first,
       and both ends and the joining line are drawn before you confirm.
     - The joined curve takes the first curve's place in the tree, its name, colour, label, plane mode and height
       axis. Its plane is fitted again to all the clicked points: with "through the ends", through the joined
       curve's outer ends.
     - Manual handles are kept (swapped on a reversed curve). Ends closer than 2 cm become one node. "And close
       the loop" also joins the outer ends.
     - Both curves are replaced; undo brings them back.
- **Curve edit mode** follows the curve's creation, or comes from "edit curve" or a double-click in the panel. Only
  the nodes react to the mouse; dragging elsewhere still moves the camera.
  - Click a node to give it a gradient: its two handles appear (yellow).
  - Drag a handle to set the gradient there. The handle follows the mouse on the curve's plane. The other handle
    turns to stay on the same line (the curve stays smooth) and keeps its length.
  - **Shift+click** on the cloud adds a point. Its place in the order is where the curve passes closest: between
    the two nodes of that piece, or before the first / after the last node when it lies beyond that end.
  - **Ctrl+click** on a node (or Del with a node active) removes it. A curve keeps at least two.
  - After an add or a remove the plane is fitted again. In "through the ends" mode a point added beyond an end
    becomes the new end. Manual handles keep their shape, turned into the plane if it turned; the new node's
    handles are automatic.
  - "Automatic" gives the node back its automatic handles. Enter, Esc or "done" ends the mode.
  - **"Show clicked points"** (for debugging a curve that looks unexpected) draws where each point was clicked
    (magenta) and a thin line to its node on the curve's plane. A long line means that click was far off the
    plane: a mis-click on another surface, or a plane mode that does not suit the points.
- **Annotations panel** (next to Height axis): a tree of **directories** and **items** (segments, curves, point
  sets).
  - New segments and curves stand alone at the top level, each with its own colour. `Ctrl+G` makes the current
    selection a point set, e.g. a magic-selected marking.
    - It goes inside the directory selected in the tree, or beside the one item selected there (into that item's
      directory); with nothing or several things selected, at the top level (2026-10-26, the user's request).
    - `Ctrl+Shift+G` always puts it at the top level, outside every directory.
  - "Duplicate" (`Ctrl+D`) copies the selected directories (with everything in them) and items. Each copy goes right
    after its original, named "<name> copy", with the same labels and uncertain marks; point sets add a `duplicate`
    record to their provenance. The copies are selected afterwards, e.g. to drag them elsewhere. Undoable
    (2026-10-26, the user's request). The point set keeps every selection operation since the last plain replace
    (tool, seed, parameters, result), so the thresholds that made it are on record.
  - "New directory" makes one at the top level, or inside the selected directory.
  - Drag items or directories onto a directory to put them in it. Dropping beside an item puts them in that item's
    directory; dropping on empty space brings them back to the top level.
  - **Colour:** everything inside a directory is drawn in the directory's colour, so separate items can share one.
    A directory made inside another takes its parent's colour until given its own ("colour"). An item back at the
    top level shows its own colour again.
  - F2 (or "rename") renames the selected directory or item, wherever it is in the tree: it always edits the name,
    whichever column was clicked, and works while the 3D view has the keyboard (2026-10-26). Double-click renames,
    or edits a curve.
  - "Select points" makes the point sets among the selected items (and inside selected directories) the
    selection. "Delete" asks first; a directory goes with everything in it.
  - Selected items draw thicker.
- **Labels** (the user's decision, 2026-10-20: labelling is done by labelling annotations, see ARCHITECTURE D-007).
  - A directory or an item can have a label: a taxonomy class picked from the list, or any text (e.g.
    "lane line").
  - An item without a label of its own takes the label of the nearest enclosing directory that has one. The tree
    shows it in grey, with where it comes from.
  - Set it with the label box and "set label" (empty removes it), or by double-clicking the label column.
  - **"Export labels"** writes everything grouped by label instead of by directory (`pclabel.annotation-labels/4`).
    For each label it gives its taxonomy class id (if it is a class), its items (each with its directory path and
    where the label comes from), counts by type, and the number of distinct points. Unlabelled items are listed
    separately, and the points shared by two labels are counted.
- **Saved** with the project after every change (`annotations.json`, point sets in `annotations/`).
- **Export / import:** one JSON file (`pclabel.annotations/1`, ARCHITECTURE "annotations"): "export all" or
  "export selected".
  - Coordinates are in the scan frame, with the offset applied. Curves carry their plane, nodes, handle positions,
    which nodes are manual, the clicked points and a polyline about 2 cm apart. Point sets are source indices.
  - Importing adds the file's tree at the top level as copies. Point sets from another scan (by sha256 and point
    count) are left out with a warning; their geometry is kept.
  - The earlier groups format (`pclabel.groups/1`) is not read any more (the user did not need it). streets 1's
    groups were converted once: a single-member group became that item, the two-point-set group a directory.
- Not yet: snapping, attributes (type, confidence, observed or inferred), DXF, undo. Annotations are not labels, so
  they do not go through the label journal.

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
| `G` | grow select: click a surface; height and intensity, or geometric (normals) (built early, from §11) |
| `I` | magic select by intensity: click a road marking (built early, from §11) |
| `O` | lasso / rectangle: select hidden points too (*through*), or only visible ones (built) |
| `U` | 3D select: a box or cylinder along the height axis, or a closed curve extruded; W A S D Q E move it, Z X turn it, `-` `=` pull a loop in / push it out, Enter / Shift+Enter / Ctrl+Enter select / add / subtract (built) |
| `Shift+Enter` | finish a curve as a closed loop (built) |
| `V` / `Shift+V` | line segment (click two points) / curve (click points, Enter: edit its gradients) (built early, §13) |
| `Ctrl+G` | make the selection a point set (an annotation item; built early, §13), in the directory selected in Annotations |
| `Ctrl+Shift+G` | the same, at the top level |
| `Ctrl+D` | duplicate the directories and items selected in Annotations |
| `F2` | rename the directory or item selected in Annotations |
| `B` / `Shift+B` | labels view on and off / label map (PNG and JSON) |
| `Enter` / `Backspace` | finish the curve, or leave curve edit mode / take back the last clicked point (built) |
| curve edit: click / Shift+click / Ctrl+click, `Del` | activate a node / add a cloud point / remove a node, remove the active node (built) |
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
| `Ctrl+Z` / `Ctrl+Y` (or `Ctrl+Shift+Z`) | undo / redo (built for the selection, planes and annotations; labels with M3's journal) |
| `Esc` | cancel the selection / tool (M1: clear the pick marker) |
| `F7` / `F8` | front / side orthographic view (M1) |
| `Home` | reset the view: perspective, default direction, framing the 1st–99th percentile box (M1) |
| `L` / `R` / `F` / `Esc` | lasso / rectangle / fit plane to the selection / drop tool, then selection (M2, built) |
| `H` / `Shift+H` | height axis z ↔ last fitted plane / height slice on and off (M2, built; `hs` comes later) |
| `K` / `Shift+K` | clip box on/off / clip box from the view (M1) |
| `P` | keep a piece of the selection: click it (Shift adds a piece, Ctrl takes one away) |
| `Shift+U` | tree: click the ground at a trunk; a cylinder 1.00–1.10 m above it (3D select); Enter, then "save the selection as a tree" |
| `Ctrl+K` / `Ctrl+Shift+K` | clip to the point sets selected in the Annotations panel / that clip off and on |
| `Ctrl+E` | eye-dome lighting on/off (M1) |
| `Ctrl+O` / `Ctrl+I` | open project / import scan (M1) |
| right drag / right mouse + `W A S D Q E` | rotate, as left drag / fly: once a key is pressed the mouse looks until the button goes up (while the right button is held these keys move instead of their usual action) |

Built in M1: `T`, `C` / `Shift+C`, `W`, `[` `]`, `Shift+[` `Shift+]` and the rows marked M1.

## 16. ideas backlog

New ideas from labelling sessions go here, with the observation that prompted each and a suggested milestone.

- (empty)
