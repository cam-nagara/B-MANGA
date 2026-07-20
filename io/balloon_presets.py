"""カスタムフキダシ形状プリセット管理 (計画書 3.1.4.2b).

2 層:
- 同梱: <addon>/presets/balloons/
- 共通: Blender ユーザー設定配下の B-MANGA 共通プリセット

パスツールで作成した閉じた頂点列を JSON として保存。Phase 3 段階では
「選択中の BMangaBalloonEntry (shape 任意) の 4 頂点 + 形状パラメータ」
を単純保存する。ベジェ曲線登録は Phase 3 後半。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils import json_io, log, paths
from . import shared_presets

_logger = log.get_logger(__name__)

_ADDON_ROOT = Path(__file__).resolve().parent.parent
GLOBAL_BALLOONS_DIR = _ADDON_ROOT / "presets" / "balloons"

PRESET_SUFFIX = ".json"
PRESET_INDEX_FILENAME = "_preset_index.json"

LINKED_TEXT_SETTING_KEYS = {
    "linkedTextOffsetXMm": ("linked_text_offset_x_mm", 0.0),
    "linkedTextOffsetYMm": ("linked_text_offset_y_mm", 0.0),
    "linkedTextPaddingXMm": ("linked_text_padding_x_mm", 6.0),
    "linkedTextPaddingYMm": ("linked_text_padding_y_mm", 6.0),
}


def linked_text_settings_from_entry(entry) -> dict[str, float]:
    return {
        key: round(float(getattr(entry, attr, default) or 0.0), 3)
        for key, (attr, default) in LINKED_TEXT_SETTING_KEYS.items()
    }


def apply_linked_text_settings(entry, data: dict | None) -> None:
    payload = data if isinstance(data, dict) else {}
    for key, (attr, default) in LINKED_TEXT_SETTING_KEYS.items():
        value = float(payload.get(key, default))
        if "Padding" in key:
            value = max(0.0, value)
        setattr(entry, attr, value)


# ---------- スタイル (形状・線種・色など) の保存/適用 (計画書 2026-07-20) ----------
#
# BMangaBalloonEntry (core/balloon.py) のフラット属性名タプル。値は必ず
# core/balloon.py の実プロパティ定義と突き合わせて確認済み (存在しない
# 属性名を書くとプリセット保存/適用が静かに失敗する)。
# インスタンス固有 (配置・本文・meldex連携・しっぽ等) は含めない。
BALLOON_STYLE_KEYS = (
    # 形状
    "shape",
    "corner_type",
    "rounded_corner_enabled",
    "rounded_corner_radius_mm",
    "rounded_corner_radius_unit",
    "rounded_corner_radius_percent",
    # 線種・線幅
    "line_style",
    "line_width_mm",
    "dashed_segment_length_mm",
    "dashed_gap_mm",
    "dotted_gap_mm",
    "line_shape_kind",
    "line_shape_spacing_mm",
    "line_shape_angle_deg",
    "line_shape_orient",
    "line_shape_jitter",
    "line_shape_seed",
    "line_image_path",
    "line_image_interval_mm",
    "line_image_angle_deg",
    "line_image_jitter",
    # パス線 (2026-07-20 追加)。line_image_path / line_image_angle_deg は
    # 上の「線種「画像」用」の項目と共有 (core/balloon.py 参照)。
    "line_image_source",
    "line_image_shape_kind",
    "line_image_shape_sides",
    "line_image_draw_mode",
    "line_image_brush_size_mm",
    "line_image_aspect_ratio",
    "line_image_spacing_percent",
    "line_image_color",
    "line_image_ribbon_repeat_mode",
    "line_image_stamp_angle_mode",
    "line_image_stamp_angle_object_name",
    "line_image_inout_size_enabled",
    "line_image_inout_opacity_enabled",
    "line_image_inout_color_enabled",
    "line_image_inout_start_color",
    "line_image_inout_end_color",
    "multi_line_count",
    "multi_line_direction",
    "multi_line_width_mm",
    "multi_line_spacing_mm",
    "multi_line_width_scale_percent",
    "multi_line_spacing_scale_percent",
    "thorn_multi_line_valley_width_pct",
    "thorn_multi_line_peak_width_pct",
    "thorn_multi_line_length_scale_near_percent",
    "thorn_multi_line_length_scale_far_percent",
    "thorn_multi_line_cross_enabled",
    "line_valley_width_pct",
    "line_peak_width_pct",
    "flash_line_count",
    "flash_line_spacing_mm",
    # 色・塗り
    "line_color",
    "fill_color",
    "fill_opacity",
    "fill_material_name",
    "line_material_name",
    "line_material_mapping",
    "line_material_stretch_single",
    "line_material_seam_fix",
    # ボカシ・グラデーション
    "fill_blur_amount",
    "fill_blur_axis",
    "fill_blur_dither",
    "fill_gradient_enabled",
    "fill_gradient_start_color",
    "fill_gradient_end_color",
    "fill_gradient_angle_deg",
    # フチ
    "outer_white_margin_enabled",
    "outer_white_margin_width_mm",
    "outer_white_margin_color",
    "inner_white_margin_enabled",
    "inner_white_margin_width_mm",
    "inner_white_margin_color",
    # その他
    "blend_mode",
    "opacity",
)

# FloatVectorProperty(subtype="COLOR") のキー。list[float] へ変換して保存する。
_BALLOON_COLOR_KEYS = frozenset(
    {
        "line_color",
        "fill_color",
        "fill_gradient_start_color",
        "fill_gradient_end_color",
        "outer_white_margin_color",
        "inner_white_margin_color",
        "line_image_color",
        "line_image_inout_start_color",
        "line_image_inout_end_color",
    }
)

# BMangaBalloonShapeParams (entry.shape_params) のフラット属性名タプル。
# core/balloon.py に "Legacy parameters kept for older B-MANGA files" と
# 明記されている互換専用フィールドは新規保存の対象に含めない。
BALLOON_SHAPE_PARAM_KEYS = (
    "cloud_bump_width_mm",
    "cloud_bump_width_jitter",
    "cloud_bump_height_mm",
    "cloud_bump_height_jitter",
    "cloud_offset_percent",
    "shape_seed",
    "cloud_sub_width_ratio",
    "cloud_sub_width_jitter",
    "cloud_sub_height_ratio",
    "cloud_sub_height_jitter",
    "cloud_valley_sharp",
    "dynamic_shape_base_kind",
    "dynamic_base_rounded_corner_enabled",
    "dynamic_base_rounded_corner_radius_mm",
    "dynamic_base_rounded_corner_radius_unit",
    "dynamic_base_rounded_corner_radius_percent",
)


def _style_value_for_json(val: Any) -> Any:
    try:
        return round(float(val), 4) if isinstance(val, float) else val
    except (TypeError, ValueError):
        return str(val)


def snapshot_style_from_entry(entry) -> dict[str, Any]:
    """BMangaBalloonEntry から保存用のスタイル辞書 (形状・線種・色など) を作成.

    io/text_presets.py の ``snapshot_from_entry`` と同じパターン
    (getattr + float丸め + Color→list変換) に、shape_params のサブdict化と
    ウニフラ/白抜き用パラメータ (既存 core/balloon.py の
    ``uni_flash_params_to_dict``) を加えたもの。
    """

    snap: dict[str, Any] = {}
    for key in BALLOON_STYLE_KEYS:
        val = getattr(entry, key, None)
        if val is None:
            continue
        if key in _BALLOON_COLOR_KEYS:
            snap[key] = [round(float(c), 4) for c in val[:4]]
        else:
            snap[key] = _style_value_for_json(val)

    shape_params = getattr(entry, "shape_params", None)
    if shape_params is not None:
        params_snap: dict[str, Any] = {}
        for key in BALLOON_SHAPE_PARAM_KEYS:
            val = getattr(shape_params, key, None)
            if val is None:
                continue
            params_snap[key] = _style_value_for_json(val)
        snap["shape_params"] = params_snap

    from ..core import balloon as balloon_core

    snap["uni_flash_params"] = balloon_core.uni_flash_params_to_dict(entry)
    return snap


def apply_style_to_entry(entry, data: dict[str, Any] | None) -> None:
    """スタイル辞書を BMangaBalloonEntry へ適用.

    未知キーは無視 (旧プリセット・新旧アドオン間の後方互換)。Color系は
    list→Color変換、shape_params はサブdictから、ウニフラ/白抜きパラメータは
    既存の ``uni_flash_params_from_dict`` から、それぞれ適用する。
    """

    if not isinstance(data, dict):
        return
    for key in BALLOON_STYLE_KEYS:
        if key not in data:
            continue
        val = data[key]
        if key in _BALLOON_COLOR_KEYS:
            try:
                prop = getattr(entry, key)
                for i, c in enumerate(val[:4]):
                    prop[i] = float(c)
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                setattr(entry, key, val)
            except Exception:  # noqa: BLE001
                pass

    params_data = data.get("shape_params")
    shape_params = getattr(entry, "shape_params", None)
    if isinstance(params_data, dict) and shape_params is not None:
        for key in BALLOON_SHAPE_PARAM_KEYS:
            if key not in params_data:
                continue
            try:
                setattr(shape_params, key, params_data[key])
            except Exception:  # noqa: BLE001
                pass

    uni_flash_data = data.get("uni_flash_params")
    if isinstance(uni_flash_data, dict):
        from ..core import balloon as balloon_core

        balloon_core.uni_flash_params_from_dict(entry, uni_flash_data)


def reset_entry_style_to_defaults(entry) -> None:
    """プリセット対象のスタイル属性だけをRNA既定値へ戻す (配置・本文等は変更しない)."""

    properties = getattr(getattr(entry, "bl_rna", None), "properties", None)
    if properties is None:
        return
    for key in BALLOON_STYLE_KEYS:
        prop = properties.get(key)
        if prop is None or bool(getattr(prop, "is_readonly", False)):
            continue
        default = (
            getattr(prop, "default_array", None)
            if bool(getattr(prop, "is_array", False))
            else getattr(prop, "default", None)
        )
        try:
            setattr(entry, key, default)
        except (AttributeError, TypeError, ValueError):
            continue


@dataclass(frozen=True)
class BalloonPreset:
    name: str
    description: str
    path: Path
    source: str  # "global" | "user"
    data: dict[str, Any]


def _list_in_dir(base: Path, *, source: str) -> list[BalloonPreset]:
    if not base.is_dir():
        return []
    out: list[BalloonPreset] = []
    for path in sorted(base.glob(f"*{PRESET_SUFFIX}")):
        try:
            data = json_io.read_json(path)
        except (OSError, ValueError) as exc:
            _logger.warning("failed to read balloon preset %s: %s", path, exc)
            continue
        if data.get("presetType") != "balloon":
            continue
        name = data.get("presetName") or path.stem
        out.append(
            BalloonPreset(
                name=name,
                description=data.get("description", ""),
                path=path,
                source=source,
                data=data,
            )
        )
    return out


def list_global_presets() -> list[BalloonPreset]:
    return _list_in_dir(GLOBAL_BALLOONS_DIR, source="global")


def list_local_presets(work_dir: Path) -> list[BalloonPreset]:
    _migrate_work_presets(work_dir)
    return list_user_presets()


def list_user_presets() -> list[BalloonPreset]:
    return _list_in_dir(shared_presets.preset_dir("balloons"), source="user")


def list_all_presets(work_dir: Path | None) -> list[BalloonPreset]:
    presets = {p.name: p for p in list_global_presets()}
    if work_dir is not None:
        _migrate_work_presets(work_dir)
    index = _read_local_index()
    order = list(index.get("order", []))
    hidden = set(index.get("hidden", []))
    for p in list_user_presets():
        presets[p.name] = p
    visible = [p for p in presets.values() if p.name not in hidden]
    return _order_presets(visible, order)


def save_preset(
    out_path: Path,
    name: str,
    description: str,
    vertices_mm: list[tuple[float, float]],
    *,
    absolute_coords: bool = False,
    extras: dict | None = None,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        # v1 = 頂点 (+ リンクテキスト余白) のみ, v2 = 形状/線種/色などスタイル
        # 全般を含む (2026-07-20 拡張)。旧アドオンで v2 を読んでも未知キーは
        # 無視されるため安全 (「プリセット非対象」参照)。
        "schemaVersion": 2,
        "presetType": "balloon",
        "presetName": name,
        "description": description,
        "coordMode": "absolute" if absolute_coords else "relative",
        "vertices": [[round(x, 3), round(y, 3)] for x, y in vertices_mm],
    }
    if extras:
        data.update(extras)
    json_io.write_json(out_path, data)
    return out_path


def save_local_preset(
    work_dir: Path,
    name: str,
    description: str,
    vertices_mm: list[tuple[float, float]],
    absolute_coords: bool = False,
    *,
    extras: dict | None = None,
) -> Path:
    del work_dir
    target_dir = shared_presets.preset_dir("balloons")
    safe = _sanitize_filename(name)
    out = target_dir / f"{safe}{PRESET_SUFFIX}"
    is_new = not out.exists()
    result = save_preset(
        out,
        name,
        description,
        vertices_mm,
        absolute_coords=absolute_coords,
        extras=extras,
    )
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    if name in hidden:
        hidden.discard(name)
        index["hidden"] = list(hidden)
        _write_local_index(index)
    if is_new:
        _insert_order_name(name)
    return result


def save_global_preset(
    name: str,
    description: str,
    vertices_mm: list[tuple[float, float]],
    absolute_coords: bool = False,
    *,
    extras: dict | None = None,
) -> Path:
    """全作品共通プリセットとして保存する."""
    safe = _sanitize_filename(name)
    out = shared_presets.preset_dir("balloons") / f"{safe}{PRESET_SUFFIX}"
    is_new = not out.exists()
    result = save_preset(
        out,
        name,
        description,
        vertices_mm,
        absolute_coords=absolute_coords,
        extras=extras,
    )
    if is_new:
        _insert_order_name(name)
    return result


_FORBIDDEN = '<>:"/\\|?*'


def _sanitize_filename(name: str) -> str:
    cleaned = "".join("_" if ch in _FORBIDDEN else ch for ch in name.strip())
    return cleaned.rstrip(". ") or "preset"


# ---------- インデックス基盤 (並び順 / 非表示) ----------


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _local_dir() -> Path:
    return shared_presets.preset_dir("balloons")


def _local_index_path() -> Path:
    return _local_dir() / PRESET_INDEX_FILENAME


def _read_local_index() -> dict[str, Any]:
    path = _local_index_path()
    if not path.is_file():
        return {"order": [], "hidden": []}
    try:
        data = json_io.read_json(path)
    except (OSError, ValueError) as exc:
        _logger.warning("failed to read balloon preset index %s: %s", path, exc)
        return {"order": [], "hidden": []}
    if not isinstance(data, dict):
        return {"order": [], "hidden": []}
    order = _string_list(data.get("order", []))
    hidden = _string_list(data.get("hidden", []))
    return {"order": order, "hidden": hidden}


def _write_local_index(index: dict[str, Any]) -> None:
    target = _local_index_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    order = _string_list(index.get("order", []))
    hidden = _string_list(index.get("hidden", []))
    json_io.write_json(
        target,
        {
            "schemaVersion": 1,
            "order": list(dict.fromkeys(order)),
            "hidden": list(dict.fromkeys(hidden)),
        },
    )


def _default_order_key(preset: BalloonPreset) -> tuple[int, str]:
    return (
        0 if preset.source == "global" else 1,
        preset.path.name,
    )


def _order_presets(preset_list: list[BalloonPreset], order: list[str]) -> list[BalloonPreset]:
    if not order:
        return sorted(preset_list, key=_default_order_key)
    order_pos = {name: i for i, name in enumerate(order)}
    return sorted(
        preset_list,
        key=lambda p: (
            0 if p.name in order_pos else 1,
            order_pos.get(p.name, 0),
            *_default_order_key(p),
        ),
    )


def _safe_local_path(name: str) -> Path:
    return _local_dir() / f"{_sanitize_filename(name)}{PRESET_SUFFIX}"


def _global_preset_by_name(name: str) -> BalloonPreset | None:
    for preset in list_global_presets():
        if preset.name == name:
            return preset
    return None


def _local_preset_by_name(name: str) -> BalloonPreset | None:
    for preset in _list_in_dir(_local_dir(), source="user"):
        if preset.name == name:
            return preset
    return None


def _visible_order_names() -> list[str]:
    return [preset.name for preset in list_all_presets(None)]


def _insert_order_name(name: str, *, after_name: str = "") -> None:
    index = _read_local_index()
    order = [item for item in _visible_order_names() if item != name]
    if after_name and after_name in order:
        order.insert(order.index(after_name) + 1, name)
    else:
        order.append(name)
    index["order"] = order
    _write_local_index(index)


def _write_local_preset_data(
    data: dict[str, Any],
    name: str,
    *,
    description: str = "",
) -> Path:
    target_dir = _local_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    existing = _local_preset_by_name(name)
    out = existing.path if existing is not None else _safe_local_path(name)
    if existing is None and out.exists():
        raise ValueError(f"同じファイル名のプリセットが既にあります: {out.name}")
    payload = copy.deepcopy(data)
    payload["presetType"] = "balloon"
    payload["presetName"] = name
    if description:
        payload["description"] = description
    json_io.write_json(out, payload)
    return out


# ---------- CRUD ----------


def preset_name_exists(name: str) -> bool:
    return any(p.name == name for p in list_all_presets(None))


def unique_preset_name(base: str = "新規フキダシプリセット") -> str:
    base = (base or "新規フキダシプリセット").strip() or "新規フキダシプリセット"
    if not preset_name_exists(base):
        return base
    for i in range(2, 1000):
        candidate = f"{base} {i:03d}"
        if not preset_name_exists(candidate):
            return candidate
    return base


def load_preset_by_name(name: str) -> BalloonPreset | None:
    for preset in list_all_presets(None):
        if preset.name == name:
            return preset
    return None


def rename_preset(old_name: str, new_name: str) -> BalloonPreset:
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not old_name or not new_name:
        raise ValueError("プリセット名が空です")
    if old_name == new_name:
        preset = load_preset_by_name(old_name)
        if preset is None:
            raise ValueError(f"プリセットが見つかりません: {old_name}")
        return preset
    if preset_name_exists(new_name):
        raise ValueError(f"同名のプリセットが既にあります: {new_name}")
    preset = load_preset_by_name(old_name)
    if preset is None:
        raise ValueError(f"プリセットが見つかりません: {old_name}")

    index = _read_local_index()
    order = _visible_order_names()
    hidden = set(index.get("hidden", []))
    if preset.source == "global":
        hidden.add(old_name)
    hidden.discard(new_name)
    out = _write_local_preset_data(preset.data, new_name)
    if preset.source == "user" and preset.path != out:
        try:
            preset.path.unlink()
        except FileNotFoundError:
            pass
    order = [new_name if item == old_name else item for item in order]
    if new_name not in order:
        order.append(new_name)
    index["hidden"] = list(hidden)
    index["order"] = order
    _write_local_index(index)
    result = _local_preset_by_name(new_name)
    if result is None:
        raise ValueError(f"プリセットの改名に失敗しました: {new_name}")
    return result


def duplicate_preset(source_name: str, new_name: str) -> BalloonPreset:
    source_name = source_name.strip()
    new_name = new_name.strip()
    if not source_name or not new_name:
        raise ValueError("プリセット名が空です")
    if preset_name_exists(new_name):
        raise ValueError(f"同名のプリセットが既にあります: {new_name}")
    preset = load_preset_by_name(source_name)
    if preset is None:
        raise ValueError(f"プリセットが見つかりません: {source_name}")
    _write_local_preset_data(preset.data, new_name)
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    if new_name in hidden:
        hidden.discard(new_name)
        index["hidden"] = list(hidden)
        _write_local_index(index)
    _insert_order_name(new_name, after_name=source_name)
    result = _local_preset_by_name(new_name)
    if result is None:
        raise ValueError(f"プリセットの複製に失敗しました: {new_name}")
    return result


def delete_preset(name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("プリセット名が空です")
    preset = load_preset_by_name(name)
    if preset is None:
        raise ValueError(f"プリセットが見つかりません: {name}")
    index = _read_local_index()
    hidden = set(index.get("hidden", []))
    local = _local_preset_by_name(name)
    if local is not None:
        try:
            local.path.unlink()
        except FileNotFoundError:
            pass
    if preset.source == "global" or _global_preset_by_name(name) is not None:
        hidden.add(name)
    index["hidden"] = list(hidden)
    index["order"] = [item for item in _visible_order_names() if item != name]
    _write_local_index(index)


def move_preset(name: str, direction: str) -> list[str]:
    name = name.strip()
    order = _visible_order_names()
    if name not in order:
        raise ValueError(f"プリセットが見つかりません: {name}")
    index = order.index(name)
    if direction == "UP":
        new_index = max(0, index - 1)
    elif direction == "DOWN":
        new_index = min(len(order) - 1, index + 1)
    else:
        raise ValueError(f"不明な移動方向です: {direction}")
    if new_index != index:
        order.insert(new_index, order.pop(index))
    preset_index = _read_local_index()
    preset_index["order"] = order
    _write_local_index(preset_index)
    return order


def _migrate_work_presets(work_dir: Path | None) -> None:
    if work_dir is None:
        return
    legacy_dir = paths.assets_dir(Path(work_dir)) / paths.ASSETS_BALLOONS_DIR
    shared_presets.copy_json_presets_once(legacy_dir, shared_presets.preset_dir("balloons"))
