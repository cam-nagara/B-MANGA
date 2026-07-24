# -*- coding: utf-8 -*-
"""標準方式 (新方式Jでない動的形状) の主線「谷/山の線幅%」がページ書き出しへ
反映されることの実機回帰テスト.

背景 (docs/balloon_std_line_width_pct_export_plan_2026-07-24.md):
  動的形状 (雲/フワフワ/トゲ/トゲ曲線) で「角を尖らせる」の方式が標準方式
  (新方式J=頂点距離方式ではない) のフキダシは、主線の谷/山の線幅%
  (entry.line_valley_width_pct / line_peak_width_pct) がビューポートでは
  効くが、ページ書き出しでは常に均一幅で描かれ、外側/内側フチも追従しない
  不具合があった (v0.6.579 の多重線動的書き出しと同じ「ビューポート正典
  生成器をメートル単位で書き出しから呼ぶ」方式で解消)。

検証内容 (計画書 §6.1 (a)-(f) に対応):
  (a) 効くこと: 山60%/谷20%のとき、書き出しの主線帯ポリゴンが100%/100%と
      実際に (面積で) 異なる
  (b) 画面と一致: ビューポート由来サンプルと書き出し由来サンプルを同じ
      生成器 (main_line_dynamic_band_polys) に通した結果が、山頂・谷の
      代表点で許容誤差内に一致する
  (c) 両方0%: 主線帯が書き出しに存在しない。外側/内側フチは本体基準で残る
  (d) フチ追従: 外側/内側フチが、細った主線の外端/内端に密着する (書き出し側)
  (e) 回帰ガード: 100%/100% (非dynamic) では従来経路が使われ、
      ゲート条件 (is_dynamic) が偽であることと、render_balloon_layer が
      正常に描画することを確認する
  (f) J方式が変わらないこと: J方式では標準dynamic経路が有効化されない
      (排他条件の直接検証。詳細は blender_balloon_j_width_pct_check.py)

対象形状: トゲ直線 (thorn, anchor-only経路) / トゲ曲線 (thorn-curve, フル
サンプル経路)。

実行:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe" --background ^
    --factory-startup --python-exit-code 1 ^
    --python "d:/Develop/Blender/B-MANGA/test/blender_balloon_std_width_pct_export_check.py"
"""

from __future__ import annotations

import importlib.util
import math
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
ADDON_NAME = "bmanga_dev_std_width_pct_export"
SENTINEL = "BMANGA_BALLOON_STD_WIDTH_PCT_EXPORT_CHECK_OK"

# blender_balloon_band_center_alignment_check.py と同一の本体形状パラメータ
# (動的幅の既存検証で実績のある組み合わせ: 山数が適度で sub_height=0 により
# 全ての山/谷がほぼ均一になり、中央値ベースの計測が安定する)。
RECT_SIZE_MM = (58.0, 76.0)
SHAPE_PARAMS = {
    "cloud_bump_width_mm": 14.0,
    "cloud_bump_height_mm": 12.0,
    "cloud_offset_percent": 50.0,
    "cloud_sub_width_ratio": 30.0,
    "cloud_sub_height_ratio": 0.0,
    "shape_seed": 7,
}
LINE_WIDTH_MM = 1.6
OUTER_MARGIN_MM = 0.6
INNER_MARGIN_MM = 0.5
PEAK_PCT = 60.0
VALLEY_PCT = 20.0
# 山頂・谷底での「100%基準距離に対する比」の許容誤差 (無次元)。 mitre 結合は
# 頂点の内角に応じて距離を増幅するが、100%基準との比を取れば増幅係数が
# 相殺されるため、比較的タイトな許容で判定できる (実測: 谷はほぼ完全一致、
# 山は平坦な頂上を持つ形状パラメータの影響で ±0.1 程度のばらつきが出た)。
TOL_RATIO = 0.15
# フチ帯と主線帯の継ぎ目 (同一ソースpolygonに由来するため、ほぼ完全一致の
# はず) の許容誤差 (mm)。
SEAM_TOL_MM = 0.05


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


# ---------------------------------------------------------------------------
# 幾何ヘルパ (blender_balloon_band_center_alignment_check.py と同じ手法)
# ---------------------------------------------------------------------------

def _ring_abs_area(ring: list[tuple[float, float]]) -> float:
    total = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(total) * 0.5


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
    if n < 2:
        return float("inf")
    return min(_point_segment_distance(point, ring[i], ring[(i + 1) % n]) for i in range(n))


def _min_distance_to_rings(point, rings: list[list[tuple[float, float]]]) -> float:
    candidates = [r for r in rings if len(r) >= 2]
    if not candidates:
        return float("inf")
    return min(_min_distance_to_ring(point, r) for r in candidates)


def _area_mm2(polys, scale: float = 1.0) -> float:
    """[(outer, holes), ...] の合計面積 (mm^2)。座標に scale を掛けてから計算する."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    shapes = []
    for outer, holes in polys:
        try:
            poly = Polygon(
                [(x * scale, y * scale) for x, y in outer],
                [[(x * scale, y * scale) for x, y in hole] for hole in holes],
            )
            if not poly.is_valid:
                poly = poly.buffer(0)
            if not poly.is_empty:
                shapes.append(poly)
        except Exception:  # noqa: BLE001
            continue
    if not shapes:
        return 0.0
    try:
        return unary_union(shapes).area
    except Exception:  # noqa: BLE001
        return sum(s.area for s in shapes)


def _outer_inner_rings_mm(polys, scale: float = 1.0):
    """最大面積ピースの (outer_ring_mm, inner_ring_mm_or_None) を返す."""
    assert polys, "帯ポリゴンが空です"
    scaled = [
        (
            [(x * scale, y * scale) for x, y in outer],
            [[(x * scale, y * scale) for x, y in h] for h in holes],
        )
        for outer, holes in polys
    ]
    outer_ring, holes = max(scaled, key=lambda oh: _ring_abs_area(oh[0]))
    inner_ring = max(holes, key=_ring_abs_area) if holes else None
    return outer_ring, inner_ring


def _all_rings_mm(polys, scale: float = 1.0) -> list[list[tuple[float, float]]]:
    """[(outer, holes), ...] の全リング (外周+穴) をフラットなリストで返す."""
    out: list[list[tuple[float, float]]] = []
    for outer, holes in polys:
        out.append([(x * scale, y * scale) for x, y in outer])
        for hole in holes:
            out.append([(x * scale, y * scale) for x, y in hole])
    return out


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _detect_peak_valley_points(line_mesh, pts_xy, center):
    """半径の局所極値 (山頂/谷底) の点をワールド座標で返す (ビューポートの
    内部検出器と同じ手法。 anchor-only/フルサンプルどちらの経路でも純幾何の
    半径極値として山頂・谷底に一致する)."""
    window = max(2, line_mesh.SAMPLES_PER_SEGMENT // 6)
    peaks, valleys = line_mesh._detect_radial_peaks_valleys(  # noqa: SLF001
        pts_xy, center, window=window,
    )
    return [pts_xy[i] for i in peaks], [pts_xy[i] for i in valleys]


def _distances_to_ring(points_xy, ring_mm, scale: float) -> list[float]:
    return [_min_distance_to_ring((x * scale, y * scale), ring_mm) for x, y in points_xy]


def _median_ratio(dyn_distances: list[float], ref_distances: list[float]) -> float:
    """各点での (dyn距離 / 100%基準距離) の中央値を返す.

    山頂・谷底 (特に谷) では mitre 結合の幾何的増幅 (頂点の内角に依存し、
    半幅そのものに比例) がかかるため、距離の絶対値を単純に半幅期待値と
    比較すると増幅係数ぶんの誤差が乗る。同一頂点の 100%基準との比を取れば、
    増幅係数は本体形状 (pct非依存) にのみ依存するため相殺され、
    比率だけが %設定に応じて変化する。"""
    ratios = [d / r for d, r in zip(dyn_distances, ref_distances) if r > 1.0e-6]
    assert ratios, "参照距離 (100%基準) が全てゼロで比率を計算できません"
    return _median(ratios)


# ---------------------------------------------------------------------------
# 画素ヘルパ (blender_balloon_j_width_pct_check.py と同じ手法)
# ---------------------------------------------------------------------------

def _count_pixels(image, predicate) -> int:
    img = image.convert("RGBA")
    count = 0
    for r, g, b, a in img.getdata():
        if predicate(r, g, b, a):
            count += 1
    return count


def _save_layer_preview(image, path) -> None:
    """透明背景の書き出しレイヤを目視確認しやすいよう、淡いグレー背景に
    合成してPNG保存する (白いフチが白背景ビューアで見えなくなるのを防ぐ)."""
    from PIL import Image

    rgba = image.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (224, 224, 224, 255))
    bg.alpha_composite(rgba)
    bg.convert("RGB").save(str(path))


def _is_black_opaque(r, g, b, a) -> bool:
    return a > 200 and r < 40 and g < 40 and b < 40


def _is_white_opaque(r, g, b, a) -> bool:
    return a > 200 and r > 235 and g > 235 and b > 235


# ---------------------------------------------------------------------------
# フキダシ生成・サンプル取得
# ---------------------------------------------------------------------------

def _make_balloon(context, page, balloon_op, entry_id: str, shape: str, *, method: str = "miter"):
    entry = balloon_op._create_balloon_entry(  # noqa: SLF001
        context, page, shape=shape, x=100.0, y=100.0,
        w=RECT_SIZE_MM[0], h=RECT_SIZE_MM[1],
    )
    entry.id = entry_id
    entry.line_width_mm = LINE_WIDTH_MM
    entry.line_style = "solid"
    entry.line_color = (0.0, 0.0, 0.0, 1.0)
    entry.outer_white_margin_enabled = True
    entry.outer_white_margin_width_mm = OUTER_MARGIN_MM
    entry.inner_white_margin_enabled = True
    entry.inner_white_margin_width_mm = INNER_MARGIN_MM
    sp = entry.shape_params
    sp.cloud_valley_sharp = True
    sp.sharp_corner_method = method  # "miter" = 標準方式 / "anchor" = 新方式J
    if method == "anchor":
        sp.sharp_peak_width_scale = 1.5
        sp.sharp_valley_width_scale = 0.5
    for key, value in SHAPE_PARAMS.items():
        setattr(sp, key, value)
    return entry


def _body_obj_for(entry):
    for obj in bpy.data.objects:
        if obj.name.endswith("__balloon__" + entry.id):
            return obj
    return None


def _viewport_samples(line_mesh, entry):
    """main_line_dynamic_band_polys に渡すのと同じビューポート samples (x,y,r) と
    (x,y)のみのリスト・中心を返す (ensure_balloon_line_mesh と同一手順)."""
    body_obj = _body_obj_for(entry)
    assert body_obj is not None, f"本体オブジェクトが見つかりません: {entry.id}"
    body_samples = line_mesh._body_samples_for_line_mesh(entry, body_obj)  # noqa: SLF001
    samples, _tails_merged = line_mesh._outline_samples_with_tails(entry, body_samples)  # noqa: SLF001
    center = line_mesh._balloon_center_m_from_samples(samples)  # noqa: SLF001
    samples_xy = [(float(s[0]), float(s[1])) for s in samples]
    return samples, samples_xy, center


def _export_outline_mm(balloon_shapes, geom, entry):
    rect = geom.Rect(0.0, 0.0, float(entry.width_mm), float(entry.height_mm))
    return balloon_shapes.outline_for_entry(entry, rect)


def _export_pts_m(export_balloon, outline_mm):
    """render_balloon_layer の std_dyn_active 座標準備と同一手順 (v0.6.579 と同型)."""
    dense_mm = export_balloon._densify_closed_outline_mm(outline_mm)  # noqa: SLF001
    pts_m = [(x * 0.001, y * 0.001, 1.0) for (x, y) in dense_mm]
    center_m = (
        sum(p[0] for p in pts_m) / len(pts_m),
        sum(p[1] for p in pts_m) / len(pts_m),
    )
    pts_xy = [(p[0], p[1]) for p in pts_m]
    return pts_m, pts_xy, center_m


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_std_width_pct_export_"))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    try:
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "StdWidthPctExport.bmanga"))
        assert result == {"FINISHED"}, f"一時作品を作成できません: {result}"
        balloon_op = _submodule("operators.balloon_op")
        balloon_curve_object = _submodule("utils.balloon_curve_object")
        line_mesh = _submodule("utils.balloon_line_mesh")
        export_balloon = _submodule("io.export_balloon")
        balloon_shapes = _submodule("utils.balloon_shapes")
        geom = _submodule("utils.geom")
        get_work = _submodule("core.work").get_work

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        assert work is not None and len(work.pages) > 0, "一時作品のページがありません"
        page = work.pages[0]

        def rebuild(entry):
            balloon_curve_object.ensure_balloon_curve_object(
                scene=scene, entry=entry, page=page, force_regenerate=True,
            )

        out_dir = ROOT / "_verify" / "2026-07-24_std_width_pct_export"
        out_dir.mkdir(parents=True, exist_ok=True)

        line_width_m = LINE_WIDTH_MM * 0.001
        outer_width_m = OUTER_MARGIN_MM * 0.001
        inner_width_m = INNER_MARGIN_MM * 0.001
        target_peak_ratio = PEAK_PCT / 100.0
        target_valley_ratio = VALLEY_PCT / 100.0

        for shape in ("thorn", "thorn-curve"):
            # =================================================================
            # 基準 (100%/100%): ゲート条件確認 + render_balloon_layer スモーク
            # =================================================================
            entry = _make_balloon(context, page, balloon_op, f"std_{shape}_main", shape)
            rebuild(entry)
            valley_sharp = line_mesh._valley_sharp_for_entry(entry)  # noqa: SLF001
            assert valley_sharp, f"{shape}: cloud_valley_sharp が反映されていない"

            is_dyn_100, _vpct100, _ppct100, bz_100 = line_mesh._line_dynamic_width_params(entry)  # noqa: SLF001
            assert not is_dyn_100, (
                f"{shape}: 100%/100%なのに is_dynamic が True (閾値ロジックの回帰)"
            )
            assert not bz_100

            layer100 = export_balloon.render_balloon_layer(entry, canvas_height_px=1000, dpi=110)
            assert layer100 is not None and layer100.image is not None, (
                f"{shape}: 基準(100/100)の render_balloon_layer が失敗"
            )
            black_100 = _count_pixels(layer100.image, _is_black_opaque)
            assert black_100 > 300, (
                f"{shape}: 基準(100/100)の主線が書き出し画像にほぼ描かれていない: {black_100}px"
            )
            _save_layer_preview(layer100.image, out_dir / f"{shape}_100_100.png")

            samples, samples_xy, center_vp = _viewport_samples(line_mesh, entry)
            outline_mm = _export_outline_mm(balloon_shapes, geom, entry)
            pts_m, pts_xy, center_exp = _export_pts_m(export_balloon, outline_mm)

            # 山頂・谷底の位置は本体形状のみで決まる (線幅%に依存しない) ため、
            # 100%基準の時点で一度だけ検出して使い回す。
            peak_pts_vp, valley_pts_vp = _detect_peak_valley_points(line_mesh, samples_xy, center_vp)
            peak_pts_exp, valley_pts_exp = _detect_peak_valley_points(line_mesh, pts_xy, center_exp)
            assert peak_pts_vp and valley_pts_vp, f"{shape}: viewport-style山/谷検出に失敗"
            assert peak_pts_exp and valley_pts_exp, f"{shape}: export-style山/谷検出に失敗"

            # (a)基準面積・(b)基準距離 (viewport-style / export-style サンプルで直接呼び出し)
            polys_100_vp = line_mesh.main_line_dynamic_band_polys(
                entry, samples, center_vp, line_width_m, valley_sharp,
            )
            polys_100_export = line_mesh.main_line_dynamic_band_polys(
                entry, pts_m, center_exp, line_width_m, valley_sharp,
            )
            assert polys_100_vp, f"{shape}: 100%/100%で主線帯ポリゴンが空 (viewport-style)"
            assert polys_100_export, f"{shape}: 100%/100%で主線帯ポリゴンが空 (export-style)"
            area_100_vp = _area_mm2(polys_100_vp, scale=1000.0)
            assert area_100_vp > 0.0

            outer_ring_100_vp, _ = _outer_inner_rings_mm(polys_100_vp, scale=1000.0)
            outer_ring_100_exp, _ = _outer_inner_rings_mm(polys_100_export, scale=1000.0)
            d_peak_100_vp = _distances_to_ring(peak_pts_vp, outer_ring_100_vp, scale=1000.0)
            d_valley_100_vp = _distances_to_ring(valley_pts_vp, outer_ring_100_vp, scale=1000.0)
            d_peak_100_exp = _distances_to_ring(peak_pts_exp, outer_ring_100_exp, scale=1000.0)
            d_valley_100_exp = _distances_to_ring(valley_pts_exp, outer_ring_100_exp, scale=1000.0)

            # =================================================================
            # 動的 (山60%/谷20%): (a)効くこと (b)画面と一致 (d)フチ追従
            # =================================================================
            entry.line_peak_width_pct = PEAK_PCT
            entry.line_valley_width_pct = VALLEY_PCT
            rebuild(entry)

            is_dyn, vpct, ppct, bz = line_mesh._line_dynamic_width_params(entry)  # noqa: SLF001
            assert is_dyn and not bz, f"{shape}: 60%/20%なのに is_dynamic/both_zero 判定が想定外"
            assert abs(vpct - VALLEY_PCT) < 1.0e-6 and abs(ppct - PEAK_PCT) < 1.0e-6

            # --- (a) 効くこと: viewport-styleサンプルで面積が基準より有意に減る ---
            polys_dyn_vp = line_mesh.main_line_dynamic_band_polys(
                entry, samples, center_vp, line_width_m, valley_sharp,
            )
            assert polys_dyn_vp, f"{shape}: 60%/20%で主線帯ポリゴンが空 (viewport-style)"
            area_dyn_vp = _area_mm2(polys_dyn_vp, scale=1000.0)
            assert area_dyn_vp < area_100_vp * 0.9, (
                f"{shape}: 60%/20%の主線帯面積が100%/100%と有意に違いません: "
                f"{area_dyn_vp:.3f}mm2 vs {area_100_vp:.3f}mm2"
            )

            # --- (b) 画面と一致: export-styleサンプルで同じ生成器を呼び、比較 ---
            polys_dyn_export = line_mesh.main_line_dynamic_band_polys(
                entry, pts_m, center_exp, line_width_m, valley_sharp,
            )
            assert polys_dyn_export, f"{shape}: 60%/20%で書き出し主線帯ポリゴンが空 (export-style)"
            area_dyn_export = _area_mm2(polys_dyn_export, scale=1000.0)
            rel_area_diff = abs(area_dyn_export - area_dyn_vp) / area_dyn_vp
            assert rel_area_diff < 0.10, (
                f"{shape}: 画面(viewport-style)と書き出し(export-style)の主線帯面積が"
                f"乖離しています: rel_diff={rel_area_diff:.3f} "
                f"(vp={area_dyn_vp:.3f}mm2 export={area_dyn_export:.3f}mm2)"
            )

            outer_ring_vp, _inner_ring_vp = _outer_inner_rings_mm(polys_dyn_vp, scale=1000.0)
            outer_ring_exp, inner_ring_exp = _outer_inner_rings_mm(polys_dyn_export, scale=1000.0)

            # 山頂・谷底での「100%基準距離に対する比」を測る。 mitre 結合による
            # 幾何的増幅は本体形状のみに依存し 100%側にも同じだけ乗るため、比を
            # 取れば増幅係数が相殺され、%設定に応じた縮み比だけが残る。
            d_peak_dyn_vp = _distances_to_ring(peak_pts_vp, outer_ring_vp, scale=1000.0)
            d_valley_dyn_vp = _distances_to_ring(valley_pts_vp, outer_ring_vp, scale=1000.0)
            d_peak_dyn_exp = _distances_to_ring(peak_pts_exp, outer_ring_exp, scale=1000.0)
            d_valley_dyn_exp = _distances_to_ring(valley_pts_exp, outer_ring_exp, scale=1000.0)

            ratio_peak_vp = _median_ratio(d_peak_dyn_vp, d_peak_100_vp)
            ratio_valley_vp = _median_ratio(d_valley_dyn_vp, d_valley_100_vp)
            ratio_peak_exp = _median_ratio(d_peak_dyn_exp, d_peak_100_exp)
            ratio_valley_exp = _median_ratio(d_valley_dyn_exp, d_valley_100_exp)

            for label, val in (
                ("viewport-style 山", ratio_peak_vp),
                ("export-style 山", ratio_peak_exp),
            ):
                assert abs(val - target_peak_ratio) < TOL_RATIO, (
                    f"{shape}/{label}: 山頂での縮み比が期待値からずれています: {val:.3f} "
                    f"(期待 {target_peak_ratio:.3f}, 許容 ±{TOL_RATIO})"
                )
            for label, val in (
                ("viewport-style 谷", ratio_valley_vp),
                ("export-style 谷", ratio_valley_exp),
            ):
                assert abs(val - target_valley_ratio) < TOL_RATIO, (
                    f"{shape}/{label}: 谷底での縮み比が期待値からずれています: {val:.3f} "
                    f"(期待 {target_valley_ratio:.3f}, 許容 ±{TOL_RATIO})"
                )
            assert abs(ratio_peak_vp - ratio_peak_exp) < TOL_RATIO, (
                f"{shape}: 山頂の縮み比が画面と書き出しで不一致: "
                f"vp={ratio_peak_vp:.3f} export={ratio_peak_exp:.3f}"
            )
            assert abs(ratio_valley_vp - ratio_valley_exp) < TOL_RATIO, (
                f"{shape}: 谷底の縮み比が画面と書き出しで不一致: "
                f"vp={ratio_valley_vp:.3f} export={ratio_valley_exp:.3f}"
            )

            # --- (d) フチ追従 (export-style): 外側/内側フチが細った主線に密着 ---
            outer_edge_polys = line_mesh.outer_edge_band_polys(
                entry, pts_m, center_exp, line_width_m, outer_width_m, valley_sharp,
            )
            inner_edge_polys = line_mesh.inner_edge_band_polys(
                entry, pts_m, center_exp, line_width_m, inner_width_m, valley_sharp,
            )
            assert outer_edge_polys, f"{shape}: 60%/20%で外側フチ帯ポリゴンが空 (export-style)"
            assert inner_edge_polys, f"{shape}: 60%/20%で内側フチ帯ポリゴンが空 (export-style)"

            outer_edge_rings_mm = _all_rings_mm(outer_edge_polys, scale=1000.0)
            inner_edge_rings_mm = _all_rings_mm(inner_edge_polys, scale=1000.0)

            stride_o = max(1, len(outer_ring_exp) // 80)
            probe_outer = outer_ring_exp[::stride_o]
            gap_outer = max(_min_distance_to_rings(p, outer_edge_rings_mm) for p in probe_outer)
            assert gap_outer < SEAM_TOL_MM, (
                f"{shape}: 主線帯の外周が外側フチ帯へ密着していません (最大隙間 {gap_outer:.4f}mm)"
            )

            assert inner_ring_exp is not None, f"{shape}: 主線帯の内周(穴)が見つかりません"
            stride_i = max(1, len(inner_ring_exp) // 80)
            probe_inner = inner_ring_exp[::stride_i]
            gap_inner = max(_min_distance_to_rings(p, inner_edge_rings_mm) for p in probe_inner)
            assert gap_inner < SEAM_TOL_MM, (
                f"{shape}: 主線帯の内周が内側フチ帯へ密着していません (最大隙間 {gap_inner:.4f}mm)"
            )

            layer_dyn = export_balloon.render_balloon_layer(entry, canvas_height_px=1000, dpi=110)
            assert layer_dyn is not None and layer_dyn.image is not None, (
                f"{shape}: 60%/20%の render_balloon_layer が失敗"
            )
            _save_layer_preview(layer_dyn.image, out_dir / f"{shape}_peak60_valley20.png")

            print(
                f"  [{shape}] area vp={area_dyn_vp:.3f}mm2 export={area_dyn_export:.3f}mm2 "
                f"peak_ratio vp={ratio_peak_vp:.3f} export={ratio_peak_exp:.3f} "
                f"valley_ratio vp={ratio_valley_vp:.3f} export={ratio_valley_exp:.3f} "
                f"gap outer={gap_outer:.4f} inner={gap_inner:.4f}"
            )

            # =================================================================
            # (c) 両方0%: 主線帯が消え、フチは本体基準で残る
            # =================================================================
            entry_zero = _make_balloon(context, page, balloon_op, f"std_{shape}_zero", shape)
            entry_zero.line_peak_width_pct = 0.0
            entry_zero.line_valley_width_pct = 0.0
            entry_zero.fill_opacity = 0.0  # 塗りを消し、外側フチ(白)を画素判定しやすくする
            rebuild(entry_zero)

            is_dyn_z, _vpz, _ppz, bz_z = line_mesh._line_dynamic_width_params(entry_zero)  # noqa: SLF001
            assert is_dyn_z and bz_z, f"{shape}: 0%/0%の both_zero 判定が想定外"

            assert bpy.data.objects.get(line_mesh._line_mesh_object_name(entry_zero.id)) is None, (  # noqa: SLF001
                f"{shape}: 谷/山線幅%が両方0%なのにビューポート主線メッシュが残っています"
            )
            assert bpy.data.objects.get(line_mesh._outer_edge_mesh_object_name(entry_zero.id)) is not None, (  # noqa: SLF001
                f"{shape}: 谷/山線幅%が両方0%なのにビューポート外側フチメッシュが消えています"
            )
            assert bpy.data.objects.get(line_mesh._inner_edge_mesh_object_name(entry_zero.id)) is not None, (  # noqa: SLF001
                f"{shape}: 谷/山線幅%が両方0%なのにビューポート内側フチメッシュが消えています"
            )

            outline_zero_mm = _export_outline_mm(balloon_shapes, geom, entry_zero)
            pts_zero_m, _pts_zero_xy, center_zero = _export_pts_m(export_balloon, outline_zero_mm)
            polys_main_zero = line_mesh.main_line_dynamic_band_polys(
                entry_zero, pts_zero_m, center_zero, line_width_m, valley_sharp,
            )
            assert not polys_main_zero, (
                f"{shape}: 谷/山線幅%が両方0%なのに書き出しの主線帯ポリゴンが空でありません"
            )
            outer_zero_polys = line_mesh.outer_edge_band_polys(
                entry_zero, pts_zero_m, center_zero, line_width_m, outer_width_m, valley_sharp,
            )
            inner_zero_polys = line_mesh.inner_edge_band_polys(
                entry_zero, pts_zero_m, center_zero, line_width_m, inner_width_m, valley_sharp,
            )
            assert outer_zero_polys, f"{shape}: 谷/山線幅%が両方0%なのに書き出しの外側フチが空です"
            assert inner_zero_polys, f"{shape}: 谷/山線幅%が両方0%なのに書き出しの内側フチが空です"

            layer_zero = export_balloon.render_balloon_layer(entry_zero, canvas_height_px=1000, dpi=110)
            assert layer_zero is not None and layer_zero.image is not None, (
                f"{shape}: 0%/0%の render_balloon_layer が失敗"
            )
            black_zero = _count_pixels(layer_zero.image, _is_black_opaque)
            white_zero = _count_pixels(layer_zero.image, _is_white_opaque)
            assert black_zero < black_100 * 0.05, (
                f"{shape}: 谷/山線幅%が両方0%なのに書き出し画像に主線が残っています: "
                f"{black_zero}px (基準 {black_100}px)"
            )
            assert white_zero > 300, (
                f"{shape}: 谷/山線幅%が両方0%なのに外側フチ(白)が書き出し画像にほぼ描かれていません: "
                f"{white_zero}px"
            )
            _save_layer_preview(layer_zero.image, out_dir / f"{shape}_zero_zero.png")

            print(f"  [{shape}] zero: black={black_zero}px white={white_zero}px (基準black={black_100}px)")

            # =================================================================
            # (e) 回帰ガード: 60%/20%から100%/100%へ戻したとき、書き出し結果が
            #     最初の基準 (black_100) を再現すること (キャッシュ・状態の
            #     取り残しが無いことも合わせて確認する)
            # =================================================================
            entry.line_peak_width_pct = 100.0
            entry.line_valley_width_pct = 100.0
            rebuild(entry)
            is_dyn_100b, _vpct100b, _ppct100b, _bz100b = line_mesh._line_dynamic_width_params(entry)  # noqa: SLF001
            assert not is_dyn_100b, f"{shape}: 100%/100%へ戻したのに is_dynamic が True のままです"
            layer100_again = export_balloon.render_balloon_layer(entry, canvas_height_px=1000, dpi=110)
            assert layer100_again is not None and layer100_again.image is not None, (
                f"{shape}: 100%/100%へ戻した後の render_balloon_layer が失敗"
            )
            black_100_again = _count_pixels(layer100_again.image, _is_black_opaque)
            assert abs(black_100_again - black_100) <= max(20, int(black_100 * 0.02)), (
                f"{shape}: 60%/20%から100%/100%へ戻しても書き出し結果が再現しません (回帰の疑い): "
                f"{black_100}px -> {black_100_again}px"
            )

            # =================================================================
            # (f) J方式が変わらないこと: 排他条件の直接検証
            #     (詳細な J 方式の書き出し検証は blender_balloon_j_width_pct_check.py)
            # =================================================================
            entry_j = _make_balloon(context, page, balloon_op, f"std_{shape}_j", shape, method="anchor")
            entry_j.line_peak_width_pct = PEAK_PCT
            entry_j.line_valley_width_pct = VALLEY_PCT
            rebuild(entry_j)

            export_anchor_cfg_j = export_balloon._anchor_cfg_for_export(entry_j)  # noqa: SLF001
            assert export_anchor_cfg_j is not None, f"{shape}: J方式が書き出し側で検出されません"
            is_dyn_j, _vpj, _ppj, _bzj = line_mesh._line_dynamic_width_params(entry_j)  # noqa: SLF001
            assert is_dyn_j, f"{shape}: J方式でも is_dynamic はTrueのはず (排他はexport_anchor_cfg側)"
            std_dyn_active_j = (export_anchor_cfg_j is None) and is_dyn_j
            assert not std_dyn_active_j, (
                f"{shape}: J方式なのに標準dynamic経路が有効判定になっています (排他条件の回帰)"
            )

            layer_j = export_balloon.render_balloon_layer(entry_j, canvas_height_px=1000, dpi=110)
            assert layer_j is not None and layer_j.image is not None, (
                f"{shape}: J方式の render_balloon_layer が失敗"
            )

        print(SENTINEL)
    finally:
        module = sys.modules.get(ADDON_NAME)
        if module is not None and hasattr(module, "unregister"):
            try:
                module.unregister()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
