"""B-Name modal ツールのアクティブ状態管理."""

from __future__ import annotations

import weakref

import bpy

_DRAWING_MODES = frozenset({
    "TEXTURE_PAINT",
    "PAINT_GREASE_PENCIL",
    "EDIT_GREASE_PENCIL",
    "SCULPT_GREASE_PENCIL",
    "VERTEX_GREASE_PENCIL",
    "WEIGHT_GREASE_PENCIL",
})


_ACTIVE_REFS: dict[str, weakref.ReferenceType | None] = {
    "object_tool": None,
    "edge_move": None,
    "knife_cut": None,
    "layer_move": None,
    "coma_create": None,
    "balloon_tool": None,
    "balloon_tail_tool": None,
    "balloon_nurbs_tool": None,
    "text_tool": None,
    "effect_line_tool": None,
    "coma_vertex_edit": None,
}

_DEFAULT_KEEP_SELECTION: dict[str, bool] = {
    "object_tool": True,
    "edge_move": True,
    "knife_cut": False,
    "layer_move": True,
    "coma_create": True,
    "balloon_tool": True,
    "balloon_tail_tool": True,
    "balloon_nurbs_tool": True,
    "text_tool": True,
    "effect_line_tool": True,
    "coma_vertex_edit": True,
}


def tag_tool_ui_redraw(context) -> None:
    screen = getattr(context, "screen", None) if context is not None else None
    if screen is None:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


def get_active(tool_name: str):
    ref = _ACTIVE_REFS.get(tool_name)
    if ref is None:
        return None
    op = ref()
    if op is None:
        _ACTIVE_REFS[tool_name] = None
    return op


def is_active(tool_name: str) -> bool:
    return get_active(tool_name) is not None


def set_active(tool_name: str, op, context=None) -> None:
    _ACTIVE_REFS[tool_name] = weakref.ref(op)
    tag_tool_ui_redraw(context)


def clear_active(tool_name: str, op=None, context=None) -> None:
    current = get_active(tool_name)
    if op is not None and current is not op:
        return
    _ACTIVE_REFS[tool_name] = None
    tag_tool_ui_redraw(context)


def finish_active(tool_name: str, context, *, keep_selection: bool = False) -> bool:
    op = get_active(tool_name)
    if op is None:
        return False
    try:
        op.finish_from_external(context, keep_selection=keep_selection)
    finally:
        clear_active(tool_name, op, context)
    return True


def finish_all(context, *, except_tool: str = "") -> bool:
    """アクティブな B-Name モーダルツールをまとめて終了する."""
    changed = False
    for tool_name in tuple(_ACTIVE_REFS.keys()):
        if tool_name == except_tool:
            continue
        changed = (
            finish_active(
                tool_name,
                context,
                keep_selection=_DEFAULT_KEEP_SELECTION.get(tool_name, True),
            )
            or changed
        )
    return changed


def mark_all_externally_finished() -> int:
    """全アクティブモーダルへ終了フラグだけ立て、参照を解放する.

    ``finish_from_external`` は scene/PropertyGroup を触るため、ファイル切替
    直後 (load_post) では参照が古くクラッシュする恐れがある。 ここでは
    ``_externally_finished`` フラグを立てるだけで scene には触らない。 各
    モーダル側は次の event で ``FINISHED`` を返して自然終了する。
    """
    count = 0
    for tool_name in tuple(_ACTIVE_REFS.keys()):
        ref = _ACTIVE_REFS.get(tool_name)
        if ref is None:
            continue
        op = ref()
        if op is not None:
            try:
                op._externally_finished = True
            except Exception:  # noqa: BLE001
                pass
            count += 1
        _ACTIVE_REFS[tool_name] = None
    return count


def set_modal_cursor(context, cursor: str) -> bool:
    window = getattr(context, "window", None) if context is not None else None
    if window is None:
        return False
    try:
        window.cursor_modal_set(cursor)
        return True
    except Exception:  # noqa: BLE001
        return False


def exit_drawing_mode(context) -> bool:
    """TEXTURE_PAINT / PAINT_GREASE_PENCIL に居る場合は OBJECT へ戻す.

    モーダルツール (枠線カット / フキダシ / テキスト / 効果線等) を起動する
    直前に呼び、 「描画開始 → 別ツール選択 → 描画終了」 を自動化する。
    TEXTURE_PAINT は ``bname.raster_layer_paint_exit`` を経由し、 PNG 自動保存と
    paper_bg 再表示も併せて行う。
    """
    obj = getattr(getattr(context, "view_layer", None), "objects", None)
    obj = getattr(obj, "active", None) if obj is not None else None
    if obj is None:
        return False
    mode = getattr(obj, "mode", "") or ""
    if mode not in _DRAWING_MODES:
        return False
    if mode == "TEXTURE_PAINT":
        try:
            bpy.ops.bname.raster_layer_paint_exit("EXEC_DEFAULT")
            return True
        except Exception:  # noqa: BLE001
            pass
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
        return True
    except Exception:  # noqa: BLE001
        return False


def restore_modal_cursor(context) -> None:
    window = getattr(context, "window", None) if context is not None else None
    if window is None:
        return
    try:
        window.cursor_modal_restore()
    except Exception:  # noqa: BLE001
        pass
