"""Point renderer (moderngl only, no Qt): paged tile streaming, LOD, colour modes, clip box, EDL, depth readback.

Streaming. Each tile's points live on the GPU as a run of pages that grow geometrically (PAGE_FIRST, PAGE_FIRST,
2x, 4x ... up to PAGE_MAX points), always a prefix of the tile, so any prefix draw is a uniform subsample and a
distant tile that needs a few hundred points costs one small page. A worker thread packs pages from the store memmaps; the render
thread uploads ready pages for at most ``upload_ms`` per frame and evicts tail pages (least recently used tiles
first) to stay within the memory budget. Pages are requested coarse-first: page 0 of every visible tile, then
page 1, and so on, larger on-screen tiles first.

Drawing. Points render into an offscreen colour + depth target; a full-screen pass applies eye-dome lighting
and writes to the target framebuffer; overlays (selection, clip box, pick and pivot markers, the shape being drawn)
go on top.
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
from pclabel.render.shaders import (
    EDL_FS,
    FULLSCREEN_VS,
    LINE_FS,
    LINE_VS,
    MODE_RGB,
    POINT_FS,
    POINT_VS,
    SEL_FS,
    SEL_VS,
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
LABEL_STRIDE = 2  # u8 label, u8 flags
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


def pack_page(src: PointSource, a: int, b: int) -> tuple[np.ndarray, np.ndarray]:
    """Vertex bytes for store points a..b: geometry (m, 5) uint32 and labels (m, 2) uint8."""
    m = b - a
    geom = np.empty((m, 5), np.uint32)
    has_rgb, has_int = src.rgb is not None, src.intensity is not None
    rgb = np.asarray(src.rgb) if has_rgb else np.zeros((1, 3), np.uint8)
    inten = np.asarray(src.intensity) if has_int else np.zeros(1, np.uint16)
    _pack_geometry(np.asarray(src.xyz), rgb, inten, a, b, geom.view(np.float32), geom, has_rgb, has_int)
    lab = np.zeros((m, 2), np.uint8)
    if src.labels is not None:
        lab[:, 0] = src.labels[a:b]
    if src.flags is not None:
        lab[:, 1] = src.flags[a:b]
    return geom, lab


class _Page:
    __slots__ = ("geom", "lab", "vao", "n")

    def __init__(self, geom, lab, vao, n):
        self.geom, self.lab, self.vao, self.n = geom, lab, vao, n

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
        self.sel_prog = ctx.program(vertex_shader=SEL_VS, fragment_shader=SEL_FS)
        self._sel = None  # (buffer, vao, n) of the selection overlay
        self.selection_color = (1.0, 0.85, 0.1, 0.55)
        self.fsq = ctx.vertex_array(self.edl, [])
        self.tex_cyclic = self._tex1d(cyclic_oklab(256), repeat=True)
        self.tex_ramp = self._tex1d(sequential_oklab(256), repeat=False)
        table = np.zeros((256, 4), np.uint8)
        table[:, :3] = np.asarray(class_colors, np.uint8)
        table[:, 3] = 255
        self.tex_classes = ctx.texture((256, 1), 4, table.tobytes())
        self.tex_classes.filter = (moderngl.NEAREST, moderngl.NEAREST)
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
        self.set_selection(None)
        for obj in (self.prog, self.edl, self.line, self.sel_prog, self.fsq, self.tex_cyclic, self.tex_ramp,
                    self.tex_classes):  # fmt: skip
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
            try:
                geom, lab = pack_page(self.src, a, b)
                self._q_out.put((t, j, geom, lab))
            except Exception as e:  # surface in the render thread
                self._q_out.put((t, j, e, None))

    def _upload(self, t, j, geom, lab):
        vg = self.ctx.buffer(geom)
        vl = self.ctx.buffer(lab)
        vao = self.ctx.vertex_array(
            self.prog,
            [(vg, "3f 4f1 u2 x2", "in_pos", "in_rgb", "in_int"), (vl, "u1 x1", "in_label")],
        )
        self.pages[t].append(_Page(vg, vl, vao, len(geom)))
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
            "u_hrange": tuple(float(v) for v in d.height_range), "u_flat": tuple(d.flat_color),
            "u_period": float(d.period), "u_h0": float(d.h0),
            "u_cyclic": 0, "u_ramp": 1, "u_classes": 2,
            "u_clip_on": int(d.clip_on),
            "u_clip_min": tuple(float(v) for v in np.asarray(d.clip_min) - off),
            "u_clip_max": tuple(float(v) for v in np.asarray(d.clip_max) - off),
            "u_size_mode": int(d.adaptive), "u_point_size": float(d.point_size), "u_max_size": float(d.max_size),
            "u_px_per_unit": float(camera.pixels_per_unit(h)), "u_ortho": int(ortho), "u_round": int(d.round_points),
        })  # fmt: skip
        self.tex_cyclic.use(0)
        self.tex_ramp.use(1)
        self.tex_classes.use(2)
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

    def set_selection(self, xyz_local: np.ndarray | None):
        """Points to highlight (local coordinates; a subsample of a large selection is enough), or None."""
        if self._sel is not None:
            self._sel[1].release()
            self._sel[0].release()
            self._sel = None
        if xyz_local is not None and len(xyz_local):
            buf = self.ctx.buffer(np.ascontiguousarray(xyz_local, np.float32))
            self._sel = (buf, self.ctx.vertex_array(self.sel_prog, [(buf, "3f", "in_pos")]), len(xyz_local))

    def _draw_overlays(self, mvp):
        lines = []
        d = self.display
        off = np.asarray(self.src.offset, np.float64)
        if self._sel is not None:
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
            self.sel_prog["u_mvp"].write(np.ascontiguousarray(mvp.T, np.float32).tobytes())
            self.sel_prog["u_size"].value = float(max(d.point_size + 1.0, 3.0))  # sparse selections stay visible
            self.sel_prog["u_color"].value = self.selection_color
            self._sel[1].render(moderngl.POINTS, vertices=self._sel[2])
            self.ctx.disable(moderngl.BLEND)
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
            self._lines(np.eye(4), pts, (1.0, 1.0, 1.0, 1.0), moderngl.LINE_LOOP)

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
