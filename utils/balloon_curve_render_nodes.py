"""フキダシ本体カーブの旧 Geometry Nodes グループを撤去するスタブ.

Phase D (v0.6.132) でフキダシの全描画責務 (塗り / 主線 / 外側フチ / 内側フチ /
多重線 / しっぽ主線フチ) を Python メッシュ (balloon_fill_mesh /
balloon_line_mesh) へ移行したため、本体カーブに付いていた
`BManga_GN_BalloonCurveRender` Geometry Nodes グループは不要になった。

このモジュールは互換のためのスタブとして残り、 旧 .blend ファイルから
読み込まれた古い modifier とノードグループを削除する。 描画 API としての
役割は持たない。
"""

from __future__ import annotations

import bpy

from . import log

_logger = log.get_logger(__name__)

MODIFIER_NAME = "B-MANGA Geometry Nodes"
GROUP_NAME = "BManga_GN_BalloonCurveRender"
PROP_GN_KIND = "bmanga_geometry_nodes_kind"
PROP_GROUP_VERSION = "bmanga_geometry_nodes_version"
KIND = "balloon_curve"
GROUP_VERSION = 100  # 後方互換: 任意の十分大きな値
FILL_BLUR_ALPHA_ATTRIBUTE = "bmanga_fill_blur_alpha"


def remove_modifier(obj: bpy.types.Object | None) -> None:
    """旧 Geometry Nodes modifier がフキダシ本体カーブに残っていれば撤去する."""
    if obj is None:
        return
    modifier = obj.modifiers.get(MODIFIER_NAME)
    if modifier is None:
        return
    try:
        obj.modifiers.remove(modifier)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: legacy GN modifier removal failed")
    try:
        if PROP_GN_KIND in obj:
            del obj[PROP_GN_KIND]
    except Exception:  # noqa: BLE001
        pass


def remove_node_group() -> None:
    """旧ノードグループが残っていれば削除する."""
    group = bpy.data.node_groups.get(GROUP_NAME)
    if group is None:
        return
    if group.users > 0:
        # まだ使われている (= modifier が残存) 場合は削除しない。
        return
    try:
        bpy.data.node_groups.remove(group)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon: legacy GN group removal failed")


def ensure_modifier(obj: bpy.types.Object | None, **_kwargs) -> None:
    """旧 ensure_modifier の互換スタブ. 何もせず、旧 modifier を撤去するだけ.

    Phase D 以降、フキダシの描画は Python メッシュ (balloon_fill_mesh /
    balloon_line_mesh) で完結する。残った旧 modifier は描画に寄与せず、
    評価コストだけ生じるため削除する。
    """
    remove_modifier(obj)
    remove_node_group()
