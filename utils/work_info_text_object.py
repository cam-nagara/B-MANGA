"""作品情報・ページ番号のテキストオブジェクト同期."""

from __future__ import annotations

import math

import bpy

from ..ui import overlay_shared
from . import log, object_naming as on, outliner_model as om, page_range, text_style
from .geom import mm_to_m, q_to_mm

_logger = log.get_logger(__name__)

WORK_INFO_TEXT_PREFIX = "work_info_text_"
WORK_INFO_MATERIAL_PREFIX = "BManga_WorkInfoText_"
PROP_WORK_INFO_KIND = "bmanga_work_info_text_kind"
PROP_WORK_INFO_OWNER_ID = "bmanga_work_info_text_owner_id"
PROP_WORK_INFO_SIGNATURE = "bmanga_work_info_text_signature"
TEXT_Z_M = 0.032
# Blender の FONT オブジェクトの ``size`` は文字の外枠そのものではなく
# フォント内部の em サイズとして扱われる。Yu Gothic など日本語フォントでは
# 20Q(5mm) をそのまま入れると見える字面が約 2.8mm になり小さすぎるため、
# 作品情報の実体テキストでは UI 上の Q 数が原稿上の見た目に近くなるよう補正する。
TEXT_OBJECT_Q_VISIBLE_HEIGHT_COMPENSATION = 1.78


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
    try:
        mat.update_tag()
    except Exception:  # noqa: BLE001
        pass
    return mat


def _work_info_font_path(work=None) -> str:
    preferred = ""
    if work is not None:
        info = getattr(work, "work_info", None)
        if info is not None:
            preferred = str(getattr(info, "font", "") or "")
    return text_style.resolve_font_path(preferred)


def _assign_default_font(curve: bpy.types.Curve, work=None) -> None:
    try:
        font_path = _work_info_font_path(work)
        if font_path:
            curve.font = bpy.data.fonts.load(font_path, check_existing=True)
    except Exception:  # noqa: BLE001
        pass


def _text_items(info, page_index: int, paper=None, page_entry=None) -> list[tuple[str, object, str]]:
    page_text = ""
    try:
        if paper is not None and page_entry is not None:
            from ..core.paper import format_page_entry_display_label
            page_text = format_page_entry_display_label(paper, page_entry)
        else:
            page_number = int(info.page_number_start) + int(page_index)
            if paper is not None:
                from ..core.paper import format_page_display_label
                page_text = format_page_display_label(paper, page_number)
            else:
                page_text = f"ページ{page_number:04d}"
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


def _anchor_rect(work):
    return overlay_shared.compute_paper_rects(work.paper).bleed


def _is_coma_mode(scene) -> bool:
    try:
        from . import page_file_scene
        role, _pid, _cid = page_file_scene.current_role(bpy.context)
        return role == page_file_scene.ROLE_COMA
    except Exception:  # noqa: BLE001
        return False


def _coma_origin_mm(scene, work):
    """コマモード時の現在ページの中心座標 (mm) を返す."""
    try:
        from . import page_file_scene, page_grid as _pg

        _role, page_id, _cid = page_file_scene.current_role(bpy.context)
        cw = max(1.0, float(getattr(work.paper, "canvas_width_mm", 1.0) or 1.0))
        ch = max(1.0, float(getattr(work.paper, "canvas_height_mm", 1.0) or 1.0))
        cols = max(1, int(getattr(scene, "bmanga_overview_cols", 4) or 4))
        gap_x, gap_y = _pg.resolve_gap_mm(scene)
        start_side = getattr(work.paper, "start_side", "right")
        read_direction = getattr(work.paper, "read_direction", "left")
        for i, p in enumerate(getattr(work, "pages", []) or []):
            if str(getattr(p, "id", "") or "") == page_id:
                ox, oy = _pg.page_grid_offset_mm(
                    i, cols, gap_x, cw, ch, start_side, read_direction,
                    work=work, gap_y_mm=gap_y,
                )
                add_x, add_y = _pg.page_manual_offset_mm(p)
                pw = _pg.page_content_width_mm(work, i, cw)
                return (ox + add_x + pw * 0.5, oy + add_y + ch * 0.5)
    except Exception:  # noqa: BLE001
        pass
    return None


def _set_page_location(obj: bpy.types.Object, scene, work, page_index: int, x_mm: float, y_mm: float) -> None:
    from . import page_grid

    ox, oy = page_grid.page_total_offset_mm(work, scene, page_index)
    abs_x = ox + x_mm
    abs_y = oy + y_mm

    if _is_coma_mode(scene):
        origin = _coma_origin_mm(scene, work)
        if origin is not None:
            org_x, org_y = origin
            x_m = mm_to_m(abs_x - org_x)
            z_m = mm_to_m(abs_y - org_y)
            loc = obj.location
            if abs(float(loc.x) - x_m) > 1.0e-9 or abs(float(loc.z) - z_m) > 1.0e-9 or abs(float(loc.y) - TEXT_Z_M) > 1.0e-9:
                obj.location = (x_m, TEXT_Z_M, z_m)
            obj.rotation_euler = (math.radians(90.0), 0.0, 0.0)
            return

    x_m = mm_to_m(abs_x)
    y_m = mm_to_m(abs_y)
    loc = obj.location
    rot = obj.rotation_euler
    needs_move = (
        abs(float(loc.x) - x_m) > 1.0e-9
        or abs(float(loc.y) - y_m) > 1.0e-9
        or abs(float(loc.z) - TEXT_Z_M) > 1.0e-9
    )
    needs_rot_reset = (abs(float(rot.x)) > 1.0e-6 or abs(float(rot.y)) > 1.0e-6 or abs(float(rot.z)) > 1.0e-6)
    if not needs_move and not needs_rot_reset:
        return
    obj.location = (x_m, y_m, TEXT_Z_M)
    if needs_rot_reset:
        obj.rotation_euler = (0.0, 0.0, 0.0)


def _link_to_work_info_collection(obj: bpy.types.Object, scene) -> None:
    coll = om.ensure_work_info_collection(scene)
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
    q_size = max(0.1, float(getattr(item, "font_size_q", 20.0) or 20.0))
    curve.size = mm_to_m(q_to_mm(q_size) * TEXT_OBJECT_Q_VISIBLE_HEIGHT_COMPENSATION)
    _assign_default_font(curve, work)
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
    rect = _anchor_rect(work)
    x_mm, y_mm, align_x, align_y = _anchor(rect, str(getattr(item, "position", "bottom-left") or "bottom-left"))
    curve.align_x = align_x
    try:
        curve.align_y = align_y
    except Exception:  # noqa: BLE001
        pass
    mat = _material(owner_id.replace(":", "_"), getattr(item, "color", (1.0, 1.0, 1.0, 1.0)))
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
    _link_to_work_info_collection(obj, scene)
    return obj


def _item_signature(page_id: str, page_index: int, item_key: str, item, text: str) -> str:
    try:
        color = tuple(round(float(c), 6) for c in getattr(item, "color", (1.0, 1.0, 1.0, 1.0))[:4])
    except Exception:  # noqa: BLE001
        color = (1.0, 1.0, 1.0, 1.0)
    return repr((
        page_id,
        int(page_index),
        str(item_key),
        str(text or ""),
        bool(getattr(item, "enabled", False)),
        str(getattr(item, "position", "bottom-left") or "bottom-left"),
        round(float(getattr(item, "font_size_q", 20.0) or 20.0), 6),
        color,
    ))


def _text_anchor_for_item(work, item) -> tuple[float, float]:
    rect = _anchor_rect(work)
    x_mm, y_mm, _align_x, _align_y = _anchor(
        rect,
        str(getattr(item, "position", "bottom-left") or "bottom-left"),
    )
    return x_mm, y_mm


def _remove_stale_work_info_texts(valid: set[str]) -> None:
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


def regenerate_all_work_info_texts(scene, work) -> int:
    """作品情報・ページ番号の実体テキストを再生成する."""
    if scene is None or work is None or not bool(getattr(work, "loaded", False)):
        return 0
    from ..core.mode import MODE_COMA, get_mode
    if get_mode() == MODE_COMA:
        return 0
    info = getattr(work, "work_info", None)
    if info is None:
        return 0
    master_visible = bool(getattr(info, "display_visible", True))
    valid: set[str] = set()
    count = 0
    for page_index, page in enumerate(getattr(work, "pages", []) or []):
        page_id = str(getattr(page, "id", "") or "")
        if not page_id or not page_range.page_in_range(page):
            continue
        paper = getattr(work, "paper", None)
        for item_key, item, text in _text_items(info, page_index, paper, page_entry=page):
            owner_id = f"{page_id}:{item_key}"
            if not master_visible or item is None or not bool(getattr(item, "enabled", False)) or not text:
                continue
            valid.add(owner_id)
            obj = _ensure_text_object(scene, work, page, page_index, item_key, item, text)
            try:
                obj[PROP_WORK_INFO_SIGNATURE] = _item_signature(page_id, page_index, item_key, item, text)
            except Exception:  # noqa: BLE001
                pass
            count += 1
    _remove_stale_work_info_texts(valid)
    return count


def sync_work_info_texts_after_page_transform(scene, work) -> int:
    """ページ位置だけの変更時、作品情報テキストを再作成せず位置だけ追従させる."""
    if scene is None or work is None or not bool(getattr(work, "loaded", False)):
        return 0
    info = getattr(work, "work_info", None)
    if info is None:
        return 0
    master_visible = bool(getattr(info, "display_visible", True))
    valid: set[str] = set()
    count = 0
    for page_index, page in enumerate(getattr(work, "pages", []) or []):
        page_id = str(getattr(page, "id", "") or "")
        if not page_id or not page_range.page_in_range(page):
            continue
        paper = getattr(work, "paper", None)
        for item_key, item, text in _text_items(info, page_index, paper, page_entry=page):
            owner_id = f"{page_id}:{item_key}"
            if not master_visible or item is None or not bool(getattr(item, "enabled", False)) or not text:
                continue
            valid.add(owner_id)
            signature = _item_signature(page_id, page_index, item_key, item, text)
            obj = bpy.data.objects.get(f"{WORK_INFO_TEXT_PREFIX}{page_id}_{item_key}")
            if obj is None or str(obj.get(PROP_WORK_INFO_SIGNATURE, "") or "") != signature:
                obj = _ensure_text_object(scene, work, page, page_index, item_key, item, text)
                try:
                    obj[PROP_WORK_INFO_SIGNATURE] = signature
                except Exception:  # noqa: BLE001
                    pass
                count += 1
                continue
            x_mm, y_mm = _text_anchor_for_item(work, item)
            _set_page_location(obj, scene, work, page_index, x_mm, y_mm)
            count += 1
    _remove_stale_work_info_texts(valid)
    return count
