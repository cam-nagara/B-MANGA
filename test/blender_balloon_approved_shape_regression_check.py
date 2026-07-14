"""Blender実機用: 承認済みのトゲ／もやもや形状契約を数値で回帰検証する。

外部画像や一時PNGには依存せず、同じ承認形状を表す輪郭・Bezier座標、
新規フキダシの初期値、効果線との共有形状、保存互換性を検証する。
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import math
import random
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
ADDON_NAME = "bmanga_dev_approved_shape_regression"
RECT_SIZE = (51.67, 63.61)
# v0.6.166の承認形状を作る26アンカー（ローカル座標・制御点を小数9桁で
# 直列化）の固定golden。現行実装同士だけを比較して誤りを見逃さないため、
# テストコード側に独立して保持する。
FLUFFY_APPROVED_BASE_ANCHOR_SHA256 = (
    "06a8116497b8abffb675b5b37d2752abf3e793dde6210a8f16792221d12c8040"
)
# 52区間を各10分割する実装の最大弦誤差は約0.0262mm。要求値0.02mm程度
# を丸め誤差込みで固定しつつ、目視不能な0.03mm未満へ抑える。
FLUFFY_OUTLINE_HAUSDORFF_TOLERANCE_MM = 0.03
# 正規化半径の 0.2% 未満は、Bezier評価や矩形基底の角付近で生じる
# 微小な数値振動として除外する。承認済みの主山・有効な小山はこの値を
# 十分に上回るため、返しトゲやノイズを山として水増ししない。
FLUFFY_EXTREMUM_PROMINENCE = 0.002
FLUFFY_APPROVED_EXTREMA = (13, 13)
FLUFFY_MIN_ACTIVE_EXTREMA = (20, 20)
FLUFFY_APPROVED_SENTINELS = {
    0: (25.835000000, 68.831500000),
    6: (-5.015795516, 36.077849774),
    13: (25.835000000, 10.388500000),
    19: (41.148145256, 29.033324570),
    25: (29.090009796, 52.721873692),
}
GEOMETRY_VALUES = {
    "cloud_bump_width_mm": 12.79,
    "cloud_bump_width_jitter": 0.0,
    "cloud_bump_height_mm": 15.61,
    "cloud_bump_height_jitter": 0.0,
    "cloud_offset": 0.5,
    "cloud_sub_width_jitter": 0.0,
    "cloud_sub_height_jitter": 0.0,
    "jitter_seed": 0,
    "base_kind": "ellipse",
    "base_corner_radius_mm": 0.0,
}


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        ADDON_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[ADDON_NAME] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _submodule(name: str):
    return importlib.import_module(f"{ADDON_NAME}.{name}")


def _point_signature(point) -> tuple[float, float] | None:
    if point is None:
        return None
    return round(float(point[0]), 6), round(float(point[1]), 6)


def _outline_signature(result) -> tuple[tuple[tuple[float, float], ...], tuple[int, ...]]:
    points, corners = result
    return tuple(_point_signature(point) for point in points), tuple(int(index) for index in corners)


def _bezier_signature(anchors) -> tuple:
    if anchors is None:
        raise AssertionError("承認形状のBezierループが生成されていません")
    return tuple(
        (
            _point_signature(anchor.co),
            _point_signature(anchor.handle_left),
            _point_signature(anchor.handle_right),
            str(anchor.handle_left_type),
            str(anchor.handle_right_type),
        )
        for anchor in anchors
    )


def _anchor_coordinate_digest(anchors) -> str:
    canonical = ";".join(
        ",".join(
            f"{float(value):.9f}"
            for point in (anchor.co, anchor.handle_left, anchor.handle_right)
            for value in point
        )
        for anchor in anchors
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _cubic_point(anchor, next_anchor, t: float) -> tuple[float, float]:
    p0 = anchor.co
    p1 = anchor.handle_right or anchor.co
    p2 = next_anchor.handle_left or next_anchor.co
    p3 = next_anchor.co
    mt = 1.0 - float(t)
    return (
        mt**3 * p0[0]
        + 3.0 * mt**2 * t * p1[0]
        + 3.0 * mt * t**2 * p2[0]
        + t**3 * p3[0],
        mt**3 * p0[1]
        + 3.0 * mt**2 * t * p1[1]
        + 3.0 * mt * t**2 * p2[1]
        + t**3 * p3[1],
    )


def _sample_bezier_loop(anchors, samples_per_segment: int) -> list[tuple[float, float]]:
    assert anchors and samples_per_segment > 0
    points: list[tuple[float, float]] = []
    for index, anchor in enumerate(anchors):
        next_anchor = anchors[(index + 1) % len(anchors)]
        for sample in range(samples_per_segment):
            points.append(_cubic_point(anchor, next_anchor, sample / samples_per_segment))
    return points


def _point_segment_distance(point, start, end) -> float:
    dx = float(end[0]) - float(start[0])
    dy = float(end[1]) - float(start[1])
    denom = dx * dx + dy * dy
    if denom <= 1.0e-24:
        return math.hypot(float(point[0]) - float(start[0]), float(point[1]) - float(start[1]))
    t = (
        (float(point[0]) - float(start[0])) * dx
        + (float(point[1]) - float(start[1])) * dy
    ) / denom
    t = max(0.0, min(1.0, t))
    nearest = (float(start[0]) + t * dx, float(start[1]) + t * dy)
    return math.hypot(float(point[0]) - nearest[0], float(point[1]) - nearest[1])


def _directed_polyline_distance(source_points, target_points) -> float:
    target = list(target_points)
    assert len(target) >= 2
    segments = list(zip(target, target[1:] + target[:1]))
    return max(
        min(_point_segment_distance(point, start, end) for start, end in segments)
        for point in source_points
    )


def _sampled_hausdorff(a_points, b_points) -> float:
    return max(
        _directed_polyline_distance(a_points, b_points),
        _directed_polyline_distance(b_points, a_points),
    )


def _closed_area(points) -> float:
    pairs = zip(points, list(points[1:]) + [points[0]])
    return abs(sum(float(a[0]) * float(b[1]) - float(a[1]) * float(b[0]) for a, b in pairs)) * 0.5


def _assert_strictly_increasing(values, label: str, *, epsilon: float = 1.0e-8) -> None:
    for before, after in zip(values, values[1:]):
        assert float(after) - float(before) > epsilon, (
            f"{label}が単調増加していません: {before:.12f} -> {after:.12f}"
        )


def _periodic_smooth(values, radius: int = 2) -> list[float]:
    count = len(values)
    assert count > radius * 2
    width = radius * 2 + 1
    return [
        sum(float(values[(index + delta) % count]) for delta in range(-radius, radius + 1))
        / width
        for index in range(count)
    ]


def _circular_sample_distance(a: int, b: int, count: int) -> int:
    delta = abs(int(a) - int(b)) % count
    return min(delta, count - delta)


def _periodic_extrema(values, *, prominence: float) -> tuple[list[int], list[int]]:
    """閉曲線上の実在する山谷だけを、局所prominence付きで返す。"""

    smooth = _periodic_smooth(values)
    count = len(smooth)
    window = max(12, count // 80)
    min_separation = max(6, count // 180)

    def candidates(is_peak: bool) -> list[tuple[float, int]]:
        found: list[tuple[float, int]] = []
        for index, value in enumerate(smooth):
            before = smooth[(index - 1) % count]
            after = smooth[(index + 1) % count]
            is_turn = value > before and value >= after if is_peak else value < before and value <= after
            if not is_turn:
                continue
            left = [smooth[(index - step) % count] for step in range(1, window + 1)]
            right = [smooth[(index + step) % count] for step in range(1, window + 1)]
            local_prominence = (
                min(value - min(left), value - min(right))
                if is_peak
                else min(max(left) - value, max(right) - value)
            )
            if local_prominence >= prominence:
                found.append((local_prominence, index))
        kept: list[int] = []
        for _score, index in sorted(found, reverse=True):
            if all(
                _circular_sample_distance(index, other, count) >= min_separation
                for other in kept
            ):
                kept.append(index)
        return sorted(kept)

    return candidates(True), candidates(False)


def _normalized_ellipse_radii(points) -> list[float]:
    width, height = RECT_SIZE
    margin = min(max(1.0, min(width, height) * 0.05), max(1.0, min(width, height) * 0.5 - 1.0))
    center_x, center_y = width * 0.5, height * 0.5
    radius_x, radius_y = width * 0.5 - margin, height * 0.5 - margin
    return [
        math.hypot(
            (float(point[0]) - center_x) / radius_x,
            (float(point[1]) - center_y) / radius_y,
        )
        for point in points
    ]


def _rounded_rect_ray_radius(unit_x: float, unit_y: float, rx: float, ry: float, radius: float) -> float:
    unit_x, unit_y = abs(float(unit_x)), abs(float(unit_y))
    radius = max(0.0, min(float(radius), rx, ry))
    if radius <= 1.0e-12:
        return min(
            rx / unit_x if unit_x > 1.0e-12 else math.inf,
            ry / unit_y if unit_y > 1.0e-12 else math.inf,
        )
    candidates: list[float] = []
    if unit_x > 1.0e-12:
        hit = rx / unit_x
        if hit * unit_y <= ry - radius + 1.0e-9:
            candidates.append(hit)
    if unit_y > 1.0e-12:
        hit = ry / unit_y
        if hit * unit_x <= rx - radius + 1.0e-9:
            candidates.append(hit)
    corner_x, corner_y = rx - radius, ry - radius
    projection = unit_x * corner_x + unit_y * corner_y
    discriminant = projection * projection - (corner_x**2 + corner_y**2 - radius**2)
    if discriminant >= -1.0e-9:
        hit = projection + math.sqrt(max(0.0, discriminant))
        if hit * unit_x >= corner_x - 1.0e-9 and hit * unit_y >= corner_y - 1.0e-9:
            candidates.append(hit)
    assert candidates, "角丸矩形の基底半径を解決できません"
    return min(candidates)


def _normalized_rect_radii(points, corner_radius_mm: float) -> list[float]:
    width, height = RECT_SIZE
    margin = min(max(1.0, min(width, height) * 0.05), max(1.0, min(width, height) * 0.5 - 1.0))
    center_x, center_y = width * 0.5, height * 0.5
    rx, ry = width * 0.5 - margin, height * 0.5 - margin
    normalized: list[float] = []
    for point in points:
        dx, dy = float(point[0]) - center_x, float(point[1]) - center_y
        distance = math.hypot(dx, dy)
        assert distance > 1.0e-12
        base_distance = _rounded_rect_ray_radius(dx / distance, dy / distance, rx, ry, corner_radius_mm)
        normalized.append(distance / base_distance)
    return normalized


def _assert_alternating_sub_peaks(values, peak_indices) -> None:
    peak_values = [float(values[index]) for index in peak_indices]
    assert len(peak_values) >= FLUFFY_MIN_ACTIVE_EXTREMA[0]
    lower_than_neighbors = 0
    for index, value in enumerate(peak_values):
        previous = peak_values[(index - 1) % len(peak_values)]
        following = peak_values[(index + 1) % len(peak_values)]
        if value < previous and value < following:
            lower_than_neighbors += 1
    assert lower_than_neighbors >= len(peak_values) // 3, (
        "もやもやの小山が主山より低い交互構造になっていません: "
        f"peaks={len(peak_values)}, lower={lower_than_neighbors}"
    )


def _shape_kwargs(sub_width: float, sub_height: float) -> dict:
    values = dict(GEOMETRY_VALUES)
    values["cloud_sub_width_ratio"] = float(sub_width)
    values["cloud_sub_height_ratio"] = float(sub_height)
    return values


def _assert_new_entry_defaults(context, work, page, balloon_op) -> None:
    expected_sharp = {
        "thorn": True,
        "thorn-curve": True,
        "cloud": False,
        "fluffy": False,
    }
    created = {}
    for index, (shape, sharp) in enumerate(expected_sharp.items()):
        entry = balloon_op._create_balloon_entry(  # noqa: SLF001
            context,
            page,
            shape=shape,
            x=10.0 + index * 60.0,
            y=10.0,
            w=RECT_SIZE[0],
            h=RECT_SIZE[1],
        )
        assert entry is not None, f"{shape}: 新規フキダシを作成できません"
        assert bool(entry.shape_params.cloud_valley_sharp) is sharp, (
            f"{shape}: 新規作成時の『角を尖らせる』が {sharp} ではありません"
        )
        assert float(entry.shape_params.cloud_sub_width_ratio) == 30.0, (
            f"{shape}: UIの小山幅初期値が30%ではありません"
        )
        assert float(entry.shape_params.cloud_sub_height_ratio) == 50.0, (
            f"{shape}: UIの小山高初期値が50%ではありません"
        )
        created[shape] = entry

    switcher = created["cloud"]
    switcher.shape_params.cloud_valley_sharp = True
    switcher.shape = "fluffy"
    assert bool(switcher.shape_params.cloud_valley_sharp), (
        "トゲ以外の形状切替で、ユーザーが明示した『角を尖らせる』が失われています"
    )
    for shape in ("thorn", "thorn-curve"):
        switcher.shape_params.cloud_valley_sharp = False
        switcher.shape = shape
        assert bool(switcher.shape_params.cloud_valley_sharp), (
            f"{shape}: 形状切替後の『角を尖らせる』がオンではありません"
        )
        assert float(switcher.shape_params.cloud_sub_width_ratio) == 30.0
        assert float(switcher.shape_params.cloud_sub_height_ratio) == 50.0
    switcher.shape = "cloud"
    assert bool(switcher.shape_params.cloud_valley_sharp), (
        "トゲ以外へ戻したときに、共有設定の『角を尖らせる』が強制オフになっています"
    )

    # 参照を返す代わりに、後続の保存互換テスト用としてworkへ保持済みである
    # ことだけを明示的に確認する。
    assert len(page.balloons) >= len(expected_sharp)
    assert work is not None


def _fluffy_base_anchors_for_test(balloon_shapes, rect):
    opts = balloon_shapes._DynamicOpts(  # noqa: SLF001
        bump_w=GEOMETRY_VALUES["cloud_bump_width_mm"],
        bump_w_jitter=0.0,
        bump_h=GEOMETRY_VALUES["cloud_bump_height_mm"],
        bump_h_jitter=0.0,
        offset=GEOMETRY_VALUES["cloud_offset"],
        sub_w=30.0,
        sub_w_jitter=0.0,
        sub_h=50.0,
        sub_h_jitter=0.0,
        rng=random.Random(0),
        base_kind="ellipse",
        base_corner_radius_mm=0.0,
    )
    anchors = balloon_shapes._fluffy_base_anchors(rect, opts)  # noqa: SLF001
    assert anchors is not None
    return anchors


def _fluffy_bezier_signature(balloon_shapes, rect, **overrides) -> tuple:
    values = _shape_kwargs(30.0, 50.0)
    values.update(overrides)
    return _bezier_signature(balloon_shapes.bezier_loop_for_shape("fluffy", rect, **values))


def _assert_shape_quality_contract(balloon_shapes, rect) -> None:
    params_30_50 = _shape_kwargs(30.0, 50.0)
    params_50_50 = _shape_kwargs(50.0, 50.0)

    # トゲ系は現在のUI初期値30x50を保存したまま、承認済み50x50で描く。
    for shape in ("thorn", "thorn-curve"):
        current = _outline_signature(
            balloon_shapes.outline_with_corners_for_shape(shape, rect, **params_30_50)
        )
        approved = _outline_signature(
            balloon_shapes.outline_with_corners_for_shape(shape, rect, **params_50_50)
        )
        assert current == approved, f"{shape}: 30x50が承認済み50x50の輪郭と一致しません"

    current_curve = _bezier_signature(
        balloon_shapes.bezier_loop_for_shape("thorn-curve", rect, **params_30_50)
    )
    approved_curve = _bezier_signature(
        balloon_shapes.bezier_loop_for_shape("thorn-curve", rect, **params_50_50)
    )
    assert current_curve == approved_curve, "トゲ（曲線）の30x50 Bezierが承認形状と一致しません"

    # もやもや30x50は、固定goldenの承認26アンカー曲線をDe Casteljauで
    # 52アンカーへ正確に分割したもの。アンカー数ではなく密サンプル曲線を比べる。
    base_local = _fluffy_base_anchors_for_test(balloon_shapes, rect)
    assert len(base_local) == 26, f"承認済みもやもや基底が26アンカーではありません: {len(base_local)}"
    assert _anchor_coordinate_digest(base_local) == FLUFFY_APPROVED_BASE_ANCHOR_SHA256, (
        "もやもやの承認26アンカー座標が固定goldenから変化しています"
    )
    for index, expected in FLUFFY_APPROVED_SENTINELS.items():
        actual = base_local[index].co
        error = math.hypot(float(actual[0]) - expected[0], float(actual[1]) - expected[1])
        assert error <= 1.0e-8, f"もやもや承認アンカー{index}が固定座標からずれています: {error}mm"

    base_rect = [balloon_shapes._local_anchor_to_rect(rect, anchor) for anchor in base_local]  # noqa: SLF001
    final_30_50 = balloon_shapes.bezier_loop_for_shape("fluffy", rect, **params_30_50)
    assert final_30_50 is not None
    assert len(final_30_50) == 52, f"De Casteljau分割後が52アンカーではありません: {len(final_30_50)}"
    base_samples = _sample_bezier_loop(base_rect, 64)
    final_samples = _sample_bezier_loop(final_30_50, 32)
    assert len(base_samples) == len(final_samples)
    split_error = max(
        math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))
        for a, b in zip(base_samples, final_samples)
    )
    assert split_error <= 1.0e-6, (
        f"もやもや30x50のDe Casteljau分割が承認曲線を変えています: {split_error:.9f}mm"
    )

    approved_radii = _normalized_ellipse_radii(base_samples)
    approved_peaks, approved_valleys = _periodic_extrema(
        approved_radii,
        prominence=FLUFFY_EXTREMUM_PROMINENCE,
    )
    assert (len(approved_peaks), len(approved_valleys)) == FLUFFY_APPROVED_EXTREMA, (
        "固定goldenの承認もやもやが13山/13谷として検出されません: "
        f"{len(approved_peaks)}山/{len(approved_valleys)}谷"
    )

    # 小山が有効な代表値では、アンカー数が増えただけでなく輪郭そのものに
    # 主山より低い小山と対応する谷が現れることを要求する。微小なBezier振動は
    # prominenceで除外し、外向き返しトゲを山数へ混ぜない。
    active_fluffy = balloon_shapes.bezier_loop_for_shape(
        "fluffy",
        rect,
        **_shape_kwargs(50.0, 90.0),
    )
    assert active_fluffy is not None
    active_samples = _sample_bezier_loop(active_fluffy, 64)
    active_radii = _normalized_ellipse_radii(active_samples)
    active_peaks, active_valleys = _periodic_extrema(
        active_radii,
        prominence=FLUFFY_EXTREMUM_PROMINENCE,
    )
    assert len(active_peaks) >= FLUFFY_MIN_ACTIVE_EXTREMA[0], (
        f"もやもやの小山が実輪郭へ増えていません: {len(active_peaks)}山"
    )
    assert len(active_valleys) >= FLUFFY_MIN_ACTIVE_EXTREMA[1], (
        f"もやもやの小谷が実輪郭へ増えていません: {len(active_valleys)}谷"
    )
    assert len(active_peaks) <= 30 and len(active_valleys) <= 30, (
        "もやもやに返しトゲまたは微小ノイズ極値が混入しています: "
        f"{len(active_peaks)}山/{len(active_valleys)}谷"
    )
    assert abs(len(active_peaks) - len(active_valleys)) <= 1
    assert len(active_peaks) > len(approved_peaks)
    assert len(active_valleys) > len(approved_valleys)
    _assert_alternating_sub_peaks(active_radii, active_peaks)

    # 30x50は承認基準点だが、小山幅・高のUI機能は独立して有効なままにする。
    base_signature = _bezier_signature(final_30_50)
    assert base_signature != _fluffy_bezier_signature(
        balloon_shapes, rect, cloud_sub_width_ratio=50.0
    ), "もやもや30x50と50x50が同一になり、小山幅調整が失われています"
    assert base_signature != _fluffy_bezier_signature(
        balloon_shapes, rect, cloud_sub_width_ratio=40.0
    ), "もやもやの小山幅だけを変えてもBezierが変化しません"
    assert base_signature != _fluffy_bezier_signature(
        balloon_shapes, rect, cloud_sub_height_ratio=60.0
    ), "もやもやの小山高だけを変えてもBezierが変化しません"

    for jitter_field, label in (
        ("cloud_sub_width_jitter", "小山幅の乱れ"),
        ("cloud_sub_height_jitter", "小山高の乱れ"),
    ):
        jitter_values = {jitter_field: 0.45, "jitter_seed": 73}
        signature_a = _fluffy_bezier_signature(balloon_shapes, rect, **jitter_values)
        signature_repeat = _fluffy_bezier_signature(balloon_shapes, rect, **jitter_values)
        signature_other_seed = _fluffy_bezier_signature(
            balloon_shapes,
            rect,
            **{jitter_field: 0.45, "jitter_seed": 91},
        )
        assert signature_a != base_signature, f"もやもやの{label}がBezierへ反映されません"
        assert signature_a == signature_repeat, f"もやもやの{label}が同じシードで再現しません"
        assert signature_a != signature_other_seed, f"もやもやの{label}がシードを変えても変化しません"

    # 効果線用の点列も、実体Bezierを十分細かくサンプルした同じ輪郭である。
    fluffy_outline, _corners = balloon_shapes.outline_with_corners_for_shape(
        "fluffy", rect, **params_30_50
    )
    dense_final = _sample_bezier_loop(final_30_50, 64)
    outline_error = _sampled_hausdorff(fluffy_outline, dense_final)
    assert outline_error <= FLUFFY_OUTLINE_HAUSDORFF_TOLERANCE_MM, (
        f"もやもやのoutlineと最終Bezierが一致しません: Hausdorff={outline_error:.6f}mm"
    )

    # 雲は今回のトゲ／もやもや品質補正の対象外。
    assert _outline_signature(
        balloon_shapes.outline_with_corners_for_shape("cloud", rect, **params_30_50)
    ) != _outline_signature(
        balloon_shapes.outline_with_corners_for_shape("cloud", rect, **params_50_50)
    ), "雲まで小山幅30%と50%が同一になっています"
    assert _bezier_signature(
        balloon_shapes.bezier_loop_for_shape("cloud", rect, **params_30_50)
    ) != _bezier_signature(
        balloon_shapes.bezier_loop_for_shape("cloud", rect, **params_50_50)
    ), "雲の小山幅変更がBezierへ反映されていません"


def _assert_simple_approved_outlines(balloon_shapes, rect) -> None:
    from shapely.geometry import LinearRing, Polygon

    for shape in ("thorn", "thorn-curve", "fluffy"):
        values = _shape_kwargs(30.0, 50.0) if shape == "fluffy" else _shape_kwargs(50.0, 50.0)
        points, _corners = balloon_shapes.outline_with_corners_for_shape(shape, rect, **values)
        ring = LinearRing(points)
        polygon = Polygon(points)
        assert ring.is_simple, f"{shape}: 承認設定の輪郭に自己交差があります"
        assert ring.is_valid, f"{shape}: 承認設定の輪郭リングが不正です"
        assert polygon.is_valid and not polygon.is_empty, f"{shape}: 承認設定の面が不正です"


def _assert_fluffy_base_and_jitter_topology(balloon_shapes, rect) -> None:
    from shapely.geometry import LinearRing, Polygon

    cases = (
        ("ellipse-approved", _shape_kwargs(30.0, 50.0)),
        (
            "ellipse-jitter",
            {
                **_shape_kwargs(50.0, 90.0),
                "cloud_bump_width_jitter": 0.22,
                "cloud_bump_height_jitter": 0.18,
                "cloud_sub_width_jitter": 0.35,
                "cloud_sub_height_jitter": 0.30,
                "jitter_seed": 73,
            },
        ),
        (
            "rect-jitter",
            {
                **_shape_kwargs(50.0, 90.0),
                "base_kind": "rect",
                "base_corner_radius_mm": 0.0,
                "cloud_bump_width_jitter": 0.18,
                "cloud_bump_height_jitter": 0.20,
                "cloud_sub_width_jitter": 0.30,
                "cloud_sub_height_jitter": 0.28,
                "jitter_seed": 91,
            },
        ),
        (
            "rounded-rect-jitter",
            {
                **_shape_kwargs(45.0, 75.0),
                "base_kind": "rect",
                "base_corner_radius_mm": 6.0,
                "cloud_bump_width_jitter": 0.20,
                "cloud_bump_height_jitter": 0.16,
                "cloud_sub_width_jitter": 0.32,
                "cloud_sub_height_jitter": 0.25,
                "jitter_seed": 117,
            },
        ),
    )
    for label, values in cases:
        points, _corners = balloon_shapes.outline_with_corners_for_shape(
            "fluffy", rect, **values
        )
        ring = LinearRing(points)
        polygon = Polygon(points)
        assert ring.is_simple and ring.is_valid, f"{label}: 輪郭に自己交差があります"
        assert polygon.geom_type == "Polygon" and polygon.is_valid and not polygon.is_empty, (
            f"{label}: もやもや面が単一の有効Polygonではありません"
        )
        assert len(polygon.interiors) == 0, f"{label}: もやもや本体に不要な穴があります"
        outer = polygon.buffer(3.1, join_style=1)
        assert outer.geom_type == "Polygon" and outer.is_valid and not outer.is_empty, (
            f"{label}: 外側3.1mmオフセットが単一Polygonではありません: {outer.geom_type}"
        )
        assert len(outer.interiors) == 0, f"{label}: 外側3.1mmオフセットに輪状の穴があります"


def _rect_fluffy_regression_values(corner_radius_mm: float, *, seed: int = 91) -> dict:
    return {
        **_shape_kwargs(50.0, 90.0),
        "base_kind": "rect",
        "base_corner_radius_mm": float(corner_radius_mm),
        "cloud_bump_width_jitter": 0.18,
        "cloud_bump_height_jitter": 0.20,
        "cloud_sub_width_jitter": 0.30,
        "cloud_sub_height_jitter": 0.28,
        "jitter_seed": int(seed),
    }


def _assert_dense_bezier_topology(anchors, label: str) -> None:
    from shapely.geometry import LinearRing, Polygon

    assert anchors is not None and len(anchors) > 4, f"{label}: 素楕円4アンカーへfallbackしました"
    dense = _sample_bezier_loop(anchors, 64)
    ring = LinearRing(dense)
    polygon = Polygon(dense)
    assert ring.is_simple and ring.is_valid, f"{label}: 最終Bezierが自己交差しています"
    assert polygon.geom_type == "Polygon" and polygon.is_valid and not polygon.is_empty
    assert len(polygon.interiors) == 0, f"{label}: 最終Bezier面に不要な穴があります"
    outer = polygon.buffer(3.1, join_style=1)
    assert outer.geom_type == "Polygon" and outer.is_valid and not outer.is_empty, (
        f"{label}: 外側3.1mmが単一Polygonではありません: {outer.geom_type}"
    )
    assert len(outer.interiors) == 0, f"{label}: 外側3.1mmに不要な穴があります"


def _assert_rect_fluffy_preserves_requested_base(balloon_shapes, rect) -> None:
    for sub_width, sub_height in ((30.0, 50.0), (50.0, 90.0)):
        signatures = {}
        for label, base_kind, corner_radius in (
            ("ellipse", "ellipse", 0.0),
            ("rect-radius-0", "rect", 0.0),
            ("rect-radius-6", "rect", 6.0),
        ):
            values = _shape_kwargs(sub_width, sub_height)
            values.update(base_kind=base_kind, base_corner_radius_mm=corner_radius)
            if base_kind == "rect":
                opts = balloon_shapes._DynamicOpts(  # noqa: SLF001
                    bump_w=12.79, bump_w_jitter=0.0, bump_h=15.61, bump_h_jitter=0.0,
                    offset=0.5, sub_w=sub_width, sub_w_jitter=0.0,
                    sub_h=sub_height, sub_h_jitter=0.0, rng=random.Random(0),
                    base_kind="rect", base_corner_radius_mm=corner_radius,
                )
                assert balloon_shapes._fluffy_local_anchors(  # noqa: SLF001
                    rect, opts, allow_rect_radius_fallback=False
                ) is not None, f"{label}: 指定した矩形角半径のまま安全な曲線を作れません"
            anchors = balloon_shapes.bezier_loop_for_shape("fluffy", rect, **values)
            case = f"{label}-{sub_width:g}x{sub_height:g}"
            _assert_dense_bezier_topology(anchors, case)
            signatures[label] = _bezier_signature(anchors)
        assert len(set(signatures.values())) == 3, (
            f"{sub_width:g}x{sub_height:g}: ellipse/矩形R0/矩形R6のBezierが区別されません"
        )


def _assert_rect_fluffy_does_not_fallback_to_plain_ellipse(balloon_shapes, rect) -> None:
    from shapely.geometry import LinearRing, Polygon

    radius_signatures = []
    for corner_radius in (0.0, 6.0):
        label = f"rect-radius-{corner_radius:g}"
        values = _rect_fluffy_regression_values(corner_radius)
        anchors = balloon_shapes.bezier_loop_for_shape("fluffy", rect, **values)
        assert anchors is not None and len(anchors) > 4, (
            f"{label}: 最終Bezierが素楕円4アンカーへfallbackしました"
        )
        signature = _bezier_signature(anchors)
        radius_signatures.append(signature)
        ellipse_values = dict(values)
        ellipse_values.update(base_kind="ellipse", base_corner_radius_mm=0.0)
        assert signature != _bezier_signature(
            balloon_shapes.bezier_loop_for_shape("fluffy", rect, **ellipse_values)
        ), f"{label}: 指定矩形が同条件の楕円へfallbackしました"
        assert signature != _bezier_signature(
            balloon_shapes.bezier_loop_for_shape(
                "fluffy", rect, **_rect_fluffy_regression_values(corner_radius, seed=92)
            )
        ), f"{label}: jitter seed 91と92が同じBezierです"

        no_sub_values = dict(values)
        no_sub_values.update(
            cloud_sub_width_ratio=0.0,
            cloud_sub_height_ratio=0.0,
            cloud_sub_width_jitter=0.0,
            cloud_sub_height_jitter=0.0,
        )
        no_sub = balloon_shapes.bezier_loop_for_shape("fluffy", rect, **no_sub_values)
        assert no_sub is not None and signature != _bezier_signature(no_sub), (
            f"{label}: 小山50x90と小山0x0が同じBezierです"
        )

        dense = _sample_bezier_loop(anchors, 64)
        no_sub_dense = _sample_bezier_loop(no_sub, 64)
        active_radii = _normalized_rect_radii(dense, corner_radius)
        no_sub_radii = _normalized_rect_radii(no_sub_dense, corner_radius)
        active_peaks, active_valleys = _periodic_extrema(
            active_radii, prominence=FLUFFY_EXTREMUM_PROMINENCE
        )
        base_peaks, base_valleys = _periodic_extrema(
            no_sub_radii, prominence=FLUFFY_EXTREMUM_PROMINENCE
        )
        assert len(active_peaks) >= max(20, len(base_peaks) + 6), (
            f"{label}: 小山のprominence山が増えていません: {len(base_peaks)}→{len(active_peaks)}"
        )
        assert len(active_valleys) >= max(20, len(base_valleys) + 6), (
            f"{label}: 小山のprominence谷が増えていません: {len(base_valleys)}→{len(active_valleys)}"
        )
        assert len(active_peaks) <= 40 and len(active_valleys) <= 40
        assert abs(len(active_peaks) - len(active_valleys)) <= 1

        ring = LinearRing(dense)
        polygon = Polygon(dense)
        assert ring.is_simple and ring.is_valid, f"{label}: 最終Bezierが自己交差しています"
        assert polygon.geom_type == "Polygon" and polygon.is_valid and not polygon.is_empty
        assert len(polygon.interiors) == 0, f"{label}: 最終Bezier面に不要な穴があります"
        outer = polygon.buffer(3.1, join_style=1)
        assert outer.geom_type == "Polygon" and outer.is_valid and not outer.is_empty, (
            f"{label}: 外側3.1mmが単一Polygonではありません: {outer.geom_type}"
        )
        assert len(outer.interiors) == 0, f"{label}: 外側3.1mmに不要な穴があります"
    assert radius_signatures[0] != radius_signatures[1], "矩形の角半径0mmと6mmが同じBezierです"


def _assert_fluffy_signed_sub_parameter_monotonicity(balloon_shapes, rect) -> None:
    sweeps = (
        ("小山幅", (1.0, 15.0, 29.0, 30.0, 31.0, 60.0, 100.0), 30.0, 50.0, True),
        ("小山高", (1.0, 10.0, 25.0, 49.0, 50.0, 51.0, 75.0, 100.0), 50.0, 30.0, False),
    )
    for label, values, baseline, fixed, change_width in sweeps:
        signatures = []
        areas = {}
        for value in values:
            width, height = (value, fixed) if change_width else (fixed, value)
            anchors = balloon_shapes.bezier_loop_for_shape(
                "fluffy", rect, **_shape_kwargs(width, height)
            )
            assert anchors is not None and len(anchors) > 4
            signatures.append(_bezier_signature(anchors))
            areas[value] = _closed_area(_sample_bezier_loop(anchors, 64))
        assert len(set(signatures)) == len(values), f"{label}: 指定値ごとのBezierが一意ではありません"

        lower = [value for value in values if value < baseline]
        upper = [value for value in values if value > baseline]
        base_area = areas[baseline]
        assert all(areas[value] < base_area for value in lower), (
            f"{label}: 基準未満が30/50基準と同じ側に変化していません"
        )
        assert all(areas[value] > base_area for value in upper), (
            f"{label}: 基準超過が30/50基準から外向きに離れていません"
        )
        _assert_strictly_increasing([areas[value] for value in (*lower, baseline)], f"{label}・下側")
        _assert_strictly_increasing(
            [areas[value] - base_area for value in upper],
            f"{label}・上側の基準差",
        )


def _assert_fluffy_zero_auto_compatibility(balloon_shapes, rect) -> None:
    """片側0%を自動50%として扱う既存契約を維持する。"""

    for actual, automatic in (((50.0, 50.0), (50.0, 0.0)), ((50.0, 75.0), (0.0, 75.0))):
        expected = balloon_shapes.bezier_loop_for_shape(
            "fluffy", rect, **_shape_kwargs(*actual)
        )
        result = balloon_shapes.bezier_loop_for_shape(
            "fluffy", rect, **_shape_kwargs(*automatic)
        )
        assert _bezier_signature(result) == _bezier_signature(expected), (
            f"もやもや{automatic[0]:g}x{automatic[1]:g}が自動50%互換ではありません"
        )


def _strict_offset_band(body_poly, signed_offset_m, width_m, *, peaks_rounded: bool):
    join_style = 1 if signed_offset_m > 0.0 and peaks_rounded else 2
    half = width_m * 0.5
    outer = body_poly.buffer(signed_offset_m + half, join_style=join_style, mitre_limit=50.0)
    inner = body_poly.buffer(signed_offset_m - half, join_style=join_style, mitre_limit=50.0)
    return outer.difference(inner)


def _assert_actual_four_line_topology(balloon_shapes, line_mesh, rect) -> None:
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    specs = (
        ("main", 0.8, 1.6),
        ("multi-1", 2.8, 1.0),
        ("multi-2", 4.5, 1.0),
        ("multi-3", 6.2, 1.0),
    )
    for shape in ("thorn", "thorn-curve", "fluffy"):
        values = _shape_kwargs(30.0, 50.0)
        points, _corners = balloon_shapes.outline_with_corners_for_shape(shape, rect, **values)
        samples = [(float(x) * 0.001, float(y) * 0.001, 1.0) for x, y in points]
        body_poly = line_mesh._build_body_polygon(samples)  # noqa: SLF001
        assert body_poly is not None and body_poly.geom_type == "Polygon" and body_poly.is_valid
        assert len(body_poly.interiors) == 0, f"{shape}: 本体輪郭に不要な穴があります"
        peaks_rounded = shape == "fluffy"
        bands = []
        for label, signed_mm, width_mm in specs:
            if label == "main":
                result = line_mesh._stroke_band_outside_union(  # noqa: SLF001
                    samples,
                    line_width_m=width_mm * 0.001,
                    valley_sharp=True,
                    peaks_rounded=peaks_rounded,
                )
            else:
                result = line_mesh.build_offset_band_polygon(
                    samples,
                    signed_offset_m=signed_mm * 0.001,
                    band_width_m=width_mm * 0.001,
                    valley_sharp=True,
                    peaks_rounded=peaks_rounded,
                    _body_poly=body_poly,
                )
            assert result is not None, f"{shape}/{label}: 実多重線経路が帯を生成できません"
            if shape == "thorn-curve":
                result = line_mesh._curve_thorn_peak_polygons(  # noqa: SLF001
                    SimpleNamespace(shape=shape), True, [result], samples)[0]
            actual = Polygon(result[0], result[1])
            strict = _strict_offset_band(
                body_poly,
                signed_mm * 0.001,
                width_mm * 0.001,
                peaks_rounded=peaks_rounded,
            )
            assert strict.geom_type == "Polygon", (
                f"{shape}/{label}: オフセット帯が余分な部品へ分裂しました: {strict.geom_type}"
            )
            assert strict.is_valid and not strict.is_empty
            assert len(strict.interiors) == 1, (
                f"{shape}/{label}: 閉線の内側以外に輪・穴があります: {len(strict.interiors)}"
            )
            assert actual.is_valid and len(actual.interiors) == 1
            if shape != "thorn-curve":
                assert abs(actual.area - strict.area) <= max(1.0e-12, strict.area * 1.0e-8)
            bands.append(actual)
        for index, first in enumerate(bands):
            for second in bands[index + 1 :]:
                assert first.disjoint(second), f"{shape}: 4線の帯同士が重なっています"
                assert first.distance(second) >= 0.00065, f"{shape}: 多重線の間隔が失われています"
        combined = unary_union(bands)
        assert combined.geom_type == "MultiPolygon" and len(combined.geoms) == 4, (
            f"{shape}: 主線+多重線3本が独立した4線になっていません"
        )


def _effect_params(shape: str, prefix: str, *, sub_width: float, sub_height: float):
    values = {
        f"{prefix}_shape": shape,
        f"{prefix}_corner_type": "square",
        f"{prefix}_rounded_corner_enabled": False,
        f"{prefix}_cloud_bump_width_mm": GEOMETRY_VALUES["cloud_bump_width_mm"],
        f"{prefix}_cloud_bump_width_jitter": 0.0,
        f"{prefix}_cloud_bump_height_mm": GEOMETRY_VALUES["cloud_bump_height_mm"],
        f"{prefix}_cloud_bump_height_jitter": 0.0,
        f"{prefix}_cloud_offset_percent": 50.0,
        f"{prefix}_cloud_sub_width_ratio": float(sub_width),
        f"{prefix}_cloud_sub_width_jitter": 0.0,
        f"{prefix}_cloud_sub_height_ratio": float(sub_height),
        f"{prefix}_cloud_sub_height_jitter": 0.0,
        "rotation_deg": 0.0,
    }
    return SimpleNamespace(**values)


def _assert_effect_line_shared_geometry(balloon_shapes, effect_line_gen, rect) -> None:
    center = (rect.x + rect.width * 0.5, rect.y + rect.height * 0.5)
    for prefix in ("start", "end"):
        for shape in ("thorn", "thorn-curve", "fluffy"):
            # とくにもやもやは現在のUI初期値30x50で、フキダシ本体と
            # 効果線の始点・終点が同じ点列を共有することを固定する。
            # 効果線の実UI初期値は小山幅30%・小山高0%で、0%は自動50%。
            sub_width, sub_height = (30.0, 0.0)
            params = _effect_params(shape, prefix, sub_width=sub_width, sub_height=sub_height)
            actual = _outline_signature(
                effect_line_gen._shape_outline_with_corners(  # noqa: SLF001
                    params,
                    prefix,
                    rect,
                    center,
                    seed=0,
                )
            )
            direct_values = _shape_kwargs(sub_width, sub_height)
            direct_values.pop("base_kind")
            direct_values.pop("base_corner_radius_mm")
            expected = _outline_signature(
                balloon_shapes.outline_with_corners_for_shape(shape, rect, **direct_values)
            )
            assert actual == expected, f"効果線{prefix}の{shape}がフキダシ輪郭と一致しません"


def _assert_sharp_schema_roundtrip(page, balloon_op, schema, context) -> None:
    for index, shape in enumerate(("thorn", "thorn-curve")):
        explicit_off = balloon_op._create_balloon_entry(  # noqa: SLF001
            context,
            page,
            shape=shape,
            x=10.0 + index * 60.0,
            y=90.0,
            w=RECT_SIZE[0],
            h=RECT_SIZE[1],
        )
        assert bool(explicit_off.shape_params.cloud_valley_sharp), (
            f"{shape}: 新規既定値がONではありません"
        )
        explicit_off.shape_params.cloud_valley_sharp = False
        payload = schema.balloon_entry_to_dict(explicit_off)
        assert payload["shapeParams"]["cloudValleySharp"] is False

        restored = page.balloons.add()
        schema.balloon_entry_from_dict(restored, copy.deepcopy(payload))
        assert restored.shape == shape
        assert bool(restored.shape_params.cloud_valley_sharp) is False, (
            f"{shape}: 明示保存した『角を尖らせるOFF』が読込時にONへ戻っています"
        )

        legacy_payload = copy.deepcopy(payload)
        legacy_payload["shapeParams"].pop("cloudValleySharp", None)
        restored_legacy = page.balloons.add()
        schema.balloon_entry_from_dict(restored_legacy, legacy_payload)
        assert restored_legacy.shape == shape
        assert bool(restored_legacy.shape_params.cloud_valley_sharp) is False, (
            f"{shape}: cloudValleySharpを持たない旧データが読込時にONへ変わっています"
        )


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_approved_shape_regression_"))
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "ApprovedShapeRegression.bmanga"))
        assert result == {"FINISHED"}, f"一時作品を作成できません: {result}"

        balloon_op = _submodule("operators.balloon_op")
        balloon_shapes = _submodule("utils.balloon_shapes")
        balloon_line_mesh = _submodule("utils.balloon_line_mesh")
        effect_line_gen = _submodule("operators.effect_line_gen")
        geom = _submodule("utils.geom")
        schema = _submodule("io.schema")
        get_work = _submodule("core.work").get_work

        context = bpy.context
        work = get_work(context)
        assert work is not None and len(work.pages) > 0, "一時作品のページがありません"
        page = work.pages[0]
        rect = geom.Rect(0.0, 0.0, RECT_SIZE[0], RECT_SIZE[1])

        _assert_new_entry_defaults(context, work, page, balloon_op)
        _assert_shape_quality_contract(balloon_shapes, rect)
        _assert_simple_approved_outlines(balloon_shapes, rect)
        _assert_fluffy_base_and_jitter_topology(balloon_shapes, rect)
        _assert_rect_fluffy_preserves_requested_base(balloon_shapes, rect)
        _assert_rect_fluffy_does_not_fallback_to_plain_ellipse(balloon_shapes, rect)
        _assert_fluffy_signed_sub_parameter_monotonicity(balloon_shapes, rect)
        _assert_fluffy_zero_auto_compatibility(balloon_shapes, rect)
        _assert_actual_four_line_topology(balloon_shapes, balloon_line_mesh, rect)
        _assert_effect_line_shared_geometry(balloon_shapes, effect_line_gen, rect)
        _assert_sharp_schema_roundtrip(page, balloon_op, schema, context)

        print("BMANGA_BALLOON_APPROVED_SHAPE_REGRESSION_CHECK_OK")
    finally:
        try:
            if module is not None:
                module.unregister()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
