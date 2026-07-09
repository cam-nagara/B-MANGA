from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import camera_comp, intersection_cache, outline_setup, presets  # noqa: E402


OUT_DIR = ROOT / "_verify" / "2026-07-10_bml_midpoint_angle_visual"
OUT_PATH = OUT_DIR / "midpoint_angle_lines_texture.png"


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for datablocks in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.node_groups,
        bpy.data.cameras,
        bpy.data.lights,
    ):
        for datablock in list(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)


def material(name: str, color: tuple[float, float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = 0.68
    return mat


def make_textured_folded_strip() -> bpy.types.Object:
    levels = 18
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int, int]] = []
    for i in range(levels):
        x = i / (levels - 1) * 5.2 - 2.6
        verts.extend(
            (
                (x, -0.55, 0.0),
                (x, 0.0, 0.34),
                (x, 0.55, 0.0),
            )
        )
    for i in range(levels - 1):
        current = i * 3
        nxt = (i + 1) * 3
        faces.append((current, nxt, nxt + 1, current + 1))
        faces.append((current + 1, nxt + 1, nxt + 2, current + 2))

    mesh = bpy.data.meshes.new("BML_midpoint_texture_strip_mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("BML_midpoint_texture_strip", mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material("BML_texture_light", (0.90, 0.94, 0.98, 1.0)))
    obj.data.materials.append(material("BML_texture_dark", (0.28, 0.37, 0.46, 1.0)))
    for poly in obj.data.polygons:
        poly.material_index = (poly.index // 2) % 2
    return obj


def configure_lines(obj: bpy.types.Object) -> None:
    settings = obj.bmanga_line_settings
    settings.outline_enabled = True
    settings.inner_line_enabled = True
    settings.intersection_enabled = True
    settings.selection_line_enabled = False
    settings.use_outline_creation_limit = False
    settings.use_inner_line_creation_limit = False
    settings.use_intersection_creation_limit = False
    settings.use_outline_distance_limit = False
    settings.use_inner_line_distance_limit = False
    settings.use_intersection_distance_limit = False
    settings.use_camera_culling = False
    settings.use_camera_compensation = False
    settings.auto_subdivision_for_midpoint = True
    settings.outline_thickness_mm = 0.50
    settings.inner_line_thickness_mm = 0.30
    settings.intersection_thickness_mm = 0.50
    settings.outline_color = (0.0, 0.18, 1.0, 1.0)
    settings.inner_line_color = (1.0, 0.0, 1.0, 1.0)
    settings.intersection_color = (0.0, 1.0, 0.0, 1.0)
    settings.inner_line_angle = math.radians(35.0)
    settings.edge_smooth_factor = -1.0
    settings.inner_edge_smooth_factor = -1.0
    settings.intersection_edge_smooth_factor = -1.0
    settings.edge_midpoint_angle = math.radians(100.0)
    settings.intersection_edge_midpoint_angle = math.radians(100.0)


def add_cached_intersection_curve(obj: bpy.types.Object) -> None:
    cache = bpy.data.objects.new(
        "BML_midpoint_visual_intersection_cache",
        bpy.data.meshes.new("BML_midpoint_visual_intersection_cache_mesh"),
    )
    bpy.context.collection.objects.link(cache)
    cache.hide_viewport = True
    cache.hide_render = True
    cache.matrix_world = obj.matrix_world.copy()

    points = []
    for index in range(30):
        t = index / 29.0
        x = -2.35 + 4.7 * t
        y = 0.09 * math.sin(t * math.pi * 2.0)
        z = 0.44 + 0.06 * math.sin(t * math.pi)
        points.append(Vector((x, y, z)))
    segments = [
        intersection_cache._CachedSegment(
            points[index],
            points[index + 1],
            Vector((0.0, 0.06 * math.sin(index), 1.0)).normalized(),
        )
        for index in range(len(points) - 1)
    ]
    intersection_cache._write_cache_mesh(cache, segments)

    degrees = [0 for _ in cache.data.vertices]
    for edge in cache.data.edges:
        degrees[edge.vertices[0]] += 1
        degrees[edge.vertices[1]] += 1
    assert degrees[0] == 1 and degrees[-1] == 1, degrees
    assert all(degree == 2 for degree in degrees[1:-1]), degrees

    intersection_cache._apply_display_modifier(
        obj,
        cache,
        [obj],
        0.005,
        0.0,
        outline_setup.get_line_material(obj, "intersection"),
    )


def setup_camera() -> None:
    bpy.ops.object.light_add(type="AREA", location=(0.0, 0.0, 5.0))
    light = bpy.context.object
    light.data.energy = 450.0
    light.data.size = 5.0
    bpy.ops.object.camera_add(
        location=(0.0, 0.0, 6.0),
        rotation=(0.0, 0.0, 0.0),
    )
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 6.5
    bpy.context.scene.camera = camera


def render_image() -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 1600
    scene.render.resolution_y = 900
    scene.render.film_transparent = False
    scene.world.color = (1.0, 1.0, 1.0)
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "Medium High Contrast"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(OUT_PATH)
    bpy.ops.render.render(write_still=True)


def main() -> None:
    b_manga_line.register()
    clear_scene()
    obj = make_textured_folded_strip()
    configure_lines(obj)
    setup_camera()
    assert presets.apply_line_settings(
        obj,
        bpy.context,
        refresh_scene=False,
        line_targets=("outline", "inner"),
    )
    bpy.context.view_layer.update()
    add_cached_intersection_curve(obj)
    camera_comp.refresh_objects(
        bpy.context,
        [obj],
        update_visibility=True,
        width_targets=("outline", "inner", "intersection"),
    )
    render_image()
    print(f"[PASS] midpoint angle visual: {OUT_PATH}")


if __name__ == "__main__":
    main()
