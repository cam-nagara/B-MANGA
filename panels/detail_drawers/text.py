"""テキストの実レイヤー／プリセット共通詳細描画。"""

from __future__ import annotations

from collections.abc import Mapping

from .basic import (
    body_columns,
    detail_operator,
    detail_operator_menu_enum,
    prop_if,
    prop_pair,
    set_operator_fields,
    value,
)


def draw_text_body(layout, _context, session, mode, *, preset_list_owner=None) -> None:
    entry = session.target.data
    preset_mode = str(getattr(mode, "value", mode)) == "preset"
    columns = body_columns(layout, session)
    primary = columns[0]
    secondary = columns[min(1, len(columns) - 1)]

    _draw_linked_balloon_preset(
        primary,
        _context,
        entry,
        session,
        list_owner=preset_list_owner,
    )
    _draw_typography(secondary, entry)
    _draw_stroke(secondary, entry)
    _draw_ruby(secondary, entry, preset_mode, session)


def _draw_linked_balloon_preset(layout, context, entry, session, *, list_owner=None) -> None:
    """モジュール大域の対象IDを書き換えず、固定済み対象IDへ適用する。"""

    box = layout.box()
    box.label(text="リンクフキダシプリセット", icon="LINKED")
    if list_owner is not None:
        from ...operators import detail_preset_apply_op

        count = detail_preset_apply_op.sync_detail_linked_balloon_preset_list(
            list_owner,
            context,
            session,
        )
        if count >= 0:
            rows = max(2, min(5, count))
            box.template_list(
                "BMANGA_UL_detail_linked_balloon_presets",
                "linked_balloon",
                list_owner,
                "detail_linked_balloon_items",
                list_owner,
                "detail_linked_balloon_index",
                rows=rows,
                maxrows=5,
            )
            return
    selected = str(value(entry, "linked_balloon_preset", "") or "")
    operator = detail_operator_menu_enum(
        box,
        "bmanga.detail_text_linked_balloon_set",
        "preset_name",
        text=selected or "なし",
    )
    set_operator_fields(
        operator,
        session_token=session.token,
        target_id=session.target.stable_id,
    )


def _draw_typography(layout, entry) -> None:
    box = layout.box()
    box.label(text="フォント・組版", icon="FONT_DATA")
    prop_if(box, entry, "font", text="基本フォント")
    prop_pair(
        box,
        entry,
        "font_size_unit",
        "font_size_value",
        font_size_unit={"text": ""},
        font_size_value={"text": "サイズ"},
    )
    prop_pair(
        box,
        entry,
        "font_bold",
        "font_italic",
        font_bold={"text": "太字", "toggle": True},
        font_italic={"text": "斜体", "toggle": True},
    )
    prop_if(box, entry, "color", text="色")
    prop_if(box, entry, "writing_mode", text="書字方向")
    if str(value(entry, "writing_mode", "horizontal") or "horizontal") == "vertical":
        prop_if(box, entry, "tatechuyoko_auto", text="縦中横の自動適用")
    prop_pair(box, entry, "line_height", "letter_spacing")


def _draw_stroke(layout, entry) -> None:
    box = layout.box()
    box.label(text="フチ")
    prop_if(box, entry, "stroke_enabled", text="フチ")
    content = box.column(align=True)
    content.enabled = bool(value(entry, "stroke_enabled", False))
    prop_if(content, entry, "stroke_width_mm", text="幅")
    prop_if(content, entry, "stroke_color", text="色")


def _draw_ruby(layout, entry, preset_mode: bool, session) -> None:
    target = session.target
    box = layout.box()
    box.label(text="ルビ・部分スタイル")
    if not preset_mode:
        _draw_counts(box, entry)
    prop_pair(box, entry, "ruby_size_percent", "ruby_gap_em")
    prop_pair(box, entry, "ruby_letter_spacing", "ruby_line_height")
    prop_pair(box, entry, "ruby_align", "ruby_small_kana")
    prop_if(box, entry, "ruby_font_preset", text="ルビ用フォント")
    prop_if(box, entry, "ruby_default_style", text="ルビ種類")
    if not preset_mode:
        row = box.row(align=True)
        add = detail_operator(
            row,
            "bmanga.detail_text_ruby_add",
            text="追加・編集",
            icon="ADD",
        )
        clear = detail_operator(
            row,
            "bmanga.detail_text_ruby_clear",
            text="全解除",
            icon="TRASH",
        )
        # 詳細対象のstable_idは誤対象防止のため「ページID:テキストID」。
        # 既存のルビ操作はpage_idとページ内text_idを別々に受け取る。
        fields = {
            "session_token": session.token,
            "page_id": _page_id(target.params),
            "text_id": str(value(entry, "id", "") or ""),
        }
        set_operator_fields(
            add,
            **fields,
            target_id=target.stable_id,
            start=0,
            length=max(1, len(str(value(entry, "body", "") or ""))),
        )
        set_operator_fields(
            clear,
            **fields,
            target_id=target.stable_id,
            start=0,
            end=len(str(value(entry, "body", "") or "")),
        )


def _draw_counts(layout, entry) -> None:
    row = layout.row(align=True)
    row.label(text=f"ルビ: {_count(entry, 'ruby_spans')} 件")
    row.label(text=f"部分フォント: {_count(entry, 'font_spans')} 件")
    row = layout.row(align=True)
    row.label(text=f"部分スタイル: {_count(entry, 'style_spans')} 件")
    row.label(text=f"縦中横: {_count(entry, 'tatechuyoko_ranges')} 件")


def _count(entry, name: str) -> int:
    return len(value(entry, name, ()) or ())


def _page_id(params) -> str:
    if isinstance(params, Mapping):
        return str(params.get("page_id", "") or "")
    return str(getattr(params, "page_id", "") or "") if params is not None else ""


__all__ = ["draw_text_body"]
