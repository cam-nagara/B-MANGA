"""Blender通常画面: 詳細設定確定後にObject Toolを再開して実ドラッグできる。"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import traceback

import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d


ROOT = Path(__file__).resolve().parents[1]
MODULE = "bmanga_dev_detail_object_tool_resume"
STATE = {"phase": "point_menu"}


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()


def _sub(path: str):
    __import__(f"{MODULE}.{path}")
    return sys.modules[f"{MODULE}.{path}"]


def _view3d_override():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((item for item in area.regions if item.type == "WINDOW"), None)
            if region is not None:
                return window, area, region
    raise AssertionError("VIEW_3D not found")


def _screen_point(x_mm: float, y_mm: float) -> tuple[int, int]:
    _window, area, region = _view3d_override()
    point = location_3d_to_region_2d(
        region,
        area.spaces.active.region_3d,
        (float(x_mm) / 1000.0, float(y_mm) / 1000.0, 0.0),
    )
    assert point is not None
    return int(region.x + point.x), int(region.y + point.y)


def _current_text():
    page = bpy.context.scene.bmanga_work.pages[0]
    return next(item for item in page.texts if item.id == "detail_resume_text")


def _prepare_drag():
    selection = _sub("operators.object_tool_selection")
    rect = selection.selection_bounds_for_key(bpy.context, STATE["selection_key"])
    assert rect is not None
    start = _screen_point(rect.x + rect.width * 0.5, rect.y + rect.height * 0.5)
    end = _screen_point(rect.x + rect.width * 0.5 + 10.0, rect.y + rect.height * 0.5)
    STATE["drag_start"] = start
    STATE["drag_end"] = end
    STATE["original_x"] = float(_current_text().x_mm)


def _tick():
    try:
        phase = STATE["phase"]
        window, area, region = _view3d_override()
        runtime = _sub("operators.detail_dialog_runtime")
        modal_state = _sub("operators.coma_modal_state")
        if phase == "point_menu":
            selection = _sub("operators.object_tool_selection")
            rect = selection.selection_bounds_for_key(bpy.context, STATE["selection_key"])
            assert rect is not None
            STATE["menu_point"] = _screen_point(*rect.center)
            STATE["phase"] = "press_menu"
            window.event_simulate(
                type="MOUSEMOVE",
                value="NOTHING",
                x=STATE["menu_point"][0],
                y=STATE["menu_point"][1],
            )
            return 0.15
        if phase == "press_menu":
            STATE["phase"] = "release_menu"
            window.event_simulate(
                type="RIGHTMOUSE",
                value="PRESS",
                x=STATE["menu_point"][0],
                y=STATE["menu_point"][1],
            )
            return 0.15
        if phase == "release_menu":
            STATE["phase"] = "choose_detail"
            window.event_simulate(
                type="RIGHTMOUSE",
                value="RELEASE",
                x=STATE["menu_point"][0],
                y=STATE["menu_point"][1],
            )
            return 0.35
        if phase == "choose_detail":
            STATE["phase"] = "change_and_close"
            window.event_simulate(type="RET", value="PRESS")
            window.event_simulate(type="RET", value="RELEASE")
            return 0.4
        if phase == "change_and_close":
            if not runtime._OPEN_ACTUAL_SESSIONS:
                return 0.1
            session = next(iter(runtime._OPEN_ACTUAL_SESSIONS.values()))
            session.target.data.font_size_value = float(session.target.data.font_size_value) + 1.0
            runtime.sync_actual_session(bpy.context, session)
            STATE["phase"] = "wait_restart"
            window.event_simulate(type="RET", value="PRESS")
            window.event_simulate(type="RET", value="RELEASE")
            return 0.4
        if phase == "wait_restart":
            if runtime._OPEN_ACTUAL_SESSIONS:
                return 0.1
            active = modal_state.get_active("object_tool")
            if active is None or id(active) == STATE["before_identity"]:
                return 0.1
            assert _sub("utils.object_selection").get_keys(bpy.context) == [
                STATE["selection_key"]
            ]
            assert _sub("operators.object_tool_selection").active_selection_key(
                bpy.context
            ) == STATE["selection_key"]
            _prepare_drag()
            STATE["phase"] = "press_drag"
            window.event_simulate(
                type="MOUSEMOVE",
                value="NOTHING",
                x=STATE["drag_start"][0],
                y=STATE["drag_start"][1],
            )
            return 0.15
        if phase == "press_drag":
            STATE["phase"] = "move_drag"
            window.event_simulate(
                type="LEFTMOUSE",
                value="PRESS",
                x=STATE["drag_start"][0],
                y=STATE["drag_start"][1],
            )
            return 0.15
        if phase == "move_drag":
            STATE["phase"] = "release_drag"
            window.event_simulate(
                type="MOUSEMOVE",
                value="NOTHING",
                x=STATE["drag_end"][0],
                y=STATE["drag_end"][1],
            )
            return 0.15
        if phase == "release_drag":
            STATE["phase"] = "invoke_cancel"
            window.event_simulate(
                type="LEFTMOUSE",
                value="RELEASE",
                x=STATE["drag_end"][0],
                y=STATE["drag_end"][1],
            )
            return 0.4
        if phase == "invoke_cancel":
            active = modal_state.get_active("object_tool")
            assert active is not None
            STATE["before_cancel_identity"] = id(active)
            STATE["font_before_cancel"] = float(_current_text().font_size_value)
            STATE["phase"] = "cancel_dialog"
            with bpy.context.temp_override(window=window, area=area, region=region):
                result = bpy.ops.bmanga.layer_detail_open(
                    "INVOKE_DEFAULT",
                    bmanga_id=STATE["bmanga_id"],
                    kind="text",
                )
            assert result == {"RUNNING_MODAL"}, result
            return 0.4
        if phase == "cancel_dialog":
            if not runtime._OPEN_ACTUAL_SESSIONS:
                return 0.1
            session = next(iter(runtime._OPEN_ACTUAL_SESSIONS.values()))
            session.target.data.font_size_value = STATE["font_before_cancel"] + 2.0
            runtime.sync_actual_session(bpy.context, session)
            STATE["phase"] = "wait_cancel_restart"
            window.event_simulate(type="ESC", value="PRESS")
            window.event_simulate(type="ESC", value="RELEASE")
            return 0.4
        if phase == "wait_cancel_restart":
            if runtime._OPEN_ACTUAL_SESSIONS:
                return 0.1
            active = modal_state.get_active("object_tool")
            if active is None or id(active) == STATE["before_cancel_identity"]:
                return 0.1
            assert abs(float(_current_text().font_size_value) - STATE["font_before_cancel"]) < 1.0e-6
            STATE["phase"] = "finish"
            return 0.1
        assert phase == "finish", phase
        final_x = float(_current_text().x_mm)
        assert final_x > STATE["original_x"] + 9.0, (STATE["original_x"], final_x)
        print(
            "BMANGA_DETAIL_OBJECT_TOOL_RESUME_OK: "
            f"x={STATE['original_x']:.3f}->{final_x:.3f}"
        )
        bpy.ops.wm.quit_blender()
        return None
    except Exception:
        traceback.print_exc()
        os._exit(1)


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    work_dir = Path(tempfile.mkdtemp(prefix="bmanga_detail_resume_")) / "Work.bmanga"
    assert bpy.ops.bmanga.work_new(filepath=str(work_dir)) == {"FINISHED"}
    assert bpy.ops.bmanga.open_page_file(index=0) == {"FINISHED"}
    context = bpy.context
    work = context.scene.bmanga_work
    page = work.pages[0]
    text = page.texts.add()
    text.id = "detail_resume_text"
    text.title = "詳細設定後操作"
    text.body = "詳細設定後操作"
    text.x_mm = 40.0
    text.y_mm = 50.0
    text.width_mm = 60.0
    text.height_mm = 30.0
    text.parent_kind = "page"
    text.parent_key = str(page.id)
    text_object = _sub("utils.text_real_object")
    assert text_object.ensure_text_real_object(scene=context.scene, entry=text, page=page)
    stack_mod = _sub("utils.layer_stack")
    stack = stack_mod.sync_layer_stack(context, preserve_active_index=True)
    uid = stack_mod.target_uid("text", f"{stack_mod.page_stack_key(page)}:{text.id}")
    index = next(i for i, item in enumerate(stack) if stack_mod.stack_item_uid(item) == uid)
    assert stack_mod.select_stack_index(context, index)
    selection = _sub("utils.object_selection")
    STATE["selection_key"] = selection.text_key(page, text)
    STATE["bmanga_id"] = text_object.text_object_bmanga_id(page, text)
    window, area, region = _view3d_override()
    with context.temp_override(window=window, area=area, region=region):
        assert bpy.ops.bmanga.object_tool("INVOKE_DEFAULT") == {"RUNNING_MODAL"}
    active = _sub("operators.coma_modal_state").get_active("object_tool")
    assert active is not None
    STATE["before_identity"] = id(active)
    bpy.app.timers.register(_tick, first_interval=0.8)


if __name__ == "__main__":
    main()
