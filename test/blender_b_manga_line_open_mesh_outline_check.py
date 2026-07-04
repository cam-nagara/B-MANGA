"""Blender check: open meshes do not generate filled outline shells."""

from __future__ import annotations

import sys
import math
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, outline_setup, presets, scale_utils  # noqa: E402


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


def _socket_id(tree: bpy.types.NodeTree, name: str) -> str:
    for item in tree.interface.items_tree:
        if (
            getattr(item, "name", None) == name
            and getattr(item, "in_out", None) == "INPUT"
        ):
            return item.identifier
    raise AssertionError(f"socket not found: {name}")


def _add_closed_non_manifold_mesh() -> bpy.types.Object:
    mesh = bpy.data.meshes.new("BML_closed_non_manifold_mesh")
    verts = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
        (0.0, -1.0, 0.0),
        (0.0, 0.0, -1.0),
    ]
    faces = [
        (0, 1, 2),
        (1, 0, 3),
        (0, 2, 3),
        (1, 3, 2),
        (0, 1, 4),
        (1, 0, 5),
        (0, 4, 5),
        (1, 5, 4),
    ]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("BML_closed_non_manifold", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    obj.data.materials.append(_surface_material("BML_closed_non_manifold_surface"))
    return obj


def _add_open_box_mesh() -> bpy.types.Object:
    mesh = bpy.data.meshes.new("BML_open_box_mesh")
    verts = [
        (-0.5, -0.5, -0.5),
        (0.5, -0.5, -0.5),
        (0.5, 0.5, -0.5),
        (-0.5, 0.5, -0.5),
        (-0.5, -0.5, 0.5),
        (0.5, -0.5, 0.5),
        (0.5, 0.5, 0.5),
        (-0.5, 0.5, 0.5),
    ]
    faces = [
        (0, 1, 2, 3),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (3, 7, 4, 0),
    ]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("BML_open_box", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    obj.data.materials.append(_surface_material("BML_open_box_surface"))
    return obj


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
        # 2026-07-03: 板ポリの輪郭は全方向チューブ（BML_SheetOutline）が担い、
        # リム面は非表示マテリアルへ逃がす
        tube_mod = plane.modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME)
        assert tube_mod is not None, "板ポリに境界チューブがありません"
        assert tube_mod.node_group is not None
        assert any(
            getattr(node, "label", "") == "BML_SheetOutlineAcutePathSplitV4"
            for node in tube_mod.node_group.nodes
        ), "板ポリアウトラインが検出角度で分割されていません"
        plane.bmanga_line_settings.edge_smooth_factor = -0.75
        plane.bmanga_line_settings.edge_midpoint_jitter_percent = 12.0
        plane.bmanga_line_settings.edge_midpoint_angle = math.radians(55.0)
        outline_setup.sync_sheet_outline_width(plane)
        assert abs(tube_mod[_socket_id(tube_mod.node_group, "中間頂点の線幅調整")] + 0.75) < 1e-7
        assert abs(tube_mod[_socket_id(tube_mod.node_group, "中間頂点の乱れ (%)")] - 12.0) < 1e-7
        assert abs(tube_mod[_socket_id(tube_mod.node_group, "検出角度")] - math.radians(55.0)) < 1e-7
        rim_mat = plane.material_slots[plane_mod.material_offset_rim].material
        assert rim_mat is not None and rim_mat.name.startswith(
            outline_setup.SHEET_RIM_HIDDEN_MATERIAL_NAME
        ), "板ポリのリム面が非表示マテリアルになっていません"
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
        assert cube_mod.offset == 1.0, "ラインのみ表示でオフセットが変わっています"
        cube.bmanga_line_settings.use_rim = True
        assert cube_mod.offset == 1.0, "ラインのみ表示中の設定変更でオフセットが変わっています"
        assert cube_mod.use_rim, "ラインのみ表示中のリム面設定が反映されていません"
        cube.bmanga_line_settings.use_rim = False
        assert cube_mod.offset == 1.0, "ラインのみ表示中の設定変更でオフセットが変わっています"
        assert not cube_mod.use_rim, "ラインのみ表示中のリム面設定オフが反映されていません"
        assert presets.apply_line_settings(cube, bpy.context)
        assert cube_mod.offset == 1.0, "ラインのみ表示中の再適用でオフセットが変わっています"
        assert bool(cube.get(core.PROP_LINE_ONLY, False)), "ラインのみ表示中の再適用で状態が解除されています"
        assert outline_setup.set_line_only(cube, False)
        assert cube_mod.offset == 1.0, "通常表示へ戻した後もラインのみ表示の形状が残っています"

        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(4.5, 0.0, 0.0))
        scaled_cube = bpy.context.object
        scaled_cube.name = "BML_scaled_line_only_wire_cube"
        scaled_cube.scale = (0.0254, 0.0254, 0.0254)
        scaled_cube.data.materials.append(_surface_material("BML_scaled_wire_surface"))
        _apply_line(scaled_cube, use_rim=False)
        outline_setup._ensure_line_only_wire(scaled_cube)
        wire = scaled_cube.modifiers.get(outline_setup.LINE_ONLY_WIREFRAME_NAME)
        assert wire is not None, "ラインのみ表示の補助ワイヤーが作成されていません"
        wire_world_width = abs(wire.thickness) * scale_utils.object_width_scale(scaled_cube)
        assert abs(wire_world_width - 0.025) < 1.0e-6, wire_world_width
        outline_setup._remove_line_only_wire(scaled_cube)

        open_box = _add_open_box_mesh()
        open_box_mod = _apply_line(open_box, use_rim=False)
        if hasattr(open_box_mod, "use_rim_only"):
            assert not open_box_mod.use_rim_only, "開いた立体を板ポリ扱いしています"
        assert not open_box_mod.use_rim, "開いた立体でリム面が強制されています"
        assert open_box_mod.offset == 1.0, "通常表示の開いた立体が内側アウトラインになっています"
        assert outline_setup.set_line_only(open_box, True)
        assert open_box_mod.offset == 1.0, "ラインのみ表示の開いた立体でオフセットが変わっています"
        if hasattr(open_box_mod, "use_rim_only"):
            assert not open_box_mod.use_rim_only, "ラインのみ表示で開いた立体を板ポリ扱いしています"
        assert not open_box_mod.use_rim, "ラインのみ表示の開いた立体でリム面が強制されています"
        assert outline_setup.set_line_only(open_box, False)
        assert open_box_mod.offset == 1.0, "通常表示へ戻した開いた立体が内側形状のままです"

        assert outline_setup.set_line_only(plane, True)
        assert plane_mod.offset == 1.0
        if hasattr(plane_mod, "use_rim_only"):
            assert plane_mod.use_rim_only
        assert outline_setup.set_line_only(plane, False)

        non_manifold = _add_closed_non_manifold_mesh()
        non_manifold_mod = _apply_line(non_manifold, use_rim=False)

        if hasattr(non_manifold_mod, "use_rim_only"):
            assert not non_manifold_mod.use_rim_only, "閉じた特殊メッシュを板ポリ扱いしています"
        assert not non_manifold_mod.use_rim, "閉じた特殊メッシュでリム面が強制されています"
        assert non_manifold_mod.offset == 1.0

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
