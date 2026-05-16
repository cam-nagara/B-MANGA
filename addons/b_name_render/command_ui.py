"""Shared UI helpers for B-Name-Render command cards."""

from __future__ import annotations

from . import core


def command_type_label(command_type: str) -> str:
    for identifier, label, _description in core.COMMAND_TYPE_ITEMS:
        if identifier == command_type:
            return label
    return str(command_type or "")


def command_summary(command) -> str:
    kind = str(getattr(command, "command_type", "") or "")
    if kind == "SET_VIEW_LAYER":
        state = "有効" if bool(getattr(command, "view_layer_enabled", False)) else "無効"
        return f"{getattr(command, 'view_layer_name', '')} / {state}"
    if kind == "SET_COLLECTION_EXCLUDE":
        state = "除外" if bool(getattr(command, "exclude_collection", False)) else "表示"
        view_layer = str(getattr(command, "view_layer_name", "") or "")
        suffix = f" / {view_layer}" if view_layer else ""
        return f"{getattr(command, 'collection_name', '')}{suffix} / {state}"
    if kind == "SET_NODE_MUTE":
        state = "ミュート" if bool(getattr(command, "mute", False)) else "ミュート解除"
        return f"{getattr(command, 'node_name', '')} / {state}"
    if kind in {"SET_OUTPUT_GROUP", "RENDER_LAYER", "FISHEYE_RENDER_IMAGE_OR_LAYER", "FISHEYE_RENDER_FACES_OR_LAYER", "FISHEYE_ASSEMBLE_OR_LAYER"}:
        return f"{getattr(command, 'node_group_name', '')} / {getattr(command, 'label_contains', '')}"
    if kind == "SET_AOV_INPUT":
        return f"{getattr(command, 'node_group_name', '')} / {getattr(command, 'input_name', '')}={getattr(command, 'float_value', 0.0):g}"
    if kind == "SET_OUTPUT_NAME":
        return str(getattr(command, "text_value", "") or "")
    if kind == "SET_OUTPUT_FOLDER":
        return str(getattr(command, "folder_path", "") or "")
    if kind in {"RENDER", "RENDER_LAYER"}:
        return f"{getattr(command, 'engine', '')} / {getattr(command, 'sample_count', 1)}"
    if kind.startswith("EEVR_"):
        folder = str(getattr(command, "folder_path", "") or "")
        image = str(getattr(command, "text_value", "") or "")
        return " / ".join(part for part in (folder, image) if part)
    if kind == "OPERATOR":
        return str(getattr(command, "operator_idname", "") or "")
    return ""


def auto_command_name(command) -> str:
    """カードの設定内容から表示名を自動生成する."""
    label = command_type_label(command.command_type)
    summary = command_summary(command)
    return f"{label}: {summary}" if summary else label


def display_name(command) -> str:
    """リスト等に表示するカード名 (自動生成 ON なら設定から生成)."""
    if bool(getattr(command, "name_auto", True)):
        return auto_command_name(command)
    manual = str(getattr(command, "name", "") or "").strip()
    return manual or auto_command_name(command)


def _is_fisheye_enabled(context) -> bool:
    scene = getattr(context, "scene", None) if context is not None else None
    return bool(
        scene is not None
        and (
            getattr(scene, "fisheye_layout_mode", False)
            or getattr(scene, "bname_coma_camera_fisheye_layout_mode", False)
        )
    )


def _draw_fisheye_output_fields(layout, command, context) -> None:
    fish = _is_fisheye_enabled(context)
    col = layout.column(align=True)
    col.enabled = fish
    col.prop(command, "folder_path", text="魚眼出力フォルダ")
    col.prop(command, "text_value", text="魚眼出力画像名")
    if not fish:
        layout.label(text="魚眼モード時のみ使用", icon="INFO")


def draw_command(layout, command, context=None) -> None:
    layout.prop(command, "enabled")
    layout.prop(command, "name_auto", text="名前を自動生成")
    if bool(getattr(command, "name_auto", True)):
        layout.label(text=f"カード名: {auto_command_name(command)}")
    else:
        layout.prop(command, "name", text="カード名")
    layout.prop(command, "command_type")
    kind = command.command_type
    if kind == "SET_VIEW_LAYER":
        layout.prop(command, "view_layer_name")
        layout.prop(command, "view_layer_enabled")
    elif kind == "SET_COLLECTION_EXCLUDE":
        layout.prop(command, "view_layer_name")
        layout.prop(command, "collection_name")
        layout.prop(command, "exclude_collection")
    elif kind == "SET_NODE_MUTE":
        layout.prop(command, "node_name")
        layout.prop(command, "mute")
    elif kind == "SET_OUTPUT_GROUP":
        layout.prop(command, "node_group_name")
        layout.prop(command, "label_contains")
        layout.prop(command, "mute")
    elif kind == "SET_AOV_INPUT":
        layout.prop(command, "node_group_name")
        layout.prop(command, "input_name")
        layout.prop(command, "float_value")
    elif kind == "SET_OUTPUT_NAME":
        layout.prop(command, "text_value", text="出力画像名")
    elif kind == "SET_OUTPUT_FOLDER":
        layout.prop(command, "folder_path", text="出力フォルダ")
    elif kind in {"RENDER", "RENDER_LAYER", "FISHEYE_RENDER_IMAGE_OR_LAYER", "FISHEYE_RENDER_FACES_OR_LAYER", "FISHEYE_ASSEMBLE_OR_LAYER"}:
        if kind != "RENDER":
            layout.prop(command, "node_group_name")
            layout.prop(command, "label_contains")
        layout.prop(command, "engine")
        layout.prop(command, "sample_count")
        if kind.startswith("FISHEYE_"):
            _draw_fisheye_output_fields(layout, command, context)
    elif kind == "EEVR_SETUP":
        _draw_fisheye_output_fields(layout, command, context)
    elif kind in {"EEVR_RENDER_IMAGE", "EEVR_RENDER_FACES", "EEVR_ASSEMBLE"}:
        _draw_fisheye_output_fields(layout, command, context)
    elif kind == "OPERATOR":
        layout.prop(command, "operator_idname")
