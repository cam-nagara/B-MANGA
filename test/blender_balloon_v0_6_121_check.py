"""v0.6.121 検証: ユーザー test97 設定そのままで多重線が描かれるか.

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-Name/test/blender_balloon_v0_6_121_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BNAME_V121_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bname_v121_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_v121",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_v121"] = mod
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


def _render_to(path: Path, *, width_px: int = 1200, height_px: int = 1200) -> None:
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
    temp_root = Path(tempfile.mkdtemp(prefix="bname_v121_work_"))
    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    result = bpy.ops.bname.work_new(filepath=str(temp_root / "V121Check.bname"))
    assert "FINISHED" in result, result

    from bname_dev_v121.core.work import get_work
    from bname_dev_v121.operators import balloon_op
    from bname_dev_v121.utils import balloon_curve_object
    from bname_dev_v121.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = get_work(context)
    page = work.pages[0]
    parent_key = page_stack_key(page)

    # ユーザー test97 完全再現: 12 トゲ + 多重線 6 本 + cross ON + 非一様幅
    entry = balloon_op._create_balloon_entry(
        context, page,
        shape="thorn", x=30.0, y=30.0, w=80.0, h=80.0,
        parent_kind="page", parent_key=parent_key,
    )
    entry.line_style = "double"
    entry.line_width_mm = 1.59
    entry.line_color = (1.0, 0.2, 0.2, 1.0)
    entry.fill_color = (1.0, 1.0, 1.0, 1.0)
    entry.multi_line_count = 6
    entry.multi_line_direction = "outside"
    entry.multi_line_width_mm = 0.66
    entry.multi_line_spacing_mm = 0.40
    entry.multi_line_width_scale_percent = 100.0
    entry.multi_line_spacing_scale_percent = 100.0
    entry.thorn_multi_line_valley_width_pct = 72.87
    entry.thorn_multi_line_peak_width_pct = 38.83
    entry.thorn_multi_line_length_scale_near_percent = 100.0
    entry.thorn_multi_line_length_scale_far_percent = 55.32
    entry.thorn_multi_line_cross_enabled = True
    sp = entry.shape_params
    sp.cloud_valley_sharp = True
    sp.cloud_bump_width_mm = 12.25
    sp.cloud_bump_height_mm = 13.18
    sp.cloud_offset_percent = 50.0
    sp.cloud_sub_width_ratio = 0.0
    sp.cloud_sub_height_ratio = 0.0
    obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    print(f"[INFO] Balloon obj created: {obj.name if obj is not None else None}")

    # 多重線オブジェクトの存在確認
    multi_obj_name = f"balloon_multi_line_mesh_{entry.id}"
    multi_obj = bpy.data.objects.get(multi_obj_name)
    if multi_obj is None:
        print(f"[FAIL] multi-line mesh '{multi_obj_name}' not generated")
    else:
        verts = len(multi_obj.data.vertices)
        faces = len(multi_obj.data.polygons)
        print(f"[OK] multi-line mesh exists: verts={verts}, faces={faces}")

    _set_ortho_camera(float(obj.location.x), float(obj.location.y), 0.30)
    out = _OUT_PATH / "07_user_test97.png"
    _render_to(out)
    print(f"[OUT] user test97: {out}")

    print(f"[DONE] 出力: {_OUT_PATH}")


if __name__ == "__main__":
    main()
