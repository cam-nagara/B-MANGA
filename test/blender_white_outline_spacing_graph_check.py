"""Blender実機用: 白抜き線の束間隔・間隔変化・保存対象を確認する。"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_white_outline_spacing"


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


def _close(actual: float, expected: float, label: str, tolerance: float = 1.0e-6) -> None:
    if not math.isclose(float(actual), float(expected), abs_tol=tolerance):
        raise AssertionError(f"{label}: actual={actual} expected={expected}")


def _configure_white_outline(params) -> None:
    params.effect_type = "white_outline"
    params.start_shape = "ellipse"
    params.end_shape = "ellipse"
    params.white_outline_count = 4
    params.white_outline_width_mm = 10.0
    params.white_outline_width_jitter_enabled = False
    params.white_outline_length_jitter_enabled = False
    params.white_outline_white_ratio_percent = 50.0
    params.white_outline_white_line_count_auto = False
    params.white_outline_white_line_count = 1
    params.white_outline_white_brush_mm = 0.4
    params.white_outline_spacing_mm = 0.6
    params.white_outline_white_spacing_scale_percent = 100.0
    params.white_outline_white_in_percent = 100.0
    params.white_outline_white_out_percent = 100.0
    params.white_outline_black_line_count_auto = False
    params.white_outline_black_line_count = 1
    params.white_outline_black_direction = "outside"
    params.white_outline_black_brush_mm = 0.4
    params.white_outline_black_spacing_mm = 0.6
    params.white_outline_black_spacing_scale_percent = 100.0
    params.white_outline_black_in_percent = 100.0
    params.white_outline_black_out_percent = 100.0
    params.white_outline_bundle_spacing_deg = 0.0
    params.white_outline_bundle_spacing_jitter = 0.0
    params.white_outline_angle_deg = 0.0


def _white_angles(strokes) -> list[float]:
    angles = []
    for stroke in strokes:
        if str(getattr(stroke, "role", "") or "") != "white_outline_white":
            continue
        point = list(getattr(stroke, "points_xyz", ()) or ())[0]
        angles.append(math.atan2(float(point[1]), float(point[0])))
    return angles


def _angle_delta_deg(left: float, right: float) -> float:
    return math.degrees((right - left) % (2.0 * math.pi))


def _generated_angles(params, effect_line_gen, seed: int) -> list[float]:
    strokes = effect_line_gen.generate_white_outline_strokes(
        params,
        (0.0, 0.0),
        20.0,
        15.0,
        seed=seed,
    )
    angles = _white_angles(strokes)
    assert len(angles) == int(params.white_outline_count), angles
    return angles


def _assert_bundle_spacing(params, effect_line_gen) -> None:
    params.white_outline_bundle_spacing_deg = 0.0
    params.white_outline_bundle_spacing_jitter = 0.0
    equal = _generated_angles(params, effect_line_gen, seed=23)
    for index in range(1, len(equal)):
        _close(_angle_delta_deg(equal[index - 1], equal[index]), 90.0, "束間隔0の従来等角")

    params.white_outline_bundle_spacing_deg = 30.0
    explicit = _generated_angles(params, effect_line_gen, seed=23)
    for index in range(1, len(explicit)):
        _close(_angle_delta_deg(explicit[index - 1], explicit[index]), 30.0, "明示した束間隔")

    params.white_outline_bundle_spacing_jitter = 0.4
    jitter_a = _generated_angles(params, effect_line_gen, seed=23)
    jitter_b = _generated_angles(params, effect_line_gen, seed=23)
    jitter_c = _generated_angles(params, effect_line_gen, seed=24)
    assert jitter_a == jitter_b, "同じシードで束の間隔乱れを再現できません"
    assert jitter_a != jitter_c, "異なるシードでも束の間隔乱れが同じです"
    assert any(
        not math.isclose(_angle_delta_deg(jitter_a[i - 1], jitter_a[i]), 30.0, abs_tol=1.0e-4)
        for i in range(1, len(jitter_a))
    ), "間隔乱れを有効にしても束間隔が変化しません"


def _assert_spacing_scale(white_outline) -> None:
    white_100 = white_outline._line_offsets(
        10.0, 0.4, 0.6, auto_count=False, count=5, spacing_scale_percent=100.0
    )
    expected_white = [-2.0, -1.0, 0.0, 1.0, 2.0]
    assert len(white_100) == len(expected_white)
    for actual, expected in zip(white_100, expected_white):
        _close(actual, expected, "白線間隔変化100%の互換")
    white_200 = white_outline._line_offsets(
        10.0, 0.4, 0.6, auto_count=False, count=5, spacing_scale_percent=200.0
    )
    assert white_200 != white_100, "白線の間隔変化を変えてもオフセットが変わりません"
    assert not math.isclose(white_200[1] - white_200[0], white_200[2] - white_200[1]), (
        "白線の間隔変化が中心からの位置差へ反映されていません"
    )

    black_100 = white_outline._edge_distances(
        10.0, 0.4, 0.6, auto_count=False, count=4, spacing_scale_percent=100.0
    )
    assert [round(distance, 6) for distance, _norm in black_100] == [0.0, 1.0, 2.0, 3.0]
    black_200 = white_outline._edge_distances(
        10.0, 0.4, 0.6, auto_count=False, count=4, spacing_scale_percent=200.0
    )
    assert black_200 != black_100, "黒線の間隔変化を変えてもオフセットが変わりません"
    black_steps = [black_200[i][0] - black_200[i - 1][0] for i in range(1, len(black_200))]
    assert max(black_steps) - min(black_steps) > 1.0e-4, "黒線の間隔変化が距離差へ反映されていません"


def _assert_defaults(params, entry) -> None:
    _close(params.start_cloud_sub_width_ratio, 30.0, "効果線の外端小山幅初期値")
    _close(params.end_cloud_sub_width_ratio, 30.0, "効果線の内端小山幅初期値")
    _close(entry.shape_params.cloud_sub_width_ratio, 30.0, "フキダシ形状の小山幅初期値")
    _close(entry.start_cloud_sub_width_ratio, 30.0, "フキダシ外端の小山幅初期値")
    _close(entry.end_cloud_sub_width_ratio, 30.0, "フキダシ内端の小山幅初期値")


def _assert_saved(params, entry, effect_line_core, balloon_core, schema) -> None:
    fields = {
        "white_outline_bundle_spacing_deg",
        "white_outline_bundle_spacing_jitter",
        "white_outline_white_spacing_scale_percent",
        "white_outline_black_spacing_scale_percent",
    }
    assert fields <= set(effect_line_core.EFFECT_PARAM_FIELDS), "効果線の新規項目が保存対象にありません"
    assert fields <= set(balloon_core.UNI_FLASH_PARAM_FIELDS), "フキダシの新規項目が保存対象にありません"

    params.white_outline_bundle_spacing_deg = 37.0
    params.white_outline_bundle_spacing_jitter = 0.25
    params.white_outline_white_spacing_scale_percent = 80.0
    params.white_outline_black_spacing_scale_percent = 140.0
    effect_saved = effect_line_core.effect_params_to_dict(params)
    _close(effect_saved["white_outline_bundle_spacing_deg"], 37.0, "効果線 束の間隔保存")
    _close(effect_saved["white_outline_bundle_spacing_jitter"], 0.25, "効果線 間隔乱れ保存")
    _close(effect_saved["white_outline_white_spacing_scale_percent"], 80.0, "効果線 白線間隔変化保存")
    _close(effect_saved["white_outline_black_spacing_scale_percent"], 140.0, "効果線 黒線間隔変化保存")

    entry.line_style = "white_outline"
    entry.white_outline_bundle_spacing_deg = 41.0
    entry.white_outline_bundle_spacing_jitter = 0.3
    entry.white_outline_white_spacing_scale_percent = 75.0
    entry.white_outline_black_spacing_scale_percent = 160.0
    balloon_saved = schema.balloon_entry_to_dict(entry)
    nested = balloon_saved["uniFlashParams"]
    _close(nested["white_outline_bundle_spacing_deg"], 41.0, "フキダシ 束の間隔保存")
    _close(nested["white_outline_bundle_spacing_jitter"], 0.3, "フキダシ 間隔乱れ保存")
    _close(nested["white_outline_white_spacing_scale_percent"], 75.0, "フキダシ 白線間隔変化保存")
    _close(nested["white_outline_black_spacing_scale_percent"], 160.0, "フキダシ 黒線間隔変化保存")


def _assert_balloon_cache_signature(entry, balloon_flash_effect_line_mesh) -> None:
    fields = (
        "white_outline_bundle_spacing_deg",
        "white_outline_bundle_spacing_jitter",
        "white_outline_white_spacing_scale_percent",
        "white_outline_black_spacing_scale_percent",
        "white_outline_white_in_percent",
        "white_outline_white_out_percent",
        "white_outline_white_in_range_percent",
        "white_outline_white_out_range_percent",
        "white_outline_white_in_easing_curve",
        "white_outline_white_out_easing_curve",
        "white_outline_black_in_percent",
        "white_outline_black_out_percent",
        "white_outline_black_in_range_percent",
        "white_outline_black_out_range_percent",
        "white_outline_black_in_easing_curve",
        "white_outline_black_out_easing_curve",
    )
    signature = balloon_flash_effect_line_mesh._effect_params_signature(
        entry, "white_outline"
    )
    missing = set(fields) - set(signature)
    assert not missing, f"フキダシの再構築条件に不足があります: {sorted(missing)}"


def main() -> None:
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()

        from bmanga_dev_white_outline_spacing.core import balloon as balloon_core
        from bmanga_dev_white_outline_spacing.core import effect_line as effect_line_core
        from bmanga_dev_white_outline_spacing.io import schema
        from bmanga_dev_white_outline_spacing.operators import effect_line_gen
        from bmanga_dev_white_outline_spacing.operators import effect_line_white_outline
        from bmanga_dev_white_outline_spacing.utils import balloon_flash_effect_line_mesh

        params = bpy.context.scene.bmanga_effect_line_params
        work = bpy.context.scene.bmanga_work
        entry = work.pages.add().balloons.add()

        _assert_defaults(params, entry)
        _configure_white_outline(params)
        _assert_bundle_spacing(params, effect_line_gen)
        _assert_spacing_scale(effect_line_white_outline)
        _assert_saved(params, entry, effect_line_core, balloon_core, schema)
        _assert_balloon_cache_signature(entry, balloon_flash_effect_line_mesh)
        print("BMANGA_WHITE_OUTLINE_SPACING_GRAPH_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
