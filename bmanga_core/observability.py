"""構造化event、処理回数counter、operation span。"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
import uuid
from collections import Counter, deque
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from functools import wraps
from typing import Callable


_LOGGER = logging.getLogger("bmanga.observability")
_CURRENT_OPERATION_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "bmanga_operation_id",
    default="",
)
_LOCK = threading.RLock()
_COUNTERS: Counter[str] = Counter()
_EVENTS: deque[dict[str, object]] = deque(maxlen=500)
_SINK: Callable[[dict[str, object]], None] | None = None


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


def increment_counter(name: str, amount: int = 1) -> None:
    if not name or amount < 1:
        raise ValueError("counter name and positive amount are required")
    with _LOCK:
        _COUNTERS[name] += amount


def emit_event(
    event: str,
    *,
    operation_id: str = "",
    target_uid: str = "",
    transaction_phase: str = "",
    dirty_reason: str = "",
    cache_status: str = "",
    outcome: str = "",
    elapsed_ms: float | None = None,
    **details: object,
) -> dict[str, object]:
    """必須fieldを固定したJSON eventを記録する。"""

    if not event:
        raise ValueError("event is required")
    record: dict[str, object] = {
        "event": event,
        "operation_id": operation_id or _CURRENT_OPERATION_ID.get(),
        "target_uid": target_uid,
        "transaction_phase": transaction_phase,
        "dirty_reason": dirty_reason,
        "cache_status": cache_status,
        "outcome": outcome,
    }
    if elapsed_ms is not None:
        record["elapsed_ms"] = round(float(elapsed_ms), 3)
    record.update({key: _json_value(value) for key, value in details.items()})
    with _LOCK:
        _EVENTS.append(dict(record))
        sink = _SINK
    _LOGGER.info(
        "BMANGA_EVENT %s",
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
    if sink is not None:
        sink(dict(record))
    return record


@dataclass
class _OperationSpan(AbstractContextManager["_OperationSpan"]):
    operation: str
    operation_id: str
    target_uid: str = ""
    transaction_phase: str = ""
    dirty_reason: str = ""
    cache_status: str = ""
    _started: float = field(init=False, default=0.0)
    _token: contextvars.Token[str] | None = field(init=False, default=None)
    _forced_failure: tuple[str, str] | None = field(init=False, default=None)

    def __enter__(self) -> "_OperationSpan":
        self._started = time.perf_counter()
        self._token = _CURRENT_OPERATION_ID.set(self.operation_id)
        increment_counter(f"{self.operation}.attempt")
        emit_event(
            "operation_started",
            operation_id=self.operation_id,
            target_uid=self.target_uid,
            transaction_phase=self.transaction_phase,
            dirty_reason=self.dirty_reason,
            cache_status=self.cache_status,
            operation=self.operation,
            outcome="started",
        )
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        elapsed_ms = (time.perf_counter() - self._started) * 1000.0
        outcome = "success" if exc is None and self._forced_failure is None else "failure"
        increment_counter(f"{self.operation}.{outcome}")
        details: dict[str, object] = {"operation": self.operation}
        if exc is not None:
            details.update(
                {
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            )
        elif self._forced_failure is not None:
            details.update(
                {
                    "error_type": self._forced_failure[0],
                    "error_message": self._forced_failure[1],
                }
            )
        emit_event(
            "operation_finished",
            operation_id=self.operation_id,
            target_uid=self.target_uid,
            transaction_phase=self.transaction_phase,
            dirty_reason=self.dirty_reason,
            cache_status=self.cache_status,
            outcome=outcome,
            elapsed_ms=elapsed_ms,
            **details,
        )
        if self._token is not None:
            _CURRENT_OPERATION_ID.reset(self._token)
        return False

    def mark_failure(self, error_type: str, error_message: str) -> None:
        self._forced_failure = (error_type, error_message)


def operation_span(
    operation: str,
    *,
    operation_id: str = "",
    target_uid: str = "",
    transaction_phase: str = "",
    dirty_reason: str = "",
    cache_status: str = "",
) -> _OperationSpan:
    """成功・失敗・所要時間を同じoperation IDで記録する。"""

    parent_id = _CURRENT_OPERATION_ID.get()
    return _OperationSpan(
        operation=operation,
        operation_id=operation_id or parent_id or uuid.uuid4().hex,
        target_uid=target_uid,
        transaction_phase=transaction_phase,
        dirty_reason=dirty_reason,
        cache_status=cache_status,
    )


def observed_operation(
    operation: str,
    *,
    failure_result: Callable[[object], bool] | None = None,
):
    """関数の正常return・例外をoperation spanへ記録するdecorator。"""

    if not operation:
        raise ValueError("operation is required")

    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with operation_span(operation) as span:
                result = function(*args, **kwargs)
                if failure_result is not None and failure_result(result):
                    span.mark_failure("FailureResult", repr(result))
                return result

        return wrapped

    return decorate


def observability_snapshot() -> dict[str, object]:
    with _LOCK:
        return {
            "counters": dict(sorted(_COUNTERS.items())),
            "events": [dict(event) for event in _EVENTS],
        }


def reset_observability() -> None:
    with _LOCK:
        _COUNTERS.clear()
        _EVENTS.clear()


def set_event_sink(
    sink: Callable[[dict[str, object]], None] | None,
) -> Callable[[dict[str, object]], None] | None:
    """テスト・host adapter用sinkを設定し、以前のsinkを返す。"""

    global _SINK
    with _LOCK:
        previous = _SINK
        _SINK = sink
    return previous
