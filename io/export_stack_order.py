"""ページ書き出しへレイヤー一覧のコマプレビュー境界を反映する。"""

from __future__ import annotations

from dataclasses import replace


def stack_uid_for_object(obj) -> str:
    try:
        from ..utils import layer_stack
        kind = str(obj.get("bmanga_kind", "") or "")
        key = str(obj.get("bmanga_id", "") or "")
        if kind in {"gp", "effect"} and key:
            return layer_stack.target_uid(kind, key)
    except Exception:  # noqa: BLE001
        pass
    return ""


def entry_stack_uid(kind: str, page, entry) -> str:
    from ..utils import layer_stack
    from ..utils.layer_hierarchy import page_stack_key
    key = str(getattr(entry, "id", "") or "")
    return layer_stack.target_uid(kind, f"{page_stack_key(page)}:{key}")


def entry_stack_parent_key(kind: str, page, entry) -> str:
    parent_key = str(getattr(entry, "parent_key", "") or "")
    folder_key = str(getattr(entry, "folder_key", "") or "")
    group_id = str(getattr(entry, "merge_group_id", "") or "") if kind == "balloon" else ""
    if not group_id:
        return folder_key or parent_key
    from ..utils.layer_hierarchy import page_stack_key
    group_key = f"{page_stack_key(page)}:{group_id}"
    try:
        import bpy
        if any(
            str(getattr(item, "key", "") or "") == group_key
            for item in getattr(bpy.context.scene, "bmanga_layer_stack", ())
        ):
            return group_key
    except Exception:  # noqa: BLE001
        pass
    return folder_key or parent_key


def apply_coma_visibility(page, layers):
    """非表示コマの全出力レイヤーを非表示にする。

    PSDではレイヤー構造を残したまま非表示、PNG等の合成では描画対象外になる。
    """
    hidden_ids = {
        str(getattr(panel, "coma_id", "") or getattr(panel, "id", "") or "")
        for panel in getattr(page, "comas", ()) or ()
        if not bool(getattr(panel, "visible", True))
    }
    if not hidden_ids:
        return list(layers)
    result = []
    for layer in layers:
        group_path = tuple(getattr(layer, "group_path", ()) or ())
        belongs_to_hidden_coma = (
            len(group_path) >= 2
            and group_path[0] == "comas"
            and str(group_path[1]) in hidden_ids
        )
        result.append(
            replace(layer, visible=False)
            if belongs_to_hidden_coma and bool(getattr(layer, "visible", True))
            else layer
        )
    return result


def _container_indexes(stack, index_by_uid) -> dict[str, int]:
    from ..utils import layer_stack
    result = {}
    for item in stack:
        uid = layer_stack.stack_item_uid(item)
        if uid in index_by_uid:
            result[str(getattr(item, "key", "") or "")] = index_by_uid[uid]
    return result


def _layer_stack_index(layer, containers, index_by_uid, work):
    from ..utils import layer_folder
    direct = index_by_uid.get(str(layer.stack_uid or ""))
    if direct is not None:
        return direct
    parent = str(layer.stack_parent_key or "")
    seen: set[str] = set()
    while parent and parent not in seen:
        found = containers.get(parent)
        if found is not None:
            return found
        seen.add(parent)
        folder = layer_folder.find_folder(work, parent)
        if folder is None:
            break
        parent = layer_folder.folder_parent_key(folder)
    return None


def _partition_or_order(result, positioned, preview_uid, preview_index, side):
    if side in {"front", "back"}:
        remove = {
            position
            for position, _layer, index in positioned
            if (side == "front" and int(index) >= preview_index)
            or (side == "back" and int(index) <= preview_index)
        }
        return [layer for position, layer in enumerate(result) if position not in remove]
    if not any(layer.stack_uid == preview_uid for _pos, layer, _idx in positioned):
        return result
    positions = {position for position, _layer, _index in positioned}
    insertion = min(positions)
    ordered = [
        layer
        for _position, layer, _index in sorted(
            positioned, key=lambda item: int(item[2]), reverse=True,
        )
    ]
    result = [layer for position, layer in enumerate(result) if position not in positions]
    result[insertion:insertion] = ordered
    return result


def apply_coma_preview_order(work, page, layers, *, side: str = "all"):
    """レイヤー一覧のプレビュー境界をPNG/PSDの合成順にも反映する。"""
    try:
        import bpy
        from ..utils import layer_object_sync
        from ..utils.layer_hierarchy import coma_stack_key
    except Exception:  # pragma: no cover - bpy unavailable outside Blender
        return layers
    result = list(layers)
    scene = getattr(bpy.context, "scene", None)
    stack = getattr(scene, "bmanga_layer_stack", None) if scene is not None else None
    if stack is None:
        return result
    for panel in getattr(page, "comas", ()):
        order, preview_uid = layer_object_sync.coma_stack_order(
            scene, work, coma_stack_key(page, panel),
        )
        index_by_uid = {uid: index for index, uid in enumerate(order)}
        if preview_uid not in index_by_uid:
            continue
        containers = _container_indexes(stack, index_by_uid)
        positioned = []
        for position, layer in enumerate(result):
            if not (layer.stack_uid or layer.stack_parent_key):
                continue
            index = _layer_stack_index(layer, containers, index_by_uid, work)
            if index is not None:
                positioned.append((position, layer, index))
        result = _partition_or_order(
            result, positioned, preview_uid, index_by_uid[preview_uid], side,
        )
    return result


def partition_around_stack_uid(
    work,
    layers,
    anchor_uid: str,
    *,
    exclude_uids=(),
):
    """合成済み順を保ったまま、操作対象の背面・対象・前面へ分割する."""
    try:
        import bpy
        from ..utils import layer_stack
    except Exception:  # pragma: no cover - bpy unavailable outside Blender
        return list(layers), [], []
    scene = getattr(bpy.context, "scene", None)
    stack = getattr(scene, "bmanga_layer_stack", None) if scene is not None else None
    if stack is None:
        return list(layers), [], []
    index_by_uid = {
        layer_stack.stack_item_uid(item): index for index, item in enumerate(stack)
    }
    anchor_index = index_by_uid.get(str(anchor_uid or ""))
    if anchor_index is None:
        return list(layers), [], []
    anchor_item = next(
        (
            item
            for item in stack
            if layer_stack.stack_item_uid(item) == str(anchor_uid or "")
        ),
        None,
    )
    anchor_key = str(getattr(anchor_item, "key", "") or "") if anchor_item else ""
    excluded = {str(uid) for uid in exclude_uids if uid}
    excluded.add(str(anchor_uid or ""))
    containers = _container_indexes(stack, index_by_uid)
    positions = []
    active_positions = set()
    for position, layer in enumerate(layers):
        uid = str(getattr(layer, "stack_uid", "") or "")
        parent = str(getattr(layer, "stack_parent_key", "") or "")
        if uid in excluded or (anchor_key and parent == anchor_key):
            active_positions.add(position)
            continue
        index = _layer_stack_index(layer, containers, index_by_uid, work)
        positions.append((position, index))
    if not active_positions:
        return list(layers), [], []
    first_active = min(active_positions)
    back = []
    active = []
    front = []
    index_by_position = dict(positions)
    for position, layer in enumerate(layers):
        if position in active_positions:
            active.append(layer)
            continue
        index = index_by_position.get(position)
        if index is None:
            (back if position < first_active else front).append(layer)
        elif int(index) > int(anchor_index):
            back.append(layer)
        else:
            front.append(layer)
    return back, active, front
