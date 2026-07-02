"""Blender実機用: ページファイルのページ移動表示とダブルクリック判定確認."""

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
        "bmanga_dev_page_nav_click",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_page_nav_click"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _assert_nav_labels(work) -> None:
    from bmanga_dev_page_nav_click.panels import work_panel

    work.paper.read_direction = "left"
    assert work_panel._page_file_nav_specs(work) == (
        ("bmanga.page_file_next", "◀　次のページへ"),
        ("bmanga.page_file_prev", "前のページへ　▶"),
    )
    work.paper.read_direction = "right"
    assert work_panel._page_file_nav_specs(work) == (
        ("bmanga.page_file_prev", "◀　前のページへ"),
        ("bmanga.page_file_next", "次のページへ　▶"),
    )
    work.paper.read_direction = "left"


def _assert_page_file_double_click_fallback() -> None:
    from bmanga_dev_page_nav_click.operators import mode_op, page_op

    event = SimpleNamespace(mouse_x=320, mouse_y=240)
    original_preview_hit = mode_op._resolve_page_preview_at_event
    original_page_hit = mode_op._resolve_page_at_event
    try:
        mode_op._resolve_page_preview_at_event = lambda _context, _event: None
        mode_op._resolve_page_at_event = lambda _context, _event: 1
        assert mode_op.page_file_index_from_viewport_event(bpy.context, event) == 1
        page_op._clear_page_open_click_state()
        assert page_op._detect_page_open_double_click(bpy.context, event) is None
        assert page_op._detect_page_open_double_click(bpy.context, event) == 1
    finally:
        mode_op._resolve_page_preview_at_event = original_preview_hit
        mode_op._resolve_page_at_event = original_page_hit
        page_op._clear_page_open_click_state()


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_page_nav_click_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PageNavClick.bmanga"))
        assert result == {"FINISHED"}, result
        result = bpy.ops.bmanga.page_add()
        assert result == {"FINISHED"}, result
        work = bpy.context.scene.bmanga_work
        _assert_nav_labels(work)
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert result == {"FINISHED"}, result
        _assert_page_file_double_click_fallback()
        print("BMANGA_PAGE_FILE_NAV_AND_DOUBLE_CLICK_OK")
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
