"""Inline text edit undo/redo history."""

from __future__ import annotations

from ..utils import layer_stack as layer_stack_utils, text_real_object, text_style

_HISTORY_LIMIT = 256


def _rect(entry) -> tuple[float, float, float, float]:
    return (
        float(getattr(entry, "x_mm", 0.0)),
        float(getattr(entry, "y_mm", 0.0)),
        float(getattr(entry, "width_mm", 0.0)),
        float(getattr(entry, "height_mm", 0.0)),
    )


def _snapshot(op, entry) -> dict:
    return {
        "body": str(getattr(entry, "body", "") or ""),
        "spans": text_style.all_spans_snapshot(entry),
        "rect": _rect(entry),
        "cursor": int(getattr(op, "_cursor_index", 0)),
        "anchor": int(getattr(op, "_selection_anchor", -1)),
    }


def _same_snapshot(a: dict | None, b: dict | None) -> bool:
    if not a or not b:
        return False
    return (
        a.get("body") == b.get("body")
        and a.get("spans") == b.get("spans")
        and a.get("rect") == b.get("rect")
    )


def begin(op, entry) -> None:
    snap = _snapshot(op, entry)
    op._text_edit_history = [snap]
    op._text_edit_redo = []


def clear(op) -> None:
    op._text_edit_history = []
    op._text_edit_redo = []


def record(op, entry) -> None:
    if entry is None or not bool(getattr(op, "_editing", False)):
        return
    history = list(getattr(op, "_text_edit_history", []) or [])
    snap = _snapshot(op, entry)
    if history and _same_snapshot(history[-1], snap):
        history[-1] = snap
    else:
        history.append(snap)
        if len(history) > _HISTORY_LIMIT:
            history = history[-_HISTORY_LIMIT:]
    op._text_edit_history = history
    op._text_edit_redo = []


def _restore(entry, snap: dict) -> tuple[int, int]:
    with text_real_object.suspend_auto_sync():
        entry.body = str(snap.get("body", "") or "")
        text_style.restore_all_spans(entry, snap.get("spans", ((), ())))
        x, y, w, h = snap.get("rect", _rect(entry))
        entry.x_mm = float(x)
        entry.y_mm = float(y)
        entry.width_mm = float(w)
        entry.height_mm = float(h)
    return int(snap.get("cursor", 0)), int(snap.get("anchor", -1))


def restore_previous(op, context) -> bool:
    history = list(getattr(op, "_text_edit_history", []) or [])
    if len(history) <= 1:
        return False
    page, entry, idx = op._current_text_entry(context)
    if page is None or entry is None or idx < 0:
        return False
    current = history.pop()
    redo = list(getattr(op, "_text_edit_redo", []) or [])
    redo.append(current)
    cursor, anchor = _restore(entry, history[-1])
    op._cursor_index = cursor
    op._selection_anchor = anchor
    op._text_edit_history = history
    op._text_edit_redo = redo
    _after_restore(context, page, entry, idx)
    return True


def restore_next(op, context) -> bool:
    redo = list(getattr(op, "_text_edit_redo", []) or [])
    if not redo:
        return False
    page, entry, idx = op._current_text_entry(context)
    if page is None or entry is None or idx < 0:
        return False
    snap = redo.pop()
    cursor, anchor = _restore(entry, snap)
    op._cursor_index = cursor
    op._selection_anchor = anchor
    history = list(getattr(op, "_text_edit_history", []) or [])
    history.append(snap)
    op._text_edit_history = history[-_HISTORY_LIMIT:]
    op._text_edit_redo = redo
    _after_restore(context, page, entry, idx)
    return True


def _after_restore(context, page, entry, idx: int) -> None:
    page.active_text_index = idx
    if hasattr(context.scene, "bmanga_active_layer_kind"):
        context.scene.bmanga_active_layer_kind = "text"
    text_real_object.set_text_object_preview_hidden(entry, page=page, hidden=True)
    layer_stack_utils.tag_view3d_redraw(context)


def handle_undo_redo(op, context, event):
    if str(getattr(event, "value", "") or "") != "PRESS":
        return None
    event_type = str(getattr(event, "type", "") or "")
    command = bool(getattr(event, "ctrl", False) or getattr(event, "oskey", False))
    if not command:
        return None
    if event_type == "Z" and bool(getattr(event, "shift", False)):
        restore_next(op, context)
        return {"RUNNING_MODAL"}
    if event_type == "Z":
        restore_previous(op, context)
        return {"RUNNING_MODAL"}
    if event_type == "Y":
        restore_next(op, context)
        return {"RUNNING_MODAL"}
    return None
