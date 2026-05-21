"""Blender UI実機用: 指定された既存 work.blend を開いて目視証拠を出す."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector


ROOT = Path(__file__).resolve().parents[1]
BLEND_PATH = Path(
    os.environ.get("BNAME_REAL_WORK_BLEND", "")
    or r"D:\TM Dropbox\Miura Tadahiro\Develop\B-Nameテスト\test05.bname\work.blend"
)
OUT_DIR = Path(
    os.environ.get("BNAME_REAL_WORK_VISUAL_OUT", "")
    or (ROOT / ".codex" / "visual" / "real_work_visual_audit")
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_real_work_audit",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_real_work_audit"] = mod
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
    raise RuntimeError("VIEW_3D が見つかりません")


def _view3d_override():
    window, screen, area, region, _rv3d = _view3d_context()
    return bpy.context.temp_override(window=window, screen=screen, area=area, region=region)


def _redraw(iterations: int = 5) -> None:
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=iterations)
    except Exception:
        pass


def _screenshot(name: str) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    _redraw(6)
    scene = bpy.context.scene
    previous_path = str(getattr(scene.render, "filepath", "") or "")
    scene.render.filepath = str(path)
    with _view3d_override():
        result = bpy.ops.render.opengl("EXEC_DEFAULT", write_still=True, view_context=True)
    scene.render.filepath = previous_path
    if "FINISHED" not in result:
        raise RuntimeError(f"viewport render failed: {result}")
    return str(path)


def _set_top_view() -> None:
    from bname_real_work_audit.utils.geom import mm_to_m

    with _view3d_override():
        bpy.ops.view3d.view_axis(type="TOP", align_active=False)
        space = bpy.context.space_data
        rv3d = space.region_3d
        rv3d.view_perspective = "ORTHO"
        rv3d.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
        rv3d.view_location = Vector((mm_to_m(105.0), mm_to_m(148.5), 0.0))
        space.overlay.show_floor = False
        space.overlay.show_axis_x = False
        space.overlay.show_axis_y = False
        space.overlay.show_object_origins = False


def _fit_all() -> None:
    cols = os.environ.get("BNAME_REAL_WORK_COLS", "").strip()
    if cols:
        bpy.context.scene.bname_overview_cols = int(cols)
    with _view3d_override():
        result = bpy.ops.bname.view_fit_all()
    if "FINISHED" not in result:
        raise AssertionError(f"全ページを一覧に失敗: {result}")
    _redraw(8)


def _open_target_blend(mod) -> None:
    if not BLEND_PATH.is_file():
        raise FileNotFoundError(str(BLEND_PATH))
    result = bpy.ops.wm.open_mainfile(filepath=str(BLEND_PATH))
    if "FINISHED" not in result:
        raise RuntimeError(f"open_mainfile failed: {result}")
    # load_post と同じ同期を明示的にもう一度走らせ、テスト環境差を潰す。
    try:
        mod.utils.handlers._bname_on_load_post(str(BLEND_PATH))
    except Exception:
        traceback.print_exc()
    _redraw(8)


def _material_method(mat) -> str:
    for attr in ("surface_render_method", "blend_method"):
        try:
            return str(getattr(mat, attr))
        except Exception:
            continue
    return ""


def _collect_report() -> dict:
    from bname_real_work_audit.core.mode import MODE_PAGE, get_mode
    from bname_real_work_audit.core.work import get_work
    from bname_real_work_audit.io import border_presets
    from bname_real_work_audit.utils import paper_guide_object
    from bname_real_work_audit.utils import coma_border_object

    scene = bpy.context.scene
    work = get_work(bpy.context)
    if work is None or not getattr(work, "loaded", False):
        raise AssertionError("B-Name作品として読み込めていません")
    work_dir = Path(str(getattr(work, "work_dir", "") or BLEND_PATH.parent))
    guide_objs = [
        obj
        for obj in bpy.data.objects
        if str(obj.get(paper_guide_object.PROP_GUIDE_OWNER_ID, "") or "")
    ]
    guide_curve_radii = [
        float(getattr(getattr(obj, "data", None), "bevel_depth", 0.0) or 0.0)
        for obj in guide_objs
        if str(obj.get(paper_guide_object.PROP_GUIDE_KIND, "") or "") == paper_guide_object.GUIDE_KIND_LINES
        and obj.type == "CURVE"
        and len(getattr(getattr(obj, "data", None), "splines", []) or []) > 0
    ]
    border_objs = [
        obj
        for obj in bpy.data.objects
        if obj.get(coma_border_object.PROP_COMA_BORDER_KIND) == "coma_border"
    ]
    brush_objs = [obj for obj in border_objs if "brush" in str(obj.name).lower() or len(border_objs) > 1]
    dither_mats = [
        mat.name
        for mat in bpy.data.materials
        if _material_method(mat) == "DITHERED"
    ]
    line_none = border_presets.load_preset_by_name("線無し", work_dir)
    line_none_data = line_none.data if line_none is not None else {}
    line_none_border = line_none_data.get("border", {}) if isinstance(line_none_data, dict) else {}
    cloud_entries = 0
    thorn_curve_entries = 0
    for page in getattr(work, "pages", []) or []:
        for entry in getattr(page, "balloons", []) or []:
            shape = str(getattr(entry, "shape_type", "") or "")
            if shape == "cloud":
                cloud_entries += 1
            if shape == "thorn-curve":
                thorn_curve_entries += 1
    return {
        "blend_path": str(BLEND_PATH),
        "mode": get_mode(bpy.context),
        "is_page_mode": get_mode(bpy.context) == MODE_PAGE,
        "work_loaded": bool(getattr(work, "loaded", False)),
        "work_dir": str(work_dir),
        "page_count": len(getattr(work, "pages", []) or []),
        "overview_mode": bool(getattr(scene, "bname_overview_mode", False)),
        "bname_overlay_enabled": bool(getattr(scene, "bname_overlay_enabled", True)),
        "guide_object_count": len(guide_objs),
        "guide_curve_radii": guide_curve_radii[:12],
        "visible_guide_count": sum(1 for obj in guide_objs if not bool(getattr(obj, "hide_viewport", False))),
        "border_object_count": len(border_objs),
        "brush_like_object_count": len(brush_objs),
        "dither_materials": dither_mats,
        "line_none_preset_exists": line_none is not None,
        "line_none_visible": line_none_border.get("visible"),
        "line_none_width": line_none_border.get("widthMm"),
        "cloud_entries": cloud_entries,
        "thorn_curve_entries": thorn_curve_entries,
    }


def _camera_switch_report() -> dict:
    scene = bpy.context.scene
    with _view3d_override():
        bpy.ops.view3d.view_camera()
    _redraw(12)
    for _i in range(8):
        try:
            bpy.app.timers.register(lambda: None, first_interval=0.0)
        except Exception:
            pass
        _redraw(2)
    _window, _screen, _area, _region, rv3d = _view3d_context()
    return {
        "overview_mode_after_camera": bool(getattr(scene, "bname_overview_mode", False)),
        "view_perspective_after_camera": str(getattr(rv3d, "view_perspective", "")),
    }


def main() -> None:
    mod = _load_addon()
    _open_target_blend(mod)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _set_top_view()
    current = _screenshot("01_current_top_view.png")
    _fit_all()
    fit_all = _screenshot("02_fit_all_pages.png")
    camera = _camera_switch_report()
    camera_view = _screenshot("03_after_camera_switch.png")
    report = _collect_report()
    report["camera"] = camera
    report["screenshots"] = {
        "current_top_view": current,
        "fit_all_pages": fit_all,
        "after_camera_switch": camera_view,
    }
    report_path = OUT_DIR / "real_work_visual_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BNAME_REAL_WORK_VISUAL_AUDIT_OK")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
