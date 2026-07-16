"""Blender 5.1: Meldex取込後のフキダシ・本文・ルビを画像で確認する。"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "_verify" / "2026-07-11_meldex_scenario_visual"
OUTPUT = OUT_DIR / "meldex_scenario_import.png"
REPORT = OUT_DIR / "result.json"
BLEND = OUT_DIR / "meldex_scenario_import.blend"
MODULE_NAME = "bmanga_dev_meldex_visual"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module.register()
    return module


def _payload() -> dict:
    return {
        "contract": "meldex-bmanga-scenario",
        "version": 1,
        "source": {"documentId": "visual-scenario"},
        "pages": [
            {
                "rows": [
                    {
                        "rowId": "dialogue",
                        "type": "ナレーション",
                        "body": "東京の朝\n物語が始まる",
                        "rubies": [
                            {
                                "start": 0,
                                "length": 2,
                                "rubyText": "とうきょう",
                                "style": "group",
                            }
                        ],
                    },
                    {
                        "rowId": "standard",
                        "type": "未登録タイプ",
                        "body": "標準フキダシ\n改行を保持",
                        "rubies": [],
                    },
                    {
                        "rowId": "long-ruby",
                        "type": "",
                        "body": "明日へ進む",
                        "rubies": [
                            {
                                "start": 0,
                                "length": 2,
                                "rubyText": "あしたへすすむためのながいルビ",
                                "style": "group",
                            }
                        ],
                    },
                ]
            },
            {"rows": [{"rowId": "page2", "type": "", "body": "二ページ目", "rubies": []}]},
        ],
    }


def _set_camera(objects: list[bpy.types.Object]) -> float:
    points = []
    for obj in objects:
        half_x = float(obj.dimensions.x) * 0.5
        half_y = float(obj.dimensions.y) * 0.5
        points.extend(
            (
                SimpleNamespace(x=float(obj.location.x) - half_x, y=float(obj.location.y) - half_y),
                SimpleNamespace(x=float(obj.location.x) + half_x, y=float(obj.location.y) + half_y),
            )
        )
    min_x = min(point.x for point in points)
    max_x = max(point.x for point in points)
    min_y = min(point.y for point in points)
    max_y = max(point.y for point in points)
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5
    camera_data = bpy.data.cameras.new("Meldex取込確認カメラ")
    camera = bpy.data.objects.new("Meldex取込確認カメラ", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = (center_x, center_y, 2.0)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = max(max_x - min_x, max_y - min_y) * 1.28
    bpy.context.scene.camera = camera
    return float(camera_data.ortho_scale)


def _render(objects: list[bpy.types.Object]) -> float:
    scene = bpy.context.scene
    bpy.context.view_layer.update()
    camera_scale = _set_camera(objects)
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 900
    scene.render.resolution_y = 1100
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.world = scene.world or bpy.data.worlds.new("World")
    scene.world.color = (0.16, 0.16, 0.16)
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.render.filepath = str(OUTPUT)
    result = bpy.ops.render.render(write_still=True)
    assert "FINISHED" in result and OUTPUT.is_file()
    _assert_text_pixels(scene, objects)
    return camera_scale


def _assert_text_pixels(scene: bpy.types.Scene, objects: list[bpy.types.Object]) -> None:
    from bpy_extras.object_utils import world_to_camera_view
    from mathutils import Vector
    from PIL import Image

    image = Image.open(OUTPUT).convert("RGB")
    width, height = image.size
    for obj in objects:
        if str(obj.get("bmanga_kind", "") or "") != "text":
            continue
        projected = [
            world_to_camera_view(scene, scene.camera, obj.matrix_world @ Vector(corner))
            for corner in obj.bound_box
        ]
        left = max(0, int(min(point.x for point in projected) * width))
        right = min(width, int(max(point.x for point in projected) * width) + 1)
        top = max(0, int((1.0 - max(point.y for point in projected)) * height))
        bottom = min(height, int((1.0 - min(point.y for point in projected)) * height) + 1)
        assert right > left and bottom > top
        dark_pixels = sum(
            1
            for red, green, blue in image.crop((left, top, right, bottom)).getdata()
            if max(red, green, blue) < 96
        )
        # マスク線が1本横切るだけでは合格しない量を要求し、文字面そのものを確認する。
        assert dark_pixels >= 500, f"レンダー内にテキストがありません: {obj.name} ({dark_pixels}px)"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _load_addon()
    work_dir = Path(tempfile.mkdtemp(prefix="bmanga_meldex_visual_")) / "visual.bmanga"
    try:
        result = bpy.ops.bmanga.work_new(filepath=str(work_dir))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result
        from bmanga_dev_meldex_visual.core.work import get_work
        from bmanga_dev_meldex_visual.io import balloon_presets, meldex_scenario_import, text_presets
        from bmanga_dev_meldex_visual.utils import balloon_curve_object, mask_apply, page_detail, text_real_object

        custom = SimpleNamespace(
            name="ナレーション",
            path=Path("ナレーション.json"),
            data={
                "vertices": [
                    [0, 18], [8, 4], [28, 0], [52, 6],
                    [60, 18], [50, 34], [28, 40], [6, 32],
                ]
            },
        )
        balloon_presets.list_all_presets = lambda _path: [custom]
        # 同梱テキストプリセット「ナレーション」と行の type 名が偶然一致すると、
        # v0.6.501 の linked_balloon_preset 連動 (text_preset があり
        # linked_balloon_preset が空ならフキダシを作らない) によりこの行の
        # フキダシが作られなくなってしまう。本テストの目的 (カスタム形状の
        # フキダシプリセット適用確認) を同梱プリセットの実データに依存させない
        # ため明示的にモックし、写像先のフキダシプリセットへ連動させておく。
        text_presets.list_all_presets = lambda _path: [
            # 未登録タイプのfallbackは、リンクフキダシを持たない先頭プリセット。
            # ナレーションは名前の完全一致で2件目を選び、カスタム形状へリンクする。
            SimpleNamespace(name="既定", data={"writing_mode": "horizontal", "linked_balloon_preset": ""}),
            SimpleNamespace(name="ナレーション", data={"writing_mode": "horizontal", "linked_balloon_preset": "ナレーション"}),
        ]
        work = get_work(bpy.context)
        initial_pages = len(work.pages)
        initial_comas = [len(page.comas) for page in work.pages]
        import_result = meldex_scenario_import.import_payload(bpy.context, work, _payload())
        assert import_result["pagesAdded"] == max(0, 2 - initial_pages)
        assert [len(page.comas) for page in work.pages[:initial_pages]] == initial_comas
        assert all(len(page.comas) == 0 for page in work.pages[initial_pages:])

        page = work.pages[0]
        page_detail.ensure_page_detail(work, page)
        visible_objects = []
        for balloon in page.balloons:
            obj = balloon_curve_object.ensure_balloon_curve_object(
                scene=bpy.context.scene,
                entry=balloon,
                page=page,
            )
            assert obj is not None
            visible_objects.append(obj)
        for text in page.texts:
            obj = text_real_object.ensure_text_real_object(
                scene=bpy.context.scene,
                entry=text,
                page=page,
            )
            assert obj is not None
            page_mask = obj.modifiers.get(mask_apply.MOD_NAME_PAGE_MASK)
            assert page_mask is not None and page_mask.object is not None
            mask_volume = page_mask.object.modifiers.get(mask_apply.MOD_NAME_PAGE_MASK_VOLUME)
            assert mask_volume is not None and mask_volume.show_render
            visible_objects.append(obj)

        assert len(page.balloons) == 3 and len(page.texts) == 3
        assert page.balloons[0].shape == "custom"
        assert page.balloons[1].shape == "ellipse"
        assert page.texts[0].writing_mode == "horizontal"
        assert page.texts[0].body == "東京の朝\n物語が始まる"
        assert page.texts[0].ruby_spans[0].ruby_text == "とうきょう"
        assert page.balloons[2].width_mm > page.texts[2].width_mm
        assert page.balloons[2].height_mm > page.texts[2].height_mm
        from bmanga_dev_meldex_visual.typography import ruby as ruby_layout

        long_ruby_pad = ruby_layout.render_pad_mm_for_entry(page.texts[2], minimum=0.0)
        assert page.balloons[2].width_mm >= page.texts[2].width_mm + long_ruby_pad * 2.0 + 12.0
        assert page.balloons[2].height_mm >= page.texts[2].height_mm + long_ruby_pad * 2.0 + 12.0
        for index, current in enumerate(page.balloons):
            for other in page.balloons[index + 1:]:
                separated = (
                    current.x_mm + current.width_mm <= other.x_mm
                    or other.x_mm + other.width_mm <= current.x_mm
                    or current.y_mm + current.height_mm <= other.y_mm
                    or other.y_mm + other.height_mm <= current.y_mm
                )
                assert separated, f"取込フキダシが重なっています: {current.id}, {other.id}"

        camera_scale = _render(visible_objects)
        REPORT.write_text(
            json.dumps(
                {
                    "pages": len(work.pages),
                    "comas": sum(len(item.comas) for item in work.pages),
                    "balloons": len(page.balloons),
                    "texts": len(page.texts),
                    "firstShape": page.balloons[0].shape,
                    "fallbackShape": page.balloons[1].shape,
                    "firstBody": page.texts[0].body,
                    "ruby": page.texts[0].ruby_spans[0].ruby_text,
                    "cameraScale": camera_scale,
                    "visualObjects": [
                        {
                            "name": obj.name,
                            "dimensions": [round(float(value), 5) for value in obj.dimensions],
                            "location": [round(float(value), 5) for value in obj.location],
                            "collections": [collection.name for collection in obj.users_collection],
                        }
                        for obj in visible_objects
                    ],
                    "output": str(OUTPUT),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        bpy.ops.wm.save_as_mainfile(filepath=str(BLEND), check_existing=False)
        print(f"BMANGA_MELDEX_SCENARIO_VISUAL_OK out={OUTPUT}", flush=True)
    finally:
        addon.unregister()


if __name__ == "__main__":
    main()
