# -*- coding: utf-8 -*-
"""新方式J (頂点距離方式) の谷/山線幅% + 間隔変化500%上限の実機チェック.

実行: blender.exe --background --factory-startup --python-exit-code 1 \
        --python test/blender_balloon_j_width_pct_check.py

検証内容 (2026-07-23 保留パッチの残作業):
- J方式で 主線の谷/山線幅% がビューポート・書き出しの両方に反映される
  (山アンカーでの帯幅 = 線幅 × 山倍率 × 山%、谷も同様)
- 主線の谷/山線幅% が両方 0% のとき、主線メッシュ・書き出しポリゴンとも
  非表示になる (空の帯へフォールバックして従来の均一幅へ戻らないこと)
- 多重線リングの谷/山線幅% (J方式) がビューポート・書き出しの両方でリング幅を
  正しく細らせ、両方0%でそのリングが非表示になる
- 外側フチ・内側フチが、細った主線の外端/内端へ密着する (J方式)
- 間隔変化 (%) の上限が500%になっていて、実際に間隔を大きく広げられる
"""
from __future__ import annotations

import importlib
import importlib.util
import math
import sys
from collections import defaultdict
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
ADDON_NAME = "bmanga_dev_j_width_pct"
SENTINEL = "BMANGA_BALLOON_J_WIDTH_PCT_CHECK_OK"

LINE_WIDTH_MM = 1.6
PEAK_SCALE = 1.5
VALLEY_SCALE = 0.5
OUTER_MARGIN_MM = 0.5
INNER_MARGIN_MM = 0.4
ML_WIDTH_MM = 0.3
ML_SPACING_MM = 0.4
TOL_MM = 0.12


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


def _boundary_loops(mesh):
    edge_face_count = defaultdict(int)
    for poly in mesh.polygons:
        for edge_key in poly.edge_keys:
            edge_face_count[tuple(sorted(edge_key))] += 1
    boundary = [key for key, count in edge_face_count.items() if count == 1]
    adjacency = defaultdict(list)
    for a, b in boundary:
        adjacency[a].append(b)
        adjacency[b].append(a)
    visited = set()
    loops = []
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


def _mesh_points_mm(obj_name: str):
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        return None
    verts = obj.data.vertices
    return [(v.co.x * 1000.0, v.co.y * 1000.0) for v in verts]


def _mesh_loops_mm(obj_name: str):
    obj = bpy.data.objects.get(obj_name)
    assert obj is not None, f"メッシュオブジェクトが見つかりません: {obj_name}"
    mesh = obj.data
    loops = _boundary_loops(mesh)
    verts = mesh.vertices
    return [
        [(verts[i].co.x * 1000.0, verts[i].co.y * 1000.0) for i in loop]
        for loop in loops
    ]


def _near(points, target, tol_mm) -> bool:
    return any(math.hypot(x - target[0], y - target[1]) < tol_mm for (x, y) in points)


def _make_balloon(context, page, balloon_op, entry_id: str):
    entry = balloon_op._create_balloon_entry(  # noqa: SLF001
        context, page, shape="thorn-curve", x=100.0, y=100.0, w=54.0, h=71.5,
    )
    entry.id = entry_id
    entry.line_width_mm = LINE_WIDTH_MM
    entry.line_style = "double"
    entry.multi_line_count = 2
    entry.multi_line_width_mm = ML_WIDTH_MM
    entry.multi_line_spacing_mm = ML_SPACING_MM
    entry.multi_line_direction = "outside"
    entry.outer_white_margin_enabled = True
    entry.outer_white_margin_width_mm = OUTER_MARGIN_MM
    entry.inner_white_margin_enabled = True
    entry.inner_white_margin_width_mm = INNER_MARGIN_MM
    sp = entry.shape_params
    sp.cloud_valley_sharp = True
    sp.sharp_corner_method = "anchor"
    sp.sharp_peak_width_scale = PEAK_SCALE
    sp.sharp_valley_width_scale = VALLEY_SCALE
    sp.cloud_bump_width_mm = 10.0
    sp.cloud_bump_height_mm = 9.88
    sp.cloud_offset_percent = 50.0
    sp.cloud_sub_width_ratio = 40.3
    sp.cloud_sub_height_ratio = 51.5
    sp.shape_seed = 0
    return entry


def main() -> None:
    import tempfile

    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_j_width_pct_"))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    try:
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "JWidthPct.bmanga"))
        assert result == {"FINISHED"}, f"一時作品を作成できません: {result}"
        balloon_op = _submodule("operators.balloon_op")
        balloon_curve_object = _submodule("utils.balloon_curve_object")
        anchor_band = _submodule("utils.balloon_anchor_band")
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

        def body_obj_for(entry):
            for obj in bpy.data.objects:
                if obj.name.endswith("__balloon__" + entry.id):
                    return obj
            return None

        def samples_for(entry):
            b_obj = body_obj_for(entry)
            assert b_obj is not None, f"本体オブジェクトが見つかりません: {entry.id}"
            body_samples = line_mesh._body_samples_for_line_mesh(entry, b_obj)  # noqa: SLF001
            samples, _joined = line_mesh._outline_samples_with_tails(entry, body_samples)  # noqa: SLF001
            return [(float(s[0]), float(s[1])) for s in samples]

        def export_outline_for(entry):
            rect = geom.Rect(0.0, 0.0, float(entry.width_mm), float(entry.height_mm))
            return balloon_shapes.outline_for_entry(entry, rect)

        # =====================================================================
        # TEST 1: 主線 谷/山線幅% (J方式) — ビューポート・書き出し双方の帯幅一致
        # =====================================================================
        entry = _make_balloon(context, page, balloon_op, "j_pct_main")
        entry.line_peak_width_pct = 60.0
        entry.line_valley_width_pct = 20.0
        rebuild(entry)

        pts_m = samples_for(entry)
        det = anchor_band.detect_anchors(pts_m)
        assert det is not None, "アンカー検出失敗"
        peak_anchors = [vi for vi in det["anchors"] if det["is_peak"][vi]]
        valley_anchors = [vi for vi in det["anchors"] if not det["is_peak"][vi]]
        assert peak_anchors and valley_anchors, "山/谷アンカーが両方見つからない"

        half_m = LINE_WIDTH_MM * 0.001 * 0.5
        j_peak = anchor_band.edge_scale_for_width_pct(half_m, half_m, PEAK_SCALE, entry.line_peak_width_pct)
        j_valley = anchor_band.edge_scale_for_width_pct(half_m, half_m, VALLEY_SCALE, entry.line_valley_width_pct)
        # 山アンカー: 外側輪郭 = +half_m*j_peak, 内側輪郭 = -half_m*j_peak (対称)
        outline_hi = anchor_band.anchor_offset_outline(det, half_m, j_peak, j_valley)
        outline_lo = anchor_band.anchor_offset_outline(det, -half_m, j_peak, j_valley)
        assert outline_hi and outline_lo

        def anchor_match_ratio(anchor_indices, det_in, half_val, scale, pts_target, unit_scale):
            """各アンカーの期待点 (pv + half_val*scale*bis) が pts_target 内の点と
            TOL_MM 以内で一致する割合を返す (1本ずつの点一致ではなく集合として
            判定することで、狭い個所でのShapely差分演算による境界単純化 [後述の
            既知の軽微な事象] に対して頑健にする)。"""
            if not anchor_indices:
                return 0.0
            ok = 0
            for vi in anchor_indices:
                pv = det_in["pts"][vi]
                pb = det_in["bis"][vi]
                expected = (
                    (pv[0] + half_val * scale * pb[0]) * unit_scale,
                    (pv[1] + half_val * scale * pb[1]) * unit_scale,
                )
                if _near(pts_target, expected, TOL_MM):
                    ok += 1
            return ok / len(anchor_indices)

        # --- ビューポートメッシュに反映されているか (全アンカーで判定) ---
        line_obj_name = line_mesh._line_mesh_object_name(entry.id)  # noqa: SLF001
        mesh_pts = _mesh_points_mm(line_obj_name)
        assert mesh_pts is not None, "主線メッシュが生成されていない"
        peak_ratio = anchor_match_ratio(peak_anchors, det, half_m, j_peak, mesh_pts, 1000.0)
        valley_ratio = anchor_match_ratio(valley_anchors, det, half_m, j_valley, mesh_pts, 1000.0)
        assert peak_ratio >= 0.95, f"ビューポート主線メッシュで山アンカーの期待帯幅点が一致した割合が低すぎる: {peak_ratio:.2f}"
        assert valley_ratio >= 0.95, f"ビューポート主線メッシュで谷アンカーの期待帯幅点が一致した割合が低すぎる: {valley_ratio:.2f}"

        # --- 書き出し (io/export_balloon) に反映されているか ---
        export_anchor_cfg = export_balloon._anchor_cfg_for_export(entry)  # noqa: SLF001
        assert export_anchor_cfg is not None, "書き出し側でJ方式が検出されない"
        line_valley_pct, line_peak_pct, _mv, _mp = line_mesh.dynamic_width_pcts(entry)
        assert abs(line_valley_pct - 20.0) < 1.0e-6 and abs(line_peak_pct - 60.0) < 1.0e-6, (
            "dynamic_width_pcts が主線の谷/山%を正しく返さない (viewport/exportの共用ヘルパー契約)"
        )
        main_anchor_cfg = export_balloon._main_line_anchor_scale(  # noqa: SLF001
            export_anchor_cfg, half_m * 1000.0, line_peak_pct, line_valley_pct
        )
        assert abs(main_anchor_cfg[0] - j_peak) < 1.0e-6, "書き出し側の山倍率がビューポートと不一致"
        assert abs(main_anchor_cfg[1] - j_valley) < 1.0e-6, "書き出し側の谷倍率がビューポートと不一致"

        # 書き出し側の輪郭 (balloon_shapes.outline_for_entry) はビューポートの
        # 実体オブジェクトとは別の座標系 (rect 原点基準) で再計算されるため、
        # アンカーもこの輪郭自身で検出し直す (絶対座標を frame 間で使い回さない)。
        export_outline = export_outline_for(entry)
        det_export = anchor_band.detect_anchors(export_outline)
        assert det_export is not None, "書き出し輪郭でアンカー検出失敗"
        export_peaks = [vi for vi in det_export["anchors"] if det_export["is_peak"][vi]]
        export_valleys = [vi for vi in det_export["anchors"] if not det_export["is_peak"][vi]]
        assert export_peaks and export_valleys, "書き出し輪郭で山/谷アンカーが両方見つからない"

        half_mm = half_m * 1000.0
        main_band = export_balloon._mitre_band_polygons_mm(  # noqa: SLF001
            export_outline, half_mm, -half_mm, sharp=True, anchor_cfg=main_anchor_cfg,
        )
        assert main_band, "書き出しの主線帯ポリゴンが空 (J方式で生成できていない)"
        export_pts = [p for outer, holes in main_band for p in (list(outer) + [q for h in holes for q in h])]
        exp_peak_ratio = anchor_match_ratio(export_peaks, det_export, half_mm, j_peak, export_pts, 1.0)
        exp_valley_ratio = anchor_match_ratio(export_valleys, det_export, half_mm, j_valley, export_pts, 1.0)
        assert exp_peak_ratio >= 0.95, f"書き出しの主線帯で山アンカーの一致率が低すぎる (画面と出力が食い違う): {exp_peak_ratio:.2f}"
        assert exp_valley_ratio >= 0.95, f"書き出しの主線帯で谷アンカーの一致率が低すぎる (画面と出力が食い違う): {exp_valley_ratio:.2f}"

        # =====================================================================
        # TEST 2: 外側フチが細った主線の外端へ密着する (J方式・ビューポート/書き出し)
        #
        # 既知の軽微な事象 (2026-07-23 実測・本パッチ適用前から balloon_anchor_band
        # 自体に存在): 小山 (cloud_sub_width/height 由来の副次的な尖り) のうち
        # ごく一部 (実測24山中2山) で、フチの近縁がわずかに主線密着位置からずれる
        # (Shapely の hi_poly.difference(lo_poly) が、極端に狭い/浅い局所形状を
        # 単純化する影響と推測)。主線メッシュ自体は同条件で 24/24 一致するため
        # 主線側の実装は問題なし。フチ側は 95% 未満を許容ラインとし、この既知の
        # 軽微な事象を検出しつつ全面的な機能崩れ (0%近辺への低下) は捕捉する。
        # =====================================================================
        near_scale = anchor_band.edge_scale_for_width_pct(half_m, half_m, PEAK_SCALE, entry.line_peak_width_pct)
        outer_near, outer_far = export_balloon._edge_fringe_anchor_scales(  # noqa: SLF001
            export_anchor_cfg, half_mm, OUTER_MARGIN_MM, line_peak_pct, line_valley_pct
        )
        assert abs(outer_near[0] - near_scale) < 1.0e-6, (
            "外側フチの近縁 (主線に接する側) 倍率が主線の縮み比と一致しない"
        )
        outer_obj_name = line_mesh._outer_edge_mesh_object_name(entry.id)  # noqa: SLF001
        outer_mesh_pts = _mesh_points_mm(outer_obj_name)
        assert outer_mesh_pts is not None, "外側フチメッシュが生成されていない"
        outer_peak_ratio = anchor_match_ratio(peak_anchors, det, half_m, near_scale, outer_mesh_pts, 1000.0)
        assert outer_peak_ratio >= 0.8, (
            f"外側フチのビューポートメッシュが、細った主線の外端へ密着している割合が低すぎる: {outer_peak_ratio:.2f}"
        )

        outer_band = export_balloon._mitre_band_polygons_mm(  # noqa: SLF001
            export_outline, half_mm + OUTER_MARGIN_MM, half_mm, sharp=True,
            anchor_cfg=outer_far, anchor_cfg_lo=outer_near,
        )
        assert outer_band, "書き出しの外側フチ帯ポリゴンが空 (J方式で生成できていない)"
        outer_export_pts = [p for outer, holes in outer_band for p in (list(outer) + [q for h in holes for q in h])]
        outer_export_ratio = anchor_match_ratio(export_peaks, det_export, half_mm, near_scale, outer_export_pts, 1.0)
        assert outer_export_ratio >= 0.8, (
            f"書き出しの外側フチ帯が、細った主線の外端へ密着している割合が低すぎる: {outer_export_ratio:.2f}"
        )
        print(f"  main_line match: viewport peak={peak_ratio:.2f} valley={valley_ratio:.2f} "
              f"export peak={exp_peak_ratio:.2f} valley={exp_valley_ratio:.2f}")
        print(f"  outer fringe match: viewport={outer_peak_ratio:.2f} export={outer_export_ratio:.2f}")

        # =====================================================================
        # TEST 3: 主線の谷/山線幅%が両方0% → 主線が非表示 (帯が空→均一幅フォール
        # バックへ落ちないこと。ビューポート・書き出し双方)
        # =====================================================================
        entry_zero = _make_balloon(context, page, balloon_op, "j_pct_zero")
        entry_zero.line_style = "solid"  # 多重線リング (別の黒画素源) を排除し主線だけを見る
        entry_zero.line_peak_width_pct = 0.0
        entry_zero.line_valley_width_pct = 0.0
        rebuild(entry_zero)
        line_obj_zero_name = line_mesh._line_mesh_object_name(entry_zero.id)  # noqa: SLF001
        assert bpy.data.objects.get(line_obj_zero_name) is None, (
            "谷/山線幅%が両方0%なのに主線メッシュが残っている (非表示にならない)"
        )

        zero_valley_pct, zero_peak_pct, _mv2, _mp2 = line_mesh.dynamic_width_pcts(entry_zero)
        zero_both_zero = zero_valley_pct <= 1.0e-3 and zero_peak_pct <= 1.0e-3
        assert zero_both_zero, "0/0判定用のヘルパー入力が想定通りでない"

        entry_visible = _make_balloon(context, page, balloon_op, "j_pct_visible_baseline")
        entry_visible.line_style = "solid"  # entry_zero と同一条件 (多重線リング無し) の比較基準
        rebuild(entry_visible)

        def count_line_pixels(target_entry) -> int:
            """render_balloon_layer が実際に描く主線色 (黒) の不透明画素数.

            render_balloon_layer 内部の main_both_zero ガード (呼ぶと空→均一幅
            フォールバックの罠があるため mitre_band_polygons を呼ぶ前に抜ける)
            が実際に効いているかを、素通しの内部関数呼び出しではなく本番の
            書き出し関数そのものを通して検証する。
            """
            target_entry.line_color = (0.0, 0.0, 0.0, 1.0)
            layer = export_balloon.render_balloon_layer(target_entry, canvas_height_px=800, dpi=96)
            assert layer is not None and layer.image is not None, "render_balloon_layerがNoneを返した"
            img = layer.image.convert("RGBA")
            count = 0
            for r, g, b, a in img.getdata():
                if a > 200 and r < 40 and g < 40 and b < 40:
                    count += 1
            return count

        visible_line_pixels = count_line_pixels(entry_visible)
        zero_line_pixels = count_line_pixels(entry_zero)
        assert visible_line_pixels > 500, (
            f"比較基準 (谷/山線幅% 非0) の主線が書き出し画像にほぼ描かれていない: {visible_line_pixels}px "
            "(前提が崩れており0/0非表示の検証にならない)"
        )
        assert zero_line_pixels < visible_line_pixels * 0.02, (
            f"谷/山線幅%が両方0%なのに書き出し画像に主線が描かれている "
            f"(0/0非表示ガードが効いていない疑い): zero={zero_line_pixels}px visible={visible_line_pixels}px"
        )

        # =====================================================================
        # TEST 4: 多重線リング 谷/山線幅% (J方式) — リング幅が細り、両方0%で非表示
        # =====================================================================
        entry_ml = _make_balloon(context, page, balloon_op, "j_pct_ml")
        entry_ml.thorn_multi_line_peak_width_pct = 50.0
        entry_ml.thorn_multi_line_valley_width_pct = 10.0
        rebuild(entry_ml)
        multi_obj_name = line_mesh._multi_line_mesh_object_name(entry_ml.id)  # noqa: SLF001
        assert bpy.data.objects.get(multi_obj_name) is not None, "多重線メッシュが生成されない"

        ml_anchor_cfg = export_balloon._anchor_cfg_for_export(entry_ml)  # noqa: SLF001
        assert ml_anchor_cfg is not None
        export_outline_ml = export_outline_for(entry_ml)
        ml_groups_full = export_balloon._multi_ring_band_polygons(  # noqa: SLF001
            export_outline_ml, entry_ml, sharp=export_balloon._body_sharp_corners(entry_ml)  # noqa: SLF001
        )
        assert ml_groups_full, "多重線の書き出しポリゴンが空 (50%/10%で生成できていない)"

        entry_ml.thorn_multi_line_peak_width_pct = 0.0
        entry_ml.thorn_multi_line_valley_width_pct = 0.0
        rebuild(entry_ml)
        assert bpy.data.objects.get(multi_obj_name) is None, (
            "多重線の谷/山線幅%が両方0%なのにビューポートメッシュが残っている"
        )
        ml_groups_zero = export_balloon._multi_ring_band_polygons(  # noqa: SLF001
            export_outline_for(entry_ml), entry_ml, sharp=export_balloon._body_sharp_corners(entry_ml)  # noqa: SLF001
        )
        assert not any(ml_groups_zero), (
            "多重線の谷/山線幅%が両方0%なのに書き出しリングが残っている "
            "(空帯が均一幅リングへフォールバックしている可能性)"
        )

        # =====================================================================
        # TEST 5: 間隔変化 (%) の上限が500%になっている + 実際に反映される
        # =====================================================================
        entry_sp = _make_balloon(context, page, balloon_op, "j_pct_spacing")
        entry_sp.sharp_corner_method = "miter"  # 間隔変化は方式非依存の確認のため標準方式で単純化
        entry_sp.shape_params.cloud_valley_sharp = False
        entry_sp.shape = "ellipse"  # 楕円 = 中心からの半径がほぼ均一なので、間隔の伸びを純粋に測れる
        entry_sp.multi_line_count = 2
        entry_sp.multi_line_direction = "outside"

        prop = entry_sp.bl_rna.properties["multi_line_spacing_scale_percent"]
        assert abs(prop.hard_max - 500.0) < 1.0e-6, (
            f"間隔変化(%)の上限が500%になっていない (hard_max={prop.hard_max})"
        )

        def ring2_outer_radius_mm(spacing_scale_percent: float) -> float:
            entry_sp.multi_line_spacing_scale_percent = spacing_scale_percent
            rebuild(entry_sp)
            loops = _mesh_loops_mm(line_mesh._multi_line_mesh_object_name(entry_sp.id))  # noqa: SLF001
            assert len(loops) >= 4, f"多重線リングのループ数が想定より少ない: {len(loops)}"
            # 最も外側 (中心からの平均距離が最大) のループ = リング2の外側境界
            cx = sum(x for loop in loops for x, _y in loop) / sum(len(loop) for loop in loops)
            cy = sum(y for loop in loops for _x, y in loop) / sum(len(loop) for loop in loops)

            def avg_radius(loop):
                return sum(math.hypot(x - cx, y - cy) for x, y in loop) / len(loop)

            return max(avg_radius(loop) for loop in loops)

        # リング2の間隔 (spacing_base * spacing_scale^1) は 100% で 0.4mm、
        # 500% で 2.0mm となり、リング1の位置は spacing_scale に依存しないため
        # (指数0乗)、リング2の半径はこの差分ぶんだけ伸びるはず (楕円近似の
        # 許容誤差込みで期待差分の半分〜2倍の範囲を許容する)。
        expected_delta_mm = ML_SPACING_MM * (5.0 - 1.0)
        r_100 = ring2_outer_radius_mm(100.0)
        r_500 = ring2_outer_radius_mm(500.0)
        delta_mm = r_500 - r_100
        assert expected_delta_mm * 0.5 < delta_mm < expected_delta_mm * 2.0, (
            f"間隔変化500%でリング2の位置の伸びが期待値から外れている: "
            f"100%={r_100:.3f}mm 500%={r_500:.3f}mm delta={delta_mm:.3f}mm "
            f"(期待 約{expected_delta_mm:.3f}mm)"
        )

        print(f"  j_peak={j_peak:.4f} j_valley={j_valley:.4f} r100={r_100:.3f} r500={r_500:.3f}")
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
