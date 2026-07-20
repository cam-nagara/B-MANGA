"""Blender 標準ショートカットの一括復旧オペレーター.

B-MANGA のキーマップ退避機構や他アドオンの干渉によって、Blender 標準の
ショートカットが ``active=False`` のまま userpref.blend へ焼き付くことがある
(2026-07-20 の実測で N / A / X / 左ドラッグ移動 / ペンのお尻消しゴム など
373 件が死んでいた)。

``keymap.repair_stale_disabled_shortcuts`` は B-MANGA の予約キー (O/F/K/T 等)
しか見ないため、それ以外は自己修復に拾われない。件数が多く手動修正は現実的
でないので、ユーザーがボタン 1 つで復旧できる口を用意する。

復元は default keyconfig と完全一致 (idname + キー + 修飾 + value) する項目に
限るため、ユーザー独自の追加設定は壊さない。
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty
from bpy.types import Operator

from ..utils import log

_logger = log.get_logger(__name__)


class BMANGA_OT_restore_standard_shortcuts(Operator):
    """無効化された Blender 標準ショートカットをまとめて復旧する."""

    bl_idname = "bmanga.restore_standard_shortcuts"
    bl_label = "標準ショートカットを復旧"
    bl_description = (
        "無効になっている Blender 標準のショートカット (サイドバー開閉、全選択、"
        "削除、ペンの消しゴム等) をまとめて有効に戻します。Blender 既定と完全に"
        "一致する項目だけを戻すので、自分で追加した設定は変わりません"
    )
    bl_options = {"REGISTER"}

    dry_run: BoolProperty(  # type: ignore[valid-type]
        name="確認のみ (変更しない)",
        description="復旧対象の件数を数えるだけで、実際には変更しません",
        default=False,
    )

    def execute(self, context):
        from ..keymap import keymap as keymap_mod

        try:
            restored, details = keymap_mod.restore_all_standard_shortcuts(
                dry_run=bool(self.dry_run)
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("restore_standard_shortcuts failed")
            self.report({"ERROR"}, f"復旧に失敗しました: {exc}")
            return {"CANCELLED"}

        for line in details[:50]:
            print(f"[B-MANGA][KEYMAP] restore: {line}")
        if len(details) > 50:
            print(f"[B-MANGA][KEYMAP] restore: ... 他 {len(details) - 50} 件")

        if restored == 0:
            self.report({"INFO"}, "無効になっている標準ショートカットはありません")
            return {"FINISHED"}
        if self.dry_run:
            self.report({"INFO"}, f"{restored} 件が復旧対象です (未変更)")
            return {"FINISHED"}
        self.report(
            {"INFO"},
            f"{restored} 件の標準ショートカットを復旧しました。"
            "この状態を保つにはプリファレンスを保存してください",
        )
        return {"FINISHED"}


_CLASSES = (BMANGA_OT_restore_standard_shortcuts,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
