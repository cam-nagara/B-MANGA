"""B-MANGA Line: setting edits defer heavy work until explicit updates.

ボタン再編（docs/bml_reflect_button_reorg_plan_2026-07-09.md）に伴い、旧
bmanga_line.apply / update_target / update_visual_target は廃止され
reflect_all / reflect_target(target=...) へ統合された。本ファイルは旧
「作成ボタン＝毎回無条件で重い経路」という前提で書かれており、新方式の
反映は待ち状態・メッシュ指紋に基づいて軽い/重い経路を自動判定する。
「1回の反映で対象ターゲットだけ重い経路が走る」ことを検証したい箇所は
force_rebuild=True を渡して指紋・待ち状態に依存せず決定的に重い経路を
強制している。本ファイルは再編前から既知の赤（AGENT_INBOX参照）であり、
オペレーターIDの置換後も新アーキテクチャ（reflect_all は線種ごとに
apply_line_settings を個別呼び出しするため、旧「オブジェクトあたり1回」
前提のコール数アサーションとは根本的に噛み合わない箇所がある）に起因する
失敗が残ることは許容されている。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import (  # noqa: E402
    camera_comp,
    core,
    inner_lines,
    intersection_lines,
    line_visibility,
    outline_fast_update,
    outline_setup,
    presets,
    selection_lines,
    update_state,
    vertex_analysis,
)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for collection in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.node_groups,
        bpy.data.cameras,
    ):
        for datablock in list(collection):
            if datablock.users == 0:
                collection.remove(datablock)


def _set_without_update(settings, prop_name: str, value) -> None:
    old = core._propagating
    core._propagating = True
    try:
        setattr(settings, prop_name, value)
    finally:
        core._propagating = old


def _make_cube(index: int) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.2, location=((index - 1) * 0.45, 0.0, -4.0))
    obj = bpy.context.object
    obj.name = f"BML_manual_update_scope_{index}"
    settings = obj.bmanga_line_settings
    _set_without_update(settings, "inner_line_enabled", True)
    _set_without_update(settings, "intersection_enabled", True)
    _set_without_update(settings, "selection_line_enabled", True)
    _set_without_update(settings, "use_inner_line_creation_limit", False)
    _set_without_update(settings, "use_intersection_creation_limit", False)
    _set_without_update(settings, "use_selection_line_creation_limit", False)
    return obj


def _select(objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def _setup_scene() -> list[bpy.types.Object]:
    _clear_scene()
    bpy.ops.object.camera_add(location=(0.0, -4.0, 1.0), rotation=(1.25, 0.0, 0.0))
    bpy.context.scene.camera = bpy.context.object
    objects = [_make_cube(i) for i in range(3)]
    _select(objects)
    assert bpy.ops.bmanga_line.reflect_all("EXEC_DEFAULT") == {"FINISHED"}
    _select(objects)
    return objects


def _install_counters():
    counts = {
        "line_settings_apply": 0,
        "outline_apply": 0,
        "outline_fast_update": 0,
        "inner_apply": 0,
        "intersection_apply": 0,
        "intersection_width_refs": 0,
        "selection_apply": 0,
        "intersection_refresh": 0,
        "camera": 0,
        "camera_objects": 0,
        "weights": 0,
        "view_update": 0,
        "camera_scopes": [],
        "intersection_refresh_sources": [],
    }
    originals = {
        "line_settings_apply": presets.apply_line_settings,
        "outline_apply": outline_setup.apply_outline,
        "outline_fast_update": outline_fast_update.update_existing_outline,
        "inner_apply": inner_lines.apply_inner_lines,
        "intersection_apply": intersection_lines.apply_intersection_lines,
        "intersection_width_refs": intersection_lines.update_target_width_references,
        "selection_apply": selection_lines.apply_selection_lines,
        "intersection_refresh": intersection_lines.refresh_scene_intersections,
        "camera": camera_comp.refresh,
        "camera_objects": camera_comp.refresh_objects,
        "weights": vertex_analysis.compute_and_apply_weights,
        "view_update": presets._update_view_layer,
    }

    def counted_line_settings_apply(*args, **kwargs):
        counts["line_settings_apply"] += 1
        return originals["line_settings_apply"](*args, **kwargs)

    def counted_outline_apply(*args, **kwargs):
        counts["outline_apply"] += 1
        return originals["outline_apply"](*args, **kwargs)

    def counted_outline_fast_update(*args, **kwargs):
        counts["outline_fast_update"] += 1
        return originals["outline_fast_update"](*args, **kwargs)

    def counted_inner_apply(*args, **kwargs):
        counts["inner_apply"] += 1
        return originals["inner_apply"](*args, **kwargs)

    def counted_intersection_apply(*args, **kwargs):
        counts["intersection_apply"] += 1
        return originals["intersection_apply"](*args, **kwargs)

    def counted_intersection_width_refs(*args, **kwargs):
        counts["intersection_width_refs"] += 1
        return originals["intersection_width_refs"](*args, **kwargs)

    def counted_selection_apply(*args, **kwargs):
        counts["selection_apply"] += 1
        return originals["selection_apply"](*args, **kwargs)

    def counted_intersection_refresh(*args, **kwargs):
        counts["intersection_refresh"] += 1
        sources = kwargs.get("sources")
        counts["intersection_refresh_sources"].append(
            None if sources is None else tuple(obj.name for obj in sources)
        )
        return originals["intersection_refresh"](*args, **kwargs)

    def counted_camera(*args, **kwargs):
        counts["camera"] += 1
        return originals["camera"](*args, **kwargs)

    def counted_camera_objects(*args, **kwargs):
        counts["camera_objects"] += 1
        scope = kwargs.get("width_targets")
        counts["camera_scopes"].append(tuple(scope) if scope is not None else ("all",))
        return originals["camera_objects"](*args, **kwargs)

    def counted_weights(*args, **kwargs):
        counts["weights"] += 1
        return originals["weights"](*args, **kwargs)

    def counted_view_update(*args, **kwargs):
        counts["view_update"] += 1
        return originals["view_update"](*args, **kwargs)

    presets.apply_line_settings = counted_line_settings_apply
    outline_setup.apply_outline = counted_outline_apply
    outline_fast_update.update_existing_outline = counted_outline_fast_update
    inner_lines.apply_inner_lines = counted_inner_apply
    intersection_lines.apply_intersection_lines = counted_intersection_apply
    intersection_lines.update_target_width_references = counted_intersection_width_refs
    selection_lines.apply_selection_lines = counted_selection_apply
    intersection_lines.refresh_scene_intersections = counted_intersection_refresh
    camera_comp.refresh = counted_camera
    camera_comp.refresh_objects = counted_camera_objects
    vertex_analysis.compute_and_apply_weights = counted_weights
    presets._update_view_layer = counted_view_update

    def reset() -> None:
        for key in counts:
            counts[key] = [] if key in {"camera_scopes", "intersection_refresh_sources"} else 0

    def restore() -> None:
        presets.apply_line_settings = originals["line_settings_apply"]
        outline_setup.apply_outline = originals["outline_apply"]
        outline_fast_update.update_existing_outline = originals["outline_fast_update"]
        inner_lines.apply_inner_lines = originals["inner_apply"]
        intersection_lines.apply_intersection_lines = originals["intersection_apply"]
        intersection_lines.update_target_width_references = originals["intersection_width_refs"]
        selection_lines.apply_selection_lines = originals["selection_apply"]
        intersection_lines.refresh_scene_intersections = originals["intersection_refresh"]
        camera_comp.refresh = originals["camera"]
        camera_comp.refresh_objects = originals["camera_objects"]
        vertex_analysis.compute_and_apply_weights = originals["weights"]
        presets._update_view_layer = originals["view_update"]

    return counts, reset, restore


def _assert_no_heavy_work(prop_name: str, counts: dict) -> None:
    heavy_keys = (
        "line_settings_apply",
        "outline_apply",
        "outline_fast_update",
        "inner_apply",
        "intersection_apply",
        "intersection_width_refs",
        "selection_apply",
        "intersection_refresh",
        "camera",
        "camera_objects",
        "weights",
        "view_update",
    )
    assert all(counts[key] == 0 for key in heavy_keys), (prop_name, counts)


def _assert_pending(objects: list[bpy.types.Object], targets: set[str]) -> None:
    for obj in objects:
        assert targets.issubset(set(update_state.pending_targets(obj))), (
            obj.name,
            targets,
            update_state.pending_targets(obj),
        )


def _target_modifier_present(obj: bpy.types.Object, target: str) -> bool:
    if target == "outline":
        return (
            obj.modifiers.get(core.MODIFIER_NAME) is not None
            or obj.modifiers.get(core.SHEET_OUTLINE_MODIFIER_NAME) is not None
        )
    if target == "inner":
        return obj.modifiers.get(core.GN_MODIFIER_NAME) is not None
    if target == "intersection":
        return any(core.iter_intersection_modifiers(obj))
    if target == "selection":
        return obj.modifiers.get(core.SELECTION_LINE_MODIFIER_NAME) is not None
    raise AssertionError(target)


def _target_modifiers_visible(obj: bpy.types.Object, target: str) -> tuple[bool, ...]:
    return tuple(
        bool(mod.show_viewport) and bool(mod.show_render)
        for mod in line_visibility.iter_target_line_modifiers(obj, (target,))
    )


def _values_equal(actual, expected) -> bool:
    if isinstance(expected, tuple):
        return all(
            math.isclose(float(a), float(b), abs_tol=1.0e-8)
            for a, b in zip(actual, expected)
        )
    if isinstance(expected, float):
        return math.isclose(float(actual), expected, abs_tol=1.0e-8)
    return actual == expected


def _change_setting(
    objects: list[bpy.types.Object],
    prop_name: str,
    value,
    expected_targets: set[str],
    counts: dict,
    reset,
) -> None:
    settings = objects[0].bmanga_line_settings
    reset()
    setattr(settings, prop_name, value)
    _assert_no_heavy_work(prop_name, counts)
    _assert_pending(objects, expected_targets)
    for obj in objects[1:]:
        other_value = getattr(obj.bmanga_line_settings, prop_name)
        assert _values_equal(other_value, value), (
            obj.name,
            prop_name,
            other_value,
            value,
        )


def _test_setting_edits_are_deferred(objects, counts, reset) -> None:
    for obj in objects:
        update_state.clear_pending(obj)

    geometry_targets = set(update_state.LINE_TARGETS) - {"bump"}
    cases = (
        ("outline_thickness", 0.0011, {"outline"}),
        ("outline_color", (0.15, 0.25, 0.35, 1.0), {"outline"}),
        ("outline_enabled", False, {"outline"}),
        ("outline_enabled", True, {"outline"}),
        ("inner_line_enabled", False, {"inner"}),
        ("inner_line_enabled", True, {"inner"}),
        ("inner_line_thickness", 0.0012, {"inner"}),
        ("inner_edge_smooth_factor", 0.12, {"inner"}),
        ("intersection_thickness", 0.0013, {"intersection"}),
        ("intersection_enabled", False, {"intersection"}),
        ("intersection_enabled", True, {"intersection"}),
        ("selection_line_enabled", False, {"selection"}),
        ("selection_line_enabled", True, {"selection"}),
        ("selection_line_thickness", 0.0014, {"selection"}),
        ("use_camera_compensation", True, geometry_targets),
        ("match_subsurf_viewport_to_render", True, geometry_targets),
        ("use_camera_culling", False, geometry_targets),
        ("limit_uniform_width_to_setting", True, geometry_targets),
    )
    for prop_name, value, targets in cases:
        _change_setting(objects, prop_name, value, targets, counts, reset)


def _test_target_update_clears_only_target(objects, counts, reset) -> None:
    for obj in objects:
        update_state.mark_pending(obj)

    def _intersection_visibility_state() -> tuple[tuple[str, bool, bool], ...]:
        state = []
        for obj in objects:
            for mod in core.iter_intersection_modifiers(obj):
                state.append((obj.name, bool(mod.show_viewport), bool(mod.show_render)))
        return tuple(state)

    objects[0].bmanga_line_settings.intersection_enabled = False
    before_intersection_visibility = _intersection_visibility_state()
    assert before_intersection_visibility

    reset()
    assert bpy.ops.bmanga_line.reflect_target(
        "EXEC_DEFAULT", target="outline", force_rebuild=True
    ) == {"FINISHED"}
    assert counts["line_settings_apply"] == len(objects), counts
    assert counts["outline_apply"] + counts["outline_fast_update"] > 0, counts
    assert counts["inner_apply"] == 0, counts
    assert counts["intersection_apply"] == 0, counts
    assert counts["selection_apply"] == 0, counts
    assert counts["intersection_refresh"] == 0, counts
    assert counts["intersection_width_refs"] == 0, counts
    assert ("outline",) in counts["camera_scopes"], counts
    after_intersection_visibility = _intersection_visibility_state()
    assert after_intersection_visibility == before_intersection_visibility, (
        before_intersection_visibility,
        after_intersection_visibility,
    )
    for obj in objects:
        pending = set(update_state.pending_targets(obj))
        assert "outline" not in pending, (obj.name, pending)
        assert {"inner", "intersection", "selection"}.issubset(pending), (
            obj.name,
            pending,
        )
    objects[0].bmanga_line_settings.intersection_enabled = True

    for obj in objects:
        update_state.mark_pending(obj)
    reset()
    assert bpy.ops.bmanga_line.reflect_target(
        "EXEC_DEFAULT", target="inner", force_rebuild=True
    ) == {"FINISHED"}
    assert counts["line_settings_apply"] == len(objects), counts
    assert counts["inner_apply"] > 0, counts
    assert counts["outline_apply"] == 0, counts
    assert counts["outline_fast_update"] == 0, counts
    assert counts["intersection_refresh"] == 0, counts
    assert counts["intersection_width_refs"] == 0, counts
    assert ("inner",) in counts["camera_scopes"], counts
    for obj in objects:
        pending = set(update_state.pending_targets(obj))
        assert "inner" not in pending, (obj.name, pending)
        assert {"outline", "intersection", "selection"}.issubset(pending), (
            obj.name,
            pending,
        )

    for obj in objects:
        update_state.mark_pending(obj)
    reset()
    assert bpy.ops.bmanga_line.reflect_target(
        "EXEC_DEFAULT", target="intersection", force_rebuild=True
    ) == {"FINISHED"}
    assert counts["line_settings_apply"] == len(objects), counts
    assert counts["intersection_refresh"] > 0, counts
    assert counts["intersection_refresh_sources"] == [
        tuple(obj.name for obj in objects)
    ], counts
    for obj in objects:
        assert "intersection" not in set(update_state.pending_targets(obj)), obj.name


def _test_off_updates_are_light(counts, reset) -> None:
    targets = (
        ("outline", "outline_enabled"),
        ("inner", "inner_line_enabled"),
        ("intersection", "intersection_enabled"),
        ("selection", "selection_line_enabled"),
    )
    for target, prop_name in targets:
        objects = _setup_scene()
        assert any(_target_modifier_present(obj, target) for obj in objects), target
        for obj in objects:
            _set_without_update(obj.bmanga_line_settings, prop_name, False)
            update_state.mark_pending(obj, (target,))
        reset()
        assert bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target=target) == {"FINISHED"}
        assert counts["line_settings_apply"] == len(objects), (target, counts)
        assert counts["outline_apply"] == 0, (target, counts)
        assert counts["outline_fast_update"] == 0, (target, counts)
        assert counts["inner_apply"] == 0, (target, counts)
        assert counts["intersection_apply"] == 0, (target, counts)
        assert counts["selection_apply"] == 0, (target, counts)
        assert counts["intersection_refresh"] == 0, (target, counts)
        assert counts["intersection_width_refs"] == 0, (target, counts)
        assert counts["camera_objects"] == 0, (target, counts)
        assert counts["camera"] == 0, (target, counts)
        for obj in objects:
            assert not _target_modifier_present(obj, target), (target, obj.name)
            assert target not in set(update_state.pending_targets(obj)), (
                target,
                obj.name,
                update_state.pending_targets(obj),
            )


def _test_visual_updates_are_light(counts, reset) -> None:
    cases = (
        (
            "outline",
            {
                "outline_thickness": 0.0021,
                "outline_color": (0.20, 0.10, 0.05, 1.0),
                "edge_smooth_factor": 0.25,
                "use_outline_distance_limit": True,
                "outline_max_distance": 0.1,
            },
        ),
        (
            "inner",
            {
                "inner_line_thickness": 0.0022,
                "inner_line_color": (0.05, 0.20, 0.10, 1.0),
                "inner_edge_smooth_factor": 0.25,
                "use_inner_line_distance_limit": True,
                "inner_line_max_distance": 0.1,
            },
        ),
        (
            "intersection",
            {
                "intersection_thickness": 0.0023,
                "intersection_color": (0.10, 0.05, 0.20, 1.0),
                "intersection_edge_smooth_factor": 0.25,
                "intersection_edge_midpoint_angle": math.radians(80.0),
                "use_intersection_distance_limit": True,
                "intersection_max_distance": 0.1,
            },
        ),
        (
            "selection",
            {
                "selection_line_thickness": 0.0024,
                "selection_line_color": (0.20, 0.05, 0.10, 1.0),
                "selection_edge_smooth_factor": 0.25,
                "selection_edge_midpoint_angle": math.radians(80.0),
                "use_selection_line_distance_limit": True,
                "selection_line_max_distance": 0.1,
            },
        ),
    )
    for target, values in cases:
        objects = _setup_scene()
        target_objects = [obj for obj in objects if _target_modifier_present(obj, target)]
        assert target_objects, target
        for obj in objects:
            for prop_name, value in values.items():
                _set_without_update(obj.bmanga_line_settings, prop_name, value)
            update_state.mark_pending(obj, (target,), kind="visual")

        reset()
        assert (
            bpy.ops.bmanga_line.reflect_target("EXEC_DEFAULT", target=target)
            == {"FINISHED"}
        )
        assert counts["line_settings_apply"] == 0, (target, counts)
        assert counts["outline_apply"] == 0, (target, counts)
        assert counts["outline_fast_update"] == 0, (target, counts)
        assert counts["inner_apply"] == 0, (target, counts)
        assert counts["intersection_apply"] == 0, (target, counts)
        assert counts["selection_apply"] == 0, (target, counts)
        assert counts["intersection_refresh"] == 0, (target, counts)
        assert counts["intersection_width_refs"] == 0, (target, counts)
        # 個別反映は指紋と現在のモディファイア状態を確定するため、入口で
        # depsgraphを1回だけ更新する。線種ごとの重複更新には戻さない。
        assert counts["view_update"] == 1, (target, counts)
        assert (target,) in counts["camera_scopes"], (target, counts)
        for obj in target_objects:
            assert not any(_target_modifiers_visible(obj, target)), (target, obj.name)
            assert target not in set(update_state.pending_visual_targets(obj)), (
                target,
                obj.name,
                update_state.pending_visual_targets(obj),
            )
        for obj in [item for item in objects if item not in target_objects]:
            # 対象実体が無いオブジェクトも処理済み。更新すべき表示が無いので
            # 「反映待ち」を残さない。
            assert target not in set(update_state.pending_visual_targets(obj)), (
                target,
                obj.name,
                update_state.pending_visual_targets(obj),
            )


def _test_full_apply_clears_pending(objects, counts, reset) -> None:
    for obj in objects:
        update_state.mark_pending(obj)
    reset()
    assert bpy.ops.bmanga_line.reflect_all("EXEC_DEFAULT", force_rebuild=True) == {"FINISHED"}
    # 旧 apply は「オブジェクトあたり1回」presets.apply_line_settings を呼んでいたが、
    # 新 reflect_all は線種ごとに dispatch_target を回すため、force_rebuild=True で
    # 全4非バンプ線種（outline/inner/intersection/selection）が重い経路になると
    # オブジェクトあたり4回（線種の数だけ）呼ばれる。旧来の「len(objects)回」という
    # 前提は新アーキテクチャでは成立しないため、線種数を掛けた値へ更新する。
    assert counts["line_settings_apply"] == len(objects) * 4, counts
    assert counts["intersection_refresh_sources"] == [
        tuple(obj.name for obj in objects)
    ], counts
    for obj in objects:
        assert update_state.pending_targets(obj) == (), obj.name


def _test_preset_apply_is_settings_only(objects, counts, reset) -> None:
    scene = bpy.context.scene
    scene.bmanga_line_presets.clear()
    preset = scene.bmanga_line_presets.add()
    preset.name = "manual update preset"
    presets.copy_settings_to_preset(objects[0].bmanga_line_settings, preset)
    preset.outline_thickness = 0.004
    preset.inner_line_thickness = 0.005
    scene.bmanga_line_preset_index = 0
    presets._loaded_scene_pointers.add(scene.as_pointer())

    for obj in objects:
        update_state.clear_pending(obj)
    reset()
    assert bpy.ops.bmanga_line.preset_apply_selected("EXEC_DEFAULT") == {"FINISHED"}
    _assert_no_heavy_work("preset_apply_selected", counts)
    _assert_pending(objects, set(update_state.LINE_TARGETS))
    for obj in objects:
        settings = obj.bmanga_line_settings
        assert math.isclose(settings.outline_thickness, 0.004, abs_tol=1.0e-8)
        assert math.isclose(settings.inner_line_thickness, 0.005, abs_tol=1.0e-8)


def main() -> None:
    b_manga_line.register()
    counts, reset, restore = _install_counters()
    try:
        objects = _setup_scene()
        _test_setting_edits_are_deferred(objects, counts, reset)
        _test_target_update_clears_only_target(objects, counts, reset)
        _test_off_updates_are_light(counts, reset)
        _test_visual_updates_are_light(counts, reset)
        objects = _setup_scene()
        _test_full_apply_clears_pending(objects, counts, reset)
        _test_preset_apply_is_settings_only(objects, counts, reset)
        print("BMANGA_LINE_CONTROL_UPDATE_SCOPE_OK")
    finally:
        restore()
        try:
            b_manga_line.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
