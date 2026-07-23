# -*- coding: utf-8 -*-
"""「角を尖らせる」新方式J (頂点距離方式) の実機チェック.

実行: blender.exe --background --factory-startup --python-exit-code 1 \
        --python test/blender_balloon_anchor_sharp_method_check.py

検証内容 (2026-07-23 承認仕様):
- sharp_corner_method="anchor" のトゲ曲線で主線・フチ・多重線メッシュが生成される
- 山の頂点で「外側輪郭の角 ↔ 内側輪郭の角」の距離 = 線幅 × 山の線幅倍率
- 谷の頂点で同距離 = 線幅 × 谷の線幅倍率 (モジュール契約)
- 既定 (method="miter") では従来のミター形状のまま (先端が伸びる) = 方式が分離
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
ADDON_NAME = "bmanga_dev_anchor_sharp"

LINE_WIDTH_MM = 1.56
PEAK_SCALE = 1.5
VALLEY_SCALE = 0.5
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


def _mesh_loops_mm(name: str):
    obj = bpy.data.objects.get(name)
    assert obj is not None, f"メッシュオブジェクトが見つかりません: {name}"
    mesh = obj.data
    loops = _boundary_loops(mesh)
    verts = mesh.vertices
    return [
        [(verts[i].co.x * MM, verts[i].co.y * MM) for i in loop]
        for loop in loops
    ]


def _make_balloon(context, page, balloon_op, *, method: str):
    entry = balloon_op._create_balloon_entry(  # noqa: SLF001
        context, page, shape="thorn-curve", x=100.0, y=100.0, w=54.0, h=71.5,
    )
    entry.line_width_mm = LINE_WIDTH_MM
    entry.line_style = "double"
    entry.multi_line_count = 3
    entry.multi_line_width_mm = 0.3
    entry.multi_line_spacing_mm = 0.4
    entry.multi_line_direction = "both"
    entry.outer_white_margin_enabled = True
    entry.outer_white_margin_width_mm = 1.0
    entry.inner_white_margin_enabled = True
    entry.inner_white_margin_width_mm = 1.0
    sp = entry.shape_params
    sp.cloud_valley_sharp = True
    sp.sharp_corner_method = method
    sp.sharp_peak_width_scale = PEAK_SCALE
    sp.sharp_valley_width_scale = VALLEY_SCALE
    sp.cloud_bump_width_mm = 10.0
    sp.cloud_bump_height_mm = 9.88
    sp.cloud_offset_percent = 50.0
    sp.cloud_sub_width_ratio = 40.3
    sp.cloud_sub_height_ratio = 51.5
    sp.shape_seed = 0
    return entry


def _top_corner_distance_mm(balloon_id: str) -> float:
    """主線メッシュの最上部で、外周リング頂点と内周リング頂点の最近対距離を測る.

    新方式J では最上部の山アンカーの「外側の角 ↔ 内側の角」距離に一致する
    (= 線幅 × 山の線幅倍率)。ミター方式では先端が伸びるため大きく異なる。
    """
    loops = _mesh_loops_mm(f"balloon_line_mesh_{balloon_id}")
    assert len(loops) >= 2, f"主線の境界ループが不足: {len(loops)}"

    def top_point(ring):
        return max(ring, key=lambda p: p[1])

    by_top = sorted(loops, key=lambda r: top_point(r)[1])
    inner_ring, outer_ring = by_top[-2], by_top[-1]
    outer_top = top_point(outer_ring)
    inner_top = top_point(inner_ring)
    return math.hypot(outer_top[0] - inner_top[0], outer_top[1] - inner_top[1])


def main() -> None:
    import tempfile

    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_anchor_sharp_"))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    try:
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "AnchorSharp.bmanga"))
        assert result == {"FINISHED"}, f"一時作品を作成できません: {result}"
        balloon_op = _submodule("operators.balloon_op")
        balloon_curve_object = _submodule("utils.balloon_curve_object")
        anchor_band = _submodule("utils.balloon_anchor_band")
        line_mesh = _submodule("utils.balloon_line_mesh")
        get_work = _submodule("core.work").get_work

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        assert work is not None and len(work.pages) > 0, "一時作品のページがありません"
        page = work.pages[0]

        # --- 新方式J ---
        entry = _make_balloon(context, page, balloon_op, method="anchor")
        balloon_curve_object.ensure_balloon_curve_object(
            scene=scene, entry=entry, page=page, force_regenerate=True,
        )
        # 主線: 最上部の角距離 = 線幅 × 山倍率
        dist = _top_corner_distance_mm(entry.id)
        expected = LINE_WIDTH_MM * PEAK_SCALE
        assert abs(dist - expected) < 0.12, (
            f"新方式Jの山の角距離が仕様と不一致: {dist:.3f}mm (期待 {expected}mm)"
        )
        # フチ・多重線メッシュも生成されること
        for name in (
            f"balloon_outer_edge_mesh_{entry.id}",
            f"balloon_inner_edge_mesh_{entry.id}",
            f"balloon_multi_line_mesh_{entry.id}",
        ):
            assert bpy.data.objects.get(name) is not None, f"メッシュ未生成: {name}"
        multi_loops = _mesh_loops_mm(f"balloon_multi_line_mesh_{entry.id}")
        assert len(multi_loops) >= 8, f"多重線のリング数が想定より少ない: {len(multi_loops)}"

        # 谷の角距離 (モジュール契約): 谷アンカーで 線幅×谷倍率
        body_obj = None
        for obj in bpy.data.objects:
            if obj.name.endswith("__balloon__" + entry.id):
                body_obj = obj
                break
        assert body_obj is not None
        body_samples = line_mesh._body_samples_for_line_mesh(entry, body_obj)  # noqa: SLF001
        samples, _joined = line_mesh._outline_samples_with_tails(entry, body_samples)  # noqa: SLF001
        pts = [(float(s[0]), float(s[1])) for s in samples]
        det = anchor_band.detect_anchors(pts)
        assert det is not None, "アンカー検出失敗"
        half_m = LINE_WIDTH_MM * 0.001 * 0.5
        valley_anchors = [vi for vi in det["anchors"] if not det["is_peak"][vi]]
        assert valley_anchors, "谷アンカーが見つからない"
        vi = valley_anchors[0]
        v = det["pts"][vi]
        b = det["bis"][vi]
        oc = (v[0] + half_m * VALLEY_SCALE * b[0], v[1] + half_m * VALLEY_SCALE * b[1])
        ic = (v[0] - half_m * VALLEY_SCALE * b[0], v[1] - half_m * VALLEY_SCALE * b[1])
        outline_hi = anchor_band.anchor_offset_outline(det, half_m, PEAK_SCALE, VALLEY_SCALE)
        outline_lo = anchor_band.anchor_offset_outline(det, -half_m, PEAK_SCALE, VALLEY_SCALE)
        assert outline_hi and outline_lo

        def near(ring, p, tol_m):
            return min(math.hypot(x - p[0], y - p[1]) for x, y in ring) < tol_m

        assert near(outline_hi, oc, 5.0e-5), "谷の外側の角が外側輪郭上にない"
        assert near(outline_lo, ic, 5.0e-5), "谷の内側の角が内側輪郭上にない"

        # --- 保存スキーマ往復で新プロパティが保持されること (保存/複製/クリップボード) ---
        schema = _submodule("io.schema")
        data = schema.balloon_entry_to_dict(entry)
        sp_json = data.get("shapeParams", {})
        assert str(sp_json.get("sharpCornerMethod")) == "anchor", "方式がスキーマに保存されない"
        assert abs(float(sp_json.get("sharpPeakWidthScale", 0.0)) - PEAK_SCALE) < 1.0e-6
        assert abs(float(sp_json.get("sharpValleyWidthScale", 0.0)) - VALLEY_SCALE) < 1.0e-6
        entry_rt = _make_balloon(context, page, balloon_op, method="miter")
        schema.balloon_entry_from_dict(entry_rt, data)
        sp_rt = entry_rt.shape_params
        assert str(sp_rt.sharp_corner_method) == "anchor", "方式が読込で復元されない"
        assert abs(float(sp_rt.sharp_peak_width_scale) - PEAK_SCALE) < 1.0e-6
        assert abs(float(sp_rt.sharp_valley_width_scale) - VALLEY_SCALE) < 1.0e-6

        # --- 既定 (miter) は従来形状のまま = J とは別物 ---
        entry_m = _make_balloon(context, page, balloon_op, method="miter")
        balloon_curve_object.ensure_balloon_curve_object(
            scene=scene, entry=entry_m, page=page, force_regenerate=True,
        )
        dist_m = _top_corner_distance_mm(entry_m.id)
        assert dist_m > expected * 1.5, (
            f"miter方式の先端がJと同じになっている (方式分離が壊れている): {dist_m:.3f}mm"
        )

        print("BMANGA_BALLOON_ANCHOR_SHARP_METHOD_CHECK_OK")
    finally:
        module = sys.modules.get(ADDON_NAME)
        if module is not None and hasattr(module, "unregister"):
            try:
                module.unregister()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    main()
