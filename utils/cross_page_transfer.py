"""ページファイル間のレイヤー移動 (クロスファイル転送).

ページファイル (ROLE_PAGE) 上で Alt+ドラッグにより選択レイヤーを
別ページのプレビュー上にドロップしたとき、ソースページの JSON から
エントリーを取り出し、ターゲットページの page.json へ書き込む。

対応レイヤー種別: balloon, text, image, raster, fill, effect
効果線はパラメータ JSON をステージングファイルに書き出し、ターゲット
ページの読込時に自動生成する。GP (手描きストローク) は未対応。
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import bpy

from . import json_io, log, page_grid, paths
from .layer_hierarchy import split_child_key

_logger = log.get_logger(__name__)

_STAGED_IMPORTS_NAME = "_staged_imports.json"

# gp_folder は構造コンテナのみのため単体転送対象外
_UNSUPPORTED_KINDS = frozenset({"gp_folder"})


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


def _copy_raster_image(
    work_dir: Path,
    src_page_id: str,
    target_page_id: str,
    entry_dict: dict,
) -> bool:
    """ラスター画像ファイルをターゲットページディレクトリへコピー."""
    rel = entry_dict.get("filepath_rel", "")
    if not rel:
        return True
    src_base = paths.page_dir(work_dir, src_page_id)
    dst_base = paths.page_dir(work_dir, target_page_id)
    src_file = src_base / rel
    dst_file = dst_base / rel
    if dst_file.is_file():
        return True
    if not src_file.is_file():
        _logger.error("raster source missing: %s", src_file)
        return False
    if src_file.is_file() and not dst_file.is_file():
        try:
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)
        except Exception:  # noqa: BLE001
            _logger.exception("raster copy failed: %s -> %s", src_file, dst_file)
            return False
    return True


# ---------- GP ストロークシリアライズ ----------


def _serialize_gp_material(mat) -> dict:
    """GP マテリアルの色情報を dict にシリアライズ."""
    gp_style = getattr(mat, "grease_pencil", None) if mat else None
    if gp_style is None:
        return {"color": [0.0, 0.0, 0.0, 1.0], "show_stroke": True, "show_fill": False}
    color = list(getattr(gp_style, "color", (0.0, 0.0, 0.0, 1.0)))
    fill_color = list(getattr(gp_style, "fill_color", (0.0, 0.0, 0.0, 0.0)))
    return {
        "color": [round(c, 6) for c in color],
        "fill_color": [round(c, 6) for c in fill_color],
        "show_stroke": bool(getattr(gp_style, "show_stroke", True)),
        "show_fill": bool(getattr(gp_style, "show_fill", False)),
    }


def _serialize_gp_stroke(stroke, offset: int = 0) -> dict:
    """1 ストロークを dict にシリアライズ."""
    points = []
    drawing = None
    for pt in getattr(stroke, "points", []):
        pos = tuple(getattr(pt, "position", (0, 0, 0)))
        r = float(getattr(pt, "radius", 0.01))
        o = float(getattr(pt, "opacity", 1.0))
        point_data = {"pos": [round(v, 6) for v in pos], "r": round(r, 6), "o": round(o, 4)}
        hl = getattr(pt, "handle_left", None)
        hr = getattr(pt, "handle_right", None)
        if hl is not None:
            point_data["hl"] = [round(v, 6) for v in tuple(hl)]
        if hr is not None:
            point_data["hr"] = [round(v, 6) for v in tuple(hr)]
        points.append(point_data)
    return {
        "points": points,
        "cyclic": bool(getattr(stroke, "cyclic", False)),
        "material_index": int(getattr(stroke, "material_index", 0)),
    }


def _serialize_gp_object(bmanga_id: str) -> dict | None:
    """GP オブジェクトの全ストロークデータを dict にシリアライズ."""
    from .object_naming import find_object_by_bmanga_id, PROP_ID, PROP_KIND

    obj = find_object_by_bmanga_id(bmanga_id, kind="gp")
    if obj is None:
        return None
    gp_data = getattr(obj, "data", None)
    if gp_data is None:
        return None

    # オブジェクトレベルのプロパティ
    result = {
        "bmanga_id": str(obj.get(PROP_ID, "") or ""),
        "title": str(obj.get("bmanga_title", "") or ""),
        "z_index": int(obj.get("bmanga_z_index", 0) or 0),
        "folder_id": str(obj.get("bmanga_folder_id", "") or ""),
    }

    # マテリアルのシリアライズ
    materials = []
    for mat in getattr(gp_data, "materials", []):
        if mat is not None:
            materials.append({"name": mat.name, **_serialize_gp_material(mat)})
        else:
            materials.append(None)
    result["materials"] = materials

    # レイヤーとストロークのシリアライズ
    layers_data = []
    for layer in getattr(gp_data, "layers", []):
        layer_info = {
            "name": str(getattr(layer, "name", "content")),
            "opacity": float(getattr(layer, "opacity", 1.0)),
            "blend_mode": str(getattr(layer, "blend_mode", "REGULAR")),
            "hide": bool(getattr(layer, "hide", False)),
            "lock": bool(getattr(layer, "lock", False)),
        }
        frames_data = []
        for frame in getattr(layer, "frames", []):
            drawing = getattr(frame, "drawing", None)
            if drawing is None:
                continue
            strokes_data = []
            for stroke in getattr(drawing, "strokes", []):
                strokes_data.append(_serialize_gp_stroke(stroke))
            frames_data.append({
                "frame_number": int(getattr(frame, "frame_number", 0)),
                "strokes": strokes_data,
            })
        layer_info["frames"] = frames_data
        layers_data.append(layer_info)
    result["layers"] = layers_data
    return result


def _create_gp_from_data(
    context,
    gp_data_dict: dict,
    parent_key: str,
) -> bpy.types.Object | None:
    """シリアライズされた dict から GP オブジェクトを再構築."""
    from . import gp_object_layer
    from . import gpencil as gp_utils

    title = gp_data_dict.get("title", "GP Layer")
    z_index = int(gp_data_dict.get("z_index", 200))
    folder_id = gp_data_dict.get("folder_id", "")

    parent_kind = "page"
    if parent_key and ":" in parent_key:
        parent_kind = "coma"

    # 新しい bmanga_id を生成 (衝突回避)
    import secrets
    new_id = f"gp_{secrets.token_hex(6)}"

    obj = gp_object_layer.create_layer_gp_object(
        scene=context.scene,
        bmanga_id=new_id,
        title=title,
        z_index=z_index,
        parent_kind=parent_kind,
        parent_key=parent_key,
        folder_id=folder_id,
    )
    if obj is None:
        return None
    gp_data = getattr(obj, "data", None)
    if gp_data is None:
        return None

    # マテリアルの再構築 (create_layer_gp_object が追加した既定スロットをクリアし
    # ソースと同じスロット順序で再追加。material_index の整合性を保証する)
    try:
        gp_data.materials.clear()
    except Exception:  # noqa: BLE001
        pass
    materials_data = gp_data_dict.get("materials", [])
    for mat_info in materials_data:
        if mat_info is None:
            gp_data.materials.append(None)
            continue
        mat_name = mat_info.get("name", "")
        color = tuple(mat_info.get("color", (0, 0, 0, 1)))
        fill_color = tuple(mat_info.get("fill_color", (0, 0, 0, 0)))
        show_stroke = mat_info.get("show_stroke", True)
        show_fill = mat_info.get("show_fill", False)
        mat = bpy.data.materials.get(mat_name)
        if mat is None:
            mat = bpy.data.materials.new(name=mat_name)
        gp_style = getattr(mat, "grease_pencil", None)
        if gp_style is None:
            try:
                bpy.data.materials.create_gpencil_data(mat)
            except (AttributeError, RuntimeError):
                pass
            gp_style = getattr(mat, "grease_pencil", None)
        if gp_style is not None:
            try:
                gp_style.show_stroke = show_stroke
                gp_style.color = color
                gp_style.show_fill = show_fill
                gp_style.fill_color = fill_color
            except Exception:  # noqa: BLE001
                pass
        try:
            gp_data.materials.append(mat)
        except Exception:  # noqa: BLE001
            pass

    # レイヤーとストロークの再構築
    layers_data = gp_data_dict.get("layers", [])
    # 既存の "content" レイヤーを削除（新しいデータで上書き）
    while len(gp_data.layers) > 0:
        try:
            gp_data.layers.remove(gp_data.layers[0])
        except Exception:  # noqa: BLE001
            break

    for layer_info in layers_data:
        layer_name = layer_info.get("name", "content")
        layer = gp_data.layers.new(layer_name)
        try:
            layer.opacity = float(layer_info.get("opacity", 1.0))
        except Exception:  # noqa: BLE001
            pass
        blend_mode = layer_info.get("blend_mode", "REGULAR")
        try:
            layer.blend_mode = blend_mode
        except Exception:  # noqa: BLE001
            pass
        try:
            layer.hide = bool(layer_info.get("hide", False))
            layer.lock = bool(layer_info.get("lock", False))
        except Exception:  # noqa: BLE001
            pass

        for frame_info in layer_info.get("frames", []):
            frame_num = int(frame_info.get("frame_number", 0))
            frame = gp_utils.ensure_active_frame(layer, frame_number=frame_num)
            if frame is None:
                continue
            drawing = getattr(frame, "drawing", None)
            if drawing is None:
                continue

            for stroke_data in frame_info.get("strokes", []):
                pts = stroke_data.get("points", [])
                if not pts:
                    continue
                positions = [tuple(p["pos"]) for p in pts]
                radii = [float(p.get("r", 0.01)) for p in pts]
                opacities = [float(p.get("o", 1.0)) for p in pts]
                gp_utils.add_stroke_to_drawing(
                    drawing,
                    positions,
                    radii=radii,
                    opacities=opacities,
                    cyclic=stroke_data.get("cyclic", False),
                    material_index=stroke_data.get("material_index", 0),
                )
    return obj


def _remove_gp_objects(bmanga_id: str) -> None:
    """GP オブジェクトと関連オブジェクトを削除."""
    from .object_naming import find_object_by_bmanga_id

    obj = find_object_by_bmanga_id(bmanga_id, kind="gp")
    if obj is None:
        return
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        pass


# ---------- 効果線ステージング ----------


def _extract_effect_meta(bmanga_id: str) -> dict | None:
    """効果線 GP オブジェクトからメタデータ (bounds + params) を抽出."""
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
            return entry
    return None


def _stage_effect_import(
    work_dir: Path,
    target_page_id: str,
    effect_data: dict,
) -> bool:
    """効果線データをターゲットページのステージングファイルに追加."""
    staged_path = paths.page_dir(work_dir, target_page_id) / _STAGED_IMPORTS_NAME
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = json_io.read_json(staged_path) if staged_path.is_file() else {}
    except Exception:  # noqa: BLE001
        existing = {}
    if not isinstance(existing, dict):
        existing = {}
    effects = existing.get("effects", [])
    if not isinstance(effects, list):
        effects = []
    effects.append(effect_data)
    existing["effects"] = effects
    try:
        json_io.write_json(staged_path, existing)
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("staged import write failed: %s", staged_path)
        return False


def _remove_effect_objects(bmanga_id: str) -> None:
    """効果線の関連 Blender オブジェクトを全て削除."""
    from .object_naming import PROP_ID, find_object_by_bmanga_id

    obj = find_object_by_bmanga_id(bmanga_id, kind="effect")
    if obj is None:
        return
    controller_id = str(obj.get(PROP_ID, "") or "")
    objs_to_remove = [obj]
    for o in bpy.data.objects:
        if str(o.get("bmanga_effect_controller_id", "") or "") == controller_id:
            objs_to_remove.append(o)
    for o in objs_to_remove:
        try:
            bpy.data.objects.remove(o, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass


def process_staged_imports(context) -> int:
    """ページ load_post で呼ばれ、ステージングファイルの効果線・GP を生成する."""
    from ..core.work import get_work
    from . import page_file_scene

    role, page_id, _ = page_file_scene.current_role(context)
    if role != page_file_scene.ROLE_PAGE or not page_id:
        return 0
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return 0
    wd = _work_dir(work)
    if wd is None:
        return 0
    staged_path = paths.page_dir(wd, page_id) / _STAGED_IMPORTS_NAME
    if not staged_path.is_file():
        return 0
    try:
        data = json_io.read_json(staged_path)
    except Exception:  # noqa: BLE001
        _logger.exception("staged import read failed: %s", staged_path)
        return 0
    if not isinstance(data, dict):
        return 0
    effects = data.get("effects", [])
    gp_layers = data.get("gp_layers", [])
    if not effects and not gp_layers:
        try:
            staged_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return 0
    created = 0

    # ── 効果線の復元 ──
    if effects:
        from ..core import effect_line as effect_line_core
        for entry in effects:
            if not isinstance(entry, dict):
                continue
            params_dict = entry.get("params")
            if not isinstance(params_dict, dict):
                continue
            bounds = (
                float(entry.get("x", 70)),
                float(entry.get("y", 110)),
                float(entry.get("w", 80)),
                float(entry.get("h", 100)),
            )
            center = entry.get("center_xy_mm")
            seed = entry.get("seed")
            parent_key = str(entry.get("parent_key", page_id) or page_id)
            scene_params = getattr(context.scene, "bmanga_effect_line_params", None)
            if scene_params is None:
                continue
            effect_line_core.effect_params_from_dict(scene_params, params_dict)
            try:
                from ..operators import effect_line_op

                obj, layer = effect_line_op._create_effect_layer(
                    context, bounds, parent_key=parent_key,
                )
                if obj is not None and layer is not None:
                    effect_line_op._write_effect_strokes(
                        context, obj, layer, bounds,
                        seed=int(seed) if seed is not None else None,
                        center_xy_mm=tuple(center) if center else None,
                    )
                    created += 1
            except Exception:  # noqa: BLE001
                _logger.exception("staged effect creation failed")

    # ── GP ストロークの復元 ──
    if gp_layers:
        from . import layer_stack as layer_stack_utils
        for gp_entry in gp_layers:
            if not isinstance(gp_entry, dict):
                continue
            parent_key = str(gp_entry.get("parent_key", page_id) or page_id)
            try:
                obj = _create_gp_from_data(context, gp_entry, parent_key)
                if obj is not None:
                    created += 1
            except Exception:  # noqa: BLE001
                _logger.exception("staged GP creation failed")
        if created > 0:
            try:
                layer_stack_utils.sync_layer_stack_after_data_change(context)
            except Exception:  # noqa: BLE001
                pass

    try:
        staged_path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return created


# ---------- 公開 API ----------


def transfer_layers_to_page(
    context,
    work,
    source_page,
    target_page_id: str,
    layer_items: list,
    *,
    target_parent_kind: str = "page",
    target_coma_id: str = "",
    drop_world_xy_mm: tuple[float, float] | None = None,
) -> int:
    """選択レイヤーをターゲットページの page.json へ転送する.

    Args:
        context: Blender context
        work: B-MANGA work PropertyGroup
        source_page: ソースページ PropertyGroup
        target_page_id: ターゲットページ ID (e.g. "p0002")
        layer_items: 移動対象のレイヤースタックアイテム
        target_parent_kind: "page" or "coma"
        target_coma_id: target_parent_kind == "coma" のときのコマ ID
        drop_world_xy_mm: ドロップ座標 (ワールド mm)。指定時にコマ自動解決

    Returns:
        移動したエントリー数
    """
    wd = _work_dir(work)
    if wd is None:
        return 0

    src_page_id = str(getattr(source_page, "id", "") or "")
    if not src_page_id or not target_page_id:
        return 0
    if src_page_id == target_page_id:
        return 0

    scene = getattr(context, "scene", None)
    src_page_idx = -1
    for i, p in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(p, "id", "") or "") == src_page_id:
            src_page_idx = i
            break
    if src_page_idx < 0:
        return 0

    src_offset = _page_offset_mm(work, scene, src_page_idx)
    dst_offset = _target_page_offset_mm(work, scene, target_page_id)

    # ターゲット page.json 読込
    target_data = _read_target_page_json(wd, target_page_id)
    if target_data is None:
        _logger.warning("target page.json not found: %s", target_page_id)
        return 0

    # ドロップ座標からコマ自動解決
    if drop_world_xy_mm is not None and not target_coma_id:
        local_x = drop_world_xy_mm[0] - dst_offset[0]
        local_y = drop_world_xy_mm[1] - dst_offset[1]
        resolved_coma = _resolve_coma_from_json(target_data, local_x, local_y)
        if resolved_coma:
            target_parent_kind = "coma"
            target_coma_id = resolved_coma

    target_parent_key = target_page_id
    if target_parent_kind == "coma" and target_coma_id:
        target_parent_key = f"{target_page_id}:{target_coma_id}"

    transferred = 0
    entries_to_remove: list[tuple[str, str]] = []  # (kind, id)
    balloon_id_map: dict[str, str] = {}  # old_id -> new_id

    # ── パス 1: フキダシとその他非テキストレイヤーを転送 ──
    for item in layer_items:
        kind = str(getattr(item, "kind", "") or "")
        if kind in _UNSUPPORTED_KINDS:
            continue
        if kind == "text":
            continue  # テキストはパス 2 で処理

        _page_part, entry_id = split_child_key(str(getattr(item, "key", "") or ""))
        if not entry_id:
            continue

        entry = _find_entry_in_page(source_page, kind, entry_id)
        if entry is None:
            continue

        entry_dict = _serialize_entry(entry, kind)
        if entry_dict is None:
            continue

        entry_dict = _convert_coords(entry_dict, src_offset, dst_offset)
        _set_parent_in_dict(entry_dict, target_parent_kind, target_parent_key)

        list_key = _json_list_key(kind)
        if list_key is None:
            continue

        if list_key not in target_data:
            target_data[list_key] = []

        existing = _existing_ids_in_json(target_data, list_key)
        new_id = _unique_id(existing, entry_id, kind)
        entry_dict["id"] = new_id

        if kind == "balloon":
            balloon_id_map[entry_id] = new_id
            child_text_ids = _collect_child_text_ids(source_page, entry_id)
            for text_id in child_text_ids:
                _transfer_child_text(
                    source_page, target_data, text_id, new_id,
                    src_offset, dst_offset,
                    target_parent_kind, target_parent_key,
                    entries_to_remove,
                )

        if kind == "raster":
            if not _copy_raster_image(wd, src_page_id, target_page_id, entry_dict):
                return 0

        target_data[list_key].append(entry_dict)
        entries_to_remove.append((kind, entry_id))
        transferred += 1

    # ── パス 2: 独立テキスト (親フキダシなし or 既に移動済み) を転送 ──
    for item in layer_items:
        kind = str(getattr(item, "kind", "") or "")
        if kind != "text":
            continue

        _page_part, entry_id = split_child_key(str(getattr(item, "key", "") or ""))
        if not entry_id:
            continue
        if any(eid == entry_id and ekind == "text" for ekind, eid in entries_to_remove):
            continue

        entry = _find_entry_in_page(source_page, "text", entry_id)
        if entry is None:
            continue

        entry_dict = _serialize_entry(entry, "text")
        if entry_dict is None:
            continue

        entry_dict = _convert_coords(entry_dict, src_offset, dst_offset)
        _set_parent_in_dict(entry_dict, target_parent_kind, target_parent_key)

        parent_bid = entry_dict.get("parentBalloonId", "")
        if parent_bid and parent_bid in balloon_id_map:
            entry_dict["parentBalloonId"] = balloon_id_map[parent_bid]

        list_key = "texts"
        if list_key not in target_data:
            target_data[list_key] = []
        existing = _existing_ids_in_json(target_data, list_key)
        new_id = _unique_id(existing, entry_id, "text")
        entry_dict["id"] = new_id

        target_data[list_key].append(entry_dict)
        entries_to_remove.append(("text", entry_id))
        transferred += 1

    # ── パス 3: 効果線をステージング方式で転送 ──
    effects_staged = 0
    for item in layer_items:
        kind = str(getattr(item, "kind", "") or "")
        if kind != "effect":
            continue
        _page_part, bmanga_id = split_child_key(str(getattr(item, "key", "") or ""))
        if not bmanga_id:
            continue
        effect_meta = _extract_effect_meta(bmanga_id)
        if effect_meta is None:
            continue
        effect_data = copy.deepcopy(effect_meta)
        # 座標変換 (ソースページローカル → ターゲットページローカル)
        dx = src_offset[0] - dst_offset[0]
        dy = src_offset[1] - dst_offset[1]
        effect_data["x"] = float(effect_data.get("x", 0)) + dx
        effect_data["y"] = float(effect_data.get("y", 0)) + dy
        if "center_x" in effect_data and "center_y" in effect_data:
            effect_data["center_x"] = float(effect_data["center_x"]) + dx
            effect_data["center_y"] = float(effect_data["center_y"]) + dy
            effect_data["center_xy_mm"] = [effect_data["center_x"], effect_data["center_y"]]
        effect_data["parent_key"] = target_parent_key
        if _stage_effect_import(wd, target_page_id, effect_data):
            _remove_effect_objects(bmanga_id)
            entries_to_remove.append(("effect", bmanga_id))
            effects_staged += 1
            transferred += 1

    # ── パス 4: GP 手描きストロークをステージング方式で転送 ──
    _staged_kinds = {"effect", "gp"}
    for item in layer_items:
        kind = str(getattr(item, "kind", "") or "")
        if kind != "gp":
            continue
        _page_part, bmanga_id = split_child_key(str(getattr(item, "key", "") or ""))
        if not bmanga_id:
            continue
        gp_data = _serialize_gp_object(bmanga_id)
        if gp_data is None:
            continue
        # ストローク座標変換 (ワールド mm → ターゲットページローカル不要:
        # GP は mm_to_m 済みのワールド座標なので、ページ原点差分を m で適用)
        from .page_grid import mm_to_m
        dx_m = mm_to_m(src_offset[0] - dst_offset[0])
        dy_m = mm_to_m(src_offset[1] - dst_offset[1])
        for layer_data in gp_data.get("layers", []):
            for frame_data in layer_data.get("frames", []):
                for stroke_data in frame_data.get("strokes", []):
                    for pt in stroke_data.get("points", []):
                        pos = pt.get("pos", [0, 0, 0])
                        pos[0] = round(pos[0] + dx_m, 6)
                        pos[1] = round(pos[1] + dy_m, 6)
                        if "hl" in pt:
                            pt["hl"][0] = round(pt["hl"][0] + dx_m, 6)
                            pt["hl"][1] = round(pt["hl"][1] + dy_m, 6)
                        if "hr" in pt:
                            pt["hr"][0] = round(pt["hr"][0] + dx_m, 6)
                            pt["hr"][1] = round(pt["hr"][1] + dy_m, 6)
        gp_data["parent_key"] = target_parent_key
        staged_path = paths.page_dir(wd, target_page_id) / _STAGED_IMPORTS_NAME
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = json_io.read_json(staged_path) if staged_path.is_file() else {}
        except Exception:  # noqa: BLE001
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        gp_list = existing.get("gp_layers", [])
        if not isinstance(gp_list, list):
            gp_list = []
        gp_list.append(gp_data)
        existing["gp_layers"] = gp_list
        try:
            json_io.write_json(staged_path, existing)
            _remove_gp_objects(bmanga_id)
            entries_to_remove.append(("gp", bmanga_id))
            transferred += 1
        except Exception:  # noqa: BLE001
            _logger.exception("GP staging failed: %s", bmanga_id)

    if transferred == 0:
        return 0

    # ターゲット page.json 書込
    has_json_entries = any(k not in _staged_kinds for k, _ in entries_to_remove)
    if has_json_entries:
        if not _write_target_page_json(wd, target_page_id, target_data):
            return 0

    # ソースページからエントリーを削除
    for kind, entry_id in entries_to_remove:
        if kind in _staged_kinds:
            continue  # ステージング系は既にオブジェクト削除済み
        _remove_entry_from_page(source_page, kind, entry_id)

    # ソースページの page.json を保存
    if has_json_entries:
        try:
            from ..io import page_io
            page_io.save_page_json(wd, source_page)
        except Exception:  # noqa: BLE001
            _logger.exception("source page.json save failed")
            return 0

    return transferred


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
) -> None:
    """フキダシの子テキストをターゲットへ転送."""
    entry = _find_entry_in_page(source_page, "text", text_id)
    if entry is None:
        return
    entry_dict = _serialize_entry(entry, "text")
    if entry_dict is None:
        return
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


def has_unsupported_layers(layer_items: list) -> bool:
    """選択レイヤーに未対応の種別 (GP 等) が含まれるか."""
    return any(
        str(getattr(item, "kind", "") or "") in _UNSUPPORTED_KINDS
        for item in layer_items
    )


def unsupported_layer_kinds(layer_items: list) -> set[str]:
    """選択レイヤーのうち未対応の種別一覧."""
    return {
        str(getattr(item, "kind", "") or "")
        for item in layer_items
        if str(getattr(item, "kind", "") or "") in _UNSUPPORTED_KINDS
    }
