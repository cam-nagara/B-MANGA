"""Blender UI実機: レイヤー一覧を狭幅にして列位置を目視確認する。"""

from __future__ import annotations

import ctypes
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_layer_panel_width_visual"
OUT_DIR = ROOT / "_verify" / "2026-07-28_layer_stack_panel_width"
_DONE = False


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _set_test_window_title() -> None:
    if os.name != "nt":
        return
    user32 = ctypes.windll.user32
    pid = os.getpid()
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _rename(hwnd, _lparam):
        window_pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if int(window_pid.value) == pid and user32.IsWindowVisible(hwnd):
            user32.SetWindowTextW(hwnd, "B-MANGA Layer Panel Width Visual Test")
        return True

    user32.EnumWindows(callback_type(_rename), 0)


def _view3d_override():
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


def _add_text(
    page,
    text_id: str,
    parent_key: str,
    *,
    x_mm: float,
    y_mm: float,
    label: str,
) -> None:
    entry = page.texts.add()
    entry.id = text_id
    entry.title = f"幅確認テキスト{text_id[-2:]}"
    entry.body = f"{label}{text_id[-2:]}"
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    entry.x_mm = x_mm
    entry.y_mm = y_mm
    entry.width_mm = 54.0
    entry.height_mm = 14.0
    entry.font_size_pt = 18.0
    entry.writing_mode = "horizontal"


def _add_balloon(page, balloon_id: str, parent_key: str) -> None:
    entry = page.balloons.add()
    entry.id = balloon_id
    entry.title = balloon_id
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key


def _prepare_rows() -> None:
    from bmanga_dev_layer_panel_width_visual.core.work import get_work
    from bmanga_dev_layer_panel_width_visual.utils import layer_stack, text_real_object

    work = get_work(bpy.context)
    page = work.pages[0]
    coma = page.comas[0]
    page_key = str(page.id)
    coma_key = f"{page_key}:{coma.coma_id or coma.id}"
    for index in range(10):
        _add_text(
            page,
            f"root_text_{index:02d}",
            page_key,
            x_mm=18.0,
            y_mm=22.0 + index * 20.0,
            label="ページ直下",
        )
        _add_balloon(page, f"root_balloon_{index:02d}", page_key)
    for index in range(5):
        _add_text(
            page,
            f"coma_text_{index:02d}",
            coma_key,
            x_mm=108.0,
            y_mm=34.0 + index * 35.0,
            label="コマ内",
        )
        _add_balloon(page, f"coma_balloon_{index:02d}", coma_key)
    for entry in page.texts:
        assert text_real_object.ensure_text_real_object(
            scene=bpy.context.scene,
            entry=entry,
            page=page,
        ) is not None
    layer_stack.sync_layer_stack_after_data_change(bpy.context)
    stack = bpy.context.scene.bmanga_layer_stack
    selected = next(
        index
        for index, item in enumerate(stack)
        if item.kind == "balloon" and item.key.endswith("coma_balloon_02")
    )
    bpy.context.scene.bmanga_active_layer_stack_index = selected


def _show_sidebar() -> None:
    override = _view3d_override()
    window = override["window"]
    area = override["area"]
    with bpy.context.temp_override(**override):
        ui_region = next((item for item in area.regions if item.type == "UI"), None)
        if ui_region is None or int(ui_region.width) <= 1:
            bpy.ops.screen.region_toggle(region_type="UI")
        if bpy.ops.wm.redraw_timer.poll():
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=4)
    with bpy.context.temp_override(window=window, screen=window.screen, area=area):
        for region in area.regions:
            if region.type == "UI" and int(region.width) > 1:
                region.active_panel_category = "B-MANGA"
        if bpy.ops.wm.redraw_timer.poll():
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=5)


def _setup() -> None:
    global _DONE
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _load_addon()
        work_path = Path(tempfile.mkdtemp(prefix="bmanga_layer_panel_width_")) / "WidthVisual.bmanga"
        assert "FINISHED" in bpy.ops.bmanga.work_new(filepath=str(work_path))
        assert "FINISHED" in bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        _prepare_rows()
        _show_sidebar()
        _set_test_window_title()
        (OUT_DIR / "ready.txt").write_text("ready", encoding="utf-8")
        _DONE = True
    except BaseException as exc:  # noqa: BLE001
        (OUT_DIR / "error.txt").write_text(repr(exc), encoding="utf-8")
        raise


def _fail_safe():
    if _DONE:
        return 1.0
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(_setup, first_interval=1.0)
bpy.app.timers.register(_fail_safe, first_interval=60.0)
