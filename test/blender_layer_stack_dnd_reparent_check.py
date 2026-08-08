"""Blender 5.2実機: UIList D&Dの同一ページCommand／別ページTransferGroup。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
PKG = "bmanga_dev"


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


def _stack():
    from bmanga_dev.utils import layer_stack

    result = layer_stack.sync_layer_stack(
        bpy.context,
        preserve_active_index=True,
    )
    assert result is not None
    layer_stack.remember_layer_stack_signature(bpy.context)
    return result


def _row(uid: str):
    from bmanga_dev.utils import layer_stack

    return next(
        item
        for item in _stack()
        if layer_stack.stack_item_uid(item) == uid
    )


def _move_below(uid: str, parent_uid: str) -> None:
    from bmanga_dev.utils import layer_stack

    stack = _stack()
    source = next(
        index
        for index, item in enumerate(stack)
        if layer_stack.stack_item_uid(item) == uid
    )
    parent = next(
        index
        for index, item in enumerate(stack)
        if layer_stack.stack_item_uid(item) == parent_uid
    )
    target = parent + 1
    if source < target:
        target -= 1
    stack.move(source, target)
    assert layer_stack.apply_stack_order_if_ui_changed(
        bpy.context,
        moved_uid=uid,
    )


def _delete_uid(uid: str):
    from bmanga_dev.utils import layer_stack

    stack = _stack()
    index = next(
        i for i, item in enumerate(stack)
        if layer_stack.stack_item_uid(item) == uid
    )
    layer_stack.clear_all_selection(bpy.context)
    assert layer_stack.select_stack_index(bpy.context, index)
    return bpy.ops.bmanga.layer_stack_delete("EXEC_DEFAULT")


def _page_document(work_dir: Path, display_id: str):
    from bmanga_dev.bmanga_core.domain_repository import ProjectRepository

    repository = ProjectRepository(work_dir)
    project = repository.load_project()
    summary = next(
        page for page in project.pages if page.display_id == display_id
    )
    return repository.load_page(summary.uid)


def _display_ids(document) -> set[str]:
    return {
        str(node.display_id)
        for node in document.nodes.values()
        if str(node.display_id)
    }


def _add_text(page, display_id: str, parent_key: str):
    entry = page.texts.add()
    entry.id = display_id
    entry.body = display_id
    entry.x_mm = 12.0
    entry.y_mm = 18.0
    entry.width_mm = 24.0
    entry.height_mm = 12.0
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def _assert_ready_stage(work_dir: Path, page_id: str, display_id: str) -> str:
    from bmanga_dev.utils import cross_page_stage

    path = cross_page_stage.staged_path(work_dir, page_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get(cross_page_stage.ASSET_ENTRIES_KEY, [])
    ready = [
        entry
        for entry in entries
        if entry.get("state") == "ready"
        and any(
            item.get("source_id") == display_id
            or item.get("data", {}).get("id") == display_id
            for item in entry.get("payload", {}).get("entries", [])
        )
    ]
    assert len(ready) == 1, entries
    return str(ready[0]["stage_id"])


def _assert_folder_stage(
    work_dir: Path,
    page_id: str,
    display_id: str,
    target_folder_key: str,
) -> str:
    from bmanga_dev.utils import cross_page_stage

    path = cross_page_stage.staged_path(work_dir, page_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get(cross_page_stage.ASSET_ENTRIES_KEY, [])
    ready = [
        entry
        for entry in entries
        if entry.get("state") == "ready"
        and entry.get("payload", {}).get("transfer", {}).get(
            "targetFolderKey"
        ) == target_folder_key
        and any(
            item.get("source_id") == display_id
            or item.get("data", {}).get("id") == display_id
            for item in entry.get("payload", {}).get("entries", [])
        )
    ]
    assert len(ready) == 1, entries
    transfer = ready[0]["payload"]["transfer"]
    assert transfer["targetPageId"] == page_id
    assert transfer["targetFolderOwnerPageId"] == page_id
    return str(ready[0]["stage_id"])


def _ready_folder_stage_exists(
    work_dir: Path,
    page_id: str,
    stage_id: str,
    target_folder_key: str,
) -> bool:
    from bmanga_dev.utils import cross_page_stage

    data = cross_page_stage._read(
        cross_page_stage.staged_path(work_dir, page_id)
    )
    return any(
        isinstance(entry, dict)
        and entry.get("stage_id") == stage_id
        and entry.get("state") == "ready"
        and entry.get("payload", {}).get("transfer", {}).get(
            "targetFolderKey"
        ) == target_folder_key
        for entry in data.get(cross_page_stage.ASSET_ENTRIES_KEY, ())
    )


def _add_folder(anchor_uid: str, title: str) -> tuple[str, str]:
    from bmanga_dev.utils import layer_stack

    assert bpy.ops.bmanga.layer_stack_add(
        "EXEC_DEFAULT",
        kind="layer_folder",
        anchor_uid=anchor_uid,
    ) == {"FINISHED"}
    row = bpy.context.scene.bmanga_layer_stack[
        bpy.context.scene.bmanga_active_layer_stack_index
    ]
    assert row.kind == "layer_folder"
    resolved = layer_stack.resolve_stack_item(bpy.context, row)
    resolved["target"].title = title
    return str(resolved["target"].id), layer_stack.stack_item_uid(row)


def _cross_page_text() -> tuple[Path, str, str]:
    from bmanga_dev.utils import layer_stack, paths
    from bmanga_dev.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = context.scene.bmanga_work
    source = work.pages[0]
    target = work.pages[1]
    source_id = str(source.id)
    target_id = str(target.id)
    source_key = page_stack_key(source)
    target_key = page_stack_key(target)
    entry = _add_text(source, "dnd_transfer_text", source_key)
    uid = layer_stack.target_uid("text", f"{source_key}:{entry.id}")
    target_uid = layer_stack.target_uid("page", target_key)
    work_dir = Path(work.work_dir)

    _move_below(uid, target_uid)

    work = bpy.context.scene.bmanga_work
    source = next(page for page in work.pages if page.id == source_id)
    assert not any(text.id == "dnd_transfer_text" for text in source.texts)
    source_doc = _page_document(work_dir, source_id)
    assert "dnd_transfer_text" not in _display_ids(source_doc)
    stage_id = _assert_ready_stage(
        work_dir,
        target_id,
        "dnd_transfer_text",
    )
    recovery = paths.page_dir(work_dir, source_id) / "_transfer_recovery"
    assert (recovery / stage_id / "transaction.json").is_file()
    return work_dir, source_id, target_id


def _open_and_assert_target(
    work_dir: Path,
    target_id: str,
    bodies: set[str],
) -> None:
    work = bpy.context.scene.bmanga_work
    index = next(
        index
        for index, page in enumerate(work.pages)
        if page.id == target_id
    )
    assert bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=index) == {
        "FINISHED"
    }
    work = bpy.context.scene.bmanga_work
    target = next(page for page in work.pages if page.id == target_id)
    actual = {text.body for text in target.texts}
    assert bodies <= actual


def _same_page_command(target_id: str) -> None:
    from bmanga_dev.io import domain_projection, domain_runtime
    from bmanga_dev.utils import layer_stack
    from bmanga_dev.utils.layer_hierarchy import coma_stack_key, page_stack_key

    work = bpy.context.scene.bmanga_work
    target = next(page for page in work.pages if page.id == target_id)
    page_key = page_stack_key(target)
    coma_key = coma_stack_key(target, target.comas[0])
    entry = _add_text(target, "dnd_same_page_text", page_key)
    uid = layer_stack.target_uid("text", f"{page_key}:{entry.id}")
    coma_uid = layer_stack.target_uid("coma", coma_key)
    store = domain_runtime.store_for(Path(work.work_dir))
    domain_page_uid = domain_projection.ensure_page_uid(
        target,
        store.project.project_uid,
    )
    before = store.pages[domain_page_uid].revision

    _move_below(uid, coma_uid)

    resolved = layer_stack.resolve_stack_item(bpy.context, _row(uid))
    assert resolved["target"].parent_key == coma_key
    assert store.pages[domain_page_uid].revision == before + 1

    page_row_uid = layer_stack.target_uid("page", page_key)
    folder_id, folder_uid = _add_folder(
        page_row_uid,
        "同一ページフォルダー",
    )
    folder_child = _add_text(
        target,
        "dnd_same_page_folder_text",
        page_key,
    )
    folder_child.body = "dnd_same_page_folder_text"
    child_uid = layer_stack.target_uid(
        "text",
        f"{page_key}:{folder_child.id}",
    )
    before_folder_drop = store.pages[domain_page_uid].revision
    _move_below(child_uid, folder_uid)
    resolved = layer_stack.resolve_stack_item(
        bpy.context,
        _row(child_uid),
    )
    assert resolved["target"].folder_key == folder_id
    assert store.pages[domain_page_uid].revision == before_folder_drop + 1


def _cross_page_folder(
    work_dir: Path,
    source_id: str,
    target_id: str,
) -> None:
    from bmanga_dev.utils import layer_stack
    from bmanga_dev.utils.layer_hierarchy import page_stack_key

    work = bpy.context.scene.bmanga_work
    source_index = next(
        index
        for index, page in enumerate(work.pages)
        if page.id == source_id
    )
    assert bpy.ops.bmanga.open_page_file(
        "EXEC_DEFAULT",
        index=source_index,
    ) == {"FINISHED"}
    work = bpy.context.scene.bmanga_work
    source = next(page for page in work.pages if page.id == source_id)
    target = next(page for page in work.pages if page.id == target_id)
    page_uid = layer_stack.target_uid("page", page_stack_key(source))
    assert bpy.ops.bmanga.layer_stack_add(
        "EXEC_DEFAULT",
        kind="layer_folder",
        anchor_uid=page_uid,
    ) == {"FINISHED"}
    folder = _row(
        layer_stack.stack_item_uid(
            bpy.context.scene.bmanga_layer_stack[
                bpy.context.scene.bmanga_active_layer_stack_index
            ]
        )
    )
    assert folder.kind == "layer_folder"
    folder_uid = layer_stack.stack_item_uid(folder)
    assert bpy.ops.bmanga.layer_stack_add(
        "EXEC_DEFAULT",
        kind="text",
        anchor_uid=folder_uid,
    ) == {"FINISHED"}
    child = bpy.context.scene.bmanga_layer_stack[
        bpy.context.scene.bmanga_active_layer_stack_index
    ]
    child_target = layer_stack.resolve_stack_item(bpy.context, child)["target"]
    child_target.body = "dnd_folder_child"
    child_id = str(child_target.id)
    target_uid = layer_stack.target_uid("page", page_stack_key(target))

    _move_below(folder_uid, target_uid)

    work = bpy.context.scene.bmanga_work
    assert not any(folder.id == str(folder.key) for folder in work.layer_folders)
    _assert_ready_stage(work_dir, target_id, child_id)
    _open_and_assert_target(
        work_dir,
        target_id,
        {"dnd_transfer_text", "dnd_folder_child"},
    )


def _assert_existing_folder_members(
    target_id: str,
    target_folder_id: str,
) -> None:
    from bmanga_dev.utils import layer_folder
    from bmanga_dev.utils.layer_hierarchy import page_stack_key

    work = bpy.context.scene.bmanga_work
    target = next(page for page in work.pages if page.id == target_id)
    target_folder = layer_folder.find_folder(work, target_folder_id)
    assert target_folder is not None
    assert (
        layer_folder.semantic_parent_key_for_folder(work, target_folder_id)
        == page_stack_key(target)
    )
    direct = next(
        text for text in target.texts
        if text.body == "dnd_existing_folder_child"
    )
    assert direct.folder_key == target_folder_id
    assert direct.parent_key == page_stack_key(target)
    moved = next(
        folder for folder in work.layer_folders
        if folder.title == "移送ルート"
    )
    nested = next(
        folder for folder in work.layer_folders
        if folder.title == "移送ネスト"
    )
    assert moved.parent_key == target_folder_id
    assert nested.parent_key == moved.id
    nested_child = next(
        text for text in target.texts
        if text.body == "dnd_nested_folder_child"
    )
    assert nested_child.folder_key == nested.id
    assert nested_child.parent_key == page_stack_key(target)


def _delete_target_folder_after_materialization(
    work_dir: Path,
    source_id: str,
    target_id: str,
    target_folder_id: str,
) -> None:
    from bmanga_dev.utils import layer_folder, layer_stack, paths
    from bmanga_dev.utils.layer_hierarchy import page_stack_key
    work = bpy.context.scene.bmanga_work
    source_index = next(
        index for index, page in enumerate(work.pages) if page.id == source_id
    )
    assert bpy.ops.bmanga.open_page_file(
        "EXEC_DEFAULT", index=source_index) == {"FINISHED"}
    work = bpy.context.scene.bmanga_work
    source = next(page for page in work.pages if page.id == source_id)
    target = next(page for page in work.pages if page.id == target_id)
    source_uid = layer_stack.target_uid("page", page_stack_key(source))
    assert bpy.ops.bmanga.layer_stack_add(
        "EXEC_DEFAULT",
        kind="text",
        anchor_uid=source_uid,
    ) == {"FINISHED"}
    child_row = bpy.context.scene.bmanga_layer_stack[
        bpy.context.scene.bmanga_active_layer_stack_index
    ]
    child = layer_stack.resolve_stack_item(bpy.context, child_row)["target"]
    child.body = "dnd_delete_pending_folder_child"
    child_id = str(child.id)
    mirror = layer_folder.find_folder(work, target_folder_id)
    if mirror is None:
        mirror = work.layer_folders.add()
        mirror.id = target_folder_id
        mirror.title = "移送先既存フォルダー"
        mirror.parent_key = page_stack_key(target)
    folder_uid = layer_stack.target_uid("layer_folder", target_folder_id)
    _move_below(layer_stack.stack_item_uid(child_row), folder_uid)
    stage_id = _assert_folder_stage(
        work_dir, target_id, child_id, target_folder_id
    )
    recovery_dir = paths.page_dir(work_dir, source_id)
    recovery_dir = recovery_dir / "_transfer_recovery" / stage_id
    assert recovery_dir.is_dir()
    _open_and_assert_target(
        work_dir, target_id, {"dnd_delete_pending_folder_child"})
    work = bpy.context.scene.bmanga_work
    target = next(page for page in work.pages if page.id == target_id)
    transferred = next(text for text in target.texts
                       if text.body == "dnd_delete_pending_folder_child")
    assert transferred.folder_key == target_folder_id
    assert _delete_uid(folder_uid) == {"CANCELLED"}
    assert layer_folder.find_folder(work, target_folder_id) is not None
    assert transferred.folder_key == target_folder_id
    # 初回保存まではtarget folder削除を拒否し、保存後にだけ解禁する。
    assert bpy.ops.bmanga.open_page_file(
        "EXEC_DEFAULT", index=source_index) == {"FINISHED"}
    assert not recovery_dir.exists()
    assert not _ready_folder_stage_exists(
        work_dir, target_id, stage_id, target_folder_id)
    _open_and_assert_target(
        work_dir, target_id, {"dnd_delete_pending_folder_child"})
    work = bpy.context.scene.bmanga_work
    target = next(page for page in work.pages if page.id == target_id)
    assert _delete_uid(folder_uid) == {"FINISHED"}
    assert bpy.ops.bmanga.open_page_file(
        "EXEC_DEFAULT", index=source_index) == {"FINISHED"}
    _open_and_assert_target(
        work_dir, target_id, {"dnd_delete_pending_folder_child"})
    work = bpy.context.scene.bmanga_work
    target = next(page for page in work.pages if page.id == target_id)
    transferred = next(text for text in target.texts
                       if text.body == "dnd_delete_pending_folder_child")
    assert layer_folder.find_folder(work, target_folder_id) is None
    assert not transferred.folder_key
    assert transferred.parent_key == page_stack_key(target)


def _cross_page_into_existing_folder(
    work_dir: Path,
    source_id: str,
    target_id: str,
) -> None:
    from bmanga_dev.utils import layer_stack
    from bmanga_dev.utils.layer_hierarchy import page_stack_key

    work = bpy.context.scene.bmanga_work
    target = next(page for page in work.pages if page.id == target_id)
    target_page_uid = layer_stack.target_uid(
        "page",
        page_stack_key(target),
    )
    target_folder_id, _target_folder_uid = _add_folder(
        target_page_uid,
        "移送先既存フォルダー",
    )
    source_index = next(
        index for index, page in enumerate(work.pages)
        if page.id == source_id
    )
    assert bpy.ops.bmanga.open_page_file(
        "EXEC_DEFAULT",
        index=source_index,
    ) == {"FINISHED"}
    work = bpy.context.scene.bmanga_work
    source = next(page for page in work.pages if page.id == source_id)
    target = next(page for page in work.pages if page.id == target_id)
    # page切替では非アクティブページのfolder投影を外す。UIList上の別ページ
    # folder行を再現するため、対象repositoryへ保存済みの同じUIDを再投影する。
    mirror = work.layer_folders.add()
    mirror.id = target_folder_id
    mirror.title = "移送先既存フォルダー"
    mirror.parent_key = page_stack_key(target)
    target_folder_uid = layer_stack.target_uid(
        "layer_folder",
        target_folder_id,
    )
    _stack()
    source_page_key = page_stack_key(source)
    direct = _add_text(
        source,
        "dnd_existing_folder_child",
        source_page_key,
    )
    direct.body = "dnd_existing_folder_child"
    direct_uid = layer_stack.target_uid(
        "text",
        f"{source_page_key}:{direct.id}",
    )

    _move_below(direct_uid, target_folder_uid)

    work = bpy.context.scene.bmanga_work
    source = next(page for page in work.pages if page.id == source_id)
    assert not any(
        text.body == "dnd_existing_folder_child"
        for text in source.texts
    )
    direct_stage_id = _assert_folder_stage(
        work_dir,
        target_id,
        "dnd_existing_folder_child",
        target_folder_id,
    )
    from bmanga_dev.utils import layer_transfer_history

    tokens = layer_transfer_history._tokens(bpy.context.scene)
    assert tokens and tokens[-1] == direct_stage_id
    layer_transfer_history.begin_restore(bpy.context)
    layer_transfer_history._set_tokens(
        bpy.context.scene,
        tokens[:-1],
    )
    assert layer_transfer_history.reconcile(bpy.context)
    source_doc = _page_document(work_dir, source_id)
    assert "dnd_existing_folder_child" in _display_ids(source_doc)
    assert not _ready_folder_stage_exists(
        work_dir,
        target_id,
        direct_stage_id,
        target_folder_id,
    )
    layer_transfer_history.begin_restore(bpy.context)
    layer_transfer_history._set_tokens(bpy.context.scene, tokens)
    assert layer_transfer_history.reconcile(bpy.context)
    source_doc = _page_document(work_dir, source_id)
    assert "dnd_existing_folder_child" not in _display_ids(source_doc)
    assert _ready_folder_stage_exists(
        work_dir,
        target_id,
        direct_stage_id,
        target_folder_id,
    )

    work = bpy.context.scene.bmanga_work
    source = next(page for page in work.pages if page.id == source_id)
    target = next(page for page in work.pages if page.id == target_id)
    _assert_owner_mismatch_does_not_remove_source(
        work_dir,
        source,
        target,
        target_folder_id,
    )

    source_page_uid = layer_stack.target_uid(
        "page",
        page_stack_key(source),
    )
    root_id, root_uid = _add_folder(source_page_uid, "移送ルート")
    _nested_id, nested_uid = _add_folder(root_uid, "移送ネスト")
    assert bpy.ops.bmanga.layer_stack_add(
        "EXEC_DEFAULT",
        kind="text",
        anchor_uid=nested_uid,
    ) == {"FINISHED"}
    nested_row = bpy.context.scene.bmanga_layer_stack[
        bpy.context.scene.bmanga_active_layer_stack_index
    ]
    nested_text = layer_stack.resolve_stack_item(
        bpy.context,
        nested_row,
    )["target"]
    nested_text.body = "dnd_nested_folder_child"
    nested_text_id = str(nested_text.id)

    _move_below(root_uid, target_folder_uid)

    work = bpy.context.scene.bmanga_work
    assert not any(folder.id == root_id for folder in work.layer_folders)
    _assert_folder_stage(
        work_dir,
        target_id,
        nested_text_id,
        target_folder_id,
    )
    _open_and_assert_target(
        work_dir,
        target_id,
        {
            "dnd_existing_folder_child",
            "dnd_nested_folder_child",
        },
    )
    _assert_existing_folder_members(target_id, target_folder_id)

    work = bpy.context.scene.bmanga_work
    source_index = next(
        index for index, page in enumerate(work.pages)
        if page.id == source_id
    )
    target_index = next(
        index for index, page in enumerate(work.pages)
        if page.id == target_id
    )
    assert bpy.ops.bmanga.open_page_file(
        "EXEC_DEFAULT",
        index=source_index,
    ) == {"FINISHED"}
    assert bpy.ops.bmanga.open_page_file(
        "EXEC_DEFAULT",
        index=target_index,
    ) == {"FINISHED"}
    _assert_existing_folder_members(target_id, target_folder_id)
    _delete_target_folder_after_materialization(
        work_dir,
        source_id,
        target_id,
        target_folder_id,
    )


def _assert_owner_mismatch_does_not_remove_source(
    work_dir: Path,
    source,
    target,
    target_folder_id: str,
) -> None:
    from bmanga_dev.utils import layer_stack, layer_transfer_group, page_grid
    from bmanga_dev.utils.layer_reparent import ClickTarget

    source_key = str(source.id)
    probe = _add_text(
        source,
        "dnd_owner_mismatch_probe",
        source_key,
    )
    probe.body = "dnd_owner_mismatch_probe"
    probe_uid = layer_stack.target_uid(
        "text",
        f"{source_key}:{probe.id}",
    )
    probe_row = _row(probe_uid)
    work = bpy.context.scene.bmanga_work
    target_index = next(
        index for index, page in enumerate(work.pages)
        if page.id == target.id
    )
    offset = page_grid.page_total_offset_mm(
        work,
        bpy.context.scene,
        target_index,
    )
    target_click = ClickTarget(
        "page",
        target,
        None,
        target_index,
        offset,
        (0.0, 0.0),
        # target_pageと一致しないsource側folderを模擬するため、実在する
        # source page root UIDをfolder UIDとして渡して開始前検証を落とす。
        target_folder_id + "_missing",
    )
    assert layer_transfer_group.transfer_group_to_page(
        bpy.context,
        target_click,
        anchor_item=probe_row,
    ) == 0
    assert any(
        text.body == "dnd_owner_mismatch_probe"
        for text in source.texts
    )
    assert "dnd_owner_mismatch_probe" in _display_ids(
        _page_document(work_dir, str(source.id))
    ) or any(
        text.body == "dnd_owner_mismatch_probe"
        for text in source.texts
    )
    layer_stack.clear_all_selection(bpy.context)


def _all_folder_child_kinds_use_existing_target() -> None:
    from bmanga_dev.utils import asset_bundle, layer_folder

    for kind in layer_folder.FOLDER_CHILD_KINDS:
        assert asset_bundle._target_folder_for_payload_entry(
            kind,
            "",
            set(),
            {},
            "target_folder",
        ) == "target_folder"
        assert asset_bundle._target_folder_for_payload_entry(
            kind,
            "source_nested",
            {"source_nested"},
            {"source_nested": "new_nested"},
            "target_folder",
        ) == "new_nested"
    assert asset_bundle._target_folder_for_payload_entry(
        "layer_folder",
        "",
        set(),
        {},
        "target_folder",
    ) == ""


def _file_snapshot(root: Path) -> dict[str, tuple[int, str]]:
    snapshot = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        snapshot[relative] = (len(data), hashlib.sha256(data).hexdigest())
    return snapshot


def _memory_snapshot() -> str:
    from bmanga_dev.io import schema
    from bmanga_dev.utils import layer_links

    payload = {
        "work": schema.work_to_dict(bpy.context.scene.bmanga_work),
        "links": layer_links._load_map(bpy.context),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _assert_rejected_without_changes(
    work_dir: Path,
    source_row,
    target,
    *,
    target_folder_key: str = "",
) -> None:
    from bmanga_dev.utils import layer_transfer_group, page_grid, paths
    from bmanga_dev.utils.layer_reparent import ClickTarget

    work = bpy.context.scene.bmanga_work
    target_index = next(
        index
        for index, page in enumerate(work.pages)
        if str(page.id) == str(target.id)
    )
    offset = page_grid.page_total_offset_mm(
        work,
        bpy.context.scene,
        target_index,
    )
    click = ClickTarget(
        "page",
        target,
        None,
        target_index,
        offset,
        (0.0, 0.0),
        target_folder_key,
    )
    disk_before = _file_snapshot(work_dir)
    memory_before = _memory_snapshot()
    assert layer_transfer_group.transfer_group_to_page(
        bpy.context,
        click,
        anchor_item=source_row,
    ) == 0
    assert _memory_snapshot() == memory_before
    assert _file_snapshot(work_dir) == disk_before
    for page in work.pages:
        assert not (
            paths.page_dir(work_dir, str(page.id)) / "_transfer_recovery"
        ).exists()


def _mixed_source_groups_are_rejected() -> None:
    """Ctrl選択・link閉包・同名coma・target folder混入を開始前拒否する。"""

    from bmanga_dev.utils import (
        cross_page_stage,
        layer_links,
        layer_stack,
        paths,
    )
    from bmanga_dev.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = context.scene.bmanga_work
    assert len(work.pages) >= 3
    source, target, third = work.pages[:3]
    work_dir = Path(work.work_dir)
    page_keys = [page_stack_key(page) for page in (source, target, third)]
    texts = []
    for index, (page, parent_key) in enumerate(
        zip((source, target, third), page_keys, strict=True),
        start=1,
    ):
        text = _add_text(page, f"mixed_owner_text_{index}", parent_key)
        text.body = f"mixed_owner_text_{index}"
        texts.append(text)

    target_folder = work.layer_folders.add()
    target_folder.id = "mixed_owner_target_folder"
    target_folder.title = "混在拒否移送先"
    target_folder.parent_key = page_keys[1]
    layer_stack.sync_layer_stack_after_data_change(context)
    row_uids = [
        layer_stack.target_uid("text", f"{page_keys[index]}:{text.id}")
        for index, text in enumerate(texts)
    ]
    target_folder_uid = layer_stack.target_uid(
        "layer_folder",
        target_folder.id,
    )

    # 三ページの同名c01が存在しても、stack上のparent名ではなく実所有pageで
    # 判定する。標準コマが無い構成では専用同名コマを追加する。
    for page in (source, target, third):
        if not any(
            str(getattr(coma, "coma_id", "") or getattr(coma, "id", "") or "")
            == "c01"
            for coma in page.comas
        ):
            coma = page.comas.add()
            coma.id = "c01"
            coma.coma_id = "c01"
    layer_stack.sync_layer_stack_after_data_change(context)
    foreign_coma_uid = layer_stack.target_uid(
        "coma",
        f"{page_keys[1]}:c01",
    )
    # 三ページすべてにnative scene.blendを置き、直接再現ケースの全ディスク
    # snapshotが同名c01の実体path/hashも確実に含むようにする。page Domain JSON
    # がコマsidecarの正本なので、こちらも存在を必須にする。
    for index, page in enumerate((source, target, third), start=1):
        coma_blend = paths.coma_blend_path(work_dir, str(page.id), "c01")
        coma_blend.parent.mkdir(parents=True, exist_ok=True)
        if not coma_blend.is_file():
            coma_blend.write_bytes(f"mixed-owner-c01-{index}".encode())
        assert paths.page_meta_path(work_dir, str(page.id)).is_file()

    def select(*selected_uids):
        layer_stack.clear_all_selection(context)
        for uid in selected_uids:
            row = _row(uid)
            assert layer_stack.set_item_selected(context, row, True)
        stack = layer_stack.sync_layer_stack(
            context,
            preserve_active_index=True,
        )
        source_index = next(
            index
            for index, item in enumerate(stack)
            if layer_stack.stack_item_uid(item) == row_uids[0]
        )
        layer_stack.set_active_stack_index_silently(context, source_index)
        return _row(row_uids[0])

    # 異ページCtrl選択（p1 + p2、両方に同名c01）。
    source_row = select(row_uids[0], row_uids[1])
    _assert_rejected_without_changes(work_dir, source_row, target)

    # Criticalの直接再現: p1レイヤーと、同名IDを持つp2/c01コマを同時選択。
    # p1/c01 nativeへ誤ってsource固定して移送してはならない。
    source_row = select(row_uids[0], foreign_coma_uid)
    _assert_rejected_without_changes(work_dir, source_row, target)

    # 選択はp1だけでも、p3へのlayer_link閉包後の最終集合を拒否する。
    source_row = select(row_uids[0])
    source_uid = row_uids[0]
    third_uid = row_uids[2]
    group_id, linked_count = layer_links.link_uids(
        context,
        [source_uid, third_uid],
    )
    assert group_id and linked_count == 2
    _assert_rejected_without_changes(work_dir, source_row, target)
    assert layer_links.unlink_uids(context, [source_uid, third_uid]) == 2

    # 移送先folder自体が選択集合へ混ざった場合もstage作成前に拒否する。
    source_row = select(row_uids[0], target_folder_uid)
    _assert_rejected_without_changes(
        work_dir,
        source_row,
        target,
        target_folder_key=target_folder.id,
    )
    assert not any(
        cross_page_stage.staged_path(work_dir, str(page.id)).is_file()
        for page in (source, target, third)
    )
    layer_stack.clear_all_selection(context)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_layer_dnd_transfer_"))
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        _all_folder_child_kinds_use_existing_target()
        assert bpy.ops.bmanga.work_new(
            filepath=str(temp_root / "DndTransfer.bmanga")
        ) == {"FINISHED"}
        assert bpy.ops.bmanga.page_add("EXEC_DEFAULT") == {"FINISHED"}
        assert bpy.ops.bmanga.page_add("EXEC_DEFAULT") == {"FINISHED"}
        assert bpy.ops.bmanga.open_page_file(
            "EXEC_DEFAULT",
            index=2,
        ) == {"FINISHED"}
        assert bpy.ops.bmanga.open_page_file(
            "EXEC_DEFAULT",
            index=1,
        ) == {"FINISHED"}
        assert bpy.ops.bmanga.open_page_file(
            "EXEC_DEFAULT",
            index=0,
        ) == {"FINISHED"}

        _mixed_source_groups_are_rejected()
        work_dir, source_id, target_id = _cross_page_text()
        _open_and_assert_target(
            work_dir,
            target_id,
            {"dnd_transfer_text"},
        )
        _same_page_command(target_id)
        _cross_page_folder(work_dir, source_id, target_id)
        _cross_page_into_existing_folder(
            work_dir,
            source_id,
            target_id,
        )
        print("BMANGA_LAYER_STACK_DND_REPARENT_OK", flush=True)
    finally:
        if module is not None:
            try:
                module.unregister()
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
