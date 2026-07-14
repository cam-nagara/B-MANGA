"""B-MANGA layer bundle assets.

This module keeps B-MANGA layer assets as ordinary Blender collection assets
with a JSON payload.  Dragging such a collection asset back into the viewport
creates a collection instance; ``asset_drop_runtime`` converts that instance
into normal B-MANGA layers.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import bpy
from mathutils import Matrix

from ..core.work import get_active_page, get_work
from . import (
    asset_bundle_extended,
    asset_preview,
    layer_links,
    layer_stack as layer_stack_utils,
    log,
    object_naming as on,
)
from . import page_grid
from .geom import m_to_mm, mm_to_m
from .layer_hierarchy import coma_containing_point, coma_stack_key

_logger = log.get_logger(__name__)

ASSET_PAYLOAD_PROP = "bmanga_asset_payload"
ASSET_KIND_PROP = "bmanga_asset_kind"
ASSET_KIND_LAYER_BUNDLE = "layer_bundle"
ASSET_PROTOTYPE_PROP = "bmanga_asset_preview"
ASSET_INSTANCE_DONE_PROP = "bmanga_asset_instance_imported"
ASSET_FILE_NAME = "B-MANGA Assets.blend"
INVALID_FILENAME_CHARS = '<>:"/\\|?*'

SUPPORTED_LAYER_KINDS = {"coma", "balloon", "text", "effect", "raster", "gp"}


@dataclass(frozen=True)
class AssetBrowserTarget:
    reference: str = "LOCAL"
    catalog_id: str = ""
    library_path: str = ""
    writable: bool = True

    @property
    def is_local(self) -> bool:
        return self.reference in {"", "LOCAL"}


def event_over_asset_browser(context, event) -> bool:
    return _asset_browser_area(context, event, require_event_hit=True) is not None


def current_asset_browser_target(context, event=None) -> AssetBrowserTarget:
    area = None
    if event is not None:
        area = _asset_browser_area(context, event, require_event_hit=True)
    if area is None:
        area = _asset_browser_area(context)
    if area is None:
        return AssetBrowserTarget()
    space = getattr(area.spaces, "active", None)
    params = getattr(space, "params", None)
    reference = str(getattr(params, "asset_library_reference", "LOCAL") or "LOCAL")
    catalog_id = str(getattr(params, "catalog_id", "") or "")
    if reference in {"", "LOCAL"}:
        return AssetBrowserTarget("LOCAL", catalog_id, "", True)
    if reference in {"ALL", "ESSENTIALS"}:
        return AssetBrowserTarget(reference, catalog_id, "", False)
    path = _asset_library_path(context, reference)
    return AssetBrowserTarget(reference, catalog_id, path, bool(path))


def register_selected_layers_as_asset(
    context,
    *,
    index: int = -1,
    name: str = "",
    event=None,
) -> bpy.types.Collection | None:
    target = current_asset_browser_target(context, event)
    if not target.writable:
        raise RuntimeError("登録先のアセットライブラリを選択してください")
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None:
        raise RuntimeError("登録できるレイヤーがありません")
    uids = _selected_or_index_uids(context, stack, index)
    items = _items_for_uids(context, stack, uids)
    if not items:
        raise RuntimeError("登録できるレイヤーを選択してください")
    payload = build_payload(context, items, name=name)
    collection = create_collection_asset(context, payload, target=target)
    return collection


def register_selected_objects_as_asset(context, *, name: str = "", event=None) -> bpy.types.Collection | None:
    target = current_asset_browser_target(context, event)
    if not target.writable:
        raise RuntimeError("登録先のアセットライブラリを選択してください")
    objects = [obj for obj in getattr(context, "selected_objects", []) or [] if obj is not None]
    if not objects and getattr(context, "active_object", None) is not None:
        objects = [context.active_object]
    if not objects:
        raise RuntimeError("登録するオブジェクトを選択してください")
    coll = _new_asset_collection(_unique_asset_name(name or _common_name(objects)))
    coll[ASSET_KIND_PROP] = "object_bundle"
    for obj in objects:
        clone = _clone_preview_object(obj, origin_mm=_object_group_origin_mm(objects))
        if clone is not None:
            coll.objects.link(clone)
    _mark_collection_asset(coll, target=target, description="B-MANGA オブジェクトアセット")
    asset_preview.set_collection_asset_preview(coll)
    _write_external_library_if_needed(coll, target, context=context)
    if target.is_local:
        _refresh_open_asset_browser(context)
    return coll


def build_payload(context, items, *, name: str = "") -> dict:
    entries: list[dict] = []
    source_uids: list[str] = []
    for item in items:
        entry = _serialize_stack_item(context, item)
        if entry is None:
            continue
        source_uid = layer_stack_utils.stack_item_uid(item)
        entry["source_uid"] = source_uid
        source_uids.append(source_uid)
        entries.append(entry)
    if not entries:
        raise RuntimeError("登録できるレイヤーがありません")
    entries.sort(key=lambda entry: 0 if str(entry.get("kind", "")) == "coma" else 1)
    origin = _payload_origin(entries)
    return {
        "version": 1,
        "name": name or _payload_default_name(entries),
        "origin": {"x": origin[0], "y": origin[1]},
        "entries": entries,
        "links": _linked_groups_for_uids(context, source_uids),
    }


def create_collection_asset(
    context,
    payload: dict,
    *,
    target: AssetBrowserTarget | None = None,
) -> bpy.types.Collection:
    target = target or AssetBrowserTarget()
    coll = _new_asset_collection(_unique_asset_name(str(payload.get("name") or "B-MANGAアセット")))
    coll[ASSET_KIND_PROP] = ASSET_KIND_LAYER_BUNDLE
    coll[ASSET_PAYLOAD_PROP] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    origin = payload.get("origin") if isinstance(payload.get("origin"), dict) else {}
    origin_mm = (float(origin.get("x", 0.0) or 0.0), float(origin.get("y", 0.0) or 0.0))
    for obj in _preview_objects_for_payload(context, payload):
        clone = _clone_preview_object(obj, origin_mm=origin_mm)
        if clone is not None:
            coll.objects.link(clone)
    _mark_collection_asset(coll, target=target, description="B-MANGA レイヤーアセット")
    asset_preview.set_collection_asset_preview(coll, payload=payload)
    _write_external_library_if_needed(coll, target, context=context, payload=payload)
    if target.is_local:
        _refresh_open_asset_browser(context)
    return coll


def payload_from_collection(collection) -> dict | None:
    if collection is None:
        return None
    if str(collection.get(ASSET_KIND_PROP, "") or "") != ASSET_KIND_LAYER_BUNDLE:
        return None
    raw = str(collection.get(ASSET_PAYLOAD_PROP, "") or "")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def instantiate_payload(
    context,
    payload: dict,
    *,
    drop_world_xy_mm: tuple[float, float] | None = None,
    drop_local_xy_mm: tuple[float, float] | None = None,
    defer_to_page_file: bool = True,
    stage_id: str = "",
) -> dict:
    work = get_work(context)
    page = get_active_page(context)
    if work is None or not getattr(work, "loaded", False) or page is None:
        raise RuntimeError("ページ一覧を開いてください")
    page_index = int(getattr(work, "active_page_index", -1))
    page_offset = page_grid.page_total_offset_mm(work, context.scene, page_index)
    if drop_local_xy_mm is not None:
        drop_local = (float(drop_local_xy_mm[0]), float(drop_local_xy_mm[1]))
    elif drop_world_xy_mm is None:
        paper = getattr(work, "paper", None)
        drop_local = (
            float(getattr(paper, "canvas_width_mm", 210.0)) * 0.5,
            float(getattr(paper, "canvas_height_mm", 297.0)) * 0.5,
        )
    else:
        drop_local = (float(drop_world_xy_mm[0]) - page_offset[0], float(drop_world_xy_mm[1]) - page_offset[1])
    page_id = str(getattr(page, "id", "") or "")
    if defer_to_page_file and not _is_target_page_file(context, page_id):
        from . import cross_page_stage

        staged_id = cross_page_stage.stage_asset_bundle(
            Path(work.work_dir),
            page_id,
            payload,
            drop_local,
        )
        if not staged_id:
            raise RuntimeError("素材を対象ページへ送れませんでした")
        return {
            "created": [],
            "id_map": {},
            "uids": {},
            "created_new_count": 0,
            "staged": True,
            "stage_id": staged_id,
        }
    origin = payload.get("origin") if isinstance(payload.get("origin"), dict) else {}
    dx = drop_local[0] - float(origin.get("x", 0.0) or 0.0)
    dy = drop_local[1] - float(origin.get("y", 0.0) or 0.0)
    parent_kind, parent_key = _parent_for_point(page, drop_local[0], drop_local[1])
    id_map: dict[str, str] = {}
    parent_key_map: dict[str, str] = {}
    new_uids_by_source: dict[str, str] = {}
    made: list[object] = []
    newly_made: list[object] = []
    for entry_index, entry in enumerate(payload.get("entries", []) or []):
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", "") or "")
        entry_parent_kind, entry_parent_key = _parent_for_payload_entry(
            entry,
            parent_kind,
            parent_key,
            parent_key_map,
        )
        obj = _find_staged_asset_entry(context, page, stage_id, entry_index, kind)
        was_created = False
        if obj is None and kind == "coma":
            obj = asset_bundle_extended.instantiate_coma(
                context,
                page,
                entry,
                dx,
                dy,
                persist_sidecars=not bool(stage_id),
            )
            if obj is not None:
                was_created = True
                source_parent = asset_bundle_extended.source_parent_key(entry)
                if source_parent:
                    parent_key_map[source_parent] = coma_stack_key(page, obj)
        elif obj is None and kind == "balloon":
            obj = _instantiate_balloon(context, page, entry, dx, dy, entry_parent_kind, entry_parent_key)
            was_created = obj is not None
        elif obj is None and kind == "text":
            obj = _instantiate_text(context, page, entry, dx, dy, entry_parent_kind, entry_parent_key, id_map)
            was_created = obj is not None
        elif obj is None and kind == "effect":
            obj = _instantiate_effect(context, entry, dx, dy, entry_parent_key)
            was_created = obj is not None
        elif obj is None and kind == "raster":
            obj = asset_bundle_extended.instantiate_raster(
                context,
                page,
                entry,
                entry_parent_kind,
                entry_parent_key,
            )
            was_created = obj is not None
        elif obj is None and kind == "gp":
            obj = asset_bundle_extended.instantiate_gp_layer(
                context,
                page,
                entry,
                dx,
                dy,
                entry_parent_kind,
                entry_parent_key,
            )
            was_created = obj is not None
        elif obj is None:
            obj = None
        if obj is None:
            continue
        if stage_id and was_created:
            from . import cross_page_stage

            cross_page_stage.stamp_asset_created(
                context,
                obj,
                stage_id,
                entry_index,
                kind,
            )
        if kind == "coma":
            source_parent = asset_bundle_extended.source_parent_key(entry)
            if source_parent:
                parent_key_map[source_parent] = coma_stack_key(page, obj)
        obj = _normalize_staged_asset_result(kind, obj)
        made.append(obj)
        if was_created:
            newly_made.append(obj)
        old_id = str(entry.get("source_id", "") or "")
        new_id = _entry_id(obj)
        if old_id and new_id:
            id_map[old_id] = new_id
        source_uid = str(entry.get("source_uid", "") or "")
        new_uid = _new_uid_for_created(context, kind, page, obj)
        if source_uid and new_uid:
            new_uids_by_source[source_uid] = new_uid
    _restore_layer_links(context, payload, new_uids_by_source)
    _link_drop_target_text_to_new_balloons(page, newly_made, drop_local)
    _link_overlapping_texts_to_new_balloons(page, newly_made)
    layer_stack_utils.sync_layer_stack_after_data_change(context)
    return {
        "created": made,
        "id_map": id_map,
        "uids": new_uids_by_source,
        "created_new_count": len(newly_made),
        "staged": False,
        "stage_id": stage_id,
    }


def _is_target_page_file(context, page_id: str) -> bool:
    from . import page_file_scene

    role, current_page_id, _ = page_file_scene.current_role(context)
    return role == page_file_scene.ROLE_PAGE and current_page_id == str(page_id or "")


def _find_staged_asset_entry(context, page, stage_id: str, index: int, kind: str):
    if not stage_id:
        return None
    from . import cross_page_stage

    return cross_page_stage.find_asset_created(context, page, stage_id, index, kind)


def _normalize_staged_asset_result(kind: str, obj):
    if kind not in {"gp", "effect"} or isinstance(obj, tuple):
        return obj
    from . import layer_object_model

    return obj, layer_object_model.content_layer(obj)


def process_dropped_collection_instance(context, obj) -> bool:
    if obj is None or bool(obj.get(ASSET_INSTANCE_DONE_PROP, False)):
        return False
    collection = getattr(obj, "instance_collection", None)
    payload = payload_from_collection(collection)
    if payload is None:
        return False
    if not _can_instantiate_now(context):
        return False
    try:
        loc = getattr(obj, "location", None)
        drop = (m_to_mm(float(loc.x)), m_to_mm(float(loc.y))) if loc is not None else None
        instantiate_payload(context, payload, drop_world_xy_mm=drop)
        obj[ASSET_INSTANCE_DONE_PROP] = True
        bpy.data.objects.remove(obj, do_unlink=True)
        return True
    except Exception:  # noqa: BLE001
        obj[ASSET_INSTANCE_DONE_PROP] = True
        _logger.exception("B-MANGA asset instance import failed")
        return False


def process_pending_dropped_assets(context=None) -> int:
    ctx = context or bpy.context
    count = 0
    for obj in list(getattr(getattr(ctx, "scene", None), "objects", []) or []):
        if process_dropped_collection_instance(ctx, obj):
            count += 1
    return count


def _can_instantiate_now(context) -> bool:
    work = get_work(context)
    if work is None or not bool(getattr(work, "loaded", False)):
        return False
    return get_active_page(context) is not None


def _asset_browser_area(context, event=None, *, require_event_hit: bool = False):
    screen = getattr(context, "screen", None)
    if screen is None:
        return None
    for area in getattr(screen, "areas", []) or []:
        if getattr(area, "type", "") != "FILE_BROWSER":
            continue
        space = getattr(area.spaces, "active", None)
        if str(getattr(space, "browse_mode", "") or "") != "ASSETS":
            continue
        if event is not None or require_event_hit:
            x = int(getattr(event, "mouse_x", -1))
            y = int(getattr(event, "mouse_y", -1))
            if not (area.x <= x < area.x + area.width and area.y <= y < area.y + area.height):
                continue
        return area
    return None


def _asset_library_path(context, reference: str) -> str:
    prefs = getattr(context, "preferences", None)
    filepaths = getattr(prefs, "filepaths", None)
    wanted = _normalize_asset_library_reference(reference)
    for lib in getattr(filepaths, "asset_libraries", []) or []:
        candidates = (
            str(getattr(lib, "name", "") or ""),
            str(getattr(lib, "idname", "") or ""),
            str(getattr(lib, "identifier", "") or ""),
            str(getattr(lib, "uuid", "") or ""),
        )
        if any(candidate == reference for candidate in candidates):
            return bpy.path.abspath(str(getattr(lib, "path", "") or ""))
        if any(_normalize_asset_library_reference(candidate) == wanted for candidate in candidates):
            return bpy.path.abspath(str(getattr(lib, "path", "") or ""))
    return ""


def _normalize_asset_library_reference(value: str) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _selected_or_index_uids(context, stack, index: int) -> list[str]:
    base: list[str] = []
    if 0 <= int(index) < len(stack):
        base.append(layer_stack_utils.stack_item_uid(stack[int(index)]))
    else:
        for item in stack:
            if layer_stack_utils.is_item_selected(context, item):
                uid = layer_stack_utils.stack_item_uid(item)
                if uid:
                    base.append(uid)
    expanded: list[str] = []
    for uid in base:
        for linked in layer_links.linked_uids_for_uid(context, uid):
            if linked not in expanded:
                expanded.append(linked)
    return expanded or base


def _items_for_uids(context, stack, uids: list[str]) -> list[object]:
    wanted = set(asset_bundle_extended.expand_asset_uids(context, stack, uids))
    out = []
    for item in stack:
        uid = layer_stack_utils.stack_item_uid(item)
        if uid not in wanted:
            continue
        if str(getattr(item, "kind", "") or "") in SUPPORTED_LAYER_KINDS:
            out.append(item)
    return out


def _serialize_stack_item(context, item) -> dict | None:
    kind = str(getattr(item, "kind", "") or "")
    resolved = layer_stack_utils.resolve_stack_item(context, item)
    if resolved is None or resolved.get("target") is None:
        return None
    target = resolved["target"]
    extended = asset_bundle_extended.serialize_stack_item(context, item)
    if extended is not None:
        return extended
    if kind == "balloon":
        return {
            "kind": kind,
            "source_id": str(getattr(target, "id", "") or ""),
            "data": _pg_to_dict(target),
            "bounds": _entry_bounds(target),
        }
    if kind == "text":
        return {
            "kind": kind,
            "source_id": str(getattr(target, "id", "") or ""),
            "data": _pg_to_dict(target),
            "bounds": _entry_bounds(target),
        }
    if kind == "effect":
        from ..operators import effect_line_op
        from . import gp_layer_parenting as gp_parent, layer_object_model

        obj = resolved.get("object")
        layer = target
        bounds = effect_line_op.effect_layer_bounds(obj, layer)
        center = effect_line_op.effect_layer_center(obj, layer, bounds)
        meta = effect_line_op._effect_meta(obj).get(effect_line_op._layer_meta_key(layer), {})
        return {
            "kind": kind,
            "source_id": str(obj.get(on.PROP_ID, "") or getattr(layer, "name", "") or ""),
            "title": layer_object_model.display_title(obj) or "効果線",
            "bounds": list(bounds or (0.0, 0.0, 30.0, 30.0)),
            "center": list(center or (0.0, 0.0)),
            "meta": meta if isinstance(meta, dict) else {},
            "parent_key": gp_parent.parent_key(layer) or str(obj.get(on.PROP_PARENT_KEY, "") or ""),
        }
    return None


def _pg_to_dict(pg) -> dict:
    data: dict = {}
    for prop in getattr(pg.bl_rna, "properties", []) or []:
        ident = prop.identifier
        if ident == "rna_type" or ident == "selected":
            continue
        try:
            value = getattr(pg, ident)
        except Exception:  # noqa: BLE001
            continue
        if prop.type == "COLLECTION":
            data[ident] = [_pg_to_dict(item) for item in value]
        elif prop.type == "POINTER":
            data[ident] = _pg_to_dict(value) if value is not None else {}
        elif prop.type == "FLOAT" and getattr(prop, "is_array", False):
            data[ident] = [float(v) for v in value]
        elif prop.type == "INT" and getattr(prop, "is_array", False):
            data[ident] = [int(v) for v in value]
        elif prop.type == "BOOLEAN" and getattr(prop, "is_array", False):
            data[ident] = [bool(v) for v in value]
        else:
            try:
                json.dumps(value)
                data[ident] = value
            except TypeError:
                data[ident] = str(value)
    return data


def _dict_to_pg(pg, data: dict, *, skip: set[str] | None = None) -> None:
    skip = skip or set()
    if not isinstance(data, dict):
        return
    props = {prop.identifier: prop for prop in getattr(pg.bl_rna, "properties", []) or []}
    for ident, value in data.items():
        if ident in skip or ident == "rna_type" or ident not in props:
            continue
        prop = props[ident]
        try:
            if prop.type == "COLLECTION":
                coll = getattr(pg, ident)
                coll.clear()
                for item_data in value or []:
                    item = coll.add()
                    _dict_to_pg(item, item_data)
            elif prop.type == "POINTER":
                _dict_to_pg(getattr(pg, ident), value)
            else:
                setattr(pg, ident, value)
        except Exception:  # noqa: BLE001
            continue


def _payload_origin(entries: list[dict]) -> tuple[float, float]:
    rects = [entry.get("bounds") for entry in entries if isinstance(entry.get("bounds"), (list, tuple))]
    vals = []
    for rect in rects:
        if len(rect) >= 4:
            vals.append((float(rect[0]), float(rect[1]), float(rect[0]) + float(rect[2]), float(rect[1]) + float(rect[3])))
    if not vals:
        return 0.0, 0.0
    left = min(v[0] for v in vals)
    bottom = min(v[1] for v in vals)
    right = max(v[2] for v in vals)
    top = max(v[3] for v in vals)
    return (left + right) * 0.5, (bottom + top) * 0.5


def _payload_default_name(entries: list[dict]) -> str:
    labels = {
        "coma": "コマ",
        "balloon": "フキダシ",
        "text": "テキスト",
        "effect": "効果線",
        "raster": "ラスター",
        "gp": "グリースペンシル",
    }
    parts = [labels.get(str(entry.get("kind", "")), "レイヤー") for entry in entries]
    return "＆".join(parts[:3]) if len(parts) <= 3 else f"{parts[0]}ほか"


def _linked_groups_for_uids(context, uids: list[str]) -> list[list[str]]:
    selected = set(uids)
    groups: list[list[str]] = []
    seen: set[str] = set()
    for uid in uids:
        if uid in seen:
            continue
        group = sorted(layer_links.linked_uids_for_uid(context, uid) & selected)
        seen.update(group)
        if len(group) >= 2:
            groups.append(group)
    return groups


def _entry_bounds(entry) -> list[float]:
    return [
        float(getattr(entry, "x_mm", 0.0) or 0.0),
        float(getattr(entry, "y_mm", 0.0) or 0.0),
        float(getattr(entry, "width_mm", 1.0) or 1.0),
        float(getattr(entry, "height_mm", 1.0) or 1.0),
    ]


def _parent_for_point(page, x_mm: float, y_mm: float) -> tuple[str, str]:
    panel = coma_containing_point(page, float(x_mm), float(y_mm))
    if panel is not None:
        return "coma", coma_stack_key(page, panel)
    return "page", str(getattr(page, "id", "") or "")


def _parent_for_payload_entry(
    entry: dict,
    default_kind: str,
    default_key: str,
    parent_key_map: dict[str, str],
) -> tuple[str, str]:
    source_parent = asset_bundle_extended.source_parent_key(entry)
    if source_parent and source_parent in parent_key_map:
        key = parent_key_map[source_parent]
        return ("coma" if ":" in key else "page"), key
    return default_kind, default_key


def _instantiate_balloon(context, page, entry: dict, dx: float, dy: float, parent_kind: str, parent_key: str):
    from ..operators import balloon_op
    from . import balloon_curve_object

    data = dict(entry.get("data") or {})
    new_entry = page.balloons.add()
    with balloon_curve_object.suspend_auto_sync():
        _dict_to_pg(new_entry, data, skip={"id", "selected"})
        new_entry.id = balloon_op._allocate_balloon_id(page, get_work(context))
        new_entry.x_mm = float(getattr(new_entry, "x_mm", 0.0)) + dx
        new_entry.y_mm = float(getattr(new_entry, "y_mm", 0.0)) + dy
        new_entry.parent_kind = parent_kind
        new_entry.parent_key = parent_key
        new_entry.selected = False
    balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=new_entry, page=page)
    return new_entry


def _instantiate_text(context, page, entry: dict, dx: float, dy: float, parent_kind: str, parent_key: str, id_map: dict[str, str]):
    from ..operators import text_op
    from . import text_real_object

    data = dict(entry.get("data") or {})
    old_parent = str(data.get("parent_balloon_id", "") or "")
    new_entry = page.texts.add()
    with text_real_object.suspend_auto_sync():
        _dict_to_pg(new_entry, data, skip={"id", "selected"})
        new_entry.id = text_op._allocate_text_id(page)
        new_entry.x_mm = float(getattr(new_entry, "x_mm", 0.0)) + dx
        new_entry.y_mm = float(getattr(new_entry, "y_mm", 0.0)) + dy
        new_entry.parent_kind = parent_kind
        new_entry.parent_key = parent_key
        new_entry.parent_balloon_id = id_map.get(old_parent, "") if old_parent else ""
        new_entry.selected = False
    text_real_object.ensure_text_real_object(scene=context.scene, entry=new_entry, page=page)
    return new_entry


def _instantiate_effect(context, entry: dict, dx: float, dy: float, parent_key: str):
    from ..core import effect_line
    from ..operators import effect_line_op

    params = getattr(context.scene, "bmanga_effect_line_params", None)
    meta = dict(entry.get("meta") or {})
    params_data = dict(meta.get("params") or {})
    if params is not None and params_data:
        effect_line.effect_params_from_dict(params, params_data)
    bounds = list(entry.get("bounds") or (0.0, 0.0, 30.0, 30.0))
    bounds = (float(bounds[0]) + dx, float(bounds[1]) + dy, float(bounds[2]), float(bounds[3]))
    center = list(entry.get("center") or (bounds[0] + bounds[2] * 0.5, bounds[1] + bounds[3] * 0.5))
    center_xy = (float(center[0]) + dx, float(center[1]) + dy)
    obj, layer = effect_line_op._create_effect_layer(context, bounds, parent_key=parent_key)
    if obj is None or layer is None:
        return None
    from . import layer_object_model

    layer_object_model.set_display_title(obj, str(entry.get("title", "") or "効果線"))
    stored = effect_line_op._effect_meta(obj)
    key = effect_line_op._layer_meta_key(layer)
    current = dict(stored.get(key) or {})
    for meta_key, meta_value in meta.items():
        if meta_key not in {"x", "y", "w", "h", "center_x", "center_y", "params"}:
            current[meta_key] = meta_value
    stored[key] = current
    effect_line_op._write_effect_meta(obj, stored)
    effect_line_op._write_effect_strokes(context, obj, layer, bounds, center_xy_mm=center_xy)
    return (obj, layer)


def _entry_id(obj) -> str:
    if isinstance(obj, tuple):
        return str(obj[0].get(on.PROP_ID, "") or "")
    return str(getattr(obj, "id", "") or "")


def _new_uid_for_created(context, kind: str, page, obj) -> str:
    extended = asset_bundle_extended.new_uid_for_created(kind, page, obj)
    if extended:
        return extended
    if kind == "balloon":
        return layer_stack_utils.target_uid("balloon", f"{getattr(page, 'id', '')}:{getattr(obj, 'id', '')}")
    if kind == "text":
        return layer_stack_utils.target_uid("text", f"{getattr(page, 'id', '')}:{getattr(obj, 'id', '')}")
    if kind == "effect" and isinstance(obj, tuple):
        return layer_stack_utils.target_uid("effect", str(obj[0].get(on.PROP_ID, "") or ""))
    return ""


def _restore_layer_links(context, payload: dict, new_uids_by_source: dict[str, str]) -> None:
    for group in payload.get("links", []) or []:
        mapped = [new_uids_by_source.get(str(uid), "") for uid in group]
        mapped = [uid for uid in mapped if uid]
        if len(mapped) >= 2:
            layer_links.link_uids(context, mapped)


def _link_overlapping_texts_to_new_balloons(page, made: list[object]) -> None:
    balloons = [item for item in made if hasattr(item, "shape") and hasattr(item, "width_mm")]
    if not balloons:
        return
    made_text_ids = {str(getattr(item, "id", "") or "") for item in made if hasattr(item, "body")}
    for balloon in balloons:
        bx = float(getattr(balloon, "x_mm", 0.0))
        by = float(getattr(balloon, "y_mm", 0.0))
        bw = float(getattr(balloon, "width_mm", 0.0))
        bh = float(getattr(balloon, "height_mm", 0.0))
        for text in getattr(page, "texts", []) or []:
            if str(getattr(text, "id", "") or "") in made_text_ids:
                continue
            if str(getattr(text, "parent_balloon_id", "") or ""):
                continue
            cx = float(getattr(text, "x_mm", 0.0)) + float(getattr(text, "width_mm", 0.0)) * 0.5
            cy = float(getattr(text, "y_mm", 0.0)) + float(getattr(text, "height_mm", 0.0)) * 0.5
            if bx <= cx <= bx + bw and by <= cy <= by + bh:
                text.parent_balloon_id = str(getattr(balloon, "id", "") or "")


def _link_drop_target_text_to_new_balloons(page, made: list[object], drop_local: tuple[float, float]) -> None:
    balloons = [item for item in made if hasattr(item, "shape") and hasattr(item, "width_mm")]
    if not balloons:
        return
    balloon = balloons[-1]
    balloon_id = str(getattr(balloon, "id", "") or "")
    if not balloon_id:
        return
    x, y = float(drop_local[0]), float(drop_local[1])
    made_text_ids = {str(getattr(item, "id", "") or "") for item in made if hasattr(item, "body")}
    for text in getattr(page, "texts", []) or []:
        if str(getattr(text, "id", "") or "") in made_text_ids:
            continue
        if str(getattr(text, "parent_balloon_id", "") or ""):
            continue
        tx = float(getattr(text, "x_mm", 0.0))
        ty = float(getattr(text, "y_mm", 0.0))
        tw = float(getattr(text, "width_mm", 0.0))
        th = float(getattr(text, "height_mm", 0.0))
        if tx <= x <= tx + tw and ty <= y <= ty + th:
            text.parent_balloon_id = balloon_id
            return


def _preview_objects_for_payload(context, payload: dict) -> list[bpy.types.Object]:
    out: list[bpy.types.Object] = []
    for entry in payload.get("entries", []) or []:
        kind = str(entry.get("kind", "") or "")
        source_id = str(entry.get("source_id", "") or "")
        obj = None
        for candidate in asset_bundle_extended.preview_objects_for_entry(entry):
            if candidate is not None and candidate not in out:
                out.append(candidate)
        if kind in asset_bundle_extended.EXTENDED_LAYER_KINDS:
            continue
        if kind == "balloon":
            from . import balloon_curve_object

            for candidate in _balloon_preview_objects(source_id, balloon_curve_object):
                if candidate is not None and candidate not in out:
                    out.append(candidate)
            continue
        elif kind == "text":
            from . import text_real_object

            page_id = _page_id_for_source_uid(str(entry.get("source_uid", "")))
            obj = text_real_object.find_text_object(page_id, source_id)
        elif kind == "effect":
            obj = on.find_object_by_bmanga_id(source_id, kind="effect")
            if obj is not None:
                from . import effect_line_object

                obj = effect_line_object.find_effect_display_object(obj) or obj
        if obj is not None and obj not in out:
            out.append(obj)
    return out


def _balloon_preview_objects(source_id: str, balloon_curve_object) -> list[bpy.types.Object]:
    body = None
    companions: list[bpy.types.Object] = []
    if not source_id:
        return []
    body = balloon_curve_object.find_balloon_object(source_id)
    try:
        from . import balloon_fill_mesh, balloon_line_mesh

        for candidate in bpy.data.objects:
            fill_owner = str(candidate.get(balloon_fill_mesh.PROP_BALLOON_FILL_MESH_OWNER_ID, "") or "")
            line_owner = str(candidate.get(balloon_line_mesh.PROP_BALLOON_LINE_MESH_OWNER_ID, "") or "")
            if source_id in {fill_owner, line_owner} and candidate not in companions:
                companions.append(candidate)
    except Exception:  # noqa: BLE001
        pass
    if companions:
        return companions
    return [body] if body is not None else []


def _clone_preview_object(obj: bpy.types.Object, *, origin_mm: tuple[float, float]) -> bpy.types.Object | None:
    try:
        clone = obj.copy()
        if getattr(obj, "data", None) is not None:
            clone.data = obj.data.copy()
        clone.animation_data_clear()
        clone.name = f"asset_preview_{obj.name}"
        clone.parent = None
        origin_offset = Matrix.Translation((-mm_to_m(float(origin_mm[0])), -mm_to_m(float(origin_mm[1])), 0.0))
        clone.matrix_world = origin_offset @ obj.matrix_world
        clone.hide_viewport = False
        clone.hide_render = False
        for key in list(clone.keys()):
            try:
                del clone[key]
            except Exception:  # noqa: BLE001
                pass
        clone[ASSET_PROTOTYPE_PROP] = True
        clone[on.PROP_MANAGED] = False
        return clone
    except Exception:  # noqa: BLE001
        _logger.exception("asset preview clone failed")
        return None


def _new_asset_collection(name: str) -> bpy.types.Collection:
    coll = bpy.data.collections.new(name)
    coll.use_fake_user = True
    return coll


def _mark_collection_asset(coll: bpy.types.Collection, *, target: AssetBrowserTarget, description: str) -> None:
    coll.asset_mark()
    coll.asset_data.description = description
    if target.catalog_id and target.catalog_id != "00000000-0000-0000-0000-000000000000":
        try:
            coll.asset_data.catalog_id = target.catalog_id
        except Exception:  # noqa: BLE001
            pass
    for obj in coll.objects:
        obj.use_fake_user = True
        if getattr(obj, "data", None) is not None:
            obj.data.use_fake_user = True


def _write_external_library_if_needed(
    coll: bpy.types.Collection,
    target: AssetBrowserTarget,
    *,
    context=None,
    payload: dict | None = None,
) -> None:
    if target.is_local:
        return
    library_path = Path(target.library_path)
    if not library_path:
        return
    library_path.mkdir(parents=True, exist_ok=True)
    blend_path = _unique_library_blend_path(library_path, coll.name)
    bpy.data.libraries.write(str(blend_path), {coll}, fake_user=True)
    asset_preview.patch_external_library_preview(
        blend_path,
        coll.name,
        payload=payload,
        objects=list(getattr(coll, "objects", []) or []),
    )
    _refresh_open_asset_browser(context)


def _refresh_open_asset_browser(context=None) -> None:
    try:
        area = _asset_browser_area(context) if context is not None else None
        if area is None or not hasattr(context, "temp_override"):
            _run_asset_browser_refresh_ops()
            return
        space = getattr(area.spaces, "active", None)
        region = next((region for region in getattr(area, "regions", []) if region.type == "WINDOW"), None)
        if region is None:
            _run_asset_browser_refresh_ops()
            return
        with context.temp_override(area=area, region=region, space_data=space):
            _run_asset_browser_refresh_ops()
        try:
            area.tag_redraw()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


def _run_asset_browser_refresh_ops() -> None:
    for op in (getattr(bpy.ops.asset, "library_refresh", None), getattr(bpy.ops.file, "refresh", None)):
        if op is None:
            continue
        try:
            op()
        except Exception:  # noqa: BLE001
            pass


def _unique_library_blend_path(library_path: Path, asset_name: str) -> Path:
    base = _safe_asset_file_stem(asset_name)
    path = library_path / f"{base}.blend"
    index = 1
    while path.exists():
        index += 1
        path = library_path / f"{base}.{index:03d}.blend"
    return path


def _safe_asset_file_stem(name: str) -> str:
    cleaned = "".join(
        "_" if ch in INVALID_FILENAME_CHARS or ord(ch) < 32 else ch
        for ch in str(name or "")
    ).strip(" .")
    return (cleaned or ASSET_FILE_NAME.removesuffix(".blend"))[:80]


def _unique_asset_name(base: str) -> str:
    clean = str(base or "B-MANGAアセット").strip() or "B-MANGAアセット"
    name = clean
    i = 1
    while name in bpy.data.collections:
        i += 1
        name = f"{clean}.{i:03d}"
    return name


def _common_name(objects: list[bpy.types.Object]) -> str:
    if len(objects) == 1:
        return objects[0].name
    return f"{objects[0].name}ほか"


def _object_group_origin_mm(objects: list[bpy.types.Object]) -> tuple[float, float]:
    if not objects:
        return 0.0, 0.0
    xs = [m_to_mm(float(obj.location.x)) for obj in objects]
    ys = [m_to_mm(float(obj.location.y)) for obj in objects]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _page_id_for_source_uid(uid: str) -> str:
    try:
        _kind, key = uid.split(":", 1)
        return key.split(":", 1)[0]
    except Exception:  # noqa: BLE001
        return ""
