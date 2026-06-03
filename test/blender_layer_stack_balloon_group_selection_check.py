"""Blender 実機用: レイヤー一覧の複数選択とフキダシ結合フォルダ確認."""

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


def _add_test_text(page, text_id: str, parent_key: str):
    entry = page.texts.add()
    entry.id = text_id
    entry.body = text_id
    entry.x_mm = 22.0
    entry.y_mm = 22.0
    entry.width_mm = 18.0
    entry.height_mm = 14.0
    entry.parent_kind = "page"
    entry.parent_key = parent_key
    return entry


def _add_test_balloon(page, balloon_id: str, parent_key: str):
    entry = page.balloons.add()
    entry.id = balloon_id
    entry.shape = "rect"
    entry.x_mm = 48.0
    entry.y_mm = 24.0
    entry.width_mm = 22.0
    entry.height_mm = 15.0
    entry.parent_kind = "page"
    entry.parent_key = parent_key
    return entry


def _stack(context):
    from bname_dev.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    assert stack is not None
    layer_stack_utils.remember_layer_stack_signature(context)
    return stack


def _find_stack_item(context, uid: str):
    from bname_dev.utils import layer_stack as layer_stack_utils

    for index, item in enumerate(_stack(context)):
        if layer_stack_utils.stack_item_uid(item) == uid:
            return index, item
    raise AssertionError(f"stack item not found: {uid}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_layer_stack_group_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "LayerStackGroup.bname"))
        assert "FINISHED" in result, result
        result = bpy.ops.bname.open_page_file(index=0)
        assert "FINISHED" in result, result

        from bname_dev.operators import layer_stack_op
        from bname_dev.utils import layer_stack as layer_stack_utils
        from bname_dev.utils import layer_stack_visible
        from bname_dev.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = context.scene.bname_work
        page = work.pages[0]
        page_key = page_stack_key(page)
        text_a = _add_test_text(page, "selection_text_a", page_key)
        text_b = _add_test_text(page, "selection_text_b", page_key)
        balloon = _add_test_balloon(page, "selection_balloon", page_key)
        layer_stack_utils.sync_layer_stack_after_data_change(context)

        text_a_uid = layer_stack_utils.target_uid("text", f"{page_key}:{text_a.id}")
        text_b_uid = layer_stack_utils.target_uid("text", f"{page_key}:{text_b.id}")
        balloon_uid = layer_stack_utils.target_uid("balloon", f"{page_key}:{balloon.id}")
        text_a_index, text_a_item = _find_stack_item(context, text_a_uid)
        text_b_index, text_b_item = _find_stack_item(context, text_b_uid)
        balloon_index, balloon_item = _find_stack_item(context, balloon_uid)

        def _invoke_multi(index: int, *, shift: bool = False, ctrl: bool = False):
            op = SimpleNamespace(index=index, mode="SET")
            op.execute = lambda ctx: layer_stack_op.BNAME_OT_layer_stack_multi_select.execute(op, ctx)
            return layer_stack_op.BNAME_OT_layer_stack_multi_select.invoke(
                op,
                context,
                SimpleNamespace(value="PRESS", shift=shift, ctrl=ctrl, oskey=False),
            )

        assert "FINISHED" in _invoke_multi(text_a_index)
        if not layer_stack_utils.is_item_selected(context, text_a_item):
            raise AssertionError("通常クリックでレイヤーを選択できません")
        assert "FINISHED" in _invoke_multi(balloon_index, ctrl=True)
        _balloon_index, balloon_item = _find_stack_item(context, balloon_uid)
        if not layer_stack_utils.is_item_selected(context, text_a_item):
            raise AssertionError("Ctrl選択後に元の選択が残りません")
        if not layer_stack_utils.is_item_selected(context, balloon_item):
            raise AssertionError("Ctrl選択で追加選択できません")
        assert "FINISHED" in _invoke_multi(balloon_index, ctrl=True)
        _balloon_index, balloon_item = _find_stack_item(context, balloon_uid)
        if layer_stack_utils.is_item_selected(context, balloon_item):
            raise AssertionError("Ctrl選択で選択解除できません")
        assert "FINISHED" in _invoke_multi(text_b_index, shift=True)
        stack = _stack(context)
        lo = min(text_a_index, text_b_index)
        hi = max(text_a_index, text_b_index)
        for item in stack[lo:hi + 1]:
            if not layer_stack_utils.is_item_selected(context, item):
                raise AssertionError("Shift選択で範囲内のレイヤーが選択されません")
        _text_b_index, text_b_item = _find_stack_item(context, text_b_uid)
        if not layer_stack_utils.is_item_selected(context, text_b_item):
            raise AssertionError("Shift選択で終端レイヤーが選択されません")

        group_a = _add_test_balloon(page, "group_balloon_a", page_key)
        group_b = _add_test_balloon(page, "group_balloon_b", page_key)
        group_id = "balloon_group_test"
        group_a.merge_group_id = group_id
        group_b.merge_group_id = group_id
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        group_key = f"{page_key}:{group_id}"
        group_uid = layer_stack_utils.target_uid("balloon_group", group_key)
        group_index, _group_item = _find_stack_item(context, group_uid)
        layer_stack_utils.sync_visible_layer_stack(context)
        visible_keys = [
            str(getattr(item, "key", "") or "")
            for _idx, item in layer_stack_visible.visible_layer_stack_entries(context)
        ]
        if group_key not in visible_keys:
            raise AssertionError("フキダシ結合フォルダがレイヤー一覧に表示されません")
        if f"{page_key}:{group_a.id}" not in visible_keys or f"{page_key}:{group_b.id}" not in visible_keys:
            raise AssertionError("開いたフキダシ結合フォルダの中身が表示されません")
        assert "FINISHED" in bpy.ops.bname.layer_stack_toggle_expanded(
            "EXEC_DEFAULT",
            index=group_index,
        )
        layer_stack_utils.sync_visible_layer_stack(context)
        collapsed_keys = [
            str(getattr(item, "key", "") or "")
            for _idx, item in layer_stack_visible.visible_layer_stack_entries(context)
        ]
        if group_key not in collapsed_keys:
            raise AssertionError("閉じたフキダシ結合フォルダ自体が消えています")
        if f"{page_key}:{group_a.id}" in collapsed_keys or f"{page_key}:{group_b.id}" in collapsed_keys:
            raise AssertionError("閉じたフキダシ結合フォルダの中身が表示されています")
        assert "FINISHED" in bpy.ops.bname.layer_stack_toggle_expanded(
            "EXEC_DEFAULT",
            index=group_index,
        )
        assert "FINISHED" in bpy.ops.bname.layer_stack_toggle_visibility(
            "EXEC_DEFAULT",
            index=group_index,
        )
        if bool(group_a.visible) or bool(group_b.visible):
            raise AssertionError("フキダシ結合フォルダの非表示が中身へ反映されません")
        assert "FINISHED" in bpy.ops.bname.layer_stack_toggle_visibility(
            "EXEC_DEFAULT",
            index=group_index,
        )
        if not bool(group_a.visible) or not bool(group_b.visible):
            raise AssertionError("フキダシ結合フォルダの再表示が中身へ反映されません")

        print("BNAME_LAYER_STACK_BALLOON_GROUP_SELECTION_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
