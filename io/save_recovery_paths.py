"""保存復旧領域を一元管理する。"""

from __future__ import annotations

import os
from pathlib import Path
import shutil


RECOVERY_ROOT_NAME = ".bmanga-save-recovery-v1"


class SaveRecoveryPathError(RuntimeError):
    pass


def _is_link(path: Path) -> bool:
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def assert_recovery_owned_path(
    work: Path,
    path: Path,
    *,
    label: str = "保存復旧パス",
) -> Path:
    """復旧パスの全階層が実作品root内の通常パスであることを保証する。"""

    lexical_root = Path(work).absolute()
    lexical_path = Path(path).absolute()
    try:
        relative = lexical_path.relative_to(lexical_root)
    except ValueError as exc:
        raise SaveRecoveryPathError(f"{label}が作品フォルダー外です") from exc

    current = lexical_root
    for part in relative.parts:
        if _is_link(current):
            raise SaveRecoveryPathError(
                f"{label}の途中にリンクまたはジャンクションがあります"
            )
        current = current / part
    if _is_link(current):
        raise SaveRecoveryPathError(
            f"{label}がリンクまたはジャンクションです"
        )

    try:
        resolved_root = lexical_root.resolve(strict=True)
        lexical_path.resolve(strict=False).relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SaveRecoveryPathError(f"{label}が作品フォルダー外です") from exc
    return Path(path)


def _checked_base(work: Path, path: Path, *, label: str) -> Path:
    return assert_recovery_owned_path(work, path, label=label)


def recovery_root(work: Path) -> Path:
    return _checked_base(
        work,
        work / RECOVERY_ROOT_NAME,
        label="保存復旧先",
    )


def native_base(work: Path) -> Path:
    root = recovery_root(work)
    return _checked_base(
        work,
        root / "native",
        label="ネイティブ保存復旧先",
    )


def sidecar_base(work: Path) -> Path:
    root = recovery_root(work)
    return _checked_base(
        work,
        root / "sidecar",
        label="作品情報の退避先",
    )


def raster_snapshot_base(work: Path) -> Path:
    root = recovery_root(work)
    return _checked_base(
        work,
        root / "raster-snapshots",
        label="ラスター保存復旧先",
    )


def native_bases(work: Path) -> tuple[Path, ...]:
    return (native_base(work),)


def sidecar_bases(work: Path) -> tuple[Path, ...]:
    return (sidecar_base(work),)


def is_safe_transaction_journal(
    work: Path,
    path: Path,
    transaction_id: str,
    bases: tuple[Path, ...],
) -> bool:
    """ジャーナルとトランザクション階層がリンクでなく、指定配置内にあるか。"""

    try:
        assert_recovery_owned_path(work, path, label="保存復旧記録")
        for base in bases:
            assert_recovery_owned_path(work, base, label="保存復旧先")
    except SaveRecoveryPathError:
        return False
    actual_parent = os.path.normcase(str(path.parent.resolve(strict=False)))
    valid_parents = {
        os.path.normcase(str((base / transaction_id).resolve(strict=False)))
        for base in bases
    }
    return actual_parent in valid_parents


def prune_empty_base(work: Path, base: Path) -> bool:
    """空の種別ディレクトリと、現行配置の空ルートだけを削除する。"""

    try:
        assert_recovery_owned_path(work, base, label="保存復旧先")
    except SaveRecoveryPathError:
        return False
    try:
        base.rmdir()
    except OSError:
        return False
    root = recovery_root(work)
    if base.parent != root:
        return True
    assert_recovery_owned_path(work, root, label="保存復旧先")
    try:
        root.rmdir()
    except OSError:
        pass
    return True


def remove_recovery_tree(
    work: Path,
    path: Path,
    *,
    ignore_errors: bool = False,
) -> None:
    """削除の直前に物理境界を再検査して復旧directoryだけを消す。"""

    try:
        assert_recovery_owned_path(work, path, label="保存復旧削除対象")
        shutil.rmtree(path)
    except SaveRecoveryPathError:
        raise
    except OSError:
        if not ignore_errors:
            raise


__all__ = [
    "RECOVERY_ROOT_NAME",
    "SaveRecoveryPathError",
    "assert_recovery_owned_path",
    "is_safe_transaction_journal",
    "native_base",
    "native_bases",
    "prune_empty_base",
    "remove_recovery_tree",
    "recovery_root",
    "raster_snapshot_base",
    "sidecar_base",
    "sidecar_bases",
]
