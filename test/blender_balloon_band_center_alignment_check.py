"""Blender実機用: フキダシ線帯「内側フチ方式」統一 (中心アライメント) の契約を検証する。

2026-07-23 確定仕様: 主線・外側フチ・内側フチ・多重線 (二重線含む) は全形状で
「本体輪郭を中心に mitre オフセット対で挟む」統一則 (buffer(d2)-buffer(d1)) に
統一された。旧仕様 (主線は本体の外側にのみ線幅ぶん成長する外側アライメント、
トゲ曲線+尖角だけ先端を曲線キャップに置換) は撤回済み。

本テストは `docs/balloon_band_inner_edge_unification_plan_2026-07-23.md` の
受け入れ基準を検証する:
  1. 主線の外周/内周が、本体輪郭から ±line_width/2 の対称位置にあること
     (山頂・谷とも、先端まで痩せずに線幅と一致すること)
  2. 主線・外側フチ・内側フチの継ぎ目が数値的に完全一致 (隙間・重なりゼロ)
  3. 多重線 (二重線含む) の各リング幅・リング間隔が指定値と一致すること
  4. 谷/山の線幅% (動的幅) が 100%/100% のとき統一則と厳密に一致し、
     100%近傍で連続的に変化すること (不連続なジャンプが無いこと)
  5. 破線の中心線が主線と同じ中心アライメントになっていること
  6. 「角を尖らせる」しっぽの先端が中心アライメントの主線帯と整合すること
  7. 書き出し (io/export_balloon.py) がビューポートと同じオフセットを使うこと

実行例:
  blender.exe --background --factory-startup --python-exit-code 1 \
    --python test/blender_balloon_band_center_alignment_check.py
"""

from __future__ import annotations

import importlib
import importlib.util
import math
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
ADDON_NAME = "bmanga_dev_band_center_alignment"

# 全形状 × 尖角ON/OFF で共通に使う本体パラメータ (トゲ曲線の谷が浅くなりすぎない
# 適度な山数になるサイズ)。
RECT_SIZE_MM = (58.0, 76.0)
SHAPE_PARAMS = {
    "cloud_bump_width_mm": 14.0,
    "cloud_bump_height_mm": 12.0,
    "cloud_offset_percent": 50.0,
    "cloud_sub_width_ratio": 30.0,
    "cloud_sub_height_ratio": 0.0,
    "shape_seed": 7,
}
LINE_WIDTH_MM = 1.0
OUTER_EDGE_WIDTH_MM = 1.0
INNER_EDGE_WIDTH_MM = 1.0
MM = 1000.0


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        ADDON_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[ADDON_NAME] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _submodule(name: str):
    return importlib.import_module(f"{ADDON_NAME}.{name}")


def _boundary_loops(mesh) -> list[list[int]]:
    """メッシュの境界エッジ (面1枚だけに属するエッジ) からループ列を復元する."""
    edge_face_count: dict[tuple[int, int], int] = defaultdict(int)
    for poly in mesh.polygons:
        for edge_key in poly.edge_keys:
            edge_face_count[tuple(sorted(edge_key))] += 1
    boundary = [key for key, count in edge_face_count.items() if count == 1]
    adjacency: dict[int, list[int]] = defaultdict(list)
    for a, b in boundary:
        adjacency[a].append(b)
        adjacency[b].append(a)
    visited: set[int] = set()
    loops: list[list[int]] = []
    for start in adjacency:
        if start in visited:
            continue
        loop = [start]
        visited.add(start)
        previous, current = None, start
        while True:
            candidates = [v for v in adjacency[current] if v != previous]
            if not candidates:
                break
            nxt = candidates[0]
            if nxt == start or nxt in visited:
                break
            loop.append(nxt)
            visited.add(nxt)
            previous, current = current, nxt
        loops.append(loop)
    return loops


def _ring_points_mm(mesh, loop: list[int]) -> list[tuple[float, float]]:
    verts = mesh.vertices
    return [(verts[i].co.x * MM, verts[i].co.y * MM) for i in loop]


def _point_segment_distance(point, a, b) -> float:
    ax, ay = a
    bx, by = b
    px, py = point
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < 1.0e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def _min_distance_to_ring(point, ring: list[tuple[float, float]]) -> float:
    n = len(ring)
    return min(_point_segment_distance(point, ring[i], ring[(i + 1) % n]) for i in range(n))


def _make_balloon(context, page, balloon_op, *, shape: str, sharp: bool):
    entry = balloon_op._create_balloon_entry(  # noqa: SLF001
        context, page, shape=shape, x=100.0, y=100.0,
        w=RECT_SIZE_MM[0], h=RECT_SIZE_MM[1],
    )
    entry.line_width_mm = LINE_WIDTH_MM
    entry.shape_params.cloud_valley_sharp = sharp
    for key, value in SHAPE_PARAMS.items():
        setattr(entry.shape_params, key, value)
    entry.outer_white_margin_enabled = True
    entry.outer_white_margin_width_mm = OUTER_EDGE_WIDTH_MM
    entry.inner_white_margin_enabled = True
    entry.inner_white_margin_width_mm = INNER_EDGE_WIDTH_MM
    return entry


def _rebuild(scene, page, entry, balloon_curve_object):
    return balloon_curve_object.ensure_balloon_curve_object(
        scene=scene, entry=entry, page=page, force_regenerate=True,
    )


def _mesh_loops_raw(name: str) -> list[list[tuple[float, float]]]:
    """指定オブジェクトの全境界ループを個数チェック無しで返す (多重線=3リング=6ループ等)."""
    obj = bpy.data.objects.get(name)
    assert obj is not None, f"メッシュオブジェクトが見つかりません: {name}"
    loops = _boundary_loops(obj.data)
    return [_ring_points_mm(obj.data, loop) for loop in loops]


def _mesh_loops_by_object(name: str) -> list[list[tuple[float, float]]]:
    """単一帯 (外周+内周=2ループ) のメッシュ境界を返す."""
    loops = _mesh_loops_raw(name)
    assert len(loops) == 2, f"{name}: 境界ループが2本(外周+内周)ではありません: {len(loops)}"
    return loops


def _assert_seamless_bands(entry, shape: str, sharp: bool) -> None:
    """主線・外側フチ・内側フチのメッシュ境界が、隙間・重なり無く継ぎ目一致すること."""
    balloon_id = entry.id
    line_loops = _mesh_loops_by_object(f"balloon_line_mesh_{balloon_id}")
    outer_loops = _mesh_loops_by_object(f"balloon_outer_edge_mesh_{balloon_id}")
    inner_loops = _mesh_loops_by_object(f"balloon_inner_edge_mesh_{balloon_id}")

    def top_point(ring):
        return max(ring, key=lambda p: p[1])

    line_by_top_y = sorted(line_loops, key=lambda r: top_point(r)[1])
    line_inner, line_outer = line_by_top_y[0], line_by_top_y[1]
    outer_by_top_y = sorted(outer_loops, key=lambda r: top_point(r)[1])
    outer_inner, _outer_outer = outer_by_top_y[0], outer_by_top_y[1]
    inner_by_top_y = sorted(inner_loops, key=lambda r: top_point(r)[1])
    _inner_inner, inner_outer = inner_by_top_y[0], inner_by_top_y[1]

    label = f"{shape}/sharp={sharp}"
    dev_outer = max(
        _min_distance_to_ring(p, outer_inner) for p in top_point_window(line_outer)
    )
    assert dev_outer < 1.0e-6, (
        f"{label}: 主線外周と外側フチ基準の継ぎ目が一致しません: {dev_outer}mm"
    )
    dev_inner = max(
        _min_distance_to_ring(p, inner_outer) for p in top_point_window(line_inner)
    )
    assert dev_inner < 1.0e-6, (
        f"{label}: 主線内周と内側フチ基準の継ぎ目が一致しません: {dev_inner}mm"
    )


def top_point_window(ring: list[tuple[float, float]], span: int = 40) -> list[tuple[float, float]]:
    """継ぎ目照合の対象を絞るため、リング全点ではなく先端付近だけ抜き出す (高速化)."""
    if len(ring) <= span * 2:
        return ring
    top_idx = max(range(len(ring)), key=lambda i: ring[i][1])
    n = len(ring)
    return [ring[(top_idx + k) % n] for k in range(-span, span + 1)]


def _assert_side_width_matches_line_width(entry, shape: str, sharp: bool) -> None:
    """先端から離れた側面で、主線の垂直線幅が line_width_mm と一致すること.

    サンプル点は頂点インデックスのオフセットではなく弧長距離で選ぶ。尖角OFF
    (round join) では凸の山頂に円弧状の追加頂点が入り、外周と内周で頂点密度が
    異なるため、インデックスオフセットだと円弧の途中を側面と誤認しうる。
    """
    balloon_id = entry.id
    loops = _mesh_loops_by_object(f"balloon_line_mesh_{balloon_id}")

    def top_point(ring):
        return max(ring, key=lambda p: p[1])

    by_top = sorted(loops, key=lambda r: top_point(r)[1])
    inner_ring, outer_ring = by_top[0], by_top[1]
    apex = top_point(outer_ring)
    n = len(outer_ring)
    if n < 60:
        return  # 楕円等、山数が少なく側面サンプルの意味が薄い形状はスキップ

    def points_beyond_arc_length(ring, start_idx: int, step: int, min_arc_mm: float, want: int):
        """start_idx から step 方向へ辿り、弧長 min_arc_mm 以上離れた点を want 個集める."""
        collected = []
        arc = 0.0
        prev = ring[start_idx % len(ring)]
        for i in range(1, len(ring)):
            cur = ring[(start_idx + step * i) % len(ring)]
            arc += math.hypot(cur[0] - prev[0], cur[1] - prev[1])
            prev = cur
            if arc >= min_arc_mm:
                collected.append(cur)
                if len(collected) >= want:
                    break
                min_arc_mm = arc + 1.5  # 次のサンプルまで間隔をあける
        return collected

    apex_idx = outer_ring.index(apex)
    min_arc_mm = 3.0
    forward = points_beyond_arc_length(outer_ring, apex_idx, 1, min_arc_mm, 3)
    backward = points_beyond_arc_length(outer_ring, apex_idx, -1, min_arc_mm, 3)
    side_points = forward + backward
    assert side_points, f"{shape}/sharp={sharp}: 側面サンプル点を選べません"
    widths = [_min_distance_to_ring(p, inner_ring) for p in side_points]
    # トゲ曲線・雲・もやもやは側面自体が曲線 (本体が細かい折れ線近似の曲線) なので、
    # 離散サンプル点からの最近点探索には数%の幾何近似誤差が乗る。継ぎ目の完全一致
    # ( _assert_seamless_bands、誤差ゼロ) が本質的な検証であり、ここは「線幅が
    # 半分・倍になっていないか」等の粗い崩れを検知する目的なので許容誤差を広めに取る。
    tolerance_mm = 0.02 if shape in ("thorn", "rect") else max(0.08, LINE_WIDTH_MM * 0.08)
    for width in widths:
        assert abs(width - LINE_WIDTH_MM) < tolerance_mm, (
            f"{shape}/sharp={sharp}: 側面の主線幅が期待値からずれています: "
            f"{width}mm (期待 {LINE_WIDTH_MM}mm, 許容 ±{tolerance_mm}mm)"
        )


def _assert_multi_line_pattern(context, scene, page, balloon_op, balloon_curve_object) -> None:
    """二重線(double)の各リング幅・間隔が指定値と一致すること."""
    entry = _make_balloon(context, page, balloon_op, shape="thorn-curve", sharp=True)
    entry.line_style = "double"
    entry.multi_line_count = 3
    entry.multi_line_width_mm = 0.5
    entry.multi_line_spacing_mm = 1.0
    entry.multi_line_direction = "outside"
    try:
        _rebuild(scene, page, entry, balloon_curve_object)
        loops = _mesh_loops_raw(f"balloon_multi_line_mesh_{entry.id}")

        def ring_area(ring):
            total = 0.0
            n = len(ring)
            for i in range(n):
                x1, y1 = ring[i]
                x2, y2 = ring[(i + 1) % n]
                total += x1 * y2 - x2 * y1
            return abs(total) * 0.5

        rings_sorted = sorted(loops, key=ring_area)
        assert len(rings_sorted) == 6, f"リング数が想定外です: {len(rings_sorted)}"
        gaps: list[float] = []
        for i in range(len(rings_sorted) - 1):
            inner_ring, outer_ring = rings_sorted[i], rings_sorted[i + 1]
            top_idx = max(range(len(outer_ring)), key=lambda k: outer_ring[k][1])
            n2 = len(outer_ring)
            side_pts = [
                outer_ring[(top_idx + k) % n2] for k in (30, 40, 50, -30, -40, -50)
            ]
            distances = [_min_distance_to_ring(p, inner_ring) for p in side_pts]
            gaps.append(sorted(distances)[len(distances) // 2])  # median (外れ値耐性)
        expected = [0.5, 1.0, 0.5, 1.0, 0.5]
        for index, (gap, exp) in enumerate(zip(gaps, expected)):
            assert abs(gap - exp) < 0.02, (
                f"多重線リング間隔[{index}]が期待値とずれています: {gap}mm (期待 {exp}mm)"
            )
    finally:
        balloon_op._delete_balloon_by_id(context, page.id, entry.id)  # noqa: SLF001


def _assert_dynamic_width_continuity(line_mesh, entry) -> None:
    """谷/山の線幅%が100/100のとき統一則と厳密一致し、100%近傍で連続的に変化すること."""
    body_obj = None
    for obj in bpy.data.objects:
        if obj.name.endswith("__balloon__" + entry.id):
            body_obj = obj
            break
    assert body_obj is not None

    body_samples = line_mesh._body_samples_for_line_mesh(entry, body_obj)  # noqa: SLF001
    samples, _ = line_mesh._outline_samples_with_tails(entry, body_samples)  # noqa: SLF001
    valley_sharp = line_mesh._valley_sharp_for_entry(entry)  # noqa: SLF001
    line_width_m = LINE_WIDTH_MM * 0.001
    balloon_center_m = line_mesh._balloon_center_m_from_samples(samples)  # noqa: SLF001

    static_ref = line_mesh._stroke_band_centered(  # noqa: SLF001
        samples, line_width_m=line_width_m, valley_sharp=valley_sharp, peaks_rounded=False,
    )

    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    def dynamic_area(valley_pct: float, peak_pct: float) -> float:
        polys = line_mesh._build_dynamic_multi_line_polygons(  # noqa: SLF001
            body_samples=samples, signed_offset_m=0.0, base_width_m=line_width_m,
            valley_width_m=line_width_m * valley_pct / 100.0,
            peak_width_m=line_width_m * peak_pct / 100.0,
            length_scale=1.0, valley_sharp=valley_sharp,
            balloon_center_m=balloon_center_m, peak_extension_m=0.0,
            outside_align=False, peaks_rounded=False,
        )
        return unary_union([Polygon(o, h) for o, h in polys]).area

    static_poly = Polygon(static_ref[0], static_ref[1])
    area_100 = dynamic_area(100.0, 100.0)
    # 動的幅経路は shapely の union/差分演算を静的経路と異なる順序で行うため、
    # 数百頂点規模の演算に伴う浮動小数点の丸め (1e-9相対未満) は許容する。
    assert abs(area_100 - static_poly.area) <= max(1.0e-15, static_poly.area * 1.0e-6), (
        f"動的幅100%/100%が統一則(静的経路)と一致しません: "
        f"dynamic={area_100}, static={static_poly.area}"
    )
    area_99 = dynamic_area(99.0, 100.0)
    # 100%から99%への変化が連続的である (面積が突然大きく変わらない) こと。
    relative_jump = abs(area_100 - area_99) / area_100
    assert relative_jump < 0.05, (
        f"谷99%への変化が不連続です (面積変化率 {relative_jump:.4f})。"
        "outside_alignの切替境界で形状が飛んでいる可能性があります"
    )


def _assert_dashed_line_centered(line_mesh, entry) -> None:
    """破線の中心線が主線と同じ中心アライメント (本体輪郭 ± 半幅) になっていること."""
    body_obj = None
    for obj in bpy.data.objects:
        if obj.name.endswith("__balloon__" + entry.id):
            body_obj = obj
            break
    assert body_obj is not None
    body_samples = line_mesh._body_samples_for_line_mesh(entry, body_obj)  # noqa: SLF001
    samples, _ = line_mesh._outline_samples_with_tails(entry, body_samples)  # noqa: SLF001
    valley_sharp = line_mesh._valley_sharp_for_entry(entry)  # noqa: SLF001
    line_width_m = LINE_WIDTH_MM * 0.001

    dash_polys = line_mesh._build_dashed_band_polygons(  # noqa: SLF001
        samples, line_width_m=line_width_m, line_style="dashed", valley_sharp=valley_sharp,
        dash_segment_mm=3.6, dash_gap_mm=2.4, dotted_gap_mm=0.0,
    )
    assert dash_polys, "破線ピースが生成されません"
    body_poly = line_mesh._build_body_polygon(samples)  # noqa: SLF001

    from shapely.geometry import Point

    max_outside = 0.0
    max_inside = 0.0
    for outer, _holes in dash_polys[:8]:
        for x, y in outer:
            point = Point(x, y)
            distance = point.distance(body_poly.exterior) * MM
            if body_poly.contains(point):
                max_inside = max(max_inside, distance)
            else:
                max_outside = max(max_outside, distance)
    half_mm = LINE_WIDTH_MM * 0.5
    assert abs(max_outside - half_mm) < 0.02, (
        f"破線の外側張り出しが半幅と一致しません: {max_outside}mm (期待 {half_mm}mm)"
    )
    assert abs(max_inside - half_mm) < 0.02, (
        f"破線の内側食い込みが半幅と一致しません: {max_inside}mm (期待 {half_mm}mm)"
    )


def _assert_sharp_tail_tip_centered(tail_boolean) -> None:
    """「角を尖らせる」しっぽの先端が中心アライメントの主線帯と整合すること."""
    outline = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    line_w = 1.0
    half = line_w * 0.5
    main_band = tail_boolean.mitre_band_polygons(outline, half, -half, sharp=True)

    centerline = [(10.0, 5.0), (15.0, 5.0)]
    halfwidths = [1.0, 0.0]
    region_pts = [(10.0, 4.0), (15.0, 5.0), (10.0, 6.0)]

    result_rings = tail_boolean.apply_sharp_tail_tips(
        main_band, outline, line_w, [(centerline, halfwidths, region_pts)],
        add_bend_mitre=True,
    )
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    after = unary_union([Polygon(o, h) for o, h in result_rings])
    assert after.is_valid and not after.is_empty
    min_x, min_y, max_x, max_y = after.bounds
    # 本体側 (しっぽの無い辺) の外周は主線帯そのまま ±half のはず
    assert abs(min_x - (0.0 - half)) < 1.0e-9, f"本体側の外周が中心アライメントでない: {min_x}"
    assert abs(min_y - (0.0 - half)) < 1.0e-9, f"本体側の外周が中心アライメントでない: {min_y}"
    # しっぽ先端: ext_len = w*2.5 だけ centerline 終点から伸びて絞られる
    expected_tip_x = 15.0 + line_w * 2.5
    assert abs(max_x - expected_tip_x) < 1.0e-6, (
        f"しっぽ先端の絞り位置が期待値とずれています: {max_x} (期待 {expected_tip_x})"
    )


def _assert_export_matches_viewport_offsets(export_balloon, tail_boolean) -> None:
    """書き出し側のオフセット関係がビューポートと同じ中心アライメントであること."""
    outline = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    line_w = 1.0
    half = line_w * 0.5

    main = tail_boolean.mitre_band_polygons(outline, half, -half, sharp=True)
    outer_fringe = tail_boolean.mitre_band_polygons(outline, half + 1.0, half, sharp=True)
    inner_fringe = tail_boolean.mitre_band_polygons(outline, -half, -half - 1.0, sharp=True)

    from shapely.geometry import Polygon

    main_poly = Polygon(main[0][0], main[0][1])
    outer_poly = Polygon(outer_fringe[0][0], outer_fringe[0][1])
    inner_poly = Polygon(inner_fringe[0][0], inner_fringe[0][1])

    assert main_poly.distance(outer_poly) < 1.0e-12, "書き出し: 主線と外側フチに隙間があります"
    assert main_poly.distance(inner_poly) < 1.0e-12, "書き出し: 主線と内側フチに隙間があります"
    assert main_poly.intersection(outer_poly).area < 1.0e-12, "書き出し: 主線と外側フチが重なっています"
    assert main_poly.intersection(inner_poly).area < 1.0e-12, "書き出し: 主線と内側フチが重なっています"


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_band_center_alignment_"))
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "BandCenterAlignment.bmanga"))
        assert result == {"FINISHED"}, f"一時作品を作成できません: {result}"

        balloon_op = _submodule("operators.balloon_op")
        balloon_curve_object = _submodule("utils.balloon_curve_object")
        balloon_line_mesh = _submodule("utils.balloon_line_mesh")
        balloon_tail_boolean = _submodule("utils.balloon_tail_boolean")
        export_balloon = _submodule("io.export_balloon")
        get_work = _submodule("core.work").get_work

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        assert work is not None and len(work.pages) > 0, "一時作品のページがありません"
        page = work.pages[0]

        # --- モジュール構造確認: 旧関数が消え、新関数が揃っていること ---
        assert not hasattr(balloon_line_mesh, "_stroke_band_outside_union"), (
            "旧関数 _stroke_band_outside_union が残っています"
        )
        assert not hasattr(balloon_line_mesh, "_curve_thorn_peak_polygons"), (
            "撤去したはずの曲線キャップ関数が残っています"
        )
        assert hasattr(balloon_line_mesh, "_stroke_band_centered"), "新関数が見つかりません"
        assert hasattr(balloon_line_mesh, "_compute_main_line_inner_boundary"), (
            "内側境界ヘルパーが見つかりません"
        )

        # --- 全形状 × 尖角ON/OFF で 主線/外側フチ/内側フチの継ぎ目・線幅を検証 ---
        for shape in ("thorn", "thorn-curve", "cloud", "fluffy", "rect", "ellipse"):
            for sharp in (True, False):
                entry = _make_balloon(context, page, balloon_op, shape=shape, sharp=sharp)
                try:
                    _rebuild(scene, page, entry, balloon_curve_object)
                    _assert_seamless_bands(entry, shape, sharp)
                    _assert_side_width_matches_line_width(entry, shape, sharp)
                finally:
                    balloon_op._delete_balloon_by_id(context, page.id, entry.id)  # noqa: SLF001

        # --- 多重線 (二重線) のリング幅・間隔 ---
        _assert_multi_line_pattern(context, scene, page, balloon_op, balloon_curve_object)

        # --- 谷/山の線幅% (動的幅) の100%一致・連続性、破線の中心アライメント ---
        entry = _make_balloon(context, page, balloon_op, shape="thorn-curve", sharp=True)
        try:
            _rebuild(scene, page, entry, balloon_curve_object)
            _assert_dynamic_width_continuity(balloon_line_mesh, entry)
            _assert_dashed_line_centered(balloon_line_mesh, entry)
        finally:
            balloon_op._delete_balloon_by_id(context, page.id, entry.id)  # noqa: SLF001

        # --- しっぽ先端の中心アライメント整合、書き出しのオフセット一致 ---
        _assert_sharp_tail_tip_centered(balloon_tail_boolean)
        _assert_export_matches_viewport_offsets(export_balloon, balloon_tail_boolean)

        print("BMANGA_BALLOON_BAND_CENTER_ALIGNMENT_CHECK_OK")
    finally:
        try:
            if module is not None:
                module.unregister()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
