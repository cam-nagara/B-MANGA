"""Blender 5.2実機用: Phase 5 Layer Commandの一取引・復元・永続化検証。"""

from __future__ import annotations

import copy
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
PKG = "bmanga_dev_phase5_layer_command"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _modules():
    from bmanga_dev_phase5_layer_command.core.work import get_work
    from bmanga_dev_phase5_layer_command.io import domain_projection, domain_runtime, schema
    from bmanga_dev_phase5_layer_command.utils import layer_links, layer_object_model
    from bmanga_dev_phase5_layer_command.utils import layer_stack

    return SimpleNamespace(
        get_work=get_work,
        projection=domain_projection,
        runtime=domain_runtime,
        schema=schema,
        links=layer_links,
        objects=layer_object_model,
        stack=layer_stack,
    )


def _work_page(mods):
    work = mods.get_work(bpy.context)
    assert work is not None and work.loaded
    index = int(work.active_page_index)
    assert 0 <= index < len(work.pages)
    return work, work.pages[index]


def _domain(mods):
    work, page = _work_page(mods)
    store = mods.runtime.store_for(Path(work.work_dir))
    page_uid = mods.projection.ensure_page_uid(page, store.project.project_uid)
    document = store.pages.get(page_uid)
    assert document is not None
    document.validate()
    return store, document


def _formal_snapshot(mods):
    work, page = _work_page(mods)
    _store, document = _domain(mods)
    native = {
        kind: sorted(
            mods.objects.stable_id(obj)
            for obj in mods.objects.iter_layer_objects(kind)
            if mods.objects.parent_key(obj).split(":", 1)[0] == page.id
        )
        for kind in ("gp", "effect")
    }
    return {
        "work": copy.deepcopy(mods.schema.work_to_dict(work)),
        "page": copy.deepcopy(mods.schema.page_to_dict(page)),
        "links": copy.deepcopy(mods.links._load_map(bpy.context)),
        "native": native,
        "stack": [
            mods.stack.stack_item_uid(row)
            for row in bpy.context.scene.bmanga_layer_stack
        ],
        "project": copy.deepcopy(_domain(mods)[0].project.to_dict()),
        "domain": copy.deepcopy(document.to_dict()),
    }


def _runtime_object_ids(mods):
    """復元で完全一致が必要な非Native実Object IDを固定する。"""

    kinds = {"balloon", "text", "image", "image_path", "raster", "fill"}
    return sorted(
        (
            str(obj.get("bmanga_kind", "") or ""),
            mods.objects.stable_id(obj),
        )
        for obj in bpy.context.scene.objects
        if str(obj.get("bmanga_kind", "") or "") in kinds
        and mods.objects.stable_id(obj)
    )


def _assert_projection_matches_domain(mods):
    work, page = _work_page(mods)
    _store, document = _domain(mods)
    candidate = mods.projection.page_document_from_projection(
        work, page, context=bpy.context, preserve_document=document
    )
    candidate_dict = candidate.to_dict()
    document_dict = document.to_dict()
    if candidate_dict != document_dict:
        print("PHASE5_PROJECTION_DIFF:", _first_diff(document_dict, candidate_dict))
    assert candidate_dict == document_dict
    assert len({uid for values in document.children.values() for uid in values}) == len(document.nodes) - 1


def _first_diff(expected, actual, path="root"):
    if type(expected) is not type(actual):
        return path, expected, actual
    if isinstance(expected, dict):
        for key in sorted(set(expected) | set(actual)):
            if key not in expected or key not in actual:
                return f"{path}.{key}", expected.get(key), actual.get(key)
            found = _first_diff(expected[key], actual[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(expected, list):
        for index, (left, right) in enumerate(zip(expected, actual)):
            found = _first_diff(left, right, f"{path}[{index}]")
            if found:
                return found
        return None if len(expected) == len(actual) else (path, expected, actual)
    return None if expected == actual else (path, expected, actual)


def _stack_rows(mods):
    stack = mods.stack.sync_layer_stack(bpy.context, preserve_active_index=True)
    assert stack is not None
    return stack


def _active_row(mods):
    stack = _stack_rows(mods)
    index = int(bpy.context.scene.bmanga_active_layer_stack_index)
    assert 0 <= index < len(stack)
    return stack[index]


def _select_uid(mods, uid: str):
    stack = _stack_rows(mods)
    for index, row in enumerate(stack):
        if mods.stack.stack_item_uid(row) == uid:
            assert mods.stack.select_stack_index(bpy.context, index)
            return row
    raise AssertionError(f"stack uid not found: {uid}")


def _row_by_uid(mods, uid: str):
    for row in _stack_rows(mods):
        if mods.stack.stack_item_uid(row) == uid:
            return row
    raise AssertionError(f"stack uid not found: {uid}")


def _select_only(mods, uids):
    wanted = set(uids)
    stack = _stack_rows(mods)
    active = -1
    for index, row in enumerate(stack):
        uid = mods.stack.stack_item_uid(row)
        mods.stack.set_item_selected(bpy.context, row, uid in wanted)
        if active < 0 and uid in wanted:
            active = index
    assert active >= 0
    mods.stack.set_active_stack_index_silently(bpy.context, active)


def _operator_once(mods, operator, label: str):
    store, before = _domain(mods)
    project_revision = store.project.revision
    result = operator()
    assert "FINISHED" in result, (label, result)
    after_store, after = _domain(mods)
    assert after.revision == before.revision + 1, (label, before.revision, after.revision)
    assert after_store.project.revision in {project_revision, project_revision + 1}
    _assert_projection_matches_domain(mods)
    return _active_row(mods)


def _project_operator_once(mods, operator, label: str):
    store, before_page = _domain(mods)
    before_project = store.project.revision
    result = operator()
    assert "FINISHED" in result, (label, result)
    after_store, after_page = _domain(mods)
    assert after_store.project.revision == before_project + 1
    assert after_page.revision == before_page.revision
    _assert_projection_matches_domain(mods)
    return _active_row(mods)


def _add_layers(mods):
    rows = {}
    _work, page = _work_page(mods)
    page_uid = mods.stack.target_uid("page", page.id)
    for kind in (
        "layer_folder",
        "balloon",
        "text",
        "fill",
        "image_path",
        "raster",
        "gp",
        "effect",
    ):
        def add_layer(kind=kind):
            kwargs = {"kind": kind, "anchor_uid": page_uid}
            if kind == "raster":
                kwargs["dpi"] = 30
            return bpy.ops.bmanga.layer_stack_add("EXEC_DEFAULT", **kwargs)

        row = _operator_once(
            mods,
            add_layer,
            f"add {kind}",
        )
        rows[kind] = mods.stack.stack_item_uid(row)
    assert {row.kind for row in _stack_rows(mods)}.issuperset(rows)
    return rows


def _exercise_project_owned_layers(mods):
    from bmanga_dev_phase5_layer_command.utils.layer_hierarchy import OUTSIDE_STACK_KEY

    outside_uid = mods.stack.target_uid("outside", OUTSIDE_STACK_KEY)
    folder = _project_operator_once(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_add(
            "EXEC_DEFAULT", kind="layer_folder", anchor_uid=outside_uid
        ),
        "outside folder",
    )
    folder_uid = mods.stack.stack_item_uid(folder)
    shared = {}
    for kind in ("balloon", "text"):
        row = _project_operator_once(
            mods,
            lambda kind=kind: bpy.ops.bmanga.layer_stack_add(
                "EXEC_DEFAULT", kind=kind, anchor_uid=folder_uid
            ),
            f"shared {kind}",
        )
        shared[kind] = mods.stack.stack_item_uid(row)
    _assert_project_ownership(mods, shared)
    _link_shared_layers(mods, shared)
    return shared


def _assert_project_ownership(mods, shared):
    work, _page = _work_page(mods)
    store, document = _domain(mods)
    assert len(work.shared_balloons) == 1 and len(work.shared_texts) == 1
    assert store.project.settings["shared_balloons"]
    assert store.project.settings["shared_texts"]
    display_ids = {node.display_id for node in document.nodes.values()}
    assert work.shared_balloons[0].id not in display_ids
    assert work.shared_texts[0].id not in display_ids
    assert set(shared).issubset({"balloon", "text"})


def _link_shared_layers(mods, shared):
    from bmanga_dev_phase5_layer_command.utils import layer_command_runtime

    _store, before_page = _domain(mods)
    before_project = _domain(mods)[0].project.revision
    group, count = mods.links.link_uids(bpy.context, list(shared.values()))
    assert group and count == 2
    layer_command_runtime.commit_projection(bpy.context, operation="link.shared")
    after_store, after_page = _domain(mods)
    assert after_page.revision == before_page.revision
    assert after_store.project.revision == before_project
    assert set(mods.links.linked_uids_for_uid(bpy.context, shared["text"])) == set(shared.values())


def _exercise_duplicate_link(mods, rows):
    _select_uid(mods, rows["balloon"])
    duplicate = _operator_once(
        mods, lambda: bpy.ops.bmanga.layer_stack_duplicate("EXEC_DEFAULT"), "duplicate"
    )
    duplicate_uid = mods.stack.stack_item_uid(duplicate)
    assert duplicate_uid != rows["balloon"]
    # 通常版の「複製」はアクティブ1件だけを複製し、明示的な
    # 「リンク複製」とは別操作である。このUI契約をDomain都合で広げない。
    assert mods.links.linked_uids_for_uid(bpy.context, duplicate_uid) == {
        duplicate_uid
    }
    _select_uid(mods, rows["balloon"])
    linked = _operator_once(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_link_duplicate("EXEC_DEFAULT"),
        "balloon link duplicate",
    )
    linked_uid = mods.stack.stack_item_uid(linked)
    assert set(mods.links.linked_uids_for_uid(bpy.context, linked_uid)) == {
        rows["balloon"],
        linked_uid,
    }
    _select_only(mods, (rows["balloon"], linked_uid))
    _operator_once(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_unlink_selected("EXEC_DEFAULT"),
        "unlink",
    )
    assert linked_uid not in mods.links._load_map(bpy.context)
    return duplicate_uid


def _exercise_all_scope_duplicates(mods, rows, shared):
    from bmanga_dev_phase5_layer_command.io import schema

    _select_uid(mods, rows["image_path"])
    image_paths = bpy.context.scene.bmanga_image_path_layers
    source_path = mods.stack.resolve_stack_item(
        bpy.context,
        _row_by_uid(mods, rows["image_path"]),
    )["target"]
    source_path_payload = schema.image_path_layer_to_dict(source_path)
    before_path_count = len(image_paths)
    duplicate_path = _operator_once(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_duplicate("EXEC_DEFAULT"),
        "image path duplicate",
    )
    assert len(image_paths) == before_path_count + 1
    duplicate_path_target = mods.stack.resolve_stack_item(
        bpy.context,
        duplicate_path,
    )["target"]
    duplicate_path_payload = schema.image_path_layer_to_dict(
        duplicate_path_target
    )
    assert duplicate_path_payload["id"] != source_path_payload["id"]
    for key in (
        "filepath",
        "pathPointsJson",
        "drawMode",
        "parentKind",
        "parentKey",
        "folderKey",
    ):
        assert duplicate_path_payload[key] == source_path_payload[key], key

    work, _page = _work_page(mods)
    for kind, collection_name, serializer in (
        ("balloon", "shared_balloons", schema.balloon_entry_to_dict),
        ("text", "shared_texts", schema.text_entry_to_dict),
    ):
        _select_uid(mods, shared[kind])
        collection = getattr(work, collection_name)
        source = mods.stack.resolve_stack_item(
            bpy.context,
            _row_by_uid(mods, shared[kind]),
        )["target"]
        source_payload = serializer(source)
        before_count = len(collection)
        duplicate = _project_operator_once(
            mods,
            lambda: bpy.ops.bmanga.layer_stack_duplicate("EXEC_DEFAULT"),
            f"shared {kind} duplicate",
        )
        assert len(collection) == before_count + 1
        target = mods.stack.resolve_stack_item(bpy.context, duplicate)["target"]
        target_payload = serializer(target)
        assert target_payload["id"] != source_payload["id"]
        assert target_payload["folderKey"] == source_payload["folderKey"]
        assert target_payload["parentKind"] == source_payload["parentKind"]
        assert target_payload["parentKey"] == source_payload["parentKey"]


def _exercise_effect_link(mods, effect_uid):
    _select_uid(mods, effect_uid)
    linked = _operator_once(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_link_duplicate("EXEC_DEFAULT"),
        "effect link duplicate",
    )
    linked_uid = mods.stack.stack_item_uid(linked)
    assert set(mods.links.linked_uids_for_uid(bpy.context, linked_uid)) == {
        effect_uid,
        linked_uid,
    }
    # 通常の削除もアクティブ1件だけ。リンク相手は残し、1件だけになった
    # リンクグループを解散する（mainのUI・操作凍結契約）。
    _select_uid(mods, linked_uid)
    _operator_once(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_delete("EXEC_DEFAULT"),
        "linked effect active-only delete",
    )
    assert effect_uid in {
        mods.stack.stack_item_uid(row) for row in _stack_rows(mods)
    }
    assert linked_uid not in {
        mods.stack.stack_item_uid(row) for row in _stack_rows(mods)
    }
    assert mods.links.linked_uids_for_uid(bpy.context, effect_uid) == {
        effect_uid
    }


def _exercise_reorder(mods, uid):
    _select_uid(mods, uid)
    before_order = [mods.stack.stack_item_uid(row) for row in _stack_rows(mods)]
    _operator_once(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_move("EXEC_DEFAULT", direction="UP"),
        "reorder",
    )
    after_order = [mods.stack.stack_item_uid(row) for row in _stack_rows(mods)]
    assert before_order != after_order


def _exercise_ten_step_reorder(mods, uid):
    _select_uid(mods, uid)
    before_order = [mods.stack.stack_item_uid(row) for row in _stack_rows(mods)]
    _operator_once(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_move_ten("EXEC_DEFAULT", direction="DOWN"),
        "ten-step reorder",
    )
    after_order = [mods.stack.stack_item_uid(row) for row in _stack_rows(mods)]
    assert before_order != after_order


def _exercise_reparent(mods, balloon_uid):
    from bmanga_dev_phase5_layer_command.utils import layer_reparent

    work, page = _work_page(mods)
    assert len(page.comas)
    _select_only(mods, (balloon_uid,))
    target = layer_reparent.ClickTarget("coma", page, page.comas[0], 0, None, None)
    _store, before = _domain(mods)
    assert layer_reparent.reparent_selected(bpy.context, target) == 1
    _store, after = _domain(mods)
    assert after.revision == before.revision + 1
    _assert_projection_matches_domain(mods)
    resolved = mods.stack.resolve_stack_item(bpy.context, _row_by_uid(mods, balloon_uid))
    assert resolved["target"].parent_key == f"{page.id}:{page.comas[0].id}"


def _exercise_folder_delete(mods):
    folder = _operator_once(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_add("EXEC_DEFAULT", kind="layer_folder"),
        "folder add",
    )
    folder_uid = mods.stack.stack_item_uid(folder)
    folder_key = str(folder.key)
    child = _operator_once(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_add(
            "EXEC_DEFAULT", kind="fill", anchor_uid=folder_uid
        ),
        "folder child add",
    )
    child_uid = mods.stack.stack_item_uid(child)
    resolved = mods.stack.resolve_stack_item(bpy.context, child)
    assert resolved["target"].folder_key == folder_key
    _select_uid(mods, folder_uid)
    _operator_once(
        mods, lambda: bpy.ops.bmanga.layer_stack_delete("EXEC_DEFAULT"), "folder delete"
    )
    child = _select_uid(mods, child_uid)
    assert not mods.stack.resolve_stack_item(bpy.context, child)["target"].folder_key


def _flaky_commit(runtime, before_raise=None):
    original = runtime._apply_projection_commands
    calls = {"count": 0}

    def fail_once(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            if before_raise is not None:
                before_raise()
            raise RuntimeError("phase5 injected command failure")
        return original(*args, **kwargs)

    return original, fail_once


def _assert_failed_operation_rolls_back(
    mods,
    invoke,
    label,
    *,
    before_raise=None,
):
    from bmanga_dev_phase5_layer_command.utils import layer_command_runtime

    before = _formal_snapshot(mods)
    original, flaky = _flaky_commit(
        layer_command_runtime,
        before_raise=before_raise,
    )
    layer_command_runtime._apply_projection_commands = flaky
    try:
        result = invoke()
    finally:
        layer_command_runtime._apply_projection_commands = original
    assert "CANCELLED" in result, (label, result)
    _immediate_store, immediate_page = _domain(mods)
    assert immediate_page.to_dict() == before["domain"], label
    after = _formal_snapshot(mods)
    if after != before:
        print(f"PHASE5_ROLLBACK_DIFF_{label}:", _first_diff(before, after))
    assert after == before, label


def _raster_file_snapshot(mods):
    from bmanga_dev_phase5_layer_command.utils import paths

    work, _page = _work_page(mods)
    root = paths.raster_dir(Path(work.work_dir))
    return {
        path.resolve(): path.read_bytes()
        for path in root.glob("*.png")
    }


def _assert_failed_raster_operation(
    mods,
    invoke,
    label,
    *,
    inject_external_file=False,
):
    from bmanga_dev_phase5_layer_command.io import save_baseline
    from bmanga_dev_phase5_layer_command.utils import paths

    work, _page = _work_page(mods)
    before_files = _raster_file_snapshot(mods)
    before_baseline = save_baseline.snapshot_baseline_registry()
    external = paths.raster_dir(Path(work.work_dir)) / "external_concurrent.png"

    def before_raise():
        if inject_external_file:
            external.write_bytes(b"external concurrent payload")

    _assert_failed_operation_rolls_back(
        mods,
        invoke,
        label,
        before_raise=before_raise if inject_external_file else None,
    )
    after_files = _raster_file_snapshot(mods)
    if inject_external_file:
        assert after_files.pop(external.resolve()) == b"external concurrent payload"
    assert after_files == before_files, label
    assert save_baseline.snapshot_baseline_registry() == before_baseline
    save_baseline.assert_no_external_changes(work.work_dir)
    if inject_external_file:
        external.unlink()


def _exercise_initial_raster_save_failure(mods):
    from bmanga_dev_phase5_layer_command.io import save_baseline
    from bmanga_dev_phase5_layer_command.operators import raster_layer_op

    before = _formal_snapshot(mods)
    before_files = _raster_file_snapshot(mods)
    before_baseline = save_baseline.snapshot_baseline_registry()
    original_save = raster_layer_op.save_raster_png
    original_log_exception = raster_layer_op._logger.exception

    def fail_save(*_args, **_kwargs):
        raise OSError("phase5 injected initial raster save failure")

    raster_layer_op.save_raster_png = fail_save
    raster_layer_op._logger.exception = lambda *_args, **_kwargs: None
    try:
        try:
            result = bpy.ops.bmanga.layer_stack_add(
                "EXEC_DEFAULT",
                kind="raster",
                dpi=30,
            )
        except RuntimeError as exc:
            assert "ラスター画像を保存できませんでした" in str(exc)
            result = {"CANCELLED"}
    finally:
        raster_layer_op.save_raster_png = original_save
        raster_layer_op._logger.exception = original_log_exception
    assert "CANCELLED" in result, result
    assert _formal_snapshot(mods) == before
    assert _raster_file_snapshot(mods) == before_files
    assert save_baseline.snapshot_baseline_registry() == before_baseline
    work, _page = _work_page(mods)
    save_baseline.assert_no_external_changes(work.work_dir)


def _exercise_failure_rollback(mods, balloon_uid, raster_uid):
    _assert_failed_operation_rolls_back(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_add("EXEC_DEFAULT", kind="fill"),
        "add rollback",
    )
    _select_uid(mods, balloon_uid)
    _assert_failed_operation_rolls_back(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_duplicate("EXEC_DEFAULT"),
        "duplicate rollback",
    )
    _assert_failed_raster_operation(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_add(
            "EXEC_DEFAULT",
            kind="raster",
            dpi=30,
        ),
        "raster add rollback",
        inject_external_file=True,
    )
    _select_uid(mods, raster_uid)
    _assert_failed_raster_operation(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_duplicate("EXEC_DEFAULT"),
        "raster duplicate rollback",
    )
    _select_uid(mods, raster_uid)
    _assert_failed_raster_operation(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_delete("EXEC_DEFAULT"),
        "raster delete rollback",
    )


def _exercise_orphan_object_rollback(mods, image_path_uid):
    """複製で増えた実ObjectもPG/Domainと一緒に除去する。"""

    from bmanga_dev_phase5_layer_command.utils import (
        layer_object_sync,
    )

    before = _runtime_object_ids(mods)
    _select_uid(mods, image_path_uid)
    _assert_failed_operation_rolls_back(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_duplicate("EXEC_DEFAULT"),
        "image path orphan rollback",
    )
    after = _runtime_object_ids(mods)
    assert after == before
    work, _page = _work_page(mods)
    layer_object_sync.assert_runtime_objects_current(bpy.context.scene, work)


def _exercise_raster_success_baseline(mods, raster_uid):
    from bmanga_dev_phase5_layer_command.io import save_baseline

    _select_uid(mods, raster_uid)
    duplicate = _operator_once(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_duplicate("EXEC_DEFAULT"),
        "raster duplicate success",
    )
    duplicate_uid = mods.stack.stack_item_uid(duplicate)
    _select_uid(mods, duplicate_uid)
    _operator_once(
        mods,
        lambda: bpy.ops.bmanga.layer_stack_delete("EXEC_DEFAULT"),
        "raster delete success",
    )
    work, _page = _work_page(mods)
    save_baseline.assert_no_external_changes(work.work_dir)


class _DragOwner:
    def __init__(self):
        self._snapshots = []

    def _capture_snapshot(self, _context, _kind, _resolved):
        return None

    def _can_apply_total(self, _context, _dx, _dy):
        return True


def _exercise_drag_cancel(mods, balloon_uid):
    from bmanga_dev_phase5_layer_command.operators.layer_drag_transaction import DragTransaction

    row = _select_uid(mods, balloon_uid)
    resolved = mods.stack.resolve_stack_item(bpy.context, row)
    before = _formal_snapshot(mods)
    obj = resolved.get("object")
    matrix = obj.matrix_world.copy() if obj is not None else None
    transaction = DragTransaction(bpy.context, _DragOwner(), "balloon", resolved)
    assert transaction.update_overlay(bpy.context, 11.0, -7.0)
    assert _formal_snapshot(mods) == before
    transaction.cancel()
    assert _formal_snapshot(mods) == before
    if matrix is not None:
        assert obj.matrix_world == matrix


def _assert_fail_closed(mods, label):
    from bmanga_dev_phase5_layer_command.utils import handlers

    work = mods.get_work(bpy.context)
    assert work is not None and not work.loaded, label
    assert handlers.save_scene_work_to_disk(
        bpy.context,
        reason=f"phase5 {label} save-block probe",
    ) is False


def _exercise_cross_page_stage_native_rollback(mods):
    from bmanga_dev_phase5_layer_command.utils import cross_page_stage_command

    before = _formal_snapshot(mods)
    assert before["native"]["gp"] and before["native"]["effect"]
    snapshot = cross_page_stage_command.capture(bpy.context)
    cross_page_stage_command.restore(bpy.context, snapshot)
    after = _formal_snapshot(mods)
    assert after == before


def _exercise_layer_command_double_failure(mods):
    from bmanga_dev_phase5_layer_command.utils import layer_command_runtime

    before = _formal_snapshot(mods)
    recovery = layer_command_runtime.capture(bpy.context, ())
    work, page = _work_page(mods)
    original_commit = layer_command_runtime.commit_projection
    original_restore = layer_command_runtime.restore
    original_log_exception = layer_command_runtime._logger.exception

    def mutate():
        page.name = "phase5 rollback failure probe"
        return 1

    def fail_commit(*_args, **_kwargs):
        raise RuntimeError("phase5 injected layer commit failure")

    def fail_restore(*_args, **_kwargs):
        raise RuntimeError("phase5 injected layer rollback failure")

    layer_command_runtime.commit_projection = fail_commit
    layer_command_runtime.restore = fail_restore
    layer_command_runtime._logger.exception = lambda *_args, **_kwargs: None
    try:
        try:
            layer_command_runtime.execute(
                bpy.context,
                items=(),
                operation="phase5.double-failure",
                mutate=mutate,
            )
            raise AssertionError("layer rollback failure was not propagated")
        except layer_command_runtime.LayerCommandRollbackError:
            pass
    finally:
        layer_command_runtime.commit_projection = original_commit
        layer_command_runtime.restore = original_restore
        layer_command_runtime._logger.exception = original_log_exception
    _assert_fail_closed(mods, "layer rollback")
    work.loaded = True
    original_restore(bpy.context, recovery)
    after = _formal_snapshot(mods)
    if after != before:
        print("PHASE5_DOUBLE_FAILURE_RECOVERY_DIFF:", _first_diff(before, after))
    assert after == before


def _exercise_reorder_double_failure(mods):
    from bmanga_dev_phase5_layer_command.utils import (
        layer_command_runtime,
        layer_stack_command_runtime,
        log,
    )

    work, _page = _work_page(mods)
    original_commit = layer_command_runtime.commit_projection
    original_restore = layer_command_runtime.restore_active_page_from_domain
    original_get_logger = log.get_logger

    def fail_commit(*_args, **_kwargs):
        raise RuntimeError("phase5 injected reorder commit failure")

    def fail_restore(*_args, **_kwargs):
        raise RuntimeError("phase5 injected reorder rollback failure")

    layer_command_runtime.commit_projection = fail_commit
    layer_command_runtime.restore_active_page_from_domain = fail_restore
    log.get_logger = lambda *_args, **_kwargs: SimpleNamespace(
        exception=lambda *_args, **_kwargs: None
    )
    try:
        try:
            layer_stack_command_runtime.commit_order(bpy.context)
            raise AssertionError("reorder rollback failure was not propagated")
        except layer_command_runtime.LayerCommandRollbackError:
            pass
    finally:
        layer_command_runtime.commit_projection = original_commit
        layer_command_runtime.restore_active_page_from_domain = original_restore
        log.get_logger = original_get_logger
    _assert_fail_closed(mods, "reorder rollback")
    work.loaded = True


def _exercise_raster_rollback_failure(mods, raster_uid):
    from bmanga_dev_phase5_layer_command.utils import (
        layer_command_runtime,
        layer_stack_command_runtime,
    )

    work, _page = _work_page(mods)
    row = _row_by_uid(mods, raster_uid)
    original_restore = layer_stack_command_runtime._restore_raster_files

    def fail_restore(*_args, **_kwargs):
        raise RuntimeError("phase5 injected raster rollback failure")

    layer_stack_command_runtime._restore_raster_files = fail_restore
    try:
        try:
            layer_stack_command_runtime.execute(
                bpy.context,
                items=(row,),
                operation="phase5.raster-double-failure",
                mutate=lambda: 0,
                tracks_raster_files=True,
            )
            raise AssertionError("raster rollback failure was not propagated")
        except layer_command_runtime.LayerCommandRollbackError:
            pass
    finally:
        layer_stack_command_runtime._restore_raster_files = original_restore
    _assert_fail_closed(mods, "raster rollback")
    work.loaded = True


def _exercise_runtime_object_rollback_failure(mods, balloon_uid):
    """Domain復元後の実Object再同期失敗を成功扱いにしない。"""

    from bmanga_dev_phase5_layer_command.utils import (
        layer_command_runtime,
        layer_object_sync,
        layer_transfer_group,
    )

    before = _formal_snapshot(mods)
    recovery = layer_command_runtime.capture(bpy.context, ())
    row = _select_uid(mods, balloon_uid)
    stack = _stack_rows(mods)
    delete_index = next(
        index
        for index, item in enumerate(stack)
        if mods.stack.stack_item_uid(item) == balloon_uid
    )
    work, _page = _work_page(mods)
    original_commit = layer_command_runtime.commit_projection
    original_mirror = layer_object_sync.mirror_work_to_outliner
    original_log_exception = layer_command_runtime._logger.exception
    original_stack_log_exception = mods.stack._logger.exception
    original_transfer_log_exception = layer_transfer_group._logger.exception

    def fail_commit(*_args, **_kwargs):
        raise RuntimeError("phase5 injected layer commit failure")

    def fail_mirror(*_args, **_kwargs):
        raise RuntimeError("phase5 injected runtime object restore failure")

    layer_command_runtime.commit_projection = fail_commit
    layer_object_sync.mirror_work_to_outliner = fail_mirror
    layer_command_runtime._logger.exception = lambda *_args, **_kwargs: None
    mods.stack._logger.exception = lambda *_args, **_kwargs: None
    layer_transfer_group._logger.exception = lambda *_args, **_kwargs: None
    try:
        try:
            layer_command_runtime.execute(
                bpy.context,
                items=(row,),
                operation="phase5.runtime-object-double-failure",
                mutate=lambda: int(
                    mods.stack.delete_stack_index(bpy.context, delete_index)
                ),
            )
            raise AssertionError("runtime object rollback failure was hidden")
        except layer_command_runtime.LayerCommandRollbackError:
            pass
    finally:
        layer_command_runtime.commit_projection = original_commit
        layer_object_sync.mirror_work_to_outliner = original_mirror
        layer_command_runtime._logger.exception = original_log_exception
        mods.stack._logger.exception = original_stack_log_exception
        layer_transfer_group._logger.exception = original_transfer_log_exception
    _assert_fail_closed(mods, "runtime object rollback")
    work.loaded = True
    layer_command_runtime.restore(bpy.context, recovery)
    layer_object_sync.assert_runtime_objects_current(bpy.context.scene, work)
    after = _formal_snapshot(mods)
    if after != before:
        print("PHASE5_RUNTIME_OBJECT_RECOVERY_DIFF:", _first_diff(before, after))
    assert after == before


def _exercise_save_reload(mods):
    before = _formal_snapshot(mods)
    filepath = Path(bpy.data.filepath)
    assert filepath.name == "page.blend"
    assert "FINISHED" in bpy.ops.wm.save_as_mainfile(
        filepath=str(filepath), check_existing=False, compress=True
    )
    assert "FINISHED" in bpy.ops.wm.open_mainfile(filepath=str(filepath), load_ui=False)
    mods.stack.sync_layer_stack_after_data_change(bpy.context)
    after = _formal_snapshot(mods)
    assert after["work"] == before["work"]
    assert after["page"] == before["page"]
    assert after["links"] == before["links"]
    assert after["native"] == before["native"]
    assert after["stack"] == before["stack"]
    assert after["project"] == before["project"]
    _assert_projection_matches_domain(mods)


def main():
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_phase5_layer_command_"))
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        work_dir = temp_root / "Phase5LayerCommand.bmanga"
        assert "FINISHED" in bpy.ops.bmanga.work_new(filepath=str(work_dir))
        assert "FINISHED" in bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        mods = _modules()
        rows = _add_layers(mods)
        shared = _exercise_project_owned_layers(mods)
        _exercise_all_scope_duplicates(mods, rows, shared)
        duplicate_uid = _exercise_duplicate_link(mods, rows)
        _exercise_effect_link(mods, rows["effect"])
        _exercise_reorder(mods, rows["fill"])
        _exercise_ten_step_reorder(mods, rows["fill"])
        _exercise_reparent(mods, rows["balloon"])
        _exercise_folder_delete(mods)
        _exercise_initial_raster_save_failure(mods)
        _exercise_failure_rollback(mods, duplicate_uid, rows["raster"])
        _exercise_orphan_object_rollback(mods, rows["image_path"])
        _exercise_raster_success_baseline(mods, rows["raster"])
        _exercise_drag_cancel(mods, rows["balloon"])
        _exercise_cross_page_stage_native_rollback(mods)
        _exercise_layer_command_double_failure(mods)
        _exercise_reorder_double_failure(mods)
        _exercise_raster_rollback_failure(mods, rows["raster"])
        _exercise_runtime_object_rollback_failure(mods, duplicate_uid)
        _exercise_save_reload(mods)
        print("BMANGA_PHASE5_LAYER_COMMAND_OK")
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
