"""B-MANGA Line: midpoint width settings are independent per line type."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, vertex_analysis  # noqa: E402


LEVELS = 9


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _make_folded_strip(name: str) -> bpy.types.Object:
    verts = []
    faces = []
    for i in range(LEVELS):
        x = i / (LEVELS - 1) * 4.0 - 2.0
        verts.extend(((x, -0.5, 0.0), (x, 0.0, 0.35), (x, 0.5, 0.0)))
    for i in range(LEVELS - 1):
        current = i * 3
        nxt = (i + 1) * 3
        faces.append((current, nxt, nxt + 1, current + 1))
        faces.append((current + 1, nxt + 1, nxt + 2, current + 2))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(bpy.data.materials.new(name + "_surface"))
    return obj


def _make_segmented_box(name: str) -> bpy.types.Object:
    verts = []
    faces = []
    for i in range(LEVELS):
        x = i / (LEVELS - 1) * 4.0 - 2.0
        verts.extend(
            (
                (x, -0.5, -0.5),
                (x, 0.5, -0.5),
                (x, 0.5, 0.5),
                (x, -0.5, 0.5),
            )
        )
    for i in range(LEVELS - 1):
        current = i * 4
        nxt = (i + 1) * 4
        faces.extend(
            (
                (current, nxt, nxt + 1, current + 1),
                (current + 1, nxt + 1, nxt + 2, current + 2),
                (current + 2, nxt + 2, nxt + 3, current + 3),
                (current + 3, nxt + 3, nxt, current),
            )
        )
    faces.append((0, 1, 2, 3))
    last = (LEVELS - 1) * 4
    faces.append((last, last + 3, last + 2, last + 1))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(bpy.data.materials.new(name + "_surface"))
    return obj


def _center_ridge_vertex() -> int:
    return (LEVELS // 2) * 3 + 1


def _center_box_corner_vertex() -> int:
    return (LEVELS // 2) * 4 + 3


def _weight(obj: bpy.types.Object, group_name: str, vertex_index: int) -> float:
    vg = obj.vertex_groups.get(group_name)
    assert vg is not None, f"{group_name} がありません"
    return vg.weight(vertex_index)


def _reset_all_groups(obj: bpy.types.Object) -> None:
    for name in (
        core.VG_LINE_WIDTH,
        core.VG_INNER_LINE_WIDTH,
        core.VG_INTERSECTION_LINE_WIDTH,
    ):
        vertex_analysis.reset_width_weights(obj, group_name=name)


def _assert_target_only(target: str, group_name: str, factor_prop: str) -> None:
    obj = _make_folded_strip("BML_midpoint_" + target)
    settings = obj.bmanga_line_settings
    settings.inner_line_angle = math.radians(10.0)
    _reset_all_groups(obj)
    setattr(settings, factor_prop, -1.0)

    vertex_analysis.compute_and_apply_weights(obj, settings, target)
    center = _center_ridge_vertex()
    assert _weight(obj, group_name, center) < 0.001

    untouched = {
        core.VG_LINE_WIDTH,
        core.VG_INNER_LINE_WIDTH,
        core.VG_INTERSECTION_LINE_WIDTH,
    } - {group_name}
    for other_name in untouched:
        assert _weight(obj, other_name, center) > 0.999, (target, other_name)


def _assert_outline_uses_detection_angle() -> None:
    obj = _make_segmented_box("BML_midpoint_outline_angle")
    settings = obj.bmanga_line_settings
    settings.edge_smooth_factor = -1.0
    center = _center_box_corner_vertex()

    settings.inner_line_angle = math.radians(120.0)
    vertex_analysis.compute_and_apply_weights(obj, settings, "outline")
    assert _weight(obj, core.VG_LINE_WIDTH, center) > 0.999

    settings.inner_line_angle = math.radians(10.0)
    vertex_analysis.compute_and_apply_weights(obj, settings, "outline")
    assert _weight(obj, core.VG_LINE_WIDTH, center) < 0.001


def main() -> None:
    b_manga_line.register()
    _clear_scene()

    _assert_target_only("outline", core.VG_LINE_WIDTH, "edge_smooth_factor")
    _assert_target_only("inner", core.VG_INNER_LINE_WIDTH, "inner_edge_smooth_factor")
    _assert_target_only(
        "intersection",
        core.VG_INTERSECTION_LINE_WIDTH,
        "intersection_edge_smooth_factor",
    )
    _assert_outline_uses_detection_angle()

    print("[PASS] midpoint width settings are independent per line type")


if __name__ == "__main__":
    main()
