"""Blender 5.1実機: 最適化メッシュと全線種のレンダー画像を生成する."""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
OUT_DIR = ROOT / "_verify" / "2026-07-11_bml_mesh_optimizer_visual"
OUT_DIR.mkdir(parents=True, exist_ok=True)

import b_manga_line  # noqa: E402
from b_manga_line import core, presets  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for collection in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras, bpy.data.lights):
        for datablock in list(collection):
            if datablock.users == 0:
                collection.remove(datablock)


def _look_at(obj, target) -> None:
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


def _material(name: str, color) -> bpy.types.Material:
    material = bpy.data.materials.new(name)
    material.diffuse_color = color
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Roughness"].default_value = 0.7
    return material


def _open_cylinder() -> bpy.types.Object:
    segments = 10
    vertices = []
    for z_value in (-0.75, 0.75):
        for index in range(segments):
            angle = math.tau * index / segments
            vertices.append((math.cos(angle), math.sin(angle), z_value))
    faces = [
        (index, (index + 1) % segments, (index + 1) % segments + segments, index + segments)
        for index in range(segments)
    ]
    mesh = bpy.data.meshes.new("BML_OptimizerVisual_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(_material("BML_OptimizerVisual_Cyan", (0.08, 0.62, 0.78, 1.0)))
    mesh.materials.append(_material("BML_OptimizerVisual_Yellow", (0.95, 0.58, 0.08, 1.0)))
    for polygon in mesh.polygons:
        polygon.use_smooth = True
        polygon.material_index = polygon.index % 2
    uv = mesh.uv_layers.new(name="UVMap")
    normals = []
    for polygon in mesh.polygons:
        for loop_index in polygon.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index
            ring_index = vertex_index % segments
            uv.data[loop_index].uv = (
                ring_index / segments,
                mesh.vertices[vertex_index].co.z / 1.5 + 0.5,
            )
            normal = mesh.vertices[vertex_index].co.copy()
            normal.z = 0.0
            normal.normalize()
            normals.append(tuple(normal))
    mesh.normals_split_custom_set(normals)
    mesh.update()
    obj = bpy.data.objects.new("購入素材_開口曲面", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _cross_object() -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.55, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = "交差確認"
    obj.scale = (1.05, 0.32, 0.95)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(_material("BML_OptimizerVisual_Pink", (0.82, 0.12, 0.42, 1.0)))
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
    scene.world.color = (0.92, 0.92, 0.92)
    bpy.ops.object.camera_add(location=(3.5, -5.3, 2.8))
    camera = bpy.context.object
    camera.data.lens = 58.0
    _look_at(camera, Vector((0.0, 0.0, 0.0)))
    scene.camera = camera
    bpy.ops.object.light_add(type="AREA", location=(-3.0, -4.0, 5.0))
    bpy.context.object.data.energy = 650.0
    bpy.context.object.data.size = 5.0


def _render(name: str) -> Path:
    path = OUT_DIR / name
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    assert path.exists() and path.stat().st_size > 10_000
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
            dark += maximum < 0.30
            near_black += maximum < 0.03
            saturated += maximum - minimum > 0.28 and maximum > 0.35
        return dark, saturated, near_black
    finally:
        bpy.data.images.remove(image)


def _mark_selection_edges(obj) -> None:
    attr = obj.data.attributes.get(core.FREESTYLE_EDGE_ATTR)
    if attr is None:
        attr = obj.data.attributes.new(core.FREESTYLE_EDGE_ATTR, "BOOLEAN", "EDGE")
    for index, item in enumerate(attr.data):
        item.value = index % 4 == 0


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
    settings.outline_thickness_mm = 0.55
    settings.inner_line_thickness_mm = 0.28
    settings.intersection_thickness_mm = 0.34
    settings.selection_line_thickness_mm = 0.42
    settings.inner_line_angle = math.radians(15.0)
    settings.outline_color = (0.01, 0.01, 0.01, 1.0)
    settings.inner_line_color = (0.02, 0.12, 0.85, 1.0)
    settings.intersection_color = (0.0, 0.75, 0.08, 1.0)
    settings.selection_line_color = (0.85, 0.0, 0.75, 1.0)
    settings.bump_line_color = (0.75, 0.15, 0.02, 1.0)


def main() -> None:
    b_manga_line.register()
    _clear_scene()
    _setup_scene()
    surface = _open_cylinder()
    crossing = _cross_object()
    before = _render("01_before.png")

    _select(surface)
    result = bpy.ops.bmanga_line.optimize_purchased_mesh("EXEC_DEFAULT")
    assert result == {"FINISHED"}
    assert surface.get("bml_surface_mesh_optimized") is True
    assert not surface.bmanga_line_settings.auto_subdivision_for_midpoint
    optimized = _render("02_optimized_surface.png")

    _mark_selection_edges(surface)
    for obj in (surface, crossing):
        _enable_lines(obj)
        assert presets.apply_line_settings(obj, bpy.context, refresh_scene=False)
    presets._refresh_after_line_settings(bpy.context)
    bpy.context.view_layer.update()
    all_lines = _render("03_optimized_all_lines.png")

    before_counts = _pixel_counts(before)
    optimized_counts = _pixel_counts(optimized)
    line_counts = _pixel_counts(all_lines)
    assert before_counts[1] > 25_000, before_counts
    assert optimized_counts[1] > 25_000, optimized_counts
    assert line_counts[1] > optimized_counts[1] + 5_000, (optimized_counts, line_counts)
    assert line_counts[2] < 120_000, line_counts
    assert len(surface.data.materials) >= 2
    assert {polygon.material_index for polygon in surface.data.polygons} >= {0, 1}
    assert surface.data.uv_layers.get("UVMap") is not None
    print(f"[OUT] {before}")
    print(f"[OUT] {optimized}")
    print(f"[OUT] {all_lines}")
    print(f"[PIXELS] before={before_counts} optimized={optimized_counts} lines={line_counts}")
    print("B-MANGA Liner mesh optimizer visual check: PASS")
    os._exit(0)


if __name__ == "__main__":
    main()
