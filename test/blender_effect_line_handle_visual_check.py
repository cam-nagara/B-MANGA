"""Blender UI実機用: 効果線の選択ハンドルが外側1組だけか確認する。"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Quaternion, Vector


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_effect_handle_visual"
OUT_DIR = Path(
    os.environ.get("BMANGA_EFFECT_HANDLE_VISUAL_OUT", "")
    or (ROOT / ".codex" / "visual" / "effect_line_handle")
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _view3d_override():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            for region in area.regions:
                if region.type == "WINDOW":
                    return bpy.context.temp_override(
                        window=window,
                        screen=window.screen,
                        area=area,
                        region=region,
                    )
    raise RuntimeError("VIEW_3D not found")


def _set_top_view() -> None:
    from bmanga_dev_effect_handle_visual.utils.geom import mm_to_m

    with _view3d_override():
        bpy.ops.view3d.view_axis(type="TOP", align_active=False)
        space = bpy.context.space_data
        rv3d = space.region_3d
        rv3d.view_perspective = "ORTHO"
        rv3d.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
        rv3d.view_location = Vector((mm_to_m(118.0), mm_to_m(147.0), 0.0))
        rv3d.view_distance = 0.55
        space.overlay.show_floor = False
        space.overlay.show_axis_x = False
        space.overlay.show_axis_y = False
        space.shading.type = "SOLID"
        space.shading.light = "FLAT"


def _screenshot() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "effect_line_handle_single.png"
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=8)
    result = bpy.ops.screen.screenshot(
        "EXEC_DEFAULT",
        filepath=str(path),
        check_existing=False,
    )
    if "FINISHED" not in result or not path.is_file():
        raise RuntimeError(f"viewport screenshot failed: {result}")
    return path


def _run_visual_check() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_effect_handle_visual_"))
    mod = None
    restores: list[tuple[object, str, object]] = []
    try:
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "EffectHandleVisual.bmanga"))
        assert "FINISHED" in result, result

        from bmanga_dev_effect_handle_visual.operators import effect_line_op, object_tool_op
        from bmanga_dev_effect_handle_visual.ui import overlay as overlay_ui
        from bmanga_dev_effect_handle_visual.ui import overlay_effect_line
        from bmanga_dev_effect_handle_visual.utils import object_selection
        from bmanga_dev_effect_handle_visual.utils.geom import Rect

        bounds = (-58.0, 120.0, 92.0, 54.0)
        key = object_selection.make_key("effect", "", "visual_effect")
        fake_obj = SimpleNamespace()
        fake_layer = SimpleNamespace(name="visual_effect")

        def patch(target, name: str, value) -> None:
            restores.append((target, name, getattr(target, name)))
            setattr(target, name, value)

        patch(object_selection, "get_keys", lambda _context: [key])
        patch(object_selection, "selected_effect_names", lambda _context: [])
        patch(object_tool_op, "active_selection_key", lambda _context: key)
        patch(object_tool_op, "selection_bounds_for_key", lambda _context, _key: Rect(*bounds))
        patch(overlay_ui, "_free_transform_quad_for_key", lambda *_args: None)
        patch(effect_line_op, "active_effect_layer_bounds", lambda _context: (fake_obj, fake_layer, bounds))
        patch(effect_line_op, "effect_layer_world_bounds", lambda *_args: bounds)
        patch(effect_line_op, "effect_layer_center", lambda *_args: (-12.0, 147.0))
        patch(effect_line_op, "effect_layer_world_point", lambda _context, _obj, point, _layer: point)
        patch(overlay_effect_line, "_draw_shape_guides", lambda *_args, **_kwargs: None)
        bpy.context.scene.bmanga_active_layer_kind = "effect"

        _set_top_view()
        path = _screenshot()
        print(f"BMANGA_EFFECT_LINE_HANDLE_VISUAL_OK {path}", flush=True)
    finally:
        for target, name, value in reversed(restores):
            setattr(target, name, value)
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


def _tick():
    try:
        _run_visual_check()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    finally:
        bpy.ops.wm.quit_blender()
    return None


if __name__ == "__main__":
    bpy.app.timers.register(_tick, first_interval=0.25)
