"""作品・ページ・コマの保存、読込、Undo/Redoを統合するCoordinator。"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import bpy

from ..bmanga_core.lifecycle import (
    LifecycleError,
    LifecycleEvent,
    LifecycleEventKind,
    LifecycleMachine,
    LifecycleState,
    LifecycleTarget,
    SessionObservation,
)
from . import lifecycle_scheduler, log, paths


_logger = log.get_logger(__name__)
MACHINE = LifecycleMachine()
PhaseHook = Callable[[LifecycleState], None]
Action = Callable[[], bool | None]
HandlerAction = Callable[[], object]


@dataclass(frozen=True, slots=True)
class TransitionOutcome:
    succeeded: bool
    error: str = ""
    failed_phase: LifecycleState | None = None
    rolled_back: bool = False


class ManagedTransition:
    """既存Operatorを段階的にCoordinatorへ載せるための遷移guard。"""

    def __init__(
        self,
        context,
        target: LifecycleTarget,
        *,
        phase_hook: PhaseHook | None = None,
    ) -> None:
        self.context = context
        self.target = target
        self.phase_hook = phase_hook
        self.source = current_target(context)
        self.ui_snapshot = _capture_ui(context)
        self.completed = False
        self.rolled_back = False
        self._blend_switch = None

    def __enter__(self) -> "ManagedTransition":
        from . import file_transition_runtime

        MACHINE.begin(self.source, self.target)
        lifecycle_scheduler.invalidate(reason="file transition")
        self._blend_switch = file_transition_runtime.blend_switch()
        self._blend_switch.__enter__()
        try:
            _call_phase_hook(self.phase_hook, LifecycleState.PREPARING)
            return self
        except Exception as exc:
            try:
                self.rolled_back = _rollback_transition(
                    self.source,
                    self.ui_snapshot,
                    error=exc,
                    rollback_open=None,
                )
            finally:
                self._close_blend_switch()
            raise

    def saving_source(self) -> None:
        MACHINE.advance(LifecycleState.SAVING_SOURCE)
        _call_phase_hook(self.phase_hook, LifecycleState.SAVING_SOURCE)

    def opening_target(self) -> None:
        MACHINE.advance(LifecycleState.OPENING_TARGET)
        _call_phase_hook(self.phase_hook, LifecycleState.OPENING_TARGET)

    def complete(self) -> None:
        if MACHINE.state is LifecycleState.OPENING_TARGET:
            MACHINE.advance(LifecycleState.HYDRATING)
        _call_phase_hook(self.phase_hook, LifecycleState.HYDRATING)
        actual = validate_current_target(self.target, context=bpy.context)
        MACHINE.complete(actual, observation=_observation())
        self.completed = True

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        try:
            if self.completed:
                return False
            if MACHINE.pending is not None:
                self.rolled_back = _rollback_transition(
                    self.source,
                    self.ui_snapshot,
                    error=exc or "transition left before completion",
                    rollback_open=None,
                )
        finally:
            self._close_blend_switch()
        return False

    def _close_blend_switch(self) -> None:
        blend_switch = self._blend_switch
        self._blend_switch = None
        if blend_switch is not None:
            blend_switch.__exit__(None, None, None)


@dataclass(frozen=True, slots=True)
class _UiSnapshot:
    active_page_index: int
    active_page_uid: str
    active_coma_index: int
    active_coma_uid: str
    current_page_id: str
    current_coma_id: str
    current_coma_page_id: str
    mode: str
    overview: bool
    active_layer_kind: str


def _path_key(path: str | Path) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    return os.path.normcase(str(Path(text).resolve(strict=False)))


def _custom_value(owner, name: str) -> str:
    if owner is None:
        return ""
    try:
        return str(owner.get(name, "") or "")
    except (AttributeError, ReferenceError, TypeError):
        return ""


def _work_from_context(context=None):
    scene = getattr(context or bpy.context, "scene", None)
    return getattr(scene, "bmanga_work", None) if scene is not None else None


def target_for_path(
    filepath: str | Path,
    *,
    work_root: str | Path | None = None,
    context=None,
) -> LifecycleTarget:
    from ..io import domain_projection_ids
    from . import page_file_scene

    key = _path_key(filepath)
    if not key:
        return LifecycleTarget()
    raw_path = Path(key)
    root = page_file_scene.find_work_root(raw_path)
    if root is None and str(work_root or "").strip():
        root = Path(work_root).resolve(strict=False)
    work = _work_from_context(context)
    context_root = _path_key(
        str(getattr(work, "work_dir", "") or "")
        if work is not None
        else ""
    )
    root_key = _path_key(root) if root is not None else ""
    project_uid = ""
    if root_key and root_key == context_root:
        project_uid = _custom_value(
            work,
            domain_projection_ids.PROJECT_UID_PROP,
        )
    elif root is not None:
        try:
            project_payload = json.loads(
                (Path(root) / "project.json").read_text(encoding="utf-8")
            )
            project_uid = str(project_payload.get("projectUid", "") or "")
        except (OSError, TypeError, ValueError):
            pass
    role = page_file_scene.ROLE_UNKNOWN
    page_uid = ""
    page_id = ""
    coma_uid = ""
    coma_id = ""
    if root is not None:
        role, page_uid, coma_uid = page_file_scene.role_from_parts(
            page_file_scene.relative_parts(raw_path, root)
        )
        if role in {page_file_scene.ROLE_PAGE, page_file_scene.ROLE_COMA}:
            try:
                resolved_role, resolved_page_id, resolved_coma_id = (
                    page_file_scene.role_from_path(raw_path, root)
                )
                if resolved_role == role:
                    page_id = resolved_page_id
                    coma_id = resolved_coma_id
            except (FileNotFoundError, KeyError, OSError, ValueError):
                pass
    return LifecycleTarget(
        filepath=key,
        work_root=_path_key(root) if root is not None else "",
        role=role,
        project_uid=project_uid,
        page_uid=page_uid,
        page_id=page_id,
        coma_uid=coma_uid,
        coma_id=coma_id,
    )


def current_target(context=None) -> LifecycleTarget:
    context = context or bpy.context
    work = _work_from_context(context)
    work_root = str(getattr(work, "work_dir", "") or "") if work is not None else ""
    return target_for_path(
        str(getattr(bpy.data, "filepath", "") or ""),
        work_root=work_root or None,
        context=context,
    )


def _capture_ui(context) -> _UiSnapshot:
    from ..core.mode import get_mode
    from ..io import domain_projection_ids

    scene = getattr(context, "scene", None)
    work = _work_from_context(context)
    page_index = int(getattr(work, "active_page_index", -1))
    page = None
    if work is not None and 0 <= page_index < len(getattr(work, "pages", ())):
        page = work.pages[page_index]
    coma_index = int(getattr(page, "active_coma_index", -1))
    coma = None
    if page is not None and 0 <= coma_index < len(getattr(page, "comas", ())):
        coma = page.comas[coma_index]
    return _UiSnapshot(
        page_index,
        _custom_value(page, domain_projection_ids.PAGE_UID_PROP),
        coma_index,
        _custom_value(coma, domain_projection_ids.COMA_UID_PROP),
        str(getattr(scene, "bmanga_current_page_id", "") or ""),
        str(getattr(scene, "bmanga_current_coma_id", "") or ""),
        str(getattr(scene, "bmanga_current_coma_page_id", "") or ""),
        str(get_mode(context)),
        bool(getattr(scene, "bmanga_overview_mode", False)),
        str(getattr(scene, "bmanga_active_layer_kind", "") or ""),
    )


def _restore_ui(context, snapshot: _UiSnapshot) -> None:
    from ..core.mode import set_mode
    from ..io import domain_projection_ids

    scene = getattr(context, "scene", None)
    work = _work_from_context(context)
    if scene is None or work is None:
        return
    page_index = next(
        (
            index
            for index, page in enumerate(getattr(work, "pages", ()) or ())
            if _custom_value(page, domain_projection_ids.PAGE_UID_PROP)
            == snapshot.active_page_uid
        ),
        snapshot.active_page_index,
    )
    if 0 <= page_index < len(getattr(work, "pages", ())):
        work.active_page_index = page_index
        page = work.pages[page_index]
        coma_index = next(
            (
                index
                for index, coma in enumerate(getattr(page, "comas", ()) or ())
                if _custom_value(coma, domain_projection_ids.COMA_UID_PROP)
                == snapshot.active_coma_uid
            ),
            snapshot.active_coma_index,
        )
        if 0 <= coma_index < len(getattr(page, "comas", ())):
            page.active_coma_index = coma_index
    scene.bmanga_current_page_id = snapshot.current_page_id
    scene.bmanga_current_coma_id = snapshot.current_coma_id
    scene.bmanga_current_coma_page_id = snapshot.current_coma_page_id
    if hasattr(scene, "bmanga_overview_mode"):
        scene.bmanga_overview_mode = snapshot.overview
    if hasattr(scene, "bmanga_active_layer_kind"):
        scene.bmanga_active_layer_kind = snapshot.active_layer_kind
    set_mode(snapshot.mode, context)


def _observation(context=None) -> SessionObservation:
    from ..io import domain_projection_ids, domain_runtime

    work = _work_from_context(context)
    if work is None:
        return SessionObservation(MACHINE.session.session_id)
    project_uid = _custom_value(
        work,
        domain_projection_ids.PROJECT_UID_PROP,
    )
    revision = int(
        _custom_value(work, domain_projection_ids.PROJECT_REVISION_PROP) or 0
    )
    content_hash = ""
    root = str(getattr(work, "work_dir", "") or "")
    if root:
        try:
            store = domain_runtime.store_for(root)
            if not store.dirty_project:
                content_hash = (
                    domain_runtime.repository_for(root)
                    .observed_project_hash()
                )
            if not content_hash:
                document = store.project
                from ..bmanga_core.domain_model import canonical_json_bytes

                content_hash = hashlib.sha256(
                    canonical_json_bytes(document)
                ).hexdigest()
        except Exception:  # noqa: BLE001
            pass
    return SessionObservation(
        MACHINE.session.session_id,
        project_uid,
        revision,
        content_hash,
    )


def validate_current_target(
    expected: LifecycleTarget,
    *,
    context=None,
) -> LifecycleTarget:
    from ..io import domain_projection_ids

    context = context or bpy.context
    actual = current_target(context)
    if not actual.same_file(expected):
        raise LifecycleError("opened mainfile does not match requested target")
    if expected.work_root and actual.work_root != expected.work_root:
        raise LifecycleError("hydrated work root does not match target")
    if expected.role == "home":
        return actual
    if expected.role == "unknown":
        return actual
    work = _work_from_context(context)
    if work is None or not bool(getattr(work, "loaded", False)):
        raise LifecycleError("target Domain was not hydrated")
    if actual.role != expected.role:
        raise LifecycleError("hydrated file role does not match target")
    if (
        expected.project_uid
        and actual.project_uid != expected.project_uid
    ):
        raise LifecycleError("hydrated project UID does not match target")
    if expected.page_uid:
        index = int(getattr(work, "active_page_index", -1))
        if not 0 <= index < len(getattr(work, "pages", ())):
            raise LifecycleError("active page is missing after hydration")
        active_page = work.pages[index]
        active_uid = _custom_value(
            active_page,
            domain_projection_ids.PAGE_UID_PROP,
        )
        if active_uid != expected.page_uid:
            raise LifecycleError("active_page_uid does not match opened page")
        scene_page_id = str(
            getattr(context.scene, "bmanga_current_page_id", "") or ""
        )
        if expected.page_id and scene_page_id != expected.page_id:
            raise LifecycleError("current page display ID does not match opened page")
    if expected.coma_uid:
        page = work.pages[int(work.active_page_index)]
        coma_index = int(getattr(page, "active_coma_index", -1))
        if not 0 <= coma_index < len(getattr(page, "comas", ())):
            raise LifecycleError("active coma is missing after hydration")
        active_uid = _custom_value(
            page.comas[coma_index],
            domain_projection_ids.COMA_UID_PROP,
        )
        if active_uid != expected.coma_uid:
            raise LifecycleError("active coma UID does not match opened coma")
    return actual


def note_load(filepath: str | Path) -> LifecycleTarget:
    """load handlerが重い同期前に渡す入口。"""

    lifecycle_scheduler.invalidate(reason="load")
    target = target_for_path(filepath, context=bpy.context)
    from . import history_runtime

    if history_runtime.is_blocked():
        history_runtime.reset_after_file_load()
        MACHINE.reset(target)
        return target
    if (
        MACHINE.state is LifecycleState.OPENING_TARGET
        and not target.filepath
    ):
        # 新規page/coma作成中のread_homefileは最終targetではない。
        return target
    MACHINE.observe_load(target)
    return target


def dispatch_event(event: LifecycleEvent):
    """Blender handler EventをLifecycleの一つの入口へ集約する。"""

    MACHINE.observe_event(event)
    if event.kind is LifecycleEventKind.LOAD:
        return note_load(event.filepath)
    if event.kind in {
        LifecycleEventKind.UNDO_PRE,
        LifecycleEventKind.REDO_PRE,
    }:
        begin_history_restore()
    return None


def handle_handler_event(
    event: LifecycleEvent,
    *,
    primary: HandlerAction | None = None,
) -> object | None:
    """Blender handlerを型付きEvent一入口から順序付きで実行する。"""

    dispatched = dispatch_event(event)
    result = primary() if primary is not None else None
    kind = event.kind
    if kind is LifecycleEventKind.LOAD:
        finalize_load_hydration(
            result is True,
            target=(
                dispatched
                if isinstance(dispatched, LifecycleTarget)
                else None
            ),
        )
    elif kind is LifecycleEventKind.SAVE_PRE:
        from . import preview_composite

        _call_handler_hook(
            "2D composite save preparation",
            preview_composite.on_lifecycle_save_pre,
        )
    elif kind is LifecycleEventKind.SAVE_POST:
        from . import file_transition_runtime, preview_composite

        _call_handler_hook(
            "file transition save completion",
            file_transition_runtime.on_lifecycle_save_post,
        )
        _call_handler_hook(
            "2D composite save completion",
            preview_composite.on_lifecycle_save_post,
        )
    elif kind is LifecycleEventKind.SAVE_FAIL:
        from . import preview_composite

        _call_handler_hook(
            "2D composite save failure",
            preview_composite.on_lifecycle_save_fail,
        )
    return result


def finalize_load_hydration(
    hydrated: bool,
    *,
    target: LifecycleTarget | None = None,
) -> None:
    """実loadとregister後の再hydrateで共有する後処理。"""

    from . import (
        active_collection_sync,
        camera_overview_sync,
        cross_addon_settings_sync,
        file_transition_runtime,
        outliner_watch,
        preview_composite,
    )

    for label, hook in (
        ("file transition", file_transition_runtime.on_lifecycle_load),
        ("2D composite", preview_composite.on_lifecycle_load),
        ("active collection", active_collection_sync.on_lifecycle_load),
        ("cross-addon settings", cross_addon_settings_sync.on_lifecycle_load),
        ("camera overview", camera_overview_sync.on_lifecycle_load),
    ):
        _call_handler_hook(label, hook)
    if hydrated:
        complete_external_load(target)
    outliner_watch.schedule_watch_timer()


def _call_handler_hook(label: str, hook: Callable[[], object]) -> None:
    try:
        hook()
    except Exception:  # noqa: BLE001
        _logger.exception("lifecycle handler hook failed: %s", label)


def complete_external_load(target: LifecycleTarget | None = None) -> None:
    if MACHINE.pending is not None:
        return
    actual = target or current_target()
    MACHINE.current = actual
    MACHINE.session = _observation()


def begin_history_restore() -> None:
    lifecycle_scheduler.invalidate(reason="history restore")
    if MACHINE.state is LifecycleState.STABLE:
        MACHINE.begin_history_restore()


def finish_history_restore() -> None:
    if MACHINE.pending is None and MACHINE.state is LifecycleState.HYDRATING:
        MACHINE.finish_history_restore(current_target())
        MACHINE.session = _observation()


def run_transition(
    context,
    target: LifecycleTarget,
    *,
    checkpoint: Action,
    open_target: Action,
    prepare: Action | None = None,
    verify: Callable[[LifecycleTarget], object] | None = None,
    rollback_open: Callable[[LifecycleTarget], bool] | None = None,
    phase_hook: PhaseHook | None = None,
) -> TransitionOutcome:
    """一つの状態機械で保存→open→hydrate→失敗復元を完結させる。"""

    from . import file_transition_runtime

    source = current_target(context)
    ui_snapshot = _capture_ui(context)
    failed_phase = LifecycleState.PREPARING
    try:
        MACHINE.begin(source, target)
        lifecycle_scheduler.invalidate(reason="file transition")
        _call_phase_hook(phase_hook, LifecycleState.PREPARING)
        if prepare is not None and prepare() is False:
            raise LifecycleError("transition preparation failed")
        MACHINE.advance(LifecycleState.SAVING_SOURCE)
        failed_phase = LifecycleState.SAVING_SOURCE
        _call_phase_hook(phase_hook, failed_phase)
        if checkpoint() is not True:
            raise LifecycleError("source checkpoint failed")
        MACHINE.advance(LifecycleState.OPENING_TARGET)
        failed_phase = LifecycleState.OPENING_TARGET
        _call_phase_hook(phase_hook, failed_phase)
        with file_transition_runtime.blend_switch():
            if open_target() is not True:
                raise LifecycleError("target open failed")
        if MACHINE.state is LifecycleState.OPENING_TARGET:
            MACHINE.advance(LifecycleState.HYDRATING)
        failed_phase = LifecycleState.HYDRATING
        _call_phase_hook(phase_hook, failed_phase)
        actual = validate_current_target(target, context=bpy.context)
        if verify is not None:
            verify(actual)
        MACHINE.complete(actual, observation=_observation())
        return TransitionOutcome(True)
    except Exception as exc:
        _logger.exception(
            "lifecycle transition failed at %s",
            failed_phase.value,
        )
        rolled_back = _rollback_transition(
            source,
            ui_snapshot,
            error=exc,
            rollback_open=rollback_open,
        )
        return TransitionOutcome(
            False,
            str(exc),
            failed_phase,
            rolled_back,
        )


def _rollback_transition(
    source: LifecycleTarget,
    ui_snapshot: _UiSnapshot,
    *,
    error: BaseException,
    rollback_open: Callable[[LifecycleTarget], bool] | None,
) -> bool:
    from ..io import blend_io
    from . import file_transition_runtime

    try:
        MACHINE.begin_rollback(error)
        lifecycle_scheduler.invalidate(reason="transition rollback")
        if _path_key(getattr(bpy.data, "filepath", "")) != source.filepath:
            with file_transition_runtime.blend_switch():
                if rollback_open is not None:
                    restored = rollback_open(source)
                elif source.filepath:
                    restored = blend_io.open_mainfile(Path(source.filepath))
                else:
                    restored = blend_io.read_homefile()
            if restored is not True:
                raise LifecycleError("source file could not be reopened")
        _restore_ui(bpy.context, ui_snapshot)
        actual = (
            validate_current_target(source, context=bpy.context)
            if source.filepath
            else current_target()
        )
        MACHINE.finish_rollback(actual if source.filepath else source)
        return True
    except Exception:  # noqa: BLE001
        _logger.exception("lifecycle rollback failed")
        work = _work_from_context(bpy.context)
        if work is not None:
            try:
                work.loaded = False
            except Exception:  # noqa: BLE001
                pass
        return False


def _call_phase_hook(
    hook: PhaseHook | None,
    state: LifecycleState,
) -> None:
    if hook is not None:
        hook(state)


def reset() -> None:
    lifecycle_scheduler.invalidate(reason="lifecycle reset")
    MACHINE.reset(current_target())


__all__ = (
    "MACHINE",
    "ManagedTransition",
    "TransitionOutcome",
    "begin_history_restore",
    "complete_external_load",
    "current_target",
    "dispatch_event",
    "finish_history_restore",
    "finalize_load_hydration",
    "handle_handler_event",
    "note_load",
    "reset",
    "run_transition",
    "transition_guard",
    "target_for_path",
    "validate_current_target",
)


def transition_guard(
    context,
    target: LifecycleTarget,
    *,
    phase_hook: PhaseHook | None = None,
) -> ManagedTransition:
    return ManagedTransition(context, target, phase_hook=phase_hook)
