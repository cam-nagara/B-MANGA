"""B-MANGA Line: render visual audit images for AI inspection."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import camera_comp, core, intersection_lines, outline_setup, presets  # noqa: E402


OUT_DIR = ROOT / "_verify" / "b_manga_line_full_visual_audit"
OUT_DIR.mkdir(parents=True, exist_ok=True)


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


def _transparent_material(name: str) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (1.0, 1.0, 1.0, 0.0)
    mat.use_nodes = True
    try:
        mat.blend_method = "BLEND"
    except (AttributeError, TypeError):
        pass
    try:
        mat.surface_render_method = "BLENDED"
    except (AttributeError, TypeError):
        pass
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    links.new(transparent.outputs["BSDF"], output.inputs["Surface"])
    return mat


def _select(*objects: bpy.types.Object, active: bpy.types.Object | None = None) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    if active is not None:
        bpy.context.view_layer.objects.active = active
    elif objects:
        bpy.context.view_layer.objects.active = objects[0]


def _setup_world(
    path: Path,
    *,
    engine: str = "BLENDER_EEVEE_NEXT",
    resolution: tuple[int, int] = (1200, 800),
) -> None:
    scene = bpy.context.scene
    try:
        scene.render.engine = engine
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"
    if engine == "CYCLES":
        scene.cycles.samples = 32
        scene.cycles.transparent_max_bounces = 32
    if hasattr(scene, "eevee"):
        scene.eevee.taa_render_samples = 64
    scene.render.resolution_x = resolution[0]
    scene.render.resolution_y = resolution[1]
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    if scene.world:
        scene.world.color = (1.0, 1.0, 1.0)
    scene.render.filepath = str(path)


def _setup_light() -> None:
    bpy.ops.object.light_add(type="AREA", location=(-3.0, -4.0, 5.0))
    light = bpy.context.object
    light.data.energy = 480.0
    light.data.size = 5.5


def _render(path: Path) -> None:
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    assert path.exists() and path.stat().st_size > 1000, path


def _dark_pixel_count(path: Path) -> int:
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        pixels = list(image.pixels)
        count = 0
        for index in range(0, len(pixels), 4):
            if max(pixels[index], pixels[index + 1], pixels[index + 2]) < 0.20:
                count += 1
        return count
    finally:
        bpy.data.images.remove(image)


def _dark_pixel_count_region(
    path: Path,
    x_min: int,
    x_max: int,
    y_min: int,
    y_max: int,
) -> int:
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width = int(image.size[0])
        height = int(image.size[1])
        pixels = list(image.pixels)
        count = 0
        for y in range(max(0, y_min), min(height, y_max)):
            for x in range(max(0, x_min), min(width, x_max)):
                index = (y * width + x) * 4
                if max(pixels[index], pixels[index + 1], pixels[index + 2]) < 0.20:
                    count += 1
        return count
    finally:
        bpy.data.images.remove(image)


def _setup_all_line_camera() -> None:
    bpy.ops.object.camera_add(location=(3.4, -5.8, 2.9))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 4.7
    _look_at(camera, Vector((0.0, 0.0, 0.15)))
    bpy.context.scene.camera = camera


def _make_all_line_cube(mat: bpy.types.Material) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    cube = bpy.context.object
    cube.name = "BML_visual_all_lines_cube"
    cube.dimensions = (2.4, 2.2, 1.25)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    cube.data.materials.append(mat)

    settings = cube.bmanga_line_settings
    settings.outline_thickness = 0.045
    settings.inner_line_enabled = True
    settings.inner_line_angle = math.radians(8.0)
    settings.inner_line_thickness = 0.018
    settings.edge_smooth_factor = -0.65
    settings.edge_midpoint_jitter_percent = 0.0
    settings.edge_width_curve_25 = 0.12
    settings.edge_width_curve_50 = 0.02
    settings.edge_width_curve_75 = 0.12
    assert presets.apply_line_settings(cube, bpy.context)
    return cube


def _make_intersection_cone(mat: bpy.types.Material) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cone_add(
        vertices=128,
        radius1=0.95,
        depth=3.1,
        location=(0.0, 0.0, 0.15),
    )
    cone = bpy.context.object
    cone.name = "BML_visual_intersection_cone"
    cone.data.materials.append(mat)
    assert outline_setup.apply_outline(
        cone,
        thickness=0.09,
        color=(0.0, 0.0, 0.0, 1.0),
        scene=bpy.context.scene,
    )
    return cone


def _scene_all_line_types() -> Path:
    _clear_scene()
    path = OUT_DIR / "01_outline_inner_intersection.png"
    _setup_world(path)
    _setup_light()
    _setup_all_line_camera()

    white = _material("BML_visual_white_surface", (0.98, 0.96, 0.90, 1.0))
    cube = _make_all_line_cube(white)
    cone = _make_intersection_cone(white)

    line_mat = outline_setup.get_outline_material(cube)
    assert line_mat is not None
    assert intersection_lines.apply_intersection_lines(
        cube,
        target=cone,
        thickness=0.018,
        material=line_mat,
        method="BOOLEAN",
    )

    _render(path)
    assert _dark_pixel_count(path) > 2000
    return path


def _scene_uniform_line_only_distance() -> Path:
    _clear_scene()
    path = OUT_DIR / "02_uniform_line_only_distance.png"
    _setup_world(path)
    _setup_light()

    bpy.ops.object.camera_add(location=(0.0, -7.0, 3.2))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 5.0
    _look_at(camera, Vector((0.0, 0.0, 0.2)))
    bpy.context.scene.camera = camera

    mats = [
        _material("BML_visual_blue", (0.55, 0.76, 1.0, 1.0)),
        _material("BML_visual_green", (0.72, 0.90, 0.62, 1.0)),
        _material("BML_visual_red", (1.0, 0.58, 0.52, 1.0)),
    ]
    cubes: list[bpy.types.Object] = []
    for index, x in enumerate((-1.9, 0.0, 1.9)):
        bpy.ops.mesh.primitive_cube_add(size=0.95, location=(x, 0.0, 0.0))
        obj = bpy.context.object
        obj.name = f"BML_visual_uniform_{index}"
        obj.data.materials.append(mats[index])
        settings = obj.bmanga_line_settings
        settings.outline_thickness_mm = 0.75
        settings.use_uniform_line_width = True
        settings.inner_line_enabled = True
        settings.inner_line_thickness_mm = 0.35
        assert presets.apply_line_settings(obj, bpy.context)
        cubes.append(obj)

    _select(cubes[1], active=cubes[1])
    assert bpy.ops.bmanga_line.set_line_only(line_only=True) == {"FINISHED"}

    cubes[2].bmanga_line_settings.use_outline_distance_limit = True
    cubes[2].bmanga_line_settings.outline_max_distance = 1.0
    cubes[2].bmanga_line_settings.use_inner_line_distance_limit = True
    cubes[2].bmanga_line_settings.inner_line_max_distance = 1.0
    camera_comp.refresh(bpy.context)
    assert not cubes[2].modifiers[core.MODIFIER_NAME].show_viewport
    assert not cubes[2].modifiers[core.GN_MODIFIER_NAME].show_viewport

    _render(path)
    assert _dark_pixel_count(path) > 500
    center_dark = _dark_pixel_count_region(path, 430, 760, 180, 610)
    right_dark = _dark_pixel_count_region(path, 890, 1185, 180, 610)
    assert center_dark > 500, center_dark
    assert right_dark < 100, right_dark
    return path


def _scene_transparent_protection() -> Path:
    _clear_scene()
    path = OUT_DIR / "03_transparent_protection.png"
    _setup_world(path, engine="CYCLES", resolution=(700, 700))

    bpy.ops.object.camera_add(location=(0.0, 0.0, 4.0), rotation=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 2.6
    bpy.context.scene.camera = camera

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = "BML_visual_transparent_cube"
    obj.data.materials.append(_transparent_material("BML_visual_clear_surface"))
    settings = obj.bmanga_line_settings
    settings.outline_thickness = 0.18
    settings.hide_through_transparent = True
    assert presets.apply_line_settings(obj, bpy.context)

    _render(path)
    assert _dark_pixel_count(path) > 1000
    return path


def main() -> None:
    b_manga_line.register()
    outputs = [
        _scene_all_line_types(),
        _scene_uniform_line_only_distance(),
        _scene_transparent_protection(),
    ]
    for output in outputs:
        print(f"[OUT] {output}")
    print("[PASS] B-MANGA Line visual audit images rendered")


if __name__ == "__main__":
    main()
