# manual

What the app does, tool by tool, as built. Each tool's thresholds are the fields of its parameter dataclass, named
here; the dataclass's docstring and the panel's tooltips give the defaults. Measured speeds and results are in
[RESULTS.md](RESULTS.md). File formats are in [ARCHITECTURE.md](ARCHITECTURE.md). When to use which label is in
[LABELLING_STANDARD.md](LABELLING_STANDARD.md).

## running it

- `python -m pclabel ["streets 1"] [--gl software]` opens the app, optionally on a project in the projects folder.
  `--gl software` (or `gl: "software"` in `%APPDATA%\PCLabel\settings.json`) renders with Mesa llvmpipe on the CPU
  and never touches the GPU, for when a training run has it; keep the point budget small (300k).
- `python -m pclabel import <file.e57> [--name N]` imports a scan (also File > Import scan, `Ctrl+I`, with progress
  and cancel). The source is opened read-only and never written.
- `python -m pclabel info <project>` summarises a project.
- `python -m pclabel labels <project> <out>` writes the labels as one class and a flags byte per point, in the scan's
  source order (`<out>.classes.npy`, `<out>.flags.npy`, `<out>.labels.json`), and prints the counts and any
  conflicts (points in sets of two classes on different branches of the tree).
- A crash writes the Python stack of every thread to `%LOCALAPPDATA%\PCLabel\_logs\crash.log`.

## the view

**Mouse**, the same in every view:
- left or right drag rotates about the surface point at the centre of the view (orthographic: turns the heading);
- Shift + left drag, or middle drag, pans; the wheel zooms toward the cursor;
- right button held and W A S D Q E pressed: fly until the button goes up (the mouse looks, Q E down and up, Shift
  4× faster, the wheel sets the speed);
- a click without dragging picks the nearest full-resolution point and shows it in **Point info**: world xyz, its
  class and flags as `python -m pclabel labels` resolves them, the point sets holding it (and a conflict between
  them, if any), RGB, intensity, range from the scanner (static scans only), points within 5 and 10 cm, store,
  source and raw indices, tile.

**Views:** `T` looks straight down and `Shift+T` straight up (to see drains under the road); pressing either again
turns back. `F7` front (looking +y), `F8` side (looking −x), `Home` resets. These are angles of the perspective
camera; orthographic projection is a separate switch in the View menu.

**Colour modes** (`C` / `Shift+C`, or the Display panel): rgb, intensity, height, flat, wrap, wrap ×
intensity, labels. All are computed in the shader, so switching costs nothing.
- Intensity is stretched to the scan's 1st to 99th percentile on open: every scan uses less than half of the stored
  range. "Hide points outside the intensity range" draws only points inside it (display only; tools still reach the
  rest).
- **Height wrap** (`W`): a cyclic colour every period along the height axis, for cm-scale relief. `[` `]` divide or
  multiply the period by 1.5; `Shift+[` `Shift+]` slide the band edges.
- **Labels view** (`B`): every point set in its label's colour (its own, or its directory's), the rest dim grey or
  hidden; segments, curves, polylines and markers in their labels' colours. Where sets overlap, the smaller one
  shows (road lines over the road). `B` again returns to the previous mode.
- Display panel: point size (fixed or adaptive), round points, eye-dome lighting (`Ctrl+E`), background, point
  budget (30M drawn points on the GPU by default).

**Height axis** (the Height axis panel). Roads in one scan slope differently, so heights are measured along an axis
the labeller chooses: z, or the normal of a plane fitted to the selection.
- `F` fits a robust plane to the selection (`analysis/frames.py`, `PlaneFitParams`: least squares, then trimming at
  2.5 × MAD, three times). The report gives tilt, RMS, grade along the selection's main direction, crossfall, and
  the smallest useful wrap period (4 × RMS). It warns when the fit does not look like ground (tilt over 10° or RMS
  over 5 cm): a box that caught a canopy edge, for example.
- Planes are named `plane N`, saved in `frames.json`, renamed or deleted in the panel, and exported as
  `pclabel.plane/1`. `H` switches between z and the last plane. Stored coordinates never change.
- **Height slice** (`Shift+H`): only points between two heights on the axis are drawn (default −10 to +30 cm).
  From the top, the visible surface under a tree is its canopy; the slice removes it.

**Clips.** What a clip hides is neither drawn nor selected by any tool.
- Clip box (`K` on and off, `Shift+K` from the view): world min and max.
- Clip to a 3D select volume (`K` while 3D select is the tool): only what is inside stays; "clip live" follows the
  volume as it moves.
- Clip to point sets (`Ctrl+K` on the sets selected in Annotations; `Ctrl+Shift+K` off and on): only those points
  are drawn and picked, and grow, magic select and clean-up build their grids from them alone. The clip is a copy:
  later edits to the sets do not change it until it is made again. Clips are not saved.

## selecting

A selection always holds full-resolution points, not only the drawn ones. Every selection records how it was made
(tool, seed, parameters), and a point set saved from it keeps that record. Selections run in a worker thread.

- **Lasso** (`L`) and **rectangle** (`R`): plain replaces, Shift adds, Ctrl subtracts. `O` switches between
  *visible* (only points not hidden by others, tested against the depth buffer: white outline) and *through* (every
  point inside the shape at any depth, e.g. the ground under a canopy: orange outline).
- **Grow select** (`G`), click a surface; Shift+click adds, Ctrl+click subtracts; a changed setting re-grows from
  the same seed.
  - *Height and intensity* (`tools/ground.py`, `GroundGrowParams`): a grid of cells over the ground layer, grown
    8-connected while the height step, roughness, intensity step and intensity against a running road average stay
    within limits; gaps without points are crossed up to `max_gap` with a slope allowance, so a curb behind a
    no-point strip still stops it. Markings up to `marking_width` are bridged. "Show where it stopped" colours the
    refused cells by the test that refused them. Fails: with the intensity tests off it leaks over a lowered curb
    onto the pavement.
  - *Geometric* (`tools/normals.py`, `NormalGrowParams`): region growing on voxel normals after PCL; follows slowly
    bending surfaces of any orientation and stops at creases. The straight edges of a result are its reach.
- **Magic select by intensity** (`I`, `tools/magic.py`, `MagicSelectParams`): click a marking; the connected ground
  of similar intensity is selected, on a 2 cm intensity picture of the ground. The tolerance is a factor (half to
  twice the clicked intensity), because paint's spread grows with its brightness. It does not climb a height step.
  `contiguous` off takes every pixel within tolerance on the clicked surface: all the dashes of a line. Fails:
  with `contiguous` off, bright patches where the road joins pavement without a step come too; on very sparse
  paint a dash needs a click per piece. Clip to the road set first.
- **3D select** (`U`, `tools/volume.py`, `VolumeParams`): a box or cylinder standing on the clicked point along the
  height axis. While it is the tool: W A S D move it across the axis, Q E along it, Z X turn it 5° (Shift 5×), `K`
  clips to it, Enter selects (Shift+Enter adds, Ctrl+Enter subtracts). These keys keep working after typing in one
  of its number fields.
  - **Extrude select** (shape "loop"): a closed curve swept along its plane's normal. `inset` pulls the outline in
    evenly (`-` and `=` change it by 1 cm); negative pushes it out. Use inset, not scale, to stay clear of a curb:
    scaling a long loop moves its ends far more than its sides.
- **Tree or pole** (`Shift+U`, `tools/trees.py`, `TreeParams`): click the ground at the foot; a cylinder appears
  1.00 to 1.10 m above it, and 3D select takes over (a click on the trunk slides it sideways only). Enter selects;
  "save the selection" makes `tree NN` (or `lamp_post NN`, etc.) holding the band as a point set and a marker
  named `position` at the centre of a circle fitted to the band, at the ground height. The fit's radius, girth, rms
  and coverage go into the provenance. "What" offers `tree` and the kinds of `pole`. "Add markers to earlier trees"
  gives older tree directories their marker.
- **Pieces** (`P`, `tools/pieces.py`, `PieceParams`): the selection's connected pieces (voxels of `gap`); a click
  keeps the piece under it, Shift adds, Ctrl takes away; the panel keeps the largest or drops small ones. Only
  selected points count, so a gap the selection left splits an object.
- **Clean up** (Select menu, `tools/cleanup.py`, `CleanParams`): takes in unselected points just above or below the
  selected surface within its footprint (grass blades left by a low 3D select), and drops specks and small islands.
  Preview tints additions orange and removals magenta.
- **Density** (Select menu or Annotations, `tools/density.py`, `DensityParams`): the parts of a point set or the
  selection with at least `min_density` points/m² across the height axis (default 2,000, from O-0011). Sparse parts
  are tinted; save the dense part as a point set.
- **Locks** (the Annotations panel's locks menu; Select > Locks on): points in a point set with a locked label are
  never selected by any tool. Labels with taxonomy `selectable: false` (`other`) start locked.
- `Esc` drops the tool, then the selection. `Ctrl+Z` / `Ctrl+Y` (or `Ctrl+Shift+Z`) undo and redo the selection,
  planes and annotations, one step per action, for the session (at most 200 steps).

## annotations and labels

Labels live on annotation items and directories, never on points directly. The Annotations panel is a tree of
directories and items: point sets, segments, curves, polylines and markers.

- **Point set** from the selection: `Ctrl+G` into the directory selected in the tree, `Ctrl+Shift+G` at the top.
- **Segment** (`V`): two clicks. **Curve** (`Shift+V`): click points in order (Backspace takes one back), Enter
  finishes, Shift+Enter finishes a closed loop. Its plane mode, chosen in the panel:
  - *through the ends* (default): the plane contains the first and last points and is turned to fit the rest;
  - *perpendicular to the height axis*, at the points' mean height;
  - *fit all points*: the least-squares plane at any tilt;
  - *no plane*: the nodes stay where they were clicked, so the curve follows a slope (the tracers save these).
- **Curve edit mode** (after Enter, or "edit line", or double-click): drag a node to move it; click a node and drag
  its handle to set the gradient; Shift+click the cloud to add a point where the curve passes closest; Ctrl+click
  or Del removes a node; "automatic" resets a node's handles; "closed loop" opens or closes it. Polylines edit the
  same way without handles. "Show clicked points" draws where each point was clicked. A tool-drawn line edited by
  hand is marked `edited`.
- **Join curves**: two open curves, choosing which ends meet. **To curves / to polylines** converts either way.
  **Smooth** (`analysis/curves.py`, `smooth_line`): a Gaussian average along the line, no point moved more than
  the limit.
- **Markers**: `Ctrl+M` (or `Ctrl+Shift+M` at the top) where the selection is, by `MarkerParams`: across the axis
  the mean or the centre of a fitted circle (use the circle for poles and trunks: the mean sits toward the
  scanner), along it the mean height or the lowest point. `Shift+M` places a marker per click, snapping to a pole's
  circle or dropping to the ground. Markers can be moved up or down and given the active height axis.
  "Attributes..." edits a marker's fields (`asset_id: ...`).
- **Labels**: the label box and "set label" (or double-click the label column). The box shows the taxonomy as a
  tree and greys out labels an item cannot take (`curb` only on lines). Labels already in use that the standard
  does not use for new labels (taxonomy status `import` or `retired`, e.g. `curb_top`, `drain`) are listed
  in grey under "not for new labels", with the reason in the tooltip; setting one still works and says why not
  to. The DXF import's boxes also offer the import-only classes. An item without a label takes its nearest
  directory's. "Uncertain" marks an item, never a directory; uncertain items are in italics.
- Rename (`F2`), duplicate (`Ctrl+D`), new directory, drag into directories, colour (a directory colours
  everything in it), delete (asks first), "select points" (the point sets as the selection).
- **Set operations** on point sets or directories: ∪, ∩, A \ B, B \ A, △, as a new point set or as the selection.
- **Split** (`tools/split.py`, `SplitParams`): a point set in two by curvature or roughness on 3 cm voxels, with
  Otsu's threshold on request, a majority and island clean-up, and doubtful voxels tinted magenta; "split into two
  point sets" replaces the set by `<name> smooth` and `<name> rough`.
- Annotations are saved after every change (`annotations.json`, point sets in `annotations/`). Export all or
  selected as one JSON file; importing adds copies, leaving out point sets from another scan.

## road tools

Lines and markers these tools draw are marked `assisted` with their parameters. Settings are in the Trace panel.

- **Curb tracer** (`Ctrl+T`, `tools/curbtrace.py`, `CurbTraceParams`): click the road beside a curb, then a point
  further along. Cross-sections every `step` find the toe and the lip, both measured; the side is found
  automatically and a start on the curb top is moved onto the road. Space stops, Backspace takes back a step, two
  more clicks trace on, Enter saves a directory `curb trace NN` of `lip k` lines labelled `curb` and, if "toe" is
  ticked (it is off by default), unlabelled `toe k` helper lines (the taxonomy's `tools` section sets these labels). "Follow the top edge only" needs no road beside the curb and saves only the lip. Every run is logged in
  the project's `trace_log.jsonl`. Gaps at drains are left, never drawn over. Fails: stops at lowered curbs, rolled
  faces wider than `max_face`, turns sharper than `max_turn`, and cars at the curb.
- **Marking tracer** (`Ctrl+Shift+T`, `tools/linetrace.py`, `LineTraceParams`): click the paint, then along it;
  solid or dashed, threshold halfway between the paint's and the road's intensity at the start.
- **Join traced pieces** (`tools/linejoin.py`, `JoinParams`): the selected lines of one label into continuous
  lines; repeats dropped, gaps bridged and recorded.
- **Smooth onto the curb top** (`tools/curbfit.py`, `CurbFitParams`): re-measures the curb top along a line and
  smooths the line while keeping it within a limit of the measurements; lowered stretches are carried across at
  the curb top's height.
- **Clearance along a line** (`tools/clearance.py`, `ClearanceParams`): the points (of vegetation and tree labels,
  or all) in the space above a line drawn on the road; each place becomes a marker at its lowest point. The scan
  shows only what it captured.
- **Cover outlines** (`tools/templates.py`, `OutlineParams`): a circle or rectangle at the selected markers;
  "reshape" redraws them.

## labels panel and exports

- **Legend**: the labels in use as the taxonomy tree, with colour, items and points. Untick a label to leave it out
  of the view, the map and exports. Colours are saved with the annotations.
- **Export labels**: everything grouped by label (`pclabel.annotation-labels`), with counts, overlaps, uncertain
  points and roll-ups along the tree.
- **Export annotations** by the ticked labels, in their directories.
- **Label map** (`Shift+B`, `tools/labelmap.py`, `LabelMapParams`): the labels projected onto z or a fitted plane,
  as PNG and JSON (`pclabel.label-map/1`).
- **Export vectors**: segments, curves, polylines and markers with the ticked labels, as DXF R12 (a layer per label,
  3D, scan coordinates; FARO Zone, AutoCAD and Leica read it) or GeoJSON (with label path, directories, uncertain,
  assisted, source and attributes; local metres).
- **Import labels (DXF)** (`Ctrl+Shift+I`, `io/dxf.py`, `store/label_import.py`): a drawing's layers as directories
  of exact 3D polylines and markers, labelled by a profile in `config/label_imports/` (ROCK planimetrics so far). The
  dialog shows how much of the drawing lies inside the scan. Binary DXF is refused.

## keys

| key | action |
|---|---|
| `Ctrl+O` / `Ctrl+I` / `Ctrl+Shift+I` / `Ctrl+Q` | open project / import scan / import labels (DXF) / quit |
| `Ctrl+Z` / `Ctrl+Y`, `Ctrl+Shift+Z` | undo / redo |
| `C` / `Shift+C` | next / previous colour mode |
| `W`, `[` `]`, `Shift+[` `Shift+]` | height wrap on/off, period ÷ / × 1.5, offset down / up |
| `T` / `Shift+T` / `F7` / `F8` / `Home` | top / bottom / front / side view / reset |
| `H` / `Shift+H` | height axis z ↔ last plane / height slice |
| `K` / `Shift+K` | clip box on/off / clip box from the view |
| `Ctrl+K` / `Ctrl+Shift+K` | clip to the selected point sets / that clip off and on |
| `Ctrl+E` | eye-dome lighting |
| `B` / `Shift+B` | labels view / label map |
| `L` / `R` / `O` | lasso / rectangle / visible or through |
| `G` / `I` / `U` / `Shift+U` / `P` | grow select / magic select / 3D select / tree or pole / pieces |
| `Ctrl+T` / `Ctrl+Shift+T` | trace a curb / a road marking |
| `F` | fit a plane to the selection |
| `V` / `Shift+V` | segment / curve |
| `Ctrl+G` / `Ctrl+Shift+G` | point set from the selection / at the top level |
| `Ctrl+M` / `Ctrl+Shift+M` / `Shift+M` | marker where the selection is / at the top level / markers by clicking |
| `F2` / `Ctrl+D` | rename / duplicate in Annotations |
| `Esc` | drop the tool, then the selection |
| in 3D select | W A S D Q E move, Z X turn, `-` `=` inset, `K` clip, Enter / Shift+Enter / Ctrl+Enter select / add / subtract |
| drawing a curve | Enter finish, Shift+Enter finish as a loop, Backspace take back a point |
| curve edit | drag a node or handle, Shift+click add, Ctrl+click or Del remove, Enter or Esc done |
| tracing | Space stop, Backspace take back a step, Enter save |

There are no keys for labels: they are set with the label box in Annotations.
