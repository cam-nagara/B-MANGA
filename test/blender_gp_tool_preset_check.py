"""Blender実機用: グリースペンシルツールプリセット管理の検証.

確認項目:
  (1) 同梱プリセット5種 (ブラシ/フィル/トリム/消しゴム/グラブ) が一覧に出る
  (2) io の CRUD (追加 / 改名 / 複製 / 並べ替え / 削除) と JSON 往復
  (3) スクラッチ PropertyGroup とプリセットデータの往復 (apply_to_entry /
      snapshot_from_entry)
  (4) 実機適用: プリセット選択で GP のモード・ツール・ブラシ・設定値が
      切り替わる (ブラシ / フィル / 消しゴム / グラブ。トリムのツール切替は
      ヘッドレスでは失敗し得るため設定値の書込のみ確認)
  (5) 「現在の設定を追加」相当のスナップショットが現在のツール状態を拾う
  (6) プリセット詳細設定ダイアログの描画関数が機能別の項目を出す
  (7) ツールパネルのプリセット種別解決が GP 描画モード中に gp_tool を返す

実行例:
  blender.exe --background --factory-startup --python-exit-code 1 \
      --python test/blender_gp_tool_preset_check.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 共通プリセットの保存先をテスト専用の一時フォルダへ隔離する
_TMP_CONFIG = tempfile.mkdtemp(prefix="bmanga_gp_tool_preset_")
os.environ["BMANGA_USER_CONFIG_DIR"] = _TMP_CONFIG

import bpy  # noqa: E402

MOD_NAME = "bmanga_dev_gp_tool_preset_check"

FAILURES: list[str] = []


def _check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)
        print(f"NG: {message}", flush=True)
    else:
        print(f"OK: {message}", flush=True)


class _RecordingLayout:
    """draw() 系関数をヘッドレスで呼ぶための最小 UILayout スタブ."""

    def __init__(self, records: list[tuple[str, str, str]]) -> None:
        self.records = records
        self.enabled = True
        self.active = True
        self.alignment = "EXPAND"
        self.operator_context = "INVOKE_DEFAULT"

    def row(self, **_kwargs):
        return self

    def column(self, **_kwargs):
        return self

    def box(self):
        return self

    def split(self, **_kwargs):
        return self

    def label(self, text: str = "", **_kwargs) -> None:
        self.records.append(("label", "", text))

    def prop(self, _data, prop_name: str, text: str = "", **_kwargs) -> None:
        self.records.append(("prop", prop_name, text))

    def operator(self, op_id: str, text: str = "", **_kwargs):
        self.records.append(("operator", op_id, text))
        return type("_Op", (), {})()

    def template_list(self, list_id: str, _list_key, _data, collection_prop: str, *_args, **_kwargs) -> None:
        self.records.append(("template_list", list_id, collection_prop))

    def separator(self, **_kwargs) -> None:
        self.records.append(("separator", "", ""))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _sub(path: str):
    import importlib

    return importlib.import_module(f"{MOD_NAME}.{path}")


def _props(records: list[tuple[str, str, str]]) -> set[str]:
    return {name for kind, name, _text in records if kind == "prop"}


def _make_gp_object():
    bpy.ops.object.grease_pencil_add(type="EMPTY")
    obj = bpy.context.active_object
    layer = obj.data.layers.new("content")
    obj.data.layers.active = layer
    layer.frames.new(bpy.context.scene.frame_current)
    return obj


def _active_mode() -> str:
    obj = bpy.context.view_layer.objects.active
    return str(getattr(obj, "mode", "") or "")


def _paint_brush(attr: str):
    paint = getattr(bpy.context.scene.tool_settings, attr, None)
    return getattr(paint, "brush", None) if paint is not None else None


def main() -> None:
    _load_addon()
    gp_tool_presets = _sub("io.gp_tool_presets")
    gp_tool_preset_op = _sub("operators.gp_tool_preset_op")
    gp_drawers = _sub("panels.detail_drawers.gp")
    tool_panel = _sub("panels.tool_panel")
    detail_dialog = _sub("utils.detail_dialog")
    detail_preset_apply_op = _sub("operators.detail_preset_apply_op")

    # ── (1) 同梱プリセット一覧 ──────────────────────────────
    names = [preset.name for preset in gp_tool_presets.list_all_presets()]
    for expected in (
        "ブラシ（標準）",
        "フィル（標準）",
        "トリム（標準）",
        "消しゴム（標準）",
        "グラブ（標準）",
    ):
        _check(expected in names, f"同梱プリセットあり: {expected}")
    _check(
        [gp_tool_presets.tool_id(p.data) for p in gp_tool_presets.list_all_presets()]
        == ["brush", "fill", "trim", "erase", "grab"],
        "同梱プリセットの機能が5種そろっている",
    )

    # ── (2) CRUD と JSON 往復 ──────────────────────────────
    saved = gp_tool_presets.save_local_preset(
        "テストペン",
        "テスト用",
        {
            "tool": "brush",
            "brushAsset": "Ink Pen",
            "sizeMode": "VIEW",
            "size": 22,
            "useSizePressure": False,
            "strength": 0.5,
            "strokeType": "BOTH",
            "capsType": "FLAT",
            "hardness": 0.8,
            "useSmoothStroke": True,
            "smoothStrokeFactor": 0.9,
        },
    )
    _check(saved.is_file(), "プリセットJSONが保存された")
    loaded = gp_tool_presets.load_preset_by_name("テストペン")
    _check(loaded is not None and loaded.data.get("brushAsset") == "Ink Pen", "保存値の往復 (brushAsset)")
    _check(loaded is not None and loaded.data.get("size") == 22, "保存値の往復 (size)")

    renamed = gp_tool_presets.rename_preset("テストペン", "テストペン2")
    _check(renamed.name == "テストペン2", "改名できた")
    duplicated = gp_tool_presets.duplicate_preset("テストペン2", "テストペン3")
    _check(duplicated.name == "テストペン3", "複製できた")
    order = gp_tool_presets.move_preset("テストペン3", "UP")
    _check(order.index("テストペン3") < order.index("テストペン2"), "並べ替えできた")
    gp_tool_presets.delete_preset("テストペン3")
    _check(gp_tool_presets.load_preset_by_name("テストペン3") is None, "削除できた")
    gp_tool_presets.delete_preset("トリム（標準）")
    _check(
        gp_tool_presets.load_preset_by_name("トリム（標準）") is None,
        "同梱プリセットも一覧から削除 (非表示) にできた",
    )
    gp_tool_presets.save_local_preset("トリム（標準）", "", {"tool": "trim"})
    _check(
        gp_tool_presets.load_preset_by_name("トリム（標準）") is not None,
        "同名で再追加すると再び表示される",
    )

    # ── (3) スクラッチ往復 ─────────────────────────────────
    scratch = bpy.context.window_manager.bmanga_preset_scratch_gp_tool
    src = gp_tool_presets.load_preset_by_name("テストペン2")
    assert src is not None
    gp_tool_presets.apply_to_entry(scratch, src.data)
    _check(scratch.tool == "brush", "スクラッチ適用 (tool)")
    _check(scratch.brush_asset == "Ink Pen", "スクラッチ適用 (brush_asset)")
    _check(scratch.size_mode == "VIEW", "スクラッチ適用 (size_mode)")
    _check(scratch.size == 22, "スクラッチ適用 (size)")
    _check(scratch.stroke_type == "BOTH", "スクラッチ適用 (stroke_type)")
    _check(scratch.use_smooth_stroke is True, "スクラッチ適用 (use_smooth_stroke)")
    snap = gp_tool_presets.snapshot_from_entry(scratch)
    _check(snap.get("brushAsset") == "Ink Pen", "スクラッチ→スナップショット (brushAsset)")
    _check(abs(float(snap.get("smoothStrokeFactor", 0.0)) - 0.9) < 1e-4, "スクラッチ→スナップショット (補正の強さ)")

    # 不正値は既定へ丸める
    gp_tool_presets.apply_to_entry(scratch, {"tool": "unknown", "eraserMode": "??"})
    _check(scratch.tool == "brush", "不正な機能IDは既定へ丸める")

    # ── (4) 実機適用 ───────────────────────────────────────
    _make_gp_object()

    ok = gp_tool_preset_op.apply_gp_tool_preset(bpy.context, "テストペン2")
    _check(ok, "ブラシプリセットの適用が成功")
    _check(_active_mode() == "PAINT_GREASE_PENCIL", "適用後は描画モード")
    brush = _paint_brush("gpencil_paint")
    _check(brush is not None and brush.name.startswith("Ink Pen"), "Ink Pen ブラシへ切替")
    if brush is not None:
        _check(brush.use_locked_size == "VIEW", "画面基準サイズが適用された")
        _check(int(brush.size) == 22, "ブラシサイズが適用された")
        _check(brush.use_pressure_size is False, "筆圧サイズOFFが適用された")
        _check(abs(float(brush.strength) - 0.5) < 1e-4, "強さが適用された")
        _check(brush.use_smooth_stroke is True, "手ブレ補正ONが適用された")
        settings = brush.gpencil_settings
        _check(settings is not None and settings.stroke_type == "BOTH", "ストロークタイプが適用された")
        _check(settings is not None and settings.caps_type == "FLAT", "キャップが適用された")
        _check(settings is not None and abs(float(settings.hardness) - 0.8) < 1e-4, "硬さが適用された")

    # ページ基準 (mm) サイズの適用 — Blender 5.2 の同梱ブラシ既定に対応
    ok = gp_tool_preset_op.apply_gp_tool_preset(bpy.context, "ブラシ（標準）")
    _check(ok, "ブラシ（標準）の適用が成功")
    brush = _paint_brush("gpencil_paint")
    _check(brush is not None and brush.name.startswith("Pencil"), "Pencil ブラシへ切替")
    if brush is not None:
        _check(brush.use_locked_size == "SCENE", "ページ基準サイズが適用された")
        _check(
            abs(float(brush.unprojected_size) - 0.001) < 1e-6,
            "ページ上1mmの太さが適用された",
        )

    gp_tool_presets.save_local_preset(
        "テストフィル",
        "",
        {
            "tool": "fill",
            "size": 6,
            "fillDirection": "INVERT",
            "fillSolver": "PIXEL",
            "fillFactor": 2.0,
            "fillDilate": 5,
            "fillExtendFactor": 1.5,
            "fillExtendMode": "RADIUS",
        },
    )
    ok = gp_tool_preset_op.apply_gp_tool_preset(bpy.context, "テストフィル")
    _check(ok, "フィルプリセットの適用が成功")
    brush = _paint_brush("gpencil_paint")
    _check(
        brush is not None and str(getattr(brush, "gpencil_brush_type", "")) == "FILL",
        "フィルブラシへ切替",
    )
    if brush is not None and brush.gpencil_settings is not None:
        settings = brush.gpencil_settings
        _check(settings.fill_direction == "INVERT", "フィル方向が適用された")
        _check(settings.fill_solver == "PIXEL", "フィル計算方式が適用された")
        _check(abs(float(settings.fill_factor) - 2.0) < 1e-4, "フィル精度が適用された")
        _check(int(settings.dilate) == 5, "フィル拡張が適用された")
        _check(abs(float(settings.extend_stroke_factor) - 1.5) < 1e-4, "すき間閉じサイズが適用された")
        _check(settings.fill_extend_mode == "RADIUS", "すき間閉じモードが適用された")

    ok = gp_tool_preset_op.apply_gp_tool_preset(bpy.context, "フィル（標準）")
    _check(ok, "フィル（標準）の適用が成功")
    brush = _paint_brush("gpencil_paint")
    if brush is not None:
        _check(brush.use_locked_size == "SCENE", "フィルにページ基準サイズが適用された")
        _check(
            abs(float(brush.unprojected_size) - 0.001) < 1e-6,
            "フィル境界線のページ上1mmが適用された",
        )

    gp_tool_presets.save_local_preset(
        "テスト消しゴム",
        "",
        {
            "tool": "erase",
            "eraserMode": "STROKE",
            "size": 80,
            "activeLayerOnly": True,
            "keepCaps": True,
        },
    )
    ok = gp_tool_preset_op.apply_gp_tool_preset(bpy.context, "テスト消しゴム")
    _check(ok, "消しゴムプリセットの適用が成功")
    brush = _paint_brush("gpencil_paint")
    _check(
        brush is not None and str(getattr(brush, "gpencil_brush_type", "")) == "ERASE",
        "消しゴムブラシへ切替",
    )
    if brush is not None and brush.gpencil_settings is not None:
        settings = brush.gpencil_settings
        _check(settings.eraser_mode == "STROKE", "消しゴムモードが適用された")
        _check(brush.use_locked_size == "VIEW", "消しゴムは画面基準サイズへ固定")
        _check(int(brush.size) == 80, "消しゴムサイズが適用された")
        _check(settings.use_active_layer_only is True, "アクティブレイヤーのみが適用された")

    gp_tool_presets.save_local_preset(
        "テストグラブ",
        "",
        {"tool": "grab", "size": 120, "strength": 0.9},
    )
    ok = gp_tool_preset_op.apply_gp_tool_preset(bpy.context, "テストグラブ")
    _check(ok, "グラブプリセットの適用が成功")
    _check(_active_mode() == "SCULPT_GREASE_PENCIL", "適用後はスカルプトモード")
    sbrush = _paint_brush("gpencil_sculpt_paint")
    _check(
        sbrush is not None
        and str(getattr(sbrush, "gpencil_sculpt_brush_type", "")) == "GRAB",
        "グラブブラシへ切替",
    )
    if sbrush is not None:
        _check(sbrush.use_locked_size == "VIEW", "グラブは画面基準サイズへ固定")
        _check(int(sbrush.size) == 120, "グラブサイズが適用された")
        _check(abs(float(sbrush.strength) - 0.9) < 1e-4, "グラブ強さが適用された")
        sculpt_paint = getattr(bpy.context.scene.tool_settings, "gpencil_sculpt_paint", None)
        ups = getattr(sculpt_paint, "unified_paint_settings", None)
        if ups is not None and bool(getattr(ups, "use_unified_size", False)):
            _check(int(ups.size) == 120, "グラブの統一サイズにも適用された (画面の実効値)")
            _check(str(ups.use_locked_size) == "VIEW", "グラブの統一サイズ基準も画面基準")

    # トリム: ツール切替はヘッドレスで失敗し得るため、設定書込のみ確認
    gp_tool_presets.save_local_preset(
        "テストトリム",
        "",
        {"tool": "trim", "activeLayerOnly": True, "keepCaps": True},
    )
    ok = gp_tool_preset_op.apply_gp_tool_preset(bpy.context, "テストトリム")
    _check(ok, "トリムプリセットの適用が成功 (モード切替まで)")
    _check(_active_mode() == "PAINT_GREASE_PENCIL", "トリム適用後は描画モード")
    brush = _paint_brush("gpencil_paint")
    if brush is not None and brush.gpencil_settings is not None:
        _check(
            brush.gpencil_settings.use_active_layer_only is True,
            "トリムのアクティブレイヤーのみが適用された",
        )
        _check(
            brush.gpencil_settings.use_keep_caps_eraser is True,
            "トリムのキャップを保持が適用された",
        )

    # ── (5) 現在の設定のスナップショット ──────────────────
    # トリムのツール切替はヘッドレスで失敗し得るため、確実な状態を作ってから拾う
    gp_tool_preset_op.apply_gp_tool_preset(bpy.context, "テスト消しゴム")
    snap = gp_tool_preset_op.snapshot_current_tool_settings(bpy.context)
    _check(snap.get("tool") in {"brush", "fill", "trim", "erase", "grab"}, "現在の機能を判定できた")
    _check(snap.get("tool") == "erase", f"現在の設定=消しゴムを拾った (実際: {snap.get('tool')})")
    _check(snap.get("eraserMode") == "STROKE", "現在の消しゴムモードを拾った")

    grab_applied = gp_tool_preset_op.apply_gp_tool_preset(bpy.context, "テストグラブ")
    snap = gp_tool_preset_op.snapshot_current_tool_settings(bpy.context)
    _check(grab_applied and snap.get("tool") == "grab", "スカルプト中はグラブを拾った")
    _check(snap.get("size") == 120, "グラブのサイズを拾った")

    # ── (6) 詳細設定ダイアログの描画 ───────────────────────
    scratch2 = bpy.context.window_manager.bmanga_preset_scratch_gp_tool
    target = detail_dialog.resolve_preset_detail_target("gp_tool", "テストペン2", scratch2)
    _check(target.kind == "gp_tool", "プリセット詳細対象の種別が gp_tool")
    layout_spec = detail_dialog.resolve_detail_layout(target, detail_dialog.DetailMode.PRESET)
    _check(layout_spec.max_columns == 2, "gp_tool のレイアウト列数が2")

    session = type("_S", (), {"target": target, "layout": layout_spec})()

    def _draw_records(tool_value: str) -> set[str]:
        scratch2.tool = tool_value
        records: list[tuple[str, str, str]] = []
        stub = _RecordingLayout(records)
        gp_drawers.draw_gp_tool_body(stub, stub, (stub,), bpy.context, session, "preset")
        return _props(records)

    props = _draw_records("brush")
    _check({"tool", "brush_asset", "size_mode", "size", "strength", "stroke_type", "caps_type", "hardness"} <= props,
           "ブラシ機能の詳細項目が描画される (画面基準)")
    _check("size_mm" not in props, "画面基準ではmmサイズ項目を出さない")
    scratch2.size_mode = "SCENE"
    props = _draw_records("brush")
    _check("size_mm" in props and "size" not in props, "ページ基準ではmmサイズ項目だけを出す")
    scratch2.size_mode = "VIEW"
    props = _draw_records("fill")
    _check({"fill_direction", "fill_solver", "size", "fill_extend_factor"} <= props,
           "フィル機能の詳細項目が描画される")
    _check("fill_factor" not in props, "ドロネー方式ではピクセル専用項目を出さない")
    scratch2.fill_solver = "PIXEL"
    props = _draw_records("fill")
    _check({"fill_factor", "fill_dilate"} <= props, "ピクセル方式で精度・拡張が出る")
    props = _draw_records("trim")
    _check({"use_active_layer_only", "use_keep_caps"} <= props, "トリム機能の詳細項目が描画される")
    _check("size" not in props, "トリムにはサイズ項目を出さない")
    props = _draw_records("erase")
    _check({"eraser_mode", "size", "strength", "use_active_layer_only"} <= props,
           "消しゴム機能の詳細項目が描画される")
    props = _draw_records("grab")
    _check({"size", "strength"} <= props, "グラブ機能の詳細項目が描画される")
    _check("brush_asset" not in props, "グラブには使用ブラシ項目を出さない")

    # プリセット編集一覧のデータ供給 (sync_preset_edit_list が使う _list_presets)
    listed = detail_preset_apply_op._list_presets(bpy.context, "gp_tool")
    _check(any(getattr(p, "name", "") == "テストペン2" for p in listed),
           "詳細ダイアログの一覧に gp_tool プリセットが供給される")

    # preset_adapters がプリセット欄を describe できる
    preset_adapters = _sub("panels.detail_drawers.preset_adapters")
    spec = preset_adapters.preset_spec_for_target(target)
    _check(spec is not None and spec.preset_type == "gp_tool", "プリセット欄の種別解決 (gp_tool)")

    # ── (7) ツールパネルの種別解決 ─────────────────────────
    gp_tool_preset_op.apply_gp_tool_preset(bpy.context, "テストペン2")
    _check(tool_panel._gp_tool_mode_active(bpy.context) is True, "GP描画モード中の判定")
    _check(tool_panel._TOOL_TO_PRESET_TYPE.get("gp_tool") == "gp_tool", "gp_tool の種別対応")

    # ── (8) ツールパネルのプリセット一覧の配線 ─────────────
    preset_management_ui = _sub("panels.preset_management_ui")
    wm = bpy.context.window_manager
    _check(hasattr(wm, "bmanga_gp_tool_preset_selector"), "セレクタが登録されている")
    _check(hasattr(wm, "bmanga_gp_tool_preset_list"), "一覧コレクションが登録されている")
    records: list[tuple[str, str, str]] = []
    stub = _RecordingLayout(records)
    preset_management_ui.draw_preset_list(stub, bpy.context, "gp_tool", compact=True)
    listed_names = [item.name for item in wm.bmanga_gp_tool_preset_list]
    _check("フィル（標準）" in listed_names, "一覧コレクションへ同梱プリセットが供給される")
    _check(
        any(kind == "template_list" for kind, _a, _b in records),
        "一覧テンプレートが描画される",
    )
    drawn_ops = {op_id for kind, op_id, _t in records if kind == "operator"}
    _check("bmanga.gp_tool_preset_add_local" in drawn_ops, "追加ボタンが描画される")
    for op_id in sorted(drawn_ops):
        if not op_id.startswith("bmanga."):
            continue
        try:
            getattr(bpy.ops.bmanga, op_id.split(".", 1)[1]).get_rna_type()
            exists = True
        except Exception:  # noqa: BLE001
            exists = False
        _check(exists, f"描画されたボタンのオペレーターが実在する: {op_id}")

    bpy.ops.object.mode_set(mode="OBJECT")
    _check(tool_panel._gp_tool_mode_active(bpy.context) is False, "オブジェクトモードでは非アクティブ")

    # ── 結果 ───────────────────────────────────────────────
    print("", flush=True)
    if FAILURES:
        print(f"RESULT: NG ({len(FAILURES)} failures)", flush=True)
        for message in FAILURES:
            print(f"  - {message}", flush=True)
        sys.exit(1)
    print("RESULT: ALL OK", flush=True)


try:
    main()
except SystemExit:
    raise
except Exception:
    import traceback

    traceback.print_exc()
    print("RESULT: EXCEPTION", flush=True)
    sys.exit(1)
