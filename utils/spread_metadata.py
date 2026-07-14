"""見開き結合・解除で使う、Blender 非依存のメタデータ変換。"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Mapping


class SpreadMetadataError(ValueError):
    pass


_GLOBAL_COLLECTIONS = {
    "raster_layers": "parent_key",
    "image_layers": "parentKey",
    "fill_layers": "parentKey",
    "image_path_layers": "parentKey",
    "layer_folders": "parentKey",
}


def merge_pages(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
    *,
    first_page_id: str,
    second_page_id: str,
    spread_id: str,
    right_offset_mm: float,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """2ページの詳細を可逆なIDマップ付きで統合する。"""

    first_data = deepcopy(dict(first))
    second_data = deepcopy(dict(second))
    coma_maps = _allocate_maps(
        first_data.get("comas", []), second_data.get("comas", []), "comaId", "c", 2, 99
    )
    balloon_maps = _allocate_maps(
        first_data.get("balloons", []), second_data.get("balloons", []), "id", "balloon_", 4
    )
    text_maps = _allocate_maps(
        first_data.get("texts", []), second_data.get("texts", []), "id", "text_", 4
    )
    id_maps = {
        first_page_id: {
            "coma": coma_maps[0], "balloon": balloon_maps[0], "text": text_maps[0]
        },
        second_page_id: {
            "coma": coma_maps[1], "balloon": balloon_maps[1], "text": text_maps[1]
        },
    }
    sources = {"coma": {}, "balloon": {}, "text": {}}
    merged = deepcopy(first_data)
    merged["id"] = spread_id
    merged["title"] = ""
    merged["spread"] = True
    for key in ("comas", "balloons", "texts"):
        merged[key] = []
    for source_id, source_data, dx in (
        (first_page_id, first_data, float(right_offset_mm)),
        (second_page_id, second_data, 0.0),
    ):
        maps = id_maps[source_id]
        flat = _flatten_maps(maps)
        for raw in source_data.get("comas", []):
            item = deepcopy(raw)
            old = str(item.get("comaId", "") or "")
            new = maps["coma"][old]
            item["comaId"] = new
            item["id"] = new
            item["layerRefs"] = [flat.get(str(value), str(value)) for value in item.get("layerRefs", [])]
            _shift_coma(item, dx)
            merged["comas"].append(item)
            sources["coma"][new] = source_id
        for raw in source_data.get("balloons", []):
            item = deepcopy(raw)
            old = str(item.get("id", "") or "")
            new = maps["balloon"][old]
            item["id"] = new
            text_id = str(item.get("textId", "") or "")
            if text_id:
                item["textId"] = maps["text"].get(text_id, text_id)
            _shift_x(item, "xMm", dx)
            merged["balloons"].append(item)
            sources["balloon"][new] = source_id
        for raw in source_data.get("texts", []):
            item = deepcopy(raw)
            old = str(item.get("id", "") or "")
            new = maps["text"][old]
            item["id"] = new
            balloon_id = str(item.get("parentBalloonId", "") or "")
            if balloon_id:
                item["parentBalloonId"] = maps["balloon"].get(balloon_id, balloon_id)
            _shift_x(item, "xMm", dx)
            merged["texts"].append(item)
            sources["text"][new] = source_id
    _reset_active_indices(merged)
    return merged, id_maps, {
        "entitySources": sources,
        "originalPageDetails": {
            first_page_id: first_data,
            second_page_id: second_data,
        },
    }


def split_page(
    spread: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    first_page_id: str,
    second_page_id: str,
    spread_id: str,
    right_offset_mm: float,
) -> dict[str, dict[str, Any]]:
    """結合時の所属印と逆IDマップを使ってページ詳細を復元する。"""

    sources = manifest.get("entitySources", {})
    id_maps = manifest.get("idMaps", {})
    object_maps = manifest.get("objectMaps", {})
    originals = manifest.get("originalPageDetails", {})
    result = {
        page_id: _empty_page(
            originals.get(page_id, spread) if isinstance(originals, Mapping) else spread,
            page_id,
        )
        for page_id in (first_page_id, second_page_id)
    }
    for kind, json_key, id_key in (
        ("coma", "comas", "comaId"),
        ("balloon", "balloons", "id"),
        ("text", "texts", "id"),
    ):
        source_map = sources.get(kind, {}) if isinstance(sources, Mapping) else {}
        for raw in spread.get(json_key, []):
            item = deepcopy(raw)
            current_id = str(item.get(id_key, "") or "")
            source_id = str(source_map.get(current_id, "") or "")
            if source_id not in result:
                raise SpreadMetadataError(
                    f"結合後に追加された {kind} の所属元を判定できないため解除できません: {current_id}"
                )
            reverse = _reverse_kind_maps(id_maps.get(source_id, {}))
            reverse_objects = _reverse_kind_maps(object_maps.get(source_id, {}))
            maps = _merge_kind_maps(reverse_objects, reverse)
            old_id = maps.get(kind, {}).get(current_id, current_id)
            item[id_key] = old_id
            if kind == "coma":
                item["id"] = old_id
                flat = _flatten_maps(maps)
                item["layerRefs"] = [flat.get(str(value), str(value)) for value in item.get("layerRefs", [])]
                if source_id == first_page_id:
                    _shift_coma(item, -float(right_offset_mm))
            elif kind == "balloon":
                text_id = str(item.get("textId", "") or "")
                if text_id:
                    item["textId"] = maps.get("text", {}).get(text_id, text_id)
                if source_id == first_page_id:
                    _shift_x(item, "xMm", -float(right_offset_mm))
            else:
                balloon_id = str(item.get("parentBalloonId", "") or "")
                if balloon_id:
                    item["parentBalloonId"] = maps.get("balloon", {}).get(balloon_id, balloon_id)
                if source_id == first_page_id:
                    _shift_x(item, "xMm", -float(right_offset_mm))
            result[source_id][json_key].append(item)
    for page_id, page in result.items():
        original = originals.get(page_id, {}) if isinstance(originals, Mapping) else {}
        _restore_active_indices(page, original)
    return result


def merge_work_data(
    work_data: Mapping[str, Any],
    *,
    first_page_id: str,
    second_page_id: str,
    spread_id: str,
    coma_maps: Mapping[str, Mapping[str, str]],
    right_offset_mm: float,
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """作品共通コレクションの親キーを見開きへ付け替える。"""

    data = deepcopy(dict(work_data))
    folder_sources = _resolve_folder_sources(data, first_page_id, second_page_id)
    source_records: dict[str, dict[str, str]] = {}
    for collection_name, parent_field in _GLOBAL_COLLECTIONS.items():
        records: dict[str, str] = {}
        seen: dict[str, str] = {}
        for entry in data.get(collection_name, []):
            entry_id = str(entry.get("id", "") or "")
            parent = str(entry.get(parent_field, "") or "")
            source_id = (
                _parent_source(parent, first_page_id, second_page_id)
                or folder_sources.get(parent, "")
                or folder_sources.get(str(entry.get("folderKey", "") or ""), "")
            )
            if collection_name == "layer_folders":
                source_id = folder_sources.get(entry_id, source_id)
            if not source_id:
                continue
            if not entry_id:
                raise SpreadMetadataError(f"{collection_name} に安定IDのない項目があります")
            if entry_id in seen:
                raise SpreadMetadataError(
                    f"{collection_name} の安定IDが重複・衝突しています: {entry_id}"
                )
            seen[entry_id] = source_id
            records[entry_id] = source_id
            entry[parent_field] = _retarget_parent(
                parent, source_id, spread_id, coma_maps.get(source_id, {})
            )
            if source_id == first_page_id:
                _shift_global_entry(collection_name, entry, float(right_offset_mm))
        source_records[collection_name] = records
    return data, source_records


def split_work_data(
    work_data: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    first_page_id: str,
    second_page_id: str,
    spread_id: str,
    right_offset_mm: float,
) -> dict[str, Any]:
    data = deepcopy(dict(work_data))
    records = manifest.get("globalSources", {})
    id_maps = manifest.get("idMaps", {})
    for collection_name, parent_field in _GLOBAL_COLLECTIONS.items():
        source_map = records.get(collection_name, {}) if isinstance(records, Mapping) else {}
        for entry in data.get(collection_name, []):
            parent = str(entry.get(parent_field, "") or "")
            entry_id = str(entry.get("id", "") or "")
            source_id = str(source_map.get(entry_id, "") or "")
            spread_parent = parent == spread_id or parent.startswith(f"{spread_id}:")
            if spread_parent and source_id not in {first_page_id, second_page_id}:
                raise SpreadMetadataError(
                    f"結合後に追加された {collection_name} の所属元を判定できないため解除できません: {entry_id}"
                )
            if source_id not in {first_page_id, second_page_id}:
                continue
            reverse_comas = {
                str(new): str(old)
                for old, new in id_maps.get(source_id, {}).get("coma", {}).items()
            }
            if spread_parent:
                entry[parent_field] = _retarget_parent(parent, spread_id, source_id, reverse_comas)
            if source_id == first_page_id:
                _shift_global_entry(collection_name, entry, -float(right_offset_mm))
    return data


def reverse_maps_for_source(manifest: Mapping[str, Any], source_id: str) -> dict[str, dict[str, str]]:
    """結合時の全IDマップを、解除ワーカー用の逆引きへまとめる。"""

    persisted = _reverse_kind_maps(manifest.get("idMaps", {}).get(source_id, {}))
    objects = _reverse_kind_maps(manifest.get("objectMaps", {}).get(source_id, {}))
    return _merge_kind_maps(objects, persisted)


def coma_storage_map_for_source(manifest: Mapping[str, Any], source_id: str) -> dict[str, str]:
    """Map merged coma-directory IDs to the original IDs restored on split."""

    source_maps = manifest.get("idMaps", {}).get(source_id, {})
    mapping = source_maps.get("coma", {}) if isinstance(source_maps, Mapping) else {}
    if not isinstance(mapping, Mapping):
        return {}
    return {str(stored): str(original) for original, stored in mapping.items()}


def reverse_link_groups_for_source(
    manifest: Mapping[str, Any], source_id: str
) -> dict[str, str]:
    """結合時に分離したリンクグループ名を元ページ用へ戻す。"""

    mapping = manifest.get("linkGroupMaps", {}).get(source_id, {})
    if not isinstance(mapping, Mapping):
        return {}
    return {str(new): str(old) for old, new in mapping.items()}


def source_memberships(manifest: Mapping[str, Any]) -> dict[str, dict[str, list[str]]]:
    """所属印を再生成された実体へ安全に復元するためのID集合。"""

    page_ids = [str(value) for value in manifest.get("sourcePages", [])]
    result: dict[str, dict[str, set[str]]] = {page_id: {} for page_id in page_ids}

    def add(source_id: str, kind: str, stable_id: str) -> None:
        if source_id in result and stable_id:
            result[source_id].setdefault(kind, set()).add(stable_id)

    for kind, values in manifest.get("entitySources", {}).items():
        if isinstance(values, Mapping):
            for stable_id, source_id in values.items():
                add(str(source_id), str(kind), str(stable_id))
    for source_id, kinds in manifest.get("objectMaps", {}).items():
        if not isinstance(kinds, Mapping):
            continue
        for kind, mapping in kinds.items():
            if isinstance(mapping, Mapping):
                for stable_id in mapping.values():
                    add(str(source_id), str(kind), str(stable_id))
    global_kinds = {
        "raster_layers": "raster",
        "image_layers": "image",
        "fill_layers": "fill",
        "image_path_layers": "image_path",
        "layer_folders": "folder",
    }
    for collection_name, values in manifest.get("globalSources", {}).items():
        if not isinstance(values, Mapping):
            continue
        kind = global_kinds.get(str(collection_name), str(collection_name))
        for stable_id, source_id in values.items():
            add(str(source_id), kind, str(stable_id))
    return {
        source_id: {
            kind: sorted(stable_ids)
            for kind, stable_ids in kinds.items()
        }
        for source_id, kinds in result.items()
    }


def _allocate_maps(
    first: list[Mapping[str, Any]],
    second: list[Mapping[str, Any]],
    key: str,
    prefix: str,
    digits: int,
    maximum: int | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    used: set[str] = set()
    maps: list[dict[str, str]] = []
    for source_index, entries in enumerate((first, second)):
        mapping: dict[str, str] = {}
        local: set[str] = set()
        for entry in entries:
            old = str(entry.get(key, "") or "")
            if not old or old in local:
                raise SpreadMetadataError(f"空または重複した安定IDがあるため安全に結合できません: {old!r}")
            if maximum is not None and not (
                len(old) == 3
                and old.startswith(prefix)
                and old[1:].isdigit()
                and 1 <= int(old[1:]) <= maximum
            ):
                raise SpreadMetadataError(f"コマIDの形式が不正なため安全に結合できません: {old}")
            local.add(old)
            new = old
            if source_index and new in used:
                new = _next_id(used, prefix, digits, maximum)
            if new in used:
                raise SpreadMetadataError(f"安定IDが重複しています: {new}")
            used.add(new)
            mapping[old] = new
        maps.append(mapping)
    return maps[0], maps[1]


def _next_id(used: set[str], prefix: str, digits: int, maximum: int | None) -> str:
    limit = maximum if maximum is not None else 999999
    for number in range(1, limit + 1):
        candidate = f"{prefix}{number:0{digits}d}"
        if candidate not in used:
            return candidate
    raise SpreadMetadataError("安全に採番できるIDが残っていません")


def _shift_coma(item: dict[str, Any], dx: float) -> None:
    shape = item.get("shape")
    if not isinstance(shape, dict):
        return
    rect = shape.get("rect")
    if isinstance(rect, dict):
        _shift_x(rect, "x", dx)
    vertices = shape.get("vertices")
    if isinstance(vertices, list):
        for vertex in vertices:
            if isinstance(vertex, list) and vertex:
                vertex[0] = float(vertex[0]) + dx
    polygons = shape.get("mergedBorderPolygons")
    if isinstance(polygons, list):
        for polygon in polygons:
            for vertex in polygon if isinstance(polygon, list) else []:
                if isinstance(vertex, list) and vertex:
                    vertex[0] = float(vertex[0]) + dx


def _shift_global_entry(collection_name: str, entry: dict[str, Any], dx: float) -> None:
    if collection_name == "image_layers":
        _shift_x(entry, "xMm", dx)
    elif collection_name == "fill_layers":
        for key in ("regionXMm", "gradientStartXMm", "gradientEndXMm"):
            _shift_x(entry, key, dx)
        _shift_json_points(entry, "lassoPointsJson", dx)
    elif collection_name == "image_path_layers":
        _shift_json_points(entry, "pathPointsJson", dx)


def _shift_json_points(entry: dict[str, Any], key: str, dx: float) -> None:
    raw = entry.get(key, "")
    if not raw:
        return
    try:
        points = json.loads(str(raw))
        for point in points if isinstance(points, list) else []:
            if isinstance(point, list) and point:
                point[0] = float(point[0]) + dx
        entry[key] = json.dumps(points, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise SpreadMetadataError(f"{key} の座標を安全に変換できません") from None


def _shift_x(item: dict[str, Any], key: str, dx: float) -> None:
    if key in item:
        item[key] = float(item.get(key, 0.0) or 0.0) + dx


def _parent_source(parent: str, first: str, second: str) -> str:
    for page_id in (first, second):
        if parent == page_id or parent.startswith(f"{page_id}:"):
            return page_id
    return ""


def _resolve_folder_sources(data: Mapping[str, Any], first: str, second: str) -> dict[str, str]:
    folders = [item for item in data.get("layer_folders", []) if isinstance(item, Mapping)]
    result: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for folder in folders:
            folder_id = str(folder.get("id", "") or "")
            if not folder_id or folder_id in result:
                continue
            parent = str(folder.get("parentKey", "") or "")
            source = _parent_source(parent, first, second) or result.get(parent, "")
            if source:
                result[folder_id] = source
                changed = True
    return result


def _retarget_parent(parent: str, old_page: str, new_page: str, coma_map: Mapping[str, str]) -> str:
    if parent == old_page:
        return new_page
    prefix = f"{old_page}:"
    if parent.startswith(prefix):
        tail = parent[len(prefix):]
        return f"{new_page}:{coma_map.get(tail, tail)}"
    return parent


def _flatten_maps(maps: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for mapping in maps.values():
        result.update({str(old): str(new) for old, new in mapping.items()})
    return result


def _reverse_kind_maps(maps: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    return {
        str(kind): {str(new): str(old) for old, new in mapping.items()}
        for kind, mapping in maps.items()
        if isinstance(mapping, Mapping)
    }


def _merge_kind_maps(*values: Mapping[str, Mapping[str, str]]) -> dict[str, dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for value in values:
        for kind, mapping in value.items():
            merged.setdefault(str(kind), {}).update(mapping)
    return merged


def _empty_page(source: Mapping[str, Any], page_id: str) -> dict[str, Any]:
    result = deepcopy(dict(source))
    result.update({"id": page_id, "spread": False, "comas": [], "balloons": [], "texts": []})
    return result


def _reset_active_indices(page: dict[str, Any]) -> None:
    page["activeComaIndex"] = 0 if page.get("comas") else -1
    page["activeBalloonIndex"] = 0 if page.get("balloons") else -1
    page["activeTextIndex"] = 0 if page.get("texts") else -1


def _restore_active_indices(page: dict[str, Any], original: Mapping[str, Any]) -> None:
    for index_key, collection_key in (
        ("activeComaIndex", "comas"),
        ("activeBalloonIndex", "balloons"),
        ("activeTextIndex", "texts"),
    ):
        length = len(page.get(collection_key, []))
        try:
            value = int(original.get(index_key, -1))
        except (TypeError, ValueError):
            value = -1
        page[index_key] = value if -1 <= value < length else (0 if length else -1)
