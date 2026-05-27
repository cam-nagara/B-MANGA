"""v0.6.127 徹底チェック: 全形状を test100 風設定でレンダリングして問題がないか目視確認.

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-Name/test/blender_balloon_v0_6_127_full_audit.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BNAME_V127_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bname_v127_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_v127",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_v127"] = mod
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


def _delete_all_balloons(page) -> None:
    while len(page.balloons) > 0:
        page.balloons.remove(0)


SHAPES = ["cloud", "fluffy", "thorn", "thorn-curve", "rect", "ellipse", "octagon"]


def _create_entry(context, page, parent_key, shape: str, balloon_op):
    entry = balloon_op._create_balloon_entry(
        context, page,
        shape=shape, x=30.0, y=30.0, w=50.58, h=54.71,
        parent_kind="page", parent_key=parent_key,
    )
    # test100 風設定
    entry.line_style = "double"
    entry.line_width_mm = 2.43
    entry.line_color = (1.0, 0.0, 0.0, 1.0)
    entry.fill_color = (0.9, 0.9, 1.0, 1.0)
    entry.multi_line_count = 5
    entry.multi_line_direction = "both"
    entry.multi_line_width_mm = 0.30
    entry.multi_line_spacing_mm = 0.40
    entry.multi_line_width_scale_percent = 100.0
    entry.multi_line_spacing_scale_percent = 100.0
    entry.thorn_multi_line_valley_width_pct = 100.0
    entry.thorn_multi_line_peak_width_pct = 100.0
    entry.thorn_multi_line_length_scale_near_percent = 90.43
    entry.thorn_multi_line_length_scale_far_percent = 22.34
    entry.thorn_multi_line_cross_enabled = True
    entry.line_valley_width_pct = 100.0
    entry.line_peak_width_pct = 100.0
    sp = entry.shape_params
    sp.cloud_valley_sharp = True
    sp.cloud_bump_width_mm = 14.35
    sp.cloud_bump_width_jitter = 0.32
    sp.cloud_bump_height_mm = 13.96
    sp.cloud_bump_height_jitter = 0.55
    sp.cloud_offset_percent = 50.0
    sp.cloud_sub_width_ratio = 0.0
    sp.cloud_sub_height_ratio = 0.0
    sp.shape_seed = 0
    return entry


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_v127_work_"))
    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    result = bpy.ops.bname.work_new(filepath=str(temp_root / "V127Check.bname"))
    assert "FINISHED" in result, result

    from bname_dev_v127.core.work import get_work
    from bname_dev_v127.operators import balloon_op
    from bname_dev_v127.utils import balloon_curve_object
    from bname_dev_v127.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = get_work(context)
    page = work.pages[0]
    parent_key = page_stack_key(page)

    audit_results = []
    for shape in SHAPES:
        _delete_all_balloons(page)
        balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
        try:
            entry = _create_entry(context, page, parent_key, shape, balloon_op)
            obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
            if obj is None:
                audit_results.append((shape, False, "balloon obj is None"))
                continue
            # multi-line mesh の verts/faces 確認
            multi_obj = bpy.data.objects.get(f"balloon_multi_line_mesh_{entry.id}")
            multi_info = ""
            if multi_obj is not None:
                multi_info = f" multi verts={len(multi_obj.data.vertices)}, faces={len(multi_obj.data.polygons)}"
            # main line mesh
            line_obj = bpy.data.objects.get(f"balloon_line_mesh_{entry.id}")
            line_info = ""
            if line_obj is not None:
                line_info = f" line verts={len(line_obj.data.vertices)}"
            bx = float(obj.location.x)
            by = float(obj.location.y)
            _set_ortho_camera(bx, by, 0.15)
            out = _OUT_PATH / f"audit_{shape}.png"
            _render_to(out)
            print(f"[OK] {shape}:{multi_info}{line_info} -> {out}")
            audit_results.append((shape, True, str(out)))
        except Exception as e:
            print(f"[FAIL] {shape}: {e}")
            audit_results.append((shape, False, str(e)))

    print()
    print(f"[DONE] 出力: {_OUT_PATH}")
    for shape, ok, info in audit_results:
        status = "OK" if ok else "FAIL"
        print(f"  {status}: {shape} - {info}")


if __name__ == "__main__":
    main()
