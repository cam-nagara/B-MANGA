"""Blender UI check: ページ一覧での連続クリック (PRESS x2) でページファイルが開く.

ペンタブレット等では Blender の DOUBLE_CLICK イベントが発生しないため、
- 通常クリック経路 (bmanga.page_pick_viewport) の自前連続クリック判定
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
MOD_NAME = "bmanga_dev_page_open_manual_dc"


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
    windows = list(bpy.context.window_manager.windows)
    current = getattr(bpy.context, "window", None)
    if current is not None:
        windows = [current, *[window for window in windows if window != current]]
    for window in windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
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
    work = scene.bmanga_work
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
        # page.blend読込後はB-MANGAサイドバーが自動表示される。WINDOW regionと
        # UI regionが重なる座標をイベントにすると、実装どおりUI操作として
        # 除外されるため、このページプレビュー試験では本文領域を確保する。
        space.show_region_ui = False
        space.show_region_toolbar = False
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
    """通常オブジェクトヒットが重なってもページファイル判定を優先すること."""
    object_tool_op = _submodule("operators.object_tool_op")
    object_selection = _submodule("utils.object_selection")

    tool = SimpleNamespace()
    for name in (
        "_clear_click_state",
        "_is_manual_coma_double_click",
        "_remember_coma_click",
        "_page_open_hit_from_event",
        "_coma_open_hit_from_hit",
        "_handle_left_press",
    ):
        setattr(tool, name, MethodType(getattr(object_tool_op.BMANGA_OT_object_tool, name), tool))
    tool._clear_click_state()
    opened = []
    tool._try_open_page_file_from_event = lambda _ctx, _event: opened.append(True) or True
    tool._try_enter_coma_from_hit = lambda _ctx, _hit: False
    # 実画面ではプレビューと同じ位置にページ本体やレイヤーがあり、通常
    # ヒットも同時に成立する。None固定ではクリック履歴キーの競合を再現できない。
    work = bpy.context.scene.bmanga_work
    underlying_index = min(1, len(work.pages) - 1)
    underlying_page = work.pages[underlying_index]
    tool._hit_object = lambda _ctx, _event: {
        "kind": "page",
        "page": underlying_index,
        "part": "body",
        "key": object_selection.page_key(underlying_page),
    }
    tool._selected_move_hit_from_event = lambda _ctx, _event: None
    tool._try_start_layer_drag = lambda _ctx, _event: False
    tool._start_marquee_select = lambda _ctx, _event, _mode: True
    tool._activate_hit = lambda _ctx, _hit, *, mode: None
    tool._start_point_for_hit = lambda _ctx, _event, _hit: (0.0, 0.0)
    tool._start_object_drag = lambda _ctx, _hit, _x, _y: None

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


def _check_visible_handle_suppresses_page_open(event) -> None:
    """表示ハンドル上ではページを開く連続クリック履歴を作らないこと."""
    object_tool_op = _submodule("operators.object_tool_op")

    tool = SimpleNamespace()
    for name in (
        "_clear_click_state",
        "_is_manual_coma_double_click",
        "_remember_coma_click",
        "_handle_left_press",
    ):
        setattr(tool, name, MethodType(getattr(object_tool_op.BMANGA_OT_object_tool, name), tool))
    tool._clear_click_state()
    opened = []
    tool._try_open_page_file_from_event = lambda _ctx, _event: opened.append(True) or True
    tool._try_enter_coma_from_hit = lambda _ctx, _hit: False
    tool._try_enter_text_edit_from_hit = lambda _ctx, _hit: False
    tool._page_open_hit_from_event = lambda _ctx, _event: {
        "kind": "page_file", "page": 1, "key": "page_file:1",
    }
    tool._coma_open_hit_from_hit = lambda _hit: None
    tool._hit_object = lambda _ctx, _event: {
        "kind": "image",
        "key": "image||visible_handle",
        "part": "top_right",
        "visible_selection_handle": True,
    }
    tool._activate_hit = lambda _ctx, _hit, *, mode: None
    tool._start_point_for_hit = lambda _ctx, _event, _hit: (0.0, 0.0)
    tool._start_object_drag = lambda _ctx, _hit, _x, _y: None

    original_extend = object_tool_op.coma_edge_move_op.extend_selected_handle_at_event
    object_tool_op.coma_edge_move_op.extend_selected_handle_at_event = lambda _ctx, _event: False
    try:
        assert tool._handle_left_press(bpy.context, event) == {"RUNNING_MODAL"}
        assert tool._handle_left_press(bpy.context, event) == {"RUNNING_MODAL"}
        assert not opened, "表示ハンドル上の連続クリックでページファイルを開いています"
        assert not tool._last_click_key, "表示ハンドル上のクリックがページ遷移履歴に残っています"
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
            page_op.BMANGA_OT_page_pick_viewport.invoke(fake_op, bpy.context, event)
            r2 = page_op.BMANGA_OT_page_pick_viewport.invoke(fake_op, bpy.context, event)
        assert r2 == {"FINISHED"}, r2
        assert scheduled == [expected_index], scheduled
    finally:
        mode_op.schedule_open_page_file = original_schedule
        page_op.shortcut_visibility.shortcuts_allowed = original_allowed


def _check_spread_right_half_hit() -> None:
    """見開きページの右半分でもページのヒット判定が当たること."""
    coma_picker = _submodule("operators.coma_picker")
    page_grid = _submodule("utils.page_grid")

    scene = bpy.context.scene
    work = scene.bmanga_work
    page = work.pages[1]
    page.spread = True
    try:
        ox, oy = page_grid.page_total_offset_mm(work, scene, 1)
        cw = float(work.paper.canvas_width_mm)
        ch = float(work.paper.canvas_height_mm)
        left_hit = coma_picker.find_page_at_world_mm(work, ox + cw * 0.5, oy + ch * 0.5)
        right_hit = coma_picker.find_page_at_world_mm(work, ox + cw * 1.5, oy + ch * 0.5)
        assert left_hit == 1, f"見開き左半分のヒット判定に失敗: {left_hit}"
        assert right_hit == 1, f"見開き右半分のヒット判定に失敗: {right_hit}"
    finally:
        page.spread = False
    print("SPREAD_RIGHT_HALF_HIT_OK", flush=True)


def _start_check(temp_root: Path) -> None:
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PageOpenManualDC.bmanga"))
    assert result == {"FINISHED"}, result
    result = bpy.ops.bmanga.page_add()
    assert result == {"FINISHED"}, result
    scene = bpy.context.scene
    work = scene.bmanga_work
    work.active_page_index = 0
    scene.bmanga_overview_mode = True

    _check_spread_right_half_hit()
    event = _press_event_for_page(1)
    _check_object_tool_manual_page_open(event)
    _check_visible_handle_suppresses_page_open(event)
    _check_pick_viewport_manual_open(event, 1)

    # ページファイル内の「ページ一覧プレビュー」でも同じ経路を通る。
    # p0001を開き、隣のp0002プレビューを対象に実座標から再検証する。
    result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
    assert result == {"FINISHED"}, result
    event = _press_event_for_page(1)
    mode_op = _submodule("operators.mode_op")
    page_file_scene = _submodule("utils.page_file_scene")
    page_preview_object = _submodule("utils.page_preview_object")
    coma_picker = _submodule("operators.coma_picker")
    view = _view3d_context()
    assert view is not None
    window, screen, area, region, space, rv3d = view
    with bpy.context.temp_override(
        window=window,
        screen=screen,
        area=area,
        region=region,
        space_data=space,
        region_data=rv3d,
    ):
        role = page_file_scene.current_role(bpy.context)
        page_hit = mode_op.page_file_index_from_viewport_event(bpy.context, event)
        world_xy = coma_picker._event_world_mm(bpy.context, event)  # noqa: SLF001
        rects = page_preview_object.preview_rects_mm(
            bpy.context.scene, bpy.context.scene.bmanga_work,
        )
        assert page_hit == 1, (
            "ページファイル内のp0002プレビューを判定できません: "
            f"role={role!r} hit={page_hit!r} world={world_xy!r} rects={rects!r}"
        )
        _check_object_tool_manual_page_open(event)
        _check_pick_viewport_manual_open(event, 1)

def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        bpy.context.preferences.view.show_splash = False
    except Exception:
        pass
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_page_open_manual_dc_"))
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
        print("BMANGA_PAGE_OPEN_MANUAL_DOUBLE_CLICK_CHECK_OK", flush=True)
        os._exit(0)
        return None

    bpy.app.timers.register(_timer, first_interval=0.1)


if __name__ == "__main__":
    main()
