"""オブジェクトツール: 別フキダシ選択直後の未選択フキダシドラッグでズレないか検証.

実行 (UI + イベントシミュレーション必須):
  blender.exe --factory-startup --enable-event-simulate --python test/blender_object_tool_drag_select_then_drag_check.py

ユーザー報告: 「他のフキダシをクリックして選択した直後に、選択されていない
フキダシの内側をドラッグすると、フキダシの位置がハンドルからズレる」。
本テストは実イベント (マウス移動/プレス/リリース) を注入して
クリック→ドラッグの実経路を再現し、作品データ上の位置・実体オブジェクト
位置・選択ハンドル矩形の三者一致を検証する。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = Path(tempfile.gettempdir()) / "bmanga_drag_select_then_drag_result.json"

_STATE: dict = {"step": 0, "events": [], "logs": [], "temp": None, "mod": None}


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_drag_select_check",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_drag_select_check"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _view3d():
    wm = bpy.data.window_managers[0]
    if not wm.windows:
        raise AssertionError("ウィンドウがありません")
    window = wm.windows[0]
    for area in window.screen.areas:
        if area.type == "VIEW_3D":
            region = next(r for r in area.regions if r.type == "WINDOW")
            rv3d = area.spaces.active.region_3d
            return window, area, region, rv3d
    raise AssertionError("VIEW_3D が見つかりません")


def _world_mm_to_window_px(region, rv3d, x_mm: float, y_mm: float):
    co = location_3d_to_region_2d(region, rv3d, (x_mm / 1000.0, y_mm / 1000.0, 0.0))
    if co is None:
        raise AssertionError(f"投影失敗: ({x_mm}, {y_mm})")
    return int(region.x + co.x), int(region.y + co.y)


def _balloon_world_center_mm(work, scene, page_index, entry):
    from bmanga_dev_drag_select_check.utils import page_grid

    ox, oy = page_grid.page_total_offset_mm(work, scene, page_index)
    return (
        ox + float(entry.x_mm) + float(entry.width_mm) * 0.5,
        oy + float(entry.y_mm) + float(entry.height_mm) * 0.5,
    )


def _create_work():
    scene = bpy.context.scene
    scene.bmanga_overview_mode = True
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_drag_select_"))
    _STATE["temp"] = temp_root
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "DragSelect.bmanga"))
    if "FINISHED" not in result:
        raise AssertionError("作品作成に失敗しました")


def _setup_scene():
    from bmanga_dev_drag_select_check.utils import balloon_curve_object

    scene = bpy.context.scene
    work = scene.bmanga_work
    page = work.pages[0]

    def add_balloon(bid, x, y, with_text):
        b = page.balloons.add()
        b.id = bid
        b.title = bid
        b.shape = "rect"
        b.x_mm = x
        b.y_mm = y
        b.width_mm = 50.0
        b.height_mm = 30.0
        b.parent_kind = "page"
        b.parent_key = page.id
        balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=b, page=page)
        if with_text:
            t = page.texts.add()
            t.id = f"text_{bid}"
            t.body = "テスト"
            t.x_mm = x + 15.0
            t.y_mm = y + 5.0
            t.width_mm = 20.0
            t.height_mm = 20.0
            t.parent_balloon_id = bid
        return b

    add_balloon("sel_first", 20.0, 160.0, False)
    add_balloon("drag_target", 90.0, 40.0, False)
    return work, page


def _frame_view(window, area, region, rv3d, work, scene):
    from bmanga_dev_drag_select_check.utils import page_grid

    ox, oy = page_grid.page_total_offset_mm(work, scene, 0)
    cw = float(work.paper.canvas_width_mm)
    ch = float(work.paper.canvas_height_mm)
    rv3d.view_perspective = "ORTHO"
    rv3d.view_rotation = (1.0, 0.0, 0.0, 0.0)
    rv3d.view_location = ((ox + cw * 0.5) / 1000.0, (oy + ch * 0.5) / 1000.0, 0.0)
    rv3d.view_distance = max(cw, ch) / 1000.0 * 1.3
    # 実際のユーザー環境と同じく、N パネルの B-MANGA タブを表示状態にする
    # (タブ非表示だとキーマップ監視が常駐ツールを終了させてしまうため)
    space = area.spaces.active
    space.show_region_ui = True
    for r in area.regions:
        if r.type == "UI":
            try:
                r.active_panel_category = "B-MANGA"
            except Exception:  # noqa: BLE001
                pass


def _queue_drag_events(window, region, rv3d, work, scene, page):
    bA = page.balloons[0]
    bB = page.balloons[1]
    axc, ayc = _balloon_world_center_mm(work, scene, 0, bA)
    bxc, byc = _balloon_world_center_mm(work, scene, 0, bB)
    # あえて「ど真ん中」を掴む: 未選択の非フラッシュフキダシでは中心ズラし
    # ハンドルを拾わず、通常の移動になることを検証する (v0.6.293 修正)
    ax, ay = _world_mm_to_window_px(region, rv3d, axc, ayc)
    bx, by = _world_mm_to_window_px(region, rv3d, bxc, byc)
    # 30mm 相当のドラッグ距離を px へ換算
    bx2, by2 = _world_mm_to_window_px(region, rv3d, bxc + 30.0, byc - 20.0)

    _STATE["px_debug"] = {
        "A_px": (ax, ay),
        "B_px": (bx, by),
        "B2_px": (bx2, by2),
        "region": (region.x, region.y, region.width, region.height),
    }
    events = []
    # クリックで A を選択
    events.append(("MOUSEMOVE", "NOTHING", ax, ay))
    events.append(("LEFTMOUSE", "PRESS", ax, ay))
    events.append(("LEFTMOUSE", "RELEASE", ax, ay))
    # 直後に未選択の B をドラッグ
    events.append(("MOUSEMOVE", "NOTHING", bx, by))
    events.append(("LEFTMOUSE", "PRESS", bx, by))
    steps = 6
    for i in range(1, steps + 1):
        mx = int(bx + (bx2 - bx) * i / steps)
        my = int(by + (by2 - by) * i / steps)
        events.append(("MOUSEMOVE", "NOTHING", mx, my))
    events.append(("LEFTMOUSE", "RELEASE", bx2, by2))
    _STATE["events"] = events
    _STATE["expected_delta_mm"] = (30.0, -20.0)
    _STATE["grab_offset_mm"] = (bxc, byc)


def _find_balloon_object(bid):
    for obj in bpy.data.objects:
        if (
            str(obj.get("bmanga_kind", "") or "") == "balloon"
            and str(obj.get("bmanga_id", "") or "") == bid
        ):
            return obj
    return None


def _evaluate():
    from bmanga_dev_drag_select_check.operators import object_tool_selection
    from bmanga_dev_drag_select_check.utils import object_selection, page_grid

    scene = bpy.context.scene
    work = scene.bmanga_work
    page = work.pages[0]
    bB = page.balloons[1]
    ox, oy = page_grid.page_total_offset_mm(work, scene, 0)
    key = object_selection.balloon_key(page, bB)
    rect = object_tool_selection.selection_bounds_for_key(bpy.context, key)
    obj = _find_balloon_object(bB.id)

    entry_xy = (float(bB.x_mm), float(bB.y_mm))
    start_xy = (90.0, 40.0)
    moved = (entry_xy[0] - start_xy[0], entry_xy[1] - start_xy[1])
    exp_dx, exp_dy = _STATE["expected_delta_mm"]

    problems = []
    # 1) ドラッグ距離どおりに動いたか (マウス追従)
    if abs(moved[0] - exp_dx) > 1.5 or abs(moved[1] - exp_dy) > 1.5:
        problems.append(
            f"移動量がマウスとズレ: moved={moved} expected=({exp_dx},{exp_dy})"
        )
    # 2) 実体オブジェクトが entry に追従しているか
    if obj is None:
        problems.append("フキダシ実体が見つからない")
    else:
        ex_ox = ox + entry_xy[0] + float(bB.width_mm) * 0.5
        ex_oy = oy + entry_xy[1] + float(bB.height_mm) * 0.5
        got = (obj.location.x * 1000.0, obj.location.y * 1000.0)
        if abs(got[0] - ex_ox) > 0.05 or abs(got[1] - ex_oy) > 0.05:
            problems.append(f"実体が entry からズレ: obj={got} expected=({ex_ox},{ex_oy})")
    # 3) ハンドル矩形が entry に一致しているか
    if rect is None:
        problems.append("ハンドル矩形が取得できない")
    else:
        if abs(rect.x - (ox + entry_xy[0])) > 0.05 or abs(rect.y - (oy + entry_xy[1])) > 0.05:
            problems.append(
                f"ハンドルが entry からズレ: handle=({rect.x},{rect.y}) expected=({ox + entry_xy[0]},{oy + entry_xy[1]})"
            )
    # 4) 選択キーが B 単独になっているか
    keys = object_selection.get_keys(bpy.context)
    if keys != [key]:
        problems.append(f"選択キーが不正: {keys}")

    from bmanga_dev_drag_select_check.operators import coma_modal_state

    bA = page.balloons[0]
    payload = {
        "problems": problems,
        "entry": entry_xy,
        "moved": moved,
        "keys": keys,
        "entry_A": (float(bA.x_mm), float(bA.y_mm)),
        "scene": bpy.context.scene.name,
        "modal_active": coma_modal_state.get_active("object_tool") is not None,
        "active_page_index": int(work.active_page_index),
        "px_debug": _STATE.get("px_debug"),
        "logs": _STATE["logs"],
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    if problems:
        print("BMANGA_DRAG_SELECT_THEN_DRAG_NG:", "; ".join(problems), flush=True)
    else:
        print("BMANGA_DRAG_SELECT_THEN_DRAG_OK", flush=True)


def _tick():
    step = _STATE["step"]
    _STATE["step"] += 1
    try:
        window, area, region, rv3d = _view3d()
        if step == 0:
            _create_work()
            return 1.0
        if step == 1:
            work, page = _setup_scene()
            _frame_view(window, area, region, rv3d, work, bpy.context.scene)
            return 0.5
        if step == 2:
            # B-MANGA タブが実際にアクティブ判定されるまで再設定して待つ
            from bmanga_dev_drag_select_check.utils import shortcut_visibility as sv

            for r in area.regions:
                if r.type == "UI":
                    try:
                        r.active_panel_category = "B-MANGA"
                    except Exception:  # noqa: BLE001
                        pass
            area.tag_redraw()
            if sv._area_bmanga_status(area) != "bmanga":
                if _STATE.setdefault("tab_retry", 0) < 20:
                    _STATE["tab_retry"] += 1
                    _STATE["step"] = 2
                    return 0.2
                raise AssertionError(
                    f"B-MANGA タブをアクティブにできません: {sv._area_bmanga_status(area)}"
                )
            scene = bpy.context.scene
            work = scene.bmanga_work
            page = work.pages[0]
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.bmanga.object_tool("INVOKE_DEFAULT")
            from bmanga_dev_drag_select_check.operators import coma_modal_state

            _STATE["op_strong"] = coma_modal_state.get_active("object_tool")
            _queue_drag_events(window, region, rv3d, work, scene, page)
            return 0.3
        events = _STATE["events"]
        if events:
            # 1 tick に 1 イベント注入し、本物のマウス操作と同じく
            # イベントごとにメインループへ処理させる
            from bmanga_dev_drag_select_check.operators import coma_modal_state
            from bmanga_dev_drag_select_check.utils import object_selection

            from bmanga_dev_drag_select_check.utils import shortcut_visibility as sv

            op = coma_modal_state.get_active("object_tool")
            strong = _STATE.get("op_strong")
            area_status = []
            for w in bpy.data.window_managers[0].windows:
                for a in w.screen.areas:
                    if a.type == "VIEW_3D":
                        area_status.append(sv._area_bmanga_status(a))
            _STATE["logs"].append(
                {
                    "before": _STATE.get("last_event"),
                    "modal": op is not None,
                    "dragging": bool(getattr(strong, "_dragging", False)) if strong else None,
                    "action": str(getattr(strong, "_drag_action", "")) if strong else None,
                    "finished": bool(getattr(strong, "_externally_finished", False)) if strong else None,
                    "keys": object_selection.get_keys(bpy.context),
                    "allowed": {
                        "interaction": sv.interaction_enabled(),
                        "file_scope": sv.shortcut_file_scope_allowed(),
                        "panel": sv.any_bmanga_panel_visible(),
                        "area_status": area_status,
                    },
                }
            )
            etype, evalue, x, y = events.pop(0)
            _STATE["last_event"] = (etype, evalue, x, y)
            window.event_simulate(type=etype, value=evalue, x=x, y=y)
            return 0.05
        if step < 100:
            _evaluate()
            bpy.ops.wm.quit_blender()
            return None
    except Exception as exc:  # noqa: BLE001
        import traceback

        _STATE["logs"].append(traceback.format_exc())
        OUT_JSON.write_text(
            json.dumps({"problems": [f"exception: {exc}"], "logs": _STATE["logs"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        print("BMANGA_DRAG_SELECT_THEN_DRAG_NG: exception", exc, flush=True)
        bpy.ops.wm.quit_blender()
        return None
    return 0.05


def main() -> None:
    _STATE["mod"] = _load_addon()
    bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)


if __name__ == "__main__":
    main()
