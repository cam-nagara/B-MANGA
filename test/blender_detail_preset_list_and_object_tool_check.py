"""Blender実機: 詳細プリセット一覧とオブジェクトツール切替の回帰検証。"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_detail_preset_list_object_tool"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _sub(path: str):
    __import__(f"{MOD_NAME}.{path}")
    return sys.modules[f"{MOD_NAME}.{path}"]


class _ItemCollection(list):
    def add(self):
        item = SimpleNamespace(
            name="",
            identifier="",
            description="",
            preset_type="",
            target_kind="",
            target_id="",
            stack_uid="",
            session_token="",
            is_selected=False,
        )
        self.append(item)
        return item

    def clear(self):
        del self[:]


class _LayoutProbe:
    def __init__(self):
        self.template_lists: list[str] = []
        self.operators: list[tuple[str, str, str]] = []
        self.labels: list[str] = []
        self.props: list[str] = []
        self.enabled = True
        self.operator_context = ""

    def row(self, **_kwargs):
        return self

    def column(self, **_kwargs):
        return self

    def separator(self):
        return None

    def label(self, *, text="", **_kwargs):
        self.labels.append(str(text))

    def prop(self, _owner, name, **_kwargs):
        self.props.append(str(name))

    def template_list(self, list_id, *_args, **_kwargs):
        self.template_lists.append(str(list_id))

    def operator(self, operator_id, *, text="", icon="NONE", **_kwargs):
        self.operators.append((str(operator_id), str(text), str(icon)))
        return SimpleNamespace()


def _assert_all_preset_lists(context) -> dict[str, list[str]]:
    apply_op = _sub("operators.detail_preset_apply_op")
    expected_kinds = {
        "border": "coma",
        "text": "text",
        "effect_line": "effect",
        "fill": "fill",
        "gradient": "fill",
        "image_path": "image_path",
        "balloon": "balloon",
    }
    result: dict[str, list[str]] = {}
    for preset_type, kind in expected_kinds.items():
        expected_entries = apply_op._detail_preset_entries(context, preset_type)  # noqa: SLF001
        expected = [str(item[0]) for item in expected_entries]
        assert expected, f"{preset_type}: 既存プリセットが0件です"
        assert len(expected) == len(set(expected)), f"{preset_type}: 同名プリセットが重複しています"
        owner = SimpleNamespace(
            detail_preset_items=_ItemCollection(),
            detail_preset_index=-1,
        )
        target = SimpleNamespace(
            kind=kind,
            stable_id=f"{kind}-target",
            stack_uid=f"{kind}:{kind}-target",
            data=SimpleNamespace(shape="ellipse"),
        )
        session = SimpleNamespace(
            token=f"session-{preset_type}",
            target=target,
            preset_selection=expected[0],
        )
        count = apply_op.sync_detail_preset_list(
            owner,
            context,
            session,
            preset_type,
        )
        actual = [item.identifier for item in owner.detail_preset_items]
        assert count == len(expected)
        assert actual == expected, f"{preset_type}: 詳細一覧が全プリセットを保持していません"
        expected_index = 1 if preset_type == "balloon" else 0
        assert owner.detail_preset_items[expected_index].is_selected
        assert owner.detail_preset_index == expected_index
        if len(actual) > 1:
            owner.detail_preset_index = (expected_index + 1) % len(actual)
            assert apply_op.sync_detail_preset_list(
                owner,
                context,
                session,
                preset_type,
            ) == len(actual)
            assert owner.detail_preset_index == expected_index, (
                f"{preset_type}: 確認待ち／取消時に元の選択行へ戻りません"
            )
        result[preset_type] = actual
    assert result["balloon"][:6] == [
        "shape:rect",
        "shape:ellipse",
        "shape:cloud",
        "shape:fluffy",
        "shape:thorn",
        "shape:thorn-curve",
    ]
    assert "輪郭ぼかし" in result["border"]
    tail_presets = _sub("io.tail_presets")
    preset_list_ui = _sub("panels.preset_list_ui")
    expected_tail = [str(item.name) for item in tail_presets.list_all_presets(None)]
    assert expected_tail, "tail: 既存プリセットが0件です"
    preset_list_ui.refresh_preset_list(context, "tail")
    actual_tail = [item.identifier for item in context.window_manager.bmanga_tail_preset_list]
    assert actual_tail == expected_tail, "tail: 詳細一覧が全プリセットを保持していません"
    result["tail"] = actual_tail
    return result


def _assert_linked_balloon_preset_list(context) -> list[str]:
    apply_op = _sub("operators.detail_preset_apply_op")
    action_op = _sub("operators.detail_transaction_action_op")
    expected = [
        str(identifier)
        for identifier, _label, _description in action_op._linked_balloon_preset_items(  # noqa: SLF001
            None,
            context,
        )
    ]
    assert expected and expected[0] == "NONE"
    owner = SimpleNamespace(
        detail_linked_balloon_items=_ItemCollection(),
        detail_linked_balloon_index=-1,
    )
    session = SimpleNamespace(
        token="session-linked-balloon",
        target=SimpleNamespace(
            stable_id="text-target",
            data=SimpleNamespace(linked_balloon_preset=""),
        ),
    )
    count = apply_op.sync_detail_linked_balloon_preset_list(owner, context, session)
    actual = [item.identifier for item in owner.detail_linked_balloon_items]
    assert count == len(expected)
    assert actual == expected, "リンクフキダシ詳細一覧が全プリセットを保持していません"
    assert owner.detail_linked_balloon_items[0].is_selected
    assert owner.detail_linked_balloon_index == 0
    return actual


def _assert_list_selection_callbacks() -> None:
    apply_op = _sub("operators.detail_preset_apply_op")
    calls: list[tuple[str, str]] = []
    original_apply = apply_op._invoke_detail_preset_apply  # noqa: SLF001
    original_linked = apply_op._invoke_detail_linked_balloon_set  # noqa: SLF001
    try:
        apply_op._invoke_detail_preset_apply = (  # noqa: SLF001
            lambda item: calls.append(("preset", item.identifier)) or {"FINISHED"}
        )
        apply_op._invoke_detail_linked_balloon_set = (  # noqa: SLF001
            lambda item: calls.append(("linked", item.identifier)) or {"FINISHED"}
        )
        owner = SimpleNamespace(
            detail_preset_items=[SimpleNamespace(identifier="横書き")],
            detail_preset_index=0,
            detail_linked_balloon_items=[SimpleNamespace(identifier="NONE")],
            detail_linked_balloon_index=0,
            _detail_preset_list_syncing=False,
            _detail_linked_balloon_list_syncing=False,
        )
        apply_op.on_detail_preset_index_changed(owner, None)
        apply_op.on_detail_linked_balloon_index_changed(owner, None)
        assert calls == [("preset", "横書き"), ("linked", "NONE")]
        owner._detail_preset_list_syncing = True
        owner._detail_linked_balloon_list_syncing = True
        apply_op.on_detail_preset_index_changed(owner, None)
        apply_op.on_detail_linked_balloon_index_changed(owner, None)
        assert len(calls) == 2, "一覧同期中にプリセットを誤適用しています"
    finally:
        apply_op._invoke_detail_preset_apply = original_apply  # noqa: SLF001
        apply_op._invoke_detail_linked_balloon_set = original_linked  # noqa: SLF001


def _assert_unsaved_switch_confirmation_contract() -> None:
    apply_op = _sub("operators.detail_preset_apply_op")
    runtime = _sub("operators.detail_dialog_runtime")
    original_guard = runtime.preset_switch_requires_confirmation
    calls: list[tuple[object, object, dict]] = []
    executed: list[bool] = []

    class _WindowManager:
        def invoke_confirm(self, operator, event, **kwargs):
            calls.append((operator, event, kwargs))
            return {"RUNNING_MODAL"}

    target = SimpleNamespace(kind="text", stable_id="text-target")
    operator = SimpleNamespace(
        confirm_unsaved_changes=True,
        session_token="text-session",
        preset_type="text",
        preset_name="横書き",
        preset_label="横書き",
        _fixed_target=lambda _context, _preset_type: target,
        execute=lambda _context: executed.append(True) or {"FINISHED"},
        report=lambda *_args: None,
    )
    context = SimpleNamespace(window_manager=_WindowManager())
    event = SimpleNamespace(type="LEFTMOUSE")
    try:
        runtime.preset_switch_requires_confirmation = lambda *_args: True
        result = apply_op.BMANGA_OT_detail_preset_apply.invoke(operator, context, event)
        assert result == {"RUNNING_MODAL"}
        assert not executed
        assert len(calls) == 1
        kwargs = calls[0][2]
        assert kwargs["title"] == "プリセットの切り替え確認"
        assert "現在の設定はプリセットに保存されていません" in kwargs["message"]
        assert "横書き" in kwargs["message"]
        assert kwargs["confirm_text"] == "保存せずに切り替える"
        assert kwargs["icon"] == "QUESTION"

        calls.clear()
        runtime.preset_switch_requires_confirmation = lambda *_args: False
        result = apply_op.BMANGA_OT_detail_preset_apply.invoke(operator, context, event)
        assert result == {"FINISHED"}
        assert executed == [True]
        assert not calls, "未変更なのに確認ダイアログを表示しています"
    finally:
        runtime.preset_switch_requires_confirmation = original_guard


def _assert_preset_setting_change_snapshots(context) -> None:
    guard = _sub("utils.detail_preset_change_guard")
    effect_line_op = _sub("operators.effect_line_op")
    work = context.scene.bmanga_work
    page = work.pages[0]

    coma = page.comas.add()
    coma.id = coma.coma_id = "guard_coma"
    text_entry = page.texts.add()
    text_entry.id = "guard_text"
    fill_entry = context.scene.bmanga_fill_layers.add()
    fill_entry.id = "guard_fill"
    gradient_entry = context.scene.bmanga_fill_layers.add()
    gradient_entry.id = "guard_gradient"
    gradient_entry.fill_type = "gradient"
    image_path = context.scene.bmanga_image_path_layers.add()
    image_path.id = "guard_image_path"
    balloon = page.balloons.add()
    balloon.id = "guard_balloon"
    params = context.scene.bmanga_effect_line_params

    cases = (
        ("border", SimpleNamespace(data=coma, params=coma), coma.border, "width_mm"),
        ("text", SimpleNamespace(data=text_entry, params=text_entry), text_entry, "line_height"),
        ("fill", SimpleNamespace(data=fill_entry, params=fill_entry), fill_entry, "opacity"),
        (
            "gradient",
            SimpleNamespace(data=gradient_entry, params=gradient_entry),
            gradient_entry,
            "opacity",
        ),
        (
            "image_path",
            SimpleNamespace(data=image_path, params=image_path),
            image_path,
            "opacity",
        ),
        ("balloon", SimpleNamespace(data=balloon, params=balloon), balloon, "shape"),
    )
    for preset_type, target, owner, attribute in cases:
        baseline = guard.capture_preset_settings(target, preset_type)
        original = getattr(owner, attribute)
        changed = "rect" if attribute == "shape" and original != "rect" else (
            "ellipse"
            if attribute == "shape"
            else float(original) + (-2.0 if float(original) >= 99.0 else 2.0)
        )
        setattr(owner, attribute, changed)
        current = guard.capture_preset_settings(target, preset_type)
        assert guard.preset_settings_changed(baseline, current), preset_type
        setattr(owner, attribute, original)
        assert not guard.preset_settings_changed(
            baseline,
            guard.capture_preset_settings(target, preset_type),
        ), f"{preset_type}: 元の値へ戻しても変更扱いです"

    effect_line_op._set_scene_params_syncing(context.scene, True)
    try:
        baseline = guard.capture_preset_settings(
            SimpleNamespace(data=params, params=params),
            "effect_line",
        )
        original = params.brush_size_mm
        params.brush_size_mm = original + 0.125
        current = guard.capture_preset_settings(
            SimpleNamespace(data=params, params=params),
            "effect_line",
        )
        assert guard.preset_settings_changed(baseline, current)
        params.brush_size_mm = original
    finally:
        effect_line_op._set_scene_params_syncing(context.scene, False)

    # 本文や配置など、プリセットが上書きしない値だけでは確認を出さない。
    text_target = SimpleNamespace(data=text_entry, params=text_entry)
    baseline = guard.capture_preset_settings(text_target, "text")
    original_x = text_entry.x_mm
    text_entry.x_mm = original_x + 10.0
    assert not guard.preset_settings_changed(
        baseline,
        guard.capture_preset_settings(text_target, "text"),
    )
    text_entry.x_mm = original_x


def _assert_sidebar_style_layout(context) -> None:
    adapters = _sub("panels.detail_drawers.preset_adapters")
    apply_op = _sub("operators.detail_preset_apply_op")
    tail_ui = _sub("operators.balloon_tail_detail_op")
    spec = adapters.PresetUiSpec("text", "テキストプリセット", "FONT_DATA", "bmanga.text_preset")
    owner = SimpleNamespace(detail_preset_items=_ItemCollection(), detail_preset_index=-1)
    session = SimpleNamespace(
        token="session-sidebar-style",
        target=SimpleNamespace(
            kind="text",
            stable_id="text-target",
            stack_uid="text:text-target",
            data=SimpleNamespace(),
        ),
        preset_selection="横書き",
    )
    layout = _LayoutProbe()
    assert adapters._draw_compact_management(  # noqa: SLF001
        layout,
        context,
        session,
        spec,
        list_owner=owner,
    )
    assert layout.template_lists == ["BMANGA_UL_detail_presets"]
    expected_ops = {
        "bmanga.detail_preset_add",
        "bmanga.detail_preset_delete",
        "bmanga.detail_preset_move",
        "bmanga.preset_detail_edit",
        "bmanga.detail_preset_rename",
        "bmanga.detail_preset_duplicate",
    }
    assert expected_ops.issubset({item[0] for item in layout.operators})
    assert all(not text for _operator, text, _icon in layout.operators)
    assert not layout.props, "プリセット管理入力欄がリスト下へ展開されています"

    item = owner.detail_preset_items[0]
    list_layout = _LayoutProbe()
    apply_op.BMANGA_UL_detail_presets.draw_item(
        SimpleNamespace(layout_type="DEFAULT"),
        context,
        list_layout,
        owner,
        item,
        0,
        owner,
        "detail_preset_index",
        0,
    )
    assert list_layout.labels == [item.name]
    assert not list_layout.operators, "プリセット行が全幅ボタンへ戻っています"

    linked_layout = _LayoutProbe()
    linked_item = SimpleNamespace(name="なし", identifier="NONE")
    apply_op.BMANGA_UL_detail_linked_balloon_presets.draw_item(
        SimpleNamespace(layout_type="DEFAULT"),
        context,
        linked_layout,
        owner,
        linked_item,
        0,
        owner,
        "detail_linked_balloon_index",
        0,
    )
    assert linked_layout.labels == ["なし"]
    assert not linked_layout.operators, "連動フキダシ行が全幅ボタンへ戻っています"

    tail_layout = _LayoutProbe()
    tail_session = SimpleNamespace(token="tail-session", target=SimpleNamespace(stable_id="tail-target"))
    tail_ui.draw_tail_preset_list_actions(
        tail_layout,
        context,
        "p0001",
        "balloon_0001",
        0,
        session=tail_session,
    )
    assert tail_layout.template_lists == ["BMANGA_UL_presets"]
    assert {
        "bmanga.detail_tail_preset_apply",
        "bmanga.balloon_tail_preset_save",
        "bmanga.balloon_tail_preset_delete",
    }.issubset({item[0] for item in tail_layout.operators})
    assert all(not text for _operator, text, _icon in tail_layout.operators)


def _assert_object_tool_routes(context) -> None:
    modal_state = _sub("operators.coma_modal_state")
    gp_panel = _sub("panels.gpencil_panel")
    raster_op = _sub("operators.raster_layer_op")
    shortcut_op = _sub("operators.shortcut_op")

    previous_active = context.view_layer.objects.active
    original_invoke = modal_state._invoke_object_tool  # noqa: SLF001
    original_activate = modal_state.activate_object_tool
    calls: list[str] = []
    try:
        context.view_layer.objects.active = None
        modal_state._invoke_object_tool = lambda: calls.append("direct") or {"RUNNING_MODAL"}  # noqa: SLF001
        result = modal_state.activate_object_tool(context)
        assert "RUNNING_MODAL" in result

        modal_state.activate_object_tool = lambda _context: calls.append("route") or {"RUNNING_MODAL"}
        for operator_class in (
            gp_panel.BMANGA_OT_gpencil_master_mode_set,
            raster_op.BMANGA_OT_raster_layer_mode_set,
            shortcut_op.BMANGA_OT_set_mode_object,
        ):
            reports: list[tuple[set[str], str]] = []
            operator = SimpleNamespace(
                mode="OBJECT",
                report=lambda levels, message: reports.append((levels, message)),
            )
            assert operator_class.execute(operator, context) == {"FINISHED"}, operator_class.__name__
            assert not reports, (operator_class.__name__, reports)
        assert calls == ["direct", "route", "route", "route"]
    finally:
        modal_state.activate_object_tool = original_activate
        modal_state._invoke_object_tool = original_invoke  # noqa: SLF001
        context.view_layer.objects.active = previous_active


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_detail_preset_list_"))
    try:
        _load_addon()
        context = bpy.context
        work_result = bpy.ops.bmanga.work_new(
            filepath=str(temp_root / "DetailPresetListObjectTool.bmanga")
        )
        assert "FINISHED" in work_result, work_result
        preset_names = _assert_all_preset_lists(context)
        linked_balloon_names = _assert_linked_balloon_preset_list(context)
        _assert_list_selection_callbacks()
        _assert_unsaved_switch_confirmation_contract()
        _assert_preset_setting_change_snapshots(context)
        _assert_sidebar_style_layout(context)
        apply_op = _sub("operators.detail_preset_apply_op")
        work = context.scene.bmanga_work
        balloon = work.pages[0].balloons.add()
        balloon.id = "detail_preset_shape_apply"
        balloon.shape = "ellipse"
        target = SimpleNamespace(data=balloon)
        assert apply_op._apply_balloon(context, target, "shape:cloud") == "雲"  # noqa: SLF001
        assert balloon.shape == "cloud" and balloon.custom_preset_name == ""
        _assert_object_tool_routes(context)
        assert hasattr(bpy.types, "BMANGA_UL_detail_presets")
        assert hasattr(bpy.types, "BMANGA_UL_detail_linked_balloon_presets")
        for operator_type, requires_main_list in (
            (bpy.ops.bmanga.layer_stack_detail, True),
            (bpy.ops.bmanga.layer_detail_open, True),
            (bpy.ops.bmanga.preset_detail_edit, False),
        ):
            properties = operator_type.get_rna_type().properties
            if requires_main_list:
                assert "detail_preset_items" in properties
                assert "detail_preset_index" in properties
            assert "detail_linked_balloon_items" in properties
            assert "detail_linked_balloon_index" in properties
        apply_properties = bpy.ops.bmanga.detail_preset_apply.get_rna_type().properties
        assert "confirm_unsaved_changes" in apply_properties
        assert "preset_label" in apply_properties
        summary = ", ".join(f"{key}={len(value)}" for key, value in preset_names.items())
        print(
            "BMANGA_DETAIL_PRESET_LIST_OBJECT_TOOL_OK: "
            f"{summary}, linked_balloon={len(linked_balloon_names)}"
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
