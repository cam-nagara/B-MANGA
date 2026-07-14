from __future__ import annotations

from contextlib import nullcontext
import importlib.util
from pathlib import Path
import sys
import types
from types import SimpleNamespace

import pytest


def _load_runtime_modules():
    root = Path(__file__).resolve().parents[1]
    package_name = "_bmanga_detail_runtime_cleanup_test"
    package = types.ModuleType(package_name)
    package.__path__ = [str(root)]
    sys.modules[package_name] = package
    for child, path in (("utils", root / "utils"), ("operators", root / "operators")):
        module = types.ModuleType(f"{package_name}.{child}")
        module.__path__ = [str(path)]
        sys.modules[module.__name__] = module

    def load(qualified_suffix: str, path: Path):
        qualified = f"{package_name}.{qualified_suffix}"
        spec = importlib.util.spec_from_file_location(qualified, path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
        return module

    detail = load("utils.detail_dialog", root / "utils" / "detail_dialog.py")
    state = load("utils.detail_dialog_state", root / "utils" / "detail_dialog_state.py")
    adapters = load(
        "utils.detail_state_adapters", root / "utils" / "detail_state_adapters.py"
    )
    load("utils.detail_target_resolver", root / "utils" / "detail_target_resolver.py")
    runtime = load(
        "operators.detail_dialog_runtime",
        root / "operators" / "detail_dialog_runtime.py",
    )
    balloon_curve = types.ModuleType(f"{package_name}.utils.balloon_curve_object")
    balloon_curve.suspend_auto_sync = nullcontext
    sys.modules[balloon_curve.__name__] = balloon_curve
    return detail, state, adapters, runtime


DETAIL, STATE, ADAPTERS, RUNTIME = _load_runtime_modules()


def _field(state, name):
    return next(value for field, _kind, value in state.fields if field == name)


def _rna_registry(kind: str):
    registry = STATE.DetailStateRegistry()

    def capture_reference(target):
        if kind == "coma":
            return {"preset_name": target.data.border.preset_name}
        return {
            "preset_name": target.data.custom_preset_name,
            "custom_outline_json": target.data.custom_outline_json,
        }

    def restore_reference(target, payload):
        if kind == "coma":
            target.data.border.preset_name = payload["preset_name"]
            return
        target.data.custom_outline_json = payload["custom_outline_json"]
        target.data.custom_preset_name = payload["preset_name"]

    def capture(target):
        if kind == "coma":
            border = ADAPTERS.RNAState(
                (("preset_name", "value", target.data.border.preset_name),),
                (),
            )
            data = ADAPTERS.RNAState(
                (
                    ("border", "pointer", border),
                    ("editable", "value", target.data.editable),
                ),
                (),
            )
        else:
            data = ADAPTERS.RNAState(
                (
                    (
                        "custom_preset_name",
                        "value",
                        target.data.custom_preset_name,
                    ),
                    (
                        "custom_outline_json",
                        "value",
                        target.data.custom_outline_json,
                    ),
                    ("editable", "value", target.data.editable),
                ),
                (),
            )
        return {"data": data, "params": None}

    def restore(target, payload):
        data = payload["data"]
        target.data.editable = _field(data, "editable")
        if kind == "coma":
            target.data.border.preset_name = _field(
                _field(data, "border"), "preset_name"
            )
        else:
            target.data.custom_preset_name = _field(data, "custom_preset_name")
            target.data.custom_outline_json = _field(data, "custom_outline_json")

    registry.register(kind, "preset_reference", capture_reference, restore_reference)
    registry.register(kind, "rna_values", capture, restore)
    return registry


def _session(kind: str, data, *, mode="actual"):
    target = DETAIL.DetailTarget(kind, f"{kind}-1", f"{kind}:{kind}-1", data)
    return STATE.begin_detail_session(
        target,
        mode,
        registry=_rna_registry(kind),
        target_validator=lambda _identity: True,
    )


def _register_actual(session) -> None:
    RUNTIME._OPEN_ACTUAL_SESSIONS[session.token] = session
    RUNTIME._OPEN_ACTUAL_SCENE_KEYS[session.token] = ("test", 1)


def _clear_runtime() -> None:
    RUNTIME._OPEN_ACTUAL_SESSIONS.clear()
    RUNTIME._OPEN_ACTUAL_SCENE_KEYS.clear()
    RUNTIME._OPEN_PRESET_SESSIONS.clear()
    RUNTIME._PREPARING_EFFECT_TARGET_IDS.clear()


def test_border_rename_advances_only_fixed_reference_and_cancel_baseline():
    _clear_runtime()
    data = SimpleNamespace(
        border=SimpleNamespace(preset_name="旧プリセット"),
        editable=10,
    )
    unrelated = SimpleNamespace(border=SimpleNamespace(preset_name="旧プリセット"))
    session = _session("coma", data)
    _register_actual(session)
    data.editable = 99

    changed = RUNTIME.reconcile_preset_reference_after_management(
        session.token,
        "coma",
        session.target.stable_id,
        "border",
        "旧プリセット",
        "新プリセット",
    )

    assert changed
    assert data.border.preset_name == "新プリセット"
    assert unrelated.border.preset_name == "旧プリセット"
    STATE.cancel_detail_session(session)
    assert data.border.preset_name == "新プリセット"
    assert data.editable == 10
    _clear_runtime()


def test_balloon_delete_clears_fixed_reference_without_preserving_other_edits():
    _clear_runtime()
    outline = "[[0.0,0.0],[1.0,0.0],[0.0,1.0]]"
    data = SimpleNamespace(
        custom_preset_name="削除対象",
        custom_outline_json="",
        editable=3,
    )
    session = _session("balloon", data)
    _register_actual(session)
    data.editable = 77

    changed = RUNTIME.reconcile_preset_reference_after_management(
        session.token,
        "balloon",
        session.target.stable_id,
        "balloon",
        "削除対象",
        None,
        balloon_outline_json=outline,
    )

    assert changed and data.custom_preset_name == ""
    assert data.custom_outline_json == outline
    STATE.cancel_detail_session(session)
    assert data.custom_preset_name == ""
    assert data.custom_outline_json == outline
    assert data.editable == 3
    _clear_runtime()


def test_balloon_initial_selection_uses_fixed_target_not_tool_selector():
    context = SimpleNamespace(
        window_manager=SimpleNamespace(
            bmanga_balloon_tool_preset_selector="custom:別レイヤー"
        )
    )
    target = DETAIL.DetailTarget(
        "balloon",
        "balloon-1",
        "balloon:balloon-1",
        SimpleNamespace(custom_preset_name="固定対象"),
    )
    assert RUNTIME.initial_preset_selection_for_target(context, target) == "固定対象"


def test_unregister_cleanup_restores_sessions_and_releases_every_lock(monkeypatch):
    _clear_runtime()
    actual_data = SimpleNamespace(
        border=SimpleNamespace(preset_name="A"), editable=1
    )
    preset_data = SimpleNamespace(
        custom_preset_name="B",
        custom_outline_json="",
        editable=2,
    )
    actual = _session("coma", actual_data)
    preset = _session("balloon", preset_data, mode="preset")
    actual_data.editable = 11
    preset_data.editable = 22
    _register_actual(actual)
    RUNTIME._OPEN_PRESET_SESSIONS[preset.token] = preset
    RUNTIME._PREPARING_EFFECT_TARGET_IDS.add("effect-1")
    released = []
    monkeypatch.setattr(RUNTIME, "_release_curve_sync", lambda target: released.append(target))

    failures = RUNTIME.cleanup_all_sessions(context=None)

    assert failures == ()
    assert actual.status is DETAIL.DetailSessionStatus.CANCELLED
    assert preset.status is DETAIL.DetailSessionStatus.CANCELLED
    assert actual_data.editable == 1 and preset_data.editable == 2
    assert released == [actual.target, preset.target]
    assert not RUNTIME._OPEN_ACTUAL_SESSIONS
    assert not RUNTIME._OPEN_ACTUAL_SCENE_KEYS
    assert not RUNTIME._OPEN_PRESET_SESSIONS
    assert not RUNTIME._PREPARING_EFFECT_TARGET_IDS


def test_unregister_cleanup_clears_locks_even_when_restore_and_release_fail(monkeypatch):
    _clear_runtime()
    session = _session(
        "coma",
        SimpleNamespace(border=SimpleNamespace(preset_name="A"), editable=1),
    )
    _register_actual(session)
    monkeypatch.setattr(
        RUNTIME.detail_dialog_state,
        "cancel_detail_session",
        lambda _session: (_ for _ in ()).throw(RuntimeError("restore failed")),
    )
    monkeypatch.setattr(
        RUNTIME,
        "_release_curve_sync",
        lambda _target: (_ for _ in ()).throw(RuntimeError("release failed")),
    )

    failures = RUNTIME.cleanup_all_sessions(context=None)

    assert len(failures) == 2
    assert not RUNTIME._OPEN_ACTUAL_SESSIONS
    assert not RUNTIME._OPEN_ACTUAL_SCENE_KEYS
    assert not RUNTIME._OPEN_PRESET_SESSIONS
    assert not RUNTIME._PREPARING_EFFECT_TARGET_IDS
