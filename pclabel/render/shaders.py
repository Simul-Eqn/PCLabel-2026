"""Shader sources (no Qt). All colour modes are computed here from per-point attributes and uniforms, so changing
a mode, range or period never touches the point data (SPEC §2).

Height (for the height ramp, the wrap and the slice) is measured along the active height frame:
``h = dot(p - u_h_origin, u_h_normal)`` with ``p`` and ``u_h_origin`` in local (store) coordinates. The scan frame
is normal (0, 0, 1) through world z = 0; a plane frame comes from a plane fit (analysis/frames.py), and
``HeightFrame.heights`` is the numpy mirror.
"""

MODE_RGB, MODE_INTENSITY, MODE_HEIGHT, MODE_FLAT, MODE_CLASS, MODE_WRAP, MODE_WRAP_SHADED = range(7)
MODE_NAMES = {
    MODE_RGB: "rgb",
    MODE_INTENSITY: "intensity",
    MODE_HEIGHT: "height",
    MODE_FLAT: "flat",
    MODE_CLASS: "class",
    MODE_WRAP: "wrap",
    MODE_WRAP_SHADED: "wrap x intensity",
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
uniform int u_clip_on;
uniform vec3 u_clip_min;
uniform vec3 u_clip_max;
uniform int u_size_mode;       // 0 fixed, 1 adaptive
uniform float u_point_size;    // fixed size, or the minimum when adaptive
uniform float u_max_size;
uniform float u_spacing;       // world spacing of the drawn subset of this tile (adaptive)
uniform float u_px_per_unit;   // perspective: pixels per world unit at eye depth 1; ortho: per world unit
uniform int u_ortho;
in vec3 in_pos;
in vec4 in_rgb;
in uint in_int;
in uint in_label;
out vec3 v_color;
const vec4 CULLED = vec4(2.0, 2.0, 2.0, 1.0);

void main() {
    vec4 cls = texelFetch(u_classes, ivec2(int(in_label), 0), 0);
    float h = dot(in_pos - u_h_origin, u_h_normal);
    bool clipped = u_clip_on == 1 && (any(lessThan(in_pos, u_clip_min)) || any(greaterThan(in_pos, u_clip_max)));
    bool sliced = u_band_on == 1 && (h < u_band.x || h > u_band.y);
    if (clipped || sliced || cls.a < 0.5) {
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
    } else {
        float phi = fract((h - u_h0) / u_period);
        c = texture(u_cyclic, vec2(phi, 0.5)).rgb;
        if (u_mode == 6) c *= 0.35 + 0.65 * inten;
    }
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

# selected points, drawn over the picture (after EDL) with blending
SEL_VS = """
#version 430
uniform mat4 u_mvp;
uniform float u_size;
in vec3 in_pos;
void main() {
    gl_Position = u_mvp * vec4(in_pos, 1.0);
    gl_PointSize = u_size;
}
"""

SEL_FS = """
#version 430
uniform vec4 u_color;
out vec4 f_color;
void main() { f_color = u_color; }
"""
