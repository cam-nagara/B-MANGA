"""ページプレビュー画像のGPUオーバーレイ描画 (POST_VIEW, テクスチャ)."""

from __future__ import annotations

import gpu
from gpu_extras.batch import batch_for_shader

try:
    import gpu.texture as gpu_texture
    _HAS_GPU_TEXTURE = True
except ImportError:
    gpu_texture = None
    _HAS_GPU_TEXTURE = False

import bpy

from ..utils import log
from ..utils.geom import mm_to_m

_logger = log.get_logger(__name__)

PREVIEW_Z_M = 0.006

_PREVIEW_SHADER = None
_PREVIEW_SHADER_FAILED = False


def draw_for_page(
    context,
    work,
    page,
    page_index: int,
    ox_mm: float,
    oy_mm: float,
    is_current_page: bool = False,
) -> None:
    if not _HAS_GPU_TEXTURE:
        return
    if is_current_page:
        return
    from ..utils import page_preview_object, page_range
    if not page_range.page_in_range(page):
        return
    page_id = str(getattr(page, "id", "") or "")
    if not page_id:
        return

    png_path = page_preview_object._preview_png_path(work, page_id)
    if png_path is None or not png_path.is_file():
        return

    paper = getattr(work, "paper", None)
    if paper is None:
        return
    cw = max(1.0, float(getattr(paper, "canvas_width_mm", 1.0) or 1.0))
    ch = max(1.0, float(getattr(paper, "canvas_height_mm", 1.0) or 1.0))
    from ..utils import page_grid
    page_w = page_grid.page_content_width_mm(work, page_index, cw)

    opacity = _preview_opacity(context)
    if opacity <= 0.0:
        return

    _draw_textured_quad(
        str(png_path),
        ox_mm, oy_mm,
        page_w, ch,
        opacity,
    )


def _preview_opacity(context) -> float:
    scene = getattr(context, "scene", None)
    if scene is None:
        return 1.0
    settings = getattr(scene, "bmanga_coma_camera_settings", None)
    if settings is None:
        return 1.0
    pct = float(getattr(settings, "name_bg_images_opacity", 50.0) or 50.0)
    from ..utils import percentage
    return max(0.0, min(1.0, percentage.percent_to_factor(pct, 50.0)))


def _get_preview_shader():
    global _PREVIEW_SHADER, _PREVIEW_SHADER_FAILED
    if _PREVIEW_SHADER_FAILED:
        return None
    if _PREVIEW_SHADER is not None:
        return _PREVIEW_SHADER
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
            color.a *= opacity;
            fragColor = color;
        }
    """
    try:
        interface = gpu.types.GPUStageInterfaceInfo("bmanga_page_preview_iface")
        interface.smooth("VEC2", "uvInterp")
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant("MAT4", "ModelViewProjectionMatrix")
        info.sampler(0, "FLOAT_2D", "image")
        info.push_constant("FLOAT", "opacity")
        info.vertex_in(0, "VEC3", "pos")
        info.vertex_in(1, "VEC2", "texCoord")
        info.vertex_out(interface)
        info.fragment_out(0, "VEC4", "fragColor")
        info.vertex_source(vertex_src)
        info.fragment_source(fragment_src)
        _PREVIEW_SHADER = gpu.shader.create_from_info(info)
        del info
    except Exception:  # noqa: BLE001
        _PREVIEW_SHADER_FAILED = True
        _logger.exception("page preview shader compile failed")
        return None
    return _PREVIEW_SHADER


def _draw_textured_quad(
    png_path: str,
    x_mm: float,
    y_mm: float,
    w_mm: float,
    h_mm: float,
    opacity: float,
) -> None:
    shader = _get_preview_shader()
    fallback_shader = gpu.shader.from_builtin("IMAGE") if shader is None else None
    active_shader = shader or fallback_shader
    if active_shader is None:
        return
    try:
        img = bpy.data.images.load(png_path, check_existing=True)
    except Exception:  # noqa: BLE001
        return
    try:
        tex = gpu_texture.from_image(img)
    except Exception:  # noqa: BLE001
        return
    z = PREVIEW_Z_M
    verts = [
        (mm_to_m(x_mm), mm_to_m(y_mm), z),
        (mm_to_m(x_mm + w_mm), mm_to_m(y_mm), z),
        (mm_to_m(x_mm + w_mm), mm_to_m(y_mm + h_mm), z),
        (mm_to_m(x_mm), mm_to_m(y_mm + h_mm), z),
    ]
    uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    indices = [(0, 1, 2), (0, 2, 3)]
    batch = batch_for_shader(
        active_shader, "TRIS",
        {"pos": verts, "texCoord": uvs},
        indices=indices,
    )
    active_shader.bind()
    mvp = gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
    active_shader.uniform_float("ModelViewProjectionMatrix", mvp)
    active_shader.uniform_sampler("image", tex)
    if shader is not None:
        active_shader.uniform_float("opacity", opacity)
    prev_blend = gpu.state.blend_get()
    prev_depth = gpu.state.depth_test_get()
    try:
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")
        batch.draw(active_shader)
    finally:
        gpu.state.blend_set(prev_blend)
        gpu.state.depth_test_set(prev_depth)
