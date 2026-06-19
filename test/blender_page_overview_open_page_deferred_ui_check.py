"""Blender UI check: overview page double-click opens the page blend deferred."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_page_overview_open_ui",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_page_overview_open_ui"] = mod
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


def _double_click_event_for_page(page_index: int):
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from bmanga_dev_page_overview_open_ui.utils import geom, page_grid

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
        rv3d.view_perspective = "ORTHO"
        rv3d.view_location = Vector((geom.mm_to_m(center_x), geom.mm_to_m(center_y), 0.0))
        rv3d.view_distance = 1.0
        rv3d.view_camera_zoom = 0
        rv3d.view_camera_offset = (0.0, 0.0)
        try:
            space.shading.type = "SOLID"
            space.shading.light = "FLAT"
            space.overlay.show_floor = False
            space.overlay.show_axis_x = False
            space.overlay.show_axis_y = False
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
    if not (0 <= point.x < region.width and 0 <= point.y < region.height):
        raise AssertionError(f"対象ページの中心が画面外です: {point!r}")
    return SimpleNamespace(
        type="LEFTMOUSE",
        value="DOUBLE_CLICK",
        mouse_x=int(region.x + point.x),
        mouse_y=int(region.y + point.y),
        ctrl=False,
        shift=False,
        alt=False,
    )


def _start_check(temp_root: Path) -> Path:
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PageOverviewOpen.bmanga"))
    assert result == {"FINISHED"}, result
    result = bpy.ops.bmanga.page_add()
    assert result == {"FINISHED"}, result
    scene = bpy.context.scene
    work = scene.bmanga_work
    work.active_page_index = 0
    scene.bmanga_overview_mode = True
    work_path = Path(bpy.data.filepath).resolve()
    expected = temp_root / "PageOverviewOpen.bmanga" / "p0002" / "page.blend"

    event = _double_click_event_for_page(1)
    from bmanga_dev_page_overview_open_ui.operators import mode_op

    resolved = mode_op.page_file_index_from_viewport_event(bpy.context, event)
    assert resolved == 1, resolved
    fake_operator = SimpleNamespace()
    result = mode_op.BMANGA_OT_enter_coma_mode_from_viewport.invoke(
        fake_operator,
        bpy.context,
        event,
    )
    assert result == {"FINISHED"}, result
    assert Path(bpy.data.filepath).resolve() == work_path, "イベント中にページファイルを開いています"
    return expected


def _assert_opened(expected: Path) -> None:
    assert Path(bpy.data.filepath).resolve() == expected.resolve(), bpy.data.filepath
    assert bpy.context.scene.bmanga_current_page_id == "p0002"
    assert int(getattr(bpy.context.scene.bmanga_work, "active_page_index", -1)) == 1


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        bpy.context.preferences.view.show_splash = False
    except Exception:
        pass
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_page_overview_open_"))
    attempts = {"count": 0}

    def _timer():
        attempts["count"] += 1
        if bpy.context.window is None and attempts["count"] < 30:
            return 0.1
        try:
            expected = _start_check(temp_root)
        except Exception:
            traceback.print_exc()
            os._exit(1)

        open_attempts = {"count": 0}

        def _assert_timer():
            open_attempts["count"] += 1
            try:
                if (
                    Path(bpy.data.filepath).resolve() != expected.resolve()
                    and open_attempts["count"] < 40
                ):
                    return 0.1
                _assert_opened(expected)
            except Exception:
                traceback.print_exc()
                os._exit(1)
            print("BMANGA_PAGE_OVERVIEW_OPEN_PAGE_DEFERRED_UI_CHECK_OK", flush=True)
            os._exit(0)
            return None

        bpy.app.timers.register(_assert_timer, first_interval=0.1, persistent=True)
        return None

    bpy.app.timers.register(_timer, first_interval=0.1)


if __name__ == "__main__":
    main()
