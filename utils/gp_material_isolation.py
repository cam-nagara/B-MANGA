"""管理対象の手描きレイヤーが使う材質を Object ごとに分離する。"""

from __future__ import annotations

import bpy

from . import log

_logger = log.get_logger(__name__)

MATERIAL_OWNER_PROP = "bmanga_gp_material_owner_id"
_LAYER_MATERIAL_PROP = "bmanga_material_name"
# GP v3 レイヤーは ID プロパティを持てないため、対応表は Object 側へ置く。
OBJECT_MATERIAL_MAP_PROP = "bmanga_layer_material_names"
_OBJECT_MATERIAL_MAP_PROP = OBJECT_MATERIAL_MAP_PROP


def _safe_suffix(name: str) -> str:
    cleaned = "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in str(name))
    cleaned = cleaned.strip().strip(".")
    return cleaned or "Layer"


def material_owner_id(obj) -> str:
    try:
        stable_id = str(obj.get("bmanga_id", "") or "").strip()
    except Exception:  # noqa: BLE001
        stable_id = ""
    return stable_id or str(getattr(obj, "name_full", "") or getattr(obj, "name", "") or "gp")


def _private_material_name(obj, material) -> str:
    owner = _safe_suffix(material_owner_id(obj))
    base = _safe_suffix(getattr(material, "name", "") or "BManga_GP_Material")
    return f"{base}__{owner}"


def _material_used_by_other_object(obj, material) -> bool:
    for candidate in bpy.data.objects:
        if candidate is obj:
            continue
        slots = getattr(getattr(candidate, "data", None), "materials", None)
        if slots is not None and any(existing is material for existing in slots):
            return True
    return False


def _refresh_layer_material_names(obj, gp_data, renamed: dict[str, str]) -> None:
    """材質複製で改名が起きた際、レイヤー→マテリアル名の対応表を追従させる.

    GP v3 レイヤーは ID プロパティを保持できないため、正となる対応表は Object
    側 (``bmanga_layer_material_names``) にある。旧ファイル互換でレイヤー側も
    可能なら更新する。
    """
    if not renamed:
        return
    mapping = {}
    try:
        stored = obj.get(_OBJECT_MATERIAL_MAP_PROP, None)
        if stored is not None:
            mapping = {str(k): str(v) for k, v in dict(stored).items()}
    except Exception:  # noqa: BLE001
        mapping = {}
    changed = False
    for key, old_name in list(mapping.items()):
        new_name = renamed.get(old_name)
        if new_name:
            mapping[key] = new_name
            changed = True
    if changed:
        try:
            obj[_OBJECT_MATERIAL_MAP_PROP] = mapping
        except Exception:  # noqa: BLE001
            _logger.exception("layer material map refresh failed: %s", getattr(obj, "name", ""))
    for layer in getattr(gp_data, "layers", ()):
        try:
            old_name = str(layer.get(_LAYER_MATERIAL_PROP, "") or "")
        except Exception:  # noqa: BLE001
            # GP v3 レイヤーは ID プロパティ非対応。Object 側の対応表が正となる。
            continue
        new_name = renamed.get(old_name)
        if not new_name:
            continue
        try:
            layer[_LAYER_MATERIAL_PROP] = new_name
        except Exception:  # noqa: BLE001
            pass


def ensure_unique_object_materials(obj) -> int:
    """管理GP Objectの材質を他Objectと共有しない状態へ正規化する。"""

    if obj is None or getattr(obj, "type", "") != "GREASEPENCIL":
        return 0
    try:
        managed = bool(obj.get("bmanga_managed", False))
        is_gp = str(obj.get("bmanga_kind", "") or "") == "gp"
        if not managed or not is_gp:
            return 0
    except Exception:  # noqa: BLE001
        return 0
    data = getattr(obj, "data", None)
    if data is None:
        return 0
    active_material_index = int(getattr(obj, "active_material_index", 0) or 0)
    try:
        real_users = int(getattr(data, "users", 1) or 1)
        if bool(getattr(data, "use_fake_user", False)):
            real_users -= 1
        if real_users > 1 or getattr(data, "library", None) is not None:
            obj.data = data.copy()
            data = obj.data
    except Exception:  # noqa: BLE001
        _logger.exception("managed GP data isolation failed: %s", getattr(obj, "name", ""))
        return 0
    slots = getattr(data, "materials", None)
    if slots is None:
        return 0

    owner_id = material_owner_id(obj)
    replacements: dict[int, object] = {}
    renamed: dict[str, str] = {}
    copied = 0
    for index in range(len(slots)):
        material = slots[index]
        if material is None:
            continue
        try:
            pointer = int(material.as_pointer())
        except Exception:  # noqa: BLE001
            pointer = id(material)
        replacement = replacements.get(pointer)
        if replacement is None:
            if _material_used_by_other_object(obj, material) or getattr(material, "library", None) is not None:
                replacement = material.copy()
                replacement.name = _private_material_name(obj, material)
                renamed[str(material.name)] = str(replacement.name)
                copied += 1
            else:
                replacement = material
            try:
                replacement[MATERIAL_OWNER_PROP] = owner_id
            except Exception:  # noqa: BLE001
                pass
            replacements[pointer] = replacement
        if replacement is not material:
            slots[index] = replacement
    _refresh_layer_material_names(obj, data, renamed)
    if len(slots):
        try:
            obj.active_material_index = max(0, min(active_material_index, len(slots) - 1))
        except (AttributeError, TypeError, ValueError):
            pass
    return copied
