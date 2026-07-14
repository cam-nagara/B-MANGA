"""詳細設定セッションのスナップショット、復元、操作境界。

種別固有の状態はアダプターとして登録する。キャンセル時は登録と逆の順序で
復元し、独立即時操作は親ダイアログの復元対象へ混ぜない。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .detail_dialog import (
    DetailActionBoundary,
    DetailActionSpec,
    DetailContractError,
    DetailMode,
    DetailSession,
    DetailSessionStatus,
    DetailTarget,
    DetailTargetIdentity,
    DetailTargetNotFoundError,
    TargetLivenessValidator,
    _new_detail_session,
    normalize_detail_kind,
)


SnapshotFunction = Callable[[DetailTarget], Any]
RestoreFunction = Callable[[DetailTarget, Any], None]
StateEqualityFunction = Callable[[Any, Any], bool]
SubjectGetter = Callable[[DetailTarget], Any]
DetailActionFunction = Callable[..., Any]
PresetApplyFunction = Callable[[DetailTarget, str, str], Any]
SnapshotPayloadUpdater = Callable[[Any], tuple[Any, bool]]


class DetailStateError(RuntimeError):
    """スナップショットまたは復元契約の違反。"""


class DetailSnapshotTargetMismatchError(DetailStateError):
    """別対象のスナップショットを復元しようとした。"""


class DetailActionConfirmationRequired(DetailStateError):
    """確認が必要な独立操作を未確認で実行しようとした。"""


class DetailIndependentActionBoundaryError(DetailStateError):
    """独立即時操作が親キャンセル対象の状態を変更した。"""


class DetailActionRequiresClosedSessionError(DetailStateError):
    """対象を無効化し得る操作を親画面が開いたまま実行しようとした。"""


class DetailRestoreError(DetailStateError):
    """全アダプターを逆順で試した後に報告する復元エラー。"""

    def __init__(self, failures: Sequence[tuple[str, BaseException]]) -> None:
        self.failures = tuple(failures)
        details = ", ".join(
            f"{name}: {type(error).__name__}: {error}"
            for name, error in self.failures
        )
        super().__init__(f"detail restore failed: {details}")


def _default_payloads_equal(left: Any, right: Any) -> bool:
    try:
        return bool(left == right)
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class DetailStateAdapter:
    kind: str
    name: str
    snapshot: SnapshotFunction
    restore: RestoreFunction
    equivalent: StateEqualityFunction = _default_payloads_equal

    def __post_init__(self) -> None:
        kind = "*" if self.kind == "*" else normalize_detail_kind(self.kind)
        object.__setattr__(self, "kind", kind)
        name = str(self.name or "").strip()
        if not name:
            raise DetailContractError("state adapter name is required")
        object.__setattr__(self, "name", name)
        if not callable(self.snapshot) or not callable(self.restore) or not callable(self.equivalent):
            raise DetailContractError("state adapter callbacks must be callable")


@dataclass(frozen=True, slots=True)
class DetailSnapshotFragment:
    adapter: DetailStateAdapter
    payload: Any


@dataclass(frozen=True, slots=True)
class DetailStateSnapshot:
    target_identity: DetailTargetIdentity
    fragments: tuple[DetailSnapshotFragment, ...]


class DetailStateRegistry:
    """登録順で取得し、スナップショットへ復元関数も固定するレジストリ。"""

    def __init__(self) -> None:
        self._adapters: list[DetailStateAdapter] = []

    def add(self, adapter: DetailStateAdapter) -> DetailStateAdapter:
        if any(item.kind == adapter.kind and item.name == adapter.name for item in self._adapters):
            raise DetailContractError(
                f"state adapter already registered: {adapter.kind}:{adapter.name}"
            )
        self._adapters.append(adapter)
        return adapter

    def register(
        self,
        kind: str,
        name: str,
        snapshot: SnapshotFunction,
        restore: RestoreFunction,
        equivalent: StateEqualityFunction | None = None,
    ) -> DetailStateAdapter:
        comparator = equivalent or _default_payloads_equal
        return self.add(DetailStateAdapter(kind, name, snapshot, restore, comparator))

    def unregister(self, kind: str, name: str) -> None:
        normalized_kind = "*" if kind == "*" else normalize_detail_kind(kind)
        for index, adapter in enumerate(self._adapters):
            if adapter.kind == normalized_kind and adapter.name == name:
                del self._adapters[index]
                return
        raise KeyError(f"state adapter is not registered: {normalized_kind}:{name}")

    def adapters_for(self, target: DetailTarget) -> tuple[DetailStateAdapter, ...]:
        return tuple(adapter for adapter in self._adapters if adapter.kind in {"*", target.kind})

    def capture(self, target: DetailTarget) -> DetailStateSnapshot:
        adapters = self.adapters_for(target)
        if not adapters:
            raise DetailStateError(
                f"no state adapter is registered for {target.kind}:{target.stable_id}"
            )
        fragments = tuple(
            DetailSnapshotFragment(adapter, adapter.snapshot(target))
            for adapter in adapters
        )
        return DetailStateSnapshot(DetailTargetIdentity.from_target(target), fragments)


DEFAULT_DETAIL_STATE_REGISTRY = DetailStateRegistry()


def register_detail_state_adapter(
    kind: str,
    name: str,
    snapshot: SnapshotFunction,
    restore: RestoreFunction,
    equivalent: StateEqualityFunction | None = None,
) -> DetailStateAdapter:
    return DEFAULT_DETAIL_STATE_REGISTRY.register(
        kind,
        name,
        snapshot,
        restore,
        equivalent,
    )


def snapshot_detail_state(
    target: DetailTarget,
    *,
    registry: DetailStateRegistry = DEFAULT_DETAIL_STATE_REGISTRY,
) -> DetailStateSnapshot:
    return registry.capture(target)


def transform_detail_snapshot_fragment(
    snapshot: DetailStateSnapshot,
    adapter_name: str,
    updater: SnapshotPayloadUpdater,
) -> tuple[DetailStateSnapshot, bool]:
    """1アダプターの退避値だけを不変更新し、他の取消対象は保持する。"""

    if not isinstance(snapshot, DetailStateSnapshot):
        raise DetailStateError("detail state snapshot is required")
    name = str(adapter_name or "").strip()
    if not name or not callable(updater):
        raise DetailContractError("snapshot adapter name and updater are required")
    changed = False
    found = False
    fragments: list[DetailSnapshotFragment] = []
    for fragment in snapshot.fragments:
        if fragment.adapter.name != name:
            fragments.append(fragment)
            continue
        if found:
            raise DetailStateError(f"duplicate snapshot adapter: {name}")
        found = True
        payload, fragment_changed = updater(fragment.payload)
        changed = bool(fragment_changed)
        fragments.append(
            DetailSnapshotFragment(fragment.adapter, payload)
            if changed
            else fragment
        )
    if not found:
        raise DetailStateError(f"snapshot adapter is missing: {name}")
    if not changed:
        return snapshot, False
    return DetailStateSnapshot(snapshot.target_identity, tuple(fragments)), True


def apply_preset_to_target(
    target: DetailTarget,
    preset_type: str,
    preset_name: str,
    *,
    applier: PresetApplyFunction | None = None,
    context: Any = None,
) -> Any:
    """固定済み対象へだけプリセットを適用する公開境界。"""

    preset_type = str(preset_type or "").strip()
    preset_name = str(preset_name or "").strip()
    if not preset_type or not preset_name:
        raise DetailContractError("preset type and name are required")
    if applier is not None:
        return applier(target, preset_type, preset_name)
    if context is None:
        raise DetailContractError("context or an explicit preset applier is required")
    from ..operators import detail_preset_apply_op

    return detail_preset_apply_op.apply_preset_to_target(
        context,
        target,
        preset_type,
        preset_name,
    )


def restore_detail_state(target: DetailTarget, snapshot: DetailStateSnapshot) -> None:
    _validate_snapshot_target(target, snapshot)
    failures: list[tuple[str, BaseException]] = []
    for fragment in reversed(snapshot.fragments):
        try:
            fragment.adapter.restore(target, fragment.payload)
        except Exception as exc:  # 全復元を試してからまとめて報告する
            failures.append((fragment.adapter.name, exc))
    if failures:
        raise DetailRestoreError(failures)


def begin_detail_session(
    target: DetailTarget,
    mode: DetailMode | str,
    *,
    registry: DetailStateRegistry = DEFAULT_DETAIL_STATE_REGISTRY,
    target_validator: TargetLivenessValidator,
    opening_snapshot: DetailStateSnapshot | None = None,
    **layout_options: Any,
) -> DetailSession:
    snapshot = (
        snapshot_detail_state(target, registry=registry)
        if opening_snapshot is None
        else opening_snapshot
    )
    if snapshot.target_identity != DetailTargetIdentity.from_target(target):
        raise DetailSnapshotTargetMismatchError("opening snapshot belongs to a different target")
    return _new_detail_session(
        target,
        mode,
        snapshot,
        target_validator=target_validator,
        **layout_options,
    )


def commit_detail_session(session: DetailSession) -> None:
    session.validate_target()
    session.mark_committed()


def cancel_detail_session(session: DetailSession) -> None:
    session.require_cancellable()
    snapshot = session.opening_snapshot
    if not isinstance(snapshot, DetailStateSnapshot):
        error = DetailStateError("session does not contain a detail state snapshot")
        session.mark_restore_failed(error)
        raise error
    try:
        session.validate_target()
        restore_detail_state(session.target, snapshot)
    except Exception as exc:
        session.mark_restore_failed(exc)
        raise
    session.mark_cancelled()


def execute_detail_action(
    session: DetailSession,
    spec: DetailActionSpec,
    action: DetailActionFunction,
    *args: Any,
    confirmed: bool = False,
    **kwargs: Any,
) -> Any:
    """固定対象を第1引数に渡し、操作境界を記録して実行する。"""

    session.require_open()
    session.validate_target()
    if spec.boundary is DetailActionBoundary.EXCLUDED:
        raise DetailContractError("excluded actions cannot run inside a detail dialog")
    if spec.requires_confirmation and not confirmed:
        raise DetailActionConfirmationRequired(spec.action_id)
    if not callable(action):
        raise DetailContractError("detail action must be callable")
    if spec.closes_parent_before_run:
        raise DetailActionRequiresClosedSessionError(spec.action_id)
    if spec.boundary is DetailActionBoundary.INDEPENDENT_IMMEDIATE:
        return _execute_independent_action(session, spec, action, args, kwargs)
    return _execute_transactional_action(session, spec, action, args, kwargs)


def _execute_transactional_action(
    session: DetailSession,
    spec: DetailActionSpec,
    action: DetailActionFunction,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
) -> Any:
    """子操作単位で退避し、失敗時は親画面を開いたまま直前へ戻す。"""

    before = _capture_current_session_state(session)
    try:
        result = action(session.target, *args, **kwargs)
        session.validate_target()
        session.record_action(spec, result)
        return result
    except Exception as action_error:
        try:
            restore_detail_state(session.target, before)
        except Exception as restore_error:
            session.mark_restore_failed(restore_error)
            raise restore_error from action_error
        raise


def execute_closed_detail_action(
    session: DetailSession,
    spec: DetailActionSpec,
    action: DetailActionFunction,
    *args: Any,
    confirmed: bool = False,
    **kwargs: Any,
) -> Any:
    """親を確定または復元して閉じた後、固定IDだけで独立操作を行う。"""

    if session.status not in {DetailSessionStatus.COMMITTED, DetailSessionStatus.CANCELLED}:
        raise DetailActionRequiresClosedSessionError(spec.action_id)
    if spec.boundary is not DetailActionBoundary.INDEPENDENT_IMMEDIATE:
        raise DetailContractError("closed-session actions must be independent")
    if not spec.closes_parent_before_run:
        raise DetailContractError("action is not declared as parent-closing")
    if spec.requires_confirmation and not confirmed:
        raise DetailActionConfirmationRequired(spec.action_id)
    if not callable(action):
        raise DetailContractError("detail action must be callable")
    session.validate_target()
    identity = DetailTargetIdentity.from_target(session.target)
    result = action(identity, *args, **kwargs)
    session.record_closed_independent_action(spec, result)
    return result


def _execute_independent_action(
    session: DetailSession,
    spec: DetailActionSpec,
    action: DetailActionFunction,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
) -> Any:
    """固定IDだけを渡し、親snapshotとの状態重複を実行時にも拒否する。"""

    before = _capture_current_session_state(session)
    identity = DetailTargetIdentity.from_target(session.target)
    try:
        result = action(identity, *args, **kwargs)
        session.validate_target()
        after = _capture_current_session_state(session)
    except DetailTargetNotFoundError as exc:
        session.mark_restore_failed(exc)
        raise DetailActionRequiresClosedSessionError(spec.action_id) from exc
    except Exception:
        restore_detail_state(session.target, before)
        raise
    if not _snapshots_equivalent(before, after):
        restore_detail_state(session.target, before)
        raise DetailIndependentActionBoundaryError(spec.action_id)
    session.record_action(spec, result)
    return result


def _capture_current_session_state(session: DetailSession) -> DetailStateSnapshot:
    template = session.opening_snapshot
    if not isinstance(template, DetailStateSnapshot):
        raise DetailStateError("session does not contain a detail state snapshot")
    fragments = tuple(
        DetailSnapshotFragment(fragment.adapter, fragment.adapter.snapshot(session.target))
        for fragment in template.fragments
    )
    return DetailStateSnapshot(DetailTargetIdentity.from_target(session.target), fragments)


def _snapshots_equivalent(left: DetailStateSnapshot, right: DetailStateSnapshot) -> bool:
    if len(left.fragments) != len(right.fragments):
        return False
    for old_fragment, new_fragment in zip(left.fragments, right.fragments, strict=True):
        adapter = old_fragment.adapter
        if adapter is not new_fragment.adapter:
            return False
        if not adapter.equivalent(old_fragment.payload, new_fragment.payload):
            return False
    return True


def make_attribute_state_adapter(
    kind: str,
    name: str,
    attributes: Sequence[str],
    *,
    subject_getter: SubjectGetter | None = None,
) -> DetailStateAdapter:
    """オブジェクトまたは辞書の属性群をdeepcopyする基本アダプター。"""

    field_names = tuple(str(field or "").strip() for field in attributes)
    if not field_names or any(not field for field in field_names):
        raise DetailContractError("at least one non-empty snapshot attribute is required")
    getter = subject_getter or (lambda target: target.data)

    def snapshot(target: DetailTarget) -> tuple[tuple[str, bool, Any], ...]:
        subject = getter(target)
        return tuple(_capture_attribute(subject, field) for field in field_names)

    def restore(target: DetailTarget, payload: Any) -> None:
        subject = getter(target)
        for field, existed, value in payload:
            _restore_attribute(subject, field, existed, value)

    return DetailStateAdapter(kind, name, snapshot, restore)


def _validate_snapshot_target(target: DetailTarget, snapshot: DetailStateSnapshot) -> None:
    target_identity = DetailTargetIdentity.from_target(target)
    if target_identity != snapshot.target_identity:
        raise DetailSnapshotTargetMismatchError(
            f"snapshot belongs to {snapshot.target_identity!r}"
        )


def _capture_attribute(subject: Any, field: str) -> tuple[str, bool, Any]:
    if isinstance(subject, Mapping):
        if field in subject:
            return field, True, deepcopy(subject[field])
        return field, False, None
    if hasattr(subject, field):
        return field, True, deepcopy(getattr(subject, field))
    return field, False, None


def _restore_attribute(subject: Any, field: str, existed: bool, value: Any) -> None:
    if isinstance(subject, MutableMapping):
        if existed:
            subject[field] = deepcopy(value)
        else:
            subject.pop(field, None)
        return
    if existed:
        setattr(subject, field, deepcopy(value))
    elif hasattr(subject, field):
        delattr(subject, field)


__all__ = [
    "DEFAULT_DETAIL_STATE_REGISTRY",
    "DetailActionConfirmationRequired",
    "DetailActionRequiresClosedSessionError",
    "DetailIndependentActionBoundaryError",
    "DetailRestoreError",
    "DetailSnapshotFragment",
    "DetailSnapshotTargetMismatchError",
    "DetailStateAdapter",
    "DetailStateError",
    "DetailStateRegistry",
    "DetailStateSnapshot",
    "begin_detail_session",
    "cancel_detail_session",
    "commit_detail_session",
    "execute_detail_action",
    "execute_closed_detail_action",
    "make_attribute_state_adapter",
    "register_detail_state_adapter",
    "restore_detail_state",
    "snapshot_detail_state",
    "transform_detail_snapshot_fragment",
]
