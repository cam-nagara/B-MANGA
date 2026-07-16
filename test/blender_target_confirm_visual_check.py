"""Blender通常画面で、確認ダイアログが対象レイヤーの右へ出ることを確認する。"""

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
from bpy.types import Operator


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_target_confirm_visual"
OUT_DIR = ROOT / "_verify" / "2026-07-17_target_confirm_popup"
IMAGE_PATH = OUT_DIR / "confirm_right_of_text_layer.png"
RESULT_PATH = OUT_DIR / "result.json"

_WORK_ROOT: Path | None = None
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
                return {"window": window, "screen": window.screen, "area": area, "region": region}
    raise AssertionError("3Dビューがありません")


def _capture() -> None:
    window = bpy.context.window or next(iter(bpy.context.window_manager.windows), None)
    assert window is not None
    with bpy.context.temp_override(window=window, screen=window.screen):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=5)
        result = bpy.ops.screen.screenshot(
            "EXEC_DEFAULT",
            filepath=str(IMAGE_PATH),
            check_existing=False,
        )
    assert "FINISHED" in result and IMAGE_PATH.is_file(), result


class BMANGA_OT_test_target_confirm(Operator):
    bl_idname = "bmanga.test_target_confirm"
    bl_label = "対象レイヤーの確認"

    def invoke(self, context, event):
        return _sub("utils.detail_popup").invoke_confirm(
            context,
            event,
            self,
            width=420,
            title="対象レイヤーの確認",
            message="この対象を隠さず、右側に表示します。",
            confirm_text="確認",
            icon="QUESTION",
        )

    def execute(self, _context):
        return {"FINISHED"}


def _after_dialog_open():
    global _ATTEMPTS, _DONE
    try:
        _ATTEMPTS += 1
        wm = bpy.context.window_manager
        if "bmanga_popup_placement_anchor_x" not in wm:
            if _ATTEMPTS < 30:
                return 0.1
            raise AssertionError("確認ダイアログの配置記録が作成されません")
        prefix = "bmanga_popup_placement_"
        placement = {
            "target_left": float(wm[f"{prefix}target_left"]),
            "target_bottom": float(wm[f"{prefix}target_bottom"]),
            "target_right": float(wm[f"{prefix}target_right"]),
            "target_top": float(wm[f"{prefix}target_top"]),
            "anchor_x": float(wm[f"{prefix}anchor_x"]),
            "anchor_y": float(wm[f"{prefix}anchor_y"]),
            "side": str(wm[f"{prefix}side"]),
        }
        scale = float(bpy.context.preferences.system.ui_scale or 1.0)
        reserved_left = placement["anchor_x"] - 420.0 * scale * 0.5
        assert placement["side"] == "right", placement
        assert reserved_left > placement["target_right"], placement
        _capture()
        payload = {**placement, "reserved_left": reserved_left, "ui_scale": scale, "image": str(IMAGE_PATH)}
        RESULT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("BMANGA_TARGET_CONFIRM_VISUAL_OK", json.dumps(payload, ensure_ascii=False), flush=True)
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
        print("確認ダイアログの画面検証が時間内に完了しませんでした", flush=True)
        os._exit(1)
    return None


def _run() -> None:
    global _WORK_ROOT
    if bpy.app.background:
        raise RuntimeError("この検証はBlender通常画面で実行してください")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _WORK_ROOT = Path(tempfile.mkdtemp(prefix="bmanga_target_confirm_"))
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(_WORK_ROOT / "config")
    _load_addon()
    bpy.utils.register_class(BMANGA_OT_test_target_confirm)
    assert "FINISHED" in bpy.ops.bmanga.work_new(filepath=str(_WORK_ROOT / "ConfirmPopup.bmanga"))
    assert "FINISHED" in bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)

    context = bpy.context
    work = _sub("core.work").get_work(context)
    page = work.pages[0]
    entry = page.texts.add()
    entry.id = "confirm_popup_text"
    entry.title = "確認ダイアログの配置対象"
    entry.body = "この対象レイヤーを隠さない"
    entry.x_mm = 65.0
    entry.y_mm = 95.0
    entry.width_mm = 92.0
    entry.height_mm = 32.0
    page.active_text_index = 0
    obj = _sub("utils.text_real_object").ensure_text_real_object(
        scene=context.scene,
        entry=entry,
        page=page,
    )
    assert obj is not None
    _sub("utils.layer_stack").sync_layer_stack_after_data_change(context)
    key = _sub("utils.object_selection").text_key(page, entry)
    _sub("utils.object_selection").set_keys(context, [key])
    context.view_layer.objects.active = obj
    obj.select_set(True)

    with context.temp_override(**_view3d_override()):
        assert "FINISHED" in bpy.ops.bmanga.view_fit_page("EXEC_DEFAULT")
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
        result = bpy.ops.bmanga.test_target_confirm("INVOKE_DEFAULT")
    assert "RUNNING_MODAL" in result, result
    bpy.app.timers.register(_after_dialog_open, first_interval=0.6)
    bpy.app.timers.register(_fail_safe, first_interval=30.0)


if __name__ == "__main__":
    try:
        _run()
    except Exception:
        traceback.print_exc()
        os._exit(1)
