"""B-MANGA Line: line-only display uses the BML_Line AOV in a real View3D."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, outline_setup, presets, viewport_aov  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _view_space() -> bpy.types.SpaceView3D:
    screen = bpy.context.screen
    assert screen is not None, "View3D screen is not available"
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for space in area.spaces:
            if space.type == "VIEW_3D":
                return space
    raise AssertionError("View3D area is not available")


def _make_line_cube() -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = "BML_AOV_line_only_cube"
    mat = bpy.data.materials.new("BML_AOV_surface")
    mat.diffuse_color = (0.2, 0.5, 0.8, 1.0)
    obj.data.materials.append(mat)
    settings = obj.bmanga_line_settings
    settings.inner_line_enabled = True
    settings.intersection_enabled = True
    assert presets.apply_line_settings(obj, bpy.context)
    return obj


def _select(obj: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def main() -> None:
    b_manga_line.register()
    _clear_scene()
    obj = _make_line_cube()
    _select(obj)
    space = _view_space()
    original_type = space.shading.type
    original_pass = space.shading.render_pass
    original_aov = space.shading.aov_name
    original_materials = [mat.name if mat else "" for mat in obj.data.materials]

    assert bpy.ops.bmanga_line.set_line_only(line_only=True) == {"FINISHED"}
    assert viewport_aov.is_line_aov_active(bpy.context)
    assert space.shading.type == "RENDERED"
    assert space.shading.render_pass in {"AOV", core.AOV_NAME}
    assert space.shading.aov_name == core.AOV_NAME
    assert [mat.name if mat else "" for mat in obj.data.materials] == original_materials
    assert outline_setup.LINE_ONLY_WIREFRAME_NAME not in obj.modifiers
    assert not bool(obj.get(core.PROP_LINE_ONLY, False))
    for mod_name in core.LINE_MODIFIER_NAMES:
        mod = obj.modifiers.get(mod_name)
        assert mod is not None
        assert mod.show_viewport and mod.show_render

    assert bpy.ops.bmanga_line.set_line_only(line_only=False) == {"FINISHED"}
    assert not viewport_aov.is_line_aov_active(bpy.context)
    assert space.shading.type == original_type
    assert space.shading.render_pass == original_pass
    assert space.shading.aov_name == original_aov
    print("[PASS] B-MANGA Line line-only display uses the BML_Line AOV")


def _run_and_quit():
    try:
        main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise
    finally:
        bpy.ops.wm.quit_blender()
    return None


if __name__ == "__main__":
    bpy.app.timers.register(_run_and_quit, first_interval=0.5)
