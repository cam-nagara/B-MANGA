"""Blender 5.2実機用: 現行page.blendで汎用レイヤーフォルダを検証する。"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
PKG = "bmanga_layer_folder_test"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PKG,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _modules():
    from bmanga_layer_folder_test.io import schema
    from bmanga_layer_folder_test.utils import (
        image_real_object,
        layer_folder,
        layer_stack,
    )
    from bmanga_layer_folder_test.utils.layer_hierarchy import (
        coma_stack_key,
        page_stack_key,
    )

    return SimpleNamespace(
        folders=layer_folder,
        image_runtime=image_real_object,
        schema=schema,
        stack=layer_stack,
        coma_key=coma_stack_key,
        page_key=page_stack_key,
    )


def _work_page():
    work = bpy.context.scene.bmanga_work
    assert work.loaded
    index = int(work.active_page_index)
    assert 0 <= index < len(work.pages)
    page = work.pages[index]
    assert page.detail_loaded
    return work, page


def _ensure_coma(page):
    if len(page.comas):
        return page.comas[0]
    entry = page.comas.add()
    entry.id = "c01"
    entry.coma_id = "c01"
    entry.title = "c01"
    entry.shape_type = "rect"
    entry.rect_x_mm = 20.0
    entry.rect_y_mm = 20.0
    entry.rect_width_mm = 60.0
    entry.rect_height_mm = 80.0
    return entry


def _add_folder(work, folder_id: str, parent_key: str):
    entry = work.layer_folders.add()
    entry.id = folder_id
    entry.title = folder_id
    entry.parent_key = parent_key
    entry.expanded = True
    return entry


def _add_image(image_id: str, parent_key: str):
    entry = bpy.context.scene.bmanga_image_layers.add()
    entry.id = image_id
    entry.title = image_id
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def _add_raster(raster_id: str, parent_key: str):
    entry = bpy.context.scene.bmanga_raster_layers.add()
    entry.id = raster_id
    entry.title = raster_id
    entry.scope = "page"
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def _add_balloon(page, balloon_id: str, parent_key: str):
    entry = page.balloons.add()
    entry.id = balloon_id
    entry.shape = "rect"
    entry.x_mm = 10.0
    entry.y_mm = 20.0
    entry.width_mm = 30.0
    entry.height_mm = 18.0
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def _add_text(page, text_id: str, parent_key: str):
    entry = page.texts.add()
    entry.id = text_id
    entry.body = text_id
    entry.x_mm = 14.0
    entry.y_mm = 24.0
    entry.width_mm = 20.0
    entry.height_mm = 10.0
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def _row(mods, kind: str, key: str):
    rows = mods.stack.sync_layer_stack(
        bpy.context,
        preserve_active_index=True,
    )
    assert rows is not None
    uid = mods.stack.target_uid(kind, key)
    return next(
        row
        for row in rows
        if mods.stack.stack_item_uid(row) == uid
    )


def _assert_stack_parent(mods, kind: str, key: str, parent_key: str) -> None:
    assert str(_row(mods, kind, key).parent_key or "") == parent_key


def _assign(mods, kind: str, key: str, folder_id: str) -> None:
    assert mods.folders.assign_item_to_folder(
        bpy.context,
        SimpleNamespace(kind=kind, key=key),
        folder_id,
    )


def _assert_serialized(mods, work, page, ids: dict[str, str]) -> None:
    work_data = mods.schema.work_to_dict(work)
    page_data = mods.schema.page_to_dict(page)
    folder_ids = {item["id"] for item in work_data["layer_folders"]}
    assert set(ids.values()) <= folder_ids
    raster = next(
        item
        for item in work_data["raster_layers"]
        if item["id"] == "folder_raster"
    )
    assert raster["folderKey"] == ids["coma"]
    balloon = next(
        item
        for item in page_data["balloons"]
        if item["id"] == "folder_balloon"
    )
    text = next(
        item
        for item in page_data["texts"]
        if item["id"] == "folder_text"
    )
    assert balloon["folderKey"] == ids["page"]
    assert text["folderKey"] == ids["page"]


def _assert_delete_preserves_children(mods, work, page_key: str) -> None:
    parent_id = "folder_delete_parent"
    child_id = "folder_delete_child"
    _add_folder(work, parent_id, page_key)
    child = _add_folder(work, child_id, parent_id)
    # このfixtureはPropertyGroupだけの純粋データ契約を検証する。フォルダ
    # Collection未生成の途中状態で画像実体を自動同期すると、Outliner逆同期が
    # 所属を未設定へ戻し得るため、実体同期はUI操作を扱う別テストへ委ねる。
    with mods.image_runtime.suspend_auto_sync():
        direct = _add_image("delete_folder_image", page_key)
        nested = _add_image("delete_child_folder_image", page_key)
        direct.folder_key = parent_id
        nested.folder_key = child_id
        assert mods.folders.remove_folder_preserve_children(work, parent_id)
    assert mods.folders.find_folder(work, parent_id) is None
    child = mods.folders.find_folder(work, child_id)
    assert child is not None and child.parent_key == page_key
    assert direct.folder_key == ""
    assert nested.folder_key == child_id


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_layer_folder_"))
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        assert "FINISHED" in bpy.ops.bmanga.work_new(
            filepath=str(temp_root / "LayerFolder.bmanga")
        )
        assert "FINISHED" in bpy.ops.bmanga.page_add("EXEC_DEFAULT")
        # Both page files are initialized through the supported lifecycle.
        assert "FINISHED" in bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=1)
        assert "FINISHED" in bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)

        mods = _modules()
        work, page = _work_page()
        page_key = mods.page_key(page)
        coma_key = mods.coma_key(page, _ensure_coma(page))
        folder_ids = {
            "page": "folder_page",
            "child": "folder_child",
            "coma": "folder_coma",
            "orphan": "folder_orphan",
        }
        _add_folder(work, folder_ids["page"], page_key)
        _add_folder(work, folder_ids["child"], folder_ids["page"])
        _add_folder(work, folder_ids["coma"], coma_key)
        _add_folder(work, folder_ids["orphan"], "p9999")

        image = _add_image("folder_image", page_key)
        raster = _add_raster("folder_raster", coma_key)
        balloon = _add_balloon(page, "folder_balloon", page_key)
        text = _add_text(page, "folder_text", page_key)

        _assign(mods, "image", image.id, folder_ids["page"])
        _assign(mods, "raster", raster.id, folder_ids["coma"])
        _assign(mods, "balloon", f"{page_key}:{balloon.id}", folder_ids["page"])
        _assign(mods, "text", f"{page_key}:{text.id}", folder_ids["page"])

        _assert_stack_parent(mods, "image", image.id, folder_ids["page"])
        _assert_stack_parent(mods, "raster", raster.id, folder_ids["coma"])
        _assert_stack_parent(
            mods,
            "balloon",
            f"{page_key}:{balloon.id}",
            folder_ids["page"],
        )
        _assert_stack_parent(
            mods,
            "text",
            f"{page_key}:{text.id}",
            folder_ids["page"],
        )

        _assign(mods, "image", image.id, folder_ids["child"])
        assert image.folder_key == folder_ids["child"]
        _assert_stack_parent(mods, "image", image.id, folder_ids["child"])

        old_folder = image.folder_key
        assert not mods.folders.assign_item_to_folder(
            bpy.context,
            SimpleNamespace(kind="image", key=image.id),
            folder_ids["orphan"],
        )
        assert image.folder_key == old_folder

        _assert_serialized(mods, work, page, folder_ids)
        _assert_delete_preserves_children(mods, work, page_key)
        print("BMANGA_LAYER_FOLDER_OK")
    finally:
        if module is not None:
            try:
                module.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
