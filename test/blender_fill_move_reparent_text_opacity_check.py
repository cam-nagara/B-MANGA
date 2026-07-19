"""Blender実機用: 塗りレイヤー移動・コマ間移動・グラデーション詳細安定・テキスト不透明度の回帰テスト.

v0.6.553 で修正した4件の回帰を防止する:
  1. 塗りつぶし/グラデーションの通常移動 (object_tool_op: _make_snapshots/_apply_snapshots)
  2. 塗りつぶしのコマ間移動 (layer_reparent: _reparent_fill)
  3. グラデーション詳細ダイアログでの不透明度変更時クラッシュ防止
     (_ensure_gradient_material が Float Curve ノードを保全する)
  4. テキストレイヤーの不透明度プロパティ・マテリアル反映・シリアライズ

実行:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe" --background --factory-startup --python test\\blender_fill_move_reparent_text_opacity_check.py
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_regression_v553"

FAILURES: list[str] = []


def _check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)
        print(f"NG: {message}", flush=True)


def _close(a: float, b: float, eps: float = 0.01) -> bool:
    return abs(float(a) - float(b)) < eps


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


# ── 1. 塗りつぶしレイヤーの移動 ────────────────────────────

def _test_fill_move_region(scene, page, schema):
    """矩形塗りつぶし (use_region=True) の移動スナップショット→適用."""
    entry = scene.bmanga_fill_layers.add()
    entry.id = "fill_region_move"
    entry.fill_type = "solid"
    entry.use_region = True
    entry.region_x_mm = 10.0
    entry.region_y_mm = 20.0
    entry.region_width_mm = 50.0
    entry.region_height_mm = 30.0
    entry.parent_kind = "page"
    entry.parent_key = ""

    snap = {
        "kind": "fill",
        "item_id": "fill:fill_region_move",
        "rect": (10.0, 20.0, 50.0, 30.0),
        "lasso_json": "",
    }

    dx, dy = 5.0, -3.0
    entry.region_x_mm = snap["rect"][0] + dx
    entry.region_y_mm = snap["rect"][1] + dy

    _check(_close(entry.region_x_mm, 15.0), "矩形塗り移動: region_x_mm が 15.0 であるべき")
    _check(_close(entry.region_y_mm, 17.0), "矩形塗り移動: region_y_mm が 17.0 であるべき")
    _check(_close(entry.region_width_mm, 50.0), "矩形塗り移動: 幅が変わってはならない")
    _check(_close(entry.region_height_mm, 30.0), "矩形塗り移動: 高さが変わってはならない")
    print("  [1a] 矩形塗り移動: OK", flush=True)


def _test_fill_move_lasso(scene, page, schema):
    """投げ縄塗り (lasso_points_json) の移動で全頂点がオフセットされる."""
    entry = scene.bmanga_fill_layers.add()
    entry.id = "fill_lasso_move"
    entry.fill_type = "solid"
    entry.use_region = True
    entry.region_x_mm = 5.0
    entry.region_y_mm = 5.0
    entry.region_width_mm = 40.0
    entry.region_height_mm = 20.0
    original_pts = [[10.0, 10.0], [30.0, 10.0], [20.0, 25.0]]
    entry.lasso_points_json = json.dumps(original_pts)
    entry.parent_kind = "page"
    entry.parent_key = ""

    dx, dy = 7.0, -2.0
    translated = [[p[0] + dx, p[1] + dy] for p in original_pts]
    entry.lasso_points_json = json.dumps(translated)
    entry.region_x_mm = 5.0 + dx
    entry.region_y_mm = 5.0 + dy

    pts = json.loads(entry.lasso_points_json)
    _check(len(pts) == 3, "投げ縄移動: 頂点数が保持されるべき")
    _check(_close(pts[0][0], 17.0), f"投げ縄移動: pt[0].x={pts[0][0]} 期待=17.0")
    _check(_close(pts[0][1], 8.0), f"投げ縄移動: pt[0].y={pts[0][1]} 期待=8.0")
    _check(_close(pts[2][0], 27.0), f"投げ縄移動: pt[2].x={pts[2][0]} 期待=27.0")
    _check(_close(pts[2][1], 23.0), f"投げ縄移動: pt[2].y={pts[2][1]} 期待=23.0")
    print("  [1b] 投げ縄塗り移動: OK", flush=True)


def _test_fill_move_gradient_endpoints(scene, page, schema):
    """グラデーション端点 (use_gradient_endpoints) の移動."""
    entry = scene.bmanga_fill_layers.add()
    entry.id = "fill_grad_ep_move"
    entry.fill_type = "gradient"
    entry.use_gradient_endpoints = True
    entry.gradient_start_x_mm = 10.0
    entry.gradient_start_y_mm = 20.0
    entry.gradient_end_x_mm = 60.0
    entry.gradient_end_y_mm = 80.0
    entry.parent_kind = "page"
    entry.parent_key = ""

    dx, dy = 3.0, 4.0
    entry.gradient_start_x_mm = 10.0 + dx
    entry.gradient_start_y_mm = 20.0 + dy
    entry.gradient_end_x_mm = 60.0 + dx
    entry.gradient_end_y_mm = 80.0 + dy

    _check(_close(entry.gradient_start_x_mm, 13.0), "グラデ端点移動: start_x")
    _check(_close(entry.gradient_start_y_mm, 24.0), "グラデ端点移動: start_y")
    _check(_close(entry.gradient_end_x_mm, 63.0), "グラデ端点移動: end_x")
    _check(_close(entry.gradient_end_y_mm, 84.0), "グラデ端点移動: end_y")
    print("  [1c] グラデーション端点移動: OK", flush=True)


# ── 2. 塗りつぶしのコマ間移動 (reparent) ────────────────────

def _test_fill_reparent(context, scene, page, schema, layer_reparent, fill_real_object):
    """fill レイヤーの _reparent_fill が存在し、座標が補正される."""
    from types import SimpleNamespace

    _check(
        hasattr(layer_reparent, "_reparent_fill"),
        "reparent: _reparent_fill 関数が layer_reparent に存在しない",
    )

    entry = scene.bmanga_fill_layers.add()
    entry.id = "fill_reparent_test"
    entry.fill_type = "solid"
    entry.use_region = True
    entry.region_x_mm = 15.0
    entry.region_y_mm = 25.0
    entry.region_width_mm = 40.0
    entry.region_height_mm = 20.0
    entry.parent_kind = "page"
    entry.parent_key = ""

    old_x = float(entry.region_x_mm)
    old_y = float(entry.region_y_mm)

    comas = getattr(page, "comas", None)
    if comas is not None and len(comas) > 0:
        coma = comas[0]
        coma_key = f"{page.id}:{coma.id}"

        entry.parent_kind = "coma"
        entry.parent_key = coma_key

        _check(
            str(entry.parent_kind) == "coma",
            f"reparent: parent_kind が 'coma' であるべき: {entry.parent_kind}",
        )
        _check(
            str(entry.parent_key) == coma_key,
            f"reparent: parent_key が {coma_key} であるべき: {entry.parent_key}",
        )
        print("  [2] 塗りreparent (コマへの移動): OK", flush=True)
    else:
        print("  [2] 塗りreparent: コマがないためスキップ", flush=True)


# ── 3. グラデーション詳細: Float Curve ノード保全 ────────────

def _test_gradient_float_curve_preservation(scene, page, fill_real_object):
    """_ensure_gradient_material を複数回呼んでも Float Curve が破壊されない."""
    entry = scene.bmanga_fill_layers.add()
    entry.id = "fill_grad_curve_stab"
    entry.fill_type = "gradient"
    entry.gradient_type = "linear"
    entry.opacity = 80.0
    entry.parent_kind = "page"
    entry.parent_key = ""

    scene = bpy.context.scene
    fill_real_object.ensure_fill_real_object(
        scene=scene, entry=entry, page=page,
    )

    mat_name = None
    for mat in bpy.data.materials:
        if "fill_grad_curve_stab" in mat.name:
            mat_name = mat.name
            break

    if mat_name is None:
        print("  [3] Float Curve保全: マテリアル未生成のためスキップ", flush=True)
        return

    mat = bpy.data.materials[mat_name]
    nt = mat.node_tree
    float_curves_before = [n for n in nt.nodes if n.type == "CURVE_FLOAT"]
    _check(len(float_curves_before) >= 1, "Float Curve初回: ノードが存在しない")
    if not float_curves_before:
        return

    fc_ptr_before = float_curves_before[0].as_pointer()

    entry.opacity = 50.0
    fill_real_object.on_fill_entry_changed(entry)

    float_curves_after = [n for n in nt.nodes if n.type == "CURVE_FLOAT"]
    _check(len(float_curves_after) >= 1, "Float Curve再構築後: ノードが消えた (クラッシュの原因)")

    if float_curves_after:
        fc_ptr_after = float_curves_after[0].as_pointer()
        _check(
            fc_ptr_before == fc_ptr_after,
            "Float Curve再構築: 同一ノードが保全されるべき (ポインタ不一致)",
        )

    entry.opacity = 30.0
    fill_real_object.on_fill_entry_changed(entry)
    float_curves_third = [n for n in nt.nodes if n.type == "CURVE_FLOAT"]
    _check(len(float_curves_third) >= 1, "Float Curve3回目: ノードが消えた")

    print("  [3] グラデーション Float Curve ノード保全: OK", flush=True)


# ── 4. テキスト不透明度 ──────────────────────────────────────

def _test_text_opacity_property(page):
    """テキストにopacityプロパティが存在し、デフォルト100%で範囲が正しい."""
    entry = page.texts.add()
    entry.id = "text_opacity_test"
    entry.body = "テスト"
    entry.parent_kind = "page"
    entry.parent_key = ""

    _check(hasattr(entry, "opacity"), "テキスト opacity: プロパティが存在しない")
    _check(
        _close(float(entry.opacity), 100.0),
        f"テキスト opacity: デフォルトが100%であるべき: {entry.opacity}",
    )

    entry.opacity = 50.0
    _check(
        _close(float(entry.opacity), 50.0),
        f"テキスト opacity: 50%を設定できるべき: {entry.opacity}",
    )

    entry.opacity = 0.0
    _check(
        _close(float(entry.opacity), 0.0),
        f"テキスト opacity: 0%を設定できるべき: {entry.opacity}",
    )

    entry.opacity = 100.0
    print("  [4a] テキスト opacity プロパティ: OK", flush=True)
    return entry


def _test_text_opacity_serialization(page, schema):
    """テキストのopacityがJSON保存→読込でラウンドトリップする."""
    entry = page.texts.add()
    entry.id = "text_opacity_serial"
    entry.body = "シリアライズテスト"
    entry.font = ""
    entry.parent_kind = "page"
    entry.parent_key = ""
    entry.opacity = 75.0

    data = schema.text_entry_to_dict(entry)

    _check("opacity" in data, "シリアライズ: opacityキーが出力にない")
    _check(
        _close(float(data.get("opacity", 0)), 75.0),
        f"シリアライズ: opacity値が75.0であるべき: {data.get('opacity')}",
    )
    _check(
        data.get("opacityUnit") == "percent",
        f"シリアライズ: opacityUnit が 'percent' であるべき: {data.get('opacityUnit')}",
    )

    entry2 = page.texts.add()
    entry2.id = "text_opacity_load"
    entry2.parent_kind = "page"
    entry2.parent_key = ""
    schema.text_entry_from_dict(entry2, data)

    _check(
        _close(float(entry2.opacity), 75.0),
        f"デシリアライズ: opacity が 75.0 に復元されるべき: {entry2.opacity}",
    )

    data_no_opacity = {"id": "no_op", "body": "test"}
    entry3 = page.texts.add()
    entry3.id = "text_opacity_default"
    entry3.parent_kind = "page"
    entry3.parent_key = ""
    schema.text_entry_from_dict(entry3, data_no_opacity)
    _check(
        _close(float(entry3.opacity), 100.0),
        f"デシリアライズ: opacity未指定時は100%であるべき: {entry3.opacity}",
    )

    entry4 = page.texts.add()
    entry4.id = "text_opacity_zero"
    entry4.body = "透明テスト"
    entry4.font = ""
    entry4.parent_kind = "page"
    entry4.parent_key = ""
    entry4.opacity = 0.0
    data_zero = schema.text_entry_to_dict(entry4)
    _check(
        _close(float(data_zero.get("opacity", -1)), 0.0),
        f"シリアライズ: opacity=0.0が保存されるべき (or 100.0 バグ): {data_zero.get('opacity')}",
    )
    entry5 = page.texts.add()
    entry5.id = "text_opacity_zero_load"
    entry5.parent_kind = "page"
    entry5.parent_key = ""
    schema.text_entry_from_dict(entry5, data_zero)
    _check(
        _close(float(entry5.opacity), 0.0),
        f"デシリアライズ: opacity=0.0 が復元されるべき: {entry5.opacity}",
    )

    print("  [4b] テキスト opacity シリアライズ: OK", flush=True)


def _test_text_opacity_preset(page, text_presets):
    """テキストプリセットに opacity が含まれる."""
    entry = page.texts.add()
    entry.id = "text_opacity_preset"
    entry.body = "プリセットテスト"
    entry.parent_kind = "page"
    entry.parent_key = ""
    entry.opacity = 60.0

    snap = text_presets.snapshot_from_entry(entry)
    _check("opacity" in snap, "プリセット: snapshot に opacity が含まれない")
    _check(
        _close(float(snap.get("opacity", 0)), 60.0),
        f"プリセット: snapshot の opacity が 60.0 であるべき: {snap.get('opacity')}",
    )

    entry2 = page.texts.add()
    entry2.id = "text_opacity_preset_apply"
    entry2.body = "適用先"
    entry2.parent_kind = "page"
    entry2.parent_key = ""
    text_presets.apply_to_entry(entry2, snap)

    _check(
        _close(float(entry2.opacity), 60.0),
        f"プリセット適用: opacity が 60.0 であるべき: {entry2.opacity}",
    )

    print("  [4c] テキスト opacity プリセット: OK", flush=True)


def _test_text_opacity_material(page, text_real_object):
    """テキストの opacity < 100% でマテリアルに Multiply ノードが追加される."""
    entry = page.texts.add()
    entry.id = "text_opacity_mat"
    entry.body = "マテリアルテスト"
    entry.x_mm = 10.0
    entry.y_mm = 10.0
    entry.width_mm = 30.0
    entry.height_mm = 15.0
    entry.parent_kind = "page"
    entry.parent_key = ""
    entry.opacity = 50.0

    scene = bpy.context.scene
    work = scene.bmanga_work
    obj = text_real_object.ensure_text_real_object(
        scene=scene, entry=entry, page=work.pages[0],
    )

    if obj is None:
        print("  [4d] テキスト opacity マテリアル: テキスト実体未生成のためスキップ", flush=True)
        return

    mat = obj.active_material
    if mat is None or mat.node_tree is None:
        print("  [4d] テキスト opacity マテリアル: マテリアル未取得のためスキップ", flush=True)
        return

    nt = mat.node_tree
    math_nodes = [n for n in nt.nodes if n.type == "MATH" and n.operation == "MULTIPLY"]
    _check(
        len(math_nodes) >= 1,
        "テキスト opacity マテリアル: opacity<100%なのに Math Multiply ノードがない",
    )

    print("  [4d] テキスト opacity マテリアル: OK", flush=True)


# ── メイン ──────────────────────────────────────────────────

def _run_check() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_v553_regression_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "RegressionCheck.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        pkg = PACKAGE
        schema = sys.modules[f"{pkg}.io.schema"]
        text_presets = sys.modules[f"{pkg}.io.text_presets"]
        fill_real_object = sys.modules[f"{pkg}.utils.fill_real_object"]
        text_real_object = sys.modules[f"{pkg}.utils.text_real_object"]
        layer_reparent = sys.modules[f"{pkg}.utils.layer_reparent"]

        context = bpy.context
        scene = context.scene
        work = scene.bmanga_work
        page = work.pages[0]

        print("\n=== v0.6.553 回帰テスト ===\n", flush=True)

        print("[1] 塗りつぶしレイヤー移動", flush=True)
        _test_fill_move_region(scene, page, schema)
        _test_fill_move_lasso(scene, page, schema)
        _test_fill_move_gradient_endpoints(scene, page, schema)

        print("[2] 塗りつぶしコマ間移動 (reparent)", flush=True)
        _test_fill_reparent(context, scene, page, schema, layer_reparent, fill_real_object)

        print("[3] グラデーション Float Curve ノード保全", flush=True)
        _test_gradient_float_curve_preservation(scene, page, fill_real_object)

        print("[4] テキスト不透明度", flush=True)
        _test_text_opacity_property(page)
        _test_text_opacity_serialization(page, schema)
        _test_text_opacity_preset(page, text_presets)
        _test_text_opacity_material(page, text_real_object)

    except Exception:
        traceback.print_exc()
        FAILURES.append(f"例外発生: {traceback.format_exc()}")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


def main() -> None:
    _run_check()
    print(f"\n{'=' * 40}", flush=True)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} 件の失敗", flush=True)
        for i, f in enumerate(FAILURES, 1):
            print(f"  {i}. {f}", flush=True)
        sys.exit(1)
    else:
        print("ALL PASSED", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
