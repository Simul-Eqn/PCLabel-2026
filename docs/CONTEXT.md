# context

Background for anyone designing PCLabel features or writing labelling rules.

## purpose and downstream use

**What the documents in this workspace show:**

- The agency already has a desktop tool, *Scene2Sketch Outdoors* (`../Roadrun/docs/USER_GUIDE.pdf`, 2026-08-26).
  - For an **incident location** given as a Google Maps link or lat/long, it pulls road features from LTA's road
    database: kerb lines, lane markings, arrow markings, lamp posts, traffic lights, SOS phones and road names.
  - It exports them as a layered DXF, which is opened in **FARO Zone 3D** (or AutoCAD or Leica software) as the
    base layer of a **sketch plan**.
- LTA data is updated quarterly and can be out of date at the time of an incident. A TLS scan taken at the scene is
  current.
- The scan work aims to produce the same layers from the scan, plus more: pavements, trees, sheltered walkways and
  building outlines.
- So the end product is a **vector road map in the scan's local frame**, made by algorithms with thresholds.
  PCLabel's labels are the reference those thresholds are tuned and scored against.

**Reasonable inference (the user can neither confirm nor deny):**

FARO Zone 3D is diagramming software for crash and crime scenes. These sketch plans are probably for traffic
incident investigation, and may end up as evidence. That makes the following matter more than in a typical
labelling tool:

- **Provenance and reproducibility.** Hash the source, never modify it, journal every edit and version the
  standard. Any tuned threshold can then be traced back to the labels and rules it was tuned on.
- **Measurement.** Kerb heights, road grade and crossfall, drain depths and positions, and distances, each recorded
  with its method and uncertainty.
- **Features that matter in a crash reconstruction:**
  - curb lines, and whether a curb is mountable or lowered;
  - drains and gratings (wheel traps for motorcycles and bicycles) and road surface depressions;
  - lamp posts (lighting) and traffic lights (signal positions);
  - markings: stop lines, give-way lines, zig-zags, yellow boxes;
  - things that block sight lines at junctions: hedges, trees, bus shelters, parked vehicles.
- **Temporary objects at a scene:** the vehicles involved, police cones, barriers, people. They hide parts of the
  road and must not be mistaken for permanent features.

The same pipeline could also serve outdoor crime-scene sketches, where the road layer is context, and feedback to
LTA about outdated data.

**Design consequence:** optimise for **precise, defensible definitions** (what exactly is "the curb line"?) and for
**measured observations**, not for label volume.

## what the labels are for (and not for)

- **Primary:** per-point ground truth for **tuning and scoring the thresholds** of geometric, colour and intensity
  rules (step height for curbs, height bands for the road surface, intensity contrast for markings, verticality for
  poles, …), and for measuring where those rules fail.
- **Secondary:** a written, example-backed **labelling standard** that other annotators and algorithm writers can
  follow.
- **Not the goal:** large volumes of ML training data. Careful labelling of representative, clearly bounded areas
  beats rough labelling of everything. Areas are marked as assessed, so unlabelled points are never counted as
  negatives.

## the scans

All scans are in `../data/` (read-only):
- local frame, metres, z up, no georeference;
- one setup per file, with an identity or near-identity pose;
- no embedded 2D images.

| file | points | size | notes |
|---|---|---|---|
| `streets 1.e57` | 633,625,174 | 12.1 GB | Street T-junction with trees and pavement, about 160 × 112 × 48 m. Spacing is about 1 mm near the scanner. Main target and stress test. |
| `newroad 2.e57` | 8,388,829 | 160 MB | Small; good for development. Not aligned with streets 1, and whether it is the same scene is unknown. |
| `ExchangeGreen/RTC360/ExchangeGreen 1.e57` | 257,251,914 | 4.9 GB | Leica RTC360 (tripod TLS). |
| `ExchangeGreen/BLKARC/Fast/ExchangeGreenFast 1.e57` | 29,701,307 | ~0.6 GB folder | Leica BLK ARC, "Fast" mode. About 100 `Setup 002-*.jpg` photos sit alongside (camera poses unknown). Ground at z ≈ −0.1…−0.3; area about 67 × 129 m. |
| `ExchangeGreen/BLKARC/Slow/ExchangeGreenSlow 1.e57` | 59,827,968 | ~1.2 GB folder | Same place, "Slow" mode. |

- All five have xyz, RGB and intensity.
- The ExchangeGreen set is **one place captured three ways**. Use it to check that the standard and the thresholds
  are not tied to one scanner: density, noise and intensity scale all differ.
- Whether the three ExchangeGreen captures are co-registered is unknown; check before relying on it.
- **Header bounds are wrong in all five files** (checked at import, 2026-10-17). In every file some points lie
  outside the header's `cartesianBounds`:
  - all five reach 31–59 m past the header's maximum y;
  - four reach 2–247 m below its minimum x (247 m in the RTC360 scan);
  - ExchangeGreenSlow reaches 34 m past its maximum x;
  - z is off by up to 5.7 m.

  Never trust E57 header bounds; PCLabel computes bounds from the points.
- The BLK ARC is a mobile (SLAM) scanner. Colour modes that assume one scanner position (range, incidence angle)
  may not apply to it; confirm how it was captured.

**Scale facts that drive the design:**

- **Label at full resolution.** Raw spacing near the ground is about 1 mm close to the scanner and coarsens with
  range. A curb face is about 0.1–0.3 m tall. Earlier work here used a 5 cm downsample, which left curb faces with
  few or no points. Label at full resolution, or at a working resolution of at most 1–2 cm with labels propagated
  to every raw point.
- **Missing points carry information.** A single TLS setup sees surfaces from one side only. Curb faces facing away
  from the scanner, and ground behind objects, have **no points**. In the 5 cm downsample of streets 1 there is a
  "no-point strip" 0.4–0.8 m wide behind some kerbs. Absence of points is information, not a class.
- **Range matters.** Range noise, density and intensity all vary with range and incidence angle. A threshold that
  works near the scanner can fail at 40 m.

## singapore street field guide

What to expect in these scenes, with the labelling question each feature raises. Each answered question becomes a
rule in [LABELLING_STANDARD.md](LABELLING_STANDARD.md). Singapore uses the spelling "kerb"; this project uses
"curb" for the class name.

### ground and edges

- **Raised kerbs** along carriageways, islands and medians. Faces are typically about 0.1–0.2 m; earlier hand
  measurements on streets 1 found real steps of 0.12–0.33 m.
  - Q: which points are "curb": the face only, the face and top, or the whole kerb unit?
  - Q: where exactly is the curb line: the toe at road level, the lip at the top, or both?
- **Lowered (dropped) kerbs and kerb ramps** at pedestrian crossings and driveways, usually with **yellow tactile
  tiles** on the ramp. On streets 1, a zebra-crossing kerb drops from 0.12–0.20 m to 0.01–0.04 m.
  - Q: is a lowered kerb still curb?
  - Q: where does the ramp stop being curb and start being pavement?
- **Drains.** Singapore roads are drained heavily: roadside drains, covered drains under slabs or gratings, kerb
  inlets and gratings at intervals along the kerb, sumps, and larger open (monsoon) drains with railings. The user
  has observed that the only points below the road surface belong to the drains that periodically interrupt the
  curbs.
  - Q: are a grating, an open channel and a drain cover on the pavement all `drain`?
  - Q: does the curb class, and the curb line, continue across an inlet?
- **Road surface variants:**
  - asphalt, concrete bus bays, patches;
  - coloured surfacing: red cycling paths, red school-zone markings, possibly others;
  - road humps, rumble strips, manhole and utility covers.

  All carriageway is road. Each variant is an observation for the intensity and RGB thresholds.
- **Painted markings:**
  - lane lines, edge lines, single and double yellow lines along the kerb (parking rules);
  - zig-zags before crossings, zebra stripes, yellow box junctions;
  - arrows, give-way triangles, text ("SLOW", "LOOK RIGHT"), chevron-hatched areas (painted islands).

  Q: is a marking a property of the surface (a flag) rather than a class? Confirm. Earlier work on streets 1 found
  two bright intensity lines 0.25 m apart, 0.6–0.8 m from the south kerb, either paint (double yellow?) or a
  gutter band. Check in RGB.
- **Pavements and paths:** footpaths, park connectors (decided: pavement), cycling and shared paths, covered
  linkways (pavement under a roof), bus-stop platforms, steps and ramps. Q: one `pavement` class with attributes,
  or subclasses?
- **Raised traffic islands and medians:** kerbed, and topped with paving, grass, shrubs or trees. Island noses can
  have no vertical step; earlier work found an island back with only a gradual 0.01–0.04 m change. Q: is a paved
  island top pavement, a planted one grass or shrub, or is `island` its own class?
- **Driveways and vehicle crossings** over the pavement, often concrete with lowered kerbs. Q: road, pavement or a
  class of their own?
- **Vegetated ground:**
  - grass verges, tree planting strips, slopes and embankments;
  - planter boxes;
  - hedges and shrubs, which are very common and often trimmed into box shapes.

  Q: where does grass end and shrub begin (height? roughness?), and how are tree pits and tree grates labelled?

### objects

- **Poles:** lamp posts (exported by the current product), traffic-light poles with signal heads (also exported),
  sign posts, bollards, CCTV and enforcement cameras, ERP gantries. Q: do arms and heads belong to the pole? One
  class per pole type? Earlier pole detection here picked up at least one tree trunk as a pole.
- **Trees:** trunks are pole-like obstacles; canopies hide the ground and darken RGB beneath them. Q: label trunk
  and canopy separately?
- **Street furniture:** railings and pedestrian guardrails, crash barriers, bus shelters, benches, bins, fire
  hydrants, cabinets, bicycle racks.
- **Structures:** buildings and walls (streets 1 has no real facades), overhead bridges, covered-linkway roofs.
- **Temporary and moving things:** parked and passing vehicles, people, cones, barricades. Moving objects leave
  **ghost streaks**. Q: label a ghost as its own class or as noise?
- **Scanner artefacts:**
  - the blind circle under the tripod;
  - mixed pixels at edges (points floating between foreground and background);
  - multipath points below the ground;
  - sky noise.

Prior observations from earlier work in this workspace are listed in [OBSERVATIONS.md](OBSERVATIONS.md) with
status `to verify`.

## glossary

- **TLS**: terrestrial laser scanning (static, on a tripod).
- **BEV**: bird's-eye view (top-down orthographic).
- **toe / lip**: the bottom and top edges of a curb face.
- **crossfall / camber**: the sideways slope of a road. **Grade**: the slope along it.
- **reference frame**: a rotation and offset (e.g. from the flatten tool) in which "height" is measured.
- **reference surface**: a fitted ground model (a plane or a smooth surface) that gives "height above road".
- **local relief**: height minus a smoothed version of itself. It shows cm-scale relief anywhere (a technique from
  archaeological LiDAR).
- **GPF**: Ground Plane Fitting, Zermas, Izzat & Papanikolopoulos, ICRA 2017.
- **3D-Curb**: a curb dataset built on SemanticKITTI. Its curb labels were generated from road boundaries by GPF and
  then refined by hand in BEV.
