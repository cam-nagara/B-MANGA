"""レイヤーパネル用の表示専用レイヤー一覧."""

from __future__ import annotations

from ..core.work import get_work
from . import layer_folder as layer_folder_utils
from .layer_hierarchy import (
    COMA_KIND,
    OUTSIDE_KIND,
    OUTSIDE_STACK_KEY,
    PAGE_KIND,
    page_stack_key,
    split_child_key,
)

COMA_PREVIEW_KIND = "coma_preview"
LAYER_FOLDER_KIND = layer_folder_utils.LAYER_FOLDER_KIND


def _target_uid(kind: str, key: str) -> str:
    return f"{kind}:{key}"


def _stack_item_uid(item) -> str:
    return _target_uid(getattr(item, "kind", ""), getattr(item, "key", ""))


def collapsed_balloon_group_keys(context) -> set[str]:
    scene = getattr(context, "scene", None)
    if scene is None or not hasattr(scene, "bmanga_collapsed_balloon_group_keys"):
        return set()
    raw = str(getattr(scene, "bmanga_collapsed_balloon_group_keys", "") or "")
    return {line.strip() for line in raw.splitlines() if line.strip()}


def is_balloon_group_collapsed(context, key: str) -> bool:
    return str(key or "") in collapsed_balloon_group_keys(context)


def set_balloon_group_collapsed(context, key: str, collapsed: bool) -> None:
    scene = getattr(context, "scene", None)
    if scene is None or not hasattr(scene, "bmanga_collapsed_balloon_group_keys"):
        return
    keys = collapsed_balloon_group_keys(context)
    text_key = str(key or "")
    if not text_key:
        return
    if collapsed:
        keys.add(text_key)
    else:
        keys.discard(text_key)
    scene.bmanga_collapsed_balloon_group_keys = "\n".join(sorted(keys))


def _copy_stack_item_values(dst, src) -> bool:
    changed = False
    values = (
        ("kind", getattr(src, "kind", "")),
        ("name", getattr(src, "name", "")),
        ("key", getattr(src, "key", "")),
        ("label", getattr(src, "label", "")),
        ("parent_key", getattr(src, "parent_key", "")),
        ("depth", int(getattr(src, "depth", 0) or 0)),
    )
    for prop_name, value in values:
        if getattr(dst, prop_name) != value:
            setattr(dst, prop_name, value)
            changed = True
    return changed


def find_stack_index_for_item(stack, item) -> int:
    """同じ参照先を持つ実レイヤー一覧の行番号を返す."""
    uid = _stack_item_uid(item)
    if stack is None or not uid:
        return -1
    for index, candidate in enumerate(stack):
        if _stack_item_uid(candidate) == uid:
            return index
    return -1


def _stack_item_page_key(item, context) -> str:
    kind = str(getattr(item, "kind", "") or "")
    key = str(getattr(item, "key", "") or "")
    parent_key = str(getattr(item, "parent_key", "") or "")
    if key == OUTSIDE_STACK_KEY or parent_key == OUTSIDE_STACK_KEY:
        return ""
    if kind in {COMA_KIND, COMA_PREVIEW_KIND, "balloon", "balloon_group", "text"}:
        page_key, _child = split_child_key(key)
        return "" if page_key == OUTSIDE_STACK_KEY else page_key
    if kind in {"raster", "image", "gp", "gp_folder", "effect", "fill"}:
        page_key, _child = split_child_key(parent_key)
        return "" if page_key == OUTSIDE_STACK_KEY else page_key
    if kind == LAYER_FOLDER_KIND:
        work = get_work(context)
        semantic_parent = layer_folder_utils.semantic_parent_key_for_folder(work, key)
        if semantic_parent == OUTSIDE_STACK_KEY:
            return ""
        page_key, _child = split_child_key(semantic_parent)
        return page_key
    return ""


def visible_layer_stack_entries(context, stack=None) -> list[tuple[int, object]]:
    """選択ページのレイヤー一覧に表示する実レイヤー行だけを返す."""
    scene = getattr(context, "scene", None)
    if scene is None:
        return []
    stack = stack if stack is not None else getattr(scene, "bmanga_layer_stack", None)
    if stack is None:
        return []
    work = get_work(context)
    active_page_idx = int(getattr(work, "active_page_index", -1)) if work is not None else -1
    active_page_key = ""
    if work is not None and 0 <= active_page_idx < len(work.pages):
        active_page_key = page_stack_key(work.pages[active_page_idx])
    if not active_page_key:
        return []

    entries: list[tuple[int, object]] = []
    collapsed_groups = collapsed_balloon_group_keys(context)
    for index, item in enumerate(stack):
        kind = str(getattr(item, "kind", "") or "")
        if kind in {OUTSIDE_KIND, PAGE_KIND}:
            continue
        if kind == "balloon" and str(getattr(item, "parent_key", "") or "") in collapsed_groups:
            continue
        if _stack_item_page_key(item, context) == active_page_key:
            entries.append((index, item))
    return entries


def visible_layer_stack_signature(context, stack=None) -> tuple[str, ...]:
    return tuple(_stack_item_uid(item) for _index, item in visible_layer_stack_entries(context, stack))


def current_visible_layer_stack_signature(scene) -> tuple[str, ...]:
    visible = getattr(scene, "bmanga_layer_stack_visible", None)
    if visible is None:
        return ()
    return tuple(_stack_item_uid(item) for item in visible)


def visible_layer_stack_is_current(context, stack=None) -> bool:
    scene = getattr(context, "scene", None)
    if scene is None:
        return True
    return current_visible_layer_stack_signature(scene) == visible_layer_stack_signature(
        context,
        stack,
    )


def set_active_visible_stack_index_silently(context, index: int) -> None:
    scene = getattr(context, "scene", None)
    if scene is None or not hasattr(scene, "bmanga_active_layer_stack_visible_index"):
        return
    if int(getattr(scene, "bmanga_active_layer_stack_visible_index", -1)) == int(index):
        return
    core_layer_stack = None
    try:
        from ..core import layer_stack as core_layer_stack

        core_layer_stack._visible_index_update_depth += 1
    except Exception:  # noqa: BLE001
        core_layer_stack = None
    try:
        scene.bmanga_active_layer_stack_visible_index = int(index)
    finally:
        if core_layer_stack is not None:
            core_layer_stack._visible_index_update_depth = max(
                0,
                core_layer_stack._visible_index_update_depth - 1,
            )


def sync_visible_layer_stack(context, *, stack=None) -> bool:
    """表示用レイヤー一覧を、選択ページ内の行だけに同期する."""
    scene = getattr(context, "scene", None)
    if scene is None:
        return False
    visible = getattr(scene, "bmanga_layer_stack_visible", None)
    if visible is None:
        return False
    stack = stack if stack is not None else getattr(scene, "bmanga_layer_stack", None)
    entries = visible_layer_stack_entries(context, stack)
    changed = False
    while len(visible) > len(entries):
        visible.remove(len(visible) - 1)
        changed = True
    for visible_index, (_source_index, source_item) in enumerate(entries):
        if visible_index >= len(visible):
            visible.add()
            changed = True
        changed = _copy_stack_item_values(visible[visible_index], source_item) or changed

    active_source_index = int(getattr(scene, "bmanga_active_layer_stack_index", -1))
    active_visible_index = -1
    for visible_index, (source_index, _source_item) in enumerate(entries):
        if source_index == active_source_index:
            active_visible_index = visible_index
            break
    if active_visible_index < 0 and len(visible) > 0:
        active_visible_index = 0
    set_active_visible_stack_index_silently(context, active_visible_index)
    return changed
