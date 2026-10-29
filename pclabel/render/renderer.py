"""Point renderer (moderngl only, no Qt): paged tile streaming, LOD, colour modes, clip box, EDL, depth readback.

Streaming. Each tile's points live on the GPU as a run of pages that grow geometrically (PAGE_FIRST, PAGE_FIRST,
2x, 4x ... up to PAGE_MAX points), always a prefix of the tile, so any prefix draw is a uniform subsample and a
distant tile that needs a few hundred points costs one small page. A worker thread packs pages from the store memmaps; the render
thread uploads ready pages for at most ``upload_ms`` per frame and evicts tail pages (least recently used tiles
first) to stay within the memory budget. Pages are requested coarse-first: page 0 of every visible tile, then
page 1, and so on, larger on-screen tiles first.

Drawing. Points render into an offscreen colour + depth target; a full-screen pass applies eye-dome lighting
and writes to the target framebuffer; overlays (clip box, pick and pivot markers, coloured marker points, the shape
being drawn) go on top.

Selection and highlights. The selected store indices are a GPU-side bit (SELECTED_BIT) in each page's flags byte, so
exactly the drawn points that are selected are tinted. Two more bits mark a preview group (GROUP_BIT, orange) and
points to look at (UNSURE_BIT, magenta), e.g. while splitting a point set. A third byte marks the points of the clip
to a point set (INSIDE_BIT); with ``Display.clip_set_on`` only those are drawn. Changing any of them rewrites the
label buffers of the resident pages of the affected tiles only; pages packed under older marks are corrected before
upload.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field

import moderngl
import numba as nb
import numpy as np

from pclabel.render.camera import ORTHO, OrbitCamera
from pclabel.render.colormaps import cyclic_oklab, sequential_oklab
from pclabel.render.lod import LodParams, lod_counts
from pclabel.tools import sets
from pclabel.render.shaders import (
    EDL_FS,
    FULLSCREEN_VS,
    LINE_FS,
    LINE_VS,
    MODE_RGB,
    POINT_FS,
    POINT_VS,
    MARK_FS,
    MARK_VS,
    VLINE_FS,
    VLINE_GS,
    VLINE_VS,
    VPOINT_FS,
    VPOINT_VS,
)

PAGE_FIRST = 1 << 14
PAGE_MAX = 1 << 18
# page j covers tile points [PAGE_ENDS[j-1], PAGE_ENDS[j]); sizes 16k, 16k, 32k, 64k, 128k, then 256k each
_SIZES = [PAGE_FIRST] + [min(PAGE_FIRST << max(j - 1, 0), PAGE_MAX) for j in range(1, 4096)]
PAGE_ENDS = np.cumsum(_SIZES).astype(np.int64)


def pages_for(k: np.ndarray) -> np.ndarray:
    """Number of leading pages needed to hold k points (0 for k = 0)."""
    k = np.asarray(k, np.int64)
    return np.where(k > 0, np.searchsorted(PAGE_ENDS, k, side="left") + 1, 0)


def page_range(j: int) -> tuple[int, int]:
    """Tile-relative point range of page j."""
    return (int(PAGE_ENDS[j - 1]) if j > 0 else 0), int(PAGE_ENDS[j])

GEOM_STRIDE = 20  # f32 x3, rgba u8 x4, u16 intensity, u16 pad
LABEL_STRIDE = 4  # u8 label, u8 flags, u8 extra bits (GPU only), u8 annotation label code (GPU only, labels view)
SELECTED_BIT = 128  # flags bit 7 on the GPU only (taxonomy flags use bits 0-4): the point is selected
GROUP_BIT = 64  # bit 6: in a preview group (orange)
UNSURE_BIT = 32  # bit 5: worth a look (magenta), e.g. possibly misclassified
INSIDE_BIT = 1 << 8  # extra byte, bit 0: in the clip to a point set
HIGHLIGHT_BITS = (SELECTED_BIT, GROUP_BIT, UNSURE_BIT)
MARK_BITS = HIGHLIGHT_BITS + (INSIDE_BIT,)
BYTES_PER_POINT = GEOM_STRIDE + LABEL_STRIDE


@dataclass
class PointSource:
    """What the renderer draws: tiles plus store-order columns (memmaps are fine)."""

    tiles: np.ndarray
    xyz: np.ndarray
    rgb: np.ndarray | None
    intensity: np.ndarray | None
    labels: np.ndarray | None
    flags: np.ndarray | None
    offset: np.ndarray = field(default_factory=lambda: np.zeros(3))

    @classmethod
    def from_project(cls, p) -> "PointSource":
        return cls(p.tiles, p.xyz, p.rgb, p.intensity, p.labels, p.flags, p.offset)

    @property
    def n(self) -> int:
        return int(self.xyz.shape[0])


@dataclass
class Display:
    """Everything that changes the picture but not the data. Heights and clip box in world coordinates."""

    mode: int = MODE_RGB
    point_size: float = 2.0  # fixed size, or the minimum when adaptive
    adaptive: bool = True
    max_size: float = 6.0
    round_points: bool = False
    edl: bool = True
    edl_strength: float = 0.4
    edl_radius: float = 1.4
    background: tuple = (0.07, 0.07, 0.08)
    flat_color: tuple = (0.80, 0.80, 0.78)
    gamma: float = 0.6
    intensity_range: tuple = (0.0, 1.0)  # stretched to black..white before gamma
    height_range: tuple = (0.0, 10.0)
    period: float = 0.03
    h0: float = 0.0
    # height axis (the active height frame): h = (p - height_origin) . height_normal, world coordinates
    height_normal: tuple = (0.0, 0.0, 1.0)
    height_origin: tuple = (0.0, 0.0, 0.0)
    band_on: bool = False  # slice: show only band[0] <= h <= band[1] (metres)
    band: tuple = (-0.10, 0.30)
    clip_on: bool = False
    clip_min: tuple = (-1e9, -1e9, -1e9)
    clip_max: tuple = (1e9, 1e9, 1e9)
    clip_volume: object = None  # tools.volume.VolumeSpec in local coordinates (from 3D select): show only inside
    clip_volume_on: bool = False
    clip_set_on: bool = False  # show only the points marked INSIDE_BIT (the clip to a point set)


@dataclass
class FrameStats:
    drawn: int = 0
    visible_tiles: int = 0
    wanted_pages: int = 0
    resident_pages: int = 0
    resident_bytes: int = 0
    memory_full: bool = False  # the memory budget cannot hold every wanted page
    inflight: int = 0
    ready: int = 0  # packed pages waiting for upload
    uploaded: int = 0
    upload_ms: float = 0.0
    cpu_ms: float = 0.0
    gpu_ms: float = float("nan")
    converged: bool = True


@nb.njit(nogil=True, cache=True)
def _pack_geometry(xyz, rgb, inten, a, b, f32, u32, has_rgb, has_int):
    for i in range(b - a):
        j = a + i
        f32[i, 0] = xyz[j, 0]
        f32[i, 1] = xyz[j, 1]
        f32[i, 2] = xyz[j, 2]
        if has_rgb:
            u32[i, 3] = np.uint32(rgb[j, 0]) | (np.uint32(rgb[j, 1]) << 8) | (np.uint32(rgb[j, 2]) << 16) | (
                np.uint32(255) << 24
            )
        else:
            u32[i, 3] = np.uint32(0xFF808080)
        u32[i, 4] = np.uint32(inten[j]) if has_int else np.uint32(0)


def label_bytes(src: PointSource, a: int, b: int, marks, layers=()) -> np.ndarray:
    """(m, 4) uint8: label, flags and extra bits of store points a..b, with the mark bits set (flags bits 5-7, or
    INSIDE_BIT in the extra byte), and the annotation label code of each point (fourth byte, the labels view).
    ``marks`` is {bit: sorted store indices}, or just the selected indices; ``layers`` is [(sorted store indices,
    code)], later layers over earlier ones."""
    lab = np.zeros((b - a, LABEL_STRIDE), np.uint8)
    if src.labels is not None:
        lab[:, 0] = src.labels[a:b]
    if src.flags is not None:
        lab[:, 1] = src.flags[a:b]
    if not isinstance(marks, dict):
        marks = {SELECTED_BIT: marks}
    for bit, idx in marks.items():
        if idx is not None and idx.size:
            i0, i1 = np.searchsorted(idx, [a, b])
            col, value = (1, bit) if bit < 256 else (2, bit >> 8)
            lab[idx[i0:i1] - a, col] |= value
    for idx, code in layers:
        i0, i1 = np.searchsorted(idx, [a, b])
        if i1 > i0:
            lab[idx[i0:i1] - a, 3] = code
    return lab


def pack_page(src: PointSource, a: int, b: int, marks=None, layers=()) -> tuple[np.ndarray, np.ndarray]:
    """Vertex bytes for store points a..b: geometry (m, 5) uint32 and labels (m, 2) uint8."""
    m = b - a
    geom = np.empty((m, 5), np.uint32)
    has_rgb, has_int = src.rgb is not None, src.intensity is not None
    rgb = np.asarray(src.rgb) if has_rgb else np.zeros((1, 3), np.uint8)
    inten = np.asarray(src.intensity) if has_int else np.zeros(1, np.uint16)
    _pack_geometry(np.asarray(src.xyz), rgb, inten, a, b, geom.view(np.float32), geom, has_rgb, has_int)
    return geom, label_bytes(src, a, b, marks, layers)


class _Page:
    __slots__ = ("geom", "lab", "vao", "n", "a")

    def __init__(self, geom, lab, vao, n, a):
        self.geom, self.lab, self.vao, self.n, self.a = geom, lab, vao, n, a  # a: first store index

    def release(self):
        self.vao.release()
        self.geom.release()
        self.lab.release()


class PointRenderer:
    def __init__(self, ctx: moderngl.Context, source: PointSource, class_colors, *, memory_bytes: int,
                 point_budget: int, upload_ms: float = 6.0, max_inflight: int = 8):  # fmt: skip
        self.ctx = ctx
        self.src = source
        self.display = Display()
        self.lod = LodParams(point_budget=point_budget)
        self.memory_bytes = int(memory_bytes)
        self.upload_ms = upload_ms
        self.max_inflight = max_inflight
        t = source.tiles
        self.n_tiles = len(t)
        self.t_start = t["start"].astype(np.int64)
        self.t_count = t["count"].astype(np.int64)
        self.t_lo = np.stack([t["x0"], t["y0"], t["zmin"]], 1).astype(np.float64)
        self.t_hi = np.stack([t["x1"], t["y1"], t["zmax"]], 1).astype(np.float64)
        self.t_area_xy = np.maximum((self.t_hi[:, 0] - self.t_lo[:, 0]) * (self.t_hi[:, 1] - self.t_lo[:, 1]), 0.01)
        self.scene_lo, self.scene_hi = self.t_lo.min(0), self.t_hi.max(0)
        self.t_pages_max = pages_for(self.t_count)
        self.memory_full = False
        self.pages: list[list[_Page]] = [[] for _ in range(self.n_tiles)]
        self.last_used = np.zeros(self.n_tiles, np.int64)
        self.resident_bytes = 0
        self.frame = 0
        self._inflight: set = set()
        self._ready: dict = {}
        self._q_in: queue.Queue = queue.Queue()
        self._q_out: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._work, daemon=True, name="page-packer")
        self._worker.start()
        self._closed = False

        self.prog = ctx.program(vertex_shader=POINT_VS, fragment_shader=POINT_FS)
        self.edl = ctx.program(vertex_shader=FULLSCREEN_VS, fragment_shader=EDL_FS)
        self.line = ctx.program(vertex_shader=LINE_VS, fragment_shader=LINE_FS)
        self.mark_prog = ctx.program(vertex_shader=MARK_VS, fragment_shader=MARK_FS)
        self._marks = None  # (buffer, vao, n): coloured marker points (e.g. where a grow stopped)
        self.vline_prog = ctx.program(vertex_shader=VLINE_VS, geometry_shader=VLINE_GS, fragment_shader=VLINE_FS)
        self.vpoint_prog = ctx.program(vertex_shader=VPOINT_VS, fragment_shader=VPOINT_FS)
        self._vectors: dict = {}  # layer -> ((buffer, vao, n) lines or None, (...) points or None), drawn on top
        self._vol_tex = None  # the clip volume's loop mask, and the volume it is for
        self._vol_tex_for = None
        self._no_mask = ctx.texture((1, 1), 1, b"\x00", dtype="f1")
        self.marks = {bit: np.empty(0, np.int64) for bit in MARK_BITS}  # bit -> sorted store indices
        self.label_layers: tuple = ()  # ((sorted store indices, code), ...): annotation labels for the labels view
        self._sel_snapshot = (dict(self.marks), 0, ())  # (marks, version, layers), read by the packer thread
        self.selection_tint = (1.0, 0.85, 0.1, 0.65)
        self.group_tint = (1.0, 0.42, 0.05, 0.8)
        self.unsure_tint = (1.0, 0.1, 0.85, 0.9)
        self.fsq = ctx.vertex_array(self.edl, [])
        self.tex_cyclic = self._tex1d(cyclic_oklab(256), repeat=True)
        self.tex_ramp = self._tex1d(sequential_oklab(256), repeat=False)
        table = np.zeros((256, 4), np.uint8)
        table[:, :3] = np.asarray(class_colors, np.uint8)
        table[:, 3] = 255
        self.tex_classes = ctx.texture((256, 1), 4, table.tobytes())
        self.tex_classes.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.tex_labels = ctx.texture((256, 1), 4, np.zeros((256, 4), np.uint8).tobytes())  # code -> rgb, a: shown
        self.tex_labels.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.labels_only = False  # the labels view: hide the points that show no label (else they are dim grey)
        self._target = None  # (size, fbo, color, depth)
        self._queries = [ctx.query(time=True), ctx.query(time=True)]
        self._q_used = [False, False]
        self._qi = 0
        self.last_mvp = None
        self.last_viewport = None
        self.last_k = np.zeros(self.n_tiles, np.int64)
        self.marker = None  # world point to mark (pick)
        self.pivot = None  # world point the camera orbits around, shown while rotating
        self.screen_shape = None  # (n, 2) NDC polyline of the shape being drawn (lasso, rectangle)
        self.screen_shape_color = (1.0, 1.0, 1.0, 1.0)

    # ---- resources ----------------------------------------------------------------------------------------

    def _tex1d(self, colors, repeat):
        tex = self.ctx.texture((len(colors), 1), 3, np.ascontiguousarray(colors, np.uint8).tobytes())
        tex.repeat_x, tex.repeat_y = repeat, False
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        return tex

    def set_class_colors(self, colors, visible=None):
        table = np.zeros((256, 4), np.uint8)
        table[:, :3] = np.asarray(colors, np.uint8)
        table[:, 3] = 255 if visible is None else np.where(np.asarray(visible, bool), 255, 0)
        self.tex_classes.write(table.tobytes())

    def set_label_colors(self, table):
        """(256, 4) uint8 RGBA per annotation label code, for the labels view; alpha 0 hides the code."""
        self.tex_labels.write(np.ascontiguousarray(table, np.uint8).tobytes())

    def set_label_layers(self, layers):
        """The annotation label code of the points: [(sorted store indices, code)], later layers over earlier
        ones. Rewrites the label buffers of resident pages in the tiles the old or new layers touch."""
        layers = tuple((np.asarray(i, np.int64), int(c)) for i, c in layers)
        touched = np.zeros(self.n_tiles, bool)
        for idx, _ in self.label_layers + layers:
            if idx.size:
                touched |= np.searchsorted(idx, self.t_start + self.t_count) > np.searchsorted(idx, self.t_start)
        self.label_layers = layers
        marks, version, _ = self._sel_snapshot
        self._sel_snapshot = (marks, version + 1, layers)
        for t in np.flatnonzero(touched):
            for pg in self.pages[t]:
                pg.lab.write(label_bytes(self.src, pg.a, pg.a + pg.n, self.marks, layers))

    def _ensure_target(self, w, h):
        if self._target is not None and self._target[0] == (w, h):
            return
        if self._target is not None:
            for obj in self._target[1:]:
                obj.release()
        color = self.ctx.texture((w, h), 4)
        color.filter = (moderngl.NEAREST, moderngl.NEAREST)
        depth = self.ctx.depth_texture((w, h))
        depth.compare_func = ""
        depth.filter = (moderngl.NEAREST, moderngl.NEAREST)
        fbo = self.ctx.framebuffer(color_attachments=[color], depth_attachment=depth)
        self._target = ((w, h), fbo, color, depth)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._q_in.put(None)
        for pages in self.pages:
            for pg in pages:
                pg.release()
        self.pages = [[] for _ in range(self.n_tiles)]
        if self._target is not None:
            for obj in self._target[1:]:
                obj.release()
        self.set_markers(None)
        for layer in list(self._vectors):
            self.set_vectors(None, None, layer)
        for tex in (self._vol_tex, self._no_mask):
            if tex is not None:
                tex.release()
        for obj in (self.prog, self.edl, self.line, self.mark_prog, self.vline_prog, self.vpoint_prog, self.fsq,
                    self.tex_cyclic, self.tex_ramp, self.tex_classes):  # fmt: skip
            obj.release()
        self._queries = []  # moderngl queries have no release(); freed with the context

    # ---- streaming ----------------------------------------------------------------------------------------

    def _work(self):
        while True:
            item = self._q_in.get()
            if item is None:
                return
            t, j = item
            lo, hi = page_range(j)
            a = int(self.t_start[t] + lo)
            b = int(self.t_start[t] + min(hi, self.t_count[t]))
            marks, version, layers = self._sel_snapshot
            try:
                geom, lab = pack_page(self.src, a, b, marks, layers)
                self._q_out.put((t, j, geom, (lab, version, a)))
            except Exception as e:  # surface in the render thread
                self._q_out.put((t, j, e, None))

    def _upload(self, t, j, geom, packed):
        lab, version, a = packed
        if version != self._sel_snapshot[1]:  # packed under older marks
            lab = label_bytes(self.src, a, a + len(geom), self.marks, self.label_layers)
        vg = self.ctx.buffer(geom)
        vl = self.ctx.buffer(lab)
        vao = self.ctx.vertex_array(
            self.prog,
            [(vg, "3f 4f1 u2 x2", "in_pos", "in_rgb", "in_int"), (vl, "u1 u1 u1 u1", "in_label", "in_flags", "in_extra", "in_ann")],
        )
        self.pages[t].append(_Page(vg, vl, vao, len(geom), a))
        self.resident_bytes += len(geom) * BYTES_PER_POINT

    def _evict_one(self, want_pages, protect_tile) -> bool:
        """Drop the last page of the best victim tile; False if nothing may be evicted."""
        have = np.array([len(p) for p in self.pages])
        excess = have - want_pages
        cand = np.flatnonzero((have > 0) & (excess > 0))
        cand = cand[cand != protect_tile]
        if cand.size == 0:
            return False
        # unwanted tiles first, then the least recently used
        order = np.lexsort((self.last_used[cand], want_pages[cand] > 0))
        t = int(cand[order[0]])
        pg = self.pages[t].pop()
        self.resident_bytes -= pg.n * BYTES_PER_POINT
        pg.release()
        return True

    def _collect(self, want_pages, deadline):
        uploaded = 0
        while True:
            try:
                t, j, geom, lab = self._q_out.get_nowait()
                self._inflight.discard((t, j))
                if isinstance(geom, Exception):
                    raise geom
                self._ready[(t, j)] = (geom, lab)
            except queue.Empty:
                break
        progressed = True
        while progressed and self._ready:
            progressed = False
            for (t, j) in sorted(self._ready, key=lambda key: key[1]):
                if j >= want_pages[t] or j < len(self.pages[t]):
                    del self._ready[(t, j)]  # no longer wanted (or already resident)
                    progressed = True
                    continue
                if j != len(self.pages[t]):
                    continue  # waits for its predecessor
                if deadline is not None and uploaded > 0 and time.perf_counter() > deadline:
                    return uploaded  # at least one page per frame, so streaming always progresses
                need = self._ready[(t, j)][0].shape[0] * BYTES_PER_POINT
                while self.resident_bytes + need > self.memory_bytes:
                    if not self._evict_one(want_pages, t):
                        self.memory_full = True
                        return uploaded
                geom, lab = self._ready.pop((t, j))
                self._upload(t, j, geom, lab)
                uploaded += 1
                progressed = True
        return uploaded

    def _request(self, want_pages, area):
        free = self.max_inflight - len(self._inflight)
        if free <= 0 or (self.memory_full and self._ready):
            return
        have = np.array([len(p) for p in self.pages])
        pending: dict = {}
        for (t, j) in list(self._inflight) + list(self._ready):
            pending.setdefault(t, set()).add(j)
        cand = []
        # every missing page from the resident prefix upwards: an evicted page below a pending one is asked for
        # again, otherwise the pending page would wait for its predecessor forever
        for t in np.flatnonzero(want_pages > have):
            p = pending.get(t, ())
            for j in range(have[t], want_pages[t]):
                if j not in p:
                    cand.append((j, -area[t], t))
        cand.sort()
        for j, _, t in cand[:free]:
            self._inflight.add((t, j))
            self._q_in.put((t, j))

    # ---- frame --------------------------------------------------------------------------------------------

    def render(self, camera: OrbitCamera, viewport, target: moderngl.Framebuffer, *, wait: bool = False) -> FrameStats:
        """Draw one frame into ``target``. ``wait=True`` blocks until every wanted page is resident (tests,
        screenshots)."""
        t0 = time.perf_counter()
        w, h = int(viewport[0]), int(viewport[1])
        self._ensure_target(w, h)
        camera.update_clip(self.scene_lo, self.scene_hi)
        mvp = camera.mvp(w / h)
        k, area = lod_counts(self.t_count, self.t_lo, self.t_hi, mvp, (w, h), self.lod)
        self.frame += 1
        self.last_used[k > 0] = self.frame
        want_pages = np.minimum(pages_for(k), self.t_pages_max)
        self.memory_full = False
        t_up = time.perf_counter()
        uploaded = self._collect(want_pages, None if wait else t_up + self.upload_ms / 1000)
        self._request(want_pages, area)
        while wait and any(len(self.pages[t]) < want_pages[t] for t in np.flatnonzero(want_pages)):
            if not self._inflight:
                self._request(want_pages, area)
                if not self._inflight:
                    break  # nothing more can come (memory budget smaller than what is wanted)
            self._q_out.put(self._q_out.get())  # block until a page is packed
            uploaded += self._collect(want_pages, None)
            self._request(want_pages, area)
        upload_ms = (time.perf_counter() - t_up) * 1000

        d = self.display
        _, fbo, color, depth = self._target
        fbo.use()
        bg = d.background
        self.ctx.clear(bg[0], bg[1], bg[2], 1.0, depth=1.0)
        self.ctx.enable(moderngl.DEPTH_TEST | moderngl.PROGRAM_POINT_SIZE)
        self.ctx.disable(moderngl.BLEND)
        ortho = camera.projection == ORTHO
        off = np.asarray(self.src.offset, np.float64)
        u = self._uniforms(self.prog, {
            "u_mvp": np.ascontiguousarray(mvp.T, np.float32).tobytes(),
            "u_mode": int(d.mode), "u_gamma": float(d.gamma),
            "u_irange": tuple(float(v) for v in d.intensity_range),
            "u_h_normal": tuple(float(v) for v in d.height_normal),
            "u_h_origin": tuple(float(v) for v in np.asarray(d.height_origin, np.float64) - off),
            "u_band_on": int(d.band_on), "u_band": tuple(float(v) for v in d.band),
            "u_sel_tint": tuple(self.selection_tint), "u_group_tint": tuple(self.group_tint),
            "u_unsure_tint": tuple(self.unsure_tint),
            "u_hrange": tuple(float(v) for v in d.height_range), "u_flat": tuple(d.flat_color),
            "u_period": float(d.period), "u_h0": float(d.h0),
            "u_cyclic": 0, "u_ramp": 1, "u_classes": 2, "u_labels": 4, "u_labels_only": int(self.labels_only),
            **self._volume_uniforms(d),
            "u_clip_on": int(d.clip_on), "u_set_on": int(d.clip_set_on),
            "u_clip_min": tuple(float(v) for v in np.asarray(d.clip_min) - off),
            "u_clip_max": tuple(float(v) for v in np.asarray(d.clip_max) - off),
            "u_size_mode": int(d.adaptive), "u_point_size": float(d.point_size), "u_max_size": float(d.max_size),
            "u_px_per_unit": float(camera.pixels_per_unit(h)), "u_ortho": int(ortho), "u_round": int(d.round_points),
        })  # fmt: skip
        self.tex_cyclic.use(0)
        self.tex_ramp.use(1)
        self.tex_classes.use(2)
        self.tex_labels.use(4)
        vm = d.clip_volume.mask if d.clip_volume is not None else None
        (self._vol_tex if self._vol_tex is not None and vm is not None and self._vol_tex_for is vm else self._no_mask).use(3)
        q = self._queries[self._qi]
        drawn = 0
        with q:
            for t in np.flatnonzero(k):
                pages = self.pages[t]
                kk = min(int(k[t]), sum(pg.n for pg in pages))
                if kk <= 0:
                    continue
                if "u_spacing" in u:
                    u["u_spacing"].value = float(np.sqrt(self.t_area_xy[t] / kk))
                left = kk
                for pg in pages:
                    m = min(left, pg.n)
                    pg.vao.render(moderngl.POINTS, vertices=m)
                    left -= m
                    if left <= 0:
                        break
                drawn += kk
        self._q_used[self._qi] = True
        other = 1 - self._qi
        gpu_ms = self._queries[other].elapsed * 1e-6 if self._q_used[other] else float("nan")
        self._qi = other

        # eye-dome lighting into the target, then overlays
        target.use()
        self.ctx.disable(moderngl.DEPTH_TEST)
        color.use(0)
        depth.use(1)
        self._uniforms(self.edl, {
            "u_color": 0, "u_depth": 1, "u_texel": (1.0 / w, 1.0 / h),
            "u_strength": float(d.edl_strength if d.edl else 0.0), "u_radius": float(d.edl_radius),
            "u_near": float(camera.near), "u_far": float(camera.far), "u_ortho": int(ortho),
            "u_ortho_ref": float(max(camera.distance, 1e-3)), "u_background": tuple(bg),
        })  # fmt: skip
        self.fsq.render(moderngl.TRIANGLES, vertices=3)
        self._cam_distance = float(camera.distance)
        self._draw_overlays(mvp)

        self.last_mvp, self.last_viewport, self.last_k = mvp, (w, h), k
        have = np.array([len(p) for p in self.pages])
        return FrameStats(
            drawn=drawn, visible_tiles=int((k > 0).sum()), wanted_pages=int(want_pages.sum()),
            resident_pages=int(have.sum()), resident_bytes=self.resident_bytes, inflight=len(self._inflight),
            ready=len(self._ready),
            uploaded=uploaded, upload_ms=upload_ms, cpu_ms=(time.perf_counter() - t0) * 1000, gpu_ms=gpu_ms,
            converged=bool(np.all(have >= want_pages)), memory_full=self.memory_full,
        )  # fmt: skip

    def _volume_uniforms(self, d) -> dict:
        """The clip volume's uniforms (and its mask texture for a loop)."""
        v = d.clip_volume if d.clip_volume_on else None
        kind = 0 if v is None else {"box": 1, "cylinder": 2, "loop": 3}[v.kind]
        if kind == 3 and self._vol_tex_for is not v.mask:  # moving a loop keeps its mask: no upload
            if self._vol_tex is not None:
                self._vol_tex.release()
            m = np.ascontiguousarray(v.mask, np.uint8) * 255
            self._vol_tex = self.ctx.texture((m.shape[1], m.shape[0]), 1, m.tobytes(), dtype="f1", alignment=1)
            self._vol_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
            self._vol_tex_for = v.mask
        f3 = lambda a: tuple(float(x) for x in a)  # noqa: E731
        if v is None:
            return {"u_vol_on": 0, "u_vol_mask": 3}
        return {"u_vol_on": kind, "u_vol_origin": f3(v.origin), "u_vol_n": f3(v.n), "u_vol_u": f3(v.u),
                "u_vol_v": f3(v.v), "u_vol_h": (float(v.bottom), float(v.top)),
                "u_vol_half": (float(v.half[0]), float(v.half[1])), "u_vol_grid": f3(v.grid), "u_vol_mask": 3}  # fmt: skip

    def _uniforms(self, prog, values):
        found = {}
        for name, v in values.items():
            if name in prog:
                if isinstance(v, bytes):
                    prog[name].write(v)
                else:
                    prog[name].value = v
        for name in ("u_spacing",):
            if name in prog:
                found[name] = prog[name]
        return found

    @property
    def selection(self) -> np.ndarray:
        return self.marks[SELECTED_BIT]

    def set_selection(self, indices: np.ndarray | None):
        """Tint these store indices (sorted) as selected."""
        self.set_marks(SELECTED_BIT, indices)

    def set_marks(self, bit: int, indices: np.ndarray | None):
        """Set one highlight bit on these store indices (sorted). Rewrites the label buffers of resident pages of
        the affected tiles only."""
        new = np.empty(0, np.int64) if indices is None else np.asarray(indices, np.int64)
        changed = sets.xor(self.marks[bit], new)  # one linear merge: these can hold hundreds of millions of points
        self.marks = {**self.marks, bit: new}
        self._sel_snapshot = (dict(self.marks), self._sel_snapshot[1] + 1, self.label_layers)
        if changed.size == 0:
            return
        first = np.searchsorted(changed, self.t_start)  # tiles are consecutive store ranges
        tiles = np.flatnonzero(np.r_[first[1:], changed.size] > first)
        for t in tiles:
            for pg in self.pages[t]:
                pg.lab.write(label_bytes(self.src, pg.a, pg.a + pg.n, self.marks, self.label_layers))

    def set_markers(self, xyz_local: np.ndarray | None, rgb: np.ndarray | None = None):
        """Coloured points drawn on top of everything (local coordinates, uint8 colours), or None."""
        if self._marks is not None:
            self._marks[1].release()
            self._marks[0].release()
            self._marks = None
        if xyz_local is not None and len(xyz_local):
            data = np.zeros(len(xyz_local), [("p", "<f4", 3), ("c", "u1", 4)])
            data["p"] = xyz_local
            data["c"][:, :3] = rgb
            data["c"][:, 3] = 255
            buf = self.ctx.buffer(data.tobytes())
            vao = self.ctx.vertex_array(self.mark_prog, [(buf, "3f 4f1", "in_pos", "in_color")])
            self._marks = (buf, vao, len(xyz_local))

    @property
    def _vlines(self):
        """The annotation layer's lines (tests look at it)."""
        return self._vectors.get("annotations", (None, None))[0]

    def set_vectors(self, lines, points, layer: str = "annotations"):
        """Vector drawings on top of everything, in named layers (each call replaces one layer; None clears it).
        - ``lines``: [(polyline (n, 3) local, rgba floats 0..1, width in pixels), ...]
        - ``points``: [(xyz (n, 3) local, rgba floats 0..1, size in pixels), ...]"""
        for v in self._vectors.pop(layer, ()):
            if v is not None:
                v[1].release()
                v[0].release()
        vlines = vpoints = None
        segs = [(np.asarray(p, np.float32), c, w) for p, c, w in (lines or []) if len(p) >= 2]
        if segs:
            n = sum(2 * (len(p) - 1) for p, _, _ in segs)
            data = np.zeros(n, [("p", "<f4", 3), ("c", "<f4", 4), ("w", "<f4")])
            k = 0
            for p, c, w in segs:
                m = 2 * (len(p) - 1)
                data["p"][k : k + m : 2] = p[:-1]
                data["p"][k + 1 : k + m : 2] = p[1:]
                data["c"][k : k + m] = c
                data["w"][k : k + m] = w
                k += m
            buf = self.ctx.buffer(data.tobytes())
            vao = self.ctx.vertex_array(self.vline_prog, [(buf, "3f 4f 1f", "in_pos", "in_color", "in_width")])
            vlines = (buf, vao, n)
        pts = [(np.asarray(p, np.float32).reshape(-1, 3), c, s) for p, c, s in (points or []) if len(p)]
        if pts:
            n = sum(len(p) for p, _, _ in pts)
            data = np.zeros(n, [("p", "<f4", 3), ("c", "<f4", 4), ("s", "<f4")])
            k = 0
            for p, c, s in pts:
                data["p"][k : k + len(p)] = p
                data["c"][k : k + len(p)] = c
                data["s"][k : k + len(p)] = s
                k += len(p)
            buf = self.ctx.buffer(data.tobytes())
            vao = self.ctx.vertex_array(self.vpoint_prog, [(buf, "3f 4f 1f", "in_pos", "in_color", "in_size")])
            vpoints = (buf, vao, n)
        if vlines is not None or vpoints is not None:
            self._vectors[layer] = (vlines, vpoints)

    def _draw_overlays(self, mvp):
        lines = []
        d = self.display
        off = np.asarray(self.src.offset, np.float64)
        if self._marks is not None:
            self.ctx.enable(moderngl.PROGRAM_POINT_SIZE)
            self.mark_prog["u_mvp"].write(np.ascontiguousarray(mvp.T, np.float32).tobytes())
            self.mark_prog["u_size"].value = 5.0
            self._marks[1].render(moderngl.POINTS, vertices=self._marks[2])
        for vlines, vpoints in self._vectors.values():
            if vlines is not None:
                self.vline_prog["u_mvp"].write(np.ascontiguousarray(mvp.T, np.float32).tobytes())
                self.vline_prog["u_viewport"].value = tuple(float(v) for v in self._target[0])
                vlines[1].render(moderngl.LINES, vertices=vlines[2])
            if vpoints is not None:
                self.ctx.enable(moderngl.PROGRAM_POINT_SIZE)
                self.vpoint_prog["u_mvp"].write(np.ascontiguousarray(mvp.T, np.float32).tobytes())
                vpoints[1].render(moderngl.POINTS, vertices=vpoints[2])
        if d.clip_on:
            lo = np.maximum(np.asarray(d.clip_min) - off, self.scene_lo - 1)
            hi = np.minimum(np.asarray(d.clip_max) - off, self.scene_hi + 1)
            c = np.array([[(hi if k & 1 else lo)[0], (hi if k & 2 else lo)[1], (hi if k & 4 else lo)[2]] for k in range(8)])
            for a, b in ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)):
                lines += [c[a], c[b]]
            self._lines(mvp, lines, (1.0, 0.8, 0.2, 1.0))
        if self.marker is not None:
            p = np.asarray(self.marker, float) - off
            s = 0.05
            m = [p - (s, 0, 0), p + (s, 0, 0), p - (0, s, 0), p + (0, s, 0), p - (0, 0, s), p + (0, 0, s)]
            self._lines(mvp, m, (1.0, 0.2, 0.9, 1.0))
        if self.pivot is not None:
            p = np.asarray(self.pivot, float) - off
            s = 0.02 * getattr(self, "_cam_distance", 10.0)  # about the same size on screen at any distance
            m = [p - (s, 0, 0), p + (s, 0, 0), p - (0, s, 0), p + (0, s, 0), p - (0, 0, s), p + (0, 0, s)]
            self._lines(mvp, m, (0.2, 0.9, 1.0, 1.0))
        if self.screen_shape is not None and len(self.screen_shape) >= 2:
            pts = np.c_[np.asarray(self.screen_shape, np.float32), np.zeros(len(self.screen_shape), np.float32)]
            self._lines(np.eye(4), pts, self.screen_shape_color, moderngl.LINE_LOOP)

    def _lines(self, mvp, pts, rgba, mode=moderngl.LINES):
        buf = self.ctx.buffer(np.asarray(pts, np.float32).tobytes())
        vao = self.ctx.vertex_array(self.line, [(buf, "3f", "in_pos")])
        self.line["u_mvp"].write(np.ascontiguousarray(mvp.T, np.float32).tobytes())
        self.line["u_color"].value = rgba
        vao.render(mode)
        vao.release()
        buf.release()

    # ---- readback -----------------------------------------------------------------------------------------

    def depth_near(self, px: float, py: float, radius: int = 4):
        """Window depth of the drawn point nearest to pixel (px, py) (top-left origin) within ``radius`` pixels:
        (px, py, depth) or None. Uses the last frame's depth target."""
        if self._target is None:
            return None
        (w, h), fbo, _, _ = self._target
        x0, x1 = max(0, int(px) - radius), min(w, int(px) + radius + 1)
        y0, y1 = max(0, int(py) - radius), min(h, int(py) + radius + 1)
        if x0 >= x1 or y0 >= y1:
            return None
        raw = fbo.read(viewport=(x0, h - y1, x1 - x0, y1 - y0), components=1, attachment=-1, dtype="f4")
        dep = np.frombuffer(raw, np.float32).reshape(y1 - y0, x1 - x0)[::-1]  # rows top-down
        yy, xx = np.nonzero(dep < 1.0)
        if yy.size == 0:
            return None
        cx, cy = xx + x0 + 0.5, yy + y0 + 0.5
        i = np.argmin((cx - px) ** 2 + (cy - py) ** 2 + dep[yy, xx] * 1e-3)
        return float(cx[i]), float(cy[i]), float(dep[yy[i], xx[i]])

    def read_depth(self) -> np.ndarray:
        """The last frame's depth buffer, (h, w) float32 window depth, row 0 at the top (for selection)."""
        (w, h), fbo, _, _ = self._target
        return np.frombuffer(fbo.read(components=1, attachment=-1, dtype="f4"), np.float32).reshape(h, w)[::-1].copy()

    def read_color(self) -> np.ndarray:
        """The last frame's point colours (before EDL), (h, w, 3) uint8, row 0 at the top."""
        (w, h), fbo, _, _ = self._target
        return np.frombuffer(fbo.read(components=3), np.uint8).reshape(h, w, 3)[::-1]
