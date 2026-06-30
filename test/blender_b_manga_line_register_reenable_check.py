"""Blender実機用: B-MANGA Lineの再有効化登録を確認."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "addons" / "b_manga_line"


def _load_package(package_name: str):
    spec = importlib.util.spec_from_file_location(
        package_name,
        PACKAGE_ROOT / "__init__.py",
        submodule_search_locations=[str(PACKAGE_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _assert_registered() -> None:
    from b_manga_line_reenable_check import core

    assert bool(getattr(core.BMangaLineSettings, "is_registered", False))
    assert getattr(bpy.types.Object, "bmanga_line_settings", None) is not None
    assert getattr(bpy.types.Scene, "bmanga_line_camera", None) is not None
    assert getattr(bpy.types, "BMANGA_LINE_PT_main", None) is not None
    subpanels = (
        "BMANGA_LINE_PT_presets",
        "BMANGA_LINE_PT_basic",
        "BMANGA_LINE_PT_outline",
        "BMANGA_LINE_PT_camera",
        "BMANGA_LINE_PT_inner_line",
        "BMANGA_LINE_PT_intersection",
    )
    for name in subpanels:
        panel = getattr(bpy.types, name, None)
        assert panel is not None, f"{name} が登録されていません"
        assert panel.bl_parent_id == "BMANGA_LINE_PT_main"


def _assert_unregistered() -> None:
    from b_manga_line_reenable_check import core

    assert not bool(getattr(core.BMangaLineSettings, "is_registered", False))
    assert getattr(bpy.types.Object, "bmanga_line_settings", None) is None
    assert getattr(bpy.types.Scene, "bmanga_line_camera", None) is None


class _CaptureLayout:
    def __init__(self, records: dict[str, list[str]] | None = None) -> None:
        self.records = records or {"props": [], "operators": [], "labels": [], "curves": []}
        self.enabled = True
        self.alignment = "LEFT"
        self.scale_y = 1.0

    def row(self, **_kwargs):
        return _CaptureLayout(self.records)

    def column(self, **_kwargs):
        return _CaptureLayout(self.records)

    def box(self):
        return _CaptureLayout(self.records)

    def separator(self) -> None:
        return None

    def label(self, text: str = "", **_kwargs) -> None:
        self.records["labels"].append(text)

    def prop(self, _data, prop_name: str, **_kwargs) -> None:
        self.records["props"].append(prop_name)

    def operator(self, operator_id: str, **_kwargs):
        self.records["operators"].append(operator_id)
        return SimpleNamespace()

    def template_list(self, *_args, **_kwargs) -> None:
        self.records["labels"].append("template_list")

    def template_curve_mapping(self, *_args, **_kwargs) -> None:
        self.records["curves"].append("template_curve_mapping")


def _assert_panels_draw_items() -> None:
    from b_manga_line_reenable_check import core, panels

    props = core.BMangaLineSettings.bl_rna.properties
    assert props["use_camera_compensation"].name == "線幅の均一化（オブジェクト単位）"
    assert props["use_uniform_line_width"].name == "線幅の均一化（頂点単位）"

    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    records = {"props": [], "operators": [], "labels": [], "curves": []}
    dummy = SimpleNamespace(layout=_CaptureLayout(records))
    for panel_cls in (
        panels.BMANGA_LINE_PT_main,
        panels.BMANGA_LINE_PT_presets,
        panels.BMANGA_LINE_PT_basic,
        panels.BMANGA_LINE_PT_outline,
        panels.BMANGA_LINE_PT_camera,
        panels.BMANGA_LINE_PT_inner_line,
        panels.BMANGA_LINE_PT_intersection,
    ):
        assert panel_cls.poll(bpy.context) if hasattr(panel_cls, "poll") else True
        panel_cls.draw(dummy, bpy.context)

    for prop_name in (
        "outline_thickness_mm",
        "outline_color",
        "exclude_sheet_meshes",
        "use_camera_compensation",
        "use_uniform_line_width",
        "edge_smooth_factor",
        "inner_edge_smooth_factor",
        "intersection_edge_smooth_factor",
        "inner_line_enabled",
        "use_marked_inner_edges",
        "use_inner_line_creation_limit",
        "inner_line_creation_max_distance",
        "intersection_enabled",
        "use_intersection_creation_limit",
        "intersection_creation_max_distance",
    ):
        assert prop_name in records["props"], f"{prop_name} がパネルにありません"
    assert "BMANGA_LINE_PT_width_details" not in dir(panels)
    bool_props = [
        prop.identifier
        for prop in props
        if getattr(prop, "type", None) == "BOOLEAN"
        and prop.identifier != "rna_type"
    ]
    for prop_name in bool_props:
        assert getattr(obj.bmanga_line_settings, prop_name) is False, (
            f"{prop_name} の初期値がオフではありません"
        )
    preset = bpy.context.scene.bmanga_line_presets.add()
    preset_bool_props = [
        prop.identifier
        for prop in preset.bl_rna.properties
        if getattr(prop, "type", None) == "BOOLEAN"
        and prop.identifier != "rna_type"
    ]
    for prop_name in preset_bool_props:
        assert getattr(preset, prop_name) is False, (
            f"プリセットの {prop_name} の初期値がオフではありません"
        )
    camera_props = [
        "use_camera_compensation",
        "use_uniform_line_width",
        "use_camera_culling",
    ]
    camera_indices = [records["props"].index(item) for item in camera_props]
    assert camera_indices == sorted(camera_indices), "カメラ設定の線幅項目の順序が違います"
    assert "intersection_target" not in records["props"], "交差対象欄が残っています"
    for operator_id in (
        "bmanga_line.apply",
        "bmanga_line.set_visibility",
        "bmanga_line.remove",
    ):
        assert operator_id in records["operators"], f"{operator_id} がパネルにありません"


def _assert_restricted_data_register_safe() -> None:
    from b_manga_line_reenable_check import outline_setup

    real_bpy = outline_setup.bpy
    fake_handlers = types.SimpleNamespace(load_post=[])
    fake_bpy = types.SimpleNamespace(
        app=types.SimpleNamespace(handlers=fake_handlers),
        data=types.SimpleNamespace(),
    )
    outline_setup.bpy = fake_bpy
    try:
        assert outline_setup.ensure_aov_passes() == 0
        outline_setup.register()
        assert outline_setup._on_load_post in fake_handlers.load_post
        outline_setup.unregister()
        assert outline_setup._on_load_post not in fake_handlers.load_post
    finally:
        outline_setup.bpy = real_bpy


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = _load_package("b_manga_line_reenable_check")
    try:
        _assert_restricted_data_register_safe()
        mod.register()
        _assert_registered()
        _assert_panels_draw_items()
        mod.register()
        _assert_registered()
        mod.unregister()
        _assert_unregistered()
        mod.register()
        _assert_registered()
        print("BMANGA_LINE_REGISTER_REENABLE_OK")
    finally:
        try:
            mod.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
