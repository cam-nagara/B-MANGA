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
        area=getattr(bpy.context, "area", None),
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
        _check_multi_select_reorder(fake_context)
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
