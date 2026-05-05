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


def draw_command(layout, command) -> None:
    layout.prop(command, "enabled")
    layout.prop(command, "name")
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
            layout.prop(command, "folder_path", text="魚眼出力フォルダ")
            layout.prop(command, "text_value", text="魚眼出力画像名")
    elif kind == "EEVR_SETUP":
        layout.prop(command, "folder_path", text="eeVR出力フォルダ")
        layout.prop(command, "text_value", text="eeVR出力画像名")
    elif kind in {"EEVR_RENDER_IMAGE", "EEVR_RENDER_FACES", "EEVR_ASSEMBLE"}:
        layout.prop(command, "folder_path", text="eeVR出力フォルダ")
        layout.prop(command, "text_value", text="eeVR出力画像名")
    elif kind == "OPERATOR":
        layout.prop(command, "operator_idname")
