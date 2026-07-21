"""Blender通常画面でグリースペンシルツールプリセットを目視検証する。

ケースは環境変数 BMANGA_GP_TOOL_VISUAL_CASE で指定する:
  panel        — ツールパネルの一覧表示と、同梱5プリセットの順次適用
                 (モード・ツール・ブラシ切替を実機確認しつつ各状態を撮影)
  dialog_brush / dialog_fill / dialog_trim / dialog_erase / dialog_grab
               — 各機能の詳細設定ダイアログを開いて撮影

実行例 (通常画面。--background を付けない):
  blender.exe --factory-startup --python test/blender_gp_tool_preset_visual_check.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_gp_tool_preset_visual"
CASE = str(os.environ.get("BMANGA_GP_TOOL_VISUAL_CASE", "panel") or "panel").strip().lower()
OUT_DIR = Path(
    os.environ.get(
        "BMANGA_GP_TOOL_VISUAL_OUT",
        str(ROOT / "_verify" / "2026-07-21_gp_tool_preset_visual"),
    )
)

_DIALOG_PRESETS = {
    "dialog_brush": "ブラシ（標準）",
    "dialog_fill": "フィル（標準）",
    "dialog_trim": "トリム（標準）",
    "dialog_erase": "消しゴム（標準）",
    "dialog_grab": "グラブ（標準）",
}

# panel ケースで順に適用する (プリセット名, 期待モード, 期待ブラシ種別/名前)
_PANEL_SEQUENCE = (
    ("ブラシ（標準）", "PAINT_GREASE_PENCIL", ("gpencil_paint", "DRAW", "Pencil")),
    ("フィル（標準）", "PAINT_GREASE_PENCIL", ("gpencil_paint", "FILL", "Fill")),
    ("トリム（標準）", "PAINT_GREASE_PENCIL", None),
    ("消しゴム（標準）", "PAINT_GREASE_PENCIL", ("gpencil_paint", "ERASE", "Eraser Hard")),
    ("グラブ（標準）", "SCULPT_GREASE_PENCIL", ("gpencil_sculpt_paint", None, "Grab")),
)

_WORK_ROOT: Path | None = None
_DONE = False
_STEP = 0
_RESULTS: list[dict] = []
_ATTEMPTS = 0


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _sub(path: str):
    __import__(f"{MOD_NAME}.{path}")
    return sys.modules[f"{MOD_NAME}.{path}"]


def _view3d_override() -> dict[str, object]:
    for window in tuple(bpy.context.window_manager.windows):
        screen = window.screen
        for area in tuple(screen.areas):
            if area.type != "VIEW_3D":
                continue
            region = next((item for item in area.regions if item.type == "WINDOW"), None)
            if region is not None:
                return {"window": window, "screen": screen, "area": area, "region": region}
    raise AssertionError("3Dビューがありません")


_SIDEBAR_READY = False


def _ensure_sidebar_ready() -> None:
    """NパネルをB-MANGAタブで表示する。

    注意: show_region_ui / active_panel_category は、起動スクリプトから
    ウィンドウ文脈なしで直接触るとBlender本体がアクセス違反で落ちる。
    必ずタイマー内 (イベントループ開始後) から、ウィンドウを
    temp_override した状態で呼ぶこと。
    """
    global _SIDEBAR_READY
    if _SIDEBAR_READY:
        return
    override = _view3d_override()
    window = override["window"]
    area = override["area"]
    ui_region = next((r for r in area.regions if r.type == "UI"), None)
    with bpy.context.temp_override(**override):
        if ui_region is None or int(getattr(ui_region, "width", 0) or 0) <= 1:
            bpy.ops.screen.region_toggle(region_type="UI")
        if bpy.ops.wm.redraw_timer.poll():
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=3)
    with bpy.context.temp_override(window=window, screen=window.screen, area=area):
        for region in area.regions:
            if region.type != "UI" or int(getattr(region, "width", 0) or 0) <= 1:
                continue
            region.active_panel_category = "B-MANGA"
        if bpy.ops.wm.redraw_timer.poll():
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=3)
    ui_region = next((r for r in area.regions if r.type == "UI"), None)
    assert ui_region is not None and int(ui_region.width) > 1, "Nパネルを表示できません"
    _SIDEBAR_READY = True


def _capture(path: Path) -> None:
    window = bpy.context.window or next(iter(bpy.context.window_manager.windows), None)
    assert window is not None and window.screen is not None
    with bpy.context.temp_override(window=window, screen=window.screen):
        if bpy.ops.wm.redraw_timer.poll():
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=5)
        result = bpy.ops.screen.screenshot(
            "EXEC_DEFAULT",
            filepath=str(path),
            check_existing=False,
        )
    assert "FINISHED" in result and path.is_file(), f"画面を保存できません: {result}"


def _active_mode() -> str:
    obj = bpy.context.view_layer.objects.active
    return str(getattr(obj, "mode", "") or "")


def _current_tool_idname(mode: str) -> str:
    try:
        tool = bpy.context.workspace.tools.from_space_view3d_mode(mode, create=False)
        return str(getattr(tool, "idname", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def _brush_info(attr: str) -> tuple[str, str]:
    paint = getattr(bpy.context.scene.tool_settings, attr, None)
    brush = getattr(paint, "brush", None) if paint is not None else None
    if brush is None:
        return "", ""
    return str(brush.name), str(getattr(brush, "gpencil_brush_type", "") or "")


def _finish(payload: dict) -> None:
    global _DONE
    _DONE = True
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"result_{CASE}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("BMANGA_GP_TOOL_PRESET_VISUAL_OK", json.dumps(payload, ensure_ascii=False), flush=True)
    if _WORK_ROOT is not None:
        shutil.rmtree(_WORK_ROOT, ignore_errors=True)
    os._exit(0)


_PENDING = False


def _panel_step():
    """同梱プリセットを1つずつ適用し、次のティックで状態確認と撮影を行う。

    適用と同じティック内で撮影すると、モード切替後のパネル再レイアウトが
    間に合わず古い並びが写るため、適用フェーズと撮影フェーズを分ける。
    """
    global _STEP, _PENDING
    try:
        if _STEP >= len(_PANEL_SEQUENCE):
            _finish({"case": CASE, "steps": _RESULTS})
            return None
        name, expected_mode, brush_expect = _PANEL_SEQUENCE[_STEP]
        if not _PENDING:
            _stage(f"apply {name}")
            _ensure_sidebar_ready()
            wm = bpy.context.window_manager
            with bpy.context.temp_override(**_view3d_override()):
                wm.bmanga_gp_tool_preset_selector = name
            _PENDING = True
            return 0.6

        _PENDING = False
        mode = _active_mode()
        assert mode == expected_mode, f"{name}: モードが違います: {mode} (期待 {expected_mode})"
        record: dict = {"preset": name, "mode": mode}
        record["tool"] = _current_tool_idname(expected_mode)
        if name == "トリム（標準）":
            assert record["tool"] == "builtin.trim", (
                f"トリム適用後のツールが違います: {record['tool']}"
            )
        if brush_expect is not None:
            attr, expected_type, expected_name = brush_expect
            brush_name, brush_type = _brush_info(attr)
            record["brush"] = brush_name
            record["brush_type"] = brush_type
            assert brush_name.startswith(expected_name), (
                f"{name}: ブラシが違います: {brush_name} (期待 {expected_name})"
            )
            if expected_type:
                assert brush_type == expected_type, (
                    f"{name}: ブラシ種別が違います: {brush_type} (期待 {expected_type})"
                )
        image = OUT_DIR / f"panel_{_STEP + 1:02d}_{gp_tool_id(name)}.png"
        _capture(image)
        record["image"] = str(image)
        _RESULTS.append(record)
        print(f"STEP OK: {name} → {record}", flush=True)
        _STEP += 1
        return 0.6
    except Exception:
        traceback.print_exc()
        os._exit(1)
    return None


def gp_tool_id(preset_name: str) -> str:
    return {
        "ブラシ（標準）": "brush",
        "フィル（標準）": "fill",
        "トリム（標準）": "trim",
        "消しゴム（標準）": "erase",
        "グラブ（標準）": "grab",
    }.get(preset_name, "unknown")


def _dialog_step():
    """プリセット詳細設定ダイアログの表示を待って撮影するタイマー。"""
    global _ATTEMPTS
    try:
        _ATTEMPTS += 1
        preset_detail_op = _sub("operators.preset_detail_op")
        open_ops = tuple(preset_detail_op._OPEN_PRESET_DETAIL_OPERATORS.values())  # noqa: SLF001
        if not open_ops:
            if _ATTEMPTS < 30:
                return 0.1
            raise AssertionError("プリセット詳細設定ダイアログが表示されません")
        operator = open_ops[0]
        assert str(operator.preset_type) == "gp_tool", operator.preset_type
        session = operator._detail_session  # noqa: SLF001
        assert session is not None and session.target.kind == "gp_tool"
        scratch = bpy.context.window_manager.bmanga_preset_scratch_gp_tool
        expected_tool = {
            "dialog_brush": "brush",
            "dialog_fill": "fill",
            "dialog_trim": "trim",
            "dialog_erase": "erase",
            "dialog_grab": "grab",
        }[CASE]
        assert str(scratch.tool) == expected_tool, (
            f"読み込まれた機能が違います: {scratch.tool} (期待 {expected_tool})"
        )
        image = OUT_DIR / f"{CASE}.png"
        _capture(image)
        _finish(
            {
                "case": CASE,
                "preset": _DIALOG_PRESETS[CASE],
                "tool": str(scratch.tool),
                "columns": int(session.layout.column_count),
                "image": str(image),
            }
        )
    except Exception:
        traceback.print_exc()
        os._exit(1)
    return None


def _fail_safe():
    if _DONE:
        return None
    print("グリースペンシルツールプリセットの画面検証が時間内に完了しませんでした", flush=True)
    os._exit(1)


def _stage(label: str) -> None:
    print(f"GP_VISUAL_STAGE: {label}", flush=True)


def _setup_work() -> None:
    global _WORK_ROOT
    _WORK_ROOT = Path(tempfile.mkdtemp(prefix="bmanga_gp_tool_visual_"))
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(_WORK_ROOT / "config")
    _stage("load_addon")
    _load_addon()
    _stage("work_new")
    result = bpy.ops.bmanga.work_new(filepath=str(_WORK_ROOT / "GpToolPresetVisual.bmanga"))
    assert "FINISHED" in result, result
    _stage("open_page_file")
    result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
    assert "FINISHED" in result, result
    _stage("layer_stack_add")
    with bpy.context.temp_override(**_view3d_override()):
        result = bpy.ops.bmanga.layer_stack_add("EXEC_DEFAULT", kind="gp")
    assert "FINISHED" in result, result
    layer_object_model = _sub("utils.layer_object_model")
    obj = next(iter(layer_object_model.iter_layer_objects("gp")), None)
    assert obj is not None, "手描きレイヤーが作成されていません"
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    _stage("setup_done")


def _run() -> None:
    if bpy.app.background:
        raise RuntimeError("この検証はBlender通常画面で実行してください")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _setup_work()

    if CASE == "panel":
        bpy.app.timers.register(_panel_step, first_interval=1.5)
        bpy.app.timers.register(_fail_safe, first_interval=90.0)
        return

    preset_name = _DIALOG_PRESETS.get(CASE)
    if preset_name is None:
        raise ValueError(f"不明なケースです: {CASE}")
    with bpy.context.temp_override(**_view3d_override()):
        result = bpy.ops.bmanga.preset_detail_edit(
            "INVOKE_DEFAULT",
            preset_type="gp_tool",
            preset_name=preset_name,
        )
    assert "RUNNING_MODAL" in result or "FINISHED" in result, result
    bpy.app.timers.register(_dialog_step, first_interval=0.5)
    bpy.app.timers.register(_fail_safe, first_interval=30.0)


if __name__ == "__main__":
    try:
        _run()
    except Exception:
        traceback.print_exc()
        os._exit(1)
