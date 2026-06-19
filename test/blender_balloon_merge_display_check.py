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
        "bmanga_dev_balloon_merge_display",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_balloon_merge_display"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _bbox_x(obj) -> tuple[float, float]:
    xs = [(obj.matrix_world @ vert.co).x for vert in obj.data.vertices]
    return min(xs), max(xs)


def _visible_generated_objects(balloon_id: str):
    from bmanga_dev_balloon_merge_display.utils import balloon_fill_mesh, balloon_line_mesh

    out = []
    for obj in bpy.data.objects:
        fill_owner = str(obj.get(balloon_fill_mesh.PROP_BALLOON_FILL_MESH_OWNER_ID, "") or "")
        line_owner = str(obj.get(balloon_line_mesh.PROP_BALLOON_LINE_MESH_OWNER_ID, "") or "")
        if balloon_id not in {fill_owner, line_owner}:
            continue
        if not obj.hide_viewport or not obj.hide_render:
            out.append(obj.name)
    return out


def main() -> None:
    mod = _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_balloon_merge_display_"))
    try:
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "BalloonMerge.bmanga"))
        assert result == {"FINISHED"}, result

        from bmanga_dev_balloon_merge_display.core.work import get_work
        from bmanga_dev_balloon_merge_display.operators import balloon_op
        from bmanga_dev_balloon_merge_display.ui import context_menu
        from bmanga_dev_balloon_merge_display.utils import balloon_curve_object, object_naming, object_selection

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
        merge_items = [item for item in labels if item.get("operator") == "bmanga.balloon_merge_selected"]
        assert merge_items and merge_items[0].get("enabled"), "右クリックメニューにフキダシ結合がありません"

        result = bpy.ops.bmanga.balloon_merge_selected("EXEC_DEFAULT")
        assert result == {"FINISHED"}, result
        group_id = str(getattr(b1, "merge_group_id", "") or "")
        assert group_id and group_id == str(getattr(b2, "merge_group_id", "") or "")

        group_obj = object_naming.find_object_by_bmanga_id(group_id, kind="balloon_group")
        assert group_obj is not None, "結合表示オブジェクトがありません"
        assert group_obj.type == "MESH"
        mod_names = [mod.name for mod in group_obj.modifiers]
        assert "B-MANGA フキダシ結合" not in mod_names, f"結合用の一時処理が残っています: {mod_names}"
        assert not group_obj.hide_viewport and not group_obj.hide_render
        assert group_obj.hide_select
        mat_indices = {int(poly.material_index) for poly in group_obj.data.polygons}
        assert {0, 1}.issubset(mat_indices), f"線と塗りが揃っていません: {mat_indices}"

        for entry in (b1, b2):
            source = object_naming.find_object_by_bmanga_id(entry.id, kind="balloon")
            assert source is not None
            assert source.hide_viewport and source.hide_render, "元フキダシが表示に残っています"
            visible_generated = _visible_generated_objects(str(entry.id))
            assert not visible_generated, f"元フキダシの塗り/線が表示に残っています: {visible_generated}"

        before_min_x, before_max_x = _bbox_x(group_obj)
        balloon_op._move_balloon_with_texts(page, b1, float(b1.x_mm) + 40.0, float(b1.y_mm))
        group_obj = object_naming.find_object_by_bmanga_id(group_id, kind="balloon_group")
        assert group_obj is not None, "移動後に結合表示オブジェクトが消えました"
        after_min_x, after_max_x = _bbox_x(group_obj)
        assert after_max_x > before_max_x + 0.01, (before_min_x, before_max_x, after_min_x, after_max_x)
        for entry in (b1, b2):
            source = object_naming.find_object_by_bmanga_id(entry.id, kind="balloon")
            assert source is not None and source.hide_viewport and source.hide_render
            visible_generated = _visible_generated_objects(str(entry.id))
            assert not visible_generated, f"移動後に元フキダシの塗り/線が表示に残っています: {visible_generated}"
        leftovers = [obj.name for obj in bpy.data.objects if obj.name.startswith("__bmanga_balloon_merge_tmp_")]
        assert not leftovers, f"一時オブジェクトが残っています: {leftovers}"
    finally:
        try:
            mod.unregister()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    print("BMANGA_BALLOON_MERGE_DISPLAY_CHECK_OK")


main()
