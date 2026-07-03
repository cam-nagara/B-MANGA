"""Blender check: sheet meshes keep generated lines (exclusion option retired).

2026-07-03 ユーザー確定: 「板ポリは内部線・交差線を作らない」オプションは廃止。
exclude_sheet_meshes プロパティが残っていても挙動へ影響しないことを確認する。
"""

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

        assert plane_filter.is_sheet_mesh(sheet), "平面が板ポリとして検出されていません"
        assert not plane_filter.is_sheet_mesh(thin_box), "薄い箱が板ポリ扱いされています"
        # 旧プロパティが True でも挙動へ影響しない（オプション廃止）
        assert plane_filter.should_exclude_generated_lines(sheet) is False
        sheet.bmanga_line_settings.inner_line_enabled = True
        sheet.bmanga_line_settings.intersection_enabled = True
        sheet.bmanga_line_settings.exclude_sheet_meshes = True
        cube.bmanga_line_settings.intersection_enabled = True

        _apply_line(sheet, cube)
        assert core.has_outline(sheet), "板ポリのアウトラインが作成されていません"
        assert sheet.modifiers.get(core.GN_MODIFIER_NAME) is not None, (
            "板ポリに内部線が作成されていません"
        )
        assert _has_intersection(cube), (
            "板ポリとの交差線が非シート側に作成されていません"
        )
        assert not _has_intersection(sheet), (
            "交差ペアの持ち主がシート側になっています"
        )

        # 旧プロパティをトグルしても線は消えない
        bpy.ops.object.select_all(action="DESELECT")
        sheet.select_set(True)
        bpy.context.view_layer.objects.active = sheet
        sheet.bmanga_line_settings.exclude_sheet_meshes = False
        sheet.bmanga_line_settings.exclude_sheet_meshes = True
        assert sheet.modifiers.get(core.GN_MODIFIER_NAME) is not None
        assert _has_intersection(cube)

        print("BMANGA_LINE_SHEET_MESH_EXCLUSION_OK")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
