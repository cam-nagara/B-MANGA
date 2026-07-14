from __future__ import annotations

import ast
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_detail_and_state(package_name: str):
    package = ModuleType(package_name)
    package.__path__ = [str(ROOT / "utils")]
    sys.modules[package_name] = package

    def load(name: str):
        qualified = f"{package_name}.{name}"
        spec = importlib.util.spec_from_file_location(
            qualified,
            ROOT / "utils" / f"{name}.py",
        )
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
        return module

    detail = load("detail_dialog")
    return detail, load("detail_dialog_state")


DETAIL, STATE = _load_detail_and_state("_bmanga_detail_action_contract")


def _load_basic():
    root_name = "_bmanga_detail_action_drawer"
    root = ModuleType(root_name)
    root.__path__ = [str(ROOT)]
    sys.modules[root_name] = root
    utils = ModuleType(f"{root_name}.utils")
    utils.__path__ = [str(ROOT / "utils")]
    utils.detail_dialog = DETAIL
    sys.modules[utils.__name__] = utils
    sys.modules[f"{utils.__name__}.detail_dialog"] = DETAIL
    panels = ModuleType(f"{root_name}.panels")
    panels.__path__ = [str(ROOT / "panels")]
    sys.modules[panels.__name__] = panels
    drawers = ModuleType(f"{root_name}.panels.detail_drawers")
    drawers.__path__ = [str(ROOT / "panels" / "detail_drawers")]
    sys.modules[drawers.__name__] = drawers
    name = f"{drawers.__name__}.basic"
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "panels" / "detail_drawers" / "basic.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_runtime():
    root_name = "_bmanga_detail_action_runtime"
    root = ModuleType(root_name)
    root.__path__ = [str(ROOT)]
    sys.modules[root_name] = root
    utils = ModuleType(f"{root_name}.utils")
    utils.__path__ = [str(ROOT / "utils")]
    sys.modules[utils.__name__] = utils
    utils.detail_dialog = DETAIL
    sys.modules[f"{utils.__name__}.detail_dialog"] = DETAIL
    for stub_name in ("detail_dialog_state", "detail_state_adapters", "detail_target_resolver"):
        stub = ModuleType(f"{utils.__name__}.{stub_name}")
        setattr(utils, stub_name, stub)
        sys.modules[stub.__name__] = stub
    operators = ModuleType(f"{root_name}.operators")
    operators.__path__ = [str(ROOT / "operators")]
    sys.modules[operators.__name__] = operators
    name = f"{operators.__name__}.detail_dialog_runtime"
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "operators" / "detail_dialog_runtime.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BASIC = _load_basic()
RUNTIME = _load_runtime()


class _RawLayout:
    def __init__(self):
        self.calls = []

    def row(self, *_args, **_kwargs):
        return self

    def operator(self, operator_id, *_args, **_kwargs):
        self.calls.append(("operator", operator_id))
        return SimpleNamespace()

    def operator_menu_enum(self, operator_id, field, *_args, **_kwargs):
        self.calls.append(("operator_menu_enum", operator_id, field))
        return SimpleNamespace()


def _property_options(call: ast.Call) -> set[str]:
    for keyword in call.keywords:
        if keyword.arg != "options":
            continue
        if isinstance(keyword.value, (ast.Set, ast.Tuple, ast.List)):
            return {
                str(item.value)
                for item in keyword.value.elts
                if isinstance(item, ast.Constant)
            }
    return set()


def test_detail_operator_fixed_identifiers_are_hidden_from_blender_ui():
    internal_fields = {
        "session_token",
        "target_kind",
        "target_id",
        "stable_id",
        "stack_uid",
        "page_id",
        "balloon_id",
        "text_id",
        "raster_id",
        "parent_session_token",
        "parent_target_kind",
        "parent_target_id",
        "preset_type",
        "tail_index",
        "force",
        "direction",
        "uid",
        "index",
        "preserve_edge_selection",
        "offset_from_selection",
        "bmanga_id",
        "kind",
    }
    fixed_preset_names = {
        ("preset_detail_op.py", "BMANGA_OT_preset_detail_edit"),
        ("detail_preset_management_op.py", "BMANGA_OT_detail_preset_rename"),
        ("detail_preset_management_op.py", "BMANGA_OT_detail_preset_duplicate"),
        ("detail_preset_management_op.py", "BMANGA_OT_detail_preset_delete"),
        ("detail_preset_management_op.py", "BMANGA_OT_detail_preset_move"),
    }
    paths = (
        "detail_preset_apply_op.py",
        "detail_preset_management_op.py",
        "detail_transaction_action_op.py",
        "raster_detail_action_op.py",
        "preset_detail_op.py",
        "layer_detail_op.py",
        "layer_stack_detail_op.py",
    )
    checked = set()
    for filename in paths:
        tree = ast.parse((ROOT / "operators" / filename).read_text(encoding="utf-8"))
        for class_node in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            for statement in class_node.body:
                if not isinstance(statement, ast.AnnAssign):
                    continue
                field = getattr(statement.target, "id", "")
                is_fixed_name = (
                    field == "preset_name"
                    and (filename, class_node.name) in fixed_preset_names
                )
                if field not in internal_fields and not is_fixed_name:
                    continue
                property_call = (
                    statement.value
                    if isinstance(statement.value, ast.Call)
                    else statement.annotation
                )
                assert isinstance(property_call, ast.Call), (
                    f"{filename}:{class_node.name}.{field} がProperty宣言ではありません"
                )
                assert "HIDDEN" in _property_options(property_call), (
                    f"{filename}:{class_node.name}.{field} がBlender UIへ露出します"
                )
                checked.add((filename, class_node.name, field))
    assert ("detail_preset_apply_op.py", "BMANGA_OT_detail_preset_apply", "target_id") in checked
    assert ("layer_stack_detail_op.py", "BMANGA_OT_layer_stack_detail", "index") in checked


def test_canonical_registry_assigns_every_required_boundary_explicitly():
    transactional = {
        "bmanga.detail_tail_add",
        "bmanga.detail_tail_remove",
        "bmanga.detail_tail_preset_apply",
        "bmanga.detail_text_ruby_add",
        "bmanga.detail_text_ruby_clear",
        "bmanga.detail_text_linked_balloon_set",
        "bmanga.detail_preset_apply",
    }
    independent = {
        "bmanga.detail_raster_paint_enter",
        "bmanga.detail_raster_save_png",
        "bmanga.preset_detail_edit",
        "bmanga.detail_preset_add",
        "bmanga.detail_preset_rename",
        "bmanga.detail_preset_duplicate",
        "bmanga.detail_preset_delete",
        "bmanga.detail_preset_move",
    }
    excluded = {
        "bmanga.effect_line_base_path_edit",
        "bmanga.raster_layer_resample",
        "bmanga.coma_merge_selected",
        "bmanga.image_layer_remove",
        "bmanga.balloon_regenerate_keep_edit",
        "bmanga.balloon_tail_add_target",
        "bmanga.text_ruby_add_dialog",
    }
    for action_id in transactional:
        spec = DETAIL.get_detail_action_spec(action_id)
        assert spec.boundary is DETAIL.DetailActionBoundary.TRANSACTIONAL
        assert not spec.undo_supported
    for action_id in independent:
        assert DETAIL.get_detail_action_spec(action_id).boundary is (
            DETAIL.DetailActionBoundary.INDEPENDENT_IMMEDIATE
        )
    for preset_type in (
        "border",
        "balloon",
        "text",
        "effect_line",
        "fill",
        "gradient",
        "image_path",
    ):
        for suffix in ("add_local", "rename", "duplicate", "delete", "move"):
            action_id = f"bmanga.{preset_type}_preset_{suffix}"
            assert DETAIL.get_detail_action_spec(action_id).boundary is (
                DETAIL.DetailActionBoundary.INDEPENDENT_IMMEDIATE
            )
    for action_id in excluded:
        assert DETAIL.get_detail_action_spec(action_id).boundary is DETAIL.DetailActionBoundary.EXCLUDED
    assert all(key == spec.action_id for key, spec in DETAIL.DETAIL_ACTION_SPECS.items())
    with pytest.raises(DETAIL.DetailContractError):
        DETAIL.get_detail_action_spec("bmanga.not_classified")


def test_registered_operator_runtime_identifier_is_normalized():
    spec = DETAIL.get_detail_action_spec("BMANGA_OT_detail_tail_add")
    assert spec.action_id == "bmanga.detail_tail_add"
    assert spec.boundary is DETAIL.DetailActionBoundary.TRANSACTIONAL


def test_classified_layout_refuses_unknown_and_excluded_operators():
    raw = _RawLayout()
    layout = BASIC.classified_layout(raw)
    layout.row().operator("bmanga.detail_tail_add")
    layout.operator_menu_enum("bmanga.detail_preset_apply", "preset_name")
    assert raw.calls == [
        ("operator", "bmanga.detail_tail_add"),
        ("operator_menu_enum", "bmanga.detail_preset_apply", "preset_name"),
    ]
    with pytest.raises(DETAIL.DetailContractError):
        layout.operator("bmanga.effect_line_base_path_edit")
    with pytest.raises(DETAIL.DetailContractError):
        layout.operator("bmanga.not_classified")


def test_every_drawer_operator_uses_the_helper_and_is_drawable():
    drawer_dir = ROOT / "panels" / "detail_drawers"
    seen = set()
    for path in drawer_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if path.name != "basic.py":
            assert ".operator(" not in source
            assert ".operator_menu_enum(" not in source
        tree = ast.parse(source)
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            name = getattr(call.func, "id", "")
            if name not in {"detail_operator", "detail_operator_menu_enum"}:
                continue
            assert len(call.args) >= 2 and isinstance(call.args[1], ast.Constant)
            action_id = str(call.args[1].value)
            seen.add(action_id)
            assert DETAIL.get_detail_action_spec(action_id).boundary is not (
                DETAIL.DetailActionBoundary.EXCLUDED
            )
    assert {
        "bmanga.detail_tail_add",
        "bmanga.detail_text_ruby_add",
        "bmanga.detail_text_linked_balloon_set",
        "bmanga.detail_preset_apply",
        "bmanga.detail_preset_add",
        "bmanga.detail_preset_move",
        "bmanga.detail_raster_save_png",
    } <= seen


def test_transaction_children_have_no_undo_and_require_fixed_identifiers():
    path = ROOT / "operators" / "detail_transaction_action_op.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in (item for item in tree.body if isinstance(item, ast.ClassDef)):
        idname = None
        options = None
        for statement in node.body:
            if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
                continue
            name = getattr(statement.targets[0], "id", "")
            if name == "bl_idname":
                idname = ast.literal_eval(statement.value)
            elif name == "bl_options":
                options = ast.literal_eval(statement.value)
        if not str(idname or "").startswith("bmanga.detail_"):
            continue
        assert options == {"INTERNAL"}
        assert DETAIL.get_detail_action_spec(idname).boundary is (
            DETAIL.DetailActionBoundary.TRANSACTIONAL
        )
    fixed_helper = ast.get_source_segment(
        source,
        next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_find_fixed_entry"),
    )
    assert "get_active_page" not in fixed_helper
    assert "owner_id" in fixed_helper and "entry_id" in fixed_helper
    assert "session_token" in source and "target_id" in source

    preset_source = (ROOT / "operators" / "preset_detail_op.py").read_text(encoding="utf-8")
    assert "parent_session_token" in preset_source
    assert "detail_action_is_allowed" in preset_source
    assert "record_detail_action" in preset_source


def _target(kind: str, stable_id: str, data):
    return DETAIL.DetailTarget(kind, stable_id, None, data)


def test_parent_cancel_restores_nested_tails_and_ruby_without_child_undo():
    cases = (
        ("balloon", "balloon-1", SimpleNamespace(tails=[{"type": "straight"}]), "tails"),
        ("text", "text-1", SimpleNamespace(ruby_spans=[{"ruby": "ほん"}]), "ruby_spans"),
    )
    for kind, stable_id, data, field in cases:
        registry = STATE.DetailStateRegistry()
        registry.add(STATE.make_attribute_state_adapter(kind, "nested", (field,)))
        target = _target(kind, stable_id, data)
        session = STATE.begin_detail_session(
            target,
            DETAIL.DetailMode.ACTUAL,
            registry=registry,
            target_validator=lambda _identity: True,
        )
        before = deepcopy(getattr(data, field))
        getattr(data, field).append({"new": True})
        STATE.cancel_detail_session(session)
        assert getattr(data, field) == before
        assert session.status is DETAIL.DetailSessionStatus.CANCELLED


def test_initial_preset_selection_is_session_local_and_decodes_target_type():
    wm = SimpleNamespace(
        bmanga_border_preset_selector="画面の枠線",
        bmanga_balloon_tool_preset_selector="custom:角丸",
        bmanga_text_tool_preset_selector="本文",
        bmanga_effect_line_tool_preset_selector="集中線",
        bmanga_fill_tool_preset_selector="ベタ",
        bmanga_gradient_tool_preset_selector="夕焼け",
        bmanga_image_path_tool_preset_selector="鎖",
    )
    context = SimpleNamespace(window_manager=wm)
    before = deepcopy(wm.__dict__)
    coma = SimpleNamespace(border=SimpleNamespace(preset_name="適用中の枠線"))
    assert RUNTIME.initial_preset_selection_for_target(context, SimpleNamespace(kind="coma", data=coma)) == "適用中の枠線"
    assert RUNTIME.initial_preset_selection_for_target(context, SimpleNamespace(kind="balloon", data=object())) == "角丸"
    assert RUNTIME.initial_preset_selection_for_target(context, SimpleNamespace(kind="text", data=object())) == "本文"
    assert RUNTIME.initial_preset_selection_for_target(context, SimpleNamespace(kind="effect", data=object())) == "集中線"
    assert RUNTIME.initial_preset_selection_for_target(context, SimpleNamespace(kind="image_path", data=object())) == "鎖"
    solid = SimpleNamespace(fill_type="solid")
    assert RUNTIME.initial_preset_selection_for_target(context, SimpleNamespace(kind="fill", data=solid, namespace=None)) == "ベタ"
    gradient = SimpleNamespace(fill_type="gradient")
    assert RUNTIME.initial_preset_selection_for_target(context, SimpleNamespace(kind="fill", data=gradient, namespace=None)) == "夕焼け"
    assert wm.__dict__ == before


def test_prepare_failure_and_entry_execute_failure_have_rollback_paths(monkeypatch):
    target = SimpleNamespace(kind="text", stable_id="text-1", data=SimpleNamespace(value="before"))
    snapshot = {"value": "before"}
    state = SimpleNamespace(
        snapshot_detail_state=lambda *_args, **_kwargs: snapshot,
        restore_detail_state=lambda item, saved: setattr(item.data, "value", saved["value"]),
    )
    monkeypatch.setattr(RUNTIME, "detail_dialog_state", state)
    monkeypatch.setattr(RUNTIME.detail_state_adapters, "ACTUAL_DETAIL_STATE_REGISTRY", object(), raising=False)
    monkeypatch.setattr(RUNTIME, "_resync_restored_target", lambda *_args: None)

    def fail_prepare(_context, item):
        item.data.value = "partly prepared"
        raise RuntimeError("prepare failed")

    monkeypatch.setattr(RUNTIME, "prepare_actual_target", fail_prepare)
    with pytest.raises(RuntimeError, match="prepare failed"):
        RUNTIME.begin_actual_session(SimpleNamespace(), target)
    assert target.data.value == "before"
    for path in (
        ROOT / "operators" / "layer_detail_op.py",
        ROOT / "operators" / "layer_stack_detail_op.py",
    ):
        source = path.read_text(encoding="utf-8")
        assert "rollback_failed_actual_session(context, session)" in source
        assert "_abort_opening_session(context)" in source
        assert 'if "CANCELLED" in result:' in source


def test_opening_abort_discards_session_even_if_restore_fails(monkeypatch):
    session = SimpleNamespace(token="opening-session")
    RUNTIME._OPEN_ACTUAL_SESSIONS[session.token] = session

    def fail_cancel(_context, _session):
        raise RuntimeError("restore failed")

    monkeypatch.setattr(RUNTIME, "cancel_actual_session", fail_cancel)
    with pytest.raises(RuntimeError, match="restore failed"):
        RUNTIME.abort_opening_actual_session(SimpleNamespace(), session)
    assert session.token not in RUNTIME._OPEN_ACTUAL_SESSIONS
