"""Blender check: sheet meshes are excluded from generated line features."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, plane_filter  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_plane(name: str, location=(0.0, 0.0, 0.0)) -> bpy.types.Object:
    bpy.ops.mesh.primitive_plane_add(size=2.0, location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


def _make_cube(
    name: str,
    location=(0.0, 0.0, 0.0),
    scale=(1.0, 1.0, 1.0),
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    return obj


def _apply_line(*objects: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}


def _has_intersection(obj: bpy.types.Object) -> bool:
    return any(core.iter_intersection_modifiers(obj))


def _assert_no_generated_lines(obj: bpy.types.Object) -> None:
    assert obj.modifiers.get(core.GN_MODIFIER_NAME) is None, "内部線が作成されています"
    assert not _has_intersection(obj), "交差線が作成されています"


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()

        sheet = _make_plane("BML_sheet_default")
        cube = _make_cube("BML_sheet_default_cube")
        thin_box = _make_cube("BML_thin_box", location=(3.0, 0.0, 0.0), scale=(1.0, 1.0, 0.01))

        assert sheet.bmanga_line_settings.exclude_sheet_meshes is False
        assert plane_filter.is_sheet_mesh(sheet), "平面が板ポリとして検出されていません"
        assert not plane_filter.is_sheet_mesh(thin_box), "薄い箱が板ポリ扱いされています"
        sheet.bmanga_line_settings.inner_line_enabled = True
        sheet.bmanga_line_settings.intersection_enabled = True
        sheet.bmanga_line_settings.exclude_sheet_meshes = True
        cube.bmanga_line_settings.intersection_enabled = True

        _apply_line(sheet, cube)
        assert core.has_outline(sheet), "板ポリのアウトラインが作成されていません"
        _assert_no_generated_lines(sheet)
        assert not _has_intersection(cube), "板ポリが交差対象に残っています"

        _clear_scene()
        included_sheet = _make_plane("BML_sheet_included")
        included_cube = _make_cube("BML_sheet_included_cube")
        assert included_sheet.bmanga_line_settings.exclude_sheet_meshes is False
        included_sheet.bmanga_line_settings.inner_line_enabled = True
        included_sheet.bmanga_line_settings.intersection_enabled = True
        included_cube.bmanga_line_settings.intersection_enabled = True
        _apply_line(included_sheet, included_cube)
        assert included_sheet.modifiers.get(core.GN_MODIFIER_NAME) is not None
        assert _has_intersection(included_sheet) or _has_intersection(included_cube)

        bpy.ops.object.select_all(action="DESELECT")
        included_sheet.select_set(True)
        bpy.context.view_layer.objects.active = included_sheet
        included_sheet.bmanga_line_settings.exclude_sheet_meshes = True
        _assert_no_generated_lines(included_sheet)
        assert not _has_intersection(included_cube), "板ポリを対象外にした後も交差線が残っています"

        print("BMANGA_LINE_SHEET_MESH_EXCLUSION_OK")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
