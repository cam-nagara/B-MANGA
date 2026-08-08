"""レイヤーUI Commandに付随するNative file rollback境界。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Callable, Iterable


@dataclass(frozen=True)
class _RasterFiles:
    work_dir: Path
    root: Path
    trash: Path
    files: frozenset[Path]
    trashed: frozenset[Path]
    source_fingerprints: tuple[tuple[Path, object], ...]
    baseline_state: dict[str, dict[str, object]]


_commit_suppress_depth = 0


def commits_suppressed() -> bool:
    return _commit_suppress_depth > 0


@contextmanager
def suppress_commits():
    """Domainから正式投影を戻す間のCommand再入を防ぐ。"""

    global _commit_suppress_depth
    _commit_suppress_depth += 1
    try:
        yield
    finally:
        _commit_suppress_depth -= 1


def commit_order(context) -> bool:
    """現在の一覧順を一度だけLayer Commandへ確定する。"""

    from . import layer_command_runtime, layer_stack

    if commits_suppressed():
        layer_stack.remember_layer_stack_signature(context)
        return True
    try:
        layer_command_runtime.commit_projection(context, operation="reorder")
        return True
    except Exception as operation_error:  # noqa: BLE001
        from . import log

        logger = log.get_logger(__name__)
        logger.exception("layer order command failed")
        try:
            layer_command_runtime.restore_active_page_from_domain(context)
        except Exception as rollback_error:  # noqa: BLE001
            logger.exception("layer order rollback failed")
            raise layer_command_runtime.fail_closed_rollback(
                context,
                operation="reorder",
                operation_error=operation_error,
                rollback_error=rollback_error,
            ) from rollback_error
        return False


def execute(
    context,
    *,
    items: Iterable[object],
    operation: str,
    mutate: Callable[[], int],
    tracks_raster_files: bool = False,
) -> int:
    """Layer Command失敗時にラスターPNGも操作前へ戻す。"""

    from . import layer_command_runtime

    if not tracks_raster_files:
        return layer_command_runtime.execute(
            context,
            items=tuple(items),
            operation=operation,
            mutate=mutate,
        )
    work_dir = _work_dir(context)
    if work_dir is None:
        return layer_command_runtime.execute(
            context,
            items=tuple(items),
            operation=operation,
            mutate=mutate,
        )
    from ..io.project_file_lock import WorkLockError, work_lock

    try:
        with work_lock(work_dir):
            return _execute_raster_locked(
                context,
                items=tuple(items),
                operation=operation,
                mutate=mutate,
            )
    except WorkLockError:
        from . import log

        log.get_logger(__name__).exception("raster layer command lock failed")
        return 0


def _execute_raster_locked(
    context,
    *,
    items: tuple[object, ...],
    operation: str,
    mutate: Callable[[], int],
) -> int:
    from ..io import save_baseline
    from . import layer_command_runtime

    snapshot = _capture_raster_files(context, items)
    if snapshot is None:
        return 0
    save_baseline.assert_no_external_changes(snapshot.work_dir)
    for source, _fingerprint in snapshot.source_fingerprints:
        save_baseline.assert_existing_target_tracked(
            snapshot.work_dir,
            source,
        )
    changed = layer_command_runtime.execute(
        context,
        items=items,
        operation=operation,
        mutate=mutate,
        before_restore=lambda: _restore_raster_files(snapshot),
    )
    if changed > 0:
        _commit_raster_file_changes(snapshot)
    return changed


def _work_dir(context) -> Path | None:
    from ..core.work import get_work

    work = get_work(context)
    work_dir = Path(str(getattr(work, "work_dir", "") or "")) if work else None
    if work_dir is None or not work_dir.is_dir():
        return None
    return work_dir.resolve()


def _capture_raster_files(
    context,
    items: tuple[object, ...],
) -> _RasterFiles | None:
    from ..io import save_baseline
    from ..operators import raster_layer_op
    from . import layer_stack, paths

    work_dir = _work_dir(context)
    if work_dir is None:
        return None
    root = paths.raster_dir(work_dir)
    trash = paths.raster_trash_dir(work_dir)
    files = frozenset(root.glob("*.png")) if root.is_dir() else frozenset()
    trashed = frozenset(trash.glob("*.png")) if trash.is_dir() else frozenset()
    sources = []
    for item in items:
        if str(getattr(item, "kind", "") or "") != "raster":
            continue
        resolved = layer_stack.resolve_stack_item(context, item)
        target = resolved.get("target") if resolved is not None else None
        if target is None:
            continue
        source = raster_layer_op._abs_png_path(work_dir, target)  # noqa: SLF001
        sources.append((source, save_baseline.fingerprint(source)))
    return _RasterFiles(
        work_dir,
        root,
        trash,
        files,
        trashed,
        tuple(sources),
        save_baseline.snapshot_baseline_registry(),
    )


def _restore_raster_files(snapshot: _RasterFiles | None) -> None:
    if snapshot is None:
        return
    from ..io import save_baseline

    current_state = save_baseline.snapshot_baseline_registry()
    before = _work_baselines(snapshot.baseline_state, snapshot.work_dir)
    after = _work_baselines(current_state, snapshot.work_dir)
    conflicts = []
    for key, expected in after.items():
        path = Path(key)
        if key in before or not _is_raster_png(path, snapshot.root):
            continue
        actual = save_baseline.fingerprint(path)
        if actual == expected:
            path.unlink(missing_ok=True)
        elif actual.exists:
            conflicts.append(path)
    added_trash = (
        set(snapshot.trash.glob("*.png")) - set(snapshot.trashed)
        if snapshot.trash.is_dir()
        else set()
    )
    for source, expected in snapshot.source_fingerprints:
        actual = save_baseline.fingerprint(source)
        if actual == expected:
            continue
        if actual.exists:
            conflicts.append(source)
            continue
        candidate = _matching_trash_file(
            source,
            added_trash,
            expected,
        )
        if candidate is None:
            conflicts.append(source)
            continue
        source.parent.mkdir(parents=True, exist_ok=True)
        candidate.replace(source)
        added_trash.discard(candidate)
    if conflicts:
        raise save_baseline.SaveBaselineConflictError(conflicts)
    save_baseline.restore_baseline_registry(snapshot.baseline_state)


def _commit_raster_file_changes(snapshot: _RasterFiles) -> None:
    from ..io import save_baseline

    for source, _expected in snapshot.source_fingerprints:
        if not source.exists():
            save_baseline.record_successful_write(source)


def _work_baselines(
    state: dict[str, dict[str, object]],
    work_dir: Path,
) -> dict[str, object]:
    key = os.path.normcase(str(work_dir.resolve(strict=False)))
    return state.get(key, {})


def _is_raster_png(path: Path, root: Path) -> bool:
    try:
        relative = path.resolve(strict=False).relative_to(
            root.resolve(strict=False),
        )
    except ValueError:
        return False
    return len(relative.parts) == 1 and path.suffix.casefold() == ".png"


def _matching_trash_file(
    source: Path,
    candidates: set[Path],
    expected,
) -> Path | None:
    from ..io import save_baseline

    matches = [
        path
        for path in candidates
        if (
            path.name == source.name
            or (
                path.suffix == source.suffix
                and path.stem.startswith(f"{source.stem}_")
            )
        )
        and save_baseline.fingerprint(path).digest == expected.digest
        and save_baseline.fingerprint(path).size == expected.size
    ]
    return matches[0] if len(matches) == 1 else None


__all__ = (
    "commit_order",
    "commits_suppressed",
    "execute",
    "suppress_commits",
)
