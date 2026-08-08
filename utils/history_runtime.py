"""Undo/Redo 復元中の監視抑止と、次イベントループでの実体再同期."""

from __future__ import annotations

from pathlib import Path

import bpy

from . import log


_logger = log.get_logger(__name__)
_restoring = False
_relaunch_object_tool = False
_blocked_error = ""
_MAX_RECONCILE_ATTEMPTS = 3
_RECONCILE_RETRY_SECONDS = 0.05


def is_restoring() -> bool:
    """Blender が B-MANGA の履歴状態を復元・再構築中なら ``True``."""

    return _restoring


def is_blocked() -> bool:
    """Undo/Redo投影が有限回retry後も一致しなかったか。"""

    return bool(_blocked_error)


def blocked_error() -> str:
    return _blocked_error


def mutation_blocked(context=None) -> bool:
    """履歴復元中・復元失敗・作品fail-closedなら書込みを拒否する。"""

    if is_restoring() or is_blocked():
        return True
    ctx = context or bpy.context
    work = getattr(getattr(ctx, "scene", None), "bmanga_work", None)
    return work is not None and not bool(getattr(work, "loaded", False))


def _fail_closed(context, error: BaseException | str) -> None:
    global _restoring, _blocked_error
    _blocked_error = str(error) or type(error).__name__
    _restoring = True
    work = getattr(getattr(context, "scene", None), "bmanga_work", None)
    try:
        if work is not None:
            work.loaded = False
    except Exception:  # noqa: BLE001
        _logger.exception("history fail-closed work state could not be recorded")


def reset_after_file_load() -> None:
    """別mainfileの厳格hydrate開始時だけ履歴fail-closedを解除する。"""

    global _restoring, _relaunch_object_tool, _blocked_error
    from . import layer_transfer_history

    _restoring = False
    _relaunch_object_tool = False
    _blocked_error = ""
    layer_transfer_history.reset_after_file_load(bpy.context)


def begin_restore(*, relaunch_object_tool: bool = False) -> None:
    """監視系を止める復元区間を開始する."""

    global _restoring, _relaunch_object_tool, _blocked_error
    _restoring = True
    if _blocked_error:
        return
    _relaunch_object_tool = _relaunch_object_tool or bool(relaunch_object_tool)


def request_object_tool_relaunch() -> None:
    global _relaunch_object_tool
    _relaunch_object_tool = True


def _refresh_object_snapshots(scene) -> None:
    from . import layer_object_sync

    layer_object_sync.clear_snapshots()
    for obj in tuple(bpy.data.objects):
        if bool(obj.get("bmanga_managed", False)):
            layer_object_sync.update_snapshot(obj)
    try:
        from . import outliner_watch

        outliner_watch.mark_entry_counts_synced(scene)
    except Exception:  # noqa: BLE001
        _logger.exception("history reconcile: entry-count snapshot failed")


def _reconcile_current_state() -> None:
    """復元後の Scene/Work を取り直し、外部ファイルへ書かず実体だけ揃える."""

    context = bpy.context
    from . import layer_transfer_history

    layer_transfer_history.reconcile(context)
    scene = getattr(context, "scene", None)
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if scene is None or work is None or not bool(getattr(work, "loaded", False)):
        return

    from . import layer_object_sync, page_file_scene

    role, page_id, _coma_id = page_file_scene.current_role(context)
    page = None
    if role == page_file_scene.ROLE_PAGE and page_id:
        page = next(
            (
                entry
                for entry in getattr(work, "pages", ())
                if str(getattr(entry, "id", "") or "") == page_id
            ),
            None,
        )
    try:
        from ..io import page_io

        page_io.reconcile_work_projection(
            Path(str(getattr(work, "work_dir", "") or "")),
            work,
            page_entry=page,
            context=context if page is not None else None,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("history reconcile: Domain projection failed")
        raise
    # work.blend のプレビュー再生成は PNG 作成を伴い得るため、履歴復元からは
    # 呼ばない。Blender が復元した実体を正として監視キャッシュだけ更新する。
    if role == page_file_scene.ROLE_PAGE and page_id:
        with layer_object_sync.suppress_sync():
            layer_object_sync.clear_snapshots()
            layer_object_sync.mirror_work_to_outliner(
                scene,
                work,
                allow_object_writeback=False,
            )
            page_file_scene.purge_other_page_data(scene, page_id)
            page_file_scene.resync_page_runtime_objects(scene, work, page_id)
            try:
                from ..operators import raster_layer_op

                raster_layer_op.ensure_all_raster_runtime(context)
            except Exception:  # noqa: BLE001
                _logger.exception("history reconcile: raster runtime failed")

    _refresh_object_snapshots(scene)
    try:
        from . import layer_stack

        layer_stack.tag_view3d_redraw(context)
    except Exception:  # noqa: BLE001
        _logger.exception("history reconcile: redraw failed")


def schedule_reconcile(*, delay_seconds: float = 0.0) -> None:
    """Undo/Redo post の次イベントループで安全に再同期する."""

    global _restoring, _blocked_error
    if _blocked_error:
        _restoring = True
        return
    context = bpy.context
    attempts = 0

    def _tick():
        nonlocal attempts
        global _restoring, _relaunch_object_tool, _blocked_error
        relaunch = _relaunch_object_tool
        try:
            attempts += 1
            _reconcile_current_state()
        except Exception as exc:  # noqa: BLE001
            _logger.exception(
                "history reconcile failed (%d/%d)",
                attempts,
                _MAX_RECONCILE_ATTEMPTS,
            )
            if attempts < _MAX_RECONCILE_ATTEMPTS:
                return _RECONCILE_RETRY_SECONDS
            _fail_closed(context, exc)
            _relaunch_object_tool = False
            return None
        _restoring = False
        _relaunch_object_tool = False
        _blocked_error = ""
        try:
            from . import lifecycle_coordinator

            lifecycle_coordinator.finish_history_restore()
        except Exception:  # noqa: BLE001
            _fail_closed(context, "履歴復元の完了状態を確認できませんでした")
            _logger.exception("history lifecycle completion failed")
            return None
        if relaunch:
            try:
                from ..operators.object_tool_op import (
                    _schedule_object_tool_relaunch,
                )

                _schedule_object_tool_relaunch(delay_seconds=0.05)
            except Exception:  # noqa: BLE001
                _logger.exception("history reconcile: object tool relaunch failed")
        return None

    try:
        from . import lifecycle_scheduler

        lifecycle_scheduler.schedule(
            "history_reconcile",
            _tick,
            first_interval=max(0.0, float(delay_seconds)),
        )
    except Exception as exc:  # noqa: BLE001
        _fail_closed(context, exc)
        _logger.exception("history reconcile scheduling failed")
