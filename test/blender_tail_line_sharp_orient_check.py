"""Blender 実機チェック: v0.6.276 しっぽ・線種の拡張.

- しっぽ「角を尖らせる」: 結合しっぽの尖りパッチ生成 / OFF では生成されない
- しっぽ線種「線」: ストロークメッシュ生成・入り抜きで幅が絞られる・本体と結合しない
- 楕円しっぽの本体結合: 本体に重なる楕円が結合され、残りだけ個別メッシュになる
- 楕円の角度・向き (始点終点/線の向き/固定、初期値=始点終点)
- 線種「図形」の向き (線の向き/中心点)
- スキーマ・プリセットの新項目往復
- 出力 (render_balloon_layer) が各設定で成功する
"""

from __future__ import annotations

import importlib
import importlib.util
import math
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bname_dev_tail_sharp_orient"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _sub(path: str):
    return importlib.import_module(f"{MOD_NAME}.{path}")


def _poly_count(name: str) -> int:
    obj = bpy.data.objects.get(name)
    if obj is None or getattr(obj, "data", None) is None:
        return -1
    return len(obj.data.polygons)


def _make_balloon(page, balloon_op, *, x=60.0, y=150.0, w=50.0, h=40.0):
    entry = balloon_op._create_balloon_entry(
        bpy.context,
        page,
        shape="ellipse",
        x=x,
        y=y,
        w=w,
        h=h,
        parent_kind="page",
        parent_key=str(page.id),
    )
    assert entry is not None
    return entry


def _ensure(page, entry) -> None:
    balloon_curve_object = _sub("utils.balloon_curve_object")
    balloon_curve_object.ensure_balloon_curve_object(scene=bpy.context.scene, entry=entry, page=page)


def _check_line_stroke_tail(page, entry, balloon_op) -> None:
    balloon_tail_geom = _sub("utils.balloon_tail_geom")
    from importlib import import_module

    Rect = import_module(f"{MOD_NAME}.utils.balloon_shapes").Rect

    tail_index = balloon_op._add_tail_polyline(entry, [(85.0, 148.0), (95.0, 120.0)])
    assert tail_index >= 0
    tail = entry.tails[tail_index]
    tail.line_type = "line"
    tail.root_width_mm = 2.0
    tail.tip_width_mm = 2.0
    rect = Rect(0.0, 0.0, float(entry.width_mm), float(entry.height_mm))
    # 線しっぽはくさび多角形を持たない (本体と結合しない)
    assert balloon_tail_geom.polygon_for_tail(rect, tail) == []
    stroke = balloon_tail_geom.line_stroke_polygon_for_tail(rect, tail)
    assert len(stroke) >= 4, f"線しっぽのストロークが空です: {len(stroke)}"

    # 入り抜き: 抜き 80% で先端側の幅が細く絞られる
    def _tip_width(poly):
        n = len(poly) // 2
        left_tip = poly[n - 1]
        right_tip = poly[n]
        return math.hypot(left_tip[0] - right_tip[0], left_tip[1] - right_tip[1])

    width_before = _tip_width(stroke)
    tail.taper_out_percent = 80.0
    width_after = _tip_width(balloon_tail_geom.line_stroke_polygon_for_tail(rect, tail))
    assert width_after < width_before * 0.5, (width_before, width_after)
    tail.taper_in_percent = 50.0
    tapered = balloon_tail_geom.line_stroke_polygon_for_tail(rect, tail)
    root_width = math.hypot(
        tapered[0][0] - tapered[-1][0], tapered[0][1] - tapered[-1][1]
    )
    assert root_width < 0.5, f"入り 50% なのに根元が太いままです: {root_width}"

    # ビューポート: ストロークメッシュが生成される
    _ensure(page, entry)
    bid = str(entry.id)
    assert _poly_count(f"balloon_tail_stroke_{bid}") > 0, "線しっぽのメッシュがありません"
    # 三角へ戻すと撤去される
    tail.line_type = "wedge"
    _ensure(page, entry)
    assert _poly_count(f"balloon_tail_stroke_{bid}") == -1, "線しっぽメッシュが残っています"
    entry.tails.remove(tail_index)
    _ensure(page, entry)
    print("LINE_STROKE_TAIL_OK", flush=True)


def _check_sharp_corners(page, entry, balloon_op) -> None:
    tail_index = balloon_op._add_tail_polyline(entry, [(85.0, 148.0), (95.0, 115.0)])
    tail = entry.tails[tail_index]
    tail.line_type = "wedge"
    tail.root_width_mm = 6.0
    tail.tip_width_mm = 0.0
    entry.line_style = "solid"
    entry.line_width_mm = 2.0
    bid = str(entry.id)
    # OFF: 主線は丸い帯のまま (しっぽ独立メッシュは無い)
    tail.sharp_corners = False
    _ensure(page, entry)
    assert _poly_count(f"balloon_tail_main_line_mesh_{bid}") == -1, "OFF なのに独立しっぽ線メッシュがあります"
    line_obj = bpy.data.objects.get(f"balloon_line_mesh_{bid}")
    assert line_obj is not None
    off_verts = len(line_obj.data.vertices)
    # ON: 主線の帯がしっぽ先端で「抜き」状に絞られる (= 主線メッシュの形状が変わる)
    tail.sharp_corners = True
    _ensure(page, entry)
    assert _poly_count(f"balloon_tail_main_line_mesh_{bid}") == -1, "ON でも独立しっぽ線メッシュは持たない (主線側で加工)"
    line_obj = bpy.data.objects.get(f"balloon_line_mesh_{bid}")
    assert line_obj is not None
    on_verts = len(line_obj.data.vertices)
    assert on_verts != off_verts, "角を尖らせる ON で主線メッシュが変化していません"
    # 出力も成功する
    export_balloon = _sub("io.export_balloon")
    layer = export_balloon.render_balloon_layer(entry, 2048, 96)
    assert layer is not None and layer.image is not None
    tail.sharp_corners = False
    entry.tails.remove(tail_index)
    _ensure(page, entry)
    print("SHARP_CORNERS_OK", flush=True)


def _check_ellipse_merge_and_orient(page, entry, balloon_op) -> None:
    balloon_tail_geom = _sub("utils.balloon_tail_geom")
    balloon_line_mesh = _sub("utils.balloon_line_mesh")
    from importlib import import_module

    Rect = import_module(f"{MOD_NAME}.utils.balloon_shapes").Rect

    # 本体の内側から外へ伸びる楕円しっぽ → 根元側の楕円は本体と結合される
    # (フキダシは x60-110, y150-190 の楕円。始点 (85,160) は本体の内側)
    tail_index = balloon_op._add_tail_polyline(entry, [(85.0, 160.0), (100.0, 105.0)])
    tail = entry.tails[tail_index]
    tail.line_type = "ellipse_chain"
    tail.root_width_mm = 6.0
    tail.tip_width_mm = 1.5
    tail.ellipse_gap_mm = 1.0
    rect = Rect(0.0, 0.0, float(entry.width_mm), float(entry.height_mm))
    total = len(balloon_tail_geom.ellipse_chain_for_tail(rect, tail))
    assert total >= 3, f"楕円が少なすぎます: {total}"

    _ensure(page, entry)
    bid = str(entry.id)
    balloon_curve_object = _sub("utils.balloon_curve_object")
    body_obj = balloon_curve_object.find_balloon_object(bid)
    assert body_obj is not None, "フキダシ本体カーブが見つかりません"
    # 本体+楕円の結合輪郭が成立する (= tails_merged)
    samples = balloon_line_mesh._body_samples_for_line_mesh(entry, body_obj)
    assert len(samples) >= 3
    _merged, merged_flag = balloon_line_mesh._outline_samples_with_tails(entry, samples)
    assert merged_flag, "本体に重なる楕円が結合されていません"
    # 個別描画される楕円は「全楕円 - 結合された楕円」: リング面数が減る
    ellipse_polys = balloon_line_mesh.ellipse_polygons_for_entry_local_m(entry)
    balloon_tail_boolean = _sub("utils.balloon_tail_boolean")
    touching, separate = balloon_tail_boolean.split_indices_touching_body(
        [(float(s[0]), float(s[1])) for s in samples], ellipse_polys
    )
    assert touching, "本体に重なる楕円が検出されていません"
    assert separate, "全楕円が結合扱いになっています (個別の楕円が残るはず)"
    line_obj = bpy.data.objects.get(f"balloon_tail_ellipse_line_{bid}")
    assert line_obj is not None
    # リングは 1 楕円 = _ELLIPSE_SEGMENTS セグメント
    tail_ellipse_mesh = _sub("utils.balloon_tail_ellipse_mesh")
    seg = int(tail_ellipse_mesh._ELLIPSE_SEGMENTS)
    assert len(line_obj.data.polygons) == len(separate) * seg, (
        len(line_obj.data.polygons), len(separate), total
    )

    # 楕円の向き: 初期値は「始点終点」で全楕円の角度が一致する
    assert str(tail.ellipse_orient) == "start_end"
    ellipses = balloon_tail_geom.ellipse_chain_for_tail(rect, tail)
    angles = [e[4] for e in ellipses]
    assert max(angles) - min(angles) < 1.0e-9, "始点終点なのに楕円の角度がバラバラです"
    # 固定 + 角度 30 度 → 全楕円が 30 度
    tail.ellipse_orient = "fixed"
    tail.ellipse_angle_deg = 30.0
    angles = [e[4] for e in balloon_tail_geom.ellipse_chain_for_tail(rect, tail)]
    assert all(abs(a - math.radians(30.0)) < 1.0e-9 for a in angles), angles
    # 線の向き: 折れ曲がるしっぽでは楕円ごとに角度が変わる
    balloon_tail_geom.add_polyline_point(tail, (70.0, 80.0))
    tail.ellipse_orient = "line"
    tail.ellipse_angle_deg = 0.0
    angles = [e[4] for e in balloon_tail_geom.ellipse_chain_for_tail(rect, tail)]
    assert max(angles) - min(angles) > 0.1, "線の向きなのに楕円の角度が変わっていません"
    tail.ellipse_orient = "start_end"
    # 出力も成功する
    export_balloon = _sub("io.export_balloon")
    layer = export_balloon.render_balloon_layer(entry, 2048, 96)
    assert layer is not None and layer.image is not None
    print("ELLIPSE_MERGE_ORIENT_OK", flush=True)


def _check_shape_orient() -> None:
    line_decor_geom = _sub("utils.line_decor_geom")
    # 原点を囲む半径 10 の円ループに三角を並べ、「中心点」向きで
    # 頂点 (apex) が常に中心へ向くことを確認する
    loop = [
        (10.0 * math.cos(i / 48.0 * math.tau), 10.0 * math.sin(i / 48.0 * math.tau))
        for i in range(48)
    ]
    for flip_y in (False, True):
        polygons = line_decor_geom.decorations_along_loop(
            loop,
            kind="triangle",
            size=2.0,
            spacing=1.0,
            orient="center",
            center=(0.0, 0.0),
            flip_y=flip_y,
        )
        assert len(polygons) >= 5
        for poly in polygons:
            apex = poly[0]  # 単位三角形の最初の点が頂点
            cx = sum(p[0] for p in poly) / len(poly)
            cy = sum(p[1] for p in poly) / len(poly)
            apex_dist = math.hypot(apex[0], apex[1])
            centroid_dist = math.hypot(cx, cy)
            assert apex_dist < centroid_dist, (flip_y, apex_dist, centroid_dist)
    # 「線の向き」(既定) は従来どおり動く
    polygons = line_decor_geom.decorations_along_loop(
        loop, kind="circle", size=2.0, spacing=1.0
    )
    assert polygons
    print("SHAPE_ORIENT_OK", flush=True)


def _check_schema_and_presets(page, entry) -> None:
    schema = _sub("io.schema")
    tail_presets = _sub("io.tail_presets")
    tail = entry.tails[0]
    tail.line_type = "line"
    tail.sharp_corners = True
    tail.taper_in_percent = 12.5
    tail.taper_out_percent = 60.0
    tail.ellipse_angle_deg = 45.0
    tail.ellipse_orient = "fixed"
    entry.line_style = "shape"
    entry.line_shape_orient = "center"
    data = schema.balloon_entry_to_dict(entry)
    assert data["tails"][0]["lineType"] == "line"
    assert data["tails"][0]["sharpCorners"] is True
    assert abs(float(data["tails"][0]["taperInPercent"]) - 12.5) < 1.0e-4
    assert abs(float(data["tails"][0]["taperOutPercent"]) - 60.0) < 1.0e-4
    assert abs(float(data["tails"][0]["ellipseAngleDeg"]) - 45.0) < 1.0e-4
    assert data["tails"][0]["ellipseOrient"] == "fixed"
    assert data["lineShapeOrient"] == "center"
    clone = page.balloons.add()
    schema.balloon_entry_from_dict(clone, data)
    assert str(clone.tails[0].line_type) == "line"
    assert bool(clone.tails[0].sharp_corners) is True
    assert abs(float(clone.tails[0].taper_out_percent) - 60.0) < 1.0e-4
    assert str(clone.tails[0].ellipse_orient) == "fixed"
    assert str(clone.line_shape_orient) == "center"
    page.balloons.remove(len(page.balloons) - 1)

    # プリセット: 新項目を往復できる + 同梱「ペン線 (抜き)」がある
    work = bpy.context.scene.bname_work
    work_dir = Path(str(work.work_dir))
    names = [p.name for p in tail_presets.list_all_presets(work_dir)]
    assert "ペン線 (抜き)" in names, names
    path = tail_presets.save_local_preset(work_dir, tail, "新項目テスト", "")
    assert path.is_file()
    other = entry.tails.add()
    preset = tail_presets.load_preset_by_name("新項目テスト", work_dir)
    tail_presets.apply_preset_to_tail(preset, other)
    assert str(other.line_type) == "line"
    assert bool(other.sharp_corners) is True
    assert abs(float(other.taper_out_percent) - 60.0) < 1.0e-4
    assert str(other.ellipse_orient) == "fixed"
    entry.tails.remove(len(entry.tails) - 1)
    assert tail_presets.delete_local_preset(work_dir, "新項目テスト")
    entry.line_style = "solid"
    entry.line_shape_orient = "line"
    tail.line_type = "wedge"
    tail.sharp_corners = False
    print("SCHEMA_PRESET_OK", flush=True)


def main() -> None:
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bname_tail_sharp_"))
    result = bpy.ops.bname.work_new(filepath=str(temp_root / "TailSharp.bname"))
    assert result == {"FINISHED"}, result
    result = bpy.ops.bname.open_page_file("EXEC_DEFAULT", index=0)
    assert result == {"FINISHED"}, result
    balloon_op = _sub("operators.balloon_op")
    work = bpy.context.scene.bname_work
    page = work.pages[0]
    entry = _make_balloon(page, balloon_op)

    _check_line_stroke_tail(page, entry, balloon_op)
    _check_sharp_corners(page, entry, balloon_op)
    _check_ellipse_merge_and_orient(page, entry, balloon_op)
    _check_shape_orient()
    _check_schema_and_presets(page, entry)
    print("BNAME_TAIL_SHARP_ORIENT_CHECK_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        os._exit(1)
    os._exit(0)
