"""Blender UI実機用: 依頼項目のAI目視証拠を生成する."""

from __future__ import annotations

import importlib.util
import json
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
    os.environ.get("BNAME_REQUESTED_VISUAL_OUT", "")
    or (ROOT / ".codex" / "visual" / "requested_items_visual_audit")
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_requested_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_requested_visual"] = mod
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


def _screenshot(name: str) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=5)
    except Exception:
        pass
    scene = bpy.context.scene
    previous_path = str(getattr(scene.render, "filepath", "") or "")
    scene.render.filepath = str(path)
    with _view3d_override():
        result = bpy.ops.render.opengl("EXEC_DEFAULT", write_still=True, view_context=True)
    scene.render.filepath = previous_path
    if "FINISHED" not in result:
        raise RuntimeError(f"viewport render failed: {result}")
    return str(path)


def _write_preview_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width = 360
    height = 240
    image = bpy.data.images.new(f"BName_VisualPreview_{path.stem}", width=width, height=height, alpha=True)
    pixels = [0.0] * (width * height * 4)
    for y in range(height):
        for x in range(width):
            u = x / max(1, width - 1)
            v = y / max(1, height - 1)
            stripe = 0.25 if (x // 28) % 2 == 0 else 0.0
            off = (y * width + x) * 4
            pixels[off] = 0.95 - 0.55 * v
            pixels[off + 1] = 0.15 + 0.75 * u
            pixels[off + 2] = 0.75 - stripe
            pixels[off + 3] = 1.0
    image.pixels.foreach_set(pixels)
    image.update()
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()


def _dismiss_splash() -> None:
    try:
        bpy.context.preferences.view.show_splash = False
    except Exception:
        pass
    window = getattr(bpy.context, "window", None)
    simulate = getattr(window, "event_simulate", None)
    if simulate is not None:
        try:
            simulate(type="ESC", value="PRESS")
            simulate(type="ESC", value="RELEASE")
            _window, _screen, _area, region, _rv3d = _view3d_context()
            click_x = int(region.x) + 24
            click_y = int(region.y) + 24
            simulate(type="LEFTMOUSE", value="PRESS", x=click_x, y=click_y)
            simulate(type="LEFTMOUSE", value="RELEASE", x=click_x, y=click_y)
        except Exception:
            pass
    if os.name == "nt":
        try:
            import ctypes

            vk_escape = 0x1B
            keyeventf_keyup = 0x0002
            ctypes.windll.user32.keybd_event(vk_escape, 0, 0, 0)
            ctypes.windll.user32.keybd_event(vk_escape, 0, keyeventf_keyup, 0)
        except Exception:
            pass
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=4)
    except Exception:
        pass


def _set_top_view() -> None:
    from bname_dev_requested_visual.utils.geom import mm_to_m

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
        space.shading.type = "SOLID"
        space.shading.light = "FLAT"


def _active_stack_item(context, kind: str, key: str):
    from bname_dev_requested_visual.utils import layer_stack as layer_stack_utils

    uid = layer_stack_utils.target_uid(kind, key)
    for item in context.scene.bname_layer_stack:
        if layer_stack_utils.stack_item_uid(item) == uid:
            return item
    raise AssertionError(f"stack item not found: {uid}")


def _configure_scene(temp_root: Path):
    mod = _load_addon()
    result = bpy.ops.bname.work_new(filepath=str(temp_root / "RequestedVisual.bname"))
    assert "FINISHED" in result, result
    for _ in range(3):
        assert "FINISHED" in bpy.ops.bname.page_add("EXEC_DEFAULT")

    from bname_dev_requested_visual.io import border_presets
    from bname_dev_requested_visual.operators import coma_op
    from bname_dev_requested_visual.utils import (
        balloon_curve_object,
        coma_border_object,
        layer_object_sync,
        layer_hierarchy,
        layer_reparent,
        layer_stack,
        paper_guide_object,
        page_grid,
        paths,
    )

    context = bpy.context
    scene = context.scene
    work = scene.bname_work
    work.paper.canvas_width_mm = 210.0
    work.paper.canvas_height_mm = 297.0
    work.paper.start_side = "right"
    work.paper.read_direction = "left"

    line_none = border_presets.load_preset_by_name("線無し", None)
    blur = border_presets.load_preset_by_name("輪郭ぼかし", None)
    assert line_none is not None
    assert blur is not None

    for idx, page in enumerate(work.pages):
        if len(page.comas) == 0:
            coma_op.create_rect_coma(work, page, Path(work.work_dir), 30.0, 55.0, 150.0, 185.0)
        coma = page.comas[0]
        coma.shape_type = "rect"
        coma.rect_x_mm = 30.0
        coma.rect_y_mm = 55.0
        coma.rect_width_mm = 150.0
        coma.rect_height_mm = 185.0
        coma.title = ["線無し", "ボカシ", "ディザ", "フキダシ"][idx]

    border_presets.apply_preset_to_coma(line_none, work.pages[0].comas[0])
    border_presets.apply_preset_to_coma(blur, work.pages[1].comas[0])
    work.pages[1].comas[0].border.blur_amount = 1.0
    _write_preview_image(
        paths.coma_thumb_path(Path(work.work_dir), work.pages[1].id, work.pages[1].comas[0].coma_id)
    )
    border_presets.apply_preset_to_coma(blur, work.pages[2].comas[0])
    work.pages[2].comas[0].border.blur_amount = 1.0
    work.pages[2].comas[0].border.blur_dither = True
    _write_preview_image(
        paths.coma_thumb_path(Path(work.work_dir), work.pages[2].id, work.pages[2].comas[0].coma_id)
    )

    page = work.pages[3]
    page_key = layer_hierarchy.page_stack_key(page)
    for shape, bid, x_mm, y_mm in (
        ("cloud", "visual_cloud", 42.0, 88.0),
        ("thorn-curve", "visual_thorn_curve", 100.0, 145.0),
    ):
        entry = page.balloons.add()
        entry.id = bid
        entry.shape = shape
        entry.x_mm = x_mm
        entry.y_mm = y_mm
        entry.width_mm = 72.0
        entry.height_mm = 48.0
        entry.parent_kind = "page"
        entry.parent_key = page_key
        balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)

    text = work.pages[0].texts.add()
    text.id = "visual_outside_text"
    text.body = "ページ外"
    text.x_mm = 68.0
    text.y_mm = 28.0
    text.width_mm = 34.0
    text.height_mm = 14.0
    text.parent_kind = "page"
    text.parent_key = layer_hierarchy.page_stack_key(work.pages[0])
    layer_stack.sync_layer_stack(context, preserve_active_index=True)
    item = _active_stack_item(context, "text", f"{text.parent_key}:{text.id}")
    assert layer_reparent.reparent_stack_item(
        context,
        item,
        target=layer_reparent.ClickTarget("outside", None, None, -1, (335.0, 80.0), None),
        new_world_xy_mm=(335.0, 80.0),
    )
    assert len(work.shared_texts) == 1
    shared = work.shared_texts[0]
    assert abs(float(shared.x_mm) + float(shared.width_mm) * 0.5 - 335.0) < 0.05
    assert abs(float(shared.y_mm) + float(shared.height_mm) * 0.5 - 80.0) < 0.05

    layer_object_sync.mirror_work_to_outliner(scene, work)
    coma_border_object.regenerate_all_coma_borders(scene, work)
    paper_guide_object.regenerate_all_paper_guides(scene, work)
    paper_guide_object.apply_view_constant_thickness()
    page_grid.apply_page_collection_transforms(context, work)
    layer_stack.sync_layer_stack(context, preserve_active_index=True)
    return mod, work


def _assert_requested_state(work) -> dict[str, object]:
    from bname_dev_requested_visual.utils import balloon_shapes, coma_border_object, paper_guide_object
    from bname_dev_requested_visual.utils.geom import Rect

    scene = bpy.context.scene
    line_none = work.pages[0].comas[0]
    line_none_owner = f"{work.pages[0].id}:{line_none.coma_id}"
    none_objects = [
        obj for obj in bpy.data.objects
        if obj.get(coma_border_object.PROP_COMA_BORDER_KIND) == "coma_border"
        and str(obj.get(coma_border_object.PROP_COMA_BORDER_OWNER_ID, "") or "") == line_none_owner
        and not obj.hide_viewport
    ]
    if none_objects:
        raise AssertionError(f"線無しの枠線が表示されています: {[obj.name for obj in none_objects]}")

    dither_mats = [
        mat for mat in bpy.data.materials
        if mat.name.startswith("BName_ComaPlane_")
        and getattr(mat, "surface_render_method", "") == "DITHERED"
    ]
    if not dither_mats:
        raise AssertionError("ディザ化したコマ面のボカシ素材が見つかりません")

    brush_border_objects = [
        obj for obj in bpy.data.objects
        if obj.name.startswith("coma_border_")
        and not bool(getattr(obj, "hide_viewport", False))
    ]
    for obj in brush_border_objects:
        owner = str(obj.get(coma_border_object.PROP_COMA_BORDER_OWNER_ID, "") or "")
        if owner in {f"{work.pages[1].id}:{work.pages[1].comas[0].id}", f"{work.pages[2].id}:{work.pages[2].comas[0].id}"}:
            raise AssertionError(f"輪郭ぼかしが別体の枠線オブジェクトとして残っています: {obj.name}")

    page = work.pages[3]
    cloud = page.balloons[0]
    thorn = page.balloons[1]
    cloud_pts, cloud_corners = balloon_shapes.outline_with_corners_for_entry(
        cloud, Rect(cloud.x_mm, cloud.y_mm, cloud.width_mm, cloud.height_mm)
    )
    thorn_pts, thorn_corners = balloon_shapes.outline_with_corners_for_entry(
        thorn, Rect(thorn.x_mm, thorn.y_mm, thorn.width_mm, thorn.height_mm)
    )
    if len(cloud_pts) < 48 or len(thorn_pts) < 48:
        raise AssertionError("雲/トゲ（曲線）の曲線点が不足しています")
    if cloud_corners or thorn_corners:
        raise AssertionError("雲/トゲ（曲線）に角張る点が残っています")

    guide_objects = [
        obj for obj in bpy.data.objects
        if obj.get(paper_guide_object.PROP_GUIDE_OWNER_ID)
        and str(obj.get(paper_guide_object.PROP_GUIDE_KIND, "") or "") == paper_guide_object.GUIDE_KIND_LINES
    ]
    if not guide_objects or any(obj.type != "GREASEPENCIL" for obj in guide_objects):
        raise AssertionError("実体ガイドがページごとのGrease Pencilになっていません")
    guide_radii = []
    for obj in guide_objects:
        for stroke in paper_guide_object._guide_strokes(obj):
            for point in getattr(stroke, "points", []) or []:
                guide_radii.append(float(getattr(point, "radius", 0.0) or 0.0))
    if not guide_radii or min(guide_radii) <= 0.0:
        raise AssertionError("実体ガイドの線に一定太さが設定されていません")

    window, screen, area, region, rv3d = _view3d_context()
    camera = scene.camera
    camera_data = getattr(camera, "data", None) if camera is not None else None
    depsgraph = bpy.context.evaluated_depsgraph_get()
    object_bounds = [None, None, None, None]
    bound_sources = []
    for obj in bpy.data.objects:
        if obj.hide_get() or bool(getattr(obj, "hide_viewport", False)):
            continue
        if getattr(obj, "type", "") == "CAMERA":
            continue
        name = str(getattr(obj, "name", "") or "")
        if not (
            name.startswith(("page_", "coma_", "B-Name"))
            or bool(obj.get("bname_paper_guide_page_id"))
            or bool(obj.get("bname_coma_border_owner_id"))
        ):
            continue
        try:
            eval_obj = obj.evaluated_get(depsgraph)
            corners = [eval_obj.matrix_world @ Vector(corner) for corner in eval_obj.bound_box]
        except Exception:
            continue
        local_bounds = [None, None, None, None]
        for corner in corners:
            x = float(corner.x)
            y = float(corner.y)
            local_bounds[0] = x if local_bounds[0] is None else min(local_bounds[0], x)
            local_bounds[1] = y if local_bounds[1] is None else min(local_bounds[1], y)
            local_bounds[2] = x if local_bounds[2] is None else max(local_bounds[2], x)
            local_bounds[3] = y if local_bounds[3] is None else max(local_bounds[3], y)
            object_bounds[0] = x if object_bounds[0] is None else min(object_bounds[0], x)
            object_bounds[1] = y if object_bounds[1] is None else min(object_bounds[1], y)
            object_bounds[2] = x if object_bounds[2] is None else max(object_bounds[2], x)
            object_bounds[3] = y if object_bounds[3] is None else max(object_bounds[3], y)
        if local_bounds[0] is not None:
            bound_sources.append((name, getattr(obj, "type", ""), local_bounds))
    camera_state = {
        "name": getattr(camera, "name", None),
        "type": getattr(camera, "type", None),
        "ortho_scale": float(getattr(camera_data, "ortho_scale", 0.0) or 0.0),
        "location": [
            float(getattr(getattr(camera, "location", None), axis, 0.0) or 0.0)
            for axis in ("x", "y", "z")
        ] if camera is not None else None,
        "view": str(getattr(rv3d, "view_perspective", "")),
        "view_camera_zoom": float(getattr(rv3d, "view_camera_zoom", 0.0) or 0.0),
        "view_camera_offset": [
            float(value)
            for value in (getattr(rv3d, "view_camera_offset", (0.0, 0.0)) or (0.0, 0.0))
        ],
        "region_size": [int(getattr(region, "width", 0)), int(getattr(region, "height", 0))],
        "object_bounds": object_bounds,
        "bound_sources": sorted(
            bound_sources,
            key=lambda item: (item[2][2] - item[2][0]) * (item[2][3] - item[2][1]),
            reverse=True,
        )[:12],
    }

    return {
        "line_none_visible_borders": len(none_objects),
        "dither_materials": len(dither_mats),
        "cloud_corners": len(cloud_corners),
        "thorn_curve_corners": len(thorn_corners),
        "guide_grease_pencils": len(guide_objects),
        "overview_mode": bool(scene.bname_overview_mode),
        "coma_border_values": [
            {
                "page": i,
                "style": str(getattr(getattr(page.comas[0], "border", None), "style", "")) if len(page.comas) else "",
                "width": float(getattr(getattr(page.comas[0], "border", None), "width_mm", 0.0) or 0.0) if len(page.comas) else 0.0,
                "blur": float(getattr(getattr(page.comas[0], "border", None), "blur_amount", 0.0) or 0.0) if len(page.comas) else 0.0,
            }
            for i, page in enumerate(work.pages)
        ],
        "camera": camera_state,
    }


def _make_contact_sheet(paths: list[str], summary: dict[str, object]) -> str:
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default()
    thumbs = []
    for path in paths:
        image = Image.open(path).convert("RGB")
        image.thumbnail((760, 430))
        thumbs.append((path, image.copy()))
    width = 1580
    height = 110 + len(thumbs) * 480
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    draw.text((24, 22), "B-Name requested items visual audit", fill=(0, 0, 0), font=font)
    draw.text((24, 50), json.dumps(summary, ensure_ascii=False), fill=(0, 0, 0), font=font)
    y = 90
    for path, image in thumbs:
        draw.text((24, y), Path(path).name, fill=(0, 0, 0), font=font)
        sheet.paste(image, (24, y + 24))
        y += 480
    out = OUT_DIR / "bname_requested_items_contact.png"
    sheet.save(out)
    return str(out)


def _run_visual_audit() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="bname_requested_visual_work_"))
    mod = None
    try:
        mod, work = _configure_scene(temp_root)
        _set_top_view()
        _dismiss_splash()
        shots: list[str] = []
        with _view3d_override():
            bpy.ops.bname.view_fit_all("EXEC_DEFAULT")
            if bpy.context.space_data.shading.type != "MATERIAL":
                raise AssertionError("輪郭ぼかし使用時にマテリアルプレビューへ切り替わっていません")
        shots.append(_screenshot("01_all_pages_fit.png"))

        with _view3d_override():
            work.active_page_index = 1
            bpy.context.scene.bname_overview_mode = False
            bpy.ops.bname.view_fit_page("EXEC_DEFAULT")
            if bpy.context.space_data.shading.type != "MATERIAL":
                raise AssertionError("ページに合わせる後にマテリアルプレビューへ切り替わっていません")
            rv3d = bpy.context.space_data.region_3d
            rv3d.view_distance = max(0.01, float(rv3d.view_distance) * 0.36)
        shots.append(_screenshot("02_blur_brush_zoom.png"))

        with _view3d_override():
            work.active_page_index = 3
            bpy.context.scene.bname_overview_mode = False
            bpy.ops.bname.view_fit_page("EXEC_DEFAULT")
            rv3d = bpy.context.space_data.region_3d
            rv3d.view_distance = max(0.01, float(rv3d.view_distance) * 0.45)
        shots.append(_screenshot("03_balloon_shapes_zoom.png"))

        with _view3d_override():
            bpy.ops.view3d.view_camera("EXEC_DEFAULT")
            from bname_dev_requested_visual.utils import camera_overview_sync

            camera_overview_sync._apply()
        shots.append(_screenshot("04_camera_switch_overview.png"))

        summary = _assert_requested_state(work)
        contact = _make_contact_sheet(shots, summary)
        result = {
            "contact_sheet": contact,
            "screenshots": shots,
            "summary": summary,
        }
        result_path = OUT_DIR / "bname_requested_items_visual_audit.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"BNAME_REQUESTED_ITEMS_VISUAL_OK visual={contact}", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


def _has_view3d_context() -> bool:
    try:
        _view3d_context()
        return True
    except Exception:
        return False


def _tick():
    if not _has_view3d_context():
        return 0.25
    try:
        _run_visual_audit()
        sys.stdout.flush()
        os._exit(0)
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)
    return None


def main() -> None:
    bpy.app.timers.register(_tick, first_interval=0.25)


if __name__ == "__main__":
    main()
