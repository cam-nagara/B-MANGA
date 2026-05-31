"""Blender 実機用: レイヤー一覧のコマ番号編集と選択ハンドル切替確認."""

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
        "bname_dev_coma_number_selection",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_coma_number_selection"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _stack_index_for_uid(context, uid: str) -> int:
    from bname_dev_coma_number_selection.utils import layer_stack

    stack = layer_stack.sync_layer_stack(context, preserve_active_index=True)
    assert stack is not None
    for index, item in enumerate(stack):
        if layer_stack.stack_item_uid(item) == uid:
            return index
    raise AssertionError(f"レイヤー一覧に行がありません: {uid}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_coma_number_selection_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "ComaNumberSelection.bname"))
        assert "FINISHED" in result, result

        from bname_dev_coma_number_selection.operators.coma_op import create_rect_coma
        from bname_dev_coma_number_selection.utils import layer_stack, object_selection
        from bname_dev_coma_number_selection.utils.layer_hierarchy import COMA_KIND, coma_stack_key

        context = bpy.context
        work = context.scene.bname_work
        work_dir = Path(work.work_dir)
        page = work.pages[0]
        first = page.comas[0]
        second = create_rect_coma(work, page, work_dir, 60.0, 20.0, 30.0, 30.0)
        page.active_coma_index = 0

        text = page.texts.add()
        text.id = "number_text"
        text.body = "number"
        text.parent_kind = "coma"
        text.parent_key = coma_stack_key(page, first)

        original_order = [coma.as_pointer() for coma in page.comas]
        first.coma_number = 5
        assert str(first.coma_id) == "c05"
        assert text.parent_key == f"{page.id}:c05", text.parent_key
        assert [coma.as_pointer() for coma in page.comas] == original_order

        second.coma_number = 5
        assert str(second.coma_id) == "c05"
        assert str(first.coma_id) == "c02"
        assert text.parent_key == f"{page.id}:c02", text.parent_key
        assert [coma.as_pointer() for coma in page.comas] == original_order

        coma_uid = layer_stack.target_uid(COMA_KIND, coma_stack_key(page, first))
        coma_index = _stack_index_for_uid(context, coma_uid)
        text_key = object_selection.text_key(page, text)
        object_selection.select_key(context, text_key, mode="single")
        assert text_key in object_selection.get_keys(context)
        assert layer_stack.select_stack_index(context, coma_index)
        keys = object_selection.get_keys(context)
        expected_coma_key = object_selection.coma_key(page, first)
        assert keys == [expected_coma_key], keys

        print("BNAME_COMA_NUMBER_AND_SELECTION_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
