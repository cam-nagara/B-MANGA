"""Blender 5.2実機: Outliner直削除をDomain削除へ変換する。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import time

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_outliner_direct_delete"
SENTINEL = "BMANGA_OUTLINER_DIRECT_DELETE_OK"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _drain_timers(seconds: float = 0.5) -> None:
    scheduler_module = sys.modules.get(
        f"{PACKAGE}.utils.lifecycle_scheduler"
    )
    if scheduler_module is not None:
        task = scheduler_module.SCHEDULER._tasks.get("history_reconcile")
        if task is not None:
            task.tick()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        bpy.context.view_layer.update()
        time.sleep(0.01)


def _write_png(path: Path) -> None:
    image = bpy.data.images.new("OutlinerDeleteSource", 4, 4, alpha=True)
    image.pixels[:] = [0.2, 0.4, 0.6, 1.0] * 16
    image.file_format = "PNG"
    image.filepath_raw = str(path)
    image.save()


def _image_entry(scene, image_id: str):
    return next(
        (
            entry
            for entry in scene.bmanga_image_layers
            if str(entry.id) == image_id
        ),
        None,
    )


def _folder_entry(work, folder_id: str):
    return next(
        (
            entry
            for entry in work.layer_folders
            if str(entry.id) == folder_id
        ),
        None,
    )


def _text_entry(page, text_id: str):
    return next(
        (
            entry
            for entry in page.texts
            if str(entry.id) == text_id
        ),
        None,
    )


def _coma_entry(page, coma_id: str):
    return next(
        (
            entry
            for entry in page.comas
            if str(entry.coma_id) == coma_id
        ),
        None,
    )


def _push(label: str) -> None:
    from bmanga_outliner_direct_delete.utils import undo_transaction

    assert undo_transaction.push_undo(label)


def _assert_image_state(scene, image_id: str, *, exists: bool) -> None:
    from bmanga_outliner_direct_delete.utils import object_naming

    entry = _image_entry(scene, image_id)
    obj = object_naming.find_object_by_bmanga_id(
        image_id,
        kind="image",
        scene=scene,
    )
    assert (entry is not None) is exists
    assert (obj is not None) is exists


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_outliner_delete_"))
    succeeded = False
    try:
        from bmanga_outliner_direct_delete.utils import (
            image_real_object,
            layer_object_sync,
            layer_stack,
            object_naming,
            outliner_model,
            outliner_change_collector,
            text_real_object,
        )

        work_dir = temp_root / "OutlinerDelete.bmanga"
        assert bpy.ops.bmanga.work_new(filepath=str(work_dir)) == {"FINISHED"}
        assert bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0) == {
            "FINISHED"
        }
        context = bpy.context
        scene = context.scene
        work = scene.bmanga_work
        page = work.pages[int(work.active_page_index)]
        page_id = str(page.id)
        image_path = temp_root / "source.png"
        _write_png(image_path)

        # 現在開いているページの構造Collectionは、標準削除からページ
        # directoryを破壊せずDomain正本から復元する。
        page_path = Path(bpy.data.filepath)
        page_collection = object_naming.find_collection_by_bmanga_id(
            page_id,
            kind="page",
            scene=scene,
        )
        assert page_collection is not None
        other_page_scene = bpy.data.scenes.new("OutlinerDeleteOtherPage")
        other_page_root = bpy.data.collections.new("B-MANGA Other Page")
        other_page_root["bmanga_managed"] = True
        other_page_root["bmanga_kind"] = "root"
        other_page_root["bmanga_id"] = "__root__"
        other_page_scene.collection.children.link(other_page_root)
        other_page_duplicate = bpy.data.collections.new("p0001 Other")
        other_page_duplicate["bmanga_managed"] = True
        other_page_duplicate["bmanga_kind"] = "page"
        other_page_duplicate["bmanga_id"] = page_id
        other_page_root.children.link(other_page_duplicate)
        outliner_change_collector.rebase()
        bpy.data.collections.remove(page_collection, do_unlink=True)
        assert outliner_change_collector.flush(scene, full=True) >= 1
        assert Path(bpy.data.filepath) == page_path
        assert any(str(entry.id) == page_id for entry in work.pages)
        restored_page = object_naming.find_collection_by_bmanga_id(
            page_id,
            kind="page",
            scene=scene,
        )
        assert restored_page is not None
        assert restored_page is not other_page_duplicate
        assert object_naming.find_collection_by_bmanga_id(
            page_id,
            kind="page",
            scene=other_page_scene,
        ) is other_page_duplicate
        assert other_page_duplicate in tuple(other_page_root.children)
        bpy.data.scenes.remove(other_page_scene)
        for collection in (other_page_duplicate, other_page_root):
            if collection.users == 0:
                bpy.data.collections.remove(collection)

        # コマCollectionの標準削除は、UI Operatorの画面pollに依存せず
        # Native transactionを通る共通Commandへ変換する。
        assert bpy.ops.bmanga.coma_add() == {"FINISHED"}
        work = scene.bmanga_work
        page = work.pages[int(work.active_page_index)]
        coma_id = str(page.comas[-1].coma_id)
        coma_key = f"{page_id}:{coma_id}"
        layer_object_sync.mirror_work_to_outliner(scene, work)
        coma_collection = object_naming.find_collection_by_bmanga_id(
            coma_key,
            kind="coma",
            scene=scene,
        )
        assert coma_collection is not None
        outliner_change_collector.rebase()
        bpy.data.collections.remove(coma_collection, do_unlink=True)
        assert outliner_change_collector.flush(scene, full=True) >= 1
        assert Path(bpy.data.filepath) == page_path
        assert _coma_entry(page, coma_id) is None
        assert object_naming.find_collection_by_bmanga_id(
            coma_key,
            kind="coma",
            scene=scene,
        ) is None

        folder_id = "layer_folder_outliner_delete"
        folder = work.layer_folders.add()
        folder.id = folder_id
        folder.title = "Outliner直削除"
        folder.parent_key = page_id
        assert outliner_model.ensure_folder_collection(
            scene,
            folder_id=folder_id,
            title=folder.title,
            parent_kind="page",
            parent_key=page_id,
            z_index=10,
        ) is not None

        image_id = "image_outliner_delete"
        image = scene.bmanga_image_layers.add()
        image.id = image_id
        image.title = "Outliner直削除画像"
        image.filepath = str(image_path)
        image.parent_kind = "page"
        image.parent_key = page_id
        image.folder_key = folder_id
        image.width_mm = 20.0
        image.height_mm = 20.0
        obj = image_real_object.ensure_image_real_object(
            scene=scene,
            entry=image,
            page=page,
        )
        assert obj is not None
        layer_stack.sync_layer_stack(context)
        layer_object_sync.mirror_work_to_outliner(scene, work)
        outliner_change_collector.rebase()

        # 定期full scanとPage Collection復元は現在Sceneだけを対象にする。
        # 別Sceneに同一管理IDがあっても、座標・階層を現在Domainへ逆流させない。
        original_size = (float(image.width_mm), float(image.height_mm))
        other_scene = bpy.data.scenes.new("OutlinerDeleteFullCopy")
        other_root = bpy.data.collections.new("B-MANGA Other")
        other_root["bmanga_managed"] = True
        other_root["bmanga_kind"] = "root"
        other_root["bmanga_id"] = "__root__"
        other_scene.collection.children.link(other_root)
        other_page = bpy.data.collections.new("p0001 Other")
        other_page["bmanga_managed"] = True
        other_page["bmanga_kind"] = "page"
        other_page["bmanga_id"] = page_id
        other_root.children.link(other_page)
        other_obj = obj.copy()
        if obj.data is not None:
            other_obj.data = obj.data.copy()
        other_obj.name = "OtherSceneSameManagedImage"
        other_page.objects.link(other_obj)
        other_obj.scale = (9.0, 7.0, 1.0)
        other_location = tuple(other_obj.location)
        other_scale = tuple(other_obj.scale)
        outliner_change_collector.rebase(scene)
        assert outliner_change_collector.flush(scene, full=True) == 0
        assert (float(image.width_mm), float(image.height_mm)) == original_size
        assert tuple(other_obj.location) == other_location
        assert tuple(other_obj.scale) == other_scale
        assert other_obj in tuple(other_scene.objects)
        assert other_obj not in tuple(scene.objects)
        assert other_obj in tuple(other_page.objects), [
            collection.name for collection in other_obj.users_collection
        ]

        current_page = object_naming.find_collection_by_bmanga_id(
            page_id,
            kind="page",
            scene=scene,
        )
        assert current_page is not None and current_page is not other_page
        assert object_naming.find_collection_by_bmanga_id(
            page_id,
            kind="page",
            scene=other_scene,
        ) is other_page
        assert other_page in tuple(other_root.children)
        assert other_obj in tuple(other_page.objects), [
            collection.name for collection in other_obj.users_collection
        ]
        bpy.data.scenes.remove(other_scene)
        if other_obj.users == 0:
            other_data = other_obj.data
            bpy.data.objects.remove(other_obj)
            if other_data is not None and other_data.users == 0:
                bpy.data.meshes.remove(other_data)
        for collection in (other_page, other_root):
            if collection.users == 0:
                bpy.data.collections.remove(collection)
        outliner_change_collector.rebase(scene)

        # 内部mirrorが同じUIDの実体を差し替える場合は、ユーザー削除へ
        # 誤変換しない。
        with layer_object_sync.suppress_sync():
            bpy.data.objects.remove(obj, do_unlink=True)
            obj = image_real_object.ensure_image_real_object(
                scene=scene,
                entry=image,
                page=page,
            )
        assert obj is not None
        assert outliner_change_collector.flush(scene) == 0
        _assert_image_state(scene, image_id, exists=True)
        outliner_change_collector.rebase()
        _push("Outliner delete baseline")

        # ObjectのBlender標準削除をPropertyGroup/Domain削除へ変換する。
        bpy.data.objects.remove(obj, do_unlink=True)
        assert outliner_change_collector.flush(scene, full=True) >= 1
        _assert_image_state(scene, image_id, exists=False)
        _push("Outliner object deleted")
        assert bpy.ops.ed.undo() == {"FINISHED"}
        _drain_timers()
        scene = bpy.context.scene
        _assert_image_state(scene, image_id, exists=True)
        assert bpy.ops.ed.redo() == {"FINISHED"}
        _drain_timers()
        scene = bpy.context.scene
        _assert_image_state(scene, image_id, exists=False)

        # テキスト実体はbmanga_idが「page_id:text_id」形式でも、対応する
        # Domain nodeを同じ直接削除Commandへ変換する。
        work = scene.bmanga_work
        page = work.pages[int(work.active_page_index)]
        text_id = "text_outliner_delete"
        text = page.texts.add()
        text.id = text_id
        text.title = "Outliner直削除テキスト"
        text.body = "削除"
        text.parent_kind = "page"
        text.parent_key = str(page.id)
        text.x_mm = 20.0
        text.y_mm = 20.0
        text.width_mm = 20.0
        text.height_mm = 12.0
        text_obj = text_real_object.ensure_text_real_object(
            scene=scene,
            entry=text,
            page=page,
        )
        assert text_obj is not None
        assert str(text_obj.get("bmanga_id", "") or "") == (
            f"{page.id}:{text_id}"
        )
        layer_stack.sync_layer_stack(bpy.context)
        outliner_change_collector.rebase()
        bpy.data.objects.remove(text_obj, do_unlink=True)
        assert outliner_change_collector.flush(scene, full=True) >= 1
        assert _text_entry(page, text_id) is None
        assert text_real_object.find_text_object(
            str(page.id),
            text_id,
        ) is None

        # CollectionのBlender標準削除も同じ削除Commandへ変換する。
        folder_collection = object_naming.find_collection_by_bmanga_id(
            folder_id,
            kind="folder",
            scene=scene,
        )
        assert folder_collection is not None
        outliner_change_collector.rebase()
        expected_folder_identity = outliner_change_collector.ManagedIdentity(
            "collection",
            "folder",
            folder_id,
        )
        other_scene = bpy.data.scenes.new("OutlinerDeleteOtherScene")
        duplicate_folder = bpy.data.collections.new(
            "OutlinerDeleteDuplicateFolder"
        )
        duplicate_folder["bmanga_managed"] = True
        duplicate_folder["bmanga_kind"] = "folder"
        duplicate_folder["bmanga_id"] = folder_id
        other_scene.collection.children.link(duplicate_folder)
        assert (
            expected_folder_identity
            in outliner_change_collector.COLLECTOR._inventory
        )
        _push("Outliner collection baseline")
        bpy.data.collections.remove(folder_collection, do_unlink=True)
        assert (
            expected_folder_identity
            not in outliner_change_collector._managed_inventory(scene)
        )
        outliner_change_collector.flush(scene, full=True)
        assert _folder_entry(work, folder_id) is None
        # 別Sceneに同じkind/IDが残っていても、現在Sceneの削除を見逃さない。
        assert duplicate_folder in tuple(other_scene.collection.children)
        bpy.data.scenes.remove(other_scene)
        if duplicate_folder.users == 0:
            bpy.data.collections.remove(duplicate_folder)
        _push("Outliner collection deleted")
        assert bpy.ops.ed.undo() == {"FINISHED"}
        _drain_timers()
        scene = bpy.context.scene
        assert _folder_entry(scene.bmanga_work, folder_id) is not None
        assert object_naming.find_collection_by_bmanga_id(
            folder_id,
            kind="folder",
            scene=scene,
        ) is not None
        assert bpy.ops.ed.redo() == {"FINISHED"}
        _drain_timers()
        scene = bpy.context.scene
        assert _folder_entry(scene.bmanga_work, folder_id) is None
        assert object_naming.find_collection_by_bmanga_id(
            folder_id,
            kind="folder",
            scene=scene,
        ) is None

        # 保存・再読込後も削除対象をDomainから復活させない。
        assert bpy.ops.bmanga.work_save("EXEC_DEFAULT") == {"FINISHED"}
        page_path = Path(bpy.data.filepath)
        assert page_path.name == "page.blend"
        page_path.resolve().relative_to(work_dir.resolve())
        assert bpy.ops.wm.open_mainfile(filepath=str(page_path)) == {
            "FINISHED"
        }
        _drain_timers()
        scene = bpy.context.scene
        _assert_image_state(scene, image_id, exists=False)
        work = scene.bmanga_work
        assert _folder_entry(work, folder_id) is None
        page = work.pages[int(work.active_page_index)]
        assert _coma_entry(page, coma_id) is None
        assert _text_entry(page, text_id) is None

        succeeded = True
        print(SENTINEL, flush=True)
    finally:
        addon.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if succeeded:
            shutil.rmtree(temp_root)
        else:
            print(f"FAILED_TEMP_ROOT={temp_root}", flush=True)


if __name__ == "__main__":
    main()
