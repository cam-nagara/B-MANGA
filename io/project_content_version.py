"""作品詳細データ版の厳密な読取りと保存世代ガード。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

try:
    from .project_content_migration_model import (
        DETAIL_DATA_VERSION_KEY,
        MIGRATION_VERSION,
        WORK_META_NAME,
    )
except ImportError:  # ファイル単体でロードする純Pythonテスト用
    from project_content_migration_model import (  # type: ignore[no-redef]
        DETAIL_DATA_VERSION_KEY,
        MIGRATION_VERSION,
        WORK_META_NAME,
    )


class DetailDataVersionError(RuntimeError):
    """版情報が壊れている、または解釈できない。"""


class DetailDataVersionMismatch(DetailDataVersionError):
    """メモリとディスクで作品の版が一致しない。"""


def detail_data_version(data: Mapping[str, Any]) -> int:
    """キー欠落だけを旧版0とし、不正値は例外にする。"""
    if DETAIL_DATA_VERSION_KEY not in data:
        return 0
    value = data[DETAIL_DATA_VERSION_KEY]
    if isinstance(value, bool):
        raise DetailDataVersionError("作品データの版情報が不正です")
    if isinstance(value, int) and value >= 0:
        return value
    raise DetailDataVersionError("作品データの版情報が不正です")


def read_work_mapping(work_dir: str | os.PathLike[str]) -> dict[str, Any]:
    path = Path(work_dir) / WORK_META_NAME
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise DetailDataVersionError("work.json の形式が不正です")
    return value


def read_work_detail_version(work_dir: str | os.PathLike[str]) -> int:
    return detail_data_version(read_work_mapping(work_dir))


def coerce_memory_version(value: Any) -> int:
    return detail_data_version({DETAIL_DATA_VERSION_KEY: value})


def assert_detail_version_matches(
    work_dir: str | os.PathLike[str],
    memory_version: Any,
) -> int:
    """旧セッションから現行作品を上書きしないよう版一致を保証する。"""
    work = Path(work_dir)
    meta = work / WORK_META_NAME
    if not meta.is_file():
        return coerce_memory_version(memory_version)
    disk = read_work_detail_version(work)
    memory = coerce_memory_version(memory_version)
    if disk > MIGRATION_VERSION or memory > MIGRATION_VERSION:
        raise DetailDataVersionMismatch("このB-MANGAより新しい作品は保存できません")
    if disk != memory:
        raise DetailDataVersionMismatch(
            f"開いている作品の版がディスクと一致しません（画面 {memory} / 保存先 {disk}）"
        )
    return disk


__all__ = [
    "DetailDataVersionError",
    "DetailDataVersionMismatch",
    "assert_detail_version_matches",
    "coerce_memory_version",
    "detail_data_version",
    "read_work_detail_version",
    "read_work_mapping",
]
