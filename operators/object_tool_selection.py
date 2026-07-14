"""Selection bounds and hit helpers for the B-MANGA object tool."""

from __future__ import annotations

from collections.abc import Callable

import bpy

from ..core.work import get_work
from ..utils import (
    active_collection_sync,
    balloon_curve_object,
    coma_hit_visibility,
    edge_selection,
    empty_layer_object,
    free_transform,
    gp_layer_parenting as gp_parent,
    layer_object_model,
    layer_stack as layer_stack_utils,
    object_naming as on,
    object_selection,
    page_grid,
    page_range,
)
from ..utils.geom import Rect
from ..utils.layer_hierarchy import OUTSIDE_STACK_KEY
from . import effect_line_op

SELECTION_HANDLE_OUTSET_MM = object_selection.SELECTION_HANDLE_OUTSET_MM


def coma_identity(panel) -> str:
    return str(getattr(panel, "coma_id", "") or getattr(panel, "id", "") or "")


def find_page_by_id(work, page_id: str):
    if work is None:
        return -1, None
    for i, page in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(page, "id", "") or "") == str(page_id or ""):
            return i, page
    return -1, None


def find_coma_by_key(work, page_id: str, coma_id: str):
    page_index, page = find_page_by_id(work, page_id)
    if page is None:
        return -1, None, -1, None
    for i, panel in enumerate(getattr(page, "comas", []) or []):
        if coma_identity(panel) == str(coma_id or ""):
            return page_index, page, i, panel
    return page_index, page, -1, None


def find_balloon_by_key(work, page_id: str, item_id: str):
    page_index, page = find_page_by_id(work, page_id)
    if page is None:
        return -1, None, -1, None
    for i, entry in enumerate(getattr(page, "balloons", []) or []):
        if str(getattr(entry, "id", "") or "") == str(item_id or ""):
            return page_index, page, i, entry
    return page_index, page, -1, None


def find_text_by_key(work, page_id: str, item_id: str):
    page_index, page = find_page_by_id(work, page_id)
    if page is None:
        return -1, None, -1, None
    for i, entry in enumerate(getattr(page, "texts", []) or []):
        if str(getattr(entry, "id", "") or "") == str(item_id or ""):
            return page_index, page, i, entry
    return page_index, page, -1, None


def find_shared_coma_by_key(work, item_id: str):
    if work is None:
        return -1, None
    for i, panel in enumerate(getattr(work, "shared_comas", []) or []):
        if coma_identity(panel) == str(item_id or "") or str(getattr(panel, "id", "") or "") == str(item_id or ""):
            return i, panel
    return -1, None


def find_shared_balloon_by_key(work, item_id: str):
    if work is None:
        return -1, None
    for i, entry in enumerate(getattr(work, "shared_balloons", []) or []):
        if str(getattr(entry, "id", "") or "") == str(item_id or ""):
            return i, entry
    return -1, None


def find_shared_text_by_key(work, item_id: str):
    if work is None:
        return -1, None
    for i, entry in enumerate(getattr(work, "shared_texts", []) or []):
        if str(getattr(entry, "id", "") or "") == str(item_id or ""):
            return i, entry
    return -1, None


def find_image_by_key(context, item_id: str):
    coll = getattr(getattr(context, "scene", None), "bmanga_image_layers", None)
    if coll is None:
        return -1, None
    for i, entry in enumerate(coll):
        if str(getattr(entry, "id", "") or "") == str(item_id or ""):
            return i, entry
    return -1, None


def find_image_path_by_key(context, item_id: str):
    coll = getattr(getattr(context, "scene", None), "bmanga_image_path_layers", None)
    if coll is None:
        return -1, None
    for i, entry in enumerate(coll):
        if str(getattr(entry, "id", "") or "") == str(item_id or ""):
            return i, entry
    return -1, None


def find_raster_by_key(context, item_id: str):
    coll = getattr(getattr(context, "scene", None), "bmanga_raster_layers", None)
    if coll is None:
        return -1, None
    for i, entry in enumerate(coll):
        if str(getattr(entry, "id", "") or "") == str(item_id or ""):
            return i, entry
    return -1, None


def find_fill_by_key(context, item_id: str):
    coll = getattr(getattr(context, "scene", None), "bmanga_fill_layers", None)
    if coll is None:
        return -1, None
    for i, entry in enumerate(coll):
        if str(getattr(entry, "id", "") or "") == str(item_id or ""):
            return i, entry
    return -1, None


def find_gp_layer(key: str):
    obj = layer_object_model.find_layer_object("gp", key)
    return obj, layer_object_model.content_layer(obj)


def find_effect_layer(key: str):
    obj = layer_object_model.find_layer_object("effect", key)
    return obj, layer_object_model.content_layer(obj)


def _page_for_image_entry(work, entry):
    page_key = page_key_for_entry(entry)
    _page_index, page = page_index_for_key(work, page_key)
    return page


def _select_managed_object(context, obj) -> bool:
    if obj is None:
        return False
    try:
        obj.select_set(True)
        context.view_layer.objects.active = obj
    except Exception:  # noqa: BLE001
        return False
    return True


def _managed_object_for_key(context, key: str):
    work = get_work(context)
    kind, page_id, item_id = object_selection.parse_key(key)
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    if kind == "image":
        obj = on.find_object_by_bmanga_id(item_id, kind="image")
        if obj is not None:
            return obj
        _idx, entry = find_image_by_key(context, item_id)
        page = _page_for_image_entry(work, entry) if entry is not None else None
        return empty_layer_object.ensure_image_empty_object(scene=scene, entry=entry, page=page)
    if kind == "image_path":
        obj = on.find_object_by_bmanga_id(item_id, kind="image_path")
        if obj is not None:
            return obj
        _idx, entry = find_image_path_by_key(context, item_id)
        if entry is not None:
            from ..utils import image_path_object

            page = image_path_object.page_for_entry(scene, work, entry)
            return image_path_object.ensure_image_path_object(scene=scene, entry=entry, page=page)
        return None
    if kind == "text":
        from ..utils import text_real_object

        bmanga_page_id = (
            text_real_object.OUTSIDE_PAGE_ID
            if page_id == OUTSIDE_STACK_KEY
            else page_id
        )
        obj = on.find_object_by_bmanga_id(
            text_real_object.text_object_bmanga_id_for_values(bmanga_page_id, item_id),
            kind="text",
        )
        if obj is not None:
            return obj
        if page_id == OUTSIDE_STACK_KEY:
            _idx, entry = find_shared_text_by_key(work, item_id)
            page = None
        else:
            _page_index, page, _idx, entry = find_text_by_key(work, page_id, item_id)
        return text_real_object.ensure_text_real_object(scene=scene, entry=entry, page=page)
    if kind == "balloon":
        obj = on.find_object_by_bmanga_id(item_id, kind="balloon")
        if obj is not None:
            return obj
        if page_id == OUTSIDE_STACK_KEY:
            _idx, entry = find_shared_balloon_by_key(work, item_id)
            page = None
        else:
            _page_index, page, _idx, entry = find_balloon_by_key(work, page_id, item_id)
        return balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
    if kind == "raster":
        obj = on.find_object_by_bmanga_id(item_id, kind="raster")
        if obj is not None:
            return obj
        _idx, entry = find_raster_by_key(context, item_id)
        if entry is None:
            return None
        from . import raster_layer_op

        return raster_layer_op.ensure_raster_plane(context, entry)
    if kind == "fill":
        obj = on.find_object_by_bmanga_id(item_id, kind="fill")
        if obj is not None:
            return obj
        _idx, entry = find_fill_by_key(context, item_id)
        if entry is not None:
            from ..utils.fill_real_object import ensure_fill_real_object, page_for_entry
            scene = getattr(context, "scene", None)
            page = page_for_entry(scene, work, entry)
            return ensure_fill_real_object(scene=scene, entry=entry, page=page)
        return None
    if kind == "gp":
        obj, layer = find_gp_layer(item_id)
        if obj is not None and layer is not None:
            try:
                obj.data.layers.active = layer
            except Exception:  # noqa: BLE001
                pass
        return obj
    if kind == "effect":
        obj, layer = find_effect_layer(item_id)
        if obj is not None and layer is not None:
            try:
                obj.data.layers.active = layer
            except Exception:  # noqa: BLE001
                pass
            try:
                from ..utils import effect_line_object as _elo

                display = _elo.find_effect_display_object(obj)
                if display is not None:
                    return display
            except Exception:  # noqa: BLE001
                pass
        return obj
    return None


def sync_outliner_selection_for_keys(context, keys) -> None:
    """Viewport selection -> Outliner object/collection selection."""
    scene = getattr(context, "scene", None)
    view_layer = getattr(context, "view_layer", None)
    if scene is None or view_layer is None:
        return
    key_list = [str(key or "") for key in (keys or []) if str(key or "")]
    for obj in tuple(on.iter_managed_objects()):
        try:
            obj.select_set(False)
        except Exception:  # noqa: BLE001
            pass
    active_obj = None
    for key in key_list:
        kind, page_id, item_id = object_selection.parse_key(key)
        if kind == "page":
            active_collection_sync.request_active_coma(context, item_id, "")
            continue
        if kind == "coma":
            if page_id == OUTSIDE_STACK_KEY:
                try:
                    from ..utils import coma_plane

                    obj = coma_plane.find_coma_plane_object(
                        coma_plane.OUTSIDE_PAGE_ID,
                        item_id,
                    )
                except Exception:  # noqa: BLE001
                    obj = None
                if _select_managed_object(context, obj):
                    active_obj = obj
                continue
            active_collection_sync.request_active_coma(context, page_id, item_id)
            continue
        obj = _managed_object_for_key(context, key)
        if _select_managed_object(context, obj):
            active_obj = obj
    if active_obj is not None:
        try:
            view_layer.objects.active = active_obj
        except Exception:  # noqa: BLE001
            pass
    try:
        from ..utils.fill_real_object import sync_gradient_handle_visibility
        sync_gradient_handle_visibility(context)
    except Exception:  # noqa: BLE001
        pass
    object_selection.tag_view3d_redraw(context)


def rect_contains_point(rect: Rect, x_mm: float, y_mm: float, pad: float = 0.0) -> bool:
    return (
        rect.x - pad <= x_mm <= rect.x2 + pad
        and rect.y - pad <= y_mm <= rect.y2 + pad
    )


def handle_rect_for_bounds(rect: Rect) -> Rect:
    return rect.inset(-SELECTION_HANDLE_OUTSET_MM)


def object_world_rect_mm(obj) -> Rect | None:
    if obj is None:
        return None
    try:
        from mathutils import Vector

        coords = [obj.matrix_world @ Vector(corner) for corner in getattr(obj, "bound_box", [])]
    except Exception:  # noqa: BLE001
        coords = []
    if not coords:
        return None
    xs = [float(co.x) * 1000.0 for co in coords]
    ys = [float(co.y) * 1000.0 for co in coords]
    min_x = min(xs)
    min_y = min(ys)
    return Rect(min_x, min_y, max(xs) - min_x, max(ys) - min_y)


def _parent_for_hit_key(context, key: str) -> tuple[str, str]:
    work = get_work(context)
    kind, page_id, item_id = object_selection.parse_key(key)
    if kind == "balloon":
        if page_id == OUTSIDE_STACK_KEY:
            return "", ""
        _pi, _page, _idx, entry = find_balloon_by_key(work, page_id, item_id)
        return str(getattr(entry, "parent_kind", "") or ""), str(getattr(entry, "parent_key", "") or "")
    if kind == "text":
        if page_id == OUTSIDE_STACK_KEY:
            return "", ""
        _pi, _page, _idx, entry = find_text_by_key(work, page_id, item_id)
        return str(getattr(entry, "parent_kind", "") or ""), str(getattr(entry, "parent_key", "") or "")
    if kind == "image":
        _idx, entry = find_image_by_key(context, item_id)
        return str(getattr(entry, "parent_kind", "") or ""), str(getattr(entry, "parent_key", "") or "")
    if kind == "image_path":
        _idx, entry = find_image_path_by_key(context, item_id)
        return str(getattr(entry, "parent_kind", "") or ""), str(getattr(entry, "parent_key", "") or "")
    if kind == "raster":
        _idx, entry = find_raster_by_key(context, item_id)
        return str(getattr(entry, "parent_kind", "") or ""), str(getattr(entry, "parent_key", "") or "")
    if kind == "fill":
        _idx, entry = find_fill_by_key(context, item_id)
        return str(getattr(entry, "parent_kind", "") or ""), str(getattr(entry, "parent_key", "") or "")
    if kind == "gp":
        _obj, layer = find_gp_layer(item_id)
        parent_key = gp_parent.parent_key(layer)
        return "coma" if ":" in parent_key else "page", parent_key
    if kind == "effect":
        obj, layer = find_effect_layer(item_id)
        parent_key = gp_parent.parent_key(layer) if layer is not None else ""
        parent_key = parent_key or str(obj.get(on.PROP_PARENT_KEY, "") or "") if obj is not None else parent_key
        return "coma" if ":" in parent_key else "page", parent_key
    return "", ""


def hit_visible_at_world(context, hit: dict | None, x_mm: float, y_mm: float) -> bool:
    if hit is None:
        return False
    key = str(hit.get("key", "") or "")
    if not key:
        return True
    parent_kind, parent_key = _parent_for_hit_key(context, key)
    return coma_hit_visibility.world_point_visible_in_parent(context, parent_kind, parent_key, x_mm, y_mm)


def rect_intersects(a: Rect, b: Rect) -> bool:
    return not (a.x2 < b.x or b.x2 < a.x or a.y2 < b.y or b.y2 < a.y)


def rect_contains_rect(outer: Rect, inner: Rect) -> bool:
    return (
        outer.x <= inner.x
        and inner.x2 <= outer.x2
        and outer.y <= inner.y
        and inner.y2 <= outer.y2
    )


def hit_part_for_rect(rect: Rect, x_mm: float, y_mm: float, threshold: float = 2.5) -> str:
    handle_rect = handle_rect_for_bounds(rect)
    if not rect_contains_point(handle_rect, x_mm, y_mm, threshold):
        return ""
    near_left = abs(x_mm - handle_rect.x) <= threshold
    near_right = abs(x_mm - handle_rect.x2) <= threshold
    near_bottom = abs(y_mm - handle_rect.y) <= threshold
    near_top = abs(y_mm - handle_rect.y2) <= threshold
    inside_x = handle_rect.x <= x_mm <= handle_rect.x2
    inside_y = handle_rect.y <= y_mm <= handle_rect.y2
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
    if rect_contains_point(rect, x_mm, y_mm):
        return "body"
    return ""


def page_offset_mm(context, work, page_index: int) -> tuple[float, float]:
    try:
        return page_grid.page_total_offset_mm(work, context.scene, page_index)
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


def page_key_for_entry(entry) -> str:
    parent_key = str(getattr(entry, "parent_key", "") or "")
    if parent_key:
        return parent_key.split(":", 1)[0]
    return ""


def page_index_for_key(work, page_key: str) -> tuple[int, object | None]:
    if work is None or not page_key:
        return -1, None
    for i, page in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(page, "id", "") or "") == str(page_key):
            return i, page
    return -1, None


def parent_offset_mm(context, work, parent_key: str) -> tuple[float, float]:
    page_key = str(parent_key or "").split(":", 1)[0]
    page_index, _page = page_index_for_key(work, page_key)
    if page_index < 0:
        return 0.0, 0.0
    return page_offset_mm(context, work, page_index)


def entry_page_offset_mm(context, work, page_index: int, entry=None) -> tuple[float, float]:
    parent_kind = str(getattr(entry, "parent_kind", "") or "") if entry is not None else ""
    parent_key = str(getattr(entry, "parent_key", "") or "") if entry is not None else ""
    if parent_kind in {"page", "coma"} and parent_key:
        return parent_offset_mm(context, work, parent_key)
    return page_offset_mm(context, work, page_index)


def world_rect_for_page_entry(
    context,
    work,
    page_index: int,
    entry,
    *,
    use_parent: bool = True,
) -> Rect:
    if use_parent:
        ox, oy = entry_page_offset_mm(context, work, page_index, entry)
    else:
        ox, oy = page_offset_mm(context, work, page_index)
    return Rect(
        float(getattr(entry, "x_mm", 0.0)) + ox,
        float(getattr(entry, "y_mm", 0.0)) + oy,
        float(getattr(entry, "width_mm", 0.0)),
        float(getattr(entry, "height_mm", 0.0)),
    )


def coma_world_rect(context, work, page_index: int, panel) -> Rect:
    ox, oy = page_offset_mm(context, work, page_index)
    if str(getattr(panel, "shape_type", "") or "") == "rect":
        return Rect(
            float(getattr(panel, "rect_x_mm", 0.0)) + ox,
            float(getattr(panel, "rect_y_mm", 0.0)) + oy,
            float(getattr(panel, "rect_width_mm", 0.0)),
            float(getattr(panel, "rect_height_mm", 0.0)),
        )
    points = [
        (float(getattr(v, "x_mm", 0.0)), float(getattr(v, "y_mm", 0.0)))
        for v in getattr(panel, "vertices", []) or []
    ]
    if not points:
        return Rect(ox, oy, 0.0, 0.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return Rect(min(xs) + ox, min(ys) + oy, max(xs) - min(xs), max(ys) - min(ys))


def page_world_rect(context, work, page_index: int) -> Rect | None:
    if work is None or not (0 <= int(page_index) < len(getattr(work, "pages", []) or [])):
        return None
    ox, oy = page_offset_mm(context, work, int(page_index))
    paper = getattr(work, "paper", None)
    if paper is None:
        return None
    cw = float(paper.canvas_width_mm)
    try:
        from ..utils.page_grid import page_content_width_mm
        w = page_content_width_mm(work, int(page_index), cw)
    except Exception:  # noqa: BLE001
        w = cw
    return Rect(ox, oy, w, float(paper.canvas_height_mm))


def raster_world_rect(context, work, entry) -> Rect | None:
    parent_key = str(getattr(entry, "parent_key", "") or "")
    parent_kind = str(getattr(entry, "parent_kind", "") or "page")
    page_key = parent_key.split(":", 1)[0] if parent_key else ""
    page_index, page = page_index_for_key(work, page_key)
    if page is None:
        page_index = int(getattr(work, "active_page_index", -1)) if work is not None else -1
        page = work.pages[page_index] if work is not None and 0 <= page_index < len(work.pages) else None
    if page is None:
        return None
    if parent_kind == "coma" and ":" in parent_key:
        coma_id = parent_key.split(":", 1)[1]
        for panel in getattr(page, "comas", []) or []:
            if coma_identity(panel) == coma_id or str(getattr(panel, "id", "") or "") == coma_id:
                return coma_world_rect(context, work, page_index, panel)
    ox, oy = page_offset_mm(context, work, page_index)
    paper = getattr(work, "paper", None)
    if paper is None:
        return None
    return Rect(ox, oy, float(paper.canvas_width_mm), float(paper.canvas_height_mm))


def gp_layer_local_bounds(layer) -> Rect | None:
    xs: list[float] = []
    ys: list[float] = []
    for point in gp_parent.iter_points(layer):
        pos = getattr(point, "position", None)
        if pos is None:
            continue
        try:
            xs.append(float(pos[0]) * 1000.0)
            ys.append(float(pos[1]) * 1000.0)
        except Exception:  # noqa: BLE001
            continue
    if not xs or not ys:
        return None
    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)
    return Rect(min_x, min_y, max(0.1, max_x - min_x), max(0.1, max_y - min_y))


def gp_layer_world_rect(context, work, layer) -> Rect | None:
    rect = gp_layer_local_bounds(layer)
    if rect is None:
        return None
    ox, oy = parent_offset_mm(context, work, gp_parent.parent_key(layer))
    return Rect(rect.x + ox, rect.y + oy, rect.width, rect.height)


def gp_object_world_rect(obj, layer) -> Rect | None:
    xs: list[float] = []
    ys: list[float] = []
    for point in gp_parent.iter_points(layer):
        try:
            world = obj.matrix_world @ point.position
            xs.append(float(world.x) * 1000.0)
            ys.append(float(world.y) * 1000.0)
        except Exception:  # noqa: BLE001
            continue
    if not xs or not ys:
        return object_world_rect_mm(obj)
    return Rect(min(xs), min(ys), max(0.1, max(xs) - min(xs)), max(0.1, max(ys) - min(ys)))


def hit_image_at_world(context, x_mm: float, y_mm: float) -> dict | None:
    work = get_work(context)
    scene = getattr(context, "scene", None)
    coll = getattr(scene, "bmanga_image_layers", None) if scene is not None else None
    if work is None or coll is None:
        return None
    for index in reversed(range(len(coll))):
        entry = coll[index]
        if not bool(getattr(entry, "visible", True)) or bool(getattr(entry, "locked", False)):
            continue
        page_index, _page = page_index_for_key(work, page_key_for_entry(entry))
        rect = world_rect_for_page_entry(context, work, page_index, entry) if page_index >= 0 else Rect(
            float(getattr(entry, "x_mm", 0.0)),
            float(getattr(entry, "y_mm", 0.0)),
            float(getattr(entry, "width_mm", 0.0)),
            float(getattr(entry, "height_mm", 0.0)),
        )
        part = hit_part_for_rect(rect, x_mm, y_mm)
        if part:
            return {
                "kind": "image",
                "index": index,
                "part": "move" if part == "body" else part,
                "key": object_selection.image_key(entry),
                "world": (float(x_mm), float(y_mm)),
            }
    return None


def hit_gp_at_world(context, x_mm: float, y_mm: float) -> dict | None:
    if get_work(context) is None:
        return None
    for obj in layer_object_model.iter_layer_objects("gp"):
        layer = layer_object_model.content_layer(obj)
        if layer is None or not layer_object_model.user_visible(obj) or layer_object_model.user_locked(obj):
            continue
        rect = gp_object_world_rect(obj, layer)
        if rect is None:
            continue
        part = hit_part_for_rect(rect, x_mm, y_mm)
        if part:
            return {
                "kind": "gp",
                "layer_key": layer_object_model.stable_id(obj),
                "part": "move" if part == "body" else part,
                "key": object_selection.gp_key(obj),
                "world": (float(x_mm), float(y_mm)),
            }
    return None


def hit_raster_at_world(context, x_mm: float, y_mm: float) -> dict | None:
    work = get_work(context)
    scene = getattr(context, "scene", None)
    coll = getattr(scene, "bmanga_raster_layers", None) if scene is not None else None
    if work is None or coll is None:
        return None
    for index in reversed(range(len(coll))):
        entry = coll[index]
        if not bool(getattr(entry, "visible", True)) or bool(getattr(entry, "locked", False)):
            continue
        rect = raster_world_rect(context, work, entry)
        if rect is None:
            continue
        part = hit_part_for_rect(rect, x_mm, y_mm)
        if part:
            if part == "body" and _raster_alpha_at_world(context, work, entry, x_mm, y_mm) <= 0.01:
                continue
            return {
                "kind": "raster",
                "index": index,
                "part": "move",
                "key": object_selection.raster_key(entry),
                "world": (float(x_mm), float(y_mm)),
            }
    return None


def fill_world_rect(context, work, entry) -> Rect | None:
    if entry is None or work is None:
        return None
    use_region = bool(getattr(entry, "use_region", False))
    if not use_region:
        paper = getattr(work, "paper", None) if work is not None else None
        cw = float(getattr(paper, "canvas_width_mm", 182.0) or 182.0)
        ch = float(getattr(paper, "canvas_height_mm", 257.0) or 257.0)
        x_mm, y_mm, w_mm, h_mm = 0.0, 0.0, cw, ch
    else:
        x_mm = float(getattr(entry, "region_x_mm", 0.0) or 0.0)
        y_mm = float(getattr(entry, "region_y_mm", 0.0) or 0.0)
        w_mm = float(getattr(entry, "region_width_mm", 0.0) or 0.0)
        h_mm = float(getattr(entry, "region_height_mm", 0.0) or 0.0)
    from ..utils.fill_real_object import page_for_entry, entry_page_offset_mm
    scene = getattr(context, "scene", None)
    page = page_for_entry(scene, work, entry)
    ox, oy = entry_page_offset_mm(scene, work, entry, page)
    return Rect(x_mm + ox, y_mm + oy, w_mm, h_mm)


def hit_fill_at_world(context, x_mm: float, y_mm: float) -> dict | None:
    work = get_work(context)
    scene = getattr(context, "scene", None)
    coll = getattr(scene, "bmanga_fill_layers", None) if scene is not None else None
    if work is None or coll is None:
        return None
    fullcanvas_hit = None
    for index in reversed(range(len(coll))):
        entry = coll[index]
        if not bool(getattr(entry, "visible", True)) or bool(getattr(entry, "locked", False)):
            continue
        rect = fill_world_rect(context, work, entry)
        if rect is None:
            continue
        part = hit_part_for_rect(rect, x_mm, y_mm)
        if part:
            hit = {
                "kind": "fill",
                "index": index,
                "part": "move",
                "key": object_selection.fill_key(entry),
                "world": (float(x_mm), float(y_mm)),
            }
            if bool(getattr(entry, "use_region", False)):
                return hit
            if fullcanvas_hit is None:
                fullcanvas_hit = hit
    return fullcanvas_hit


def hit_fill_at_event(context, event, event_world_xy: Callable) -> dict | None:
    x_mm, y_mm = event_world_xy(context, event)
    if x_mm is None or y_mm is None:
        return None
    return hit_fill_at_world(context, x_mm, y_mm)


def hit_shared_text_at_world(context, x_mm: float, y_mm: float) -> dict | None:
    work = get_work(context)
    if work is None:
        return None
    coll = getattr(work, "shared_texts", None)
    if coll is None:
        return None
    for index in reversed(range(len(coll))):
        entry = coll[index]
        if not bool(getattr(entry, "visible", True)) or bool(getattr(entry, "locked", False)):
            continue
        rect = world_rect_for_page_entry(context, work, -1, entry, use_parent=False)
        part = hit_part_for_rect(rect, x_mm, y_mm)
        if part:
            return {
                "kind": "text",
                "page_id": OUTSIDE_STACK_KEY,
                "index": index,
                "part": "move",
                "key": object_selection.text_key(None, entry),
                "world": (float(x_mm), float(y_mm)),
            }
    return None


def hit_shared_balloon_at_world(context, x_mm: float, y_mm: float) -> dict | None:
    work = get_work(context)
    if work is None:
        return None
    coll = getattr(work, "shared_balloons", None)
    if coll is None:
        return None
    for index in reversed(range(len(coll))):
        entry = coll[index]
        if not bool(getattr(entry, "visible", True)) or bool(getattr(entry, "locked", False)):
            continue
        rect = world_rect_for_page_entry(context, work, -1, entry, use_parent=False)
        part = hit_part_for_rect(rect, x_mm, y_mm)
        if not part and free_transform.entry_enabled(entry):
            quad = free_transform.quad_from_rect_offsets(rect, free_transform.entry_offsets(entry))
            if free_transform.point_in_quad(quad, x_mm, y_mm, tolerance_mm=2.5):
                part = "body"
        if part:
            return {
                "kind": "balloon",
                "page_id": OUTSIDE_STACK_KEY,
                "index": index,
                "part": "move" if part == "body" else part,
                "key": object_selection.balloon_key(None, entry),
                "world": (float(x_mm), float(y_mm)),
            }
    return None


def _raster_alpha_at_world(context, work, entry, x_mm: float, y_mm: float) -> float:
    parent_key = str(getattr(entry, "parent_key", "") or "")
    page_key = parent_key.split(":", 1)[0] if parent_key else ""
    page_index, _page = page_index_for_key(work, page_key)
    if page_index < 0:
        page_index = int(getattr(work, "active_page_index", -1)) if work is not None else -1
    if page_index < 0:
        return 0.0
    ox, oy = page_offset_mm(context, work, page_index)
    paper = getattr(work, "paper", None)
    if paper is None:
        return 0.0
    width_mm = max(1.0e-6, float(getattr(paper, "canvas_width_mm", 0.0) or 0.0))
    height_mm = max(1.0e-6, float(getattr(paper, "canvas_height_mm", 0.0) or 0.0))
    u = (float(x_mm) - ox) / width_mm
    v = (float(y_mm) - oy) / height_mm
    if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
        return 0.0
    image_name = str(getattr(entry, "image_name", "") or "")
    image = bpy.data.images.get(image_name) if image_name else None
    if image is None:
        return 0.0
    img_w = int(getattr(image, "size", (0, 0))[0])
    img_h = int(getattr(image, "size", (0, 0))[1])
    if img_w <= 0 or img_h <= 0:
        return 0.0
    px = max(0, min(img_w - 1, int(round(u * float(img_w - 1)))))
    py = max(0, min(img_h - 1, int(round(v * float(img_h - 1)))))
    offset = (py * img_w + px) * 4 + 3
    try:
        return float(image.pixels[offset])
    except Exception:  # noqa: BLE001
        return 0.0


def hit_image_at_event(context, event, event_world_xy: Callable) -> dict | None:
    x_mm, y_mm = event_world_xy(context, event)
    if x_mm is None or y_mm is None:
        return None
    return hit_image_at_world(context, x_mm, y_mm)


def hit_image_path_at_world(context, x_mm: float, y_mm: float) -> dict | None:
    scene = getattr(context, "scene", None)
    coll = getattr(scene, "bmanga_image_path_layers", None) if scene is not None else None
    if coll is None:
        return None
    for entry in reversed(list(coll)):
        if not bool(getattr(entry, "visible", True)):
            continue
        key = object_selection.image_path_key(entry)
        rect = selection_bounds_for_key(context, key)
        if rect is not None and rect_contains_point(rect, x_mm, y_mm, pad=1.0):
            return {"kind": "image_path", "part": "move", "key": key}
    return None


def hit_image_path_at_event(context, event, event_world_xy: Callable) -> dict | None:
    x_mm, y_mm = event_world_xy(context, event)
    if x_mm is None or y_mm is None:
        return None
    return hit_image_path_at_world(context, x_mm, y_mm)


def hit_shared_text_at_event(context, event, event_world_xy: Callable) -> dict | None:
    x_mm, y_mm = event_world_xy(context, event)
    if x_mm is None or y_mm is None:
        return None
    return hit_shared_text_at_world(context, x_mm, y_mm)


def hit_shared_balloon_at_event(context, event, event_world_xy: Callable) -> dict | None:
    x_mm, y_mm = event_world_xy(context, event)
    if x_mm is None or y_mm is None:
        return None
    return hit_shared_balloon_at_world(context, x_mm, y_mm)


def hit_gp_at_event(context, event, event_world_xy: Callable) -> dict | None:
    x_mm, y_mm = event_world_xy(context, event)
    if x_mm is None or y_mm is None:
        return None
    return hit_gp_at_world(context, x_mm, y_mm)


def hit_raster_at_event(context, event, event_world_xy: Callable) -> dict | None:
    x_mm, y_mm = event_world_xy(context, event)
    if x_mm is None or y_mm is None:
        return None
    return hit_raster_at_world(context, x_mm, y_mm)


def selection_bounds_for_key(context, key: str) -> Rect | None:
    work = get_work(context)
    kind, page_id, item_id = object_selection.parse_key(key)
    if work is None:
        return None
    if kind == "page":
        page_index, _page = page_index_for_key(work, item_id)
        return page_world_rect(context, work, page_index)
    if kind == "coma":
        if page_id == OUTSIDE_STACK_KEY:
            _coma_index, panel = find_shared_coma_by_key(work, item_id)
            return coma_world_rect(context, work, -1, panel) if panel is not None else None
        page_index, _page, _coma_index, panel = find_coma_by_key(work, page_id, item_id)
        return coma_world_rect(context, work, page_index, panel) if panel is not None else None
    if kind == "balloon":
        if page_id == OUTSIDE_STACK_KEY:
            _idx, entry = find_shared_balloon_by_key(work, item_id)
            return world_rect_for_page_entry(context, work, -1, entry, use_parent=False) if entry is not None else None
        page_index, _page, _idx, entry = find_balloon_by_key(work, page_id, item_id)
        return world_rect_for_page_entry(context, work, page_index, entry, use_parent=False) if entry is not None else None
    if kind == "text":
        if page_id == OUTSIDE_STACK_KEY:
            _idx, entry = find_shared_text_by_key(work, item_id)
            return world_rect_for_page_entry(context, work, -1, entry, use_parent=False) if entry is not None else None
        page_index, _page, _idx, entry = find_text_by_key(work, page_id, item_id)
        return world_rect_for_page_entry(context, work, page_index, entry, use_parent=False) if entry is not None else None
    if kind == "effect":
        obj, layer = find_effect_layer(item_id)
        bounds = effect_line_op.effect_layer_bounds(obj, layer)
        world_bounds = effect_line_op.effect_layer_world_bounds(context, obj, layer, bounds)
        if world_bounds is None:
            return None
        return Rect(float(world_bounds[0]), float(world_bounds[1]), float(world_bounds[2]), float(world_bounds[3]))
    if kind == "image":
        _idx, entry = find_image_by_key(context, item_id)
        if entry is None:
            return None
        page_index, _page = page_index_for_key(work, page_key_for_entry(entry))
        if page_index < 0:
            return Rect(float(entry.x_mm), float(entry.y_mm), float(entry.width_mm), float(entry.height_mm))
        return world_rect_for_page_entry(context, work, page_index, entry)
    if kind == "image_path":
        obj = on.find_object_by_bmanga_id(item_id, kind="image_path")
        return object_world_rect_mm(obj)
    if kind == "raster":
        _idx, entry = find_raster_by_key(context, item_id)
        return raster_world_rect(context, work, entry) if entry is not None else None
    if kind == "fill":
        _idx, entry = find_fill_by_key(context, item_id)
        return fill_world_rect(context, work, entry) if entry is not None else None
    if kind == "gp":
        obj, layer = find_gp_layer(item_id)
        return gp_object_world_rect(obj, layer) if obj is not None and layer is not None else None
    return None


def _iter_effect_layers_for_selection():
    for obj in layer_stack_utils._iter_effect_objects():
        layer = layer_object_model.content_layer(obj)
        if layer is not None:
            yield layer


def _iter_rect_select_candidates(context):
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return
    for page_index, page in enumerate(getattr(work, "pages", []) or []):
        if not page_range.page_in_range(page):
            continue
        page_id = str(getattr(page, "id", "") or "")
        page_key = object_selection.page_key(page)
        page_rect = page_world_rect(context, work, page_index)
        if page_rect is not None:
            yield {
                "key": page_key,
                "rect": page_rect,
                "hit": {
                    "kind": "page",
                    "page": page_index,
                    "part": "body",
                    "key": page_key,
                },
            }
        for text_index, entry in enumerate(reversed(list(getattr(page, "texts", []) or []))):
            actual_index = len(page.texts) - 1 - text_index
            key = object_selection.text_key(page, entry)
            yield {
                "key": key,
                "rect": world_rect_for_page_entry(context, work, page_index, entry, use_parent=False),
                "hit": {
                    "kind": "text",
                    "page_id": page_id,
                    "index": actual_index,
                    "part": "move",
                    "key": key,
                },
            }
        for balloon_index, entry in enumerate(reversed(list(getattr(page, "balloons", []) or []))):
            actual_index = len(page.balloons) - 1 - balloon_index
            key = object_selection.balloon_key(page, entry)
            yield {
                "key": key,
                "rect": world_rect_for_page_entry(context, work, page_index, entry, use_parent=False),
                "hit": {
                    "kind": "balloon",
                    "page_id": page_id,
                    "index": actual_index,
                    "part": "move",
                    "key": key,
                },
            }
    for text_index, entry in enumerate(reversed(list(getattr(work, "shared_texts", []) or []))):
        actual_index = len(work.shared_texts) - 1 - text_index
        if not bool(getattr(entry, "visible", True)):
            continue
        key = object_selection.text_key(None, entry)
        yield {
            "key": key,
            "rect": world_rect_for_page_entry(context, work, -1, entry, use_parent=False),
            "hit": {
                "kind": "text",
                "page_id": OUTSIDE_STACK_KEY,
                "index": actual_index,
                "part": "move",
                "key": key,
            },
        }
    for balloon_index, entry in enumerate(reversed(list(getattr(work, "shared_balloons", []) or []))):
        actual_index = len(work.shared_balloons) - 1 - balloon_index
        if not bool(getattr(entry, "visible", True)):
            continue
        key = object_selection.balloon_key(None, entry)
        yield {
            "key": key,
            "rect": world_rect_for_page_entry(context, work, -1, entry, use_parent=False),
            "hit": {
                "kind": "balloon",
                "page_id": OUTSIDE_STACK_KEY,
                "index": actual_index,
                "part": "move",
                "key": key,
            },
        }
    for layer in _iter_effect_layers_for_selection():
        key = object_selection.effect_key(layer)
        rect = selection_bounds_for_key(context, key)
        if rect is None:
            continue
        yield {
            "key": key,
            "rect": rect,
            "hit": {
                "kind": "effect",
                "layer_name": object_selection.parse_key(key)[2],
                "part": "move",
                "key": key,
            },
        }
    scene = getattr(context, "scene", None)
    for entry in reversed(list(getattr(scene, "bmanga_image_layers", []) or [])):
        if not bool(getattr(entry, "visible", True)):
            continue
        key = object_selection.image_key(entry)
        rect = selection_bounds_for_key(context, key)
        if rect is None:
            continue
        yield {"key": key, "rect": rect, "hit": {"kind": "image", "part": "move", "key": key}}
    for entry in reversed(list(getattr(scene, "bmanga_image_path_layers", []) or [])):
        if not bool(getattr(entry, "visible", True)):
            continue
        key = object_selection.image_path_key(entry)
        rect = selection_bounds_for_key(context, key)
        if rect is None:
            continue
        yield {"key": key, "rect": rect, "hit": {"kind": "image_path", "part": "move", "key": key}}
    for obj in layer_object_model.iter_layer_objects("gp"):
        key = object_selection.gp_key(obj)
        rect = selection_bounds_for_key(context, key)
        if rect is None:
            continue
        yield {
            "key": key,
            "rect": rect,
            "hit": {
                "kind": "gp",
                "layer_key": layer_object_model.stable_id(obj),
                "part": "move",
                "key": key,
            },
        }
    for entry in reversed(list(getattr(scene, "bmanga_raster_layers", []) or [])):
        if not bool(getattr(entry, "visible", True)):
            continue
        key = object_selection.raster_key(entry)
        rect = selection_bounds_for_key(context, key)
        if rect is None:
            continue
        yield {"key": key, "rect": rect, "hit": {"kind": "raster", "part": "move", "key": key}}
    for entry in reversed(list(getattr(scene, "bmanga_fill_layers", []) or [])):
        if not bool(getattr(entry, "visible", True)):
            continue
        key = object_selection.fill_key(entry)
        rect = selection_bounds_for_key(context, key)
        if rect is None:
            continue
        yield {"key": key, "rect": rect, "hit": {"kind": "fill", "part": "move", "key": key}}
    for page_index, page in enumerate(getattr(work, "pages", []) or []):
        if not page_range.page_in_range(page):
            continue
        for coma_index, panel in enumerate(reversed(list(getattr(page, "comas", []) or []))):
            actual_index = len(page.comas) - 1 - coma_index
            key = object_selection.coma_key(page, panel)
            yield {
                "key": key,
                "rect": coma_world_rect(context, work, page_index, panel),
                "hit": {
                    "kind": "coma",
                    "page": page_index,
                    "coma": actual_index,
                    "part": "body",
                    "key": key,
                },
            }
    for coma_index, panel in enumerate(reversed(list(getattr(work, "shared_comas", []) or []))):
        actual_index = len(work.shared_comas) - 1 - coma_index
        if not bool(getattr(panel, "visible", True)):
            continue
        key = object_selection.coma_key(None, panel)
        yield {
            "key": key,
            "rect": coma_world_rect(context, work, -1, panel),
            "hit": {
                "kind": "coma",
                "page": -1,
                "coma": actual_index,
                "part": "body",
                "key": key,
            },
        }


def select_keys_in_world_rect(
    context,
    rect: Rect,
    *,
    mode: str = "single",
    activate: Callable[[object, dict, str], None] | None = None,
) -> list[str]:
    candidates = []
    for item in _iter_rect_select_candidates(context):
        kind = object_selection.parse_key(item["key"])[0]
        if kind == "page":
            if rect_contains_rect(rect, item["rect"]):
                candidates.append(item)
            continue
        if rect_intersects(rect, item["rect"]):
            candidates.append(item)
    if not candidates:
        if mode == "single":
            object_selection.clear(context)
            sync_outliner_selection_for_keys(context, [])
        edge_selection.clear_selection(context)
        return []
    current_keys = object_selection.get_keys(context)
    if activate is not None:
        activate(context, candidates[0]["hit"], "single")
    keys = [str(item["key"]) for item in candidates]
    if mode == "add":
        keys = current_keys + keys
    elif mode == "toggle":
        current = current_keys
        for key in keys:
            if key in current:
                current = [item for item in current if item != key]
            else:
                current.append(key)
        keys = current
    object_selection.set_keys(context, keys)
    sync_outliner_selection_for_keys(context, keys)
    edge_selection.clear_selection(context)
    return object_selection.get_keys(context)


def active_selection_key(context) -> str:
    item = layer_stack_utils.active_stack_item(context)
    resolved = layer_stack_utils.resolve_stack_item(context, item) if item is not None else None
    target = resolved.get("target") if resolved is not None else None
    if item is None or target is None:
        return ""
    kind = str(getattr(item, "kind", "") or "")
    if kind == "gp":
        return object_selection.gp_key(resolved.get("object") or target)
    if kind == "image":
        return object_selection.image_key(target)
    if kind == "image_path":
        return object_selection.image_path_key(target)
    if kind == "raster":
        return object_selection.raster_key(target)
    if kind == "effect":
        return object_selection.effect_key(resolved.get("object") or target)
    if kind == "fill":
        return object_selection.fill_key(target)
    if kind == "balloon":
        page = resolved.get("page")
        return object_selection.balloon_key(page, target)
    if kind == "text":
        page = resolved.get("page")
        return object_selection.text_key(page, target)
    if kind == "coma":
        page = resolved.get("page")
        return object_selection.coma_key(page, target)
    if kind == "page":
        return object_selection.page_key(target)
    return ""


def scale_gp_layer_from_snapshot(layer, old_rect, new_rect) -> None:
    old_x, old_y, old_w, old_h = old_rect
    new_x, new_y, new_w, new_h = new_rect
    sx = float(new_w) / float(old_w) if abs(float(old_w)) > 1.0e-6 else 1.0
    sy = float(new_h) / float(old_h) if abs(float(old_h)) > 1.0e-6 else 1.0
    for point in gp_parent.iter_points(layer):
        pos = getattr(point, "position", None)
        if pos is None:
            continue
        try:
            px = float(pos[0]) * 1000.0
            py = float(pos[1]) * 1000.0
            nx = float(new_x) + (px - float(old_x)) * sx
            ny = float(new_y) + (py - float(old_y)) * sy
            point.position = (nx / 1000.0, ny / 1000.0, float(pos[2]))
        except Exception:  # noqa: BLE001
            continue
