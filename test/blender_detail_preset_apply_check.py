"""Blender実機: 詳細設定のプリセットが固定対象だけへ適用されることを確認する。

一時作品と一時プリセット保存先だけを使い、ユーザー作品は開かない。
同種レイヤーを二つ作り、一覧側のアクティブ対象をBにしたままAへ適用する。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_detail_preset_apply"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    detail_module = __import__(
        f"{MOD_NAME}.operators.detail_preset_apply_op", fromlist=["register"]
    )
    if not hasattr(bpy.types, "BMANGA_OT_detail_preset_apply"):
        detail_module.register()
    return module


def _apply(preset_type: str, name: str, target_kind: str, stable_id: str):
    return bpy.ops.bmanga.detail_preset_apply(
        preset_type=preset_type,
        preset_name=name,
        target_kind=target_kind,
        target_id=stable_id,
        stable_id=stable_id,
    )


def _check_fill(context) -> None:
    from bmanga_dev_detail_preset_apply.io import fill_presets, gradient_presets

    scene = context.scene
    first = scene.bmanga_fill_layers.add()
    first.id = "fixed_fill_a"
    first.opacity = 98.0
    second = scene.bmanga_fill_layers.add()
    second.id = "fixed_fill_b"
    second.opacity = 77.0
    fill_presets.save_local_preset("固定ベタ", "", {"color": [0.2, 0.3, 0.4, 1], "opacity": 34})
    scene.bmanga_active_fill_layer_index = 1
    scene.bmanga_active_layer_kind = "fill"
    assert _apply("fill", "固定ベタ", "fill", first.id) == {"FINISHED"}
    assert abs(first.opacity - 34.0) < 1e-5
    assert abs(second.opacity - 77.0) < 1e-5, "アクティブな別レイヤーが変更された"

    gradient = scene.bmanga_fill_layers.add()
    gradient.id = "fixed_gradient_a"
    gradient.fill_type = "gradient"
    gradient.opacity = 86.0
    gradient_presets.save_local_preset(
        "固定グラデ", "", {"gradient_type": "radial", "opacity": 45}
    )
    assert _apply("gradient", "固定グラデ", "fill", gradient.id) == {"FINISHED"}
    assert gradient.gradient_type == "radial" and abs(gradient.opacity - 45.0) < 1e-5
    assert _apply("gradient", "固定グラデ", "fill", first.id) == {"CANCELLED"}


def _check_text(context, page) -> None:
    from bmanga_dev_detail_preset_apply.io import text_presets

    first = page.texts.add()
    first.id = "fixed_text_a"
    first.body = "A"
    first.line_height = 1.0
    second = page.texts.add()
    second.id = "fixed_text_b"
    second.body = "B"
    second.line_height = 1.4
    other_page = context.scene.bmanga_work.pages.add()
    other_page.id = "p0099"
    duplicate = other_page.texts.add()
    duplicate.id = first.id
    duplicate.body = "別ページの同名ID"
    duplicate.line_height = 1.8
    text_presets.save_local_preset(None, "固定テキスト", "", {"line_height": 2.25})
    page.active_text_index = 1
    context.scene.bmanga_active_layer_kind = "text"
    stable_id = f"{page.id}:{first.id}"
    assert _apply("text", "固定テキスト", "text", stable_id) == {"FINISHED"}
    assert abs(first.line_height - 2.25) < 1e-5
    assert abs(second.line_height - 1.4) < 1e-5, "アクティブな別テキストが変更された"
    assert abs(duplicate.line_height - 1.8) < 1e-5, "別ページの同名テキストが変更された"


def _check_image_path(context) -> None:
    from bmanga_dev_detail_preset_apply.io import image_path_presets

    scene = context.scene
    first = scene.bmanga_image_path_layers.add()
    first.id = "fixed_path_a"
    first.opacity = 31.0
    first.spacing_percent = 145.0
    image_path_presets.save_local_preset(None, first, "固定パターン", "")
    first.opacity = 90.0
    first.spacing_percent = 15.0
    second = scene.bmanga_image_path_layers.add()
    second.id = "fixed_path_b"
    second.opacity = 82.0
    scene.bmanga_active_image_path_layer_index = 1
    scene.bmanga_active_layer_kind = "image_path"
    assert _apply("image_path", "固定パターン", "image_path", first.id) == {"FINISHED"}
    assert abs(first.opacity - 31.0) < 1e-5 and abs(first.spacing_percent - 145.0) < 1e-5
    assert abs(second.opacity - 82.0) < 1e-5, "アクティブな別パターンが変更された"


def _check_balloon(context, page, temp_root: Path) -> None:
    from bmanga_dev_detail_preset_apply.io import balloon_presets

    balloon_presets.save_local_preset(
        temp_root, "固定フキダシ", "", [(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)]
    )
    first = page.balloons.add()
    first.id = "fixed_balloon_a"
    first.shape = "ellipse"
    second = page.balloons.add()
    second.id = "fixed_balloon_b"
    second.shape = "rect"
    page.active_balloon_index = 1
    context.scene.bmanga_active_layer_kind = "balloon"
    assert _apply("balloon", "固定フキダシ", "balloon", first.id) == {"FINISHED"}
    assert first.shape == "custom" and first.custom_preset_name == "固定フキダシ"
    assert second.shape == "rect", "アクティブな別フキダシが変更された"


def _check_border(context, page, work_dir: Path) -> None:
    from bmanga_dev_detail_preset_apply.io import border_presets

    first = page.comas.add()
    first.id = first.coma_id = "c81"
    first.border.width_mm = 0.77
    border_presets.save_local_preset(work_dir, first, "固定枠線", "")
    first.border.width_mm = 0.11
    second = page.comas.add()
    second.id = second.coma_id = "c82"
    second.border.width_mm = 0.88
    page.active_coma_index = 1
    context.scene.bmanga_active_layer_kind = "coma"
    stable_id = f"{page.id}:{first.coma_id}"
    assert _apply("border", "固定枠線", "coma", stable_id) == {"FINISHED"}
    assert abs(first.border.width_mm - 0.77) < 1e-5
    assert abs(second.border.width_mm - 0.88) < 1e-5, "アクティブな別コマが変更された"


def _check_effect(context) -> None:
    from bmanga_dev_detail_preset_apply.io import effect_line_presets
    from bmanga_dev_detail_preset_apply.operators import effect_line_op
    from bmanga_dev_detail_preset_apply.utils import (
        effect_line_object,
        layer_object_model,
        layer_object_sync,
    )

    params = context.scene.bmanga_effect_line_params
    work = context.scene.bmanga_work
    layer_object_sync.mirror_work_to_outliner(context.scene, work)
    effect_line_op._set_scene_params_syncing(context.scene, True)
    try:
        params.brush_size_mm = 0.61
    finally:
        effect_line_op._set_scene_params_syncing(context.scene, False)
    first = effect_line_object.create_effect_line_object(
        scene=context.scene, bmanga_id="fixed_effect_a", title="効果線A", z_index=210,
        parent_kind="page", parent_key=work.pages[0].id,
    )
    second = effect_line_object.create_effect_line_object(
        scene=context.scene, bmanga_id="fixed_effect_b", title="効果線B", z_index=220,
        parent_kind="page", parent_key=work.pages[0].id,
    )
    assert first is not None and second is not None
    first_layer = layer_object_model.content_layer(first)
    second_layer = layer_object_model.content_layer(second)
    effect_line_op._write_effect_strokes(
        context, first, first_layer, (10.0, 10.0, 40.0, 40.0),
        params_override=params, propagate_link=False,
    )
    effect_line_op._write_effect_strokes(
        context, second, second_layer, (60.0, 10.0, 40.0, 40.0),
        params_override=params, propagate_link=False,
    )
    effect_line_op._set_scene_params_syncing(context.scene, True)
    try:
        params.brush_size_mm = 1.23
        effect_line_presets.save_local_preset(None, params, "固定効果線", "")
        params.brush_size_mm = 0.61
    finally:
        effect_line_op._set_scene_params_syncing(context.scene, False)
    first_id = layer_object_model.stable_id(first)
    assert _apply("effect_line", "固定効果線", "effect", first_id) == {"FINISHED"}
    first_data = effect_line_op._layer_params_data(first, first_layer)
    second_data = effect_line_op._layer_params_data(second, second_layer)
    assert abs(float(first_data["brush_size_mm"]) - 1.23) < 1e-5
    assert abs(float(second_data["brush_size_mm"]) - 0.61) < 1e-5, "別の効果線が変更された"


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_detail_preset_apply_"))
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(temp_root / "config")
    addon = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        addon = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "FixedPreset.bmanga"))
        assert result == {"FINISHED"}, result
        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]
        _check_fill(context)
        _check_text(context, page)
        _check_image_path(context)
        _check_balloon(context, page, temp_root)
        _check_border(context, page, Path(work.work_dir))
        _check_effect(context)
        before = context.scene.bmanga_fill_layers[1].opacity
        assert _apply("fill", "固定ベタ", "fill", "missing-id") == {"CANCELLED"}
        assert context.scene.bmanga_fill_layers[1].opacity == before
        print("BMANGA_DETAIL_PRESET_FIXED_TARGET_OK")
    finally:
        if addon is not None:
            try:
                addon.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        os.environ.pop("BMANGA_USER_CONFIG_DIR", None)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
