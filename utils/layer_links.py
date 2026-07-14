"""レイヤー一覧のリンク状態管理."""

from __future__ import annotations

import json
import uuid

from . import layer_hierarchy


LINK_PROP = "bmanga_layer_link_groups"
LINKABLE_KINDS = {"gp", "effect", "raster", "image", "balloon", "text"}
# テキスト⇔フキダシの紐付けが対応する kind (リンクグループとは別機構)
_TEXT_BALLOON_KINDS = {"balloon", "text"}


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
        stack = getattr(scene, "bmanga_layer_stack", None) if scene is not None else None
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


def is_uid_linked(context, uid: str) -> bool:
    return bool(_load_map(context).get(str(uid or ""), ""))


def selected_any_linked(context) -> bool:
    return any(
        is_uid_linked(context, uid)
        for uid in selected_linkable_uids(context, sync=False)
    )


def unlink_uids(context, uids: list[str]) -> int:
    """指定レイヤーをリンクグループから外す。1件だけ残ったグループは解散する."""
    unique = [str(uid) for uid in uids if str(uid or "")]
    if not unique:
        return 0
    mapping = _load_map(context)
    removed = 0
    for uid in unique:
        if mapping.pop(uid, ""):
            removed += 1
    if removed:
        counts: dict[str, int] = {}
        for group in mapping.values():
            counts[group] = counts.get(group, 0) + 1
        mapping = {
            uid: group for uid, group in mapping.items() if counts.get(group, 0) >= 2
        }
        _save_map(context, mapping)
    return removed


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
        return object_selection.gp_key(resolved.get("object"))
    if kind == "effect":
        return object_selection.effect_key(resolved.get("object"))
    if kind == "image":
        return object_selection.image_key(target)
    if kind == "raster":
        return object_selection.raster_key(target)
    if kind == "balloon":
        page = resolved.get("page")
        return object_selection.balloon_key(page, target)
    if kind == "text":
        page = resolved.get("page")
        return object_selection.text_key(page, target)
    if kind == "coma":
        page = resolved.get("page")
        return object_selection.coma_key(page, target)
    return ""


def _balloon_text_partner_uids(context, kind: str, entry, page) -> set[str]:
    """テキスト⇔フキダシの紐付け相手 uid を返す (同一ページ内のみ対応).

    - テキスト側: ``entry.parent_balloon_id`` が指すフキダシ
    - フキダシ側: ``entry.text_id`` が指すテキスト、および
      ``text.parent_balloon_id == entry.id`` となる全テキスト
    """
    from . import layer_stack as layer_stack_utils

    if entry is None or page is None:
        return set()
    page_key = layer_hierarchy.page_stack_key(page)
    partners: set[str] = set()
    if kind == "text":
        balloon_id = str(getattr(entry, "parent_balloon_id", "") or "")
        if not balloon_id:
            return partners
        for balloon in getattr(page, "balloons", []) or []:
            if str(getattr(balloon, "id", "") or "") == balloon_id:
                partners.add(layer_stack_utils.target_uid("balloon", f"{page_key}:{balloon_id}"))
                break
    elif kind == "balloon":
        balloon_id = str(getattr(entry, "id", "") or "")
        text_id = str(getattr(entry, "text_id", "") or "")
        for text in getattr(page, "texts", []) or []:
            tid = str(getattr(text, "id", "") or "")
            parent_id = str(getattr(text, "parent_balloon_id", "") or "")
            if (balloon_id and parent_id == balloon_id) or (text_id and tid == text_id):
                if tid:
                    partners.add(layer_stack_utils.target_uid("text", f"{page_key}:{tid}"))
    return partners


def _link_group_partners(context, uid: str) -> set[str]:
    """uid のリンクグループ相手を返す (グループが無い/自分だけなら空集合)."""
    group = linked_uids_for_uid(context, uid)
    return set(group) if len(group) > 1 else set()


def related_uids_for_target(context, kind: str, entry, page=None) -> set[str]:
    """kind/entry(/page) が指すレイヤー1件の『リンク相手』 uid 集合を返す (自分は含まない).

    相手は次の2種類の和集合:
      (a) ``bmanga_layer_link_groups`` の同グループメンバー
      (b) テキスト⇔フキダシの紐付け相手 (``parent_balloon_id`` / ``text_id``)

    行オブジェクト (``bmanga_layer_stack`` の item) を持たない詳細設定ダイアログ
    (右クリック版: ``operators.layer_detail_op``) からも呼べるよう、
    entry ベースで uid を組み立てる。gp/effect はノードキー方式で entry に
    ``id`` が無く、ここでは uid を再構築できないため対象外
    (呼び出し側で gp/effect は別の描画経路へ既に分岐済み)。
    レイヤー一覧側は ``related_uids_for_item`` / ``related_uids_for_selection`` を使う。
    """
    from . import layer_stack as layer_stack_utils

    kind = str(kind or "")
    entry_id = str(getattr(entry, "id", "") or "")
    if not kind or not entry_id:
        return set()
    if kind in _TEXT_BALLOON_KINDS:
        page_key = layer_hierarchy.OUTSIDE_STACK_KEY if page is None else layer_hierarchy.page_stack_key(page)
        key = f"{page_key}:{entry_id}"
    else:
        key = entry_id
    uid = layer_stack_utils.target_uid(kind, key)
    partners: set[str] = set()
    if kind in LINKABLE_KINDS:
        partners.update(_link_group_partners(context, uid))
    if kind in _TEXT_BALLOON_KINDS:
        partners.update(_balloon_text_partner_uids(context, kind, entry, page))
    partners.discard(uid)
    return partners


def related_uids_for_item(context, item) -> set[str]:
    """スタック行 (item) の『リンク相手』 uid 集合を返す (自分は含まない).

    ``item.key`` (= ``stack_item_uid``) は kind ごとの正しい形式で既にスタック
    同期時に組み立て済みなので (gp/effect のノードキー方式も含む)、
    ``related_uids_for_target`` のような entry からの uid 再構築はせず、
    ここでは ``stack_item_uid`` をそのまま使う。
    """
    if item is None:
        return set()
    from . import layer_stack as layer_stack_utils

    uid = layer_stack_utils.stack_item_uid(item)
    if not uid:
        return set()
    kind = str(getattr(item, "kind", "") or "")
    partners: set[str] = set()
    if kind in LINKABLE_KINDS:
        partners.update(_link_group_partners(context, uid))
    if kind in _TEXT_BALLOON_KINDS:
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        target = resolved.get("target") if resolved is not None else None
        page = resolved.get("page") if resolved is not None else None
        if target is not None:
            partners.update(_balloon_text_partner_uids(context, kind, target, page))
    partners.discard(uid)
    return partners


def related_uids_for_selection(context, stack=None) -> set[str]:
    """選択中の全行について『リンク相手あり』の uid 集合を返す (自分自身も含む).

    レイヤー一覧のリンクマーク表示に使う。1行ごとに JSON をパースし直すため
    毎 draw_item で呼ぶとコストが積み上がる。呼び出し側 (gpencil_panel) で
    パネル描画につき1回だけ計算してキャッシュし、draw_item はそのキャッシュ
    を参照するだけにすること。
    """
    from . import layer_stack as layer_stack_utils

    if stack is None:
        scene = _scene(context)
        # 全ページ横断の統合スタックではなく、画面に出ている行 (現在ページ+共有)
        # だけを走査する。マークが付くのは可視行だけなので判定はこれで足り、
        # ページ数×レイヤー数に比例した毎描画の全件走査を避けられる。
        # (可視リストが未構築の場合のみ統合スタックへフォールバック)
        visible = getattr(scene, "bmanga_layer_stack_visible", None) if scene is not None else None
        if visible is not None and len(visible) > 0:
            stack = visible
        else:
            stack = getattr(scene, "bmanga_layer_stack", None) if scene is not None else None
    if stack is None:
        return set()
    result: set[str] = set()
    for item in stack:
        if not layer_stack_utils.is_item_selected(context, item):
            continue
        partners = related_uids_for_item(context, item)
        if not partners:
            continue
        uid = layer_stack_utils.stack_item_uid(item)
        if uid:
            result.add(uid)
        result.update(partners)
    return result
