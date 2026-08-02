from __future__ import annotations

from pathlib import Path

import pytest

from bmanga_core.lifecycle import (
    LifecycleError,
    LifecycleEvent,
    LifecycleEventKind,
    LifecycleMachine,
    LifecycleState,
    LifecycleTarget,
    SessionObservation,
)


def test_lifecycle_scheduler_is_the_only_main_addon_timer_owner():
    root = Path(__file__).resolve().parents[1]
    sources = [root / "preferences.py"]
    for directory in (
        "core",
        "io",
        "keymap",
        "operators",
        "panels",
        "typography",
        "ui",
        "utils",
    ):
        sources.extend((root / directory).rglob("*.py"))
    offenders = []
    for path in sources:
        if path == root / "utils" / "lifecycle_scheduler.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "bpy.app.timers." in text:
            offenders.append(path.relative_to(root).as_posix())
    assert offenders == []


def test_lifecycle_coordinator_is_the_only_file_and_history_handler_owner():
    root = Path(__file__).resolve().parents[1]
    sources = [root / "preferences.py"]
    for directory in (
        "core",
        "io",
        "keymap",
        "operators",
        "panels",
        "typography",
        "ui",
        "utils",
    ):
        sources.extend((root / directory).rglob("*.py"))
    needles = tuple(
        f".{name}.append("
        for name in (
            "load_post",
            "save_pre",
            "save_post",
            "save_post_fail",
            "undo_pre",
            "undo_post",
            "redo_pre",
            "redo_post",
        )
    )
    offenders = []
    for path in sources:
        if path == root / "utils" / "handlers.py":
            continue
        text = path.read_text(encoding="utf-8")
        if any(needle in text for needle in needles):
            offenders.append(path.relative_to(root).as_posix())
    assert offenders == []


def _target(name: str, *, page: str = "", coma: str = "") -> LifecycleTarget:
    role = "coma" if coma else "page" if page else "work"
    return LifecycleTarget(
        filepath=f"C:/Works/Sample.bmanga/{name}",
        work_root="C:/Works/Sample.bmanga",
        role=role,
        project_uid="project_" + "1" * 32,
        page_uid=page,
        coma_uid=coma,
    )


def test_successful_transition_uses_the_required_phase_order() -> None:
    machine = LifecycleMachine()
    source = _target("work.blend")
    target = _target(
        "pages/page_11111111111111111111111111111111/page.blend",
        page="page_" + "1" * 32,
    )
    snapshot = machine.begin(source, target)
    assert snapshot.source == source
    assert machine.state is LifecycleState.PREPARING
    machine.advance(LifecycleState.SAVING_SOURCE)
    machine.advance(LifecycleState.OPENING_TARGET)
    machine.observe_load(target)
    assert machine.state is LifecycleState.HYDRATING
    observation = SessionObservation(
        machine.session.session_id,
        source.project_uid,
        4,
        "a" * 64,
    )
    machine.complete(target, observation=observation)
    assert machine.state is LifecycleState.STABLE
    assert machine.current == target
    assert machine.session == observation


@pytest.mark.parametrize(
    "failure_state",
    (
        LifecycleState.PREPARING,
        LifecycleState.SAVING_SOURCE,
        LifecycleState.OPENING_TARGET,
        LifecycleState.HYDRATING,
    ),
)
def test_failure_from_every_transition_phase_rolls_back(failure_state) -> None:
    machine = LifecycleMachine()
    source = _target("work.blend")
    target = _target(
        "pages/page_11111111111111111111111111111111/page.blend",
        page="page_" + "1" * 32,
    )
    machine.begin(source, target)
    while machine.state is not failure_state:
        machine.advance(
            {
                LifecycleState.PREPARING: LifecycleState.SAVING_SOURCE,
                LifecycleState.SAVING_SOURCE: LifecycleState.OPENING_TARGET,
                LifecycleState.OPENING_TARGET: LifecycleState.HYDRATING,
            }[machine.state]
        )
    machine.begin_rollback(f"failed at {failure_state.value}")
    machine.finish_rollback(source)
    assert machine.state is LifecycleState.STABLE
    assert machine.current == source
    assert machine.pending is None
    assert failure_state.value in machine.last_error


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("work_root", "C:/Works/Replaced.bmanga"),
        ("role", "page"),
        ("project_uid", "project_" + "2" * 32),
        ("page_uid", "page_" + "2" * 32),
        ("coma_uid", "coma_" + "2" * 32),
    ),
)
def test_rollback_rejects_same_path_with_different_identity(
    field_name,
    replacement,
) -> None:
    machine = LifecycleMachine()
    source = _target(
        "pages/page_11111111111111111111111111111111/"
        "comas/coma_11111111111111111111111111111111/scene.blend",
        page="page_" + "1" * 32,
        coma="coma_" + "1" * 32,
    )
    target = _target("work.blend")
    machine.begin(source, target)
    machine.begin_rollback("forced")
    actual_values = {
        name: getattr(source, name)
        for name in LifecycleTarget.__dataclass_fields__
    }
    actual_values[field_name] = replacement
    actual = LifecycleTarget(**actual_values)

    with pytest.raises(LifecycleError, match="source identity"):
        machine.finish_rollback(actual)

    assert machine.state is LifecycleState.ROLLING_BACK
    assert machine.pending is not None


def test_old_or_wrong_load_cannot_complete_transition() -> None:
    machine = LifecycleMachine()
    source = _target("work.blend")
    target = _target(
        "pages/page_11111111111111111111111111111111/page.blend",
        page="page_" + "1" * 32,
    )
    machine.begin(source, target)
    machine.advance(LifecycleState.SAVING_SOURCE)
    machine.advance(LifecycleState.OPENING_TARGET)
    with pytest.raises(LifecycleError):
        machine.observe_load(source)


def test_same_target_can_be_reopened_inside_hydration_boundary() -> None:
    machine = LifecycleMachine()
    source = _target("work.blend")
    target = _target(
        "pages/page_11111111111111111111111111111111/"
        "comas/coma_11111111111111111111111111111111/scene.blend",
        page="page_" + "1" * 32,
        coma="coma_" + "1" * 32,
    )
    machine.begin(source, target)
    machine.advance(LifecycleState.SAVING_SOURCE)
    machine.advance(LifecycleState.OPENING_TARGET)
    machine.observe_load(target)
    assert machine.state is LifecycleState.HYDRATING
    machine.observe_load(target)
    assert machine.state is LifecycleState.HYDRATING
    with pytest.raises(LifecycleError):
        machine.observe_load(
            _target(
                "pages/page_11111111111111111111111111111111/"
                "comas/coma_22222222222222222222222222222222/scene.blend",
                page="page_" + "1" * 32,
                coma="coma_" + "2" * 32,
            )
        )


def test_history_restore_has_a_distinct_hydration_boundary() -> None:
    machine = LifecycleMachine()
    current = _target("work.blend")
    machine.observe_load(current)
    generation = machine.begin_history_restore()
    assert generation == machine.generation
    assert machine.state is LifecycleState.HYDRATING
    machine.finish_history_restore(current)
    assert machine.state is LifecycleState.STABLE


def test_typed_handler_event_is_recorded_without_changing_state() -> None:
    machine = LifecycleMachine()
    event = LifecycleEvent(
        LifecycleEventKind.SAVE_PRE,
        "C:/Works/Sample.bmanga/work.blend",
        {"reason": "test"},
    )

    machine.observe_event(event)

    assert machine.last_event == event
    assert machine.state is LifecycleState.STABLE
