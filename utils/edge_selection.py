"""B-MANGA のコマ辺選択状態を共有するヘルパ."""

from __future__ import annotations


def _parse_vertex_indices(value: str) -> set[int]:
    out: set[int] = set()
    for part in str(value or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            idx = int(part)
        except ValueError:
            continue
        if idx >= 0:
            out.add(idx)
    return out


def _format_vertex_indices(indices) -> str:
    values: set[int] = set()
    for value in indices or ():
        try:
            idx = int(value)
        except (TypeError, ValueError):
            continue
        if idx >= 0:
            values.add(idx)
    return ",".join(str(v) for v in sorted(values))


def tag_view3d_redraw(context) -> None:
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in getattr(screen, "areas", []):
        if getattr(area, "type", "") == "VIEW_3D":
            try:
                area.tag_redraw()
            except Exception:  # noqa: BLE001
                pass


def _sync_active_panel_stack_item(context, work, page, panel) -> None:
    scene = getattr(context, "scene", None)
    if scene is None or page is None or panel is None:
        return
    try:
        from . import layer_stack as layer_stack_utils
        from .layer_hierarchy import COMA_KIND, coma_stack_key

        if hasattr(scene, "bmanga_active_layer_kind"):
            scene.bmanga_active_layer_kind = COMA_KIND
        if hasattr(scene, "bmanga_active_gp_folder_key"):
            scene.bmanga_active_gp_folder_key = ""
        stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        uid = layer_stack_utils.target_uid(COMA_KIND, coma_stack_key(page, panel))
        if stack is not None:
            for i, item in enumerate(stack):
                if layer_stack_utils.stack_item_uid(item) == uid:
                    layer_stack_utils.set_active_stack_index_silently(context, i)
                    break
        layer_stack_utils.remember_layer_stack_signature(context)
    except Exception:  # noqa: BLE001
        pass


def set_selection(
    context,
    kind: str,
    *,
    page_index: int = -1,
    coma_index: int = -1,
    edge_index: int = -1,
    vertex_index: int = -1,
    vertex_indices=None,
    sync_style: bool = True,
) -> bool:
    """アクティブなコマ辺選択を WindowManager プロパティに保存する."""
    wm = getattr(context, "window_manager", None)
    if wm is None or not hasattr(wm, "bmanga_edge_select_kind"):
        return False
    if kind not in {"none", "edge", "border", "vertex"}:
        kind = "none"
    if kind == "none":
        page_index = coma_index = edge_index = vertex_index = -1
        vertex_indices = ()
    elif kind == "vertex":
        if vertex_indices is None:
            vertex_indices = (vertex_index,) if int(vertex_index) >= 0 else ()
        vertex_set = _parse_vertex_indices(_format_vertex_indices(vertex_indices))
        if int(vertex_index) >= 0:
            vertex_set.add(int(vertex_index))
        vertex_indices = vertex_set
    else:
        vertex_indices = ()
    try:
        wm.bmanga_edge_select_kind = kind
        wm.bmanga_edge_select_page = int(page_index)
        wm.bmanga_edge_select_coma = int(coma_index)
        wm.bmanga_edge_select_edge = int(edge_index) if kind == "edge" else -1
        wm.bmanga_edge_select_vertex = int(vertex_index) if kind == "vertex" else -1
        if hasattr(wm, "bmanga_edge_select_vertices"):
            wm.bmanga_edge_select_vertices = _format_vertex_indices(vertex_indices)
    except Exception:  # noqa: BLE001
        return False
    if kind != "none":
        try:
            from ..core.work import get_work

            work = get_work(context)
            if work is not None and 0 <= int(page_index) < len(work.pages):
                work.active_page_index = int(page_index)
                page = work.pages[int(page_index)]
                if 0 <= int(coma_index) < len(page.comas):
                    page.active_coma_index = int(coma_index)
                    _sync_active_panel_stack_item(context, work, page, page.comas[int(coma_index)])
        except Exception:  # noqa: BLE001
            pass
    if sync_style and kind != "none":
        try:
            from ..operators import coma_edge_style_op

            coma_edge_style_op.sync_selected_style_props(context)
        except Exception:  # noqa: BLE001
            pass
    tag_view3d_redraw(context)
    return True


def clear_selection(context) -> bool:
    return set_selection(context, "none")


def selected_vertices(context, *, page_index: int = -1, coma_index: int = -1) -> set[int]:
    wm = getattr(context, "window_manager", None) if context is not None else None
    if wm is None:
        return set()
    if str(getattr(wm, "bmanga_edge_select_kind", "none") or "none") != "vertex":
        return set()
    if page_index >= 0 and int(getattr(wm, "bmanga_edge_select_page", -1)) != int(page_index):
        return set()
    if coma_index >= 0 and int(getattr(wm, "bmanga_edge_select_coma", -1)) != int(coma_index):
        return set()
    values = _parse_vertex_indices(str(getattr(wm, "bmanga_edge_select_vertices", "") or ""))
    active = int(getattr(wm, "bmanga_edge_select_vertex", -1))
    if active >= 0:
        values.add(active)
    return values


def set_vertex_selection(
    context,
    *,
    page_index: int,
    coma_index: int,
    vertex_index: int,
    mode: str = "single",
) -> set[int]:
    try:
        vertex_index = int(vertex_index)
    except (TypeError, ValueError):
        set_selection(context, "none")
        return set()
    current = selected_vertices(context, page_index=page_index, coma_index=coma_index)
    if mode == "add":
        current.add(vertex_index)
    elif mode == "toggle":
        if vertex_index in current:
            current.remove(vertex_index)
        else:
            current.add(vertex_index)
    else:
        current = {vertex_index}
    if not current:
        set_selection(context, "none")
        return set()
    active = vertex_index if vertex_index in current else min(current)
    set_selection(
        context,
        "vertex",
        page_index=page_index,
        coma_index=coma_index,
        vertex_index=active,
        vertex_indices=current,
    )
    return set(current)


def update_overlay_pointer(context, region, event) -> None:
    """B-MANGA modal の MOUSEMOVE から呼ぶ: ▲ hover ハイライト用に
    region 相対のカーソル位置を WindowManager に書き込む.

    overlay_coma_selection.draw / coma_edge_move_op._draw_callback の
    hover 判定で読まれる。 region が None なら invalid フラグを立てる。
    """
    wm = getattr(context, "window_manager", None)
    if wm is None:
        return
    if region is None or event is None:
        try:
            if hasattr(wm, "bmanga_overlay_pointer_valid"):
                wm.bmanga_overlay_pointer_valid = False
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        if hasattr(event, "mouse_region_x") and hasattr(event, "mouse_region_y"):
            rx = int(getattr(event, "mouse_region_x", 0))
            ry = int(getattr(event, "mouse_region_y", 0))
        else:
            rx = int(getattr(event, "mouse_x", 0)) - int(getattr(region, "x", 0))
            ry = int(getattr(event, "mouse_y", 0)) - int(getattr(region, "y", 0))
        if hasattr(wm, "bmanga_overlay_pointer_x"):
            wm.bmanga_overlay_pointer_x = rx
        if hasattr(wm, "bmanga_overlay_pointer_y"):
            wm.bmanga_overlay_pointer_y = ry
        if hasattr(wm, "bmanga_overlay_pointer_valid"):
            wm.bmanga_overlay_pointer_valid = True
    except Exception:  # noqa: BLE001
        pass


def clear_overlay_pointer(context) -> None:
    """modal 終了時に hover 状態をリセット."""
    wm = getattr(context, "window_manager", None)
    if wm is None:
        return
    try:
        if hasattr(wm, "bmanga_overlay_pointer_valid"):
            wm.bmanga_overlay_pointer_valid = False
    except Exception:  # noqa: BLE001
        pass


def get_overlay_pointer(context) -> tuple[int, int] | None:
    """``(x, y)`` (region 相対 px) を返す。 valid フラグが False なら None."""
    wm = getattr(context, "window_manager", None) if context is not None else None
    if wm is None:
        return None
    if not bool(getattr(wm, "bmanga_overlay_pointer_valid", False)):
        return None
    try:
        return int(getattr(wm, "bmanga_overlay_pointer_x", -1)), int(
            getattr(wm, "bmanga_overlay_pointer_y", -1)
        )
    except Exception:  # noqa: BLE001
        return None
