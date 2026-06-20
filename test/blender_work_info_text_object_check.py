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
        "bmanga_dev_work_info_text",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_work_info_text"] = mod
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
        from bmanga_dev_work_info_text.io import schema
        from bmanga_dev_work_info_text.ui import overlay, overlay_shared
        from bmanga_dev_work_info_text.utils import outliner_model, page_grid, work_info_text_object
        from bmanga_dev_work_info_text.utils.geom import m_to_mm, q_to_mm

        assert not hasattr(overlay, "_draw_work_info_texts"), "作品情報の古いオーバーレイ描画が残っています"
        assert not hasattr(overlay, "_draw_work_info_texts_pixel"), "作品情報の古いオーバーレイ描画が残っています"

        scene = bpy.context.scene
        work = scene.bmanga_work
        work.loaded = True
        work.paper.canvas_width_mm = 257.0
        work.paper.canvas_height_mm = 364.0
        work.paper.finish_width_mm = 221.81
        work.paper.finish_height_mm = 328.78
        work.paper.bleed_mm = 7.0
        work.paper.inner_frame_width_mm = 180.0
        work.paper.inner_frame_height_mm = 270.0
        work.paper.inner_frame_offset_x_mm = 0.0
        work.paper.inner_frame_offset_y_mm = 0.0
        for idx in range(2):
            page = work.pages.add()
            page.id = f"p{idx + 1:04d}"
            page.title = f"{idx + 1}ページ"

        info = work.work_info
        for item in (
            info.display_work_name,
            info.display_episode,
            info.display_subtitle,
            info.display_author,
            info.display_page_number,
        ):
            assert tuple(round(float(c), 6) for c in item.color[:4]) == (1.0, 1.0, 1.0, 1.0)
        schema.display_item_from_dict(info.display_subtitle, {})
        assert tuple(round(float(c), 6) for c in info.display_subtitle.color[:4]) == (1.0, 1.0, 1.0, 1.0)
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
        assert all(
            tuple(round(float(c), 6) for c in obj.active_material.diffuse_color[:4])
            == (1.0, 1.0, 1.0, 1.0)
            for obj in objs
        ), "作品情報テキストの初期色が白ではありません"
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
        rects = overlay_shared.compute_paper_rects(work.paper)
        ox, oy = page_grid.page_total_offset_mm(work, scene, 0)
        work_name_x = m_to_mm(float(work_name_obj.location.x)) - ox
        work_name_y = m_to_mm(float(work_name_obj.location.y)) - oy
        assert abs(work_name_x - rects.bleed.x) < 0.001, "作品名の左位置が裁ち落とし枠に揃っていません"
        assert abs(work_name_y - (rects.bleed.y2 + 2.0)) < 0.001, "作品名の上位置が裁ち落とし枠外側にありません"
        assert work_name_x < rects.inner_frame.x and work_name_y > rects.inner_frame.y2, (
            "作品情報が基本枠基準に戻っています"
        )
        author_obj = next(obj for obj in objs if str(obj.data.body) == "作者")
        author_x = m_to_mm(float(author_obj.location.x)) - ox
        author_y = m_to_mm(float(author_obj.location.y)) - oy
        assert abs(author_x - rects.bleed.x2) < 0.001, "作者名の右位置が裁ち落とし枠に揃っていません"
        assert abs(author_y - (rects.bleed.y - 2.0)) < 0.001, "作者名の下位置が裁ち落とし枠外側にありません"
        assert author_x > rects.inner_frame.x2 and author_y < rects.inner_frame.y
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

        print("BMANGA_WORK_INFO_TEXT_OBJECT_CHECK_OK")
    finally:
        mod.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


main()
