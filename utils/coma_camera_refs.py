"""コマ編集カメラ用のページ参照画像生成ヘルパ."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterable

from ..core.work import find_page_by_id
from ..io import export_pipeline
from . import log, page_grid, coma_preview, paths
from .geom import mm_to_px, canvas_rect, safe_rect
from .coma_camera_constants import (
    DEFAULT_REF_DPI,
    KOMA_REF_PREFIX,
    NAME_REF_PREFIX,
    REFERENCE_DIR_NAME,
)

_logger = log.get_logger(__name__)


class ReferenceImage:
    def __init__(
        self,
        path: Path,
        label: str,
        kind: str,
        page_id: str,
        visible: bool,
        *,
        full_page_mask: bool = False,
        page_count: int = 1,
        render_side: str = "full",
    ) -> None:
        self.path = Path(path)
        self.label = label
        self.kind = kind
        self.page_id = page_id
        self.visible = visible
        self.full_page_mask = full_page_mask
        self.page_count = max(1, int(page_count))
        self.render_side = render_side if render_side in {"left", "right", "full"} else "full"


def ensure_reference_images(work, current_page_id: str, coma_id: str) -> list[ReferenceImage]:
    """現在コマ用のページ全体マスク下絵を生成して返す."""
    if not export_pipeline.has_pillow():
        _logger.warning("panel camera references require Pillow")
        return _collect_existing_reference_images(work, current_page_id, coma_id)
    work_dir = Path(work.work_dir)
    ref_dir = reference_dir(work_dir)
    ref_dir.mkdir(parents=True, exist_ok=True)

    refs: list[ReferenceImage] = []
    include_work_blend_mtime = paths.work_blend_path(work_dir).is_file()
    page = find_page_by_id(work, current_page_id)
    panel = _resolve_coma(work, current_page_id, coma_id)
    if page is not None and panel is not None:
        page_count, render_side, _width_mm, _height_mm = _reference_frame_info(work, current_page_id, coma_id)
        current_page_ref = _ensure_page_reference(
            work,
            work_dir,
            page,
            ref_dir,
            include_work_blend_mtime,
            transparent_coma_id=coma_id,
        )
        mate_page = _find_spread_mate_page(work, current_page_id)
        mate_page_ref = None
        if mate_page is not None:
            mate_page_ref = _ensure_page_reference(work, work_dir, mate_page, ref_dir, include_work_blend_mtime)
        masked_page = _koma_ref_path(ref_dir, page.id, coma_id)
        if _coma_mask_is_stale((current_page_ref, mate_page_ref), masked_page):
            _render_current_coma_page_mask(work, page, panel, current_page_ref, mate_page, mate_page_ref, masked_page)
        if masked_page.is_file():
            refs.insert(
                0,
                ReferenceImage(
                    masked_page,
                    f"{KOMA_REF_PREFIX}_{page.id}_{coma_id}",
                    "own_page",
                    page.id,
                    visible=True,
                    full_page_mask=True,
                    page_count=page_count,
                    render_side=render_side,
                ),
            )
    return refs


def reference_dir(work_dir: Path) -> Path:
    return paths.assets_dir(Path(work_dir)) / REFERENCE_DIR_NAME


def _collect_existing_reference_images(work, current_page_id: str, coma_id: str) -> list[ReferenceImage]:
    """Pillow が無い環境でも、既存PNGやコマプレビューを下絵として拾う."""
    work_dir = Path(getattr(work, "work_dir", "") or "")
    ref_dir = reference_dir(work_dir)
    refs: list[ReferenceImage] = []
    page_count, render_side, _width_mm, _height_mm = _reference_frame_info(work, current_page_id, coma_id)
    masked_page = _koma_ref_path(ref_dir, current_page_id, coma_id)
    legacy_crop = ref_dir / f"{KOMA_REF_PREFIX}_{current_page_id}_{coma_id}.png"
    crop = masked_page if masked_page.is_file() else legacy_crop
    if crop.is_file():
        is_full_mask = (crop == masked_page)
        refs.insert(
            0,
            ReferenceImage(
                crop,
                f"{KOMA_REF_PREFIX}_{current_page_id}_{coma_id}",
                "own_page" if is_full_mask else "koma",
                current_page_id,
                visible=True,
                full_page_mask=is_full_mask,
                page_count=page_count,
                render_side=render_side,
            ),
        )
        return refs
    panel = _resolve_coma(work, current_page_id, coma_id)
    source = coma_preview.coma_preview_source_path(work_dir, current_page_id, panel)
    if source is not None and source.is_file():
        refs.insert(
            0,
            ReferenceImage(
                source,
                f"{KOMA_REF_PREFIX}_{current_page_id}_{coma_id}",
                "koma",
                current_page_id,
                visible=True,
            ),
        )
    return refs


def _page_ref_path(ref_dir: Path, page_id: str) -> Path:
    return ref_dir / f"{NAME_REF_PREFIX}_pageclean_{page_id}.png"


def _page_coma_ref_path(ref_dir: Path, page_id: str, coma_id: str) -> Path:
    return ref_dir / f"{NAME_REF_PREFIX}_pageclean_{page_id}_{coma_id}.png"


def _koma_ref_path(ref_dir: Path, page_id: str, coma_id: str) -> Path:
    return ref_dir / f"{KOMA_REF_PREFIX}_{page_id}_{coma_id}_page.png"


def _reference_frame_info(work, page_id: str, coma_id: str = "") -> tuple[int, str, float, float]:
    paper = getattr(work, "paper", None) if work is not None else None
    page = find_page_by_id(work, page_id) if work is not None and page_id else None
    page_width = float(getattr(paper, "canvas_width_mm", 0.0) or 0.0)
    page_height = float(getattr(paper, "canvas_height_mm", 0.0) or 0.0)
    if page is None or page_width <= 0.0 or page_height <= 0.0:
        return 1, "full", page_width, page_height
    if bool(getattr(page, "spread", False)):
        panel = _resolve_coma(work, page_id, coma_id)
        return 2, _spread_coma_side(panel, page_width), page_width, page_height
    mate = _find_spread_mate_page(work, page_id)
    if mate is None:
        return 1, "full", page_width, page_height
    side = "left" if _is_page_left_half(work, page_id) else "right"
    return 2, side, page_width, page_height


def _spread_coma_side(panel, page_width_mm: float) -> str:
    bbox = _coma_bbox(panel)
    if bbox is None or page_width_mm <= 0.0:
        return "full"
    center_x = (bbox[0] + bbox[2]) * 0.5
    return "left" if center_x < page_width_mm else "right"


def _ensure_page_reference(
    work,
    work_dir: Path,
    page,
    ref_dir: Path,
    include_work_blend_mtime: bool,
    *,
    transparent_coma_id: str = "",
) -> Path | None:
    page_id = str(getattr(page, "id", "") or "")
    if not page_id:
        return None
    out = _page_coma_ref_path(ref_dir, page_id, transparent_coma_id) if transparent_coma_id else _page_ref_path(ref_dir, page_id)
    if _reference_is_stale(work_dir, page, out, include_work_blend=include_work_blend_mtime):
        _render_page_reference(work, page, out, transparent_coma_id=transparent_coma_id)
    return out if out.is_file() else None


def _find_spread_mate_page(work, current_page_id: str):
    pages = list(getattr(work, "pages", []) or [])
    current_index = next(
        (i for i, page in enumerate(pages) if str(getattr(page, "id", "") or "") == current_page_id),
        -1,
    )
    if current_index < 0:
        return None
    current_page = pages[current_index]
    if bool(getattr(current_page, "spread", False)):
        return None
    paper = getattr(work, "paper", None)
    start_side = str(getattr(paper, "start_side", "right") or "right")
    read_direction = str(getattr(paper, "read_direction", "left") or "left")
    if read_direction == "down":
        return None
    current_slot = page_grid.slot_for_page_in_work(work, current_index, start_side, read_direction)
    mate_slot = current_slot - 1 if current_slot % 2 else current_slot + 1
    for index, page in enumerate(pages):
        if index == current_index or bool(getattr(page, "spread", False)):
            continue
        slot = page_grid.slot_for_page_in_work(work, index, start_side, read_direction)
        if slot == mate_slot:
            return page
    return None


def _is_page_left_half(work, page_id: str) -> bool:
    pages = list(getattr(work, "pages", []) or [])
    page_index = next((i for i, page in enumerate(pages) if str(getattr(page, "id", "") or "") == page_id), 0)
    paper = getattr(work, "paper", None)
    start_side = str(getattr(paper, "start_side", "right") or "right")
    read_direction = str(getattr(paper, "read_direction", "left") or "left")
    return page_grid.is_left_half_page(page_index, start_side, read_direction, work=work)


def _render_page_reference(work, page, out: Path, *, transparent_coma_id: str = "") -> bool:
    work_dir = Path(str(getattr(work, "work_dir", "") or ""))
    page_id = str(getattr(page, "id", "") or "")
    if work_dir and page_id and not _current_mainfile_is(paths.work_blend_path(work_dir)):
        if _render_page_reference_from_work_blend(work_dir, page_id, out, transparent_coma_id=transparent_coma_id):
            return True
    return _render_page_reference_in_scene(work, page, out, transparent_coma_id=transparent_coma_id)


def _overlay_safe_area_fill(img, work, dpi: int):
    """セーフライン外の塗りつぶしをプレビュー画像に合成する."""
    Image = export_pipeline.Image
    ImageDraw = export_pipeline.ImageDraw
    if Image is None or ImageDraw is None or img is None:
        return img
    paper = getattr(work, "paper", None)
    overlay_cfg = getattr(work, "safe_area_overlay", None)
    if paper is None or overlay_cfg is None:
        return img
    if not getattr(overlay_cfg, "enabled", True):
        return img
    opacity_pct = float(getattr(overlay_cfg, "opacity", 30.0) or 30.0)
    if opacity_pct <= 0.0:
        return img
    color = getattr(overlay_cfg, "color", (0.0, 0.0, 0.0))
    alpha = max(0, min(255, int(round(opacity_pct / 100.0 * 255))))
    r = max(0, min(255, int(round(float(color[0]) * 255))))
    g = max(0, min(255, int(round(float(color[1]) * 255))))
    b = max(0, min(255, int(round(float(color[2]) * 255))))

    canvas = canvas_rect(paper)
    safe = safe_rect(paper)
    w, h = img.size

    def to_px(mm_val):
        return int(round(mm_to_px(mm_val, dpi)))

    sx = to_px(safe.x)
    sy_top = to_px(canvas.height - safe.y2)
    sx2 = to_px(safe.x2)
    sy_bottom = to_px(canvas.height - safe.y)
    sx = max(0, min(w, sx))
    sx2 = max(0, min(w, sx2))
    sy_top = max(0, min(h, sy_top))
    sy_bottom = max(0, min(h, sy_bottom))

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    fc = (r, g, b, alpha)
    if sy_top > 0:
        draw.rectangle([0, 0, w - 1, sy_top - 1], fill=fc)
    if sy_bottom < h:
        draw.rectangle([0, sy_bottom, w - 1, h - 1], fill=fc)
    if sx > 0:
        draw.rectangle([0, sy_top, sx - 1, sy_bottom - 1], fill=fc)
    if sx2 < w:
        draw.rectangle([sx2, sy_top, w - 1, sy_bottom - 1], fill=fc)

    result = img.convert("RGBA")
    return Image.alpha_composite(result, overlay)


def _render_page_reference_in_scene(work, page, out: Path, *, transparent_coma_id: str = "") -> bool:
    try:
        options = export_pipeline.ExportOptions(
            format="png",
            color_mode="rgb",
            area="canvas",
            dpi_override=DEFAULT_REF_DPI,
            include_tombo=False,
            include_paper_color=True,
            include_coma_previews=False,
        )
        img = _render_page_with_transparent_coma_background(
            work,
            page,
            options,
            transparent_coma_id,
        )
        if img is None:
            return False
        dpi = int(getattr(options, "dpi_override", 0) or getattr(getattr(work, "paper", None), "dpi", DEFAULT_REF_DPI))
        img = _overlay_safe_area_fill(img, work, dpi)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out))
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("panel camera page reference render failed: %s", getattr(page, "id", ""))
        return False


def _render_page_with_transparent_coma_background(work, page, options, transparent_coma_id: str):
    if not transparent_coma_id:
        return export_pipeline.render_page(work, page, options)
    panel = _resolve_coma(work, str(getattr(page, "id", "") or ""), transparent_coma_id)
    if panel is None:
        return export_pipeline.render_page(work, page, options)
    try:
        layers = export_pipeline.build_page_layers(work, page, options)
        target_group = export_pipeline._coma_content_group_path(panel)
        dpi = int(getattr(options, "dpi_override", 0) or getattr(getattr(work, "paper", None), "dpi", DEFAULT_REF_DPI))
        prepared_layers = []
        target_prefix = tuple(target_group)
        for layer in layers:
            gp = tuple(layer.group_path)
            if len(gp) >= len(target_prefix) and gp[: len(target_prefix)] == target_prefix:
                continue
            if layer.name == "paper":
                layer = _layer_with_coma_background_hole(layer, panel, dpi)
            prepared_layers.append(layer)
        size = export_pipeline._page_canvas_size_px(work, page, options)
        group_masks = export_pipeline._coma_group_masks(work, page, options)
        from ..io import export_group_masks
        Image = export_pipeline.Image
        ImageChops = export_pipeline.ImageChops
        prepared_layers = export_group_masks.apply_group_masks_to_layers(
            prepared_layers, group_masks, Image, ImageChops,
        )
        image = export_pipeline._flatten_layers(prepared_layers, size)
        return export_pipeline._convert_flatten_mode(image, options)
    except Exception:  # noqa: BLE001
        _logger.exception("panel camera transparent page reference render failed")
        return export_pipeline.render_page(work, page, options)


def _layer_with_coma_background_hole(layer, panel, dpi: int):
    Image = export_pipeline.Image
    ImageDraw = export_pipeline.ImageDraw
    if Image is None or ImageDraw is None:
        return layer
    try:
        from . import coma_own_page_mask

        width_mm = max(1.0e-6, float(layer.image.width) / max(1, int(dpi)) * 25.4)
        height_mm = max(1.0e-6, float(layer.image.height) / max(1, int(dpi)) * 25.4)
        masked = coma_own_page_mask.apply_current_coma_cutout(layer.image, panel, width_mm, height_mm)
        if masked is not None:
            return replace(layer, image=masked)
    except Exception:  # noqa: BLE001
        _logger.exception("panel camera soft page hole failed")
    points = _coma_points_px(panel, layer.image.height, dpi, 0)
    if len(points) < 3:
        return layer
    image = layer.image.convert("RGBA").copy()
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    alpha = image.getchannel("A")
    alpha.paste(0, mask=mask)
    image.putalpha(alpha)
    return replace(layer, image=image)


def _render_page_reference_from_work_blend(
    work_dir: Path,
    page_id: str,
    out: Path,
    *,
    transparent_coma_id: str = "",
) -> bool:
    work_blend = paths.work_blend_path(Path(work_dir))
    if not work_blend.is_file():
        return False
    try:
        import bpy
    except Exception:  # pragma: no cover - bpy unavailable outside Blender
        return False

    before = _snapshot_bpy_ids(bpy)
    old_scene = getattr(getattr(bpy.context, "window", None), "scene", None)
    try:
        with bpy.data.libraries.load(str(work_blend.resolve()), link=False) as (data_from, data_to):
            data_to.scenes = list(getattr(data_from, "scenes", []) or [])
        loaded_scenes = _new_bpy_ids(bpy.data.scenes, before["scenes"])
        scene, loaded_work, loaded_page = _loaded_page_scene(loaded_scenes, work_dir, page_id)
        if scene is None or loaded_work is None or loaded_page is None:
            return False
        window = getattr(bpy.context, "window", None)
        if window is not None:
            window.scene = scene
        try:
            with bpy.context.temp_override(scene=scene):
                return _render_page_reference_in_scene(
                    loaded_work,
                    loaded_page,
                    out,
                    transparent_coma_id=transparent_coma_id,
                )
        except Exception:  # noqa: BLE001
            if window is not None:
                window.scene = scene
            return _render_page_reference_in_scene(
                loaded_work,
                loaded_page,
                out,
                transparent_coma_id=transparent_coma_id,
            )
    except Exception:  # noqa: BLE001
        _logger.exception("panel camera work.blend reference render failed: %s", page_id)
        return False
    finally:
        try:
            window = getattr(bpy.context, "window", None)
            if window is not None and old_scene is not None:
                window.scene = old_scene
        except Exception:  # noqa: BLE001
            pass
        _remove_new_bpy_ids(bpy, before)


def _loaded_page_scene(loaded_scenes, work_dir: Path, page_id: str):
    for scene in loaded_scenes:
        work = getattr(scene, "bmanga_work", None)
        if work is None:
            continue
        try:
            _reload_loaded_work_metadata(work, work_dir)
        except Exception:  # noqa: BLE001
            _logger.exception("panel camera loaded work metadata sync failed")
        page = find_page_by_id(work, page_id)
        if page is None:
            continue
        for index, candidate in enumerate(getattr(work, "pages", []) or []):
            if str(getattr(candidate, "id", "") or "") == page_id:
                try:
                    work.active_page_index = index
                except Exception:  # noqa: BLE001
                    pass
                break
        return scene, work, page
    return None, None, None


def _reload_loaded_work_metadata(work, work_dir: Path) -> None:
    from ..io import page_io, work_io
    from . import handlers

    work_io.load_work_json(work_dir, work)
    page_io.load_pages_json(work_dir, work)
    # ファイルの役割に応じた詳細読込 (自ページのみ等) を handlers と共有する
    handlers._reload_all_pages_panels(work, work_dir)
    work.work_dir = str(Path(work_dir).resolve())
    work.loaded = True


def _snapshot_bpy_ids(bpy_module) -> dict[str, set[int]]:
    return {
        "scenes": _id_keys(bpy_module.data.scenes),
        "objects": _id_keys(bpy_module.data.objects),
        "collections": _id_keys(bpy_module.data.collections),
        "meshes": _id_keys(bpy_module.data.meshes),
        "curves": _id_keys(bpy_module.data.curves),
        "cameras": _id_keys(bpy_module.data.cameras),
        "materials": _id_keys(bpy_module.data.materials),
        "images": _id_keys(bpy_module.data.images),
        "grease_pencils": _id_keys(_grease_pencil_blocks(bpy_module)),
    }


def _id_keys(blocks) -> set[int]:
    out: set[int] = set()
    for block in tuple(blocks or []):
        try:
            out.add(int(block.as_pointer()))
        except Exception:  # noqa: BLE001
            out.add(id(block))
    return out


def _new_bpy_ids(blocks, before: set[int]) -> list[object]:
    out = []
    for block in tuple(blocks or []):
        try:
            key = int(block.as_pointer())
        except Exception:  # noqa: BLE001
            key = id(block)
        if key not in before:
            out.append(block)
    return out


def _grease_pencil_blocks(bpy_module):
    blocks = getattr(bpy_module.data, "grease_pencils_v3", None)
    if blocks is None:
        blocks = getattr(bpy_module.data, "grease_pencils", None)
    return blocks or []


def _remove_new_bpy_ids(bpy_module, before: dict[str, set[int]]) -> None:
    for scene in _new_bpy_ids(bpy_module.data.scenes, before["scenes"]):
        try:
            bpy_module.data.scenes.remove(scene)
        except Exception:  # noqa: BLE001
            pass
    for obj in _new_bpy_ids(bpy_module.data.objects, before["objects"]):
        try:
            bpy_module.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
    for coll in _new_bpy_ids(bpy_module.data.collections, before["collections"]):
        try:
            bpy_module.data.collections.remove(coll)
        except Exception:  # noqa: BLE001
            pass
    _remove_orphan_new_blocks(bpy_module.data.meshes, before["meshes"])
    _remove_orphan_new_blocks(bpy_module.data.curves, before["curves"])
    _remove_orphan_new_blocks(bpy_module.data.cameras, before["cameras"])
    _remove_orphan_new_blocks(_grease_pencil_blocks(bpy_module), before["grease_pencils"])
    _remove_orphan_new_blocks(bpy_module.data.materials, before["materials"])
    _remove_orphan_new_blocks(bpy_module.data.images, before["images"])


def _remove_orphan_new_blocks(blocks, before: set[int]) -> None:
    remove = getattr(blocks, "remove", None)
    if remove is None:
        return
    for block in _new_bpy_ids(blocks, before):
        if int(getattr(block, "users", 0) or 0) > 0:
            continue
        try:
            remove(block)
        except Exception:  # noqa: BLE001
            pass


def _current_mainfile_is(path: Path) -> bool:
    try:
        import bpy

        current = Path(str(getattr(bpy.data, "filepath", "") or "")).resolve()
        return bool(current) and current == Path(path).resolve()
    except Exception:  # noqa: BLE001
        return False


def _render_current_coma_page_mask(work, page, panel, page_ref: Path | None, mate_page, mate_ref: Path | None, out: Path) -> bool:
    Image = export_pipeline.Image
    if Image is None or page_ref is None:
        return False
    try:
        with Image.open(str(page_ref)) as opened:
            page_img = opened.convert("RGBA")
    except Exception:  # noqa: BLE001
        return False
    mate_img = None
    if mate_page is not None and mate_ref is not None and mate_ref.is_file():
        try:
            with Image.open(str(mate_ref)) as opened:
                mate_img = opened.convert("RGBA")
        except Exception:  # noqa: BLE001
            mate_img = None
    canvas, panel_offset_x = _compose_page_reference_pair(work, page, page_img, mate_img)
    _ = panel, panel_offset_x
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(str(out))
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("panel camera page mask render failed: %s", getattr(page, "id", ""))
        return False


def _compose_page_reference_pair(work, page, page_img, mate_img):
    Image = export_pipeline.Image
    if Image is None:
        return page_img.copy(), 0
    if mate_img is None:
        return page_img.copy(), 0
    page_is_left = _is_page_left_half(work, str(getattr(page, "id", "") or ""))
    width = page_img.width + mate_img.width
    height = max(page_img.height, mate_img.height)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if page_is_left:
        canvas.paste(page_img, (0, 0))
        canvas.paste(mate_img, (page_img.width, 0))
        return canvas, 0
    canvas.paste(mate_img, (0, 0))
    canvas.paste(page_img, (mate_img.width, 0))
    return canvas, mate_img.width


def _coma_points_px(panel, image_height: int, dpi: int, offset_x: int) -> list[tuple[int, int]]:
    points = _coma_points_mm(panel)
    out: list[tuple[int, int]] = []
    for x_mm, y_mm in points:
        x = offset_x + int(round(mm_to_px(x_mm, dpi)))
        y = image_height - int(round(mm_to_px(y_mm, dpi)))
        out.append((x, y))
    return out


def _coma_points_mm(panel) -> list[tuple[float, float]]:
    if panel is None:
        return []
    if getattr(panel, "shape_type", "") == "rect":
        x = float(getattr(panel, "rect_x_mm", 0.0))
        y = float(getattr(panel, "rect_y_mm", 0.0))
        w = float(getattr(panel, "rect_width_mm", 0.0))
        h = float(getattr(panel, "rect_height_mm", 0.0))
        if w <= 0.0 or h <= 0.0:
            return []
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return [(float(v.x_mm), float(v.y_mm)) for v in getattr(panel, "vertices", [])]


def _reference_is_stale(work_dir: Path, page, out: Path, *, include_work_blend: bool) -> bool:
    if not out.is_file():
        return True
    latest = _path_mtime(paths.work_meta_path(work_dir))
    if include_work_blend:
        latest = max(latest, _path_mtime(paths.work_blend_path(work_dir)))
    latest = max(latest, _path_mtime(paths.pages_meta_path(work_dir)))
    latest = max(latest, _path_mtime(paths.page_meta_path(work_dir, page.id)))
    for panel in getattr(page, "comas", []):
        source = coma_preview.coma_preview_source_path(work_dir, page.id, panel)
        if source is not None:
            latest = max(latest, _path_mtime(source))
    return _path_mtime(out) < latest


def _has_master_gpencil() -> bool:
    try:
        from . import gpencil as gp_utils

        return gp_utils.get_master_gpencil() is not None
    except Exception:  # noqa: BLE001
        return False


def _coma_mask_is_stale(page_refs: Iterable[Path | None], out: Path) -> bool:
    valid_refs = [Path(ref) for ref in page_refs if ref is not None and Path(ref).is_file()]
    if not valid_refs:
        return False
    if not out.is_file():
        return True
    out_mtime = _path_mtime(out)
    return any(out_mtime < _path_mtime(ref) for ref in valid_refs)


def _path_mtime(path: Path) -> float:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


def _resolve_coma(work, page_id: str, coma_id: str):
    page = find_page_by_id(work, page_id) if work is not None else None
    if page is None:
        return None
    for panel in getattr(page, "comas", []):
        if getattr(panel, "coma_id", "") == coma_id:
            return panel
    return None


def _coma_bbox_size(panel) -> tuple[float, float]:
    bbox = _coma_bbox(panel)
    if bbox is None:
        return 0.0, 0.0
    return max(0.0, bbox[2] - bbox[0]), max(0.0, bbox[3] - bbox[1])


def _coma_bbox(panel) -> tuple[float, float, float, float] | None:
    if panel is None:
        return None
    if getattr(panel, "shape_type", "") == "rect":
        x = float(getattr(panel, "rect_x_mm", 0.0))
        y = float(getattr(panel, "rect_y_mm", 0.0))
        w = float(getattr(panel, "rect_width_mm", 0.0))
        h = float(getattr(panel, "rect_height_mm", 0.0))
        if w <= 0.0 or h <= 0.0:
            return None
        return x, y, x + w, y + h
    verts = [(float(v.x_mm), float(v.y_mm)) for v in getattr(panel, "vertices", [])]
    if not verts:
        return None
    xs = [p[0] for p in verts]
    ys = [p[1] for p in verts]
    return min(xs), min(ys), max(xs), max(ys)
