"""レイヤー一覧のリンク状態管理."""

from __future__ import annotations

import json
import uuid


LINK_PROP = "bname_layer_link_groups"
LINKABLE_KINDS = {"gp", "effect", "raster", "image", "balloon", "text"}


def _scene(context):
    return getattr(context, "scene", None) if context is not None else None


def _load_map(context) -> dict[str, str]:
    scene = _scene(context)
    if scene is None:
        return {}
    raw = str(scene.get(LINK_PROP, "") or "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(uid): str(group)
        for uid, group in data.items()
        if str(uid or "") and str(group or "")
    }


def _save_map(context, mapping: dict[str, str]) -> None:
    scene = _scene(context)
    if scene is None:
        return
    cleaned = {
        str(uid): str(group)
        for uid, group in mapping.items()
        if str(uid or "") and str(group or "")
    }
    scene[LINK_PROP] = json.dumps(
        cleaned,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def is_linkable_item(item) -> bool:
    return str(getattr(item, "kind", "") or "") in LINKABLE_KINDS


def linked_uids_for_uid(context, uid: str) -> set[str]:
    uid = str(uid or "")
    if not uid:
        return set()
    mapping = _load_map(context)
    group_id = mapping.get(uid, "")
    if not group_id:
        return {uid}
    return {item_uid for item_uid, group in mapping.items() if group == group_id}


def selected_linkable_uids(context, stack=None, *, sync: bool = True) -> list[str]:
    from . import layer_stack as layer_stack_utils

    if stack is None:
        scene = _scene(context)
        stack = getattr(scene, "bname_layer_stack", None) if scene is not None else None
        if stack is None and sync:
            stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None:
        return []
    uids: list[str] = []
    for item in stack:
        if not is_linkable_item(item):
            continue
        if not _is_visible_layer_list_item(context, item):
            continue
        if not layer_stack_utils.is_item_selected(context, item):
            continue
        uid = layer_stack_utils.stack_item_uid(item)
        if uid and uid not in uids:
            uids.append(uid)
    return uids


def _is_visible_layer_list_item(context, item) -> bool:
    from ..core.work import get_work
    from . import layer_stack as layer_stack_utils

    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return True
    active_idx = int(getattr(work, "active_page_index", -1))
    if not (0 <= active_idx < len(getattr(work, "pages", []))):
        return False
    active_page_key = layer_stack_utils.page_stack_key(work.pages[active_idx])
    if str(getattr(item, "kind", "") or "") == "page":
        return str(getattr(item, "key", "") or "") == active_page_key
    page_key = layer_stack_utils._stack_item_page_key(item, context)
    return bool(active_page_key and page_key == active_page_key)


def selected_linkable_count(context) -> int:
    return len(selected_linkable_uids(context, sync=False))


def link_uids(context, uids: list[str]) -> tuple[str, int]:
    unique = [str(uid) for uid in uids if str(uid or "")]
    unique = list(dict.fromkeys(unique))
    if len(unique) < 2:
        return "", 0
    mapping = _load_map(context)
    existing_groups = {mapping[uid] for uid in unique if mapping.get(uid)}
    group_id = sorted(existing_groups)[0] if existing_groups else f"layer_link_{uuid.uuid4().hex}"
    if existing_groups:
        for uid, current_group in list(mapping.items()):
            if current_group in existing_groups:
                mapping[uid] = group_id
    for uid in unique:
        mapping[uid] = group_id
    _save_map(context, mapping)
    return group_id, len(unique)


def link_selected(context, stack=None) -> tuple[str, int]:
    return link_uids(context, selected_linkable_uids(context, stack=stack))


def set_item_and_linked_selected(context, item, value: bool, *, stack=None) -> bool:
    from . import layer_stack as layer_stack_utils

    if stack is None:
        stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None or item is None:
        return False
    uid = layer_stack_utils.stack_item_uid(item)
    targets = linked_uids_for_uid(context, uid) if is_linkable_item(item) else {uid}
    changed = False
    for row in stack:
        if layer_stack_utils.stack_item_uid(row) in targets:
            changed = layer_stack_utils.set_item_selected(context, row, bool(value)) or changed
    return changed


def expand_linked_selection(context, *, stack=None, base_item=None) -> int:
    from . import layer_stack as layer_stack_utils

    if stack is None:
        stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None:
        return 0
    target_uids: set[str] = set()
    if base_item is not None and is_linkable_item(base_item):
        target_uids.update(linked_uids_for_uid(context, layer_stack_utils.stack_item_uid(base_item)))
    else:
        for uid in selected_linkable_uids(context, stack=stack):
            target_uids.update(linked_uids_for_uid(context, uid))
    if not target_uids:
        return 0
    changed = 0
    for item in stack:
        if layer_stack_utils.stack_item_uid(item) in target_uids:
            if layer_stack_utils.set_item_selected(context, item, True):
                changed += 1
    return changed


def linked_object_keys_for_key(context, key: str) -> list[str]:
    from . import layer_stack as layer_stack_utils

    key = str(key or "")
    if not key:
        return []
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None:
        return [key]
    object_key_by_uid: dict[str, str] = {}
    matched_uids: list[str] = []
    for item in stack:
        if not is_linkable_item(item):
            continue
        uid = layer_stack_utils.stack_item_uid(item)
        object_key = _object_key_for_item(context, item)
        if not uid or not object_key:
            continue
        object_key_by_uid[uid] = object_key
        if object_key == key:
            matched_uids.append(uid)
    if not matched_uids:
        return [key]
    linked_uids: set[str] = set()
    for uid in matched_uids:
        linked_uids.update(linked_uids_for_uid(context, uid))
    out: list[str] = []
    for item in stack:
        uid = layer_stack_utils.stack_item_uid(item)
        object_key = object_key_by_uid.get(uid, "")
        if uid in linked_uids and object_key and object_key not in out:
            out.append(object_key)
    return out or [key]


def object_key_for_item(context, item) -> str:
    return _object_key_for_item(context, item)


def _object_key_for_item(context, item) -> str:
    from . import layer_stack as layer_stack_utils
    from . import object_selection

    resolved = layer_stack_utils.resolve_stack_item(context, item)
    if resolved is None:
        return ""
    target = resolved.get("target")
    if target is None:
        return ""
    kind = str(getattr(item, "kind", "") or "")
    if kind == "gp":
        return object_selection.gp_key(target)
    if kind == "effect":
        return object_selection.effect_key(target)
    if kind == "image":
        return object_selection.image_key(target)
    if kind == "raster":
        return object_selection.raster_key(target)
    if kind == "balloon":
        page = resolved.get("page")
        return object_selection.balloon_key(page, target) if page is not None else ""
    if kind == "text":
        page = resolved.get("page")
        return object_selection.text_key(page, target) if page is not None else ""
    return ""
