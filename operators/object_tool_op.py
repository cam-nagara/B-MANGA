"""B-MANGA object tool for viewport selection, moving and box resizing."""

from __future__ import annotations

import json
import time

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..ui import reparent_overlay
from ..utils import (
    balloon_curve_object,
    edge_selection,
    free_transform,
    gp_layer_parenting as gp_parent,
    layer_reparent,
    layer_stack as layer_stack_utils,
    log,
    object_selection,
)

_logger = log.get_logger(__name__)
from ..utils.geom import Rect
from ..utils.layer_hierarchy import OUTSIDE_STACK_KEY, outside_child_key
from .alt_reparent_op import (
    _DRAG_THRESHOLD_PX as _REPARENT_DRAG_PX,
    _has_selected_targets as _reparent_has_targets,
    _selected_count as _reparent_count,
    _set_confirm_for_target as _reparent_set_confirm,
    _set_error_for_target as _reparent_set_error,
    _set_overlay_for_target as _reparent_set_overlay,
)
from . import (
    balloon_op,
    object_tool_free_transform,
    object_tool_balloon_tail,
    effect_line_op,
    layer_move_session,
    coma_edge_drag_session,
    layer_move_op,
    coma_edge_move_op,
    coma_modal_state,
    coma_picker,
    object_tool_selection,
    raster_layer_op,
    selection_context_menu,
    text_op,
    view_event_region,
)

_DRAG_EPS_MM = 0.05
_EDGE_PICK_WORLD_TOLERANCE_MIN_MM = 3.0
_EDGE_PICK_WORLD_TOLERANCE_MAX_MM = 12.0
_DOUBLE_CLICK_INTERVAL_SEC = 0.4
_DOUBLE_CLICK_DISTANCE_PX = 8.0
_ARROW_KEYS = {"UP_ARROW", "DOWN_ARROW", "LEFT_ARROW", "RIGHT_ARROW"}
# 選択中フォールバック (_selected_move_hit_from_event) のヒット余白。
# ハンドルは本体境界の外側 SELECTION_HANDLE_OUTSET_MM (3mm) に描画され、
# ハンドル四角自体も幅2mm (半径1mm) あるため、境界からの最大到達距離は
# 3+1=4mm。他の判定 (_balloon_hit_part 等) が threshold 分の余裕を
# 持たせているのに合わせ、ここでも同程度の余裕を加える。
_SELECTED_MOVE_HIT_PAD_MM = object_tool_selection.SELECTION_HANDLE_OUTSET_MM + 2.5
_NUDGE_PX = 1.0
_NUDGE_SHIFT_PX = 20.0


def _focus_parent_coma_for_entry(context, work, page_index: int, page, entry) -> None:
    """エントリの parent_key からコマを特定し、そのコマを active にする."""
    if page is None or entry is None or work is None:
        return
    parent_key = str(getattr(entry, "parent_key", "") or "")
    if not parent_key:
        return
    parts = parent_key.split(":", 1)
    if len(parts) < 2 or not parts[1]:
        return
    coma_id = parts[1]
    for ci, panel in enumerate(getattr(page, "comas", []) or []):
        if _coma_identity(panel) == coma_id:
            from ..utils import active_target as _at
            _at.focus_active_coma(context.scene, work, page_index, ci)
            return


def _focus_parent_coma_for_entry_by_key(context, work, entry) -> None:
    """エントリの parent_key からページとコマを特定し、active にする."""
    if entry is None or work is None:
        return
    parent_key = str(getattr(entry, "parent_key", "") or "")
    if not parent_key:
        return
    parts = parent_key.split(":", 1)
    if not parts[0] or parts[0] == OUTSIDE_STACK_KEY:
        return
    page_id = parts[0]
    page_index, page = _find_page_by_id(work, page_id)
    if page is None:
        return
    if len(parts) < 2 or not parts[1]:
        return
    coma_id = parts[1]
    for ci, panel in enumerate(getattr(page, "comas", []) or []):
        if _coma_identity(panel) == coma_id:
            from ..utils import active_target as _at
            _at.focus_active_coma(context.scene, work, page_index, ci)
            return


def _focus_parent_coma_for_gp_layer(context, work, layer) -> None:
    """GPレイヤーの parent_key からコマを特定し、そのコマを active にする."""
    if layer is None or work is None:
        return
    pkey = gp_parent.parent_key(layer)
    if not pkey:
        return
    parts = pkey.split(":", 1)
    if len(parts) < 2 or not parts[1]:
        return
    page_id = parts[0]
    coma_id = parts[1]
    page_index, page = _find_page_by_id(work, page_id)
    if page is None:
        return
    for ci, panel in enumerate(getattr(page, "comas", []) or []):
        if _coma_identity(panel) == coma_id:
            from ..utils import active_target as _at
            _at.focus_active_coma(context.scene, work, page_index, ci)
            return


def _find_page_by_id(work, page_id: str):
    if work is None:
        return -1, None
    for i, page in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(page, "id", "") or "") == str(page_id or ""):
            return i, page
    return -1, None


def _coma_identity(panel) -> str:
    return str(getattr(panel, "coma_id", "") or getattr(panel, "id", "") or "")


def _find_coma_by_key(work, page_id: str, coma_id: str):
    page_index, page = _find_page_by_id(work, page_id)
    if page is None:
        return -1, None, -1, None
    for i, panel in enumerate(getattr(page, "comas", []) or []):
        if _coma_identity(panel) == str(coma_id or ""):
            return page_index, page, i, panel
    return page_index, page, -1, None


def _find_balloon_by_key(work, page_id: str, item_id: str):
    if str(page_id or "") == OUTSIDE_STACK_KEY:
        if work is None:
            return -1, None, -1, None
        for i, entry in enumerate(getattr(work, "shared_balloons", []) or []):
            if str(getattr(entry, "id", "") or "") == str(item_id or ""):
                return -1, None, i, entry
        return -1, None, -1, None
    page_index, page = _find_page_by_id(work, page_id)
    if page is None:
        return -1, None, -1, None
    for i, entry in enumerate(getattr(page, "balloons", []) or []):
        if str(getattr(entry, "id", "") or "") == str(item_id or ""):
            return page_index, page, i, entry
    return page_index, page, -1, None


def _find_text_by_key(work, page_id: str, item_id: str):
    page_index, page = _find_page_by_id(work, page_id)
    if page is None:
        return -1, None, -1, None
    for i, entry in enumerate(getattr(page, "texts", []) or []):
        if str(getattr(entry, "id", "") or "") == str(item_id or ""):
            return page_index, page, i, entry
    return page_index, page, -1, None


def _find_image_by_key(context, item_id: str):
    return object_tool_selection.find_image_by_key(context, item_id)


def _find_image_path_by_key(context, item_id: str):
    return object_tool_selection.find_image_path_by_key(context, item_id)


def _find_raster_by_key(context, item_id: str):
    return object_tool_selection.find_raster_by_key(context, item_id)


def _find_fill_by_key(context, item_id: str):
    return object_tool_selection.find_fill_by_key(context, item_id)


def _find_gp_layer(key: str):
    return object_tool_selection.find_gp_layer(key)


def _find_effect_layer(key: str):
    return object_tool_selection.find_effect_layer(key)


def _event_world_xy_mm(context, event) -> tuple[float | None, float | None]:
    return effect_line_op._event_world_xy_mm(context, event)


def _selection_mode(event) -> str:
    if bool(getattr(event, "ctrl", False)):
        return "toggle"
    if bool(getattr(event, "shift", False)):
        return "add"
    return "single"


def _point_segment_distance_mm(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return ((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq))
    nearest = (start[0] + dx * t, start[1] + dy * t)
    return ((point[0] - nearest[0]) ** 2 + (point[1] - nearest[1]) ** 2) ** 0.5


def _edge_pick_world_tolerance_mm(region, rv3d, mx: float, my: float, px_tolerance: float) -> float:
    here = coma_edge_move_op._region_to_world_mm(region, rv3d, mx, my)
    side = coma_edge_move_op._region_to_world_mm(region, rv3d, mx + 1.0, my)
    if here is None or side is None:
        return _EDGE_PICK_WORLD_TOLERANCE_MAX_MM
    mm_per_px = ((side[0] - here[0]) ** 2 + (side[1] - here[1]) ** 2) ** 0.5
    return min(
        max(_EDGE_PICK_WORLD_TOLERANCE_MIN_MM, mm_per_px * float(px_tolerance) * 1.5),
        _EDGE_PICK_WORLD_TOLERANCE_MAX_MM,
    )


def _edge_hit_close_in_world(context, work, edge_hit: dict, area, region, rv3d, mx: float, my: float) -> bool:
    point = coma_edge_move_op._region_to_world_mm(region, rv3d, mx, my)
    if point is None:
        return True
    page_index = int(edge_hit.get("page", -1))
    coma_index = int(edge_hit.get("coma", -1))
    if not (0 <= page_index < len(work.pages)):
        return False
    page = work.pages[page_index]
    if not (0 <= coma_index < len(page.comas)):
        return False
    poly = coma_edge_move_op._coma_polygon(page.comas[coma_index])
    if len(poly) < 2:
        return False
    ox, oy = coma_edge_move_op._page_offset_for_area(context, work, area, page_index)
    world_poly = [(float(x) + ox, float(y) + oy) for x, y in poly]
    if edge_hit.get("type") == "vertex":
        vertex_index = int(edge_hit.get("vertex", -1))
        if not (0 <= vertex_index < len(world_poly)):
            return False
        vertex = world_poly[vertex_index]
        dist = ((point[0] - vertex[0]) ** 2 + (point[1] - vertex[1]) ** 2) ** 0.5
        tolerance = _edge_pick_world_tolerance_mm(region, rv3d, mx, my, coma_edge_move_op.VERTEX_PICK_TOLERANCE_PX)
        return dist <= tolerance
    edge_index = int(edge_hit.get("edge", -1))
    if not (0 <= edge_index < len(world_poly)):
        return False
    start = world_poly[edge_index]
    end = world_poly[(edge_index + 1) % len(world_poly)]
    dist = _point_segment_distance_mm(point, start, end)
    tolerance = _edge_pick_world_tolerance_mm(region, rv3d, mx, my, coma_edge_move_op.EDGE_PICK_TOLERANCE_PX)
    return dist <= tolerance


def _pick_selected_coma_edge_or_vertex(
    context, work, area, region, rv3d, mx: int, my: int,
) -> dict | None:
    """選択中/アクティブなコマの辺/頂点をヒットテストする (表示位置で判定)."""
    selected_keys = set()
    for k in object_selection.get_keys(context):
        if object_selection.parse_key(k)[0] == "coma":
            selected_keys.add(k)
    ak = active_selection_key(context)
    if ak and object_selection.parse_key(ak)[0] == "coma":
        selected_keys.add(ak)
    if not selected_keys:
        return None
    edge_hit = coma_edge_move_op.pick_selected_coma_edge_or_vertex(
        work, region, rv3d, mx, my,
        context=context, area=area,
        selected_keys=selected_keys,
    )
    if edge_hit is None:
        return None
    page_index = int(edge_hit["page"])
    coma_index = int(edge_hit["coma"])
    if page_index < 0 or page_index >= len(work.pages):
        return None
    page = work.pages[page_index]
    comas = list(getattr(page, "comas", []) or [])
    if coma_index < 0 or coma_index >= len(comas):
        return None
    panel = comas[coma_index]
    key = object_selection.coma_key(page, panel)
    kind = "coma_vertex" if edge_hit.get("type") == "vertex" else "coma_edge"
    hit = dict(edge_hit)
    hit.update({
        "kind": kind,
        "key": key,
        "area": area,
        "region": region,
        "rv3d": rv3d,
    })
    return hit


def hit_object_at_event(context, event) -> dict | None:
    """Return the selectable B-MANGA object under a viewport event."""
    work = get_work(context)
    if work is None:
        return None
    view = view_event_region.view3d_window_under_event(context, event)
    if view is None:
        return None
    area, region, rv3d, mx, my = view
    world_x_mm, world_y_mm = _event_world_xy_mm(context, event)

    def _hit_visible(hit: dict | None) -> bool:
        if hit is None:
            return False
        if world_x_mm is None or world_y_mm is None:
            return True
        part = str(hit.get("part", "") or "")
        if part and part not in {"body", "move"}:
            return True
        key = str(hit.get("key", "") or "")
        if key and object_selection.is_selected(context, key):
            return True
        return object_tool_selection.hit_visible_at_world(context, hit, world_x_mm, world_y_mm)

    transformed = object_tool_free_transform.hit_transformed_handle_at_event(context, event, _event_world_xy_mm)
    if _hit_visible(transformed):
        return transformed
    # 選択中/アクティブなコマの辺/頂点はレイヤーオブジェクトより先にチェック
    # （ハンドルが描画されているのに背面オブジェクトに負けるのを防ぐ）
    active_coma_edge = _pick_selected_coma_edge_or_vertex(
        context, work, area, region, rv3d, int(mx), int(my),
    )
    if active_coma_edge is not None:
        return active_coma_edge
    for resolver in (
        _hit_gradient_handle_at_event,
        _hit_text_at_event,
        _hit_shared_text_at_event,
        _hit_balloon_at_event,
        _hit_shared_balloon_at_event,
        _hit_effect_at_event,
        _hit_image_at_event,
        _hit_image_path_at_event,
        _hit_gp_at_event,
        _hit_raster_at_event,
        _hit_fill_at_event,
    ):
        hit = resolver(context, event)
        if _hit_visible(hit):
            return hit
    edge_hit = coma_edge_move_op._pick_edge_or_vertex(
        work,
        region,
        rv3d,
        int(mx),
        int(my),
        context=context,
        area=area,
    )
    if edge_hit is not None and not _edge_hit_close_in_world(context, work, edge_hit, area, region, rv3d, mx, my):
        edge_hit = None
    if edge_hit is not None:
        page = work.pages[int(edge_hit["page"])]
        panel = page.comas[int(edge_hit["coma"])]
        kind = "coma_vertex" if edge_hit.get("type") == "vertex" else "coma_edge"
        hit = dict(edge_hit)
        hit.update({
            "kind": kind,
            "key": object_selection.coma_key(page, panel),
            "area": area,
            "region": region,
            "rv3d": rv3d,
        })
        return hit
    for resolver in (
        _hit_coma_at_event,
        _hit_page_at_event,
    ):
        hit = resolver(context, event)
        if hit is not None:
            return hit
    return None


def _hit_text_at_event(context, event) -> dict | None:
    work, page, lx, ly, hit_index, hit_entry, hit_part, _can_create = (
        text_op._resolve_text_hit_from_event(context, event)
    )
    if work is None or page is None or hit_entry is None or hit_index < 0 or lx is None or ly is None:
        return None
    return {
        "kind": "text",
        "page_id": getattr(page, "id", ""),
        "index": hit_index,
        "part": "move",
        "key": object_selection.text_key(page, hit_entry),
        "local": (float(lx), float(ly)),
    }


def _hit_shared_text_at_event(context, event) -> dict | None:
    return object_tool_selection.hit_shared_text_at_event(context, event, _event_world_xy_mm)


def _hit_balloon_at_event(context, event) -> dict | None:
    work, page, lx, ly = balloon_op._resolve_page_from_event(context, event)
    if work is None or page is None or lx is None or ly is None:
        return None
    hit_index, hit_entry, hit_part = balloon_op._hit_balloon_entry(page, lx, ly)
    if hit_entry is None or hit_index < 0:
        return None
    return {
        "kind": "balloon",
        "page_id": getattr(page, "id", ""),
        "index": hit_index,
        "part": "move" if hit_part == "body" else hit_part,
        "key": object_selection.balloon_key(page, hit_entry),
        "local": (float(lx), float(ly)),
    }


def _hit_shared_balloon_at_event(context, event) -> dict | None:
    return object_tool_selection.hit_shared_balloon_at_event(context, event, _event_world_xy_mm)


def _hit_effect_at_event(context, event) -> dict | None:
    x_mm, y_mm = _event_world_xy_mm(context, event)
    if x_mm is None or y_mm is None:
        return None
    obj, layer, bounds, part = effect_line_op._hit_effect_layer(context, x_mm, y_mm)
    if obj is None or layer is None or bounds is None:
        return None
    return {
        "kind": "effect",
        "layer_name": object_selection.parse_key(object_selection.effect_key(layer))[2],
        "part": "move" if part == "body" else part,
        "key": object_selection.effect_key(layer),
        "world": (float(x_mm), float(y_mm)),
    }


def _hit_image_at_event(context, event) -> dict | None:
    return object_tool_selection.hit_image_at_event(context, event, _event_world_xy_mm)


def _hit_image_path_at_event(context, event) -> dict | None:
    return object_tool_selection.hit_image_path_at_event(context, event, _event_world_xy_mm)


def _hit_gp_at_event(context, event) -> dict | None:
    return object_tool_selection.hit_gp_at_event(context, event, _event_world_xy_mm)


def _hit_raster_at_event(context, event) -> dict | None:
    return object_tool_selection.hit_raster_at_event(context, event, _event_world_xy_mm)


def _hit_fill_at_event(context, event) -> dict | None:
    return object_tool_selection.hit_fill_at_event(context, event, _event_world_xy_mm)


_HANDLE_HIT_RADIUS_MM = 5.0


def _hit_gradient_handle_at_event(context, event) -> dict | None:
    from ..utils.fill_real_object import GRADIENT_HANDLE_KIND, find_fill_entry
    from ..utils import object_naming as _on

    x_mm, y_mm = _event_world_xy_mm(context, event)
    if x_mm is None or y_mm is None:
        return None
    from ..utils.geom import m_to_mm

    best = None
    best_dist = _HANDLE_HIT_RADIUS_MM
    for obj in bpy.data.objects:
        if obj.get(_on.PROP_KIND) != GRADIENT_HANDLE_KIND:
            continue
        if obj.hide_viewport:
            continue
        hx = m_to_mm(obj.location.x)
        hy = m_to_mm(obj.location.y)
        dist = ((hx - x_mm) ** 2 + (hy - y_mm) ** 2) ** 0.5
        if dist < best_dist:
            best_dist = dist
            fill_id = str(obj.get(_on.PROP_ID, "") or "")
            end_tag = str(obj.get("bmanga_handle_end", "") or "")
            best = {
                "kind": "gradient_handle",
                "fill_id": fill_id,
                "end": end_tag,
                "part": "move",
                "key": object_selection.gradient_handle_key(fill_id, end_tag),
            }
    return best


def selection_bounds_for_key(context, key: str) -> Rect | None:
    return object_tool_selection.selection_bounds_for_key(context, key)


def active_selection_key(context) -> str:
    return object_tool_selection.active_selection_key(context)


def _hit_coma_at_event(context, event) -> dict | None:
    work = get_work(context)
    panel_hit = coma_picker.find_coma_at_event(context, event)
    if work is None or panel_hit is None:
        return None
    page_index, coma_index = panel_hit
    page = work.pages[page_index]
    panel = page.comas[coma_index]
    return {
        "kind": "coma",
        "page": page_index,
        "coma": coma_index,
        "part": "body",
        "key": object_selection.coma_key(page, panel),
    }


def _hit_page_at_event(context, event) -> dict | None:
    work = get_work(context)
    page_index = coma_picker.find_page_at_event(context, event)
    if work is None or page_index is None or not (0 <= page_index < len(work.pages)):
        return None
    page = work.pages[page_index]
    return {
        "kind": "page",
        "page": page_index,
        "part": "body",
        "key": object_selection.page_key(page),
    }


def _select_stack_target(context, kind: str, key: str) -> bool:
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None:
        return False
    uid = layer_stack_utils.target_uid(kind, key)
    for i, item in enumerate(stack):
        if layer_stack_utils.stack_item_uid(item) == uid:
            return bool(layer_stack_utils.select_stack_index(context, i))
    return False


def activate_hit(context, hit: dict, *, mode: str) -> None:
    """Activate a hit object in the same way as the object tool selection path."""
    work = get_work(context)
    if work is None:
        return
    kind = hit["kind"]
    key = str(hit.get("key", "") or "")
    # 「自由変形」モードは対象以外を選択した時点で終了する
    if key != object_tool_free_transform.mode_key(context):
        object_tool_free_transform.clear_mode(context)
    if kind == "page":
        page_index = int(hit["page"])
        if 0 <= page_index < len(work.pages):
            from ..utils import active_target as _at

            _at.focus_active_page(context.scene, work, page_index)
            _select_stack_target(context, "page", getattr(work.pages[page_index], "id", ""))
        edge_selection.clear_selection(context)
    elif kind in {"coma", "coma_edge", "coma_vertex"}:
        page_index = int(hit["page"])
        coma_index = int(hit["coma"])
        if page_index < 0:
            _select_stack_target(context, "coma", outside_child_key(object_selection.parse_key(key)[2]))
            edge_selection.clear_selection(context)
            object_selection.select_key(context, key, mode=mode)
            object_tool_selection.sync_outliner_selection_for_keys(
                context,
                object_selection.get_keys(context),
            )
            return
        page = work.pages[page_index]
        # 新規レイヤー追加 (resolve_active_target) もこのコマを active として
        # 扱えるよう、active_page_index / active_coma_index に加えて
        # bmanga_current_coma_id も同期する
        from ..utils import active_target as _at

        _at.focus_active_coma(context.scene, work, page_index, coma_index)
        if kind == "coma_edge":
            edge_selection.set_selection(
                context,
                "edge",
                page_index=page_index,
                coma_index=coma_index,
                edge_index=int(hit.get("edge", -1)),
            )
        elif kind == "coma_vertex":
            vertex_mode = "toggle" if mode == "toggle" else "add" if mode == "add" else "single"
            edge_selection.set_vertex_selection(
                context,
                page_index=page_index,
                coma_index=coma_index,
                vertex_index=int(hit.get("vertex", -1)),
                mode=vertex_mode,
            )
        else:
            edge_selection.set_selection(
                context,
                "border",
                page_index=page_index,
                coma_index=coma_index,
            )
    elif kind == "balloon":
        page_index, page = _find_page_by_id(work, hit.get("page_id", ""))
        if page is None and hit.get("page_id", "") == OUTSIDE_STACK_KEY:
            _select_stack_target(context, "balloon", outside_child_key(object_selection.parse_key(key)[2]))
            balloon_op._select_balloon_index(
                context,
                work,
                None,
                int(hit.get("index", -1)),
                mode=mode,
            )
        elif page is not None:
            balloon_index = int(hit.get("index", -1))
            balloons = getattr(page, "balloons", None)
            if balloons is not None and 0 <= balloon_index < len(balloons):
                _focus_parent_coma_for_entry(context, work, page_index, page, balloons[balloon_index])
            balloon_op._select_balloon_index(
                context,
                work,
                page,
                balloon_index,
                mode=mode,
            )
            work.active_page_index = page_index
        edge_selection.clear_selection(context)
    elif kind == "text":
        page_index, page = _find_page_by_id(work, hit.get("page_id", ""))
        if page is None and hit.get("page_id", "") == OUTSIDE_STACK_KEY:
            _select_stack_target(context, "text", outside_child_key(object_selection.parse_key(key)[2]))
        elif page is not None:
            text_index = int(hit.get("index", -1))
            texts = getattr(page, "texts", None)
            if texts is not None and 0 <= text_index < len(texts):
                _focus_parent_coma_for_entry(context, work, page_index, page, texts[text_index])
            text_op._select_text_index(context, work, page, text_index)
        edge_selection.clear_selection(context)
    elif kind == "effect":
        obj, layer = _find_effect_layer(hit.get("layer_name", ""))
        if layer is not None:
            _focus_parent_coma_for_gp_layer(context, work, layer)
            effect_line_op._select_effect_layer(context, obj, layer)
        edge_selection.clear_selection(context)
    elif kind == "image":
        index, entry = _find_image_by_key(context, object_selection.parse_key(key)[2])
        if entry is not None:
            _focus_parent_coma_for_entry_by_key(context, work, entry)
            if not _select_stack_target(context, "image", getattr(entry, "id", "")):
                context.scene.bmanga_active_image_layer_index = index
                context.scene.bmanga_active_layer_kind = "image"
        edge_selection.clear_selection(context)
    elif kind == "image_path":
        index, entry = _find_image_path_by_key(context, object_selection.parse_key(key)[2])
        if entry is not None:
            _focus_parent_coma_for_entry_by_key(context, work, entry)
            if not _select_stack_target(context, "image_path", getattr(entry, "id", "")):
                context.scene.bmanga_active_image_path_layer_index = index
                context.scene.bmanga_active_layer_kind = "image_path"
        edge_selection.clear_selection(context)
    elif kind == "raster":
        index, entry = _find_raster_by_key(context, object_selection.parse_key(key)[2])
        if entry is not None:
            _focus_parent_coma_for_entry_by_key(context, work, entry)
            if not _select_stack_target(context, "raster", getattr(entry, "id", "")):
                context.scene.bmanga_active_raster_layer_index = index
                context.scene.bmanga_active_layer_kind = "raster"
        edge_selection.clear_selection(context)
    elif kind == "fill":
        index, entry = _find_fill_by_key(context, object_selection.parse_key(key)[2])
        if entry is not None:
            _focus_parent_coma_for_entry_by_key(context, work, entry)
            if not _select_stack_target(context, "fill", getattr(entry, "id", "")):
                context.scene.bmanga_active_fill_layer_index = index
                context.scene.bmanga_active_layer_kind = "fill"
        edge_selection.clear_selection(context)
    elif kind == "gp":
        obj, layer = _find_gp_layer(hit.get("layer_key", object_selection.parse_key(key)[2]))
        if layer is not None:
            _focus_parent_coma_for_gp_layer(context, work, layer)
            if not _select_stack_target(context, "gp", layer_stack_utils._node_stack_key(layer)):
                try:
                    context.view_layer.objects.active = obj
                    obj.select_set(True)
                    obj.data.layers.active = layer
                except Exception:  # noqa: BLE001
                    pass
                context.scene.bmanga_active_layer_kind = "gp"
        edge_selection.clear_selection(context)
    if kind == "gradient_handle":
        edge_selection.clear_selection(context)
    if kind != "balloon":
        object_selection.select_key(context, key, mode=mode)
    object_tool_selection.sync_outliner_selection_for_keys(
        context,
        object_selection.get_keys(context),
    )


def enter_coma_from_hit(context, hit: dict) -> bool:
    if str(hit.get("kind", "") or "") != "coma":
        return False
    activate_hit(context, hit, mode="single")
    try:
        # ダブルクリックは EXEC_DEFAULT 呼び出し (ウィンドウ/モーダル無し)。
        # 未作成コマのテンプレート選択ダイアログ (fileselect_add) は
        # この文脈では機能せず RUNNING_MODAL のまま何も開かない。
        # ダブルクリックでは確実にコマを開くため、プロンプトは抑止し
        # 既存 cNN.blend / 解決済みテンプレート / 空シーンから開く。
        result = bpy.ops.bmanga.enter_coma_mode(
            "EXEC_DEFAULT", prompt_template_if_missing=False
        )
    except Exception:  # noqa: BLE001
        return False
    return result in ({"FINISHED"}, {"RUNNING_MODAL"})


def _rect_resize_result(
    action: str,
    x: float,
    y: float,
    w: float,
    h: float,
    dx: float,
    dy: float,
    min_size: float,
) -> tuple[float, float, float, float]:
    if action == "move":
        return x + dx, y + dy, w, h
    right = x + w
    top = y + h
    new_left = x
    new_right = right
    new_bottom = y
    new_top = top
    if "left" in action:
        new_left = min(right - min_size, x + dx)
    if "right" in action:
        new_right = max(x + min_size, right + dx)
    if "bottom" in action:
        new_bottom = min(top - min_size, y + dy)
    if "top" in action:
        new_top = max(y + min_size, top + dy)
    return new_left, new_bottom, new_right - new_left, new_top - new_bottom


_CORNER_PARTS = {"top_left", "top_right", "bottom_left", "bottom_right"}


def _uniform_scale_result(
    action: str,
    x: float, y: float, w: float, h: float,
    dx: float, dy: float, min_size: float,
) -> tuple[float, float, float, float]:
    """コーナードラッグで縦横比を維持したまま拡大縮小する."""
    dir_x = w if "right" in action else -w
    dir_y = h if "top" in action else -h
    diag = (dir_x ** 2 + dir_y ** 2) ** 0.5
    if diag < 0.001:
        return x, y, w, h
    projected = (dx * dir_x + dy * dir_y) / diag
    factor = max(min_size / max(w, h, 0.001), 1.0 + projected / diag)
    new_w = max(min_size, w * factor)
    new_h = max(min_size, h * factor)
    new_x = x if "right" in action else x + w - new_w
    new_y = y if "top" in action else y + h - new_h
    return new_x, new_y, new_w, new_h


def _schedule_object_tool_relaunch(delay_seconds: float = 0.3) -> None:
    """取り消し処理などで一旦終了したオブジェクトツールを自動で再開する.

    timer から modal を起動するには 3D ビューのウィンドウ文脈が必要なため、
    temp_override で最初の 3D ビューを指して INVOKE する。
    """

    def _relaunch():
        try:
            from . import coma_modal_state as _state

            if _state.get_active("object_tool") is not None:
                return None
            wm = getattr(bpy.context, "window_manager", None)
            for window in getattr(wm, "windows", []) or []:
                screen = getattr(window, "screen", None)
                for area in getattr(screen, "areas", []) or []:
                    if area.type != "VIEW_3D":
                        continue
                    region = next((r for r in area.regions if r.type == "WINDOW"), None)
                    if region is None:
                        continue
                    with bpy.context.temp_override(window=window, area=area, region=region):
                        if bpy.ops.bmanga.object_tool.poll():
                            bpy.ops.bmanga.object_tool("INVOKE_DEFAULT")
                    return None
        except Exception:  # noqa: BLE001
            _logger.exception("object tool relaunch failed")
        return None

    try:
        bpy.app.timers.register(_relaunch, first_interval=max(0.05, float(delay_seconds)), persistent=True)
    except Exception:  # noqa: BLE001
        _logger.exception("object tool relaunch scheduling failed")


class BMANGA_OT_object_tool(Operator):
    bl_idname = "bmanga.object_tool"
    bl_label = "オブジェクトツール"
    bl_options = {"REGISTER"}

    _externally_finished: bool
    _cursor_modal_set: bool
    _dragging: bool
    _drag_action: str
    _drag_start_x: float
    _drag_start_y: float
    _drag_keys: list[str]
    _snapshots: list[dict]
    _drag_moved: bool
    _edge_drag: object | None
    _layer_drag: object | None
    _marquee_mode: str
    _marquee_start_x: float
    _marquee_start_y: float
    _marquee_current_x: float
    _marquee_current_y: float
    _center_snap_targets: list[tuple[float, float]]
    _original_center: tuple[float, float] | None
    _center_snap_armed: bool

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(work is not None and getattr(work, "loaded", False))

    def invoke(self, context, _event):
        # 心拍が止まった残骸 (Blender 側で modal が打ち切られた参照) は回収し、
        # ツールを選び直せば必ず操作可能な状態へ復帰できるようにする
        active = coma_modal_state.active_or_reclaim("object_tool")
        if active is not None:
            return {"FINISHED"}
        coma_modal_state.exit_drawing_mode(context)
        coma_modal_state.finish_all(context, except_tool="object_tool")
        self._externally_finished = False
        self._cursor_modal_set = coma_modal_state.set_modal_cursor(context, "DEFAULT")
        self._clear_drag_state()
        self._clear_click_state()
        self._ft_mode = False
        self._ft_snapshot = None
        self._ft_key = ""
        object_tool_balloon_tail.clear_pending(self)
        context.window_manager.modal_handler_add(self)
        coma_modal_state.set_active("object_tool", self, context)
        self.report({"INFO"}, "オブジェクトツール: クリックで選択、ドラッグで移動/リサイズ")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        coma_modal_state.mark_heartbeat(self)
        if getattr(self, "_externally_finished", False):
            coma_modal_state.clear_active("object_tool", self, context)
            return {"FINISHED", "PASS_THROUGH"}
        if view_event_region.toggle_modal_sidebar_if_requested(context, event):
            return {"RUNNING_MODAL"}
        # ▲ ハンドル hover ハイライト用にカーソル位置を WM に記録
        # (overlay_coma_selection.draw が読む)
        if event.type == "MOUSEMOVE":
            self._update_overlay_pointer(context, event)
        if getattr(self, "_dragging", False):
            return self._modal_dragging(context, event)
        if view_event_region.modal_navigation_ui_passthrough(self, context, event):
            return {"PASS_THROUGH"}
        if event.type == "RIGHTMOUSE" and event.value == "PRESS":
            if object_tool_balloon_tail.open_point_menu(context, event):
                return {"RUNNING_MODAL"}
            if selection_context_menu.open_for_object_tool(self, context, event):
                return {"RUNNING_MODAL"}
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        if event.type == "ESC" and event.value == "PRESS":
            if getattr(self, "_ft_mode", False):
                self._cancel_free_transform(context)
                return {"RUNNING_MODAL"}
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        if getattr(self, "_ft_mode", False):
            if event.value == "PRESS" and event.type in {"RET", "NUMPAD_ENTER"}:
                self._confirm_free_transform(context)
                return {"RUNNING_MODAL"}
            if event.type == "MOUSEMOVE":
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE":
                if not view_event_region.is_view3d_window_event(context, event):
                    return {"RUNNING_MODAL"}
                return self._handle_left_press(context, event)
            return {"RUNNING_MODAL"}
        if event.value == "PRESS" and event.type == "T" and event.ctrl and not event.alt:
            self._enter_free_transform(context)
            return {"RUNNING_MODAL"}
        if event.value == "PRESS" and event.type in {"P", "F", "K", "T"} and not event.ctrl and not event.alt:
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED", "PASS_THROUGH"}
        if event.value == "PRESS" and event.type in {"Z", "Y"} and event.ctrl:
            # 取り消し/やり直しを Blender に渡す。undo_post ハンドラが
            # モーダルの無効化と再起動を担当する。
            self._cleanup(context)
            self._externally_finished = True
            coma_modal_state.clear_active("object_tool", self, context)
            return {"FINISHED", "PASS_THROUGH"}
        if event.value == "PRESS" and event.type in _ARROW_KEYS:
            if self._nudge_selection(context, event):
                return {"RUNNING_MODAL"}
        if not view_event_region.is_view3d_window_event(context, event):
            return {"PASS_THROUGH"}
        if event.type != "LEFTMOUSE" or event.value not in {"PRESS", "DOUBLE_CLICK"}:
            return {"PASS_THROUGH"}
        return self._handle_left_press(context, event)

    def _handle_left_press(self, context, event):
        mode = _selection_mode(event)
        if mode != "single":
            self._clear_click_state()
        if event.value == "DOUBLE_CLICK" and mode == "single":
            if self._try_open_page_file_from_event(context, event):
                return {"FINISHED"}
            hit = self._hit_object(context, event)
            if self._try_enter_text_edit_from_hit(context, hit):
                return {"FINISHED"}
            coma_hit = self._coma_open_hit_from_hit(hit)
            if coma_hit is not None and self._try_enter_coma_from_hit(context, coma_hit):
                return {"FINISHED"}
        if (
            event.value == "PRESS"
            and mode == "single"
            and coma_edge_move_op.extend_selected_handle_at_event(context, event)
        ):
            self._clear_click_state()
            return {"RUNNING_MODAL"}
        # Alt+ドラッグ: 選択レイヤーを別コマ/ページへ移動 (Ctrl 併用はブラシ
        # サイズ用なので除外)。オブジェクトツールが常駐モーダルとして左
        # クリックを専有するため、ここで Alt を解釈する必要がある。
        if (
            event.value == "PRESS"
            and bool(getattr(event, "alt", False))
            and not bool(getattr(event, "ctrl", False))
            and _reparent_has_targets(context)
        ):
            self._clear_click_state()
            if bool(getattr(event, "shift", False)):
                self._do_reparent_out(context, event)
                return {"RUNNING_MODAL"}
            if self._start_reparent_drag(context, event):
                return {"RUNNING_MODAL"}
        if event.value == "PRESS" and bool(getattr(event, "ctrl", False)):
            if object_tool_balloon_tail.handle_ctrl_press(self, context, event):
                self._clear_click_state()
                return {"RUNNING_MODAL"}
        if event.value == "PRESS" and not bool(getattr(event, "ctrl", False)):
            object_tool_balloon_tail.clear_pending(self)
            # 選択中フキダシの既存しっぽポイントは Ctrl 無しでもつかめる
            if mode == "single" and object_tool_balloon_tail.handle_plain_press(self, context, event):
                self._clear_click_state()
                return {"RUNNING_MODAL"}
        hit = self._hit_object(context, event)
        if event.value == "PRESS" and mode == "single":
            # Blender の DOUBLE_CLICK はモーダル実行中に届かないため、
            # ページファイルを開く判定もコマと同じ自前連続クリック検出で行う。
            # ページ一覧 (全ページ表示) ではページ判定をコマ判定より優先する。
            if hit is not None and self._is_manual_coma_double_click(event, hit):
                if str(hit.get("kind", "") or "") == "text":
                    self._clear_click_state()
                    if self._try_enter_text_edit_from_hit(context, hit):
                        return {"FINISHED"}
            open_hit = self._page_open_hit_from_event(context, event)
            if open_hit is None:
                open_hit = self._coma_open_hit_from_hit(hit)
            if self._is_manual_coma_double_click(event, open_hit):
                self._clear_click_state()
                if str(open_hit.get("kind", "") or "") == "page_file":
                    if self._try_open_page_file_from_event(context, event):
                        return {"FINISHED"}
                elif self._try_enter_coma_from_hit(context, open_hit):
                    return {"FINISHED"}
            self._remember_coma_click(event, hit or open_hit)
        if hit is None:
            if mode == "single":
                rot_hit = object_tool_free_transform.hit_rotation_zone_at_event(
                    context, event, _event_world_xy_mm,
                )
                if rot_hit is not None:
                    self._start_rotation_drag(context, event, rot_hit)
                    return {"RUNNING_MODAL"}
                move_hit = self._selected_move_hit_from_event(context, event)
                if move_hit is not None:
                    self._activate_hit(context, move_hit, mode="single")
                    x_mm, y_mm = self._start_point_for_hit(context, event, move_hit)
                    if x_mm is not None and y_mm is not None:
                        self._start_object_drag(context, move_hit, x_mm, y_mm)
                        return {"RUNNING_MODAL"}
            if mode == "single" and self._try_start_layer_drag(context, event):
                return {"RUNNING_MODAL"}
            if self._start_marquee_select(context, event, mode):
                return {"RUNNING_MODAL"}
            if mode == "single":
                object_selection.clear(context)
                object_tool_selection.sync_outliner_selection_for_keys(context, [])
                edge_selection.clear_selection(context)
                object_tool_free_transform.clear_mode(context)
                self._clear_click_state()
            return {"RUNNING_MODAL"}
        if event.value == "DOUBLE_CLICK" and mode == "single" and self._try_enter_coma_from_hit(context, hit):
            return {"FINISHED"}
        free_action = object_tool_free_transform.free_action_for_hit(
            hit,
            ctrl=event.value == "PRESS" and bool(getattr(event, "ctrl", False)),
            context=context if event.value == "PRESS" else None,
        )
        if free_action:
            mode = "single"
            hit = dict(hit)
            hit["part"] = free_action
        self._activate_hit(context, hit, mode=mode)
        if mode in {"toggle", "add"}:
            return {"RUNNING_MODAL"}
        x_mm, y_mm = self._start_point_for_hit(context, event, hit)
        if x_mm is None or y_mm is None:
            return {"RUNNING_MODAL"}
        if hit["kind"] in {"coma_edge", "coma_vertex"}:
            self._start_coma_edge_drag(context, hit, event, x_mm, y_mm)
        else:
            self._start_object_drag(context, hit, x_mm, y_mm)
        return {"RUNNING_MODAL"}

    def _is_manual_coma_double_click(self, event, hit: dict | None) -> bool:
        if hit is None:
            return False
        key = str(hit.get("key", "") or "")
        if not key or key != str(getattr(self, "_last_click_key", "") or ""):
            return False
        now = time.time()
        if now - float(getattr(self, "_last_click_time", 0.0) or 0.0) > _DOUBLE_CLICK_INTERVAL_SEC:
            return False
        last_x, last_y = getattr(self, "_last_click_xy", (-1.0e9, -1.0e9))
        dx = float(getattr(event, "mouse_x", 0.0)) - float(last_x)
        dy = float(getattr(event, "mouse_y", 0.0)) - float(last_y)
        return (dx * dx + dy * dy) ** 0.5 <= _DOUBLE_CLICK_DISTANCE_PX

    def _remember_coma_click(self, event, hit: dict | None) -> None:
        if hit is None:
            self._clear_click_state()
            return
        self._last_click_time = time.time()
        self._last_click_xy = (
            float(getattr(event, "mouse_x", 0.0)),
            float(getattr(event, "mouse_y", 0.0)),
        )
        self._last_click_key = str(hit.get("key", "") or "")

    def _clear_click_state(self) -> None:
        self._last_click_time = 0.0
        self._last_click_xy = (-1.0e9, -1.0e9)
        self._last_click_key = ""

    def _nudge_selection(self, context, event) -> bool:
        keys = object_selection.get_keys(context)
        if not keys:
            return False
        from ..utils.geom import px_to_mm

        work = get_work(context)
        dpi = 600
        if work is not None:
            dpi = int(getattr(getattr(work, "paper", None), "dpi", 600) or 600)
        px = _NUDGE_SHIFT_PX if bool(getattr(event, "shift", False)) else _NUDGE_PX
        step = px_to_mm(px, dpi)
        dx, dy = 0.0, 0.0
        if event.type == "RIGHT_ARROW":
            dx = step
        elif event.type == "LEFT_ARROW":
            dx = -step
        elif event.type == "UP_ARROW":
            dy = step
        elif event.type == "DOWN_ARROW":
            dy = -step
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return False
        self._drag_action = "move"
        self._snapshots = self._make_snapshots(
            context, keys, primary_key=keys[0], action="move",
        )
        if not self._snapshots:
            return False
        self._apply_snapshots(context, dx, dy)
        self._snapshots = []
        object_selection.tag_view3d_redraw(context)
        return True

    def _page_open_hit_from_event(self, context, event) -> dict | None:
        """連続クリックで開くべきページファイルがあれば、その判定情報を返す."""
        try:
            from . import mode_op

            page_index = mode_op.page_file_index_from_viewport_event(context, event)
        except Exception:  # noqa: BLE001
            return None
        if page_index is None:
            return None
        return {
            "kind": "page_file",
            "page": int(page_index),
            "key": f"page_file:{int(page_index)}",
        }

    def _coma_open_hit_from_hit(self, hit: dict | None) -> dict | None:
        if hit is None or str(hit.get("kind", "") or "") not in {"coma", "coma_edge", "coma_vertex"}:
            return None
        page_index = int(hit.get("page", -1))
        coma_index = int(hit.get("coma", -1))
        if page_index < 0 or coma_index < 0:
            return None
        return {
            "kind": "coma",
            "page": page_index,
            "coma": coma_index,
            "part": "body",
            "key": str(hit.get("key", "") or ""),
        }

    def _try_open_page_file_from_event(self, context, event) -> bool:
        try:
            from . import mode_op

            page_index = mode_op.page_file_index_from_viewport_event(context, event)
            if page_index is None:
                return False
            scheduled = mode_op.schedule_open_page_file(int(page_index))
        except Exception:  # noqa: BLE001
            return False
        if not scheduled:
            return False
        self.finish_from_external(context, keep_selection=True)
        return True

    def _try_enter_coma_from_hit(self, context, hit: dict) -> bool:
        if str(hit.get("kind", "") or "") != "coma":
            return False
        try:
            page_index = int(hit.get("page", -1))
            coma_index = int(hit.get("coma", -1))
        except (TypeError, ValueError):
            return False
        if page_index < 0 or coma_index < 0:
            return False
        activate_hit(context, hit, mode="single")
        try:
            from . import mode_op

            scheduled = mode_op.schedule_enter_coma_mode(
                page_index,
                coma_index,
                prompt_template_if_missing=False,
            )
        except Exception:  # noqa: BLE001
            return False
        if not scheduled:
            return False
        self.finish_from_external(context, keep_selection=True)
        return True

    def _try_enter_text_edit_from_hit(self, context, hit: dict | None) -> bool:
        if hit is None or str(hit.get("kind", "") or "") != "text":
            return False
        page_id = str(hit.get("page_id", "") or "")
        if not page_id or page_id == OUTSIDE_STACK_KEY:
            return False
        key = str(hit.get("key", "") or "")
        _kind, _hit_page_id, text_id = object_selection.parse_key(key)
        if not text_id:
            return False
        activate_hit(context, hit, mode="single")
        self.finish_from_external(context, keep_selection=True)
        return text_op.start_editing_existing_from_object_tool(context, page_id, text_id)

    def _hit_object(self, context, event) -> dict | None:
        return hit_object_at_event(context, event)

    def _selected_move_hit_from_event(self, context, event) -> dict | None:
        x_mm, y_mm = _event_world_xy_mm(context, event)
        if x_mm is None or y_mm is None:
            return None
        keys = object_selection.get_keys(context)
        key = active_selection_key(context)
        if not key and keys:
            key = keys[-1]
        if not key:
            return None
        kind, page_id, item_id = object_selection.parse_key(key)
        if kind not in {
            "balloon", "text", "effect", "image", "image_path", "raster", "fill", "gp",
            "gradient_handle",
        }:
            return None
        rect = selection_bounds_for_key(context, key)
        # key は選択中(アクティブ)のものだけなので、ハンドルリングぶんまで
        # 常に余白を広げてよい (効果線・フキダシ・テキストと同じ「選択中のみ
        # 拡張」仕様)。
        if rect is None or not object_tool_selection.rect_contains_point(
            rect, float(x_mm), float(y_mm), pad=_SELECTED_MOVE_HIT_PAD_MM
        ):
            return None
        hit = {
            "kind": kind,
            "part": "move",
            "key": key,
            "world": (float(x_mm), float(y_mm)),
        }
        work = get_work(context)
        if kind == "balloon":
            _page_index, _page, index, _entry = _find_balloon_by_key(work, page_id, item_id)
            hit["page_id"] = page_id
            hit["index"] = index
        elif kind == "text":
            _page_index, _page, index, _entry = _find_text_by_key(work, page_id, item_id)
            hit["page_id"] = page_id
            hit["index"] = index
        elif kind == "effect":
            hit["layer_name"] = item_id
        elif kind == "image":
            hit["item_id"] = item_id
        elif kind == "image_path":
            hit["item_id"] = item_id
        elif kind == "raster":
            hit["item_id"] = item_id
        elif kind == "fill":
            hit["item_id"] = item_id
        elif kind == "gp":
            hit["layer_key"] = item_id
        elif kind == "gradient_handle":
            hit["fill_id"] = item_id
            hit["end"] = page_id
        return hit

    def _hit_text(self, context, event) -> dict | None:
        return _hit_text_at_event(context, event)

    def _hit_balloon(self, context, event) -> dict | None:
        return _hit_balloon_at_event(context, event)

    def _hit_effect(self, context, event) -> dict | None:
        return _hit_effect_at_event(context, event)

    def _hit_image(self, context, event) -> dict | None:
        return _hit_image_at_event(context, event)

    def _hit_gp(self, context, event) -> dict | None:
        return _hit_gp_at_event(context, event)

    def _hit_raster(self, context, event) -> dict | None:
        return _hit_raster_at_event(context, event)

    def _activate_hit(self, context, hit: dict, *, mode: str) -> None:
        activate_hit(context, hit, mode=mode)

    def _start_point_for_hit(self, context, event, hit: dict) -> tuple[float | None, float | None]:
        if "world" in hit:
            return hit["world"]
        if hit["kind"] in {"coma", "coma_edge", "coma_vertex"}:
            view = view_event_region.view3d_window_under_event(context, event)
            if view is None:
                return None, None
            _area, region, rv3d, mx, my = view
            return coma_edge_move_op._region_to_world_mm(region, rv3d, mx, my)
        return _event_world_xy_mm(context, event)

    def _start_coma_edge_drag(self, context, hit: dict, event, x_mm: float, y_mm: float) -> None:
        selection = {
            "type": "vertex" if hit["kind"] == "coma_vertex" else "edge",
            "page": int(hit["page"]),
            "coma": int(hit["coma"]),
        }
        if selection["type"] == "vertex":
            selection["vertex"] = int(hit.get("vertex", -1))
            selected_vertices = edge_selection.selected_vertices(
                context,
                page_index=selection["page"],
                coma_index=selection["coma"],
            )
            if selection["vertex"] in selected_vertices and len(selected_vertices) > 1:
                selection["vertices"] = sorted(selected_vertices)
        else:
            selection["edge"] = int(hit.get("edge", -1))
        view = view_event_region.view3d_window_under_event(context, event)
        if view is None:
            return
        area, region, rv3d, _mx, _my = view
        self._edge_drag = coma_edge_drag_session.ComaEdgeDragSession(
            context,
            get_work(context),
            area,
            region,
            rv3d,
            selection,
            (float(x_mm), float(y_mm)),
        )
        self._dragging = True
        self._drag_action = "coma_edge"
        self._drag_moved = False

    def _try_start_layer_drag(self, context, event) -> bool:
        scene = getattr(context, "scene", None)
        if scene is None or getattr(scene, "bmanga_active_layer_kind", "") not in {"gp", "image"}:
            return False
        item = layer_stack_utils.active_stack_item(context)
        if item is None or getattr(item, "kind", "") not in {"gp", "image"}:
            return False
        x_mm, y_mm = _event_world_xy_mm(context, event)
        if x_mm is None or y_mm is None:
            return False
        session = layer_move_session.LayerMoveDragSession(context, (float(x_mm), float(y_mm)))
        if not session.started:
            return False
        self._layer_drag = session
        self._dragging = True
        self._drag_action = "layer_move"
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._drag_keys = []
        self._snapshots = []
        self._drag_moved = False
        return True

    # ---------- Alt+ドラッグ: 別コマ/ページへ移動 (reparent) ----------

    def _start_reparent_drag(self, context, event) -> bool:
        target = layer_reparent.find_target_for_drop(context, event)
        self._dragging = True
        self._drag_action = "reparent"
        self._drag_keys = []
        self._snapshots = []
        self._drag_moved = False
        self._reparent_start_px = (float(event.mouse_x), float(event.mouse_y))
        self._reparent_target = target
        _reparent_set_overlay(target)
        if target.world_xy_mm is not None:
            reparent_overlay.set_preview(
                world_xy_mm=target.world_xy_mm,
                count=_reparent_count(context),
            )
        return True

    def _do_reparent_out(self, context, event) -> None:
        click_target = layer_reparent.find_click_target(context, event)
        scene = context.scene
        stack = getattr(scene, "bmanga_layer_stack", None)
        if stack is None:
            return
        active_idx = int(getattr(scene, "bmanga_active_layer_stack_index", -1))
        candidates = []
        if 0 <= active_idx < len(stack):
            candidates.append(stack[active_idx])
        for item in stack:
            if layer_stack_utils.is_item_selected(context, item) and not any(
                layer_stack_utils.stack_item_uid(c) == layer_stack_utils.stack_item_uid(item)
                for c in candidates
            ):
                candidates.append(item)
        target = None
        for item in candidates:
            t = layer_reparent.shallower_target_for_item(context, item, click_target)
            if t is not None:
                target = t
                break
        if target is None:
            reparent_overlay.flash_error("page", duration=0.3)
            return
        changed = layer_reparent.reparent_selected(context, target)
        if changed > 0:
            _reparent_set_confirm(target)
            try:
                bpy.ops.ed.undo_push(message="B-MANGA: Alt+Shift で外へ移動")
            except Exception:  # noqa: BLE001
                pass
            layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
        else:
            _reparent_set_error(target)

    def _finish_reparent(self, context) -> None:
        target = getattr(self, "_reparent_target", None)
        reparent_overlay.clear_hover()
        reparent_overlay.clear_preview()
        if target is None:
            return
        moved = bool(getattr(self, "_drag_moved", False))
        new_xy = target.world_xy_mm if moved else None
        changed = layer_reparent.reparent_selected(
            context, target, new_world_xy_mm=new_xy
        )
        if changed > 0:
            _reparent_set_confirm(target)
            try:
                bpy.ops.ed.undo_push(message="B-MANGA: Alt+ドラッグで移動")
            except Exception:  # noqa: BLE001
                pass
            layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
        else:
            _reparent_set_error(target)

    def _start_object_drag(self, context, hit: dict, x_mm: float, y_mm: float) -> None:
        action = str(hit.get("part", "move") or "move")
        if str(hit.get("kind", "") or "") == "text":
            action = "move"
        key = str(hit.get("key", "") or "")
        selected = object_selection.get_keys(context)
        if action == "move" and key in selected:
            keys = selected
        else:
            keys = [key]
        self._dragging = True
        self._drag_action = action
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._drag_keys = keys
        self._snapshots = self._make_snapshots(context, keys, primary_key=key, action=action)
        self._drag_moved = False
        self._center_snap_targets = []
        self._original_center = None
        self._center_snap_armed = False
        self._setup_center_snap(context)

    def _setup_center_snap(self, context) -> None:
        """ドラッグ開始時に中心点スナップデータを準備する。"""
        if self._drag_action != "move":
            return
        from ..utils import center_point_snap

        work = get_work(context)
        if work is None:
            return
        original_center = None
        exclude_balloon_ids: set[str] = set()
        exclude_effect_layer_names: set[str] = set()
        page = None

        for snapshot in self._snapshots:
            kind = snapshot.get("kind", "")
            if kind == "balloon":
                co = snapshot.get("center_offset")
                if co is not None:
                    rect = snapshot.get("rect", (0, 0, 0, 0))
                    x, y, w, h = rect
                    c = (x + w * 0.5 + co[0], y + h * 0.5 + co[1])
                    if original_center is None:
                        page_id = snapshot.get("page_id", "")
                        item_id = snapshot.get("item_id", "")
                        _pi, page, _idx, entry = _find_balloon_by_key(work, page_id, item_id)
                        if entry is not None:
                            from ..utils import balloon_shapes
                            if balloon_shapes.is_flash_line_style(getattr(entry, "line_style", "")):
                                original_center = c
                    exclude_balloon_ids.add(snapshot.get("item_id", ""))
            elif kind == "effect":
                center = snapshot.get("center")
                if center is not None and original_center is None:
                    original_center = (float(center[0]), float(center[1]))
                # snapshot["item_id"] はポインタ由来キー("ptr_XXXX")なので、
                # collect_page_center_points が比較する layer.name へ解決してから除外する。
                _obj, _layer = _find_effect_layer(snapshot.get("item_id", ""))
                if _layer is not None:
                    exclude_effect_layer_names.add(str(getattr(_layer, "name", "") or ""))

        if original_center is None or page is None:
            if original_center is not None and page is None:
                page = get_active_page(context)
            if original_center is None:
                return

        if page is None:
            return
        self._original_center = original_center
        self._center_snap_targets = center_point_snap.collect_page_center_points(
            context, page,
            exclude_balloon_ids=exclude_balloon_ids,
            exclude_effect_layer_names=exclude_effect_layer_names,
        )

    def _start_rotation_drag(self, context, event, rot_hit: dict) -> None:
        x_mm, y_mm = _event_world_xy_mm(context, event)
        if x_mm is None or y_mm is None:
            return
        self._dragging = True
        self._drag_action = "rotate"
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._rotate_center = rot_hit["center"]
        self._drag_keys = [rot_hit["key"]]
        self._drag_moved = False
        self._rotate_snapshots = []
        key = rot_hit["key"]
        kind, page_id, item_id = object_selection.parse_key(key)
        work = get_work(context)
        if kind == "balloon" and work is not None:
            _pi, _p, _idx, entry = _find_balloon_by_key(work, page_id, item_id)
            if entry is not None:
                self._rotate_snapshots.append({
                    "entry": entry,
                    "rotation_deg": float(getattr(entry, "rotation_deg", 0.0)),
                })
        elif kind == "effect":
            scene = getattr(context, "scene", None)
            params = getattr(scene, "bmanga_effect_line_params", None) if scene else None
            if params is not None:
                self._rotate_snapshots.append({
                    "entry": params,
                    "rotation_deg": float(getattr(params, "rotation_deg", 0.0)),
                })
        elif kind == "image":
            _idx, entry = _find_image_by_key(context, item_id)
            if entry is not None:
                self._rotate_snapshots.append({
                    "entry": entry,
                    "rotation_deg": float(getattr(entry, "rotation_deg", 0.0)),
                })
        coma_modal_state.set_modal_cursor(context, "SCROLL_XY")

    def _start_marquee_select(self, context, event, mode: str) -> bool:
        x_mm, y_mm = _event_world_xy_mm(context, event)
        if x_mm is None or y_mm is None:
            return False
        self._dragging = True
        self._drag_action = "marquee"
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._marquee_start_x = float(x_mm)
        self._marquee_start_y = float(y_mm)
        self._marquee_current_x = float(x_mm)
        self._marquee_current_y = float(y_mm)
        self._marquee_mode = str(mode or "single")
        self._drag_keys = []
        self._snapshots = []
        self._drag_moved = False
        return True

    def _make_snapshots(self, context, keys: list[str], *, primary_key: str, action: str) -> list[dict]:
        work = get_work(context)
        snapshots: list[dict] = []
        for key in keys:
            kind, page_id, item_id = object_selection.parse_key(key)
            if action != "move" and key != primary_key:
                continue
            if kind == "coma":
                page_index, page, coma_index, panel = _find_coma_by_key(work, page_id, item_id)
                if panel is None:
                    continue
                poly = coma_edge_move_op._coma_polygon(panel)
                gp_key = layer_stack_utils.gp_parent_key_for_coma(page, panel)
                snapshots.append({
                    "kind": "coma",
                    "page_index": page_index,
                    "page_id": page_id,
                    "coma_id": item_id,
                    "shape": getattr(panel, "shape_type", ""),
                    "rect": (
                        float(getattr(panel, "rect_x_mm", 0.0)),
                        float(getattr(panel, "rect_y_mm", 0.0)),
                        float(getattr(panel, "rect_width_mm", 0.0)),
                        float(getattr(panel, "rect_height_mm", 0.0)),
                    ),
                    "poly": poly,
                    "children": self._panel_child_snapshots(page, panel),
                    "gp": layer_stack_utils.capture_gp_layers_for_parent_keys(context, {gp_key}),
                    "effect_gp": layer_stack_utils.capture_effect_layers_for_parent_keys(context, {gp_key}),
                    "raster": layer_stack_utils.capture_raster_layers_for_parent_keys(context, {gp_key}),
                    "gp_key": gp_key,
                })
            elif kind == "balloon":
                _page_index, page, _idx, entry = _find_balloon_by_key(work, page_id, item_id)
                if entry is None:
                    continue
                snapshots.append({
                    "kind": "balloon",
                    "page_id": page_id,
                    "item_id": item_id,
                    "rect": (float(entry.x_mm), float(entry.y_mm), float(entry.width_mm), float(entry.height_mm)),
                    "free_transform": free_transform.entry_snapshot(entry),
                    "center_offset": (
                        float(getattr(entry, "center_offset_x_mm", 0.0) or 0.0),
                        float(getattr(entry, "center_offset_y_mm", 0.0) or 0.0),
                    ),
                })
            elif kind == "text":
                _page_index, _page, _idx, entry = _find_text_by_key(work, page_id, item_id)
                if entry is None:
                    continue
                snapshots.append({
                    "kind": "text",
                    "page_id": page_id,
                    "item_id": item_id,
                    "rect": (float(entry.x_mm), float(entry.y_mm), float(entry.width_mm), float(entry.height_mm)),
                    "free_transform": free_transform.entry_snapshot(entry),
                })
            elif kind == "effect":
                obj, layer = _find_effect_layer(item_id)
                bounds = effect_line_op.effect_layer_bounds(obj, layer)
                if layer is None or bounds is None:
                    continue
                center = effect_line_op.effect_layer_center(obj, layer, bounds) or (
                    float(bounds[0]) + float(bounds[2]) * 0.5,
                    float(bounds[1]) + float(bounds[3]) * 0.5,
                )
                snapshots.append({
                    "kind": "effect",
                    "item_id": item_id,
                    "rect": (float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3])),
                    "center": (float(center[0]), float(center[1])),
                    "free_transform": free_transform.effect_payload_for_layer(obj, layer),
                })
            elif kind == "image":
                _idx, entry = _find_image_by_key(context, item_id)
                if entry is None:
                    continue
                snapshots.append({
                    "kind": "image",
                    "item_id": item_id,
                    "rect": (float(entry.x_mm), float(entry.y_mm), float(entry.width_mm), float(entry.height_mm)),
                })
            elif kind == "image_path":
                _idx, entry = _find_image_path_by_key(context, item_id)
                if entry is None:
                    continue
                snapshots.append({
                    "kind": "image_path",
                    "item_id": item_id,
                    "path_points_json": str(getattr(entry, "path_points_json", "") or ""),
                })
            elif kind == "raster":
                _idx, entry = _find_raster_by_key(context, item_id)
                if entry is None:
                    continue
                image = raster_layer_op.ensure_raster_image(context, entry, create_missing=False)
                if image is None:
                    continue
                try:
                    pixels = image.pixels[:]
                except Exception:  # noqa: BLE001
                    pixels = ()
                snapshots.append({
                    "kind": "raster",
                    "item_id": item_id,
                    "image_name": str(getattr(image, "name", "") or ""),
                    "pixels": tuple(pixels),
                })
            elif kind == "fill":
                _idx, entry = _find_fill_by_key(context, item_id)
                if entry is None:
                    continue
                if bool(getattr(entry, "use_region", False)):
                    snapshots.append({
                        "kind": "fill",
                        "item_id": item_id,
                        "rect": (
                            float(getattr(entry, "region_x_mm", 0.0)),
                            float(getattr(entry, "region_y_mm", 0.0)),
                            float(getattr(entry, "region_width_mm", 0.0)),
                            float(getattr(entry, "region_height_mm", 0.0)),
                        ),
                    })
            elif kind == "gp":
                obj, layer = _find_gp_layer(item_id)
                bounds = object_tool_selection.gp_layer_local_bounds(layer)
                if layer is None or bounds is None:
                    continue
                snapshots.append({
                    "kind": "gp",
                    "item_id": item_id,
                    "rect": (bounds.x, bounds.y, bounds.width, bounds.height),
                    "points": gp_parent.capture_layers([layer]),
                })
            elif kind == "gradient_handle":
                from ..utils.fill_real_object import find_fill_entry as _find_fill
                entry = _find_fill(context.scene, item_id)
                if entry is None:
                    continue
                end_tag = page_id  # parse_key: kind|end|fill_id
                if end_tag == "start":
                    hx = float(getattr(entry, "gradient_start_x_mm", 0.0) or 0.0)
                    hy = float(getattr(entry, "gradient_start_y_mm", 0.0) or 0.0)
                else:
                    hx = float(getattr(entry, "gradient_end_x_mm", 0.0) or 0.0)
                    hy = float(getattr(entry, "gradient_end_y_mm", 0.0) or 0.0)
                snapshots.append({
                    "kind": "gradient_handle",
                    "fill_id": item_id,
                    "end": end_tag,
                    "x": hx,
                    "y": hy,
                })
        return snapshots

    def _panel_child_snapshots(self, page, panel) -> list[tuple[str, str, float, float]]:
        balloons, texts = layer_move_op._panel_children(page, panel)
        snapshots = []
        for balloon in balloons:
            snapshots.append(("balloon", getattr(balloon, "id", ""), float(balloon.x_mm), float(balloon.y_mm)))
        for text in texts:
            snapshots.append(("text", getattr(text, "id", ""), float(text.x_mm), float(text.y_mm)))
        return snapshots

    def _modal_dragging(self, context, event):
        if event.type == "MOUSEMOVE":
            self._update_overlay_pointer(context, event)
            self._update_drag(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self._update_drag(context, event)
            self._finish_drag(context)
            return {"RUNNING_MODAL"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._cancel_drag(context)
            return {"RUNNING_MODAL"}
        return {"RUNNING_MODAL"}

    def _update_overlay_pointer(self, context, event) -> None:
        try:
            view = view_event_region.view3d_window_under_event(context, event)
        except Exception:  # noqa: BLE001
            view = None
        if view is None:
            edge_selection.update_overlay_pointer(context, None, event)
            self._update_rotation_cursor(context, event, in_view=False)
            return
        _area, region, _rv3d, _mx, _my = view
        edge_selection.update_overlay_pointer(context, region, event)
        self._update_rotation_cursor(context, event, in_view=True)
        try:
            region.tag_redraw()
        except Exception:  # noqa: BLE001
            pass

    def _update_rotation_cursor(self, context, event, *, in_view: bool) -> None:
        was_rotate = getattr(self, "_rotate_cursor_active", False)
        if in_view and not getattr(self, "_dragging", False):
            rot_hit = object_tool_free_transform.hit_rotation_zone_at_event(
                context, event, _event_world_xy_mm,
            )
            if rot_hit is not None:
                if not was_rotate:
                    coma_modal_state.set_modal_cursor(context, "SCROLL_XY")
                    self._rotate_cursor_active = True
                return
        if was_rotate:
            coma_modal_state.set_modal_cursor(context, "DEFAULT")
            self._rotate_cursor_active = False

    def _update_drag(self, context, event) -> None:
        if self._drag_action == "rotate":
            x_mm, y_mm = _event_world_xy_mm(context, event)
            if x_mm is None or y_mm is None:
                return
            delta_deg = object_tool_free_transform.compute_rotation_delta(
                self._rotate_center,
                self._drag_start_x, self._drag_start_y,
                x_mm, y_mm,
            )
            if abs(delta_deg) > 0.001:
                self._drag_moved = True
            for snap in self._rotate_snapshots:
                entry = snap.get("entry")
                if entry is not None:
                    entry.rotation_deg = float(snap.get("rotation_deg", 0.0)) + delta_deg
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if self._drag_action == "marquee":
            x_mm, y_mm = _event_world_xy_mm(context, event)
            if x_mm is None or y_mm is None:
                return
            self._marquee_current_x = float(x_mm)
            self._marquee_current_y = float(y_mm)
            dx = float(x_mm) - self._marquee_start_x
            dy = float(y_mm) - self._marquee_start_y
            if abs(dx) > _DRAG_EPS_MM or abs(dy) > _DRAG_EPS_MM:
                self._drag_moved = True
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if self._drag_action == "coma_edge":
            if self._edge_drag is not None and self._edge_drag.apply(event):
                self._drag_moved = True
            return
        if self._drag_action == "layer_move":
            if self._layer_drag is not None and self._layer_drag.apply(context, event):
                self._drag_moved = True
            return
        if self._drag_action == "reparent":
            target = layer_reparent.find_target_for_drop(context, event)
            self._reparent_target = target
            _reparent_set_overlay(target)
            if target.world_xy_mm is not None:
                reparent_overlay.set_preview(
                    world_xy_mm=target.world_xy_mm,
                    count=_reparent_count(context),
                )
            sx, sy = getattr(self, "_reparent_start_px", (0.0, 0.0))
            if (
                abs(float(event.mouse_x) - sx) >= _REPARENT_DRAG_PX
                or abs(float(event.mouse_y) - sy) >= _REPARENT_DRAG_PX
            ):
                self._drag_moved = True
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if object_tool_balloon_tail.update_drag(self, context, event):
            return
        x_mm, y_mm = _event_world_xy_mm(context, event)
        if x_mm is None or y_mm is None:
            return
        dx = float(x_mm) - self._drag_start_x
        dy = float(y_mm) - self._drag_start_y
        if self._center_snap_targets and self._original_center:
            from ..utils import center_point_snap
            # クリック時の微小ジッタでスナップが発火しないよう、
            # 生の移動量が発動しきい値を超えて初めてスナップを有効化する。
            # 一度有効化されたらそのドラッグ中は継続して有効。
            if not self._center_snap_armed and max(abs(dx), abs(dy)) >= center_point_snap.SNAP_ACTIVATION_MM:
                self._center_snap_armed = True
            if self._center_snap_armed:
                dx, dy = center_point_snap.snap_center(self._original_center, dx, dy, self._center_snap_targets)
        if abs(dx) > _DRAG_EPS_MM or abs(dy) > _DRAG_EPS_MM:
            self._drag_moved = True
        self._apply_snapshots(context, dx, dy)
        layer_stack_utils.tag_view3d_redraw(context)

    def _apply_snapshots(self, context, dx: float, dy: float) -> None:
        work = get_work(context)
        balloon_move_uids: set[str] = set()
        if self._drag_action == "move":
            for snapshot in self._snapshots:
                if snapshot.get("kind") != "balloon":
                    continue
                balloon_move_uids.add(
                    layer_stack_utils.target_uid(
                        "balloon",
                        f"{snapshot.get('page_id', '')}:{snapshot.get('item_id', '')}",
                    )
                )
        balloon_link_updated: set[str] = set()
        for snapshot in self._snapshots:
            kind = snapshot["kind"]
            x, y, w, h = snapshot.get("rect", (0.0, 0.0, 0.0, 0.0))
            if kind == "coma":
                _page_index, page, _coma_index, panel = _find_coma_by_key(
                    work,
                    snapshot["page_id"],
                    snapshot["coma_id"],
                )
                if panel is None or page is None:
                    continue
                if self._drag_action == "move":
                    self._apply_panel_move(context, page, panel, snapshot, dx, dy)
                elif getattr(panel, "shape_type", "") == "rect":
                    nx, ny, nw, nh = _rect_resize_result(self._drag_action, x, y, w, h, dx, dy, 2.0)
                    panel.rect_x_mm = nx
                    panel.rect_y_mm = ny
                    panel.rect_width_mm = nw
                    panel.rect_height_mm = nh
                # NOTE: rect_*_mm 変更は core/coma.py の update callback 経由で
                # coma_plane Mesh が自動追従する。
            elif kind == "balloon":
                _page_index, page, _idx, entry = _find_balloon_by_key(
                    work,
                    snapshot["page_id"],
                    snapshot["item_id"],
                )
                if entry is None:
                    continue
                corner = free_transform.corner_from_action(self._drag_action)
                if corner:
                    with balloon_curve_object.suspend_auto_sync():
                        free_transform.apply_corner_drag_to_entry(
                            entry,
                            snapshot.get("free_transform"),
                            corner,
                            dx,
                            dy,
                        )
                    balloon_curve_object.on_balloon_entry_changed(entry)
                    balloon_op._sync_balloon_merge_display_if_needed(page, entry)
                    try:
                        from . import layer_link_duplicate_op

                        layer_link_duplicate_op.propagate_linked_balloon_center_free(context, page, entry)
                    except Exception:  # noqa: BLE001
                        pass
                    continue
                if self._drag_action == "center":
                    cx, cy = snapshot.get("center_offset", (0.0, 0.0))
                    with balloon_curve_object.suspend_auto_sync():
                        if hasattr(entry, "center_offset_x_mm"):
                            entry.center_offset_x_mm = float(cx) + dx
                        if hasattr(entry, "center_offset_y_mm"):
                            entry.center_offset_y_mm = float(cy) + dy
                    balloon_curve_object.on_balloon_entry_changed(entry)
                    balloon_op._sync_balloon_merge_display_if_needed(page, entry)
                    try:
                        from . import layer_link_duplicate_op

                        layer_link_duplicate_op.propagate_linked_balloon_center_free(context, page, entry)
                    except Exception:  # noqa: BLE001
                        pass
                    continue
                if self._drag_action == "move":
                    old_x = float(getattr(entry, "x_mm", 0.0) or 0.0)
                    old_y = float(getattr(entry, "y_mm", 0.0) or 0.0)
                    balloon_op._move_balloon_with_texts(page, entry, x + dx, y + dy)
                    actual_dx = float(getattr(entry, "x_mm", 0.0) or 0.0) - old_x
                    actual_dy = float(getattr(entry, "y_mm", 0.0) or 0.0) - old_y
                    try:
                        from . import layer_link_duplicate_op

                        layer_link_duplicate_op.propagate_linked_balloon_move_delta(
                            context,
                            page,
                            entry,
                            actual_dx,
                            actual_dy,
                            skip_uids=balloon_move_uids,
                            updated_uids=balloon_link_updated,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    continue
                nx, ny, nw, nh = _rect_resize_result(self._drag_action, x, y, w, h, dx, dy, 2.0)
                balloon_op._set_balloon_rect(page, entry, nx, ny, nw, nh)
            elif kind == "text":
                _page_index, _page, _idx, entry = _find_text_by_key(
                    work,
                    snapshot["page_id"],
                    snapshot["item_id"],
                )
                if entry is None:
                    continue
                if self._drag_action != "move":
                    continue
                text_op._set_text_rect(entry, x + dx, y + dy, w, h)
            elif kind == "effect":
                obj, layer = _find_effect_layer(snapshot["item_id"])
                if layer is None:
                    continue
                corner = free_transform.corner_from_action(self._drag_action)
                cx, cy = snapshot.get("center", (x + w * 0.5, y + h * 0.5))
                if corner:
                    meta = effect_line_op._effect_meta(obj)
                    key = effect_line_op._layer_meta_key(layer)
                    entry = meta.get(key) if isinstance(meta.get(key), dict) else {}
                    free_transform.apply_corner_drag_to_effect_entry(
                        entry,
                        snapshot.get("free_transform"),
                        corner,
                        dx,
                        dy,
                    )
                    meta[key] = entry
                    effect_line_op._write_effect_meta(obj, meta)
                    effect_line_op._write_effect_strokes(context, obj, layer, (x, y, w, h), center_xy_mm=(cx, cy))
                    continue
                if self._drag_action == "center":
                    nx, ny, nw, nh = x, y, w, h
                elif self._drag_action in _CORNER_PARTS:
                    nx, ny, nw, nh = _uniform_scale_result(self._drag_action, x, y, w, h, dx, dy, 2.0)
                else:
                    nx, ny, nw, nh = _rect_resize_result(self._drag_action, x, y, w, h, dx, dy, 2.0)
                if self._drag_action in {"move", "center"}:
                    center = (float(cx) + dx, float(cy) + dy)
                else:
                    center = (
                        float(cx) + (float(nx) + float(nw) * 0.5) - (float(x) + float(w) * 0.5),
                        float(cy) + (float(ny) + float(nh) * 0.5) - (float(y) + float(h) * 0.5),
                    )
                effect_line_op._write_effect_strokes(context, obj, layer, (nx, ny, nw, nh), center_xy_mm=center)
            elif kind == "fill":
                _idx, entry = _find_fill_by_key(context, snapshot["item_id"])
                if entry is None or not bool(getattr(entry, "use_region", False)):
                    continue
                if self._drag_action == "move":
                    entry.region_x_mm = x + dx
                    entry.region_y_mm = y + dy
            elif kind == "image":
                _idx, entry = _find_image_by_key(context, snapshot["item_id"])
                if entry is None:
                    continue
                nx, ny, nw, nh = _rect_resize_result(self._drag_action, x, y, w, h, dx, dy, 2.0)
                entry.x_mm = nx
                entry.y_mm = ny
                entry.width_mm = nw
                entry.height_mm = nh
            elif kind == "image_path":
                _idx, entry = _find_image_path_by_key(context, snapshot["item_id"])
                if entry is None or self._drag_action != "move":
                    continue
                try:
                    points = json.loads(str(snapshot.get("path_points_json", "") or "[]"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    points = []
                moved = []
                for point in points if isinstance(points, list) else []:
                    if not isinstance(point, (list, tuple)) or len(point) < 2:
                        continue
                    moved.append([float(point[0]) + dx, float(point[1]) + dy])
                if moved:
                    entry.path_points_json = json.dumps(moved)
            elif kind == "raster":
                _idx, entry = _find_raster_by_key(context, snapshot["item_id"])
                if entry is None:
                    continue
                image = bpy.data.images.get(str(snapshot.get("image_name", "") or ""))
                pixels = snapshot.get("pixels", ())
                if image is not None and pixels:
                    try:
                        image.pixels[:] = pixels
                        image.update()
                    except Exception:  # noqa: BLE001
                        pass
                if self._drag_action == "move":
                    raster_layer_op.translate_raster_layer_pixels(context, entry, dx, dy)
            elif kind == "gp":
                _obj, layer = _find_gp_layer(snapshot["item_id"])
                if layer is None:
                    continue
                layer_stack_utils.restore_gp_layer_snapshots(snapshot.get("points", []))
                nx, ny, nw, nh = _rect_resize_result(self._drag_action, x, y, w, h, dx, dy, 0.5)
                if self._drag_action == "move":
                    gp_parent.translate_layer(layer, dx, dy)
                else:
                    object_tool_selection.scale_gp_layer_from_snapshot(
                        layer,
                        (x, y, w, h),
                        (nx, ny, nw, nh),
                    )
            elif kind == "gradient_handle":
                if self._drag_action != "move":
                    continue
                from ..utils.fill_real_object import find_fill_entry as _find_fill
                entry = _find_fill(context.scene, snapshot["fill_id"])
                if entry is None:
                    continue
                end_tag = snapshot.get("end", "")
                sx, sy = snapshot.get("x", 0.0), snapshot.get("y", 0.0)
                if end_tag == "start":
                    entry.gradient_start_x_mm = sx + dx
                    entry.gradient_start_y_mm = sy + dy
                else:
                    entry.gradient_end_x_mm = sx + dx
                    entry.gradient_end_y_mm = sy + dy

    def _apply_panel_move(self, context, page, panel, snapshot: dict, dx: float, dy: float) -> None:
        if snapshot["shape"] == "rect":
            x, y, w, h = snapshot["rect"]
            panel.shape_type = "rect"
            panel.rect_x_mm = x + dx
            panel.rect_y_mm = y + dy
            panel.rect_width_mm = w
            panel.rect_height_mm = h
        else:
            coma_edge_move_op._set_coma_polygon(
                panel,
                [(x + dx, y + dy) for x, y in snapshot["poly"]],
            )
        for child_kind, child_id, x, y in snapshot.get("children", []):
            if child_kind == "balloon":
                idx = balloon_op._find_balloon_index(page, child_id)
                if 0 <= idx < len(page.balloons):
                    balloon_op._move_balloon_with_texts(page, page.balloons[idx], x + dx, y + dy)
            elif child_kind == "text":
                idx = text_op._find_text_index(page, child_id)
                if 0 <= idx < len(page.texts):
                    page.texts[idx].x_mm = x + dx
                    page.texts[idx].y_mm = y + dy
        layer_stack_utils.restore_gp_layer_snapshots(snapshot.get("gp", []))
        layer_stack_utils.translate_gp_layers_for_parent_keys(context, {snapshot["gp_key"]}, dx, dy)
        layer_stack_utils.restore_gp_layer_snapshots(snapshot.get("effect_gp", []))
        layer_stack_utils.translate_effect_layers_for_parent_keys(context, {snapshot["gp_key"]}, dx, dy)
        layer_stack_utils.restore_raster_layer_snapshots(context, snapshot.get("raster", []))
        layer_stack_utils.translate_raster_layers_for_parent_keys(context, {snapshot["gp_key"]}, dx, dy)

    def _finish_drag(self, context) -> None:
        if self._drag_action == "rotate":
            coma_modal_state.set_modal_cursor(context, "DEFAULT")
            self._rotate_cursor_active = False
            if self._drag_moved:
                self._clear_click_state()
                try:
                    bpy.ops.ed.undo_push(message="B-MANGA: 回転")
                except Exception:  # noqa: BLE001
                    pass
                layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
            self._clear_drag_state()
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if self._drag_action == "marquee":
            self._finish_marquee_select(context)
            if self._drag_moved:
                self._clear_click_state()
            self._clear_drag_state()
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if self._drag_action == "reparent":
            self._finish_reparent(context)
            self._clear_click_state()
            self._clear_drag_state()
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if self._drag_action == "balloon_tail_create":
            object_tool_balloon_tail.finish_create_drag(self, context)
            self._clear_click_state()
            self._clear_drag_state()
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if self._drag_action == "balloon_tail_point":
            moved = bool(getattr(self, "_drag_moved", False))
            if moved:
                self._clear_click_state()
                try:
                    bpy.ops.ed.undo_push(message="B-MANGA: しっぽ制御点移動")
                except Exception:  # noqa: BLE001
                    pass
                layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
            else:
                layer_stack_utils.tag_view3d_redraw(context)
            self._clear_drag_state()
            return
        moved = bool(getattr(self, "_drag_moved", False))
        if moved:
            self._clear_click_state()
        changed = moved
        edge_session = self._drag_action == "coma_edge"
        layer_session = self._drag_action == "layer_move"
        if self._drag_action == "coma_edge" and self._edge_drag is not None:
            changed = bool(self._edge_drag.finish())
        elif self._drag_action == "layer_move" and self._layer_drag is not None:
            changed = bool(self._layer_drag.finish(context))
        if changed:
            if not edge_session and not layer_session:
                try:
                    bpy.ops.ed.undo_push(message="B-MANGA: オブジェクト編集")
                except Exception:  # noqa: BLE001
                    pass
            if not layer_session:
                layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
        elif not layer_session:
            layer_stack_utils.tag_view3d_redraw(context)
        self._clear_drag_state()

    def _finish_marquee_select(self, context) -> None:
        moved = bool(getattr(self, "_drag_moved", False))
        mode = str(getattr(self, "_marquee_mode", "single") or "single")
        if not moved:
            if mode == "single":
                object_selection.clear(context)
                edge_selection.clear_selection(context)
            return
        x0 = float(getattr(self, "_marquee_start_x", 0.0))
        y0 = float(getattr(self, "_marquee_start_y", 0.0))
        x1 = float(getattr(self, "_marquee_current_x", x0))
        y1 = float(getattr(self, "_marquee_current_y", y0))
        rect = Rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
        object_tool_selection.select_keys_in_world_rect(
            context,
            rect,
            mode=mode,
            activate=lambda ctx, hit, hit_mode: activate_hit(ctx, hit, mode=hit_mode),
        )

    def _cancel_drag(self, context) -> None:
        if self._drag_action == "rotate":
            for snap in self._rotate_snapshots:
                entry = snap.get("entry")
                if entry is not None:
                    entry.rotation_deg = float(snap.get("rotation_deg", 0.0))
            coma_modal_state.set_modal_cursor(context, "DEFAULT")
            self._rotate_cursor_active = False
            self._clear_drag_state()
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if self._drag_action == "reparent":
            reparent_overlay.clear_hover()
            reparent_overlay.clear_preview()
            self._clear_drag_state()
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if self._drag_action == "layer_move" and self._layer_drag is not None:
            self._layer_drag.cancel(context)
        elif self._drag_action == "coma_edge" and self._edge_drag is not None:
            self._edge_drag.cancel()
        elif object_tool_balloon_tail.cancel_point_drag(self, context):
            pass
        elif self._drag_action == "balloon_tail_create":
            pass
        elif self._drag_action != "coma_edge":
            self._apply_snapshots(context, 0.0, 0.0)
        self._clear_drag_state()
        layer_stack_utils.tag_view3d_redraw(context)

    def _clear_drag_state(self) -> None:
        self._dragging = False
        self._drag_action = ""
        self._drag_start_x = 0.0
        self._drag_start_y = 0.0
        self._drag_keys = []
        self._snapshots = []
        self._drag_moved = False
        self._edge_drag = None
        self._layer_drag = None
        self._drag_page_id = ""
        self._drag_balloon_id = ""
        self._tail_drag_tail_index = -1
        self._tail_drag_point_index = -1
        self._tail_drag_points = []
        self._last_tail_xy = (0.0, 0.0)
        self._marquee_mode = "single"
        self._marquee_start_x = 0.0
        self._marquee_start_y = 0.0
        self._marquee_current_x = 0.0
        self._marquee_current_y = 0.0
        self._rotate_center = (0.0, 0.0)
        self._rotate_snapshots = []
        self._reparent_start_px = (0.0, 0.0)
        self._reparent_target = None
        self._center_snap_targets = []
        self._original_center = None
        self._center_snap_armed = False

    def _enter_free_transform(self, context) -> None:
        key = object_tool_selection.active_selection_key(context)
        if not key:
            return
        kind = object_selection.parse_key(key)[0]
        if kind not in {"balloon", "effect"}:
            return
        snapshot = self._capture_ft_snapshot(context, key, kind)
        if snapshot is None:
            return
        self._ft_mode = True
        self._ft_key = key
        self._ft_snapshot = snapshot
        wm = context.window_manager
        if hasattr(wm, "bmanga_free_transform_key"):
            wm.bmanga_free_transform_key = key
        context.workspace.status_text_set("自由変形: Enter で確定 / Esc でキャンセル")
        layer_stack_utils.tag_view3d_redraw(context)

    def _confirm_free_transform(self, context) -> None:
        self._ft_mode = False
        self._ft_snapshot = None
        self._ft_key = ""
        object_tool_free_transform.clear_mode(context)
        context.workspace.status_text_set(None)
        try:
            bpy.ops.ed.undo_push(message="B-MANGA: 自由変形")
        except Exception:  # noqa: BLE001
            pass
        layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
        layer_stack_utils.tag_view3d_redraw(context)

    def _cancel_free_transform(self, context) -> None:
        if self._ft_snapshot is not None:
            self._restore_ft_snapshot(context, self._ft_snapshot)
        self._ft_mode = False
        self._ft_snapshot = None
        self._ft_key = ""
        object_tool_free_transform.clear_mode(context)
        context.workspace.status_text_set(None)
        layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
        layer_stack_utils.tag_view3d_redraw(context)

    def _capture_ft_snapshot(self, context, key: str, kind: str) -> dict | None:
        work = get_work(context)
        if work is None:
            return None
        _kind, page_id, item_id = object_selection.parse_key(key)
        if kind == "balloon":
            _pi, _p, _idx, entry = _find_balloon_by_key(work, page_id, item_id)
            if entry is None:
                return None
            tails_snapshot = []
            for tail in getattr(entry, "tails", []) or []:
                pts = [(float(getattr(p, "x_mm", 0)), float(getattr(p, "y_mm", 0))) for p in getattr(tail, "points", []) or []]
                td = {"points": pts}
                if bool(getattr(tail, "custom_points_enabled", False)):
                    td["start"] = (float(getattr(tail, "start_x_mm", 0)), float(getattr(tail, "start_y_mm", 0)))
                    td["end"] = (float(getattr(tail, "end_x_mm", 0)), float(getattr(tail, "end_y_mm", 0)))
                tails_snapshot.append(td)
            return {
                "kind": "balloon",
                "key": key,
                "page_id": page_id,
                "item_id": item_id,
                "offsets": free_transform.entry_offsets(entry),
                "enabled": bool(getattr(entry, "free_transform_enabled", False)),
                "line_width_scale": float(getattr(entry, "free_transform_line_width_scale", 1.0) or 1.0),
                "tails": tails_snapshot,
            }
        if kind == "effect":
            obj, layer = _find_effect_layer(item_id)
            if layer is None:
                return None
            payload = free_transform.effect_payload_for_layer(obj, layer)
            return {
                "kind": "effect",
                "key": key,
                "item_id": item_id,
                "payload": dict(payload) if payload else {},
            }
        return None

    def _restore_ft_snapshot(self, context, snapshot: dict) -> None:
        work = get_work(context)
        if work is None:
            return
        kind = snapshot["kind"]
        if kind == "balloon":
            _pi, _p, _idx, entry = _find_balloon_by_key(
                work, snapshot["page_id"], snapshot["item_id"],
            )
            if entry is None:
                return
            free_transform.set_entry_offsets(
                entry, snapshot["offsets"], enabled=snapshot["enabled"],
            )
            if hasattr(entry, "free_transform_line_width_scale"):
                entry.free_transform_line_width_scale = snapshot["line_width_scale"]
            for ti, tail in enumerate(getattr(entry, "tails", []) or []):
                if ti >= len(snapshot.get("tails", [])):
                    break
                td = snapshot["tails"][ti]
                for pi, point in enumerate(getattr(tail, "points", []) or []):
                    if pi >= len(td.get("points", [])):
                        break
                    point.x_mm, point.y_mm = td["points"][pi]
                if "start" in td and bool(getattr(tail, "custom_points_enabled", False)):
                    tail.start_x_mm, tail.start_y_mm = td["start"]
                    tail.end_x_mm, tail.end_y_mm = td["end"]
            balloon_curve_object.on_balloon_entry_changed(entry)
        elif kind == "effect":
            obj, layer = _find_effect_layer(snapshot["item_id"])
            if layer is None:
                return
            meta = effect_line_op._effect_meta(obj)
            key = effect_line_op._layer_meta_key(layer)
            entry = dict(meta.get(key, {}) if isinstance(meta.get(key, {}), dict) else {})
            free_transform.set_effect_payload_on_meta_entry(entry, snapshot["payload"])
            meta[key] = entry
            effect_line_op._write_effect_meta(obj, meta)
            bounds = effect_line_op.effect_layer_bounds(obj, layer)
            if bounds is not None:
                effect_line_op._write_effect_strokes(context, obj, layer, bounds)

    def _cleanup(self, context) -> None:
        if getattr(self, "_cursor_modal_set", False):
            coma_modal_state.restore_modal_cursor(context)
            self._cursor_modal_set = False
        if getattr(self, "_drag_action", "") == "coma_edge" and self._edge_drag is not None:
            self._edge_drag.cancel()
        elif getattr(self, "_drag_action", "") == "layer_move" and self._layer_drag is not None:
            self._layer_drag.cancel(context)
        elif getattr(self, "_drag_action", "") == "reparent":
            reparent_overlay.clear_hover()
            reparent_overlay.clear_preview()
        self._clear_drag_state()
        object_tool_balloon_tail.clear_pending(self)
        if getattr(self, "_ft_mode", False):
            self._ft_mode = False
            self._ft_snapshot = None
            self._ft_key = ""
            object_tool_free_transform.clear_mode(context)
            try:
                context.workspace.status_text_set(None)
            except Exception:  # noqa: BLE001
                pass

    def finish_from_external(self, context, *, keep_selection: bool) -> None:
        if getattr(self, "_externally_finished", False):
            return
        self._externally_finished = True
        if not keep_selection:
            object_selection.clear(context)
        self._cleanup(context)
        edge_selection.clear_overlay_pointer(context)
        coma_modal_state.clear_active("object_tool", self, context)


_CLASSES = (BMANGA_OT_object_tool,)


def register() -> None:
    bpy.types.WindowManager.bmanga_object_selection_keys = StringProperty(default="")
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    try:
        del bpy.types.WindowManager.bmanga_object_selection_keys
    except AttributeError:
        pass
