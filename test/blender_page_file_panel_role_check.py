"""Blender実機用: ページ一覧/ページ編集ファイル別のB-Nameパネル整理確認."""

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
        "bname_dev_page_panel_role",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_page_panel_role"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


class _DummyOp:
    def __setattr__(self, _name, _value):
        return


class _FakeLayout:
    def __init__(self, records: list[tuple[str, str]], group: str = "", depth: int = 0):
        object.__setattr__(self, "_records", records)
        object.__setattr__(self, "_group", group)
        object.__setattr__(self, "_depth", depth)

    def __setattr__(self, _name, _value):
        return

    def _child(self):
        return _FakeLayout(self._records, self._group, self._depth + 1)

    def _add(self, kind: str, value: str) -> None:
        value = str(value or "")
        if value:
            self._records.append((kind, value))

    def row(self, **_kwargs):
        return self._child()

    def column(self, **_kwargs):
        return self._child()

    def box(self, **_kwargs):
        return self._child()

    def split(self, **_kwargs):
        return self._child()

    def grid_flow(self, **_kwargs):
        return self._child()

    def separator(self, **_kwargs) -> None:
        return

    def label(self, text: str = "", icon: str = "", **_kwargs) -> None:
        self._add("label", text or icon)

    def prop(self, _data, prop_name: str, text: str | None = None, **_kwargs) -> None:
        self._add("prop", text if text not in {None, ""} else prop_name)

    def operator(self, idname: str, text: str = "", icon: str = "", **_kwargs):
        self._add("operator", text or idname or icon)
        return _DummyOp()

    def menu(self, menu_id: str, text: str = "", icon: str = "", **_kwargs):
        self._add("menu", text or menu_id or icon)

    def template_list(self, listtype_name: str, _list_id: str, _data, propname: str, *_args, **_kwargs):
        self._add("template_list", f"{listtype_name}:{propname}")

    def template_ID(self, _data, propname: str, **_kwargs):
        self._add("template_ID", propname)

    def __getattr__(self, name: str):
        def _fallback(*_args, **_kwargs):
            self._add("fallback", name)
            return self._child()

        return _fallback


def _draw_records(panel_cls, context) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    panel_cls.draw(SimpleNamespace(layout=_FakeLayout(records)), context)
    return records


def _draw_ui_list_item_records(ui_list_cls, context, data, item, index: int) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    ui_list_cls.draw_item(
        SimpleNamespace(layout_type="DEFAULT"),
        context,
        _FakeLayout(records),
        data,
        item,
        0,
        data,
        "active_page_index",
        index,
    )
    return records


def _values(records: list[tuple[str, str]]) -> set[str]:
    return {value for _kind, value in records}


def _assert_present(records: list[tuple[str, str]], *values: str) -> None:
    present = _values(records)
    missing = [value for value in values if value not in present]
    if missing:
        raise AssertionError(f"表示されるべき項目がありません: {missing}")


def _assert_absent(records: list[tuple[str, str]], *values: str) -> None:
    present = _values(records)
    extras = [value for value in values if value in present]
    if extras:
        raise AssertionError(f"この状態では不要な項目が残っています: {extras}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_page_panel_role_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "PanelRole.bname"))
        assert result == {"FINISHED"}, result
        result = bpy.ops.bname.page_add()
        assert result == {"FINISHED"}, result

        from bname_dev_page_panel_role.panels import (
            export_panel,
            gpencil_panel,
            outliner_layer_panel,
            page_panel,
            tool_panel,
            view_panel,
            work_panel,
        )
        from bname_dev_page_panel_role.utils import page_file_scene

        context = bpy.context
        role, _page_id, _coma_id = page_file_scene.current_role(context)
        assert role == page_file_scene.ROLE_WORK
        assert not tool_panel.BNAME_PT_tools.poll(context)
        assert export_panel.BNAME_PT_export.poll(context)

        work_records = _draw_records(work_panel.BNAME_PT_work, context)
        _assert_present(work_records, "作品情報", "ページ数", "ページ一覧プレビュー", "コマ用blendファイル (この作品のみ)")
        layer_records = _draw_records(gpencil_panel.BNAME_PT_layer_stack, context)
        _assert_present(layer_records, "BNAME_UL_layer_panel_pages:pages", "bname.page_add", "bname.open_page_file")
        _assert_absent(layer_records, "BNAME_UL_layer_stack:bname_layer_stack_visible", "wm.call_menu")
        row_records = _draw_ui_list_item_records(
            gpencil_panel.BNAME_UL_layer_panel_pages,
            context,
            context.scene.bname_work,
            context.scene.bname_work.pages[1],
            1,
        )
        _assert_present(row_records, "bname.open_page_file")
        page_row_records = _draw_ui_list_item_records(
            page_panel.BNAME_UL_pages,
            context,
            context.scene.bname_work,
            context.scene.bname_work.pages[1],
            1,
        )
        _assert_present(page_row_records, "bname.open_page_file")
        maintenance_records = _draw_records(outliner_layer_panel.BNAME_PT_outliner_layers, context)
        _assert_present(maintenance_records, "bname.organize_data_names")
        _assert_absent(
            maintenance_records,
            "bname.repair_hierarchy",
            "bname.coma_renumber_active_page",
            "bname.mask_regenerate_all",
            "bname.mask_remove_orphans",
        )
        assert not bpy.ops.bname.repair_hierarchy.poll()
        assert not bpy.ops.bname.coma_renumber_active_page.poll()
        assert not bpy.ops.bname.mask_regenerate_all.poll()
        assert not bpy.ops.bname.mask_remove_orphans.poll()
        assert bpy.ops.bname.organize_data_names.poll()

        result = bpy.ops.bname.open_page_file(index=0)
        assert result == {"FINISHED"}, result
        context = bpy.context
        role, page_id, _coma_id = page_file_scene.current_role(context)
        assert role == page_file_scene.ROLE_PAGE
        assert page_id == "p0001"
        assert tool_panel.BNAME_PT_tools.poll(context)
        assert not export_panel.BNAME_PT_export.poll(context)
        assert not work_panel.BNAME_PT_work.poll(context)

        transition_records = _draw_records(work_panel.BNAME_PT_coma_return, context)
        _assert_present(transition_records, "ページ一覧に戻る")
        _assert_absent(transition_records, "作品情報", "ページ数", "コマ用blendファイル (この作品のみ)")
        view_records = _draw_records(view_panel.BNAME_PT_view, context)
        _assert_present(view_records, "ページ一覧表示", "前後ページ数", "画像解像度%", "列数", "間隔mm")
        _assert_absent(view_records, "全ページを一覧", "選択ページ")
        layer_records = _draw_records(gpencil_panel.BNAME_PT_layer_stack, context)
        _assert_present(layer_records, "BNAME_UL_layer_stack:bname_layer_stack_visible", "wm.call_menu")
        _assert_absent(
            layer_records,
            "BNAME_UL_layer_panel_pages:pages",
            "bname.page_add",
            "bname.page_duplicate",
            "bname.page_remove",
            "bname.open_page_file",
        )
        maintenance_records = _draw_records(outliner_layer_panel.BNAME_PT_outliner_layers, context)
        _assert_present(maintenance_records, "bname.repair_hierarchy", "bname.mask_regenerate_all", "bname.mask_remove_orphans", "bname.coma_renumber_active_page")
        _assert_absent(maintenance_records, "bname.organize_data_names")
        assert bpy.ops.bname.repair_hierarchy.poll()
        assert bpy.ops.bname.coma_renumber_active_page.poll()
        assert bpy.ops.bname.mask_regenerate_all.poll()
        assert bpy.ops.bname.mask_remove_orphans.poll()
        assert not bpy.ops.bname.organize_data_names.poll()

        active_before = int(getattr(context.scene.bname_work, "active_page_index", -1))
        context.scene.bname_active_page_number = 2
        assert int(getattr(context.scene.bname_work, "active_page_index", -1)) == active_before

        context.scene.bname_work.active_page_index = 0
        context.scene.bname_work.pages[0].active_coma_index = 0
        result = bpy.ops.bname.enter_coma_mode()
        assert result == {"FINISHED"}, result
        context = bpy.context
        role, _page_id, _coma_id = page_file_scene.current_role(context)
        assert role == page_file_scene.ROLE_COMA
        assert work_panel.BNAME_PT_coma_return.poll(context)
        transition_records = _draw_records(work_panel.BNAME_PT_coma_return, context)
        _assert_present(transition_records, "ページに戻る", "保存フォルダを開く")
        _assert_absent(
            transition_records,
            "ページ一覧位置",
            "フィット",
            "ページ一覧ビューを開く",
        )
        assert view_panel.BNAME_PT_view.poll(context)
        view_records = _draw_records(view_panel.BNAME_PT_view, context)
        _assert_present(view_records, "ページ一覧ビュー", "位置", "サイズ", "フィット", "専用ワークスペース")
        _assert_absent(
            view_records,
            "ページ一覧表示",
            "前後ページ数",
            "画像解像度%",
            "全ページを一覧",
            "選択ページ",
        )
        print("BNAME_PAGE_FILE_PANEL_ROLE_OK")
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
