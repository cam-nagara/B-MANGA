"""Blender非依存のファイルLifecycle状態機械。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class LifecycleError(RuntimeError):
    """Lifecycleの順序または遷移後identityが不正。"""


class LifecycleState(StrEnum):
    STABLE = "STABLE"
    PREPARING = "PREPARING"
    SAVING_SOURCE = "SAVING_SOURCE"
    OPENING_TARGET = "OPENING_TARGET"
    HYDRATING = "HYDRATING"
    ROLLING_BACK = "ROLLING_BACK"


class LifecycleEventKind(StrEnum):
    LOAD = "load"
    SAVE_PRE = "save_pre"
    SAVE_POST = "save_post"
    SAVE_FAIL = "save_fail"
    UNDO_PRE = "undo_pre"
    UNDO_POST = "undo_post"
    REDO_PRE = "redo_pre"
    REDO_POST = "redo_post"
    REGISTER = "register"
    UNREGISTER = "unregister"


@dataclass(frozen=True, slots=True)
class LifecycleTarget:
    """作品内で開いている物理ファイルとDomain identity。"""

    filepath: str = ""
    work_root: str = ""
    role: str = "unknown"
    project_uid: str = ""
    page_uid: str = ""
    page_id: str = ""
    coma_uid: str = ""
    coma_id: str = ""

    def same_file(self, other: "LifecycleTarget") -> bool:
        return self.filepath == other.filepath

    def matches_identity(self, actual: "LifecycleTarget") -> bool:
        """このtargetの既知identityをactualがすべて満たすか。"""

        if not self.same_file(actual):
            return False
        for field_name in (
            "work_root",
            "project_uid",
            "page_uid",
            "page_id",
            "coma_uid",
            "coma_id",
        ):
            expected = str(getattr(self, field_name, "") or "")
            if expected and str(getattr(actual, field_name, "") or "") != expected:
                return False
        return self.role in {"", "unknown"} or actual.role == self.role


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    kind: LifecycleEventKind
    filepath: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SessionObservation:
    session_id: str
    project_uid: str = ""
    project_revision: int = 0
    content_hash: str = ""


@dataclass(frozen=True, slots=True)
class TransitionSnapshot:
    generation: int
    source: LifecycleTarget
    target: LifecycleTarget


_NEXT_STATE = {
    LifecycleState.PREPARING: LifecycleState.SAVING_SOURCE,
    LifecycleState.SAVING_SOURCE: LifecycleState.OPENING_TARGET,
    LifecycleState.OPENING_TARGET: LifecycleState.HYDRATING,
}


class LifecycleMachine:
    """作品、ページ、コマ、Undo/Redoを同じ順序で扱う。"""

    def __init__(self) -> None:
        self.state = LifecycleState.STABLE
        self.generation = 0
        self.current = LifecycleTarget()
        self.pending: TransitionSnapshot | None = None
        self.last_error = ""
        self.last_event: LifecycleEvent | None = None
        self.session = SessionObservation(uuid.uuid4().hex)

    def observe_event(self, event: LifecycleEvent) -> None:
        if not isinstance(event, LifecycleEvent):
            raise TypeError("LifecycleEvent is required")
        self.last_event = event

    def begin(
        self,
        source: LifecycleTarget,
        target: LifecycleTarget,
    ) -> TransitionSnapshot:
        if self.state is not LifecycleState.STABLE:
            raise LifecycleError(
                f"lifecycle is busy: {self.state.value}"
            )
        if not target.filepath and target.role != "home":
            raise LifecycleError("transition target filepath is required")
        self.generation += 1
        self.state = LifecycleState.PREPARING
        self.current = source
        self.last_error = ""
        self.pending = TransitionSnapshot(self.generation, source, target)
        return self.pending

    def advance(self, state: LifecycleState) -> None:
        expected = _NEXT_STATE.get(self.state)
        if expected is not state:
            raise LifecycleError(
                f"invalid lifecycle advance: {self.state.value} -> {state.value}"
            )
        self.state = state

    def observe_load(self, target: LifecycleTarget) -> None:
        """load_postで物理ファイルが確定したことだけを状態機械へ渡す。"""

        if self.state is LifecycleState.OPENING_TARGET:
            pending = self._require_pending()
            if not target.same_file(pending.target):
                raise LifecycleError(
                    "opened file does not match pending transition target"
                )
            self.state = LifecycleState.HYDRATING
            return
        if self.state is LifecycleState.ROLLING_BACK:
            pending = self._require_pending()
            if not target.same_file(pending.source):
                raise LifecycleError(
                    "rollback opened a file other than the source"
                )
            return
        if self.state is LifecycleState.HYDRATING and self.pending is not None:
            pending = self._require_pending()
            if not target.same_file(pending.target):
                raise LifecycleError(
                    "additional load does not match pending transition target"
                )
            # 新規コマ作成では、保存直後に同じmainfileを開き直してUndo境界を
            # 確定する。identityが同じ再読込は同一hydrate phaseとして扱う。
            return
        if self.state is not LifecycleState.STABLE:
            raise LifecycleError(
                f"unexpected load during {self.state.value}"
            )
        self.generation += 1
        self.current = target
        self.pending = None

    def complete(
        self,
        actual: LifecycleTarget,
        *,
        observation: SessionObservation | None = None,
    ) -> None:
        pending = self._require_pending()
        if self.state is not LifecycleState.HYDRATING:
            raise LifecycleError(
                f"cannot complete from {self.state.value}"
            )
        if not pending.target.matches_identity(actual):
            raise LifecycleError(
                "hydrated file identity does not match transition target"
            )
        self.current = actual
        self.pending = None
        self.state = LifecycleState.STABLE
        if observation is not None:
            self.session = observation

    def begin_rollback(self, error: BaseException | str) -> None:
        if self.state is LifecycleState.STABLE or self.pending is None:
            raise LifecycleError("there is no transition to roll back")
        self.last_error = str(error)
        self.state = LifecycleState.ROLLING_BACK

    def finish_rollback(self, actual: LifecycleTarget) -> None:
        pending = self._require_pending()
        if self.state is not LifecycleState.ROLLING_BACK:
            raise LifecycleError("lifecycle is not rolling back")
        if not pending.source.matches_identity(actual):
            raise LifecycleError("rollback did not restore the source identity")
        self.current = actual
        self.pending = None
        self.state = LifecycleState.STABLE

    def begin_history_restore(self) -> int:
        if self.state is not LifecycleState.STABLE:
            raise LifecycleError(
                f"history restore during {self.state.value}"
            )
        self.generation += 1
        self.state = LifecycleState.HYDRATING
        return self.generation

    def finish_history_restore(self, actual: LifecycleTarget) -> None:
        if self.state is not LifecycleState.HYDRATING or self.pending is not None:
            raise LifecycleError("history restore is not active")
        self.current = actual
        self.state = LifecycleState.STABLE

    def reset(self, current: LifecycleTarget | None = None) -> None:
        self.generation += 1
        self.state = LifecycleState.STABLE
        self.current = current or LifecycleTarget()
        self.pending = None
        self.last_error = ""
        self.last_event = None

    def _require_pending(self) -> TransitionSnapshot:
        if self.pending is None:
            raise LifecycleError("pending transition is missing")
        return self.pending


__all__ = (
    "LifecycleError",
    "LifecycleEvent",
    "LifecycleEventKind",
    "LifecycleMachine",
    "LifecycleState",
    "LifecycleTarget",
    "SessionObservation",
    "TransitionSnapshot",
)
