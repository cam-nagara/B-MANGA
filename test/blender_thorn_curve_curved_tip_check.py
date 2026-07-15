"""Blender実機用: トゲ（曲線）の線外周が先端まで曲線になる契約を検証する。

実行例:
  blender.exe --background --factory-startup --python-exit-code 1 \
    --python test/blender_thorn_curve_curved_tip_check.py
"""

from __future__ import annotations

import importlib
import importlib.util
import math
import statistics
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
ADDON_NAME = "bmanga_dev_thorn_curve_curved_tip"
RECT_SIZE_MM = (51.67, 63.61)
LINE_SPECS_MM = (
    ("main", 1.6, 0.0),
    ("multi-1", 3.3, 2.3),
    ("multi-2", 5.0, 4.0),
    ("multi-3", 6.7, 5.7),
)
SHAPE_VALUES = {
    "cloud_bump_width_mm": 12.79,
    "cloud_bump_width_jitter": 0.0,
    "cloud_bump_height_mm": 15.61,
    "cloud_bump_height_jitter": 0.0,
    "cloud_offset": 0.5,
    "cloud_sub_width_ratio": 30.0,
    "cloud_sub_width_jitter": 0.0,
    "cloud_sub_height_ratio": 50.0,
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


def _distance(a, b) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _rings_signature(rings) -> tuple:
    return tuple(
        (
            tuple((round(float(x), 12), round(float(y), 12)) for x, y in outer),
            tuple(
                tuple((round(float(x), 12), round(float(y), 12)) for x, y in hole)
                for hole in holes
            ),
        )
        for outer, holes in rings
    )


def _open_test_ring(ring):
    points = list(ring)
    if len(points) >= 2 and _distance(points[0], points[-1]) <= 1.0e-12:
        points.pop()
    return points


def _canonical_ring(ring) -> tuple:
    points = [
        (round(float(x), 10), round(float(y), 10))
        for x, y in _open_test_ring(ring)
    ]
    variants = []
    for ordered in (points, list(reversed(points))):
        start = min(range(len(ordered)), key=lambda index: ordered[index])
        variants.append(tuple(ordered[start:] + ordered[:start]))
    return min(variants)


def _legacy_tip_triplets(ring, expected_count: int | None):
    lengths = [
        _distance(ring[index], ring[(index + 1) % len(ring)])
        for index in range(len(ring))
    ]
    baseline = statistics.median(length for length in lengths if length > 1.0e-9)
    tips = []
    ratios = []
    for index, point in enumerate(ring):
        before = lengths[(index - 1) % len(ring)]
        after = lengths[index]
        before_shoulder = max(lengths[(index - 2) % len(ring)], 1.0e-9)
        after_shoulder = max(lengths[(index + 1) % len(ring)], 1.0e-9)
        ratios.append(min(before / before_shoulder, after / after_shoulder))
        if before > before_shoulder * 2.35 and after > after_shoulder * 2.35:
            tips.append(
                (ring[(index - 1) % len(ring)], point, ring[(index + 1) % len(ring)])
            )
    if expected_count is not None:
        assert len(tips) == expected_count, (
            f"旧mitre先端を全て検出できません: {len(tips)}/{expected_count}, "
            f"median={baseline:.4f}mm, max={max(lengths):.4f}mm, ratio={max(ratios):.4f}"
        )
    return tips


def _nearest_index(ring, point, tolerance_mm: float = 0.05) -> int:
    index = min(range(len(ring)), key=lambda candidate: _distance(ring[candidate], point))
    error = _distance(ring[index], point)
    assert error <= tolerance_mm, f"旧mitre先端位置が維持されていません: {error:.6f}mm"
    return index


def _cyclic_path(ring, start_index: int, end_index: int, direction: int):
    path = [ring[start_index]]
    index = start_index
    for _step in range(len(ring)):
        index = (index + direction) % len(ring)
        path.append(ring[index])
        if index == end_index:
            return path
    raise AssertionError("先端から肩までの経路を解決できません")


def _shorter_path(ring, start, end):
    forward = _cyclic_path(ring, start, end, 1)
    backward = _cyclic_path(ring, start, end, -1)
    return min((forward, backward), key=len)


def _point_to_chord_distance(point, start, end) -> float:
    dx, dy = float(end[0]) - float(start[0]), float(end[1]) - float(start[1])
    length = math.hypot(dx, dy)
    assert length > 1.0e-9
    cross = (
        dx * (float(point[1]) - float(start[1]))
        - dy * (float(point[0]) - float(start[0]))
    )
    return abs(cross) / length


def _assert_curved_side(path, label: str) -> None:
    assert len(path) >= 4, f"{label}: 先端側面に曲線用の途中点がありません: {len(path)}点"
    chord = _distance(path[0], path[-1])
    required = max(0.05, chord * 0.004)
    deviation = max(
        _point_to_chord_distance(point, path[0], path[-1]) for point in path[1:-1]
    )
    assert deviation >= required, (
        f"{label}: 先端側面がまだ直線です: deviation={deviation:.6f}mm, "
        f"required={required:.6f}mm"
    )


def _shoulder_turn_degrees(ring, shoulder) -> float:
    points = _open_test_ring(ring)
    index = _nearest_index(points, shoulder)
    previous = points[(index - 1) % len(points)]
    following = points[(index + 1) % len(points)]
    incoming = (
        float(shoulder[0]) - float(previous[0]),
        float(shoulder[1]) - float(previous[1]),
    )
    outgoing = (
        float(following[0]) - float(shoulder[0]),
        float(following[1]) - float(shoulder[1]),
    )
    incoming_len = math.hypot(*incoming)
    outgoing_len = math.hypot(*outgoing)
    assert incoming_len > 1.0e-9 and outgoing_len > 1.0e-9
    dot = (
        incoming[0] * outgoing[0] + incoming[1] * outgoing[1]
    ) / (incoming_len * outgoing_len)
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


def _assert_ring_tip_contract(
    old_ring, new_ring, expected_count: int, label: str, *, tips_required: bool = True
) -> None:
    old_ring = _open_test_ring(old_ring)
    new_ring = _open_test_ring(new_ring)
    triplets = _legacy_tip_triplets(old_ring, expected_count if tips_required else None)
    if not triplets:
        assert _canonical_ring(old_ring) == _canonical_ring(new_ring), (
            f"{label}: mitre先端がない輪郭を変更しました"
        )
        return
    assert len(triplets) == expected_count, (
        f"{label}: mitre先端数が不正です: {len(triplets)}/{expected_count}"
    )
    for number, (old_left, old_tip, old_right) in enumerate(triplets):
        tip_index = _nearest_index(new_ring, old_tip)
        left = _shorter_path(new_ring, tip_index, _nearest_index(new_ring, old_left))
        right = _shorter_path(new_ring, tip_index, _nearest_index(new_ring, old_right))
        _assert_curved_side(left, f"{label}/tip-{number + 1}/left")
        _assert_curved_side(right, f"{label}/tip-{number + 1}/right")
        left_turn = _shoulder_turn_degrees(new_ring, old_left)
        right_turn = _shoulder_turn_degrees(new_ring, old_right)
        assert left_turn <= 1.0, (
            f"{label}/tip-{number + 1}/left: 肩でカクついています: {left_turn:.4f}°"
        )
        assert right_turn <= 1.0, (
            f"{label}/tip-{number + 1}/right: 肩でカクついています: {right_turn:.4f}°"
        )


def _assert_tip_contract(legacy, curved, expected_count: int, label: str) -> None:
    assert len(legacy) == len(curved) == 1, f"{label}: 帯が単一Polygonではありません"
    old_outer, old_holes = legacy[0]
    new_outer, new_holes = curved[0]
    assert len(old_holes) == len(new_holes), f"{label}: 穴数が変わりました"
    _assert_ring_tip_contract(old_outer, new_outer, expected_count, f"{label}/outer")
    for hole_index, (old_hole, new_hole) in enumerate(zip(old_holes, new_holes)):
        if _canonical_ring(old_hole) != _canonical_ring(new_hole):
            _assert_ring_tip_contract(
                old_hole,
                new_hole,
                expected_count,
                f"{label}/hole-{hole_index + 1}",
                tips_required=False,
            )


def _assert_valid_band(rings, label: str) -> None:
    from shapely.geometry import Polygon

    assert len(rings) == 1, f"{label}: 不要な帯部品が発生しました: {len(rings)}"
    outer, holes = rings[0]
    polygon = Polygon(outer, holes)
    assert polygon.is_valid and not polygon.is_empty, f"{label}: 自己交差した無効Polygonです"
    assert len(polygon.interiors) == 1, f"{label}: 穴数が1ではありません: {len(polygon.interiors)}"


def _assert_band(tail_boolean, curve_helper, outline, corner_count: int, spec):
    label, outer_mm, inner_mm = spec
    legacy = tail_boolean.mitre_band_polygons(outline, outer_mm, inner_mm, sharp=True)
    explicit_off = tail_boolean.mitre_band_polygons(
        outline,
        outer_mm,
        inner_mm,
        sharp=True,
        curve_thorn_peaks=False,
        curve_reference_points=outline,
    )
    curved = tail_boolean.mitre_band_polygons(
        outline,
        outer_mm,
        inner_mm,
        sharp=True,
        curve_thorn_peaks=True,
        curve_reference_points=outline,
    )
    direct = curve_helper.curve_thorn_peak_band_polygons(legacy, outline)
    assert _rings_signature(explicit_off) == _rings_signature(legacy), (
        f"{label}: flag=Falseが従来結果を変えました"
    )
    assert _rings_signature(direct) == _rings_signature(curved), (
        f"{label}: 公開経路と純粋helperが不一致です"
    )
    _assert_valid_band(curved, label)
    _assert_tip_contract(legacy, curved, corner_count, label)
    return curved


def _assert_disabled_contract(tail_boolean, outline) -> None:
    rounded = tail_boolean.mitre_band_polygons(outline, 1.6, 0.0, sharp=False)
    rounded_flag = tail_boolean.mitre_band_polygons(
        outline,
        1.6,
        0.0,
        sharp=False,
        curve_thorn_peaks=True,
        curve_reference_points=outline,
    )
    assert _rings_signature(rounded_flag) == _rings_signature(rounded), (
        "角を尖らせるOFFでトゲ曲線専用加工が適用されました"
    )


def _assert_tail_is_not_curve_target(tail_boolean, curve_helper, outline) -> None:
    """本体と同じ方向へ伸びる鋭いしっぽを、本体の山と誤認しないこと。"""
    center_y = RECT_SIZE_MM[1] * 0.5
    tail = [
        (RECT_SIZE_MM[0] - 4.0, center_y - 5.0),
        (RECT_SIZE_MM[0] + 35.0, center_y),
        (RECT_SIZE_MM[0] - 4.0, center_y + 5.0),
    ]
    combined, changed = tail_boolean.combine_body_with_tail_polygons(outline, [tail])
    assert changed and combined is not None, "しっぽ結合の回帰ケースを作成できません"
    merged = [(float(x), float(y)) for x, y in combined.exterior.coords[:-1]]
    legacy = tail_boolean.mitre_band_polygons(merged, 1.6, 0.0, sharp=True)
    curved = curve_helper.curve_thorn_peak_band_polygons(legacy, outline)
    assert _rings_signature(curved) != _rings_signature(legacy), (
        "しっぽ結合後に本体のトゲが曲線化されません"
    )
    far_x = RECT_SIZE_MM[0] + 20.0
    old_tail = sorted(
        (round(x, 10), round(y, 10))
        for outer, _holes in legacy for x, y in outer if x >= far_x
    )
    new_tail = sorted(
        (round(x, 10), round(y, 10))
        for outer, _holes in curved for x, y in outer if x >= far_x
    )
    assert old_tail and old_tail == new_tail, (
        "本体の曲線化が鋭いしっぽ先端へ誤適用されました"
    )


def _assert_outer_edge_shared_boundary(tail_boolean, outline) -> None:
    main = tail_boolean.mitre_band_polygons(
        outline,
        1.6,
        0.0,
        sharp=True,
        curve_thorn_peaks=True,
        curve_reference_points=outline,
    )
    edge = tail_boolean.mitre_band_polygons(
        outline,
        2.2,
        1.6,
        sharp=True,
        curve_thorn_peaks=True,
        curve_reference_points=outline,
        curve_thorn_holes=True,
    )
    assert len(main) == len(edge) == 1 and len(edge[0][1]) == 1
    assert _canonical_ring(main[0][0]) == _canonical_ring(edge[0][1][0]), (
        "外側フチの内周が主線外周と一致せず、隙間または重なりが発生します"
    )
    _assert_valid_band(edge, "outer-edge")


def _assert_viewport_gate(line_mesh, curve_helper, tail_boolean, outline) -> None:
    legacy = tail_boolean.mitre_band_polygons(outline, 1.6, 0.0, sharp=True)
    expected = curve_helper.curve_thorn_peak_band_polygons(legacy, outline)
    curved_entry = SimpleNamespace(shape="thorn-curve")
    actual = line_mesh._curve_thorn_peak_polygons(  # noqa: SLF001
        curved_entry, True, legacy, outline
    )
    assert _rings_signature(actual) == _rings_signature(expected), (
        "viewport経路がトゲ曲線＋角を尖らせるONへ曲線キャップを適用しません"
    )
    for shape in ("thorn", "cloud", "fluffy", "rect"):
        entry = SimpleNamespace(shape=shape)
        unchanged = line_mesh._curve_thorn_peak_polygons(  # noqa: SLF001
            entry, True, legacy, outline
        )
        assert _rings_signature(unchanged) == _rings_signature(legacy), (
            f"viewport経路が非対象形状 {shape} へトゲ曲線専用加工を適用しました"
        )
    disabled = line_mesh._curve_thorn_peak_polygons(  # noqa: SLF001
        curved_entry, False, legacy, outline
    )
    assert _rings_signature(disabled) == _rings_signature(legacy), (
        "viewport経路が角を尖らせるOFFへ曲線キャップを適用しました"
    )


def _assert_export_gate(export_balloon, curve_helper, tail_boolean, outline) -> None:
    legacy = tail_boolean.mitre_band_polygons(outline, 1.6, 0.0, sharp=True)
    expected = curve_helper.curve_thorn_peak_band_polygons(legacy, outline)
    actual = export_balloon._mitre_band_polygons_mm(  # noqa: SLF001
        outline, 1.6, 0.0, sharp=True, curve_thorn_peaks=True,
        curve_reference_points=outline,
    )
    assert _rings_signature(actual) == _rings_signature(expected), (
        "ページ出力経路がviewportと同じ曲線キャップを使用しません"
    )


def _new_export_entry(page, line_style: str):
    entry = page.balloons.add()
    entry.id = f"thorn_curve_bbox_{line_style}"
    entry.shape = "thorn-curve"
    entry.x_mm, entry.y_mm = 20.0, 20.0
    entry.width_mm, entry.height_mm = RECT_SIZE_MM
    entry.line_style = line_style
    entry.line_width_mm = 1.6
    entry.line_color = (0.0, 0.0, 0.0, 1.0)
    entry.fill_opacity = 0.0
    entry.opacity = 100.0
    entry.multi_line_count = 3
    entry.multi_line_direction = "outside"
    entry.multi_line_width_mm = 1.0
    entry.multi_line_spacing_mm = 0.7
    entry.multi_line_width_scale_percent = 100.0
    entry.multi_line_spacing_scale_percent = 100.0
    entry.thorn_multi_line_valley_width_pct = 100.0
    entry.thorn_multi_line_peak_width_pct = 100.0
    entry.outer_white_margin_enabled = True
    entry.outer_white_margin_width_mm = 1.2
    entry.inner_white_margin_enabled = True
    entry.inner_white_margin_width_mm = 0.8
    params = entry.shape_params
    params.cloud_bump_width_mm = SHAPE_VALUES["cloud_bump_width_mm"]
    params.cloud_bump_height_mm = SHAPE_VALUES["cloud_bump_height_mm"]
    params.cloud_offset_percent = SHAPE_VALUES["cloud_offset"] * 100.0
    params.cloud_sub_width_ratio = 50.0
    params.cloud_sub_height_ratio = 50.0
    params.cloud_valley_sharp = True
    params.dynamic_shape_base_kind = "ellipse"
    return entry


def _assert_export_fringe_styles(export_balloon, page) -> None:
    """非band線種でも、従来どおり内外フチを生成して同じ帯を描くこと。"""
    original_mitre = export_balloon._mitre_band_polygons_mm  # noqa: SLF001
    original_composite = export_balloon._composite_patches_px  # noqa: SLF001
    for line_style in ("dashed", "dotted", "shape", "image"):
        while len(page.balloons):
            page.balloons.remove(0)
        entry = _new_export_entry(page, line_style)
        generated_fringe_ids = set()
        drawn_ids = set()

        def wrapped_mitre(*args, **kwargs):
            result = original_mitre(*args, **kwargs)
            offsets = (round(float(args[1]), 6), round(float(args[2]), 6))
            if offsets in {(2.8, 1.6), (0.0, -0.8)}:
                generated_fringe_ids.add(id(result))
            return result

        def wrapped_composite(canvas, patches, *args, **kwargs):
            drawn_ids.add(id(patches))
            return original_composite(canvas, patches, *args, **kwargs)

        export_balloon._mitre_band_polygons_mm = wrapped_mitre  # noqa: SLF001
        export_balloon._composite_patches_px = wrapped_composite  # noqa: SLF001
        try:
            layer = export_balloon.render_balloon_layer(
                entry, canvas_height_px=1800, dpi=144
            )
        finally:
            export_balloon._mitre_band_polygons_mm = original_mitre  # noqa: SLF001
            export_balloon._composite_patches_px = original_composite  # noqa: SLF001
        assert layer is not None
        assert len(generated_fringe_ids) == 2 and generated_fringe_ids <= drawn_ids, (
            f"{line_style}: 内外フチが生成・描画されません"
        )


def _export_outline_for_entry(export_balloon, geom_module, entry):
    rect = geom_module.Rect(
        float(entry.x_mm), float(entry.y_mm),
        float(entry.width_mm), float(entry.height_mm),
    )
    outline = export_balloon._balloon_outline_mm(entry, rect)  # noqa: SLF001
    outline = export_balloon._apply_entry_free_transform(entry, outline, rect)  # noqa: SLF001
    return export_balloon._apply_balloon_transforms(  # noqa: SLF001
        outline, rect, False, False, 0.0
    )


def _assert_dark_export_pixel(layer, geom_module, point, canvas_height_px, dpi, label):
    x_px = int(round(geom_module.mm_to_px(float(point.x), dpi))) - layer.left
    y_px = (
        canvas_height_px
        - int(round(geom_module.mm_to_px(float(point.y), dpi)))
        - layer.top
    )
    neighborhood = [
        layer.image.getpixel((x, y))
        for y in range(max(0, y_px - 2), min(layer.image.height, y_px + 3))
        for x in range(max(0, x_px - 2), min(layer.image.width, x_px + 3))
    ]
    assert any(
        len(pixel) >= 4
        and int(pixel[3]) >= 200
        and max(int(pixel[0]), int(pixel[1]), int(pixel[2])) <= 32
        for pixel in neighborhood
    ), f"{label}: 書き出し画像でリングが後続リングの穴に消されています"


def _assert_export_multiline_pixels(export_balloon, geom_module, page) -> None:
    """書き出し画像で主線と外向き3リングがすべて実画素として残ること。"""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    while len(page.balloons):
        page.balloons.remove(0)
    entry = _new_export_entry(page, "double")
    entry.outer_white_margin_enabled = False
    entry.inner_white_margin_enabled = False
    canvas_height_px = 9000
    dpi = 600
    layer = export_balloon.render_balloon_layer(entry, canvas_height_px, dpi)
    assert layer is not None, "double: 多重線のページ出力を生成できません"

    outline = _export_outline_for_entry(export_balloon, geom_module, entry)
    groups = export_balloon._multi_ring_band_polygons(  # noqa: SLF001
        outline,
        entry,
        sharp=True,
        curve_thorn_peaks=True,
        curve_reference_points=outline,
    )
    main_band = export_balloon._mitre_band_polygons_mm(  # noqa: SLF001
        outline,
        float(entry.line_width_mm),
        0.0,
        sharp=True,
        curve_thorn_peaks=True,
        curve_reference_points=outline,
    )
    assert len(groups) == 3 and main_band, (
        f"double: 主線＋外側3リングの帯を生成できません: groups={len(groups)}"
    )

    targets = [("main", main_band)] + [
        (f"outer-{index}", patches) for index, patches in enumerate(groups, start=1)
    ]
    for label, patches in targets:
        polygons = [Polygon(outer, holes) for outer, holes in patches]
        surface = unary_union(polygons)
        assert not surface.is_empty and surface.is_valid, f"{label}: 検査帯が無効です"
        _assert_dark_export_pixel(
            layer, geom_module, surface.representative_point(),
            canvas_height_px, dpi, label,
        )


def _assert_export_bbox(export_balloon, geom_module) -> None:
    """長いmitre先端を含む実描画が画像端で切れないこと。"""
    with tempfile.TemporaryDirectory(prefix="bmanga_thorn_curve_bbox_") as temp_root:
        result = bpy.ops.bmanga.work_new(
            filepath=str(Path(temp_root) / "thorn_curve_bbox.bmanga")
        )
        assert "FINISHED" in result, result
        page = bpy.context.scene.bmanga_work.pages[0]
        for line_style in ("solid", "double"):
            while len(page.balloons):
                page.balloons.remove(0)
            entry = _new_export_entry(page, line_style)
            layer = export_balloon.render_balloon_layer(
                entry, canvas_height_px=9000, dpi=600
            )
            assert layer is not None, f"{line_style}: ページ出力を生成できません"
            alpha = layer.image.getchannel("A")
            bbox = alpha.getbbox()
            assert bbox is not None
            left, top, right, bottom = bbox
            width, height = layer.image.size
            margins = (left, top, width - right, height - bottom)
            assert min(margins) >= 2, (
                f"{line_style}: 尖角が出力画像端で切れています: {margins}"
            )
        _assert_export_multiline_pixels(export_balloon, geom_module, page)
        _assert_export_fringe_styles(export_balloon, page)


def _assert_line_spacing(bands) -> None:
    from shapely.geometry import Polygon

    polygons = [Polygon(outer, holes) for band in bands for outer, holes in band]
    assert len(polygons) == len(LINE_SPECS_MM)
    # 2026-07-15 確定仕様 (手描き理想図): 先端付近は線の内外輪郭とも曲線化され、
    # 線幅・線間隔が先端に向かって細くなってよい。そのため間隔の下限は
    # 「側面の設定間隔 0.7mm が概ね保たれ、絶対に接触しない」ことの確認に留める。
    for index, first in enumerate(polygons):
        for following_index, second in enumerate(polygons[index + 1 :], start=index + 1):
            distance = first.distance(second)
            assert first.disjoint(second) and distance >= 0.40, (
                f"{LINE_SPECS_MM[index][0]}/{LINE_SPECS_MM[following_index][0]}: "
                f"多重線間隔が不足しています: {distance:.6f}mm"
            )


def _assert_thin_far_lines_curve(tail_boolean, curve_helper, outline, tip_count) -> None:
    """細い0.3mm線を遠くへ5本置いても、旧直線へ戻らないこと。"""
    running_inner = 1.6
    for ring_index in range(1, 6):
        inner = running_inner + 0.7
        outer = inner + 0.3
        legacy = tail_boolean.mitre_band_polygons(
            outline, outer, inner, sharp=True
        )
        curved = curve_helper.curve_thorn_peak_band_polygons(legacy, outline)
        _assert_tip_contract(
            legacy, curved, tip_count, f"thin-far-{ring_index}"
        )
        _assert_valid_band(curved, f"thin-far-{ring_index}")
        running_inner = outer


def _curve_deviation_ratio(path) -> float:
    chord = _distance(path[0], path[-1]) if len(path) >= 2 else 0.0
    if chord <= 1.0e-15:
        return 0.0
    deviation = max(
        (_point_to_chord_distance(point, path[0], path[-1]) for point in path[1:-1]),
        default=0.0,
    )
    return deviation / chord


def _nearest_ring_index(ring, point) -> int:
    return min(range(len(ring)), key=lambda index: _distance(ring[index], point))


def _assert_blocked_cluster_unchanged(old_ring, new_ring, blocked) -> None:
    for apex_index in blocked:
        new_index = _nearest_ring_index(new_ring, old_ring[apex_index])
        assert all(
            _distance(
                new_ring[(new_index + delta) % len(new_ring)],
                old_ring[(apex_index + delta) % len(old_ring)],
            ) <= 1.0e-11
            for delta in (-1, 0, 1)
        ), "adjacent-apex: blocked cluster近傍へ新しい折り返しを挿入しました"


def _assert_no_inserted_u_turn(curve_helper, old_ring, new_ring) -> None:
    for index, point in enumerate(new_ring):
        if min(_distance(point, old_point) for old_point in old_ring) <= 1.0e-11:
            continue
        turn = math.degrees(curve_helper._turn_radians(  # noqa: SLF001
            new_ring[(index - 1) % len(new_ring)], point,
            new_ring[(index + 1) % len(new_ring)],
        ))
        assert turn < 170.0, f"adjacent-apex: 新規{turn:.4f}°Uターンを生成しました"


def _count_gently_curved_independent_apexes(old_ring, new_ring, independent) -> int:
    count = 0
    for apex_index in independent:
        tip_index = _nearest_ring_index(new_ring, old_ring[apex_index])
        paths = tuple(
            _shorter_path(
                new_ring, tip_index,
                _nearest_ring_index(new_ring, old_ring[(apex_index + delta) % len(old_ring)]),
            )
            for delta in (-1, 1)
        )
        if all(
            len(path) >= 4 and _curve_deviation_ratio(path) >= 1.0e-4
            for path in paths
        ):
            count += 1
    return count


def _assert_dynamic_polygon_contract(curve_helper, reference, legacy, curved):
    from shapely.geometry import Polygon

    old_outer, old_holes = legacy
    new_outer, new_holes = curved
    old_ring = _open_test_ring(old_outer)
    new_ring = _open_test_ring(new_outer)
    center = curve_helper._polygon_center(reference)  # noqa: SLF001
    matched = curve_helper._matched_apex_indices(old_ring, reference, center)  # noqa: SLF001
    blocked = {
        index for index in matched
        if any((index + delta) % len(old_ring) in matched for delta in (-2, -1, 1, 2))
    }
    independent = matched - blocked
    polygon = Polygon(new_outer, new_holes)
    assert polygon.is_valid and not polygon.is_empty, "adjacent-apex: 修正後bandが無効です"
    # 2026-07-15 確定仕様: 内側輪郭 (穴) もミター先端位置を保ったまま曲線化される。
    # 変更される場合は「本体山頂に対応するmitre先端を持ち、先端位置が不変」であること。
    assert len(old_holes) == len(new_holes), "adjacent-apex: 内周の本数が変わりました"
    for old_hole, new_hole in zip(old_holes, new_holes):
        if _canonical_ring(old_hole) == _canonical_ring(new_hole):
            continue
        old_hole_ring = _open_test_ring(old_hole)
        new_hole_ring = _open_test_ring(new_hole)
        hole_matched = curve_helper._matched_apex_indices(  # noqa: SLF001
            old_hole_ring, reference, center
        )
        assert hole_matched, "adjacent-apex: 対応する山が無い内周が変更されました"
        for hole_apex in hole_matched:
            old_tip = old_hole_ring[hole_apex]
            new_tip = new_hole_ring[_nearest_ring_index(new_hole_ring, old_tip)]
            assert _distance(new_tip, old_tip) <= 1.0e-11, (
                "adjacent-apex: 内周のmitre先端位置が変わりました"
            )
    for apex_index in matched:
        old_tip = old_ring[apex_index]
        new_tip = new_ring[_nearest_ring_index(new_ring, old_tip)]
        assert _distance(new_tip, old_tip) <= 1.0e-11, "adjacent-apex: mitre先端位置が変わりました"
    _assert_blocked_cluster_unchanged(old_ring, new_ring, blocked)
    _assert_no_inserted_u_turn(curve_helper, old_ring, new_ring)
    curved_independent = _count_gently_curved_independent_apexes(
        old_ring, new_ring, independent
    )
    return len(blocked), len(independent), curved_independent


def _assert_dynamic_adjacent_apex_cluster(
    shapes, geom_module, line_mesh, curve_helper
) -> None:
    """可変幅の隣接mitre先端を二重曲線化してUターンを作らないこと。"""
    shape_values = dict(SHAPE_VALUES)
    shape_values["cloud_sub_height_ratio"] = 30.0
    outline, _corners = shapes.outline_with_corners_for_shape(
        "thorn-curve",
        geom_module.Rect(0.0, 0.0, RECT_SIZE_MM[0], RECT_SIZE_MM[1]),
        **shape_values,
    )
    reference = [(float(x) * 0.001, float(y) * 0.001) for x, y in outline]
    samples = [(x, y, 1.0) for x, y in reference]
    center = (
        sum(point[0] for point in samples) / len(samples),
        sum(point[1] for point in samples) / len(samples),
    )
    legacy = line_mesh._build_dynamic_multi_line_polygons(  # noqa: SLF001
        body_samples=samples, signed_offset_m=-21.5 * 0.001,
        base_width_m=4.0 * 0.001, valley_width_m=0.0,
        peak_width_m=4.0 * 0.5 * 0.001, length_scale=1.0,
        valley_sharp=True, balloon_center_m=center,
        cross_extension_m=0.0, peak_extension_m=0.0,
        outside_align=False, peaks_rounded=False,
    )
    curved = curve_helper.curve_thorn_peak_band_polygons(legacy, reference)
    assert legacy and len(legacy) == len(curved), "adjacent-apex: 帯数が変わりました"
    totals = [
        _assert_dynamic_polygon_contract(curve_helper, reference, old, new)
        for old, new in zip(legacy, curved)
    ]
    blocked = sum(counts[0] for counts in totals)
    independent = sum(counts[1] for counts in totals)
    curved_independent = sum(counts[2] for counts in totals)
    assert blocked and independent and curved_independent, (
        "adjacent-apex: clusterを維持しつつ独立先端を曲線化できません: "
        f"{blocked}/{independent}/{curved_independent}"
    )


def main() -> None:
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        shapes = _submodule("utils.balloon_shapes")
        tail_boolean = _submodule("utils.balloon_tail_boolean")
        curve_helper = _submodule("utils.balloon_thorn_curve_stroke")
        line_mesh = _submodule("utils.balloon_line_mesh")
        export_balloon = _submodule("io.export_balloon")
        geom = _submodule("utils.geom")
        rect = geom.Rect(0.0, 0.0, RECT_SIZE_MM[0], RECT_SIZE_MM[1])
        outline, corners = shapes.outline_with_corners_for_shape(
            "thorn-curve", rect, **SHAPE_VALUES
        )
        assert len(corners) >= 8, f"トゲ曲線の山数が不足しています: {len(corners)}"
        bands = [
            _assert_band(tail_boolean, curve_helper, outline, len(corners), spec)
            for spec in LINE_SPECS_MM
        ]
        _assert_line_spacing(bands)
        _assert_thin_far_lines_curve(
            tail_boolean, curve_helper, outline, len(corners)
        )
        _assert_dynamic_adjacent_apex_cluster(shapes, geom, line_mesh, curve_helper)
        _assert_disabled_contract(tail_boolean, outline)
        _assert_tail_is_not_curve_target(tail_boolean, curve_helper, outline)
        _assert_outer_edge_shared_boundary(tail_boolean, outline)
        _assert_viewport_gate(line_mesh, curve_helper, tail_boolean, outline)
        _assert_export_gate(export_balloon, curve_helper, tail_boolean, outline)
        _assert_export_bbox(export_balloon, geom)
        print("BMANGA_THORN_CURVE_CURVED_TIP_CHECK_OK")
    finally:
        if module is not None:
            module.unregister()


if __name__ == "__main__":
    main()
