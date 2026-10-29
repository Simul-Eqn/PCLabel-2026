"""Shader sources (no Qt). All colour modes are computed here from per-point attributes and uniforms, so changing
a mode, range or period never touches the point data (SPEC §2).

Height (for the height ramp, the wrap and the slice) is measured along the active height frame:
``h = dot(p - u_h_origin, u_h_normal)`` with ``p`` and ``u_h_origin`` in local (store) coordinates. The scan frame
is normal (0, 0, 1) through world z = 0; a plane frame comes from a plane fit (analysis/frames.py), and
``HeightFrame.heights`` is the numpy mirror.
"""

MODE_RGB, MODE_INTENSITY, MODE_HEIGHT, MODE_FLAT, MODE_CLASS, MODE_WRAP, MODE_WRAP_SHADED, MODE_LABELS = range(8)
MODE_NAMES = {
    MODE_RGB: "rgb",
    MODE_INTENSITY: "intensity",
    MODE_HEIGHT: "height",
    MODE_FLAT: "flat",
    MODE_CLASS: "class",
    MODE_WRAP: "wrap",
    MODE_WRAP_SHADED: "wrap x intensity",
    MODE_LABELS: "labels",  # annotation labels (D-007): each label in its colour, the rest dim grey (or hidden)
}

POINT_VS = """
#version 430
uniform mat4 u_mvp;
uniform int u_mode;
uniform vec3 u_h_normal;     // height axis (unit)
uniform vec3 u_h_origin;     // local point where h = 0
uniform int u_band_on;       // slice: show only u_band.x <= h <= u_band.y
uniform vec2 u_band;
uniform float u_gamma;
uniform vec2 u_irange;       // intensity stretch (0..1 of the stored range)
uniform vec2 u_hrange;
uniform vec3 u_flat;
uniform float u_period;
uniform float u_h0;
uniform sampler2D u_cyclic;
uniform sampler2D u_ramp;
uniform sampler2D u_classes;  // rgb = class colour, a = visible
uniform sampler2D u_labels;   // annotation label code -> rgb, a = shown (the labels view)
uniform int u_labels_only;     // the labels view: hide points that show no label
uniform int u_clip_on;
uniform int u_set_on;          // clip to a point set: only points with extra bit 0
uniform vec3 u_clip_min;
uniform vec3 u_clip_max;
uniform int u_vol_on;          // clip volume: 0 off, 1 box, 2 cylinder, 3 loop (a mask) - local coordinates
uniform vec3 u_vol_origin;
uniform vec3 u_vol_n;          // its axis: from u_vol_h.x to u_vol_h.y along it
uniform vec3 u_vol_u;          // across it: x along u, y along v
uniform vec3 u_vol_v;
uniform vec2 u_vol_h;
uniform vec2 u_vol_half;       // box half-sizes, or (radius, radius)
uniform vec3 u_vol_grid;       // loop mask: x0, y0, cell
uniform sampler2D u_vol_mask;
uniform int u_size_mode;       // 0 fixed, 1 adaptive
uniform float u_point_size;    // fixed size, or the minimum when adaptive
uniform float u_max_size;
uniform float u_spacing;       // world spacing of the drawn subset of this tile (adaptive)
uniform float u_px_per_unit;   // perspective: pixels per world unit at eye depth 1; ortho: per world unit
uniform int u_ortho;
uniform vec4 u_sel_tint;       // rgb tint and strength for selected points (flags bit 7, GPU only)
uniform vec4 u_group_tint;     // a preview group (bit 6)
uniform vec4 u_unsure_tint;    // points worth a look (bit 5)
in vec3 in_pos;
in vec4 in_rgb;
in uint in_int;
in uint in_label;
in uint in_flags;
in uint in_extra;              // GPU-only bits: 1 = in the clip to a point set
in uint in_ann;                // annotation label code (0: in no labelled point set), the labels view
out vec3 v_color;
const vec4 CULLED = vec4(2.0, 2.0, 2.0, 1.0);

bool outside_volume(vec3 p) {
    vec3 d = p - u_vol_origin;
    float h = dot(d, u_vol_n);
    if (h < u_vol_h.x || h > u_vol_h.y) return true;
    float x = dot(d, u_vol_u);
    float y = dot(d, u_vol_v);
    if (u_vol_on == 1) return abs(x) > u_vol_half.x || abs(y) > u_vol_half.y;
    if (u_vol_on == 2) return x * x + y * y > u_vol_half.x * u_vol_half.x;
    ivec2 size = textureSize(u_vol_mask, 0);
    ivec2 ij = ivec2(floor((x - u_vol_grid.x) / u_vol_grid.z), floor((y - u_vol_grid.y) / u_vol_grid.z));
    if (ij.x < 0 || ij.y < 0 || ij.x >= size.x || ij.y >= size.y) return true;
    return texelFetch(u_vol_mask, ij, 0).r < 0.5;
}

void main() {
    vec4 cls = texelFetch(u_classes, ivec2(int(in_label), 0), 0);
    float h = dot(in_pos - u_h_origin, u_h_normal);
    bool clipped = (u_clip_on == 1 && (any(lessThan(in_pos, u_clip_min)) || any(greaterThan(in_pos, u_clip_max))))
                   || (u_vol_on > 0 && outside_volume(in_pos))
                   || (u_set_on == 1 && (in_extra & 1u) == 0u);
    bool sliced = u_band_on == 1 && (h < u_band.x || h > u_band.y);
    vec4 lab = texelFetch(u_labels, ivec2(int(in_ann), 0), 0);
    bool labelled = in_ann != 0u && lab.a > 0.5;
    if (clipped || sliced || cls.a < 0.5 || (u_mode == 7 && u_labels_only == 1 && !labelled)) {
        gl_Position = CULLED;
        gl_PointSize = 1.0;
        v_color = vec3(0.0);
        return;
    }
    gl_Position = u_mvp * vec4(in_pos, 1.0);
    float size = u_point_size;
    if (u_size_mode == 1) {
        float w = u_ortho == 1 ? 1.0 : max(gl_Position.w, 1e-3);
        size = clamp(u_spacing * u_px_per_unit / w, u_point_size, u_max_size);
    }
    gl_PointSize = size;
    float inten = clamp((float(in_int) / 65535.0 - u_irange.x) / max(u_irange.y - u_irange.x, 1e-6), 0.0, 1.0);
    vec3 c;
    if (u_mode == 0) {
        c = in_rgb.rgb;
    } else if (u_mode == 1) {
        c = vec3(pow(inten, u_gamma));
    } else if (u_mode == 2) {
        float t = clamp((h - u_hrange.x) / max(u_hrange.y - u_hrange.x, 1e-6), 0.0, 1.0);
        c = texture(u_ramp, vec2((t * 255.0 + 0.5) / 256.0, 0.5)).rgb;
    } else if (u_mode == 3) {
        c = u_flat;
    } else if (u_mode == 4) {
        c = cls.rgb;
    } else if (u_mode == 7) {
        c = labelled ? lab.rgb : vec3(0.16 + 0.3 * dot(in_rgb.rgb, vec3(0.299, 0.587, 0.114)));
    } else {
        float phi = fract((h - u_h0) / u_period);
        c = texture(u_cyclic, vec2(phi, 0.5)).rgb;
        if (u_mode == 6) c *= 0.35 + 0.65 * inten;
    }
    if ((in_flags & 32u) != 0u) c = mix(c, u_unsure_tint.rgb, u_unsure_tint.a);
    else if ((in_flags & 64u) != 0u) c = mix(c, u_group_tint.rgb, u_group_tint.a);
    else if ((in_flags & 128u) != 0u) c = mix(c, u_sel_tint.rgb, u_sel_tint.a);
    v_color = c;
}
"""

POINT_FS = """
#version 430
uniform int u_round;
in vec3 v_color;
out vec4 f_color;
void main() {
    if (u_round == 1) {
        vec2 d = gl_PointCoord - 0.5;
        if (dot(d, d) > 0.25) discard;
    }
    f_color = vec4(v_color, 1.0);
}
"""

# full-screen triangle; eye-dome lighting (Boucheny 2009, as in Potree / CloudCompare) on the point depth
FULLSCREEN_VS = """
#version 430
out vec2 v_uv;
void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    v_uv = p;
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

EDL_FS = """
#version 430
uniform sampler2D u_color;
uniform sampler2D u_depth;
uniform vec2 u_texel;
uniform float u_strength;   // 0 = off
uniform float u_radius;     // pixels
uniform float u_near;
uniform float u_far;
uniform int u_ortho;
uniform float u_ortho_ref;  // ortho: compare depth steps as a perspective view at this distance would
uniform vec3 u_background;
in vec2 v_uv;
out vec4 f_color;

// log2 eye depth (perspective); in ortho, linear depth scaled so that d(z) ~ d(log2 z) at u_ortho_ref
float edl_depth(float d) {
    if (u_ortho == 1) return (u_near + d * (u_far - u_near)) / (u_ortho_ref * 0.693147);
    float z = d * 2.0 - 1.0;
    return log2(2.0 * u_near * u_far / (u_far + u_near - z * (u_far - u_near)));
}

void main() {
    float d = texture(u_depth, v_uv).r;
    if (d >= 1.0) {
        f_color = vec4(u_background, 1.0);
        return;
    }
    vec3 c = texture(u_color, v_uv).rgb;
    if (u_strength > 0.0) {
        float z = edl_depth(d);
        float sum = 0.0;
        for (int i = 0; i < 8; i++) {
            float a = 0.785398163 * float(i);
            vec2 uv = v_uv + vec2(cos(a), sin(a)) * u_radius * u_texel;
            float dn = texture(u_depth, uv).r;
            if (dn < 1.0) sum += max(0.0, z - edl_depth(dn));
        }
        c *= exp(-sum / 8.0 * 300.0 * u_strength);
    }
    f_color = vec4(c, 1.0);
}
"""

LINE_VS = """
#version 430
uniform mat4 u_mvp;
in vec3 in_pos;
void main() { gl_Position = u_mvp * vec4(in_pos, 1.0); }
"""

LINE_FS = """
#version 430
uniform vec4 u_color;
out vec4 f_color;
void main() { f_color = u_color; }
"""

# coloured marker points (e.g. where a grow stopped), drawn over the picture after EDL
MARK_VS = """
#version 430
uniform mat4 u_mvp;
uniform float u_size;
in vec3 in_pos;
in vec4 in_color;
out vec3 v_color;
void main() {
    gl_Position = u_mvp * vec4(in_pos, 1.0);
    gl_PointSize = u_size;
    v_color = in_color.rgb;
}
"""

MARK_FS = """
#version 430
in vec3 v_color;
out vec4 f_color;
void main() { f_color = vec4(v_color, 1.0); }
"""

# vector annotations (segments, curves): lines of any width, as screen-space quads made by a geometry shader
VLINE_VS = """
#version 430
uniform mat4 u_mvp;
in vec3 in_pos;
in vec4 in_color;
in float in_width;
out vec4 v_color;
out float v_width;
void main() {
    gl_Position = u_mvp * vec4(in_pos, 1.0);
    v_color = in_color;
    v_width = in_width;
}
"""

VLINE_GS = """
#version 430
layout(lines) in;
layout(triangle_strip, max_vertices = 4) out;
uniform vec2 u_viewport;  // pixels
in vec4 v_color[];
in float v_width[];
out vec4 g_color;
void main() {
    vec4 a = gl_in[0].gl_Position;
    vec4 b = gl_in[1].gl_Position;
    const float EPS = 1e-4;
    if (a.w < EPS && b.w < EPS) return;
    if (a.w < EPS) a = mix(a, b, (EPS - a.w) / (b.w - a.w));  // clip at the eye plane
    else if (b.w < EPS) b = mix(b, a, (EPS - b.w) / (a.w - b.w));
    vec2 half_vp = 0.5 * u_viewport;
    vec2 d = b.xy / b.w * half_vp - a.xy / a.w * half_vp;
    float len = length(d);
    d = len > 1e-6 ? d / len : vec2(1.0, 0.0);
    vec2 n = vec2(-d.y, d.x) * (0.5 * v_width[0]) / half_vp;
    g_color = v_color[0]; gl_Position = vec4(a.xy + n * a.w, a.zw); EmitVertex();
    g_color = v_color[0]; gl_Position = vec4(a.xy - n * a.w, a.zw); EmitVertex();
    g_color = v_color[1]; gl_Position = vec4(b.xy + n * b.w, b.zw); EmitVertex();
    g_color = v_color[1]; gl_Position = vec4(b.xy - n * b.w, b.zw); EmitVertex();
    EndPrimitive();
}
"""

VLINE_FS = """
#version 430
in vec4 g_color;
out vec4 f_color;
void main() { f_color = g_color; }
"""

# vector annotation nodes and handles: round points with a dark rim
VPOINT_VS = """
#version 430
uniform mat4 u_mvp;
in vec3 in_pos;
in vec4 in_color;
in float in_size;
out vec4 v_color;
void main() {
    gl_Position = u_mvp * vec4(in_pos, 1.0);
    gl_PointSize = in_size;
    v_color = in_color;
}
"""

VPOINT_FS = """
#version 430
in vec4 v_color;
out vec4 f_color;
void main() {
    float r = length(gl_PointCoord - 0.5);
    if (r > 0.5) discard;
    f_color = r > 0.34 ? vec4(0.0, 0.0, 0.0, 1.0) : v_color;
}
"""
