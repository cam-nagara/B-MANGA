"""Blender 実機用: B-Name 右クリックメニュー項目の確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _create_work(work_dir: Path):
    result = bpy.ops.bname.work_new(filepath=str(work_dir))
    assert result == {"FINISHED"}, result
    work = bpy.context.scene.bname_work
    page = work.pages[0]
    result = bpy.ops.bname.coma_add()
    assert result == {"FINISHED"}, result

    balloon = page.balloons.add()
    balloon.id = "menu_balloon"
    balloon.x_mm = 20.0
    balloon.y_mm = 20.0
    balloon.width_mm = 30.0
    balloon.height_mm = 20.0

    text = page.texts.add()
    text.id = "menu_text"
    text.body = "右クリック"
    text.x_mm = 60.0
    text.y_mm = 20.0
    text.width_mm = 30.0
    text.height_mm = 20.0

    from bname_dev.operators import effect_line_op
    from bname_dev.utils import gp_layer_parenting as gp_parent
    from bname_dev.utils import gpencil as gp_utils
    from bname_dev.utils.geom import mm_to_m

    effect_line_op._create_effect_layer(
        bpy.context,
        (20.0, 60.0, 35.0, 35.0),
        parent_key="",
    )

    gp_obj = gp_utils.ensure_master_gpencil(bpy.context.scene)
    gp_layer = gp_obj.data.layers.new("menu_gp")
    gp_parent.set_parent_key(gp_layer, "")
    frame = gp_utils.ensure_active_frame(gp_layer)
    assert frame is not None and getattr(frame, "drawing", None) is not None
    assert gp_utils.add_stroke_to_drawing(
        frame.drawing,
        [
            (mm_to_m(100.0), mm_to_m(40.0), 0.0),
            (mm_to_m(120.0), mm_to_m(60.0), 0.0),
        ],
    )

    raster_result = bpy.ops.bname.raster_layer_add("EXEC_DEFAULT", dpi=30, bit_depth="gray8", enter_paint=False)
    assert "FINISHED" in raster_result, raster_result

    image = bpy.context.scene.bname_image_layers.add()
    image.id = "menu_image"
    image.title = "画像"
    image.x_mm = 100.0
    image.y_mm = 70.0
    image.width_mm = 20.0
    image.height_mm = 15.0

    from bname_dev.utils import layer_stack as layer_stack_utils

    layer_stack_utils.sync_layer_stack_after_data_change(bpy.context)
    return work


def _stack_index_for_kind(kind: str) -> int:
    from bname_dev.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(bpy.context)
    assert stack is not None
    for index, item in enumerate(stack):
        if str(getattr(item, "kind", "") or "") == kind:
            return index
    raise AssertionError(f"stack kind not found: {kind}")


def _clear_layer_selection() -> None:
    from bname_dev.utils import layer_stack as layer_stack_utils
    from bname_dev.utils import object_selection

    object_selection.clear(bpy.context)
    layer_stack_utils.clear_all_selection(bpy.context)


def _assert_menu_for_kind(kind: str) -> None:
    from bname_dev.ui import context_menu
    from bname_dev.utils import layer_stack as layer_stack_utils

    _clear_layer_selection()
    index = _stack_index_for_kind(kind)
    assert layer_stack_utils.select_stack_index(bpy.context, index)
    items = context_menu.selection_command_items(bpy.context)
    labels = [str(item.get("label", "")) for item in items]
    expected = ["詳細設定", "コピー", "貼り付け", "複製", "リンク複製"]
    if kind in {"balloon", "effect"}:
        expected.append("中心点を中心へ戻す")
    if kind in {"balloon", "text", "effect"}:
        expected.append("自由変形をリセット")
    if kind == "balloon":
        expected.extend(["拡大・縮小", "回転"])
    expected.append("選択レイヤーをリンク")
    expected.append("リンクを解除")
    if kind == "balloon":
        expected.extend(["フキダシを結合", "しっぽをコピー", "しっぽを貼り付け"])
    expected.append("削除")
    assert labels == expected, (kind, labels)
    for item in items:
        op_id = str(item.get("operator", "") or "")
        namespace, name = op_id.split(".", 1)
        assert getattr(getattr(bpy.ops, namespace), name, None) is not None, (kind, op_id)
    enabled = {str(item.get("label", "")): bool(item.get("enabled", False)) for item in items}
    assert enabled["詳細設定"]
    assert enabled["コピー"] is (kind in {"gp", "effect", "raster", "balloon", "text"}), (kind, enabled)
    assert enabled["貼り付け"] is False, (kind, enabled)
    assert enabled["複製"]
    assert enabled["削除"]
    assert enabled["リンク複製"] is (kind in {"balloon", "effect"}), (kind, enabled)
    if kind in {"balloon", "effect"}:
        assert enabled["中心点を中心へ戻す"] is True, (kind, enabled)
    if kind in {"balloon", "text", "effect"}:
        assert enabled["自由変形をリセット"] is True, (kind, enabled)
    if kind == "balloon":
        assert enabled["拡大・縮小"] is True, (kind, enabled)
        assert enabled["回転"] is True, (kind, enabled)
    assert enabled["選択レイヤーをリンク"] is False, (kind, enabled)
    assert enabled["リンクを解除"] is False, (kind, enabled)
    if kind == "balloon":
        assert enabled["フキダシを結合"] is False, enabled
        assert enabled["しっぽをコピー"] is False, enabled
        assert enabled["しっぽを貼り付け"] is False, enabled


def _assert_link_selected_menu() -> None:
    from bname_dev.ui import context_menu
    from bname_dev.utils import layer_stack as layer_stack_utils

    _clear_layer_selection()
    stack = layer_stack_utils.sync_layer_stack(bpy.context)
    assert stack is not None
    selected = 0
    for index, item in enumerate(stack):
        if str(getattr(item, "kind", "") or "") not in {"balloon", "text"}:
            continue
        if selected == 0:
            assert layer_stack_utils.select_stack_index(bpy.context, index)
        else:
            assert layer_stack_utils.set_item_selected(bpy.context, item, True)
        selected += 1
        if selected >= 2:
            break
    assert selected >= 2
    items = context_menu.selection_command_items(bpy.context)
    enabled = {str(item.get("label", "")): bool(item.get("enabled", False)) for item in items}
    assert enabled["選択レイヤーをリンク"] is True, enabled
    assert enabled["リンクを解除"] is False, enabled
    result = bpy.ops.bname.layer_stack_link_selected("EXEC_DEFAULT")
    assert result == {"FINISHED"}, result
    # リンク後はメニューの「リンクを解除」が有効になり、
    # リンクボタン (同オペレーター) の再実行で解除される (トグル)
    items = context_menu.selection_command_items(bpy.context)
    enabled = {str(item.get("label", "")): bool(item.get("enabled", False)) for item in items}
    assert enabled["リンクを解除"] is True, enabled
    result = bpy.ops.bname.layer_stack_link_selected("EXEC_DEFAULT")
    assert result == {"FINISHED"}, result
    from bname_dev.utils import layer_links

    assert not layer_links.selected_any_linked(bpy.context), "トグルでリンクが解除されていません"
    # 再リンクして解除オペレーター単体も確認
    result = bpy.ops.bname.layer_stack_link_selected("EXEC_DEFAULT")
    assert result == {"FINISHED"}, result
    assert layer_links.selected_any_linked(bpy.context)
    result = bpy.ops.bname.layer_stack_unlink_selected("EXEC_DEFAULT")
    assert result == {"FINISHED"}, result
    assert not layer_links.selected_any_linked(bpy.context), "リンク解除オペレーターが効いていません"


class _FakeLayout:
    def __init__(self):
        self.operator_context = "EXEC_DEFAULT"
        self.enabled = True
        self.ops = []

    def row(self, align=False):
        _ = align
        return self

    def label(self, **kwargs):
        _ = kwargs

    def separator(self):
        pass

    def operator(self, op_id, **kwargs):
        self.ops.append((op_id, kwargs))
        return type("_OpProps", (), {})()


def _assert_menu_draw_does_not_resync() -> None:
    from bname_dev.ui import context_menu
    from bname_dev.utils import layer_stack as layer_stack_utils

    _clear_layer_selection()
    index = _stack_index_for_kind("effect")
    assert layer_stack_utils.select_stack_index(bpy.context, index)
    original_sync = layer_stack_utils.sync_layer_stack

    def _forbidden_sync(*_args, **_kwargs):
        raise AssertionError("menu draw must not resync layer stack")

    try:
        layer_stack_utils.sync_layer_stack = _forbidden_sync
        layout = _FakeLayout()
        context_menu._draw_layer_commands(layout, bpy.context)
        labels = [op_id for op_id, _kwargs in layout.ops]
        assert "bname.layer_stack_detail" in labels or "bname.layer_detail_open" in labels, labels
        assert "bname.layer_clipboard_copy" in labels, labels
        assert "bname.layer_stack_duplicate" in labels, labels
        assert "bname.layer_stack_delete" in labels, labels
    finally:
        layer_stack_utils.sync_layer_stack = original_sync


def _assert_viewport_tool_menu_paths(work) -> None:
    from bname_dev.operators import object_tool_op, selection_context_menu
    from bname_dev.utils import layer_stack as layer_stack_utils
    from bname_dev.utils import object_selection

    class _Event:
        ctrl = False
        shift = False

    page = work.pages[0]
    text_key = object_selection.text_key(page, page.texts[0])
    balloon_key = object_selection.balloon_key(page, page.balloons[0])

    original_hit = object_tool_op.hit_object_at_event
    original_activate = object_tool_op.activate_hit
    original_call = selection_context_menu._call_selection_menu
    calls = []
    modes = []
    try:
        selection_context_menu._call_selection_menu = lambda _context: calls.append("menu") or True
        object_tool_op.hit_object_at_event = lambda _context, _event: None
        assert selection_context_menu.open_for_viewport_object(bpy.context, _Event())
        assert calls == ["menu"], calls

        object_selection.set_keys(bpy.context, [text_key, balloon_key])
        object_tool_op.hit_object_at_event = lambda _context, _event: {"kind": "text", "key": text_key}
        object_tool_op.activate_hit = lambda _context, _hit, *, mode: modes.append(mode)
        assert selection_context_menu.open_for_viewport_object(bpy.context, _Event())
        assert modes == ["add"], modes

        _clear_layer_selection()
        stack = layer_stack_utils.sync_layer_stack(bpy.context)
        assert stack is not None
        text_index = _stack_index_for_kind("text")
        balloon_index = _stack_index_for_kind("balloon")
        assert layer_stack_utils.select_stack_index(bpy.context, text_index)
        assert layer_stack_utils.set_item_selected(bpy.context, stack[text_index], True)
        assert layer_stack_utils.set_item_selected(bpy.context, stack[balloon_index], True)
        modes.clear()
        assert selection_context_menu.open_for_viewport_object(bpy.context, _Event())
        assert modes == ["add"], modes
    finally:
        object_tool_op.hit_object_at_event = original_hit
        object_tool_op.activate_hit = original_activate
        selection_context_menu._call_selection_menu = original_call


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_context_menu_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        work = _create_work(temp_root / "Context_Menu.bname")
        for kind in ("page", "coma", "gp", "effect", "raster", "image", "balloon", "text"):
            _assert_menu_for_kind(kind)
        assert hasattr(bpy.types, "BNAME_OT_view_context_menu")
        _assert_link_selected_menu()
        _assert_menu_draw_does_not_resync()
        _assert_viewport_tool_menu_paths(work)
        print("BNAME_CONTEXT_MENU_COMMANDS_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
