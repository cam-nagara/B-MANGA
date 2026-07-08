"""B-MANGA Liner: existing outline updates avoid full outline rebuild."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, outline_setup, presets  # noqa: E402
from b_manga_line.scale_utils import modifier_thickness_for_world_width  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_camera() -> None:
    bpy.ops.object.camera_add(location=(0.0, -6.0, 4.0), rotation=(1.0, 0.0, 0.0))
    bpy.context.scene.camera = bpy.context.object


def _outline_material(obj: bpy.types.Object) -> bpy.types.Material:
    for slot in obj.material_slots:
        if slot.material and outline_setup._line_material_target(slot.material) == "outline":
            return slot.material
    raise AssertionError(f"{obj.name} のアウトライン素材がありません")


def _line_color(mat: bpy.types.Material) -> tuple[float, float, float, float]:
    for node in mat.node_tree.nodes:
        if node.type == "RGB" and node.label == "BML_Color":
            return tuple(node.outputs[0].default_value)
    raise AssertionError(f"{mat.name} のライン色ノードがありません")


def _assert_close_tuple(actual, expected, epsilon: float = 1.0e-6) -> None:
    assert len(actual) == len(expected)
    for left, right in zip(actual, expected):
        assert abs(float(left) - float(right)) <= epsilon


def _block_full_outline_rebuild():
    original = outline_setup.apply_outline

    def _blocked(*_args, **_kwargs):
        raise AssertionError("作成済みアウトライン更新が初回作成経路へ戻りました")

    outline_setup.apply_outline = _blocked
    return original


def _assert_solid_outline_fast_update() -> None:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    settings = obj.bmanga_line_settings
    settings.outline_thickness = 0.01
    settings.outline_offset = 0.0

    assert presets.apply_line_settings(
        obj,
        bpy.context,
        refresh_scene=False,
        line_targets=("outline",),
    )
    mod = obj.modifiers.get(core.MODIFIER_NAME)
    assert mod is not None

    settings.outline_thickness = 0.025
    settings.outline_offset = -0.2
    settings.outline_color = (1.0, 0.2, 0.1, 1.0)

    original = _block_full_outline_rebuild()
    try:
        assert presets.apply_line_settings(
            obj,
            bpy.context,
            refresh_scene=False,
            line_targets=("outline",),
        )
    finally:
        outline_setup.apply_outline = original

    assert abs(mod.thickness - modifier_thickness_for_world_width(obj, 0.025)) < 1.0e-9
    assert abs(mod.offset + 0.2) < 1.0e-6
    _assert_close_tuple(_line_color(_outline_material(obj)), (1.0, 0.2, 0.1, 1.0))


def _assert_sheet_outline_fast_update() -> None:
    bpy.ops.mesh.primitive_plane_add(size=2.0, location=(3.0, 0.0, 0.0))
    obj = bpy.context.object
    settings = obj.bmanga_line_settings
    settings.outline_thickness = 0.01

    assert presets.apply_line_settings(
        obj,
        bpy.context,
        refresh_scene=False,
        line_targets=("outline",),
    )
    assert obj.modifiers.get(core.MODIFIER_NAME) is None
    sheet_mod = obj.modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME)
    assert sheet_mod is not None

    settings.outline_thickness = 0.035
    settings.outline_color = (0.1, 0.3, 1.0, 1.0)

    original = _block_full_outline_rebuild()
    try:
        assert presets.apply_line_settings(
            obj,
            bpy.context,
            refresh_scene=False,
            line_targets=("outline",),
        )
    finally:
        outline_setup.apply_outline = original

    assert abs(outline_setup.sheet_outline_world_width(obj) - 0.035) < 1.0e-6
    _assert_close_tuple(_line_color(_outline_material(obj)), (0.1, 0.3, 1.0, 1.0))


def _make_mixed_cube_plane() -> bpy.types.Object:
    mesh = bpy.data.meshes.new("BML_fast_mixed_cube_plane_mesh")
    mesh.from_pydata(
        [
            (-0.5, -0.5, -0.5),
            (0.5, -0.5, -0.5),
            (0.5, 0.5, -0.5),
            (-0.5, 0.5, -0.5),
            (-0.5, -0.5, 0.5),
            (0.5, -0.5, 0.5),
            (0.5, 0.5, 0.5),
            (-0.5, 0.5, 0.5),
            (1.2, -0.5, 0.0),
            (2.2, -0.5, 0.0),
            (2.2, 0.5, 0.0),
            (1.2, 0.5, 0.0),
        ],
        [],
        [
            (0, 1, 2, 3),
            (4, 7, 6, 5),
            (0, 4, 5, 1),
            (1, 5, 6, 2),
            (2, 6, 7, 3),
            (3, 7, 4, 0),
            (8, 9, 10, 11),
        ],
    )
    mesh.update()
    obj = bpy.data.objects.new("BML_fast_mixed_cube_plane", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


def _assert_mixed_outline_fast_update() -> None:
    obj = _make_mixed_cube_plane()
    settings = obj.bmanga_line_settings
    settings.outline_thickness = 0.01

    assert presets.apply_line_settings(
        obj,
        bpy.context,
        refresh_scene=False,
        line_targets=("outline",),
    )
    solid = obj.modifiers.get(core.MODIFIER_NAME)
    sheet = obj.modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME)
    assert solid is not None
    assert sheet is not None

    settings.outline_thickness = 0.028
    settings.outline_color = (0.0, 0.75, 0.25, 1.0)

    original = _block_full_outline_rebuild()
    try:
        assert presets.apply_line_settings(
            obj,
            bpy.context,
            refresh_scene=False,
            line_targets=("outline",),
        )
    finally:
        outline_setup.apply_outline = original

    assert abs(solid.thickness - modifier_thickness_for_world_width(obj, 0.028)) < 1.0e-9
    assert abs(outline_setup.sheet_outline_world_width(obj) - 0.028) < 1.0e-6
    _assert_close_tuple(_line_color(_outline_material(obj)), (0.0, 0.75, 0.25, 1.0))


def main() -> None:
    b_manga_line.register()
    try:
        _clear_scene()
        _make_camera()
        _assert_solid_outline_fast_update()
        _assert_sheet_outline_fast_update()
        _assert_mixed_outline_fast_update()
        print("[PASS] existing outline updates avoid full rebuild")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
