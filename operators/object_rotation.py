"""オブジェクトツール: 選択矩形の角の少し外側をドラッグして回転する機能.

背景 (v0.6.301 で実装されたが、実際にはほぼ到達不能だったバグの修正):

1. **ゲート問題**: 従来の判定は「コマ/ページなど他オブジェクトへの
   ヒットが無い時だけ」回転ゾーンを調べていた。コマ/ページは矩形内部
   全体でヒットを返すため、コマ内・ページ上のオブジェクトでは回転
   判定へ到達できなかった。この関数群は「回転リングに入っているか」を
   他オブジェクトへのヒット有無と無関係に判定し、同じキーの精密ハンドル
   (リサイズ角/自由変形角など) とだけ排他させる。
2. **幾何の重なり**: リングの基準点を「実矩形の角」ではなく「実際に
   ハンドルが描画される角」(自由変形クアッドがあればその拡張角、
   無ければ SELECTION_HANDLE_OUTSET_MM 分外側へ拡張した角) に統一し、
   リサイズハンドルのヒット判定と綺麗に棲み分けるようにした。
3. **kind別ロジックの一元化**: balloon/effect/image で個別に実装されて
   いた回転スナップショットの取得・適用ロジックを ``ROTATION_HANDLERS``
   レジストリへ集約し、今後 kind を追加する際は
   ``register_rotation_handler`` を呼ぶだけで済むようにした。

このモジュールは object_tool_op.py と handle_intercept.py の両方から
使われる (オブジェクトツール本体と、他ツールからのハンドル横取りの両方で
同じ回転判定・同じ回転処理を共有するため)。
"""

from __future__ import annotations

import math
from collections.abc import Callable

from ..core.work import get_work
from ..utils import free_transform, object_selection
from ..utils.layer_hierarchy import OUTSIDE_STACK_KEY
from . import object_tool_free_transform, object_tool_selection

# ---------------------------------------------------------------------------
# リング幾何の定数
# ---------------------------------------------------------------------------

# ハンドル (表示角) から見た回転リングの内側/外側半径 (mm)。
# 内側半径はリサイズ用角ハンドルのヒット半径 (_HANDLE_HIT_MM や
# hit_part_for_rect の既定 threshold) とおおよそ一致させてあるが、
# 実際の排他判定は半径の大小関係ではなく _is_precise_handle_hit による
# 「同一キーの精密ハンドルが実際にヒットしたか」で行う。
_ROTATE_ZONE_INNER_MM = 3.0
_ROTATE_ZONE_OUTER_MM = 8.0

_UNSET = object()


# ---------------------------------------------------------------------------
# kind別 回転ハンドラーレジストリ
# ---------------------------------------------------------------------------
# 各ハンドラーは (capture_fn, apply_fn, can_rotate_fn) の3つ組。
#   capture_fn(context, key) -> dict | None
#       回転開始時に1回呼ばれる。None を返すと対象キーは回転できない。
#       返す dict には必ず "base_rotation_deg" (回転開始時点の角度) を含める。
#   apply_fn(context, snapshot, rotation_deg) -> None
#       ドラッグ中/キャンセル復元時に呼ばれ、絶対角度 rotation_deg を書き込む。
#   can_rotate_fn(context, key) -> bool | None
#       省略可 (既定 None = 常に可)。回転リングのヒット判定 (ホバー含む)
#       のたびに呼ばれる軽量プローブ。capture_fn が None を返すと分かって
#       いる対象を先んじて弾き、リングそのものを無効化する
#       (capture_fn のような対象のアクティブ化などの副作用は絶対に行わない)。
ROTATION_HANDLERS: dict[str, tuple[Callable, Callable, Callable | None]] = {}


def register_rotation_handler(
    kind: str,
    capture_fn: Callable,
    apply_fn: Callable,
    can_rotate_fn: Callable | None = None,
) -> None:
    """kind別の回転ハンドラーを登録する (後続の kind 追加用の公開API)."""
    ROTATION_HANDLERS[str(kind or "")] = (capture_fn, apply_fn, can_rotate_fn)


def _capture_balloon_rotation(context, key: str) -> dict | None:
    _kind, page_id, item_id = object_selection.parse_key(key)
    work = get_work(context)
    if work is None:
        return None
    if page_id == OUTSIDE_STACK_KEY:
        _idx, entry = object_tool_selection.find_shared_balloon_by_key(work, item_id)
    else:
        _pi, _page, _idx, entry = object_tool_selection.find_balloon_by_key(work, page_id, item_id)
    if entry is None:
        return None
    return {"entry": entry, "base_rotation_deg": float(getattr(entry, "rotation_deg", 0.0))}


def _apply_balloon_rotation(context, snapshot: dict, rotation_deg: float) -> None:
    entry = snapshot.get("entry")
    if entry is not None:
        entry.rotation_deg = float(rotation_deg)


def _capture_image_rotation(context, key: str) -> dict | None:
    _kind, _page_id, item_id = object_selection.parse_key(key)
    _idx, entry = object_tool_selection.find_image_by_key(context, item_id)
    if entry is None:
        return None
    return {"entry": entry, "base_rotation_deg": float(getattr(entry, "rotation_deg", 0.0))}


def _apply_image_rotation(context, snapshot: dict, rotation_deg: float) -> None:
    entry = snapshot.get("entry")
    if entry is not None:
        entry.rotation_deg = float(rotation_deg)


def _capture_effect_rotation(context, key: str) -> dict | None:
    _kind, _page_id, item_id = object_selection.parse_key(key)
    obj, layer = object_tool_selection.find_effect_layer(item_id)
    if layer is None:
        return None
    # 効果線の回転角は「シーン単一のアクティブレイヤー用バッファ」
    # (scene.bmanga_effect_line_params) を経由して各レイヤーの保存値へ
    # 反映される。クリック選択時に使われるアクティブ化関数を先に通す
    # ことで、複数の効果線レイヤーを選択していても「ドラッグした方の
    # レイヤー」が正しくバッファへ読み込まれ、回転もそのレイヤーへ
    # 書き戻される (対象キー以外を選び直すわけではないので、ビューポート
    # の複数選択状態 (object_selection) は変更しない)。
    from . import effect_line_op

    effect_line_op._select_effect_layer(context, obj, layer)
    scene = getattr(context, "scene", None)
    params = getattr(scene, "bmanga_effect_line_params", None) if scene is not None else None
    if params is None:
        return None
    return {"params": params, "base_rotation_deg": float(getattr(params, "rotation_deg", 0.0))}


def _apply_effect_rotation(context, snapshot: dict, rotation_deg: float) -> None:
    params = snapshot.get("params")
    if params is not None:
        params.rotation_deg = float(rotation_deg)


register_rotation_handler("balloon", _capture_balloon_rotation, _apply_balloon_rotation)
register_rotation_handler("image", _capture_image_rotation, _apply_image_rotation)
register_rotation_handler("effect", _capture_effect_rotation, _apply_effect_rotation)


def capture_rotation_snapshot(context, key: str) -> dict | None:
    """kind別ハンドラーで回転スナップショットを1件作る (未対応kindはNone)."""
    kind = object_selection.parse_key(key)[0]
    handler = ROTATION_HANDLERS.get(kind)
    if handler is None:
        return None
    capture_fn, _apply_fn, _can_rotate_fn = handler
    try:
        snapshot = capture_fn(context, key)
    except Exception:  # noqa: BLE001
        return None
    if snapshot is None:
        return None
    snapshot["kind"] = kind
    snapshot["key"] = key
    return snapshot


def apply_rotation_snapshot(context, snapshot: dict, rotation_deg: float) -> None:
    """スナップショットの対象へ絶対角度 rotation_deg を書き込む."""
    kind = str(snapshot.get("kind", "") or "")
    handler = ROTATION_HANDLERS.get(kind)
    if handler is None:
        return
    _capture_fn, apply_fn, _can_rotate_fn = handler
    try:
        apply_fn(context, snapshot, rotation_deg)
    except Exception:  # noqa: BLE001
        pass


def can_rotate(context, key: str) -> bool:
    """回転リングを表示/反応させてよいか (kind別 can_rotate_fn への委譲).

    未登録kind・プローブ関数未登録・プローブ例外はすべて「可」扱いにする
    (最終判定は capture_rotation_snapshot の None 復帰に任せる安全側フォール
    バック。ここは「明らかに不可と分かっている対象を早期に弾く」ための
    軽量フィルタに過ぎない)。
    """
    kind = object_selection.parse_key(key)[0]
    handler = ROTATION_HANDLERS.get(kind)
    if handler is None:
        return True
    _capture_fn, _apply_fn, can_rotate_fn = handler
    if can_rotate_fn is None:
        return True
    try:
        return bool(can_rotate_fn(context, key))
    except Exception:  # noqa: BLE001
        return True


def restore_rotation_snapshot(context, snapshot: dict) -> None:
    """回転開始時点の角度へ戻す (キャンセル用)."""
    apply_rotation_snapshot(context, snapshot, float(snapshot.get("base_rotation_deg", 0.0)))


# ---------------------------------------------------------------------------
# リング幾何 (表示ハンドル角ベース)
# ---------------------------------------------------------------------------


def _handle_corners_for_key(context, key: str) -> list[tuple[float, float]] | None:
    """回転リングの基準点 (実際にハンドルが描画される角) を4つ返す.

    自由変形クアッドが有効な balloon/effect はその拡張角 (角ハンドルの
    ヒット判定 = hit_transformed_handle_at_event と同じ基準) を使う。
    それ以外は矩形の角を SELECTION_HANDLE_OUTSET_MM だけ外側へ拡張した
    位置 (通常のリサイズハンドルの描画位置) を使う。
    """
    kind = object_selection.parse_key(key)[0]
    rect = object_tool_selection.selection_bounds_for_key(context, key)
    if rect is None:
        return None
    if kind in {"balloon", "effect"}:
        quad = object_tool_free_transform._quad_for_key(context, key, force=False)
        if quad:
            expanded = object_tool_free_transform._expanded_quad_for_hit(quad)
            points = free_transform.ordered_quad_points(expanded)
            if points:
                return points
    handle_rect = object_tool_selection.handle_rect_for_bounds(rect)
    return [
        (handle_rect.x, handle_rect.y),
        (handle_rect.x2, handle_rect.y),
        (handle_rect.x2, handle_rect.y2),
        (handle_rect.x, handle_rect.y2),
    ]


def _rect_center_for_key(context, key: str) -> tuple[float, float] | None:
    rect = object_tool_selection.selection_bounds_for_key(context, key)
    if rect is None:
        return None
    return rect.center


def _corner_in_ring(corners: list[tuple[float, float]], x_mm: float, y_mm: float) -> bool:
    for cx, cy in corners:
        dist = math.hypot(x_mm - cx, y_mm - cy)
        if _ROTATE_ZONE_INNER_MM < dist <= _ROTATE_ZONE_OUTER_MM:
            return True
    return False


def _is_precise_handle_hit(hit: dict | None) -> bool:
    """hit が「回転より優先すべき精密ハンドルへのヒット」かどうか."""
    if hit is None:
        return False
    kind = str(hit.get("kind", "") or "")
    if kind in {"coma_edge", "coma_vertex", "gradient_handle"}:
        return True
    part = str(hit.get("part", "") or "")
    return part not in {"", "move", "body"}


# コンテナ系kind (矩形/多角形の内部全体でヒットを返す)。これらへのヒットは
# 従来どおり無視してリングを勝たせる (v0.6.301 の到達不能バグ修正の本体)。
_CONTAINER_KINDS = frozenset({"coma", "page", "page_file"})


def _is_container_kind(kind: str) -> bool:
    return kind in _CONTAINER_KINDS


def _default_event_world_xy(context, event) -> tuple[float | None, float | None]:
    from . import effect_line_op

    return effect_line_op._event_world_xy_mm(context, event)


def rotation_hit_with_priority(
    context,
    event,
    event_world_xy: Callable | None = None,
    *,
    hit=_UNSET,
) -> dict | None:
    """回転リングのヒット判定 (他オブジェクトのヒット有無に左右されない).

    ``hit`` には呼び出し側で計算済みの hit_object_at_event 相当の結果を渡す。
    渡さない場合 (ホバー時など) はリングに入った時だけ内部で計算する
    (安価なリング判定を先に済ませてから重い判定を遅延実行するため)。

    優先順位 (敵対的レビューで確認された欠陥の修正後の仕様):
      1. ``precise_hit`` が _is_precise_handle_hit と判定される精密ハンドル
         ヒットなら、調査中キーと一致するかに関係なく常に回転より優先する
         (グラデーション端点ハンドルは kind="gradient_handle" で fill 本体と
         キー形式そのものが異なるため、キー一致だけを条件にすると素通りして
         リングに横取りされていた)。
      2. ``precise_hit`` が「調査中キーと異なるキー」かつ kind がコンテナ系
         (coma/page/page_file) 以外の前景オブジェクトへの通常クリック
         (part が move/body 等) であれば、そのオブジェクトへのクリックを
         優先し回転しない (複数選択中の別オブジェクト/未選択の別オブジェクト
         へのクリックが回転にすり替わるのを防ぐ)。
      3. コンテナ系 (coma/page/page_file) へのヒットはキーが不一致でも無視
         してリングが勝つ (v0.6.301 の到達不能バグ修正の本体。ここは絶対に
         退行させない)。
      4. 同一キーの part=="move"/"body" ヒットは従来どおりリングが勝つ。
    加えて、can_rotate (kind別プローブ) が False を返す対象はそもそも
    リングの候補から除外する (回転不可能な対象で空ドラッグ/空Undoが積まれる
    のを防ぐ)。

    戻り値は {"key", "kind", "center", "world"} の dict、非該当なら None。
    """
    resolver = event_world_xy or _default_event_world_xy
    x_mm, y_mm = resolver(context, event)
    if x_mm is None or y_mm is None:
        return None
    keys = list(object_selection.get_keys(context))
    active_key = object_tool_selection.active_selection_key(context)
    if active_key and active_key not in keys:
        keys.append(active_key)

    computed_hit = _UNSET
    for key in reversed(keys):
        kind = object_selection.parse_key(key)[0]
        if kind not in ROTATION_HANDLERS:
            continue
        corners = _handle_corners_for_key(context, key)
        if not corners or not _corner_in_ring(corners, float(x_mm), float(y_mm)):
            continue
        if not can_rotate(context, key):
            # 回転不可能と分かっている対象 (端点グラデーション塗り/
            # bezier・freeformコマ/Image未生成のラスター等) はリングそのもの
            # を無効化する (hit 計算より前に弾いて無駄な重い判定も避ける)。
            continue
        precise_hit = hit
        if precise_hit is _UNSET:
            if computed_hit is _UNSET:
                from . import object_tool_op

                computed_hit = object_tool_op.hit_object_at_event(context, event)
            precise_hit = computed_hit
        if _is_precise_handle_hit(precise_hit):
            # ルール1: キーに関係なく、精密ハンドルへの実ヒットが最優先。
            continue
        if precise_hit is not None:
            hit_key = str(precise_hit.get("key", "") or "")
            hit_kind = str(precise_hit.get("kind", "") or "")
            if hit_key and hit_key != key and not _is_container_kind(hit_kind):
                # ルール2: 別キーの前景オブジェクトへの通常クリックを優先し、
                # 回転にすり替えない。
                continue
        center = _rect_center_for_key(context, key)
        if center is None:
            continue
        return {
            "key": key,
            "kind": kind,
            "center": center,
            "world": (float(x_mm), float(y_mm)),
        }
    return None


def update_rotation_hover_cursor(context, event, op, *, restore_cursor: str = "DEFAULT") -> None:
    """非ドラッグ中の MOUSEMOVE で回転カーソルの表示/非表示を切り替える.

    オブジェクトツール以外 (テキストツール/フキダシツール等) の modal からも
    呼べる公開ヘルパー。``op`` には bool 属性 ``_rotate_cursor_active`` を
    自由に持たせてよい (無ければ False 扱い)。

    ``restore_cursor`` にはリングから外れた時に復帰させるカーソル種別を渡す。
    オブジェクトツールは既定の "DEFAULT" のままでよいが、それ以外のツール
    (テキストツールの "TEXT"/"NONE"、各種描画ツールの "CROSSHAIR" 等) は
    呼び出し側がそのツール自身のカーソル文字列を渡すこと。
    """
    from . import coma_modal_state

    was_rotate = bool(getattr(op, "_rotate_cursor_active", False))
    rot_hit = rotation_hit_with_priority(context, event)
    if rot_hit is not None:
        if not was_rotate:
            coma_modal_state.set_modal_cursor(context, "SCROLL_XY")
            op._rotate_cursor_active = True
        return
    if was_rotate:
        coma_modal_state.set_modal_cursor(context, restore_cursor)
        op._rotate_cursor_active = False


def compute_rotation_delta(
    center: tuple[float, float],
    prev_x: float, prev_y: float,
    curr_x: float, curr_y: float,
) -> float:
    cx, cy = center
    angle_prev = math.atan2(prev_y - cy, prev_x - cx)
    angle_curr = math.atan2(curr_y - cy, curr_x - cx)
    return math.degrees(angle_curr - angle_prev)
