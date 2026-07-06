"""B-MANGA Line: inner midpoint width treats branch intersections as endpoints."""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import inner_lines  # noqa: E402
from b_manga_line.core import FREESTYLE_EDGE_ATTR  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for datablocks in (bpy.data.meshes, bpy.data.materials, bpy.data.node_groups):
        for item in list(datablocks):
            if item.users == 0:
                datablocks.remove(item)


def _make_material(name: str, color: tuple[float, float, float, float]):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    return mat


def _make_marked_edge_mesh(
    name: str,
    core_verts: list[tuple[float, float, float]],
    selected_edges: list[tuple[int, int]],
) -> bpy.types.Mesh:
    verts = list(core_verts)
    faces = []
    side_offset = 0.12
    for a, b in selected_edges:
        start = Vector(core_verts[a])
        end = Vector(core_verts[b])
        direction = end - start
        perp = Vector((-direction.y, direction.x, 0.0))
        if perp.length <= 1.0e-8:
            perp = Vector((0.0, 0.0, side_offset))
        else:
            perp.normalize()
            perp *= side_offset
        side_a = len(verts)
        side_b = side_a + 1
        verts.append(tuple(start + perp))
        verts.append(tuple(end + perp))
        faces.append((a, b, side_b, side_a))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    attr = mesh.attributes.new(FREESTYLE_EDGE_ATTR, "BOOLEAN", "EDGE")
    selected_lookup = {tuple(sorted(pair)) for pair in selected_edges}
    for edge in mesh.edges:
        attr.data[edge.index].value = tuple(sorted(edge.vertices)) in selected_lookup
    return mesh


def _make_marked_t_junction() -> tuple[bpy.types.Object, bpy.types.Material]:
    mesh = _make_marked_edge_mesh(
        "BML_inner_branch_endpoint_mesh",
        [
        (-2.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (0.0, 2.0, 0.0),
        ],
        [(0, 1), (1, 2), (1, 3)],
    )

    obj = bpy.data.objects.new("BML_inner_branch_endpoint", mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(_make_material("BML_inner_branch_surface", (1, 1, 1, 1)))
    line_mat = _make_material("BML_inner_branch_line", (0, 0, 0, 1))
    obj.data.materials.append(line_mat)
    return obj, line_mat


def _make_marked_subdivided_t_junction() -> tuple[bpy.types.Object, bpy.types.Material]:
    mesh = _make_marked_edge_mesh(
        "BML_inner_branch_endpoint_subdivided_mesh",
        [
        (-2.0, 0.0, 0.0),
        (-1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 2.0, 0.0),
        ],
        [(0, 1), (1, 2), (2, 3), (3, 4), (2, 5), (5, 6)],
    )

    obj = bpy.data.objects.new("BML_inner_branch_endpoint_subdivided", mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(_make_material("BML_inner_branch_subdivided_surface", (1, 1, 1, 1)))
    line_mat = _make_material("BML_inner_branch_subdivided_line", (0, 0, 0, 1))
    obj.data.materials.append(line_mat)
    return obj, line_mat


def _evaluated_line_mesh(obj: bpy.types.Object) -> bpy.types.Mesh:
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))


def _line_vertices(mesh: bpy.types.Mesh, material: bpy.types.Material) -> list[Vector]:
    material_index = None
    for index, mat in enumerate(mesh.materials):
        if mat == material or (mat and mat.name.startswith(material.name)):
            material_index = index
            break
    assert material_index is not None, "内部線の素材が見つかりません"

    indices: set[int] = set()
    for poly in mesh.polygons:
        if poly.material_index == material_index:
            indices.update(poly.vertices)
    assert indices, "内部線ジオメトリが生成されていません"
    return [mesh.vertices[index].co.copy() for index in indices]


def _sample_radius(vertices: list[Vector], center: tuple[float, float, float]) -> float:
    center_vec = Vector(center)
    nearby = [co for co in vertices if (co - center_vec).length <= 0.09]
    assert nearby, f"{center} 付近の内部線頂点を測定できません"
    return max((co - center_vec).length for co in nearby)


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()
        obj, line_mat = _make_marked_t_junction()
        assert inner_lines.apply_inner_lines(
            obj,
            angle=math.radians(60.0),
            thickness=0.04,
            offset=0.0,
            material=line_mat,
            use_marked_edges=True,
            midpoint_factor=-1.0,
            resample_count=9,
        )

        mesh = _evaluated_line_mesh(obj)
        vertices = _line_vertices(mesh, line_mat)
        branch_radius = _sample_radius(vertices, (0.0, 0.0, 0.0))
        left_mid_radius = _sample_radius(vertices, (-1.0, 0.0, 0.0))
        right_mid_radius = _sample_radius(vertices, (1.0, 0.0, 0.0))
        upper_mid_radius = _sample_radius(vertices, (0.0, 1.0, 0.0))

        assert branch_radius > 0.015, {
            "branch": branch_radius,
            "left_mid": left_mid_radius,
            "right_mid": right_mid_radius,
            "upper_mid": upper_mid_radius,
        }
        assert max(left_mid_radius, right_mid_radius, upper_mid_radius) < 0.004, {
            "branch": branch_radius,
            "left_mid": left_mid_radius,
            "right_mid": right_mid_radius,
            "upper_mid": upper_mid_radius,
        }

        _clear_scene()
        obj, line_mat = _make_marked_subdivided_t_junction()
        assert inner_lines.apply_inner_lines(
            obj,
            angle=math.radians(60.0),
            thickness=0.04,
            offset=0.0,
            material=line_mat,
            use_marked_edges=True,
            midpoint_factor=-1.0,
            resample_count=9,
        )
        mesh = _evaluated_line_mesh(obj)
        vertices = _line_vertices(mesh, line_mat)
        branch_radius_subdivided = _sample_radius(vertices, (0.0, 0.0, 0.0))
        near_branch_radius = _sample_radius(vertices, (-0.5, 0.0, 0.0))
        assert branch_radius_subdivided > 0.015, {
            "branch": branch_radius_subdivided,
            "near_branch": near_branch_radius,
        }
        assert 0.006 < near_branch_radius < 0.014, {
            "branch": branch_radius_subdivided,
            "near_branch": near_branch_radius,
        }
        print(
            "[PASS] inner midpoint width uses branch intersections as endpoints:",
            {
                "branch": round(branch_radius, 5),
                "left_mid": round(left_mid_radius, 5),
                "right_mid": round(right_mid_radius, 5),
                "upper_mid": round(upper_mid_radius, 5),
                "subdivided_branch": round(branch_radius_subdivided, 5),
                "subdivided_near_branch": round(near_branch_radius, 5),
            },
            flush=True,
        )
        os._exit(0)
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.wm.quit_blender()


if __name__ == "__main__":
    main()
