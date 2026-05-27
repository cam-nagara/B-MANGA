"""v0.6.120 検証: トゲ(直線) + 小山 + 長さ変化で破片化しないこと.

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-Name/test/blender_balloon_v0_6_120_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BNAME_V120_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bname_v120_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_v120",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_v120"] = mod
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
    # 背景を白にして黒の多重線が見えるように
    world = scene.world
    if world is not None:
        world.use_nodes = False
        world.color = (1.0, 1.0, 1.0)
    bpy.ops.render.render(write_still=True)


def _delete_all_balloons(page) -> None:
    while len(page.balloons) > 0:
        page.balloons.remove(0)


def _setup_thorn(context, page, parent_key, *, length_near, length_far, valley_sharp=True, cross_enabled=False, sub_bumps=True):
    from bname_dev_v120.operators import balloon_op
    from bname_dev_v120.utils import balloon_curve_object
    entry = balloon_op._create_balloon_entry(
        context, page,
        shape="thorn", x=30.0, y=30.0, w=80.0, h=80.0,
        parent_kind="page", parent_key=parent_key,
    )
    entry.line_style = "double"
    entry.line_width_mm = 2.5
    entry.line_color = (1.0, 0.2, 0.2, 1.0)  # 赤系 (背景の暗色と区別するため)
    entry.fill_color = (1.0, 1.0, 1.0, 1.0)
    entry.multi_line_count = 3
    entry.multi_line_direction = "outside"
    entry.multi_line_width_mm = 0.3
    entry.multi_line_spacing_mm = 0.4
    entry.multi_line_width_scale_percent = 100.0
    entry.multi_line_spacing_scale_percent = 100.0
    entry.thorn_multi_line_valley_width_pct = 100.0
    entry.thorn_multi_line_peak_width_pct = 100.0
    entry.thorn_multi_line_length_scale_near_percent = length_near
    entry.thorn_multi_line_length_scale_far_percent = length_far
    entry.thorn_multi_line_cross_enabled = cross_enabled
    sp = entry.shape_params
    sp.cloud_valley_sharp = valley_sharp
    sp.cloud_bump_width_mm = 16.87
    sp.cloud_bump_height_mm = 16.42
    sp.cloud_offset_percent = 50.0
    if sub_bumps:
        sp.cloud_sub_width_ratio = 29.88
        sp.cloud_sub_height_ratio = 30.42
    else:
        sp.cloud_sub_width_ratio = 0.0
        sp.cloud_sub_height_ratio = 0.0
    obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    return obj, entry


def _center_and_render(objects, out: Path) -> None:
    xs = [float(o.location.x) for o in objects]
    ys = [float(o.location.y) for o in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = max((max(xs) - min(xs)) + 0.1, 0.15)
    _set_ortho_camera(cx, cy, scale)
    _render_to(out)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_v120_work_"))
    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    result = bpy.ops.bname.work_new(filepath=str(temp_root / "V120Check.bname"))
    assert "FINISHED" in result, result

    from bname_dev_v120.core.work import get_work
    from bname_dev_v120.utils import balloon_curve_object
    from bname_dev_v120.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = get_work(context)
    page = work.pages[0]
    parent_key = page_stack_key(page)

    # --- 1) ベースライン: トゲ直線 + 小山 ON、長さ near=100% far=100% (= 全周閉ループ) ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    obj, _ = _setup_thorn(context, page, parent_key, length_near=100.0, length_far=100.0)
    out = _OUT_PATH / "01_thorn_sub_full.png"
    _center_and_render([obj], out)
    print(f"[OUT] thorn + sub-bumps, length 100/100 (baseline): {out}")

    # --- 2) スクショ再現: 長さ near=99.99% far=82.98% ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    obj, _ = _setup_thorn(context, page, parent_key, length_near=99.99, length_far=82.98)
    out = _OUT_PATH / "02_thorn_sub_99_82.png"
    _center_and_render([obj], out)
    print(f"[OUT] thorn + sub-bumps, near=99.99 far=82.98 (must NOT fragment): {out}")

    # --- 3) スクショ再現: 長さ near=95.74% far=53.19% ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    obj, _ = _setup_thorn(context, page, parent_key, length_near=95.74, length_far=53.19)
    out = _OUT_PATH / "03_thorn_sub_95_53.png"
    _center_and_render([obj], out)
    print(f"[OUT] thorn + sub-bumps, near=95.74 far=53.19 (must NOT fragment): {out}")

    # --- 4) 小山なしで同じ: 長さ near=95% far=50% ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    obj, _ = _setup_thorn(context, page, parent_key, length_near=95.0, length_far=50.0, sub_bumps=False)
    out = _OUT_PATH / "04_thorn_no_sub_95_50.png"
    _center_and_render([obj], out)
    print(f"[OUT] thorn no sub-bumps, near=95 far=50: {out}")

    # --- 5) 山谷を延ばして交差 ON + 長さ near=80% far=50% (小山あり) ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    obj, _ = _setup_thorn(context, page, parent_key, length_near=80.0, length_far=50.0, cross_enabled=True)
    out = _OUT_PATH / "05_thorn_sub_cross_80_50.png"
    _center_and_render([obj], out)
    print(f"[OUT] thorn + sub-bumps + cross, near=80 far=50: {out}")

    # --- 6) ユーザー再現: 谷=100%, 山=94.15% (僅か非一様) + 長さ 90/24 ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    from bname_dev_v120.operators import balloon_op
    entry = balloon_op._create_balloon_entry(
        context, page,
        shape="thorn", x=30.0, y=30.0, w=80.0, h=80.0,
        parent_kind="page", parent_key=parent_key,
    )
    entry.line_style = "double"
    entry.line_width_mm = 3.45
    entry.line_color = (1.0, 0.2, 0.2, 1.0)
    entry.fill_color = (1.0, 1.0, 1.0, 1.0)
    entry.multi_line_count = 3
    entry.multi_line_direction = "outside"
    entry.multi_line_width_mm = 0.30
    entry.multi_line_spacing_mm = 0.40
    entry.thorn_multi_line_valley_width_pct = 100.0
    entry.thorn_multi_line_peak_width_pct = 94.15
    entry.thorn_multi_line_length_scale_near_percent = 90.43
    entry.thorn_multi_line_length_scale_far_percent = 24.47
    sp = entry.shape_params
    sp.cloud_valley_sharp = True
    sp.cloud_bump_width_mm = 22.09
    sp.cloud_bump_height_mm = 11.44
    sp.cloud_offset_percent = 50.0
    sp.cloud_sub_width_ratio = 24.60
    sp.cloud_sub_height_ratio = 0.0
    obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    out = _OUT_PATH / "06_user_repro.png"
    _center_and_render([obj], out)
    print(f"[OUT] user repro (valley=100 peak=94.15 length 90/24): {out}")

    print(f"[DONE] 出力: {_OUT_PATH}")


if __name__ == "__main__":
    main()
