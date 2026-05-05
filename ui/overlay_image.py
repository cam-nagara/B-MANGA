"""画像レイヤーのビューポート描画."""

from __future__ import annotations

import math

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

try:
    import gpu.texture as gpu_texture  # type: ignore
    _HAS_GPU_TEXTURE = True
except ImportError:  # pragma: no cover - 古い Blender
    gpu_texture = None  # type: ignore
    _HAS_GPU_TEXTURE = False

from ..utils import log
from ..utils.geom import mm_to_m

_logger = log.get_logger(__name__)

_IMAGE_LAYER_SHADER = None
_IMAGE_LAYER_SHADER_FAILED = False


def _apply_blend_mode(_mode: str) -> None:
    """ブレンドモード指定を GPU state に反映する (Phase 1 暫定)."""
    # GPU state API の blend_set には MULTIPLY 相当が無いので、現段階では
    # すべて ALPHA ブレンドにフォールバックする。
    gpu.state.blend_set("ALPHA")


def _get_image_layer_shader():
    global _IMAGE_LAYER_SHADER, _IMAGE_LAYER_SHADER_FAILED
    if _IMAGE_LAYER_SHADER_FAILED:
        return None
    if _IMAGE_LAYER_SHADER is not None:
        return _IMAGE_LAYER_SHADER
    vertex_src = """
        void main()
        {
            gl_Position = ModelViewProjectionMatrix * vec4(pos, 1.0);
            uvInterp = texCoord;
        }
    """
    fragment_src = """
        void main()
        {
            vec4 color = texture(image, uvInterp);
            color.rgb = pow(max(color.rgb, vec3(0.0)), vec3(1.0 / 2.2));
            color.rgb = (color.rgb - vec3(0.5)) * (1.0 + contrast) + vec3(0.5 + brightness);
            color.rgb = clamp(color.rgb, 0.0, 1.0);
            if (binarize_enabled > 0.5) {
                float lum = dot(color.rgb, vec3(0.299, 0.587, 0.114));
                float v = lum >= binarize_threshold ? 1.0 : 0.0;
                color.rgb = vec3(v);
            }
            color.rgb *= tint.rgb;
            color.a *= tint.a * opacity;
            fragColor = color;
        }
    """
    try:
        interface = gpu.types.GPUStageInterfaceInfo("bname_image_layer_iface")
        interface.smooth("VEC2", "uvInterp")
        shader_info = gpu.types.GPUShaderCreateInfo()
        shader_info.push_constant("MAT4", "ModelViewProjectionMatrix")
        shader_info.sampler(0, "FLOAT_2D", "image")
        shader_info.push_constant("VEC4", "tint")
        shader_info.push_constant("FLOAT", "opacity")
        shader_info.push_constant("FLOAT", "brightness")
        shader_info.push_constant("FLOAT", "contrast")
        shader_info.push_constant("FLOAT", "binarize_enabled")
        shader_info.push_constant("FLOAT", "binarize_threshold")
        shader_info.vertex_in(0, "VEC3", "pos")
        shader_info.vertex_in(1, "VEC2", "texCoord")
        shader_info.vertex_out(interface)
        shader_info.fragment_out(0, "VEC4", "fragColor")
        shader_info.vertex_source(vertex_src)
        shader_info.fragment_source(fragment_src)
        _IMAGE_LAYER_SHADER = gpu.shader.create_from_info(shader_info)
        del shader_info
    except Exception:  # noqa: BLE001
        _IMAGE_LAYER_SHADER_FAILED = True
        _logger.exception("image layer shader compile failed")
        return None
    return _IMAGE_LAYER_SHADER


def image_quad_points_mm(entry) -> list[tuple[float, float]]:
    """画像レイヤーの回転済み四隅を mm 座標で返す."""
    x0 = float(getattr(entry, "x_mm", 0.0))
    y0 = float(getattr(entry, "y_mm", 0.0))
    x1 = x0 + float(getattr(entry, "width_mm", 0.0))
    y1 = y0 + float(getattr(entry, "height_mm", 0.0))
    points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    rotation = float(getattr(entry, "rotation_deg", 0.0))
    if abs(rotation) <= 1e-6:
        return points

    cx = (x0 + x1) * 0.5
    cy = (y0 + y1) * 0.5
    cos_r = math.cos(math.radians(rotation))
    sin_r = math.sin(math.radians(rotation))
    out = []
    for x, y in points:
        dx = x - cx
        dy = y - cy
        out.append((cx + dx * cos_r - dy * sin_r, cy + dx * sin_r + dy * cos_r))
    return out


def draw_image_layers(scene) -> None:
    """画像レイヤーを gpu.texture 経由でオーバーレイ描画."""
    coll = getattr(scene, "bname_image_layers", None)
    if coll is None or not len(coll):
        return
    if not _HAS_GPU_TEXTURE:
        return
    custom_shader = _get_image_layer_shader()
    fallback_shader = gpu.shader.from_builtin("IMAGE") if custom_shader is None else None
    for entry in coll:
        if not entry.visible or not entry.filepath:
            continue
        img = _ensure_bpy_image(entry.filepath)
        if img is None:
            continue
        try:
            tex = gpu_texture.from_image(img)
        except Exception:  # noqa: BLE001
            continue
        quad = image_quad_points_mm(entry)
        u0, u1 = (1.0, 0.0) if entry.flip_x else (0.0, 1.0)
        v0, v1 = (1.0, 0.0) if entry.flip_y else (0.0, 1.0)
        verts = [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in quad]
        uvs = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
        indices = [(0, 1, 2), (0, 2, 3)]
        shader = custom_shader or fallback_shader
        if shader is None:
            continue
        batch = batch_for_shader(
            shader,
            "TRIS",
            {"pos": verts, "texCoord": uvs},
            indices=indices,
        )
        shader.bind()
        shader.uniform_sampler("image", tex)
        if custom_shader is not None:
            _bind_image_adjustment_uniforms(shader, entry)
        prev_blend = gpu.state.blend_get()
        try:
            _apply_blend_mode(getattr(entry, "blend_mode", "normal"))
            gpu.state.blend_set("ALPHA")
            batch.draw(shader)
        finally:
            gpu.state.blend_set(prev_blend)


def _bind_image_adjustment_uniforms(shader, entry) -> None:
    tint = getattr(entry, "tint_color", (1.0, 1.0, 1.0, 1.0))
    shader.uniform_float(
        "tint",
        (
            float(tint[0]),
            float(tint[1]),
            float(tint[2]),
            float(tint[3]) if len(tint) > 3 else 1.0,
        ),
    )
    shader.uniform_float("opacity", max(0.0, min(1.0, float(getattr(entry, "opacity", 1.0)))))
    shader.uniform_float("brightness", max(-1.0, min(1.0, float(getattr(entry, "brightness", 0.0)))))
    shader.uniform_float("contrast", max(-1.0, min(1.0, float(getattr(entry, "contrast", 0.0)))))
    shader.uniform_float("binarize_enabled", 1.0 if getattr(entry, "binarize_enabled", False) else 0.0)
    shader.uniform_float(
        "binarize_threshold",
        max(0.0, min(1.0, float(getattr(entry, "binarize_threshold", 0.5)))),
    )


def _ensure_bpy_image(filepath: str):
    """bpy.data.images に対象画像を読み込み (check_existing でキャッシュ)."""
    if not filepath:
        return None
    try:
        img = bpy.data.images.load(bpy.path.abspath(filepath), check_existing=True)
    except Exception:  # noqa: BLE001
        return None
    try:
        img.colorspace_settings.name = "sRGB"
    except Exception:  # noqa: BLE001
        pass
    return img
