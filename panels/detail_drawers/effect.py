"""効果線の固定対象向け共通詳細描画。"""

from __future__ import annotations


def draw_effect_body(_sidebar_top, sidebar_below, body_cols, context, session, mode) -> None:
    """明示されたparamsだけを描画し、シーンの選択状態を探索しない。

    効果線は全パラメータがプリセット保存対象のため、左列(サイドバー)の
    上段は使わない。列1(サイドバー)のプリセット一覧の下へ「種類」box、
    列2(body_cols[0])へ外端形状・内端形状・線・まとまり・入り抜き・色、
    列3(body_cols[1]、一番右)へ「パス」boxを描画する。
    """

    from .. import effect_line_panel

    params = session.target.params
    preset_mode = str(getattr(mode, "value", mode)) == "preset"
    content_column = body_cols[0]
    path_column = body_cols[min(1, len(body_cols) - 1)]
    effect_line_panel.draw_effect_params(
        content_column,
        params,
        with_generate_button=False,
        columns=(content_column,),
        type_layout=sidebar_below,
        path_layout=path_column,
        preset_mode=preset_mode,
        # 編集モードへ移る基準パス操作は親ダイアログの復元範囲外なので、
        # 詳細設定からは除外し、効果線ツール側の独立入口だけに置く。
        allow_path_edit=False,
    )


__all__ = ["draw_effect_body"]
