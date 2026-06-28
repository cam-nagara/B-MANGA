"""コマID変更時に、コマ実体オブジェクト名と持ち主情報を追従させる。"""

from __future__ import annotations

import bpy


_OWNER_PROPS = (
    "bmanga_coma_plane_owner_id",
    "bmanga_coma_mask_owner_id",
    "bmanga_coma_border_owner_id",
    "bmanga_coma_white_margin_owner_id",
)

_NAME_PREFIXES = (
    "coma_plane_",
    "coma_plane_mesh_",
    "BManga_ComaPlane_",
    "coma_mask_",
    "coma_mask_mesh_",
    "coma_border_",
    "coma_border_curve_",
    "coma_border_mesh_",
    "BManga_ComaBorder_",
    "coma_border_texture_mesh_",
    "BManga_ComaBorderTexture_",
    "BManga_ComaBorderAlpha_",
    "BManga_ComaPlaneAlpha_",
    "coma_white_margin_",
    "coma_white_margin_mesh_",
    "BManga_ComaWhiteMargin_",
)


def _replace_key(value: str, old_key: str, new_key: str, *, prefix: bool) -> str:
    if value == old_key:
        return new_key
    if prefix and value.startswith(f"{old_key}:"):
        return f"{new_key}:{value.split(':', 1)[1]}"
    return value


def _token(key: str) -> str:
    return str(key or "").replace(":", "_")


def _rename_if_coma_runtime(datablock, old_token: str, new_token: str) -> None:
    name = str(getattr(datablock, "name", "") or "")
    if not name.startswith(_NAME_PREFIXES):
        return
    if old_token not in name:
        return
    try:
        datablock.name = name.replace(old_token, new_token, 1)
    except Exception:  # noqa: BLE001
        pass


def _retarget_owner_props(datablock, old_key: str, new_key: str, *, prefix: bool) -> None:
    for prop in _OWNER_PROPS:
        value = str(datablock.get(prop, "") or "")
        if not value:
            continue
        replacement = _replace_key(value, old_key, new_key, prefix=prefix)
        if replacement != value:
            try:
                datablock[prop] = replacement
            except Exception:  # noqa: BLE001
                pass


def retarget_coma_runtime_ids(old_key: str, new_key: str, *, prefix: bool = False) -> None:
    """コマID/ページIDの変更を、非管理扱いのコマ実体へ反映する。

    ``old_key`` / ``new_key`` は ``p0001:c01`` またはページ単体 ``p0001``。
    ``prefix=True`` の場合はページID変更として ``p0001:*`` も置換する。
    """
    old_key = str(old_key or "")
    new_key = str(new_key or "")
    if not old_key or not new_key or old_key == new_key:
        return
    old_token = _token(old_key)
    new_token = _token(new_key)
    if not old_token or not new_token or old_token == new_token:
        return
    for datablocks in (
        bpy.data.objects,
        bpy.data.meshes,
        bpy.data.curves,
        bpy.data.materials,
        bpy.data.images,
    ):
        for datablock in list(datablocks):
            _retarget_owner_props(datablock, old_key, new_key, prefix=prefix)
            _rename_if_coma_runtime(datablock, old_token, new_token)
