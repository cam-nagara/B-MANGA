"""Blender 5.2 ヘッドレス検証: v0.6.116
- 「主線・谷/山の線幅」「谷/山の線幅」の最大値を 100% に制限.
- 0% に設定したとき、ちゃんと線が細く消えるところまでフェードする (= 100% に
  戻る挙動 = `or 100.0` フォールバックバグ修正).
- 可変幅主線で body の谷の尖り/形状がそのまま残る.
- 多重線が雲フキダシの小山にも変化が反映される.
- 「長さ変化」 < 100% で 多重線が谷から伸び、山側終点が谷に近づく形で短くなる.

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_v0_6_116_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_V116_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_v116_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_v116",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_v116"] = mod
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
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_v116_work_"))
    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "V116Check.bmanga"))
    assert "FINISHED" in result, result

    from bmanga_dev_v116.core.work import get_work
    from bmanga_dev_v116.operators import balloon_op
    from bmanga_dev_v116.utils import balloon_curve_object
    from bmanga_dev_v116.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = get_work(context)
    page = work.pages[0]
    parent_key = page_stack_key(page)

    SHAPES = ["cloud", "fluffy", "thorn", "thorn-curve"]

    # --- max=100% に変更されたか確認 (entry インスタンスの bl_rna 経由) ---
    sample_entry = balloon_op._create_balloon_entry(
        context, page, shape="cloud", x=0.0, y=0.0, w=10.0, h=10.0,
        parent_kind="page", parent_key=parent_key,
    )
    for prop_name in ("line_valley_width_pct", "line_peak_width_pct",
                      "thorn_multi_line_valley_width_pct", "thorn_multi_line_peak_width_pct"):
        prop = sample_entry.bl_rna.properties[prop_name]
        assert abs(prop.hard_max - 100.0) < 1e-6, f"{prop_name} hard_max={prop.hard_max} expected 100"
        print(f"[OK] {prop_name} hard_max=100%")
    page.balloons.remove(len(page.balloons) - 1)

    # --- 1) 主線: 谷100%/山0% (谷で太く山で消える線形テーパー). body の鋭い谷が残るか ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    objects = []
    for idx, shape in enumerate(SHAPES):
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape, x=20.0 + idx * 80.0, y=20.0, w=70.0, h=70.0,
            parent_kind="page", parent_key=parent_key,
        )
        entry.line_style = "solid"
        entry.line_width_mm = 2.0
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry.line_valley_width_pct = 100.0
        entry.line_peak_width_pct = 0.0  # 山で消える
        entry.shape_params.cloud_valley_sharp = True  # 角を尖らせる
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        objects.append((obj, shape))
    xs = [float(o.location.x) for o, _ in objects]
    ys = [float(o.location.y) for o, _ in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = (max(xs) - min(xs)) + 0.1
    _set_ortho_camera(cx, cy, scale)
    out = _OUT_PATH / "01_main_taper_sharp.png"
    _render_to(out)
    print(f"[OUT] main taper (valley100/peak0, sharp): {out}")

    # --- 2) 主線: 谷33%/山100% (スクショ再現: ユーザー設定をシミュレート) ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    objects = []
    for idx, shape in enumerate(SHAPES):
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape, x=20.0 + idx * 80.0, y=20.0, w=70.0, h=70.0,
            parent_kind="page", parent_key=parent_key,
        )
        entry.line_style = "solid"
        entry.line_width_mm = 3.5
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (0.2, 1.0, 0.2, 1.0)
        entry.line_valley_width_pct = 33.0
        entry.line_peak_width_pct = 100.0
        entry.shape_params.cloud_valley_sharp = True
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        objects.append((obj, shape))
    xs = [float(o.location.x) for o, _ in objects]
    ys = [float(o.location.y) for o, _ in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = (max(xs) - min(xs)) + 0.1
    _set_ortho_camera(cx, cy, scale)
    out = _OUT_PATH / "02_main_valley33_peak100_sharp.png"
    _render_to(out)
    print(f"[OUT] main valley33/peak100 (no shape collapse): {out}")

    # --- 3) 「0% で 100% に戻る」バグ修正検証: 主線・山=0% にしたとき主線が
    #         実際に細くなって消えるところまでフェードする ---
    _delete_all_balloons(page)
    balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
    objects = []
    for idx, shape in enumerate(SHAPES):
        entry = balloon_op._create_balloon_entry(
            context, page,
            shape=shape, x=20.0 + idx * 80.0, y=20.0, w=70.0, h=70.0,
            parent_kind="page", parent_key=parent_key,
        )
        entry.line_style = "solid"
        entry.line_width_mm = 2.5
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry.line_valley_width_pct = 0.0  # 谷で 0
        entry.line_peak_width_pct = 100.0
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        objects.append((obj, shape))
    xs = [float(o.location.x) for o, _ in objects]
    ys = [float(o.location.y) for o, _ in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = (max(xs) - min(xs)) + 0.1
    _set_ortho_camera(cx, cy, scale)
    out = _OUT_PATH / "03_main_valley_zero_fades_at_valley.png"
    _render_to(out)
    print(f"[OUT] main valley=0% (fades to 0 at valleys, NOT 100%): {out}")

    # --- 4) 多重線が雲フキダシの小山にも反映される (小山の谷で太く山で細い) ---
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
        entry.line_width_mm = 0.8
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry.multi_line_count = 3
        entry.multi_line_direction = "outside"
        entry.multi_line_width_mm = 0.5
        entry.multi_line_spacing_mm = 1.0
        entry.thorn_multi_line_valley_width_pct = 100.0
        entry.thorn_multi_line_peak_width_pct = 0.0
        # 雲・もやもや・トゲ曲線では小山もある程度設定
        sp = entry.shape_params
        sp.cloud_sub_width_ratio = 60.0
        sp.cloud_sub_height_ratio = 60.0
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        objects.append((obj, shape))
    xs = [float(o.location.x) for o, _ in objects]
    ys = [float(o.location.y) for o, _ in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = (max(xs) - min(xs)) + 0.1
    _set_ortho_camera(cx, cy, scale)
    out = _OUT_PATH / "04_multiline_with_sub_bumps.png"
    _render_to(out)
    print(f"[OUT] multiline reflects sub bumps too: {out}")

    # --- 5) 長さ変化 60%: 多重線が谷から伸び、山側終点が谷に近づく (山頂が削れる) ---
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
        entry.line_width_mm = 0.8
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry.multi_line_count = 4
        entry.multi_line_direction = "outside"
        entry.multi_line_width_mm = 0.4
        entry.multi_line_spacing_mm = 1.0
        entry.thorn_multi_line_valley_width_pct = 100.0
        entry.thorn_multi_line_peak_width_pct = 100.0
        entry.thorn_multi_line_length_scale_percent = 60.0  # 山頂を削って 60% の長さ
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        objects.append((obj, shape))
    xs = [float(o.location.x) for o, _ in objects]
    ys = [float(o.location.y) for o, _ in objects]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    scale = (max(xs) - min(xs)) + 0.1
    _set_ortho_camera(cx, cy, scale)
    out = _OUT_PATH / "05_length_scale_60pct.png"
    _render_to(out)
    print(f"[OUT] length_scale 60% (extend from valley): {out}")

    print(f"[DONE] 出力: {_OUT_PATH}")


if __name__ == "__main__":
    main()
