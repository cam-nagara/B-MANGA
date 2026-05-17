"""Blender 実機(背景)用: 作品情報テキストの実体化確認."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_work_info_text",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_work_info_text"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _work_info_objects(work_info_text):
    return [
        obj for obj in bpy.data.objects
        if obj.get(work_info_text.PROP_WORK_INFO_KIND) == "work_info_text"
    ]


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    from bname_dev_work_info_text.utils import page_grid, work_info_text_object

    scene = bpy.context.scene
    work = scene.bname_work
    work.loaded = True
    work.paper.canvas_width_mm = 210.0
    work.paper.canvas_height_mm = 297.0
    for idx in range(2):
        page = work.pages.add()
        page.id = f"p{idx + 1:04d}"
        page.title = f"{idx + 1}ページ"

    info = work.work_info
    info.work_name = "作品テスト"
    info.author = "作者"
    info.page_number_start = 5
    info.display_work_name.enabled = True
    info.display_work_name.position = "top-left"
    info.display_author.enabled = True
    info.display_author.position = "bottom-right"
    info.display_page_number.enabled = True
    info.display_page_number.position = "bottom-center"

    page_grid.apply_page_collection_transforms(bpy.context, work)
    objs = _work_info_objects(work_info_text_object)
    assert len(objs) == 6, f"作品情報テキスト数が不正です: {len(objs)}"
    assert all(obj.type == "FONT" for obj in objs), "作品情報がテキストオブジェクトではありません"
    bodies = sorted(str(obj.data.body) for obj in objs)
    assert "作品テスト" in bodies
    assert "作者" in bodies
    assert "ページ0005" in bodies and "ページ0006" in bodies

    info.work_name = "更新後"
    page_grid.apply_page_collection_transforms(bpy.context, work)
    bodies = [str(obj.data.body) for obj in _work_info_objects(work_info_text_object)]
    assert "更新後" in bodies and "作品テスト" not in bodies

    info.display_author.enabled = False
    page_grid.apply_page_collection_transforms(bpy.context, work)
    bodies = [str(obj.data.body) for obj in _work_info_objects(work_info_text_object)]
    assert "作者" not in bodies, "非表示にした作品情報テキストが残っています"

    print("BNAME_WORK_INFO_TEXT_OBJECT_CHECK_OK")


main()
