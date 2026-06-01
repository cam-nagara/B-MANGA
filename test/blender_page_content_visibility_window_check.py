"""ページ一覧で選択ページ周辺だけ中身を表示することを確認."""

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
        "bname_dev_page_content_visibility",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_page_content_visibility"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _make_layer_object(page_id: str, suffix: str, *, parent_key: str = ""):
    from bname_dev_page_content_visibility.utils import object_naming as on

    obj = bpy.data.objects.new(f"visibility_probe_{page_id}_{suffix}", None)
    bpy.context.scene.collection.objects.link(obj)
    obj[on.PROP_KIND] = "balloon"
    obj[on.PROP_ID] = f"{page_id}_{suffix}"
    obj[on.PROP_PARENT_KEY] = parent_key or page_id
    obj[on.PROP_MANAGED] = True
    return obj


def _assert_hidden(obj, expected: bool, label: str) -> None:
    actual = bool(getattr(obj, "hide_viewport", False))
    if actual != expected:
        raise AssertionError(f"{label}: hidden expected {expected}, got {actual}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_page_content_visibility_"))
    mod = None
    try:
        mod = _load_addon()
        bpy.context.scene.bname_overview_mode = True
        if "FINISHED" not in bpy.ops.bname.work_new(
            filepath=str(temp_root / "PageContentVisibility.bname")
        ):
            raise AssertionError("作品作成に失敗しました")

        from bname_dev_page_content_visibility.core.work import get_work
        from bname_dev_page_content_visibility.utils import page_content_visibility, page_range

        scene = bpy.context.scene
        scene.bname_overview_mode = True
        work = get_work(bpy.context)
        work.work_info.page_number_start = 1
        work.work_info.page_number_end = 6
        page_range.ensure_pages_for_number_range(bpy.context)

        page_ids = [str(page.id) for page in work.pages]
        objects = {
            page_id: _make_layer_object(page_id, "main")
            for page_id in page_ids
        }
        originally_hidden = _make_layer_object(page_ids[0], "originally_hidden")
        originally_hidden.hide_viewport = True

        folder = work.layer_folders.add()
        folder.id = "visibility_folder_p4"
        folder.title = "フォルダ"
        folder.parent_key = page_ids[3]
        folder_object = _make_layer_object(
            page_ids[3],
            "folder_child",
            parent_key=folder.id,
        )

        work.active_page_index = 2
        page_content_visibility.apply_page_content_visibility(bpy.context, work)
        for index, page_id in enumerate(page_ids):
            _assert_hidden(objects[page_id], index not in {1, 2, 3}, f"active3 page{index + 1}")
        _assert_hidden(folder_object, False, "folder child active3")
        _assert_hidden(originally_hidden, True, "original hidden while far")

        work.active_page_index = 0
        page_content_visibility.apply_page_content_visibility(bpy.context, work)
        for index, page_id in enumerate(page_ids):
            _assert_hidden(objects[page_id], index not in {0, 1}, f"active1 page{index + 1}")
        _assert_hidden(folder_object, True, "folder child active1")
        _assert_hidden(originally_hidden, True, "original hidden restored")
        if bool(originally_hidden.get(page_content_visibility.PROP_VIRTUAL_HIDDEN, False)):
            raise AssertionError("元から非表示だったレイヤーの仮非表示印が残っています")

        work.active_page_index = 4
        page_content_visibility.apply_page_content_visibility(bpy.context, work)
        for index, page_id in enumerate(page_ids):
            _assert_hidden(objects[page_id], index not in {3, 4, 5}, f"active5 page{index + 1}")
        _assert_hidden(folder_object, False, "folder child active5")

        page_content_visibility.restore_all_virtual_hidden(bpy.context, work)
        for index, page_id in enumerate(page_ids):
            _assert_hidden(objects[page_id], False, f"restore page{index + 1}")
        _assert_hidden(folder_object, False, "folder child restored")
        _assert_hidden(originally_hidden, True, "original hidden remains hidden")

        print("BNAME_PAGE_CONTENT_VISIBILITY_WINDOW_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
