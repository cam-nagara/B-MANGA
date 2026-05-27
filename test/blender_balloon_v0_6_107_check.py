"""Blender 5.1.1 ヘッドレス検証: v0.6.107 の修正:
1. 多重線が角 (とくに谷) でごちゃごちゃせず、意図しないトゲが出ないこと
2. トゲ曲線のピークが直線ではなく曲線になっていること

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-Name/test/blender_balloon_v0_6_107_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BNAME_V107_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bname_v107_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_v107",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_v107"] = mod
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


def _render_to(path: Path, *, width_px: int = 1400, height_px: int = 1200) -> None:
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
    temp_root = Path(tempfile.mkdtemp(prefix="bname_v107_work_"))
    _OUT_PATH.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    result = bpy.ops.bname.work_new(filepath=str(temp_root / "V107Check.bname"))
    assert "FINISHED" in result, result

    from bname_dev_v107.core.work import get_work
    from bname_dev_v107.operators import balloon_op
    from bname_dev_v107.utils import balloon_curve_object
    from bname_dev_v107.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = get_work(context)
    page = work.pages[0]
    parent_key = page_stack_key(page)

    # --- 1) 多重線の角/谷ごちゃごちゃ修正: トゲ系で確認 ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    objects = []
    for idx, shape in enumerate(["thorn", "thorn-curve", "fluffy", "cloud", "octagon"]):
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape, x=20.0 + idx * 70.0, y=20.0, w=60.0, h=60.0,
            parent_kind="page", parent_key=parent_key,
        )
        entry.line_style = "double"
        entry.line_width_mm = 1.0
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (0.6, 1.0, 0.8, 1.0)
        entry.multi_line_count = 4
        entry.multi_line_direction = "outside"
        entry.multi_line_width_mm = 0.5
        entry.multi_line_spacing_mm = 0.8
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        objects.append((obj, shape))
    xs = [float(o.location.x) for o, _ in objects]
    ys = [float(o.location.y) for o, _ in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = (max(xs) - min(xs)) + 0.1
    _set_ortho_camera(cx, cy, scale)
    out = _OUT_PATH / "01_multiline_no_artifacts.png"
    _render_to(out, width_px=2200, height_px=600)
    print(f"[OUT] multiline no artifacts: {out}")

    # --- 2) トゲ曲線: 主線 + 多重線 のクローズアップで曲線の山を確認 ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    entry = balloon_op._create_balloon_entry(
        context, page,
        shape="thorn-curve", x=20.0, y=20.0, w=80.0, h=80.0,
        parent_kind="page", parent_key=parent_key,
    )
    entry.line_style = "solid"
    entry.line_width_mm = 1.5
    entry.line_color = (0.05, 0.05, 0.05, 1.0)
    entry.fill_color = (0.6, 1.0, 0.8, 1.0)
    obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    cx = float(obj.location.x)
    cy = float(obj.location.y)
    _set_ortho_camera(cx, cy, 0.13)
    out = _OUT_PATH / "02_thorn_curve_smooth_peaks.png"
    _render_to(out, width_px=900, height_px=900)
    print(f"[OUT] thorn-curve smooth peaks: {out}")
    # クローズアップ: 1 山だけを画面に大きく出して山先端の曲線を確認
    _set_ortho_camera(cx + 0.025, cy, 0.04)
    out_close = _OUT_PATH / "02b_thorn_curve_peak_closeup.png"
    _render_to(out_close, width_px=900, height_px=900)
    print(f"[OUT] thorn-curve peak closeup: {out_close}")

    # --- 3) トゲ曲線 多重線 ---
    entry.line_style = "double"
    entry.multi_line_count = 3
    entry.multi_line_direction = "outside"
    entry.multi_line_width_mm = 0.5
    entry.multi_line_spacing_mm = 1.2
    balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    out = _OUT_PATH / "03_thorn_curve_multiline.png"
    _render_to(out, width_px=900, height_px=900)
    print(f"[OUT] thorn-curve multi-line: {out}")

    print(f"[DONE] 出力: {_OUT_PATH}")


if __name__ == "__main__":
    main()
