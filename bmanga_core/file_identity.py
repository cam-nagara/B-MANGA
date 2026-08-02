"""保存直後の通常ファイルを物理IDと内容hashで識別する。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from typing import Callable


_HASH_BUFFER_SIZE = 1024 * 1024


def _is_reparse_stat(value: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(
        int(getattr(value, "st_file_attributes", 0) or 0) & flag
    )


@dataclass(frozen=True, slots=True)
class FileIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int
    sha256: str

    @classmethod
    def from_stat(
        cls,
        value: os.stat_result,
        sha256: str,
    ) -> "FileIdentity":
        return cls(
            device=int(value.st_dev),
            inode=int(value.st_ino),
            size=int(value.st_size),
            mtime_ns=int(value.st_mtime_ns),
            sha256=str(sha256),
        )

    def to_record(self) -> dict[str, object]:
        return {
            "kind": "file",
            "device": self.device,
            "inode": self.inode,
            "size": self.size,
            "mtimeNs": self.mtime_ns,
            "sha256": self.sha256,
        }


ArtifactCommitHook = Callable[[Path, FileIdentity], None]


def identity_from_written_handle(
    handle,
    sha256: str,
) -> FileIdentity:
    """書込みhandleを閉じる前の物理実体を期待指紋にする。"""

    value = os.fstat(handle.fileno())
    if not stat.S_ISREG(value.st_mode) or _is_reparse_stat(value):
        raise RuntimeError("保存生成物が通常ファイルではありません")
    return FileIdentity.from_stat(value, sha256)


def capture_file_identity(path: Path) -> FileIdentity:
    """pathが同じ実体のまま安定して読めた場合だけ指紋を返す。"""

    target = Path(path)
    if target.is_symlink():
        raise RuntimeError("保存生成物にリンクを使用できません")
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or _is_reparse_stat(before):
            raise RuntimeError("保存生成物が通常ファイルではありません")
        while chunk := handle.read(_HASH_BUFFER_SIZE):
            digest.update(chunk)
        after = os.fstat(handle.fileno())
    current = target.stat()
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if any(
        getattr(before, name) != getattr(after, name)
        or getattr(after, name) != getattr(current, name)
        for name in stable_fields
    ):
        raise RuntimeError("保存生成物が指紋取得中に変更されました")
    return FileIdentity.from_stat(after, digest.hexdigest())


def matches_file_identity(path: Path, expected: FileIdentity) -> bool:
    try:
        return capture_file_identity(Path(path)) == expected
    except (OSError, RuntimeError):
        return False


__all__ = (
    "ArtifactCommitHook",
    "FileIdentity",
    "capture_file_identity",
    "identity_from_written_handle",
    "matches_file_identity",
)
