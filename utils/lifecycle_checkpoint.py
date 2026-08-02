"""Ctrl+Sとファイル遷移で共有する最小checkpoint。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import bpy

from . import log


_logger = log.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CheckpointOutcome:
    succeeded: bool
    metadata_saved: bool = False
    native_saved: bool = False
    native_skipped_clean: bool = False
    filepath: str = ""
    error: str = ""


def checkpoint_current(
    context=None,
    *,
    reason: str,
    force_native: bool = False,
) -> CheckpointOutcome:
    """dirtyなDomain/ラスターと必要なnative mainfileだけを確定する。"""

    from . import (
        file_transition_runtime,
        handlers,
        lifecycle_coordinator,
    )

    context = context or bpy.context
    target = lifecycle_coordinator.current_target(context)
    from . import history_runtime

    if history_runtime.is_restoring() or history_runtime.is_blocked():
        return CheckpointOutcome(
            False,
            filepath=target.filepath,
            error="Undo／Redo後の作品状態を復元できていないため保存できません",
        )
    if target.role not in {"work", "page", "coma"} or not target.filepath:
        return CheckpointOutcome(
            False,
            filepath=target.filepath,
            error="現在の正規作品ファイルを判定できません",
        )
    try:
        scene_dirty = file_transition_runtime.scene_content_dirty(
            getattr(context, "scene", None)
        )
        try:
            from ..operators import raster_layer_op

            raster_dirty = bool(raster_layer_op.dirty_raster_paths(context))
        except Exception:  # noqa: BLE001
            raster_dirty = True
        native_dirty = bool(
            force_native
            or getattr(bpy.data, "is_dirty", False)
            or scene_dirty
            or raster_dirty
            or not Path(target.filepath).is_file()
        )
        if not native_dirty:
            metadata_saved = handlers.save_scene_work_to_disk(
                context,
                reason=reason,
                strict_rasters=True,
                refresh_runtime=False,
            )
            if not metadata_saved:
                raise RuntimeError("作品情報を保存できませんでした")
            return CheckpointOutcome(
                True,
                metadata_saved=True,
                native_skipped_clean=True,
                filepath=target.filepath,
            )
        from ..io import blend_io

        work_root = Path(target.work_root)
        if target.role == "work":
            native_saved = blend_io.save_work_blend(work_root)
        elif target.role == "page":
            native_saved = blend_io.save_page_blend(
                work_root,
                target.page_id or target.page_uid,
            )
        else:
            native_saved = blend_io.save_coma_blend(
                work_root,
                target.page_id or target.page_uid,
                target.coma_id or target.coma_uid,
            )
        if not native_saved:
            raise RuntimeError("Blenderファイルを保存できませんでした")
        file_transition_runtime.mark_scene_clean(
            getattr(context, "scene", None)
        )
        return CheckpointOutcome(
            True,
            metadata_saved=True,
            native_saved=True,
            filepath=target.filepath,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.exception("lifecycle checkpoint failed: %s", reason)
        return CheckpointOutcome(
            False,
            filepath=target.filepath,
            error=str(exc),
        )


def checkpoint_succeeded(
    context=None,
    *,
    reason: str,
    force_native: bool = False,
) -> bool:
    return checkpoint_current(
        context,
        reason=reason,
        force_native=force_native,
    ).succeeded


__all__ = (
    "CheckpointOutcome",
    "checkpoint_current",
    "checkpoint_succeeded",
)
