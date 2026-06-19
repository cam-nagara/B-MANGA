"""ページ一覧の位置更新が重い全再生成に戻らないことを確認."""

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
        "bmanga_dev_page_lightweight_sync",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_page_lightweight_sync"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_page_lightweight_sync_"))
    mod = None
    try:
        mod = _load_addon()
        bpy.context.scene.bmanga_overview_mode = True
        if "FINISHED" not in bpy.ops.bmanga.work_new(
            filepath=str(temp_root / "PageLightweightSync.bmanga")
        ):
            raise AssertionError("作品作成に失敗しました")

        from bmanga_dev_page_lightweight_sync.core.work import get_work
        from bmanga_dev_page_lightweight_sync.utils import page_grid, page_range
        from bmanga_dev_page_lightweight_sync.utils import paper_guide_object, work_info_text_object

        work = get_work(bpy.context)
        info = work.work_info
        info.page_number_start = 1
        info.page_number_end = 20
        page_range.ensure_pages_for_number_range(bpy.context)

        # 初回同期で、全ページの用紙ガイドと作品情報テキストへ軽量同期用の印を付ける。
        page_grid.apply_page_collection_transforms(bpy.context, work)

        heavy_calls = {"paper": 0, "info": 0}
        original_paper = paper_guide_object.regenerate_all_paper_guides
        original_info = work_info_text_object.regenerate_all_work_info_texts

        def _heavy_paper(*args, **kwargs):
            heavy_calls["paper"] += 1
            return original_paper(*args, **kwargs)

        def _heavy_info(*args, **kwargs):
            heavy_calls["info"] += 1
            return original_info(*args, **kwargs)

        paper_guide_object.regenerate_all_paper_guides = _heavy_paper
        work_info_text_object.regenerate_all_work_info_texts = _heavy_info
        try:
            page_grid.apply_page_collection_transforms(bpy.context, work)
        finally:
            paper_guide_object.regenerate_all_paper_guides = original_paper
            work_info_text_object.regenerate_all_work_info_texts = original_info

        if heavy_calls["paper"] != 0:
            raise AssertionError("ページ位置更新で用紙ガイドを全ページ再生成しています")
        if heavy_calls["info"] != 0:
            raise AssertionError("ページ位置更新で作品情報テキストを全ページ再生成しています")

        print("BMANGA_PAGE_LIST_LIGHTWEIGHT_SYNC_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
