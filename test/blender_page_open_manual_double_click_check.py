"""Blender UI check: ページ一覧での連続クリック (PRESS x2) でページファイルが開く.

ペンタブレット等では Blender の DOUBLE_CLICK イベントが発生しないため、
- 通常クリック経路 (bname.page_pick_viewport) の自前連続クリック判定
- 常駐オブジェクトツール経路 (_handle_left_press) のページ優先判定
の両方を検証する。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import MethodType, SimpleNamespace

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bname_dev_page_open_manual_dc"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _view3d_context():
    window = next(iter(bpy.context.window_manager.windows), None)
    screen = getattr(window, "screen", None)
    if window is None or screen is None:
        return None
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        space = area.spaces.active
        rv3d = getattr(space, "region_3d", None)
        if region is not None and rv3d is not None:
            return window, screen, area, region, space, rv3d
    return None


def _submodule(path: str):
    import importlib

    return importlib.import_module(f"{MOD_NAME}.{path}")


def _press_event_for_page(page_index: int):
    from bpy_extras.view3d_utils import location_3d_to_region_2d

    geom = _submodule("utils.geom")
    page_grid = _submodule("utils.page_grid")

    view = _view3d_context()
    if view is None:
        raise RuntimeError("VIEW_3D が見つかりません")
    window, screen, area, region, space, rv3d = view
    scene = bpy.context.scene
    work = scene.bname_work
    ox, oy = page_grid.page_total_offset_mm(work, scene, page_index)
    center_x = ox + float(work.paper.canvas_width_mm) * 0.5
    center_y = oy + float(work.paper.canvas_height_mm) * 0.5
    with bpy.context.temp_override(
        window=window,
        screen=screen,
        area=area,
        region=region,
        space_data=space,
        region_data=rv3d,
    ):
        try:
            bpy.ops.view3d.view_axis(type="TOP", align_active=False)
        except Exception:
            pass
        rv3d.view_perspective = "ORTHO"
        rv3d.view_location = Vector((geom.mm_to_m(center_x), geom.mm_to_m(center_y), 0.0))
        rv3d.view_distance = 1.0
        try:
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
        except Exception:
            pass
    point = location_3d_to_region_2d(
        region,
        rv3d,
        (geom.mm_to_m(center_x), geom.mm_to_m(center_y), 0.0),
    )
    if point is None:
        raise AssertionError("対象ページの中心が画面座標へ変換できません")
    return SimpleNamespace(
        type="LEFTMOUSE",
        value="PRESS",
        mouse_x=int(region.x + point.x),
        mouse_y=int(region.y + point.y),
        mouse_region_x=int(point.x),
        mouse_region_y=int(point.y),
        ctrl=False,
        shift=False,
        alt=False,
        oskey=False,
    )


def _check_object_tool_manual_page_open(event) -> None:
    """常駐オブジェクトツールの連続クリックがページファイル判定を優先すること."""
    object_tool_op = _submodule("operators.object_tool_op")

    tool = SimpleNamespace()
    for name in (
        "_clear_click_state",
        "_is_manual_coma_double_click",
        "_remember_coma_click",
        "_page_open_hit_from_event",
        "_coma_open_hit_from_hit",
        "_handle_left_press",
    ):
        setattr(tool, name, MethodType(getattr(object_tool_op.BNAME_OT_object_tool, name), tool))
    tool._clear_click_state()
    opened = []
    tool._try_open_page_file_from_event = lambda _ctx, _event: opened.append(True) or True
    tool._try_enter_coma_from_hit = lambda _ctx, _hit: False
    tool._hit_object = lambda _ctx, _event: None
    tool._selected_move_hit_from_event = lambda _ctx, _event: None
    tool._try_start_layer_drag = lambda _ctx, _event: False
    tool._start_marquee_select = lambda _ctx, _event, _mode: True

    original_extend = object_tool_op.coma_edge_move_op.extend_selected_handle_at_event
    object_tool_op.coma_edge_move_op.extend_selected_handle_at_event = (
        lambda _ctx, _event: False
    )
    try:
        r1 = tool._handle_left_press(bpy.context, event)
        assert r1 == {"RUNNING_MODAL"}, r1
        assert not opened, "1回目のクリックでページファイルを開こうとしています"
        r2 = tool._handle_left_press(bpy.context, event)
        assert r2 == {"FINISHED"}, r2
        assert opened, "オブジェクトツールの連続クリックでページファイルを開けません"
    finally:
        object_tool_op.coma_edge_move_op.extend_selected_handle_at_event = original_extend


def _check_pick_viewport_manual_open(event, expected_index: int) -> None:
    """通常クリック経路の連続クリック判定がページファイルを開くこと."""
    page_op = _submodule("operators.page_op")
    mode_op = _submodule("operators.mode_op")

    page_op._clear_page_open_click_state()
    first = page_op._detect_page_open_double_click(bpy.context, event)
    assert first is None, f"1回目のクリックで開いてしまいます: {first}"
    second = page_op._detect_page_open_double_click(bpy.context, event)
    assert second == expected_index, f"2回目のクリックで開けません: {second}"

    # invoke 経路: 2回目の PRESS で FINISHED (遅延オープン予約) になること
    page_op._clear_page_open_click_state()
    scheduled = []
    original_schedule = mode_op.schedule_open_page_file
    original_allowed = page_op.shortcut_visibility.shortcuts_allowed
    mode_op.schedule_open_page_file = lambda idx: scheduled.append(int(idx)) or True
    page_op.shortcut_visibility.shortcuts_allowed = lambda _ctx: True
    try:
        view = _view3d_context()
        window, screen, area, region, space, rv3d = view
        fake_op = SimpleNamespace(report=lambda *_a, **_k: None)
        with bpy.context.temp_override(
            window=window, screen=screen, area=area, region=region,
            space_data=space, region_data=rv3d,
        ):
            page_op.BNAME_OT_page_pick_viewport.invoke(fake_op, bpy.context, event)
            r2 = page_op.BNAME_OT_page_pick_viewport.invoke(fake_op, bpy.context, event)
        assert r2 == {"FINISHED"}, r2
        assert scheduled == [expected_index], scheduled
    finally:
        mode_op.schedule_open_page_file = original_schedule
        page_op.shortcut_visibility.shortcuts_allowed = original_allowed


def _start_check(temp_root: Path) -> None:
    result = bpy.ops.bname.work_new(filepath=str(temp_root / "PageOpenManualDC.bname"))
    assert result == {"FINISHED"}, result
    result = bpy.ops.bname.page_add()
    assert result == {"FINISHED"}, result
    scene = bpy.context.scene
    work = scene.bname_work
    work.active_page_index = 0
    scene.bname_overview_mode = True

    event = _press_event_for_page(1)
    _check_object_tool_manual_page_open(event)
    _check_pick_viewport_manual_open(event, 1)


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        bpy.context.preferences.view.show_splash = False
    except Exception:
        pass
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bname_page_open_manual_dc_"))
    attempts = {"count": 0}

    def _timer():
        attempts["count"] += 1
        if bpy.context.window is None and attempts["count"] < 30:
            return 0.1
        try:
            _start_check(temp_root)
        except Exception:
            traceback.print_exc()
            os._exit(1)
        print("BNAME_PAGE_OPEN_MANUAL_DOUBLE_CLICK_CHECK_OK", flush=True)
        os._exit(0)
        return None

    bpy.app.timers.register(_timer, first_interval=0.1)


if __name__ == "__main__":
    main()
