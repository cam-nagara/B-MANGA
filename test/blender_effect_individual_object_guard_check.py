"""効果線の個別管理限定と設定分離を Blender 実機で確認する。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_effect_individual_guard"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _managed_effect(scene, stable_id: str, title: str, z_index: int):
    from bmanga_effect_individual_guard.utils import effect_line_object, layer_object_model

    obj = effect_line_object.create_effect_line_object(
        scene=scene,
        bmanga_id=stable_id,
        title=title,
        z_index=z_index,
        parent_kind="none",
        parent_key="",
    )
    assert layer_object_model.is_layer_object(obj, "effect")
    layer = layer_object_model.content_layer(obj)
    assert layer is not None
    obj.hide_viewport = False
    return obj, layer


def _assert_close(actual: float, expected: float, label: str) -> None:
    assert abs(float(actual) - float(expected)) <= 1.0e-6, (label, actual, expected)


def _check_param_isolation(context, effect_line_op) -> None:
    params = context.scene.bmanga_effect_line_params
    defaults = {
        "effect_type": str(params.effect_type),
        "brush_size_mm": float(params.brush_size_mm),
        "spacing_distance_mm": float(params.spacing_distance_mm),
    }
    obj_a, layer_a = _managed_effect(context.scene, "effect_guard_a", "効果線A", 10)
    obj_b, layer_b = _managed_effect(context.scene, "effect_guard_b", "効果線B", 20)
    effect_line_op._set_layer_bounds(
        obj_a,
        layer_a,
        (100.0, 100.0, 20.0, 20.0),
        params_data={
            "schema_version": 20,
            "effect_type": "speed",
            "brush_size_mm": 2.75,
            "spacing_distance_mm": 9.5,
            "opacity": 41.0,
        },
    )
    effect_line_op._set_layer_bounds(obj_b, layer_b, (140.0, 100.0, 20.0, 20.0), params_data={})
    layer_b.opacity = 0.37

    for _ in range(2):
        effect_line_op._set_active_effect_layer(context, obj_a, layer_a)
        assert params.effect_type == "speed"
        _assert_close(params.brush_size_mm, 2.75, "効果線A 線幅")
        _assert_close(params.spacing_distance_mm, 9.5, "効果線A 間隔")
        _assert_close(params.opacity, 41.0, "効果線A 不透明度")

        effect_line_op._set_active_effect_layer(context, obj_b, layer_b)
        assert params.effect_type == defaults["effect_type"]
        _assert_close(params.brush_size_mm, defaults["brush_size_mm"], "効果線B 線幅既定値")
        _assert_close(params.spacing_distance_mm, defaults["spacing_distance_mm"], "効果線B 間隔既定値")
        _assert_close(params.opacity, 37.0, "効果線B レイヤー不透明度")
        assert not effect_line_op._scene_params_syncing(context.scene)


def _check_legacy_guard(context, effect_line_op) -> None:
    from bmanga_effect_individual_guard.utils import gpencil

    data = gpencil.ensure_gpencil("BManga_EffectLines_TestData")
    legacy_obj = bpy.data.objects.new("BManga_EffectLines", data)
    context.scene.collection.objects.link(legacy_obj)
    legacy_layer = gpencil.ensure_layer(data, "旧集約効果線")
    effect_line_op._set_layer_bounds(legacy_obj, legacy_layer, (5.0, 5.0, 20.0, 20.0))

    hit_obj, hit_layer, _bounds, _part = effect_line_op._hit_effect_layer(context, 10.0, 10.0)
    assert hit_obj is not legacy_obj
    assert hit_layer is None

    before = len(data.layers)
    effect_line_op._delete_effect_layer(context, legacy_obj, legacy_layer)
    assert bpy.data.objects.get(legacy_obj.name) is legacy_obj
    assert len(data.layers) == before

    probe = SimpleNamespace(_drag_obj_name="", _drag_layer_name=legacy_layer.name)
    drag_obj, drag_layer = effect_line_op.BMANGA_OT_effect_line_tool._drag_target(probe, context)
    assert drag_obj is None and drag_layer is None


def main() -> None:
    addon = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        addon = _load_addon()
        from bmanga_effect_individual_guard.operators import effect_line_op

        _check_param_isolation(bpy.context, effect_line_op)
        _check_legacy_guard(bpy.context, effect_line_op)
        print("BMANGA_EFFECT_INDIVIDUAL_OBJECT_GUARD_OK", flush=True)
    finally:
        if addon is not None:
            addon.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
