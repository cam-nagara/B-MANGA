"""ページ間移動で対象同士のリンクグループを復元する。"""

from __future__ import annotations

import json
from collections.abc import Callable


LINK_ENTRIES_KEY = "link_transfers"
MANIFEST_PROP = "bmanga_cross_page_link_stage_manifest"


def _manifest(scene) -> dict[str, str]:
    raw = str(scene.get(MANIFEST_PROP, "") or "") if scene is not None else ""
    try:
        data = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        data = {}
    return {
        str(key): str(value)
        for key, value in data.items()
        if str(key or "") and str(value or "")
    } if isinstance(data, dict) else {}


def _save_manifest(scene, manifest: dict[str, str]) -> None:
    if scene is not None:
        scene[MANIFEST_PROP] = json.dumps(
            manifest,
            ensure_ascii=False,
            separators=(",", ":"),
        )


def _normalized_groups(entry: dict) -> list[list[str]]:
    from . import layer_uid

    result = []
    for raw_group in entry.get("groups", []) if isinstance(entry, dict) else []:
        if not isinstance(raw_group, list):
            continue
        group = []
        for raw_uid in raw_group:
            try:
                uid = layer_uid.validate_uid(raw_uid)
            except (TypeError, ValueError):
                continue
            if uid not in group:
                group.append(uid)
        if len(group) >= 2:
            result.append(group)
    return result


def _uid_exists(context, page, uid: str) -> bool:
    from . import layer_object_model, layer_uid

    parsed = layer_uid.parse_uid(uid)
    page_id = str(getattr(page, "id", "") or "")
    if parsed.kind in {"gp", "effect"}:
        obj = layer_object_model.find_layer_object(parsed.kind, parsed.key)
        if obj is None:
            return False
        return layer_object_model.parent_key(obj).split(":", 1)[0] == page_id
    if parsed.kind not in {"balloon", "text"} or parsed.parts[0] != page_id:
        return False
    collection = getattr(page, "balloons" if parsed.kind == "balloon" else "texts", None)
    return collection is not None and any(
        str(getattr(item, "id", "") or "") == parsed.parts[1]
        for item in collection
    )


def _clean_singletons(mapping: dict[str, str]) -> dict[str, str]:
    counts: dict[str, int] = {}
    for group_id in mapping.values():
        counts[group_id] = counts.get(group_id, 0) + 1
    return {uid: group for uid, group in mapping.items() if counts.get(group, 0) >= 2}


def _apply(context, page, entry: dict, token: str) -> bool:
    from . import layer_links

    groups = _normalized_groups(entry)
    all_uids = {uid for group in groups for uid in group}
    if not groups or not all(_uid_exists(context, page, uid) for uid in all_uids):
        return False
    mapping = layer_links._load_map(context)
    mapping = _clean_singletons({uid: group for uid, group in mapping.items() if uid not in all_uids})
    transfer_id = str(entry.get("transfer_id", "") or "")
    used_groups = set(mapping.values())
    for index, group in enumerate(groups):
        group_id = f"layer_link_move_{transfer_id}_{index}"
        while group_id in used_groups:
            group_id += "_"
        used_groups.add(group_id)
        mapping.update({uid: group_id for uid in group})
    layer_links._save_map(context, mapping)
    manifest = _manifest(getattr(context, "scene", None))
    manifest[transfer_id] = token
    _save_manifest(getattr(context, "scene", None), manifest)
    return True


def process(
    context,
    page,
    entries: list,
    token_factory: Callable[[str, dict], str],
) -> tuple[set[str], list[str]]:
    """保存済みは確定対象とし、未処理分だけ適用する。"""
    manifest = _manifest(getattr(context, "scene", None))
    processed: set[str] = set()
    pending: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        transfer_id = str(entry.get("transfer_id", "") or "")
        token = token_factory("link", entry)
        if not transfer_id or not token:
            continue
        if manifest.get(transfer_id) == token:
            processed.add(token)
        elif not _apply(context, page, entry, token):
            pending.append(transfer_id)
    return processed, pending


def is_saved(context, entry: dict, token: str) -> bool:
    transfer_id = str(entry.get("transfer_id", "") or "")
    return bool(
        transfer_id
        and token
        and _manifest(getattr(context, "scene", None)).get(transfer_id) == token
    )


__all__ = ["LINK_ENTRIES_KEY", "is_saved", "process"]
