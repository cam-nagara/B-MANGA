"""作品情報・ページ番号のテキストオブジェクト同期."""

from __future__ import annotations

import bpy

from ..ui import overlay_shared
from . import log, object_naming as on, outliner_model as om, page_range, text_style
from .geom import mm_to_m, q_to_mm

_logger = log.get_logger(__name__)

WORK_INFO_TEXT_PREFIX = "work_info_text_"
WORK_INFO_MATERIAL_PREFIX = "BName_WorkInfoText_"
PROP_WORK_INFO_KIND = "bname_work_info_text_kind"
PROP_WORK_INFO_OWNER_ID = "bname_work_info_text_owner_id"
TEXT_Z_M = 0.032


def _material(owner_id: str, color) -> bpy.types.Material:
    name = f"{WORK_INFO_MATERIAL_PREFIX}{owner_id}"
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    rgba = tuple(float(c) for c in color[:4])
    mat.diffuse_color = rgba
    mat.use_nodes = True
    try:
        mat.blend_method = "BLEND"
        mat.surface_render_method = "BLENDED"
    except Exception:  # noqa: BLE001
        pass
    nt = mat.node_tree
    for node in list(nt.nodes):
        nt.nodes.remove(node)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    emission = nt.nodes.new("ShaderNodeEmission")
    try:
        emission.inputs["Color"].default_value = rgba
        emission.inputs["Strength"].default_value = 1.0
        nt.links.new(emission.outputs["Emission"], out.inputs["Surface"])
    except Exception:  # noqa: BLE001
        _logger.exception("work info text material setup failed")
    return mat


def _assign_default_font(curve: bpy.types.Curve) -> None:
    try:
        font_path = text_style.resolve_font_path("")
        if font_path:
            curve.font = bpy.data.fonts.load(font_path, check_existing=True)
    except Exception:  # noqa: BLE001
        pass


def _text_items(info, page_index: int) -> list[tuple[str, object, str]]:
    page_text = ""
    try:
        page_text = f"ページ{int(info.page_number_start) + int(page_index):04d}"
    except Exception:  # noqa: BLE001
        page_text = ""
    return [
        ("work_name", info.display_work_name, str(getattr(info, "work_name", "") or "")),
        (
            "episode",
            info.display_episode,
            f"第{int(info.episode_number)}話" if int(getattr(info, "episode_number", 0) or 0) else "",
        ),
        ("subtitle", info.display_subtitle, str(getattr(info, "subtitle", "") or "")),
        ("author", info.display_author, str(getattr(info, "author", "") or "")),
        ("page_number", info.display_page_number, page_text),
    ]


def _anchor(anchor_rect, position: str) -> tuple[float, float, str, str]:
    pad = 2.0
    if position.endswith("right"):
        x_mm = anchor_rect.x2
        align_x = "RIGHT"
    elif position.endswith("center"):
        x_mm = (anchor_rect.x + anchor_rect.x2) * 0.5
        align_x = "CENTER"
    else:
        x_mm = anchor_rect.x
        align_x = "LEFT"
    if position.startswith("top"):
        y_mm = anchor_rect.y2 + pad
        align_y = "BOTTOM"
    else:
        y_mm = anchor_rect.y - pad
        align_y = "TOP"
    return x_mm, y_mm, align_x, align_y


def _set_page_location(obj: bpy.types.Object, scene, work, page_index: int, x_mm: float, y_mm: float) -> None:
    from . import page_grid

    ox, oy = page_grid.page_total_offset_mm(work, scene, page_index)
    obj.location = (mm_to_m(ox + x_mm), mm_to_m(oy + y_mm), TEXT_Z_M)


def _link_to_page(obj: bpy.types.Object, scene, page) -> None:
    page_id = str(getattr(page, "id", "") or "")
    coll = om.ensure_page_collection(scene, page_id, str(getattr(page, "title", "") or page_id))
    if coll is not None and not any(existing is obj for existing in coll.objects):
        coll.objects.link(obj)
    for user_coll in tuple(obj.users_collection):
        if user_coll is coll:
            continue
        try:
            user_coll.objects.unlink(obj)
        except Exception:  # noqa: BLE001
            pass


def _ensure_text_object(scene, work, page, page_index: int, item_key: str, item, text: str) -> bpy.types.Object:
    page_id = str(getattr(page, "id", "") or "")
    owner_id = f"{page_id}:{item_key}"
    obj_name = f"{WORK_INFO_TEXT_PREFIX}{page_id}_{item_key}"
    data_name = f"{obj_name}_curve"
    curve = bpy.data.curves.get(data_name)
    if curve is None:
        curve = bpy.data.curves.new(data_name, type="FONT")
    curve.body = text
    curve.size = mm_to_m(q_to_mm(float(getattr(item, "font_size_q", 20.0) or 20.0)))
    _assign_default_font(curve)
    obj = bpy.data.objects.get(obj_name)
    if obj is not None and obj.type != "FONT":
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
        obj = None
    if obj is None:
        obj = bpy.data.objects.new(obj_name, curve)
    elif obj.data is not curve:
        obj.data = curve
    rect = overlay_shared.compute_paper_rects(work.paper).bleed
    x_mm, y_mm, align_x, align_y = _anchor(rect, str(getattr(item, "position", "bottom-left") or "bottom-left"))
    curve.align_x = align_x
    try:
        curve.align_y = align_y
    except Exception:  # noqa: BLE001
        pass
    mat = _material(owner_id.replace(":", "_"), getattr(item, "color", (0.0, 0.0, 0.0, 1.0)))
    if not curve.materials:
        curve.materials.append(mat)
    elif curve.materials[0] is not mat:
        curve.materials[0] = mat
    obj[PROP_WORK_INFO_KIND] = "work_info_text"
    obj[PROP_WORK_INFO_OWNER_ID] = owner_id
    obj[on.PROP_MANAGED] = False
    obj.hide_select = True
    obj.hide_viewport = False
    obj.hide_render = False
    _set_page_location(obj, scene, work, page_index, x_mm, y_mm)
    _link_to_page(obj, scene, page)
    return obj


def regenerate_all_work_info_texts(scene, work) -> int:
    """作品情報・ページ番号の実体テキストを再生成する."""
    if scene is None or work is None or not bool(getattr(work, "loaded", False)):
        return 0
    info = getattr(work, "work_info", None)
    if info is None:
        return 0
    valid: set[str] = set()
    count = 0
    for page_index, page in enumerate(getattr(work, "pages", []) or []):
        page_id = str(getattr(page, "id", "") or "")
        if not page_id or not page_range.page_in_range(page):
            continue
        for item_key, item, text in _text_items(info, page_index):
            owner_id = f"{page_id}:{item_key}"
            if item is None or not bool(getattr(item, "enabled", False)) or not text:
                continue
            valid.add(owner_id)
            _ensure_text_object(scene, work, page, page_index, item_key, item, text)
            count += 1
    for obj in list(bpy.data.objects):
        if obj.get(PROP_WORK_INFO_KIND) != "work_info_text":
            continue
        if str(obj.get(PROP_WORK_INFO_OWNER_ID, "") or "") in valid:
            continue
        data = obj.data
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
        if data is not None and getattr(data, "users", 0) == 0:
            try:
                bpy.data.curves.remove(data)
            except Exception:  # noqa: BLE001
                pass
    return count
