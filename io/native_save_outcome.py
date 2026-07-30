"""Blender保存ハンドラの確定結果を同期呼び出し元へ返す。"""

from __future__ import annotations

import os
from pathlib import Path
from threading import RLock


_lock = RLock()
_outcomes: dict[str, bool] = {}


def _key(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(str(Path(path).resolve(strict=False)))


def clear(path: str | os.PathLike[str]) -> None:
    with _lock:
        _outcomes.pop(_key(path), None)


def record(path: str | os.PathLike[str], result) -> None:
    succeeded = bool(
        getattr(result, "native_save_succeeded", False)
        and getattr(result, "metadata_saved", False)
        and not getattr(result, "restored", False)
    )
    with _lock:
        _outcomes[_key(path)] = succeeded


def consume(path: str | os.PathLike[str]) -> bool | None:
    with _lock:
        return _outcomes.pop(_key(path), None)


__all__ = ["clear", "consume", "record"]
