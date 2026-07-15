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
        summary = ", ".join(f"{key}={len(value)}" for key, value in preset_names.items())
        print(
            "BMANGA_DETAIL_PRESET_LIST_OBJECT_TOOL_OK: "
            f"{summary}, linked_balloon={len(linked_balloon_names)}"
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
