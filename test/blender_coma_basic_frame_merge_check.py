"""Blender regression checks for basic-frame panels and panel merge."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

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


def _assert_close(actual: float, expected: float, label: str, tol: float = 0.05) -> None:
    if abs(float(actual) - float(expected)) > tol:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def _expected_basic_rect(work, page) -> tuple[float, float, float, float]:
    from bmanga_dev.utils import page_grid

    paper = work.paper
    y_mm = (paper.canvas_height_mm - paper.inner_frame_height_mm) / 2.0 + paper.inner_frame_offset_y_mm
    if bool(getattr(page, "spread", False)):
        right_offset = page_grid.spread_right_page_offset_mm(
            page,
            paper.canvas_width_mm,
            paper.finish_width_mm,
        )
        left_x = (paper.canvas_width_mm - paper.inner_frame_width_mm) / 2.0 - paper.inner_frame_offset_x_mm
        right_end = (
            right_offset
            + (paper.canvas_width_mm - paper.inner_frame_width_mm) / 2.0
            + paper.inner_frame_offset_x_mm
            + paper.inner_frame_width_mm
        )
        return left_x, y_mm, right_end - left_x, paper.inner_frame_height_mm
    x_mm = (paper.canvas_width_mm - paper.inner_frame_width_mm) / 2.0 + paper.inner_frame_offset_x_mm
    return x_mm, y_mm, paper.inner_frame_width_mm, paper.inner_frame_height_mm


def _assert_basic_frame(work, page, coma, label: str) -> None:
    expected = _expected_basic_rect(work, page)
    if str(getattr(coma, "shape_type", "") or "") != "rect":
        raise AssertionError(f"{label}: panel is not rectangular")
    actual = (
        float(coma.rect_x_mm),
        float(coma.rect_y_mm),
        float(coma.rect_width_mm),
        float(coma.rect_height_mm),
    )
    for value, target, axis in zip(actual, expected, ("x", "y", "width", "height"), strict=False):
        _assert_close(value, target, f"{label} {axis}")


def _current_page(work):
    page_id = str(getattr(bpy.context.scene, "bmanga_current_page_id", "") or "")
    for page in work.pages:
        if str(page.id) == page_id:
            return page
    return work.pages[work.active_page_index]


def _add_coma_children(work, page, parent_key: str) -> dict[str, object]:
    scene = bpy.context.scene
    entries: dict[str, object] = {}

    balloon = page.balloons.add()
    balloon.id = "merge_child_balloon"
    balloon.parent_kind = "coma"
    balloon.parent_key = parent_key
    balloon.x_mm = 12.0
    balloon.y_mm = 12.0
    balloon.width_mm = 12.0
    balloon.height_mm = 8.0
    entries["balloon"] = balloon

    text = page.texts.add()
    text.id = "merge_child_text"
    text.parent_kind = "coma"
    text.parent_key = parent_key
    text.x_mm = 14.0
    text.y_mm = 14.0
    text.width_mm = 10.0
    text.height_mm = 6.0
    entries["text"] = text

    image = scene.bmanga_image_layers.add()
    image.id = "merge_child_image"
    image.parent_kind = "coma"
    image.parent_key = parent_key
    entries["image"] = image

    raster = scene.bmanga_raster_layers.add()
    raster.id = "merge_child_raster"
    raster.scope = "page"
    raster.parent_kind = "coma"
    raster.parent_key = parent_key
    entries["raster"] = raster

    fill = scene.bmanga_fill_layers.add()
    fill.id = "merge_child_fill"
    fill.parent_kind = "coma"
    fill.parent_key = parent_key
    entries["fill"] = fill

    image_path = scene.bmanga_image_path_layers.add()
    image_path.id = "merge_child_image_path"
    image_path.parent_kind = "coma"
    image_path.parent_key = parent_key
    entries["image_path"] = image_path

    folder = work.layer_folders.add()
    folder.id = "merge_child_folder"
    folder.parent_key = parent_key
    entries["folder"] = folder

    return entries


def _assert_coma_children_parent(entries: dict[str, object], expected_key: str) -> None:
    for label, entry in entries.items():
        actual = str(getattr(entry, "parent_key", "") or "")
        if actual != expected_key:
            raise AssertionError(f"{label} parent was not moved: expected {expected_key}, got {actual}")
        if label != "folder" and str(getattr(entry, "parent_kind", "") or "") != "coma":
            raise AssertionError(f"{label} parent kind was not kept as panel child")


def _test_page_file_basic_frame_and_merge(temp_root: Path) -> None:
    from bmanga_dev.io import page_io
    from bmanga_dev.operators.coma_op import create_rect_coma
    from bmanga_dev.utils.layer_hierarchy import coma_stack_key
    from bmanga_dev.utils import coma_border_object, coma_plane, object_selection

    work_path = temp_root / "PageBasicFrame.bmanga"
    result = bpy.ops.bmanga.work_new(filepath=str(work_path))
    if "FINISHED" not in result:
        raise AssertionError(f"work_new failed: {result}")
    result = bpy.ops.bmanga.page_add("EXEC_DEFAULT")
    if "FINISHED" not in result:
        raise AssertionError(f"page_add failed: {result}")

    work = bpy.context.scene.bmanga_work
    page = work.pages[1]
    work_dir = Path(work.work_dir)
    page.comas.clear()
    page.coma_count = 1
    page_io.save_page_json(work_dir, page)
    page_io.save_pages_json(work_dir, work)

    result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=1)
    if "FINISHED" not in result:
        raise AssertionError(f"open_page_file failed: {result}")

    work = bpy.context.scene.bmanga_work
    page = _current_page(work)
    if len(page.comas) != 1:
        raise AssertionError(f"basic-frame panel was not restored: {len(page.comas)}")
    _assert_basic_frame(work, page, page.comas[0], "restored page-file panel")

    plane = coma_plane.find_coma_plane_object(page.id, page.comas[0].id)
    if plane is None:
        raise AssertionError("restored panel plane object was not created")
    border = coma_border_object.ensure_coma_border_object(bpy.context.scene, work, page, page.comas[0])
    if border is None:
        raise AssertionError("restored panel border object was not created")

    result = bpy.ops.bmanga.coma_add("EXEC_DEFAULT")
    if "FINISHED" not in result:
        raise AssertionError(f"coma_add failed: {result}")
    _assert_basic_frame(work, page, page.comas[-1], "toolbar-added panel")

    result = bpy.ops.bmanga.layer_stack_add("EXEC_DEFAULT", kind="coma")
    if "FINISHED" not in result:
        raise AssertionError(f"layer_stack_add panel failed: {result}")
    _assert_basic_frame(work, page, page.comas[-1], "layer-list-added panel")

    first = create_rect_coma(work, page, work_dir, 10.0, 10.0, 20.0, 20.0)
    second = create_rect_coma(work, page, work_dir, 90.0, 10.0, 20.0, 20.0)
    child_entries = _add_coma_children(work, page, coma_stack_key(page, first))
    before_count = len(page.comas)
    object_selection.set_keys(
        bpy.context,
        [object_selection.coma_key(page, first), object_selection.coma_key(page, second)],
    )
    result = bpy.ops.bmanga.coma_merge_selected("EXEC_DEFAULT", border_mode="separate")
    if "FINISHED" not in result:
        raise AssertionError(f"coma_merge_selected separate failed: {result}")
    if len(page.comas) != before_count - 1:
        raise AssertionError("panel merge did not reduce panel count by one")
    survivor = page.comas[page.active_coma_index]
    _assert_coma_children_parent(child_entries, coma_stack_key(page, survivor))
    if str(getattr(survivor, "merged_border_mode", "") or "") != "separate":
        raise AssertionError("separate border mode was not stored")
    stored = json.loads(str(getattr(survivor, "merged_border_polygons_json", "") or "[]"))
    if len(stored) != 2:
        raise AssertionError(f"expected two stored border polygons, got {len(stored)}")
    border = coma_border_object.ensure_coma_border_object(bpy.context.scene, work, page, survivor)
    if border is None or border.type != "MESH" or len(border.data.polygons) < 8:
        raise AssertionError("separate border object did not contain both original outlines")


def _test_spread_basic_frame_merge(temp_root: Path) -> None:
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "SpreadBasicFrame.bmanga"))
    if "FINISHED" not in result:
        raise AssertionError(f"work_new spread failed: {result}")
    result = bpy.ops.bmanga.page_add("EXEC_DEFAULT")
    if "FINISHED" not in result:
        raise AssertionError(f"page_add spread failed: {result}")
    work = bpy.context.scene.bmanga_work
    result = bpy.ops.bmanga.pages_merge_spread("EXEC_DEFAULT", left_index=0, tombo_aligned=True, tombo_gap_mm=-9.6)
    if "FINISHED" not in result:
        raise AssertionError(f"pages_merge_spread failed: {result}")
    if len(work.pages) != 1:
        raise AssertionError(f"spread merge should leave one page, got {len(work.pages)}")
    page = work.pages[0]
    if not bool(page.spread):
        raise AssertionError("merged page is not marked as spread")
    if len(page.comas) != 1:
        raise AssertionError(f"basic-frame spread panels were not merged: {len(page.comas)}")
    _assert_basic_frame(work, page, page.comas[0], "spread merged basic-frame panel")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_basic_frame_"))
    mod = None
    success = False
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        _test_page_file_basic_frame_and_merge(temp_root)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _test_spread_basic_frame_merge(temp_root)
        print("BMANGA_COMA_BASIC_FRAME_MERGE_OK", flush=True)
        success = True
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)
        os._exit(0 if success else 1)


if __name__ == "__main__":
    main()
