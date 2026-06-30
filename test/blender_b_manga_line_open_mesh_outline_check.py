"""Blender check: open meshes do not generate filled outline shells."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, outline_setup, presets  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _surface_material(name: str) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    return mat


def _apply_line(obj: bpy.types.Object, *, use_rim: bool) -> bpy.types.Modifier:
    settings = obj.bmanga_line_settings
    settings.outline_thickness_mm = 0.3
    settings.use_rim = use_rim
    settings.inner_line_enabled = False
    settings.intersection_enabled = False
    assert presets.apply_line_settings(obj, bpy.context)
    mod = obj.modifiers.get(core.MODIFIER_NAME)
    assert mod is not None, f"{obj.name}: アウトラインが作成されていません"
    return mod


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()

        bpy.ops.mesh.primitive_plane_add(size=2.0)
        plane = bpy.context.object
        plane.name = "BML_open_plane"
        plane.data.materials.append(_surface_material("BML_open_plane_surface"))
        plane_mod = _apply_line(plane, use_rim=False)

        assert getattr(plane_mod, "use_rim", False), "板ポリの縁生成が有効になっていません"
        if hasattr(plane_mod, "use_rim_only"):
            assert plane_mod.use_rim_only, "板ポリで黒い面を作る外殻生成になっています"
        assert plane_mod.material_offset_rim == plane_mod.material_offset
        assert plane_mod.offset == 1.0

        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(3.0, 0.0, 0.0))
        cube = bpy.context.object
        cube.name = "BML_closed_cube"
        cube.data.materials.append(_surface_material("BML_closed_cube_surface"))
        cube_mod = _apply_line(cube, use_rim=False)

        if hasattr(cube_mod, "use_rim_only"):
            assert not cube_mod.use_rim_only, "閉じた立体まで縁だけ生成になっています"
        assert not cube_mod.use_rim, "閉じた立体でリム面が強制されています"
        assert cube_mod.offset == 1.0, "通常表示の閉じた立体が内側アウトラインになっています"

        assert outline_setup.set_line_only(cube, True)
        assert cube_mod.offset == -1.0, "ラインのみ表示で黒い面を避ける形状に切り替わっていません"
        assert outline_setup.set_line_only(cube, False)
        assert cube_mod.offset == 1.0, "通常表示へ戻した後もラインのみ表示の形状が残っています"

        assert outline_setup.set_line_only(plane, True)
        assert plane_mod.offset == 1.0
        if hasattr(plane_mod, "use_rim_only"):
            assert plane_mod.use_rim_only
        assert outline_setup.set_line_only(plane, False)

        cube_mod = _apply_line(cube, use_rim=True)
        assert cube_mod.use_rim, "閉じた立体のリム面設定が反映されていません"

        print("BMANGA_LINE_OPEN_MESH_OUTLINE_OK")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
