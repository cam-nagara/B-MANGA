# -*- coding: utf-8 -*-
"""新方式J (頂点距離方式) の多重線「長さ変化」「山谷を延ばして交差」実機チェック.

実行: blender.exe --background --factory-startup --python-exit-code 1 \
        --python test/blender_balloon_j_multiline_dynamic_check.py

検証内容 (2026-07-23 J対応。AGENT_INBOX「新方式Jは長さ変化・交差に未対応」の解消):
1. J方式 + 長さ変化=100%: 多重線リングは全周のまま (山・谷アンカーの期待点が
   両方ともメッシュに存在する / ポジティブコントロール)
2. 遠い側(far)=50%: far リングだけ山アンカーの期待点が消え (山頂側が削れる)、
   谷アンカーの期待点は残る。near リングは全周のまま。
3. 主線寄り(near)=50% / far=100%: 効き方が入れ替わる (リング別独立)。
4. 「山谷を延ばして交差」ON: ピース両端が延長され、多重線メッシュの総面積が増える。
5. 谷/山の線幅% (J) と併用: pct 補正後の谷期待点に一致し、破綻しない。
6. 書き出し (io/export_balloon._multi_ring_band_polygons) がビューポートと同じ
   規則で keep 区間ピースを返す (山期待点なし / 谷期待点あり / near全周)。
7. 標準方式 (miter) の長さ変化は従来どおり動く (回帰なしスモーク)。

目視用 PNG を _verify/2026-07-23_j_multiline_dynamic/ へ出力する。
"""
from __future__ import annotations

import importlib
import importlib.util
import math
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
ADDON_NAME = "bmanga_dev_j_ml_dynamic"
SENTINEL = "BMANGA_BALLOON_J_MULTILINE_DYNAMIC_CHECK_OK"
VERIFY_DIR = ROOT / "_verify" / "2026-07-23_j_multiline_dynamic"

LINE_WIDTH_MM = 1.2
PEAK_SCALE = 1.5
VALLEY_SCALE = 0.5
ML_WIDTH_MM = 0.6
ML_SPACING_MM = 0.9
TOL_MM = 0.15


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


def _reset_work():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_j_ml_dyn_"))
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "JMlDyn.bmanga"))  # type: ignore[attr-defined]
    assert "FINISHED" in result, result
    return bpy.context


def _page_key():
    from importlib import import_module

    page_stack_key = import_module(f"{ADDON_NAME}.utils.layer_hierarchy").page_stack_key
    return page_stack_key(bpy.context.scene.bmanga_work.pages[0])


def _add_j_thorn_balloon(page, parent_key):
    entry = page.balloons.add()
    entry.id = "j_ml_dyn_thorn"
    entry.title = "j_ml_dyn"
    entry.shape = "thorn"
    entry.x_mm = 20.0
    entry.y_mm = 80.0
    entry.width_mm = 60.0
    entry.height_mm = 60.0
    entry.parent_kind = "page"
    entry.parent_key = parent_key
    entry.line_style = "double"
    entry.line_width_mm = LINE_WIDTH_MM
    entry.line_color = (0.0, 0.0, 0.0, 1.0)
    entry.fill_color = (1.0, 1.0, 1.0, 1.0)
    entry.fill_opacity = 100.0
    entry.opacity = 100.0
    entry.multi_line_count = 2
    entry.multi_line_direction = "outside"
    entry.multi_line_width_mm = ML_WIDTH_MM
    entry.multi_line_spacing_mm = ML_SPACING_MM
    entry.multi_line_width_scale_percent = 100.0
    entry.multi_line_spacing_scale_percent = 100.0
    entry.thorn_multi_line_valley_width_pct = 100.0
    entry.thorn_multi_line_peak_width_pct = 100.0
    entry.thorn_multi_line_length_scale_near_percent = 100.0
    entry.thorn_multi_line_length_scale_far_percent = 100.0
    entry.thorn_multi_line_cross_enabled = False
    sp = entry.shape_params
    sp.cloud_valley_sharp = True
    sp.sharp_corner_method = "anchor"
    sp.sharp_peak_width_scale = PEAK_SCALE
    sp.sharp_valley_width_scale = VALLEY_SCALE
    sp.cloud_bump_width_mm = 10.0
    sp.cloud_bump_height_mm = 8.0
    sp.cloud_sub_width_ratio = 0.0
    sp.cloud_sub_height_ratio = 0.0
    sp.shape_seed = 0
    return entry


def _rebuild(context, entry, page) -> None:
    bco = _submodule("utils.balloon_curve_object")
    bco.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)


def _body_obj_for(entry):
    bco = _submodule("utils.balloon_curve_object")
    return bco.find_balloon_object(entry.id)


def _samples_for(entry):
    line_mesh = _submodule("utils.balloon_line_mesh")
    b_obj = _body_obj_for(entry)
    assert b_obj is not None, f"本体オブジェクトが見つかりません: {entry.id}"
    body_samples = line_mesh._body_samples_for_line_mesh(entry, b_obj)  # noqa: SLF001
    samples, _joined = line_mesh._outline_samples_with_tails(entry, body_samples)  # noqa: SLF001
    return [(float(s[0]), float(s[1])) for s in samples]


def _multi_mesh_obj(entry):
    line_mesh = _submodule("utils.balloon_line_mesh")
    name = line_mesh._multi_line_mesh_object_name(entry.id)  # noqa: SLF001
    return bpy.data.objects.get(name)


def _multi_mesh_points_mm(entry):
    obj = _multi_mesh_obj(entry)
    assert obj is not None and obj.data is not None, "多重線メッシュが生成されていない"
    return [(v.co.x * 1000.0, v.co.y * 1000.0) for v in obj.data.vertices]


def _multi_mesh_area(entry) -> float:
    obj = _multi_mesh_obj(entry)
    assert obj is not None and obj.data is not None, "多重線メッシュが生成されていない"
    return sum(p.area for p in obj.data.polygons)


def _near(points, target, tol_mm) -> bool:
    return any(math.hypot(x - target[0], y - target[1]) < tol_mm for (x, y) in points)


def _ring_centers_mm() -> list[float]:
    """outside 2 リングの中心変位 (mm)。running の進みは実装と同一規則."""
    centers = []
    running = LINE_WIDTH_MM * 0.5
    for _ring in range(2):
        inner = running + ML_SPACING_MM
        centers.append(inner + ML_WIDTH_MM * 0.5)
        running = inner + ML_WIDTH_MM
    return centers


def _expected_pts_mm(det, delta_m, scale, anchors):
    """アンカー vi の帯縁期待点 (mm): pv + delta*scale*bis."""
    out = []
    for vi in anchors:
        pv = det["pts"][vi]
        pb = det["bis"][vi]
        out.append((
            (pv[0] + delta_m * scale * pb[0]) * 1000.0,
            (pv[1] + delta_m * scale * pb[1]) * 1000.0,
        ))
    return out


def _match_ratio(points_mm, expected_mm) -> float:
    if not expected_mm:
        return 0.0
    ok = sum(1 for e in expected_mm if _near(points_mm, e, TOL_MM))
    return ok / len(expected_mm)


def _export_groups(entry):
    eb = _submodule("io.export_balloon")
    balloon_shapes = _submodule("utils.balloon_shapes")
    geom = _submodule("utils.geom")
    rect = geom.Rect(0.0, 0.0, float(entry.width_mm), float(entry.height_mm))
    outline = balloon_shapes.outline_for_entry(entry, rect)
    groups = eb._multi_ring_band_polygons(outline, entry, sharp=eb._body_sharp_corners(entry))  # noqa: SLF001
    return outline, groups


def _export_points_mm(groups):
    pts = []
    for group in groups:
        for outer, holes in group:
            pts.extend(outer)
            for hole in holes:
                pts.extend(hole)
    return pts


def _rasterize_polys_mm(polys_groups, outline, path, scale=8.0):
    """穴つきポリゴン群 (mm) を PNG に描く (目視確認用)."""
    try:
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001
        return False
    all_pts = list(outline)
    for group in polys_groups:
        for outer, _holes in group:
            all_pts.extend(outer)
    if len(all_pts) < 3:
        return False
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    pad = 4.0
    width_px = max(1, int((maxx - minx + 2 * pad) * scale))
    height_px = max(1, int((maxy - miny + 2 * pad) * scale))

    def to_px(p):
        return ((p[0] - minx + pad) * scale, (maxy - p[1] + pad) * scale)

    base = Image.new("RGBA", (width_px, height_px), (255, 255, 255, 255))
    ImageDraw.Draw(base).polygon([to_px(p) for p in outline], outline=(170, 170, 170, 255))
    for group in polys_groups:
        layer = Image.new("RGBA", (width_px, height_px), (0, 0, 0, 0))
        dl = ImageDraw.Draw(layer)
        for outer, _holes in group:
            if len(outer) >= 3:
                dl.polygon([to_px(p) for p in outer], fill=(210, 30, 30, 255))
        for _outer, holes in group:
            for hole in holes:
                if len(hole) >= 3:
                    dl.polygon([to_px(p) for p in hole], fill=(0, 0, 0, 0))
        base = Image.alpha_composite(base, layer)
    path.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(str(path))
    return True


def _rasterize_viewport_mesh_mm(entry, path, scale=8.0):
    """ビューポート多重線メッシュの三角形を PNG に描く (目視確認用)."""
    try:
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001
        return False
    obj = _multi_mesh_obj(entry)
    if obj is None or obj.data is None:
        return False
    mesh = obj.data
    pts = [(v.co.x * 1000.0, v.co.y * 1000.0) for v in mesh.vertices]
    if len(pts) < 3:
        return False
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    pad = 4.0
    width_px = max(1, int((maxx - minx + 2 * pad) * scale))
    height_px = max(1, int((maxy - miny + 2 * pad) * scale))

    def to_px(p):
        return ((p[0] - minx + pad) * scale, (maxy - p[1] + pad) * scale)

    img = Image.new("RGB", (width_px, height_px), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for poly in mesh.polygons:
        tri = [to_px(pts[i]) for i in poly.vertices]
        if len(tri) >= 3:
            draw.polygon(tri, fill=(30, 30, 210))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path))
    return True


def main() -> None:
    context = _reset_work()
    anchor_band = _submodule("utils.balloon_anchor_band")

    page = context.scene.bmanga_work.pages[0]
    pk = _page_key()
    entry = _add_j_thorn_balloon(page, pk)

    centers_mm = _ring_centers_mm()
    d_hi_near_m = (centers_mm[0] + ML_WIDTH_MM * 0.5) * 0.001
    d_hi_far_m = (centers_mm[1] + ML_WIDTH_MM * 0.5) * 0.001

    # =====================================================================
    # TEST 1: 全周ベースライン (near/far=100%) — 山・谷とも期待点が存在する
    # =====================================================================
    _rebuild(context, entry, page)
    samples = _samples_for(entry)
    det = anchor_band.detect_anchors(samples)
    assert det is not None, "アンカー検出失敗 (ビューポート samples)"
    peaks = [vi for vi in det["anchors"] if det["is_peak"][vi]]
    valleys = [vi for vi in det["anchors"] if not det["is_peak"][vi]]
    assert len(peaks) >= 3 and len(valleys) >= 3, f"山/谷アンカー不足: {len(peaks)}/{len(valleys)}"

    mesh_pts = _multi_mesh_points_mm(entry)
    exp_peak_far = _expected_pts_mm(det, d_hi_far_m, PEAK_SCALE, peaks)
    exp_valley_far = _expected_pts_mm(det, d_hi_far_m, VALLEY_SCALE, valleys)
    exp_peak_near = _expected_pts_mm(det, d_hi_near_m, PEAK_SCALE, peaks)
    r_pk_far = _match_ratio(mesh_pts, exp_peak_far)
    r_vl_far = _match_ratio(mesh_pts, exp_valley_far)
    r_pk_near = _match_ratio(mesh_pts, exp_peak_near)
    assert r_pk_far >= 0.9, f"全周時: far リング山期待点の一致率が低い {r_pk_far:.2f}"
    assert r_vl_far >= 0.9, f"全周時: far リング谷期待点の一致率が低い {r_vl_far:.2f}"
    assert r_pk_near >= 0.9, f"全周時: near リング山期待点の一致率が低い {r_pk_near:.2f}"
    area_full = _multi_mesh_area(entry)
    _rasterize_viewport_mesh_mm(entry, VERIFY_DIR / "viewport_j_base_full.png")
    print(f"TEST1 OK (full ring: pk_far={r_pk_far:.2f} vl_far={r_vl_far:.2f} pk_near={r_pk_near:.2f})")

    # =====================================================================
    # TEST 2: far=50% — far リングの山頂側だけ削れる
    # =====================================================================
    entry.thorn_multi_line_length_scale_far_percent = 50.0
    _rebuild(context, entry, page)
    mesh_pts = _multi_mesh_points_mm(entry)
    r_pk_far = _match_ratio(mesh_pts, exp_peak_far)
    r_vl_far = _match_ratio(mesh_pts, exp_valley_far)
    r_pk_near = _match_ratio(mesh_pts, exp_peak_near)
    assert r_pk_far <= 0.1, f"far=50%: far リングの山期待点が残っている (長さ変化が効いていない) {r_pk_far:.2f}"
    assert r_vl_far >= 0.9, f"far=50%: far リングの谷期待点が消えた (谷起点で生えるべき) {r_vl_far:.2f}"
    assert r_pk_near >= 0.9, f"far=50%: near リングまで削れている (リング別独立でない) {r_pk_near:.2f}"
    area_far50 = _multi_mesh_area(entry)
    assert area_far50 < area_full * 0.98, (
        f"far=50% で多重線の総面積が減っていない: {area_far50:.3e} vs {area_full:.3e}"
    )
    _rasterize_viewport_mesh_mm(entry, VERIFY_DIR / "viewport_j_far50.png")
    print(f"TEST2 OK (far=50%: pk_far={r_pk_far:.2f} vl_far={r_vl_far:.2f} pk_near={r_pk_near:.2f})")

    # =====================================================================
    # TEST 3: near=50% / far=100% — 効き方が入れ替わる
    # =====================================================================
    entry.thorn_multi_line_length_scale_near_percent = 50.0
    entry.thorn_multi_line_length_scale_far_percent = 100.0
    _rebuild(context, entry, page)
    mesh_pts = _multi_mesh_points_mm(entry)
    r_pk_far = _match_ratio(mesh_pts, exp_peak_far)
    r_pk_near = _match_ratio(mesh_pts, exp_peak_near)
    assert r_pk_far >= 0.9, f"near=50%: far リングが削れている (入れ替わりが効いていない) {r_pk_far:.2f}"
    assert r_pk_near <= 0.1, f"near=50%: near リングの山期待点が残っている {r_pk_near:.2f}"
    _rasterize_viewport_mesh_mm(entry, VERIFY_DIR / "viewport_j_near50.png")
    print(f"TEST3 OK (near=50%: pk_far={r_pk_far:.2f} pk_near={r_pk_near:.2f})")

    # =====================================================================
    # TEST 4: 山谷を延ばして交差 — 総面積が増える
    # =====================================================================
    entry.thorn_multi_line_length_scale_near_percent = 50.0
    entry.thorn_multi_line_length_scale_far_percent = 50.0
    entry.thorn_multi_line_cross_enabled = False
    _rebuild(context, entry, page)
    area_nocross = _multi_mesh_area(entry)
    entry.thorn_multi_line_cross_enabled = True
    _rebuild(context, entry, page)
    area_cross = _multi_mesh_area(entry)
    assert area_cross > area_nocross * 1.02, (
        f"交差ONで多重線の総面積が増えない (延長が効いていない): "
        f"{area_cross:.3e} vs {area_nocross:.3e}"
    )
    _rasterize_viewport_mesh_mm(entry, VERIFY_DIR / "viewport_j_cross50.png")
    print(f"TEST4 OK (cross: area {area_nocross:.3e} -> {area_cross:.3e})")

    # =====================================================================
    # TEST 5: 谷/山の線幅% 併用 — pct 補正後の谷期待点に一致
    # =====================================================================
    entry.thorn_multi_line_cross_enabled = False
    entry.thorn_multi_line_length_scale_near_percent = 100.0
    entry.thorn_multi_line_length_scale_far_percent = 50.0
    entry.thorn_multi_line_valley_width_pct = 60.0
    entry.thorn_multi_line_peak_width_pct = 40.0
    _rebuild(context, entry, page)
    mesh_pts = _multi_mesh_points_mm(entry)
    half_ml_m = ML_WIDTH_MM * 0.001 * 0.5
    j_valley_far = anchor_band.edge_scale_for_width_pct(
        d_hi_far_m, half_ml_m, VALLEY_SCALE, 60.0
    )
    exp_valley_far_pct = _expected_pts_mm(det, d_hi_far_m, j_valley_far, valleys)
    r_vl_far_pct = _match_ratio(mesh_pts, exp_valley_far_pct)
    assert r_vl_far_pct >= 0.9, (
        f"far=50%+pct: 谷期待点 (pct補正) の一致率が低い {r_vl_far_pct:.2f}"
    )
    _rasterize_viewport_mesh_mm(entry, VERIFY_DIR / "viewport_j_far50_pct.png")
    print(f"TEST5 OK (pct併用: vl_far_pct={r_vl_far_pct:.2f})")

    # =====================================================================
    # TEST 6: 書き出し経路 — ビューポートと同じ規則でピースを返す
    # =====================================================================
    entry.thorn_multi_line_valley_width_pct = 100.0
    entry.thorn_multi_line_peak_width_pct = 100.0
    entry.thorn_multi_line_length_scale_near_percent = 100.0
    entry.thorn_multi_line_length_scale_far_percent = 50.0
    eb = _submodule("io.export_balloon")
    outline, groups = _export_groups(entry)
    assert groups, "書き出し多重線グループが空"
    exp_pts = _export_points_mm(groups)
    dense_mm = eb._densify_closed_outline_mm(outline)  # noqa: SLF001
    det_mm = anchor_band.detect_anchors(dense_mm)
    assert det_mm is not None, "アンカー検出失敗 (書き出し outline)"
    peaks_mm = [vi for vi in det_mm["anchors"] if det_mm["is_peak"][vi]]
    valleys_mm = [vi for vi in det_mm["anchors"] if not det_mm["is_peak"][vi]]

    def _exp_mm(delta_mm, scale, anchors):
        out = []
        for vi in anchors:
            pv = det_mm["pts"][vi]
            pb = det_mm["bis"][vi]
            out.append((pv[0] + delta_mm * scale * pb[0], pv[1] + delta_mm * scale * pb[1]))
        return out

    d_hi_near_mm = centers_mm[0] + ML_WIDTH_MM * 0.5
    d_hi_far_mm = centers_mm[1] + ML_WIDTH_MM * 0.5
    r_pk_far_e = _match_ratio(exp_pts, _exp_mm(d_hi_far_mm, PEAK_SCALE, peaks_mm))
    r_vl_far_e = _match_ratio(exp_pts, _exp_mm(d_hi_far_mm, VALLEY_SCALE, valleys_mm))
    r_pk_near_e = _match_ratio(exp_pts, _exp_mm(d_hi_near_mm, PEAK_SCALE, peaks_mm))
    assert r_pk_far_e <= 0.1, f"書き出し far=50%: far リング山期待点が残っている {r_pk_far_e:.2f}"
    assert r_vl_far_e >= 0.9, f"書き出し far=50%: far リング谷期待点が消えた {r_vl_far_e:.2f}"
    assert r_pk_near_e >= 0.9, f"書き出し far=50%: near リングが削れている {r_pk_near_e:.2f}"
    _rasterize_polys_mm(groups, outline, VERIFY_DIR / "export_j_far50.png")

    entry.thorn_multi_line_cross_enabled = True
    entry.thorn_multi_line_length_scale_near_percent = 50.0
    _outline_c, groups_cross = _export_groups(entry)
    assert groups_cross, "書き出し多重線グループが空 (交差ON)"
    _rasterize_polys_mm(groups_cross, _outline_c, VERIFY_DIR / "export_j_cross50.png")
    entry.thorn_multi_line_cross_enabled = False
    entry.thorn_multi_line_length_scale_near_percent = 100.0
    print(f"TEST6 OK (export: pk_far={r_pk_far_e:.2f} vl_far={r_vl_far_e:.2f} pk_near={r_pk_near_e:.2f})")

    # =====================================================================
    # TEST 7: 標準方式 (miter) の長さ変化は従来どおり (回帰なしスモーク)
    # =====================================================================
    entry.shape_params.sharp_corner_method = "miter"
    entry.thorn_multi_line_length_scale_far_percent = 50.0
    _rebuild(context, entry, page)
    area_miter = _multi_mesh_area(entry)
    assert area_miter > 0.0, "標準方式 far=50% で多重線メッシュが空 (回帰)"
    _outline_m, groups_miter = _export_groups(entry)
    assert groups_miter, "標準方式 far=50% で書き出し多重線が空 (回帰)"
    _rasterize_viewport_mesh_mm(entry, VERIFY_DIR / "viewport_miter_far50.png")
    print("TEST7 OK (標準方式スモーク)")

    print(SENTINEL)


if __name__ == "__main__":
    main()
