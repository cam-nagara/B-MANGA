"""B-MANGA Line: line-only display avoids AOV switching in a real View3D."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, outline_setup, presets, viewport_aov  # noqa: E402

OUT_DIR = ROOT / "_verify" / "b_manga_line_aov_view_line_only"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _view3d_override() -> tuple[dict, bpy.types.SpaceView3D]:
    screen = bpy.context.screen
    assert screen is not None, "View3D screen is not available"
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        region = next((item for item in area.regions if item.type == "WINDOW"), None)
        for space in area.spaces:
            if space.type != "VIEW_3D":
                continue
            rv3d = getattr(space, "region_3d", None)
            if region is None or rv3d is None:
                continue
            space.overlay.show_overlays = False
            try:
                space.show_gizmo = False
            except (AttributeError, TypeError):
                pass
            return (
                {
                    "window": bpy.context.window,
                    "screen": screen,
                    "area": area,
                    "region": region,
                    "space_data": space,
                    "region_data": rv3d,
                },
                space,
            )
    raise AssertionError("View3D area is not available")


def _non_view3d_override() -> dict:
    screen = bpy.context.screen
    assert screen is not None, "Screen is not available"
    for area in screen.areas:
        if area.type == "VIEW_3D":
            continue
        region = next((item for item in area.regions if item.type == "WINDOW"), None)
        if region is None:
            continue
        return {
            "window": bpy.context.window,
            "screen": screen,
            "area": area,
            "region": region,
        }
    raise AssertionError("Non-View3D area is not available")


def _look_at(obj: bpy.types.Object, target: Vector) -> None:
    direction = target - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _make_camera() -> None:
    bpy.ops.object.light_add(type="AREA", location=(0.0, -3.5, 4.0))
    light = bpy.context.object
    light.data.energy = 450.0
    light.data.size = 4.0

    bpy.ops.object.camera_add(location=(2.2, -4.0, 2.2))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 2.7
    _look_at(camera, Vector((0.0, 0.0, 0.0)))
    bpy.context.scene.camera = camera


def _make_line_cube() -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = "BML_AOV_line_only_cube"
    mat = bpy.data.materials.new("BML_AOV_surface")
    mat.diffuse_color = (0.2, 0.5, 0.8, 1.0)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (0.2, 0.5, 0.8, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.55
    obj.data.materials.append(mat)
    settings = obj.bmanga_line_settings
    settings.outline_color = (1.0, 0.0, 1.0, 1.0)
    settings.inner_line_enabled = True
    settings.intersection_enabled = True
    assert presets.apply_line_settings(obj, bpy.context)
    return obj


def _render_view(path: Path, override: dict) -> None:
    scene = bpy.context.scene
    scene.render.filepath = str(path)
    scene.render.resolution_x = 720
    scene.render.resolution_y = 480
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    with bpy.context.temp_override(**override):
        rv3d = override.get("region_data")
        if getattr(rv3d, "view_perspective", "") != "CAMERA":
            bpy.ops.view3d.view_camera()
        space = override.get("space_data")
        if space is not None:
            space.overlay.show_overlays = False
            try:
                space.show_gizmo = False
            except (AttributeError, TypeError):
                pass
        if bpy.ops.wm.redraw_timer.poll():
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=4)
        result = bpy.ops.render.opengl(write_still=True, view_context=True)
    assert result == {"FINISHED"}, result
    assert path.exists() and path.stat().st_size > 1000, path


def _image_counts(path: Path) -> dict[str, int]:
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        pixels = list(image.pixels)
        colored = 0
        dark = 0
        blue_surface = 0
        for index in range(0, len(pixels), 4):
            r, g, b = pixels[index], pixels[index + 1], pixels[index + 2]
            mx = max(r, g, b)
            mn = min(r, g, b)
            if mx < 0.18:
                dark += 1
            if b > 0.25 and b - r > 0.18 and g - r > 0.10:
                blue_surface += 1
            if mx > 0.25 and mx - mn > 0.18:
                colored += 1
        return {"colored": colored, "dark": dark, "blue_surface": blue_surface}
    finally:
        bpy.data.images.remove(image)


def _select(obj: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def main() -> None:
    b_manga_line.register()
    _clear_scene()
    _make_camera()
    obj = _make_line_cube()
    _select(obj)
    override, space = _view3d_override()
    space.shading.type = "RENDERED"
    original_type = space.shading.type
    original_pass = space.shading.render_pass
    original_aov = space.shading.aov_name
    original_materials = [mat.name if mat else "" for mat in obj.data.materials]
    before_path = OUT_DIR / "01_before.png"
    line_only_path = OUT_DIR / "02_material_line_only.png"

    _render_view(before_path, override)
    before_counts = _image_counts(before_path)

    with bpy.context.temp_override(**override):
        assert bpy.ops.bmanga_line.set_line_only(line_only=True) == {"FINISHED"}
    assert not viewport_aov.is_line_aov_active(bpy.context)
    assert space.shading.type == original_type
    assert space.shading.render_pass == original_pass
    assert space.shading.aov_name == original_aov
    assert [mat.name if mat else "" for mat in obj.data.materials[:1]] == [
        outline_setup.LINE_ONLY_MATERIAL_NAME
    ]
    assert outline_setup.LINE_ONLY_WIREFRAME_NAME not in obj.modifiers
    assert bool(obj.get(core.PROP_LINE_ONLY, False))
    _render_view(line_only_path, override)
    aov_counts = _image_counts(line_only_path)
    assert before_counts["blue_surface"] > 1000, before_counts
    assert aov_counts["blue_surface"] < before_counts["blue_surface"] * 0.01, (
        before_counts,
        aov_counts,
    )
    assert aov_counts["dark"] > 100, aov_counts
    line_modifiers = list(core.iter_line_modifiers(obj))
    assert line_modifiers
    for mod in line_modifiers:
        assert mod.show_viewport and mod.show_render

    with bpy.context.temp_override(**override):
        assert bpy.ops.bmanga_line.set_line_only(line_only=False) == {"FINISHED"}
    assert not viewport_aov.is_line_aov_active(bpy.context)
    assert space.shading.type == original_type
    assert space.shading.render_pass == original_pass
    assert space.shading.aov_name == original_aov
    assert not bool(obj.get(core.PROP_LINE_ONLY, False))
    assert [mat.name if mat else "" for mat in obj.data.materials] == original_materials

    with bpy.context.temp_override(**_non_view3d_override()):
        assert bpy.ops.bmanga_line.set_line_only(line_only=True) == {"FINISHED"}
    assert not viewport_aov.is_line_aov_active(bpy.context)
    assert bool(obj.get(core.PROP_LINE_ONLY, False))
    assert [mat.name if mat else "" for mat in obj.data.materials[:1]] == [
        outline_setup.LINE_ONLY_MATERIAL_NAME
    ]
    with bpy.context.temp_override(**_non_view3d_override()):
        assert bpy.ops.bmanga_line.set_line_only(line_only=False) == {"FINISHED"}
    assert not viewport_aov.is_line_aov_active(bpy.context)
    assert not bool(obj.get(core.PROP_LINE_ONLY, False))
    assert [mat.name if mat else "" for mat in obj.data.materials] == original_materials
    print("[PASS] B-MANGA Line line-only display uses material mode without AOV")


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
