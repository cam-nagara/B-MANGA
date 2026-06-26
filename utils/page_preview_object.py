"""Lightweight page preview images for page edit files."""

from __future__ import annotations

import math
from pathlib import Path

import bpy

from . import log, object_naming as on, page_grid, page_range, paths, spread_merge_geometry
from .geom import mm_to_m

_logger = log.get_logger(__name__)

PREVIEW_KIND = "page_preview"
PREVIEW_COLLECTION_NAME = "ページ一覧プレビュー"
PREVIEW_IMAGE_PREFIX = "BManga_PagePreview_"
PREVIEW_MESH_PREFIX = "page_preview_mesh_"
PREVIEW_OBJECT_PREFIX = "page_preview_"
PREVIEW_MATERIAL_PREFIX = "BManga_PagePreview_"
PREVIEW_OPACITY_NODE = "B-MANGA Preview Opacity"
PREVIEW_OPACITY_MATH_NODE = "B-MANGA Preview Opacity Multiply"
PREVIEW_PAGE_ID_PROP = "bmanga_page_preview_page_id"
PREVIEW_CAMERA_FOLLOW_PROP = "bmanga_page_preview_camera_follow"
PREVIEW_CAMERA_ANCHOR_PROP = "bmanga_page_preview_camera_anchor"
# プレビュー画像 (長辺) の上限。GPU メモリ保護のための安全弁。
PREVIEW_MAX_LONG_PX = 4096
# 用紙 DPI が取れない場合のフォールバック解像度基準。
PREVIEW_FALLBACK_LONG_PX = 2560
DEFAULT_PREVIEW_PAGE_RADIUS = 3
PREVIEW_RANGE_ALL = "ALL"
PREVIEW_RANGE_NEAR = "NEAR"
DEFAULT_PREVIEW_RANGE_MODE = PREVIEW_RANGE_ALL
DEFAULT_PREVIEW_RESOLUTION_PERCENTAGE = 25.0
PREVIEW_RENDER_SUPERSAMPLE = 2
# 目標サイズがこれ以上なら、スーパーサンプリング無しで直接描画する。
PREVIEW_SUPERSAMPLE_MAX_TARGET_PX = 1024
PREVIEW_Z_M = 0.006
PREVIEW_FILENAME = "page_preview.png"
PREVIEW_RENDER_VERSION = "10"
PREVIEW_RENDER_VERSION_KEY = "BMangaPreviewVersion"
PREVIEW_RENDER_VARIANT_KEY = "BMangaPreviewVariant"
PREVIEW_RENDER_SIGNATURE_KEY = "BMangaPreviewSignature"
PREVIEW_RENDER_VARIANT_WORK = "work"
PREVIEW_RENDER_VARIANT_DETAIL = "detail"
_DEFERRED_SYNC_FORCE = False


def preview_enabled(scene=None) -> bool:
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None:
        return False
    try:
        from . import page_file_scene

        if page_file_scene.is_work_list_scene(scene):
            return True
    except Exception:  # noqa: BLE001
        pass
    return bool(getattr(scene, "bmanga_page_preview_enabled", True))


def preview_page_radius(scene=None) -> int:
    scene = scene or getattr(bpy.context, "scene", None)
    value = getattr(scene, "bmanga_page_preview_page_radius", DEFAULT_PREVIEW_PAGE_RADIUS)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return DEFAULT_PREVIEW_PAGE_RADIUS


def preview_range_mode(scene=None) -> str:
    scene = scene or getattr(bpy.context, "scene", None)
    value = str(
        getattr(scene, "bmanga_page_preview_range_mode", DEFAULT_PREVIEW_RANGE_MODE)
        or DEFAULT_PREVIEW_RANGE_MODE
    ).upper()
    return value if value in {PREVIEW_RANGE_ALL, PREVIEW_RANGE_NEAR} else DEFAULT_PREVIEW_RANGE_MODE


def preview_resolution_percentage(scene=None) -> float:
    scene = scene or getattr(bpy.context, "scene", None)
    value = getattr(
        scene,
        "bmanga_page_preview_resolution_percentage",
        DEFAULT_PREVIEW_RESOLUTION_PERCENTAGE,
    )
    try:
        return max(5.0, min(200.0, float(value)))
    except (TypeError, ValueError):
        return DEFAULT_PREVIEW_RESOLUTION_PERCENTAGE


def _is_page_edit_scene(scene) -> tuple[bool, str]:
    try:
        from . import page_file_scene

        role, page_id, _coma_id = page_file_scene.current_role(bpy.context)
        if role == page_file_scene.ROLE_PAGE and paths.is_valid_page_id(page_id):
            return True, page_id
        page_id = page_file_scene.current_page_id(scene)
        return bool(page_id and page_file_scene.is_page_edit_scene(scene)), page_id
    except Exception:  # noqa: BLE001
        return False, ""


def _preview_scene_role(scene) -> tuple[str, str]:
    try:
        from . import page_file_scene

        role, page_id, _coma_id = page_file_scene.current_role(bpy.context)
        if role == page_file_scene.ROLE_PAGE and paths.is_valid_page_id(page_id):
            return "page", page_id
        if role == page_file_scene.ROLE_COMA and paths.is_valid_page_id(page_id):
            return "coma", page_id
        if role == page_file_scene.ROLE_WORK:
            return "work", ""
        page_id = page_file_scene.current_page_id(scene)
        if page_id and page_file_scene.is_page_edit_scene(scene):
            return "page", page_id
        if page_file_scene.is_work_list_scene(scene):
            return "work", ""
    except Exception:  # noqa: BLE001
        pass
    return "", ""


def _preview_collection(scene: bpy.types.Scene) -> bpy.types.Collection:
    coll = bpy.data.collections.get(PREVIEW_COLLECTION_NAME)
    if coll is None:
        coll = bpy.data.collections.new(PREVIEW_COLLECTION_NAME)
    if not any(child is coll for child in scene.collection.children):
        try:
            scene.collection.children.link(coll)
        except Exception:  # noqa: BLE001
            pass
    coll.hide_render = True
    coll[on.PROP_KIND] = PREVIEW_KIND
    coll[on.PROP_MANAGED] = False
    coll[on.PROP_NO_NORMALIZE] = True
    return coll


def _iter_preview_objects():
    for obj in list(bpy.data.objects):
        if str(obj.get(on.PROP_KIND, "") or "") == PREVIEW_KIND:
            yield obj


def _clear_preview_camera_follow(obj: bpy.types.Object, *, clear_anchor: bool = True) -> None:
    if obj is None or not bool(obj.get(PREVIEW_CAMERA_FOLLOW_PROP, False)):
        return
    matrix = obj.matrix_world.copy()
    try:
        obj.parent = None
        obj.matrix_parent_inverse.identity()
        obj.matrix_world = matrix
        obj[PREVIEW_CAMERA_FOLLOW_PROP] = False
        if clear_anchor and PREVIEW_CAMERA_ANCHOR_PROP in obj:
            del obj[PREVIEW_CAMERA_ANCHOR_PROP]
    except Exception:  # noqa: BLE001
        pass


def _preview_camera_delta(obj: bpy.types.Object, scene: bpy.types.Scene) -> tuple[float, float, float]:
    camera = getattr(scene, "camera", None) if scene is not None else None
    if obj is None or camera is None or getattr(camera, "type", "") != "CAMERA":
        return 0.0, 0.0, 0.0
    location = camera.matrix_world.to_translation()
    anchor = obj.get(PREVIEW_CAMERA_ANCHOR_PROP)
    try:
        if anchor is None or len(anchor) < 3:
            return 0.0, 0.0, 0.0
        return (
            float(location.x) - float(anchor[0]),
            float(location.y) - float(anchor[1]),
            float(location.z) - float(anchor[2]),
        )
    except Exception:  # noqa: BLE001
        return 0.0, 0.0, 0.0


def _apply_preview_camera_follow(obj: bpy.types.Object, scene: bpy.types.Scene) -> None:
    camera = getattr(scene, "camera", None) if scene is not None else None
    if obj is None or camera is None or getattr(camera, "type", "") != "CAMERA":
        _clear_preview_camera_follow(obj)
        return
    matrix = obj.matrix_world.copy()
    try:
        if PREVIEW_CAMERA_ANCHOR_PROP not in obj:
            location = camera.matrix_world.to_translation()
            obj[PREVIEW_CAMERA_ANCHOR_PROP] = (
                float(location.x),
                float(location.y),
                float(location.z),
            )
        obj.parent = camera
        obj.matrix_parent_inverse = camera.matrix_world.inverted()
        obj.matrix_world = matrix
        obj[PREVIEW_CAMERA_FOLLOW_PROP] = True
    except Exception:  # noqa: BLE001
        _clear_preview_camera_follow(obj)


def hide_page_previews(scene=None) -> None:
    for obj in _iter_preview_objects():
        obj.hide_viewport = True
        obj.hide_render = True


def show_page_previews() -> None:
    """全プレビューオブジェクトをビューポートに表示する."""
    for obj in _iter_preview_objects():
        obj.hide_viewport = False


def exclude_preview_collection_from_view_layer(scene=None) -> None:
    """ページファイルでプレビューコレクションをビューレイヤーから除外する."""
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None:
        return
    coll = bpy.data.collections.get(PREVIEW_COLLECTION_NAME)
    if coll is None:
        return
    vl = getattr(bpy.context, "view_layer", None)
    if vl is None:
        return
    try:
        lc = vl.layer_collection.children.get(PREVIEW_COLLECTION_NAME)
        if lc is not None:
            lc.exclude = True
    except Exception:  # noqa: BLE001
        pass


def remove_page_previews() -> int:
    removed = 0
    for obj in list(_iter_preview_objects()):
        mats = [s.material for s in getattr(obj, "material_slots", []) if s.material]
        data = getattr(obj, "data", None)
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
        except Exception:  # noqa: BLE001
            continue
        if data is not None and getattr(data, "users", 0) == 0:
            try:
                bpy.data.meshes.remove(data)
            except Exception:  # noqa: BLE001
                pass
        for mat in mats:
            if getattr(mat, "users", 0) == 0:
                try:
                    bpy.data.materials.remove(mat)
                except Exception:  # noqa: BLE001
                    pass
    coll = bpy.data.collections.get(PREVIEW_COLLECTION_NAME)
    if coll is not None:
        try:
            bpy.data.collections.remove(coll)
        except Exception:  # noqa: BLE001
            pass
    return removed


def _preview_opacity_factor(scene=None) -> float:
    scene = scene or getattr(bpy.context, "scene", None)
    role, _page_id = _preview_scene_role(scene)
    if role != "coma":
        return 1.0
    settings = getattr(scene, "bmanga_coma_camera_settings", None) if scene is not None else None
    try:
        value = float(getattr(settings, "name_bg_images_opacity", 100.0) or 100.0)
    except (TypeError, ValueError):
        value = 100.0
    return max(0.0, min(1.0, value / 100.0))


def _preview_scale_factor(scene=None) -> float:
    scene = scene or getattr(bpy.context, "scene", None)
    settings = getattr(scene, "bmanga_coma_camera_settings", None) if scene is not None else None
    try:
        value = float(getattr(settings, "bg_images_scale", 1.0) or 1.0)
    except (TypeError, ValueError):
        value = 1.0
    return max(0.1, min(10.0, value))


def _linear_to_srgb(value: float) -> float:
    v = max(0.0, min(1.0, float(value)))
    if v <= 0.0031308:
        return v * 12.92
    return 1.055 * (v ** (1.0 / 2.4)) - 0.055


def _rgba255(rgba, fallback=(255, 255, 255, 255)) -> tuple[int, int, int, int]:
    try:
        r, g, b, a = rgba[:4]
        return (
            int(round(_linear_to_srgb(r) * 255.0)),
            int(round(_linear_to_srgb(g) * 255.0)),
            int(round(_linear_to_srgb(b) * 255.0)),
            int(round(max(0.0, min(1.0, float(a))) * 255.0)),
        )
    except Exception:  # noqa: BLE001
        return fallback


def _image_size(work, scene=None, page=None) -> tuple[int, int]:
    cw = max(1.0, float(getattr(work.paper, "canvas_width_mm", 1.0) or 1.0))
    ch = max(1.0, float(getattr(work.paper, "canvas_height_mm", 1.0) or 1.0))
    if page is not None:
        fw = max(1.0, float(getattr(work.paper, "finish_width_mm", 1.0) or 1.0))
        cw = page_grid.spread_content_width_mm(page, cw, fw)
    # 「画像解像度%」はページ実解像度 (用紙サイズ × DPI) に対する割合。
    # 長辺は PREVIEW_MAX_LONG_PX を上限にしてメモリを保護する。
    try:
        dpi = float(getattr(work.paper, "dpi", 0.0) or 0.0)
    except (TypeError, ValueError):
        dpi = 0.0
    if dpi > 0.0:
        full_long_px = max(cw, ch) / 25.4 * dpi
    else:
        full_long_px = float(PREVIEW_FALLBACK_LONG_PX)
    max_px = int(round(full_long_px * preview_resolution_percentage(scene) / 100.0))
    max_px = max(64, min(PREVIEW_MAX_LONG_PX, max_px))
    if cw >= ch:
        width = max_px
        height = max(1, int(round(max_px * ch / cw)))
    else:
        height = max_px
        width = max(1, int(round(max_px * cw / ch)))
    return width, height


def _resize_preview_image(image, width: int, height: int):
    if tuple(image.size) == (int(width), int(height)):
        return image
    from PIL import Image

    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
    return image.resize((int(width), int(height)), resampling)


# PNG 判定のキャッシュ: path -> (mtime, size, 角ピクセル RGBA, 生成仕様版, 用途, 表示状態)。
# 毎回 PIL で開き直すとページ数分の固定コストになるため。
_PNG_USABLE_CACHE: dict[str, tuple[float, tuple[int, int], tuple[int, int, int, int], str, str, str]] = {}


def _preview_render_variant(scene=None) -> str:
    try:
        from . import page_preview_decor

        if page_preview_decor.preview_detail_variant(scene or bpy.context.scene):
            return PREVIEW_RENDER_VARIANT_DETAIL
    except Exception:  # noqa: BLE001
        pass
    return PREVIEW_RENDER_VARIANT_WORK


def _preview_render_signature(work, scene=None) -> str:
    variant = _preview_render_variant(scene)
    if variant != PREVIEW_RENDER_VARIANT_DETAIL:
        return PREVIEW_RENDER_VARIANT_WORK
    try:
        from . import page_preview_decor

        guides = int(page_preview_decor.page_guides_visible(work, scene))
    except Exception:  # noqa: BLE001
        guides = 1
    return f"{variant}:guides={guides}:labels=overlay"


def _preview_png_usable(
    path: Path,
    expected_size: tuple[int, int],
    *,
    current: bool,
    variant: str,
    signature: str,
) -> bool:
    """既存プレビュー PNG を使い回せるか。

    見開きの期待サイズは横長 (`_image_size` が page 対応) なので、
    見開き化の途中で作られた単ページ縦横比の古いプレビューは
    サイズ不一致で自動的に再生成へ回る。
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    key = str(path)
    cached = _PNG_USABLE_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        size, (r, g, b, a), version = cached[1], cached[2], cached[3]
        png_variant = cached[4] if len(cached) > 4 else ""
        png_signature = cached[5] if len(cached) > 5 else ""
    else:
        try:
            from PIL import Image

            with Image.open(path) as image:
                size = tuple(image.size)
                version = str(image.info.get(PREVIEW_RENDER_VERSION_KEY, "") or "")
                png_variant = str(image.info.get(PREVIEW_RENDER_VARIANT_KEY, "") or "")
                png_signature = str(image.info.get(PREVIEW_RENDER_SIGNATURE_KEY, "") or "")
                rgba = image.convert("RGBA")
                r, g, b, a = rgba.getpixel((min(1, rgba.width - 1), min(1, rgba.height - 1)))
        except Exception:  # noqa: BLE001
            return False
        if len(_PNG_USABLE_CACHE) > 512:
            _PNG_USABLE_CACHE.clear()
        _PNG_USABLE_CACHE[key] = (mtime, size, (r, g, b, a), version, png_variant, png_signature)
    if version != PREVIEW_RENDER_VERSION:
        return False
    if png_variant != variant:
        return False
    if png_signature != signature:
        return False
    if tuple(size) != tuple(expected_size):
        return False
    if a < 200 or r > 120:
        return False
    if current:
        return g <= 170 and b >= 220
    return g >= 170 and b >= 180


def _draw_preview_frame(draw, width: int, height: int, *, current: bool) -> None:
    outline = (72, 190, 222, 255)
    if current:
        outline = (64, 140, 255, 255)
    draw.rectangle((0, 0, width - 1, height - 1), outline=outline, width=3 if current else 2)


def _page_number(work, page_index: int) -> str:
    info = getattr(work, "work_info", None)
    start = int(getattr(info, "page_number_start", 1) or 1) if info is not None else 1
    return f"{start + int(page_index):03d}"


def _coma_polygon_mm(coma) -> list[tuple[float, float]]:
    shape = str(getattr(coma, "shape_type", "rect") or "rect")
    if shape == "rect":
        x = float(getattr(coma, "rect_x_mm", 0.0) or 0.0)
        y = float(getattr(coma, "rect_y_mm", 0.0) or 0.0)
        w = max(0.1, float(getattr(coma, "rect_width_mm", 0.1) or 0.1))
        h = max(0.1, float(getattr(coma, "rect_height_mm", 0.1) or 0.1))
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    vertices = list(getattr(coma, "vertices", []) or [])
    if len(vertices) >= 3:
        return [(float(v.x_mm), float(v.y_mm)) for v in vertices]
    return []


def _bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _preview_png_path(work, page_id: str) -> Path | None:
    work_dir_text = str(getattr(work, "work_dir", "") or "")
    if not work_dir_text:
        return None
    work_dir = Path(work_dir_text)
    return work_dir / page_id / PREVIEW_FILENAME


def _draw_coma_thumb(draw, image, work, page, coma, points_px, bbox_px) -> None:
    try:
        from PIL import Image
        from . import coma_preview

        src = coma_preview.coma_preview_source_path(Path(work.work_dir), page.id, coma)
        if src is None or not Path(src).is_file():
            return
        x0, y0, x1, y1 = bbox_px
        width = max(1, int(round(x1 - x0)))
        height = max(1, int(round(y1 - y0)))
        from ..io import export_pipeline

        src_image = export_pipeline._safe_load_image(Path(src))
        if src_image is None:
            return
        thumb = src_image.resize((width, height))
        mask_draw = Image.new("L", (width, height), 0)
        local_points = [(int(round(x - x0)), int(round(y - y0))) for x, y in points_px]
        from PIL import ImageDraw

        ImageDraw.Draw(mask_draw).polygon(local_points, fill=255)
        image.paste(thumb, (int(round(x0)), int(round(y0))), mask_draw)
    except Exception:  # noqa: BLE001
        return


def _render_preview_image(work, page, page_index: int, *, current: bool, scene=None):
    from PIL import Image, ImageDraw

    # 見開きはタイル自体が 2 ページ分の横長 (_image_size が page 対応)
    target_width, target_height = _image_size(work, scene, page)
    cw = max(1.0, float(getattr(work.paper, "canvas_width_mm", 1.0) or 1.0))
    ch = max(1.0, float(getattr(work.paper, "canvas_height_mm", 1.0) or 1.0))
    fw = max(1.0, float(getattr(work.paper, "finish_width_mm", 1.0) or 1.0))
    content_width_mm = page_grid.spread_content_width_mm(page, cw, fw)
    # 大きい目標サイズではスーパーサンプリング不要 (生成時間とメモリの節約)
    scale = max(1, int(PREVIEW_RENDER_SUPERSAMPLE))
    if max(target_width, target_height) >= PREVIEW_SUPERSAMPLE_MAX_TARGET_PX:
        scale = 1
    width = max(1, target_width * scale)
    height = max(1, target_height * scale)
    exported = _render_preview_image_from_export(work, page, width, height, scene=scene)
    if exported is not None:
        exported = _resize_preview_image(exported, target_width, target_height)
        from . import page_preview_decor

        page_preview_decor.draw_preview_decoration(
            exported,
            work,
            page,
            scene=scene,
            include_fills=False,
        )
        draw = ImageDraw.Draw(exported)
        _draw_preview_frame(draw, target_width, target_height, current=current)
        return exported

    img = Image.new("RGBA", (width, height), (250, 250, 250, 255))
    draw = ImageDraw.Draw(img)

    def point_px(pt: tuple[float, float]) -> tuple[float, float]:
        x, y = pt
        return (x / content_width_mm * width, height - (y / ch * height))

    paper_color = _rgba255(getattr(work.paper, "paper_color", (1, 1, 1, 1)))
    draw.rectangle((0, 0, width - 1, height - 1), fill=paper_color)

    for coma in getattr(page, "comas", []) or []:
        if not bool(getattr(coma, "visible", True)):
            continue
        pts = _coma_polygon_mm(coma)
        if len(pts) < 3:
            continue
        pts_px = [point_px(p) for p in pts]
        fill = _rgba255(getattr(coma, "background_color", (1, 1, 1, 1)))
        if not bool(getattr(coma, "paper_visible", True)):
            fill = (255, 255, 255, 0)
        draw.polygon(pts_px, fill=fill)
        bbox = _bbox(pts_px)
        if bbox is not None:
            _draw_coma_thumb(draw, img, work, page, coma, pts_px, bbox)
        border = getattr(coma, "border", None)
        border_color = _rgba255(getattr(border, "color", (0, 0, 0, 1)), (0, 0, 0, 255))
        line_w_mm = max(0.2, float(getattr(border, "width_mm", 0.5) or 0.5))
        px_per_mm = max(width / content_width_mm, height / ch)
        line_w = max(1, int(round(line_w_mm * px_per_mm)))
        spread_basic_side = ""
        spread_basic_rect = None
        try:
            spread_basic_side, spread_basic_rect = spread_merge_geometry.basic_frame_info(work, page, coma)
        except Exception:  # noqa: BLE001
            spread_basic_side = ""
            spread_basic_rect = None
        if spread_basic_side == "left" and spread_basic_rect is not None:
            merged_pts = [
                (float(spread_basic_rect.x), float(spread_basic_rect.y)),
                (float(spread_basic_rect.x2), float(spread_basic_rect.y)),
                (float(spread_basic_rect.x2), float(spread_basic_rect.y2)),
                (float(spread_basic_rect.x), float(spread_basic_rect.y2)),
            ]
            merged_pts_px = [point_px(p) for p in merged_pts]
            closed = merged_pts_px + [merged_pts_px[0]]
            draw.line(closed, fill=border_color, width=line_w, joint="curve")
        elif spread_basic_side == "right":
            continue
        else:
            closed = pts_px + [pts_px[0]]
            draw.line(closed, fill=border_color, width=line_w, joint="curve")

    from . import page_preview_decor

    page_preview_decor.draw_preview_decoration(
        img,
        work,
        page,
        scene=scene,
        include_fills=True,
    )
    img = _resize_preview_image(img, target_width, target_height)
    draw = ImageDraw.Draw(img)
    _draw_preview_frame(draw, target_width, target_height, current=current)
    return img


def _render_preview_image_from_export(work, page, width: int, height: int, *, scene=None):
    try:
        from PIL import Image
        from ..io import export_pipeline

        if not export_pipeline.has_pillow():
            return None
        cw = max(1.0, float(getattr(work.paper, "canvas_width_mm", 1.0) or 1.0))
        ch = max(1.0, float(getattr(work.paper, "canvas_height_mm", 1.0) or 1.0))
        fw = max(1.0, float(getattr(work.paper, "finish_width_mm", 1.0) or 1.0))
        cw = page_grid.spread_content_width_mm(page, cw, fw)
        dpi = max(8, int(round(max(width / cw, height / ch) * 25.4)))
        from . import page_preview_decor

        detail_preview = _preview_render_variant(scene) == PREVIEW_RENDER_VARIANT_DETAIL
        page_overlay_visible = detail_preview and page_preview_decor.page_guides_visible(work, scene)
        options = export_pipeline.ExportOptions(
            area="canvas",
            dpi_override=dpi,
            include_border=True,
            include_white_margin=True,
            include_nombre=False,
            include_work_info=False,
            include_tombo=False,
            include_paper_color=True,
            include_coma_previews=True,
            include_page_overlay_fills=page_overlay_visible,
        )
        image = export_pipeline.render_page(work, page, options)
        if image is None:
            return None
        image = image.convert("RGBA")
        image = _resize_preview_image(image, width, height)
        return image
    except Exception:  # noqa: BLE001
        _logger.exception("page preview export render failed: %s", getattr(page, "id", ""))
        return None


def ensure_preview_png(work, page, page_index: int, *, current: bool, scene=None, force: bool = False) -> Path | None:
    page_id = str(getattr(page, "id", "") or "")
    if not paths.is_valid_page_id(page_id):
        return None
    path = _preview_png_path(work, page_id)
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        expected_size = _image_size(work, scene, page)
        if not force and not _preview_png_fresh_for_page(work, page, path):
            force = True
        variant = _preview_render_variant(scene)
        signature = _preview_render_signature(work, scene)
        if not force and _preview_png_usable(
            path,
            expected_size,
            current=current,
            variant=variant,
            signature=signature,
        ):
            return path
        # 作品ファイルではページ詳細を常駐させないため、プレビュー再生成の
        # 間だけ page.json から読み込み、使用後に破棄する
        from . import page_detail

        loaded_here = page_detail.ensure_page_detail(work, page)
        try:
            image = _render_preview_image(work, page, page_index, current=current, scene=scene)
        finally:
            if loaded_here:
                page_detail.clear_page_detail(page)
        if image is None:
            return None
        _save_preview_png(image, path, variant=variant, signature=signature)
        return path
    except Exception:  # noqa: BLE001
        _logger.exception("page preview render failed: %s", page_id)
        return None


def _save_preview_png(image, path: Path, *, variant: str, signature: str) -> None:
    try:
        from PIL import PngImagePlugin

        metadata = PngImagePlugin.PngInfo()
        metadata.add_text(PREVIEW_RENDER_VERSION_KEY, PREVIEW_RENDER_VERSION)
        metadata.add_text(PREVIEW_RENDER_VARIANT_KEY, str(variant or PREVIEW_RENDER_VARIANT_WORK))
        metadata.add_text(PREVIEW_RENDER_SIGNATURE_KEY, str(signature or ""))
        image.save(path, pnginfo=metadata)
        return
    except Exception:  # noqa: BLE001
        image.save(path)


def _load_image(path: Path, expected_size: tuple[int, int] | None = None) -> bpy.types.Image | None:
    try:
        abspath = str(path.resolve())
        mtime = path.stat().st_mtime
    except OSError:
        return None
    img = None
    # 高速パス: プレビュー画像は決まった名前で保持しているため、まず名前で引く
    # (全画像を走査してパス解決する従来経路はページ数に比例して重い)
    expected_name = f"{PREVIEW_IMAGE_PREFIX}{path.parent.name}"
    named = bpy.data.images.get(expected_name)
    if named is not None:
        try:
            if str(Path(bpy.path.abspath(named.filepath)).resolve()) == abspath:
                img = named
                if (
                    float(named.get("_bmanga_page_preview_mtime", -1.0)) == mtime
                    and (
                        expected_size is None
                        or tuple(int(v) for v in named.size[:2]) == tuple(expected_size)
                    )
                ):
                    return named
        except Exception:  # noqa: BLE001
            img = None
    if img is None:
        for candidate in bpy.data.images:
            try:
                if str(Path(bpy.path.abspath(candidate.filepath)).resolve()) == abspath:
                    img = candidate
                    break
            except Exception:  # noqa: BLE001
                continue
    if img is not None and expected_size is not None:
        try:
            if tuple(int(v) for v in img.size[:2]) != tuple(expected_size):
                bpy.data.images.remove(img)
                img = None
        except Exception:  # noqa: BLE001
            img = None
    if img is None:
        try:
            img = bpy.data.images.load(abspath, check_existing=True)
        except Exception:  # noqa: BLE001
            return None
    else:
        try:
            if float(img.get("_bmanga_page_preview_mtime", -1.0)) != mtime:
                img.reload()
        except Exception:  # noqa: BLE001
            pass
    img.name = f"{PREVIEW_IMAGE_PREFIX}{path.parent.name}"
    img["_bmanga_page_preview_mtime"] = mtime
    try:
        img.colorspace_settings.name = "sRGB"
    except Exception:  # noqa: BLE001
        pass
    return img


def _ensure_material(page_id: str, image: bpy.types.Image | None) -> bpy.types.Material:
    mat = bpy.data.materials.get(f"{PREVIEW_MATERIAL_PREFIX}{page_id}")
    if mat is None:
        mat = bpy.data.materials.new(f"{PREVIEW_MATERIAL_PREFIX}{page_id}")
    # 既に同じ画像へ結線済みならノード再構築をスキップ (ページ追加等の高速化)。
    # 名前一致だけでは、解像度変更などで画像データブロックが入れ替わったときに
    # 古い参照を掴み続けるため、テクスチャノードの実体一致まで確認する。
    try:
        if (
            image is not None
            and mat.use_nodes
            and str(mat.get("_bmanga_preview_image", "") or "") == str(image.name)
            and mat.node_tree is not None
            and len(mat.node_tree.nodes) >= 5
            and any(
                getattr(node, "type", "") == "TEX_IMAGE" and node.image == image
                for node in mat.node_tree.nodes
            )
        ):
            _apply_material_opacity(mat, _preview_opacity_factor())
            return mat
    except Exception:  # noqa: BLE001
        pass
    mat.use_nodes = True
    try:
        mat.show_transparent_back = False
    except Exception:  # noqa: BLE001
        pass
    nt = mat.node_tree
    for node in list(nt.nodes):
        nt.nodes.remove(node)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    emission = nt.nodes.new("ShaderNodeEmission")
    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    tex = nt.nodes.new("ShaderNodeTexImage")
    alpha = nt.nodes.new("ShaderNodeValue")
    alpha.name = PREVIEW_OPACITY_NODE
    alpha.label = PREVIEW_OPACITY_NODE
    alpha.outputs[0].default_value = _preview_opacity_factor()
    multiply = nt.nodes.new("ShaderNodeMath")
    multiply.name = PREVIEW_OPACITY_MATH_NODE
    multiply.label = PREVIEW_OPACITY_MATH_NODE
    multiply.operation = "MULTIPLY"
    tex.image = image
    try:
        emission.inputs["Strength"].default_value = 1.0
        nt.links.new(tex.outputs["Color"], emission.inputs["Color"])
        nt.links.new(tex.outputs["Alpha"], multiply.inputs[0])
        nt.links.new(alpha.outputs[0], multiply.inputs[1])
        nt.links.new(multiply.outputs[0], mix.inputs["Fac"])
        nt.links.new(transparent.outputs["BSDF"], mix.inputs[1])
        emission_out = emission.outputs.get("Emission") or next(iter(emission.outputs), None)
        if emission_out is not None:
            nt.links.new(emission_out, mix.inputs[2])
        nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    except Exception:  # noqa: BLE001
        _logger.exception("page preview material link failed")
    _apply_material_opacity(mat, _preview_opacity_factor())
    try:
        mat["_bmanga_preview_image"] = str(getattr(image, "name", "") or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        mat.update_tag()
    except Exception:  # noqa: BLE001
        pass
    return mat


def _apply_material_opacity(mat: bpy.types.Material | None, opacity: float) -> None:
    if mat is None:
        return
    opacity = max(0.0, min(1.0, float(opacity)))
    try:
        mat.blend_method = "OPAQUE" if opacity >= 0.999 else "BLEND"
        mat.show_transparent_back = False
        mat.diffuse_color = (1.0, 1.0, 1.0, opacity)
    except Exception:  # noqa: BLE001
        pass
    node_tree = getattr(mat, "node_tree", None)
    if node_tree is None:
        try:
            mat.update_tag()
        except Exception:  # noqa: BLE001
            pass
        return
    nodes = node_tree.nodes
    value = nodes.get(PREVIEW_OPACITY_NODE)
    if value is not None and getattr(value, "type", "") == "VALUE":
        try:
            value.outputs[0].default_value = opacity
        except Exception:  # noqa: BLE001
            pass
        try:
            mat.update_tag()
        except Exception:  # noqa: BLE001
            pass
        return
    tex = next((node for node in nodes if getattr(node, "type", "") == "TEX_IMAGE"), None)
    mix = next((node for node in nodes if getattr(node, "type", "") == "MIX_SHADER"), None)
    if tex is None or mix is None:
        return
    value = nodes.new("ShaderNodeValue")
    value.name = PREVIEW_OPACITY_NODE
    value.label = PREVIEW_OPACITY_NODE
    value.outputs[0].default_value = opacity
    multiply = nodes.new("ShaderNodeMath")
    multiply.name = PREVIEW_OPACITY_MATH_NODE
    multiply.label = PREVIEW_OPACITY_MATH_NODE
    multiply.operation = "MULTIPLY"
    try:
        for link in list(node_tree.links):
            if link.to_node is mix and link.to_socket == mix.inputs["Fac"]:
                node_tree.links.remove(link)
        node_tree.links.new(tex.outputs["Alpha"], multiply.inputs[0])
        node_tree.links.new(value.outputs[0], multiply.inputs[1])
        node_tree.links.new(multiply.outputs[0], mix.inputs["Fac"])
    except Exception:  # noqa: BLE001
        pass
    try:
        mat.update_tag()
    except Exception:  # noqa: BLE001
        pass


def set_preview_opacity(context=None, opacity: float | None = None) -> None:
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    factor = _preview_opacity_factor(scene) if opacity is None else max(0.0, min(1.0, float(opacity)))
    for obj in _iter_preview_objects():
        for mat in getattr(getattr(obj, "data", None), "materials", []) or []:
            _apply_material_opacity(mat, factor)


def set_preview_scale(context=None, scale: float | None = None) -> None:
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    factor = _preview_scale_factor(scene) if scale is None else max(0.1, min(10.0, float(scale)))
    for obj in _iter_preview_objects():
        try:
            obj.scale.x = factor
            obj.scale.y = factor
            obj.scale.z = 1.0
        except Exception:  # noqa: BLE001
            pass


def _ensure_plane_mesh(page_id: str, width_mm: float, height_mm: float) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.get(f"{PREVIEW_MESH_PREFIX}{page_id}")
    if mesh is None:
        mesh = bpy.data.meshes.new(f"{PREVIEW_MESH_PREFIX}{page_id}")
    # 同じ寸法ならジオメトリ再構築をスキップ
    try:
        if (
            len(mesh.vertices) == 4
            and abs(float(mesh.get("_bmanga_w", -1.0)) - float(width_mm)) < 1.0e-6
            and abs(float(mesh.get("_bmanga_h", -1.0)) - float(height_mm)) < 1.0e-6
        ):
            return mesh
    except Exception:  # noqa: BLE001
        pass
    hw = mm_to_m(width_mm) * 0.5
    hh = mm_to_m(height_mm) * 0.5
    mesh.clear_geometry()
    mesh.from_pydata(
        [(-hw, -hh, 0.0), (hw, -hh, 0.0), (hw, hh, 0.0), (-hw, hh, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    uv = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
    for loop_index, uv_value in zip(
        mesh.polygons[0].loop_indices,
        ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)),
        strict=False,
    ):
        uv.data[loop_index].uv = uv_value
    mesh["_bmanga_w"] = float(width_mm)
    mesh["_bmanga_h"] = float(height_mm)
    return mesh


def preview_rects_mm(scene, work) -> dict[str, tuple[int, float, float, float, float]]:
    if scene is None or work is None or not getattr(work, "loaded", False):
        return {}
    cw = max(1.0, float(getattr(work.paper, "canvas_width_mm", 1.0) or 1.0))
    ch = max(1.0, float(getattr(work.paper, "canvas_height_mm", 1.0) or 1.0))
    cols = max(1, int(getattr(scene, "bmanga_overview_cols", 4) or 4))
    gap_x, gap_y = page_grid.resolve_gap_mm(scene)
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")
    rects: dict[str, tuple[int, float, float, float, float]] = {}
    target_indices = _preview_page_indices(scene, work)
    for i, page in enumerate(getattr(work, "pages", []) or []):
        if i not in target_indices:
            continue
        page_id = str(getattr(page, "id", "") or "")
        if not page_id or not page_range.page_in_range(page):
            continue
        ox, oy = page_grid.page_grid_offset_mm(
            i, cols, gap_x, cw, ch, start_side, read_direction,
            work=work, gap_y_mm=gap_y,
        )
        add_x, add_y = page_grid.page_manual_offset_mm(page)
        x0 = ox + add_x
        y0 = oy + add_y
        page_w = page_grid.page_content_width_mm(work, i, cw)
        rects[page_id] = (i, x0, y0, x0 + page_w, y0 + ch)
    return rects


def _preview_page_indices(scene, work) -> set[int]:
    pages = list(getattr(work, "pages", []) or [])
    if not pages:
        return set()
    try:
        from . import page_file_scene as _pfs
        if _pfs.is_work_list_scene(scene):
            return set(range(len(pages)))
    except Exception:  # noqa: BLE001
        pass
    role, current_page_id = _preview_scene_role(scene)
    if preview_range_mode(scene) == PREVIEW_RANGE_ALL:
        return set(range(len(pages)))
    if role != "coma":
        _is_page_scene, current_page_id = _is_page_edit_scene(scene)
    current_index = -1
    for i, page in enumerate(pages):
        if str(getattr(page, "id", "") or "") == current_page_id:
            current_index = i
            break
    if current_index < 0:
        try:
            current_index = max(0, min(len(pages) - 1, int(getattr(work, "active_page_index", 0))))
        except (TypeError, ValueError):
            current_index = 0
    radius = 1
    first = max(0, current_index - radius)
    last = min(len(pages) - 1, current_index + radius)
    return set(range(first, last + 1))


def preview_page_indices(scene, work) -> set[int]:
    """ページファイルで表示対象になる周辺ページ index を返す."""
    return set(_preview_page_indices(scene, work))


def page_index_at_world_mm(scene, work, x_mm: float, y_mm: float) -> int | None:
    if not preview_enabled(scene):
        return None
    for _page_id, (index, x0, y0, x1, y1) in preview_rects_mm(scene, work).items():
        if x0 <= x_mm <= x1 and y0 <= y_mm <= y1:
            return index
    return None


_highlighted_page_id: str = ""


def highlighted_page_id() -> str:
    """現在ハイライト中のページIDを返す."""
    return _highlighted_page_id


def highlight_preview_page(scene, work, page_index: int | None) -> None:
    """指定ページのプレビューオブジェクトをハイライトする."""
    global _highlighted_page_id  # noqa: PLW0603
    if work is None:
        _highlighted_page_id = ""
        return
    pages = list(getattr(work, "pages", []) or [])
    if page_index is not None and 0 <= page_index < len(pages):
        _highlighted_page_id = str(getattr(pages[page_index], "id", "") or "")
    else:
        _highlighted_page_id = ""


def _ensure_preview_object(scene, work, page, page_index: int, rect, *, current: bool, force: bool = False, coma_origin_mm=None) -> None:
    role, _current_page_id = _preview_scene_role(scene)
    is_coma = role == "coma"
    page_id = str(getattr(page, "id", "") or "")
    path = ensure_preview_png(work, page, page_index, current=current, scene=scene, force=force)
    image = _load_image(path, _image_size(work, scene, page)) if path is not None else None
    _index, x0, y0, x1, y1 = rect
    mesh = _ensure_plane_mesh(page_id, x1 - x0, y1 - y0)
    mat = _ensure_material(page_id, image)
    if not mesh.materials:
        mesh.materials.append(mat)
    elif mesh.materials[0] is not mat:
        mesh.materials[0] = mat
    obj = bpy.data.objects.get(f"{PREVIEW_OBJECT_PREFIX}{page_id}")
    if obj is None:
        obj = bpy.data.objects.new(f"{PREVIEW_OBJECT_PREFIX}{page_id}", mesh)
    elif obj.data is not mesh:
        obj.data = mesh
    _clear_preview_camera_follow(obj, clear_anchor=True)
    center_x_mm = (x0 + x1) * 0.5
    center_y_mm = (y0 + y1) * 0.5
    if is_coma and coma_origin_mm is not None:
        origin_x, origin_y = coma_origin_mm
        obj.location.x = mm_to_m(center_x_mm - origin_x)
        obj.location.z = mm_to_m(center_y_mm - origin_y)
        obj.location.y = PREVIEW_Z_M
        obj.rotation_euler = (math.radians(90.0), 0.0, 0.0)
    else:
        obj.location.x = mm_to_m(center_x_mm)
        obj.location.y = mm_to_m(center_y_mm)
        obj.location.z = PREVIEW_Z_M
        obj.rotation_euler = (0.0, 0.0, 0.0)
    obj.scale.x = _preview_scale_factor(scene)
    obj.scale.y = _preview_scale_factor(scene)
    obj.scale.z = 1.0
    obj.hide_viewport = True
    obj.hide_render = True
    obj.hide_select = True
    obj.show_name = False
    obj[on.PROP_KIND] = PREVIEW_KIND
    obj[on.PROP_ID] = page_id
    obj[PREVIEW_PAGE_ID_PROP] = page_id
    obj[on.PROP_MANAGED] = False
    obj[on.PROP_NO_NORMALIZE] = True
    coll = _preview_collection(scene)
    if not any(o is obj for o in coll.objects):
        try:
            coll.objects.link(obj)
        except RuntimeError:
            pass
    for users_coll in list(getattr(obj, "users_collection", ()) or ()):
        if users_coll is coll:
            continue
        try:
            users_coll.objects.unlink(obj)
        except Exception:  # noqa: BLE001
            pass


def _preview_png_fresh_for_page(work, page, path: Path) -> bool:
    """プレビュー PNG が保存内容 (page.json / コマ画像) より新しいかを返す."""
    try:
        png_mtime = path.stat().st_mtime
    except OSError:
        return False
    try:
        from . import coma_preview

        work_dir = Path(str(getattr(work, "work_dir", "") or ""))
        work_meta = paths.work_meta_path(work_dir)
        if work_meta.is_file() and work_meta.stat().st_mtime > png_mtime:
            return False
        page_id = str(getattr(page, "id", "") or "")
        meta = paths.page_meta_path(work_dir, page_id)
        if meta.is_file() and meta.stat().st_mtime > png_mtime:
            return False
        for coma in getattr(page, "comas", []) or []:
            src = coma_preview.coma_preview_source_path(work_dir, page_id, coma)
            if src is not None and src.is_file() and src.stat().st_mtime > png_mtime:
                return False
    except Exception:  # noqa: BLE001
        return False
    return True


def sync_page_previews(context=None, work=None, *, force: bool = False) -> int:
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return 0
    if work is None:
        work = getattr(scene, "bmanga_work", None)
    role, current_page_id = _preview_scene_role(scene)
    if role not in {"page", "work", "coma"} or not preview_enabled(scene):
        hide_page_previews(scene)
        return 0
    if work is None or not getattr(work, "loaded", False):
        hide_page_previews(scene)
        return 0
    rects = preview_rects_mm(scene, work)
    valid_page_ids = set(rects)
    if role == "coma":
        updated = 0
        for page in getattr(work, "pages", []) or []:
            page_id = str(getattr(page, "id", "") or "")
            rect = rects.get(page_id)
            if rect is None:
                continue
            ensure_preview_png(
                work,
                page,
                int(rect[0]),
                current=page_id == current_page_id,
                scene=scene,
                force=force,
            )
            updated += 1
        hide_page_previews(scene)
        try:
            for area in getattr(context, "screen", None).areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
        except Exception:  # noqa: BLE001
            pass
        return updated
    if role == "page" and current_page_id:
        current_rect = rects.get(current_page_id)
        if current_rect is not None:
            current_index = int(current_rect[0])
            try:
                page = getattr(work, "pages", [])[current_index]
                png_path = _preview_png_path(work, current_page_id)
                needs = png_path is None or not _preview_png_fresh_for_page(work, page, png_path)
                if needs:
                    ensure_preview_png(work, page, current_index, current=True, scene=scene, force=True)
            except Exception:  # noqa: BLE001
                _logger.exception("current page preview update failed: %s", current_page_id)
    # ── メッシュ平面方式 (ページファイル・作品ファイル共通) ──
    for obj in _iter_preview_objects():
        page_id = str(obj.get(PREVIEW_PAGE_ID_PROP, "") or "")
        if page_id not in valid_page_ids:
            obj.hide_viewport = True
            obj.hide_render = True
    coma_origin_mm = None
    if role == "coma" and current_page_id:
        current_rect = rects.get(current_page_id)
        if current_rect is not None:
            _ci, cx0, cy0, cx1, cy1 = current_rect
            coma_origin_mm = ((cx0 + cx1) * 0.5, (cy0 + cy1) * 0.5)
    updated = 0
    for page in getattr(work, "pages", []) or []:
        page_id = str(getattr(page, "id", "") or "")
        rect = rects.get(page_id)
        if rect is None:
            continue
        _ensure_preview_object(
            scene,
            work,
            page,
            int(rect[0]),
            rect,
            current=page_id == current_page_id,
            force=force,
            coma_origin_mm=coma_origin_mm,
        )
        updated += 1
    try:
        for area in getattr(context, "screen", None).areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass
    return updated


def _run_deferred_sync_page_previews():
    global _DEFERRED_SYNC_FORCE
    force = bool(_DEFERRED_SYNC_FORCE)
    _DEFERRED_SYNC_FORCE = False
    try:
        scene = getattr(bpy.context, "scene", None)
        work = getattr(scene, "bmanga_work", None) if scene is not None else None
        sync_page_previews(bpy.context, work, force=force)
    except Exception:  # noqa: BLE001
        _logger.exception("deferred page preview setup failed")
    return None


def schedule_sync_page_previews(*, force: bool = False, delay: float = 0.2) -> None:
    """次のUI更新後にページ一覧プレビューを同期する."""
    global _DEFERRED_SYNC_FORCE
    _DEFERRED_SYNC_FORCE = bool(_DEFERRED_SYNC_FORCE or force)
    try:
        if bpy.app.timers.is_registered(_run_deferred_sync_page_previews):
            return
        bpy.app.timers.register(
            _run_deferred_sync_page_previews,
            first_interval=max(0.01, float(delay)),
        )
    except Exception:  # noqa: BLE001
        _logger.exception("page preview deferred sync registration failed")
