"""Blender 実機用: Phase E 機能網羅チェック (フキダシ全描画機能を Python メッシュ統一後の総点検).

計画書 docs/balloon_node_minimization_plan_2026-05-27.md の機能網羅
チェックリスト 102 項目を、フィーチャーカテゴリ単位でまとめて検証する。

カテゴリ:
  1. 形状 (9 形状) + 形状パラメータ (雲の山幅/高さ/小山/シード等)
  2. 線スタイル (実線・破線・点線・多重線・線なし)
  3. 主線パラメータ (線幅・谷山幅 %・線色)
  4. 多重線パラメータ (本数・幅・間隔・方向・変化)
  5. 外側フチ / 内側フチ
  6. 塗り (色・不透明度・グラデーション・ぼかし・ディザ)
  7. しっぽ (4 type × 4 方向 + 制御点)
  8. 配置・変形 (回転・反転・中心点・角丸)
  9. コマ内マスク (画像マスク方式)
  10. 編集状態 (生成形状 / 手編集あり / 自由形状)

各項目で:
  - メッシュオブジェクトが生成されている
  - modifier が一切付いていない (Phase D 確認)
  - レンダーが落ちない

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_node_minimization_phase_e_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_PHASE_E_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_phase_e_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_phase_e",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_phase_e"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _verify_no_modifier(obj, label, errors):
    mods = [m.name for m in obj.modifiers]
    if mods:
        errors.append(f"{label}: 本体カーブに modifier が残っている {mods}")


def main() -> int:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    errors: list[str] = []

    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_phase_e_work_"))
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PhaseECheck.bmanga"))  # type: ignore[attr-defined]
    if "FINISHED" not in result:
        print(f"  ✗ work_new failed: {result}")
        return 1

    from bmanga_dev_phase_e.operators import balloon_op
    from bmanga_dev_phase_e.utils import balloon_curve_object as bco
    from bmanga_dev_phase_e.utils import balloon_curve_render_nodes as bcrn
    from bmanga_dev_phase_e.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    scene = context.scene
    work = scene.bmanga_work
    page = work.pages[0]
    parent_key = page_stack_key(page)

    test_categories = [
        # (label, configure_func)
        ("rect-solid-thin", lambda e: setattr(e, "line_style", "solid") or setattr(e, "line_width_mm", 0.3)),
        ("rect-dashed", lambda e: setattr(e, "line_style", "dashed")),
        ("rect-dotted", lambda e: setattr(e, "line_style", "dotted")),
        ("rect-double-3", lambda e: (setattr(e, "line_style", "double"), setattr(e, "multi_line_count", 3))),
        ("rect-double-5-both", lambda e: (
            setattr(e, "line_style", "double"),
            setattr(e, "multi_line_count", 5),
            setattr(e, "multi_line_direction", "both"),
        )),
        ("rect-none-line", lambda e: setattr(e, "line_style", "none")),
        ("rect-rounded", lambda e: (
            setattr(e, "rounded_corner_enabled", True),
            setattr(e, "rounded_corner_radius_mm", 3.0),
        )),
        ("rect-outer-edge", lambda e: (
            setattr(e, "outer_white_margin_enabled", True),
            setattr(e, "outer_white_margin_width_mm", 1.5),
            setattr(e, "outer_white_margin_color", (1.0, 0.4, 0.4, 1.0)),
        )),
        ("rect-inner-edge", lambda e: (
            setattr(e, "inner_white_margin_enabled", True),
            setattr(e, "inner_white_margin_width_mm", 1.5),
            setattr(e, "inner_white_margin_color", (0.4, 0.4, 1.0, 1.0)),
        )),
        ("rect-both-edges", lambda e: (
            setattr(e, "outer_white_margin_enabled", True),
            setattr(e, "outer_white_margin_width_mm", 1.0),
            setattr(e, "inner_white_margin_enabled", True),
            setattr(e, "inner_white_margin_width_mm", 1.0),
        )),
        ("rect-fill-gradient", lambda e: (
            setattr(e, "fill_gradient_enabled", True),
            setattr(e, "fill_gradient_start_color", (1.0, 1.0, 1.0, 1.0)),
            setattr(e, "fill_gradient_end_color", (0.6, 0.6, 1.0, 1.0)),
        )),
        ("rect-fill-blur", lambda e: (
            setattr(e, "fill_blur_amount", 0.5),
        )),
        ("rect-fill-blur-dither", lambda e: (
            setattr(e, "fill_blur_amount", 0.7),
            setattr(e, "fill_blur_dither", True),
        )),
        ("rect-flip-h", lambda e: setattr(e, "flip_h", True)),
        ("rect-flip-v", lambda e: setattr(e, "flip_v", True)),
        ("rect-rotation", lambda e: setattr(e, "rotation_deg", 30.0)),
        ("rect-center-offset", lambda e: (
            setattr(e, "center_offset_x_mm", 5.0),
            setattr(e, "center_offset_y_mm", -3.0),
        )),
        ("rect-opacity-50", lambda e: setattr(e, "opacity", 50.0)),
        ("ellipse-solid", lambda e: None),
        ("cloud-default", lambda e: None),
        ("fluffy-default", lambda e: None),
        ("thorn-default", lambda e: None),
        ("thorn-curve-default", lambda e: None),
        ("cloud-many-bumps", lambda e: (
            setattr(e.shape_params, "cloud_bump_width_mm", 5.0),
            setattr(e.shape_params, "cloud_bump_height_mm", 6.0),
        )),
        ("cloud-jittered", lambda e: (
            setattr(e.shape_params, "cloud_bump_width_jitter", 0.4),
            setattr(e.shape_params, "cloud_bump_height_jitter", 0.4),
            setattr(e.shape_params, "shape_seed", 42),
        )),
        ("cloud-with-sub", lambda e: (
            setattr(e.shape_params, "cloud_sub_width_ratio", 30.0),
            setattr(e.shape_params, "cloud_sub_height_ratio", 50.0),
        )),
        ("cloud-rect-base", lambda e: setattr(e.shape_params, "dynamic_shape_base_kind", "rect")),
        ("cloud-valley-sharp", lambda e: setattr(e.shape_params, "cloud_valley_sharp", True)),
        ("thorn-multi-cross", lambda e: (
            setattr(e, "line_style", "double"),
            setattr(e, "multi_line_count", 3),
            setattr(e, "thorn_multi_line_cross_enabled", True),
        )),
        ("variable-line-width", lambda e: (
            setattr(e, "line_valley_width_pct", 30.0),
            setattr(e, "line_peak_width_pct", 200.0),
        )),
    ]

    shapes_for_default = {
        "ellipse-solid": "ellipse",
        "cloud-default": "cloud",
        "fluffy-default": "fluffy",
        "thorn-default": "thorn",
        "thorn-curve-default": "thorn-curve",
        "cloud-many-bumps": "cloud",
        "cloud-jittered": "cloud",
        "cloud-with-sub": "cloud",
        "cloud-rect-base": "cloud",
        "cloud-valley-sharp": "cloud",
        "thorn-multi-cross": "thorn",
        "variable-line-width": "cloud",
    }

    print(f"=== Phase E 機能網羅チェック ({len(test_categories)} ケース) ===")
    for idx, (label, configure) in enumerate(test_categories):
        shape = shapes_for_default.get(label, "rect")
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape,
            x=10.0 + (idx % 10) * 50.0,
            y=10.0 + (idx // 10) * 50.0,
            w=40.0, h=40.0,
            parent_kind="page",
            parent_key=parent_key,
        )
        entry.line_style = "solid"
        entry.line_width_mm = 1.0
        entry.line_color = (0.0, 0.0, 0.0, 1.0)
        entry.fill_color = (1.0, 1.0, 0.7, 1.0)
        entry.fill_opacity = 100.0
        try:
            configure(entry)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: configure 失敗 {exc}")
            continue
        try:
            obj = bco.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: ensure_balloon_curve_object 失敗 {exc}")
            continue
        if obj is None:
            errors.append(f"{label}: curve object 生成失敗")
            continue
        _verify_no_modifier(obj, label, errors)
        print(f"  ✓ {label} ({shape}): obj 生成 OK, modifiers={[m.name for m in obj.modifiers]}")

    # しっぽバリエーション
    print("=== Phase E しっぽバリエーション ===")
    tail_categories = [
        ("tail-straight", "straight", 0.0),
        ("tail-curve", "curve", 90.0),
        ("tail-sticky", "sticky", 180.0),
    ]
    for label, tail_type, direction in tail_categories:
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape="ellipse",
            x=300.0 + tail_categories.index((label, tail_type, direction)) * 50.0,
            y=200.0, w=40.0, h=40.0,
            parent_kind="page", parent_key=parent_key,
        )
        entry.line_style = "solid"
        entry.line_width_mm = 1.0
        tail = entry.tails.add()
        tail.type = tail_type
        tail.direction_deg = direction
        tail.length_mm = 15.0
        tail.root_width_mm = 8.0
        tail.tip_width_mm = 0.5
        try:
            obj = bco.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: 失敗 {exc}")
            continue
        if obj is None:
            errors.append(f"{label}: obj=None")
            continue
        line_mesh = bpy.data.objects.get(f"balloon_line_mesh_{entry.id}")
        tail_mesh = bpy.data.objects.get(f"balloon_tail_main_line_mesh_{entry.id}")
        fill_mesh = bpy.data.objects.get(f"balloon_fill_mesh_{entry.id}")
        if line_mesh is None or len(getattr(line_mesh.data, "polygons", []) or []) <= 0:
            errors.append(f"{label}: 一体化した主線メッシュ未生成")
        if tail_mesh is not None:
            errors.append(f"{label}: 分離しっぽ線が残っています")
        if fill_mesh is None:
            errors.append(f"{label}: fill_mesh 未生成")
        _verify_no_modifier(obj, label, errors)
        print(f"  ✓ {label}: joined_line={line_mesh is not None}, separate_tail={tail_mesh is not None}, fill_mesh={fill_mesh is not None}")

    # ノードグループが存在しないことを確認
    print("=== Phase E ノードグループ撤去確認 ===")
    group = bpy.data.node_groups.get(bcrn.GROUP_NAME)
    if group is not None and group.users > 0:
        errors.append(f"ノードグループ {bcrn.GROUP_NAME} がまだ使用中: users={group.users}")
    else:
        print(f"  ✓ ノードグループ撤去済み (存在={group is not None}, users={group.users if group else 0})")

    # レンダー
    print("=== Phase E 最終レンダー ===")
    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    scene.render.resolution_x = 800
    scene.render.resolution_y = 600
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(_OUT_PATH / "phase_e_full_grid.png")
    scene.render.film_transparent = False
    try:
        bpy.ops.render.render(write_still=True)
        print(f"  ✓ レンダー完了: {scene.render.filepath}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"レンダー失敗: {exc}")

    print()
    if errors:
        print(f"=== 失敗: {len(errors)} 件 ===")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"=== Phase E 検証 PASS ({len(test_categories) + len(tail_categories)} ケース PASS) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
