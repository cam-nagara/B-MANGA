"""Object/Outlinerの直接編集を一つの差分単位へ集約する。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import bpy

from . import log


_logger = log.get_logger(__name__)
_OBJECT_DELETE_KINDS = frozenset(
    {
        "gp",
        "effect",
        "image",
        "image_path",
        "raster",
        "fill",
        "balloon",
        "text",
    }
)
_COLLECTION_DELETE_KINDS = frozenset({"page", "coma", "folder"})


@dataclass(frozen=True, slots=True)
class ManagedIdentity:
    owner_type: str
    kind: str
    bmanga_id: str


def _identity(owner, owner_type: str) -> ManagedIdentity | None:
    if not bool(owner.get("bmanga_managed", False)):
        return None
    kind = str(owner.get("bmanga_kind", "") or "")
    bmanga_id = str(owner.get("bmanga_id", "") or "")
    allowed = (
        _OBJECT_DELETE_KINDS
        if owner_type == "object"
        else _COLLECTION_DELETE_KINDS
    )
    if kind not in allowed or not bmanga_id:
        return None
    return ManagedIdentity(
        owner_type,
        kind,
        bmanga_id,
    )


def _scene_collections(scene):
    seen = set()
    stack = list(getattr(getattr(scene, "collection", None), "children", ()))
    while stack:
        collection = stack.pop()
        pointer = int(collection.as_pointer())
        if pointer in seen:
            continue
        seen.add(pointer)
        yield collection
        stack.extend(tuple(collection.children))


def _managed_inventory(scene) -> Counter[ManagedIdentity]:
    result: Counter[ManagedIdentity] = Counter(
        identity
        for obj in tuple(getattr(scene, "objects", ()) or ())
        if (identity := _identity(obj, "object")) is not None
    )
    result.update(
        identity
        for collection in _scene_collections(scene)
        if (identity := _identity(collection, "collection")) is not None
    )
    return result


def _item_matches_identity(item, identity: ManagedIdentity) -> bool:
    from .layer_hierarchy import split_child_key

    item_kind = str(getattr(item, "kind", "") or "")
    item_key = str(getattr(item, "key", "") or "")
    expected_kind = (
        "layer_folder" if identity.kind == "folder" else identity.kind
    )
    if item_kind != expected_kind:
        return False
    if item_key == identity.bmanga_id:
        return True
    if identity.kind in {"balloon", "text"}:
        _page_key, child_key = split_child_key(item_key)
        return child_key == identity.bmanga_id
    return False


def _restore_deleted_page_collection(scene, page_id: str) -> bool:
    """現在ファイルのPage Collectionを正本Domainから安全に再投影する。"""

    from . import layer_object_sync, outliner_model

    work = getattr(scene, "bmanga_work", None)
    page = next(
        (
            entry
            for entry in getattr(work, "pages", ()) or ()
            if str(getattr(entry, "id", "") or "") == page_id
        ),
        None,
    )
    if page is None:
        return False
    restored = outliner_model.ensure_page_collection(
        scene,
        page_id,
        str(getattr(page, "title", "") or ""),
    )
    if restored is None:
        return False
    layer_object_sync.mirror_work_to_outliner(scene, work)
    _logger.warning(
        "managed page Collection restored; use B-MANGA page delete: %s",
        page_id,
    )
    return True


def _delete_missing_identity(scene, identity: ManagedIdentity) -> bool:
    """消失した管理実体を、レイヤー一覧の明示削除操作へ変換する。"""

    from . import layer_links, layer_stack

    context = bpy.context
    if getattr(context, "scene", None) is not scene:
        raise RuntimeError("Outliner deletion belongs to another Scene")
    if identity.kind == "page":
        # 現在開いているpage.blendのPage Collectionをその場でDomain削除すると、
        # 自分自身の保存先directoryまで消えて以後のcheckpointが不能になる。
        # ページ削除は確認付きB-MANGA Commandだけに限定し、標準Outlinerから
        # 消された派生Collectionは正本Domainから再投影する。
        return _restore_deleted_page_collection(
            scene,
            identity.bmanga_id,
        )
    if identity.kind in {"gp", "effect"}:
        # Native Objectそのものが正本の2種は既に消失済み。リンク参照と
        # UI投影だけを除き、次checkpointのPagePatchでDomain nodeを削除する。
        uid = layer_stack.target_uid(identity.kind, identity.bmanga_id)
        layer_links.unlink_uids(context, [uid])
        layer_stack.sync_layer_stack(
            context,
            preserve_active_index=True,
        )
        layer_stack.tag_view3d_redraw(context)
        return True
    # PropertyGroupを正本とする種別とCollection種別は、通常の削除Commandと
    # 同じ副作用（ラスター退避、子参照解除、Native transaction）を通す。
    refreshed = layer_stack.sync_layer_stack(
        context,
        preserve_active_index=True,
    )
    index = next(
        (
            item_index
            for item_index, item in enumerate(refreshed or ())
            if _item_matches_identity(item, identity)
        ),
        -1,
    )
    if index < 0:
        return False
    layer_links.unlink_uids(
        context,
        [layer_stack.stack_item_uid(refreshed[index])],
    )
    return bool(layer_stack.delete_stack_index(context, index))


@dataclass(slots=True)
class ChangeCollector:
    _object_names: set[str] = field(default_factory=set)
    _structural_dirty: bool = False
    _flushing: bool = False
    _inventory: Counter[ManagedIdentity] = field(default_factory=Counter)
    _scene_pointer: int = 0

    @property
    def pending_count(self) -> int:
        return len(self._object_names) + int(self._structural_dirty)

    def collect_object(self, obj) -> None:
        name = str(getattr(obj, "name", "") or "")
        if name:
            self._object_names.add(name)

    def collect_depsgraph(self, depsgraph) -> None:
        for update in getattr(depsgraph, "updates", ()) or ():
            updated = getattr(update, "id", None)
            if isinstance(updated, bpy.types.Collection):
                self._structural_dirty = True
                continue
            if not isinstance(updated, bpy.types.Object):
                continue
            real = bpy.data.objects.get(
                str(getattr(updated, "name", "") or "")
            )
            if real is None:
                self._structural_dirty = True
                continue
            self.collect_object(real)
            if bool(real.get("bmanga_managed", False)):
                self._structural_dirty = True

    def mark_structure(self) -> None:
        self._structural_dirty = True

    def clear(self) -> None:
        self._object_names.clear()
        self._structural_dirty = False
        self._inventory.clear()
        self._scene_pointer = 0

    def rebase(self, scene=None) -> None:
        """内部mirror/load/Undo後の現状を削除検出の新基準にする。"""

        target = scene or getattr(bpy.context, "scene", None)
        self._object_names.clear()
        self._structural_dirty = False
        self._inventory = (
            _managed_inventory(target) if target is not None else Counter()
        )
        self._scene_pointer = (
            int(target.as_pointer()) if target is not None else 0
        )

    def flush(self, scene, *, full: bool = False) -> int:
        """集約済み差分を一回のDomain投影単位として反映する。"""

        if scene is None or self._flushing:
            return 0
        names = tuple(self._object_names)
        structural = self._structural_dirty
        self._object_names.clear()
        self._structural_dirty = False
        self._flushing = True
        changed = 0
        try:
            from . import (
                history_runtime,
                layer_object_sync,
                object_state_sync,
                outliner_watch,
            )

            if (
                layer_object_sync.is_sync_in_progress()
                or history_runtime.is_restoring()
            ):
                return 0
            scene_pointer = int(scene.as_pointer())
            if self._scene_pointer != scene_pointer:
                self._inventory = _managed_inventory(scene)
                self._scene_pointer = scene_pointer
            current_inventory = _managed_inventory(scene)
            missing = {
                identity
                for identity, count in self._inventory.items()
                if count > 0 and current_inventory.get(identity, 0) == 0
            }
            for identity in sorted(
                missing,
                key=lambda item: (
                    item.owner_type != "collection",
                    item.kind not in {"page", "coma", "folder"},
                    item.kind,
                    item.bmanga_id,
                ),
            ):
                if _delete_missing_identity(scene, identity):
                    changed += 1
            scene_objects = {
                int(obj.as_pointer()): obj
                for obj in tuple(getattr(scene, "objects", ()) or ())
            }
            objects = (
                tuple(scene_objects.values())
                if full
                else tuple(
                    obj
                    for name in names
                    if (obj := bpy.data.objects.get(name)) is not None
                    and int(obj.as_pointer()) in scene_objects
                )
            )
            for obj in objects:
                if object_state_sync.sync_from_blender_object(scene, obj):
                    changed += 1
            if full or structural:
                changed += outliner_watch.writeback_collected_changes(
                    scene,
                    objects=objects,
                )
            if changed:
                from . import file_transition_runtime

                file_transition_runtime.mark_scene_dirty(
                    scene,
                    reason="outliner_change_collector",
                )
            self._inventory = _managed_inventory(scene)
            return changed
        except Exception:  # noqa: BLE001
            self._object_names.update(names)
            self._structural_dirty = self._structural_dirty or structural
            _logger.exception("outliner change collector flush failed")
            raise
        finally:
            self._flushing = False


COLLECTOR = ChangeCollector()


def collect_depsgraph(depsgraph) -> None:
    COLLECTOR.collect_depsgraph(depsgraph)


def flush(scene=None, *, full: bool = False) -> int:
    target = scene or getattr(bpy.context, "scene", None)
    return COLLECTOR.flush(target, full=full)


def clear() -> None:
    COLLECTOR.clear()


def rebase(scene=None) -> None:
    COLLECTOR.rebase(scene)


__all__ = (
    "COLLECTOR",
    "ChangeCollector",
    "ManagedIdentity",
    "clear",
    "collect_depsgraph",
    "flush",
    "rebase",
)
