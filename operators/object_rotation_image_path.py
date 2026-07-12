"""オブジェクトツール回転: 画像パスレイヤー (kind="image_path"、パターンカーブ) 対応.

背景:
    画像パスレイヤーの実体は scene.bmanga_image_path_layers
    (core/image_path_layer.py の BMangaImagePathLayer) で、rotation_deg
    相当のプロパティは無い (image_angle_deg はパスに沿って並ぶスタンプ
    1個ごとの向きで別物)。gp レイヤー (object_rotation_gp.py) と同じ
    「パス頂点座標の直接書き換え」方式で実装する。

    プロパティ方式 (rotation_euler 等の新規追加) を採らない理由:
      - 選択矩形 (AABB) が回転で膨張し、ハンドル位置とズレる
      - 表示メッシュとは別に存在する編集用ベジェカーブ実体
        (utils/image_path_object.py の _ensure_edit_curve_object) との
        回転同期が別途必要になる
      - 保存スキーマ (io/image_path_schema.py) や書き出し経路への対応が
        追加で必要になる
    パス頂点 (entry.path_points_json) 自体を焼き込む方式なら、表示メッシュ・
    編集用カーブの両方が既存の update コールバック
    (BMangaImagePathLayer.path_points_json の update=_on_image_path_changed
    -> utils.image_path_object.on_image_path_entry_changed) 経由で自動的に
    再構築され、保存スキーマも書き出しも無改造で整合する。

座標空間 (utils/image_path_object.py・operators/image_path_tool_op.py・
operators/object_tool_op.py を読んで確認済み):
    entry.path_points_json は「ページローカル mm」(ページのグリッド上の
    ワールドオフセットを含まない座標) で保存されている。
      - image_path_tool_op._create_image_path はストローク点を
        ``wx - self._page_ox`` の形 (ページの原点を引いた値) で積んでいる。
      - object_tool_op.py の移動ドラッグは、ワールド mm のマウス移動量
        dx/dy をこの JSON へそのまま加算する。オフセットはドラッグ中
        不変な定数なので、差分適用ならどちらの空間でも結果は同じになる
        (これは移動が成立する理由であって、回転中心のように「絶対位置」を
        使う操作では空間の統一が必須)。
      - utils/image_path_object.py ensure_image_path_object は
        ``obj.location = mm_to_m(path_points_center + ページのワールド
        オフセット)`` で表示メッシュを配置する
        (entry_page_offset_mm と同じ計算)。
    そのため回転中心は、selection_bounds_for_key が返す実体メッシュの
    「ワールド」境界矩形の中心から、表示メッシュの配置と全く同じ
    entry_page_offset_mm (utils/image_real_object.py) を差し引いて
    「ページローカル mm」へ変換してから使う
    (object_rotation_gp._capture_gp_rotation の parent_offset_mm 変換と
    同じ考え方)。以降の回転計算はページローカル空間だけで完結する。

既知の制限:
    stamp_angle_mode が既定の "line" (パスの向きに追従) 以外
    (固定角度・方向オブジェクト) の場合、パスの形自体は正しく回転するが、
    スタンプ個々の向き (image_angle_deg / stamp_angle_object_name) は
    この回転処理では一切変更しないため、パスの回転に追従しない。
"""

from __future__ import annotations

import json
import math

from ..core.work import get_work
from ..utils import object_selection
from ..utils.image_real_object import entry_page_offset_mm, page_for_entry
from . import object_rotation, object_tool_selection


def rotate_point_around_center(
    x: float, y: float, cx: float, cy: float, angle_deg: float,
) -> tuple[float, float]:
    """(x, y) を (cx, cy) 中心に angle_deg 度 (反時計回り) 回転した座標を返す.

    object_rotation.compute_rotation_delta の ``atan2(y - cy, x - cx)`` と
    同じ座標系・回転方向を前提にした純関数 (object_rotation_gp.py の同名
    関数と同一の式。あちらは編集禁止ファイルのため、ここへ複製している)。
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


def _capture_image_path_rotation(context, key: str) -> dict | None:
    """画像パスレイヤーの回転スナップショットを作る (未対応/対象無しは None)."""
    _kind, _page_id, item_id = object_selection.parse_key(key)
    _idx, entry = object_tool_selection.find_image_path_by_key(context, item_id)
    if entry is None:
        return None
    original_json = str(getattr(entry, "path_points_json", "") or "")
    try:
        parsed_points = json.loads(original_json) if original_json else []
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed_points = []
    if not isinstance(parsed_points, list) or len(parsed_points) < 2:
        # 頂点が2点未満だとパスが定まらず回転する形が無い。
        return None
    rect = object_tool_selection.selection_bounds_for_key(context, key)
    if rect is None:
        # 実体メッシュが無い等で選択矩形が定まらない場合は回転中心を決められない。
        return None
    center_world_x, center_world_y = rect.center
    work = get_work(context)
    scene = getattr(context, "scene", None)
    page = page_for_entry(scene, work, entry)
    ox_mm, oy_mm = entry_page_offset_mm(scene, work, entry, page)
    return {
        "entry": entry,
        "original_json": original_json,
        # ワールド中心をページローカル mm (path_points_json と同じ空間) へ
        # 変換して保存する。以降の回転計算はこのローカル空間だけで完結する。
        "center_mm": (center_world_x - ox_mm, center_world_y - oy_mm),
        "base_rotation_deg": 0.0,
    }


def _apply_image_path_rotation(context, snapshot: dict, rotation_deg: float) -> None:
    """スナップショットの元 JSON を基準に、絶対角度 rotation_deg で回転を書き込む."""
    entry = snapshot.get("entry")
    if entry is None:
        return
    original_json = str(snapshot.get("original_json", "") or "")
    if abs(float(rotation_deg)) < 1e-9:
        # 0度は「元へ戻す」操作なので、演算による誤差を一切残さないよう
        # 保存しておいた元の JSON 文字列をそのまま書き戻す。
        entry.path_points_json = original_json
        return
    center_mm = snapshot.get("center_mm")
    if center_mm is None or not original_json:
        return
    try:
        data = json.loads(original_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return
    if not isinstance(data, list):
        return
    cx_mm, cy_mm = center_mm
    rotated: list = []
    changed = False
    for item in data:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            rotated.append(item)
            continue
        try:
            x = float(item[0])
            y = float(item[1])
        except (TypeError, ValueError):
            rotated.append(item)
            continue
        # 累積させず、常に元 JSON の座標から絶対角度で回転し直す
        # (キャンセル/ドラッグ再開時にも歪みが蓄積しないようにするため)。
        nx, ny = rotate_point_around_center(x, y, cx_mm, cy_mm, rotation_deg)
        extra = list(item[2:]) if len(item) > 2 else []
        rotated.append([nx, ny, *extra])
        changed = True
    if not changed:
        return
    # update コールバック (_on_image_path_changed) が表示メッシュ・編集用
    # カーブの両方を自動再構築するため、明示的な再構築呼び出しは不要。
    entry.path_points_json = json.dumps(rotated, ensure_ascii=False, separators=(",", ":"))


object_rotation.register_rotation_handler(
    "image_path", _capture_image_path_rotation, _apply_image_path_rotation,
)
