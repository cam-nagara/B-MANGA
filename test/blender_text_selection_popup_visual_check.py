"""Blender通常画面で、選択文字設定が編集中テキストの右へ出ることを確認する。"""

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
MOD_NAME = "bmanga_dev_text_selection_popup_visual"
OUT_DIR = Path(
    os.environ.get(
        "BMANGA_TEXT_SELECTION_POPUP_VISUAL_OUT",
        str(ROOT / "_verify" / "2026-07-17_text_selection_popup"),
    )
)
IMAGE_PATH = OUT_DIR / "selection_settings_right_of_text.png"
RESULT_PATH = OUT_DIR / "result.json"

_WORK_ROOT: Path | None = None
_TEXT_TOOL = None
_DONE = False
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
        for area in tuple(window.screen.areas):
            if area.type != "VIEW_3D":
                continue
            region = next((item for item in area.regions if item.type == "WINDOW"), None)
            if region is not None:
                return {
                    "window": window,
                    "screen": window.screen,
                    "area": area,
                    "region": region,
                }
    raise AssertionError("3Dビューがありません")


def _capture(path: Path) -> None:
    window = bpy.context.window or next(iter(bpy.context.window_manager.windows), None)
    assert window is not None
    with bpy.context.temp_override(window=window, screen=window.screen):
        if bpy.ops.wm.redraw_timer.poll():
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=5)
        result = bpy.ops.screen.screenshot(
            "EXEC_DEFAULT",
            filepath=str(path),
            check_existing=False,
        )
    assert "FINISHED" in result and path.is_file(), result


def _placement_payload() -> dict[str, float | str]:
    wm = bpy.context.window_manager
    prefix = "bmanga_popup_placement_"
    return {
        "target_left": float(wm[f"{prefix}target_left"]),
        "target_bottom": float(wm[f"{prefix}target_bottom"]),
        "target_right": float(wm[f"{prefix}target_right"]),
        "target_top": float(wm[f"{prefix}target_top"]),
        "anchor_x": float(wm[f"{prefix}anchor_x"]),
        "anchor_y": float(wm[f"{prefix}anchor_y"]),
        "side": str(wm[f"{prefix}side"]),
    }


def _after_dialog_open():
    global _ATTEMPTS, _DONE
    try:
        _ATTEMPTS += 1
        wm = bpy.context.window_manager
        if "bmanga_popup_placement_anchor_x" not in wm:
            if _ATTEMPTS < 30:
                return 0.1
            raise AssertionError("選択文字設定の配置記録が作成されません")
        placement = _placement_payload()
        scale = float(bpy.context.preferences.system.ui_scale or 1.0)
        popup_left = float(placement["anchor_x"]) - 320.0 * scale * 0.5
        assert placement["side"] == "right", placement
        assert popup_left > float(placement["target_right"]), placement
        assert float(placement["target_right"]) > float(placement["target_left"]), placement
        assert float(placement["target_top"]) > float(placement["target_bottom"]), placement

        _capture(IMAGE_PATH)
        payload = {
            **placement,
            "popup_left": popup_left,
            "ui_scale": scale,
            "image": str(IMAGE_PATH),
        }
        RESULT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("BMANGA_TEXT_SELECTION_POPUP_VISUAL_OK", json.dumps(payload, ensure_ascii=False), flush=True)
        _DONE = True
        if _WORK_ROOT is not None:
            shutil.rmtree(_WORK_ROOT, ignore_errors=True)
        os._exit(0)
    except Exception:
        traceback.print_exc()
        os._exit(1)
    return None


def _fail_safe():
    if not _DONE:
        print("選択文字設定の画面検証が時間内に完了しませんでした", flush=True)
        os._exit(1)
    return None


def _create_text_target(context):
    work = _sub("core.work").get_work(context)
    page = work.pages[0]
    entry = page.texts.add()
    entry.id = "selection_popup_text"
    entry.title = "選択文字設定の配置確認"
    entry.body = "この選択文字列を隠さずに設定する"
    entry.writing_mode = "horizontal"
    entry.x_mm = 65.0
    entry.y_mm = 95.0
    entry.width_mm = 92.0
    entry.height_mm = 32.0
    page.active_text_index = 0
    text_real_object = _sub("utils.text_real_object")
    obj = text_real_object.ensure_text_real_object(scene=context.scene, entry=entry, page=page)
    assert obj is not None
    _sub("utils.layer_stack").sync_layer_stack_after_data_change(context)
    for candidate in tuple(context.view_layer.objects):
        candidate.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return page, entry, text_real_object


def _open_selection_dialog(context, page, entry, text_real_object):
    global _TEXT_TOOL

    override = _view3d_override()
    with context.temp_override(**override):
        fit_result = bpy.ops.bmanga.view_fit_page("EXEC_DEFAULT")
        assert "FINISHED" in fit_result, fit_result
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)

        class _TextEditProbe:
            _editing = True
            _page_id = page.id
            _text_id = entry.id
            _selection_anchor = 2
            _cursor_index = 8

            def _current_text_entry(self, _context):
                return page, entry, 0

            def begin_dialog_cursor_override(self, _context):
                return None

            def end_dialog_cursor_override(self, _context):
                return None

        _TEXT_TOOL = _TextEditProbe()
        _sub("operators.coma_modal_state").set_active("text_tool", _TEXT_TOOL, context)
        _sub("operators.text_edit_runtime").set_view_edit_state(
            context,
            page.id,
            entry.id,
            _TEXT_TOOL._cursor_index,
            _TEXT_TOOL._selection_anchor,
        )
        text_real_object.set_text_object_preview_hidden(entry, page=page, hidden=True)
        edit_rect = _sub("utils.detail_popup")._text_edit_target_rect(context)
        assert edit_rect is not None and edit_rect.right - edit_rect.left > 50.0, edit_rect
        popup_result = bpy.ops.bmanga.text_selection_style_popup(
            "INVOKE_DEFAULT",
            page_id=page.id,
            text_id=entry.id,
            start=2,
            end=8,
        )
    return popup_result


def _run() -> None:
    global _WORK_ROOT
    if bpy.app.background:
        raise RuntimeError("この検証はBlender通常画面で実行してください")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _WORK_ROOT = Path(tempfile.mkdtemp(prefix="bmanga_text_selection_popup_"))
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(_WORK_ROOT / "config")
    _load_addon()
    result = bpy.ops.bmanga.work_new(filepath=str(_WORK_ROOT / "SelectionPopup.bmanga"))
    assert "FINISHED" in result, result
    result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
    assert "FINISHED" in result, result
    context = bpy.context
    page, entry, text_real_object = _create_text_target(context)
    popup_result = _open_selection_dialog(context, page, entry, text_real_object)
    assert "RUNNING_MODAL" in popup_result, popup_result
    bpy.app.timers.register(_after_dialog_open, first_interval=0.6)
    bpy.app.timers.register(_fail_safe, first_interval=30.0)


if __name__ == "__main__":
    try:
        _run()
    except Exception:
        traceback.print_exc()
        os._exit(1)
