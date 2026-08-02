"""Lifecycleに関わるBlender timerを一元所有するScheduler。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import bpy

from . import log


_logger = log.get_logger(__name__)
TimerCallback = Callable[[], float | None]
CancelCallback = Callable[[], None]


@dataclass(slots=True)
class _ScheduledTask:
    name: str
    generation: int
    callback: TimerCallback
    tick: TimerCallback
    first_interval: float
    persistent: bool
    restart_on_invalidate: bool
    on_cancel: CancelCallback | None


class LifecycleScheduler:
    def __init__(self) -> None:
        self._generation = 0
        self._tasks: dict[str, _ScheduledTask] = {}

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def task_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tasks))

    def invalidate(
        self,
        *,
        reason: str = "",
        restart_persistent: bool = True,
    ) -> int:
        self._generation += 1
        tasks = tuple(self._tasks.values())
        restart_tasks = tuple(
            task
            for task in tasks
            if restart_persistent and task.restart_on_invalidate
        )
        restart_ids = {id(task) for task in restart_tasks}
        for task in tasks:
            self._cancel_task(
                task,
                notify=id(task) not in restart_ids,
            )
        for task in restart_tasks:
            try:
                self.schedule(
                    task.name,
                    task.callback,
                    first_interval=task.first_interval,
                    persistent=task.persistent,
                    replace=False,
                    restart_on_invalidate=True,
                    on_cancel=task.on_cancel,
                )
            except Exception:  # noqa: BLE001
                self._notify_cancel(task)
                _logger.exception(
                    "persistent lifecycle task restart failed: %s",
                    task.name,
                )
        if reason:
            _logger.debug(
                "lifecycle scheduler generation=%d (%s)",
                self._generation,
                reason,
            )
        return self._generation

    def schedule(
        self,
        name: str,
        callback: TimerCallback,
        *,
        first_interval: float = 0.0,
        persistent: bool = False,
        replace: bool = True,
        restart_on_invalidate: bool = False,
        on_cancel: CancelCallback | None = None,
    ) -> int:
        task_name = str(name or "").strip()
        if not task_name:
            raise ValueError("scheduled task name is required")
        if replace:
            self.cancel(task_name)
        elif task_name in self._tasks:
            return self._tasks[task_name].generation
        generation = self._generation

        def _tick() -> float | None:
            task = self._tasks.get(task_name)
            if (
                task is None
                or task.tick is not _tick
                or task.generation != generation
                or generation != self._generation
            ):
                return None
            try:
                delay = callback()
            except Exception:  # noqa: BLE001
                if self._tasks.get(task_name) is task:
                    self._tasks.pop(task_name, None)
                    self._notify_cancel(task)
                _logger.exception(
                    "lifecycle scheduled task failed: %s",
                    task_name,
                )
                return None
            if self._tasks.get(task_name) is not task:
                # callback自身が同名タスクを再予約した場合、新しい予約を旧tickが
                # 完了扱いで削除してはならない。
                return None
            if delay is None:
                self._tasks.pop(task_name, None)
                return None
            try:
                return max(0.0, float(delay))
            except (TypeError, ValueError, OverflowError):
                self._tasks.pop(task_name, None)
                self._notify_cancel(task)
                _logger.exception(
                    "lifecycle scheduled task returned an invalid delay: %s",
                    task_name,
                )
                return None

        self._tasks[task_name] = _ScheduledTask(
            task_name,
            generation,
            callback,
            _tick,
            max(0.0, float(first_interval)),
            bool(persistent),
            bool(restart_on_invalidate),
            on_cancel,
        )
        try:
            bpy.app.timers.register(
                _tick,
                first_interval=self._tasks[task_name].first_interval,
                persistent=self._tasks[task_name].persistent,
            )
        except Exception:
            task = self._tasks.pop(task_name, None)
            if task is not None:
                self._notify_cancel(task)
            raise
        return generation

    def cancel(self, name: str) -> bool:
        task = self._tasks.get(str(name))
        if task is None:
            return False
        self._cancel_task(task, notify=True)
        return True

    def _cancel_task(
        self,
        task: _ScheduledTask,
        *,
        notify: bool,
    ) -> None:
        if self._tasks.get(task.name) is task:
            self._tasks.pop(task.name, None)
        try:
            if bpy.app.timers.is_registered(task.tick):
                bpy.app.timers.unregister(task.tick)
        except Exception:  # noqa: BLE001
            _logger.exception(
                "lifecycle scheduled task cancel failed: %s",
                task.name,
            )
        if notify:
            self._notify_cancel(task)

    @staticmethod
    def _notify_cancel(task: _ScheduledTask) -> None:
        if task.on_cancel is None:
            return
        try:
            task.on_cancel()
        except Exception:  # noqa: BLE001
            _logger.exception(
                "lifecycle scheduled task cancel hook failed: %s",
                task.name,
            )

    def is_scheduled(self, name: str) -> bool:
        return str(name) in self._tasks


SCHEDULER = LifecycleScheduler()


def invalidate(*, reason: str = "") -> int:
    return SCHEDULER.invalidate(reason=reason)


def schedule(
    name: str,
    callback: TimerCallback,
    *,
    first_interval: float = 0.0,
    persistent: bool = False,
    replace: bool = True,
    restart_on_invalidate: bool = False,
    on_cancel: CancelCallback | None = None,
) -> int:
    return SCHEDULER.schedule(
        name,
        callback,
        first_interval=first_interval,
        persistent=persistent,
        replace=replace,
        restart_on_invalidate=restart_on_invalidate,
        on_cancel=on_cancel,
    )


def cancel(name: str) -> bool:
    return SCHEDULER.cancel(name)


def is_scheduled(name: str) -> bool:
    return SCHEDULER.is_scheduled(name)


def unregister() -> None:
    SCHEDULER.invalidate(
        reason="unregister",
        restart_persistent=False,
    )


__all__ = (
    "LifecycleScheduler",
    "SCHEDULER",
    "cancel",
    "invalidate",
    "is_scheduled",
    "schedule",
    "unregister",
)
