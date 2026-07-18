"""Blender 実機用: 効果線・ウニフラの「外端までの長さ」設定を確認 (2026-07-18 新機能)。

内端形状から外端形状までの長さを mm 指定できるようにした変更の検証。
既定オフでは従来どおり外端=内端の2倍、オンでは内端半径+指定mmになることを、
汎用の効果線 (集中線 / 白抜き線 / ガイド) とフキダシのウニフラ entry の両方で確認する。
"""

from __future__ import annotations

import importlib.util
import math
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_start_distance",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_start_distance"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _points_mm_for_roles(strokes, roles: set[str]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for stroke in strokes:
        role = str(getattr(stroke, "role", "line") or "line")
        if role not in roles:
            continue
        pts = getattr(stroke, "points_xyz", None) or []
        if not pts:
            continue
        x0, y0, _z0 = pts[0]
        out.append((x0 * 1000.0, y0 * 1000.0))
    return out


def _distances_from_center(points_mm, center_mm) -> list[float]:
    cx, cy = center_mm
    return [math.hypot(x - cx, y - cy) for x, y in points_mm]


def _reset_jitter(target) -> None:
    for attr, value in (
        ("length_jitter_enabled", False),
        ("end_length_jitter_enabled", False),
        ("spacing_jitter_enabled", False),
        ("bundle_enabled", False),
        ("brush_jitter_enabled", False),
        ("white_outline_width_jitter_enabled", False),
        ("white_outline_length_jitter_enabled", False),
        ("white_outline_bundle_spacing_jitter", 0.0),
    ):
        if hasattr(target, attr):
            setattr(target, attr, value)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_start_distance_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "StartDistance.bmanga"))
        assert "FINISHED" in result, result

        from bmanga_dev_start_distance.core import balloon as balloon_core
        from bmanga_dev_start_distance.core.work import get_work
        from bmanga_dev_start_distance.operators import balloon_op, effect_line_gen
        from bmanga_dev_start_distance.utils import balloon_flash_effect_line_mesh
        from bmanga_dev_start_distance.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        page_key = page_stack_key(page)

        center_mm = (110.0, 160.0)
        radius_mm = 40.0  # rx == ry の円形にして、角度に依存せず距離だけで検証する

        # --- (a)(b): 汎用の効果線 (集中線) --------------------------------
        params = context.scene.bmanga_effect_line_params
        params.effect_type = "focus"
        params.start_shape = "ellipse"
        params.end_shape = "ellipse"
        params.start_rounded_corner_enabled = False
        params.end_rounded_corner_enabled = False
        params.start_to_coma_frame = False
        params.spacing_distance_mm = 8.0
        _reset_jitter(params)

        assert params.start_distance_enabled is False, "start_distance_enabled の既定値がオンになっています"
        assert abs(float(params.start_distance_mm) - 20.0) < 1.0e-6, "start_distance_mm の既定値が20mmではありません"

        # (a) 既定オフ: 外端 = 内端の2倍 (従来挙動)
        strokes_off = effect_line_gen.generate_focus_strokes(params, center_mm, radius_mm, radius_mm, seed=0)
        points_off = _points_mm_for_roles(strokes_off, {"line"})
        assert points_off, "既定オフの集中線ストロークがありません"
        distances_off = _distances_from_center(points_off, center_mm)
        expected_off = radius_mm * 2.0
        assert all(abs(d - expected_off) < 0.5 for d in distances_off), (
            f"既定オフの外端距離が2倍(={expected_off}mm)から外れています: {distances_off}"
        )

        # (b) オン+20mm: 外端 = 内端半径 + 20mm
        params.start_distance_enabled = True
        params.start_distance_mm = 20.0
        strokes_on = effect_line_gen.generate_focus_strokes(params, center_mm, radius_mm, radius_mm, seed=0)
        points_on = _points_mm_for_roles(strokes_on, {"line"})
        assert points_on, "長さ指定オン時の集中線ストロークがありません"
        distances_on = _distances_from_center(points_on, center_mm)
        expected_on = radius_mm + 20.0
        assert all(abs(d - expected_on) < 0.5 for d in distances_on), (
            f"長さ指定オン時の外端距離が期待値(={expected_on}mm)から外れています: {distances_on}"
        )

        # 長さ0mm指定なら内端と外端が一致する (境界値)
        params.start_distance_mm = 0.0
        strokes_zero = effect_line_gen.generate_focus_strokes(params, center_mm, radius_mm, radius_mm, seed=0)
        distances_zero = _distances_from_center(_points_mm_for_roles(strokes_zero, {"line"}), center_mm)
        assert distances_zero and all(abs(d - radius_mm) < 0.5 for d in distances_zero), (
            f"長さ0mm指定で内端と外端が一致していません: {distances_zero}"
        )
        params.start_distance_mm = 20.0

        # (e) 外端ガイド (generate_shape_guide_strokes) も指定長へ追従する
        guides = effect_line_gen.generate_shape_guide_strokes(
            params, center_xy_mm=center_mm, radius_xy_mm=(radius_mm, radius_mm)
        )
        start_guide = next((g for g in guides if str(getattr(g, "role", "") or "") == "start_guide"), None)
        assert start_guide is not None, "外端ガイドのストロークがありません"
        guide_points_mm = [(x * 1000.0, y * 1000.0) for x, y, _z in start_guide.points_xyz]
        guide_distances = _distances_from_center(guide_points_mm, center_mm)
        assert guide_distances and all(abs(d - expected_on) < 0.5 for d in guide_distances), (
            f"外端ガイドが指定長へ追従していません: {guide_distances}"
        )

        # 白抜き線 (effect_type) の実際の生成ストロークも外端が指定長へ追従する。
        # (operators/effect_line_white_outline.py 側の始点矩形が旧2倍のままだと
        #  ガイドだけ追従し実体が追従しない不整合になるため、実体側も確認する)
        params.effect_type = "white_outline"
        params.white_outline_bundle_placement = "spacing"
        params.white_outline_count = 4
        params.white_outline_bundle_spacing_deg = 45.0
        _reset_jitter(params)
        white_strokes = effect_line_gen.generate_strokes(
            params, center_xy_mm=center_mm, radius_xy_mm=(radius_mm, radius_mm), seed=0
        )
        white_points = _points_mm_for_roles(white_strokes, {"white_outline_white", "white_outline_black"})
        assert white_points, "白抜き線のストロークが生成されていません"
        white_distances = _distances_from_center(white_points, center_mm)
        # 白線・黒線はオフセット分だけ束の中心からずれるため許容を少し広げる
        assert all(abs(d - expected_on) < 3.0 for d in white_distances), (
            f"白抜き線の外端距離が指定長へ追従していません (期待値近傍 {expected_on}mm): {white_distances}"
        )
        params.effect_type = "focus"

        # --- (c): フキダシのウニフラ entry ----------------------------------
        entry = balloon_op._create_balloon_entry(
            context,
            page,
            shape="ellipse",
            x=20.0,
            y=100.0,
            w=radius_mm * 2.0,
            h=radius_mm * 2.0,
            parent_kind="page",
            parent_key=page_key,
        )
        entry.line_style = "uni_flash"
        balloon_core.apply_balloon_line_style_defaults(entry, force=True)
        entry.start_shape = "ellipse"
        entry.start_rounded_corner_enabled = False
        _reset_jitter(entry)
        entry.spacing_mode = "distance"
        entry.spacing_distance_mm = 8.0
        assert abs(float(entry.uni_flash_offset_percent) - 0.0) < 1.0e-6, "ズラし量の既定値が0%ではありません"

        balloon_center, balloon_rx, balloon_ry, _outline = balloon_flash_effect_line_mesh._base_rect_with_outline(entry)
        assert abs(balloon_rx - radius_mm) < 0.5 and abs(balloon_ry - radius_mm) < 0.5, (
            f"フキダシ本体の半径が想定と異なります: rx={balloon_rx} ry={balloon_ry}"
        )

        entry.start_distance_enabled = False
        strokes_balloon_off = balloon_flash_effect_line_mesh.generate_flash_strokes_rect_local(entry)
        balloon_points_off = _points_mm_for_roles(strokes_balloon_off, {"line"})
        assert balloon_points_off, "フキダシウニフラの既定オフでストロークがありません"
        balloon_distances_off = _distances_from_center(balloon_points_off, balloon_center)
        assert all(abs(d - expected_off) < 1.0 for d in balloon_distances_off), (
            f"フキダシウニフラの既定オフが2倍から外れています: {balloon_distances_off}"
        )

        entry.start_distance_enabled = True
        entry.start_distance_mm = 20.0
        strokes_balloon_on = balloon_flash_effect_line_mesh.generate_flash_strokes_rect_local(entry)
        balloon_points_on = _points_mm_for_roles(strokes_balloon_on, {"line"})
        assert balloon_points_on, "フキダシウニフラの長さ指定オン時にストロークがありません"
        balloon_distances_on = _distances_from_center(balloon_points_on, balloon_center)
        assert all(abs(d - expected_on) < 1.0 for d in balloon_distances_on), (
            f"フキダシウニフラの長さ指定が反映されていません: {balloon_distances_on}"
        )

        # --- (d): uni_flash_params_to_dict / from_dict の往復 --------------
        data = balloon_core.uni_flash_params_to_dict(entry)
        assert data.get("start_distance_enabled") is True, "保存データに start_distance_enabled が反映されていません"
        assert abs(float(data.get("start_distance_mm", 0.0)) - 20.0) < 1.0e-6, (
            "保存データに start_distance_mm が反映されていません"
        )
        entry.start_distance_enabled = False
        entry.start_distance_mm = 0.0
        balloon_core.uni_flash_params_from_dict(entry, data)
        assert entry.start_distance_enabled is True, "復元後に start_distance_enabled が戻っていません"
        assert abs(float(entry.start_distance_mm) - 20.0) < 1.0e-6, "復元後に start_distance_mm が戻っていません"

        print("BMANGA_EFFECT_LINE_START_DISTANCE_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
