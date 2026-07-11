"""Blender 5.1実機: 四角面化・Catmull-Clark・全線種の画像確認."""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
OUT_DIR = ROOT / "_verify" / "2026-07-11_bml_auto_quad_repair_visual"
OUT_DIR.mkdir(parents=True, exist_ok=True)

import b_manga_line  # noqa: E402
from b_manga_line import core, presets  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for collection in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.cameras, bpy.data.lights):
        for datablock in list(collection):
            if datablock.users == 0:
                collection.remove(datablock)


def _look_at(obj, target) -> None:
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


def _checker_material(name: str, first, second) -> bpy.types.Material:
    image = bpy.data.images.new(f"{name}_Image", width=64, height=64)
    pixels = []
    for y in range(64):
        for x in range(64):
            color = first if ((x // 8) + (y // 8)) % 2 == 0 else second
            pixels.extend((*color, 1.0))
    image.pixels.foreach_set(pixels)
    image.update()
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.interpolation = "Closest"
    texture.extension = "REPEAT"
    material.node_tree.links.new(texture.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.65
    return material


def _surface_object() -> bpy.types.Object:
    segments = 10
    vertices = []
    for z_value in (-0.75, 0.75):
        for index in range(segments):
            angle = math.tau * index / segments
            vertices.append((math.cos(angle), math.sin(angle), z_value))
    faces = []
    for index in range(segments):
        nxt = (index + 1) % segments
        faces.extend(
            (
                (index, nxt, nxt + segments),
                (index, nxt + segments, index + segments),
            )
        )
    mesh = bpy.data.meshes.new("BML_QuadVisual_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(
        _checker_material("BML_QuadVisual_Red", (0.9, 0.12, 0.04), (1.0, 0.9, 0.2))
    )
    mesh.materials.append(
        _checker_material("BML_QuadVisual_Blue", (0.02, 0.45, 0.9), (0.1, 0.9, 0.75))
    )
    uv = mesh.uv_layers.new(name="UVMap")
    normals = []
    for polygon in mesh.polygons:
        polygon.material_index = (polygon.index // 2) % 2
        polygon.use_smooth = True
        for loop_index in polygon.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index
            ring = vertex_index % segments
            uv.data[loop_index].uv = (
                ring / segments * 3.0,
                (mesh.vertices[vertex_index].co.z / 1.5 + 0.5) * 3.0,
            )
            normal = mesh.vertices[vertex_index].co.copy()
            normal.z = 0.0
            normal.normalize()
            normals.append(tuple(normal))
    mesh.normals_split_custom_set(normals)
    mesh.update()
    obj = bpy.data.objects.new("購入素材_三角面曲面", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _cross_object() -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.58, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = "交差確認"
    obj.scale = (1.0, 0.3, 0.9)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    material = bpy.data.materials.new("交差確認素材")
    material.diffuse_color = (0.78, 0.08, 0.5, 1.0)
    obj.data.materials.append(material)
    return obj


def _select(*objects) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def _setup_scene() -> None:
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 900
    scene.render.resolution_y = 700
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.world.color = (0.9, 0.9, 0.9)
    bpy.ops.object.camera_add(location=(3.6, -5.5, 2.8))
    camera = bpy.context.object
    camera.data.lens = 58.0
    _look_at(camera, Vector((0.0, 0.0, 0.0)))
    scene.camera = camera
    bpy.ops.object.light_add(type="AREA", location=(-3.0, -4.0, 5.0))
    bpy.context.object.data.energy = 700.0
    bpy.context.object.data.size = 5.0


def _render(name: str) -> Path:
    path = OUT_DIR / name
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    assert path.exists() and path.stat().st_size > 20_000
    return path


def _pixel_counts(path: Path) -> tuple[int, int, int]:
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        pixels = list(image.pixels)
        dark = saturated = near_black = 0
        for index in range(0, len(pixels), 4):
            rgb = pixels[index : index + 3]
            maximum = max(rgb)
            minimum = min(rgb)
            dark += maximum < 0.28
            near_black += maximum < 0.025
            saturated += maximum - minimum > 0.30 and maximum > 0.38
        return dark, saturated, near_black
    finally:
        bpy.data.images.remove(image)


def _enable_lines(obj) -> None:
    settings = obj.bmanga_line_settings
    settings.outline_enabled = True
    settings.inner_line_enabled = True
    settings.intersection_enabled = True
    settings.selection_line_enabled = True
    settings.bump_line_enabled = True
    settings.use_camera_culling = False
    settings.use_outline_creation_limit = False
    settings.use_inner_line_creation_limit = False
    settings.use_intersection_creation_limit = False
    settings.use_selection_line_creation_limit = False
    settings.outline_thickness_mm = 0.5
    settings.inner_line_thickness_mm = 0.25
    settings.intersection_thickness_mm = 0.32
    settings.selection_line_thickness_mm = 0.4
    settings.inner_line_angle = math.radians(18.0)
    settings.outline_color = (0.01, 0.01, 0.01, 1.0)
    settings.inner_line_color = (0.02, 0.15, 0.9, 1.0)
    settings.intersection_color = (0.0, 0.8, 0.08, 1.0)
    settings.selection_line_color = (0.9, 0.0, 0.75, 1.0)
    settings.bump_line_color = (0.8, 0.12, 0.02, 1.0)


def _mark_selection_edges(obj) -> None:
    attribute = obj.data.attributes.new(core.FREESTYLE_EDGE_ATTR, "BOOLEAN", "EDGE")
    for index, item in enumerate(attribute.data):
        item.value = index % 5 == 0


def main() -> None:
    b_manga_line.register()
    _clear_scene()
    _setup_scene()
    surface = _surface_object()
    crossing = _cross_object()
    before = _render("01_before.png")

    _select(surface)
    assert bpy.ops.bmanga_line.auto_repair_quad_mesh("EXEC_DEFAULT") == {"FINISHED"}
    assert all(len(polygon.vertices) == 4 for polygon in surface.data.polygons)
    assert len(surface.data.materials) == 2
    assert {polygon.material_index for polygon in surface.data.polygons} == {0, 1}
    repaired = _render("02_quad_repaired.png")

    subdivision = surface.modifiers.new("Catmull-Clark確認", "SUBSURF")
    subdivision.subdivision_type = "CATMULL_CLARK"
    subdivision.levels = 1
    subdivision.render_levels = 1
    _mark_selection_edges(surface)
    for obj in (surface, crossing):
        _enable_lines(obj)
        assert presets.apply_line_settings(obj, bpy.context, refresh_scene=False)
    presets._refresh_after_line_settings(bpy.context)
    bpy.context.view_layer.update()
    with_lines = _render("03_catmull_all_lines.png")

    before_counts = _pixel_counts(before)
    repaired_counts = _pixel_counts(repaired)
    line_counts = _pixel_counts(with_lines)
    assert before_counts[1] > 20_000, before_counts
    assert repaired_counts[1] > 20_000, repaired_counts
    assert line_counts[1] > 20_000, line_counts
    assert line_counts[0] > repaired_counts[0] + 1_000, (repaired_counts, line_counts)
    assert line_counts[2] < 120_000, line_counts
    assert len(surface.data.materials) >= 2
    assert {polygon.material_index for polygon in surface.data.polygons} == {0, 1}
    assert surface.data.uv_layers.get("UVMap") is not None
    print(f"[OUT] {before}")
    print(f"[OUT] {repaired}")
    print(f"[OUT] {with_lines}")
    print(f"[PIXELS] before={before_counts} repaired={repaired_counts} lines={line_counts}")
    print("B-MANGA Liner auto quad repair visual check: PASS")
    os._exit(0)


if __name__ == "__main__":
    main()
