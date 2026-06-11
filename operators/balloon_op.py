"""フキダシ関連 Operator (Phase 3 ページ単位対応).

- 各ページの ``page.balloons`` CollectionProperty にフキダシを追加/削除
- invoke ではマウス直下のページを逆引きして active に追随 (overview 対応)
- 親子連動: 子テキスト (``BNameTextEntry.parent_balloon_id`` でリンク) は
  フキダシの移動に合わせて同じ delta で追随する
- 旧 ``Scene.bname_balloons`` (グローバル) は廃止
"""

from __future__ import annotations

import math
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator

from ..core import balloon as balloon_core
from ..core.work import get_active_page, get_work
from ..io import balloon_presets
from ..ui import overlay_creation_range
from ..utils import (
    balloon_curve_object,
    coma_hit_visibility,
    balloon_merge_object,
    balloon_shapes,
    balloon_tail_geom,
    free_transform,
    layer_stack as layer_stack_utils,
    log,
    object_selection,
    page_file_scene,
)
from ..utils import active_target as _active_target


def _focus_creation_target(context, work, page, parent_kind: str, parent_key: str) -> None:
    try:
        _active_target.focus_creation_target(context, work, page, parent_kind, parent_key)
    except Exception:  # noqa: BLE001
        pass
from ..utils.layer_hierarchy import (
    OUTSIDE_STACK_KEY,
    coma_containing_point,
    coma_stack_key,
    page_stack_key,
)
from . import balloon_tail_op, coma_modal_state, selection_context_menu, view_event_region

_logger = log.get_logger(__name__)

_BALLOON_DEFAULT_SHAPE = "ellipse"
_BALLOON_MIN_SIZE_MM = 2.0
_BALLOON_HANDLE_HIT_MM = 2.5
_BALLOON_DRAG_EPS_MM = 0.05
_BALLOON_TAIL_MIN_LENGTH_MM = 2.0
_BALLOON_TAIL_POINT_HIT_MM = 2.5

_SHAPE_FOR_ADD = (
    ("rect", "矩形", ""),
    ("ellipse", "楕円", ""),
    ("cloud", "雲", ""),
    ("fluffy", "もやもや", ""),
    ("thorn", "トゲ（直線）", ""),
    ("thorn-curve", "トゲ（曲線）", ""),
    ("octagon", "八角形", ""),
    ("none", "本体なし (テキスト単体)", ""),
)


def _allocate_balloon_id_from_collection(collection, prefix: str = "balloon") -> str:
    used = {str(getattr(b, "id", "") or "") for b in collection or []}
    i = 1
    while True:
        candidate = f"{prefix}_{i:04d}"
        if candidate not in used:
            return candidate
        i += 1


def _collect_used_balloon_ids(work) -> set[str]:
    """全ページの balloons + shared_balloons で使用中の balloon id を集める."""
    used: set[str] = set()
    if work is None:
        return used
    for p in getattr(work, "pages", []) or []:
        for b in getattr(p, "balloons", []) or []:
            used.add(str(getattr(b, "id", "") or ""))
    for b in getattr(work, "shared_balloons", []) or []:
        used.add(str(getattr(b, "id", "") or ""))
    return used


def _allocate_balloon_id(page, work=None) -> str:
    # フキダシ id はページ横断で一意にする。ページ単位で採番すると別ページの
    # フキダシと id が衝突し、実体オブジェクト名 (id 由来) が重なって 1 ページ目の
    # 位置に作られてしまい、2 ページ目以降では表示されない (保存時の採番し直しで
    # 初めて直る)。作成時点で全ページを走査して未使用 id を割り当てる。
    # work を渡さない呼び出し (フキダシテキスト作成 / レイヤースタック作成 /
    # 複製 / 別ページへの移動など) でもページ横断一意を保つため、ここで補完する。
    if work is None:
        try:
            work = get_work(bpy.context)
        except Exception:  # noqa: BLE001
            work = None
    used = _collect_used_balloon_ids(work)
    used |= {str(getattr(b, "id", "") or "") for b in getattr(page, "balloons", []) or []}
    i = 1
    while True:
        candidate = f"balloon_{i:04d}"
        if candidate not in used:
            return candidate
        i += 1


def _resolve_page_from_event(context, event):
    """event.mouse_x/y の位置からアクティブページを逆引き + local mm 座標を返す.

    戻り値: (work, page, local_x_mm, local_y_mm) or (work, page, None, None)
    VIEW_3D 領域外クリック (N パネル等) の場合は active ページのみ返し、
    mm 座標は None。overview OFF モードなら常に active ページ + None。
    """
    from bpy_extras.view3d_utils import region_2d_to_location_3d

    from ..utils import geom, page_grid

    work = get_work(context)
    page = get_active_page(context)
    if work is None or not work.loaded or page is None:
        return work, page, None, None

    view = view_event_region.view3d_window_under_event(context, event)
    if view is None:
        return work, page, None, None
    _area, region, rv3d, mx, my = view
    loc = region_2d_to_location_3d(region, rv3d, (mx, my), (0.0, 0.0, 0.0))
    if loc is None:
        return work, page, None, None
    x_mm = geom.m_to_mm(loc.x)
    y_mm = geom.m_to_mm(loc.y)
    scene = context.scene
    page_idx = page_grid.page_index_at_world_mm(work, scene, x_mm, y_mm)
    if page_idx is not None and 0 <= page_idx < len(work.pages):
        work.active_page_index = page_idx
        page = work.pages[page_idx]
        cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
        gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
        cw = work.paper.canvas_width_mm
        ch = work.paper.canvas_height_mm
        start_side = getattr(work.paper, "start_side", "right")
        read_direction = getattr(work.paper, "read_direction", "left")
        ox, oy = page_grid.page_grid_offset_mm(
            page_idx, cols, gap, cw, ch, start_side, read_direction, work=work
        )
        add_x, add_y = page_grid.page_manual_offset_mm(page)
        ox += add_x
        oy += add_y
        return work, page, x_mm - ox, y_mm - oy
    return work, page, None, None


def _find_page_with_index_by_id(work, page_id: str):
    if work is None:
        return -1, None
    for i, page in enumerate(work.pages):
        if getattr(page, "id", "") == page_id:
            return i, page
    return -1, None


def _event_world_xy_mm(context, event) -> tuple[float | None, float | None]:
    from bpy_extras.view3d_utils import region_2d_to_location_3d

    from ..utils import geom

    view = view_event_region.view3d_window_under_event(context, event)
    if view is None:
        return None, None
    _area, region, rv3d, mx, my = view
    loc = region_2d_to_location_3d(region, rv3d, (mx, my), (0.0, 0.0, 0.0))
    if loc is None:
        return None, None
    return geom.m_to_mm(loc.x), geom.m_to_mm(loc.y)


def _creation_context_from_event(context, event):
    from ..utils import page_grid

    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return None
    world_x_mm, world_y_mm = _event_world_xy_mm(context, event)
    if world_x_mm is None or world_y_mm is None:
        return None
    scene = context.scene
    page_idx = page_grid.page_index_at_world_mm(work, scene, world_x_mm, world_y_mm)
    if page_idx is not None and 0 <= page_idx < len(work.pages):
        page = work.pages[page_idx]
        work.active_page_index = page_idx
        ox_mm, oy_mm = page_grid.page_total_offset_mm(work, scene, page_idx)
        local_x = float(world_x_mm) - ox_mm
        local_y = float(world_y_mm) - oy_mm
        parent_kind, parent_key = _parent_for_creation_point(page, local_x, local_y)
        return work, page, local_x, local_y, float(world_x_mm), float(world_y_mm), parent_kind, parent_key
    return (
        work,
        None,
        float(world_x_mm),
        float(world_y_mm),
        float(world_x_mm),
        float(world_y_mm),
        "outside",
        OUTSIDE_STACK_KEY,
    )


def _resolve_local_xy_for_page_from_event(context, event, page_id: str):
    from ..utils import page_grid

    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return None, None, None, None
    page_index, page = _find_page_with_index_by_id(work, page_id)
    if page is None:
        return work, None, None, None
    world_x_mm, world_y_mm = _event_world_xy_mm(context, event)
    if world_x_mm is None or world_y_mm is None:
        return work, page, None, None
    ox_mm, oy_mm = page_grid.page_total_offset_mm(work, context.scene, page_index)
    return work, page, world_x_mm - ox_mm, world_y_mm - oy_mm


def _default_position_for(work, page, local_x_mm: float | None, local_y_mm: float | None):
    """配置 mm 座標を決定.

    カーソル解決に成功すればその座標、失敗すればキャンバス中央付近を返す。
    """
    if local_x_mm is not None and local_y_mm is not None:
        return local_x_mm, local_y_mm
    paper = work.paper
    return paper.canvas_width_mm / 2.0, paper.canvas_height_mm / 2.0


def _creation_violates_layer_scope(context, page, x_mm: float, y_mm: float, width_mm: float, height_mm: float) -> bool:
    from ..core.mode import MODE_COMA, MODE_PAGE, get_mode
    from ..utils import layer_stack

    cx = x_mm + width_mm * 0.5
    cy = y_mm + height_mm * 0.5
    mode = get_mode(context)
    if page is None:
        return False
    if mode == MODE_PAGE:
        return False
    if mode == MODE_COMA:
        idx = int(getattr(page, "active_coma_index", -1))
        if not (0 <= idx < len(page.comas)):
            return False
        hit = layer_stack.coma_containing_point(page, cx, cy)
        return (
            hit is None
            or str(getattr(hit, "coma_id", "") or "")
            != str(getattr(page.comas[idx], "coma_id", "") or "")
        )
    return False


def _balloon_rect(entry) -> tuple[float, float, float, float]:
    x = float(getattr(entry, "x_mm", 0.0))
    y = float(getattr(entry, "y_mm", 0.0))
    w = float(getattr(entry, "width_mm", 0.0))
    h = float(getattr(entry, "height_mm", 0.0))
    return x, y, x + w, y + h


def _balloon_hit_part(entry, x_mm: float, y_mm: float) -> str:
    left, bottom, right, top = _balloon_rect(entry)
    width = max(0.0, right - left)
    height = max(0.0, top - bottom)
    threshold = min(
        _BALLOON_HANDLE_HIT_MM,
        max(0.35, min(width, height) * 0.25),
    )
    if not (
        left - threshold <= x_mm <= right + threshold
        and bottom - threshold <= y_mm <= top + threshold
    ):
        if free_transform.entry_enabled(entry):
            quad = free_transform.quad_from_rect_offsets(
                (left, bottom, width, height),
                free_transform.entry_offsets(entry),
            )
            if free_transform.point_in_quad(quad, x_mm, y_mm, tolerance_mm=threshold):
                return "body"
        return ""
    near_left = abs(x_mm - left) <= threshold
    near_right = abs(x_mm - right) <= threshold
    near_bottom = abs(y_mm - bottom) <= threshold
    near_top = abs(y_mm - top) <= threshold
    inside_x = left <= x_mm <= right
    inside_y = bottom <= y_mm <= top
    if near_left and near_top:
        return "top_left"
    if near_right and near_top:
        return "top_right"
    if near_left and near_bottom:
        return "bottom_left"
    if near_right and near_bottom:
        return "bottom_right"
    if near_left and inside_y:
        return "left"
    if near_right and inside_y:
        return "right"
    if near_top and inside_x:
        return "top"
    if near_bottom and inside_x:
        return "bottom"
    cx = left + width * 0.5 + float(getattr(entry, "center_offset_x_mm", 0.0) or 0.0)
    cy = bottom + height * 0.5 + float(getattr(entry, "center_offset_y_mm", 0.0) or 0.0)
    if math.hypot(float(x_mm) - cx, float(y_mm) - cy) <= max(threshold, _BALLOON_HANDLE_HIT_MM):
        return "center"
    if inside_x and inside_y:
        return "body"
    if free_transform.entry_enabled(entry):
        quad = free_transform.quad_from_rect_offsets(
            (left, bottom, width, height),
            free_transform.entry_offsets(entry),
        )
        if free_transform.point_in_quad(quad, x_mm, y_mm, tolerance_mm=threshold):
            return "body"
    return ""


def _hit_balloon_collection(collection, active_idx: int, x_mm: float, y_mm: float, page=None):
    indices: list[int] = []
    if collection is None:
        return -1, None, ""
    if 0 <= active_idx < len(collection):
        indices.append(active_idx)
    indices.extend(i for i in reversed(range(len(collection))) if i != active_idx)
    for idx in indices:
        entry = collection[idx]
        if getattr(entry, "shape", "rect") == "none":
            continue
        if not coma_hit_visibility.local_point_visible_in_entry_parent(page, entry, x_mm, y_mm):
            continue
        part = _balloon_tail_hit_part(entry, x_mm, y_mm)
        if part:
            return idx, entry, part
        part = _balloon_hit_part(entry, x_mm, y_mm)
        if part:
            return idx, entry, part
    return -1, None, ""



def _hit_balloon_entry(page, x_mm: float, y_mm: float):
    return _hit_balloon_collection(
        getattr(page, "balloons", None),
        int(getattr(page, "active_balloon_index", -1)),
        x_mm,
        y_mm,
        page,
    )


def _hit_shared_balloon_entry(work, x_mm: float, y_mm: float):
    return _hit_balloon_collection(getattr(work, "shared_balloons", None), -1, x_mm, y_mm)


def _balloon_tail_hit_part(entry, x_mm: float, y_mm: float) -> str:
    rect = _tail_rect_for_entry(entry)
    local_x = float(x_mm) - rect.x
    local_y = float(y_mm) - rect.y
    threshold = _BALLOON_TAIL_POINT_HIT_MM
    for tail_index, tail in enumerate(getattr(entry, "tails", []) or []):
        points = balloon_tail_geom.tail_local_points(tail)
        if len(points) < 2:
            points = [(x - rect.x, y - rect.y) for x, y in balloon_tail_geom.tail_world_points(rect, tail)]
        for point_index, (px, py) in enumerate(points):
            if math.hypot(local_x - px, local_y - py) <= threshold:
                return f"tail_point:{tail_index}:{point_index}"
        segment_hit = _hit_tail_segment(points, local_x, local_y, threshold)
        if segment_hit >= 0:
            return f"tail_segment:{tail_index}:{segment_hit + 1}"
    return ""


def _hit_tail_segment(points: list[tuple[float, float]], x_mm: float, y_mm: float, threshold: float) -> int:
    best_index = -1
    best_distance = threshold
    for index, (p0, p1) in enumerate(zip(points, points[1:])):
        distance = _distance_to_segment((x_mm, y_mm), p0, p1)
        if distance <= best_distance:
            best_distance = distance
            best_index = index
    return best_index


def _distance_to_segment(point, p0, p1) -> float:
    px, py = point
    x0, y0 = p0
    x1, y1 = p1
    dx = x1 - x0
    dy = y1 - y0
    length_sq = dx * dx + dy * dy
    if length_sq <= 1.0e-9:
        return math.hypot(px - x0, py - y0)
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / length_sq))
    cx = x0 + dx * t
    cy = y0 + dy * t
    return math.hypot(px - cx, py - cy)


def _tail_rect_for_entry(entry):
    from ..utils.balloon_shapes import Rect

    return Rect(
        float(getattr(entry, "x_mm", 0.0) or 0.0),
        float(getattr(entry, "y_mm", 0.0) or 0.0),
        float(getattr(entry, "width_mm", 0.0) or 0.0),
        float(getattr(entry, "height_mm", 0.0) or 0.0),
    )


def _clear_balloon_selection(page) -> None:
    for entry in getattr(page, "balloons", []):
        if hasattr(entry, "selected"):
            entry.selected = False


def _clear_balloon_collection_selection(collection) -> None:
    for entry in collection or []:
        if hasattr(entry, "selected"):
            entry.selected = False


def _clear_all_balloon_selections(work) -> None:
    if work is None:
        return
    for page in getattr(work, "pages", []) or []:
        _clear_balloon_collection_selection(getattr(page, "balloons", None))
    _clear_balloon_collection_selection(getattr(work, "shared_balloons", None))


def _set_balloon_object_selection(context, page, entry, *, mode: str) -> None:
    key = object_selection.balloon_key(page, entry)
    current = object_selection.get_keys(context)
    if mode == "toggle":
        current = [item for item in current if item != key]
        if bool(getattr(entry, "selected", False)):
            current.append(key)
    elif mode == "add":
        current = current if key in current else [*current, key]
    else:
        current = [key]
    object_selection.set_keys(context, current)


def _selected_balloon_indices(page) -> list[int]:
    return [
        i for i, entry in enumerate(getattr(page, "balloons", []))
        if bool(getattr(entry, "selected", False))
    ]


def _selected_balloon_indices_in_collection(collection) -> list[int]:
    return [
        i for i, entry in enumerate(collection or [])
        if bool(getattr(entry, "selected", False))
    ]


def _select_balloon_index(context, work, page, index: int, *, mode: str = "single") -> bool:
    collection = getattr(page, "balloons", None) if page is not None else getattr(work, "shared_balloons", None)
    if collection is None or not (0 <= index < len(collection)):
        return False
    entry = collection[index]
    if mode == "single":
        _clear_all_balloon_selections(work)
        entry.selected = True
    elif mode == "toggle":
        entry.selected = not bool(getattr(entry, "selected", False))
        if not _selected_balloon_indices_in_collection(collection):
            entry.selected = True
    elif mode == "add":
        entry.selected = True
    if page is not None:
        page.active_balloon_index = index
    if work is not None and page is not None:
        for page_index, candidate in enumerate(work.pages):
            if candidate == page or getattr(candidate, "id", "") == getattr(page, "id", ""):
                work.active_page_index = page_index
                break
    if hasattr(context.scene, "bname_active_layer_kind"):
        context.scene.bname_active_layer_kind = "balloon"
    if hasattr(context.scene, "bname_active_gp_folder_key"):
        context.scene.bname_active_gp_folder_key = ""
    _sync_active_balloon_stack_item(context, page, entry)
    _set_balloon_object_selection(context, page, entry, mode=mode)
    return True


def _sync_active_balloon_stack_item(context, page, entry) -> None:
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    page_key = OUTSIDE_STACK_KEY if page is None else page_stack_key(page)
    uid = layer_stack_utils.target_uid(
        "balloon",
        f"{page_key}:{getattr(entry, 'id', '')}",
    )
    if stack is not None:
        for i, item in enumerate(stack):
            if layer_stack_utils.stack_item_uid(item) == uid:
                layer_stack_utils.set_active_stack_index_silently(context, i)
                break
    layer_stack_utils.remember_layer_stack_signature(context)
    layer_stack_utils.tag_view3d_redraw(context)


def _move_balloon_with_texts(page, entry, x_mm: float, y_mm: float) -> None:
    dx = float(x_mm) - float(getattr(entry, "x_mm", 0.0))
    dy = float(y_mm) - float(getattr(entry, "y_mm", 0.0))
    if abs(dx) <= 1.0e-9 and abs(dy) <= 1.0e-9:
        return
    in_merge_group = bool(str(getattr(entry, "merge_group_id", "") or ""))
    with balloon_curve_object.defer_auto_sync():
        entry.x_mm = float(x_mm)
        entry.y_mm = float(y_mm)
    scene = bpy.context.scene
    work = get_work(bpy.context)
    if not balloon_curve_object.sync_balloon_object_transform_only(scene, work, page, entry):
        with balloon_curve_object.suspend_auto_sync():
            balloon_curve_object.on_balloon_entry_changed(entry)
    _sync_balloon_merge_display_if_needed(page, entry)
    bid = str(getattr(entry, "id", "") or "")
    for text in getattr(page, "texts", []):
        if getattr(text, "parent_balloon_id", "") == bid:
            text.x_mm += dx
            text.y_mm += dy


def _sync_balloon_merge_display_if_needed(page, entry) -> None:
    if page is None or not str(getattr(entry, "merge_group_id", "") or ""):
        return
    try:
        scene = bpy.context.scene
        balloon_merge_object.sync_group_for_entry(scene, get_work(bpy.context), page, entry)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon merge display sync failed")


def _text_rect(entry) -> tuple[float, float, float, float]:
    x = float(getattr(entry, "x_mm", 0.0) or 0.0)
    y = float(getattr(entry, "y_mm", 0.0) or 0.0)
    w = max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0))
    h = max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0))
    return x, y, x + w, y + h


def _rect_contains_rect(
    outer: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
    *,
    tolerance_mm: float = 0.05,
) -> bool:
    outer_left, outer_bottom, outer_right, outer_top = outer
    inner_left, inner_bottom, inner_right, inner_top = inner
    tolerance = max(0.0, float(tolerance_mm))
    return (
        inner_left >= outer_left - tolerance
        and inner_right <= outer_right + tolerance
        and inner_bottom >= outer_bottom - tolerance
        and inner_top <= outer_top + tolerance
    )


def _attach_texts_enclosed_by_balloon(context, page, entry) -> int:
    if page is None or entry is None:
        return 0
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return 0
    balloon_rect = _balloon_rect(entry)
    changed = 0
    for text in getattr(page, "texts", []) or []:
        if not bool(getattr(text, "visible", True)):
            continue
        if str(getattr(text, "parent_balloon_id", "") or ""):
            continue
        if not _rect_contains_rect(balloon_rect, _text_rect(text)):
            continue
        text.parent_balloon_id = balloon_id
        changed += 1
    if changed:
        layer_stack_utils.sync_layer_stack_after_data_change(context)
    return changed


def _set_balloon_rect(page, entry, x: float, y: float, width: float, height: float, *, propagate_link: bool = True) -> None:
    old_rect = (
        float(getattr(entry, "x_mm", 0.0) or 0.0),
        float(getattr(entry, "y_mm", 0.0) or 0.0),
        max(_BALLOON_MIN_SIZE_MM, float(getattr(entry, "width_mm", 0.0) or 0.0)),
        max(_BALLOON_MIN_SIZE_MM, float(getattr(entry, "height_mm", 0.0) or 0.0)),
    )
    new_rect = (
        float(x),
        float(y),
        max(_BALLOON_MIN_SIZE_MM, float(width)),
        max(_BALLOON_MIN_SIZE_MM, float(height)),
    )
    transformed_curve = balloon_curve_object.transform_manual_curve_to_rect(entry, old_rect, new_rect)
    with balloon_curve_object.defer_auto_sync():
        _move_balloon_with_texts(page, entry, new_rect[0], new_rect[1])
        entry.width_mm = new_rect[2]
        entry.height_mm = new_rect[3]
    with balloon_curve_object.suspend_auto_sync():
        balloon_curve_object.on_balloon_entry_changed(entry)
    _sync_balloon_merge_display_if_needed(page, entry)
    if propagate_link:
        try:
            from . import layer_link_duplicate_op

            layer_link_duplicate_op.propagate_linked_balloon_transform_absolute(bpy.context, page, entry)
        except Exception:  # noqa: BLE001
            pass
    if transformed_curve:
        layer_stack_utils.tag_view3d_redraw(bpy.context)


def _parent_for_creation_point(page, x_mm: float, y_mm: float) -> tuple[str, str]:
    panel = coma_containing_point(page, x_mm, y_mm)
    if panel is not None:
        return "coma", coma_stack_key(page, panel)
    return "page", page_stack_key(page)


def _create_balloon_entry(
    context,
    page,
    *,
    shape: str,
    x: float,
    y: float,
    w: float,
    h: float,
    parent_kind: str = "",
    parent_key: str = "",
):
    work = get_work(context)
    if page is None:
        if work is None:
            return None
        collection = getattr(work, "shared_balloons", None)
        if collection is None:
            return None
        entry = collection.add()
        entry.id = _allocate_balloon_id_from_collection(collection, "shared_balloon")
        default_parent_kind = "outside"
        default_parent_key = ""
    else:
        collection = page.balloons
        entry = collection.add()
        entry.id = _allocate_balloon_id(page, work)
        default_parent_kind = "page"
        default_parent_key = page_stack_key(page)
    entry.shape = balloon_shapes.normalize_shape(shape)
    balloon_core.apply_balloon_shape_defaults(entry, force=True)
    entry.x_mm = float(x)
    entry.y_mm = float(y)
    entry.width_mm = max(_BALLOON_MIN_SIZE_MM, float(w))
    entry.height_mm = max(_BALLOON_MIN_SIZE_MM, float(h))
    entry.rounded_corner_enabled = (entry.shape == "rect")
    entry.corner_type = "rounded" if entry.rounded_corner_enabled else "square"
    entry.corner_type_initialized = True
    entry.parent_kind = str(parent_kind or default_parent_kind)
    entry.parent_key = "" if entry.parent_kind in {"outside", "none"} else str(parent_key or default_parent_key)
    if page is not None:
        page.active_balloon_index = len(page.balloons) - 1
    _clear_all_balloon_selections(work)
    entry.selected = True
    if hasattr(context.scene, "bname_active_layer_kind"):
        context.scene.bname_active_layer_kind = "balloon"
    object_selection.set_keys(context, [object_selection.balloon_key(page, entry)])
    layer_stack_utils.sync_layer_stack_after_data_change(context)
    _sync_active_balloon_stack_item(context, page, entry)
    return entry


def _delete_balloon_by_id(context, page_id: str, balloon_id: str) -> None:
    work = get_work(context)
    _page_index, page = _find_page_with_index_by_id(work, page_id)
    if page is None:
        return
    for i, entry in enumerate(page.balloons):
        if getattr(entry, "id", "") == balloon_id:
            try:
                from ..utils import balloon_curve_object

                balloon_curve_object.remove_balloon_objects_by_id(balloon_id)
            except Exception:  # noqa: BLE001
                _logger.exception("delete balloon object failed: %s", balloon_id)
            page.balloons.remove(i)
            page.active_balloon_index = min(i, len(page.balloons) - 1) if len(page.balloons) else -1
            layer_stack_utils.sync_layer_stack_after_data_change(context)
            return


def _allocate_merge_group_id(page) -> str:
    used = {str(getattr(entry, "merge_group_id", "") or "") for entry in page.balloons}
    i = 1
    while True:
        candidate = f"balloon_group_{i:04d}"
        if candidate not in used:
            return candidate
        i += 1


def _selected_balloon_entries(page) -> list[object]:
    return [page.balloons[i] for i in _selected_balloon_indices(page)]


def _find_balloon_index(page, balloon_id: str) -> int:
    for i, entry in enumerate(getattr(page, "balloons", [])):
        if getattr(entry, "id", "") == balloon_id:
            return i
    return -1


def _find_balloon_index_in_collection(collection, balloon_id: str) -> int:
    for i, entry in enumerate(collection or []):
        if getattr(entry, "id", "") == balloon_id:
            return i
    return -1


def _rect_from_points(x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    left = min(float(x0), float(x1))
    right = max(float(x0), float(x1))
    bottom = min(float(y0), float(y1))
    top = max(float(y0), float(y1))
    return (
        left,
        bottom,
        max(_BALLOON_MIN_SIZE_MM, right - left),
        max(_BALLOON_MIN_SIZE_MM, top - bottom),
    )


def _point_in_balloon_rect(entry, x_mm: float, y_mm: float) -> bool:
    left, bottom, right, top = _balloon_rect(entry)
    return left <= x_mm <= right and bottom <= y_mm <= top


def _add_tail_to_point(entry, tip_x: float, tip_y: float) -> bool:
    left, bottom, right, top = _balloon_rect(entry)
    cx = (left + right) * 0.5
    cy = (bottom + top) * 0.5
    rx = max((right - left) * 0.5, 0.01)
    ry = max((top - bottom) * 0.5, 0.01)
    vx = float(tip_x) - cx
    vy = float(tip_y) - cy
    distance = math.hypot(vx, vy)
    if distance <= _BALLOON_TAIL_MIN_LENGTH_MM:
        return False
    dx = vx / distance
    dy = vy / distance
    denom = math.hypot(dx / rx, dy / ry)
    base_x = cx + (dx / denom) if denom > 0 else cx
    base_y = cy + (dy / denom) if denom > 0 else cy
    length = math.hypot(float(tip_x) - base_x, float(tip_y) - base_y)
    if length <= _BALLOON_TAIL_MIN_LENGTH_MM:
        return False
    tail = entry.tails.add()
    tail.type = "straight"
    tail.direction_deg = math.degrees(math.atan2(dy, dx))
    tail.length_mm = length
    tail.root_width_mm = max(3.0, min(10.0, min(rx, ry) * 0.35))
    tail.tip_width_mm = 0.0
    return True


def _add_tail_polyline(entry, points_page: list[tuple[float, float]]) -> int:
    if len(points_page) < 2:
        return -1
    local = _page_points_to_tail_local(entry, points_page)
    if math.hypot(local[-1][0] - local[0][0], local[-1][1] - local[0][1]) <= _BALLOON_TAIL_MIN_LENGTH_MM:
        return -1
    tail = entry.tails.add()
    tail.type = "straight"
    left, bottom, right, top = _balloon_rect(entry)
    tail.root_width_mm = max(3.0, min(10.0, min(right - left, top - bottom) * 0.18))
    tail.tip_width_mm = 0.0
    balloon_tail_geom.write_polyline_points(tail, local)
    return len(entry.tails) - 1


def _page_points_to_tail_local(entry, points_page: list[tuple[float, float]]) -> list[tuple[float, float]]:
    ox = float(getattr(entry, "x_mm", 0.0) or 0.0)
    oy = float(getattr(entry, "y_mm", 0.0) or 0.0)
    return [(float(x) - ox, float(y) - oy) for x, y in points_page]


def _insert_tail_point_page(entry, tail_index: int, insert_index: int, x_mm: float, y_mm: float) -> int:
    if not (0 <= int(tail_index) < len(entry.tails)):
        return -1
    tail = entry.tails[int(tail_index)]
    if len(balloon_tail_geom.tail_local_points(tail)) < 2:
        balloon_tail_geom.write_polyline_points(tail, list(balloon_tail_geom.local_axis_points(entry, tail)))
    local = (float(x_mm) - float(getattr(entry, "x_mm", 0.0) or 0.0), float(y_mm) - float(getattr(entry, "y_mm", 0.0) or 0.0))
    return balloon_tail_geom.add_polyline_point(tail, local, insert_index=int(insert_index))


def _append_tail_point_page(entry, tail_index: int, x_mm: float, y_mm: float) -> int:
    if not (0 <= int(tail_index) < len(entry.tails)):
        return -1
    tail = entry.tails[int(tail_index)]
    if len(balloon_tail_geom.tail_local_points(tail)) < 2:
        balloon_tail_geom.write_polyline_points(tail, list(balloon_tail_geom.local_axis_points(entry, tail)))
    local = (float(x_mm) - float(getattr(entry, "x_mm", 0.0) or 0.0), float(y_mm) - float(getattr(entry, "y_mm", 0.0) or 0.0))
    return balloon_tail_geom.add_polyline_point(tail, local, insert_index=len(tail.points))


def _event_in_view3d_window(context, event) -> bool:
    return view_event_region.is_view3d_window_event(context, event)


class BNAME_OT_balloon_add(Operator):
    bl_idname = "bname.balloon_add"
    bl_label = "フキダシを追加"
    bl_options = {"REGISTER", "UNDO"}

    shape: EnumProperty(  # type: ignore[valid-type]
        name="形状",
        items=_SHAPE_FOR_ADD,
        default="rect",
    )
    x_mm: FloatProperty(name="X (mm)", default=0.0)  # type: ignore[valid-type]
    y_mm: FloatProperty(name="Y (mm)", default=0.0)  # type: ignore[valid-type]
    width_mm: FloatProperty(name="幅 (mm)", default=40.0, min=0.1)  # type: ignore[valid-type]
    height_mm: FloatProperty(name="高さ (mm)", default=20.0, min=0.1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(
            work is not None
            and work.loaded
            and get_active_page(context) is not None
            and page_file_scene.is_page_edit_scene(getattr(context, "scene", None))
        )

    def invoke(self, context, event):
        work, page, lx, ly = _resolve_page_from_event(context, event)
        if work is None or page is None:
            self.report({"ERROR"}, "ページが選択されていません")
            return {"CANCELLED"}
        cx, cy = _default_position_for(work, page, lx, ly)
        # 追加時はカーソル位置を左下ではなく中央と解釈し、規定サイズで周囲に広げる
        self.x_mm = cx - self.width_mm / 2.0
        self.y_mm = cy - self.height_mm / 2.0
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            self.report({"ERROR"}, "ページが選択されていません")
            return {"CANCELLED"}
        if _creation_violates_layer_scope(
            context, page, self.x_mm, self.y_mm, self.width_mm, self.height_mm
        ):
            self.report({"ERROR"}, "このモードではその位置にフキダシを作成できません")
            return {"CANCELLED"}
        entry = _create_balloon_entry(
            context,
            page,
            shape=self.shape,
            x=self.x_mm,
            y=self.y_mm,
            w=self.width_mm,
            h=self.height_mm,
        )
        self.report({"INFO"}, f"フキダシ追加: {entry.id} ({self.shape})")
        return {"FINISHED"}


class BNAME_OT_balloon_remove(Operator):
    bl_idname = "bname.balloon_remove"
    bl_label = "フキダシを削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        if page is None:
            return False
        return 0 <= page.active_balloon_index < len(page.balloons)

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_balloon_index
        if not (0 <= idx < len(page.balloons)):
            return {"CANCELLED"}
        bid = page.balloons[idx].id
        try:
            from ..utils import balloon_curve_object

            balloon_curve_object.remove_balloon_objects_by_id(bid)
        except Exception:  # noqa: BLE001
            _logger.exception("delete balloon object failed: %s", bid)
        # 子テキストの parent_balloon_id をクリア (孤立テキスト化)
        for txt in page.texts:
            if txt.parent_balloon_id == bid:
                txt.parent_balloon_id = ""
        page.balloons.remove(idx)
        if len(page.balloons) == 0:
            page.active_balloon_index = -1
        elif idx >= len(page.balloons):
            page.active_balloon_index = len(page.balloons) - 1
        if len(page.balloons) == 0 and hasattr(context.scene, "bname_active_layer_kind"):
            context.scene.bname_active_layer_kind = "gp"
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        self.report({"INFO"}, f"フキダシ削除: {bid}")
        return {"FINISHED"}


class BNAME_OT_balloon_tail_add(Operator):
    bl_idname = "bname.balloon_tail_add"
    bl_label = "尻尾を追加"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        if page is None:
            return False
        return 0 <= page.active_balloon_index < len(page.balloons)

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_balloon_index
        if not (0 <= idx < len(page.balloons)):
            return {"CANCELLED"}
        entry = page.balloons[idx]
        tail = entry.tails.add()
        tail.type = "straight"
        tail.length_mm = 6.0
        tail.root_width_mm = 3.0
        layer_stack_utils.tag_view3d_redraw(context)
        return {"FINISHED"}


class BNAME_OT_balloon_move(Operator):
    """アクティブフキダシを delta だけ平行移動. 子テキストも連動.

    UI の数値ドラッグではなく、親子連動を保証するための専用オペレータ。
    N パネルのフキダシ詳細 UI から x_mm/y_mm を直接編集した場合は
    連動しない (ユーザーが意図的に独立移動したとみなす)。
    """

    bl_idname = "bname.balloon_move"
    bl_label = "フキダシを平行移動"
    bl_options = {"REGISTER", "UNDO"}

    delta_x_mm: FloatProperty(name="ΔX (mm)", default=0.0)  # type: ignore[valid-type]
    delta_y_mm: FloatProperty(name="ΔY (mm)", default=0.0)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        if page is None:
            return False
        return 0 <= page.active_balloon_index < len(page.balloons)

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_balloon_index
        if not (0 <= idx < len(page.balloons)):
            return {"CANCELLED"}
        entry = page.balloons[idx]
        dx = float(self.delta_x_mm)
        dy = float(self.delta_y_mm)
        _move_balloon_with_texts(page, entry, entry.x_mm + dx, entry.y_mm + dy)
        try:
            from . import layer_link_duplicate_op

            layer_link_duplicate_op.propagate_linked_balloon_move_delta(context, page, entry, dx, dy)
        except Exception:  # noqa: BLE001
            pass
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return {"FINISHED"}


class BNAME_OT_balloon_merge_selected(Operator):
    bl_idname = "bname.balloon_merge_selected"
    bl_label = "フキダシを結合"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return page is not None and len(_selected_balloon_indices(page)) >= 2

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            return {"CANCELLED"}
        entries = _selected_balloon_entries(page)
        if len(entries) < 2:
            self.report({"ERROR"}, "結合するフキダシを2つ以上選択してください")
            return {"CANCELLED"}
        group_id = _allocate_merge_group_id(page)
        for entry in entries:
            entry.merge_group_id = group_id
            entry.selected = True
        first_id = str(getattr(entries[0], "id", "") or "")
        page.active_balloon_index = next(
            (i for i, item in enumerate(page.balloons) if getattr(item, "id", "") == first_id),
            page.active_balloon_index,
        )
        if hasattr(context.scene, "bname_active_layer_kind"):
            context.scene.bname_active_layer_kind = "balloon"
        try:
            from ..utils import balloon_merge_object

            balloon_merge_object.sync_groups_for_page(context.scene, get_work(context), page)
        except Exception:  # noqa: BLE001
            _logger.exception("balloon merge display sync failed")
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        self.report({"INFO"}, f"フキダシを結合: {group_id}")
        return {"FINISHED"}


class BNAME_OT_balloon_tool(Operator):
    bl_idname = "bname.balloon_tool"
    bl_label = "フキダシツール"
    bl_options = {"REGISTER", "UNDO"}

    _externally_finished: bool
    _cursor_modal_set: bool
    _dragging: bool
    _drag_action: str
    _drag_page_id: str
    _drag_balloon_id: str
    _drag_start_x: float
    _drag_start_y: float
    _drag_start_world_x: float
    _drag_start_world_y: float
    _drag_last_x: float
    _drag_last_y: float
    _drag_parent_kind: str
    _drag_parent_key: str
    _drag_moved: bool
    _snapshots: list[tuple[str, float, float, float, float]]

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(
            work is not None
            and work.loaded
            and get_active_page(context) is not None
            and page_file_scene.is_page_edit_scene(getattr(context, "scene", None))
        )

    def invoke(self, context, _event):
        if coma_modal_state.get_active("balloon_tool") is not None:
            return {"FINISHED"}
        coma_modal_state.exit_drawing_mode(context)
        coma_modal_state.finish_active("coma_vertex_edit", context, keep_selection=True)
        coma_modal_state.finish_active("knife_cut", context, keep_selection=False)
        coma_modal_state.finish_active("edge_move", context, keep_selection=True)
        coma_modal_state.finish_active("layer_move", context, keep_selection=True)
        coma_modal_state.finish_active("text_tool", context, keep_selection=True)
        coma_modal_state.finish_active("effect_line_tool", context, keep_selection=True)
        self._externally_finished = False
        self._cursor_modal_set = coma_modal_state.set_modal_cursor(context, "CROSSHAIR")
        self._clear_drag_state()
        self._clear_tail_polyline_state()
        context.window_manager.modal_handler_add(self)
        coma_modal_state.set_active("balloon_tool", self, context)
        self.report({"INFO"}, "フキダシツール: ドラッグで作成")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if getattr(self, "_externally_finished", False):
            coma_modal_state.clear_active("balloon_tool", self, context)
            return {"FINISHED", "PASS_THROUGH"}
        if view_event_region.toggle_modal_sidebar_if_requested(context, event):
            return {"RUNNING_MODAL"}
        if getattr(self, "_dragging", False):
            return self._modal_dragging(context, event)
        if view_event_region.modal_navigation_ui_passthrough(self, context, event):
            return {"PASS_THROUGH"}
        if not _event_in_view3d_window(context, event):
            return {"PASS_THROUGH"}
        if event.type == "RIGHTMOUSE" and event.value == "PRESS":
            if self._open_tail_point_menu(context, event):
                return {"RUNNING_MODAL"}
            if selection_context_menu.open_for_balloon_tool(context, event):
                return {"RUNNING_MODAL"}
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        if event.type == "ESC" and event.value == "PRESS":
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED"}
        if self._should_leave_for_tool_key(event):
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED", "PASS_THROUGH"}
        if event.type != "LEFTMOUSE" or event.value != "PRESS":
            return {"PASS_THROUGH"}
        return self._handle_left_press(context, event)

    def _should_leave_for_tool_key(self, event) -> bool:
        return (
            event.value == "PRESS"
            and event.type in {"O", "P", "F", "K", "T"}
            and not event.ctrl
            and not event.alt
        )

    def _handle_left_press(self, context, event):
        work, page, lx, ly = _resolve_page_from_event(context, event)
        if work is None:
            return {"PASS_THROUGH"}
        hit_index, hit_entry, hit_part = (-1, None, "")
        if page is not None and lx is not None and ly is not None:
            hit_index, hit_entry, hit_part = _hit_balloon_entry(page, lx, ly)
        elif lx is None or ly is None:
            wx, wy = _event_world_xy_mm(context, event)
            if wx is not None and wy is not None:
                hit_index, hit_entry, hit_part = _hit_shared_balloon_entry(work, wx, wy)
                if hit_entry is not None:
                    page = None
                    lx, ly = wx, wy
        if event.ctrl:
            if page is None or lx is None or ly is None:
                return {"RUNNING_MODAL"}
            return self._handle_ctrl_left_press(context, work, page, lx, ly, hit_index, hit_entry, hit_part)
        self._clear_tail_polyline_state()
        if hit_entry is not None and hit_index >= 0:
            mode = "toggle" if event.ctrl else "add" if event.shift else "single"
            if (
                mode == "single"
                and hit_part == "body"
                and bool(getattr(hit_entry, "selected", False))
                and len(_selected_balloon_indices(page)) >= 2
            ):
                mode = "add"
            _select_balloon_index(context, work, page, hit_index, mode=mode)
            if event.alt and hit_part == "body":
                self._start_tail_drag(page, hit_entry, lx, ly)
            elif not (event.ctrl or event.shift):
                self._start_balloon_drag(page, hit_entry, hit_part, lx, ly)
            return {"RUNNING_MODAL"}
        if event.alt or event.ctrl or event.shift:
            return {"RUNNING_MODAL"}
        create_ctx = _creation_context_from_event(context, event)
        if create_ctx is None:
            return {"PASS_THROUGH"}
        work, page, lx, ly, wx, wy, parent_kind, parent_key = create_ctx
        if page is not None and _creation_violates_layer_scope(
            context, page, lx, ly, _BALLOON_MIN_SIZE_MM, _BALLOON_MIN_SIZE_MM
        ):
            self.report({"ERROR"}, "このモードではその位置にフキダシを作成できません")
            return {"RUNNING_MODAL"}
        # 作成位置のコマ (またはページ直下) を active 階層にも反映し、
        # Outliner Collection ハイライトを同期する
        if page is not None:
            _focus_creation_target(context, work, page, parent_kind, parent_key)
        self._start_create_preview(
            page,
            lx,
            ly,
            wx,
            wy,
            parent_kind,
            parent_key,
        )
        return {"RUNNING_MODAL"}

    def _handle_ctrl_left_press(self, context, work, page, lx: float, ly: float, hit_index: int, hit_entry, hit_part: str):
        if hit_entry is not None and hit_index >= 0:
            _select_balloon_index(context, work, page, hit_index, mode="single")
            if hit_part == "body":
                self._start_tail_drag(page, hit_entry, lx, ly, start_at_pointer=True)
                return {"RUNNING_MODAL"}
            if hit_part.startswith("tail_segment:"):
                _prefix, tail_index, insert_index = hit_part.split(":")
                if _insert_tail_point_page(hit_entry, int(tail_index), int(insert_index), lx, ly) >= 0:
                    self._push_undo_step("B-Name: しっぽ制御点追加")
                    layer_stack_utils.sync_layer_stack_after_data_change(context)
                return {"RUNNING_MODAL"}
            if hit_part.startswith("tail_point:"):
                _prefix, tail_index, point_index = hit_part.split(":")
                self._start_tail_point_drag(page, hit_entry, int(tail_index), int(point_index), lx, ly)
                return {"RUNNING_MODAL"}
        if self._append_pending_tail_click(context, page, lx, ly):
            return {"RUNNING_MODAL"}
        return {"RUNNING_MODAL"}

    def _open_tail_point_menu(self, context, event) -> bool:
        work, page, lx, ly = _resolve_page_from_event(context, event)
        if work is None or page is None or lx is None or ly is None:
            return False
        hit_index, entry, part = _hit_balloon_entry(page, lx, ly)
        if entry is None or hit_index < 0 or not str(part).startswith("tail_point:"):
            return False
        _prefix, tail_index, point_index = str(part).split(":")
        tail = entry.tails[int(tail_index)]
        if len(balloon_tail_geom.tail_local_points(tail)) < 2:
            balloon_tail_geom.write_polyline_points(tail, list(balloon_tail_geom.local_axis_points(entry, tail)))
        return balloon_tail_op.open_tail_point_context_menu(
            context,
            str(getattr(page, "id", "") or ""),
            str(getattr(entry, "id", "") or ""),
            int(tail_index),
            int(point_index),
        )

    def _start_create_preview(
        self,
        page,
        local_x_mm: float,
        local_y_mm: float,
        world_x_mm: float,
        world_y_mm: float,
        parent_kind: str,
        parent_key: str,
    ) -> None:
        self._dragging = True
        self._drag_action = "create_preview"
        self._drag_page_id = getattr(page, "id", "") if page is not None else OUTSIDE_STACK_KEY
        self._drag_balloon_id = ""
        self._drag_start_x = float(local_x_mm)
        self._drag_start_y = float(local_y_mm)
        self._drag_start_world_x = float(world_x_mm)
        self._drag_start_world_y = float(world_y_mm)
        self._drag_last_x = float(local_x_mm)
        self._drag_last_y = float(local_y_mm)
        self._drag_parent_kind = str(parent_kind or "")
        self._drag_parent_key = str(parent_key or "")
        self._drag_moved = False
        self._snapshots = []
        overlay_creation_range.set_bounds(
            (world_x_mm, world_y_mm, _BALLOON_MIN_SIZE_MM, _BALLOON_MIN_SIZE_MM)
        )

    def _start_tail_drag(self, page, entry, x_mm: float, y_mm: float, *, start_at_pointer: bool = False) -> None:
        self._dragging = True
        self._drag_action = "tail"
        self._drag_page_id = getattr(page, "id", "")
        self._drag_balloon_id = getattr(entry, "id", "")
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._tail_start_at_pointer = bool(start_at_pointer)
        self._drag_moved = False
        self._snapshots = []

    def _start_tail_point_drag(self, page, entry, tail_index: int, point_index: int, x_mm: float, y_mm: float) -> None:
        self._dragging = True
        self._drag_action = "tail_point"
        self._drag_page_id = getattr(page, "id", "")
        self._drag_balloon_id = getattr(entry, "id", "")
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._tail_drag_tail_index = int(tail_index)
        self._tail_drag_point_index = int(point_index)
        tail = entry.tails[int(tail_index)]
        points = balloon_tail_geom.tail_local_points(tail)
        if len(points) < 2:
            points = list(balloon_tail_geom.local_axis_points(entry, tail))
            balloon_tail_geom.write_polyline_points(tail, points)
        self._tail_drag_points = points
        self._drag_moved = False
        self._snapshots = []

    def _start_balloon_drag(self, page, entry, part: str, x_mm: float, y_mm: float) -> None:
        self._dragging = True
        self._drag_action = "move" if part == "body" else part
        self._drag_page_id = getattr(page, "id", "") if page is not None else OUTSIDE_STACK_KEY
        self._drag_balloon_id = getattr(entry, "id", "")
        self._drag_start_x = float(x_mm)
        self._drag_start_y = float(y_mm)
        self._drag_orig_center_offset_x = float(getattr(entry, "center_offset_x_mm", 0.0) or 0.0)
        self._drag_orig_center_offset_y = float(getattr(entry, "center_offset_y_mm", 0.0) or 0.0)
        self._drag_moved = False
        self._snapshots = self._make_snapshots(page, entry)

    def _make_snapshots(self, page, entry) -> list[tuple[str, float, float, float, float]]:
        work = get_work(bpy.context)
        collection = getattr(page, "balloons", None) if page is not None else getattr(work, "shared_balloons", None)
        if bool(getattr(entry, "selected", False)) and self._drag_action == "move":
            indices = [
                i for i, item in enumerate(collection or [])
                if bool(getattr(item, "selected", False))
            ]
        else:
            indices = [_find_balloon_index_in_collection(collection, getattr(entry, "id", ""))]
        snapshots = []
        for idx in indices:
            if collection is not None and 0 <= idx < len(collection):
                item = collection[idx]
                snapshots.append((item.id, item.x_mm, item.y_mm, item.width_mm, item.height_mm))
        return snapshots

    def _clear_drag_state(self) -> None:
        self._dragging = False
        self._drag_action = ""
        self._drag_page_id = ""
        self._drag_balloon_id = ""
        self._drag_start_x = 0.0
        self._drag_start_y = 0.0
        self._drag_start_world_x = 0.0
        self._drag_start_world_y = 0.0
        self._drag_last_x = 0.0
        self._drag_last_y = 0.0
        self._drag_parent_kind = ""
        self._drag_parent_key = ""
        self._tail_start_at_pointer = False
        self._tail_drag_tail_index = -1
        self._tail_drag_point_index = -1
        self._tail_drag_points = []
        self._drag_orig_center_offset_x = 0.0
        self._drag_orig_center_offset_y = 0.0
        self._drag_moved = False
        self._snapshots = []
        overlay_creation_range.clear()

    def _clear_tail_polyline_state(self) -> None:
        self._pending_tail_page_id = ""
        self._pending_tail_balloon_id = ""
        self._pending_tail_points = []
        self._pending_tail_index = -1

    def _append_pending_tail_click(self, context, page, x_mm: float, y_mm: float) -> bool:
        page_id = str(getattr(page, "id", "") or "")
        balloon_id = str(getattr(self, "_pending_tail_balloon_id", "") or "")
        if not page_id or not balloon_id or page_id != str(getattr(self, "_pending_tail_page_id", "") or ""):
            return False
        idx = _find_balloon_index(page, balloon_id)
        if idx < 0:
            self._clear_tail_polyline_state()
            return False
        entry = page.balloons[idx]
        points = list(getattr(self, "_pending_tail_points", []) or [])
        points.append((float(x_mm), float(y_mm)))
        tail_index = int(getattr(self, "_pending_tail_index", -1))
        if tail_index < 0:
            tail_index = _add_tail_polyline(entry, points)
            if tail_index < 0:
                return False
            self._pending_tail_index = tail_index
            self._pending_tail_points = points
            self._push_undo_step("B-Name: しっぽ作成")
        else:
            appended = _append_tail_point_page(entry, tail_index, x_mm, y_mm)
            if appended < 0:
                return False
            self._pending_tail_points = [
                (float(getattr(point, "x_mm", 0.0) or 0.0) + float(entry.x_mm), float(getattr(point, "y_mm", 0.0) or 0.0) + float(entry.y_mm))
                for point in entry.tails[tail_index].points
            ]
            self._push_undo_step("B-Name: しっぽ制御点追加")
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return True

    def _modal_dragging(self, context, event):
        if event.type == "MOUSEMOVE":
            self._update_drag(context, event)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self._update_drag(context, event)
            self._finish_drag(context, event)
            return {"RUNNING_MODAL"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._cancel_drag(context)
            return {"RUNNING_MODAL"}
        return {"RUNNING_MODAL"}

    def _drag_page_and_entry(self, context):
        work = get_work(context)
        if str(getattr(self, "_drag_page_id", "") or "") == OUTSIDE_STACK_KEY:
            idx = _find_balloon_index_in_collection(
                getattr(work, "shared_balloons", None),
                self._drag_balloon_id,
            )
            entry = work.shared_balloons[idx] if work is not None and 0 <= idx < len(work.shared_balloons) else None
            return None, entry
        _page_index, page = _find_page_with_index_by_id(work, self._drag_page_id)
        if page is None:
            return None, None
        idx = _find_balloon_index(page, self._drag_balloon_id)
        entry = page.balloons[idx] if 0 <= idx < len(page.balloons) else None
        return page, entry

    def _update_drag(self, context, event) -> None:
        if self._drag_action == "create_preview":
            self._update_create_preview(context, event)
            return
        page, entry = self._drag_page_and_entry(context)
        if entry is None:
            self._clear_drag_state()
            return
        if page is None:
            work = get_work(context)
            lx, ly = _event_world_xy_mm(context, event)
            if work is None or lx is None or ly is None:
                return
        else:
            work, current_page, lx, ly = _resolve_local_xy_for_page_from_event(
                context, event, getattr(page, "id", "")
            )
            if work is None or current_page is None or lx is None or ly is None:
                return
        dx = float(lx) - self._drag_start_x
        dy = float(ly) - self._drag_start_y
        if abs(dx) > _BALLOON_DRAG_EPS_MM or abs(dy) > _BALLOON_DRAG_EPS_MM:
            self._drag_moved = True
        if self._drag_action == "tail":
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if self._drag_action == "tail_point":
            tail_index = int(getattr(self, "_tail_drag_tail_index", -1))
            point_index = int(getattr(self, "_tail_drag_point_index", -1))
            points = list(getattr(self, "_tail_drag_points", []) or [])
            if 0 <= tail_index < len(entry.tails) and 0 <= point_index < len(points):
                new_point = (points[point_index][0] + dx, points[point_index][1] + dy)
                balloon_tail_geom.set_point(entry.tails[tail_index], point_index, new_point)
            layer_stack_utils.tag_view3d_redraw(context)
            return
        if self._drag_action == "create":
            x, y, w, h = _rect_from_points(self._drag_start_x, self._drag_start_y, lx, ly)
            if _creation_violates_layer_scope(context, page, x, y, w, h):
                return
            _set_balloon_rect(page, entry, x, y, w, h)
        elif self._drag_action == "center":
            with balloon_curve_object.suspend_auto_sync():
                if hasattr(entry, "center_offset_x_mm"):
                    entry.center_offset_x_mm = self._drag_orig_center_offset_x + dx
                if hasattr(entry, "center_offset_y_mm"):
                    entry.center_offset_y_mm = self._drag_orig_center_offset_y + dy
            balloon_curve_object.on_balloon_entry_changed(entry)
            _sync_balloon_merge_display_if_needed(page, entry)
            try:
                from . import layer_link_duplicate_op

                layer_link_duplicate_op.propagate_linked_balloon_center_free(context, page, entry)
            except Exception:  # noqa: BLE001
                pass
        elif self._drag_action == "move":
            if self._move_violates_layer_scope(context, page, dx, dy):
                return
            self._apply_move_snapshots(context, page, dx, dy)
        else:
            x, y, w, h = self._resize_result_rect(entry, dx, dy)
            if _creation_violates_layer_scope(context, page, x, y, w, h):
                return
            _set_balloon_rect(page, entry, x, y, w, h)
        layer_stack_utils.tag_view3d_redraw(context)

    def _update_create_preview(self, context, event) -> None:
        world_x_mm, world_y_mm = _event_world_xy_mm(context, event)
        if world_x_mm is None or world_y_mm is None:
            return
        page = self._drag_page_for_create(context)
        if page is None:
            lx, ly = float(world_x_mm), float(world_y_mm)
        else:
            lx, ly = self._local_xy_for_page(context, page, world_x_mm, world_y_mm)
        dx = float(lx) - self._drag_start_x
        dy = float(ly) - self._drag_start_y
        if abs(dx) > _BALLOON_DRAG_EPS_MM or abs(dy) > _BALLOON_DRAG_EPS_MM:
            self._drag_moved = True
        self._drag_last_x = float(lx)
        self._drag_last_y = float(ly)
        overlay_creation_range.set_bounds(
            _rect_from_points(
                self._drag_start_world_x,
                self._drag_start_world_y,
                float(world_x_mm),
                float(world_y_mm),
            )
        )
        layer_stack_utils.tag_view3d_redraw(context)

    def _drag_page_for_create(self, context):
        if str(getattr(self, "_drag_page_id", "") or "") == OUTSIDE_STACK_KEY:
            return None
        work = get_work(context)
        _page_index, page = _find_page_with_index_by_id(work, self._drag_page_id)
        return page

    def _local_xy_for_page(self, context, page, world_x_mm: float, world_y_mm: float) -> tuple[float, float]:
        from ..utils import page_grid

        work = get_work(context)
        if work is None or page is None:
            return float(world_x_mm), float(world_y_mm)
        page_id = str(getattr(page, "id", "") or "")
        for index, candidate in enumerate(getattr(work, "pages", []) or []):
            if str(getattr(candidate, "id", "") or "") == page_id:
                ox_mm, oy_mm = page_grid.page_total_offset_mm(work, context.scene, index)
                return float(world_x_mm) - ox_mm, float(world_y_mm) - oy_mm
        return float(world_x_mm), float(world_y_mm)

    def _finish_drag(self, context, event) -> None:
        if self._drag_action == "create_preview":
            self._finish_create_preview(context)
            return
        page, entry = self._drag_page_and_entry(context)
        moved = bool(getattr(self, "_drag_moved", False))
        action = self._drag_action
        if action == "create" and not moved:
            _delete_balloon_by_id(context, self._drag_page_id, self._drag_balloon_id)
        elif action == "tail" and moved and page is not None and entry is not None:
            self._finish_tail_drag(context, event, page, entry)
        elif action == "tail" and page is not None and entry is not None and bool(getattr(self, "_tail_start_at_pointer", False)):
            self._start_pending_tail_click(context, page, entry, self._drag_start_x, self._drag_start_y)
        elif action == "tail_point" and moved:
            self._push_undo_step("B-Name: しっぽ制御点移動")
            layer_stack_utils.sync_layer_stack_after_data_change(context)
        elif moved:
            self._push_undo_step("B-Name: フキダシ編集")
            layer_stack_utils.sync_layer_stack_after_data_change(context)
        else:
            layer_stack_utils.tag_view3d_redraw(context)
        self._clear_drag_state()

    def _finish_tail_drag(self, context, event, page, entry) -> None:
        _work, _page, lx, ly = _resolve_local_xy_for_page_from_event(
            context, event, getattr(page, "id", "")
        )
        if lx is None or ly is None:
            return
        if bool(getattr(self, "_tail_start_at_pointer", False)):
            tail_index = _add_tail_polyline(entry, [(self._drag_start_x, self._drag_start_y), (float(lx), float(ly))])
            if tail_index >= 0:
                self._clear_tail_polyline_state()
                self._push_undo_step("B-Name: しっぽ作成")
                layer_stack_utils.sync_layer_stack_after_data_change(context)
            return
        if _point_in_balloon_rect(entry, lx, ly):
            return
        if _add_tail_to_point(entry, lx, ly):
            self._push_undo_step("B-Name: フキダシしっぽ作成")
            layer_stack_utils.sync_layer_stack_after_data_change(context)

    def _start_pending_tail_click(self, context, page, entry, x_mm: float, y_mm: float) -> None:
        self._pending_tail_page_id = str(getattr(page, "id", "") or "")
        self._pending_tail_balloon_id = str(getattr(entry, "id", "") or "")
        self._pending_tail_points = [(float(x_mm), float(y_mm))]
        self._pending_tail_index = -1
        layer_stack_utils.tag_view3d_redraw(context)

    def _apply_move_snapshots(self, context, page, dx: float, dy: float) -> None:
        work = get_work(context)
        collection = getattr(page, "balloons", None) if page is not None else getattr(work, "shared_balloons", None)
        page_key = OUTSIDE_STACK_KEY if page is None else page_stack_key(page)
        snapshot_uids = {
            layer_stack_utils.target_uid("balloon", f"{page_key}:{balloon_id}")
            for balloon_id, *_rest in self._snapshots
        }
        linked_updated: set[str] = set()
        for balloon_id, x, y, _w, _h in self._snapshots:
            idx = _find_balloon_index_in_collection(collection, balloon_id)
            if collection is not None and 0 <= idx < len(collection):
                entry = collection[idx]
                old_x = float(getattr(entry, "x_mm", 0.0) or 0.0)
                old_y = float(getattr(entry, "y_mm", 0.0) or 0.0)
                _move_balloon_with_texts(page, entry, x + dx, y + dy)
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
                        skip_uids=snapshot_uids,
                        updated_uids=linked_updated,
                    )
                except Exception:  # noqa: BLE001
                    pass

    def _move_violates_layer_scope(self, context, page, dx: float, dy: float) -> bool:
        if page is None:
            return False
        for _balloon_id, x, y, w, h in self._snapshots:
            if _creation_violates_layer_scope(context, page, x + dx, y + dy, w, h):
                return True
        return False

    def _resize_result_rect(self, entry, dx: float, dy: float) -> tuple[float, float, float, float]:
        _bid, x, y, w, h = self._snapshots[0]
        right = x + w
        top = y + h
        new_left = x
        new_right = right
        new_bottom = y
        new_top = top
        action = self._drag_action
        if "left" in action:
            new_left = min(right - _BALLOON_MIN_SIZE_MM, x + dx)
        if "right" in action:
            new_right = max(x + _BALLOON_MIN_SIZE_MM, right + dx)
        if "bottom" in action:
            new_bottom = min(top - _BALLOON_MIN_SIZE_MM, y + dy)
        if "top" in action:
            new_top = max(y + _BALLOON_MIN_SIZE_MM, top + dy)
        return new_left, new_bottom, new_right - new_left, new_top - new_bottom

    def _cancel_drag(self, context) -> None:
        if self._drag_action == "create_preview":
            self._clear_drag_state()
            layer_stack_utils.tag_view3d_redraw(context)
            return
        page, entry = self._drag_page_and_entry(context)
        if self._drag_action == "tail_point" and page is not None and entry is not None:
            tail_index = int(getattr(self, "_tail_drag_tail_index", -1))
            points = list(getattr(self, "_tail_drag_points", []) or [])
            if 0 <= tail_index < len(entry.tails) and len(points) >= 2:
                balloon_tail_geom.write_polyline_points(entry.tails[tail_index], points)
        else:
            work = get_work(context)
            collection = getattr(page, "balloons", None) if page is not None else getattr(work, "shared_balloons", None)
            for balloon_id, x, y, w, h in self._snapshots:
                idx = _find_balloon_index_in_collection(collection, balloon_id)
                if collection is not None and 0 <= idx < len(collection):
                    _set_balloon_rect(page, collection[idx], x, y, w, h)
                    if self._drag_action == "center":
                        with balloon_curve_object.suspend_auto_sync():
                            collection[idx].center_offset_x_mm = self._drag_orig_center_offset_x
                            collection[idx].center_offset_y_mm = self._drag_orig_center_offset_y
                        balloon_curve_object.on_balloon_entry_changed(collection[idx])
                        _sync_balloon_merge_display_if_needed(page, collection[idx])
                        try:
                            from . import layer_link_duplicate_op

                            layer_link_duplicate_op.propagate_linked_balloon_center_free(context, page, collection[idx])
                        except Exception:  # noqa: BLE001
                            pass
        self._clear_drag_state()
        layer_stack_utils.tag_view3d_redraw(context)

    def _push_undo_step(self, message: str) -> None:
        try:
            bpy.ops.ed.undo_push(message=message)
        except Exception:  # noqa: BLE001
            _logger.exception("balloon_tool: undo_push failed")

    def _cleanup(self, context) -> None:
        if getattr(self, "_cursor_modal_set", False):
            coma_modal_state.restore_modal_cursor(context)
            self._cursor_modal_set = False
        self._clear_drag_state()

    def _finish_create_preview(self, context) -> None:
        moved = bool(getattr(self, "_drag_moved", False))
        if moved:
            page = self._drag_page_for_create(context)
            x, y, w, h = _rect_from_points(
                self._drag_start_x,
                self._drag_start_y,
                self._drag_last_x,
                self._drag_last_y,
            )
            if page is not None and _creation_violates_layer_scope(context, page, x, y, w, h):
                self.report({"ERROR"}, "このモードではその位置にフキダシを作成できません")
            else:
                entry = _create_balloon_entry(
                    context,
                    page,
                    shape=_BALLOON_DEFAULT_SHAPE,
                    x=x,
                    y=y,
                    w=w,
                    h=h,
                    parent_kind=str(getattr(self, "_drag_parent_kind", "") or ("outside" if page is None else "page")),
                    parent_key=str(getattr(self, "_drag_parent_key", "") or ""),
                )
                if entry is not None:
                    _attach_texts_enclosed_by_balloon(context, page, entry)
                    self._push_undo_step("B-Name: フキダシ作成")
        else:
            layer_stack_utils.tag_view3d_redraw(context)
        self._clear_drag_state()
        self._clear_tail_polyline_state()

    def finish_from_external(self, context, *, keep_selection: bool) -> None:
        _ = keep_selection
        if getattr(self, "_externally_finished", False):
            return
        self._externally_finished = True
        self._cleanup(context)
        coma_modal_state.clear_active("balloon_tool", self, context)


class BNAME_OT_balloon_save_preset(Operator):
    """選択中フキダシの形状をカスタムプリセット JSON として保存."""

    bl_idname = "bname.balloon_save_preset"
    bl_label = "カスタム形状として保存"
    bl_options = {"REGISTER"}

    preset_name: StringProperty(name="プリセット名", default="新規フキダシ")  # type: ignore[valid-type]
    description: StringProperty(name="説明", default="")  # type: ignore[valid-type]
    absolute_coords: BoolProperty(name="絶対座標で登録", default=False)  # type: ignore[valid-type]
    to_global: BoolProperty(  # type: ignore[valid-type]
        name="グローバルに登録",
        description="ON: <addon>/presets/balloons/ に保存 / OFF: 作品ローカル",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        if page is None:
            return False
        return 0 <= page.active_balloon_index < len(page.balloons)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        page = get_active_page(context)
        if page is None:
            return {"CANCELLED"}
        idx = page.active_balloon_index
        entry = page.balloons[idx]
        # Phase 3 骨格: 矩形 4 頂点を保存。パスツール実装後は任意形状へ。
        verts = [
            (entry.x_mm, entry.y_mm),
            (entry.x_mm + entry.width_mm, entry.y_mm),
            (entry.x_mm + entry.width_mm, entry.y_mm + entry.height_mm),
            (entry.x_mm, entry.y_mm + entry.height_mm),
        ]
        try:
            if self.to_global:
                out = balloon_presets.save_global_preset(
                    self.preset_name, self.description, verts, self.absolute_coords
                )
            else:
                work = get_work(context)
                if work is None or not work.loaded or not work.work_dir:
                    self.report({"ERROR"}, "ローカル保存には作品を開く必要があります")
                    return {"CANCELLED"}
                out = balloon_presets.save_local_preset(
                    Path(work.work_dir),
                    self.preset_name,
                    self.description,
                    verts,
                    self.absolute_coords,
                )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("balloon_save_preset failed")
            self.report({"ERROR"}, f"保存失敗: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"フキダシプリセット保存: {out.name}")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_balloon_add,
    BNAME_OT_balloon_remove,
    BNAME_OT_balloon_tail_add,
    BNAME_OT_balloon_move,
    BNAME_OT_balloon_merge_selected,
    BNAME_OT_balloon_tool,
    BNAME_OT_balloon_save_preset,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
