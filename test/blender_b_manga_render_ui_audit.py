"""Blender実機用: B-MANGA Render UI導線とカード設定の監査."""

from __future__ import annotations

import importlib.util
import html
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(
    os.environ.get("BMANGA_RENDER_UI_AUDIT_OUT", "")
    or (ROOT / ".codex" / "visual" / "bmanga_render_ui_audit")
)


def _load_render_package():
    package_root = ROOT / "addons" / "b_manga_render"
    spec = importlib.util.spec_from_file_location(
        "bmanga_render_ui_audit",
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_render_ui_audit"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


class CaptureLayout:
    def __init__(self) -> None:
        self.props: list[str] = []
        self.labels: list[str] = []
        self.operators: list[str] = []
        self.operator_contexts: list[tuple[str, str]] = []
        self.templates: list[str] = []
        self.enabled = True
        self.alignment = ""
        self.operator_context = "EXEC_DEFAULT"

    def prop(self, obj, attr: str, *args, **kwargs):
        label = str(kwargs.get("text", "") or attr)
        self.props.append(f"{attr}:{label}:{getattr(obj, attr, '')}")
        return None

    def label(self, *args, **kwargs):
        self.labels.append(str(kwargs.get("text", "") or ""))
        return None

    def operator(self, operator_id: str, *args, **kwargs):
        self.operators.append(operator_id)
        self.operator_contexts.append((operator_id, str(self.operator_context)))
        return SimpleNamespace()

    def template_list(self, *args, **kwargs):
        self.templates.append(str(args[0]) if args else "")
        return None

    def separator(self, *args, **kwargs):
        return None

    def box(self):
        return self

    def row(self, *args, **kwargs):
        return self

    def column(self, *args, **kwargs):
        return self

    def grid_flow(self, *args, **kwargs):
        return self

    def split(self, *args, **kwargs):
        return self


def _extra_required_props(command_type: str) -> set[str]:
    if command_type == "SET_VIEW_LAYER":
        return {"view_layer_name", "view_layer_enabled"}
    if command_type == "SET_COLLECTION_EXCLUDE":
        return {"view_layer_name", "collection_name", "exclude_collection"}
    if command_type == "SET_NODE_MUTE":
        return {"node_name", "mute"}
    if command_type == "SET_OUTPUT_GROUP":
        return {"node_group_name", "label_contains", "mute"}
    if command_type == "SET_AOV_INPUT":
        return {"node_group_name", "input_name", "float_value"}
    if command_type == "SET_OUTPUT_NAME":
        return {"text_value"}
    if command_type == "SET_OUTPUT_FOLDER":
        return {"folder_path"}
    if command_type == "RENDER":
        return {"engine", "sample_count"}
    if command_type == "RENDER_LAYER":
        return {"node_group_name", "label_contains", "engine", "sample_count"}
    if command_type.startswith("FISHEYE_"):
        return {"node_group_name", "label_contains", "engine", "sample_count", "folder_path", "text_value"}
    if command_type.startswith("EEVR_"):
        return {"folder_path", "text_value"}
    if command_type == "OPERATOR":
        return {"operator_idname"}
    return set()


def _assert_command_ui(mod) -> dict:
    from bmanga_render_ui_audit import command_ui, core

    state = bpy.context.scene.bmanga_render_state
    preset = state.presets.add()
    preset.name = "UI監査"
    results: dict[str, dict] = {}
    for identifier, _label, _description in core.COMMAND_TYPE_ITEMS:
        command = preset.commands.add()
        command.command_type = identifier
        command.name = identifier
        command.name_auto = False
        command.node_group_name = "出力_背景"
        command.label_contains = "パス"
        command.folder_path = str(OUT_DIR)
        command.text_value = "監査"
        command.input_name = "落ち影切替"
        command.node_name = "背景"
        command.collection_name = "コマ枠"
        command.view_layer_name = "背景"
        command.operator_idname = "render.render"
        capture = CaptureLayout()
        command_ui.draw_command(capture, command)
        prop_names = {item.split(":", 1)[0] for item in capture.props}
        required = {"enabled", "name", "command_type"} | _extra_required_props(identifier)
        missing = sorted(required - prop_names)
        label = command_ui.command_type_label(identifier)
        assert label and label != identifier, identifier
        assert not missing, (identifier, missing, sorted(prop_names))
        results[identifier] = {
            "label": label,
            "props": sorted(prop_names),
            "summary": command_ui.command_summary(command),
        }
    return results


def _assert_panel_access(mod) -> dict:
    assert getattr(bpy.types, "BMANGA_RENDER_PT_main", None) is not None
    assert getattr(bpy.types, "BMANGA_RENDER_PT_node", None) is not None
    main = bpy.types.BMANGA_RENDER_PT_main
    node = bpy.types.BMANGA_RENDER_PT_node
    assert main.bl_space_type == "VIEW_3D"
    assert main.bl_region_type == "UI"
    assert main.bl_category == "B-MANGA Render"
    assert node.bl_space_type == "NODE_EDITOR"
    assert node.bl_region_type == "UI"
    assert node.bl_category == "B-MANGA Render"
    result = bpy.ops.bmanga_render.load_builtin_presets(reset=True)
    assert result == {"FINISHED"}, result
    state = bpy.context.scene.bmanga_render_state
    assert len(state.presets) >= 30
    assert all(not command.name.startswith("未設定") for preset in state.presets for command in preset.commands)

    capture = CaptureLayout()
    mod.panels.draw_main_panel(capture, bpy.context)
    assert "BMANGA_RENDER_UL_presets" in capture.templates
    assert "BMANGA_RENDER_UL_commands" in capture.templates
    for required_op in (
        "bmanga_render.preset_run",
        "bmanga_render.preset_settings",
        "bmanga_render.command_add",
        "bmanga_render.command_remove",
        "bmanga_render.command_move",
    ):
        assert required_op in capture.operators, required_op
    assert any(item.startswith("sound_enabled:") for item in capture.props)
    return {
        "entry_points": ["3Dビュー > サイドバー > B-MANGA Render", "ノードエディター > サイドバー > B-MANGA Render"],
        "preset_count": len(state.presets),
        "command_count": sum(len(preset.commands) for preset in state.presets),
        "panel_props": capture.props[:80],
        "panel_labels": capture.labels[:80],
    }


def _assert_resolution_modes() -> dict:
    scene = bpy.context.scene
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.original_resolution_x = 0
    scene.original_resolution_y = 0
    scene.preview_scale_percentage = 12.5
    scene.fisheye_layout_mode = True
    assert (scene.render.resolution_x, scene.render.resolution_y) == (1920, 1920)
    scene.reduction_mode = True
    assert (scene.render.resolution_x, scene.render.resolution_y) == (240, 240)
    scene.fisheye_layout_mode = False
    assert (scene.render.resolution_x, scene.render.resolution_y) == (240, 135)
    scene.reduction_mode = False
    assert (scene.render.resolution_x, scene.render.resolution_y) == (1920, 1080)
    return {
        "original": [scene.original_resolution_x, scene.original_resolution_y],
        "current": [scene.render.resolution_x, scene.render.resolution_y],
    }


def _make_visual_sheet(access: dict, command_results: dict, resolution: dict) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    width = 1320
    row_h = 26
    height = 260 + row_h * len(command_results)
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]

    def text(x: int, y: int, value: str, size: int = 14, weight: str = "400", fill: str = "#222") -> None:
        escaped = html.escape(str(value), quote=False)
        lines.append(
            f'<text x="{x}" y="{y}" font-family="Yu Gothic, Meiryo, sans-serif" '
            f'font-size="{size}" font-weight="{weight}" fill="{fill}">{escaped}</text>'
        )

    y = 40
    text(24, y, "B-MANGA Render UI / アクションリスト 仕様監査", 24, "700", "#000")
    y += 44
    text(24, y, "入口: " + " / ".join(access["entry_points"]), 17, "500", "#000")
    y += 28
    text(24, y, f"プリセット: {access['preset_count']} / カード総数: {access['command_count']} / 解像度復元: {resolution['current']}", 17, "500", "#000")
    y += 30
    text(24, y, "カード内容はリストで選択し、追加・削除・並び替えなどの操作はリスト右側のボタンから行う。", 13, "400", "#333")
    y += 34
    lines.append(f'<line x1="24" y1="{y - 20}" x2="{width - 24}" y2="{y - 20}" stroke="#d7d7d7"/>')
    text(24, y, "コマンド種類", 17, "700", "#000")
    text(360, y, "設定項目", 17, "700", "#000")
    text(1040, y, "要約", 17, "700", "#000")
    y += 28
    for identifier, data in command_results.items():
        lines.append(f'<rect x="18" y="{y - 18}" width="{width - 36}" height="{row_h}" fill="#f7f7f7" opacity="{0.55 if (y // row_h) % 2 else 0}"/>')
        text(24, y, data["label"], 13, "400", "#000")
        text(360, y, ", ".join(data["props"])[:105], 13, "400", "#333")
        text(1040, y, str(data["summary"])[:38], 13, "400", "#333")
        y += row_h
    lines.append("</svg>")
    path = OUT_DIR / "b_manga_render_ui_audit.svg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = None
    try:
        mod = _load_render_package()
        access = _assert_panel_access(mod)
        command_results = _assert_command_ui(mod)
        resolution = _assert_resolution_modes()
        visual_path = _make_visual_sheet(access, command_results, resolution)
        result_path = OUT_DIR / "b_manga_render_ui_audit.json"
        result_path.write_text(
            json.dumps(
                {
                    "access": access,
                    "command_results": command_results,
                    "resolution": resolution,
                    "visual": visual_path,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"BMANGA_RENDER_UI_AUDIT_OK visual={visual_path}")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
