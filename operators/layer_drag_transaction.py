"""レイヤー移動の「一時表示」と「確定データ更新」を分離する."""

from __future__ import annotations

from dataclasses import dataclass

import bpy

from ..utils import layer_object_sync, object_naming as on
from ..utils.geom import mm_to_m


_COMA_OWNER_PROPS = (
    "bmanga_coma_plane_owner_id",
    "bmanga_coma_mask_owner_id",
    "bmanga_coma_border_owner_id",
    "bmanga_coma_white_margin_owner_id",
)


@dataclass
class _ObjectState:
    obj: bpy.types.Object
    matrix_world: object


def _descendants(obj) -> set:
    result = set()
    pending = list(getattr(obj, "children", ()) or ())
    while pending:
        child = pending.pop()
        if child in result:
            continue
        result.add(child)
        pending.extend(list(getattr(child, "children", ()) or ()))
    return result


def _snapshot_entry_ids(owner, resolved: dict) -> set[str]:
    page = resolved.get("page")
    page_id = str(getattr(page, "id", "") or "")
    ids: set[str] = set()
    for kind, target, _data in getattr(owner, "_snapshots", ()) or ():
        if target is None or kind not in {
            "balloon",
            "text",
            "attached_text",
            "image",
            "raster",
            "fill",
        }:
            continue
        item_id = str(getattr(target, "id", "") or "")
        if not item_id:
            continue
        ids.add(item_id)
        if page_id:
            ids.add(f"{page_id}:{item_id}")
    target = resolved.get("target")
    item_id = str(getattr(target, "id", "") or getattr(target, "name", "") or "")
    if item_id:
        ids.add(item_id)
        if page_id:
            ids.add(f"{page_id}:{item_id}")
    return ids


def _collection_objects(kind: str, resolved: dict) -> set:
    page = resolved.get("page")
    target = resolved.get("target")
    page_id = str(getattr(page, "id", "") or "")
    if kind == "page":
        page_id = str(getattr(target, "id", "") or page_id)
        collection_id = page_id
        collection_kind = "page"
    elif kind == "coma" and page_id and target is not None:
        coma_id = str(
            getattr(target, "coma_id", "") or getattr(target, "id", "") or ""
        )
        collection_id = f"{page_id}:{coma_id}"
        collection_kind = "coma"
    else:
        return set()
    collection = on.find_collection_by_bmanga_id(collection_id, kind=collection_kind)
    return set(getattr(collection, "all_objects", ()) or ()) if collection else set()


def _candidate_objects(owner, kind: str, resolved: dict) -> list:
    candidates = _collection_objects(kind, resolved)
    ids = _snapshot_entry_ids(owner, resolved)
    target_obj = resolved.get("object")
    if target_obj is not None:
        candidates.add(target_obj)
        candidates.update(_descendants(target_obj))

    page = resolved.get("page")
    target = resolved.get("target")
    page_id = str(getattr(page, "id", "") or "")
    coma_id = (
        str(getattr(target, "coma_id", "") or getattr(target, "id", "") or "")
        if kind == "coma"
        else ""
    )
    coma_key = f"{page_id}:{coma_id}" if page_id and coma_id else ""
    for obj in bpy.data.objects:
        stable_id = str(obj.get(on.PROP_ID, "") or "")
        parent_key = str(obj.get(on.PROP_PARENT_KEY, "") or "")
        owner_match = coma_key and any(
            str(obj.get(prop, "") or "") == coma_key for prop in _COMA_OWNER_PROPS
        )
        if stable_id in ids or owner_match:
            candidates.add(obj)
            candidates.update(_descendants(obj))
        elif coma_key and parent_key == coma_key:
            candidates.add(obj)
            candidates.update(_descendants(obj))

    # 親子を両方動かすと子へ二重に変換が掛かるので、最上位Objectだけを保持する。
    roots = [
        obj
        for obj in candidates
        if getattr(obj, "parent", None) not in candidates
    ]
    return sorted(roots, key=lambda obj: str(getattr(obj, "name", "") or ""))


class DragTransaction:
    """ドラッグ中はObjectの一時行列だけ、確定時にデータを一度だけ更新する."""

    def __init__(self, context, owner, kind: str, resolved: dict) -> None:
        self._owner = owner
        self._kind = str(kind or "")
        self._resolved = resolved
        self._total = (0.0, 0.0)
        self._closed = False
        owner._capture_snapshot(context, self._kind, resolved)
        self._objects = [
            _ObjectState(obj=obj, matrix_world=obj.matrix_world.copy())
            for obj in _candidate_objects(owner, self._kind, resolved)
        ]
        layer_object_sync.begin_sync_suppression()

    @property
    def total(self) -> tuple[float, float]:
        return self._total

    def update_overlay(self, context, total_dx_mm: float, total_dy_mm: float) -> bool:
        if self._closed:
            return False
        if not self._owner._can_apply_total(
            context,
            float(total_dx_mm),
            float(total_dy_mm),
        ):
            return False
        self._total = (float(total_dx_mm), float(total_dy_mm))
        dx_m = mm_to_m(self._total[0])
        dy_m = mm_to_m(self._total[1])
        for state in self._objects:
            if state.obj is None:
                continue
            matrix = state.matrix_world.copy()
            matrix.translation.x += dx_m
            matrix.translation.y += dy_m
            state.obj.matrix_world = matrix
        return True

    def _restore_objects(self) -> None:
        for state in self._objects:
            if state.obj is not None:
                state.obj.matrix_world = state.matrix_world

    def commit(self, context) -> bool:
        if self._closed:
            return False
        self._restore_objects()
        dx_mm, dy_mm = self._total
        try:
            changed = bool(
                (dx_mm or dy_mm)
                and self._owner._apply_delta(context, dx_mm, dy_mm)
            )
        finally:
            self._closed = True
            layer_object_sync.end_sync_suppression()
        return changed

    def cancel(self) -> None:
        if self._closed:
            return
        try:
            self._restore_objects()
        finally:
            self._closed = True
            layer_object_sync.end_sync_suppression()
