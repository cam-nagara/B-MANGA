"""1 GP Object = 1 B-MANGA レイヤー モデル.

新規 GP Object をコマ Collection 直下に生成し、B-MANGA 安定 ID を stamp する。
"""

from __future__ import annotations

from typing import Optional

import bpy

from . import gpencil as gp_utils
from . import layer_object_sync as los
from . import log
from . import object_naming as on

_logger = log.get_logger(__name__)

# 新モデル GP Object の data 名 prefix
PER_LAYER_GP_DATA_PREFIX = "BManga_LayerGP_"
INTERNAL_MASK_LAYER_NAME = "__bmanga_mask"


def _resolve_unique_data_name(base: str) -> str:
    """``base`` をベースに、まだ未使用の GP data 名を返す.

    既存 data block を別 Object が使っている場合に複数 Object が同 data を
    共有してしまう事故を防ぐため、必ず未使用の名前を採用する。
    """
    coll = gp_utils._gp_data_blocks()
    if base not in coll:
        return base
    for i in range(1, 10000):
        candidate = f"{base}.{i:03d}"
        if candidate not in coll:
            return candidate
    # 例外的に到達したら Blender に任せて .NNN を付けさせる
    return base


def _new_gp_object_for_layer(
    *,
    bmanga_id: str,
    title: str,
) -> bpy.types.Object:
    """新 GP Object と GP data を生成する (まだ Collection に link しない).

    GP data 名は **必ず未使用** にする。既存 data 名と衝突したら .001 を
    付与した名前を採用し、別 Object との data 共有を防ぐ。
    """
    base_data_name = f"{PER_LAYER_GP_DATA_PREFIX}{bmanga_id}"
    data_name = _resolve_unique_data_name(base_data_name)
    gp_data = gp_utils.ensure_gpencil(data_name)
    obj_name = title or bmanga_id  # 後で assign_canonical_name で正規名へ書換え
    # bpy.data.objects.new は同名衝突で .001 を自動付加するので名前指定 OK
    obj = bpy.data.objects.new(obj_name, gp_data)
    # 既定レイヤー (content) を 1 つだけ用意。__bmanga_mask は後段で必要に応じて。
    if len(gp_data.layers) == 0:
        try:
            gp_utils.ensure_layer(gp_data, "content")
        except Exception:  # noqa: BLE001
            _logger.exception("new GP object: default layer create failed")
    return obj


def create_layer_gp_object(
    *,
    scene: bpy.types.Scene,
    bmanga_id: str,
    title: str,
    z_index: int,
    parent_kind: str,
    parent_key: str,
    folder_id: str = "",
) -> Optional[bpy.types.Object]:
    """新 GP Object を生成し、B-MANGA 安定 ID を stamp してコマ Collection に link.

    既に同 ``bmanga_id`` の Object が存在すれば再利用する。

    Args:
        bmanga_id: ``"gp_xxxxxx"`` 形式の安定 ID。
        title: ユーザー表示名。
        z_index: 重なり順 (0 詰め 4 桁化される)。
        parent_kind: ``"page" | "coma" | "folder" | "outside" | "none"``。
        parent_key: 親キー (例: ``"p0001:c01"``)。
        folder_id: フォルダ配下時の folder_id。
    """
    if scene is None or not bmanga_id:
        return None
    obj = on.find_object_by_bmanga_id(bmanga_id, kind="gp")
    if obj is None:
        obj = _new_gp_object_for_layer(bmanga_id=bmanga_id, title=title)
    # stamp + link は layer_object_sync 経由 (Phase 0 で実装済)
    los.stamp_layer_object(
        obj,
        kind="gp",
        bmanga_id=bmanga_id,
        title=title,
        z_index=z_index,
        parent_kind=parent_kind,
        parent_key=parent_key,
        folder_id=folder_id,
        scene=scene,
    )
    # 黒線材質を確保 (空マテリアルだと Draw モードで白線になる)
    try:
        gp_utils.ensure_default_stroke_material(obj)
        gp_utils.ensure_unique_object_materials(obj)
    except Exception:  # noqa: BLE001
        _logger.exception("create_layer_gp_object: default material failed")
    # コマ/ページマスクを GP Mask Modifier で適用
    try:
        from . import mask_apply

        mask_apply.apply_mask_to_layer_object(obj)
    except Exception:  # noqa: BLE001
        _logger.exception("create_layer_gp_object: mask_apply failed")
    return obj


def ensure_default_page_layer(
    scene: bpy.types.Scene,
    page_id: str,
    *,
    title: str = "ネーム",
) -> Optional[bpy.types.Object]:
    """ページ直下に個別GPが無い場合だけ既定レイヤーを作る。"""

    from . import layer_object_model

    parent_key = str(page_id or "")
    for obj in layer_object_model.iter_layer_objects("gp"):
        if layer_object_model.parent_key(obj) == parent_key:
            return obj
    return create_layer_gp_object(
        scene=scene,
        bmanga_id=layer_object_model.make_stable_id("gp"),
        title=title,
        z_index=210,
        parent_kind="page",
        parent_key=parent_key,
    )


def legacy_layer_migration_issue(layer) -> str:
    """旧GP内部レイヤーを個別Objectへ移せない理由を返す。"""

    masks = getattr(layer, "mask_layers", None)
    if masks is not None:
        names = {
            str(getattr(item, "name", "") or "")
            for item in list(masks)
        }
        unsupported = sorted(name for name in names if name != INTERNAL_MASK_LAYER_NAME)
        if unsupported:
            return "対応できないレイヤーマスク: " + ", ".join(unsupported)
    return ""


def _keep_only_migrated_layers(gp_data, source_name: str):
    layers = getattr(gp_data, "layers", None)
    selected = layers.get(source_name) if layers is not None else None
    if selected is None:
        raise RuntimeError("移行元の手描きレイヤーを複製できませんでした")
    for layer in list(layers):
        name = str(getattr(layer, "name", "") or "")
        if layer is selected or name == INTERNAL_MASK_LAYER_NAME:
            continue
        layers.remove(layer)
    for layer in list(layers):
        gp_utils.move_layer_to_group(gp_data, layer, None)
    selected.name = "content"
    try:
        layers.active = selected
    except Exception:  # noqa: BLE001
        pass
    return selected


def _transform_layer_points(layer, matrix) -> None:
    for frame in list(getattr(layer, "frames", ()) or ()):
        drawing = getattr(frame, "drawing", None)
        for stroke in list(getattr(drawing, "strokes", ()) or ()):
            for point in list(getattr(stroke, "points", ()) or ()):
                point.position = matrix @ point.position


def clone_legacy_layer_object(
    *,
    scene: bpy.types.Scene,
    source_obj: bpy.types.Object,
    source_layer,
    bmanga_id: str,
    title: str,
    z_index: int,
    parent_kind: str,
    parent_key: str,
    folder_id: str = "",
) -> bpy.types.Object:
    """旧集約GPの1内部レイヤーを、描画属性を失わず個別Objectへ移す。"""

    issue = legacy_layer_migration_issue(source_layer)
    if issue:
        raise ValueError(issue)
    source_matrix = source_obj.matrix_world.copy()
    clone = source_obj.copy()
    clone.data = source_obj.data.copy()
    clone.animation_data_clear()
    content = _keep_only_migrated_layers(clone.data, source_layer.name)
    los.stamp_layer_object(
        clone,
        kind="gp",
        bmanga_id=bmanga_id,
        title=title,
        z_index=z_index,
        parent_kind=parent_kind,
        parent_key=parent_key,
        folder_id=folder_id,
        scene=scene,
    )
    transform = clone.matrix_world.inverted_safe() @ source_matrix
    _transform_layer_points(content, transform)
    clone["bmanga_user_visible"] = not bool(getattr(source_layer, "hide", False))
    clone["bmanga_user_locked"] = bool(getattr(source_layer, "lock", False))
    gp_utils.ensure_unique_object_materials(clone)
    return clone


