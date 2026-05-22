"""Create a viewport evidence image for selected effect-line shape guides."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(
    os.environ.get("BNAME_EFFECT_SHAPE_VISUAL_OUT", "")
    or (ROOT / ".codex" / "visual" / "effect_line_shape_overlay")
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_effect_shape_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_effect_shape_visual"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _view3d_context():
    for window in bpy.context.window_manager.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for region in area.regions:
                if region.type == "WINDOW":
                    return window, screen, area, region, area.spaces.active.region_3d
    raise RuntimeError("VIEW_3D not found")


def _view3d_override():
    window, screen, area, region, _rv3d = _view3d_context()
    return bpy.context.temp_override(window=window, screen=screen, area=area, region=region)


def _set_top_view() -> None:
    from bname_dev_effect_shape_visual.utils.geom import mm_to_m

    with _view3d_override():
        bpy.ops.view3d.view_axis(type="TOP", align_active=False)
        space = bpy.context.space_data
        rv3d = space.region_3d
        rv3d.view_perspective = "ORTHO"
        rv3d.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
        rv3d.view_location = Vector((mm_to_m(128.0), mm_to_m(170.0), 0.0))
        rv3d.view_distance = 0.7
        space.overlay.show_floor = False
        space.overlay.show_axis_x = False
        space.overlay.show_axis_y = False
        space.overlay.show_overlays = True
        space.shading.type = "SOLID"
        space.shading.light = "FLAT"


def _screenshot(name: str) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=8)
    except Exception:
        pass
    result = bpy.ops.screen.screenshot("EXEC_DEFAULT", filepath=str(path), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"screenshot failed: {result}")
    return str(path)


def _bbox_from_segments(segments):
    points = [point for segment in segments for point in segment]
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _assert_close(label: str, actual: float, expected: float, eps: float = 0.8) -> None:
    if abs(float(actual) - float(expected)) > eps:
        raise AssertionError(f"{label}: expected {expected:.3f}, got {actual:.3f}")


def _setup_scene(temp_root: Path):
    mod = _load_addon()
    result = bpy.ops.bname.work_new(filepath=str(temp_root / "EffectShapeVisual.bname"))
    assert "FINISHED" in result, result

    from bname_dev_effect_shape_visual.core.work import get_work
    from bname_dev_effect_shape_visual.operators import effect_line_op
    from bname_dev_effect_shape_visual.utils import object_selection
    from bname_dev_effect_shape_visual.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = get_work(context)
    assert work is not None and work.loaded
    page = work.pages[0]
    params = context.scene.bname_effect_line_params
    params.effect_type = "focus"
    params.start_to_coma_frame = False
    params.start_shape = "ellipse"
    params.end_shape = "ellipse"
    params.spacing_mode = "angle"
    params.spacing_angle_deg = 5.0
    params.max_line_count = 300
    params.brush_size_mm = 0.3
    params.fill_base_shape = False
    params.opacity = 1.0

    bounds = (72.0, 120.0, 92.0, 54.0)
    obj, layer = effect_line_op._create_effect_layer(context, bounds, parent_key=page_stack_key(page))
    assert obj is not None and layer is not None
    effect_line_op._write_effect_strokes(context, obj, layer, bounds, seed=9, params_override=params)
    effect_line_op._select_effect_layer(context, obj, layer)
    object_selection.select_key(context, object_selection.effect_key(layer), mode="single")
    return mod, obj, layer, bounds


def _assert_overlay_guides(obj, layer, bounds) -> None:
    from bname_dev_effect_shape_visual.operators import effect_line_op
    from bname_dev_effect_shape_visual.ui import overlay_effect_line

    context = bpy.context
    guides = []

    def _fill(_rect, _color):
        return None

    def _outline(_rect, *_args, **_kwargs):
        return None

    def _segments(segments, color, width_mm):
        guides.append((segments, color, width_mm))

    overlay_effect_line.draw_active_effect_line_bounds(
        context,
        draw_rect_fill=_fill,
        draw_rect_outline=_outline,
        draw_segments_mm=_segments,
    )
    if len(guides) < 2:
        raise AssertionError("selected effect line did not draw start/end shape guides")
    world_bounds = effect_line_op.effect_layer_world_bounds(context, obj, layer, bounds)
    assert world_bounds is not None
    end_bbox = _bbox_from_segments(guides[-1][0])
    _assert_close("end guide left", end_bbox[0], world_bounds[0])
    _assert_close("end guide bottom", end_bbox[1], world_bounds[1])
    _assert_close("end guide right", end_bbox[2], world_bounds[0] + world_bounds[2])
    _assert_close("end guide top", end_bbox[3], world_bounds[1] + world_bounds[3])


def _run_visual_check() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_effect_shape_visual_"))
    mod = None
    try:
        try:
            bpy.context.preferences.view.show_splash = False
        except Exception:
            pass
        mod, obj, layer, bounds = _setup_scene(temp_root)
        _assert_overlay_guides(obj, layer, bounds)
        _set_top_view()
        path = _screenshot("effect_line_shape_overlay.png")
        print(f"BNAME_EFFECT_LINE_SHAPE_OVERLAY_VISUAL_OK {path}", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


def _visual_check_tick():
    try:
        _run_visual_check()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    finally:
        bpy.ops.wm.quit_blender()
    return None


if __name__ == "__main__":
    bpy.app.timers.register(_visual_check_tick, first_interval=0.25)
