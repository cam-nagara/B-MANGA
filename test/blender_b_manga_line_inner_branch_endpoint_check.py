"""B-MANGA Line: inner midpoint width treats branch intersections as endpoints."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import inner_lines  # noqa: E402


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


def _make_marked_t_junction() -> tuple[bpy.types.Object, bpy.types.Material]:
    mesh = bpy.data.meshes.new("BML_inner_branch_endpoint_mesh")
    verts = [
        (-2.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (0.0, 2.0, 0.0),
    ]
    edges = [(0, 1), (1, 2), (1, 3)]
    mesh.from_pydata(verts, edges, [])
    mesh.update()

    attr = mesh.attributes.new("sharp_edge", "BOOLEAN", "EDGE")
    for item in attr.data:
        item.value = True

    obj = bpy.data.objects.new("BML_inner_branch_endpoint", mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(_make_material("BML_inner_branch_surface", (1, 1, 1, 1)))
    line_mat = _make_material("BML_inner_branch_line", (0, 0, 0, 1))
    obj.data.materials.append(line_mat)
    return obj, line_mat


def _make_marked_subdivided_t_junction() -> tuple[bpy.types.Object, bpy.types.Material]:
    mesh = bpy.data.meshes.new("BML_inner_branch_endpoint_subdivided_mesh")
    verts = [
        (-2.0, 0.0, 0.0),
        (-1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 2.0, 0.0),
    ]
    edges = [(0, 1), (1, 2), (2, 3), (3, 4), (2, 5), (5, 6)]
    mesh.from_pydata(verts, edges, [])
    mesh.update()

    attr = mesh.attributes.new("sharp_edge", "BOOLEAN", "EDGE")
    for item in attr.data:
        item.value = True

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
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
