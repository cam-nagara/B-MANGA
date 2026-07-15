"""表示中の選択ハンドルを最優先でヒット・ドラッグする共通処理."""

from __future__ import annotations

from types import MethodType, SimpleNamespace

from ..utils import free_transform, object_selection
from ..utils.layer_hierarchy import OUTSIDE_STACK_KEY
from . import object_tool_free_transform, object_tool_selection


_HANDLE_KINDS = {
    "balloon",
    "text",
    "effect",
    "image",
    "image_path",
    "raster",
    "fill",
    "gp",
}
_MOVE_ONLY_KINDS = {"text", "image_path", "raster", "fill"}
_VISIBLE_HANDLE_MARKER = "visible_selection_handle"
_FREE_HANDLE_MARKER = "visible_free_transform_handle"


def _selection_keys_front_first(context) -> list[str]:
    keys = list(object_selection.get_keys(context))
    active = object_tool_selection.active_selection_key(context)
    if active:
        keys = [key for key in keys if key != active]
        keys.append(active)
    return list(reversed(keys))


def _mark_handle(hit: dict, *, free: bool = False) -> dict:
    marked = dict(hit)
    marked[_VISIBLE_HANDLE_MARKER] = True
    if free:
        marked[_FREE_HANDLE_MARKER] = True
    return marked


def _standard_hit_for_key(
    context,
    key: str,
    x_mm: float,
    y_mm: float,
) -> dict | None:
    kind, page_id, item_id = object_selection.parse_key(key)
    if kind not in _HANDLE_KINDS:
        return None
    rect = object_tool_selection.selection_bounds_for_key(context, key)
    if rect is None:
        return None
    part = object_tool_selection.hit_part_for_rect(rect, x_mm, y_mm)
    if part in {"", "body"}:
        return None
    hit: dict = {
        "kind": kind,
        "key": key,
        "part": "move" if kind in _MOVE_ONLY_KINDS else part,
        "handle_part": part,
        "world": (float(x_mm), float(y_mm)),
    }
    work = getattr(getattr(context, "scene", None), "bmanga_work", None)
    if kind == "balloon":
        if page_id == OUTSIDE_STACK_KEY:
            index, entry = object_tool_selection.find_shared_balloon_by_key(work, item_id)
        else:
            _page_index, _page, index, entry = object_tool_selection.find_balloon_by_key(
                work, page_id, item_id,
            )
        if entry is None:
            return None
        hit.update(page_id=page_id, index=index)
    elif kind == "text":
        if page_id == OUTSIDE_STACK_KEY:
            index, entry = object_tool_selection.find_shared_text_by_key(work, item_id)
        else:
            _page_index, _page, index, entry = object_tool_selection.find_text_by_key(
                work, page_id, item_id,
            )
        if entry is None:
            return None
        hit.update(page_id=page_id, index=index)
    elif kind == "effect":
        hit["layer_name"] = item_id
    elif kind in {"image", "image_path", "raster", "fill"}:
        if kind == "fill":
            _fill_index, fill_entry = object_tool_selection.find_fill_by_key(context, item_id)
            if fill_entry is None or not bool(getattr(fill_entry, "use_region", False)):
                return None
        hit["item_id"] = item_id
    elif kind == "gp":
        hit["layer_key"] = item_id
    return _mark_handle(hit)


def hit_visible_selected_handle(context, event, event_world_xy) -> dict | None:
    """描画中の選択ハンドルだけを、背面オブジェクトより先に判定する."""
    x_mm, y_mm = event_world_xy(context, event)
    if x_mm is None or y_mm is None:
        return None

    # Ctrl 自由変形、または右クリックメニューから開始した自由変形の角。
    transformed = object_tool_free_transform.hit_transformed_handle_at_event(
        context, event, event_world_xy,
    )
    if transformed is not None:
        return _mark_handle(transformed, free=True)

    # 既に変形済みのクアッドは、修飾キーがなくても画面上に角が見えている。
    # その見えている角を継続操作できるよう、実際の描画座標で先に判定する。
    for key in _selection_keys_front_first(context):
        transformed = object_tool_free_transform._hit_for_selected_key(  # noqa: SLF001
            context,
            key,
            float(x_mm),
            float(y_mm),
            force=False,
        )
        if transformed is not None:
            return _mark_handle(transformed, free=True)

    for key in _selection_keys_front_first(context):
        hit = _standard_hit_for_key(context, key, float(x_mm), float(y_mm))
        if hit is not None:
            return hit
    return None


def is_visible_handle_hit(hit: dict | None) -> bool:
    return bool(hit and hit.get(_VISIBLE_HANDLE_MARKER))


def resolved_drag_hit(context, event, hit: dict) -> dict:
    """表示ハンドルの種類を、オブジェクトツールの実ドラッグactionへ変換する."""
    resolved = dict(hit)
    if str(hit.get("kind", "") or "") in _MOVE_ONLY_KINDS:
        resolved["part"] = "move"
        return resolved
    free_action = object_tool_free_transform.free_action_for_hit(
        hit,
        ctrl=bool(getattr(event, "ctrl", False)),
        context=context,
    )
    if not free_action and bool(hit.get(_FREE_HANDLE_MARKER)):
        free_action = free_transform.action_for_part(str(hit.get("part", "") or ""))
    if free_action:
        resolved["part"] = free_action
    return resolved


_PROXY_METHODS = (
    "_clear_click_state",
    "_clear_drag_state",
    "_make_snapshots",
    "_panel_child_snapshots",
    "_setup_center_snap",
    "_start_object_drag",
    "_apply_snapshots",
    "_apply_panel_move",
    "_update_drag",
    "_finish_drag",
    "_cancel_drag",
)


class SharedHandleDrag:
    """他ツールのmodal中でもオブジェクトツールと同じドラッグ契約を使う."""

    def __init__(self, proxy) -> None:
        self._proxy = proxy

    @property
    def moved(self) -> bool:
        return bool(getattr(self._proxy, "_drag_moved", False))

    def update(self, context, event) -> None:
        self._proxy._update_drag(context, event)

    def finish(self, context) -> None:
        self._proxy._finish_drag(context)

    def cancel(self, context) -> None:
        self._proxy._cancel_drag(context)


def begin_shared_handle_drag(context, event, hit: dict, event_world_xy) -> SharedHandleDrag | None:
    """現在のツールを終了せず、表示ハンドルのドラッグだけを開始する."""
    from . import object_tool_op

    x_mm, y_mm = event_world_xy(context, event)
    if x_mm is None or y_mm is None:
        return None
    proxy = SimpleNamespace(
        _dragging=False,
        _drag_action="",
        _edge_drag=None,
        _layer_drag=None,
        _rotate_cursor_active=False,
    )
    cls = object_tool_op.BMANGA_OT_object_tool
    for name in _PROXY_METHODS:
        setattr(proxy, name, MethodType(getattr(cls, name), proxy))
    proxy._clear_click_state()
    object_tool_op.activate_hit(context, hit, mode="single")
    proxy._start_object_drag(
        context,
        resolved_drag_hit(context, event, hit),
        float(x_mm),
        float(y_mm),
    )
    if not getattr(proxy, "_snapshots", None):
        return None
    return SharedHandleDrag(proxy)
