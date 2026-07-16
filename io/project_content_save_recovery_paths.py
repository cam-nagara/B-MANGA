"""保存復旧領域の現行配置と旧配置を一元管理する。"""

from __future__ import annotations

import os
from pathlib import Path


RECOVERY_ROOT_NAME = ".bmanga-save-recovery-v1"


class SaveRecoveryPathError(RuntimeError):
    pass


def _checked_base(path: Path, *, label: str) -> Path:
    if path.is_symlink():
        raise SaveRecoveryPathError(f"{label}がシンボリックリンクです")
    return path


def recovery_root(work: Path) -> Path:
    return _checked_base(
        work / RECOVERY_ROOT_NAME,
        label="保存復旧先",
    )


def native_base(work: Path) -> Path:
    root = recovery_root(work)
    return _checked_base(root / "native", label="ネイティブ保存復旧先")


def sidecar_base(work: Path) -> Path:
    root = recovery_root(work)
    return _checked_base(root / "sidecar", label="作品情報の退避先")


def legacy_native_base(work: Path) -> Path:
    return _checked_base(
        work.parent / f".{work.name}.native-save-recovery-v1",
        label="旧ネイティブ保存復旧先",
    )


def legacy_sidecar_base(work: Path) -> Path:
    return _checked_base(
        work.parent / f".{work.name}.sidecar-save-recovery-v1",
        label="旧作品情報の退避先",
    )


def native_bases(work: Path) -> tuple[Path, ...]:
    return native_base(work), legacy_native_base(work)


def sidecar_bases(work: Path) -> tuple[Path, ...]:
    return sidecar_base(work), legacy_sidecar_base(work)


def is_safe_transaction_journal(
    path: Path,
    transaction_id: str,
    bases: tuple[Path, ...],
) -> bool:
    """ジャーナルとトランザクション階層がリンクでなく、指定配置内にあるか。"""

    if path.is_symlink() or path.parent.is_symlink():
        return False
    actual_parent = os.path.normcase(os.path.abspath(path.parent))
    valid_parents = {
        os.path.normcase(os.path.abspath(base / transaction_id))
        for base in bases
    }
    return actual_parent in valid_parents


def prune_empty_base(work: Path, base: Path) -> bool:
    """空の種別ディレクトリと、現行配置の空ルートだけを削除する。"""

    if base.is_symlink():
        return False
    try:
        base.rmdir()
    except OSError:
        return False
    root = recovery_root(work)
    if base.parent != root or root.is_symlink():
        return True
    try:
        root.rmdir()
    except OSError:
        pass
    return True


__all__ = [
    "RECOVERY_ROOT_NAME",
    "SaveRecoveryPathError",
    "legacy_native_base",
    "legacy_sidecar_base",
    "is_safe_transaction_journal",
    "native_base",
    "native_bases",
    "prune_empty_base",
    "recovery_root",
    "sidecar_base",
    "sidecar_bases",
]
