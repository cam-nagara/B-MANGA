"""Blender実機用: 効果線・フキダシ白抜き線の黒線入り抜きを確認する。"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_black_inout"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _black_strokes(strokes):
    return [stroke for stroke in strokes if str(getattr(stroke, "role", "")) == "white_outline_black"]


class _LabelLayout:
    def __init__(self, labels=None):
        self.labels = [] if labels is None else labels
        self.enabled = True

    def box(self):
        return _LabelLayout(self.labels)

    def row(self, align=False):
        del align
        return _LabelLayout(self.labels)

    def label(self, *, text="", **_kwargs):
        self.labels.append(str(text))

    def prop(self, *_args, **_kwargs):
        return None


def _assert_black_before_white(layout: _LabelLayout, label: str) -> None:
    black_index = layout.labels.index("黒線")
    white_index = layout.labels.index("白線")
    if black_index >= white_index:
        raise AssertionError(f"{label}: 黒線設定の下に白線設定がありません: {layout.labels}")


def _assert_ratio(actual: float, base: float, ratio: float, label: str) -> None:
    if not math.isclose(float(actual), float(base) * float(ratio), rel_tol=1.0e-5, abs_tol=1.0e-8):
        raise AssertionError(f"{label}: actual={actual} base={base} ratio={ratio}")


def _set_effect_values(params, effect_line_op, context) -> None:
    effect_line_op._set_scene_params_syncing(context.scene, True)
    try:
        params.effect_type = "white_outline"
        params.start_shape = "ellipse"
        params.end_shape = "ellipse"
        params.white_outline_count = 1
        params.white_outline_width_mm = 10.0
        params.white_outline_width_jitter_enabled = False
        params.white_outline_length_jitter_enabled = False
        params.white_outline_white_ratio_percent = 50.0
        params.white_outline_white_line_count_auto = False
        params.white_outline_white_line_count = 1
        params.white_outline_white_brush_mm = 0.4
        params.white_outline_white_in_percent = 100.0
        params.white_outline_white_out_percent = 100.0
        params.white_outline_black_line_count_auto = False
        params.white_outline_black_line_count = 1
        params.white_outline_black_direction = "outside"
        params.white_outline_black_brush_mm = 0.5
        params.white_outline_black_width_scale_percent = 100.0
        params.white_outline_black_length_scale_near_percent = 100.0
        params.white_outline_black_length_scale_far_percent = 100.0
        params.white_outline_black_attenuation = 0.0
        params.white_outline_black_in_percent = 20.0
        params.white_outline_black_out_percent = 40.0
        params.white_outline_black_inout_range_mode = "percent"
        params.white_outline_black_in_range_percent = 25.0
        params.white_outline_black_out_range_percent = 25.0
    finally:
        effect_line_op._set_scene_params_syncing(context.scene, False)


def _stroke_end_metrics(stroke, effect_line_gen, end_outline):
    points = list(getattr(stroke, "points_xyz", ()) or ())
    start_mm = (float(points[0][0]) * 1000.0, float(points[0][1]) * 1000.0)
    actual_mm = (float(points[-1][0]) * 1000.0, float(points[-1][1]) * 1000.0)
    expected_mm = effect_line_gen._focus_end_point((0.0, 0.0), end_outline, start_mm)
    return start_mm, actual_mm, expected_mm


def _end_outline(params, effect_line_gen, seed: int):
    rect = effect_line_gen._scaled_rect(0.0, 0.0, 20.0, 15.0, 1.0)
    return effect_line_gen._shape_outline(params, "end", rect, (0.0, 0.0), seed=seed + 23)


def _assert_full_length_shapes(params, effect_line_gen, start_outline) -> None:
    for shape, rotation_deg, rounded in (
        ("ellipse", 0.0, False),
        ("rect", 0.0, False),
        ("rect", 23.0, True),
        ("cloud", 11.0, False),
        ("fluffy", 0.0, False),
        ("thorn", 0.0, False),
        ("thorn-curve", 0.0, False),
    ):
        params.end_shape = shape
        params.rotation_deg = rotation_deg
        params.end_rounded_corner_enabled = rounded
        strokes = effect_line_gen.generate_strokes(
            params,
            center_xy_mm=(0.0, 0.0),
            radius_xy_mm=(20.0, 15.0),
            seed=17,
            start_outline_mm=start_outline,
            start_extend_mm=0.5,
        )
        expected_outline = _end_outline(params, effect_line_gen, 17)
        misses = []
        for stroke in strokes:
            role = str(getattr(stroke, "role", "") or "")
            if role not in {"white_outline_white", "white_outline_black"}:
                continue
            _start_mm, actual_mm, expected_mm = _stroke_end_metrics(stroke, effect_line_gen, expected_outline)
            error_mm = math.dist(actual_mm, expected_mm)
            if error_mm > 1.0e-4:
                misses.append((role, actual_mm, expected_mm, error_mm))
        assert strokes and not misses, (
            f"コマ枠始点の白抜き線が終点形状へ届いていません: shape={shape} misses={misses[:8]}"
        )


def _assert_relative_length_jitter(params, effect_line_gen, start_outline) -> None:
    params.end_shape = "ellipse"
    params.rotation_deg = 0.0
    params.end_rounded_corner_enabled = False
    params.white_outline_length_jitter_enabled = True
    params.white_outline_length_min_percent = 60.0
    strokes = effect_line_gen.generate_strokes(
        params,
        center_xy_mm=(0.0, 0.0),
        radius_xy_mm=(20.0, 15.0),
        seed=31,
        start_outline_mm=start_outline,
    )
    expected_outline = _end_outline(params, effect_line_gen, 31)
    ratios = []
    for stroke in strokes:
        start_mm, actual_mm, expected_mm = _stroke_end_metrics(stroke, effect_line_gen, expected_outline)
        full_length = math.dist(start_mm, expected_mm)
        ratios.append(math.dist(start_mm, actual_mm) / max(1.0e-9, full_length))
    assert len(ratios) == 21, f"長さ乱れ確認用の線数が不正です: {len(ratios)}"
    for index in range(0, len(ratios), 3):
        bundle = ratios[index:index + 3]
        assert max(bundle) - min(bundle) < 1.0e-5, f"同じ束の白線と黒線で長さ率が不一致です: {bundle}"
        assert 0.6 <= bundle[0] <= 1.0, f"長さ乱れが設定範囲外です: {bundle[0]}"
    params.white_outline_length_jitter_enabled = False


def _assert_coma_frame_bundles_reach_end_shape(params, effect_line_gen) -> None:
    params.white_outline_count = 7
    params.white_outline_angle_deg = 0.0
    params.white_outline_length_jitter_enabled = False
    params.white_outline_white_attenuation = 0.0
    params.white_outline_black_attenuation = 0.0
    params.white_outline_black_length_scale_near_percent = 100.0
    params.white_outline_black_length_scale_far_percent = 100.0
    start_outline = [(-80.0, -50.0), (80.0, -50.0), (80.0, 50.0), (-80.0, 50.0)]
    _assert_full_length_shapes(params, effect_line_gen, start_outline)
    _assert_relative_length_jitter(params, effect_line_gen, start_outline)


def _check_effect(context) -> None:
    from bmanga_dev_black_inout.core import effect_line as effect_core
    from bmanga_dev_black_inout.operators import effect_line_gen, effect_line_op
    from bmanga_dev_black_inout.panels import line_effect_settings_ui

    params = context.scene.bmanga_effect_line_params
    for field, expected in (
        ("white_outline_black_in_percent", 100.0),
        ("white_outline_black_out_percent", 100.0),
        ("white_outline_black_in_range_percent", 100.0),
        ("white_outline_black_out_range_percent", 100.0),
        ("white_outline_black_in_range_mm", 10.0),
        ("white_outline_black_out_range_mm", 10.0),
    ):
        assert math.isclose(float(getattr(params, field)), expected, abs_tol=1.0e-6), field
    assert params.white_outline_black_inout_range_mode == "percent"
    layout = _LabelLayout()
    line_effect_settings_ui.draw_effect_white_outline_settings(layout, params)
    _assert_black_before_white(layout, "効果線")

    params.white_outline_black_in_percent = 7.0
    params.white_outline_black_out_percent = 9.0
    effect_core.effect_params_from_dict(
        params,
        {"schema_version": 16, "effect_type": "white_outline"},
    )
    assert math.isclose(params.white_outline_black_in_percent, 100.0, abs_tol=1.0e-6)
    assert math.isclose(params.white_outline_black_out_percent, 100.0, abs_tol=1.0e-6)

    _set_effect_values(params, effect_line_op, context)
    stored = effect_core.effect_params_to_dict(params)
    for field in (
        "white_outline_black_in_percent",
        "white_outline_black_out_percent",
        "white_outline_black_inout_range_mode",
        "white_outline_black_in_range_percent",
        "white_outline_black_out_range_percent",
        "white_outline_black_in_range_mm",
        "white_outline_black_out_range_mm",
    ):
        assert field in stored, field

    strokes = effect_line_gen.generate_white_outline_strokes(params, (0.0, 0.0), 40.0, 30.0, seed=3)
    black = _black_strokes(strokes)
    assert black, "効果線の黒線が生成されません"
    for stroke in black:
        assert stroke.radii is not None and len(stroke.radii) >= 4
        _assert_ratio(stroke.radii[0], stroke.radius, 0.2, "効果線 黒線入り")
        _assert_ratio(stroke.radii[-1], stroke.radius, 0.4, "効果線 黒線抜き")
        assert max(stroke.radii) >= stroke.radius * 0.999

    _assert_coma_frame_bundles_reach_end_shape(params, effect_line_gen)


def _check_balloon(context) -> None:
    from bmanga_dev_black_inout.core import balloon as balloon_core
    from bmanga_dev_black_inout.panels import line_effect_settings_ui
    from bmanga_dev_black_inout.utils import balloon_flash_effect_line_mesh

    work = context.scene.bmanga_work
    page = work.pages.add()
    entry = page.balloons.add()
    entry.id = "black_inout_balloon"
    entry.shape = "ellipse"
    entry.width_mm = 80.0
    entry.height_mm = 60.0
    entry.line_style = "white_outline"
    entry.line_width_mm = 0.5
    entry.line_peak_width_pct = 100.0
    entry.line_valley_width_pct = 0.0
    entry.flash_white_outline_count = 1
    entry.white_outline_white_line_count_auto = False
    entry.flash_white_outline_white_line_count = 1
    entry.white_outline_black_line_count_auto = False
    entry.flash_white_outline_black_line_count = 1
    entry.white_outline_black_direction = "outside"
    entry.white_outline_black_width_scale_percent = 100.0
    entry.white_outline_black_length_scale_near_percent = 100.0
    entry.white_outline_black_length_scale_far_percent = 100.0
    entry.white_outline_black_in_percent = 20.0
    entry.white_outline_black_out_percent = 40.0
    entry.white_outline_black_inout_range_mode = "percent"
    entry.white_outline_black_in_range_percent = 25.0
    entry.white_outline_black_out_range_percent = 25.0
    entry.in_percent = 0.0
    entry.out_percent = 0.0
    layout = _LabelLayout()
    line_effect_settings_ui.draw_balloon_white_outline_settings(layout, entry)
    _assert_black_before_white(layout, "フキダシ")

    stored = balloon_core.uni_flash_params_to_dict(entry)
    for field in (
        "white_outline_black_in_percent",
        "white_outline_black_out_percent",
        "white_outline_black_inout_range_mode",
        "white_outline_black_in_range_percent",
        "white_outline_black_out_range_percent",
        "white_outline_black_in_range_mm",
        "white_outline_black_out_range_mm",
    ):
        assert field in stored, field
    restored = page.balloons.add()
    balloon_core.uni_flash_params_from_dict(restored, stored)
    assert math.isclose(restored.white_outline_black_in_percent, 20.0, abs_tol=1.0e-6)
    assert math.isclose(restored.white_outline_black_out_percent, 40.0, abs_tol=1.0e-6)

    black = _black_strokes(balloon_flash_effect_line_mesh.generate_flash_strokes_rect_local(entry))
    assert black and black[0].radii is not None
    _assert_ratio(black[0].radii[0], black[0].radius, 0.2, "フキダシ 黒線入り")
    _assert_ratio(black[0].radii[-1], black[0].radius, 0.4, "フキダシ 黒線抜き")

    entry.inout_apply_brush_size = True
    entry.inout_apply_opacity = False
    entry.in_percent = 50.0
    entry.out_percent = 50.0
    entry.in_start_percent = 50.0
    entry.out_start_percent = 50.0
    combined = _black_strokes(balloon_flash_effect_line_mesh.generate_flash_strokes_rect_local(entry))
    assert combined and combined[0].radii is not None
    _assert_ratio(combined[0].radii[0], combined[0].radius, 0.1, "フキダシ 合成後の黒線入り")
    _assert_ratio(combined[0].radii[-1], combined[0].radius, 0.2, "フキダシ 合成後の黒線抜き")


def main() -> None:
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        _check_effect(bpy.context)
        _check_balloon(bpy.context)
        print("BMANGA_WHITE_OUTLINE_BLACK_INOUT_OK")
    finally:
        if mod is not None:
            mod.unregister()


if __name__ == "__main__":
    main()
