"""別ページレイヤー移送のBlender Undoと永続状態を往復させる."""

from __future__ import annotations

import copy
from contextlib import ExitStack
from dataclasses import dataclass
import json
from pathlib import Path


_TOKENS_PROP = "bmanga_layer_transfer_history_tokens"
_records: dict[str, "TransferHistoryRecord"] = {}
_before_history_tokens: tuple[str, ...] = ()


class HistoryGenerationError(RuntimeError):
    """外部更新またはbackup破損により履歴適用を開始できない。"""


@dataclass(frozen=True)
class TransferHistoryRecord:
    token: str
    work_dir: Path
    source_page_id: str
    target_page_id: str
    stage_id: str
    recovery_dir: Path
    manifest: dict
    pre_files: dict[Path, Path | None]
    post_files: dict[Path, Path | None]
    pre_fingerprints: dict[Path, dict]
    post_fingerprints: dict[Path, dict]
    pre_backup_fingerprints: dict[Path, dict]
    post_backup_fingerprints: dict[Path, dict]
    stage_entry: dict


@dataclass(frozen=True)
class _HistoryStep:
    token: str
    record: TransferHistoryRecord
    undo: bool


def register(
    context,
    *,
    work_dir: Path,
    source_page_id: str,
    target_page_id: str,
    stage_id: str,
    recovery_dir: Path,
) -> None:
    """確定済み移送の前後世代をUndo可能期間だけ保持する."""

    from . import (
        cross_page_stage,
        json_io,
        layer_transfer_group,
        layer_transfer_recovery_manifest,
    )

    manifest_path = recovery_dir / layer_transfer_group._RECOVERY_MANIFEST_NAME
    manifest, pre_files = (
        layer_transfer_recovery_manifest.validate(
            work_dir,
            recovery_dir,
            json_io.read_json(manifest_path),
        )
    )
    if not cross_page_stage.asset_entry_matches_snapshot(
        work_dir,
        target_page_id,
        stage_id,
        manifest["target_stage"],
        state="ready",
    ):
        raise RuntimeError("transfer history ready stage identity is invalid")
    stage_entry = copy.deepcopy(manifest["target_stage"]["entry"])
    stage_entry["state"] = "ready"
    pre_fingerprints = _backup_generation(pre_files)
    post_fingerprints = _current_generation(pre_files)
    post_files = layer_transfer_group._backup_source_files(
        work_dir,
        source_page_id,
        recovery_dir / "history_post",
    )
    pre_backup_fingerprints = _backup_generation(pre_files)
    post_backup_fingerprints = _backup_generation(post_files)
    _assert_backup_generation(pre_files, pre_backup_fingerprints)
    _assert_backup_generation(post_files, post_backup_fingerprints)
    token = str(stage_id or "")
    if not token:
        raise RuntimeError("transfer history token is empty")
    current = _tokens(getattr(context, "scene", None))
    _discard_invalidated_redo(current, context=context)
    record = TransferHistoryRecord(
        token=token,
        work_dir=Path(work_dir).resolve(),
        source_page_id=source_page_id,
        target_page_id=target_page_id,
        stage_id=stage_id,
        recovery_dir=recovery_dir.resolve(),
        manifest=copy.deepcopy(manifest),
        pre_files=pre_files,
        post_files=post_files,
        pre_fingerprints=pre_fingerprints,
        post_fingerprints=post_fingerprints,
        pre_backup_fingerprints=pre_backup_fingerprints,
        post_backup_fingerprints=post_backup_fingerprints,
        stage_entry=stage_entry,
    )
    _set_tokens(context.scene, current + (token,))
    _records[token] = record


def begin_restore(context) -> None:
    """BlenderがRNAを戻す前の移送履歴列を記録する."""

    global _before_history_tokens
    _before_history_tokens = _tokens(getattr(context, "scene", None))


def reconcile(context) -> bool:
    """Undo/Redo後のtoken差分をDomain・sidecar・native fileへ反映する."""

    global _before_history_tokens
    after = _tokens(getattr(context, "scene", None))
    before = _before_history_tokens
    common = 0
    while (
        common < len(before)
        and common < len(after)
        and before[common] == after[common]
    ):
        common += 1
    removed = before[common:]
    added = after[common:]
    steps = _history_steps(reversed(removed), added)
    if steps:
        _apply_group(steps, context=context)
    _before_history_tokens = after
    return bool(removed or added)


def reset_after_file_load(context=None) -> None:
    """mainfileを跨げないBlender履歴とprocess内journal参照を破棄する."""

    global _before_history_tokens
    _records.clear()
    _before_history_tokens = ()
    scene = getattr(context, "scene", None) if context is not None else None
    if scene is not None and _TOKENS_PROP in scene:
        del scene[_TOKENS_PROP]


def _history_steps(removed, added) -> tuple[_HistoryStep, ...]:
    steps = []
    operations = (
        tuple((token, True) for token in removed)
        + tuple((token, False) for token in added)
    )
    for token, undo in operations:
        record = _records.get(token)
        if record is None:
            raise RuntimeError(
                "別ページ移送のUndo資料がありません。保存せず作品を開き直してください"
            )
        steps.append(_HistoryStep(token, record, undo))
    return tuple(steps)


def _apply_group(steps: tuple[_HistoryStep, ...], *, context=None) -> None:
    from ..io.project_file_lock import work_lock

    work_dirs = sorted({step.record.work_dir for step in steps}, key=str)
    with ExitStack() as locks:
        for work_dir in work_dirs:
            locks.enter_context(work_lock(work_dir, blocking=True))
        try:
            _preflight_group(steps)
            applied = []
            try:
                for step in steps:
                    _apply_files(step, context=context)
                    applied.append(step)
            except Exception as operation_error:
                try:
                    for step in reversed(applied):
                        _apply_files(
                            _HistoryStep(step.token, step.record, not step.undo),
                            context=context,
                        )
                except Exception as rollback_error:
                    from . import history_runtime

                    history_runtime._fail_closed(context, rollback_error)
                    raise RuntimeError(
                        "transfer history group compensation failed"
                    ) from rollback_error
                raise operation_error
        except Exception as exc:
            if isinstance(exc, HistoryGenerationError):
                from . import history_runtime

                history_runtime._fail_closed(context, exc)
            raise


def _preflight_group(steps: tuple[_HistoryStep, ...]) -> None:
    expected_current: dict[Path, dict] = {}
    for step in steps:
        record = step.record
        _assert_backup_generation(
            record.pre_files,
            record.pre_backup_fingerprints,
        )
        _assert_backup_generation(
            record.post_files,
            record.post_backup_fingerprints,
        )
        before = record.post_fingerprints if step.undo else record.pre_fingerprints
        after = record.pre_fingerprints if step.undo else record.post_fingerprints
        for destination, fingerprint in before.items():
            chained = expected_current.get(destination)
            if chained is None:
                _assert_current_generation({destination: fingerprint})
            elif chained != fingerprint:
                raise HistoryGenerationError(
                    f"transfer history generation chain diverged: {destination}"
                )
        expected_current.update(after)
        _preflight_stage(record, undo=step.undo)


def _apply_files(step: _HistoryStep, *, context=None) -> None:
    record = step.record
    try:
        (_undo_files if step.undo else _redo_files)(record)
        _assert_postcondition(record, undo=step.undo)
        _reload_domain(record)
    except Exception as operation_error:
        try:
            (_redo_files if step.undo else _undo_files)(record)
            _assert_postcondition(record, undo=not step.undo)
            _reload_domain(record)
        except Exception as rollback_error:
            from . import history_runtime

            history_runtime._fail_closed(context, rollback_error)
            raise RuntimeError(
                "transfer history operation and compensation both failed"
            ) from rollback_error
        raise operation_error


def _apply(token: str, *, undo: bool, context=None) -> None:
    """単一token用の既存テスト・診断入口."""
    _apply_group(_history_steps((token,) if undo else (), () if undo else (token,)), context=context)


def _preflight(record: TransferHistoryRecord, *, undo: bool) -> None:
    _assert_backup_generation(record.pre_files, record.pre_backup_fingerprints)
    _assert_backup_generation(record.post_files, record.post_backup_fingerprints)
    _assert_current_generation(
        record.post_fingerprints if undo else record.pre_fingerprints
    )
    _preflight_stage(record, undo=undo)


def _preflight_stage(record: TransferHistoryRecord, *, undo: bool) -> None:
    from . import cross_page_stage

    if undo:
        if not cross_page_stage.asset_entry_matches_snapshot(
            record.work_dir,
            record.target_page_id,
            record.stage_id,
            record.manifest["target_stage"],
            state="ready",
        ):
            raise HistoryGenerationError(
                "transfer Undo ready stage was externally updated"
            )
    elif _stage_state(record):
        raise HistoryGenerationError(
            "transfer Redo requires an absent target stage"
        )


def _assert_postcondition(record: TransferHistoryRecord, *, undo: bool) -> None:
    from . import cross_page_stage

    _assert_current_generation(
        record.pre_fingerprints if undo else record.post_fingerprints
    )
    if undo and _stage_state(record):
        raise RuntimeError("transfer Undo target stage remains")
    if not undo and not cross_page_stage.asset_entry_matches_snapshot(
        record.work_dir,
        record.target_page_id,
        record.stage_id,
        record.manifest["target_stage"],
        state="ready",
    ):
        raise RuntimeError("transfer Redo target stage is incomplete")


def _stage_state(record: TransferHistoryRecord) -> str:
    from . import layer_transfer_group

    return layer_transfer_group._recovery_stage_state(
        record.work_dir,
        record.target_page_id,
        record.stage_id,
    )


def _undo_files(record: TransferHistoryRecord) -> None:
    from . import layer_transfer_group

    layer_transfer_group._remove_stage(
        record.work_dir,
        record.target_page_id,
        record.stage_id,
    )
    if layer_transfer_group._recovery_stage_state(
        record.work_dir,
        record.target_page_id,
        record.stage_id,
    ):
        raise RuntimeError("transfer Undo could not remove the target stage")
    if not layer_transfer_group._restore_manifest_comas(
        record.work_dir,
        record.manifest,
    ):
        raise RuntimeError("transfer Undo could not restore coma files")
    if not layer_transfer_group._restore_source_files(
        record.work_dir,
        record.pre_files,
    ):
        raise RuntimeError("transfer Undo could not restore source files")


def _redo_files(record: TransferHistoryRecord) -> None:
    from . import cross_page_stage, layer_transfer_group

    _move_comas_to_target(record)
    if not layer_transfer_group._restore_source_files(
        record.work_dir,
        record.post_files,
    ):
        raise RuntimeError("transfer Redo could not restore source files")
    if not cross_page_stage._append_unique(
        record.work_dir,
        record.target_page_id,
        cross_page_stage.ASSET_ENTRIES_KEY,
        copy.deepcopy(record.stage_entry),
        record.stage_id,
    ):
        raise RuntimeError("transfer Redo could not restore the target stage")
    if layer_transfer_group._recovery_stage_state(
        record.work_dir,
        record.target_page_id,
        record.stage_id,
    ) != "ready":
        raise RuntimeError("transfer Redo target stage is not ready")


def _move_comas_to_target(record: TransferHistoryRecord) -> None:
    from ..io import coma_io
    from . import paths

    for raw in record.manifest.get("coma_moves", ()) or ():
        if not isinstance(raw, dict) or not bool(raw.get("source_existed", True)):
            continue
        source_id = str(raw.get("source_id", "") or "")
        target_id = str(raw.get("target_id", "") or "")
        source = paths.coma_dir(
            record.work_dir,
            record.source_page_id,
            source_id,
        )
        target = paths.coma_dir(
            record.work_dir,
            record.target_page_id,
            target_id,
        )
        if target.is_dir() and not source.exists():
            continue
        if source.is_dir() and not target.exists():
            coma_io.move_coma_files(
                record.work_dir,
                record.source_page_id,
                record.target_page_id,
                source_id,
                target_id,
            )
            continue
        raise RuntimeError(f"transfer Redo coma generation is ambiguous: {source_id}")


def _reload_domain(record: TransferHistoryRecord) -> None:
    from ..io import domain_runtime
    from . import paths

    repository = domain_runtime.repository_for(record.work_dir)
    project = repository.load_project()
    source_uid = paths.resolve_page_uid(
        record.work_dir,
        record.source_page_id,
    )
    source = repository.load_page(source_uid)
    domain_runtime.install_store(record.work_dir, project, (source,))


def _current_generation(files: dict[Path, Path | None]) -> dict[Path, dict]:
    from . import layer_transfer_recovery_manifest

    return {
        destination: layer_transfer_recovery_manifest.fingerprint_dict(destination)
        for destination in files
    }


def _backup_generation(files: dict[Path, Path | None]) -> dict[Path, dict]:
    from . import layer_transfer_recovery_manifest

    return {
        destination: (
            layer_transfer_recovery_manifest.fingerprint_dict(saved)
            if saved is not None
            else {"exists": False, "digest": "", "size": 0, "mtime_ns": 0}
        )
        for destination, saved in files.items()
    }


def _assert_current_generation(expected: dict[Path, dict]) -> None:
    from . import layer_transfer_recovery_manifest

    for destination, fingerprint in expected.items():
        if layer_transfer_recovery_manifest.fingerprint_dict(destination) != fingerprint:
            raise HistoryGenerationError(
                f"transfer history source generation changed: {destination}"
            )


def _assert_backup_generation(
    files: dict[Path, Path | None],
    expected: dict[Path, dict],
) -> None:
    actual = _backup_generation(files)
    if actual != expected:
        raise HistoryGenerationError("transfer history backup generation is damaged")


def _tokens(scene) -> tuple[str, ...]:
    if scene is None:
        return ()
    raw = str(scene.get(_TOKENS_PROP, "") or "")
    try:
        values = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return ()
    if not isinstance(values, list):
        return ()
    return tuple(str(value) for value in values if str(value))


def _set_tokens(scene, values: tuple[str, ...]) -> None:
    if values:
        scene[_TOKENS_PROP] = json.dumps(
            values,
            ensure_ascii=True,
            separators=(",", ":"),
        )
    elif _TOKENS_PROP in scene:
        del scene[_TOKENS_PROP]


def _discard_invalidated_redo(applied: tuple[str, ...], *, context=None) -> None:
    applied_set = set(applied)
    for token, record in tuple(_records.items()):
        if token in applied_set:
            continue
        from . import layer_transfer_group

        try:
            layer_transfer_group._set_recovery_terminal(
                record.recovery_dir,
                "rollback_applied",
                work_dir=record.work_dir,
            )
            layer_transfer_group._remove_recovery_dir(record.recovery_dir)
        except Exception:
            work = getattr(getattr(context, "scene", None), "bmanga_work", None)
            layer_transfer_group._mark_transfer_fail_closed(work)
            raise
        _records.pop(token, None)


__all__ = (
    "begin_restore",
    "reconcile",
    "register",
    "reset_after_file_load",
)
