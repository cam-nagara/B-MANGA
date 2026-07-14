"""Blender 実機用: B-MANGA 右クリックメニュー項目の確認."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _create_work(work_dir: Path):
    result = bpy.ops.bmanga.work_new(filepath=str(work_dir))
    assert result == {"FINISHED"}, result
    result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
    assert result == {"FINISHED"}, result
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    result = bpy.ops.bmanga.coma_add()
    assert result == {"FINISHED"}, result
    from bmanga_dev.utils.layer_hierarchy import page_stack_key

    page_key = page_stack_key(page)

    balloon = page.balloons.add()
    balloon.id = "menu_balloon"
    balloon.x_mm = 20.0
    balloon.y_mm = 20.0
    balloon.width_mm = 30.0
    balloon.height_mm = 20.0
    balloon.parent_kind = "page"
    balloon.parent_key = page_key

    text = page.texts.add()
    text.id = "menu_text"
    text.body = "右クリック"
    text.x_mm = 60.0
    text.y_mm = 20.0
    text.width_mm = 30.0
    text.height_mm = 20.0
    text.parent_kind = "page"
    text.parent_key = page_key

    from bmanga_dev.operators import effect_line_op
    from bmanga_dev.utils import gp_layer_parenting as gp_parent
    from bmanga_dev.utils import gpencil as gp_utils
    from bmanga_dev.utils.geom import mm_to_m

    effect_line_op._create_effect_layer(
        bpy.context,
        (20.0, 60.0, 35.0, 35.0),
        parent_key=page_key,
    )

    from bmanga_dev.utils import gp_object_layer, layer_object_model

    gp_obj = gp_object_layer.create_layer_gp_object(
        scene=bpy.context.scene,
        bmanga_id=layer_object_model.make_stable_id("gp"),
        title="menu_gp",
        z_index=210,
        parent_kind="page",
        parent_key=page_key,
    )
    gp_layer = layer_object_model.content_layer(gp_obj)
    assert gp_layer is not None
    frame = gp_utils.ensure_active_frame(gp_layer)
    assert frame is not None and getattr(frame, "drawing", None) is not None
    assert gp_utils.add_stroke_to_drawing(
        frame.drawing,
        [
            (mm_to_m(100.0), mm_to_m(40.0), 0.0),
            (mm_to_m(120.0), mm_to_m(60.0), 0.0),
        ],
    )

    raster_result = bpy.ops.bmanga.raster_layer_add("EXEC_DEFAULT", dpi=30, bit_depth="gray8", enter_paint=False)
    assert "FINISHED" in raster_result, raster_result
    raster_index = int(bpy.context.scene.bmanga_active_raster_layer_index)
    assert raster_index >= 0
    raster = bpy.context.scene.bmanga_raster_layers[raster_index]
    raster.parent_kind = "page"
    raster.parent_key = page_key

    image = bpy.context.scene.bmanga_image_layers.add()
    image.id = "menu_image"
    image.title = "画像"
    image.x_mm = 100.0
    image.y_mm = 70.0
    image.width_mm = 20.0
    image.height_mm = 15.0
    image.parent_kind = "page"
    image.parent_key = page_key

    from bmanga_dev.utils import layer_stack as layer_stack_utils

    layer_stack_utils.sync_layer_stack_after_data_change(bpy.context)
    return work


def _stack_index_for_kind(kind: str) -> int:
    from bmanga_dev.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(bpy.context)
    assert stack is not None
    for index, item in enumerate(stack):
        if str(getattr(item, "kind", "") or "") == kind:
            return index
    raise AssertionError(f"stack kind not found: {kind}")


def _clear_layer_selection() -> None:
    from bmanga_dev.utils import layer_stack as layer_stack_utils
    from bmanga_dev.utils import object_selection

    object_selection.clear(bpy.context)
    layer_stack_utils.clear_all_selection(bpy.context)


def _assert_menu_for_kind(kind: str) -> None:
    from bmanga_dev.ui import context_menu
    from bmanga_dev.utils import layer_stack as layer_stack_utils

    _clear_layer_selection()
    index = _stack_index_for_kind(kind)
    assert layer_stack_utils.select_stack_index(bpy.context, index)
    items = context_menu.selection_command_items(bpy.context)
    labels = [str(item.get("label", "")) for item in items]
    expected = ["詳細設定", "コピー", "貼り付け", "複製", "リンク複製"]
    if kind in {"balloon", "effect"}:
        expected.append("中心点を中心へ戻す")
        expected.append("自由変形")
    if kind in {"balloon", "text", "effect"}:
        expected.append("自由変形をリセット")
    if kind == "balloon":
        expected.append("拡大・縮小・回転")
    expected.append("選択レイヤーをリンク")
    expected.append("リンクを解除")
    if kind == "balloon":
        expected.extend(["フキダシを結合", "しっぽをコピー", "しっぽを貼り付け"])
    if kind == "page":
        expected.extend(["見開きに変更", "見開きを解除"])
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
        assert enabled["自由変形"] is True, (kind, enabled)
    if kind in {"balloon", "text", "effect"}:
        assert "自由変形をリセット" in enabled, (kind, enabled)
    if kind == "balloon":
        assert enabled["拡大・縮小・回転"] is True, (kind, enabled)
    assert enabled["選択レイヤーをリンク"] is False, (kind, enabled)
    assert enabled["リンクを解除"] is False, (kind, enabled)
    if kind == "balloon":
        assert enabled["フキダシを結合"] is False, enabled
        assert enabled["しっぽをコピー"] is False, enabled
        assert enabled["しっぽを貼り付け"] is False, enabled
    if kind == "page":
        assert enabled["見開きに変更"] is False, enabled
        assert enabled["見開きを解除"] is False, enabled


def _assert_link_selected_menu() -> None:
    from bmanga_dev.ui import context_menu
    from bmanga_dev.utils import layer_stack as layer_stack_utils

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
    result = bpy.ops.bmanga.layer_stack_link_selected("EXEC_DEFAULT")
    assert result == {"FINISHED"}, result
    # リンク後はメニューの「リンクを解除」が有効になり、
    # リンクボタン (同オペレーター) の再実行で解除される (トグル)
    items = context_menu.selection_command_items(bpy.context)
    enabled = {str(item.get("label", "")): bool(item.get("enabled", False)) for item in items}
    assert enabled["リンクを解除"] is True, enabled
    result = bpy.ops.bmanga.layer_stack_link_selected("EXEC_DEFAULT")
    assert result == {"FINISHED"}, result
    from bmanga_dev.utils import layer_links

    assert not layer_links.selected_any_linked(bpy.context), "トグルでリンクが解除されていません"
    # 再リンクして解除オペレーター単体も確認
    result = bpy.ops.bmanga.layer_stack_link_selected("EXEC_DEFAULT")
    assert result == {"FINISHED"}, result
    assert layer_links.selected_any_linked(bpy.context)
    result = bpy.ops.bmanga.layer_stack_unlink_selected("EXEC_DEFAULT")
    assert result == {"FINISHED"}, result
    assert not layer_links.selected_any_linked(bpy.context), "リンク解除オペレーターが効いていません"


class _FakeLayout:
    def __init__(self):
        self.operator_context = "EXEC_DEFAULT"
        self.enabled = True
        self.ops = []
        self.menus = []

    def row(self, align=False):
        _ = align
        return self

    def label(self, **kwargs):
        _ = kwargs

    def separator(self):
        pass

    def menu(self, menu_idname, **kwargs):
        self.menus.append((menu_idname, kwargs))

    def operator(self, op_id, **kwargs):
        self.ops.append((op_id, kwargs))
        return type("_OpProps", (), {})()


def _assert_menu_draw_does_not_resync() -> None:
    from bmanga_dev.ui import context_menu
    from bmanga_dev.utils import layer_stack as layer_stack_utils

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
        assert "bmanga.layer_stack_detail" in labels or "bmanga.layer_detail_open" in labels, labels
        assert "bmanga.layer_clipboard_copy" in labels, labels
        assert "bmanga.layer_stack_duplicate" in labels, labels
        assert "bmanga.layer_stack_delete" in labels, labels
    finally:
        layer_stack_utils.sync_layer_stack = original_sync


def _assert_context_menu_does_not_warp_cursor() -> None:
    from bmanga_dev.utils import detail_popup

    class _FakeWindow:
        def __init__(self):
            self.warps = []

        def cursor_warp(self, x, y):
            self.warps.append((int(x), int(y)))

    fake_window = _FakeWindow()
    fake_context = SimpleNamespace(window=fake_window)
    event = SimpleNamespace(mouse_x=240, mouse_y=320)
    calls = []
    original_call = detail_popup._call_blender_menu
    try:
        detail_popup._call_blender_menu = lambda menu_idname: calls.append(str(menu_idname))
        assert detail_popup.call_menu_right_of_cursor(
            fake_context,
            event,
            "BMANGA_MT_selection_context",
        )
    finally:
        detail_popup._call_blender_menu = original_call
    assert calls == ["BMANGA_MT_selection_context"], calls
    assert fake_window.warps == [], fake_window.warps


def _assert_viewport_tool_menu_paths(work) -> None:
    from bmanga_dev.operators import object_tool_op, selection_context_menu
    from bmanga_dev.utils import layer_stack as layer_stack_utils
    from bmanga_dev.utils import object_selection

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
        selection_context_menu._call_selection_menu = (
            lambda _context, _event=None: calls.append("menu") or True
        )
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


def _assert_coma_file_object_context_menu() -> None:
    from bmanga_dev.ui import context_menu
    from bmanga_dev.utils import page_file_scene, shortcut_visibility

    original_current_role = page_file_scene.current_role
    original_current_blend_is_coma = shortcut_visibility.current_blend_is_coma_blend
    original_panel_visible = shortcut_visibility.bmanga_panel_visible
    try:
        page_file_scene.current_role = lambda _context=None: (page_file_scene.ROLE_COMA, "p0001", "c01")
        shortcut_visibility.current_blend_is_coma_blend = lambda: True
        shortcut_visibility.bmanga_panel_visible = lambda _context=None: True

        layout = _FakeLayout()
        context_menu.BMANGA_MT_object_context.draw(SimpleNamespace(layout=layout), bpy.context)
        assert [op_id for op_id, _kwargs in layout.ops] == [
            "bmanga.open_link_source",
            "bmanga.record_asset_link",
        ], layout.ops
        assert [kwargs.get("text") for _op_id, kwargs in layout.ops] == [
            "リンク元ファイルを開く",
            "このリンクを記録",
        ], layout.ops

        assert context_menu._OUTLINER_APPEND_MENUS == ("OUTLINER_MT_object",)
        assert "OUTLINER_MT_context_menu" in context_menu._OUTLINER_CLEANUP_MENUS
        assert "OUTLINER_MT_collection" in context_menu._OUTLINER_CLEANUP_MENUS

        bpy.ops.mesh.primitive_cube_add(size=1.0)
        try:
            with_panel = SimpleNamespace(layout=_FakeLayout())
            context_menu._draw_in_object_context(with_panel, bpy.context)
            assert [menu_id for menu_id, _kwargs in with_panel.layout.menus] == [
                context_menu.BMANGA_MT_object_context.bl_idname
            ], with_panel.layout.menus

            shortcut_visibility.bmanga_panel_visible = lambda _context=None: False
            without_panel = SimpleNamespace(layout=_FakeLayout())
            context_menu._draw_in_object_context(without_panel, bpy.context)
            assert without_panel.layout.menus == [], without_panel.layout.menus
        finally:
            bpy.ops.object.delete()
    finally:
        page_file_scene.current_role = original_current_role
        shortcut_visibility.current_blend_is_coma_blend = original_current_blend_is_coma
        shortcut_visibility.bmanga_panel_visible = original_panel_visible


def _menu_item_by_label(items: list[dict], label: str) -> dict:
    for item in items:
        if str(item.get("label", "") or "") == label:
            return item
    raise AssertionError(f"右クリックメニューに項目がありません: {label}")


def _assert_page_spread_context_menu_commands(work) -> None:
    from bmanga_dev.operators import object_tool_op, selection_context_menu
    from bmanga_dev.ui import context_menu
    from bmanga_dev.utils import object_selection

    while len(work.pages) < 3:
        result = bpy.ops.bmanga.page_add()
        assert result == {"FINISHED"}, result

    class _Event:
        ctrl = False
        shift = False

    original_hit = object_tool_op.hit_object_at_event
    original_call = selection_context_menu._call_selection_menu
    try:
        object_tool_op.hit_object_at_event = lambda _context, _event: {
            "kind": "page",
            "page": 0,
            "part": "body",
            "key": object_selection.page_key(work.pages[0]),
        }
        selection_context_menu._call_selection_menu = lambda _context, _event=None: True
        assert selection_context_menu.open_for_viewport_object(bpy.context, _Event())
        assert int(work.active_page_index) == 0

        items = context_menu.selection_command_items(bpy.context)
        merge_item = _menu_item_by_label(items, "見開きに変更")
        split_item = _menu_item_by_label(items, "見開きを解除")
        assert bool(merge_item.get("enabled", False)), items
        assert not bool(split_item.get("enabled", False)), items
        assert dict(merge_item.get("props", {})).get("left_index") == 0

        result = bpy.ops.bmanga.pages_merge_spread("EXEC_DEFAULT", **dict(merge_item.get("props", {})))
        assert result == {"FINISHED"}, result
        assert work.pages[0].spread
        assert int(work.active_page_index) == 0

        object_tool_op.hit_object_at_event = lambda _context, _event: {
            "kind": "page",
            "page": 0,
            "part": "body",
            "key": object_selection.page_key(work.pages[0]),
        }
        assert selection_context_menu.open_for_viewport_object(bpy.context, _Event())

        items = context_menu.selection_command_items(bpy.context)
        merge_item = _menu_item_by_label(items, "見開きに変更")
        split_item = _menu_item_by_label(items, "見開きを解除")
        assert not bool(merge_item.get("enabled", False)), items
        assert bool(split_item.get("enabled", False)), items
        assert dict(split_item.get("props", {})).get("spread_index") == 0

        result = bpy.ops.bmanga.pages_split_spread("EXEC_DEFAULT", **dict(split_item.get("props", {})))
        assert result == {"FINISHED"}, result
        assert not work.pages[0].spread
        assert [str(page.id) for page in work.pages[:3]] == ["p0001", "p0002", "p0003"]
        assert int(work.active_page_index) == 0
    finally:
        object_tool_op.hit_object_at_event = original_hit
        selection_context_menu._call_selection_menu = original_call


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_context_menu_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        work = _create_work(temp_root / "Context_Menu.bmanga")
        for kind in ("page", "coma", "gp", "effect", "raster", "image", "balloon", "text"):
            _assert_menu_for_kind(kind)
        assert hasattr(bpy.types, "BMANGA_OT_view_context_menu")
        _assert_link_selected_menu()
        _assert_menu_draw_does_not_resync()
        _assert_context_menu_does_not_warp_cursor()
        _assert_viewport_tool_menu_paths(work)
        _assert_coma_file_object_context_menu()
        _assert_page_spread_context_menu_commands(work)
        print("BMANGA_CONTEXT_MENU_COMMANDS_OK")
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
