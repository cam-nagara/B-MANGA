"""Blender 5.1.1 ヘッドレス検証: v0.6.114
1. 山の幅最大 200mm
2. ズラし量 100% = 全周 1 回転
3. 動的形状のベース 楕円/矩形 切り替え

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_v0_6_114_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_V114_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_v114_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_v114",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_v114"] = mod
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
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_v114_work_"))
    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "V114Check.bmanga"))
    assert "FINISHED" in result, result

    from bmanga_dev_v114.core.work import get_work
    from bmanga_dev_v114.operators import balloon_op
    from bmanga_dev_v114.utils import balloon_curve_object
    from bmanga_dev_v114.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = get_work(context)
    page = work.pages[0]
    parent_key = page_stack_key(page)

    DYNAMIC_SHAPES = ["cloud", "fluffy", "thorn", "thorn-curve"]

    # --- 1) 動的形状 ベース楕円 (default) ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    objects = []
    for idx, shape in enumerate(DYNAMIC_SHAPES):
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape, x=20.0 + idx * 80.0, y=20.0, w=70.0, h=70.0,
            parent_kind="page", parent_key=parent_key,
        )
        entry.line_style = "solid"
        entry.line_width_mm = 1.5
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry.shape_params.dynamic_shape_base_kind = "ellipse"
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        objects.append((obj, shape))
    xs = [float(o.location.x) for o, _ in objects]
    ys = [float(o.location.y) for o, _ in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = (max(xs) - min(xs)) + 0.1
    _set_ortho_camera(cx, cy, scale)
    out = _OUT_PATH / "01_base_ellipse.png"
    _render_to(out, width_px=2400, height_px=700)
    print(f"[OUT] base ellipse: {out}")

    # --- 2) 動的形状 ベース矩形 (新規) ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    objects = []
    for idx, shape in enumerate(DYNAMIC_SHAPES):
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape, x=20.0 + idx * 80.0, y=20.0, w=70.0, h=70.0,
            parent_kind="page", parent_key=parent_key,
        )
        entry.line_style = "solid"
        entry.line_width_mm = 1.5
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry.shape_params.dynamic_shape_base_kind = "rect"
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        objects.append((obj, shape))
    xs = [float(o.location.x) for o, _ in objects]
    ys = [float(o.location.y) for o, _ in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = (max(xs) - min(xs)) + 0.1
    _set_ortho_camera(cx, cy, scale)
    out = _OUT_PATH / "02_base_rect.png"
    _render_to(out, width_px=2400, height_px=700)
    print(f"[OUT] base rect: {out}")

    # --- 3) ズラし量で 0%, 25%, 50%, 75% を並べて回転を確認 (cloud) ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    objects = []
    for idx, offset_pct in enumerate([0.0, 25.0, 50.0, 75.0]):
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape="cloud", x=20.0 + idx * 80.0, y=20.0, w=70.0, h=70.0,
            parent_kind="page", parent_key=parent_key,
        )
        entry.line_style = "solid"
        entry.line_width_mm = 1.5
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry.shape_params.cloud_offset_percent = offset_pct
        entry.shape_params.shape_seed = 1  # 同じ seed で形状固定
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        objects.append((obj, f"offset={offset_pct}"))
    xs = [float(o.location.x) for o, _ in objects]
    ys = [float(o.location.y) for o, _ in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = (max(xs) - min(xs)) + 0.1
    _set_ortho_camera(cx, cy, scale)
    out = _OUT_PATH / "03_offset_rotation.png"
    _render_to(out, width_px=2400, height_px=700)
    print(f"[OUT] offset rotation 0/25/50/75 %: {out}")

    # --- 4) 山の幅 200mm を反映 (大きな雲フキダシで 山の幅 = 150mm) ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    entry = balloon_op._create_balloon_entry(
        context, page,
        shape="cloud", x=20.0, y=20.0, w=200.0, h=120.0,
        parent_kind="page", parent_key=parent_key,
    )
    entry.line_style = "solid"
    entry.line_width_mm = 2.0
    entry.line_color = (0.05, 0.05, 0.05, 1.0)
    entry.fill_color = (1.0, 1.0, 1.0, 1.0)
    entry.shape_params.cloud_bump_width_mm = 150.0  # 大きな bump (max 200mm)
    obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    _set_ortho_camera(float(obj.location.x), float(obj.location.y), 0.30)
    out = _OUT_PATH / "04_bump_width_150mm.png"
    _render_to(out, width_px=1400, height_px=900)
    print(f"[OUT] bump width 150mm: {out}")

    print(f"[DONE] 出力: {_OUT_PATH}")


if __name__ == "__main__":
    main()
