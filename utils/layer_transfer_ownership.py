"""ページ間移送するスタック行の実所有ページを厳密に解決する。"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.work import get_work
from .layer_hierarchy import OUTSIDE_STACK_KEY, split_child_key


@dataclass(frozen=True, slots=True)
class ItemOwner:
    scope: str
    page_id: str = ""


UNKNOWN_OWNER = ItemOwner("unknown")
OUTSIDE_OWNER = ItemOwner("outside")


def validate_single_page_group(
    context,
    items,
    expected_page_id: str,
    *,
    forbidden_folder_key: str = "",
) -> bool:
    """全itemが同じ現在ページだけに属すると証明できる場合だけTrue。"""

    expected = str(expected_page_id or "")
    forbidden = str(forbidden_folder_key or "")
    if not expected:
        return False
    found = False
    for item in items or ():
        found = True
        if (
            forbidden
            and str(getattr(item, "kind", "") or "") == "layer_folder"
            and str(getattr(item, "key", "") or "") == forbidden
        ):
            return False
        if resolve_item_owner(context, item) != ItemOwner("page", expected):
            return False
    return found


def resolve_item_owner(context, item) -> ItemOwner:
    """stack表示親ではなく、参照先実体と永続親から所有scopeを返す。"""

    from . import layer_folder, layer_object_model, layer_stack

    work = get_work(context)
    if work is None or item is None:
        return UNKNOWN_OWNER
    kind = str(getattr(item, "kind", "") or "")
    key = str(getattr(item, "key", "") or "")
    resolved = layer_stack.resolve_stack_item(context, item)
    target = resolved.get("target") if resolved is not None else None
    if target is None:
        return UNKNOWN_OWNER

    if kind == "coma":
        page = resolved.get("page")
        page_id = str(getattr(page, "id", "") or "")
        key_page, child_id = split_child_key(key)
        actual_child = str(
            getattr(target, "coma_id", "") or getattr(target, "id", "") or ""
        )
        if not page_id or key_page != page_id or child_id != actual_child:
            return UNKNOWN_OWNER
        return ItemOwner("page", page_id)

    if kind == "layer_folder":
        if str(getattr(target, "id", "") or "") != key:
            return UNKNOWN_OWNER
        return _folder_owner(work, layer_folder, key)

    if kind in {"balloon", "text"}:
        page = resolved.get("page")
        page_id = str(getattr(page, "id", "") or "")
        key_page, child_id = split_child_key(key)
        if (
            not page_id
            or key_page != page_id
            or child_id != str(getattr(target, "id", "") or "")
        ):
            return UNKNOWN_OWNER
        return _entry_owner(
            work,
            layer_folder,
            target,
            required_page_id=page_id,
        )

    if kind in {"gp", "effect"}:
        obj = resolved.get("object")
        if obj is None or layer_object_model.stable_id(obj) != key:
            return UNKNOWN_OWNER
        return _persistent_owner(
            work,
            layer_folder,
            layer_object_model.parent_key(obj),
            folder_key=layer_object_model.folder_id(obj),
        )

    if kind in {"image", "image_path", "raster", "fill"}:
        if str(getattr(target, "id", "") or "") != key:
            return UNKNOWN_OWNER
        return _entry_owner(work, layer_folder, target)

    return UNKNOWN_OWNER


def _entry_owner(
    work,
    layer_folder,
    entry,
    *,
    required_page_id: str = "",
) -> ItemOwner:
    parent_kind = str(getattr(entry, "parent_kind", "") or "")
    parent_key = str(getattr(entry, "parent_key", "") or "")
    folder_key = str(getattr(entry, "folder_key", "") or "")
    if str(getattr(entry, "scope", "") or "") == "master":
        owner = OUTSIDE_OWNER
    elif required_page_id and not parent_kind and not parent_key:
        # balloon/textはpage collection所属自体が正本。旧データで明示親が
        # 無くても、resolve_stack_itemが特定page collection内の実体を返せば
        # 所有pageは一意に証明できる。
        owner = ItemOwner("page", required_page_id)
    elif parent_kind == "none" or (not parent_kind and not parent_key):
        owner = OUTSIDE_OWNER
    else:
        owner = _parent_owner(work, layer_folder, parent_key, parent_kind)
    if folder_key and _folder_owner(work, layer_folder, folder_key) != owner:
        return UNKNOWN_OWNER
    if required_page_id and owner != ItemOwner("page", required_page_id):
        return UNKNOWN_OWNER
    return owner


def _persistent_owner(
    work,
    layer_folder,
    parent_key: str,
    *,
    parent_kind: str = "",
    folder_key: str = "",
) -> ItemOwner:
    parent_owner = _parent_owner(work, layer_folder, parent_key, parent_kind)
    if not folder_key:
        return parent_owner
    folder_owner = _folder_owner(work, layer_folder, folder_key)
    if parent_owner != folder_owner:
        return UNKNOWN_OWNER
    return parent_owner


def _folder_owner(work, layer_folder, folder_key: str) -> ItemOwner:
    folder = layer_folder.find_folder(work, str(folder_key or ""))
    if folder is None:
        return UNKNOWN_OWNER
    seen: set[str] = set()
    current = folder
    while current is not None:
        current_key = str(getattr(current, "id", "") or "")
        if not current_key or current_key in seen:
            return UNKNOWN_OWNER
        seen.add(current_key)
        parent_key = str(getattr(current, "parent_key", "") or "")
        parent = layer_folder.find_folder(work, parent_key)
        if parent is None:
            return _parent_owner(work, layer_folder, parent_key, "")
        current = parent
    return UNKNOWN_OWNER


def _parent_owner(work, layer_folder, parent_key: str, parent_kind: str) -> ItemOwner:
    key = str(parent_key or "")
    kind = str(parent_kind or "")
    if not key or key == OUTSIDE_STACK_KEY or kind == "none":
        return OUTSIDE_OWNER
    if layer_folder.find_folder(work, key) is not None:
        return _folder_owner(work, layer_folder, key)

    page_id, child_id = split_child_key(key)
    if child_id:
        page = _page_by_id(work, page_id)
        if page is None or not _page_has_coma(page, child_id):
            return UNKNOWN_OWNER
        return ItemOwner("page", page_id)

    if kind == "coma":
        owners = [
            str(getattr(page, "id", "") or "")
            for page in getattr(work, "pages", ())
            if _page_has_coma(page, key)
        ]
        return ItemOwner("page", owners[0]) if len(owners) == 1 else UNKNOWN_OWNER

    page = _page_by_id(work, key)
    return ItemOwner("page", key) if page is not None else UNKNOWN_OWNER


def _page_by_id(work, page_id: str):
    for page in getattr(work, "pages", ()):
        if str(getattr(page, "id", "") or "") == str(page_id or ""):
            return page
    return None


def _page_has_coma(page, child_id: str) -> bool:
    expected = str(child_id or "")
    return any(
        str(getattr(coma, "coma_id", "") or getattr(coma, "id", "") or "")
        == expected
        for coma in getattr(page, "comas", ())
    )


__all__ = (
    "ItemOwner",
    "OUTSIDE_OWNER",
    "UNKNOWN_OWNER",
    "resolve_item_owner",
    "validate_single_page_group",
)
