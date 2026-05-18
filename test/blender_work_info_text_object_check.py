"""Blender 実機(背景)用: 作品情報テキストの実体化確認."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import bpy
from mathutils import Vector

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
    mod = _load_addon()
    try:
        from bname_dev_work_info_text.ui import overlay
        from bname_dev_work_info_text.utils import outliner_model, page_grid, work_info_text_object
        from bname_dev_work_info_text.utils.geom import m_to_mm, q_to_mm

        assert not hasattr(overlay, "_draw_work_info_texts"), "作品情報の古いオーバーレイ描画が残っています"
        assert not hasattr(overlay, "_draw_work_info_texts_pixel"), "作品情報の古いオーバーレイ描画が残っています"

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
        workinfo_coll = outliner_model.ensure_work_info_collection(scene)
        assert len(objs) == 6, f"作品情報テキスト数が不正です: {len(objs)}"
        assert all(obj.type == "FONT" for obj in objs), "作品情報がテキストオブジェクトではありません"
        assert workinfo_coll.name == "workinfo"
        for obj in objs:
            if list(obj.users_collection) != [workinfo_coll]:
                raise AssertionError(f"作品情報が workinfo コレクション外にあります: {obj.name}")
        bodies = sorted(str(obj.data.body) for obj in objs)
        assert "作品テスト" in bodies
        assert "作者" in bodies
        assert "ページ0005" in bodies and "ページ0006" in bodies
        bpy.context.view_layer.update()
        work_name_obj = next(obj for obj in objs if str(obj.data.body) == "作品テスト")
        bbox = [work_name_obj.matrix_world @ Vector(corner) for corner in work_name_obj.bound_box]
        height_mm = m_to_mm(max(v.y for v in bbox) - min(v.y for v in bbox))
        expected_mm = q_to_mm(float(info.display_work_name.font_size_q))
        assert expected_mm * 0.9 <= height_mm <= expected_mm * 1.15, (
            f"作品情報のQ数サイズが原稿上の見た目に反映されていません: "
            f"height={height_mm:.3f}mm expected={expected_mm:.3f}mm"
        )

        info.work_name = "更新後"
        page_grid.apply_page_collection_transforms(bpy.context, work)
        bodies = [str(obj.data.body) for obj in _work_info_objects(work_info_text_object)]
        assert "更新後" in bodies and "作品テスト" not in bodies

        info.display_author.enabled = False
        page_grid.apply_page_collection_transforms(bpy.context, work)
        bodies = [str(obj.data.body) for obj in _work_info_objects(work_info_text_object)]
        assert "作者" not in bodies, "非表示にした作品情報テキストが残っています"

        print("BNAME_WORK_INFO_TEXT_OBJECT_CHECK_OK")
    finally:
        mod.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


main()
