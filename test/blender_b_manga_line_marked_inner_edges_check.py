"""B-MANGA Line: internal lines can use only marked mesh edges."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, inner_lines  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_material(name: str, color: tuple[float, float, float, float]):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    return mat


def _make_folded_strip(name: str) -> tuple[bpy.types.Object, bpy.types.Material, list[tuple[int, int]]]:
    levels = 5
    verts = []
    faces = []
    ridge_pairs = []
    for i in range(levels):
        x = i / (levels - 1) * 4.0 - 2.0
        verts.extend(((x, -0.5, 0.0), (x, 0.0, 0.35), (x, 0.5, 0.0)))
    for i in range(levels - 1):
        current = i * 3
        nxt = (i + 1) * 3
        faces.append((current, nxt, nxt + 1, current + 1))
        faces.append((current + 1, nxt + 1, nxt + 2, current + 2))
        ridge_pairs.append((current + 1, nxt + 1))

    mesh = bpy.data.meshes.new(name + "_mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(_make_material(name + "_surface", (1, 1, 1, 1)))
    line_mat = _make_material(name + "_line", (0, 0, 0, 1))
    obj.data.materials.append(line_mat)
    return obj, line_mat, ridge_pairs


def _edge_index_for_vertices(mesh: bpy.types.Mesh, vertices: tuple[int, int]) -> int:
    wanted = set(vertices)
    for index, edge in enumerate(mesh.edges):
        if set(edge.vertices) == wanted:
            return index
    raise AssertionError(f"edge {vertices} not found")


def _set_edge_value(
    mesh: bpy.types.Mesh,
    attr_name: str,
    data_type: str,
    vertices: tuple[int, int],
    value,
) -> None:
    attr = mesh.attributes.get(attr_name)
    if attr is None:
        attr = mesh.attributes.new(attr_name, data_type, "EDGE")
    edge_index = _edge_index_for_vertices(mesh, vertices)
    attr.data[edge_index].value = value
    mesh.update()


def _modifier_input(mod, socket_name: str):
    tree = mod.node_group
    assert tree is not None
    for item in tree.interface.items_tree:
        if (
            getattr(item, "name", None) == socket_name
            and getattr(item, "in_out", None) == "INPUT"
        ):
            return mod[item.identifier]
    raise AssertionError(f"{socket_name} socket not found")


def _line_coords(obj: bpy.types.Object, line_mat: bpy.types.Material):
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    try:
        line_index = None
        for index, mat in enumerate(mesh.materials):
            if mat and mat.name.startswith(line_mat.name):
                line_index = index
                break
        assert line_index is not None, f"{line_mat.name}: line material not found"

        line_vertices = set()
        for poly in mesh.polygons:
            if poly.material_index == line_index:
                line_vertices.update(poly.vertices)
        return [mesh.vertices[index].co.copy() for index in line_vertices]
    finally:
        bpy.data.meshes.remove(mesh)


def _assert_line_span(
    obj: bpy.types.Object,
    line_mat: bpy.types.Material,
    expected_min: float,
    expected_max: float,
) -> None:
    coords = _line_coords(obj, line_mat)
    assert coords, f"{obj.name}: 指定済みの内部線が生成されていません"
    actual_min = min(co.x for co in coords)
    actual_max = max(co.x for co in coords)
    assert abs(actual_min - expected_min) < 0.08, (actual_min, actual_max)
    assert abs(actual_max - expected_max) < 0.08, (actual_min, actual_max)


def _apply(
    obj: bpy.types.Object,
    line_mat: bpy.types.Material,
    *,
    marked_only: bool,
) -> None:
    ok = inner_lines.apply_inner_lines(
        obj,
        angle=math.radians(10.0),
        thickness=0.04,
        material=line_mat,
        use_marked_edges=marked_only,
    )
    assert ok, f"{obj.name}: 内部線を追加できませんでした"


def main() -> None:
    b_manga_line.register()
    try:
        _clear_scene()

        angle_obj, angle_mat, _ = _make_folded_strip("BML_marked_angle_mode")
        assert angle_obj.bmanga_line_settings.use_marked_inner_edges is False
        _apply(angle_obj, angle_mat, marked_only=False)
        assert _line_coords(angle_obj, angle_mat), "初期状態の角度検出で内部線が出ていません"
        angle_mod = angle_obj.modifiers.get(core.GN_MODIFIER_NAME)
        assert angle_mod is not None
        assert _modifier_input(angle_mod, "指定済みの辺だけ線にする") is False

        empty_obj, empty_mat, _ = _make_folded_strip("BML_marked_empty")
        _apply(empty_obj, empty_mat, marked_only=True)
        assert not _line_coords(empty_obj, empty_mat), "未指定の辺に内部線が生成されています"

        sharp_obj, sharp_mat, sharp_ridges = _make_folded_strip("BML_marked_sharp")
        _set_edge_value(sharp_obj.data, "sharp_edge", "BOOLEAN", sharp_ridges[1], True)
        _apply(sharp_obj, sharp_mat, marked_only=True)
        sharp_mod = sharp_obj.modifiers.get(core.GN_MODIFIER_NAME)
        assert sharp_mod is not None
        assert _modifier_input(sharp_mod, "指定済みの辺だけ線にする") is True
        _assert_line_span(sharp_obj, sharp_mat, -1.0, 0.0)

        crease_obj, crease_mat, crease_ridges = _make_folded_strip("BML_marked_crease")
        _set_edge_value(crease_obj.data, "crease_edge", "FLOAT", crease_ridges[2], 0.75)
        _apply(crease_obj, crease_mat, marked_only=True)
        _assert_line_span(crease_obj, crease_mat, 0.0, 1.0)

        print("[PASS] marked inner edges use sharp_edge / crease_edge only")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
