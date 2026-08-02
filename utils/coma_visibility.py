"""コマレイヤーと内包Collectionの表示状態を同期する。"""

from __future__ import annotations

import bpy

from . import object_naming
from .layer_hierarchy import coma_stack_key, page_stack_key

PROP_PARENT_HIDDEN = "bmanga_coma_parent_hidden"
PROP_PREVIOUS_HIDE = "bmanga_coma_parent_previous_hide"
PROP_PREVIOUS_HIDE_RENDER = "bmanga_coma_parent_previous_hide_render"
_SYNC_SCHEDULED = False


def _same_rna_value(left, right) -> bool:
    if left is right:
        return True
    try:
        return int(left.as_pointer()) == int(right.as_pointer())
    except Exception:  # noqa: BLE001
        return False


def _set_collection_visible(collection, visible: bool) -> bool:
    if collection is None:
        return False
    hidden = not bool(visible)
    changed = False
    for attr in ("hide_viewport", "hide_render"):
        if not hasattr(collection, attr):
            continue
        if bool(getattr(collection, attr)) == hidden:
            continue
        setattr(collection, attr, hidden)
        changed = True
    return changed


def _object_semantic_parent_key(obj, work) -> str:
    parent_key = str(obj.get(object_naming.PROP_PARENT_KEY, "") or "")
    folder_key = str(obj.get(object_naming.PROP_FOLDER_ID, "") or "")
    if folder_key:
        try:
            from . import layer_folder

            semantic = layer_folder.semantic_parent_key_for_folder(work, folder_key)
            if semantic:
                return str(semantic)
        except Exception:  # noqa: BLE001
            pass
    return parent_key


def _set_object_parent_visible(obj, visible: bool) -> bool:
    hidden = not bool(visible)
    changed = False
    if hidden:
        if not bool(obj.get(PROP_PARENT_HIDDEN, False)):
            try:
                obj[PROP_PREVIOUS_HIDE] = bool(obj.hide_get())
            except Exception:  # noqa: BLE001
                obj[PROP_PREVIOUS_HIDE] = False
            obj[PROP_PREVIOUS_HIDE_RENDER] = bool(getattr(obj, "hide_render", False))
            obj[PROP_PARENT_HIDDEN] = True
        try:
            if not bool(obj.hide_get()):
                obj.hide_set(True)
                changed = True
        except Exception:  # noqa: BLE001
            pass
        if not bool(getattr(obj, "hide_render", False)):
            obj.hide_render = True
            changed = True
        return changed
    if not bool(obj.get(PROP_PARENT_HIDDEN, False)):
        return False
    previous_hide = bool(obj.get(PROP_PREVIOUS_HIDE, False))
    previous_render = bool(obj.get(PROP_PREVIOUS_HIDE_RENDER, False))
    try:
        if bool(obj.hide_get()) != previous_hide:
            obj.hide_set(previous_hide)
            changed = True
    except Exception:  # noqa: BLE001
        pass
    if bool(getattr(obj, "hide_render", False)) != previous_render:
        obj.hide_render = previous_render
        changed = True
    for prop in (
        PROP_PARENT_HIDDEN,
        PROP_PREVIOUS_HIDE,
        PROP_PREVIOUS_HIDE_RENDER,
    ):
        try:
            del obj[prop]
        except Exception:  # noqa: BLE001
            pass
    return changed


def _sync_coma_child_objects(work, coma_key: str, visible: bool) -> bool:
    changed = False
    for obj in bpy.data.objects:
        if _object_semantic_parent_key(obj, work) != coma_key:
            continue
        changed = _set_object_parent_visible(obj, visible) or changed
    return changed


def _parent_coma_visible(work, parent_key: str) -> bool | None:
    for page in getattr(work, "pages", []) or []:
        for coma in getattr(page, "comas", []) or []:
            if coma_stack_key(page, coma) == parent_key:
                return bool(getattr(coma, "visible", True))
    return None


def sync_object_from_parent(scene, obj) -> bool:
    """生成・再配置された実体へ、現在の親コマ表示状態を適用する。"""
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if work is None or obj is None:
        return False
    parent_key = _object_semantic_parent_key(obj, work)
    coma_visible = _parent_coma_visible(work, parent_key)
    # 非表示コマから外へ移された実体は、退避していた固有状態へ戻す。
    return _set_object_parent_visible(
        obj,
        True if coma_visible is None else coma_visible,
    )


def sync_coma_collection(scene, page, coma, collection=None) -> bool:
    """コマCollectionへ ``coma.visible`` を反映する。

    Collection単位で隠すため、内包レイヤー自身の表示設定は保持される。コマを
    再表示した時は、各レイヤーが元から持っていた表示／非表示へ正しく戻る。
    """
    if scene is None or page is None or coma is None:
        return False
    key = coma_stack_key(page, coma)
    if not key:
        return False
    collection = collection or object_naming.find_collection_by_bmanga_id(key, kind="coma")
    visible = bool(getattr(coma, "visible", True))
    changed = _set_collection_visible(collection, visible)
    work = getattr(scene, "bmanga_work", None)
    if work is not None:
        changed = _sync_coma_child_objects(work, key, visible) or changed
    return changed


def sync_entry_collection(coma, context=None) -> bool:
    """PropertyGroup更新コールバックから対象コマCollectionを同期する。"""
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if work is None:
        return False
    for page in getattr(work, "pages", []) or []:
        for entry in getattr(page, "comas", []) or []:
            if _same_rna_value(entry, coma):
                return sync_coma_collection(scene, page, entry)
    return False


def sync_collection_from_work(
    scene,
    page_id: str,
    coma_id: str,
    *,
    collection=None,
) -> bool:
    """Collection生成・復旧時に、保存済みコマ表示状態を再適用する。"""
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if work is None:
        return False
    page_id = str(page_id or "")
    coma_id = str(coma_id or "")
    for page in getattr(work, "pages", []) or []:
        if page_stack_key(page) != page_id:
            continue
        for coma in getattr(page, "comas", []) or []:
            stem = str(
                getattr(coma, "coma_id", "")
                or getattr(coma, "id", "")
                or ""
            )
            if stem == coma_id:
                changed = sync_coma_collection(scene, page, coma, collection)
                schedule_sync_all()
                return changed
        return False
    return False


def sync_all_coma_collections(scene=None) -> bool:
    """全コマの親表示を同期する。読込後に遅れて生成された実体も対象にする。"""
    scene = scene or getattr(bpy.context, "scene", None)
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if work is None:
        return False
    changed = False
    for page in getattr(work, "pages", []) or []:
        for coma in getattr(page, "comas", []) or []:
            changed = sync_coma_collection(scene, page, coma) or changed
    return changed


def schedule_sync_all() -> None:
    """Collection構築完了後にもう一度同期し、共通text Collectionも覆う。"""
    global _SYNC_SCHEDULED
    if _SYNC_SCHEDULED:
        return
    _SYNC_SCHEDULED = True

    def _run():
        global _SYNC_SCHEDULED
        _SYNC_SCHEDULED = False
        sync_all_coma_collections()
        return None

    try:
        from . import lifecycle_scheduler

        lifecycle_scheduler.schedule(
            "coma.visibility.sync",
            _run,
            first_interval=0.05,
            on_cancel=_cancel_scheduled_sync,
        )
    except Exception:  # noqa: BLE001
        _SYNC_SCHEDULED = False


def _cancel_scheduled_sync() -> None:
    global _SYNC_SCHEDULED
    _SYNC_SCHEDULED = False
