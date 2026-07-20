"""起動後の遅延自己修復 — 標準ショートカット無効化残りの確実な復旧.

Blender 起動時の初期化順は「アドオン登録 → userpref.blend に保存された
キーマップカスタマイズの適用」であるため、``keymap.register()`` 内で実行
する自己修復 (``ensure_standard_view_toggles_enabled`` /
``repair_stale_disabled_shortcuts``) は、過去セッションで無効化されたまま
焼き付いた標準キー (N のサイドバー開閉、編集モードの F/K/O/T 等) が
user keyconfig へ適用される **前** に走って空振りすることがある
(2026-07-18 に N キーのサイドバー開閉で実測)。

このモジュールは register 後およそ 2 秒 / 10 秒 / 30 秒の 3 回、自己修復を
再実行するタイマーを提供する。これにより、タブを開いたまま Blender が
終了・クラッシュして無効状態が userpref.blend に保存されても、次回起動の
数秒後には必ず標準キーへ復旧する。
"""

from __future__ import annotations

from typing import Optional

import bpy

from ..utils import log
from . import keymap as keymap_mod

_logger = log.get_logger(__name__)

# 各修復パスの実行時刻 (register からの累積秒)。タイマーの戻り値は差分で返す。
_REPAIR_PASS_DELAYS = (2.0, 10.0, 30.0)
# mainfile 切替直後などキーマップ操作の一時停止中は、パスを消化せず再試行する
_SUSPEND_RETRY_INTERVAL = 2.0
_SUSPEND_RETRY_LIMIT = 30

_pass_index = 0
_suspend_retries = 0


def _repair_tick() -> Optional[float]:
    """自己修復を 1 パス実行し、次パスまでの間隔 (秒) か None (終了) を返す."""
    global _pass_index, _suspend_retries
    if keymap_mod.get_state() is None:
        return None  # アドオン無効化済み → タイマー停止
    if keymap_mod.is_visibility_update_suspended():
        # ファイル切替の不安定期間はキーマップを触らない (パスは消化しない)
        _suspend_retries += 1
        if _suspend_retries > _SUSPEND_RETRY_LIMIT:
            return None
        return _SUSPEND_RETRY_INTERVAL
    try:
        repaired_sidebar = keymap_mod.ensure_standard_view_toggles_enabled()
        repaired_stale = keymap_mod.repair_stale_disabled_shortcuts()
        # ペイント系ストローク (paint.image_paint / grease_pencil.brush_stroke 等)
        # も同じタイミングの穴に嵌まる。register() 内の修復だけでは userpref の
        # 無効化状態が適用される前に空振りし、「GP/ラスターに一切描けない」状態が
        # 再起動のたびに復活する (2026-07-20 実測)。ここでも必ず再実行する。
        repaired_paint = keymap_mod.ensure_paint_brush_strokes_enabled()
        if repaired_sidebar or repaired_stale or repaired_paint:
            print(
                "[B-MANGA][KEYMAP] startup repair pass"
                f" {_pass_index + 1}/{len(_REPAIR_PASS_DELAYS)}:"
                f" sidebar={repaired_sidebar} stale={repaired_stale}"
                f" paint={repaired_paint}"
            )
    except Exception:  # noqa: BLE001
        _logger.exception("startup repair pass failed")
    _pass_index += 1
    if _pass_index >= len(_REPAIR_PASS_DELAYS):
        return None
    return _REPAIR_PASS_DELAYS[_pass_index] - _REPAIR_PASS_DELAYS[_pass_index - 1]


def register() -> None:
    global _pass_index, _suspend_retries
    _pass_index = 0
    _suspend_retries = 0
    unregister()
    # persistent=True: 起動直後に作品ファイルを開いてもタイマーを維持する
    bpy.app.timers.register(
        _repair_tick,
        first_interval=_REPAIR_PASS_DELAYS[0],
        persistent=True,
    )


def unregister() -> None:
    if bpy.app.timers.is_registered(_repair_tick):
        try:
            bpy.app.timers.unregister(_repair_tick)
        except ValueError:
            pass
