"""B-MANGA Line: internal lines follow midpoint vertex width weights."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import inner_line_cache, inner_lines, vertex_analysis  # noqa: E402
from b_manga_line.core import VG_INNER_LINE_WIDTH  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_material(name: str, color: tuple[float, float, float, float]):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    return mat


def _make_folded_strip() -> bpy.types.Object:
    levels = 9
    verts = []
    faces = []
    for i in range(levels):
        x = i / (levels - 1) * 4.0 - 2.0
        verts.extend(((x, -0.5, 0.0), (x, 0.0, 0.35), (x, 0.5, 0.0)))
    for i in range(levels - 1):
        current = i * 3
        nxt = (i + 1) * 3
        faces.append((current, nxt, nxt + 1, current + 1))
        faces.append((current + 1, nxt + 1, nxt + 2, current + 2))

    mesh = bpy.data.meshes.new("BML_inner_width_folded_strip")
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    obj = bpy.data.objects.new("BML_inner_width_folded_strip", mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(_make_material("BML_inner_width_surface", (1, 1, 1, 1)))
    line_mat = _make_material("BML_inner_width_line", (0, 0, 0, 1))
    obj.data.materials.append(line_mat)

    ok = inner_lines.apply_inner_lines(
        obj,
        angle=math.radians(10.0),
        thickness=0.04,
        offset=0.0,
        material=line_mat,
    )
    assert ok, "内部線を追加できませんでした"

    weights = [1.0] * len(obj.data.vertices)
    for i in range(levels):
        t = i / (levels - 1)
        midpoint = 1.0 - abs(t - 0.5) * 2.0
        weight = 1.0 - midpoint
        for offset in range(3):
            weights[i * 3 + offset] = weight
    vertex_analysis.write_width_weights(obj, weights, "inner")
    assert obj.data.attributes.get(VG_INNER_LINE_WIDTH) is not None
    assert obj.vertex_groups.get(VG_INNER_LINE_WIDTH) is None
    obj.data.update()
    obj.update_tag(refresh={"DATA"})
    assert inner_lines.update_parameters(obj), "保存済み稜谷線の線幅属性を更新できませんでした"
    bpy.context.view_layer.update()
    return obj


def _make_low_density_folded_strip() -> bpy.types.Object:
    verts = [
        (-2.0, -0.5, 0.0),
        (-2.0, 0.0, 0.35),
        (-2.0, 0.5, 0.0),
        (2.0, -0.5, 0.0),
        (2.0, 0.0, 0.35),
        (2.0, 0.5, 0.0),
    ]
    faces = [
        (0, 3, 4, 1),
        (1, 4, 5, 2),
    ]
    mesh = bpy.data.meshes.new("BML_inner_width_low_density_strip")
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    obj = bpy.data.objects.new("BML_inner_width_low_density_strip", mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(_make_material("BML_inner_width_low_surface", (1, 1, 1, 1)))
    line_mat = _make_material("BML_inner_width_line_low", (0, 0, 0, 1))
    obj.data.materials.append(line_mat)

    ok = inner_lines.apply_inner_lines(
        obj,
        angle=math.radians(10.0),
        thickness=0.04,
        offset=0.0,
        material=line_mat,
        midpoint_factor=-1.0,
        resample_count=1,
    )
    assert ok, "低密度メッシュへ内部線を追加できませんでした"
    bpy.context.view_layer.update()
    return obj


def _sample_line_radii(mesh: bpy.types.Mesh) -> dict[float, float]:
    line_index = None
    for i, mat in enumerate(mesh.materials):
        if mat and mat.name.startswith("BML_inner_width_line"):
            line_index = i
            break
    assert line_index is not None, "内部線の素材が見つかりません"

    line_vertices = set()
    for poly in mesh.polygons:
        if poly.material_index == line_index:
            line_vertices.update(poly.vertices)

    samples: dict[float, list[float]] = {-2.0: [], 0.0: [], 2.0: []}
    for vi in line_vertices:
        co = mesh.vertices[vi].co
        nearest = min(samples, key=lambda x: abs(co.x - x))
        if abs(co.x - nearest) <= 0.08:
            samples[nearest].append(math.hypot(co.y, co.z - 0.35))

    radii = {}
    for x, values in samples.items():
        assert values, f"x={x} の内部線を測定できませんでした"
        radii[x] = max(values)
    return radii


def _assert_profile_quality(obj: bpy.types.Object) -> None:
    mod = obj.modifiers.get("BML_InnerLines")
    assert mod is not None and mod.node_group is not None, "内部線ノードが見つかりません"
    assert inner_line_cache.is_cached_modifier(mod), "稜谷線が保存済み線方式ではありません"
    profile = next(
        (
            node for node in mod.node_group.nodes
            if getattr(node, "label", "") == inner_line_cache._PROFILE_LABEL
        ),
        None,
    )
    assert profile is not None, "内部線の滑らかな断面ノードがありません"
    resolution = profile.inputs.get("Resolution")
    assert resolution is not None and int(resolution.default_value) >= 12, (
        "内部線の断面解像度が低すぎます"
    )
    assert any(
        node.bl_idname == "GeometryNodeSetShadeSmooth"
        for node in mod.node_group.nodes
    ), "内部線チューブがスムーズ化されていません"


def main() -> None:
    b_manga_line.register()
    _clear_scene()
    obj = _make_folded_strip()
    _assert_profile_quality(obj)

    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    radii = _sample_line_radii(mesh)

    assert abs(radii[-2.0] - 0.02) < 0.005, radii
    assert abs(radii[2.0] - 0.02) < 0.005, radii
    assert radii[0.0] < 0.001, radii

    low = _make_low_density_folded_strip()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    low_mesh = bpy.data.meshes.new_from_object(low.evaluated_get(depsgraph))
    low_radii = _sample_line_radii(low_mesh)
    assert abs(low_radii[-2.0] - 0.02) < 0.005, low_radii
    assert abs(low_radii[2.0] - 0.02) < 0.005, low_radii
    assert low_radii[0.0] < 0.001, low_radii
    print(f"[PASS] inner line midpoint radius reaches zero: {radii}")


if __name__ == "__main__":
    main()
