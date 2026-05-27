"""v0.6.118 検証: 長さ変化 95% で多重線が破片化しないこと.

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-Name/test/blender_balloon_v0_6_118_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BNAME_V118_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bname_v118_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_v118",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_v118"] = mod
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


def _render_to(path: Path, *, width_px: int = 2400, height_px: int = 700) -> None:
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
    temp_root = Path(tempfile.mkdtemp(prefix="bname_v118_work_"))
    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    result = bpy.ops.bname.work_new(filepath=str(temp_root / "V118Check.bname"))
    assert "FINISHED" in result, result

    from bname_dev_v118.core.work import get_work
    from bname_dev_v118.operators import balloon_op
    from bname_dev_v118.utils import balloon_curve_object
    from bname_dev_v118.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = get_work(context)
    page = work.pages[0]
    parent_key = page_stack_key(page)

    SHAPES = ["cloud", "fluffy", "thorn", "thorn-curve"]

    # 長さ変化 95% で多重線がきれいに見えるか (= 破片化しない)
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    objects = []
    for idx, shape in enumerate(SHAPES):
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape, x=20.0 + idx * 80.0, y=20.0, w=70.0, h=70.0,
            parent_kind="page", parent_key=parent_key,
        )
        entry.line_style = "double"
        entry.line_width_mm = 2.0
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry.multi_line_count = 3
        entry.multi_line_direction = "outside"
        entry.multi_line_width_mm = 0.5
        entry.multi_line_spacing_mm = 1.0
        entry.thorn_multi_line_valley_width_pct = 100.0
        entry.thorn_multi_line_peak_width_pct = 100.0
        entry.thorn_multi_line_length_scale_percent = 95.0  # 5% 削る
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        objects.append((obj, shape))
    xs = [float(o.location.x) for o, _ in objects]
    ys = [float(o.location.y) for o, _ in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = (max(xs) - min(xs)) + 0.1
    _set_ortho_camera(cx, cy, scale)
    out = _OUT_PATH / "01_length95_uniform_width.png"
    _render_to(out)
    print(f"[OUT] length 95% uniform width (no fragmentation): {out}")

    # 長さ変化 50% で 多重線が valley 起点で短くなる (山頂が削れる)
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    objects = []
    for idx, shape in enumerate(SHAPES):
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape, x=20.0 + idx * 80.0, y=20.0, w=70.0, h=70.0,
            parent_kind="page", parent_key=parent_key,
        )
        entry.line_style = "double"
        entry.line_width_mm = 2.0
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry.multi_line_count = 3
        entry.multi_line_direction = "outside"
        entry.multi_line_width_mm = 0.5
        entry.multi_line_spacing_mm = 1.0
        entry.thorn_multi_line_valley_width_pct = 100.0
        entry.thorn_multi_line_peak_width_pct = 100.0
        entry.thorn_multi_line_length_scale_percent = 50.0
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        objects.append((obj, shape))
    out = _OUT_PATH / "02_length50_uniform_width.png"
    xs = [float(o.location.x) for o, _ in objects]
    ys = [float(o.location.y) for o, _ in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = (max(xs) - min(xs)) + 0.1
    _set_ortho_camera(cx, cy, scale)
    _render_to(out)
    print(f"[OUT] length 50% uniform width: {out}")

    print(f"[DONE] 出力: {_OUT_PATH}")


if __name__ == "__main__":
    main()
