"""Blender実機用: ウニフラの内端輪郭がフキダシ本体の輪郭に一本化されたことの検証.

2026-07-16 仕様変更: フキダシの線種「ウニフラ」の放射線の内側終端・下地塗りは、
entry.end_shape (内端形状) ではなくフキダシ本体の形状 (entry.shape +
shape_params、カスタム形状含む) の輪郭を使う。end_shape 系フィールドは保存
互換のため残るが、フキダシの生成には影響しない (スタンドアロン効果線では
従来どおり有効)。

検証項目:
  1. shape="cloud" のウニフラで各放射線の内側端点が本体輪郭から 0.5mm 以内
  2. shape="rect" + 角丸でも同様
  3. custom 形状 (4点) でも同様
  4. end_shape を変えても生成結果が変わらない (一本化の確認)
  5. white_outline は start/end_shape=ellipse のハードコードパスが維持され、
     end_shape を変えても生成結果が変わらない
  6. fill_base_shape=True の塗りストロークが本体輪郭と一致する
  7. scene.bmanga_show_line_shape_guides が存在し既定 True
  8. 形状ガイド (generate_shape_guide_strokes + end_outline_mm) の内端ガイドが
     本体輪郭と一致し、ビューポートのフキダシガイド描画がトグルに従う

実行 (--factory-startup 必須):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python test\\blender_balloon_uniflash_inner_body_check.py
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_uniflash_inner_body"

FAILURES: list[str] = []


def _check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)
        print(f"NG: {message}", flush=True)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _distance_to_polyline_mm(point, outline) -> float:
    px, py = point
    best = float("inf")
    count = len(outline)
    for i in range(count):
        ax, ay = float(outline[i][0]), float(outline[i][1])
        bx, by = float(outline[(i + 1) % count][0]), float(outline[(i + 1) % count][1])
        dx = bx - ax
        dy = by - ay
        seg_len2 = dx * dx + dy * dy
        if seg_len2 <= 1.0e-12:
            dist = math.hypot(px - ax, py - ay)
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len2))
            dist = math.hypot(px - (ax + dx * t), py - (ay + dy * t))
        best = min(best, dist)
    return best


def _line_inner_endpoints_mm(strokes) -> list[tuple[float, float]]:
    ends = []
    for stroke in strokes:
        if str(getattr(stroke, "role", "") or "line") != "line":
            continue
        if bool(getattr(stroke, "cyclic", False)):
            continue
        points = list(getattr(stroke, "points_xyz", None) or [])
        if len(points) < 2:
            continue
        ex, ey, _ez = points[-1]
        ends.append((float(ex) * 1000.0, float(ey) * 1000.0))
    return ends


def _stroke_fingerprint(strokes):
    out = []
    for stroke in strokes:
        out.append(
            (
                str(getattr(stroke, "role", "") or ""),
                bool(getattr(stroke, "cyclic", False)),
                tuple(
                    (round(float(x), 9), round(float(y), 9), round(float(z), 9))
                    for x, y, z in (getattr(stroke, "points_xyz", None) or [])
                ),
                tuple(round(float(r), 9) for r in (getattr(stroke, "radii", None) or ())),
            )
        )
    return out


def _body_outline_mm(balloon_shapes, Rect, entry):
    rect = Rect(0.0, 0.0, float(entry.width_mm), float(entry.height_mm))
    return balloon_shapes.outline_for_entry(entry, rect)


def _stabilize_uni_flash(entry) -> None:
    """端点位置の検証で乱れ要素を止める (仕様の既定値には依存しない)."""
    entry.length_jitter_enabled = False
    entry.end_length_jitter_enabled = False
    entry.spacing_jitter_enabled = False
    entry.brush_jitter_enabled = False
    entry.bundle_enabled = False
    entry.white_underlay_enabled = False
    entry.uni_flash_offset_percent = 0.0
    entry.fill_base_shape = False


def _assert_inner_endpoints_on_body(label: str, flash_mesh, balloon_shapes, Rect, entry) -> None:
    strokes = flash_mesh.generate_flash_strokes_rect_local(entry)
    ends = _line_inner_endpoints_mm(strokes)
    _check(len(ends) >= 8, f"{label}: 放射線ストロークが不足しています ({len(ends)} 本)")
    outline = _body_outline_mm(balloon_shapes, Rect, entry)
    _check(len(outline) >= 3, f"{label}: 本体輪郭が取得できません")
    if not ends or len(outline) < 3:
        return
    worst = max(_distance_to_polyline_mm(pt, outline) for pt in ends)
    _check(
        worst <= 0.5,
        f"{label}: 内側端点が本体輪郭から離れています (最大 {worst:.3f}mm)",
    )


def _run_check() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_uniflash_inner_body_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "UniFlashInnerBody.bmanga"))
        assert "FINISHED" in result, result

        from bmanga_dev_uniflash_inner_body.core import balloon as balloon_core
        from bmanga_dev_uniflash_inner_body.core.work import get_work
        from bmanga_dev_uniflash_inner_body.operators import balloon_op, effect_line_gen
        from bmanga_dev_uniflash_inner_body.ui import overlay_effect_line
        from bmanga_dev_uniflash_inner_body.utils import (
            balloon_curve_object,
            balloon_flash_effect_line_mesh as flash_mesh,
            balloon_shapes,
            object_selection,
        )
        from bmanga_dev_uniflash_inner_body.utils.geom import Rect
        from bmanga_dev_uniflash_inner_body.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        page_key = page_stack_key(page)

        entry = balloon_op._create_balloon_entry(
            context,
            page,
            shape="ellipse",
            x=24.0,
            y=40.0,
            w=58.0,
            h=42.0,
            parent_kind="page",
            parent_key=page_key,
        )
        entry.line_style = "uni_flash"
        balloon_core.apply_balloon_line_style_defaults(entry, force=True)
        _stabilize_uni_flash(entry)

        # ---- 1. 雲形状: 内側端点が本体輪郭上にある ----
        entry.shape = "cloud"
        sp = entry.shape_params
        sp.dynamic_shape_base_kind = "ellipse"
        sp.cloud_bump_width_mm = 6.0
        sp.cloud_bump_height_mm = 4.0
        sp.cloud_offset_percent = 25.0
        sp.cloud_bump_width_jitter = 0.0
        sp.cloud_bump_height_jitter = 0.0
        sp.cloud_sub_width_ratio = 0.0
        sp.cloud_sub_height_ratio = 0.0
        sp.shape_seed = 0
        _assert_inner_endpoints_on_body("cloud", flash_mesh, balloon_shapes, Rect, entry)

        # ---- 4. end_shape を変えても生成結果が変わらない (一本化) ----
        entry.end_shape = "ellipse"
        before = _stroke_fingerprint(flash_mesh.generate_flash_strokes_rect_local(entry))
        entry.end_shape = "rect"
        entry.end_rounded_corner_enabled = True
        after = _stroke_fingerprint(flash_mesh.generate_flash_strokes_rect_local(entry))
        _check(
            before == after,
            "uni_flash: end_shape の変更が生成結果に影響しています (一本化されていません)",
        )
        entry.end_shape = "ellipse"
        entry.end_rounded_corner_enabled = False

        # ---- 6. fill_base_shape=True の塗りが本体輪郭と一致 ----
        entry.fill_base_shape = True
        strokes = flash_mesh.generate_flash_strokes_rect_local(entry)
        fills = [
            stroke
            for stroke in strokes
            if str(getattr(stroke, "role", "") or "") == "end_fill"
            and bool(getattr(stroke, "cyclic", False))
        ]
        _check(len(fills) == 1, f"cloud: 下地塗りストロークが 1 本ではありません ({len(fills)})")
        if fills:
            outline = _body_outline_mm(balloon_shapes, Rect, entry)
            fill_pts = [(float(x) * 1000.0, float(y) * 1000.0) for x, y, _z in fills[0].points_xyz]
            _check(
                len(fill_pts) == len(outline),
                f"cloud: 下地塗りの点数が本体輪郭と一致しません ({len(fill_pts)} != {len(outline)})",
            )
            if fill_pts and outline:
                worst = max(
                    math.hypot(px - float(ox), py - float(oy))
                    for (px, py), (ox, oy) in zip(fill_pts, outline)
                )
                _check(worst <= 1.0e-6, f"cloud: 下地塗りが本体輪郭とずれています (最大 {worst:.9f}mm)")
        entry.fill_base_shape = False

        # ---- 8a. 形状ガイドの内端が本体輪郭と一致 ----
        params = flash_mesh._focus_params(entry)
        center, rx, ry, body_outline = flash_mesh._base_rect_with_outline(entry)
        guides = effect_line_gen.generate_shape_guide_strokes(
            params,
            center_xy_mm=center,
            radius_xy_mm=(rx, ry),
            seed=int(sp.shape_seed),
            end_outline_mm=body_outline,
        )
        end_guides = [g for g in guides if str(getattr(g, "role", "") or "") == "end_guide"]
        start_guides = [g for g in guides if str(getattr(g, "role", "") or "") == "start_guide"]
        _check(len(end_guides) == 1, "ガイド: 内端ガイドが 1 本ではありません")
        _check(len(start_guides) == 1, "ガイド: 外端ガイドが 1 本ではありません")
        if end_guides:
            guide_pts = [(float(x) * 1000.0, float(y) * 1000.0) for x, y, _z in end_guides[0].points_xyz]
            _check(
                len(guide_pts) == len(body_outline)
                and max(
                    math.hypot(px - float(ox), py - float(oy))
                    for (px, py), (ox, oy) in zip(guide_pts, body_outline)
                )
                <= 1.0e-6,
                "ガイド: 内端ガイドが本体輪郭と一致しません",
            )

        # ---- 2. 矩形 + 角丸 ----
        entry.shape = "rect"
        entry.rounded_corner_enabled = True
        entry.rounded_corner_radius_mm = 6.0
        try:
            entry.corner_type = "rounded"
        except Exception:  # noqa: BLE001
            pass
        _assert_inner_endpoints_on_body("rect+角丸", flash_mesh, balloon_shapes, Rect, entry)

        # ---- 3. カスタム形状 (4点) ----
        entry.shape = "custom"
        entry.custom_preset_name = ""
        entry.custom_outline_json = json.dumps(
            [[0.0, 0.0], [58.0, 5.0], [52.0, 42.0], [3.0, 38.0]]
        )
        rect = Rect(0.0, 0.0, float(entry.width_mm), float(entry.height_mm))
        custom_outline = balloon_shapes.outline_for_entry(entry, rect)
        _check(
            len(custom_outline) == 4,
            f"custom: 4点カスタム輪郭が使われていません ({len(custom_outline)} 点)",
        )
        _assert_inner_endpoints_on_body("custom4点", flash_mesh, balloon_shapes, Rect, entry)

        # ---- 5. white_outline は ellipse ハードコードパスが維持される ----
        entry.shape = "ellipse"
        entry.line_style = "white_outline"
        balloon_core.apply_balloon_line_style_defaults(entry, force=True)
        wo_params = flash_mesh._white_outline_params(entry, black_brush_mm=0.3)
        _check(
            str(getattr(wo_params, "start_shape", "")) == "ellipse"
            and str(getattr(wo_params, "end_shape", "")) == "ellipse",
            "white_outline: start/end_shape=ellipse のハードコードパスが変わっています",
        )
        entry.end_shape = "ellipse"
        wo_before = _stroke_fingerprint(flash_mesh.generate_flash_strokes_rect_local(entry))
        _check(bool(wo_before), "white_outline: ストロークが生成されていません")
        entry.end_shape = "rect"
        wo_after = _stroke_fingerprint(flash_mesh.generate_flash_strokes_rect_local(entry))
        _check(
            wo_before == wo_after,
            "white_outline: end_shape の変更が生成結果に影響しています",
        )
        entry.end_shape = "ellipse"

        # ---- 7. Scene トグルが存在し既定 True ----
        scene = context.scene
        _check(
            hasattr(scene, "bmanga_show_line_shape_guides"),
            "scene.bmanga_show_line_shape_guides が登録されていません",
        )
        if hasattr(scene, "bmanga_show_line_shape_guides"):
            _check(
                bool(scene.bmanga_show_line_shape_guides) is True,
                "scene.bmanga_show_line_shape_guides の既定が True ではありません",
            )
            prop = scene.bl_rna.properties.get("bmanga_show_line_shape_guides")
            _check(
                prop is not None and bool(getattr(prop, "default", False)) is True,
                "bmanga_show_line_shape_guides の RNA 既定値が True ではありません",
            )

        # ---- 8b. フキダシ選択中のガイド描画がトグルに従う ----
        entry.line_style = "uni_flash"
        balloon_core.apply_balloon_line_style_defaults(entry, force=True)
        _stabilize_uni_flash(entry)
        body_obj = balloon_curve_object.ensure_balloon_curve_object(
            scene=context.scene, entry=entry, page=page
        )
        _check(body_obj is not None, "フキダシ本体オブジェクトを作成できません")
        try:
            context.view_layer.update()
        except Exception:  # noqa: BLE001
            pass
        object_selection.set_keys(context, [object_selection.balloon_key(page, entry)])
        collected: list[tuple[int, tuple, float]] = []

        def _collector(segments, color, width_mm):
            collected.append((len(segments), tuple(color), float(width_mm)))

        scene.bmanga_show_line_shape_guides = True
        overlay_effect_line.draw_selected_balloon_flash_guides(
            context, draw_segments_mm=_collector
        )
        _check(bool(collected), "ガイド描画: 選択中ウニフラのガイドが描かれていません")
        colors = {color for _count, color, _w in collected}
        _check(
            len(colors) >= 2,
            "ガイド描画: 外端色と内端色の両方が描かれていません",
        )
        collected.clear()
        scene.bmanga_show_line_shape_guides = False
        overlay_effect_line.draw_selected_balloon_flash_guides(
            context, draw_segments_mm=_collector
        )
        _check(not collected, "ガイド描画: トグル OFF でもガイドが描かれています")
        scene.bmanga_show_line_shape_guides = True

        # ---- UI: フキダシのウニフラでは内端形状ボックスを出さない ----
        class _RecLayout:
            def __init__(self, props=None):
                self.props = [] if props is None else props
                self.enabled = True
                self.active = True

            def box(self):
                return _RecLayout(self.props)

            def row(self, align=False):
                return _RecLayout(self.props)

            def column(self, align=False):
                return _RecLayout(self.props)

            def split(self, factor=0.5, align=False):
                return _RecLayout(self.props)

            def grid_flow(self, **_kwargs):
                return _RecLayout(self.props)

            def separator(self, **_kwargs):
                return None

            def label(self, **_kwargs):
                return None

            def prop(self, _owner, attr, **_kwargs):
                self.props.append(str(attr))
                return None

            def prop_search(self, _owner, attr, *_args, **_kwargs):
                self.props.append(str(attr))
                return None

            def operator(self, *_args, **_kwargs):
                return object()

            def template_curve_mapping(self, *_args, **_kwargs):
                return None

        from bmanga_dev_uniflash_inner_body.panels import effect_line_panel

        hidden_layout = _RecLayout()
        effect_line_panel.draw_effect_params(
            hidden_layout,
            entry,
            with_generate_button=False,
            fixed_effect_type="uni_flash",
            show_type=False,
            show_path_settings=False,
            show_end_shape=False,
        )
        _check(
            "end_shape" not in hidden_layout.props,
            "UI: show_end_shape=False でも内端形状が表示されています",
        )
        shown_layout = _RecLayout()
        effect_line_panel.draw_effect_params(
            shown_layout,
            entry,
            with_generate_button=False,
            fixed_effect_type="uni_flash",
            show_type=False,
            show_path_settings=False,
        )
        _check(
            "end_shape" in shown_layout.props,
            "UI: 既定 (スタンドアロン効果線相当) で内端形状が表示されていません",
        )
        _check(
            entry.bl_rna.properties["fill_base_shape"].name == "フキダシの形状を下地として塗る",
            "UI: fill_base_shape のラベルが「フキダシの形状を下地として塗る」になっていません",
        )

        if FAILURES:
            raise AssertionError(f"{len(FAILURES)} 件の検証失敗があります")
        print("BMANGA_BALLOON_UNIFLASH_INNER_BODY_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:  # noqa: BLE001
                pass


def _main() -> None:
    try:
        _run_check()
        sys.stdout.flush()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)
    os._exit(0)


if __name__ == "__main__":
    _main()
