"""塗りつぶしレイヤー (kind="fill") をオブジェクトツールの回転コアへ登録する.

operators/object_rotation.py 本体は並行セッションと共有のため直接編集しない。
代わりに ``object_rotation.register_rotation_handler`` を呼んで塗りつぶし用の
capture/apply 関数を登録する (balloon/image/text と同じレジストリ方式)。
このモジュールは import されるだけで登録が完了する (副作用による登録)。

グラデーション端点 (use_gradient_endpoints=True) の扱いについて:
    塗りの実体 (utils/fill_real_object.py) はメッシュ・UV・グラデーション材質
    ノードのすべてを「中心基準のローカル空間」で構築し、obj.rotation_euler[2]
    を掛けるだけで剛体として正しく回転する (角度指定グラデーション・ベタ塗り
    はこれで完全に一貫する)。

    しかし端点指定グラデーションには、絶対mm座標を直接参照する2つの副産物が
    ある: (1) ドラッグ用の始点/終点ハンドル (別オブジェクト、
    _ensure_gradient_handles で絶対mm配置)、(2) 選択時の始点→終点オーバーレイ
    線 (ui/overlay.py の _draw_gradient_lines、gradient_start/end_x/y_mm を
    そのまま参照)。これらはオブジェクト回転に追従しないため、塗り本体だけ
    回すと「実際のグラデーション方向は回るのにハンドルと線は元のまま」という
    視覚的な不整合が生じる。

    端点データ自体を回転角に合わせて回す方式も検討したが、その場合
    _ensure_material が回転後の端点mm座標から材質のUVマッピング方向を
    再焼き込みし、さらにオブジェクト回転が剛体としてそれを回すため、
    同じ回転が二重に掛かってしまう (材質側の焼き込み分 + オブジェクト
    回転分)。二重回転を避けつつハンドル・線・材質を一貫させるには、塗りの
    形状データ自体 (region/lasso) を回転角ぶん再構築する設計変更が必要で、
    今回のタスク範囲 (ensure_fill_real_object への数行追加) を大きく超える。

    そのため、視覚不整合を出さない安全側の判断として、端点指定グラデーション
    (fill_type=="gradient" かつ use_gradient_endpoints=True。判定条件は
    utils.fill_real_object.is_gradient_endpoint_rotation_locked に集約) の
    塗りは capture_fn で None を返し、回転リング自体が反応しない (回転対象外)
    扱いにする。ベタ塗り・角度指定グラデーションの塗りは通常どおり回転対応
    する。

    fill_type 変更との整合について:
        判定条件を「use_gradient_endpoints のみ」にすると、端点グラデ→ベタ
        塗りへタイプ変更した際に use_gradient_endpoints=True が残留し、
        パネルの回転欄は編集可能なのに回転リングだけ永久に無反応になる
        不整合が起きる (utils/fill_real_object.py の rotation_euler 抑制、
        panels/layer_stack_detail_ui.py の回転欄活性判定と条件式を統一する
        必要がある)。そのため is_gradient_endpoint_rotation_locked
        (fill_type=="gradient" かつ use_gradient_endpoints) をヘルパー関数
        として1箇所に集約し、capture_fn・can_rotate_fn の両方から呼ぶ。
"""

from __future__ import annotations

from ..utils import object_selection
from ..utils.fill_real_object import is_gradient_endpoint_rotation_locked
from . import object_rotation, object_tool_selection


def _capture_fill_rotation(context, key: str) -> dict | None:
    _kind, _page_id, item_id = object_selection.parse_key(key)
    _idx, entry = object_tool_selection.find_fill_by_key(context, item_id)
    if entry is None:
        return None
    if is_gradient_endpoint_rotation_locked(entry):
        # モジュール docstring 参照: 端点グラデーションは回転対象外
        # (ドラッグハンドル・オーバーレイ線が絶対mm座標のまま追従しないため)。
        return None
    return {"entry": entry, "base_rotation_deg": float(getattr(entry, "rotation_deg", 0.0))}


def _apply_fill_rotation(context, snapshot: dict, rotation_deg: float) -> None:
    entry = snapshot.get("entry")
    if entry is not None:
        entry.rotation_deg = float(rotation_deg)


def _can_rotate_fill(context, key: str) -> bool:
    """回転リングのホバー用軽量プローブ (capture と同じ条件、副作用無し)."""
    _kind, _page_id, item_id = object_selection.parse_key(key)
    _idx, entry = object_tool_selection.find_fill_by_key(context, item_id)
    if entry is None:
        # 対象不明時は安全側 (permissive) にしておき、最終判定は capture に
        # 委ねる (item 3: capture が None なら回転ドラッグ自体が開始しない)。
        return True
    return not is_gradient_endpoint_rotation_locked(entry)


object_rotation.register_rotation_handler(
    "fill", _capture_fill_rotation, _apply_fill_rotation, _can_rotate_fill,
)
