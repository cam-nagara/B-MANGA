"""Blender実機用: B-MANGA本体の部分対応項目を決定的に検証する.

Computer Use なしで GUI を直接クリックできない環境向けに、作画仕様の
状態変化・描画順・ページ別表示対象を Blender API と診断画像で確認する。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(
    os.environ.get("BMANGA_PARTIAL_VISUAL_OUT", "")
    or tempfile.mkdtemp(prefix="bmanga_partial_visual_")
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _inside_panel_point(panel) -> tuple[float, float]:
    return (
        float(panel.rect_x_mm) + float(panel.rect_width_mm) * 0.5,
        float(panel.rect_y_mm) + float(panel.rect_height_mm) * 0.5,
    )


def _outside_panel_point(work, page) -> tuple[float, float]:
    from bmanga_dev.utils import layer_hierarchy

    width = float(getattr(work.paper, "canvas_width_mm", 210.0) or 210.0)
    height = float(getattr(work.paper, "canvas_height_mm", 297.0) or 297.0)
    for x_mm, y_mm in (
        (width * 0.08, height * 0.08),
        (width * 0.92, height * 0.08),
        (width * 0.08, height * 0.92),
        (width * 0.92, height * 0.92),
    ):
        if layer_hierarchy.coma_containing_point(page, x_mm, y_mm) is None:
            return x_mm, y_mm
    raise AssertionError("page-local point outside panels was not found")


def _stack(context):
    from bmanga_dev.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    assert stack is not None
    return stack


def _stack_item(context, kind: str, key: str):
    from bmanga_dev.utils import layer_stack as layer_stack_utils

    uid = layer_stack_utils.target_uid(kind, key)
    for index, item in enumerate(_stack(context)):
        if layer_stack_utils.stack_item_uid(item) == uid:
            return index, item
    raise AssertionError(f"stack item not found: {uid}")


def _visible_stack_uids(context) -> list[str]:
    from bmanga_dev.panels import gpencil_panel
    from bmanga_dev.utils import layer_stack as layer_stack_utils

    stack = _stack(context)
    fake_ui = SimpleNamespace(bitflag_filter_item=1)
    flags, _order = gpencil_panel.BMANGA_UL_layer_stack.filter_items(
        fake_ui,
        context,
        context.scene,
        "bmanga_layer_stack",
    )
    return [
        layer_stack_utils.stack_item_uid(item)
        for item, flag in zip(stack, flags, strict=False)
        if flag
    ]


def _add_gp_layer(context, parent_key: str):
    from bmanga_dev.utils import gp_layer_parenting as gp_parent
    from bmanga_dev.utils import gpencil as gp_utils
    from bmanga_dev.utils.geom import mm_to_m

    obj = gp_utils.ensure_master_gpencil(context.scene)
    layer = obj.data.layers.new("partial_gp")
    gp_parent.set_parent_key(layer, parent_key)
    frame = gp_utils.ensure_active_frame(layer)
    assert frame is not None and getattr(frame, "drawing", None) is not None
    assert gp_utils.add_stroke_to_drawing(
        frame.drawing,
        [
            (mm_to_m(20.0), mm_to_m(20.0), 0.0),
            (mm_to_m(35.0), mm_to_m(25.0), 0.0),
        ],
    )
    return obj, layer


def _assert_text_collection(context) -> None:
    from bmanga_dev.utils import object_naming as on
    from bmanga_dev.utils import outliner_model

    text_coll = outliner_model.ensure_text_collection(context.scene)
    root_coll = outliner_model.ensure_root_collection(context.scene)
    assert text_coll.name == "text"
    assert len(root_coll.children) > 0 and root_coll.children[0] == text_coll
    text_objects = [
        obj for obj in bpy.data.objects
        if str(obj.get(on.PROP_KIND, "") or "") == "text"
    ]
    assert text_objects, "text objects were not mirrored"
    for obj in text_objects:
        if list(obj.users_collection) != [text_coll]:
            raise AssertionError(f"text object is outside text collection: {obj.name}")


def _force_full_outliner_mirror(scene, work) -> None:
    """作品一覧軽量化をテスト内だけ外し、実体同期そのものを検証する."""
    from bmanga_dev.utils import layer_object_sync
    from bmanga_dev.utils import page_file_scene

    original = page_file_scene.is_work_list_scene
    page_file_scene.is_work_list_scene = lambda _scene=None: False
    try:
        layer_object_sync.mirror_work_to_outliner(scene, work)
    finally:
        page_file_scene.is_work_list_scene = original


def _assert_paper_guides_use_real_objects(context, work, page) -> list[str]:
    from bmanga_dev.core.mode import MODE_PAGE
    from bmanga_dev.ui import overlay
    from bmanga_dev.ui import overlay_text
    from bmanga_dev.ui import overlay_image
    from bmanga_dev.utils import page_file_scene
    from bmanga_dev.utils import paper_bg_object
    from bmanga_dev.utils import paper_guide_object

    calls: list[str] = []
    original = {
        "scene_current_page_id": str(getattr(context.scene, "bmanga_current_page_id", "") or ""),
        "current_coma_id": str(getattr(context.scene, "bmanga_current_coma_id", "") or ""),
        "current_coma_page_id": str(getattr(context.scene, "bmanga_current_coma_page_id", "") or ""),
        "overview_mode": bool(getattr(context.scene, "bmanga_overview_mode", False)),
        "active_layer_kind": str(getattr(context.scene, "bmanga_active_layer_kind", "") or ""),
        "active_page_index": int(getattr(work, "active_page_index", -1)),
        "page_guides_visible": bool(getattr(context.scene, "bmanga_page_guides_visible", True)),
        "is_page_edit_scene": page_file_scene.is_page_edit_scene,
        "current_page_id_func": page_file_scene.current_page_id,
        "current_role_func": page_file_scene.current_role,
        "paper_bg": overlay.overlay_paper_bg.draw_for_page,
        "paper_guide": overlay.overlay_paper_guide.draw_for_page,
        "page_preview": overlay.overlay_page_preview.draw_for_page,
        "draw_rect_outline": overlay._draw_rect_outline,
        "draw_trim_marks": overlay._draw_trim_marks,
        "draw_frame_with_hole": overlay._draw_frame_with_hole,
        "draw_comas": overlay._draw_comas,
        "draw_text_guides": overlay_text.draw_text_guides,
        "draw_image_layers": overlay_image.draw_image_layers,
        "draw_shared_layers": overlay._draw_shared_layers,
        "depth_test_set": overlay.gpu.state.depth_test_set,
        "blend_set": overlay.gpu.state.blend_set,
    }

    def mark(name):
        def _inner(*_args, **_kwargs):
            calls.append(name)
        return _inner

    def depth(value):
        calls.append(f"depth:{value}")

    def safe_overlay(_outer, _inner, color):
        calls.append("safe_overlay")

    def draw_comas(*_args, **kwargs):
        calls.append(f"coma_borders:{bool(kwargs.get('draw_borders', True))}")

    page_id = str(getattr(page, "id", "") or "")
    try:
        page_file_scene.is_page_edit_scene = lambda _scene=None: True
        page_file_scene.current_page_id = lambda _scene=None: page_id
        page_file_scene.current_role = lambda _context=None: (page_file_scene.ROLE_PAGE, page_id, "")
        if not page_file_scene.set_page_edit_state(context, page_id):
            raise AssertionError(f"page edit state could not be set: {page_id}")
        page_file_scene.resync_page_runtime_objects(context.scene, work, page_id)
        context.scene.bmanga_page_guides_visible = True
        paper_guide_object.regenerate_all_paper_guides(context.scene, page_file_scene.work_for_pages(work, {page_id}))
        paper_guide_object.apply_view_constant_thickness()

        overlay.overlay_paper_bg.draw_for_page = mark("paper_bg_overlay")
        overlay.overlay_paper_guide.draw_for_page = mark("paper_guide_overlay")
        overlay.overlay_page_preview.draw_for_page = mark("page_preview_overlay")
        overlay._draw_rect_outline = mark("paper")
        overlay._draw_trim_marks = mark("paper")
        overlay._draw_frame_with_hole = safe_overlay
        overlay._draw_comas = draw_comas
        overlay_text.draw_text_guides = mark("text")
        overlay_image.draw_image_layers = mark("image")
        overlay._draw_shared_layers = mark("shared")
        overlay.gpu.state.depth_test_set = depth
        overlay.gpu.state.blend_set = mark("blend")
        overlay._draw_page_overlay(
            context,
            work,
            work.paper,
            overlay.overlay_shared.compute_paper_rects(work.paper),
            page,
            MODE_PAGE,
            draw_image_layers=True,
        )
    finally:
        context.scene.bmanga_current_page_id = original["scene_current_page_id"]
        context.scene.bmanga_current_coma_id = original["current_coma_id"]
        context.scene.bmanga_current_coma_page_id = original["current_coma_page_id"]
        context.scene.bmanga_overview_mode = original["overview_mode"]
        context.scene.bmanga_active_layer_kind = original["active_layer_kind"]
        context.scene.bmanga_page_guides_visible = original["page_guides_visible"]
        work.active_page_index = original["active_page_index"]
        page_file_scene.is_page_edit_scene = original["is_page_edit_scene"]
        page_file_scene.current_page_id = original["current_page_id_func"]
        page_file_scene.current_role = original["current_role_func"]
        overlay.overlay_paper_bg.draw_for_page = original["paper_bg"]
        overlay.overlay_paper_guide.draw_for_page = original["paper_guide"]
        overlay.overlay_page_preview.draw_for_page = original["page_preview"]
        overlay._draw_rect_outline = original["draw_rect_outline"]
        overlay._draw_trim_marks = original["draw_trim_marks"]
        overlay._draw_frame_with_hole = original["draw_frame_with_hole"]
        overlay._draw_comas = original["draw_comas"]
        overlay_text.draw_text_guides = original["draw_text_guides"]
        overlay_image.draw_image_layers = original["draw_image_layers"]
        overlay._draw_shared_layers = original["draw_shared_layers"]
        overlay.gpu.state.depth_test_set = original["depth_test_set"]
        overlay.gpu.state.blend_set = original["blend_set"]

    page_file_scene.is_page_edit_scene = lambda _scene=None: True
    page_file_scene.current_page_id = lambda _scene=None: page_id
    page_file_scene.current_role = lambda _context=None: (page_file_scene.ROLE_PAGE, page_id, "")
    if not page_file_scene.set_page_edit_state(context, page_id):
        raise AssertionError(f"page edit state could not be restored: {page_id}")
    page_file_scene.resync_page_runtime_objects(context.scene, work, page_id)

    for required in ("coma_borders:False", "text"):
        if required not in calls:
            raise AssertionError(f"{required} draw marker missing: {calls}")
    for forbidden in ("paper_bg_overlay", "paper_guide_overlay", "page_preview_overlay", "image", "shared", "coma_borders:True"):
        if forbidden in calls:
            raise AssertionError(f"current page must use real objects instead of {forbidden}: {calls}")
    if "paper" in calls:
        raise AssertionError(f"paper guides must not be drawn by overlay: {calls}")
    if "safe_overlay" in calls:
        raise AssertionError(f"safe area fill must not be drawn by overlay: {calls}")

    bg_obj = bpy.data.objects.get(f"{paper_bg_object.PAPER_BG_NAME_PREFIX}{page_id}")
    if bg_obj is None:
        raise AssertionError("paper background real object is missing")
    if bool(getattr(bg_obj, "hide_viewport", True)):
        raise AssertionError("paper background real object is hidden on current page")

    guide_objs = [
        obj
        for obj in bpy.data.objects
        if str(obj.get(paper_guide_object.PROP_GUIDE_OWNER_ID, "") or "") == page_id
    ]
    guide_kinds = {str(obj.get(paper_guide_object.PROP_GUIDE_KIND, "") or "") for obj in guide_objs}
    expected_kinds = {paper_guide_object.GUIDE_KIND_LINES, "safe_fill", "bleed_outer_fill"}
    if not expected_kinds.issubset(guide_kinds):
        raise AssertionError(f"missing paper guide objects: expected={expected_kinds}, actual={guide_kinds}")
    guide_line_objs = [
        obj for obj in guide_objs
        if str(obj.get(paper_guide_object.PROP_GUIDE_KIND, "") or "") == paper_guide_object.GUIDE_KIND_LINES
    ]
    if len(guide_line_objs) != 1:
        raise AssertionError(f"paper guide lines must be one curve object: {guide_line_objs}")
    for obj in guide_line_objs:
        if obj.type != "CURVE":
            raise AssertionError(f"paper guide lines should be Curve: {obj.name} ({obj.type})")
        if len(getattr(getattr(obj, "data", None), "splines", []) or []) == 0:
            raise AssertionError(f"paper guide has no splines: {obj.name}")
        if bool(getattr(obj, "show_in_front", False)) or bool(getattr(obj, "show_transparent", False)):
            raise AssertionError(f"paper guide should not rely on in-front transparent wire display: {obj.name}")
    safe_fill_objs = [
        obj for obj in guide_objs
        if str(obj.get(paper_guide_object.PROP_GUIDE_KIND, "") or "") == "safe_fill"
    ]
    if len(safe_fill_objs) != 1:
        raise AssertionError(f"safe area fill should be one mesh object: {safe_fill_objs}")
    safe_fill = safe_fill_objs[0]
    if safe_fill.type != "MESH":
        raise AssertionError(f"safe area fill should be mesh: {safe_fill.name} ({safe_fill.type})")
    if getattr(safe_fill, "display_type", "") != "SOLID":
        raise AssertionError("safe area fill should display as solid")
    if bool(getattr(safe_fill, "show_in_front", False)):
        raise AssertionError("safe area fill should not rely on viewport in-front display")
    if safe_fill.active_material is None or len(getattr(safe_fill.data, "materials", [])) != 1:
        raise AssertionError("safe area fill should have one viewport material")
    expected_color = tuple(float(v) for v in getattr(work.safe_area_overlay, "color", (0.0, 0.0, 0.0))) + (
        float(getattr(work.safe_area_overlay, "opacity", 30.0)) / 100.0,
    )
    for actual, expected in zip(tuple(safe_fill.color), expected_color, strict=False):
        if abs(float(actual) - expected) > 1.0e-4:
            raise AssertionError(f"safe area fill viewport color mismatch: {tuple(safe_fill.color)} != {expected_color}")
    for actual, expected in zip(tuple(safe_fill.active_material.diffuse_color), expected_color, strict=False):
        if abs(float(actual) - expected) > 1.0e-4:
            raise AssertionError(
                f"safe area fill viewport material color mismatch: "
                f"{tuple(safe_fill.active_material.diffuse_color)} != {expected_color}"
            )
    try:
        if not page_file_scene.set_page_edit_state(context, page_id):
            raise AssertionError(f"page edit state could not be restored for guide toggle: {page_id}")
        context.scene.bmanga_page_guides_visible = False
        paper_guide_object.regenerate_all_paper_guides(context.scene, page_file_scene.work_for_pages(work, {page_id}))
        hidden_objs = [
            obj for obj in bpy.data.objects
            if str(obj.get(paper_guide_object.PROP_GUIDE_OWNER_ID, "") or "") == page_id
            and str(obj.get(paper_guide_object.PROP_GUIDE_KIND, "") or "") in expected_kinds
        ]
        if any(not bool(getattr(obj, "hide_viewport", False)) for obj in hidden_objs):
            raise AssertionError("paper guide real objects remained visible after guide toggle off")
        bg_after_toggle = bpy.data.objects.get(f"{paper_bg_object.PAPER_BG_NAME_PREFIX}{page_id}")
        if bg_after_toggle is None or bool(getattr(bg_after_toggle, "hide_viewport", True)):
            raise AssertionError("paper background should remain visible when only guides are hidden")
        context.scene.bmanga_page_guides_visible = True
        paper_guide_object.regenerate_all_paper_guides(context.scene, page_file_scene.work_for_pages(work, {page_id}))
    finally:
        context.scene.bmanga_current_page_id = original["scene_current_page_id"]
        context.scene.bmanga_current_coma_id = original["current_coma_id"]
        context.scene.bmanga_current_coma_page_id = original["current_coma_page_id"]
        context.scene.bmanga_overview_mode = original["overview_mode"]
        context.scene.bmanga_active_layer_kind = original["active_layer_kind"]
        context.scene.bmanga_page_guides_visible = original["page_guides_visible"]
        work.active_page_index = original["active_page_index"]
        page_file_scene.is_page_edit_scene = original["is_page_edit_scene"]
        page_file_scene.current_page_id = original["current_page_id_func"]
        page_file_scene.current_role = original["current_role_func"]
    return calls


def _assert_coma_overlay_cleanup(context, work, page) -> None:
    from bmanga_dev.operators import object_tool_op
    from bmanga_dev.ui import overlay
    from bmanga_dev.ui import overlay_text
    from bmanga_dev.utils import object_selection

    if len(page.comas) == 0:
        raise AssertionError("coma overlay cleanup needs a coma")
    coma = page.comas[0]
    coma_key = object_selection.coma_key(page, coma)
    calls: list[str] = []
    original = {
        "get_keys": object_selection.get_keys,
        "active_selection_key": object_tool_op.active_selection_key,
        "selection_bounds_for_key": object_tool_op.selection_bounds_for_key,
        "draw_rect_outline": overlay._draw_rect_outline,
        "draw_rect_fill": overlay._draw_rect_fill,
        "draw_polygon_fill": overlay._draw_polygon_fill,
        "draw_stroke_band_fill": overlay._draw_stroke_band_fill,
        "draw_segments_mm": overlay._draw_segments_mm,
        "draw_styled_segment_mm": overlay._draw_styled_segment_mm,
        "draw_frame_with_hole": overlay._draw_frame_with_hole,
        "draw_polyline_loop": overlay._draw_polyline_loop,
        "draw_text_guides": overlay_text.draw_text_guides,
    }

    def mark(name):
        def _inner(*_args, **_kwargs):
            calls.append(name)
        return _inner

    old_border_visible = bool(coma.border.visible)
    old_white_margin_enabled = bool(coma.white_margin.enabled)
    old_background = tuple(coma.background_color)
    shared_index = -1
    try:
        object_selection.get_keys = lambda _context: [coma_key]
        object_tool_op.active_selection_key = lambda _context: coma_key
        object_tool_op.selection_bounds_for_key = lambda _context, _key: overlay.Rect(0.0, 0.0, 10.0, 10.0)
        overlay._draw_rect_outline = mark("selection_outline")
        overlay._draw_rect_fill = mark("selection_handle")
        overlay._draw_object_tool_layer_bounds(context)
        if calls:
            raise AssertionError(f"coma selection handles should not be drawn: {calls}")

        calls.clear()
        coma.border.visible = True
        coma.border.style = "brush"
        coma.border.width_mm = 3.0
        coma.white_margin.enabled = True
        coma.white_margin.width_mm = 2.0
        overlay._draw_rect_fill = mark("coma_white_margin_rect")
        overlay._draw_stroke_band_fill = mark("coma_stroke_band")
        overlay._draw_segments_mm = mark("coma_selection_or_border")
        overlay._draw_styled_segment_mm = mark("coma_styled_border")
        overlay._draw_frame_with_hole = mark("coma_white_margin_frame")
        overlay._draw_comas(work, page, draw_borders=False)
        forbidden = {
            "coma_white_margin_rect",
            "coma_stroke_band",
            "coma_styled_border",
            "coma_white_margin_frame",
        }
        if any(name in forbidden for name in calls):
            raise AssertionError(f"coma border/white margin overlay should not be drawn: {calls}")

        calls.clear()
        coma.border.visible = False
        coma.white_margin.enabled = False
        coma.background_color = (0.2, 0.4, 0.8, 1.0)
        overlay._draw_polygon_fill = mark("coma_background")
        overlay._draw_comas(work, page, draw_borders=False)
        if "coma_background" in calls:
            raise AssertionError(f"coma background overlay should not be drawn: {calls}")

        shared = work.shared_comas.add()
        shared_index = len(work.shared_comas) - 1
        shared.id = "overlay_cleanup_shared"
        shared.coma_id = "overlay_cleanup_shared"
        shared.shape_type = "rect"
        shared.rect_x_mm = 300.0
        shared.rect_y_mm = 30.0
        shared.rect_width_mm = 40.0
        shared.rect_height_mm = 30.0
        shared.background_color = (1.0, 0.0, 0.0, 1.0)
        overlay._draw_polyline_loop = mark("shared_outline")
        overlay_text.draw_text_guides = lambda *_args, **_kwargs: None
        overlay._draw_shared_layers(work)
        if "coma_background" in calls:
            raise AssertionError(f"shared coma background overlay should not be drawn: {calls}")
    finally:
        object_selection.get_keys = original["get_keys"]
        object_tool_op.active_selection_key = original["active_selection_key"]
        object_tool_op.selection_bounds_for_key = original["selection_bounds_for_key"]
        overlay._draw_rect_outline = original["draw_rect_outline"]
        overlay._draw_rect_fill = original["draw_rect_fill"]
        overlay._draw_polygon_fill = original["draw_polygon_fill"]
        overlay._draw_stroke_band_fill = original["draw_stroke_band_fill"]
        overlay._draw_segments_mm = original["draw_segments_mm"]
        overlay._draw_styled_segment_mm = original["draw_styled_segment_mm"]
        overlay._draw_frame_with_hole = original["draw_frame_with_hole"]
        overlay._draw_polyline_loop = original["draw_polyline_loop"]
        overlay_text.draw_text_guides = original["draw_text_guides"]
        coma.border.visible = old_border_visible
        coma.white_margin.enabled = old_white_margin_enabled
        coma.background_color = old_background
        if shared_index >= 0 and shared_index < len(work.shared_comas):
            work.shared_comas.remove(shared_index)


def _assert_brush_size_texture_paint(context) -> dict:
    from bmanga_dev.operators import brush_size_op

    try:
        if getattr(context.object, "mode", "OBJECT") != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    result = bpy.ops.bmanga.raster_layer_add("EXEC_DEFAULT", dpi=30, bit_depth="gray8")
    assert "FINISHED" in result, result
    result = bpy.ops.bmanga.raster_layer_paint_enter("EXEC_DEFAULT")
    assert "FINISHED" in result, result
    brush = brush_size_op._active_brush(context)
    assert brush is not None
    brush.size = 12
    assert brush_size_op._active_brush(context) == brush
    assert brush_size_op.BMANGA_OT_brush_size_drag.poll(context)
    brush_size_op._set_brush_size(brush, 37)
    assert brush.size == 37
    return {"texture_brush_size": int(brush.size)}


def _assert_text_create_drag_rect(text_op, page, entry, x_mm: float, y_mm: float) -> None:
    drag = SimpleNamespace()
    text_op.BMANGA_OT_text_tool._start_text_drag(drag, page, entry, "create", x_mm, y_mm)
    x, y, w, h = text_op.BMANGA_OT_text_tool._drag_result_rect(drag, 24.0, 18.0)
    text_op._set_text_rect(entry, x, y, w, h)
    for label, actual, expected in (
        ("text drag x", entry.x_mm, x_mm),
        ("text drag y", entry.y_mm, y_mm),
        ("text drag width", entry.width_mm, 24.0),
        ("text drag height", entry.height_mm, 18.0),
    ):
        if abs(float(actual) - float(expected)) > 1.0e-4:
            raise AssertionError(f"{label}: expected {expected}, got {actual}")


def _write_visual_report(state: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "bmanga_partial_completion_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return OUT_DIR / "bmanga_partial_completion_state.json"
    image = Image.new("RGB", (980, 620), "white")
    draw = ImageDraw.Draw(image)
    font = _diagnostic_font(ImageFont, size=13)
    draw.text((24, 18), "B-MANGA partial completion visual report", fill=(0, 0, 0), font=font)
    page_rect = (70, 72, 390, 526)
    draw.rectangle(page_rect, fill=(250, 250, 248), outline=(0, 0, 0), width=1)
    coma_rect = (132, 170, 330, 380)
    draw.rectangle(coma_rect, fill=(235, 235, 235), outline=(25, 25, 25), width=7)
    focus = (258, 292)
    for start in ((150, 188), (184, 177), (220, 173), (297, 188), (310, 224), (309, 260), (169, 352), (306, 345)):
        draw.line((start[0], start[1], focus[0], focus[1]), fill=(35, 95, 210), width=3)
    draw.text((268, 302), "効果線", fill=(35, 95, 210), font=font)
    draw.ellipse((185, 208, 302, 287), fill=(255, 255, 255), outline=(0, 0, 0), width=4)
    draw.text((211, 238), "text", fill=(20, 20, 20), font=font)
    draw.line((70, 102, 390, 102), fill=(235, 35, 145), width=3)
    draw.line((100, 72, 100, 526), fill=(235, 35, 145), width=3)
    draw.rectangle((84, 88, 376, 510), outline=(235, 35, 145), width=3)
    draw.text((68, 544), "paper guides are real viewport objects", fill=(0, 0, 0), font=font)
    draw.text((470, 76), "Verified state", fill=(0, 0, 0), font=font)
    y = 112
    for item in state.get("checks", []):
        draw.rectangle((470, y, 920, y + 28), fill=(238, 248, 238), outline=(85, 135, 85))
        draw.text((482, y + 8), item, fill=(0, 0, 0), font=font)
        y += 38
    image_path = OUT_DIR / "bmanga_partial_completion_visual.png"
    image.save(image_path)
    return image_path


def _diagnostic_font(ImageFont, *, size: int):
    for path in (
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
    ):
        try:
            if Path(path).is_file():
                return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_partial_completion_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PartialCompletion.bmanga"))
        assert "FINISHED" in result, result
        assert "FINISHED" in bpy.ops.bmanga.page_add("EXEC_DEFAULT")
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=1)
        assert "FINISHED" in result, result

        from bmanga_dev.operators import balloon_op
        from bmanga_dev.operators import effect_line_op
        from bmanga_dev.operators import text_op
        from bmanga_dev.utils import gp_layer_parenting as gp_parent
        from bmanga_dev.utils import layer_hierarchy
        from bmanga_dev.utils import layer_object_sync
        from bmanga_dev.utils import layer_reparent
        from bmanga_dev.utils import layer_stack as layer_stack_utils
        from bmanga_dev.utils import object_naming as on
        from bmanga_dev.utils import page_grid

        context = bpy.context
        scene = context.scene
        work = scene.bmanga_work
        assert bool(work.safe_area_overlay.enabled)
        assert abs(float(work.safe_area_overlay.opacity) - 30.0) <= 1.0e-4
        work.safe_area_overlay.color = (0.10, 0.20, 0.30)
        page1 = work.pages[0]
        page2 = work.pages[1]
        panel2 = page2.comas[0]
        page1_key = layer_hierarchy.page_stack_key(page1)
        page2_key = layer_hierarchy.page_stack_key(page2)
        coma2_key = layer_hierarchy.coma_stack_key(page2, panel2)
        ox2, oy2 = page_grid.page_total_offset_mm(work, scene, 1)
        local_x, local_y = _inside_panel_point(panel2)
        page_local_x, page_local_y = _outside_panel_point(work, page2)

        resolved = effect_line_op._creation_context_for_world_point(
            context,
            ox2 + local_x,
            oy2 + local_y,
        )
        assert resolved is not None and resolved[2] == 1 and resolved[5] == coma2_key
        effect_obj, effect_layer = effect_line_op._create_effect_layer(
            context,
            (resolved[3], resolved[4], 18.0, 12.0),
            parent_key=resolved[5],
        )
        assert gp_parent.parent_key(effect_layer) == coma2_key
        assert str(effect_obj.get(on.PROP_PARENT_KEY, "") or "") == coma2_key

        parent_kind, parent_key = balloon_op._parent_for_creation_point(page2, local_x, local_y)
        assert parent_kind == "coma" and parent_key == coma2_key
        balloon = balloon_op._create_balloon_entry(
            context,
            page2,
            shape="rect",
            x=local_x,
            y=local_y,
            w=28.0,
            h=18.0,
            parent_kind=parent_kind,
            parent_key=parent_key,
        )

        text, missing = text_op._create_text_entry(
            context,
            page2,
            body="コマ内テキスト",
            speaker_type="normal",
            x_mm=local_x,
            y_mm=local_y,
            width_mm=30.0,
            height_mm=16.0,
            parent_kind="coma",
            parent_key=coma2_key,
        )
        assert not missing
        assert text.parent_kind == "coma" and text.parent_key == coma2_key

        page_text, _missing = text_op._create_text_entry(
            context,
            page2,
            body="ページ直下テキスト",
            speaker_type="normal",
            x_mm=page_local_x,
            y_mm=page_local_y,
            width_mm=30.0,
            height_mm=16.0,
            parent_kind="page",
            parent_key=page2_key,
        )
        assert page_text.parent_kind == "page" and page_text.parent_key == page2_key
        _assert_text_create_drag_rect(text_op, page2, page_text, page_local_x, page_local_y)

        _force_full_outliner_mirror(scene, work)
        _assert_text_collection(context)

        work.active_page_index = 1
        _stack(context)
        page2_visible = _visible_stack_uids(context)
        if not any(page2_key in uid for uid in page2_visible):
            raise AssertionError(f"page2 rows are not visible: {page2_visible}")
        if not any(uid.startswith("coma:") and page2_key in uid for uid in page2_visible):
            raise AssertionError(f"page2 coma rows are not visible: {page2_visible}")
        if any(page1_key in uid for uid in page2_visible):
            raise AssertionError(f"page1 leaked into page2 filtered list: {page2_visible}")

        gp_obj, gp_layer = _add_gp_layer(context, page2_key)
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        _idx, gp_item = _stack_item(
            context,
            "gp",
            layer_stack_utils._node_stack_key(gp_layer),
        )
        assert layer_reparent.reparent_stack_item(
            context,
            gp_item,
            target=layer_reparent.ClickTarget("coma", page2, panel2, 1, None, None),
        )
        assert gp_parent.parent_key(gp_layer) == coma2_key

        _idx, effect_item = _stack_item(
            context,
            "effect",
            layer_stack_utils._node_stack_key(effect_layer),
        )
        assert layer_reparent.reparent_stack_item(
            context,
            effect_item,
            target=layer_reparent.ClickTarget("page", page2, None, 1, None, None),
        )
        assert gp_parent.parent_key(effect_layer) == page2_key

        _idx, balloon_item = _stack_item(context, "balloon", f"{page2_key}:{balloon.id}")
        assert layer_reparent.reparent_stack_item(
            context,
            balloon_item,
            target=layer_reparent.ClickTarget("outside", None, None, -1, (80.0, 90.0), None),
            new_world_xy_mm=(80.0, 90.0),
        )
        assert len(work.shared_balloons) >= 1
        assert work.shared_balloons[-1].parent_kind == "none"
        _force_full_outliner_mirror(scene, work)
        shared_balloon_obj = on.find_object_by_bmanga_id(work.shared_balloons[-1].id, kind="balloon")
        if shared_balloon_obj is None or shared_balloon_obj.hide_viewport:
            raise AssertionError("page-outside balloon object is not visible")

        draw_calls = _assert_paper_guides_use_real_objects(context, work, page2)
        _assert_coma_overlay_cleanup(context, work, page2)
        brush_state = _assert_brush_size_texture_paint(context)

        visual_path = _write_visual_report(
            {
                "checks": [
                    "用紙ガイド: 実体オブジェクトで表示",
                    "作成所属: 2ページ目のコマ内/ページ直下を確認",
                    "テキスト: ドラッグ範囲作成を確認",
                    "テキスト: B-MANGA直下の「テキスト」に集約",
                    "セーフライン外: 実体オブジェクトの色と不透明度を確認",
                    "レイヤーリスト: 選択ページだけを表示",
                    "Alt階層移動: GP/効果線/フキダシを確認",
                    "コマ表示: 選択ハンドルと背景オーバーレイ削除を確認",
                    "ラスター中ブラシサイズ: Texture Paintブラシを確認",
                ],
                "visible_stack_count": len(page2_visible),
                "draw_calls": draw_calls,
                "gp_object": getattr(gp_obj, "name", ""),
                **brush_state,
            }
        )
        print(f"BMANGA_PARTIAL_COMPLETION_OK visual={visual_path}")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
