"""Blender 実機(背景)用: 枠線ボカシ/枠線プリセット/コマ作成ツール/
効果線入り抜き範囲 の register + ロジック確認."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    failures: list[str] = []

    # 1. 輪郭ぼかし線種 + blur_amount
    from bmanga_dev.core import coma_border

    styles = {s[0] for s in coma_border._LINE_STYLE_ITEMS}
    if "brush" not in styles:
        failures.append("線種に brush が無い")
    if not hasattr(coma_border.BMangaComaBorder.bl_rna.properties, "__contains__") or (
        "blur_amount" not in coma_border.BMangaComaBorder.bl_rna.properties
    ):
        failures.append("BMangaComaBorder.blur_amount が無い")

    # 2. 枠線プリセット WM セレクタ + 同梱プリセット
    if not hasattr(bpy.types.WindowManager, "bmanga_border_preset_selector"):
        failures.append("WindowManager.bmanga_border_preset_selector が無い")
    from bmanga_dev.io import border_presets, schema

    gp = {p.name for p in border_presets.list_global_presets()}
    for need in ("線無し", "標準", "極太", "輪郭ぼかし"):
        if need not in gp:
            failures.append(f"同梱枠線プリセット {need} が見つからない (見つかった={sorted(gp)})")
    for opid in ("border_preset_apply", "border_preset_save_local"):
        if not hasattr(bpy.types, f"BMANGA_OT_{opid}".upper().replace("BMANGA_OT_", "BMANGA_OT_")):
            pass  # operator class名はクラス参照で確認しないため省略
    if not hasattr(bpy.ops.bmanga, "border_preset_apply"):
        failures.append("operator bmanga.border_preset_apply 未登録")

    # 3. コマ作成ツール operator
    if not hasattr(bpy.ops.bmanga, "coma_create_tool"):
        failures.append("operator bmanga.coma_create_tool 未登録")

    # 4. 効果線 入り抜き範囲 プロパティ
    from bmanga_dev.core import effect_line as el
    from bmanga_dev.operators import effect_line_gen as elg
    from bmanga_dev.utils import effect_inout_curve as eic

    props = el.BMangaEffectLineParams.bl_rna.properties
    for need in (
        "inout_range_mode",
        "inout_apply_brush_size",
        "inout_apply_opacity",
        "in_range_percent",
        "out_range_percent",
        "in_range_mm",
        "out_range_mm",
        "in_start_percent",
        "out_start_percent",
        "in_easing_curve",
        "out_easing_curve",
    ):
        if need not in props:
            failures.append(f"効果線パラメータ {need} が無い")

    # 4a. 後方互換: 範囲100%(percent) で従来の線形(入り100→抜き0)と一致
    base_r = 0.0015  # m
    p_full = SimpleNamespace(
        inout_apply="brush_size",
        in_percent=100.0,
        out_percent=0.0,
        inout_range_mode="percent",
        in_range_percent=100.0,
        out_range_percent=100.0,
        in_range_mm=10.0,
        out_range_mm=10.0,
    )
    L = 0.10  # m
    profile, d_in, d_out = elg._inout_profile(p_full, L)
    v0, vm, v1 = profile(0.0), profile(L * 0.5), profile(L)
    if not (abs(v0 - 1.0) < 1e-6 and abs(vm - 0.5) < 1e-6 and abs(v1 - 0.0) < 1e-6):
        failures.append(f"後方互換プロファイル不一致: {v0},{vm},{v1}")

    # 4b. 範囲を絞ると中央が満タン(プラトー)になる
    p_range = SimpleNamespace(
        inout_apply="brush_size",
        in_percent=0.0,
        out_percent=0.0,
        inout_range_mode="percent",
        in_range_percent=20.0,
        out_range_percent=20.0,
        in_range_mm=10.0,
        out_range_mm=10.0,
    )
    pr, _di, _do = elg._inout_profile(p_range, L)
    if not (abs(pr(0.0)) < 1e-6 and abs(pr(L * 0.5) - 1.0) < 1e-6 and abs(pr(L)) < 1e-6):
        failures.append(
            f"範囲プロファイル不一致: start={pr(0.0)} mid={pr(L*0.5)} end={pr(L)}"
        )

    # 4c. 長さ指定モード: in_range_mm=20mm → 0.02m まで入り区間
    p_len = SimpleNamespace(
        inout_apply="brush_size",
        in_percent=0.0,
        out_percent=100.0,
        inout_range_mode="length",
        in_range_percent=100.0,
        out_range_percent=100.0,
        in_range_mm=20.0,
        out_range_mm=0.0,
    )
    pl, di, do = pr_res = elg._inout_profile(p_len, L)
    if abs(di - 0.02) > 1e-6:
        failures.append(f"長さ指定 d_in 不一致: {di} (期待 0.02)")
    if not (abs(pl(0.0)) < 1e-6 and abs(pl(0.02) - 1.0) < 1e-6 and abs(pl(L) - 1.0) < 1e-6):
        failures.append(f"長さ指定プロファイル不一致: {pl(0.0)},{pl(0.02)},{pl(L)}")

    p_new = SimpleNamespace(
        inout_apply="brush_size",
        in_percent=0.0,
        out_percent=0.0,
        inout_range_mode="percent",
        in_range_percent=100.0,
        out_range_percent=100.0,
        in_range_mm=10.0,
        out_range_mm=10.0,
        in_start_percent=50.0,
        out_start_percent=30.0,
        in_easing_curve="0.0000,0.0000;0.5000,0.2500;1.0000,1.0000",
        out_easing_curve="0.0000,0.0000;0.5000,0.2500;1.0000,1.0000",
    )
    pn, dni, dno = elg._inout_profile(p_new, L)
    if abs(dni - L * 0.5) > 1e-6 or abs(dno - L * 0.3) > 1e-6:
        failures.append(f"入り始点/抜き始点の距離不一致: {dni},{dno}")
    if not (abs(pn(0.0)) < 1e-6 and abs(pn(L * 0.5) - 1.0) < 1e-6 and abs(pn(L * 0.7) - 1.0) < 1e-6 and abs(pn(L)) < 1e-6):
        failures.append(f"新入り抜きプロファイル不一致: {pn(0.0)},{pn(L*0.5)},{pn(L*0.7)},{pn(L)}")

    graph_points = eic.profile_points_from_params(p_new)
    if not (
        abs(graph_points[0][0]) < 1e-6
        and abs(graph_points[0][1]) < 1e-6
        and any(abs(x - 0.5) < 1e-6 and abs(y - 1.0) < 1e-6 for x, y in graph_points)
        and any(abs(x - 0.7) < 1e-6 and abs(y - 1.0) < 1e-6 for x, y in graph_points)
        and abs(graph_points[-1][0] - 1.0) < 1e-6
        and abs(graph_points[-1][1]) < 1e-6
    ):
        failures.append(f"線幅グラフ点列が数値と一致しない: {graph_points}")

    p_graph = SimpleNamespace(
        in_percent=100.0,
        out_percent=100.0,
        in_start_percent=0.0,
        out_start_percent=0.0,
        in_easing_curve="",
        out_easing_curve="",
    )
    eic.profile_points_to_params(p_graph, ((0.0, 0.25), (0.35, 1.0), (0.80, 1.0), (1.0, 0.10)))
    if not (
        abs(p_graph.in_percent - 25.0) < 1e-4
        and abs(p_graph.out_percent - 10.0) < 1e-4
        and abs(p_graph.in_start_percent - 35.0) < 1e-4
        and abs(p_graph.out_start_percent - 20.0) < 1e-4
    ):
        failures.append(
            "線幅グラフから数値へ反映できない: "
            f"{p_graph.in_percent},{p_graph.out_percent},{p_graph.in_start_percent},{p_graph.out_start_percent}"
        )
    profile_node = eic.ensure_profile_node(p_new)
    if profile_node is None or getattr(profile_node, "label", "") != "線幅グラフ":
        failures.append("線幅グラフのUIノードを作成できない")

    # 4d. _apply_inout_profile が 2点線にブレークポイントを挿入する
    from bmanga_dev.operators.effect_line_gen import EffectLineStroke

    s = EffectLineStroke(
        points_xyz=[(0.0, 0.0, 0.0), (L, 0.0, 0.0)], radius=base_r, role="line"
    )
    out = elg._apply_inout_profile([s], p_range)
    if not out or len(out[0].points_xyz) < 3 or out[0].radii is None:
        failures.append(
            f"_apply_inout_profile がブレークポイントを挿入していない: pts={len(out[0].points_xyz) if out else 0}"
        )
    else:
        rad = out[0].radii
        mid_i = len(rad) // 2
        if abs(rad[0]) > 1e-6 or abs(rad[mid_i] - base_r) > 1e-6:
            failures.append(f"半径プロファイル不正: {rad}")

    # 4e. 入り抜きの適用先は線幅と不透明度を同時に使える
    p_both = SimpleNamespace(
        inout_apply="brush_size",
        inout_apply_brush_size=True,
        inout_apply_opacity=True,
        in_percent=0.0,
        out_percent=0.0,
        inout_range_mode="percent",
        in_range_percent=20.0,
        out_range_percent=20.0,
        in_range_mm=10.0,
        out_range_mm=10.0,
    )
    both = elg._apply_inout_profile([s], p_both)
    if not both or both[0].radii is None or both[0].opacities is None:
        failures.append("入り抜きの線幅/不透明度同時適用ができない")

    # 5. プリセット往復: 標準/輪郭ぼかし を coma に適用して値確認
    bpy.context.scene.bmanga_work  # noqa: B018  -- 存在確認
    work = bpy.context.scene.bmanga_work
    page = work.pages.add()
    coma = page.comas.add()
    if not bool(coma.white_margin.enabled):
        failures.append("新規コマのフチ初期値がオンではない")
    if abs(float(coma.white_margin.width_mm) - 0.5) > 1e-6:
        failures.append(f"新規コマのフチ幅初期値が0.5mmではない: {coma.white_margin.width_mm}")
    std = border_presets.load_preset_by_name("標準", None)
    if std is None:
        failures.append("標準 プリセットを load 出来ない")
    else:
        border_presets.apply_preset_to_coma(std, coma)
        if not bool(coma.white_margin.enabled):
            failures.append("標準 プリセット適用後にフチがオンではない")
        if abs(float(coma.white_margin.width_mm) - 0.5) > 1e-6:
            failures.append(f"標準 プリセット適用後のフチ幅が0.5mmではない: {coma.white_margin.width_mm}")
    pre = border_presets.load_preset_by_name("輪郭ぼかし", None)
    if pre is None:
        failures.append("輪郭ぼかし プリセットを load 出来ない")
    else:
        border_presets.apply_preset_to_coma(pre, coma)
        if coma.border.style != "brush":
            failures.append(f"プリセット適用後 style != brush ({coma.border.style})")
        if abs(coma.border.blur_amount - 1.0) > 1e-3:
            failures.append(f"プリセット適用後 blur_amount != 1.0 ({coma.border.blur_amount})")
        saved = schema.coma_border_to_dict(coma.border)
        if "perEdge" in saved:
            failures.append("枠線プリセット保存データに辺別設定が残っている")
    legacy = border_presets.load_preset_by_name("ボカシブラシ", None)
    if legacy is None or legacy.name != "輪郭ぼかし":
        failures.append("旧名 ボカシブラシ から輪郭ぼかしプリセットを参照できない")

    no_line = border_presets.load_preset_by_name("線無し", None)
    if no_line is None:
        failures.append("線無し プリセットを load 出来ない")
    else:
        border_presets.apply_preset_to_coma(no_line, coma)
        if bool(coma.border.visible):
            failures.append("線無し プリセット適用後に枠線が表示のまま")
        if bool(coma.white_margin.enabled):
            failures.append("線無し プリセット適用後にフチが有効のまま")

    # 6. 背景チェックボックス + 背景色のプリセット往復 (v0.6.295)
    coma.paper_visible = False
    coma.background_color = (0.25, 0.5, 0.75, 0.9)
    data = border_presets.preset_dict_from_coma(coma, "背景往復テスト")
    if "paperVisible" not in data or data["paperVisible"] is not False:
        failures.append(f"プリセット保存に背景チェックが含まれない: {data.get('paperVisible')}")
    if "backgroundColor" not in data:
        failures.append("プリセット保存に背景色が含まれない")
    coma2 = page.comas.add()
    coma2.paper_visible = True
    fake = border_presets.BorderPreset(
        name="背景往復テスト",
        description="",
        path=Path("dummy.json"),
        source="local",
        data=data,
    )
    border_presets.apply_preset_to_coma(fake, coma2)
    if bool(coma2.paper_visible):
        failures.append("プリセット適用で背景チェックが復元されない")
    bg = tuple(coma2.background_color)
    src = tuple(coma.background_color)
    if any(abs(bg[i] - src[i]) > 1.0 / 255.0 + 1e-6 for i in range(3)) or abs(bg[3] - src[3]) > 5e-3:
        failures.append(f"プリセット適用で背景色が復元されない: {bg} != {src}")
    # 古いプリセット (背景キー無し) は背景設定を変えない
    coma2.paper_visible = True
    coma2.background_color = (1.0, 1.0, 1.0, 1.0)
    if std is not None and "paperVisible" not in std.data:
        border_presets.apply_preset_to_coma(std, coma2)
        if not bool(coma2.paper_visible):
            failures.append("背景キー無しプリセットが背景チェックを変えてしまう")

    if failures:
        print("=== CHECK FAILURES ===")
        for f in failures:
            print(" - " + f)
        print(f"RESULT: FAIL ({len(failures)})")
        raise SystemExit(1)
    print("RESULT: PASS — 全項目 OK")


main()
