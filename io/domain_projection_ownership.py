"""作品直下とページ配下の共有layer payload所有権を分類する。"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


WORK_LAYER_FIELDS = (
    "raster_layers",
    "image_layers",
    "fill_layers",
    "image_path_layers",
    "layer_folders",
)


def page_owned_payloads(
    work_payload: Mapping[str, Any],
    page_payload: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """指定ページ配下のfolderとlayer payloadだけを返す。"""

    page_id = str(page_payload.get("id", "") or "")
    owner_keys = {page_id, *_coma_owner_keys(page_id, page_payload)}
    folders = _mapping_values(work_payload, "layer_folders")
    selected_folders = _collect_owned_folders(folders, owner_keys, page_id)
    owner_keys.update(
        str(folder.get("id", "") or "")
        for folder in selected_folders
        if str(folder.get("id", "") or "")
    )
    result = {"layer_folders": selected_folders}
    for key in WORK_LAYER_FIELDS:
        if key == "layer_folders":
            continue
        result[key] = [
            value
            for value in _mapping_values(work_payload, key)
            if _belongs_to_page(value, owner_keys, page_id)
        ]
    return result


def project_owned_payloads(
    work_payload: Mapping[str, Any],
    page_ids: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """どのページにも属さない作品直下payloadだけを返す。"""

    values = {
        field: _mapping_values(work_payload, field)
        for field in WORK_LAYER_FIELDS
    }
    page_folders = _page_owned_folder_ids(values["layer_folders"], page_ids)
    result = {
        "layer_folders": [
            value
            for value in values["layer_folders"]
            if str(value.get("id", "") or "") not in page_folders
        ]
    }
    for field in WORK_LAYER_FIELDS:
        if field == "layer_folders":
            continue
        result[field] = [
            value
            for value in values[field]
            if not _is_page_owned(value, page_ids, page_folders)
        ]
    return result


def _mapping_values(
    payload: Mapping[str, Any],
    field: str,
) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(dict(value))
        for value in payload.get(field, ())
        if isinstance(value, Mapping)
    ]


def _coma_owner_keys(
    page_id: str,
    page_payload: Mapping[str, Any],
) -> set[str]:
    return {
        f"{page_id}:{str(coma.get('comaId') or coma.get('id') or '')}"
        for coma in page_payload.get("comas", ())
        if isinstance(coma, Mapping)
    }


def _collect_owned_folders(
    folders: list[dict[str, Any]],
    owner_keys: set[str],
    page_id: str,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    remaining = list(folders)
    while remaining:
        accepted = [
            folder
            for folder in remaining
            if _belongs_to_page(folder, owner_keys, page_id)
        ]
        if not accepted:
            break
        selected.extend(accepted)
        owner_keys.update(
            str(folder.get("id", "") or "")
            for folder in accepted
            if str(folder.get("id", "") or "")
        )
        accepted_ids = {id(folder) for folder in accepted}
        remaining = [
            folder for folder in remaining if id(folder) not in accepted_ids
        ]
    return selected


def _belongs_to_page(
    value: Mapping[str, Any],
    owner_keys: set[str],
    page_id: str,
) -> bool:
    parent = payload_parent_key(value)
    return (
        parent in owner_keys
        or parent.split(":", 1)[0] == page_id
        or str(value.get("folderKey", "") or "") in owner_keys
    )


def _page_owned_folder_ids(
    folders: list[dict[str, Any]],
    page_ids: set[str],
) -> set[str]:
    owned: set[str] = set()
    pending = list(folders)
    while pending:
        accepted = [
            value
            for value in pending
            if _is_page_owned(value, page_ids, owned)
        ]
        if not accepted:
            break
        owned.update(
            str(value.get("id", "") or "")
            for value in accepted
            if str(value.get("id", "") or "")
        )
        accepted_ids = {id(value) for value in accepted}
        pending = [value for value in pending if id(value) not in accepted_ids]
    return owned


def _is_page_owned(
    value: Mapping[str, Any],
    page_ids: set[str],
    page_folders: set[str],
) -> bool:
    parent = payload_parent_key(value)
    return (
        parent in page_folders
        or parent in page_ids
        or parent.split(":", 1)[0] in page_ids
    )


def payload_parent_key(value: Mapping[str, Any]) -> str:
    return str(
        value.get("folderKey")
        or value.get("parentKey")
        or value.get("parent_key")
        or ""
    )


__all__ = (
    "WORK_LAYER_FIELDS",
    "page_owned_payloads",
    "payload_parent_key",
    "project_owned_payloads",
)
