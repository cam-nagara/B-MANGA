"""Domainの親別children順とBlenderレイヤー一覧を一方向に投影する。"""

from __future__ import annotations

import copy
from collections.abc import Iterable

from ..bmanga_core.domain_ids import UIDKind, is_uid
from ..bmanga_core.domain_model import PageDocument
from .domain_projection_ids import NODE_UID_PROP, custom_get


_STACK_TO_DOMAIN_KIND = {
    "layer_folder": "folder",
}


def apply_ranked_order(
    document: PageDocument,
    ordered_node_uids: Iterable[str],
) -> PageDocument:
    """一覧の前面→背面順を、各親のchildren配列へ反映する。

    一覧へ投影しない未知nodeは元の位置を保つ。これにより、後続Phaseのnodeを
    先にDomainへ載せても、現在のUI操作がその順序を壊さない。
    """

    result = copy.deepcopy(document)
    rank = _unique_rank(ordered_node_uids, result)
    if not rank:
        return result
    for parent_uid, children in tuple(result.children.items()):
        ranked = sorted(
            (uid for uid in children if uid in rank),
            key=rank.__getitem__,
        )
        if len(ranked) < 2:
            continue
        replacements = iter(ranked)
        result.children[parent_uid] = [
            next(replacements) if uid in rank else uid
            for uid in children
        ]
    result.validate()
    return result


def _unique_rank(
    ordered_node_uids: Iterable[str],
    document: PageDocument,
) -> dict[str, int]:
    rank: dict[str, int] = {}
    for value in ordered_node_uids:
        uid = str(value or "")
        if (
            uid == document.root_uid
            or uid not in document.nodes
            or uid in rank
        ):
            continue
        rank[uid] = len(rank)
    return rank


def stack_node_uids(context, document: PageDocument) -> tuple[str, ...]:
    """現在のレイヤー一覧をDomain node UID列へ変換する。"""

    scene = getattr(context, "scene", None)
    stack = getattr(scene, "bmanga_layer_stack", None) if scene is not None else None
    if stack is None:
        return ()
    fallback = _fallback_node_index(document)
    resolved: list[str] = []
    for item in stack:
        uid = _node_uid_for_stack_item(context, item, fallback)
        if uid and uid in document.nodes and uid != document.root_uid:
            resolved.append(uid)
    return tuple(dict.fromkeys(resolved))


def capture_stack_order(context, document: PageDocument) -> PageDocument:
    """Blender一覧の順序だけをDomain treeへ取り込む。"""

    ordered = stack_node_uids(context, document)
    result = apply_ranked_order(document, ordered)
    return result


def project_document_order(context, document: PageDocument) -> bool:
    """Domain tree順を既存のレイヤー一覧行へ投影する。"""

    scene = getattr(context, "scene", None)
    stack = getattr(scene, "bmanga_layer_stack", None) if scene is not None else None
    if stack is None or len(stack) < 2:
        return False
    fallback = _fallback_node_index(document)
    desired = _tree_order(document)
    desired_rank = {uid: index for index, uid in enumerate(desired)}
    slots = []
    current = []
    for index, item in enumerate(stack):
        uid = _node_uid_for_stack_item(context, item, fallback)
        if uid in desired_rank:
            slots.append(index)
            current.append(uid)
    ordered = sorted(current, key=desired_rank.__getitem__)
    if current == ordered:
        return False
    for slot, uid in zip(slots, ordered, strict=True):
        current_index = _stack_index_for_node(
            context,
            stack,
            uid,
            fallback,
        )
        if current_index >= 0 and current_index != slot:
            stack.move(current_index, slot)
    return True


def _tree_order(document: PageDocument) -> tuple[str, ...]:
    result: list[str] = []

    def visit(parent_uid: str) -> None:
        for child_uid in document.children[parent_uid]:
            result.append(child_uid)
            visit(child_uid)

    visit(document.root_uid)
    return tuple(result)


def _fallback_node_index(
    document: PageDocument,
) -> dict[tuple[str, str], str]:
    return {
        (node.kind, node.display_id): uid
        for uid, node in document.nodes.items()
        if uid != document.root_uid
    }


def _node_uid_for_stack_item(context, item, fallback) -> str:
    from ..utils import layer_stack

    resolved = layer_stack.resolve_stack_item(context, item)
    if resolved is None:
        return ""
    target = resolved.get("target")
    obj = resolved.get("object")
    for owner in (target, obj):
        uid = str(custom_get(owner, NODE_UID_PROP, "") or "")
        if is_uid(uid, UIDKind.NODE):
            return uid
    kind = _STACK_TO_DOMAIN_KIND.get(
        str(getattr(item, "kind", "") or ""),
        str(getattr(item, "kind", "") or ""),
    )
    display_id = _resolved_display_id(item, resolved)
    return fallback.get((kind, display_id), "")


def _resolved_display_id(item, resolved: dict) -> str:
    target = resolved.get("target")
    value = str(
        getattr(target, "coma_id", "")
        or getattr(target, "id", "")
        or resolved.get("stable_id", "")
        or ""
    )
    if value:
        return value
    key = str(getattr(item, "key", "") or "")
    if str(getattr(item, "kind", "") or "") in {"balloon", "text", "coma"}:
        return key.rsplit(":", 1)[-1]
    return key


def _stack_index_for_node(context, stack, node_uid: str, fallback) -> int:
    for index, item in enumerate(stack):
        if _node_uid_for_stack_item(context, item, fallback) == node_uid:
            return index
    return -1


__all__ = (
    "apply_ranked_order",
    "capture_stack_order",
    "project_document_order",
    "stack_node_uids",
)
