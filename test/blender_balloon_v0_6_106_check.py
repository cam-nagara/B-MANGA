"""Blender 5.1.1 ヘッドレス検証: v0.6.106 で扱う 4 つの修正を一括 AI 目視確認.

1. 「角を尖らせる」を全形状に適用 (round vs mitre 比較)
2. 多重線の外側基準を主線外側アウトラインに変更 (太い線でもリング 1 が見える)
3. フキダシ重なり時、下の線が上の塗りに透けないこと
4. 破線 / 点線の対応 (各形状で実線と差異が出る)

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_v0_6_106_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_V106_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_v106_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_v106",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_v106"] = mod
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
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_v106_work_"))
    _OUT_PATH.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "V106Check.bmanga"))
    assert "FINISHED" in result, result

    from bmanga_dev_v106.core.work import get_work
    from bmanga_dev_v106.operators import balloon_op
    from bmanga_dev_v106.utils import balloon_curve_object
    from bmanga_dev_v106.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = get_work(context)
    page = work.pages[0]
    parent_key = page_stack_key(page)

    SHAPES = ["rect", "ellipse", "octagon", "cloud", "fluffy", "thorn", "thorn-curve"]

    # --- 1) 角を尖らせる: round vs mitre 比較 (全形状) ---
    for valley_sharp_value in (False, True):
        _delete_all_balloons(page)
        balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
        objects = []
        for idx, shape in enumerate(SHAPES):
            entry = balloon_op._create_balloon_entry(
                context, page,
                shape=shape, x=20.0 + idx * 60.0, y=20.0, w=50.0, h=50.0,
                parent_kind="page", parent_key=parent_key,
            )
            entry.line_style = "solid"
            entry.line_width_mm = 2.0
            entry.line_color = (0.05, 0.05, 0.05, 1.0)
            entry.fill_color = (1.0, 1.0, 1.0, 1.0)
            entry.fill_opacity = 100.0
            entry.shape_params.cloud_valley_sharp = bool(valley_sharp_value)
            obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
            objects.append((obj, shape))
        xs = [float(o.location.x) for o, _ in objects]
        ys = [float(o.location.y) for o, _ in objects]
        cx = (min(xs) + max(xs)) * 0.5
        cy = (min(ys) + max(ys)) * 0.5
        scale = (max(xs) - min(xs)) + 0.1
        _set_ortho_camera(cx, cy, scale)
        suffix = "mitre" if valley_sharp_value else "round"
        out = _OUT_PATH / f"01_sharp_corners_{suffix}.png"
        _render_to(out, width_px=2200, height_px=600)
        print(f"[OUT] sharp_corners {suffix}: {out}")

    # --- 2) 多重線外側基準: 太い線で多重線がちゃんと外側に並ぶか (count=3 outside) ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    objects = []
    for idx, shape in enumerate(SHAPES):
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape, x=20.0 + idx * 70.0, y=20.0, w=50.0, h=50.0,
            parent_kind="page", parent_key=parent_key,
        )
        entry.line_style = "double"
        entry.line_width_mm = 5.0  # 太線で多重線が隠れないか確認
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry.multi_line_count = 3
        entry.multi_line_direction = "outside"
        entry.multi_line_width_mm = 0.6
        entry.multi_line_spacing_mm = 1.0
        entry.multi_line_width_scale_percent = 100.0
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        objects.append((obj, shape))
    xs = [float(o.location.x) for o, _ in objects]
    ys = [float(o.location.y) for o, _ in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = (max(xs) - min(xs)) + 0.1
    _set_ortho_camera(cx, cy, scale)
    out = _OUT_PATH / "02_multiline_outside_thick_line.png"
    _render_to(out, width_px=2400, height_px=700)
    print(f"[OUT] multi-line thick line: {out}")

    # --- 3) フキダシ重なり: 下のフキダシの線が上の塗りに透けないか ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    # 下に大きな緑のフキダシ
    entry_a = balloon_op._create_balloon_entry(
        context, page,
        shape="cloud", x=30.0, y=30.0, w=80.0, h=80.0,
        parent_kind="page", parent_key=parent_key,
    )
    entry_a.line_style = "solid"
    entry_a.line_width_mm = 3.0
    entry_a.line_color = (0.1, 0.1, 0.1, 1.0)
    entry_a.fill_color = (0.6, 1.0, 0.6, 1.0)
    entry_a.fill_opacity = 100.0
    obj_a = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry_a, page=page)
    # 上に重ねる白いフキダシ
    entry_b = balloon_op._create_balloon_entry(
        context, page,
        shape="rect", x=70.0, y=70.0, w=70.0, h=70.0,
        parent_kind="page", parent_key=parent_key,
    )
    entry_b.line_style = "solid"
    entry_b.line_width_mm = 1.5
    entry_b.line_color = (0.1, 0.1, 0.1, 1.0)
    entry_b.fill_color = (1.0, 1.0, 1.0, 1.0)
    entry_b.fill_opacity = 100.0
    obj_b = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry_b, page=page)
    # ページ世界オフセットを camera 位置に反映
    cx = (float(obj_a.location.x) + float(obj_b.location.x)) * 0.5
    cy = (float(obj_a.location.y) + float(obj_b.location.y)) * 0.5
    _set_ortho_camera(cx, cy, 0.18)
    out = _OUT_PATH / "03_balloon_overlap.png"
    _render_to(out, width_px=900, height_px=900)
    print(f"[OUT] balloon overlap: {out}")

    # --- 4) 破線 / 点線 (全形状 × dashed/dotted) ---
    for style in ("dashed", "dotted"):
        _delete_all_balloons(page)
        balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
        objects = []
        for idx, shape in enumerate(SHAPES):
            entry = balloon_op._create_balloon_entry(
                context, page,
                shape=shape, x=20.0 + idx * 60.0, y=20.0, w=50.0, h=50.0,
                parent_kind="page", parent_key=parent_key,
            )
            entry.line_style = style
            entry.line_width_mm = 1.5
            entry.line_color = (0.05, 0.05, 0.05, 1.0)
            entry.fill_color = (1.0, 1.0, 1.0, 1.0)
            entry.fill_opacity = 100.0
            obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
            objects.append((obj, shape))
        xs = [float(o.location.x) for o, _ in objects]
        ys = [float(o.location.y) for o, _ in objects]
        cx = (min(xs) + max(xs)) * 0.5
        cy = (min(ys) + max(ys)) * 0.5
        scale = (max(xs) - min(xs)) + 0.1
        _set_ortho_camera(cx, cy, scale)
        out = _OUT_PATH / f"04_line_style_{style}.png"
        _render_to(out, width_px=2200, height_px=600)
        print(f"[OUT] line_style {style}: {out}")

    print(f"[DONE] 出力: {_OUT_PATH}")


if __name__ == "__main__":
    main()
