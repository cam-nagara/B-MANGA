"""B-MANGA Line: display toggles keep all line types consistent."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "_verify" / "2026-07-07_bml_display_modes"
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    core,
    intersection_lines,
    outline_local_subdivision,
    presets,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for collection in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.node_groups,
        bpy.data.cameras,
        bpy.data.lights,
        bpy.data.images,
    ):
        for datablock in list(collection):
            if datablock.users == 0:
                collection.remove(datablock)


def _look_at(obj: bpy.types.Object, target: Vector) -> None:
    direction = target - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _material(name: str, color: tuple[float, float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = 0.72
    return mat


def _setup_render() -> None:
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"
    if hasattr(scene, "eevee"):
        scene.eevee.taa_render_samples = 32
    scene.render.resolution_x = 900
    scene.render.resolution_y = 600
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.world = scene.world or bpy.data.worlds.new("World")
    scene.world.color = (1.0, 1.0, 1.0)
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        background.inputs["Strength"].default_value = 1.0

    bpy.ops.object.camera_add(location=(0.0, -7.0, 3.2))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 4.8
    _look_at(camera, Vector((0.0, 0.0, 0.35)))
    scene.camera = camera

    bpy.ops.object.light_add(type="AREA", location=(-2.0, -4.0, 5.5))
    light = bpy.context.object
    light.data.energy = 500.0
    light.data.size = 5.0


def _set_flat_faces(obj: bpy.types.Object) -> None:
    for poly in obj.data.polygons:
        poly.use_smooth = False


def _make_scene_objects() -> list[bpy.types.Object]:
    mat_a = _material("BML_Display_A", (0.72, 0.78, 0.86, 1.0))
    mat_b = _material("BML_Display_B", (0.86, 0.76, 0.70, 1.0))

    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.72, depth=1.6, location=(-0.72, 0.0, 0.0))
    cylinder = bpy.context.object
    cylinder.name = "BML_Display_Cylinder"
    cylinder.data.materials.append(mat_a)
    _set_flat_faces(cylinder)

    bpy.ops.mesh.primitive_cube_add(size=1.15, location=(0.0, 0.0, -0.1))
    cube = bpy.context.object
    cube.name = "BML_Display_Cube"
    cube.data.materials.append(mat_b)
    _set_flat_faces(cube)

    bpy.ops.mesh.primitive_cone_add(vertices=32, radius1=0.55, radius2=0.0, depth=1.2, location=(0.92, 0.0, 0.05))
    cone = bpy.context.object
    cone.name = "BML_Display_Cone"
    cone.data.materials.append(mat_a)
    _set_flat_faces(cone)

    return [cylinder, cube, cone]


def _select(objects: list[bpy.types.Object], active: bpy.types.Object | None = None) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active or objects[0]


def _configure(objects: list[bpy.types.Object]) -> None:
    old = core._propagating
    core._propagating = True
    try:
        for obj in objects:
            settings = obj.bmanga_line_settings
            settings.outline_enabled = True
            settings.inner_line_enabled = True
            settings.intersection_enabled = True
            settings.selection_line_enabled = False
            settings.outline_thickness_mm = 0.8
            settings.inner_line_thickness_mm = 0.8
            settings.intersection_thickness_mm = 0.8
            settings.outline_color = (0.0, 0.0, 0.0, 1.0)
            settings.inner_line_color = (0.0, 0.0, 0.0, 1.0)
            settings.intersection_color = (0.0, 0.0, 0.0, 1.0)
            settings.edge_angle = 0.02
            settings.inner_line_angle = 0.02
            settings.intersection_line_angle = 0.02
            settings.use_outline_creation_limit = False
            settings.use_inner_line_creation_limit = False
            settings.use_intersection_creation_limit = False
            settings.use_outline_distance_limit = False
            settings.use_inner_line_distance_limit = False
            settings.use_intersection_distance_limit = False
            settings.use_camera_culling = False
            settings.use_uniform_line_width = True
            settings.use_camera_compensation = True
            settings.camera_compensation_influence = 1.0
    finally:
        core._propagating = old


def _apply_lines(objects: list[bpy.types.Object]) -> None:
    for obj in objects:
        assert presets.apply_line_settings(obj, bpy.context, refresh_scene=False)
    presets._refresh_after_line_settings(bpy.context)
    bpy.context.view_layer.update()


def _assert_all_line_modifiers_visible(objects: list[bpy.types.Object], expected: bool) -> None:
    for obj in objects:
        mods = list(core.iter_line_modifiers(obj))
        assert mods, obj.name
        local = outline_local_subdivision.get_modifier(obj)
        for mod in mods:
            mod_expected = expected and not (
                mod.name == core.MODIFIER_NAME and local is not None
            )
            assert bool(mod.show_viewport) is mod_expected, (obj.name, mod.name, mod.show_viewport)
            assert bool(mod.show_render) is mod_expected, (obj.name, mod.name, mod.show_render)


def _render(path: Path) -> None:
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    assert path.exists() and path.stat().st_size > 1000, path


def _dark_pixels(path: Path, box: tuple[int, int, int, int] | None = None) -> int:
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        pixels = list(image.pixels)
        if box is None:
            x0, y0, x1, y1 = 0, 0, width, height
        else:
            x0, y0, x1, y1 = box
        count = 0
        for y in range(max(0, y0), min(height, y1)):
            row = y * width * 4
            for x in range(max(0, x0), min(width, x1)):
                index = row + x * 4
                if max(pixels[index], pixels[index + 1], pixels[index + 2]) < 0.22:
                    count += 1
        return count
    finally:
        bpy.data.images.remove(image)


def _set_outline_modifiers(objects: list[bpy.types.Object], visible: bool) -> None:
    for obj in objects:
        settings = obj.bmanga_line_settings
        settings.outline_enabled = visible
        for name in (core.MODIFIER_NAME, core.SHEET_OUTLINE_MODIFIER_NAME):
            mod = obj.modifiers.get(name)
            if mod is not None:
                mod.show_viewport = visible
                mod.show_render = visible
    bpy.context.view_layer.update()


def _assert_line_visible_off_survives_intersection_refresh(objects: list[bpy.types.Object]) -> None:
    _select(objects)
    objects[0].bmanga_line_settings.lines_visible = False
    _assert_all_line_modifiers_visible(objects, False)
    intersection_lines.refresh_scene_intersections(bpy.context.scene)
    bpy.context.view_layer.update()
    _assert_all_line_modifiers_visible(objects, False)

    objects[0].bmanga_line_settings.lines_visible = True
    _assert_all_line_modifiers_visible(objects, True)


def _assert_line_only_keeps_cylinder_lines(objects: list[bpy.types.Object]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _select(objects)
    assert bpy.ops.bmanga_line.set_line_only("EXEC_DEFAULT", line_only=True) == {"FINISHED"}
    _set_outline_modifiers(objects, False)
    inner_path = OUT_DIR / "line_only_inner_reference.png"
    _render(inner_path)
    cylinder_inner_dark = _dark_pixels(inner_path, (180, 150, 430, 470))
    assert cylinder_inner_dark > 450, cylinder_inner_dark

    _set_outline_modifiers(objects, True)
    all_path = OUT_DIR / "line_only_all_lines.png"
    _render(all_path)
    cylinder_all_dark = _dark_pixels(all_path, (180, 150, 430, 470))
    assert cylinder_all_dark >= cylinder_inner_dark * 0.8, (
        cylinder_inner_dark,
        cylinder_all_dark,
    )

    assert bpy.ops.bmanga_line.set_line_only("EXEC_DEFAULT", line_only=False) == {"FINISHED"}


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()
        _setup_render()
        objects = _make_scene_objects()
        _configure(objects)
        _apply_lines(objects)
        _assert_line_visible_off_survives_intersection_refresh(objects)
        _assert_line_only_keeps_cylinder_lines(objects)
        print("[PASS] B-MANGA Line display mode toggles keep all line types visible/hidden")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
