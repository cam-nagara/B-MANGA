"""Blender実機用: レイヤーパネル内ページリストとレイヤー一覧連動の確認."""

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
        "bname_dev_page_list",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_page_list"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _visible_layer_rows(context) -> list[object]:
    from bname_dev_page_list.panels import gpencil_panel
    from bname_dev_page_list.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(
        context,
        preserve_active_index=True,
        align_page_order=True,
    )
    assert stack is not None
    return [
        item
        for _index, item in gpencil_panel._visible_layer_stack_entries(context, stack)
    ]


def _assert_layer_list_page(context, expected_page_key: str) -> None:
    rows = _visible_layer_rows(context)
    visible_rows = list(getattr(context.scene, "bname_layer_stack_visible", []))
    if [str(getattr(item, "key", "") or "") for item in visible_rows] != [
        str(getattr(item, "key", "") or "") for item in rows
    ]:
        raise AssertionError("レイヤー一覧の表示用行が選択ページと一致していません")
    page_rows = [
        str(getattr(item, "key", "") or "")
        for item in rows
        if str(getattr(item, "kind", "") or "") == "page"
    ]
    if page_rows:
        raise AssertionError(f"レイヤー一覧にページ行が残っています: {page_rows}")
    coma_parent_keys = [
        str(getattr(item, "parent_key", "") or "")
        for item in rows
        if str(getattr(item, "kind", "") or "") == "coma"
    ]
    if expected_page_key not in coma_parent_keys:
        raise AssertionError(
            f"選択ページ内のコマがレイヤー一覧に出ていません: "
            f"expected={expected_page_key}, actual={coma_parent_keys}"
        )
    context.scene.bname_active_layer_stack_visible_index = 0
    active_index = int(getattr(context.scene, "bname_active_layer_stack_index", -1))
    active = context.scene.bname_layer_stack[active_index]
    if str(getattr(active, "parent_key", "") or "") != expected_page_key:
        raise AssertionError("レイヤー一覧の選択が選択ページ内の行へ反映されていません")


def _visible_collection_parent_keys(context) -> list[str]:
    return [
        str(getattr(item, "parent_key", "") or "")
        for item in getattr(context.scene, "bname_layer_stack_visible", [])
        if str(getattr(item, "kind", "") or "") == "coma"
    ]


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_page_list_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "PageList.bname"))
        assert "FINISHED" in result, result

        from bname_dev_page_list.panels import layer_stack_detail_ui
        from bname_dev_page_list.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = context.scene.bname_work
        assert hasattr(bpy.types, "BNAME_UL_layer_panel_pages")
        assert hasattr(bpy.types, "BNAME_PT_layer_stack")
        assert hasattr(context.scene, "bname_layer_stack_visible")
        assert len(work.pages) == 1
        assert layer_stack_detail_ui.page_layer_name(work.pages[0], work) == "ページ001"

        assert "FINISHED" in bpy.ops.bname.page_add("EXEC_DEFAULT")
        assert len(work.pages) == 2
        first_id = str(work.pages[0].id)
        second_id = str(work.pages[1].id)

        assert "FINISHED" in bpy.ops.bname.page_select("EXEC_DEFAULT", index=0)
        _assert_layer_list_page(context, page_stack_key(work.pages[0]))

        from bname_dev_page_list.utils import layer_stack as layer_stack_utils

        page0_key = page_stack_key(work.pages[0])
        page1_key = page_stack_key(work.pages[1])
        work.active_page_index = 1
        if page1_key in _visible_collection_parent_keys(context):
            raise AssertionError("直接ページ切替前から表示用レイヤー一覧が更新されています")
        if not layer_stack_utils.schedule_layer_stack_draw_maintenance(context):
            raise AssertionError("直接ページ切替後のレイヤー一覧更新が予約されません")
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        if page1_key not in _visible_collection_parent_keys(context):
            raise AssertionError("直接ページ切替後に表示用レイヤー一覧が更新されません")
        assert page0_key not in _visible_collection_parent_keys(context)

        assert "FINISHED" in bpy.ops.bname.page_select("EXEC_DEFAULT", index=1)
        _assert_layer_list_page(context, page_stack_key(work.pages[1]))

        assert "FINISHED" in bpy.ops.bname.page_move("EXEC_DEFAULT", direction=-1)
        assert str(work.pages[0].id) == second_id
        assert str(work.pages[1].id) == first_id
        assert layer_stack_detail_ui.page_layer_name(work.pages[0], work) == "ページ001"
        assert layer_stack_detail_ui.page_layer_name(work.pages[1], work) == "ページ002"

        assert "FINISHED" in bpy.ops.bname.page_duplicate("EXEC_DEFAULT")
        assert len(work.pages) == 3
        assert work.active_page_index == 1
        assert layer_stack_detail_ui.page_layer_name(work.pages[1], work) == "ページ002"

        assert "FINISHED" in bpy.ops.bname.page_remove("EXEC_DEFAULT")
        assert len(work.pages) == 2
        print("BNAME_LAYER_PANEL_PAGE_LIST_OK")
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
