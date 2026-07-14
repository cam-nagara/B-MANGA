"""ページファイル間のレイヤー移動 (クロスファイル転送).

ページファイル (ROLE_PAGE) 上で Alt+ドラッグにより選択レイヤーを
別ページのプレビュー上にドロップしたとき、ソースページの JSON から
エントリーを取り出し、ターゲットページの page.json へ書き込む。

対応レイヤー種別: balloon, text, effect, GP
画像・ラスター・塗り・レイヤーフォルダーは、ページ用 blend 内の
実体と参照を一括保全できるまで原子的に拒否する。
効果線はパラメータ JSON をステージングファイルに書き出し、ターゲット
ページの読込時に自動生成する。GP も1管理Object単位で同様に復元する。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import uuid

import bpy

from . import cross_page_gp_transfer, cross_page_stage, json_io, log, page_grid, paths
from .layer_hierarchy import split_child_key

_logger = log.get_logger(__name__)

_STAGED_IMPORTS_NAME = cross_page_stage.STAGED_IMPORTS_NAME

_SUPPORTED_KINDS = frozenset({"balloon", "text", "effect", "gp"})
_UNSUPPORTED_KINDS = frozenset({"image", "raster", "fill", "layer_folder"})


def _work_dir(work) -> Path | None:
    wd = str(getattr(work, "work_dir", "") or "").strip()
    if not wd:
        return None
    return Path(wd)


def _read_target_page_json(work_dir: Path, target_page_id: str) -> dict | None:
    meta_path = paths.page_meta_path(work_dir, target_page_id)
    if not meta_path.is_file():
        return None
    try:
        return json_io.read_json(meta_path)
    except Exception:  # noqa: BLE001
        _logger.exception("target page.json read failed: %s", meta_path)
        return None


def _write_target_page_json(work_dir: Path, target_page_id: str, data: dict) -> bool:
    meta_path = paths.page_meta_path(work_dir, target_page_id)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        json_io.write_json(meta_path, data)
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("target page.json write failed: %s", meta_path)
        return False


def _page_offset_mm(work, scene, page_index: int) -> tuple[float, float]:
    try:
        return page_grid.page_total_offset_mm(work, scene, page_index)
    except Exception:  # noqa: BLE001
        return (0.0, 0.0)


def _target_page_offset_mm(work, scene, target_page_id: str) -> tuple[float, float]:
    for i, page in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(page, "id", "") or "") == target_page_id:
            return _page_offset_mm(work, scene, i)
    return (0.0, 0.0)


def _convert_coords(
    entry_dict: dict,
    src_offset: tuple[float, float],
    dst_offset: tuple[float, float],
) -> dict:
    """ソースページ座標 → ワールド → ターゲットページ座標."""
    d = copy.deepcopy(entry_dict)
    for xkey, ykey in [("xMm", "yMm"), ("x_mm", "y_mm")]:
        if xkey in d and ykey in d:
            d[xkey] = float(d[xkey] or 0) + src_offset[0] - dst_offset[0]
            d[ykey] = float(d[ykey] or 0) + src_offset[1] - dst_offset[1]
            break
    return d


def _resolve_coma_from_json(
    page_data: dict,
    drop_x_mm: float,
    drop_y_mm: float,
) -> str:
    """page.json の comas からドロップ座標を含むコマ ID を返す。見つからなければ空文字."""
    from .layer_hierarchy import point_in_polygon

    comas = page_data.get("comas", [])
    best_id = ""
    best_z = -1
    for coma in comas:
        if not isinstance(coma, dict):
            continue
        shape = coma.get("shape", {})
        if not isinstance(shape, dict):
            continue
        verts = shape.get("vertices", [])
        if isinstance(verts, list) and len(verts) >= 3:
            poly = [(float(v[0]), float(v[1])) for v in verts if isinstance(v, (list, tuple)) and len(v) >= 2]
        else:
            rect = shape.get("rect", {})
            if not isinstance(rect, dict):
                continue
            rx = float(rect.get("x", 0))
            ry = float(rect.get("y", 0))
            rw = float(rect.get("widthMm", 0))
            rh = float(rect.get("heightMm", 0))
            if rw <= 0 or rh <= 0:
                continue
            poly = [(rx, ry), (rx + rw, ry), (rx + rw, ry + rh), (rx, ry + rh)]
        if not point_in_polygon((drop_x_mm, drop_y_mm), poly):
            continue
        z = int(coma.get("zOrder", 0))
        if z > best_z:
            best_z = z
            coma_id = str(coma.get("comaId", "") or coma.get("id", "") or "")
            if coma_id:
                best_id = coma_id
    return best_id


def _unique_id(existing_ids: set[str], preferred: str, prefix: str) -> str:
    if preferred and preferred not in existing_ids:
        return preferred
    i = 1
    while True:
        candidate = f"{prefix}_{i:04d}"
        if candidate not in existing_ids:
            return candidate
        i += 1


def _collect_child_text_ids(page, balloon_id: str) -> list[str]:
    """フキダシに紐づく子テキスト ID を収集."""
    result = []
    for text in getattr(page, "texts", []) or []:
        if str(getattr(text, "parent_balloon_id", "") or "") == balloon_id:
            result.append(str(getattr(text, "id", "") or ""))
    return result


def _serialize_entry(entry, kind: str):
    """Blender PropertyGroup エントリーを dict にシリアライズ."""
    from ..io import schema

    if kind == "balloon":
        return schema.balloon_entry_to_dict(entry)
    if kind == "text":
        return schema.text_entry_to_dict(entry)
    if kind == "image":
        return schema.image_layer_to_dict(entry)
    if kind == "raster":
        return schema.raster_layer_to_dict(entry)
    if kind == "fill":
        return schema.fill_layer_to_dict(entry)
    return None


def _json_list_key(kind: str) -> str | None:
    return {
        "balloon": "balloons",
        "text": "texts",
        "image": "imageLayers",
        "raster": "rasterLayers",
        "fill": "fillLayers",
    }.get(kind)


def _existing_ids_in_json(data: dict, list_key: str) -> set[str]:
    entries = data.get(list_key) or []
    ids: set[str] = set()
    for e in entries:
        eid = e.get("id", "")
        if eid:
            ids.add(str(eid))
    return ids


def _remove_entry_from_page(page, kind: str, entry_id: str) -> bool:
    """ソースページの PropertyGroup コレクションからエントリーを削除."""
    collection_attr = {
        "balloon": "balloons",
        "text": "texts",
        "image": "image_layers",
        "raster": "raster_layers",
        "fill": "fill_layers",
    }.get(kind)
    if collection_attr is None:
        return False
    coll = getattr(page, collection_attr, None)
    if coll is None:
        return False
    for i, entry in enumerate(coll):
        eid = str(getattr(entry, "id", "") or "")
        if eid == entry_id:
            coll.remove(i)
            return True
    return False


def _source_entry_snapshot(page, kind: str, entry_id: str) -> tuple[str, str, dict, int] | None:
    collection_attr = {
        "balloon": "balloons",
        "text": "texts",
        "image": "image_layers",
        "raster": "raster_layers",
        "fill": "fill_layers",
    }.get(kind)
    coll = getattr(page, collection_attr, None) if collection_attr else None
    if coll is None:
        return None
    for index, entry in enumerate(coll):
        if str(getattr(entry, "id", "") or "") != entry_id:
            continue
        data = _serialize_entry(entry, kind)
        return (kind, entry_id, data, index) if isinstance(data, dict) else None
    return None


def _restore_source_entries(page, snapshots: list[tuple[str, str, dict, int]]) -> None:
    from ..io import schema

    specs = {
        "balloon": ("balloons", schema.balloon_entry_from_dict, True),
        "text": ("texts", schema.text_entry_from_dict, False),
        "image": ("image_layers", schema.image_layer_from_dict, True),
        "raster": ("raster_layers", schema.raster_layer_from_dict, True),
        "fill": ("fill_layers", schema.fill_layer_from_dict, True),
    }
    for kind, entry_id, data, original_index in sorted(snapshots, key=lambda item: item[3]):
        spec = specs.get(kind)
        if spec is None:
            continue
        coll = getattr(page, spec[0], None)
        if coll is None or any(str(getattr(item, "id", "") or "") == entry_id for item in coll):
            continue
        restored = coll.add()
        if spec[2]:
            spec[1](restored, data, opacity_percent=True)
        else:
            spec[1](restored, data)
        current_index = len(coll) - 1
        if current_index != original_index:
            coll.move(current_index, max(0, min(original_index, current_index)))


def _rollback_transfer(
    work_dir: Path,
    source_page,
    target_page_id: str,
    target_original: dict,
    source_snapshots: list[tuple[str, str, dict, int]],
    copied_rasters: list[Path],
    staged_tokens: dict[str, set[str]],
    *,
    source_was_modified: bool,
    target_was_written: bool,
) -> None:
    """失敗した移動のtarget/stage/source-memory/PNGを元へ戻す。"""
    # targetへまだ書き込んでいない準備段階の失敗では、同じ内容であっても
    # page.jsonを再保存しない。mtimeや外部変更基準まで動かすと「失敗時は
    # 無変更」というトランザクション契約を破るためである。
    target_restored = True
    if target_was_written:
        target_restored = _write_target_page_json(work_dir, target_page_id, target_original)
    if source_was_modified:
        _restore_source_entries(source_page, source_snapshots)
        try:
            from ..io import page_io

            page_io.save_page_json(work_dir, source_page)
        except Exception:  # noqa: BLE001
            _logger.exception("source page rollback save failed")
    cross_page_stage._remove_processed_entries(
        work_dir,
        target_page_id,
        staged_tokens,
    )
    if target_restored or not target_was_written:
        _cleanup_new_rasters(copied_rasters)


def _copy_raster_image(
    work_dir: Path,
    src_page_id: str,
    entry_dict: dict,
    target_data: dict,
) -> Path | None:
    """一意IDへアトミック複製し、参照を新しいPNGへ差し替える。"""
    old_id = str(entry_dict.get("id", "") or "")
    rel = str(entry_dict.get("filepath_rel", "") or f"raster/{old_id}.png")
    source = _resolve_raster_source(work_dir, src_page_id, rel, old_id)
    if source is None:
        _logger.error("raster source missing: %s", rel)
        return None
    existing = _existing_ids_in_json(target_data, "rasterLayers")
    destination = None
    new_id = ""
    for _ in range(128):
        candidate = uuid.uuid4().hex[:12]
        candidate_path = paths.raster_png_path(work_dir, candidate)
        if candidate not in existing and not candidate_path.exists():
            new_id = candidate
            destination = candidate_path
            break
    if destination is None:
        _logger.error("raster transfer ID allocation failed")
        return None
    try:
        _atomic_verified_copy(source, destination)
    except Exception:  # noqa: BLE001
        _logger.exception("raster copy failed: %s -> %s", source, destination)
        return None
    entry_dict["id"] = new_id
    entry_dict["image_name"] = f"raster_{new_id}"
    entry_dict["filepath_rel"] = f"{paths.RASTER_DIR_NAME}/{new_id}.png"
    return destination


def _resolve_raster_source(
    work_dir: Path,
    src_page_id: str,
    rel: str,
    raster_id: str,
) -> Path | None:
    """現行の作品直下配置と旧ページ内配置から安全な転送元を探す。"""
    work_root = work_dir.resolve()
    candidates: list[Path] = []
    if rel:
        rel_path = Path(rel)
        if not rel_path.is_absolute():
            candidates.extend((work_dir / rel_path, paths.page_dir(work_dir, src_page_id) / rel_path))
    try:
        candidates.append(paths.raster_png_path(work_dir, raster_id))
    except ValueError:
        pass
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        marker = os.path.normcase(str(resolved))
        if marker in seen:
            continue
        seen.add(marker)
        try:
            resolved.relative_to(work_root)
        except ValueError:
            continue
        if candidate.is_symlink() or not resolved.is_file():
            continue
        return resolved
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_verified_copy(source: Path, destination: Path) -> None:
    """同一ディレクトリ一時ファイルへ複製し、サイズ/hash一致後に置換する。"""
    from ..io.project_content_migration_lock import guard_path_write
    from ..io.project_content_save_baseline import record_successful_write

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() or destination.exists():
        raise OSError("raster destination already exists or is a symbolic link")
    expected_size = source.stat().st_size
    expected_hash = _sha256_file(source)
    with guard_path_write(destination):
        fd, temp_name = tempfile.mkstemp(
            prefix=destination.name + ".",
            suffix=".tmp",
            dir=str(destination.parent),
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as target, source.open("rb") as source_handle:
                for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if temp_path.stat().st_size != expected_size or _sha256_file(temp_path) != expected_hash:
                raise OSError("raster temporary copy verification failed")
            os.replace(temp_path, destination)
            if destination.stat().st_size != expected_size or _sha256_file(destination) != expected_hash:
                raise OSError("raster final copy verification failed")
            record_successful_write(destination)
        except BaseException:
            # destination はこの処理が新規確保したパスなので、置換後の検証失敗時も
            # 同じguard内で必ず残骸を回収し、非存在を新しい基準にする。
            destination.unlink(missing_ok=True)
            record_successful_write(destination)
            raise
        finally:
            temp_path.unlink(missing_ok=True)


def _cleanup_new_rasters(paths_to_remove: list[Path]) -> None:
    from ..io.project_content_migration_lock import guard_path_write
    from ..io.project_content_save_baseline import record_successful_write

    for path in paths_to_remove:
        try:
            with guard_path_write(path):
                path.unlink(missing_ok=True)
                record_successful_write(path)
        except Exception:  # noqa: BLE001
            _logger.exception("uncommitted raster cleanup failed: %s", path)


# ---------- 効果線ステージング ----------


def _extract_effect_meta(bmanga_id: str) -> dict | None:
    """効果線 GP オブジェクトからメタデータ (bounds + params) を抽出."""
    from . import layer_object_model
    from .object_naming import find_object_by_bmanga_id

    obj = find_object_by_bmanga_id(bmanga_id, kind="effect")
    if obj is None:
        return None
    data = getattr(obj, "data", None)
    if data is None:
        return None
    raw = data.get("bmanga_effect_line_meta", "{}")
    try:
        meta = json.loads(raw) if isinstance(raw, str) else {}
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(meta, dict) or not meta:
        return None
    for _layer_name, entry in meta.items():
        if isinstance(entry, dict) and "params" in entry:
            result = copy.deepcopy(entry)
            result["bmanga_id"] = layer_object_model.stable_id(obj)
            result["title"] = layer_object_model.display_title(obj)
            result["z_index"] = layer_object_model.z_index(obj)
            result["folder_id"] = layer_object_model.folder_id(obj)
            result["parent_key"] = layer_object_model.parent_key(obj)
            result["visible"] = layer_object_model.user_visible(obj)
            result["locked"] = layer_object_model.user_locked(obj)
            return result
    return None


def _remove_effect_objects(bmanga_id: str) -> bool:
    """効果線の関連 Blender オブジェクトを削除し、残存しないことを確認する。"""
    from .object_naming import PROP_ID, find_object_by_bmanga_id

    obj = find_object_by_bmanga_id(bmanga_id, kind="effect")
    if obj is None:
        return True
    controller_id = str(obj.get(PROP_ID, "") or "")
    objs_to_remove = [obj]
    for o in bpy.data.objects:
        if str(o.get("bmanga_effect_controller_id", "") or "") == controller_id:
            objs_to_remove.append(o)
    failed = False
    for o in dict.fromkeys(objs_to_remove):
        try:
            bpy.data.objects.remove(o, do_unlink=True)
        except Exception:  # noqa: BLE001
            failed = True
            _logger.exception("effect object removal failed: %s", bmanga_id)
    primary_remains = find_object_by_bmanga_id(bmanga_id, kind="effect") is not None
    dependent_remains = any(
        str(o.get("bmanga_effect_controller_id", "") or "") == controller_id
        for o in bpy.data.objects
    )
    if failed or primary_remains or dependent_remains:
        _logger.error("effect object removal incomplete: %s", bmanga_id)
        return False
    return True


def process_staged_imports(context, *, page_id: str = "") -> int:
    """ページ load_post で呼ばれ、保存待ちのレイヤーと素材を復元する。"""
    return cross_page_stage.process_staged_imports(context, page_id=page_id)


def commit_staged_imports_after_save(
    context=None,
    *,
    blend_path: str | Path = "",
    metadata_saved: bool = False,
    native_save_succeeded: bool = True,
) -> int:
    """blend とページ情報の保存成功後だけ、復元元を確定削除する。"""
    return cross_page_stage.commit_staged_imports_after_save(
        context,
        blend_path=blend_path,
        metadata_saved=metadata_saved,
        native_save_succeeded=native_save_succeeded,
    )


# ---------- 公開 API ----------


def _layer_object_id(item, kind: str) -> str:
    key = str(getattr(item, "key", "") or "").strip()
    if not key:
        return ""
    if key.startswith(f"{kind}_"):
        return key
    _page_part, child_id = split_child_key(key)
    return child_id or key


def transfer_layers_to_page(
    context,
    work,
    source_page,
    target_page_id: str,
    layer_items: list,
    *,
    target_parent_kind: str = "page",
    target_coma_id: str = "",
    target_folder_id: str = "",
    drop_world_xy_mm: tuple[float, float] | None = None,
) -> int:
    """作品ロック中に対象再読込から移動元確定までを一括実行する。"""
    wd = _work_dir(work)
    if wd is None:
        return 0
    try:
        from ..io.project_content_migration_lock import work_lock

        with work_lock(wd, blocking=True):
            return _transfer_layers_to_page_locked(
                context,
                work,
                source_page,
                target_page_id,
                layer_items,
                target_parent_kind=target_parent_kind,
                target_coma_id=target_coma_id,
                target_folder_id=target_folder_id,
                drop_world_xy_mm=drop_world_xy_mm,
            )
    except Exception:  # noqa: BLE001
        _logger.exception("cross-page transfer transaction failed: %s", target_page_id)
        return 0


def _transfer_layers_to_page_locked(
    context,
    work,
    source_page,
    target_page_id: str,
    layer_items: list,
    *,
    target_parent_kind: str = "page",
    target_coma_id: str = "",
    target_folder_id: str = "",
    drop_world_xy_mm: tuple[float, float] | None = None,
) -> int:
    """責務別のトランザクション実装へ委譲する。"""
    from . import cross_page_transfer_transaction

    return cross_page_transfer_transaction.execute_locked(
        context,
        work,
        source_page,
        target_page_id,
        layer_items,
        target_parent_kind=target_parent_kind,
        target_coma_id=target_coma_id,
        target_folder_id=target_folder_id,
        drop_world_xy_mm=drop_world_xy_mm,
    )


def _parent_page_id(parent_key: str) -> str:
    page_id, _child_id = split_child_key(str(parent_key or ""))
    return page_id


def transfer_layer_object_to_parent(
    context,
    work,
    obj,
    target_parent_key: str,
    *,
    folder_id: str | None = None,
) -> bool:
    """個別GP／効果線Objectを別ページのステージングへ移す。"""
    from . import layer_object_model

    kind = layer_object_model.layer_kind(obj)
    bmanga_id = layer_object_model.stable_id(obj)
    source_page_id = _parent_page_id(layer_object_model.parent_key(obj))
    target_page_id, target_coma_id = split_child_key(str(target_parent_key or ""))
    if kind not in {"gp", "effect"} or not bmanga_id:
        return False
    if not source_page_id or not target_page_id or source_page_id == target_page_id:
        return False
    source_page = next(
        (
            page for page in getattr(work, "pages", [])
            if str(getattr(page, "id", "") or "") == source_page_id
        ),
        None,
    )
    if source_page is None:
        return False
    moved = transfer_layers_to_page(
        context,
        work,
        source_page,
        target_page_id,
        [SimpleNamespace(kind=kind, key=bmanga_id)],
        target_parent_kind="coma" if target_coma_id else "page",
        target_coma_id=target_coma_id,
        # 別ページの意味的な親へ移す場合、移動元ページのフォルダーは引き継がない。
        # 呼出側が移動先フォルダーを明示した場合だけ、そのIDを使う。
        target_folder_id=str(folder_id or "") if folder_id is not None else "",
    )
    return moved == 1


def _find_entry_in_page(page, kind: str, entry_id: str):
    """ページ内から指定 kind/id のエントリーを検索."""
    collection_attr = {
        "balloon": "balloons",
        "text": "texts",
        "image": "image_layers",
        "raster": "raster_layers",
        "fill": "fill_layers",
    }.get(kind)
    if collection_attr is None:
        return None
    for entry in getattr(page, collection_attr, []) or []:
        if str(getattr(entry, "id", "") or "") == entry_id:
            return entry
    return None


def _set_parent_in_dict(d: dict, parent_kind: str, parent_key: str) -> None:
    """dict 内の parentKind / parentKey を更新."""
    for pk_key in ("parentKind", "parent_kind"):
        if pk_key in d:
            d[pk_key] = parent_kind
            break
    else:
        d["parentKind"] = parent_kind

    for pk_key in ("parentKey", "parent_key"):
        if pk_key in d:
            d[pk_key] = parent_key
            break
    else:
        d["parentKey"] = parent_key


def _transfer_child_text(
    source_page,
    target_data: dict,
    text_id: str,
    new_balloon_id: str,
    src_offset: tuple[float, float],
    dst_offset: tuple[float, float],
    target_parent_kind: str,
    target_parent_key: str,
    entries_to_remove: list[tuple[str, str]],
) -> str:
    """フキダシの子テキストをターゲットへ転送."""
    entry = _find_entry_in_page(source_page, "text", text_id)
    if entry is None:
        return ""
    entry_dict = _serialize_entry(entry, "text")
    if entry_dict is None:
        return ""
    entry_dict = _convert_coords(entry_dict, src_offset, dst_offset)
    _set_parent_in_dict(entry_dict, target_parent_kind, target_parent_key)
    entry_dict["parentBalloonId"] = new_balloon_id

    list_key = "texts"
    if list_key not in target_data:
        target_data[list_key] = []
    existing = _existing_ids_in_json(target_data, list_key)
    new_id = _unique_id(existing, text_id, "text")
    entry_dict["id"] = new_id

    target_data[list_key].append(entry_dict)
    entries_to_remove.append(("text", text_id))
    return new_id


def has_unsupported_layers(layer_items: list) -> bool:
    """選択に安全にページ間移動できない種別が含まれるか。"""
    return bool(unsupported_layer_kinds(layer_items))


def unsupported_layer_kinds(layer_items: list) -> set[str]:
    """選択レイヤーのうち未対応の種別一覧."""
    return {
        str(getattr(item, "kind", "") or "")
        for item in layer_items
        if str(getattr(item, "kind", "") or "") not in _SUPPORTED_KINDS
    }
