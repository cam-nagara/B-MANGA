"""コマプレビュー画像をビューポート上のコマ形状へ描画するヘルパ."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ..utils import image_transparency
from ..utils import coma_preview
from ..utils import log
from ..utils.geom import mm_to_m


_logger = log.get_logger(__name__)
_COMA_PREVIEW_SHADER = None
_COMA_PREVIEW_SHADER_FAILED = False


def draw_coma_preview(work, page, entry, ox_mm: float = 0.0, oy_mm: float = 0.0) -> bool:
    """cNN_preview/thumb をコマ形状内へ描画する."""
    if work is None or page is None or not getattr(work, "work_dir", ""):
        return False
    poly = _coma_polygon_mm(entry)
    if len(poly) < 3:
        return False
    source = coma_preview.coma_preview_source_path(Path(work.work_dir), page.id, entry)
    if source is None:
        return False
    source = _display_source_for_panel(source, entry)
    img = _ensure_bpy_image_current(source)
    if img is None:
        return False
    bbox = _bbox(poly)
    if bbox is None:
        return False
    min_x, min_y, max_x, max_y = bbox
    width = max_x - min_x
    height = max_y - min_y
    if width <= 0.0 or height <= 0.0:
        return False

    # z は coma_plane (z=0.1m) と raster (z=0.1m) より上、テキスト等より下に
    # して、コマプレビューが coma_plane の白塗りに上書きされないようにする。
    # GPU shader (IMAGE) は depth_test を有効化しないため Z 値は実質
    # OPAQUE Mesh との描画順序を制御するためだけに使う。
    _COMA_PREVIEW_Z = 0.15
    verts = [
        (mm_to_m(x + ox_mm), mm_to_m(y + oy_mm), _COMA_PREVIEW_Z)
        for x, y in poly
    ]
    uvs = [
        ((x - min_x) / width, (y - min_y) / height)
        for x, y in poly
    ]
    indices = [(0, i, i + 1) for i in range(1, len(poly) - 1)]
    if not indices:
        return False

    try:
        import gpu.texture as gpu_texture  # type: ignore

        tex = gpu_texture.from_image(img)
    except Exception:  # noqa: BLE001
        return False

    shader = _get_coma_preview_shader() or gpu.shader.from_builtin("IMAGE")
    batch = batch_for_shader(
        shader,
        "TRIS",
        {"pos": verts, "texCoord": uvs},
        indices=indices,
    )
    shader.bind()
    shader.uniform_sampler("image", tex)
    # depth テストを一時的に無効化して thumb が必ず描画されるようにする。
    # SOLID viewport の場合、 coma_plane (z=0.1, OPAQUE Mesh) が POST_VIEW
    # handler より先に depth buffer に書き込むため、 thumb の depth_test が
    # 残っていると coma_plane に覆われて何も見えなくなる。
    prev_depth_test = gpu.state.depth_test_get()
    prev_blend = gpu.state.blend_get()
    try:
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("ALPHA")
        batch.draw(shader)
    finally:
        gpu.state.depth_test_set(prev_depth_test)
        gpu.state.blend_set(prev_blend)
    return True


def _get_coma_preview_shader():
    """sRGB 画像をビューポート上の表示色として描くためのシェーダ."""
    global _COMA_PREVIEW_SHADER, _COMA_PREVIEW_SHADER_FAILED
    if _COMA_PREVIEW_SHADER_FAILED:
        return None
    if _COMA_PREVIEW_SHADER is not None:
        return _COMA_PREVIEW_SHADER
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
            fragColor = color;
        }
    """
    try:
        interface = gpu.types.GPUStageInterfaceInfo("bname_coma_preview_iface")
        interface.smooth("VEC2", "uvInterp")
        shader_info = gpu.types.GPUShaderCreateInfo()
        shader_info.push_constant("MAT4", "ModelViewProjectionMatrix")
        shader_info.sampler(0, "FLOAT_2D", "image")
        shader_info.vertex_in(0, "VEC3", "pos")
        shader_info.vertex_in(1, "VEC2", "texCoord")
        shader_info.vertex_out(interface)
        shader_info.fragment_out(0, "VEC4", "fragColor")
        shader_info.vertex_source(vertex_src)
        shader_info.fragment_source(fragment_src)
        _COMA_PREVIEW_SHADER = gpu.shader.create_from_info(shader_info)
        del shader_info
    except Exception:  # noqa: BLE001
        _COMA_PREVIEW_SHADER_FAILED = True
        _logger.exception("coma preview shader compile failed")
        return None
    return _COMA_PREVIEW_SHADER


def _display_source_for_panel(source: Path, entry) -> Path:
    if not image_transparency.coma_background_is_transparent(entry):
        return source
    from ..io import export_pipeline

    Image = export_pipeline.Image
    if Image is None:
        return source
    try:
        source_mtime = source.stat().st_mtime
    except OSError:
        return source
    cache_path = _transparent_cache_path(source)
    try:
        cache_mtime = cache_path.stat().st_mtime
    except OSError:
        cache_mtime = -1.0
    if cache_path.is_file() and cache_mtime >= source_mtime:
        return cache_path
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(str(source)) as opened:
            image = image_transparency.make_background_transparent(opened)
            image.save(str(cache_path))
        return cache_path
    except Exception:  # noqa: BLE001
        return source


def _transparent_cache_path(source: Path) -> Path:
    resolved = str(Path(source).resolve())
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "bname_coma_preview_alpha" / f"{digest}.png"


def _coma_polygon_mm(entry) -> list[tuple[float, float]]:
    shape = getattr(entry, "shape_type", "")
    if shape == "rect":
        x = float(getattr(entry, "rect_x_mm", 0.0))
        y = float(getattr(entry, "rect_y_mm", 0.0))
        w = float(getattr(entry, "rect_width_mm", 0.0))
        h = float(getattr(entry, "rect_height_mm", 0.0))
        if w <= 0.0 or h <= 0.0:
            return []
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    if shape == "polygon":
        return [(float(v.x_mm), float(v.y_mm)) for v in getattr(entry, "vertices", [])]
    return []


def _bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _ensure_bpy_image_current(path: Path):
    abspath = str(Path(path).resolve())
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return None

    for img in bpy.data.images:
        try:
            if str(Path(bpy.path.abspath(img.filepath)).resolve()) != abspath:
                continue
            if float(img.get("_bname_mtime", -1.0)) != mtime:
                img.reload()
                img["_bname_mtime"] = mtime
            _set_image_display_colorspace(img)
            return img
        except Exception:  # noqa: BLE001
            continue

    try:
        img = bpy.data.images.load(abspath, check_existing=True)
        img["_bname_mtime"] = mtime
        _set_image_display_colorspace(img)
        return img
    except Exception:  # noqa: BLE001
        return None


def _set_image_display_colorspace(img) -> None:
    try:
        img.colorspace_settings.name = "sRGB"
    except Exception:  # noqa: BLE001
        pass
