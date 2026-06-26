"""B-MANGA Line visual check: cube + cone with tapered cube inner lines."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    inner_lines,
    intersection_lines,
    outline_setup,
    vertex_analysis,
)


OUT_DIR = ROOT / "_verify" / "b_manga_line_cone_cube_visual"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FULL = OUT_DIR / "cone_cube_corner_inner_taper_full.png"
OUT_CORNER = OUT_DIR / "cone_cube_corner_inner_taper_zoom.png"
OUT_LINES = OUT_DIR / "cone_cube_corner_inner_taper_lines_only.png"

CUBE_SIZE = 2.6
CUBE_HEIGHT = 1.45
NEAR_CORNER = Vector((CUBE_SIZE * 0.5, -CUBE_SIZE * 0.5, CUBE_HEIGHT * 0.5))
NEAR_CORNER_EDGES = (
    (NEAR_CORNER, Vector((-CUBE_SIZE * 0.5, -CUBE_SIZE * 0.5, CUBE_HEIGHT * 0.5))),
    (NEAR_CORNER, Vector((CUBE_SIZE * 0.5, CUBE_SIZE * 0.5, CUBE_HEIGHT * 0.5))),
    (NEAR_CORNER, Vector((CUBE_SIZE * 0.5, -CUBE_SIZE * 0.5, -CUBE_HEIGHT * 0.5))),
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
    ):
        for datablock in list(collection):
            if datablock.users == 0:
                collection.remove(datablock)


def _make_surface_material(name: str, color: tuple[float, float, float, float]):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = 0.62
    return mat


def _make_black_material(name: str, *, emission: bool = False):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (0.0, 0.0, 0.0, 1.0)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    if emission:
        shader = nodes.new("ShaderNodeEmission")
        shader.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
        shader.inputs["Strength"].default_value = 1.0
        links.new(shader.outputs["Emission"], out.inputs["Surface"])
    else:
        shader = nodes.new("ShaderNodeBsdfPrincipled")
        shader.inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1.0)
        shader.inputs["Roughness"].default_value = 0.55
        links.new(shader.outputs["BSDF"], out.inputs["Surface"])
    return mat


def _look_at(obj: bpy.types.Object, target: Vector) -> None:
    direction = target - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _setup_camera(
    *,
    location: tuple[float, float, float],
    target: tuple[float, float, float],
    ortho_scale: float,
) -> None:
    bpy.ops.object.camera_add(location=location)
    camera = bpy.context.object
    camera.name = "AI目視_カメラ"
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = ortho_scale
    _look_at(camera, Vector(target))
    bpy.context.scene.camera = camera


def _setup_light() -> None:
    bpy.ops.object.light_add(type="AREA", location=(-3.2, -4.2, 5.0))
    light = bpy.context.object
    light.name = "AI目視_ライト"
    light.data.energy = 520
    light.data.size = 5.5


def _add_camera_backdrop(camera: bpy.types.Object, *, size: float) -> None:
    direction = camera.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
    right = camera.matrix_world.to_quaternion() @ Vector((1.0, 0.0, 0.0))
    up = camera.matrix_world.to_quaternion() @ Vector((0.0, 1.0, 0.0))
    center = Vector((0.0, 0.0, 0.0)) + direction * 0.75
    verts = [
        tuple(center - right * size - up * size),
        tuple(center + right * size - up * size),
        tuple(center + right * size + up * size),
        tuple(center - right * size + up * size),
    ]
    mesh = bpy.data.meshes.new("AI目視_白背景")
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    mesh.update()
    plane = bpy.data.objects.new("AI目視_白背景", mesh)
    bpy.context.collection.objects.link(plane)
    plane.data.materials.append(_make_surface_material("AI目視_白背景", (1.0, 1.0, 1.0, 1.0)))


def _setup_render() -> None:
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"
    if hasattr(scene, "eevee"):
        scene.eevee.taa_render_samples = 96
    scene.render.resolution_x = 1600
    scene.render.resolution_y = 1100
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "Medium High Contrast"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.world.color = (0.78, 0.78, 0.78)


def _distance_to_segment(point: Vector, start: Vector, end: Vector) -> float:
    axis = end - start
    denom = axis.dot(axis)
    if denom <= 1e-8:
        return (point - start).length
    t = max(0.0, min(1.0, (point - start).dot(axis) / denom))
    return (point - (start + axis * t)).length


def _add_midpoint_supplement_modifier(obj: bpy.types.Object) -> None:
    sub = obj.modifiers.new(name="AI目視_中間頂点補足", type="SUBSURF")
    sub.subdivision_type = "SIMPLE"
    sub.levels = 3
    sub.render_levels = 3


def _make_cube(surface_mat: bpy.types.Material) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    cube = bpy.context.object
    cube.name = "AI目視_キューブ_中間頂点補足"
    cube.dimensions = (CUBE_SIZE, CUBE_SIZE, CUBE_HEIGHT)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    cube.data.materials.append(surface_mat)

    _add_midpoint_supplement_modifier(cube)
    outline_setup.apply_outline(
        cube,
        thickness=0.026,
        color=(0.0, 0.0, 0.0, 1.0),
        use_rim=True,
        use_vertex_group=True,
        scene=bpy.context.scene,
    )

    inner_mat = _make_black_material("AI目視_キューブ内部線_黒")
    assert inner_lines.apply_inner_lines(
        cube,
        angle=math.radians(8.0),
        thickness=0.090,
        material=inner_mat,
    )

    settings = cube.bmanga_line_settings
    settings.edge_smooth_factor = -1.0
    settings.use_vertex_color = False
    settings.use_ao_influence = False
    vertex_analysis.compute_and_apply_weights(cube, settings)

    vg = cube.vertex_groups.get("BML_LineWidth")
    weights = []
    if vg is not None:
        for vert in cube.data.vertices:
            try:
                weights.append(vg.weight(vert.index))
            except RuntimeError:
                pass
    if weights:
        print(
            f"[WEIGHT] キューブ 中間頂点補足後: min={min(weights):.4f} max={max(weights):.4f}",
            flush=True,
        )
    return cube


def _make_cone(surface_mat: bpy.types.Material) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cone_add(
        vertices=48,
        radius1=0.96,
        radius2=0.0,
        depth=3.15,
        location=(0.0, 0.0, 0.38),
    )
    cone = bpy.context.object
    cone.name = "AI目視_少し大きめ円錐"
    cone.data.materials.append(surface_mat)
    outline_setup.apply_outline(
        cone,
        thickness=0.12,
        color=(0.0, 0.0, 0.0, 1.0),
        use_rim=True,
        use_vertex_group=True,
        scene=bpy.context.scene,
    )
    return cone


def _render_scene(path: Path, *, location, target, ortho_scale) -> bpy.types.Object:
    _setup_camera(location=location, target=target, ortho_scale=ortho_scale)
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    print(f"[OUT] {path}", flush=True)
    return bpy.context.scene.camera


def _extract_near_corner_inner_lines(cube: bpy.types.Object) -> bpy.types.Object:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    src_mesh = bpy.data.meshes.new_from_object(cube.evaluated_get(depsgraph))
    selected_materials = {
        index
        for index, mat in enumerate(src_mesh.materials)
        if mat and mat.name.startswith("AI目視_キューブ内部線_黒")
    }
    assert selected_materials, "キューブ内部線の素材が見つかりません"

    used: dict[int, int] = {}
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    for poly in src_mesh.polygons:
        if poly.material_index not in selected_materials:
            continue
        center = sum((src_mesh.vertices[vi].co for vi in poly.vertices), Vector())
        center /= len(poly.vertices)
        if min(
            _distance_to_segment(center, start, end)
            for start, end in NEAR_CORNER_EDGES
        ) > 0.085:
            continue
        face = []
        for vi in poly.vertices:
            if vi not in used:
                used[vi] = len(verts)
                verts.append(tuple(src_mesh.vertices[vi].co))
            face.append(used[vi])
        faces.append(tuple(face))

    assert faces, "こちらを向く角の3本の内部線を抽出できません"
    mesh = bpy.data.meshes.new("AI目視_キューブ角_内部線のみ")
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    obj = bpy.data.objects.new("AI目視_キューブ角_内部線のみ", mesh)
    obj.data.materials.append(_make_black_material("AI目視_内部線のみ_黒", emission=True))
    return obj


def _print_near_corner_line_widths(line_obj: bpy.types.Object) -> None:
    bins = {
        "角付近": [],
        "中間": [],
        "反対側": [],
    }
    for vertex in line_obj.data.vertices:
        co = vertex.co
        best = None
        for start, end in NEAR_CORNER_EDGES:
            axis = end - start
            denom = axis.dot(axis)
            if denom <= 1e-8:
                continue
            t = max(0.0, min(1.0, (co - start).dot(axis) / denom))
            center = start + axis * t
            dist = (co - center).length
            if best is None or dist < best[0]:
                best = (dist, t)
        if best is None:
            continue
        dist, t = best
        if t < 0.12:
            bins["角付近"].append(dist)
        elif 0.45 <= t <= 0.55:
            bins["中間"].append(dist)
        elif t > 0.88:
            bins["反対側"].append(dist)
    for label, values in bins.items():
        if values:
            print(f"[LINE_WIDTH] {label}: radius={max(values):.5f}", flush=True)


def _render_extracted_lines(cube: bpy.types.Object) -> None:
    lines = _extract_near_corner_inner_lines(cube)
    _print_near_corner_line_widths(lines)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    bpy.context.collection.objects.link(lines)
    _setup_render()
    bpy.context.scene.world.color = (1.0, 1.0, 1.0)
    _setup_camera(
        location=(4.0, -5.4, 2.9),
        target=(0.0, 0.0, 0.0),
        ortho_scale=4.0,
    )
    _add_camera_backdrop(bpy.context.scene.camera, size=8.0)
    bpy.context.scene.render.filepath = str(OUT_LINES)
    bpy.ops.render.render(write_still=True)
    print(f"[OUT] {OUT_LINES}", flush=True)


def main() -> None:
    b_manga_line.register()
    _clear_scene()
    _setup_render()
    _setup_light()

    white = _make_surface_material("AI目視_白", (1.0, 1.0, 1.0, 1.0))
    cube = _make_cube(white)
    cone = _make_cone(white)

    line_mat = outline_setup.get_outline_material(cube)
    assert line_mat is not None, "キューブの線素材がありません"
    assert intersection_lines.apply_intersection_lines(
        cube,
        target=cone,
        thickness=0.020,
        material=line_mat,
        method="BOOLEAN",
    )

    _render_scene(
        OUT_FULL,
        location=(3.3, -6.1, 2.7),
        target=(0.0, 0.0, 0.22),
        ortho_scale=4.6,
    )
    _render_scene(
        OUT_CORNER,
        location=(4.1, -5.5, 2.9),
        target=(0.78, -0.78, 0.35),
        ortho_scale=1.85,
    )
    _render_extracted_lines(cube)


if __name__ == "__main__":
    main()
