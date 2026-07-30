"""決定的な失敗注入point。

既定では一切注入しない。テストが明示的にarmした回数だけ例外を発生させる。
現行I/O adapterと将来のTransactionは同じpointを呼ぶ。
"""

from __future__ import annotations

import os
import threading
from collections import Counter
from contextlib import contextmanager
from enum import StrEnum
from typing import Iterator


class FaultPoint(StrEnum):
    JSON_READ = "json.read.before"
    JSON_WRITE = "json.write.before"
    JSON_WRITE_AFTER_STAGE = "json.write.after_stage"
    JSON_WRITE_AFTER_COMMIT = "json.write.after_commit"
    ASSET_CREATE = "asset.create.before"
    ASSET_CREATE_AFTER_STAGE = "asset.create.after_stage"
    ASSET_CREATE_AFTER_COMMIT = "asset.create.after_commit"
    ASSET_INSTANTIATE = "asset.instantiate.before"
    ASSET_INSTANTIATE_AFTER_STAGE = "asset.instantiate.after_stage"
    ASSET_INSTANTIATE_AFTER_COMMIT = "asset.instantiate.after_commit"
    OPEN_MAINFILE = "open.mainfile.before"
    OPEN_MAINFILE_AFTER_STAGE = "open.mainfile.after_stage"
    OPEN_MAINFILE_AFTER_PREPARE = "open.mainfile.after_stage"
    OPEN_MAINFILE_AFTER_COMMIT = "open.mainfile.after_commit"
    EXPORT_WRITE = "export.write.before"
    EXPORT_WRITE_AFTER_STAGE = "export.write.after_stage"
    EXPORT_WRITE_AFTER_COMMIT = "export.write.after_commit"
    CHECKPOINT_AFTER_INSTALL = "checkpoint.after_install"
    CHECKPOINT_AFTER_COMMIT = "checkpoint.after_commit"


class FaultInjectedError(RuntimeError):
    """テストが意図して発生させた失敗。"""

    def __init__(self, point: FaultPoint, details: dict[str, object] | None = None):
        self.point = point
        self.details = dict(details or {})
        super().__init__(f"injected fault: {point.value}")


class _Registry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._armed: Counter[FaultPoint] = Counter()
        self._hits: Counter[FaultPoint] = Counter()
        self._injections: Counter[FaultPoint] = Counter()

    def arm(self, point: FaultPoint, times: int) -> None:
        if times < 1:
            raise ValueError("fault injection count must be positive")
        with self._lock:
            self._armed[point] += times

    def check(self, point: FaultPoint, details: dict[str, object]) -> None:
        with self._lock:
            self._hits[point] += 1
            if self._armed[point] <= 0:
                return
            self._armed[point] -= 1
            self._injections[point] += 1
        raise FaultInjectedError(point, details)

    def reset(self) -> None:
        with self._lock:
            self._armed.clear()
            self._hits.clear()
            self._injections.clear()

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {
                "armed": _counter_dict(self._armed),
                "hits": _counter_dict(self._hits),
                "injections": _counter_dict(self._injections),
            }


def _counter_dict(counter: Counter[FaultPoint]) -> dict[str, int]:
    return {
        point.value: int(value)
        for point, value in sorted(counter.items(), key=lambda item: item[0].value)
        if value
    }


_REGISTRY = _Registry()


def _point(value: FaultPoint | str) -> FaultPoint:
    if isinstance(value, FaultPoint):
        return value
    return FaultPoint(str(value))


def arm_fault(point: FaultPoint | str, *, times: int = 1) -> None:
    """指定pointを、次の``times``回だけ失敗させる。"""

    _REGISTRY.arm(_point(point), times)


def check_fault(point: FaultPoint | str, **details: object) -> None:
    """point到達を記録し、arm済みなら``FaultInjectedError``を送出する。"""

    _REGISTRY.check(_point(point), details)


def reset_faults() -> None:
    _REGISTRY.reset()


def fault_snapshot() -> dict[str, dict[str, int]]:
    return _REGISTRY.snapshot()


def configure_faults_from_environment() -> None:
    """明示許可されたテストprocessだけ環境変数からarmする。

    ``BMANGA_ENABLE_FAULT_INJECTION=1``が無い通常起動では、別の環境変数が
    偶然残っていても注入しない。
    """

    if os.environ.get("BMANGA_ENABLE_FAULT_INJECTION") != "1":
        return
    raw = os.environ.get("BMANGA_FAULT_POINTS", "")
    for item in raw.split(","):
        value = item.strip()
        if value:
            arm_fault(value)


@contextmanager
def isolated_faults() -> Iterator[None]:
    """テスト間で注入状態を漏らさないcontext。"""

    reset_faults()
    try:
        yield
    finally:
        reset_faults()
