"""B-MANGA Line: cached intersection lines update without redetecting geometry."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    camera_comp,
    core,
    inner_line_cache,
    inner_lines,
    intersection_cache,
    intersection_lines,
    presets,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for datablocks in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.node_groups,
        bpy.data.cameras,
        bpy.data.collections,
    ):
        for datablock in list(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)


def _make_camera() -> bpy.types.Object:
    bpy.ops.object.camera_add(location=(0.0, -6.0, 3.0), rotation=(1.1, 0.0, 0.0))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 4.0
    bpy.context.scene.camera = camera
    return camera


def _make_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    settings = obj.bmanga_line_settings
    settings.outline_enabled = True
    settings.inner_line_enabled = False
    settings.selection_line_enabled = False
    settings.intersection_enabled = True
    settings.outline_thickness_mm = 0.7
    settings.intersection_thickness_mm = 0.7
    settings.use_outline_creation_limit = False
    settings.use_intersection_creation_limit = False
    settings.use_outline_distance_limit = False
    settings.use_intersection_distance_limit = False
    settings.use_camera_culling = False
    settings.use_camera_compensation = True
    return obj


def _apply(objects: list[bpy.types.Object]) -> None:
    for obj in objects:
        assert presets.apply_line_settings(obj, bpy.context, refresh_scene=False), obj.name
    presets._refresh_after_line_settings(bpy.context)
    bpy.context.view_layer.update()


def _evaluated_polygon_count(obj: bpy.types.Object) -> int:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return len(mesh.polygons)
    finally:
        evaluated.to_mesh_clear()


def _socket_value(mod: bpy.types.Modifier, socket_name: str):
    sid = inner_line_cache._find_socket_id(mod.node_group, socket_name)
    assert sid is not None, f"socket not found: {socket_name}"
    return mod[sid]


def main() -> None:
    b_manga_line.register()
    real_builder = intersection_cache.build_cached_segments
    try:
        _clear_scene()
        _make_camera()
        source = _make_cube("BML_cache_A_source", (0.0, 0.0, 0.0))
        target = _make_cube("BML_cache_B_target", (0.35, 0.0, 0.0))
        _apply([source, target])

        mod = source.modifiers.get(core.INTERSECTION_MODIFIER_NAME)
        assert mod is not None, "保存済み交差線の表示モディファイアがありません"
        assert mod.node_group is not None
        assert mod.node_group.name.startswith(intersection_cache.CACHE_TREE_NAME)
        assert int(_socket_value(mod, "線の分割数")) == 1, (
            "中間頂点調整が無効な交差線に余分な分割が入っています"
        )
        source.bmanga_line_settings.intersection_edge_smooth_factor = -1.0
        source.bmanga_line_settings.intersection_edge_midpoint_jitter_percent = 50.0
        assert intersection_lines.update_parameters(source)
        assert int(_socket_value(mod, "線の分割数")) == 3, (
            "中間頂点調整が有効な交差線の表示分割が過剰です"
        )
        source.bmanga_line_settings.intersection_edge_smooth_factor = 0.0
        source.bmanga_line_settings.intersection_edge_midpoint_jitter_percent = 0.0
        assert intersection_lines.update_parameters(source)
        assert int(_socket_value(mod, "線の分割数")) == 1

        cache_name = str(source.get(intersection_cache.CACHE_OBJECT_PROP, "") or "")
        cache = bpy.data.objects.get(cache_name)
        assert cache is not None, "保存済み交差線オブジェクトが作られていません"
        assert cache.hide_viewport and cache.hide_render
        assert len(cache.data.edges) > 0, "保存済み交差線に中心線がありません"

        targets = {item.name for item in intersection_lines.modifier_targets(mod)}
        assert target.name in targets, "保存済み交差線の対象が記録されていません"
        assert _evaluated_polygon_count(source) > len(source.data.polygons), (
            "保存済み交差線が元オブジェクトの表示結果に合成されていません"
        )

        build_calls = 0

        def count_builder(*args, **kwargs):
            nonlocal build_calls
            build_calls += 1
            return real_builder(*args, **kwargs)

        intersection_cache.build_cached_segments = count_builder
        assert intersection_lines.refresh_scene_intersections(bpy.context.scene)
        assert build_calls == 0, (
            "変更なしのシーン再反映で交差検出が再実行されています"
        )

        target.location.x += 0.04
        bpy.context.view_layer.update()
        build_calls = 0
        assert intersection_lines.refresh_scene_intersections(bpy.context.scene)
        assert build_calls > 0, "交差対象の移動後に交差線が再検出されていません"

        target.data.vertices[0].co.x += 0.04
        target.data.update()
        build_calls = 0
        assert intersection_lines.refresh_scene_intersections(bpy.context.scene)
        assert build_calls > 0, "交差対象のメッシュ編集後に交差線が再検出されていません"

        source.location.y += 0.04
        bpy.context.view_layer.update()
        build_calls = 0
        assert intersection_lines.refresh_scene_intersections(bpy.context.scene)
        assert build_calls > 0, "交差線を持つ側の移動後に交差線が再検出されていません"

        extra_target = _make_cube("BML_cache_C_extra_target", (0.15, 0.15, 0.0))
        extra_mod = extra_target.modifiers.new(core.MODIFIER_NAME, "SOLIDIFY")
        extra_mod.thickness = 0.001
        bpy.context.view_layer.update()
        build_calls = 0
        assert intersection_lines.refresh_scene_intersections(bpy.context.scene)
        assert build_calls > 0, "交差対象の追加後に交差線が再検出されていません"
        assert extra_target.name in intersection_cache.target_names(source)

        def fail_builder(*_args, **_kwargs):
            raise AssertionError("設定更新で交差検出が再実行されています")

        intersection_cache.build_cached_segments = fail_builder
        assert intersection_lines.update_parameters(source, thickness=0.003)
        offset_sid = intersection_cache._find_socket_id(
            mod.node_group,
            intersection_cache._OFFSET_SOCKET,
        )
        assert offset_sid is not None, "保存済み交差線にオフセット入力がありません"
        assert intersection_lines.update_parameters(source, offset=0.35)
        assert abs(float(mod[offset_sid]) - 0.35) < 1.0e-7
        assert camera_comp.refresh_objects(
            bpy.context,
            [source],
            width_targets=("intersection",),
        )

        before_cache = source.get(intersection_cache.CACHE_OBJECT_PROP)
        source.bmanga_line_settings.intersection_thickness_mm = 1.4
        assert camera_comp.refresh_objects(
            bpy.context,
            [source],
            width_targets=("intersection",),
        )
        assert source.get(intersection_cache.CACHE_OBJECT_PROP) == before_cache

        color_material = bpy.data.materials.new("BML_cache_recolor_material")
        assert intersection_lines.update_parameters(source, material=color_material)
        assert any(slot.material == color_material for slot in source.material_slots)

        source.bmanga_line_settings.use_intersection_distance_limit = True
        source.bmanga_line_settings.intersection_max_distance = 0.1
        assert camera_comp.refresh_objects(
            bpy.context,
            [source],
            update_visibility=True,
            width_targets=("intersection",),
            visibility_targets=("intersection",),
        )
        assert not mod.show_viewport and not mod.show_render

        source.bmanga_line_settings.intersection_max_distance = 100.0
        assert camera_comp.refresh_objects(
            bpy.context,
            [source],
            update_visibility=True,
            width_targets=("intersection",),
            visibility_targets=("intersection",),
        )
        assert mod.show_viewport and mod.show_render

        source.bmanga_line_settings.inner_line_enabled = True
        source.bmanga_line_settings.inner_line_thickness_mm = 0.7
        source.bmanga_line_settings.use_inner_line_creation_limit = False
        source.bmanga_line_settings.use_inner_line_distance_limit = False
        source.bmanga_line_settings.inner_edge_smooth_factor = 0.0
        source.bmanga_line_settings.inner_edge_midpoint_jitter_percent = 0.0
        assert presets.apply_line_settings(
            source,
            bpy.context,
            refresh_scene=False,
            line_targets=("inner",),
        )
        bpy.context.view_layer.update()
        inner_mod = source.modifiers.get(core.GN_MODIFIER_NAME)
        assert inner_mod is not None, "稜谷線の表示モディファイアがありません"
        assert int(_socket_value(inner_mod, "線の分割数")) == 1, (
            "中間頂点調整が無効な稜谷線に余分な分割が入っています"
        )
        source.bmanga_line_settings.inner_edge_smooth_factor = -1.0
        source.bmanga_line_settings.inner_edge_midpoint_jitter_percent = 50.0
        assert inner_lines.update_parameters(
            source,
            midpoint_factor=-1.0,
            midpoint_jitter_percent=50.0,
        )
        assert int(_socket_value(inner_mod, "線の分割数")) == 3, (
            "中間頂点調整が有効な稜谷線の表示分割が過剰です"
        )
        inner_cache_name = str(source.get(inner_line_cache.CACHE_OBJECT_PROP, "") or "")
        inner_cache = bpy.data.objects.get(inner_cache_name)
        assert inner_cache is not None, "保存済み稜谷線オブジェクトが作られていません"
        assert 0 < len(inner_cache.data.edges) <= 24, (
            f"稜谷線の保存済み中心線が過剰に増えています: {len(inner_cache.data.edges)}"
        )
        assert mod.show_viewport and mod.show_render, (
            "稜谷線更新後に交差線の表示がオフになっています"
        )
        assert _evaluated_polygon_count(source) > len(source.data.polygons), (
            "稜谷線更新後に交差線が表示結果に残っていません"
        )

        intersection_cache.build_cached_segments = real_builder
        assert intersection_lines.remove_intersection_lines(source)
        assert source.modifiers.get(core.INTERSECTION_MODIFIER_NAME) is None
        assert bpy.data.objects.get(cache_name) is None

        print("[PASS] cached intersection lines update display without redetecting")
    finally:
        intersection_cache.build_cached_segments = real_builder
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
