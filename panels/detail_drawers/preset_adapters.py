"""実レイヤーから固定対象へ適用するプリセットUI。"""

from __future__ import annotations

from dataclasses import dataclass

from .basic import (
    detail_operator,
    detail_operator_menu_enum,
    set_operator_fields,
    value,
)


@dataclass(frozen=True)
class PresetUiSpec:
    preset_type: str
    label: str
    icon: str
    operator_prefix: str


_SPECS = {
    "coma": PresetUiSpec("border", "枠線プリセット", "MESH_PLANE", "bmanga.border_preset"),
    "image_path": PresetUiSpec(
        "image_path",
        "パターンカーブプリセット",
        "CURVE_BEZCURVE",
        "bmanga.image_path_preset",
    ),
    "text": PresetUiSpec("text", "テキストプリセット", "FONT_DATA", "bmanga.text_preset"),
    "balloon": PresetUiSpec(
        "balloon",
        "フキダシプリセット",
        "MESH_CIRCLE",
        "bmanga.balloon_preset",
    ),
    "effect": PresetUiSpec(
        "effect_line",
        "効果線プリセット",
        "FORCE_FORCE",
        "bmanga.effect_line_preset",
    ),
}


def preset_spec_for_target(target) -> PresetUiSpec | None:
    if target.kind != "fill":
        return _SPECS.get(target.kind)
    fill_type = str(value(target.data, "fill_type", "solid") or "solid")
    gradient = fill_type == "gradient"
    if gradient:
        return PresetUiSpec(
            "gradient",
            "グラデーションプリセット",
            "NODE_TEXTURE",
            "bmanga.gradient_preset",
        )
    return PresetUiSpec("fill", "囲い塗りプリセット", "SNAP_FACE", "bmanga.fill_preset")


def draw_preset_management(layout, _context, session, mode) -> bool:
    """一覧側のアクティブ選択を参照しないプリセット欄を描画する。"""

    if str(getattr(mode, "value", mode)) != "actual":
        return False
    spec = preset_spec_for_target(session.target)
    if spec is None:
        return False
    session_type = getattr(session, "preset_type", None)
    if session_type is not None and session_type != spec.preset_type:
        session.set_preset_context(spec.preset_type, None)

    box = layout.box()
    box.label(text=spec.label, icon=spec.icon)
    _draw_target_apply(box, session, spec)
    _draw_selected_management(box, _context, session, spec)
    box.label(
        text="プリセットの追加・編集・管理は、この画面のキャンセルでは戻りません",
        icon="INFO",
    )
    return True


def _draw_target_apply(layout, session, spec: PresetUiSpec) -> None:
    selected = str(getattr(session, "preset_selection", "") or "")
    text = selected or "プリセットを選択して適用"
    operator = detail_operator_menu_enum(
        layout,
        "bmanga.detail_preset_apply",
        "preset_name",
        text=text,
        icon="PRESET",
    )
    set_operator_fields(
        operator,
        target_kind=session.target.kind,
        target_id=session.target.stable_id,
        stable_id=session.target.stable_id,
        stack_uid=session.target.stack_uid or "",
        preset_type=spec.preset_type,
        session_token=session.token,
    )


def _management_draft(context, session, spec: PresetUiSpec, selected: str):
    if getattr(context, "window_manager", None) is not None:
        from ...operators import detail_preset_management_op

        return detail_preset_management_op.detail_preset_draft(
            context,
            session,
            spec.preset_type,
            selected,
        )
    return None


def _draw_add_management(layout, draft, common: dict) -> None:
    add_box = layout.box()
    add_box.label(text="現在の設定を追加", icon="ADD")
    if draft is not None:
        add_box.prop(draft, "add_name")
        add_box.prop(draft, "add_description")
    add_row = add_box.row()
    add_row.operator_context = "EXEC_DEFAULT"
    add = detail_operator(
        add_row,
        "bmanga.detail_preset_add",
        text="この名前で追加",
        icon="ADD",
    )
    set_operator_fields(
        add,
        **common,
        preset_name=str(getattr(draft, "add_name", "") or ""),
        description=str(getattr(draft, "add_description", "") or ""),
    )


def _draw_overwrite_management(
    layout, session, spec: PresetUiSpec, selected: str
) -> None:
    row = layout.row(align=True)
    edit = detail_operator(
        row,
        "bmanga.preset_detail_edit",
        text="選択プリセットを現在の設定で上書き",
        icon="FILE_TICK",
    )
    set_operator_fields(
        edit,
        preset_type=spec.preset_type,
        preset_name=selected,
        parent_session_token=session.token,
        parent_target_kind=session.target.kind,
        parent_target_id=session.target.stable_id,
    )
    row.enabled = bool(selected)


def _draw_existing_management(layout, draft, common: dict, selected: str) -> None:
    rename_row = layout.row(align=True)
    if draft is not None:
        rename_row.prop(draft, "rename_name")
    rename_row.operator_context = "EXEC_DEFAULT"
    rename = detail_operator(
        rename_row,
        "bmanga.detail_preset_rename",
        text="名前を変更",
    )
    duplicate_row = layout.row(align=True)
    if draft is not None:
        duplicate_row.prop(draft, "duplicate_name")
    duplicate_row.operator_context = "EXEC_DEFAULT"
    duplicate = detail_operator(
        duplicate_row,
        "bmanga.detail_preset_duplicate",
        text="この名前で複製",
    )
    delete_row = layout.row(align=True)
    delete = detail_operator(
        delete_row,
        "bmanga.detail_preset_delete",
        text="削除",
        icon="TRASH",
    )
    fields = {**common, "preset_name": selected}
    set_operator_fields(
        rename,
        **fields,
        new_name=str(getattr(draft, "rename_name", selected) or ""),
    )
    set_operator_fields(
        duplicate,
        **fields,
        new_name=str(getattr(draft, "duplicate_name", f"{selected} コピー") or ""),
    )
    set_operator_fields(delete, **fields)

    order = layout.row(align=True)
    move_up = detail_operator(order, "bmanga.detail_preset_move", text="上へ", icon="TRIA_UP")
    move_down = detail_operator(
        order,
        "bmanga.detail_preset_move",
        text="下へ",
        icon="TRIA_DOWN",
    )
    set_operator_fields(move_up, **fields, direction="UP")
    set_operator_fields(move_down, **fields, direction="DOWN")


def _draw_selected_management(layout, context, session, spec: PresetUiSpec) -> None:
    selected = str(getattr(session, "preset_selection", "") or "")
    common = {
        "session_token": session.token,
        "target_kind": session.target.kind,
        "target_id": session.target.stable_id,
        "preset_type": spec.preset_type,
    }
    draft = _management_draft(context, session, spec, selected)
    _draw_add_management(layout, draft, common)
    _draw_overwrite_management(layout, session, spec, selected)
    if selected:
        _draw_existing_management(layout, draft, common, selected)


__all__ = ["PresetUiSpec", "draw_preset_management", "preset_spec_for_target"]
