"""Phase 4のファイルLifecycle・Scheduler・遅延詳細読込を実機検証する。"""

from __future__ import annotations

import copy
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_phase4_lifecycle"
SENTINEL = "BMANGA_LIFECYCLE_COORDINATOR_OK"


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


def _check_lifecycle_handler_ownership() -> None:
    from bmanga_phase4_lifecycle.bmanga_core.lifecycle import (
        LifecycleEventKind,
    )
    from bmanga_phase4_lifecycle.utils import lifecycle_coordinator

    assert (
        lifecycle_coordinator.MACHINE.last_event.kind
        is LifecycleEventKind.REGISTER
    )
    expected = [
        (bpy.app.handlers.load_post, "_bmanga_on_load_post"),
        (bpy.app.handlers.save_pre, "_bmanga_on_save_pre"),
        (bpy.app.handlers.save_post, "_bmanga_on_save_post"),
        (bpy.app.handlers.undo_pre, "_bmanga_on_undo_pre"),
        (bpy.app.handlers.redo_pre, "_bmanga_on_redo_pre"),
        (bpy.app.handlers.undo_post, "_bmanga_on_undo_post"),
        (bpy.app.handlers.redo_post, "_bmanga_on_redo_post"),
    ]
    save_post_fail = getattr(bpy.app.handlers, "save_post_fail", None)
    if save_post_fail is not None:
        expected.append(
            (save_post_fail, "_bmanga_on_save_post_fail")
        )
    for handler_list, expected_name in expected:
        owned = [
            handler
            for handler in handler_list
            if str(getattr(handler, "__module__", "")).startswith(PACKAGE)
        ]
        assert [handler.__name__ for handler in owned] == [expected_name]


def _uid(owner, name: str) -> str:
    return str(owner.get(name, "") or "")


def _active_ids():
    from bmanga_phase4_lifecycle.io import domain_projection_ids

    work = bpy.context.scene.bmanga_work
    page = work.pages[int(work.active_page_index)]
    page_uid = _uid(page, domain_projection_ids.PAGE_UID_PROP)
    coma_uid = ""
    if 0 <= int(page.active_coma_index) < len(page.comas):
        coma_uid = _uid(
            page.comas[int(page.active_coma_index)],
            domain_projection_ids.COMA_UID_PROP,
        )
    return page_uid, coma_uid


def _assert_stable_target(
    role: str,
    *,
    filepath: Path,
    page_uid: str = "",
    coma_uid: str = "",
) -> None:
    from bmanga_phase4_lifecycle.bmanga_core.lifecycle import LifecycleState
    from bmanga_phase4_lifecycle.utils import lifecycle_coordinator

    actual = lifecycle_coordinator.current_target()
    assert lifecycle_coordinator.MACHINE.state is LifecycleState.STABLE
    assert lifecycle_coordinator.MACHINE.pending is None
    assert actual.role == role, actual
    assert Path(actual.filepath).resolve() == filepath.resolve()
    if page_uid:
        active_page_uid, active_coma_uid = _active_ids()
        assert actual.page_uid == page_uid
        assert active_page_uid == page_uid
        if coma_uid:
            assert actual.coma_uid == coma_uid
            assert active_coma_uid == coma_uid


def _assert_failure_restored_work(
    outcome,
    *,
    work_file: Path,
    page_uid: str,
    expected_index: int,
    expected_mode: str,
    expected_overview: bool,
    expected_layer_kind: str,
) -> None:
    from bmanga_phase4_lifecycle.bmanga_core.lifecycle import LifecycleState
    from bmanga_phase4_lifecycle.core.mode import get_mode

    assert not outcome.succeeded
    assert outcome.rolled_back
    _assert_stable_target("work", filepath=work_file)
    work = bpy.context.scene.bmanga_work
    assert int(work.active_page_index) == expected_index
    assert _active_ids()[0] == page_uid
    assert get_mode(bpy.context) == expected_mode
    assert bool(bpy.context.scene.bmanga_overview_mode) is expected_overview
    assert (
        str(bpy.context.scene.bmanga_active_layer_kind or "")
        == expected_layer_kind
    )
    assert outcome.failed_phase in {
        LifecycleState.PREPARING,
        LifecycleState.SAVING_SOURCE,
        LifecycleState.OPENING_TARGET,
        LifecycleState.HYDRATING,
    }


def _check_failure_injection(
    work_dir: Path,
    page_uid: str,
    page_id: str,
) -> None:
    from bmanga_phase4_lifecycle.bmanga_core.lifecycle import LifecycleState
    from bmanga_phase4_lifecycle.core.mode import get_mode
    from bmanga_phase4_lifecycle.io import blend_io
    from bmanga_phase4_lifecycle.utils import lifecycle_coordinator, paths

    work_file = paths.work_blend_path(work_dir)
    page_file = paths.page_blend_path(work_dir, page_uid)
    target = lifecycle_coordinator.target_for_path(
        page_file,
        work_root=work_dir,
        context=bpy.context,
    )
    work = bpy.context.scene.bmanga_work
    expected_index = int(work.active_page_index)
    expected_mode = get_mode(bpy.context)
    expected_overview = bool(bpy.context.scene.bmanga_overview_mode)
    expected_layer_kind = str(
        bpy.context.scene.bmanga_active_layer_kind or ""
    )

    original_log_exception = lifecycle_coordinator._logger.exception
    lifecycle_coordinator._logger.exception = lambda *_args, **_kwargs: None
    try:
        for failure_state in (
            LifecycleState.PREPARING,
            LifecycleState.SAVING_SOURCE,
            LifecycleState.OPENING_TARGET,
            LifecycleState.HYDRATING,
        ):
            def fail_at(state, *, expected=failure_state):
                if state is expected:
                    raise RuntimeError(
                        f"injected lifecycle failure: {state.value}"
                    )

            outcome = lifecycle_coordinator.run_transition(
                bpy.context,
                target,
                prepare=lambda: True,
                checkpoint=lambda: True,
                open_target=lambda: bool(
                    blend_io.open_page_blend(work_dir, page_id)
                ),
                phase_hook=fail_at,
            )
            assert outcome.failed_phase is failure_state
            _assert_failure_restored_work(
                outcome,
                work_file=work_file,
                page_uid=page_uid,
                expected_index=expected_index,
                expected_mode=expected_mode,
                expected_overview=expected_overview,
                expected_layer_kind=expected_layer_kind,
            )
    finally:
        lifecycle_coordinator._logger.exception = original_log_exception


def _check_old_timer_is_discarded() -> None:
    from bmanga_phase4_lifecycle.utils import lifecycle_scheduler

    calls: list[str] = []
    persistent_services = {
        "asset_drop.poll",
        "cross_addon.settings",
        "outliner.watch",
    }
    assert persistent_services <= set(
        lifecycle_scheduler.SCHEDULER.task_names
    )
    lifecycle_scheduler.schedule(
        "phase4.old_timer_probe",
        lambda: calls.append("ran"),
        first_interval=60.0,
    )
    old_tick = lifecycle_scheduler.SCHEDULER._tasks[
        "phase4.old_timer_probe"
    ].tick
    lifecycle_scheduler.invalidate(reason="phase4 stale timer test")
    assert old_tick() is None
    assert calls == []
    assert "phase4.old_timer_probe" not in (
        lifecycle_scheduler.SCHEDULER.task_names
    )
    assert persistent_services <= set(
        lifecycle_scheduler.SCHEDULER.task_names
    )
    lifecycle_scheduler.schedule(
        "phase4.replace_timer_probe",
        lambda: calls.append("old"),
        first_interval=60.0,
    )
    replaced_tick = lifecycle_scheduler.SCHEDULER._tasks[
        "phase4.replace_timer_probe"
    ].tick
    lifecycle_scheduler.schedule(
        "phase4.replace_timer_probe",
        lambda: calls.append("new"),
        first_interval=60.0,
    )
    assert replaced_tick() is None
    assert calls == []
    current_tick = lifecycle_scheduler.SCHEDULER._tasks[
        "phase4.replace_timer_probe"
    ].tick
    assert current_tick() is None
    assert calls == ["new"]

    def _replace_from_callback():
        lifecycle_scheduler.schedule(
            "phase4.self_replace_timer_probe",
            lambda: calls.append("self-new"),
            first_interval=60.0,
        )
        return None

    lifecycle_scheduler.schedule(
        "phase4.self_replace_timer_probe",
        _replace_from_callback,
        first_interval=60.0,
    )
    self_replacing_tick = lifecycle_scheduler.SCHEDULER._tasks[
        "phase4.self_replace_timer_probe"
    ].tick
    assert self_replacing_tick() is None
    assert lifecycle_scheduler.SCHEDULER.is_scheduled(
        "phase4.self_replace_timer_probe"
    )
    self_replaced_tick = lifecycle_scheduler.SCHEDULER._tasks[
        "phase4.self_replace_timer_probe"
    ].tick
    assert self_replaced_tick() is None
    assert calls == ["new", "self-new"]

    cancelled: list[str] = []
    lifecycle_scheduler.schedule(
        "phase4.persistent_probe",
        lambda: calls.append("persistent"),
        first_interval=60.0,
        persistent=True,
        restart_on_invalidate=True,
        on_cancel=lambda: cancelled.append("persistent"),
    )
    persistent_old_tick = lifecycle_scheduler.SCHEDULER._tasks[
        "phase4.persistent_probe"
    ].tick
    lifecycle_scheduler.invalidate(reason="phase4 persistent restart test")
    persistent_new_task = lifecycle_scheduler.SCHEDULER._tasks[
        "phase4.persistent_probe"
    ]
    assert persistent_new_task.tick is not persistent_old_tick
    assert persistent_old_tick() is None
    assert cancelled == []
    assert persistent_new_task.tick() is None
    assert calls == ["new", "self-new", "persistent"]

    lifecycle_scheduler.schedule(
        "phase4.cancel_hook_probe",
        lambda: None,
        first_interval=60.0,
        on_cancel=lambda: cancelled.append("cancelled"),
    )
    assert lifecycle_scheduler.cancel("phase4.cancel_hook_probe")
    assert cancelled == ["cancelled"]


def _check_current_file_sync_keeps_its_retry_generation() -> None:
    from bmanga_phase4_lifecycle.utils import (
        file_transition_runtime,
        handlers,
        lifecycle_scheduler,
    )

    handlers.schedule_current_file_sync(retries=2, interval=60.0)
    task = lifecycle_scheduler.SCHEDULER._tasks["current_file_sync"]
    assert task.tick() == 60.0
    assert file_transition_runtime.tracking_armed(bpy.context.scene)
    assert lifecycle_scheduler.SCHEDULER.is_scheduled("current_file_sync")
    assert task.tick() is None
    assert not lifecycle_scheduler.SCHEDULER.is_scheduled(
        "current_file_sync"
    )


def _check_runtime_cache_properties_are_not_saved() -> None:
    from bmanga_phase4_lifecycle.core.page import BMangaPageEntry
    from bmanga_phase4_lifecycle.core.work import BMangaWorkData

    assert BMangaWorkData.bl_rna.properties["loaded"].is_skip_save
    assert BMangaPageEntry.bl_rna.properties["detail_loaded"].is_skip_save
    for name in (
        "bmanga_layer_stack",
        "bmanga_layer_stack_visible",
        "bmanga_active_layer_stack_index",
        "bmanga_active_layer_stack_visible_index",
        "bmanga_active_layer_kind",
    ):
        assert bpy.types.Scene.bl_rna.properties[name].is_skip_save, name


def _check_project_uid_is_part_of_transition_identity(
    temp_root: Path,
) -> None:
    from bmanga_phase4_lifecycle.bmanga_core.lifecycle import (
        LifecycleError,
        LifecycleTarget,
    )
    from bmanga_phase4_lifecycle.utils import lifecycle_coordinator

    actual = lifecycle_coordinator.current_target()
    mismatched = LifecycleTarget(
        filepath=actual.filepath,
        work_root=actual.work_root,
        role=actual.role,
        project_uid="0" * 32,
    )
    try:
        lifecycle_coordinator.validate_current_target(mismatched)
    except LifecycleError as exc:
        assert "project UID" in str(exc)
    else:
        raise AssertionError("project UID mismatch was accepted")

    other_root = temp_root / "Other.bmanga"
    other_root.mkdir()
    other_uid = "1234567890abcdef1234567890abcdef"
    (other_root / "project.json").write_text(
        '{"projectUid":"' + other_uid + '"}',
        encoding="utf-8",
    )
    target = lifecycle_coordinator.target_for_path(
        other_root / "work.blend",
        work_root=other_root,
    )
    assert target.project_uid == other_uid


def _check_outliner_change_collector_batches_only_changed_objects() -> None:
    from bmanga_phase4_lifecycle.utils import (
        object_state_sync,
        outliner_change_collector,
        outliner_watch,
    )

    probe = bpy.data.objects.new("phase4_collector_probe", None)
    bpy.context.scene.collection.objects.link(probe)
    probe["bmanga_managed"] = True

    class Update:
        id = probe

    class Depsgraph:
        updates = (Update(),)

    synced = []
    written = []
    original_sync = object_state_sync.sync_from_blender_object
    original_writeback = outliner_watch.writeback_collected_changes
    object_state_sync.sync_from_blender_object = (
        lambda _scene, obj: synced.append(obj.name) or True
    )
    outliner_watch.writeback_collected_changes = (
        lambda _scene, *, objects=None:
        written.append(tuple(obj.name for obj in objects or ())) or 1
    )
    try:
        outliner_change_collector.collect_depsgraph(Depsgraph())
        assert outliner_change_collector.COLLECTOR.pending_count == 2
        changed = outliner_change_collector.flush(bpy.context.scene)
        assert changed == 2
        assert synced == [probe.name]
        assert written == [(probe.name,)]
        assert outliner_change_collector.COLLECTOR.pending_count == 0
    finally:
        object_state_sync.sync_from_blender_object = original_sync
        outliner_watch.writeback_collected_changes = original_writeback
        outliner_change_collector.clear()
        bpy.data.objects.remove(probe, do_unlink=True)


def _check_history_boundary() -> None:
    from bmanga_phase4_lifecycle.bmanga_core.lifecycle import (
        LifecycleEventKind,
        LifecycleState,
    )
    from bmanga_phase4_lifecycle.utils import (
        handlers,
        lifecycle_coordinator,
        lifecycle_scheduler,
    )

    handlers._bmanga_on_undo_pre()
    assert lifecycle_coordinator.MACHINE.state is LifecycleState.HYDRATING
    assert (
        lifecycle_coordinator.MACHINE.last_event.kind
        is LifecycleEventKind.UNDO_PRE
    )
    handlers._bmanga_on_undo_post()
    assert (
        lifecycle_coordinator.MACHINE.last_event.kind
        is LifecycleEventKind.UNDO_POST
    )
    task = lifecycle_scheduler.SCHEDULER._tasks.get("history_reconcile")
    assert task is not None
    assert task.tick() is None
    assert lifecycle_coordinator.MACHINE.state is LifecycleState.STABLE


def _check_history_failure_is_fail_closed() -> None:
    from bmanga_phase4_lifecycle.bmanga_core.lifecycle import LifecycleState
    from bmanga_phase4_lifecycle.utils import (
        handlers,
        history_runtime,
        lifecycle_checkpoint,
        lifecycle_coordinator,
        lifecycle_scheduler,
    )

    original = history_runtime._reconcile_current_state
    history_runtime._reconcile_current_state = (
        lambda: (_ for _ in ()).throw(
            RuntimeError("injected history projection failure")
        )
    )
    try:
        handlers._bmanga_on_undo_pre()
        handlers._bmanga_on_undo_post()
        task = lifecycle_scheduler.SCHEDULER._tasks.get(
            "history_reconcile"
        )
        assert task is not None
        assert task.tick() == history_runtime._RECONCILE_RETRY_SECONDS
        assert task.tick() == history_runtime._RECONCILE_RETRY_SECONDS
        assert task.tick() is None
        assert history_runtime.is_restoring()
        assert history_runtime.is_blocked()
        assert (
            lifecycle_coordinator.MACHINE.state
            is LifecycleState.HYDRATING
        )
        outcome = lifecycle_checkpoint.checkpoint_current(
            bpy.context,
            reason="history fail-closed test",
        )
        assert not outcome.succeeded
        assert "保存できません" in outcome.error
    finally:
        history_runtime._reconcile_current_state = original
    lifecycle_coordinator.note_load(Path(bpy.data.filepath))
    assert not history_runtime.is_restoring()
    assert not history_runtime.is_blocked()
    assert lifecycle_coordinator.MACHINE.state is LifecycleState.STABLE


def _check_dirty_store_noop_projection_is_checkpointed(
    work_dir: Path,
) -> None:
    from bmanga_phase4_lifecycle.bmanga_core.domain_store import (
        SetProjectSetting,
    )
    from bmanga_phase4_lifecycle.io import (
        domain_projection,
        domain_runtime,
        page_io,
    )

    store = domain_runtime.store_for(work_dir)
    store.execute(
        SetProjectSetting(
            "phase4_dirty_store_probe",
            "checkpointed",
        )
    )
    expected_revision = store.project.revision
    assert store.dirty_project
    domain_projection.bind_project_document(
        bpy.context.scene.bmanga_work,
        store.project,
    )
    page_io.save_work_projection(
        work_dir,
        bpy.context.scene.bmanga_work,
    )
    assert not store.dirty_project
    persisted = domain_runtime.repository_for(work_dir).load_project()
    assert persisted.revision == expected_revision
    assert (
        persisted.settings["phase4_dirty_store_probe"]
        == "checkpointed"
    )


def _install_eighty_page_domain(work_dir: Path) -> None:
    from bmanga_phase4_lifecycle.bmanga_core.domain_ids import (
        UIDKind,
        derived_uid,
    )
    from bmanga_phase4_lifecycle.bmanga_core.domain_model import (
        DomainNode,
        PageDocument,
        PageSummary,
    )
    from bmanga_phase4_lifecycle.io import domain_runtime
    from bmanga_phase4_lifecycle.utils import handlers

    repository = domain_runtime.repository_for(work_dir)
    project = repository.load_project()
    assert len(project.pages) == 1
    new_pages = []
    for number in range(2, 81):
        display_id = f"p{number:04d}"
        page_uid = derived_uid(
            UIDKind.PAGE,
            project.project_uid,
            f"phase4-{display_id}",
        )
        root_uid = derived_uid(UIDKind.NODE, page_uid, "root")
        project.pages.append(
            PageSummary(
                page_uid,
                display_id,
                number,
                title=f"ページ{number}",
            )
        )
        new_pages.append(
            PageDocument(
                project_uid=project.project_uid,
                page_uid=page_uid,
                revision=0,
                root_uid=root_uid,
                settings={},
                nodes={
                    root_uid: DomainNode(
                        root_uid,
                        "page",
                        display_id,
                        title=f"ページ{number}",
                    )
                },
                children={root_uid: []},
            )
        )
    project.revision += 1
    repository.checkpoint(project, new_pages)

    work = handlers.sync_scene_work_from_disk(bpy.context, work_dir)
    assert work is not None and work.loaded
    assert len(work.pages) == 80
    assert all(not bool(page.detail_loaded) for page in work.pages)
    assert all(len(page.comas) == 0 for page in work.pages)
    assert all(len(page.balloons) == 0 for page in work.pages)
    assert all(len(page.texts) == 0 for page in work.pages)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_phase4_lifecycle_"))
    addon = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        addon = _load_addon()
        _check_lifecycle_handler_ownership()
        from bmanga_phase4_lifecycle.io import domain_projection_ids
        from bmanga_phase4_lifecycle.utils import paths

        work_dir = temp_root / "Lifecycle.bmanga"
        assert bpy.ops.bmanga.work_new(filepath=str(work_dir)) == {"FINISHED"}
        work = bpy.context.scene.bmanga_work
        page_uid = _uid(
            work.pages[0],
            domain_projection_ids.PAGE_UID_PROP,
        )
        page_id = str(work.pages[0].id)
        work.active_page_index = 0
        _check_current_file_sync_keeps_its_retry_generation()

        # page.blendを一度作ってからwork.blendへ戻し、同じ確定targetへ
        # 各phaseの障害を注入できる状態にする。
        assert bpy.ops.bmanga.open_page_file(
            "EXEC_DEFAULT",
            index=0,
        ) == {"FINISHED"}
        _assert_stable_target(
            "page",
            filepath=paths.page_blend_path(work_dir, page_uid),
            page_uid=page_uid,
        )
        assert bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT") == {"FINISHED"}
        _assert_stable_target(
            "work",
            filepath=paths.work_blend_path(work_dir),
        )

        _check_failure_injection(work_dir, page_uid, page_id)
        _check_old_timer_is_discarded()
        _check_runtime_cache_properties_are_not_saved()
        _check_project_uid_is_part_of_transition_identity(temp_root)
        _check_outliner_change_collector_batches_only_changed_objects()

        # 作品→ページ→コマ→ページ→作品の全往復で物理pathとUIDが一致する。
        assert bpy.ops.bmanga.open_page_file(
            "EXEC_DEFAULT",
            index=0,
        ) == {"FINISHED"}
        _assert_stable_target(
            "page",
            filepath=paths.page_blend_path(work_dir, page_uid),
            page_uid=page_uid,
        )
        work = bpy.context.scene.bmanga_work
        page = work.pages[int(work.active_page_index)]
        assert len(page.comas) >= 1
        coma_uid = _uid(
            page.comas[int(page.active_coma_index)],
            domain_projection_ids.COMA_UID_PROP,
        )
        assert bpy.ops.bmanga.enter_coma_mode("EXEC_DEFAULT") == {"FINISHED"}
        _assert_stable_target(
            "coma",
            filepath=paths.coma_blend_path(work_dir, page_uid, coma_uid),
            page_uid=page_uid,
            coma_uid=coma_uid,
        )
        assert bpy.ops.bmanga.exit_coma_mode("EXEC_DEFAULT") == {"FINISHED"}
        _assert_stable_target(
            "page",
            filepath=paths.page_blend_path(work_dir, page_uid),
            page_uid=page_uid,
        )
        assert bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT") == {"FINISHED"}
        _assert_stable_target(
            "work",
            filepath=paths.work_blend_path(work_dir),
        )

        _check_history_boundary()
        _check_history_failure_is_fail_closed()
        _check_dirty_store_noop_projection_is_checkpointed(work_dir)
        _install_eighty_page_domain(work_dir)
        _assert_stable_target(
            "work",
            filepath=paths.work_blend_path(work_dir),
        )
        print(SENTINEL)
    finally:
        if addon is not None:
            scheduler = sys.modules.get(
                f"{PACKAGE}.utils.lifecycle_scheduler"
            )
            coordinator = sys.modules.get(
                f"{PACKAGE}.utils.lifecycle_coordinator"
            )
            addon.unregister()
            if scheduler is not None:
                assert scheduler.SCHEDULER.task_names == ()
            if coordinator is not None:
                from bmanga_phase4_lifecycle.bmanga_core.lifecycle import (
                    LifecycleEventKind,
                )

                assert (
                    coordinator.MACHINE.last_event.kind
                    is LifecycleEventKind.UNREGISTER
                )
        for name in tuple(sys.modules):
            if name == PACKAGE or name.startswith(f"{PACKAGE}."):
                sys.modules.pop(name, None)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
