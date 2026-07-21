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
    "gp_tool": PresetUiSpec(
        "gp_tool",
        "グリースペンシルツールプリセット",
        "GREASEPENCIL",
        "bmanga.gp_tool_preset",
    ),
}


def preset_spec_for_target(target) -> PresetUiSpec | None:
    if target.kind == "balloon_shape":
        return _SPECS["balloon"]
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


def draw_preset_management(layout, _context, session, mode, *, list_owner=None) -> bool:
    """一覧側のアクティブ選択を参照しないプリセット欄を描画する。"""

    spec = preset_spec_for_target(session.target)
    if spec is None:
        return False
    if str(getattr(mode, "value", mode)) == "preset":
        return _draw_preset_edit_list(
            layout,
            _context,
            session,
            spec,
            list_owner=list_owner,
        )
    session_type = getattr(session, "preset_type", None)
    if session_type is not None and session_type != spec.preset_type:
        session.set_preset_context(spec.preset_type, None)

    box = layout.box()
    box.label(text=spec.label, icon=spec.icon)
    compact = _draw_compact_management(
        box,
        _context,
        session,
        spec,
        list_owner=list_owner,
    )
    if not compact:
        _draw_target_apply(box, _context, session, spec, list_owner=list_owner)
        _draw_selected_management(box, _context, session, spec)
    return True


def _draw_preset_edit_list(layout, context, session, spec: PresetUiSpec, *, list_owner=None) -> bool:
    """プリセット編集入口でも、同種の全プリセットを標準リストで表示する。"""

    if list_owner is None or not hasattr(list_owner, "sync_preset_edit_list"):
        return False
    count = list_owner.sync_preset_edit_list(context, session, spec.preset_type)
    box = layout.box()
    box.label(text=spec.label, icon=spec.icon)
    if count <= 0:
        row = box.row()
        row.enabled = False
        row.label(text="（プリセットなし）", icon="PRESET")
        return True
    rows = max(3, min(6, count))
    box.template_list(
        "BMANGA_UL_detail_presets",
        f"preset_edit_{spec.preset_type}",
        list_owner,
        "detail_preset_items",
        list_owner,
        "detail_preset_index",
        rows=rows,
        maxrows=6,
    )
    return True


def _action_row(layout, *, enabled: bool):
    row = layout.row(align=True)
    row.enabled = bool(enabled)
    return row


def _draw_compact_management(
    layout,
    context,
    session,
    spec: PresetUiSpec,
    *,
    list_owner=None,
) -> bool:
    """サイドバーと同じ標準リスト＋右側縦ボタンだけを描画する。"""

    if list_owner is None:
        return False
    from ...operators import detail_preset_apply_op

    count = detail_preset_apply_op.sync_detail_preset_list(
        list_owner,
        context,
        session,
        spec.preset_type,
    )
    if count < 0:
        return False

    row = layout.row()
    if count == 0:
        empty = row.column()
        empty.enabled = False
        empty.label(text="（プリセットなし）", icon="PRESET")
    else:
        rows = max(3, min(6, count))
        row.template_list(
            "BMANGA_UL_detail_presets",
            spec.preset_type,
            list_owner,
            "detail_preset_items",
            list_owner,
            "detail_preset_index",
            rows=rows,
            maxrows=6,
        )

    selected = _manageable_selection(session, spec)
    has_selected = bool(selected)
    common = {
        "session_token": session.token,
        "target_kind": session.target.kind,
        "target_id": session.target.stable_id,
        "preset_type": spec.preset_type,
    }
    actions = row.column(align=True)
    actions.operator_context = "INVOKE_DEFAULT"
    add = detail_operator(
        _action_row(actions, enabled=True),
        "bmanga.detail_preset_add",
        text="",
        icon="ADD",
    )
    set_operator_fields(add, **common)
    delete = detail_operator(
        _action_row(actions, enabled=has_selected),
        "bmanga.detail_preset_delete",
        text="",
        icon="REMOVE",
    )
    set_operator_fields(delete, **common, preset_name=selected)
    actions.separator()
    move_up = detail_operator(
        _action_row(actions, enabled=has_selected),
        "bmanga.detail_preset_move",
        text="",
        icon="TRIA_UP",
    )
    set_operator_fields(move_up, **common, preset_name=selected, direction="UP")
    move_down = detail_operator(
        _action_row(actions, enabled=has_selected),
        "bmanga.detail_preset_move",
        text="",
        icon="TRIA_DOWN",
    )
    set_operator_fields(move_down, **common, preset_name=selected, direction="DOWN")
    actions.separator()
    overwrite = detail_operator(
        _action_row(actions, enabled=has_selected),
        "bmanga.preset_detail_edit",
        text="",
        icon="FILE_TICK",
    )
    set_operator_fields(
        overwrite,
        preset_type=spec.preset_type,
        preset_name=selected,
        parent_session_token=session.token,
        parent_target_kind=session.target.kind,
        parent_target_id=session.target.stable_id,
    )
    rename = detail_operator(
        _action_row(actions, enabled=has_selected),
        "bmanga.detail_preset_rename",
        text="",
        icon="GREASEPENCIL",
    )
    set_operator_fields(rename, **common, preset_name=selected, new_name=selected)
    duplicate = detail_operator(
        _action_row(actions, enabled=has_selected),
        "bmanga.detail_preset_duplicate",
        text="",
        icon="DUPLICATE",
    )
    set_operator_fields(
        duplicate,
        **common,
        preset_name=selected,
        new_name=f"{selected} コピー" if selected else "",
    )
    return True


def _draw_target_apply(
    layout,
    context,
    session,
    spec: PresetUiSpec,
    *,
    list_owner=None,
) -> None:
    if list_owner is not None:
        from ...operators import detail_preset_apply_op

        count = detail_preset_apply_op.sync_detail_preset_list(
            list_owner,
            context,
            session,
            spec.preset_type,
        )
        if count >= 0:
            if count == 0:
                empty = layout.row()
                empty.enabled = False
                empty.label(text="（プリセットなし）", icon="PRESET")
                return
            rows = max(3, min(6, count))
            layout.template_list(
                "BMANGA_UL_detail_presets",
                spec.preset_type,
                list_owner,
                "detail_preset_items",
                list_owner,
                "detail_preset_index",
                rows=rows,
                maxrows=6,
            )
            return

    # 公開描画APIを単体利用する互換経路。通常の詳細ダイアログは必ず上のUIListを使う。
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
    selected = _manageable_selection(session, spec)
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


def _manageable_selection(session, spec: PresetUiSpec) -> str:
    if spec.preset_type != "balloon":
        return str(getattr(session, "preset_selection", "") or "")
    # フキダシは形状/線種/色などスタイル全体がプリセット保存対象
    # (2026-07-20 拡張) のため、custom_preset_name が現在適用中のプリセット名を
    # shape の値に関わらず一貫して指す (shape=="custom" に限定していた旧仕様を
    # 廃止)。組み込み形状を直接選んだ場合は apply_balloon_preset_reference が
    # custom_preset_name を空にするため、その場合は管理対象なしになる。
    return str(value(session.target.data, "custom_preset_name", "") or "")


__all__ = ["PresetUiSpec", "draw_preset_management", "preset_spec_for_target"]
