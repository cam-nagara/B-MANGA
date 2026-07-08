"""B-MANGA Line: Subdivision Surface levels drive inner/intersection generation."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from collections import Counter

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    batch_update,
    core,
    inner_lines,
    intersection_cache,
    intersection_lines,
    intersection_shell,
    outline_setup,
    subdivision_lod,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for datablocks in (bpy.data.meshes, bpy.data.materials, bpy.data.node_groups):
        for item in list(datablocks):
            if item.users == 0:
                datablocks.remove(item)


def _surface_material(name: str):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    return mat


def _make_cube(name: str, location: tuple[float, float, float]) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(_surface_material(name + "_surface"))
    obj.bmanga_line_settings.auto_subdivision_for_midpoint = True
    return obj


def _auto_mod(obj: bpy.types.Object) -> bpy.types.Modifier:
    mod = subdivision_lod.ensure_auto_subdivision(obj, bpy.context.scene)
    assert mod is not None
    if hasattr(mod, "subdivision_type"):
        assert mod.subdivision_type == subdivision_lod.AUTO_SUBSURF_SUBDIVISION_TYPE
    return mod


def _inner_resample_count(obj: bpy.types.Object) -> int:
    mod = obj.modifiers.get(core.GN_MODIFIER_NAME)
    assert mod is not None and mod.node_group is not None
    sid = inner_lines._find_socket_id(mod.node_group, "線の分割数")
    assert sid is not None
    return int(mod[sid])


def _evaluated_material_counts(obj: bpy.types.Object) -> Counter:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        materials = [mat.name if mat else "" for mat in mesh.materials]
        return Counter(
            materials[poly.material_index] if poly.material_index < len(materials) else ""
            for poly in mesh.polygons
        )
    finally:
        evaluated.to_mesh_clear()


def _assert_inner_levels_sync() -> None:
    obj = _make_cube("内部線_サブディビジョン同期", (0.0, 0.0, 0.0))
    mod = _auto_mod(obj)
    material = outline_setup.get_line_material(obj, "inner")
    assert inner_lines.apply_inner_lines(
        obj,
        angle=math.radians(10.0),
        thickness=0.03,
        material=material,
    )

    mod.levels = 0
    mod.render_levels = 4
    subdivision_lod.sync_generated_line_subdivision(obj)
    assert _inner_resample_count(obj) == 1

    mod.levels = 2
    subdivision_lod.sync_generated_line_subdivision(obj)
    assert _inner_resample_count(obj) == 1

    subdivision_lod.sync_generated_line_subdivision(obj, for_render=True)
    assert _inner_resample_count(obj) == 1

    subdivision_lod.sync_generated_line_subdivision(obj, for_render=False)
    assert _inner_resample_count(obj) == 1

    obj.bmanga_line_settings.inner_edge_smooth_factor = -1.0
    obj.bmanga_line_settings.inner_edge_midpoint_jitter_percent = 50.0
    assert inner_lines.update_parameters(
        obj,
        midpoint_factor=-1.0,
        midpoint_jitter_percent=50.0,
    )
    assert _inner_resample_count(obj) == 3

    mod.levels = 4
    mod.render_levels = 4
    subdivision_lod.sync_generated_line_subdivision(obj, for_render=True)
    assert _inner_resample_count(obj) == 3


def _assert_inner_lines_do_not_follow_subdivision_grid() -> None:
    obj = _make_cube("内部線_レンダー細分グリッド除外", (0.0, 0.0, 0.0))
    mod = _auto_mod(obj)
    material = outline_setup.get_line_material(obj, "inner")
    assert inner_lines.apply_inner_lines(
        obj,
        angle=math.radians(45.0),
        thickness=0.03,
        material=material,
    )

    mod.levels = 2
    mod.render_levels = 2
    counts = _evaluated_material_counts(obj)
    line_faces = sum(
        count for name, count in counts.items()
        if name.startswith("BML_Outline_Inner")
    )
    surface_faces = counts.get(obj.data.materials[0].name, 0)
    max_expected_faces = 48 * inner_lines.INNER_TUBE_PROFILE_RESOLUTION * 3
    assert 0 < line_faces <= max_expected_faces, counts
    assert surface_faces == 96, counts


def _intersection_cache_for_source(source: bpy.types.Object) -> bpy.types.Object:
    name = str(source.get(intersection_cache.CACHE_OBJECT_PROP, "") or "")
    cache = bpy.data.objects.get(name)
    assert cache is not None and cache.data is not None, "保存済み交差線キャッシュが見つかりません"
    assert len(cache.data.edges) > 0, "保存済み交差線キャッシュが空です"
    return cache


def _assert_intersection_proxy_levels_sync() -> None:
    source = _make_cube("A_intersection_sync_source", (-0.25, 0.0, 0.0))
    target = _make_cube("B_intersection_sync_target", (0.25, 0.0, 0.0))
    source_mod = _auto_mod(source)
    target_mod = _auto_mod(target)
    source_mod.levels = 1
    target_mod.levels = 2
    target_mod.render_levels = 3

    outline_setup.apply_outline(source, thickness=0.03, color=(0.0, 0.0, 0.0, 1.0))
    outline_setup.apply_outline(target, thickness=0.03, color=(0.0, 0.0, 0.0, 1.0))
    assert intersection_lines.apply_intersection_lines(
        source,
        target=target,
        thickness=0.03,
        material=outline_setup.get_line_material(source, "intersection"),
        scene=bpy.context.scene,
    )
    cache = _intersection_cache_for_source(source)
    assert cache.hide_viewport and cache.hide_render
    assert not any(
        str(obj.get(intersection_shell._PROXY_SOURCE_PROP, "") or "") == target.name_full
        for obj in bpy.data.objects
    ), "保存済み交差線方式で旧プロキシが作成されています"

    target_mod.levels = 4
    target_mod.render_levels = 1
    changed = intersection_shell.sync_proxy_subdivision_for_target(target)
    assert changed == 0
    assert cache.name in bpy.data.objects


def _assert_manual_subsurf_proxy_levels_sync() -> None:
    source = _make_cube("A_manual_subsurf_source", (-0.25, 0.0, 0.0))
    target = _make_cube("B_manual_subsurf_target", (0.25, 0.0, 0.0))
    manual = target.modifiers.new("User_Subdivision_Surface", "SUBSURF")
    manual.levels = 2
    manual.render_levels = 4

    outline_setup.apply_outline(source, thickness=0.03, color=(0.0, 0.0, 0.0, 1.0))
    outline_setup.apply_outline(target, thickness=0.03, color=(0.0, 0.0, 0.0, 1.0))
    assert intersection_lines.apply_intersection_lines(
        source,
        target=target,
        thickness=0.03,
        material=outline_setup.get_line_material(source, "intersection"),
        scene=bpy.context.scene,
    )

    cache = _intersection_cache_for_source(source)
    assert cache.hide_viewport and cache.hide_render

    manual.levels = 1
    manual.render_levels = 3
    changed = intersection_shell.sync_proxy_subdivision_for_target(target)
    assert changed == 0
    assert cache.name in bpy.data.objects


def _assert_match_viewport_checkbox_restores_zero() -> None:
    obj = _make_cube("ビューポート段数チェックボックス", (0.0, 0.0, 0.0))
    mod = _auto_mod(obj)
    mod.levels = 0
    mod.render_levels = 2
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    settings = obj.bmanga_line_settings
    settings.match_subsurf_viewport_to_render = True
    batch_update._update_match_subsurf_viewport_to_render([obj])
    bpy.context.view_layer.update()
    assert int(mod.levels) == 2

    settings.match_subsurf_viewport_to_render = False
    batch_update._update_match_subsurf_viewport_to_render([obj])
    bpy.context.view_layer.update()
    assert int(mod.levels) == 0


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _clear_scene()
        _assert_inner_levels_sync()
        _clear_scene()
        _assert_inner_lines_do_not_follow_subdivision_grid()
        _clear_scene()
        _assert_intersection_proxy_levels_sync()
        _clear_scene()
        _assert_manual_subsurf_proxy_levels_sync()
        _clear_scene()
        _assert_match_viewport_checkbox_restores_zero()
        print("[PASS] subdivision levels sync to inner/intersection lines", flush=True)
    finally:
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
