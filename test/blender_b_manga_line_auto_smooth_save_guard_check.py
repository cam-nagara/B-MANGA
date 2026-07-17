"""B-MANGA Line: 保存時に壊れた自動スムーズを復旧する実機テスト."""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import auto_smooth_guard, core, intersection_cache  # noqa: E402
from b_manga_line.gn_socket_compat import get_gn_modifier_input  # noqa: E402


ANGLE = math.radians(60.0)
VISUAL_DIR = ROOT / "_verify" / "b_manga_line_auto_smooth_save_guard"
VISUAL_PATH = VISUAL_DIR / "auto_smooth_save_guard.png"


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for collection in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.node_groups,
        bpy.data.cameras,
        bpy.data.lights,
    ):
        for datablock in list(collection):
            if datablock.users == 0:
                collection.remove(datablock)


def _make_material(name: str, color: tuple[float, float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = 0.55
    return mat


def _make_line_target() -> bpy.types.Object:
    surface = _make_material("AutoSmooth_Surface", (0.82, 0.91, 1.0, 1.0))
    bpy.ops.mesh.primitive_cube_add(size=0.9, location=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = "AutoSmoothSaveProbe"
    obj.data.materials.append(surface)

    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.7, depth=1.25, location=(0.42, 0.0, 0.0))
    other = bpy.context.object
    other.name = "AutoSmoothSaveProbe_IntersectionTarget"
    other.data.materials.append(surface)

    for item in (obj, other):
        settings = item.bmanga_line_settings
        settings.outline_enabled = True
        settings.inner_line_enabled = True
        settings.intersection_enabled = True
        settings.use_intersection_creation_limit = False
        settings.intersection_method = "SHELL"
        settings.outline_thickness = 0.025
        settings.inner_line_thickness = 0.014
        settings.intersection_thickness = 0.018

    bpy.ops.object.select_all(action="DESELECT")
    for item in (obj, other):
        item.select_set(True)
    bpy.context.view_layer.objects.active = obj
    result = bpy.ops.bmanga_line.reflect_all()
    if "FINISHED" not in result:
        raise AssertionError(f"ライン適用に失敗しました: {result}")

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.shade_auto_smooth(angle=ANGLE)
    return obj


def _smooth_modifier(obj: bpy.types.Object) -> bpy.types.Modifier:
    mod = obj.modifiers.get(auto_smooth_guard.AUTO_SMOOTH_NAME)
    if mod is None:
        raise AssertionError("自動スムーズがありません")
    return mod


def _assert_valid_smooth(obj: bpy.types.Object, expected_angle: float) -> None:
    mod = _smooth_modifier(obj)
    if mod.node_group is None:
        raise AssertionError("自動スムーズのノードがありません")
    if mod.node_group.name != auto_smooth_guard.AUTO_SMOOTH_NAME:
        raise AssertionError(f"自動スムーズのノード名が不正です: {mod.node_group.name}")
    actual = float(get_gn_modifier_input(mod, auto_smooth_guard.ANGLE_SOCKET_ID, 0.0))
    if abs(actual - expected_angle) > 1.0e-4:
        raise AssertionError(
            f"自動スムーズの角度が保持されていません: actual={actual} expected={expected_angle}"
        )


def _assert_line_stack_survived(obj: bpy.types.Object) -> None:
    names = [mod.name for mod in obj.modifiers]
    if core.MODIFIER_NAME not in names:
        raise AssertionError("アウトラインが保存後に消えました")
    if core.INTERSECTION_MODIFIER_NAME not in names and not any(
        name.startswith(core.INTERSECTION_MODIFIER_PREFIX) for name in names
    ):
        raise AssertionError("交差線が保存後に消えました")
    cache_name = str(obj.get(intersection_cache.CACHE_OBJECT_PROP, "") or "")
    cache = bpy.data.objects.get(cache_name)
    if cache is None or len(getattr(cache.data, "edges", ())) == 0:
        raise AssertionError("保存済み交差線の中心線が保存後に消えました")
    if auto_smooth_guard.AUTO_SMOOTH_NAME not in names:
        raise AssertionError("自動スムーズが保存後に消えました")


def _break_smooth_modifier(obj: bpy.types.Object) -> None:
    mod = _smooth_modifier(obj)
    mod.node_group = None
    if mod.node_group is not None:
        raise AssertionError("テスト用に自動スムーズを壊せませんでした")


def _setup_visual_scene(obj: bpy.types.Object) -> None:
    VISUAL_DIR.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.light_add(type="AREA", location=(-2.0, -3.0, 4.0))
    light = bpy.context.object
    light.name = "AutoSmoothSaveProbe_Light"
    light.data.energy = 450
    light.data.size = 4.0

    bpy.ops.object.camera_add(location=(2.4, -4.2, 2.2), rotation=(math.radians(62), 0.0, math.radians(31)))
    camera = bpy.context.object
    bpy.context.scene.camera = camera

    bpy.context.scene.render.engine = "BLENDER_EEVEE"
    bpy.context.scene.render.resolution_x = 960
    bpy.context.scene.render.resolution_y = 720
    bpy.context.scene.render.filepath = str(VISUAL_PATH)
    bpy.context.scene.view_settings.view_transform = "Filmic"
    bpy.context.scene.view_settings.look = "Medium High Contrast"
    obj.select_set(True)


def main() -> None:
    b_manga_line.register()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_line_auto_smooth_save_guard_"))
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _clear_scene()
        obj = _make_line_target()
        expected_angle = float(
            get_gn_modifier_input(_smooth_modifier(obj), auto_smooth_guard.ANGLE_SOCKET_ID, 0.0)
        )
        _assert_valid_smooth(obj, expected_angle)
        _assert_line_stack_survived(obj)
        auto_smooth_guard.ensure_auto_smooth_nodes([obj], bpy.context)

        _break_smooth_modifier(obj)
        repaired = auto_smooth_guard.ensure_auto_smooth_nodes([obj], bpy.context)
        if repaired != 1:
            raise AssertionError(f"自動スムーズの直接復旧数が不正です: {repaired}")
        _assert_valid_smooth(obj, expected_angle)
        _assert_line_stack_survived(obj)

        _break_smooth_modifier(obj)
        save_path = temp_root / "auto_smooth_save_guard.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(save_path))
        _assert_valid_smooth(obj, expected_angle)
        _assert_line_stack_survived(obj)

        bpy.ops.wm.open_mainfile(filepath=str(save_path))
        reopened = bpy.data.objects.get("AutoSmoothSaveProbe")
        if reopened is None:
            raise AssertionError("保存したオブジェクトを開き直せません")
        _assert_valid_smooth(reopened, expected_angle)
        _assert_line_stack_survived(reopened)

        _setup_visual_scene(reopened)
        bpy.ops.render.render(write_still=True)
        if not VISUAL_PATH.is_file():
            raise AssertionError("目視確認用画像を保存できませんでした")
        print(f"[VISUAL] {VISUAL_PATH}")
        print("[PASS] B-MANGA Line auto smooth save guard")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass


if __name__ == "__main__":
    main()
