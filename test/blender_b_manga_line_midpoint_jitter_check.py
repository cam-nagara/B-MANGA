"""B-MANGA Line: midpoint jitter moves the zero-width vertex along sharp edges."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import outline_setup, vertex_analysis  # noqa: E402
from b_manga_line.core import VG_LINE_WIDTH  # noqa: E402


LEVELS = 21


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


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
        a = i * 4
        b = (i + 1) * 4
        faces.extend(
            (
                (a, b, b + 1, a + 1),
                (a + 1, b + 1, b + 2, a + 2),
                (a + 2, b + 2, b + 3, a + 3),
                (a + 3, b + 3, b, a),
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
    obj.data.materials.append(bpy.data.materials.new(name + "_mat"))
    return obj


def _edge_indices(corner_offset: int) -> list[int]:
    return [i * 4 + corner_offset for i in range(LEVELS)]


def _zero_t_values(obj: bpy.types.Object, corner_offset: int) -> list[float]:
    vg = obj.vertex_groups.get(VG_LINE_WIDTH)
    assert vg is not None, "線幅用の頂点グループがありません"
    values = []
    for i, vi in enumerate(_edge_indices(corner_offset)):
        if vg.weight(vi) < 1e-6:
            values.append(i / (LEVELS - 1))
    return values


def _weight_at_t(obj: bpy.types.Object, corner_offset: int, t: float) -> float:
    vg = obj.vertex_groups.get(VG_LINE_WIDTH)
    assert vg is not None, "線幅用の頂点グループがありません"
    index = round(t * (LEVELS - 1))
    return vg.weight(_edge_indices(corner_offset)[index])


def _apply_weights(
    obj: bpy.types.Object,
    jitter_percent: float,
    curve_points: tuple[float, float, float] = (0.25, 0.50, 0.75),
) -> None:
    settings = obj.bmanga_line_settings
    settings.edge_smooth_factor = -1.0
    settings.edge_midpoint_jitter_percent = jitter_percent
    settings.edge_width_curve_25 = curve_points[0]
    settings.edge_width_curve_50 = curve_points[1]
    settings.edge_width_curve_75 = curve_points[2]
    settings.use_vertex_color = False
    vertex_analysis.compute_and_apply_weights(obj, settings)


def _select_objects(active: bpy.types.Object, others: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    active.select_set(True)
    for obj in others:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active


def _apply_outline_for_update_test(obj: bpy.types.Object) -> None:
    assert outline_setup.apply_outline(
        obj,
        thickness=0.02,
        color=(0.0, 0.0, 0.0, 1.0),
        use_vertex_group=True,
        scene=bpy.context.scene,
    )


def _assert_multiselect_curve_update() -> None:
    first = _make_segmented_box("BML_curve_update_active")
    second = _make_segmented_box("BML_curve_update_selected")
    _apply_outline_for_update_test(first)
    _apply_outline_for_update_test(second)
    _select_objects(first, [second])

    settings = first.bmanga_line_settings
    settings.edge_smooth_factor = -1.0
    settings.edge_midpoint_jitter_percent = 0.0
    settings.edge_width_curve_25 = 1.0
    settings.edge_width_curve_50 = 1.0
    settings.edge_width_curve_75 = 1.0
    assert bpy.ops.bmanga_line.update_target("EXEC_DEFAULT", target="outline") == {"FINISHED"}

    for obj in (first, second):
        s = obj.bmanga_line_settings
        assert s.edge_width_curve_25 == 1.0
        assert s.edge_width_curve_50 == 1.0
        assert s.edge_width_curve_75 == 1.0
        assert _weight_at_t(obj, 3, 0.25) < 1e-6

    settings.edge_width_curve_25 = 0.0
    settings.edge_width_curve_50 = 0.0
    settings.edge_width_curve_75 = 0.0
    assert bpy.ops.bmanga_line.update_target("EXEC_DEFAULT", target="outline") == {"FINISHED"}

    for obj in (first, second):
        s = obj.bmanga_line_settings
        assert s.edge_width_curve_25 == 0.0
        assert s.edge_width_curve_50 == 0.0
        assert s.edge_width_curve_75 == 0.0
        assert _weight_at_t(obj, 3, 0.25) > 0.99
        assert _weight_at_t(obj, 3, 0.5) < 1e-6


def main() -> None:
    b_manga_line.register()
    _clear_scene()

    centered = _make_segmented_box("BML_midpoint_jitter_center")
    _apply_weights(centered, 0.0)
    centered_zero = _zero_t_values(centered, 3)
    assert centered_zero == [0.5], centered_zero

    jittered = _make_segmented_box("BML_midpoint_jitter_shifted")
    _apply_weights(jittered, 30.0)
    edge_zero_sets = [_zero_t_values(jittered, offset) for offset in range(4)]
    flat = [value for values in edge_zero_sets for value in values]
    assert flat, "乱れありでゼロ幅の中間頂点が見つかりません"
    assert all(len(values) == 1 for values in edge_zero_sets), edge_zero_sets
    assert all(0.2 <= value <= 0.8 for value in flat), edge_zero_sets
    assert any(abs(value - 0.5) > 1e-6 for value in flat), edge_zero_sets
    assert len({round(value, 4) for value in flat}) == len(flat), edge_zero_sets

    early = _make_segmented_box("BML_midpoint_curve_early")
    _apply_weights(early, 0.0, (1.0, 1.0, 1.0))
    assert _weight_at_t(early, 3, 0.25) < 1e-6

    late = _make_segmented_box("BML_midpoint_curve_late")
    _apply_weights(late, 0.0, (0.0, 0.0, 0.0))
    assert _weight_at_t(late, 3, 0.25) > 0.99
    assert _weight_at_t(late, 3, 0.5) < 1e-6

    _assert_multiselect_curve_update()

    print(
        "[PASS] midpoint jitter and width curve: "
        f"{edge_zero_sets}",
        flush=True,
    )


if __name__ == "__main__":
    main()
