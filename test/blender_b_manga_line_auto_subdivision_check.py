"""B-MANGA Line: auto midpoint Subdivision Surface setup."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    core,
    outline_setup,
    outline_width_attribute,
    subdivision_lod,
    vertex_analysis,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _auto_subsurf(obj: bpy.types.Object) -> bpy.types.Modifier | None:
    for mod in obj.modifiers:
        if subdivision_lod.is_auto_subsurf_modifier(mod):
            return mod
    return None


def _assert_render_level_ladder() -> None:
    cases = (
        (0.0, 4),
        (4.99, 4),
        (5.0, 3),
        (9.99, 3),
        (10.0, 2),
        (14.99, 2),
        (15.0, 1),
        (19.99, 1),
        (20.0, 0),
        (25.0, 0),
    )
    for distance, expected in cases:
        actual = subdivision_lod.render_levels_for_distance(distance)
        if actual != expected:
            raise AssertionError((distance, actual, expected))


def _assert_auto_modifier_before_lines(obj: bpy.types.Object) -> None:
    auto_mod = _auto_subsurf(obj)
    assert auto_mod is not None, "自動サブディビジョンサーフェスがありません"
    names = [mod.name for mod in obj.modifiers]
    auto_index = names.index(auto_mod.name)
    outline_index = names.index(core.MODIFIER_NAME)
    assert auto_index < outline_index, names


def _assert_cube_edges_are_creased(obj: bpy.types.Object) -> None:
    attr = obj.data.attributes.get(subdivision_lod.CREASE_EDGE_ATTR)
    assert attr is not None, "クリース属性がありません"
    creased = [
        index for index, item in enumerate(attr.data)
        if abs(float(item.value) - 1.0) < 1.0e-6
    ]
    assert len(creased) == 12, creased


def _radial_distribution(obj: bpy.types.Object) -> list[tuple[float, int]]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        bins: dict[float, int] = {}
        for vertex in mesh.vertices:
            value = round(max(abs(vertex.co.x), abs(vertex.co.y), abs(vertex.co.z)), 3)
            bins[value] = bins.get(value, 0) + 1
        return sorted(bins.items())
    finally:
        evaluated.to_mesh_clear()


def _assert_evaluated_outline_width_reaches_subsurf_points() -> None:
    bpy.ops.mesh.primitive_cube_add(size=1.8, location=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.data.materials.append(bpy.data.materials.new("BML_evaluated_width_surface"))
    settings = obj.bmanga_line_settings
    settings.auto_subdivision_for_midpoint = True
    settings.edge_smooth_factor = -1.0
    settings.edge_midpoint_jitter_percent = 0.0
    settings.outline_thickness = 0.20

    auto_mod = subdivision_lod.ensure_auto_subdivision(obj, bpy.context.scene)
    assert auto_mod is not None
    auto_mod.levels = 2
    auto_mod.render_levels = 2
    outline_setup.apply_outline(
        obj,
        thickness=0.20,
        color=(1.0, 0.0, 0.8, 1.0),
        use_vertex_group=True,
        scene=bpy.context.scene,
    )
    vertex_analysis.compute_and_apply_weights(obj, settings, "outline")
    outline_width_attribute.ensure_outline_width_attribute(obj, settings)
    tree = outline_width_attribute.get_or_create_tree()
    assert tree.name.startswith("BML_OutlineWidthAttributeV2")
    assert any(
        getattr(node, "label", "") == "BML_OutlineEvaluatedWidthSplit"
        for node in tree.nodes
    ), "アウトライン評価後線幅が検出角度で区間分割されていません"
    assert not any(
        node.bl_idname == "ShaderNodeMath" and getattr(node, "operation", "") == "MODULO"
        for node in tree.nodes
    ), "固定分割順だけで線幅を決める旧アウトライン処理が残っています"
    names = [mod.name for mod in obj.modifiers]
    assert core.OUTLINE_WIDTH_ATTR_MODIFIER_NAME in names, names
    assert names.index(core.OUTLINE_WIDTH_ATTR_MODIFIER_NAME) < names.index(core.MODIFIER_NAME)

    with_evaluated_width = _radial_distribution(obj)
    outline_width_attribute.remove_outline_width_attribute(obj)
    without_evaluated_width = _radial_distribution(obj)

    assert with_evaluated_width != without_evaluated_width
    assert any(0.91 < radius < 1.09 for radius, _count in with_evaluated_width), (
        with_evaluated_width,
        without_evaluated_width,
    )


def _assert_existing_simple_subsurf_is_repaired() -> None:
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    obj = bpy.context.object
    mod = obj.modifiers.new(subdivision_lod.AUTO_SUBSURF_MODIFIER_NAME, "SUBSURF")
    if hasattr(mod, "subdivision_type"):
        mod.subdivision_type = "SIMPLE"
    changed = subdivision_lod.repair_auto_subdivision_modifiers(bpy.context.scene)
    assert changed >= 1
    assert mod.subdivision_type == subdivision_lod.AUTO_SUBSURF_SUBDIVISION_TYPE
    _assert_cube_edges_are_creased(obj)


def _make_flat_two_quad_strip() -> bpy.types.Object:
    mesh = bpy.data.meshes.new("BML_flat_two_quad_strip")
    mesh.from_pydata(
        [
            (-1.0, -0.5, 0.0),
            (-1.0, 0.0, 0.0),
            (-1.0, 0.5, 0.0),
            (1.0, -0.5, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 0.5, 0.0),
        ],
        [],
        [
            (0, 3, 4, 1),
            (1, 4, 5, 2),
        ],
    )
    mesh.update()
    obj = bpy.data.objects.new("BML_flat_two_quad_strip", mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def _make_open_folded_strip() -> bpy.types.Object:
    mesh = bpy.data.meshes.new("BML_open_folded_strip")
    mesh.from_pydata(
        [
            (-1.0, -0.5, 0.0),
            (-1.0, 0.0, 0.4),
            (-1.0, 0.5, 0.0),
            (1.0, -0.5, 0.0),
            (1.0, 0.0, 0.4),
            (1.0, 0.5, 0.0),
        ],
        [],
        [
            (0, 3, 4, 1),
            (1, 4, 5, 2),
        ],
    )
    mesh.update()
    obj = bpy.data.objects.new("BML_open_folded_strip", mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def _assert_open_edges_are_creased() -> None:
    obj = _make_open_folded_strip()
    count = subdivision_lod.mark_sharp_edges_for_subsurf(obj)
    assert count == 7, count
    attr = obj.data.attributes.get(subdivision_lod.CREASE_EDGE_ATTR)
    assert attr is not None, "クリース属性がありません"
    creased = [
        index for index, item in enumerate(attr.data)
        if abs(float(item.value) - 1.0) < 1.0e-6
    ]
    assert len(creased) == 7, creased


def _assert_smooth_edges_are_uncreased() -> None:
    obj = _make_flat_two_quad_strip()
    attr = subdivision_lod._ensure_crease_attribute(obj.data)
    for item in attr.data:
        item.value = 1.0

    count = subdivision_lod.mark_sharp_edges_for_subsurf(obj)
    assert count == 6, count
    shared_edge = next(
        edge for edge in obj.data.edges
        if set(edge.vertices) == {1, 4}
    )
    assert abs(float(attr.data[shared_edge.index].value)) < 1.0e-6


def _assert_auto_subdivision_skips_boundary_mesh() -> None:
    obj = _make_open_folded_strip()
    assert not subdivision_lod.auto_subdivision_supported(obj)
    assert subdivision_lod.ensure_auto_subdivision(obj, bpy.context.scene) is None
    assert _auto_subsurf(obj) is None

    stale = obj.modifiers.new(subdivision_lod.AUTO_SUBSURF_MODIFIER_NAME, "SUBSURF")
    stale.levels = 2
    changed = subdivision_lod.repair_auto_subdivision_modifiers(bpy.context.scene)
    assert changed >= 1
    assert _auto_subsurf(obj) is None
    assert obj.modifiers.get(core.OUTLINE_WIDTH_ATTR_MODIFIER_NAME) is None


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _assert_render_level_ladder()
        _clear_scene()
        _assert_open_edges_are_creased()
        _clear_scene()
        _assert_smooth_edges_are_uncreased()
        _clear_scene()
        _assert_auto_subdivision_skips_boundary_mesh()
        _clear_scene()
        bpy.ops.object.camera_add(location=(0.0, -3.0, 0.0))
        bpy.context.scene.camera = bpy.context.object
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
        obj = bpy.context.object
        obj.data.materials.append(bpy.data.materials.new("BML_auto_subsurf_surface"))
        obj.bmanga_line_settings.auto_subdivision_for_midpoint = True
        obj.bmanga_line_settings.edge_smooth_factor = -0.5

        assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
        auto_mod = _auto_subsurf(obj)
        assert auto_mod is not None
        assert auto_mod.subdivision_type == subdivision_lod.AUTO_SUBSURF_SUBDIVISION_TYPE
        assert auto_mod.levels == 0
        assert auto_mod.render_levels == 4
        _assert_auto_modifier_before_lines(obj)
        _assert_cube_edges_are_creased(obj)

        obj.location.y = 9.0
        bpy.context.view_layer.update()
        subdivision_lod.ensure_auto_subdivision(obj, bpy.context.scene)
        assert _auto_subsurf(obj).render_levels == 2

        obj.bmanga_line_settings.auto_subdivision_for_midpoint = False
        assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
        assert _auto_subsurf(obj) is None

        obj.bmanga_line_settings.auto_subdivision_for_midpoint = True
        assert bpy.ops.bmanga_line.apply("EXEC_DEFAULT") == {"FINISHED"}
        assert _auto_subsurf(obj) is not None
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        assert bpy.ops.bmanga_line.remove("EXEC_DEFAULT") == {"FINISHED"}
        assert _auto_subsurf(obj) is None
        assert obj.modifiers.get(core.MODIFIER_NAME) is None
        assert obj.modifiers.get(core.OUTLINE_WIDTH_ATTR_MODIFIER_NAME) is None

        _clear_scene()
        _assert_evaluated_outline_width_reaches_subsurf_points()
        _clear_scene()
        _assert_existing_simple_subsurf_is_repaired()

        print("BMANGA_LINE_AUTO_SUBDIVISION_OK")
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
