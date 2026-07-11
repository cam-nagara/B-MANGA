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
    # 本数指定は指定本数を白線領域の幅いっぱいへ均等配置する。
    expected_white = [-5.0, -2.5, 0.0, 2.5, 5.0]
    assert len(white_100) == len(expected_white)
    for actual, expected in zip(white_100, expected_white):
        _close(actual, expected, "白線の本数指定の均等配置")
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
    expected_black = [0.0, 10.0 / 3.0, 20.0 / 3.0, 10.0]
    for (distance, norm), expected in zip(black_100, expected_black):
        _close(distance, expected, "黒線の本数指定の均等配置")
        _close(norm, expected / 10.0, "黒線の本数指定の位置率")
    black_200 = white_outline._edge_distances(
        10.0, 0.4, 0.6, auto_count=False, count=4, spacing_scale_percent=200.0
    )
    assert black_200 != black_100, "黒線の間隔変化を変えてもオフセットが変わりません"
    black_steps = [black_200[i][0] - black_200[i - 1][0] for i in range(1, len(black_200))]
    assert max(black_steps) - min(black_steps) > 1.0e-4, "黒線の間隔変化が距離差へ反映されていません"

    # 間隔変化は線ピッチ全体に掛かる。間隔 0mm でも太さ分のピッチが変化する
    zero_gap_100 = white_outline._edge_distances(
        10.0, 1.0, 0.0, auto_count=False, count=4, spacing_scale_percent=100.0
    )
    zero_gap_300 = white_outline._edge_distances(
        10.0, 1.0, 0.0, auto_count=False, count=4, spacing_scale_percent=300.0
    )
    assert zero_gap_300 != zero_gap_100, "間隔0mmで間隔変化が無効になっています"
    zero_steps = [zero_gap_300[i][0] - zero_gap_300[i - 1][0] for i in range(1, len(zero_gap_300))]
    assert zero_steps[0] < zero_steps[-1], "間隔0mmの間隔変化が端に向かって広がっていません"


def _band_strokes(params, effect_line_gen, *, role: str):
    strokes = effect_line_gen.generate_white_outline_strokes(
        params,
        (0.0, 0.0),
        20.0,
        15.0,
        seed=7,
    )
    return [s for s in strokes if str(getattr(s, "role", "") or "") == role]


def _max_stroke_length_mm(strokes) -> float:
    best = 0.0
    for stroke in strokes:
        points = list(getattr(stroke, "points_xyz", ()) or ())
        if len(points) < 2:
            continue
        sx, sy, _sz = points[0]
        ex, ey, _ez = points[-1]
        best = max(best, math.hypot(float(ex) - float(sx), float(ey) - float(sy)) * 1000.0)
    return best


def _assert_black_ratio_and_length(params, effect_line_gen) -> None:
    params.white_outline_black_line_count_auto = True
    params.white_outline_white_line_count_auto = True

    params.white_outline_white_ratio_percent = 0.0
    whites_none = _band_strokes(params, effect_line_gen, role="white_outline_white")
    assert not whites_none, "白線割合0%でも白線が生成されています"
    params.white_outline_white_ratio_percent = 50.0

    params.white_outline_black_ratio_percent = 70.0
    params.white_outline_length_percent = 100.0
    blacks_default = _band_strokes(params, effect_line_gen, role="white_outline_black")
    assert blacks_default, "既定の黒線割合で黒線が生成されていません"

    params.white_outline_black_ratio_percent = 0.0
    blacks_none = _band_strokes(params, effect_line_gen, role="white_outline_black")
    assert not blacks_none, "黒線割合0%でも黒線が生成されています"

    params.white_outline_black_ratio_percent = 100.0
    blacks_full = _band_strokes(params, effect_line_gen, role="white_outline_black")
    assert len(blacks_full) >= len(blacks_default), "黒線割合を増やしても黒線が増えません"

    params.white_outline_black_ratio_percent = 70.0
    length_full = _max_stroke_length_mm(
        _band_strokes(params, effect_line_gen, role="white_outline_white")
    )
    params.white_outline_length_percent = 50.0
    length_half = _max_stroke_length_mm(
        _band_strokes(params, effect_line_gen, role="white_outline_white")
    )
    assert length_full > 0.0, "白抜き線の白線が生成されていません"
    assert math.isclose(length_half, length_full * 0.5, rel_tol=1.0e-4), (
        f"長さ(%)が線の長さへ反映されていません: {length_full} -> {length_half}"
    )
    params.white_outline_length_percent = 100.0


def _stroke_lengths_mm(strokes) -> list[float]:
    lengths = []
    for stroke in strokes:
        points = list(getattr(stroke, "points_xyz", ()) or ())
        if len(points) < 2:
            continue
        start = points[0]
        end = points[-1]
        lengths.append(math.hypot(float(end[0]) - float(start[0]), float(end[1]) - float(start[1])) * 1000.0)
    return sorted(lengths)


def _assert_black_inside_attenuation(params, effect_line_gen) -> None:
    params.white_outline_count = 1
    params.white_outline_white_ratio_percent = 80.0
    params.white_outline_black_ratio_percent = 20.0
    params.white_outline_white_line_count_auto = False
    params.white_outline_white_line_count = 1
    params.white_outline_black_line_count_auto = False
    params.white_outline_black_line_count = 3
    params.white_outline_black_direction = "inside"
    params.white_outline_black_attenuation = 0.0
    baseline = _stroke_lengths_mm(_band_strokes(params, effect_line_gen, role="white_outline_black"))
    params.white_outline_black_attenuation = 80.0
    attenuated = _stroke_lengths_mm(_band_strokes(params, effect_line_gen, role="white_outline_black"))
    assert baseline and len(baseline) == len(attenuated), "内側黒線の減衰比較用ストロークが不足しています"
    assert any(after < before - 1.0e-4 for before, after in zip(baseline, attenuated)), (
        "重ねる方向=内側で黒線の減衰が効いていません"
    )
    params.white_outline_black_attenuation = 0.0
    params.white_outline_black_direction = "outside"
    params.white_outline_white_ratio_percent = 50.0
    params.white_outline_black_ratio_percent = 50.0
    params.white_outline_count = 4


def _assert_balloon_legacy_migration(page, entry, schema, balloon_mesh) -> None:
    legacy = schema.balloon_entry_to_dict(entry)
    legacy.pop("whiteOutlineSettingsVersion", None)
    legacy.pop("flashWhiteOutlineWhiteBrushMm", None)
    legacy.update(
        {
            "lineStyle": "white_outline",
            "lineWidthMm": 0.4,
            "freeTransformLineWidthScale": 1.75,
            "linePeakWidthPct": 80.0,
            "flashWhiteLinePeakWidthPct": 75.0,
            "flashWhiteLineValleyWidthPct": 25.0,
            "flashWhiteLineWidthPercent": 150.0,
        }
    )
    params = dict(legacy.get("uniFlashParams") or {})
    for field in (
        "white_outline_width_min_percent",
        "white_outline_length_min_percent",
        "white_outline_white_line_count_auto",
        "white_outline_black_line_count_auto",
        "white_outline_white_ratio_percent",
        "white_outline_black_ratio_percent",
    ):
        params.pop(field, None)
    params.update(
        {
            "white_outline_white_in_percent": 60.0,
            "white_outline_white_out_percent": 40.0,
            "white_outline_white_attenuation": 0.5,
            "white_outline_black_attenuation": 0.25,
        }
    )
    legacy["uniFlashParams"] = params

    restored = page.balloons.add()
    schema.balloon_entry_from_dict(restored, legacy)
    expected_brush = 0.4 * 0.8 * 0.75 * 1.5
    _close(restored.flash_white_outline_white_brush_mm, expected_brush, "旧白線太さの基準mm移行")
    _close(restored.white_outline_white_in_percent, 20.0, "旧白線入りの実効値移行")
    _close(restored.white_outline_white_out_percent, 40.0 / 3.0, "旧白線抜きの実効値移行")
    _close(restored.white_outline_white_attenuation, 50.0, "旧白線減衰の百分率移行")
    _close(restored.white_outline_black_attenuation, 25.0, "旧黒線減衰の百分率移行")
    assert not restored.white_outline_white_line_count_auto
    assert not restored.white_outline_black_line_count_auto
    _close(restored.white_outline_white_ratio_percent, 70.0, "旧フキダシの白線割合")
    _close(restored.white_outline_black_ratio_percent, 30.0, "旧フキダシの黒線割合")
    _close(restored.white_outline_width_min_percent, 100.0, "旧フキダシの太さ乱れ最小値")
    _close(restored.white_outline_length_min_percent, 100.0, "旧フキダシの長さ乱れ最小値")
    adapted = balloon_mesh._white_outline_params(restored, black_brush_mm=0.32 * 1.75)
    _close(adapted.white_outline_white_brush_mm, expected_brush * 1.75, "自由変形後の白線太さ")

    migrated = schema.balloon_entry_to_dict(restored)
    assert migrated["whiteOutlineSettingsVersion"] == 2
    second = page.balloons.add()
    schema.balloon_entry_from_dict(second, migrated)
    _close(second.flash_white_outline_white_brush_mm, expected_brush, "白線太さの二重移行防止")
    _close(second.white_outline_white_in_percent, 20.0, "白線入りの二重移行防止")
    _close(second.white_outline_white_attenuation, 50.0, "白線減衰の二重移行防止")
    page.balloons.remove(len(page.balloons) - 1)
    page.balloons.remove(len(page.balloons) - 1)


def _assert_schema_migration(params, effect_line_core) -> None:
    params.white_outline_white_ratio_percent = 45.0
    params.white_outline_black_ratio_percent = 55.0
    params.white_outline_length_percent = 60.0
    effect_line_core.effect_params_from_dict(params, {"schema_version": 18})
    _close(params.white_outline_white_ratio_percent, 30.0, "旧データ読込の白線割合既定値")
    _close(params.white_outline_black_ratio_percent, 70.0, "旧データ読込の黒線割合既定値")
    _close(params.white_outline_length_percent, 100.0, "旧データ読込の長さ既定値")
    params.white_outline_bundle_placement = "corner"
    params.white_outline_position_percent = 40.0
    params.start_corner_type = "bevel"
    effect_line_core.effect_params_from_dict(
        params, {"schema_version": 19, "start_rounded_corner_enabled": True}
    )
    assert params.white_outline_bundle_placement == "spacing", "旧データ読込の束配置既定値"
    _close(params.white_outline_position_percent, 100.0, "旧データ読込の位置既定値")
    assert params.start_corner_type == "rounded", "旧データの角丸チェックが角タイプへ移行されていません"


def _assert_builtin_preset_defaults(params, effect_line_presets) -> None:
    preset = effect_line_presets.load_preset_by_name("白抜き線")
    assert preset is not None, "組込の白抜き線プリセットがありません"
    params.white_outline_white_ratio_percent = 12.0
    params.white_outline_black_ratio_percent = 34.0
    effect_line_presets.apply_preset_to_params(preset, params)
    _close(params.white_outline_white_ratio_percent, 50.0, "組込プリセットの白線割合")
    _close(params.white_outline_black_ratio_percent, 50.0, "組込プリセットの黒線割合")


def _bundle_direction_bins(params, effect_line_gen, *, tol_deg: float = 12.0) -> set[float]:
    strokes = effect_line_gen.generate_white_outline_strokes(
        params,
        (0.0, 0.0),
        40.0,
        30.0,
        seed=3,
    )
    directions: set[float] = set()
    for stroke in strokes:
        if str(getattr(stroke, "role", "") or "") != "white_outline_white":
            continue
        x, y, _z = list(getattr(stroke, "points_xyz", ()) or ())[0]
        directions.add(math.atan2(float(y), float(x)) % (2.0 * math.pi))
    bins: set[float] = set()
    for angle in directions:
        merged = False
        for existing in tuple(bins):
            delta = abs(angle - existing)
            if min(delta, 2.0 * math.pi - delta) < math.radians(tol_deg):
                merged = True
                break
        if not merged:
            bins.add(angle)
    return bins


def _assert_bundle_placement(params, effect_line_gen) -> None:
    params.end_shape = "rect"
    params.white_outline_count = 6
    params.white_outline_bundle_placement = "corner"
    corner_bins = _bundle_direction_bins(params, effect_line_gen)
    assert len(corner_bins) == 4, f"矩形内端の角配置が4方向になっていません: {len(corner_bins)}"
    expected = {
        math.atan2(sy * 30.0, sx * 40.0) % (2.0 * math.pi)
        for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1))
    }
    for angle in corner_bins:
        best = min(
            (min(abs(angle - e), 2.0 * math.pi - abs(angle - e)) for e in expected),
        )
        assert best < math.radians(12.0), f"角配置の方向がずれています: {math.degrees(angle)}"

    params.white_outline_bundle_placement = "edge_center"
    edge_bins = _bundle_direction_bins(params, effect_line_gen)
    assert len(edge_bins) == 4, f"角間の中心配置が4方向になっていません: {len(edge_bins)}"
    for angle in edge_bins:
        best = min(
            min(abs(angle - e), 2.0 * math.pi - abs(angle - e))
            for e in (0.0, math.pi / 2.0, math.pi, 3.0 * math.pi / 2.0)
        )
        assert best < math.radians(12.0), f"角間の中心の方向がずれています: {math.degrees(angle)}"

    # 角が無い内端形状 (楕円) では従来の等間隔配置へフォールバックする
    params.end_shape = "ellipse"
    params.white_outline_bundle_placement = "corner"
    fallback_bins = _bundle_direction_bins(params, effect_line_gen, tol_deg=20.0)
    assert len(fallback_bins) == 6, f"楕円内端のフォールバックが束の数と一致しません: {len(fallback_bins)}"
    params.white_outline_bundle_placement = "spacing"
    params.white_outline_count = 4


def _assert_position_percent(params, effect_line_gen) -> None:
    params.end_shape = "ellipse"
    params.white_outline_bundle_placement = "spacing"
    params.white_outline_length_percent = 100.0
    params.white_outline_angle_deg = 0.0
    params.white_outline_bundle_spacing_deg = 0.0
    params.white_outline_bundle_spacing_jitter = 0.0

    def _axis_white_extents(position: float) -> tuple[float, float]:
        """方位角 0 の束の中心線 (X 軸上の白線) の外側・内側半径 (mm)。"""
        params.white_outline_position_percent = position
        strokes = effect_line_gen.generate_white_outline_strokes(
            params, (0.0, 0.0), 40.0, 30.0, seed=5
        )
        for stroke in strokes:
            if str(getattr(stroke, "role", "") or "") != "white_outline_white":
                continue
            x0, y0, _z0 = stroke.points_xyz[0]
            if abs(float(y0)) > 1.0e-6:
                continue
            x1, y1, _z1 = stroke.points_xyz[-1]
            r_start = math.hypot(float(x0), float(y0)) * 1000.0
            r_end = math.hypot(float(x1), float(y1)) * 1000.0
            return max(r_start, r_end), min(r_start, r_end)
        raise AssertionError("X軸上の白線が見つかりません")

    outer_100, inner_100 = _axis_white_extents(100.0)
    length_mm = outer_100 - inner_100
    assert length_mm > 1.0, f"白線の長さが取れません: {length_mm}"
    outer_0, inner_0 = _axis_white_extents(0.0)
    _close((outer_0 + inner_0) * 0.5, inner_100, "位置0%で線の中心が内端形状上", tolerance=1.0e-3)
    outer_m100, _inner_m100 = _axis_white_extents(-100.0)
    _close(outer_m100, inner_100, "位置-100%で線の外側端が内端形状上", tolerance=1.0e-3)
    params.white_outline_position_percent = 100.0


def _assert_defaults(params, entry) -> None:
    for owner, label in ((params, "効果線"), (entry, "フキダシ")):
        _close(owner.white_outline_white_ratio_percent, 50.0, f"{label}の白線割合初期値")
        _close(owner.white_outline_black_ratio_percent, 50.0, f"{label}の黒線割合初期値")
        assert bool(owner.white_outline_white_line_count_auto), f"{label}の白線本数自動計算が初期ONではありません"
        assert bool(owner.white_outline_black_line_count_auto), f"{label}の黒線本数自動計算が初期ONではありません"
        _close(owner.white_outline_width_min_percent, 50.0, f"{label}の太さ乱れ最小値")
        _close(owner.white_outline_length_min_percent, 50.0, f"{label}の長さ乱れ最小値")
        _close(owner.white_outline_white_out_percent, 0.0, f"{label}の白線抜き初期値")
    _close(params.white_outline_spacing_mm, 0.2, "効果線の白線間隔初期値")
    _close(params.white_outline_black_spacing_mm, 0.2, "効果線の黒線間隔初期値")
    _close(entry.flash_white_outline_spacing_mm, 0.2, "フキダシの白線間隔初期値")
    _close(entry.flash_white_outline_black_spacing_mm, 0.2, "フキダシの黒線間隔初期値")
    _close(entry.flash_white_outline_white_brush_mm, 0.3, "フキダシの白線太さ初期値")
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
        "white_outline_black_ratio_percent",
        "white_outline_length_percent",
        "white_outline_bundle_placement",
        "white_outline_position_percent",
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
    entry.flash_white_outline_white_brush_mm = 0.47
    balloon_saved = schema.balloon_entry_to_dict(entry)
    nested = balloon_saved["uniFlashParams"]
    assert balloon_saved["whiteOutlineSettingsVersion"] == 2
    _close(balloon_saved["flashWhiteOutlineWhiteBrushMm"], 0.47, "フキダシ 白線太さ保存")
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
        "white_outline_black_ratio_percent",
        "white_outline_length_percent",
        "white_outline_bundle_placement",
        "white_outline_position_percent",
        "flash_white_outline_white_brush_mm",
    )
    signature = balloon_flash_effect_line_mesh._effect_params_signature(
        entry, "white_outline"
    )
    missing = set(fields) - set(signature)
    assert not missing, f"フキダシの再構築条件に不足があります: {sorted(missing)}"
    obsolete = {
        "line_valley_width_pct",
        "flash_white_line_width_percent",
        "flash_white_line_valley_width_pct",
        "flash_white_line_peak_width_pct",
    }
    assert not (obsolete & set(signature)), "白抜き線の再構築条件に旧・非表示係数が残っています"


def _assert_black_zero_keeps_white(entry, balloon_mesh) -> None:
    entry.line_style = "white_outline"
    entry.line_peak_width_pct = 0.0
    entry.white_outline_white_ratio_percent = 50.0
    entry.white_outline_black_ratio_percent = 50.0
    strokes = balloon_mesh.generate_flash_strokes_rect_local(entry)
    roles = [str(getattr(stroke, "role", "") or "") for stroke in strokes]
    assert "white_outline_white" in roles, "黒線太さ0%で白線まで消えています"
    assert "white_outline_black" not in roles, "黒線太さ0%でも黒線が残っています"
    entry.line_peak_width_pct = 100.0


def _assert_zero_white_ratio_cache(entry, balloon_mesh) -> None:
    mesh = bpy.data.meshes.new("BMangaWhiteOutlineZeroRatioCacheCheck")
    try:
        mesh.from_pydata(((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)), (), ((0, 1, 2),))
        mesh.update()
        mesh.polygons[0].material_index = 0
        entry.white_outline_white_ratio_percent = 0.0
        assert balloon_mesh._mesh_has_expected_layers(mesh, entry, "white_outline"), (
            "白線割合0%の黒線だけのメッシュが未完成扱いになります"
        )
        entry.white_outline_white_ratio_percent = 50.0
        assert not balloon_mesh._mesh_has_expected_layers(mesh, entry, "white_outline"), (
            "白線割合ありで白線面が無いメッシュを完成扱いしています"
        )
    finally:
        bpy.data.meshes.remove(mesh)
        entry.white_outline_white_ratio_percent = 50.0


def main() -> None:
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()

        from bmanga_dev_white_outline_spacing.core import balloon as balloon_core
        from bmanga_dev_white_outline_spacing.core import effect_line as effect_line_core
        from bmanga_dev_white_outline_spacing.io import schema
        from bmanga_dev_white_outline_spacing.io import effect_line_presets
        from bmanga_dev_white_outline_spacing.operators import effect_line_gen
        from bmanga_dev_white_outline_spacing.operators import effect_line_white_outline
        from bmanga_dev_white_outline_spacing.utils import balloon_flash_effect_line_mesh

        params = bpy.context.scene.bmanga_effect_line_params
        work = bpy.context.scene.bmanga_work
        page = work.pages.add()
        entry = page.balloons.add()

        _assert_defaults(params, entry)
        _configure_white_outline(params)
        _assert_bundle_spacing(params, effect_line_gen)
        _assert_spacing_scale(effect_line_white_outline)
        _assert_black_ratio_and_length(params, effect_line_gen)
        _assert_black_inside_attenuation(params, effect_line_gen)
        _assert_bundle_placement(params, effect_line_gen)
        _assert_position_percent(params, effect_line_gen)
        _assert_schema_migration(params, effect_line_core)
        _assert_builtin_preset_defaults(params, effect_line_presets)
        _assert_saved(params, entry, effect_line_core, balloon_core, schema)
        _assert_balloon_cache_signature(entry, balloon_flash_effect_line_mesh)
        _assert_black_zero_keeps_white(entry, balloon_flash_effect_line_mesh)
        _assert_zero_white_ratio_cache(entry, balloon_flash_effect_line_mesh)
        _assert_balloon_legacy_migration(page, entry, schema, balloon_flash_effect_line_mesh)
        print("BMANGA_WHITE_OUTLINE_SPACING_GRAPH_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
