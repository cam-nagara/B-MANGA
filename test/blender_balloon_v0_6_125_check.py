"""v0.6.125 検証: ユーザー test99 (18 トゲ, length 123/43, cross ON) で崩れないか.

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_v0_6_125_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_V125_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_v125_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_v125",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_v125"] = mod
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


def _render_to(path: Path, *, width_px: int = 1400, height_px: int = 1400) -> None:
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


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_v125_work_"))
    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "V125Check.bmanga"))
    assert "FINISHED" in result, result

    from bmanga_dev_v125.core.work import get_work
    from bmanga_dev_v125.operators import balloon_op
    from bmanga_dev_v125.utils import balloon_curve_object
    from bmanga_dev_v125.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = get_work(context)
    page = work.pages[0]
    parent_key = page_stack_key(page)

    # ユーザー test99 完全再現: 18 トゲ + line_width 2.58mm + length 123/43 + cross ON
    entry = balloon_op._create_balloon_entry(
        context, page,
        shape="thorn", x=30.0, y=30.0, w=57.31, h=64.48,
        parent_kind="page", parent_key=parent_key,
    )
    entry.line_style = "double"
    entry.line_width_mm = 2.58
    entry.line_color = (1.0, 0.0, 0.0, 1.0)  # 赤線で可視化
    entry.fill_color = (1.0, 1.0, 1.0, 1.0)
    entry.multi_line_count = 3
    entry.multi_line_direction = "outside"
    entry.multi_line_width_mm = 0.30
    entry.multi_line_spacing_mm = 0.40
    entry.multi_line_width_scale_percent = 100.0
    entry.multi_line_spacing_scale_percent = 100.0
    entry.thorn_multi_line_valley_width_pct = 100.0
    entry.thorn_multi_line_peak_width_pct = 100.0
    entry.thorn_multi_line_length_scale_near_percent = 123.40
    entry.thorn_multi_line_length_scale_far_percent = 43.62
    entry.thorn_multi_line_cross_enabled = True
    sp = entry.shape_params
    sp.cloud_valley_sharp = True
    sp.cloud_bump_width_mm = 10.00
    sp.cloud_bump_height_mm = 12.64
    sp.cloud_offset_percent = 50.0
    sp.cloud_sub_width_ratio = 0.0
    sp.cloud_sub_height_ratio = 0.0
    obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    print(f"[INFO] Balloon created: {obj.name if obj else None}")

    multi_obj = bpy.data.objects.get(f"balloon_multi_line_mesh_{entry.id}")
    if multi_obj is not None:
        print(f"[OK] multi-line mesh: verts={len(multi_obj.data.vertices)}, faces={len(multi_obj.data.polygons)}")

    bx = float(obj.location.x)
    by = float(obj.location.y)
    _set_ortho_camera(bx, by, 0.15)  # 150mm 視野で 57mm 体を表示
    out = _OUT_PATH / "10_user_test99.png"
    _render_to(out)
    print(f"[OUT] user test99 (18 トゲ, length 123/43, cross ON): {out}")

    print(f"[DONE] 出力: {_OUT_PATH}")


if __name__ == "__main__":
    main()
