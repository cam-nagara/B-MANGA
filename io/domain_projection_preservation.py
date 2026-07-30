"""context外ページのDomain要素を削除せず投影へ合成する。"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from ..bmanga_core.domain_model import DomainLink, PageDocument


PayloadReader = Callable[[PageDocument, str], list[dict[str, Any]]]


def stored_page_document(work, page_uid: str) -> PageDocument | None:
    work_dir = str(getattr(work, "work_dir", "") or "")
    if not work_dir:
        return None
    from . import domain_runtime

    repository = domain_runtime.repository_for(work_dir)
    if not repository.page_path(page_uid).is_file():
        return None
    document = repository.load_page(page_uid)
    try:
        domain_runtime.hydrate_page(work_dir, document)
    except RuntimeError:
        project = repository.load_project()
        domain_runtime.install_store(work_dir, project, (document,))
    return document


def _payload_uid(value: Mapping[str, Any]) -> str:
    return str(value.get("nodeUid", "") or "")


def _payload_display_id(value: Mapping[str, Any]) -> str:
    return str(value.get("id") or value.get("comaId") or "")


def merge_payload_values(
    current: Iterable[Mapping[str, Any]],
    preserved: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    preserved_values = [copy.deepcopy(dict(value)) for value in preserved]
    by_uid = {
        uid: index
        for index, value in enumerate(preserved_values)
        if (uid := _payload_uid(value))
    }
    by_display = {
        display_id: index
        for index, value in enumerate(preserved_values)
        if (display_id := _payload_display_id(value))
    }
    result: list[dict[str, Any]] = []
    used: set[int] = set()
    for value in current:
        payload = copy.deepcopy(dict(value))
        index = by_uid.get(_payload_uid(payload))
        if index is None:
            index = by_display.get(_payload_display_id(payload))
        merged = (
            copy.deepcopy(preserved_values[index])
            if index is not None
            else {}
        )
        if not _payload_uid(payload):
            payload.pop("nodeUid", None)
        merged.update(payload)
        result.append(merged)
        if index is not None:
            used.add(index)
    result.extend(
        value
        for index, value in enumerate(preserved_values)
        if index not in used
    )
    return result


def preserve_work_payloads(
    work_payload: dict[str, Any],
    document: PageDocument,
    payloads_for_kind: PayloadReader,
) -> None:
    by_field = {
        "layer_folders": "folder",
        "raster_layers": "raster",
        "image_layers": "image",
        "fill_layers": "fill",
        "image_path_layers": "image_path",
    }
    for field, kind in by_field.items():
        work_payload[field] = merge_payload_values(
            work_payload.get(field, ()),
            payloads_for_kind(document, kind),
        )


def merge_links(
    preserved: Mapping[str, DomainLink],
    current: Mapping[str, DomainLink],
) -> dict[str, DomainLink]:
    """UI管理外linkを保ち、UI管理linkは現在mapの完全差分にする。"""
    result = {
        uid: copy.deepcopy(link)
        for uid, link in preserved.items()
        if link.kind != "linked-duplicate"
    }
    for uid, link in current.items():
        result[uid] = copy.deepcopy(link)
    return result


def native_layer_payloads(
    context,
    page_id: str,
    node_uid_prop: str,
) -> tuple[tuple[str, list[dict[str, Any]]], ...]:
    if context is None:
        return ()
    try:
        from ..utils import layer_object_model

        values: dict[str, list[dict[str, Any]]] = {"gp": [], "effect": []}
        for kind in values:
            for obj in layer_object_model.iter_layer_objects(kind):
                parent_key = layer_object_model.parent_key(obj)
                if parent_key.split(":", 1)[0] != page_id:
                    continue
                values[kind].append(
                    {
                        "id": layer_object_model.stable_id(obj),
                        "title": layer_object_model.display_title(obj),
                        "parentKey": parent_key,
                        "folderKey": layer_object_model.folder_id(obj),
                        "visible": layer_object_model.user_visible(obj),
                        "locked": layer_object_model.user_locked(obj),
                        "nativeUid": layer_object_model.stable_id(obj),
                        "nodeUid": str(obj.get(node_uid_prop, "") or ""),
                    }
                )
        return tuple(values.items())
    except Exception as exc:
        raise RuntimeError("Native layer collection failed") from exc


__all__ = (
    "merge_links",
    "merge_payload_values",
    "native_layer_payloads",
    "preserve_work_payloads",
    "stored_page_document",
)
