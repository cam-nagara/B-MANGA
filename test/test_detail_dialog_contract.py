from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

import pytest


def _load_contract_modules():
    root = Path(__file__).resolve().parents[1]
    package_name = "_bmanga_detail_contract_test"
    package = types.ModuleType(package_name)
    package.__path__ = [str(root / "utils")]
    sys.modules[package_name] = package

    def load(name: str):
        qualified_name = f"{package_name}.{name}"
        path = root / "utils" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(qualified_name, path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[qualified_name] = module
        spec.loader.exec_module(module)
        return module

    detail = load("detail_dialog")
    state = load("detail_dialog_state")
    resolver = load("detail_target_resolver")
    return detail, state, resolver


DETAIL, STATE, RESOLVER = _load_contract_modules()


class Obj:
    def __init__(self, **values) -> None:
        for key, value in values.items():
            setattr(self, key, value)


def _target(kind="text", *, stable_id="layer-1", data=None, params=None, stack_uid=None):
    if data is None:
        data = Obj()
    if kind == "effect" and params is None:
        params = Obj(effect_type="focus")
    return DETAIL.DetailTarget(
        kind=kind,
        stable_id=stable_id,
        stack_uid=stack_uid,
        data=data,
        params=params,
    )


def _noop_registry(kind="text"):
    registry = STATE.DetailStateRegistry()
    registry.register(kind, "noop", lambda _target: None, lambda _target, _saved: None)
    return registry


def _always_alive(_identity):
    return True


def test_target_contract_normalizes_kinds_and_requires_effect_params():
    target = _target("pattern_curve")
    assert target.kind == "image_path"
    with pytest.raises(DETAIL.DetailContractError):
        DETAIL.DetailTarget("unknown", "id", None, Obj())
    with pytest.raises(DETAIL.DetailContractError):
        DETAIL.DetailTarget("gp_folder", "id", None, Obj())
    with pytest.raises(DETAIL.DetailContractError):
        DETAIL.DetailTarget("effect_legacy", "id", None, Obj(), params=Obj())
    with pytest.raises(DETAIL.DetailContractError):
        DETAIL.DetailTarget("effect", "id", None, Obj())


def test_actual_detail_availability_rejects_virtual_and_missing_targets():
    target = Obj()
    assert RESOLVER.actual_detail_kind_is_supported("raster")
    assert RESOLVER.can_open_actual_detail("raster", target)
    assert not RESOLVER.can_open_actual_detail("raster", None)
    for kind in ("outside_group", "coma_preview", "balloon_group", "effect_legacy"):
        assert not RESOLVER.actual_detail_kind_is_supported(kind)
        assert not RESOLVER.can_open_actual_detail(kind, target)


def test_preset_target_is_explicit_scratch_state():
    scratch = Obj(effect_type="speed")
    target = DETAIL.resolve_preset_detail_target(
        "effect_line",
        "細い集中線",
        scratch,
        params=scratch,
    )
    assert target.kind == "effect"
    assert target.stable_id == "preset:effect_line:細い集中線"
    assert target.stack_uid is None
    assert target.object_ref is None
    assert target.data is scratch
    assert target.params is scratch
    assert target.namespace == "effect_line"


def test_preset_namespace_keeps_fill_and_gradient_with_same_name_distinct():
    fill = DETAIL.resolve_preset_detail_target("fill", "標準", Obj())
    gradient = DETAIL.resolve_preset_detail_target("gradient", "標準", Obj())
    assert fill.kind == gradient.kind == "fill"
    assert fill.stable_id == "preset:fill:標準"
    assert gradient.stable_id == "preset:gradient:標準"
    assert fill.stable_id != gradient.stable_id
    assert fill.namespace == "fill"
    assert gradient.namespace == "gradient"


def test_resolvers_reject_fallback_to_a_different_target():
    target = _target("image", stable_id="image-2", stack_uid="image:image-2")
    assert DETAIL.resolve_detail_target_from_object("image-2", lambda _key: target) is target
    assert DETAIL.resolve_detail_target_from_stack("image:image-2", lambda _key: target) is target
    with pytest.raises(DETAIL.DetailContractError):
        DETAIL.resolve_detail_target_from_object("image-1", lambda _key: target)
    with pytest.raises(DETAIL.DetailTargetNotFoundError):
        DETAIL.resolve_detail_target_from_stack("missing", lambda _key: None)


def test_kind_maximum_determines_width_once_while_visible_columns_switch():
    target = _target("balloon", data=Obj(line_style="solid", shape="ellipse"))
    initial = DETAIL.resolve_detail_layout(target, DETAIL.DetailMode.ACTUAL)
    wide_content = DETAIL.resolve_detail_layout(
        target,
        DETAIL.DetailMode.ACTUAL,
        current_columns=3,
        section_columns=(("形状",), ("線",), ("しっぽ",)),
    )
    assert initial.column_count == 1
    assert initial.max_columns == 3
    assert initial.dialog_width == wide_content.dialog_width
    assert initial.column_width == wide_content.column_width

    session = STATE.begin_detail_session(
        target,
        DETAIL.DetailMode.ACTUAL,
        registry=_noop_registry("balloon"),
        target_validator=_always_alive,
    )
    fixed_width = session.layout.dialog_width
    session.set_current_columns(3, (("形状",), ("線",), ("しっぽ",)))
    assert session.layout.column_count == 3
    assert session.layout.dialog_width == fixed_width
    with pytest.raises(AttributeError):
        session.target = _target("balloon", stable_id="other")


def test_layout_contract_rejects_a_smaller_entry_specific_maximum():
    target = _target("balloon")
    layout = DETAIL.resolve_detail_layout(target, "actual")
    with pytest.raises(DETAIL.DetailContractError):
        DETAIL.DetailLayoutSpec(
            kind="balloon",
            mode="actual",
            max_columns=1,
            column_count=1,
            dialog_width=layout.column_width + layout.outer_padding * 2,
            column_width=layout.column_width,
            column_gap=layout.column_gap,
            outer_padding=layout.outer_padding,
            available_width=None,
            screen_margin=DETAIL.DEFAULT_SCREEN_MARGIN,
            section_columns=((),),
        )
    with pytest.raises(DETAIL.DetailContractError):
        DETAIL.DetailLayoutSpec(
            kind="balloon",
            mode="actual",
            max_columns=3,
            column_count=1,
            dialog_width=3,
            column_width=1,
            column_gap=0,
            outer_padding=0,
            available_width=None,
            screen_margin=DETAIL.DEFAULT_SCREEN_MARGIN,
            section_columns=((),),
        )


def test_work_area_cap_shrinks_every_maximum_column_equally():
    target = _target("effect", params=Obj(effect_type="focus"))
    layout = DETAIL.resolve_detail_layout(target, "actual", available_width=700)
    profile = DETAIL.DETAIL_LAYOUT_PROFILES["effect"]
    expected = (
        layout.column_width * profile.max_columns
        + profile.column_gap * (profile.max_columns - 1)
        + profile.outer_padding * 2
    )
    assert layout.dialog_width == expected
    assert layout.dialog_width <= 700 - DETAIL.DEFAULT_SCREEN_MARGIN * 2
    assert layout.max_columns == 3


def test_every_supported_kind_uses_its_single_fixed_maximum_profile():
    expected = {
        "page": 1,
        "coma": 2,
        "gp": 2,
        "layer_folder": 1,
        "image": 2,
        "image_path": 2,
        "raster": 2,
        "fill": 2,
        "balloon": 3,
        "text": 2,
        "effect": 3,
        "balloon_tail": 2,
        "balloon_shape": 1,
    }
    assert {kind: profile.max_columns for kind, profile in DETAIL.DETAIL_LAYOUT_PROFILES.items()} == expected
    for kind, maximum in expected.items():
        target = _target(kind)
        compact = DETAIL.resolve_detail_layout(target, "actual", current_columns=1)
        fullest = DETAIL.resolve_detail_layout(target, "preset", current_columns=maximum)
        assert compact.dialog_width == fullest.dialog_width
        assert compact.column_width == fullest.column_width


def test_current_columns_follow_line_type_but_explicit_hint_wins():
    speed = _target("effect", params=Obj(effect_type="speed"))
    focus = _target("effect", params=Obj(effect_type="focus"))
    override = _target(
        "effect",
        params=Obj(effect_type="focus", detail_column_count=1),
    )
    assert DETAIL.current_column_count_for_target(speed, "actual") == 2
    assert DETAIL.current_column_count_for_target(focus, "actual") == 3
    assert DETAIL.current_column_count_for_target(override, "actual") == 1
    assert DETAIL.resolve_detail_layout(speed, "actual").dialog_width == DETAIL.resolve_detail_layout(
        focus, "actual"
    ).dialog_width


def test_static_two_column_drawers_use_both_columns_by_default():
    for kind in ("coma", "gp", "image", "image_path", "raster", "fill", "text", "balloon_tail"):
        target = _target(kind)
        assert DETAIL.current_column_count_for_target(target, "actual") == 2


def test_snapshot_adapters_restore_in_reverse_registration_order():
    data = Obj(value=1, curve=[1, 2])
    target = _target(data=data)
    events = []
    registry = STATE.DetailStateRegistry()

    registry.register(
        "text",
        "entry",
        lambda item: (item.data.value, list(item.data.curve)),
        lambda item, saved: (events.append("entry"), setattr(item.data, "value", saved[0])),
    )
    registry.register(
        "text",
        "curve",
        lambda item: list(item.data.curve),
        lambda item, saved: (events.append("curve"), setattr(item.data, "curve", saved)),
    )
    snapshot = STATE.snapshot_detail_state(target, registry=registry)
    data.value = 9
    data.curve[:] = [8]
    STATE.restore_detail_state(target, snapshot)
    assert events == ["curve", "entry"]
    assert data.value == 1
    assert data.curve == [1, 2]


def test_preparation_after_opening_snapshot_is_cancelled_too():
    data = Obj(value="before")
    target = _target(data=data)
    registry = STATE.DetailStateRegistry()
    registry.add(STATE.make_attribute_state_adapter("text", "value", ("value",)))
    opening = STATE.snapshot_detail_state(target, registry=registry)
    data.value = "prepared"
    session = STATE.begin_detail_session(
        target,
        "actual",
        registry=registry,
        target_validator=_always_alive,
        opening_snapshot=opening,
    )
    data.value = "edited"

    STATE.cancel_detail_session(session)

    assert data.value == "before"


def test_attribute_adapter_deep_copies_and_removes_new_attributes():
    data = Obj(settings={"size": [10]}, title="before")
    target = _target(data=data)
    registry = STATE.DetailStateRegistry()
    registry.add(
        STATE.make_attribute_state_adapter(
            "text",
            "properties",
            ("settings", "title", "temporary"),
        )
    )
    snapshot = registry.capture(target)
    data.settings["size"].append(20)
    data.title = "after"
    data.temporary = True
    STATE.restore_detail_state(target, snapshot)
    assert data.settings == {"size": [10]}
    assert data.title == "before"
    assert not hasattr(data, "temporary")


def test_cancel_restores_transaction_but_keeps_independent_immediate_result():
    data = {"value": 1}
    target = _target(data=data)
    registry = STATE.DetailStateRegistry()
    registry.add(STATE.make_attribute_state_adapter("text", "value", ("value",)))
    session = STATE.begin_detail_session(
        target,
        DETAIL.DetailMode.ACTUAL,
        registry=registry,
        target_validator=_always_alive,
        token="session-1",
    )
    external_results = []
    transactional = DETAIL.DetailActionSpec(
        "change-value",
        DETAIL.DetailActionBoundary.TRANSACTIONAL,
    )
    immediate = DETAIL.DetailActionSpec(
        "write-file",
        DETAIL.DetailActionBoundary.INDEPENDENT_IMMEDIATE,
        requires_confirmation=True,
        undo_supported=False,
    )

    STATE.execute_detail_action(
        session,
        transactional,
        lambda item, value: item.data.__setitem__("value", value),
        9,
    )
    STATE.execute_detail_action(
        session,
        immediate,
        lambda item: external_results.append(item.stable_id) or "saved",
        confirmed=True,
    )
    STATE.cancel_detail_session(session)

    assert data["value"] == 1
    assert external_results == ["layer-1"]
    assert transactional.parent_cancel_restores
    assert not immediate.parent_cancel_restores
    assert session.status is DETAIL.DetailSessionStatus.CANCELLED
    assert session.opening_snapshot is None
    assert session.independent_actions[0].result == "saved"


def test_independent_action_cannot_overlap_parent_snapshot_state():
    data = {"value": 1}
    target = _target(data=data)
    registry = STATE.DetailStateRegistry()
    registry.add(STATE.make_attribute_state_adapter("text", "value", ("value",)))
    session = STATE.begin_detail_session(
        target,
        "actual",
        registry=registry,
        target_validator=_always_alive,
    )
    transactional = DETAIL.DetailActionSpec(
        "change-value",
        DETAIL.DetailActionBoundary.TRANSACTIONAL,
    )
    immediate = DETAIL.DetailActionSpec(
        "unsafe-immediate",
        DETAIL.DetailActionBoundary.INDEPENDENT_IMMEDIATE,
    )
    STATE.execute_detail_action(
        session,
        transactional,
        lambda item: item.data.__setitem__("value", 2),
    )

    def mutate_snapshot_state(identity):
        assert not hasattr(identity, "data")
        data["value"] = 9

    with pytest.raises(STATE.DetailIndependentActionBoundaryError):
        STATE.execute_detail_action(session, immediate, mutate_snapshot_state)
    assert data["value"] == 2
    assert session.independent_actions == ()
    STATE.cancel_detail_session(session)
    assert data["value"] == 1


def test_confirmation_and_excluded_boundaries_block_before_callback():
    session = STATE.begin_detail_session(
        _target(),
        "actual",
        registry=_noop_registry(),
        target_validator=_always_alive,
    )
    calls = []
    immediate = DETAIL.DetailActionSpec(
        "destructive",
        DETAIL.DetailActionBoundary.INDEPENDENT_IMMEDIATE,
        requires_confirmation=True,
    )
    excluded = DETAIL.DetailActionSpec("unsafe", DETAIL.DetailActionBoundary.EXCLUDED)
    with pytest.raises(STATE.DetailActionConfirmationRequired):
        STATE.execute_detail_action(session, immediate, lambda _target: calls.append("ran"))
    with pytest.raises(DETAIL.DetailContractError):
        STATE.execute_detail_action(session, excluded, lambda _target: calls.append("ran"))
    assert calls == []


def test_snapshot_cannot_restore_a_different_stable_target():
    registry = STATE.DetailStateRegistry()
    registry.add(STATE.make_attribute_state_adapter("text", "value", ("value",)))
    first = _target(stable_id="first", data={"value": 1})
    second = _target(stable_id="second", data={"value": 2})
    snapshot = registry.capture(first)
    with pytest.raises(STATE.DetailSnapshotTargetMismatchError):
        STATE.restore_detail_state(second, snapshot)


def test_snapshot_cannot_cross_stack_uids_of_the_same_stable_object():
    registry = STATE.DetailStateRegistry()
    registry.add(STATE.make_attribute_state_adapter("text", "value", ("value",)))
    first = _target(stable_id="same", stack_uid="text:same", data={"value": 1})
    virtual = _target(stable_id="same", stack_uid="virtual:same", data={"value": 2})
    snapshot = registry.capture(first)
    with pytest.raises(STATE.DetailSnapshotTargetMismatchError):
        STATE.restore_detail_state(virtual, snapshot)


def test_restore_attempts_all_adapters_and_reports_failures():
    target = _target()
    registry = STATE.DetailStateRegistry()
    events = []

    def fail(_target, _payload):
        events.append("first")
        raise RuntimeError("failed")

    registry.register("text", "first", lambda _target: None, fail)
    registry.register(
        "text",
        "second",
        lambda _target: None,
        lambda _target, _payload: events.append("second"),
    )
    snapshot = registry.capture(target)
    with pytest.raises(STATE.DetailRestoreError) as error:
        STATE.restore_detail_state(target, snapshot)
    assert events == ["second", "first"]
    assert error.value.failures[0][0] == "first"


def test_failed_cancel_keeps_snapshot_and_can_retry_restore():
    data = {"value": 1}
    target = _target(data=data)
    registry = STATE.DetailStateRegistry()
    attempts = []

    def restore(_target, saved):
        attempts.append(saved)
        if len(attempts) == 1:
            raise RuntimeError("temporary failure")
        data["value"] = saved

    registry.register("text", "value", lambda _target: data["value"], restore)
    session = STATE.begin_detail_session(
        target,
        "actual",
        registry=registry,
        target_validator=_always_alive,
    )
    data["value"] = 9
    with pytest.raises(STATE.DetailRestoreError):
        STATE.cancel_detail_session(session)
    assert session.status is DETAIL.DetailSessionStatus.RESTORE_FAILED
    assert isinstance(session.opening_snapshot, STATE.DetailStateSnapshot)
    assert isinstance(session.restore_error, STATE.DetailRestoreError)
    STATE.cancel_detail_session(session)
    assert data["value"] == 1
    assert session.status is DETAIL.DetailSessionStatus.CANCELLED


def test_target_invalidating_action_requires_parent_to_close_first():
    alive = {"layer-1": True}
    session = STATE.begin_detail_session(
        _target(),
        "actual",
        registry=_noop_registry(),
        target_validator=lambda identity: alive.get(identity.stable_id, False),
    )
    action = DETAIL.DetailActionSpec(
        "delete-target",
        DETAIL.DetailActionBoundary.INDEPENDENT_IMMEDIATE,
        closes_parent_before_run=True,
        invalidates_target=True,
    )
    delete = lambda identity: alive.__setitem__(identity.stable_id, False) or "deleted"
    with pytest.raises(STATE.DetailActionRequiresClosedSessionError):
        STATE.execute_detail_action(session, action, delete)
    STATE.cancel_detail_session(session)
    assert STATE.execute_closed_detail_action(session, action, delete) == "deleted"
    assert not alive["layer-1"]
    assert session.independent_actions[-1].result == "deleted"


def test_undeclared_target_invalidation_stops_and_keeps_recovery_snapshot():
    alive = {"layer-1": True}
    session = STATE.begin_detail_session(
        _target(),
        "actual",
        registry=_noop_registry(),
        target_validator=lambda identity: alive.get(identity.stable_id, False),
    )
    action = DETAIL.DetailActionSpec(
        "bad-delete",
        DETAIL.DetailActionBoundary.INDEPENDENT_IMMEDIATE,
    )
    delete = lambda identity: alive.__setitem__(identity.stable_id, False)
    with pytest.raises(STATE.DetailActionRequiresClosedSessionError):
        STATE.execute_detail_action(session, action, delete)
    assert session.status is DETAIL.DetailSessionStatus.RESTORE_FAILED
    assert session.opening_snapshot is not None
    alive["layer-1"] = True
    STATE.cancel_detail_session(session)


def test_liveness_failure_preserves_snapshot_until_target_returns():
    alive = {"layer-1": True}
    session = STATE.begin_detail_session(
        _target(),
        "actual",
        registry=_noop_registry(),
        target_validator=lambda identity: alive.get(identity.stable_id, False),
    )
    alive["layer-1"] = False
    with pytest.raises(DETAIL.DetailTargetNotFoundError):
        session.set_preset_selection("標準")
    with pytest.raises(DETAIL.DetailTargetNotFoundError):
        STATE.cancel_detail_session(session)
    assert session.status is DETAIL.DetailSessionStatus.RESTORE_FAILED
    assert session.opening_snapshot is not None
    alive["layer-1"] = True
    STATE.cancel_detail_session(session)
    assert session.status is DETAIL.DetailSessionStatus.CANCELLED


def test_commit_discards_snapshot_and_closes_layout_changes():
    session = STATE.begin_detail_session(
        _target("image"),
        "actual",
        registry=_noop_registry("image"),
        target_validator=_always_alive,
    )
    STATE.commit_detail_session(session)
    assert session.status is DETAIL.DetailSessionStatus.COMMITTED
    assert session.opening_snapshot is None
    with pytest.raises(DETAIL.DetailSessionClosedError):
        session.set_current_columns(2)


def test_session_cannot_open_without_a_cancel_restore_adapter():
    assert "new_detail_session" not in DETAIL.__all__
    with pytest.raises(STATE.DetailStateError):
        STATE.begin_detail_session(
            _target(),
            "actual",
            registry=STATE.DetailStateRegistry(),
            target_validator=_always_alive,
        )


def _run_standalone() -> None:
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
    print(f"BMANGA_DETAIL_DIALOG_CONTRACT_OK: {len(tests)} tests")


if __name__ == "__main__":
    _run_standalone()
