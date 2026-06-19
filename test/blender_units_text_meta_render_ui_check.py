"""Blender実機用: 単位切替、テキストメタ情報、魚眼UIの監査."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_package(package_name: str, package_root: Path):
    spec = importlib.util.spec_from_file_location(
        package_name,
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


class _CaptureLayout:
    def __init__(self, enabled: bool = True, sink=None) -> None:
        self.enabled = enabled
        self.sink = [] if sink is None else sink

    def prop(self, _obj, attr: str, *args, **kwargs):
        self.sink.append((attr, str(kwargs.get("text", "") or attr), bool(self.enabled)))
        return None

    def label(self, *args, **kwargs):
        self.sink.append(("label", str(kwargs.get("text", "") or ""), bool(self.enabled)))
        return None

    def column(self, *args, **kwargs):
        return _CaptureLayout(bool(self.enabled), self.sink)

    def row(self, *args, **kwargs):
        return _CaptureLayout(bool(self.enabled), self.sink)

    def box(self, *args, **kwargs):
        return _CaptureLayout(bool(self.enabled), self.sink)


def _assert_paper_units() -> None:
    from bmanga_units_meta_dev.io import schema
    from bmanga_units_meta_dev.utils.geom import pt_to_q, px_to_mm, q_to_pt

    work = bpy.context.scene.bmanga_work
    work.loaded = True
    paper = work.paper
    paper.dpi = 100
    paper.canvas_width_mm = 25.4
    paper.unit = "mm"
    assert abs(paper.canvas_width_value - 25.4) < 0.001
    paper.unit = "inch"
    assert abs(paper.canvas_width_value - 1.0) < 0.001
    paper.canvas_width_value = 2.0
    assert abs(paper.canvas_width_mm - 50.8) < 0.001
    paper.unit = "px"
    assert abs(paper.canvas_width_value - 200.0) < 0.001
    paper.canvas_width_value = 300.0
    assert abs(paper.canvas_width_mm - px_to_mm(300.0, 100)) < 0.001

    item = work.work_info.display_work_name
    item.font_size_q = 20.0
    assert abs(item.font_size_pt - q_to_pt(20.0)) < 0.001
    item.font_size_unit = "pt"
    assert abs(item.font_size_value - q_to_pt(20.0)) < 0.001
    item.font_size_value = 12.0
    assert abs(item.font_size_q - pt_to_q(12.0)) < 0.001
    item.enabled = True
    data = schema.display_item_to_dict(item)
    assert data["fontSizeUnit"] == "pt", data
    restored = work.work_info.display_episode
    schema.display_item_from_dict(restored, data)
    assert restored.font_size_unit == "pt"
    assert abs(restored.font_size_pt - 12.0) < 0.001
    assert bpy.ops.bmanga.work_meta_dialog() == {"FINISHED"}


def _assert_text_size_and_meta_dialog() -> None:
    from bmanga_units_meta_dev.io import schema
    from bmanga_units_meta_dev.utils.geom import pt_to_q, q_to_pt

    work = bpy.context.scene.bmanga_work
    page = work.pages.add()
    page.id = "p0001"
    page.title = "1ページ"
    work.active_page_index = 0
    entry = page.texts.add()
    entry.id = "t001"
    entry.body = "本文"
    page.active_text_index = 0
    entry.font_size_q = 20.0
    assert abs(entry.font_size_pt - q_to_pt(20.0)) < 0.001
    entry.font_size_unit = "pt"
    assert abs(entry.font_size_value - q_to_pt(20.0)) < 0.001
    entry.font_size_value = 10.0
    assert abs(entry.font_size_q - pt_to_q(10.0)) < 0.001
    entry.font_size_unit = "q"
    assert abs(entry.font_size_value - pt_to_q(10.0)) < 0.001
    entry.speaker_type = "thought"
    entry.speaker_name = "話者"
    data = schema.text_entry_to_dict(entry)
    assert data["fontSizeUnit"] == "q", data
    assert abs(float(data["fontSizePt"]) - 10.0) < 0.001, data
    restored = page.texts.add()
    schema.text_entry_from_dict(restored, data)
    assert restored.font_size_unit == "q"
    assert abs(restored.font_size_pt - 10.0) < 0.001
    assert restored.speaker_type == "thought"
    assert restored.speaker_name == "話者"
    result = bpy.ops.bmanga.text_meta_dialog()
    assert result == {"FINISHED"}, result


def _assert_render_ui_and_native_setup() -> None:
    from bmanga_render_units_meta import command_ui, core, eevr_bridge

    labels = [label for _identifier, label, _desc in core.COMMAND_TYPE_ITEMS]
    assert not any("eeVR" in label for label in labels), labels
    command = bpy.context.scene.bmanga_render_state.presets.add().commands.add()
    command.command_type = "FISHEYE_RENDER_IMAGE_OR_LAYER"
    command.node_group_name = "出力_背景"
    command.label_contains = "背景線画"
    command.folder_path = str(ROOT)
    command.text_value = "魚眼"

    scene = bpy.context.scene
    scene.fisheye_layout_mode = False
    capture = _CaptureLayout()
    command_ui.draw_command(capture, command, bpy.context)
    fish_props = [item for item in capture.sink if item[0] in {"folder_path", "text_value"}]
    assert fish_props and all(item[2] is False for item in fish_props), fish_props

    scene.fisheye_layout_mode = True
    capture = _CaptureLayout()
    command_ui.draw_command(capture, command, bpy.context)
    fish_props = [item for item in capture.sink if item[0] in {"folder_path", "text_value"}]
    assert fish_props and all(item[2] is True for item in fish_props), fish_props

    cam_data = bpy.data.cameras.new("魚眼監査カメラ")
    cam = bpy.data.objects.new("魚眼監査カメラ", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    assert eevr_bridge.setup(scene, cam, output_dir="//passes/", output_name="監査")
    assert cam.data.type == "PANO"
    assert scene["bmanga_render_fisheye_output_name"] == "監査"


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bmanga = None
    render = None
    try:
        bmanga = _load_package("bmanga_units_meta_dev", ROOT)
        _assert_paper_units()
        _assert_text_size_and_meta_dialog()
        render = _load_package("bmanga_render_units_meta", ROOT / "addons" / "b_manga_render")
        _assert_render_ui_and_native_setup()
        print("BMANGA_UNITS_TEXT_META_RENDER_UI_OK")
    finally:
        if render is not None:
            render.unregister()
        if bmanga is not None:
            bmanga.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
