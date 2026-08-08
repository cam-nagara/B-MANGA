"""ビューポート座標からレイヤー移送先を解決する。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from bpy_extras.view3d_utils import region_2d_to_location_3d

from .layer_hierarchy import (
    OUTSIDE_STACK_KEY,
    coma_containing_point,
    coma_stack_key,
    outside_child_key,
    page_stack_key,
    split_child_key,
)


@dataclass(frozen=True)
class ClickTarget:
    kind: str
    page: Optional[object]
    panel: Optional[object]
    page_index: int
    world_xy_mm: Optional[tuple[float, float]]
    local_xy_mm: Optional[tuple[float, float]]
    folder_key: str = ""


def find_click_target(context, event) -> ClickTarget:
    """event位置の最深コンテナを返す。"""

    world = _world_xy_mm_from_event(context, event)
    if world is None:
        return ClickTarget("outside", None, None, -1, None, None)
    page_index, page, local_x, local_y = _resolve_local_xy_mm(
        context,
        world[0],
        world[1],
    )
    if page is None or local_x is None or local_y is None:
        return ClickTarget("outside", None, None, -1, world, None)
    panel = coma_containing_point(page, local_x, local_y)
    if panel is not None:
        return ClickTarget(
            "coma",
            page,
            panel,
            page_index,
            world,
            (local_x, local_y),
        )
    return ClickTarget(
        "page",
        page,
        None,
        page_index,
        world,
        (local_x, local_y),
    )


def find_target_for_drop(context, event) -> ClickTarget:
    """ページプレビューを含め、Alt+dragの移送先を返す。"""

    target = find_click_target(context, event)
    if target.kind != "outside":
        return target
    from . import page_file_scene, page_preview_object

    role, _page_id, _coma_id = page_file_scene.current_role(context)
    if role != page_file_scene.ROLE_PAGE:
        return target
    world = _world_xy_mm_from_event(context, event)
    if world is None:
        return target
    from ..core.work import get_work

    work = get_work(context)
    scene = getattr(context, "scene", None)
    if work is None or scene is None:
        return target
    preview_index = page_preview_object.page_index_at_world_mm(
        scene,
        work,
        world[0],
        world[1],
    )
    if preview_index is None or not (0 <= preview_index < len(work.pages)):
        return target
    return _preview_target(work, scene, preview_index, world)


def parent_key_for_target(target: ClickTarget) -> str:
    if target.kind == "coma" and target.page is not None and target.panel is not None:
        return coma_stack_key(target.page, target.panel)
    if target.kind == "coma" and target.page is None and target.panel is not None:
        stem = str(
            getattr(target.panel, "coma_id", "")
            or getattr(target.panel, "id", "")
            or ""
        )
        return outside_child_key(stem)
    if target.kind == "page" and target.page is not None:
        return page_stack_key(target.page)
    return ""


def current_parent_key(item) -> str:
    return str(getattr(item, "parent_key", "") or "")


def shallower_target_for_item(
    context,
    item,
    click_target: ClickTarget,
) -> Optional[ClickTarget]:
    """itemから1段浅い親候補を返す。"""

    parent_key = current_parent_key(item)
    if not parent_key or parent_key == OUTSIDE_STACK_KEY:
        return None
    page_id, child_id = split_child_key(parent_key)
    if not child_id:
        return ClickTarget(
            "outside",
            None,
            None,
            -1,
            click_target.world_xy_mm,
            click_target.local_xy_mm,
        )
    from ..core.work import get_work

    work = get_work(context)
    if work is None:
        return None
    for index, page in enumerate(work.pages):
        if page_stack_key(page) == page_id:
            return ClickTarget(
                "page",
                page,
                None,
                index,
                click_target.world_xy_mm,
                click_target.local_xy_mm,
            )
    return None


def _world_xy_mm_from_event(
    context,
    event,
) -> Optional[tuple[float, float]]:
    from ..operators import view_event_region
    from . import geom

    view = view_event_region.view3d_window_under_event(context, event)
    if view is None:
        return None
    _area, region, rv3d, mouse_x, mouse_y = view
    location = region_2d_to_location_3d(
        region,
        rv3d,
        (mouse_x, mouse_y),
        (0.0, 0.0, 0.0),
    )
    if location is None:
        return None
    return geom.m_to_mm(location.x), geom.m_to_mm(location.y)


def _resolve_local_xy_mm(context, world_x_mm: float, world_y_mm: float):
    from ..core.work import get_work
    from . import page_grid

    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return -1, None, None, None
    page_index = page_grid.page_index_at_world_mm(
        work,
        context.scene,
        world_x_mm,
        world_y_mm,
    )
    if page_index is None or not (0 <= page_index < len(work.pages)):
        return -1, None, None, None
    page = work.pages[page_index]
    offset_x, offset_y = page_grid.page_total_offset_mm(
        work,
        context.scene,
        page_index,
    )
    return (
        page_index,
        page,
        world_x_mm - offset_x,
        world_y_mm - offset_y,
    )


def _preview_target(work, scene, page_index: int, world) -> ClickTarget:
    from . import page_grid

    page = work.pages[page_index]
    offset_x, offset_y = page_grid.page_total_offset_mm(
        work,
        scene,
        page_index,
    )
    local = world[0] - offset_x, world[1] - offset_y
    panel = coma_containing_point(page, local[0], local[1])
    if panel is not None:
        return ClickTarget("coma", page, panel, page_index, world, local)
    return ClickTarget("page", page, None, page_index, world, local)


__all__ = (
    "ClickTarget",
    "current_parent_key",
    "find_click_target",
    "find_target_for_drop",
    "parent_key_for_target",
    "shallower_target_for_item",
)
