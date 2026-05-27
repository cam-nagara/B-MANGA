"""Blender 5.1.1 ヘッドレス検証: v0.6.110 の修正:
1. 「間隔変化 (%)」 が機能する (新規)
2. 「長さ変化 (%)」「谷の線幅」「山の線幅」 が cloud/fluffy/thorn/thorn-curve で機能する

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-Name/test/blender_balloon_v0_6_110_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BNAME_V110_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bname_v110_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_v110",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_v110"] = mod
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


def _render_to(path: Path, *, width_px: int = 1400, height_px: int = 1000) -> None:
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
    temp_root = Path(tempfile.mkdtemp(prefix="bname_v110_work_"))
    _OUT_PATH.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    result = bpy.ops.bname.work_new(filepath=str(temp_root / "V110Check.bname"))
    assert "FINISHED" in result, result

    from bname_dev_v110.core.work import get_work
    from bname_dev_v110.operators import balloon_op
    from bname_dev_v110.utils import balloon_curve_object
    from bname_dev_v110.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = get_work(context)
    page = work.pages[0]
    parent_key = page_stack_key(page)

    DYNAMIC_SHAPES = ["cloud", "fluffy", "thorn", "thorn-curve"]

    # --- 1) 間隔変化 (%) を 80% に設定して各形状で多重線レンダー ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    objects = []
    for idx, shape in enumerate(DYNAMIC_SHAPES):
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape, x=20.0 + idx * 70.0, y=20.0, w=60.0, h=60.0,
            parent_kind="page", parent_key=parent_key,
        )
        entry.line_style = "double"
        entry.line_width_mm = 1.0
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (0.8, 1.0, 0.9, 1.0)
        entry.multi_line_count = 4
        entry.multi_line_direction = "outside"
        entry.multi_line_width_mm = 0.5
        entry.multi_line_spacing_mm = 1.0
        entry.multi_line_width_scale_percent = 100.0
        entry.multi_line_spacing_scale_percent = 70.0  # 間隔がリングごとに 70% 縮む
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        objects.append((obj, shape))
    xs = [float(o.location.x) for o, _ in objects]
    ys = [float(o.location.y) for o, _ in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = (max(xs) - min(xs)) + 0.1
    _set_ortho_camera(cx, cy, scale)
    out = _OUT_PATH / "01_spacing_scale_70pct.png"
    _render_to(out, width_px=2200, height_px=700)
    print(f"[OUT] spacing_scale 70%: {out}")

    # --- 2) 長さ変化 (%) を 50% にして各形状で確認 ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    objects = []
    for idx, shape in enumerate(DYNAMIC_SHAPES):
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape, x=20.0 + idx * 70.0, y=20.0, w=60.0, h=60.0,
            parent_kind="page", parent_key=parent_key,
        )
        entry.line_style = "double"
        entry.line_width_mm = 1.0
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (0.8, 1.0, 0.9, 1.0)
        entry.multi_line_count = 3
        entry.multi_line_direction = "outside"
        entry.multi_line_width_mm = 0.6
        entry.multi_line_spacing_mm = 0.8
        entry.multi_line_width_scale_percent = 100.0
        entry.multi_line_spacing_scale_percent = 100.0
        entry.thorn_multi_line_length_scale_percent = 50.0  # 山周りだけ描く
        entry.thorn_multi_line_valley_width_mm = 0.6
        entry.thorn_multi_line_peak_width_mm = 0.6
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        objects.append((obj, shape))
    xs = [float(o.location.x) for o, _ in objects]
    ys = [float(o.location.y) for o, _ in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = (max(xs) - min(xs)) + 0.1
    _set_ortho_camera(cx, cy, scale)
    out = _OUT_PATH / "02_length_scale_50pct.png"
    _render_to(out, width_px=2200, height_px=700)
    print(f"[OUT] length_scale 50%: {out}")

    # --- 3) 谷の線幅と山の線幅を差分 (谷 0.2mm, 山 1.0mm) ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    objects = []
    for idx, shape in enumerate(DYNAMIC_SHAPES):
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape, x=20.0 + idx * 70.0, y=20.0, w=60.0, h=60.0,
            parent_kind="page", parent_key=parent_key,
        )
        entry.line_style = "double"
        entry.line_width_mm = 1.0
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (0.8, 1.0, 0.9, 1.0)
        entry.multi_line_count = 3
        entry.multi_line_direction = "outside"
        entry.multi_line_width_mm = 0.6
        entry.multi_line_spacing_mm = 0.8
        entry.multi_line_width_scale_percent = 100.0
        entry.multi_line_spacing_scale_percent = 100.0
        entry.thorn_multi_line_length_scale_percent = 100.0
        entry.thorn_multi_line_valley_width_mm = 0.2  # 谷だけ細く
        entry.thorn_multi_line_peak_width_mm = 1.5   # 山は太く
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        objects.append((obj, shape))
    xs = [float(o.location.x) for o, _ in objects]
    ys = [float(o.location.y) for o, _ in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = (max(xs) - min(xs)) + 0.1
    _set_ortho_camera(cx, cy, scale)
    out = _OUT_PATH / "03_valley_peak_width_diff.png"
    _render_to(out, width_px=2200, height_px=700)
    print(f"[OUT] valley/peak width diff: {out}")

    # --- 4) 谷幅=0 AND 山幅=0 → 多重線全体が非表示になることを確認 ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    objects = []
    for idx, shape in enumerate(DYNAMIC_SHAPES):
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape, x=20.0 + idx * 70.0, y=20.0, w=60.0, h=60.0,
            parent_kind="page", parent_key=parent_key,
        )
        entry.line_style = "double"
        entry.line_width_mm = 1.0
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (0.8, 1.0, 0.9, 1.0)
        entry.multi_line_count = 3
        entry.multi_line_direction = "outside"
        entry.multi_line_width_mm = 0.6
        entry.multi_line_spacing_mm = 0.8
        entry.thorn_multi_line_valley_width_mm = 0.0  # 両方 0
        entry.thorn_multi_line_peak_width_mm = 0.0
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        objects.append((obj, shape))
    xs = [float(o.location.x) for o, _ in objects]
    ys = [float(o.location.y) for o, _ in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = (max(xs) - min(xs)) + 0.1
    _set_ortho_camera(cx, cy, scale)
    out = _OUT_PATH / "04_both_widths_zero.png"
    _render_to(out, width_px=2200, height_px=700)
    print(f"[OUT] both widths zero: {out}")

    # --- 5) 長さ変化を per-ring で確認: count=4 outside, length_scale=50% ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    objects = []
    for idx, shape in enumerate(DYNAMIC_SHAPES):
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape, x=20.0 + idx * 80.0, y=20.0, w=70.0, h=70.0,
            parent_kind="page", parent_key=parent_key,
        )
        entry.line_style = "double"
        entry.line_width_mm = 1.0
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (0.8, 1.0, 0.9, 1.0)
        entry.multi_line_count = 4
        entry.multi_line_direction = "outside"
        entry.multi_line_width_mm = 0.5
        entry.multi_line_spacing_mm = 0.8
        entry.thorn_multi_line_length_scale_percent = 50.0
        # valley/peak は base に揃える (= 長さ変化のみ確認)
        entry.thorn_multi_line_valley_width_mm = 0.5
        entry.thorn_multi_line_peak_width_mm = 0.5
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        objects.append((obj, shape))
    xs = [float(o.location.x) for o, _ in objects]
    ys = [float(o.location.y) for o, _ in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = (max(xs) - min(xs)) + 0.1
    _set_ortho_camera(cx, cy, scale)
    out = _OUT_PATH / "05_length_scale_per_ring.png"
    _render_to(out, width_px=2400, height_px=800)
    print(f"[OUT] length_scale per-ring: {out}")

    print(f"[DONE] 出力: {_OUT_PATH}")


if __name__ == "__main__":
    main()
