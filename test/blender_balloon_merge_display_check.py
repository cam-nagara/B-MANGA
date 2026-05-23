"""Blender runtime check: 複数フキダシの表示上の結合."""

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
        "bname_dev_balloon_merge_display",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_balloon_merge_display"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> None:
    mod = _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bname_balloon_merge_display_"))
    try:
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "BalloonMerge.bname"))
        assert result == {"FINISHED"}, result

        from bname_dev_balloon_merge_display.core.work import get_work
        from bname_dev_balloon_merge_display.operators import balloon_op
        from bname_dev_balloon_merge_display.ui import context_menu
        from bname_dev_balloon_merge_display.utils import balloon_curve_object, object_naming, object_selection

        work = get_work(bpy.context)
        assert work is not None
        page = work.pages[0]
        b1 = balloon_op._create_balloon_entry(  # noqa: SLF001
            bpy.context,
            page,
            shape="ellipse",
            x=70.0,
            y=110.0,
            w=42.0,
            h=30.0,
        )
        b2 = balloon_op._create_balloon_entry(  # noqa: SLF001
            bpy.context,
            page,
            shape="ellipse",
            x=94.0,
            y=110.0,
            w=42.0,
            h=30.0,
        )
        for entry in (b1, b2):
            entry.line_width_mm = 1.0
            entry.fill_color = (1.0, 1.0, 1.0, 1.0)
            entry.fill_opacity = 100.0
            assert balloon_curve_object.ensure_balloon_curve_object(
                scene=bpy.context.scene,
                entry=entry,
                page=page,
            ) is not None

        object_selection.set_keys(
            bpy.context,
            [
                object_selection.balloon_key(page, b1),
                object_selection.balloon_key(page, b2),
            ],
        )
        labels = context_menu.selection_command_items(bpy.context)
        merge_items = [item for item in labels if item.get("operator") == "bname.balloon_merge_selected"]
        assert merge_items and merge_items[0].get("enabled"), "右クリックメニューにフキダシ結合がありません"

        result = bpy.ops.bname.balloon_merge_selected("EXEC_DEFAULT")
        assert result == {"FINISHED"}, result
        group_id = str(getattr(b1, "merge_group_id", "") or "")
        assert group_id and group_id == str(getattr(b2, "merge_group_id", "") or "")

        group_obj = object_naming.find_object_by_bname_id(group_id, kind="balloon_group")
        assert group_obj is not None, "結合表示オブジェクトがありません"
        assert group_obj.type == "MESH"
        assert not group_obj.hide_viewport and not group_obj.hide_render
        assert group_obj.hide_select
        mat_indices = {int(poly.material_index) for poly in group_obj.data.polygons}
        assert {0, 1}.issubset(mat_indices), f"線と塗りが揃っていません: {mat_indices}"

        for entry in (b1, b2):
            source = object_naming.find_object_by_bname_id(entry.id, kind="balloon")
            assert source is not None
            assert source.hide_viewport and source.hide_render, "元フキダシが表示に残っています"
    finally:
        try:
            mod.unregister()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    print("BNAME_BALLOON_MERGE_DISPLAY_CHECK_OK")


main()
