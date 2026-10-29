# architecture

How PCLabel is built. Planning decisions are marked **(planning)**; the M0 spike and M1 confirm or replace them, and
the decision log at the end records what was chosen. Formats carry a `schema` string; bump it on any incompatible
change.

## overview

```
source file (.e57/.las/.laz/.ply, read-only)
   │  import (chunked, worker process/thread)
   ▼
project directory (outside the repo)
   points/   columns in store order (spatially tiled, randomised within tiles)
   tiles     tile table: bounds, start, count
   labels/   labels.u8, flags.u8, instance.u32 (memmapped, same order)
   journal/  ops.jsonl + per-op data
   frames, surfaces, regions, observations, derived feature caches
   │
   ├── render/  LOD streaming of tile prefixes → GPU buffers → shaders (colour modes, wrap, EDL)
   ├── tools/   selection kernels (numba) on full-resolution columns → label ops → journal
   └── analysis/ frames, surfaces, features, stats, queries, lint
   ▼
exports: labels in source order (.npy + sidecar), LAS/PLY with labels, observations, reports
```

## import

As built in M1 (`pclabel/store/importer.py`; `python -m pclabel import <file>` or File > Import in the app):

1. **Read in chunks** of 4M records (`ImportParams.chunk_size`), so peak RAM is bounded whatever the file size.
   - E57: libe57's CompressedVectorNode reader with fixed-size buffers (`pclabel/io/e57.py`). The file is opened
     through `libe57.ImageFile(path, "r")` only, never `pye57.E57`, whose constructor has an error path that
     deletes its file. Scans with no records are skipped (libe57 cannot open a reader on them).
   - LAS/LAZ: `laspy` chunk iterator. PLY: memmap for binary, streaming for ASCII. (Not yet; E57 only so far.)
2. **Validity.** Drop records whose `cartesianInvalidState` / `sphericalInvalidState` is non-zero or whose
   coordinates are not finite. Convert spherical coordinates if no cartesian fields exist. Apply each scan's pose.
   Concatenate scans in file order.
   - Colours are scaled to 8 bits from `colorLimits`, intensity to 16 bits from `intensityLimits` (or the
     prototype's range).
   - A scan without colour gets grey (128), and one without intensity gets 0, when another scan in the file has
     them.
3. **Offset** (float64): the centre of the pose-applied header bounds, rounded to whole metres, else from the first
   chunk. Positions are float32 relative to it (precision about 8 µm at 100 m). The header bounds serve only this
   purpose: they are wrong in all five source files (CONTEXT.md), so the project's bounds come from the points.
4. **Pass A:** stream the valid points into source-order temporary columns (memmaps in the temporary project
   directory): xyz f32, rgb u8, intensity u16 and the raw record index.
5. **Tiling** (`pclabel/store/tiles.py`, D-002): an xy count histogram (0.25 m cells) over the pass-A xyz, then
   a 2D quadtree split until a leaf has at most `T` = 2M points or is a single cell.
   - Tiles get tight bounds of their own points (better culling than the quadtree cell).
   - xy tiles suit the BEV-first workflow and the street scale (about 160 × 112 m).
   - Tiles are frustum-culled with full 3D bounding boxes.
6. **Pass B:** a stable counting sort gives `order` (store position → source index), then each tile's range is
   shuffled (Fisher-Yates with a per-tile counter-based RNG, deterministic for a seed). Any prefix of a tile is
   then a uniform random subsample, which is what the LOD needs. Each column is gathered into store order in
   blocks of 16M points (numba, parallel).
7. **Write** (layout below): the store columns, `source_index`, `raw_index` (only if records were dropped),
   `tiles.npy`, zero-filled `labels.u8` and `flags.u8`, then `project.json`.
8. **Hash the source file** (sha256 in a background thread, plain read-only binary reads) and record its size and
   mtime. If either changed during the import, the import fails.
9. **Progress and cancel** throughout (stages: read, tile, reorder, finish).
   - The CLI prints progress; the app runs the import in a worker thread with a progress dialog.
   - Everything is written into `<projects>/.importing-<name>-<id>/` and renamed to `<projects>/<name>/` only when
     complete. Cancel or failure deletes it.
   - An existing project is never overwritten.

Measured in M1 (ROADMAP): streets 1 imports in 110 s with a peak working set of 22 GB (6.4 GB private), newroad 2
in 1.6 s.

Sizes for `streets 1.e57` (633.6M points):

| column | size |
|---|---|
| xyz f32 | 7.6 GB |
| rgb u8 | 1.9 GB |
| intensity u16 | 1.3 GB |
| source_index u32 | 2.5 GB |
| labels u8 + flags u8 | 1.3 GB |
| instance u32 (optional, lazy) | 2.5 GB |
| **total** | **about 15–17 GB** (about 570 GB free) |

## source order

The **source order** is the order in which every export expresses per-point arrays:
- scans in file order, records in stored order within each scan;
- records with an invalid state or non-finite coordinates removed;
- `N_valid` = the number of remaining points (this matches what pye57's `read_scan` returns per scan, concatenated);
- the raw record index is kept in the project for anyone who needs the unfiltered order.

Export: `out = np.zeros(N_valid, uint8); out[source_index] = labels` (store order → source order).

Round-trip test: synthetic E57 written with pye57, including invalid records → import → label by index → export →
compare with the expected source indices.

## project directory

```
<projects>/<name>/
  project.json            schema pclabel.project/1 (below)
  points/xyz.f32          float32 (n, 3), store order, relative to the offset
  points/rgb.u8           uint8 (n, 3), store order (absent if no scan has colour)
  points/intensity.u16    uint16 (n,), store order, 0..65535 over the scan's intensity range (absent if none)
  points/source_index.u32 uint32 (n,) (.u64 if n >= 2^32): store position -> source index (the export order)
  points/raw_index.u32    only if records were dropped: source index -> raw record index (all scans, file order);
                          when absent, the raw index equals the source index
  tiles.npy               structured array (below)
  labels/labels.u8  flags.u8   uint8 (n,), store order, zero-filled at import (0 = unassessed)
  labels/instance.u32     created on first use
  labels/snapshots/       periodic copies of labels+flags with the op id they correspond to (keep last N)
  journal/ops.jsonl       one line per operation (below)
  journal/data/<op>.npz   indices and previous values per operation
  frames.json             height frames, schema pclabel.frames/1 (below)
  annotations.json        segments, curves and point sets in directories, schema pclabel.annotations/5 (below)
  annotations/<id>.npy    a point set's store indices (int64, sorted)
  surfaces/<name>.npz     reference / relief surfaces (grid origin, cell, heights, mask, parameters)
  regions.json            assessed regions (polygon, z range, per-class status) and patrol records
  observations/observations.jsonl   observations/img/*.png
  derived/                cached features (normals, roughness, ...) keyed by parameters; deletable
  sessions.jsonl          session logs
```

Column files are raw little-endian arrays without headers; their dtype and shape are in `project.json`.

**`project.json`, schema `pclabel.project/1`** (written atomically: temp file, then replace):

| key | contents |
|---|---|
| `schema`, `name`, `created`, `app` | format id, project name, UTC time, `{name, version}` |
| `source` | `path`, `format`, `size`, `mtime_ns`, `mtime` (ISO), `sha256` |
| `n_raw`, `n_valid` | records in the file; points kept (the length of every column) |
| `offset` | float64 [x, y, z]; world = stored + offset |
| `bounds` | `{min, max}` of the points, relative to the offset |
| `attributes` | the optional columns present: `rgb`, `intensity` |
| `scans` | per scan: `index`, `name`, `coords`, `n_raw`, `n_valid`, `raw_start`, `rotation` (3 × 3), `translation`, `origin` (scanner position relative to the offset), `fields`, `intensity_range`, `color_max` |
| `columns` | per column: `file`, `dtype` (numpy dtype string), `shape`, `order` (`store` or `source`); index columns also `maps` |
| `tiles` | `file`, `count`, `max_points`, `cell`, `seed` |
| `import` | `params`, `seconds`, `stage_seconds`, `peak_working_set_gb`, `peak_commit_gb` |
| `scan_metadata` | user-filled: `scanner`, `capture` (`static` or `mobile`), `datetime`, `weather`, `surface_wetness`, `settings`, `notes` |

**`tiles.npy`**: `id` u4, `depth` u1, `x0 y0 x1 y1` f8 and `zmin zmax` f4 (tight bounds of the tile's points,
relative to the offset), `size` f8 (quadtree node edge, m), `start count` u8 (store range).

**`frames.json`, schema `pclabel.frames/1`** (written atomically): `{schema, active, frames: [...]}`.
- `active` is the name of the plane frame in use, or null for z.
- Each frame is `{name, normal, origin, along, report}`, in world coordinates. `h = (p − origin) · normal`;
  `normal` is a unit vector pointing up; `along` is the horizontal unit vector along which the grade is reported.
- `report` holds the fit's method and parameters, point counts (selected, fitted, kept), `rms_m`, `tilt_deg`,
  `along_azimuth_deg`, `grade_percent`, `crossfall_percent`, `suggested_min_period_m`, `created`, and `warning` if
  the fit does not look like ground.

Frames never change stored coordinates (D-006). Algorithms can read this file to use the same "height above road".

**`annotations.json` and exported annotations, schema `pclabel.annotations/5`** (2 added `uncertain` on nodes,
2026-10-22; 3 keeps it on items only, 2026-10-26; 4 added `label_colors`, 2026-10-28; 5 added `labels_exported`,
2026-10-29. Schema 1 files still read,
with nothing uncertain; in schema 2 files an uncertain directory passes its mark to the items that take its label;
schema 3 files read with no label colours. Written atomically; it replaced `pclabel.groups/1`, which is no longer
read):
- **Header:** `{schema, coordinates, source: {file, sha256, n_valid}, offset, counter, locks, unlocked, locks_on,
  label_colors, tree: [...]}`, and `labels_exported` in an export by label (below). `label_colors` maps a label to `#rrggbb` for the labels view and the label map (a
  label not listed takes its taxonomy class colour); importing a file adds the colours of labels new to the
  project. `locks` lists the labels whose points cannot be selected. Classes with `selectable: false` in the
  taxonomy are locked as well, except those listed in `unlocked`. `locks_on: false` switches all of them off
  (missing: true).
- **Coordinates:** every coordinate is in the scan frame (world = stored + offset), metres, float64.
- **Tree:** a list of nodes in display order, each `{id, type, name, color, label, ...}`; items (not directories)
  also have `uncertain` (true/false: the item's label is uncertain). `label` is a taxonomy
  class name or any text; empty means "the enclosing directory's".
  - `directory`: `children` (nodes). `color` may be null: the parent's colour.
  - Items draw in the colour of the nearest enclosing directory that has one, else in their own `color`.
- **Items** by `type`:
  - `segment`: `a`, `b`, `length_m`, `picked_source_indices` (the two clicked points; −1 if unknown).
  - `curve`:
    - `plane`: `{mode, normal, point, tilt_deg, height_axis: {name, normal, origin}, height (mode "axis" only)}`.
      The plane is `dot(p − point, normal) = 0`. Its mode is `ends` (through the first and last clicked points,
      turned to fit the others), `axis` (perpendicular to the height axis at the points' mean height) or `fit`
      (the least-squares plane through all the clicked points). A curve
      document without `mode` (drawn before modes existed) is read as `axis`.
    - `nodes`; `handles_in` and `handles_out` (positions, not offsets); `manual` (per node: handles set by the
      labeller); `closed` (a loop: one more piece from the last node back to the first); `picked` (the clicked
      points before projection); `picked_source_indices`; `length_m`.
    - `polyline`: points about 2 cm apart along the curve. Piece k is the cubic Bézier `nodes[k]`,
      `handles_out[k]`, `handles_in[k+1]`, `nodes[k+1]`.
  - `points`: `count`, `provenance` (the selection operations), and either `file` (the project's copy: store
    indices in `annotations/<id>.npy`) or `source_indices` (an export: sorted source indices, the export order).
- **Labels export, schema `pclabel.annotation-labels/5`** (2 added `kind` and `flag_bit`, 2026-10-21; 3 added the
  uncertain counts and marks, 2026-10-22; 4 dropped `uncertain_from`, 2026-10-26; 5 added the subsets, 2026-10-28):
  `{schema, coordinates, source, offset, labels: [...], unlabelled, overlaps}`.
  - A class that is part of another (taxonomy `subset_of`, e.g. `road_line` of `road`) also has `subset_of` and
    `outside_parent` (its distinct points in no point set of the parent). A class with subsets in use also has
    `subsets` (their names) and `points_with_subsets` (its distinct points with theirs).
  - Each label is `{label, kind ("class", "flag" or "text"), class_id (null if not a taxonomy class), flag_bit
    (null if not a taxonomy flag), counts {segment, curve, points}, points (distinct), uncertain_items,
    uncertain_points (distinct points in uncertain point sets), items}`. A flag label (e.g. `marking`, R-006) is
    meant to overlap a class's points.
  - Each item has its own `uncertain` (true/false).
  - Items are as in the tree, plus `path` (enclosing directory names) and `label_from` (the name of the node that
    carries the label).
  - Labels are ordered taxonomy classes first (by id), then flags (by bit), then other labels by name.
  - `overlaps` lists `{labels: [a, b], points, subset}` for labels whose point sets share points; `subset` is true
    when one is part of the other (expected).
- **Import:** source indices are mapped back to store indices with one parallel pass over `source_index`
  (0.55 s on streets 1). Point sets are only imported into a project of the same source (`sha256` and `n_valid`).

## rendering and LOD

As built in M1 (`pclabel/render/`). `renderer.py` uses moderngl only and runs in tests on an offscreen context;
`viewport.py` is the Qt widget around it.

- **Context:** moderngl attached to the `QOpenGLWidget`'s context (OpenGL 4.3 core requested) in `initializeGL`.
  Frames end in `defaultFramebufferObject()` via `ctx.detect_framebuffer(...)`.
- **OpenGL implementation** (`render/glsetup.py`; settings `gl`, or `--gl`):
  - `hardware`: the GPU driver;
  - `software`: Mesa llvmpipe on the CPU, never touching the GPU, for when the GPU is busy with training runs.
    - It loads a current Mesa build (`%LOCALAPPDATA%\PCLabel\mesa-<version>`) through Qt's
      `AA_UseSoftwareOpenGL` + `QT_OPENGL_DLL`, and pins `GALLIUM_DRIVER=llvmpipe`.
    - If the renderer string is not llvmpipe, the app refuses to start.
    - Qt's bundled `opengl32sw.dll` (Mesa 11.2, GL 3.0) is too old.
  - Tests always use `software`.
- **Pages:** each tile lives on the GPU as a run of pages that grow geometrically (16k, 16k, 32k, 64k, 128k, then
  256k points each), always a prefix of the tile. A distant tile that needs a few hundred points costs one
  350 KB page.
  - Each page has a geometry buffer (20 B/point: position f32 × 3 relative to the offset, rgba u8 × 4, intensity
    u16, pad) and a label buffer (4 B/point: label u8, flags u8, GPU-only extra bits u8, GPU-only annotation
    label code u8 for the labels view), so label edits re-upload only label bytes. Extra bit 0 marks the clip to
    point sets.
  - Memory budget: `gpu_memory_gb` (8 GB) in hardware mode, `software_memory_gb` (2 GB) in software mode.
  - Eviction drops the last page of a tile (prefixes stay intact): unwanted tiles first, then the least recently
    used. Wanted pages are never evicted. If the budget cannot hold everything wanted, the frame reports
    `memory_full` and stops asking for repaints.
- **LOD selection per frame** (`render/lod.py`):
  - For each visible tile, project its box and choose `k = min(n, area_px / spacing_px²)`.
  - Scale all `k` down to fit the global **point budget**. Default 30M from the M0 benchmark (50.7 fps worst
    case at 2 px; fill rate dominates, so larger points need a smaller budget); 300k in software mode (about
    15 fps on llvmpipe at 1920 × 1009).
  - Draw `k` points from the start of the tile's pages.
- **Streaming:**
  - A worker thread packs pages from the memmaps (numba).
  - The render thread uploads ready pages for at most 6 ms per frame and keeps up to 8 requests in flight.
  - Requests are coarse-first: page 0 of every visible tile, then page 1, and so on, larger on-screen tiles
    first.
  - The view repaints until every wanted page is resident.
- **Shaders** (`render/shaders.py`):
  - colour modes RGB, intensity (gamma), height (z ramp), flat, class (256-entry colour/visibility texture),
    wrap, wrap × intensity;
  - clip box in world coordinates (points outside are culled in the vertex shader);
  - fixed or adaptive point size: adaptive projects the tile's drawn spacing `sqrt(area_xy / k)` to pixels,
    clamped between the fixed size and a maximum;
  - optional round points.

  Heights for the height and wrap modes are world z for now; M2 adds the frame matrix and the surface texture.
- **EDL** (eye-dome lighting): a full-screen pass over the offscreen depth texture.
  - It samples 8 neighbours at radius r: shade = exp(−300 · strength · mean(max(0, Δ log2 depth))).
  - In orthographic views the depth difference is scaled as a perspective view at the camera distance would see
    it.
- **Label updates:** after an operation, re-upload only the label buffers of the affected pages (M3).
- **Picking:**
  1. Read back the depth near the cursor from the last frame (the nearest drawn pixel).
  2. Unproject it.
  3. Find the nearest full-resolution point by brute force over the few tiles around that position (numba;
     about 1 ms per 2M-point tile, no KD-tree build). The search radius is three pixel footprints.
  4. Count that point's neighbours within 5 and 10 cm.

## selection engine

- **Input:**
  - a screen polygon (or brush strokes), rasterised once to a mask at viewport resolution;
  - camera matrices;
  - optionally a depth-buffer copy;
  - the active filters.
- **Candidate tiles:** tile bounding boxes projected to screen, intersected with the mask's bounding box.
- **numba kernel per tile** (`parallel=True, nogil=True`) over *all* points of the tile, not only the drawn prefix:
  1. transform to clip space;
  2. look up the mask pixel;
  3. optional depth test (point depth ≤ buffer depth + tolerance);
  4. filters (class, locks, band on `h`/`hs`, clip box, query mask).

  Output: global store indices (sorted uint32/uint64).
- **Target:** at least 200M points/s tested on the workstation. Points that are not drawn but lie behind drawn
  surfaces are handled by the depth test, within its tolerance.
- **Queries** (SPEC §9) compile to the same per-tile kernels or vectorised numpy, limited to a region.
- **As built (M2, part; `tools/selection.py: select_visible`):**
  - The shape is rasterised at physical viewport resolution.
  - The depth buffer is read back from the last frame and min-filtered over 2 pixels (scipy), so gaps between
    drawn LOD points do not let hidden surfaces through.
  - Candidate tiles are the visible ones whose projected box meets the shape's bounding box. The kernel runs per
    tile.
  - Clip box and height slice are applied to the hits (numpy).
  - Output: sorted int64 store indices.
  - It runs in a worker thread. The app keeps the selection as a sorted index array.
  - The renderer marks selected points with bit 7 of the flags byte in the GPU label buffers only (the taxonomy
    uses bits 0–4). The point shader tints them. Bits 6 and 5, also GPU-only, mark a preview group (orange) and
    points to look at (magenta). The split tool uses them; the tint priority is bit 5, then 6, then 7. The flags
    byte is then full, so the clip to point sets uses bit 0 of a third, GPU-only byte (`INSIDE_BIT`); the shader
    drops unmarked points while `u_set_on` is set.
  - A selection change rewrites the label buffers of the resident pages of affected tiles. Pages packed under an
    older selection are corrected before upload. Labelling goes through the journal (M3).
- **3D select** (`tools/volume.py`): tiles whose bounds meet the volume's bounding box are tested point by point
  (numba, parallel). The test: height along the axis within [bottom, top]; across it, inside the turned rectangle
  or the circle. The clip box and the slice are applied afterwards, as for shapes.
- **Volumes** (`tools/volume.py: VolumeSpec`): one description of a 3D select volume. It has an origin, an axis n
  with two directions across it, bottom and top, and box half-sizes, a radius, or a loop mask on a grid.
  - `contains()` tests points (numba).
  - `SelectFilters.volume` uses it for "what is hidden is not selected"; seed-tool results are filtered by it too.
  - The point shader clips to it with the same arithmetic: `u_vol_*` uniforms, and the loop mask as an R8 texture
    on unit 3. The texture is uploaded again only when the mask changes, not when the loop moves.
- **Extrude select** (`tools/volume.py: select_loop`): the loop (the curve's polyline, scaled, turned, moved) is
  rasterised in its plane at 1 cm (coarser above 16M cells). It is shrunk or grown by the inset with an exact
  distance transform (scipy). Points within the height range along the normal are tested by a cell lookup
  (numba, parallel), only in tiles meeting the extruded box.
- **Combining selections:** `tools/sets.py` merges sorted index arrays in one pass (union, intersection,
  difference, xor). Point set operations (`Annotations.set_operation`) use the same merges. The renderer finds the
  tiles a selection change touches with one search per tile, not per point.
- **Locks:** the window keeps the sorted store indices of locked points, recomputed when the annotations or the
  locks change. Every selection result except a subtraction is filtered against them with a parallel
  binary-search membership test (`store.annotations.isin_sorted`).
- **Clip to point sets** (`tools/sets.py: PointMask`): the chosen point sets' store indices, plus a dense boolean
  mask over the store (1 B/point: 634 MB for streets 1, built in about 10 ms by a parallel scatter).
  - Every kernel that reads points takes it (`inside=`): the ground grid, the magic picture, the geometric voxels,
    picking and the clean-up's columns. They skip tiles that hold none of the set's points (two binary searches
    per tile) and test `mask[i]` for the rest. `SelectFilters.inside` does the same for shapes and 3D select.
  - Cached grids, pictures and voxels carry the mask's `token` in their keys, so a new clip never reuses a cache
    built from other points.
- **Pieces** (`tools/pieces.py`): the selected points' voxel keys (numba, parallel) go into the open-addressing
  table of `tools/normals.py` (doubled when half full); a union-find over the occupied voxels joins each with its
  13 forward neighbours, and pieces are numbered by size. A click looks for the nearest occupied voxel within
  30 cm. Memory: 8 B per selected point for its key and 8 B for its slot, plus the table.
- **Density** (`tools/density.py`): the set's points projected across the height axis (numba, parallel) into
  int32 column numbers, counted with `bincount`; the density is two disk convolutions (points, occupied columns).
  Thresholds and the smallest piece are cheap to change (labels on the column grid, one gather per point).
- **Seed tools** (`tools/ground.py`, `tools/magic.py`): grids over xy around the click.
  - Each tile in reach is read by a worker thread into its own block of the grid. Blocks are merged in tile order,
    so the result equals one pass over the tiles in that order.
  - Ground grow keeps the first 48 points per 10 cm cell. Magic select sums intensity and height per 2 cm pixel
    over the ground layer.
  - Magic select keeps its picture (1.5 × the reach) for later clicks. Grids lie on fixed lattices, so results do
    not depend on which picture is reused.

## journal, undo, crash safety

**Built so far (2026-10-19): session undo** (`ui/undo.py`).
- Before the selection, the height frames or the annotation tree change, the window pushes a snapshot of that
  state. Undo restores it and keeps the current state for redo.
- Index arrays are never changed in place, so snapshots share them. Annotation snapshots copy segment and curve
  geometry but share point-set indices.
- The history is in memory only. Restored annotations and frames are saved to the project at once.
- The label journal below is still to come (M3).

- **One operation** is one `ops.jsonl` line, schema `pclabel.op/1`:
  ```
  {op_id, time, session, tool, params{...}, rules[...], selection{type, polygon|query|seed, camera, filters},
   target{class|flags|instance}, n_points, standard_sha256, taxonomy_sha256, data: "data/<op>.npz",
   parent_op (for undo/redo), committed: true}
  ```
- **`<op>.npz`:** sorted store indices (delta-encoded, compressed), previous labels, previous flags (and
  instances), and new values if they are not constant.
- **Write order:**
  1. write the npz;
  2. write the journal line with `committed: false`;
  3. apply to the memmaps and flush;
  4. append a commit marker line.

  On open, an uncommitted last operation is re-applied from its npz (idempotent) or rolled back.
- **Undo** writes a new operation that restores the previous values; history is never deleted. Redo re-applies.
- **Snapshots** of labels and flags every N operations or M minutes (633 MB each for streets 1) make recovery and
  "label state at op k" cheap.
- **Hashes:** `LABELLING_STANDARD.md` and `taxonomy.yaml` are hashed at session start and on change; every
  operation carries both hashes.

## exports

| export | contents |
|---|---|
| `<scan>.labels.npy` | uint8, length `N_valid`, source order |
| `<scan>.flags.npy` | uint8 flags, same order |
| `<scan>.instances.npy` | uint32, if used |
| `<scan>.labels.json` | sidecar: schema `pclabel.labels/1`, source path/size/sha256/N_valid, taxonomy snapshot, standard version + hash, assessed regions, op count, export time, app version |
| LAS 1.4 (PDRF 7) | xyz, rgb, intensity; `classification` = class id; `user_data` = flags; extra bytes for instance. Optional crop and/or voxel downsample (majority label) for sharing pieces |
| PLY | binary, with `label` and `flags` properties |
| collapsed export | classes mapped to their `group`, or a named mapping table in taxonomy.yaml |
| observations | JSONL as stored, plus markdown in the OBSERVATIONS.md template |
| threshold / statistics reports | JSON, CSV, markdown (SPEC §10) |
| frames / surfaces | JSON / npz, so algorithms can use the same "height above road" |
| annotations | JSON, schema `pclabel.annotations/5` (above): segments, curves and point sets (source indices) in directories; all, the selected entries, or by label: only the items whose label (own or inherited; "" for none) is in `labels_exported`, each in its directories (which keep their names and labels but only those items), so the file imports with the same labels |
| label map | PNG (the map, then a 240 px legend strip on the right) and a JSON sidecar, schema `pclabel.label-map/1`: `source`, `offset`, `plane` (the frame), `transform {plane_normal, plane_origin (world), map_x, map_y, pixel, width, height (the map's columns and rows, without the strip), top_left (map metres), pixel_to_world}` (world = plane_origin + (top_left[0] + (col + 0.5) pixel) map_x + (top_left[1] − (row + 0.5) pixel) map_y), `image {png, map_columns, legend_strip}`, `params {pixel, dot, line_width, margin, h_min, h_max (null: none), max_side, context}`, `overlaps` (the drawing order), `legend [{label, color, code, class_id, flag_bit, pixels, points_drawn}]`, `vectors [{name, kind, label, closed, uncertain, map_xy}]` (map metres), `seconds`, `created` |
| a plane | JSON, schema `pclabel.plane/1`: `name`, `equation {a, b, c, d}` (`a x + b y + c z + d = 0`, unit normal up, scan coordinates: the left side is the height above the plane), `z_of_xy {p, q, r}`, `normal`, `origin`, `along`, `fit` (the frame's report), `source`, `offset` |

Every export writes its sidecar and never overwrites without asking.

## threading

- Qt main thread: UI and GL.
- `QThreadPool` (or `concurrent.futures`) workers for import, streaming reads, kernels, feature computation and
  exports.
- numba kernels release the GIL.
- Shared state changes (labels) happen only through the operation pipeline, one at a time.

## performance targets (measure; record results in ROADMAP.md)

| operation | target | on |
|---|---|---|
| import | ≤ 30 min, peak RAM ≤ 48 GB | streets 1 |
| import | ≤ 1 min | newroad 2 |
| open project to first frame | ≤ 15 s | streets 1 |
| navigation | ≥ 30 fps at the default budget; no frame > 100 ms while streaming | streets 1 |
| colour mode / period / frame change | next frame | any |
| lasso preview / apply | ≤ 0.5 s / ≤ 2 s with 50M candidate points | streets 1 |
| undo | ≤ 1 s | streets 1 |
| crash | lose at most the operation in progress | any |

## testing

- **`pclabel.synth.make_street(...)`**: a configurable synthetic street with ground-truth class per point.
  - road with crossfall and grade; curbs (with a lowered section); pavement; drain inlets below the road at
    intervals;
  - grass with roughness; tree trunk and canopy; lamp post; a lane marking (high intensity);
  - Gaussian range noise;
  - density falling with range from a scanner origin; **occlusion** (points on surfaces hidden from the origin
    removed, giving no-point strips behind kerbs).
  - Can write E57 (pye57) and LAS so the import path is tested end to end.
- **Unit tests:**
  - readers (including invalid records and multi-scan poses); store and tiles;
  - source-order round trip;
  - selection kernels (against a slow reference implementation);
  - frames (recover plane / crossfall), surfaces, the wrap formula (numpy mirror of the shader);
  - journal crash recovery (kill between steps); undo/redo;
  - query parser (rejects anything outside the whitelist);
  - assisted tools against synthetic ground truth.
- **GL tests** (shader mirror, renderer, UI) run on Mesa llvmpipe through an offscreen Qt context, never on the GPU
  (`PCLABEL_TEST_GL=hardware` opts in).
- **Benchmarks in `bench/`** print measured numbers; they are not part of pytest. `bench/check_open.py` is the M1
  manual check (open time, scripted navigation, frame log).

## decision log

| id | decision | status |
|---|---|---|
| D-001 | Python 3.12 + PySide6 + moderngl + numpy/numba | confirmed by M0 (2026-10-17): read, selection and render meet their targets (ROADMAP M0) |
| D-002 | 2D xy quadtree tiles with in-tile random order, LOD = tile prefixes | kept after M0 (100M points tiled in 2.9 s); revisit if M1 shows tall tiles or uneven density problems on real scans (alternative: 3D octree) |
| D-003 | Labels canonical in store order; `source_index` maps to source order for exports | planning |
| D-004 | Journal-first label operations with npz deltas and snapshots | planning |
| D-005 | Selection always on full-resolution columns with an optional depth test | planning |
| D-006 | Reference frames and surfaces are view/analysis transforms, never applied to stored data | planning |
| D-007 | Labelling is done by labelling annotations: a label on a directory or item (inherited by what it holds) covers its point sets, segments and curves; "export labels" groups them by label | decided by the user, 2026-10-20. It stands in for M3's per-point label column for now; changes are recorded in `annotations.json` (point sets keep how they were selected) and undoable in the session, but there is no persistent operation journal |
