"""Blender 5.2実機: ビュー表示切替が全件再生成を起動しない。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import time

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_view_toggle_test",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_view_toggle_test"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _guide_objects(page_id: str):
    return [
        obj
        for obj in bpy.data.objects
        if str(obj.get("bmanga_paper_guide_page_id", "") or "") == page_id
    ]


def main() -> None:
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        temp_root = Path(tempfile.mkdtemp(prefix="bmanga_view_toggle_"))
        assert "FINISHED" in bpy.ops.bmanga.work_new(
            filepath=str(temp_root / "ViewToggle.bmanga")
        )
        assert "FINISHED" in bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)

        from bmanga_view_toggle_test.utils import page_preview_object, paper_guide_object

        scene = bpy.context.scene
        work = scene.bmanga_work
        page_id = str(work.pages[work.active_page_index].id)
        guides = _guide_objects(page_id)
        assert guides
        calls = {"preview": 0, "guides": 0}
        original_preview = page_preview_object.sync_page_previews
        original_guides = paper_guide_object.regenerate_all_paper_guides
        page_preview_object.sync_page_previews = (
            lambda *_args, **_kwargs: calls.__setitem__("preview", calls["preview"] + 1)
        )
        paper_guide_object.regenerate_all_paper_guides = (
            lambda *_args, **_kwargs: calls.__setitem__("guides", calls["guides"] + 1)
        )
        try:
            started = time.perf_counter()
            for _ in range(10):
                scene.bmanga_overlay_enabled = not scene.bmanga_overlay_enabled
                scene.bmanga_page_work_info_visible = (
                    not scene.bmanga_page_work_info_visible
                )
                scene.bmanga_page_guides_visible = not scene.bmanga_page_guides_visible
            elapsed = time.perf_counter() - started
            scene.bmanga_overlay_enabled = True
            scene.bmanga_page_work_info_visible = True
            scene.bmanga_page_guides_visible = True
        finally:
            page_preview_object.sync_page_previews = original_preview
            paper_guide_object.regenerate_all_paper_guides = original_guides

        assert calls == {"preview": 0, "guides": 0}, calls
        assert any(not bool(obj.hide_viewport) for obj in guides)
        assert elapsed < 1.0, elapsed

        # 保存時に非表示だった実体でも、再読込直後に現在の表示設定へ復元する。
        for obj in guides:
            obj.hide_viewport = True
        assert "FINISHED" in bpy.ops.wm.save_as_mainfile(
            filepath=bpy.data.filepath,
            check_existing=False,
        )
        assert "FINISHED" in bpy.ops.wm.open_mainfile(
            filepath=bpy.data.filepath,
            load_ui=False,
        )
        scene = bpy.context.scene
        work = scene.bmanga_work
        page_id = str(work.pages[work.active_page_index].id)
        reloaded_guides = _guide_objects(page_id)
        assert reloaded_guides
        assert any(not bool(obj.hide_viewport) for obj in reloaded_guides)
        assert bool(scene.bmanga_overlay_enabled)
        assert bool(scene.bmanga_page_work_info_visible)
        assert bool(scene.bmanga_page_guides_visible)
        print(f"BMANGA_VIEW_TOGGLE_LIGHTWEIGHT_OK elapsed={elapsed:.6f}")
    finally:
        if module is not None:
            try:
                module.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
