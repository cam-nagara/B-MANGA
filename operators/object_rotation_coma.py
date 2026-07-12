"""オブジェクトツール回転: コマ (kind="coma", 漫画のコマ枠) 対応.

背景:
    「コマの回転」= コマ枠ポリゴンの回転であり、コマ辺編集
    (operators/coma_edge_move_op.py) と全く同じ意味論を持つ (コマ辺移動・
    ナイフカットと同じ「枠形状編集」)。コマ内側のレイヤー (Gペン/ラスター/
    フキダシ/テキスト等) は回転させない。中身はコマ枠のマスクに追従して
    クリップされ直すだけである。

    プロパティ (rotation_deg) は新設せず、頂点座標への焼き込み方式で実装する:
    - 矩形コマ (shape_type=="rect") は回転開始時の rect_*_mm をスナップ
      ショットとして保持し、角度適用時に「矩形4頂点を中心周りに回転した
      多角形」へ変換する (shape_type を "polygon" へ変更)。角度 0.0
      (キャンセル/リセット) では矩形へ完全復元する (rect_*_mm をビット単位
      で書き戻す。回転計算を経由しないため浮動小数点誤差が一切乗らない。
      operators/coma_edge_drag_session.py の cancel() と全く同じ手順)。
    - 元から多角形のコマは全頂点を中心周りに回転する。角度 0.0 では元の
      頂点列をそのまま書き戻す (同じ理由で回転計算を経由しない)。
    - bezier / freeform 等、頂点列を取得できない形状は非対応 (capture が
      None を返し、回転リング自体が無効化される)。

    頂点の書き込みは operators/coma_edge_move_op.py の _set_coma_polygon
    (コマ辺移動ドラッグ中に毎フレーム呼ばれている関数) をそのまま再利用する。
    これによりコマ背景メッシュ・枠線・マスクの更新が既存の辺編集と全く同じ
    経路 (rect_*_mm / vertices の update コールバック) で連動する。

座標空間:
    panel.rect_*_mm / vertices は「ページローカル mm」(ページオフセットを
    含まない)。一方、回転コアが渡す回転中心
    (object_tool_selection.selection_bounds_for_key -> coma_world_rect) は
    「ワールド mm」(ページオフセット加算済み) であるため、capture 時に一度
    だけページオフセットを差し引いてローカル mm へ変換して保存し、以降は
    ローカル空間だけで回転計算を完結させる (object_rotation_gp.py と同じ
    設計)。共有コマ (page_id == OUTSIDE_STACK_KEY) はページに属さないため
    オフセットは常に (0, 0) になる (page_offset_mm が page_index=-1 で
    0,0 を返すことは object_tool_selection.coma_world_rect の実装と同一)。

既知の制限:
    ドラッグ確定後は多角形として保存される (既存スキーマの shape.vertices
    にそのまま乗るため io/schema.py の変更は不要)。確定後に「矩形へ戻す」
    手段は Undo のみ (bmanga.coma_to_rect による手動矩形化は「外接矩形」に
    近似するため、元の矩形へは戻らない)。
"""

from __future__ import annotations

from ..core.work import get_work
from ..utils import object_selection
from ..utils.layer_hierarchy import OUTSIDE_STACK_KEY
from . import coma_edge_move_op, object_rotation, object_tool_selection
from .object_rotation_gp import rotate_point_around_center


def _find_coma_panel(context, key: str):
    """key からコマ panel を解決する (共有コマは page_index=-1 を返す)."""
    work = get_work(context)
    if work is None:
        return None, -1
    _kind, page_id, item_id = object_selection.parse_key(key)
    if page_id == OUTSIDE_STACK_KEY:
        _idx, panel = object_tool_selection.find_shared_coma_by_key(work, item_id)
        return panel, -1
    page_index, _page, _idx, panel = object_tool_selection.find_coma_by_key(work, page_id, item_id)
    return panel, page_index


def _capture_coma_rotation(context, key: str) -> dict | None:
    """コマの回転スナップショットを作る (未対応形状/対象無しは None)."""
    work = get_work(context)
    if work is None:
        return None
    panel, page_index = _find_coma_panel(context, key)
    if panel is None:
        return None
    rect = object_tool_selection.selection_bounds_for_key(context, key)
    if rect is None:
        # 頂点0件など、矩形が定まらない場合は回転中心が決められない。
        return None
    world_cx, world_cy = rect.center
    ox, oy = object_tool_selection.page_offset_mm(context, work, page_index)
    shape_type = str(getattr(panel, "shape_type", "rect") or "rect")
    vertices = coma_edge_move_op._coma_polygon(panel)
    if not vertices:
        # bezier/freeform 等、頂点列を取得できない形状は回転非対応。
        return None
    snapshot: dict = {
        "panel": panel,
        "shape_type": shape_type,
        # 回転開始時点の頂点列 (ページローカル mm)。apply のたびにここから
        # 回転し直す (累積回転にしない)。
        "vertices": list(vertices),
        # ワールド中心をページローカル mm へ変換して保存 (以降はローカル
        # 空間だけで完結させる)。
        "center_mm": (world_cx - ox, world_cy - oy),
        "base_rotation_deg": 0.0,
    }
    if shape_type == "rect":
        # 矩形は角度0での完全復元をビット単位で行うため、rect_*_mm を
        # そのまま (回転計算を経由せず) 保存しておく。
        snapshot["rect_mm"] = (
            float(panel.rect_x_mm),
            float(panel.rect_y_mm),
            float(panel.rect_width_mm),
            float(panel.rect_height_mm),
        )
    return snapshot


def _restore_original_shape(panel, snapshot: dict) -> None:
    """回転開始前の形状へ完全復元する (浮動小数点誤差を避け、回転計算を経由しない)."""
    if snapshot.get("shape_type") == "rect":
        rect_mm = snapshot.get("rect_mm")
        if rect_mm is None:
            return
        x, y, w, h = rect_mm
        # coma_edge_drag_session.cancel() と同じ手順: shape_type を先に
        # "rect" へ戻してから rect_*_mm を書き込む (update コールバックが
        # 現在の shape_type を見て coma_plane Mesh を再構築するため)。
        panel.shape_type = "rect"
        panel.rect_x_mm = x
        panel.rect_y_mm = y
        panel.rect_width_mm = w
        panel.rect_height_mm = h
        if len(panel.vertices) > 0:
            panel.vertices.clear()
        return
    # 元々多角形だったコマは、回転開始時点の頂点列をそのまま書き戻す。
    original_vertices = snapshot.get("vertices") or []
    coma_edge_move_op._set_coma_polygon(panel, original_vertices)


def _apply_coma_rotation(context, snapshot: dict, rotation_deg: float) -> None:
    """スナップショットの頂点列へ復元してから中心周りに絶対角度で回転する."""
    panel = snapshot.get("panel")
    if panel is None:
        return
    rotation_deg = float(rotation_deg)
    if rotation_deg == 0.0:
        # 完全復元 (キャンセル/リセット)。回転計算を経由しないため、矩形は
        # rect_*_mm がビット単位で元通りになる。
        _restore_original_shape(panel, snapshot)
        return
    original_vertices = snapshot.get("vertices") or []
    center_mm = snapshot.get("center_mm")
    if not original_vertices or center_mm is None:
        return
    cx, cy = center_mm
    rotated = [
        rotate_point_around_center(x, y, cx, cy, rotation_deg)
        for x, y in original_vertices
    ]
    # 辺移動ドラッグと同じ書き込み経路 (shape_type="polygon" + vertices
    # 差し替え)。update コールバック経由でコマ背景メッシュ・枠線・マスクが
    # 追従する。
    coma_edge_move_op._set_coma_polygon(panel, rotated)


def _can_rotate_coma(context, key: str) -> bool:
    """回転リングのホバー用軽量プローブ (capture と同じ形状判定、副作用無し).

    bezier/freeform 等、_coma_polygon が空を返す形状は回転非対応なので
    リングそのものを無効化する (capture 呼び出し無しで判定できる軽量版)。
    """
    panel, _page_index = _find_coma_panel(context, key)
    if panel is None:
        return True
    return bool(coma_edge_move_op._coma_polygon(panel))


object_rotation.register_rotation_handler(
    "coma", _capture_coma_rotation, _apply_coma_rotation, _can_rotate_coma,
)
