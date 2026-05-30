"""Blender 実機用: テキスト入力中の自動枠調整と移動時の軽量更新確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _new_entry(work_dir: Path):
    result = bpy.ops.bname.work_new(filepath=str(work_dir))
    assert result == {"FINISHED"}, result
    from bname_dev.utils import text_real_object

    work = bpy.context.scene.bname_work
    page = work.pages[0]
    entry = page.texts.add()
    with text_real_object.suspend_auto_sync():
        entry.id = "text_autofit_perf"
        entry.body = "ABC"
        entry.x_mm = 20.0
        entry.y_mm = 30.0
        entry.width_mm = 8.0
        entry.height_mm = 6.0
        entry.writing_mode = "horizontal"
        entry.font_size_q = 20.0
    return work, page, entry


def _assert_no_overflow(entry):
    from bname_dev.operators import text_edit_runtime
    from bname_dev.typography import layout as text_layout

    rect = text_edit_runtime.text_rect(entry)
    region = text_edit_runtime.text_inner_rect(rect)
    result = text_layout.typeset(entry, region.x, region.y, region.width, region.height)
    assert not result.overflow, (entry.body, rect, result)


def _assert_text_has_handle_gap(entry):
    from bname_dev.operators import text_edit_runtime

    rect = text_edit_runtime.text_rect(entry)
    region = text_edit_runtime.text_inner_rect(rect)
    assert region.x - rect.x >= 2.49, (rect, region)
    assert rect.x2 - region.x2 >= 2.49, (rect, region)
    assert region.y - rect.y >= 2.49, (rect, region)
    assert rect.y2 - region.y2 >= 2.49, (rect, region)


def _assert_shift_enter_does_not_move_up_or_right(entry, *, body: str, mode: str):
    from bname_dev.operators import text_edit_runtime
    from bname_dev.utils import text_real_object

    with text_real_object.suspend_auto_sync():
        entry.body = body
        entry.writing_mode = mode
        entry.x_mm = 20.0
        entry.y_mm = 30.0
        entry.width_mm = 6.0
        entry.height_mm = 6.0
        text_edit_runtime.fit_text_rect_to_body(entry, min_width=2.0, min_height=2.0)
        before = text_edit_runtime.text_rect(entry)
        entry.body = body[:2] + "\n" + body[2:]
        text_edit_runtime.fit_text_rect_to_body(entry, min_width=2.0, min_height=2.0)
        after = text_edit_runtime.text_rect(entry)

    assert abs(after.y2 - before.y2) < 1.0e-5, (before, after)
    assert after.y <= before.y + 1.0e-5, (before, after)
    if mode == "horizontal":
        assert abs(after.x - before.x) < 1.0e-5, (before, after)
        assert abs(after.x2 - before.x2) < 1.0e-5, (before, after)
    else:
        assert abs(after.x2 - before.x2) < 1.0e-5, (before, after)
        assert after.x <= before.x + 1.0e-5, (before, after)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_text_autofit_perf_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        _work, page, entry = _new_entry(temp_root / "TextAutofitPerf.bname")

        from bname_dev.operators import text_edit_runtime
        from bname_dev.utils import page_grid, text_real_object

        with text_real_object.suspend_auto_sync():
            entry.body = "ABCDEFGHIJKL"
            entry.width_mm = 6.0
            entry.height_mm = 6.0
            entry.writing_mode = "horizontal"
            changed = text_edit_runtime.fit_text_rect_to_body(entry, min_width=2.0, min_height=2.0)
        assert changed
        assert entry.width_mm > 6.0, entry.width_mm
        _assert_text_has_handle_gap(entry)
        _assert_no_overflow(entry)

        with text_real_object.suspend_auto_sync():
            entry.body = "日本語テキスト長め"
            entry.width_mm = 6.0
            entry.height_mm = 6.0
            entry.writing_mode = "vertical"
            changed = text_edit_runtime.fit_text_rect_to_body(entry, min_width=2.0, min_height=2.0)
        assert changed
        assert entry.height_mm > 6.0, entry.height_mm
        _assert_text_has_handle_gap(entry)
        _assert_no_overflow(entry)
        _assert_shift_enter_does_not_move_up_or_right(
            entry,
            body="ABCDEFGHIJKL",
            mode="horizontal",
        )
        _assert_shift_enter_does_not_move_up_or_right(
            entry,
            body="日本語テキスト長め",
            mode="vertical",
        )

        calls = {"render": 0}
        original_render = text_real_object._render_entry_to_pillow

        def _counted_render(target_entry):
            calls["render"] += 1
            return original_render(target_entry)

        text_real_object._render_entry_to_pillow = _counted_render
        try:
            obj = text_real_object.ensure_text_real_object(scene=bpy.context.scene, entry=entry, page=page)
            assert obj is not None
            assert calls["render"] == 1, calls
            entry.x_mm += 12.0
            entry.y_mm += 8.0
            assert calls["render"] == 1, calls
            ox, oy = page_grid.page_total_offset_mm(_work, bpy.context.scene, 0)
            assert abs(obj.location.x * 1000.0 - (entry.x_mm + ox)) < 1.0e-4
            assert abs(obj.location.y * 1000.0 - (entry.y_mm + oy)) < 1.0e-4
            text_real_object.ensure_text_real_object(scene=bpy.context.scene, entry=entry, page=page)
            assert calls["render"] == 1, calls
            entry.body += "Z"
            assert calls["render"] == 2, calls
            with text_real_object.suspend_auto_sync():
                entry.body += "Y"
                text_edit_runtime.fit_text_rect_to_body(entry, min_width=2.0, min_height=2.0)
            assert calls["render"] == 2, calls
            text_real_object.ensure_text_real_object(scene=bpy.context.scene, entry=entry, page=page)
            assert calls["render"] == 3, calls
        finally:
            text_real_object._render_entry_to_pillow = original_render

        print("BNAME_TEXT_AUTOFIT_PERF_OK")
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
