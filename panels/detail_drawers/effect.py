"""効果線の固定対象向け共通詳細描画。"""

from __future__ import annotations

from .basic import body_columns


def draw_effect_body(layout, _context, session, mode) -> None:
    """明示されたparamsだけを描画し、シーンの選択状態を探索しない。"""

    from .. import effect_line_panel

    params = session.target.params
    preset_mode = str(getattr(mode, "value", mode)) == "preset"
    columns = body_columns(layout, session)
    effect_line_panel.draw_effect_params(
        columns[0],
        params,
        with_generate_button=False,
        columns=columns,
        preset_mode=preset_mode,
        # 編集モードへ移る基準パス操作は親ダイアログの復元範囲外なので、
        # 詳細設定からは除外し、効果線ツール側の独立入口だけに置く。
        allow_path_edit=False,
    )


__all__ = ["draw_effect_body"]
