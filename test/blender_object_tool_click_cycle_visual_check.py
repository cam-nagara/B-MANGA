"""オブジェクトツールの同一点クリック循環を実イベントと画面で検証する.

実行:
  blender.exe --factory-startup --enable-event-simulate \
    --python test/blender_object_tool_click_cycle_visual_check.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d

ROOT = Path(__file__).resolve().parents[1]
VERIFY_DIR = ROOT / "_verify" / "2026-07-28_layer_click_cycle"
OUT_PNG = VERIFY_DIR / "layer_click_cycle_balloon.png"
OUT_JSON = VERIFY_DIR / "result.json"

_STATE = {
    "phase": "create",
    "events": [],
    "records": [],
    "problems": [],
    "candidate_scans": 0,
}


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_click_cycle",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_click_cycle"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _view3d():
    wm = bpy.data.window_managers[0]
    window = wm.windows[0]
    for area in window.screen.areas:
        if area.type != "VIEW_3D":
            continue
        region = next(r for r in area.regions if r.type == "WINDOW")
        return window, area, region, area.spaces.active.region_3d
    raise AssertionError("VIEW_3Dが見つかりません")


def _world_to_window(region, rv3d, x_mm: float, y_mm: float) -> tuple[int, int]:
    point = location_3d_to_region_2d(
        region,
        rv3d,
        (x_mm / 1000.0, y_mm / 1000.0, 0.0),
    )
    if point is None:
        raise AssertionError("クリック位置を画面へ投影できません")
    return int(region.x + point.x), int(region.y + point.y)


def _create_work() -> None:
    root = Path(tempfile.mkdtemp(prefix="bmanga_click_cycle_"))
    result = bpy.ops.bmanga.work_new(filepath=str(root / "ClickCycle.bmanga"))
    if "FINISHED" not in result:
        raise AssertionError(f"作品作成に失敗: {result}")
    result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
    if "FINISHED" not in result:
        raise AssertionError(f"ページファイルを開けません: {result}")


def _setup_scene(window, area, region, rv3d) -> tuple[float, float]:
    from bmanga_dev_click_cycle.utils import balloon_curve_object, layer_stack, page_grid

    context = bpy.context
    scene = context.scene
    work = scene.bmanga_work
    page = work.pages[0]
    page_id = str(page.id)

    balloon = page.balloons.add()
    balloon.id = "cycle_balloon"
    balloon.title = "背面フキダシ"
    balloon.shape = "rect"
    balloon.x_mm = 55.0
    balloon.y_mm = 95.0
    balloon.width_mm = 72.0
    balloon.height_mm = 54.0
    balloon.parent_kind = "page"
    balloon.parent_key = page_id
    balloon_curve_object.ensure_balloon_curve_object(
        scene=scene,
        entry=balloon,
        page=page,
    )

    text = page.texts.add()
    text.id = "cycle_text"
    text.title = "前面テキスト"
    text.body = "重なり選択"
    text.x_mm = 60.0
    text.y_mm = 100.0
    text.width_mm = 62.0
    text.height_mm = 44.0
    text.parent_kind = "page"
    text.parent_key = page_id
    text.parent_balloon_id = balloon.id

    top_text = page.texts.add()
    top_text.id = "cycle_text_top"
    top_text.title = "最前面テキスト"
    top_text.body = "重なり選択"
    top_text.x_mm = 60.0
    top_text.y_mm = 100.0
    top_text.width_mm = 62.0
    top_text.height_mm = 44.0
    top_text.parent_kind = "page"
    top_text.parent_key = page_id
    top_text.parent_balloon_id = balloon.id
    layer_stack.sync_layer_stack_after_data_change(context)

    ox, oy = page_grid.page_total_offset_mm(work, scene, 0)
    center_x = ox + 91.0
    center_y = oy + 122.0
    rv3d.view_perspective = "ORTHO"
    rv3d.view_rotation = (1.0, 0.0, 0.0, 0.0)
    rv3d.view_location = (
        (ox + float(work.paper.canvas_width_mm) * 0.5) / 1000.0,
        (oy + float(work.paper.canvas_height_mm) * 0.5) / 1000.0,
        0.0,
    )
    rv3d.view_distance = max(
        float(work.paper.canvas_width_mm),
        float(work.paper.canvas_height_mm),
    ) / 1000.0 * 1.2
    area.spaces.active.show_region_ui = True
    for candidate in area.regions:
        if candidate.type == "UI":
            try:
                candidate.active_panel_category = "B-MANGA"
            except Exception:  # noqa: BLE001
                pass
    area.tag_redraw()
    return center_x, center_y


def _queue_click(x: int, y: int, *, include_move: bool) -> None:
    if include_move:
        _STATE["events"].append(("MOUSEMOVE", "NOTHING", x, y))
    _STATE["events"].extend(
        (
            ("LEFTMOUSE", "PRESS", x, y),
            ("LEFTMOUSE", "RELEASE", x, y),
        )
    )


def _record(name: str) -> None:
    from bmanga_dev_click_cycle.operators import coma_modal_state
    from bmanga_dev_click_cycle.ui import selection_cycle_overlay
    from bmanga_dev_click_cycle.utils import layer_stack, object_selection

    op = coma_modal_state.get_active("object_tool")
    active = layer_stack.active_stack_item(bpy.context)
    _STATE["records"].append(
        {
            "name": name,
            "cycle_index": int(getattr(op, "_click_cycle_index", -1)) if op else -1,
            "cycle_keys": list(getattr(op, "_click_cycle_keys", ())) if op else [],
            "active_kind": str(getattr(active, "kind", "") or "") if active else "",
            "active_label": str(getattr(active, "label", "") or "") if active else "",
            "selected_keys": object_selection.get_keys(bpy.context),
            "overlay": str(selection_cycle_overlay._state.label or ""),  # noqa: SLF001
            "candidate_scans": int(_STATE["candidate_scans"]),
        }
    )


def _screenshot(window, area) -> None:
    VERIFY_DIR.mkdir(parents=True, exist_ok=True)
    area.tag_redraw()
    with bpy.context.temp_override(window=window):
        result = bpy.ops.screen.screenshot(
            "EXEC_DEFAULT",
            filepath=str(OUT_PNG),
            check_existing=False,
        )
    if result != {"FINISHED"}:
        raise AssertionError(f"スクリーンショット失敗: {result}")


def _evaluate() -> None:
    records = {item["name"]: item for item in _STATE["records"]}
    first = records.get("first", {})
    second = records.get("second", {})
    third = records.get("third", {})
    reset = records.get("reset", {})
    restarted = records.get("restarted", {})
    expired = records.get("expired", {})
    problems = _STATE["problems"]
    if (
        first.get("active_kind") != "text"
        or first.get("active_label") != "最前面テキスト"
        or first.get("cycle_index") != 0
    ):
        problems.append(f"1回目が通常のテキスト選択ではありません: {first}")
    if (
        second.get("active_kind") != "text"
        or second.get("active_label") != "前面テキスト"
        or second.get("cycle_index") != 1
    ):
        problems.append(f"2回目で同種の背面テキストへ循環しません: {second}")
    if third.get("active_kind") != "balloon" or third.get("cycle_index") != 2:
        problems.append(f"3回目で背面フキダシへ循環しません: {third}")
    if "背面フキダシ" not in str(third.get("overlay", "")):
        problems.append(f"カーソル名表示がフキダシ名ではありません: {third}")
    if reset.get("cycle_keys"):
        problems.append(f"別位置へのマウス移動で循環状態が解除されません: {reset}")
    if (
        restarted.get("active_kind") != "text"
        or restarted.get("active_label") not in {"最前面テキスト", "前面テキスト"}
        or restarted.get("cycle_index") != 0
        or "1/4" not in str(restarted.get("overlay", ""))
    ):
        problems.append(f"解除後のクリックが通常選択候補から再開しません: {restarted}")
    if expired.get("overlay"):
        problems.append(f"一時レイヤー名が期限後も残ります: {expired}")
    if int(_STATE["candidate_scans"]) != 2:
        problems.append(
            "候補収集が初回クリックと循環解除後以外にも再実行されました: "
            f"{_STATE['candidate_scans']}回"
        )
    if not OUT_PNG.exists() or OUT_PNG.stat().st_size < 1000:
        problems.append("AI目視用スクリーンショットが生成されません")

    VERIFY_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(
            {
                "problems": problems,
                "records": _STATE["records"],
                "setup_debug": _STATE.get("setup_debug"),
                "screenshot": str(OUT_PNG),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if problems:
        print("BMANGA_OBJECT_CLICK_CYCLE_NG:", "; ".join(problems), flush=True)
    else:
        print("BMANGA_OBJECT_CLICK_CYCLE_OK", flush=True)


def _tick():
    try:
        window, area, region, rv3d = _view3d()
        phase = _STATE["phase"]
        if phase == "create":
            _create_work()
            _STATE["phase"] = "setup"
            return 0.8
        if phase == "setup":
            _STATE["world_target"] = _setup_scene(window, area, region, rv3d)
            _STATE["phase"] = "activate"
            return 0.5
        if phase == "activate":
            from bmanga_dev_click_cycle.operators import object_tool_click_candidates
            from bmanga_dev_click_cycle.utils import shortcut_visibility

            if shortcut_visibility._area_bmanga_status(area) != "bmanga":
                if int(_STATE.get("tab_retry", 0)) < 20:
                    _STATE["tab_retry"] = int(_STATE.get("tab_retry", 0)) + 1
                    for candidate in area.regions:
                        if candidate.type == "UI":
                            try:
                                candidate.active_panel_category = "B-MANGA"
                            except Exception:  # noqa: BLE001
                                pass
                    area.tag_redraw()
                    return 0.2
                raise AssertionError("B-MANGAタブをアクティブにできません")
            _STATE["click_xy"] = _world_to_window(
                region,
                rv3d,
                *_STATE["world_target"],
            )
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.bmanga.object_tool("INVOKE_DEFAULT")
            original_candidates_at_world = object_tool_click_candidates.candidates_at_world

            def _counted_candidates_at_world(*args, **kwargs):
                _STATE["candidate_scans"] += 1
                return original_candidates_at_world(*args, **kwargs)

            object_tool_click_candidates.candidates_at_world = _counted_candidates_at_world
            x, y = _STATE["click_xy"]
            _queue_click(x, y, include_move=True)
            _STATE["phase"] = "events_first"
            return 0.2
        if _STATE["events"]:
            event_type, value, x, y = _STATE["events"].pop(0)
            window.event_simulate(type=event_type, value=value, x=x, y=y)
            return 0.08
        if phase == "events_first":
            _record("first")
            _STATE["phase"] = "queue_second"
            # 0.4秒未満でも、フキダシと重なるテキストは編集モードへ
            # 誤遷移せず2候補目へ循環する。
            return 0.12
        if phase == "queue_second":
            x, y = _STATE["click_xy"]
            _queue_click(x, y, include_move=False)
            _STATE["phase"] = "events_second"
            return 0.05
        if phase == "events_second":
            _record("second")
            _STATE["phase"] = "queue_third"
            return 0.12
        if phase == "queue_third":
            x, y = _STATE["click_xy"]
            _queue_click(x, y, include_move=False)
            _STATE["phase"] = "events_third"
            return 0.05
        if phase == "events_third":
            _record("third")
            _screenshot(window, area)
            x, y = _STATE["click_xy"]
            _STATE["events"].append(("MOUSEMOVE", "NOTHING", x + 30, y + 20))
            _STATE["phase"] = "events_reset"
            return 0.05
        if phase == "events_reset":
            _record("reset")
            x, y = _STATE["click_xy"]
            _queue_click(x, y, include_move=True)
            _STATE["phase"] = "events_restart"
            return 0.05
        if phase == "events_restart":
            _record("restarted")
            _STATE["phase"] = "wait_expire"
            return 1.45
        if phase == "wait_expire":
            _record("expired")
            _evaluate()
            bpy.ops.wm.quit_blender()
            return None
    except Exception as exc:  # noqa: BLE001
        import traceback

        _STATE["problems"].append(f"exception: {exc}")
        _STATE["records"].append({"traceback": traceback.format_exc()})
        print(traceback.format_exc(), flush=True)
        try:
            _evaluate()
        except Exception:  # noqa: BLE001
            print(traceback.format_exc(), flush=True)
        finally:
            bpy.ops.wm.quit_blender()
        return None
    return 0.1


def main() -> None:
    VERIFY_DIR.mkdir(parents=True, exist_ok=True)
    _STATE["mod"] = _load_addon()
    bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)


if __name__ == "__main__":
    main()
