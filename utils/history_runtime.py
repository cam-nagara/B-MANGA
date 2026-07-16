"""Undo/Redo 復元中の監視抑止と、次イベントループでの実体再同期."""

from __future__ import annotations

import bpy

from . import log


_logger = log.get_logger(__name__)
_restoring = False
_relaunch_object_tool = False
_generation = 0


def is_restoring() -> bool:
    """Blender が B-MANGA の履歴状態を復元・再構築中なら ``True``."""

    return _restoring


def begin_restore(*, relaunch_object_tool: bool = False) -> None:
    """監視系を止める復元区間を開始する."""

    global _restoring, _relaunch_object_tool
    _restoring = True
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
    scene = getattr(context, "scene", None)
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if scene is None or work is None or not bool(getattr(work, "loaded", False)):
        return

    from . import layer_object_sync, page_file_scene

    role, page_id, _coma_id = page_file_scene.current_role(context)
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

    global _generation, _restoring
    _generation += 1
    generation = _generation

    def _tick():
        global _restoring, _relaunch_object_tool
        if generation != _generation:
            return None
        relaunch = _relaunch_object_tool
        try:
            _reconcile_current_state()
        except Exception:  # noqa: BLE001
            _logger.exception("history reconcile failed")
        finally:
            _restoring = False
            _relaunch_object_tool = False
        if relaunch:
            try:
                from ..operators.object_tool_op import _schedule_object_tool_relaunch

                _schedule_object_tool_relaunch(delay_seconds=0.05)
            except Exception:  # noqa: BLE001
                _logger.exception("history reconcile: object tool relaunch failed")
        return None

    try:
        bpy.app.timers.register(
            _tick,
            first_interval=max(0.0, float(delay_seconds)),
        )
    except Exception:  # noqa: BLE001
        _restoring = False
        _logger.exception("history reconcile scheduling failed")
