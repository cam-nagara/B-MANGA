"""Blender 実機用: 効果線プリセットの選択・管理 UI を確認。"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_effect_line_preset_ui"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _sub(path: str):
    __import__(f"{MOD_NAME}.{path}")
    return sys.modules[f"{MOD_NAME}.{path}"]


class _Layout:
    def __init__(self, props=None, labels=None, ops=None, op_instances=None):
        self.props = [] if props is None else props
        self.labels = [] if labels is None else labels
        self.ops = [] if ops is None else ops
        self.op_instances = [] if op_instances is None else op_instances
        self.enabled = True

    def box(self):
        return _Layout(self.props, self.labels, self.ops, self.op_instances)

    def row(self, align: bool = False):
        return _Layout(self.props, self.labels, self.ops, self.op_instances)

    def column(self, align: bool = False):
        return _Layout(self.props, self.labels, self.ops, self.op_instances)

    def grid_flow(self, **_kwargs):
        return _Layout(self.props, self.labels, self.ops, self.op_instances)

    def separator(self, **_kwargs):
        return None

    def label(self, text: str = "", **_kwargs):
        self.labels.append(str(text))
        return None

    def prop(self, _owner, attr: str, **_kwargs):
        self.props.append(str(attr))
        return None

    def prop_search(self, _owner, attr: str, *_args, **_kwargs):
        self.props.append(str(attr))
        return None

    def operator(self, op_id: str, **_kwargs):
        self.ops.append(str(op_id))
        op = _Op(str(op_id))
        self.op_instances.append(op)
        return op

    def template_curve_mapping(self, *_args, **_kwargs):
        return None


class _Op:
    def __init__(self, op_id: str):
        self.op_id = op_id


class _DummyTool:
    pass


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_effect_line_preset_ui_"))
    old_config = os.environ.get("BMANGA_USER_CONFIG_DIR")
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(temp_root / "config")
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "EffectLinePresetUI.bmanga"))
        assert "FINISHED" in result, result

        effect_line_presets = _sub("io.effect_line_presets")
        effect_line_preset_op = _sub("operators.effect_line_preset_op")
        coma_modal_state = _sub("operators.coma_modal_state")
        effect_line_panel = _sub("panels.effect_line_panel")
        tool_panel = _sub("panels.tool_panel")

        presets = effect_line_presets.list_all_presets(None)
        names = {preset.name for preset in presets}
        assert {"集中線", "ウニフラ", "ベタフラ", "流線", "白抜き線"} <= names, names

        wm = bpy.context.window_manager
        assert hasattr(wm, "bmanga_effect_line_tool_preset_selector")
        wm.bmanga_effect_line_tool_preset_selector = "流線"
        params = bpy.context.scene.bmanga_effect_line_params
        assert params.effect_type == "speed"
        wm.bmanga_effect_line_tool_preset_selector = "白抜き線"
        assert params.effect_type == "white_outline"

        params.effect_type = "uni_flash"
        params.brush_size_mm = 7.5
        name = effect_line_presets.unique_preset_name(None, "テスト効果線")
        effect_line_presets.save_local_preset(None, params, name, insert_after="ウニフラ")
        effect_line_preset_op._set_effect_line_preset_selector(bpy.context, name)
        params.effect_type = "focus"
        assert effect_line_preset_op.apply_selected_effect_line_preset(bpy.context)
        assert params.effect_type == "uni_flash"
        assert abs(float(params.brush_size_mm) - 7.5) < 1.0e-6

        layout = _Layout()
        effect_line_panel.draw_effect_line_preset_management(layout, bpy.context)
        assert "bmanga_effect_line_tool_preset_selector" in layout.props
        assert {
            "bmanga.effect_line_preset_add_local",
            "bmanga.effect_line_preset_rename",
            "bmanga.effect_line_preset_duplicate",
            "bmanga.effect_line_preset_delete",
        } <= set(layout.ops)
        op_by_id = {op.op_id: op for op in layout.op_instances}
        assert op_by_id["bmanga.effect_line_preset_rename"].preset_name == name
        assert op_by_id["bmanga.effect_line_preset_rename"].new_name == name
        assert op_by_id["bmanga.effect_line_preset_duplicate"].preset_name == name
        assert op_by_id["bmanga.effect_line_preset_duplicate"].new_name.startswith(f"{name} コピー")
        assert op_by_id["bmanga.effect_line_preset_delete"].preset_name == name

        tool = _DummyTool()
        coma_modal_state.set_active("effect_line_tool", tool, bpy.context)
        try:
            layout = _Layout()
            tool_panel._draw_active_tool_preset_row(layout, bpy.context)
            assert "効果線プリセット" in layout.labels
            assert "bmanga_effect_line_tool_preset_selector" in layout.props
        finally:
            coma_modal_state.clear_active("effect_line_tool", tool, bpy.context)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        if old_config is None:
            os.environ.pop("BMANGA_USER_CONFIG_DIR", None)
        else:
            os.environ["BMANGA_USER_CONFIG_DIR"] = old_config
        shutil.rmtree(temp_root, ignore_errors=True)
    print("[ok] effect line preset UI works")


if __name__ == "__main__":
    try:
        main()
        os._exit(0)
    except Exception:
        import traceback

        traceback.print_exc()
        os._exit(1)
