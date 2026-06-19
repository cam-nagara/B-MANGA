"""フキダシ複数重ねで BLENDED + 画像マスクの重なり順を検証する.

surface_render_method=BLENDED は depth を書き込まないため、 z_index で
奥/手前の sort が正しく効くか実機で確認する。

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_mask_overlap_repro.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_OVERLAP_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_overlap_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_overlap",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_overlap"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_ortho_camera(center_x_m: float, center_y_m: float, scale_m: float):
    for old in list(bpy.data.objects):
        if old.type == "CAMERA":
            bpy.data.objects.remove(old, do_unlink=True)
    cam_data = bpy.data.cameras.new("確認カメラ")
    cam = bpy.data.objects.new("確認カメラ", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = (center_x_m, center_y_m, 2.0)
    cam.rotation_euler = (0.0, 0.0, 0.0)
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = scale_m
    bpy.context.scene.camera = cam


def _render(path: Path, low_sample: bool = False):
    scene = bpy.context.scene
    engine_items = {item.identifier for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engine_items else "BLENDER_EEVEE"
    scene.render.resolution_x = 900
    scene.render.resolution_y = 900
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(path)
    scene.render.film_transparent = False
    eevee = getattr(scene, "eevee", None)
    if eevee is not None and low_sample:
        try:
            eevee.taa_render_samples = 1
            eevee.taa_samples = 1
        except (AttributeError, TypeError):
            pass
    bpy.ops.render.render(write_still=True)


def main():
    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()

    from bmanga_dev_overlap.core.work import get_work
    from bmanga_dev_overlap.utils import balloon_curve_object, coma_plane, coma_border_object
    from bmanga_dev_overlap.utils.layer_hierarchy import coma_stack_key
    from bmanga_dev_overlap.utils import page_grid

    tmp = Path(tempfile.mkdtemp(prefix="bmanga_overlap_"))
    res = bpy.ops.bmanga.work_new(filepath=str(tmp / "Overlap.bmanga"))
    assert "FINISHED" in res, res

    scene = bpy.context.scene
    work = get_work(bpy.context)
    page = work.pages[0]
    coma = page.comas[0]
    coma.shape_type = "rect"
    coma.rect_x_mm = 10.0
    coma.rect_y_mm = 10.0
    coma.rect_width_mm = 120.0
    coma.rect_height_mm = 120.0
    coma.background_color = (0.85, 0.85, 0.85, 1.0)
    coma.border.visible = True
    coma.border.style = "solid"
    coma.border.width_mm = 0.5
    coma.border.color = (0.0, 0.0, 0.0, 1.0)

    parent_key = coma_stack_key(page, coma)
    coma_plane.ensure_coma_plane(scene, work, page, coma)
    coma_plane.ensure_coma_mask(scene, work, page, coma)
    coma_border_object.ensure_coma_border_object(scene, work, page, coma)

    # フキダシ A: 奥 (青塗り), 左上
    a = page.balloons.add()
    a.id = "balloon_a_back"
    a.title = "奥A"
    a.shape = "cloud"
    a.x_mm = 25.0
    a.y_mm = 65.0
    a.width_mm = 55.0
    a.height_mm = 55.0
    a.parent_kind = "coma"
    a.parent_key = parent_key
    a.line_style = "solid"
    a.line_width_mm = 1.5
    a.line_color = (0.0, 0.0, 0.3, 1.0)
    a.fill_color = (0.4, 0.7, 1.0, 1.0)
    a.fill_opacity = 100.0

    # フキダシ B: 手前 (赤塗り), 中央 (A と重なる)
    b = page.balloons.add()
    b.id = "balloon_b_front"
    b.title = "手前B"
    b.shape = "cloud"
    b.x_mm = 55.0
    b.y_mm = 40.0
    b.width_mm = 55.0
    b.height_mm = 55.0
    b.parent_kind = "coma"
    b.parent_key = parent_key
    b.line_style = "solid"
    b.line_width_mm = 1.5
    b.line_color = (0.3, 0.0, 0.0, 1.0)
    b.fill_color = (1.0, 0.5, 0.5, 1.0)
    b.fill_opacity = 100.0

    obj_a = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=a, page=page)
    obj_b = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=b, page=page)
    assert obj_a is not None and obj_b is not None
    bpy.context.view_layer.update()

    page_off_x, page_off_y = page_grid.page_total_offset_mm(work, scene, 0)
    cx = (coma.rect_x_mm + coma.rect_width_mm * 0.5 + page_off_x) / 1000.0
    cy = (coma.rect_y_mm + coma.rect_height_mm * 0.5 + page_off_y) / 1000.0

    _set_ortho_camera(cx, cy, 0.20)

    out_f12 = _OUT_PATH / "overlap_f12.png"
    _render(out_f12)
    print(f"[OUT F12] {out_f12}")

    out_vp = _OUT_PATH / "overlap_viewport.png"
    _render(out_vp, low_sample=True)
    print(f"[OUT viewport] {out_vp}")

    print(f"[DONE] 出力: {_OUT_PATH}")
    print(f"  A (奥) z_index = {obj_a.get('bmanga_z_index', 'n/a')}")
    print(f"  B (手前) z_index = {obj_b.get('bmanga_z_index', 'n/a')}")


if __name__ == "__main__":
    main()
