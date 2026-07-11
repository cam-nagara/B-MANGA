"""Blender 実機用: 多重線本数 +1 修正と主線→ring1 間隔修正を AI 目視で確認.

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_multi_line_count_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_MULTILINE_COUNT_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_multiline_count_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_multiline_count",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_multiline_count"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_ortho_camera(center_x_m: float, center_y_m: float, scale_m: float) -> None:
    if "確認カメラ" in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects["確認カメラ"], do_unlink=True)
    if "確認カメラ" in bpy.data.cameras:
        bpy.data.cameras.remove(bpy.data.cameras["確認カメラ"])
    camera_data = bpy.data.cameras.new("確認カメラ")
    camera = bpy.data.objects.new("確認カメラ", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = (center_x_m, center_y_m, 2.0)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = scale_m
    bpy.context.scene.camera = camera


def _render_to(path: Path, *, width_px: int = 900, height_px: int = 900) -> None:
    scene = bpy.context.scene
    engine_items = {
        item.identifier for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
    }
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engine_items else "BLENDER_EEVEE"
    scene.render.resolution_x = width_px
    scene.render.resolution_y = height_px
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(path)
    scene.render.film_transparent = False
    bpy.ops.render.render(write_still=True)


def _delete_all_balloons(page) -> None:
    while len(page.balloons) > 0:
        page.balloons.remove(0)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_multiline_count_work_"))
    _OUT_PATH.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "MultiCountCheck.bmanga"))
    assert "FINISHED" in result, result

    from bmanga_dev_multiline_count.core.work import get_work
    from bmanga_dev_multiline_count.operators import balloon_op
    from bmanga_dev_multiline_count.utils import balloon_curve_object
    from bmanga_dev_multiline_count.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = get_work(context)
    assert work is not None and work.loaded
    page = work.pages[0]
    parent_key = page_stack_key(page)

    # count=3 outside で「主線+多重線3本」が見える。spacing と本数を AI 目視できるサイズで配置。
    for shape, count in [("cloud", 3), ("rect", 3), ("ellipse", 4), ("thorn", 3), ("thorn-curve", 3), ("fluffy", 3)]:
        _delete_all_balloons(page)
        balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape, x=20.0, y=20.0, w=50.0, h=50.0,
            parent_kind="page", parent_key=parent_key,
        )
        entry.line_style = "double"
        entry.line_width_mm = 1.0
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry.fill_opacity = 100.0
        entry.multi_line_count = count
        entry.multi_line_direction = "outside"
        entry.multi_line_width_mm = 0.5
        entry.multi_line_spacing_mm = 1.5
        entry.multi_line_width_scale_percent = 100.0
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        assert obj is not None

        cx = float(obj.location.x)
        cy = float(obj.location.y)
        _set_ortho_camera(cx, cy, 0.085)
        out = _OUT_PATH / f"multi_count_{shape}_n{count}.png"
        _render_to(out)
        print(f"[OUT] {shape} count={count}: {out}")
    print(f"[DONE] 出力: {_OUT_PATH}")


if __name__ == "__main__":
    main()
