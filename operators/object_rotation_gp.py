"""オブジェクトツール回転: Gペン(グリースペンシル)線画レイヤー (kind="gp") 対応.

背景:
    gp レイヤーには rotation_deg 相当のプロパティも PropertyGroup も無い。
    移動・スケールと同じく「ストローク点座標の直接書き換え」方式で実装する
    (Blender ネイティブの GreasePencilLayer.rotation/translation/scale は本
    アドオンでは一切使わない。選択矩形計算 (gp_layer_local_bounds など) が
    layer transform を合成しないため、使うと表示と判定がズレるからである)。

方式:
    capture 時に「現在の全ストローク点座標のスナップショット」と「回転開始
    時点で確定させた回転中心 (選択矩形の中心)」を dict に保存し、apply 時は
    毎回そのスナップショットへ座標を復元してから rotation_deg 度だけ回転
    する (累積回転にしない。0度なら復元のみで完全に元へ戻る)。

    点は「親 (ページ/コマ) からのオフセットを除いたローカル mm 座標」で
    保持される (gp_layer_local_bounds / gp_layer_world_rect と同じ前提)。
    選択矩形の中心はワールド mm で返るため、capture 時に一度だけ
    parent_offset_mm を差し引いてローカル mm へ変換し、以降はローカル空間
    だけで回転計算を完結させる (ページオフセットは回転ドラッグ中に不変な
    定数なので、都度足し引きする必要が無い)。

既知の制限:
    リンク複製された gp レイヤーの回転は単一レイヤーのみに適用され、
    リンク先へは伝播しない (balloon のようなリンク同期は未実装)。
"""

from __future__ import annotations

import math

from ..core.work import get_work
from ..utils import gp_layer_parenting as gp_parent
from ..utils import layer_stack as layer_stack_utils
from ..utils import object_selection
from . import object_rotation, object_tool_selection


def rotate_point_around_center(
    x: float, y: float, cx: float, cy: float, angle_deg: float,
) -> tuple[float, float]:
    """(x, y) を (cx, cy) 中心に angle_deg 度 (反時計回り) 回転した座標を返す.

    compute_rotation_delta (object_rotation.py) の
    ``atan2(y - cy, x - cx)`` と同じ座標系・回転方向を前提にした純関数。
    """
    angle = math.radians(float(angle_deg))
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    dx = float(x) - float(cx)
    dy = float(y) - float(cy)
    return (
        float(cx) + dx * cos_a - dy * sin_a,
        float(cy) + dx * sin_a + dy * cos_a,
    )


def _capture_gp_rotation(context, key: str) -> dict | None:
    """gp レイヤーの回転スナップショットを作る (未対応/対象無しは None)."""
    _kind, _page_id, item_id = object_selection.parse_key(key)
    obj, layer = object_tool_selection.find_gp_layer(item_id)
    if layer is None:
        return None
    work = get_work(context)
    rect = object_tool_selection.selection_bounds_for_key(context, key)
    if rect is None:
        # ストロークが無い等で矩形が定まらない場合は回転中心が決められない。
        return None
    center_world_x, center_world_y = rect.center
    ox, oy = object_tool_selection.parent_offset_mm(context, work, gp_parent.parent_key(layer))
    points = gp_parent.capture_layers([layer])
    if not points or not points[0][1]:
        return None
    return {
        "obj": obj,
        "layer": layer,
        "points": points,
        # ワールド中心をローカル mm へ変換して保存 (以降はローカル空間で完結)。
        "center_mm": (center_world_x - ox, center_world_y - oy),
        "base_rotation_deg": 0.0,
    }


def _apply_gp_rotation(context, snapshot: dict, rotation_deg: float) -> None:
    """スナップショットの点位置へ復元してから中心周りに絶対角度で回転する."""
    points = snapshot.get("points") or []
    center_mm = snapshot.get("center_mm")
    if not points or center_mm is None:
        return
    cx_mm, cy_mm = center_mm
    changed = False
    for _layer, point_list in points:
        for point, original_pos in point_list:
            try:
                orig_x_mm = float(original_pos[0]) * 1000.0
                orig_y_mm = float(original_pos[1]) * 1000.0
                new_x_mm, new_y_mm = rotate_point_around_center(
                    orig_x_mm, orig_y_mm, cx_mm, cy_mm, rotation_deg,
                )
                point.position = (new_x_mm / 1000.0, new_y_mm / 1000.0, float(original_pos[2]))
                changed = True
            except Exception:  # noqa: BLE001
                continue
    if changed:
        # 呼び出し元 (object_tool_op.py の _update_drag/_cancel_drag) でも
        # 毎回 tag_view3d_redraw を呼んでいるが、scale_gp_layer_from_snapshot
        # と同様にこの関数自身の責務としても再描画タグを保証しておく。
        layer_stack_utils.tag_view3d_redraw(context)


object_rotation.register_rotation_handler("gp", _capture_gp_rotation, _apply_gp_rotation)
