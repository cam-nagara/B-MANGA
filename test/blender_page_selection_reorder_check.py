"""Blender実機用: ページ一覧の複数選択とAltドラッグ並べ替え確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_page_select_reorder",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_page_select_reorder"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _page_ids(work) -> list[str]:
    return [str(getattr(page, "id", "") or "") for page in work.pages]


def _ensure_page_count(count: int) -> None:
    while len(bpy.context.scene.bmanga_work.pages) < count:
        result = bpy.ops.bmanga.page_add()
        assert result == {"FINISHED"}, result


def _fake_context(scene):
    return SimpleNamespace(
        scene=scene,
        mode="OBJECT",
        window_manager=SimpleNamespace(modal_handler_add=lambda _op: None),
        view_layer=bpy.context.view_layer,
        screen=getattr(bpy.context, "screen", None),
        area=getattr(bpy.context, "area", None) or SimpleNamespace(type="VIEW_3D"),
    )


def _check_alt_invoke_accepts_page_reorder(context) -> None:
    from bmanga_dev_page_select_reorder.operators import coma_picker, page_reorder_drag_op

    original_find = coma_picker.find_page_at_event
    try:
        coma_picker.find_page_at_event = lambda _context, _event: 1
        context.scene.bmanga_active_layer_kind = "page"
        event_alt = SimpleNamespace(
            value="PRESS",
            alt=True,
            ctrl=False,
            shift=False,
            mouse_x=100,
            mouse_y=100,
        )
        op = SimpleNamespace()
        result = page_reorder_drag_op.BMANGA_OT_page_reorder_drag.invoke(
            op,
            context,
            event_alt,
        )
        assert result == {"RUNNING_MODAL"}, result
        event_ctrl = SimpleNamespace(
            value="PRESS",
            alt=False,
            ctrl=True,
            shift=False,
            mouse_x=100,
            mouse_y=100,
        )
        result = page_reorder_drag_op.BMANGA_OT_page_reorder_drag.invoke(
            op,
            context,
            event_ctrl,
        )
        assert result == {"PASS_THROUGH"}, result
    finally:
        coma_picker.find_page_at_event = original_find


def _check_multi_select_reorder(context) -> None:
    from bmanga_dev_page_select_reorder.operators import page_reorder_drag_op
    from bmanga_dev_page_select_reorder.ui import overlay
    from bmanga_dev_page_select_reorder.utils import object_selection

    work = context.scene.bmanga_work
    before = _page_ids(work)
    object_selection.set_keys(
        bpy.context,
        [
            object_selection.page_key(work.pages[1]),
            object_selection.page_key(work.pages[2]),
        ],
    )
    op = SimpleNamespace()
    op._start_page_index = 1
    op._dst_index = len(work.pages)
    op._sync_after_reorder = (
        lambda ctx: page_reorder_drag_op.BMANGA_OT_page_reorder_drag._sync_after_reorder(
            op,
            ctx,
        )
    )
    page_reorder_drag_op.BMANGA_OT_page_reorder_drag._execute_reorder(op, bpy.context)
    after = _page_ids(work)
    expected = [before[0], before[3], before[4], before[1], before[2]]
    assert after == expected, {"before": before, "after": after, "expected": expected}
    selected_ids = [
        object_selection.parse_key(key)[2]
        for key in object_selection.get_keys(bpy.context)
        if object_selection.parse_key(key)[0] == "page"
    ]
    assert selected_ids == [before[1], before[2]], selected_ids
    assert overlay._selected_page_ids(bpy.context) == {before[1], before[2]}


def _check_viewport_shift_pick_updates_active(context) -> None:
    from bmanga_dev_page_select_reorder.operators import coma_picker, page_op
    from bmanga_dev_page_select_reorder.utils import object_selection

    work = context.scene.bmanga_work
    original_wm = context.window_manager
    original_pick = page_op._pick_object_layer_at_event
    original_edge = coma_picker.find_coma_edge_at_event
    original_coma = coma_picker.find_coma_at_event
    original_page = coma_picker.find_page_at_event
    try:
        context.window_manager = bpy.context.window_manager
        page_op._pick_object_layer_at_event = lambda _context, _event: (None, None)
        coma_picker.find_coma_edge_at_event = lambda _context, _event: None
        coma_picker.find_coma_at_event = lambda _context, _event: None
        page_hit = {"index": 2}
        coma_picker.find_page_at_event = lambda _context, _event: page_hit["index"]

        work.active_page_index = 0
        object_selection.set_keys(context, [object_selection.page_key(work.pages[0])])
        event_shift = SimpleNamespace(
            value="PRESS",
            alt=False,
            ctrl=False,
            shift=True,
            oskey=False,
            mouse_x=100,
            mouse_y=100,
        )
        result = page_op.BMANGA_OT_page_pick_viewport.invoke(SimpleNamespace(), context, event_shift)
        assert result == {"FINISHED"}, result
        assert work.active_page_index == 2, work.active_page_index
        keys = object_selection.get_keys(context)
        assert object_selection.page_key(work.pages[0]) in keys
        assert object_selection.page_key(work.pages[2]) in keys

        page_hit["index"] = 0
        event_ctrl = SimpleNamespace(
            value="PRESS",
            alt=False,
            ctrl=True,
            shift=False,
            oskey=False,
            mouse_x=100,
            mouse_y=100,
        )
        result = page_op.BMANGA_OT_page_pick_viewport.invoke(SimpleNamespace(), context, event_ctrl)
        assert result == {"FINISHED"}, result
        assert work.active_page_index == 0, work.active_page_index
        keys = object_selection.get_keys(context)
        assert object_selection.page_key(work.pages[0]) not in keys
        assert object_selection.page_key(work.pages[2]) in keys
    finally:
        page_op._pick_object_layer_at_event = original_pick
        coma_picker.find_coma_edge_at_event = original_edge
        coma_picker.find_coma_at_event = original_coma
        coma_picker.find_page_at_event = original_page
        context.window_manager = original_wm


def _check_page_highlight_outline_only() -> None:
    from bmanga_dev_page_select_reorder.ui import overlay

    calls: list[str] = []
    original_gpu = overlay.gpu
    original_fill = overlay._draw_rect_fill
    original_outline = overlay._draw_rect_outline
    try:
        overlay.gpu = SimpleNamespace(
            state=SimpleNamespace(
                depth_test_get=lambda: "LESS_EQUAL",
                depth_test_set=lambda _value: None,
            ),
        )
        overlay._draw_rect_fill = lambda *_args, **_kwargs: calls.append("fill")
        overlay._draw_rect_outline = lambda *_args, **_kwargs: calls.append("outline")
        overlay._draw_page_highlight(overlay.Rect(0.0, 0.0, 100.0, 100.0))
    finally:
        overlay.gpu = original_gpu
        overlay._draw_rect_fill = original_fill
        overlay._draw_rect_outline = original_outline
    assert calls == ["outline", "outline"], calls


def _check_work_overview_current_page_keeps_preview(context, temp_root: Path) -> None:
    from bmanga_dev_page_select_reorder.ui import overlay_page_preview
    from bmanga_dev_page_select_reorder.utils import page_preview_object

    scene = context.scene
    work = scene.bmanga_work
    page = work.pages[0]
    page_id = str(getattr(page, "id", "") or "")
    preview_path = page_preview_object._preview_png_path(work, page_id)  # noqa: SLF001
    assert preview_path is not None
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_bytes(b"not-a-real-png")

    calls: list[tuple[str, float, float, float, float, float]] = []
    original_has_gpu_texture = overlay_page_preview._HAS_GPU_TEXTURE
    original_draw_textured_quad = overlay_page_preview._draw_textured_quad
    original_preview_opacity = overlay_page_preview._preview_opacity
    try:
        overlay_page_preview._HAS_GPU_TEXTURE = True
        overlay_page_preview._preview_opacity = lambda _context: 1.0

        def _record(path, x_mm, y_mm, w_mm, h_mm, opacity):
            calls.append((str(path), float(x_mm), float(y_mm), float(w_mm), float(h_mm), float(opacity)))

        overlay_page_preview._draw_textured_quad = _record
        overlay_page_preview.draw_for_page(
            context,
            work,
            page,
            0,
            0.0,
            0.0,
            is_current_page=True,
        )
    finally:
        overlay_page_preview._HAS_GPU_TEXTURE = original_has_gpu_texture
        overlay_page_preview._draw_textured_quad = original_draw_textured_quad
        overlay_page_preview._preview_opacity = original_preview_opacity
        try:
            preview_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
    assert calls, "作品ファイルのページ一覧で選択中ページのプレビューが描画されていません"
    assert calls[-1][0] == str(preview_path), calls
    assert temp_root.exists(), "一時作品フォルダが途中で消えています"


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_page_select_reorder_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PageSelectReorder.bmanga"))
        assert result == {"FINISHED"}, result
        _ensure_page_count(5)
        scene = bpy.context.scene
        scene.bmanga_overview_mode = True
        fake_context = _fake_context(scene)
        _check_alt_invoke_accepts_page_reorder(fake_context)
        _check_viewport_shift_pick_updates_active(fake_context)
        _check_multi_select_reorder(fake_context)
        _check_page_highlight_outline_only()
        _check_work_overview_current_page_keeps_preview(bpy.context, temp_root)
        print("BMANGA_PAGE_SELECTION_REORDER_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
