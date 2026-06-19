"""Blender 実機用: 全形状で主線・外側フチ・内側フチが Shapely buffer + earcut で
描画されること、および多重線本数と spacing 修正を確認する.

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_all_shapes_shapely_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_ALL_SHAPES_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_all_shapes_shapely_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_all_shapes_shapely",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_all_shapes_shapely"] = mod
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


def _render_to(path: Path, *, width_px: int = 1400, height_px: int = 1400) -> None:
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
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_all_shapes_work_"))
    _OUT_PATH.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "AllShapesShapely.bmanga"))
    assert "FINISHED" in result, result

    from bmanga_dev_all_shapes_shapely.core.work import get_work
    from bmanga_dev_all_shapes_shapely.operators import balloon_op
    from bmanga_dev_all_shapes_shapely.utils import balloon_curve_object
    from bmanga_dev_all_shapes_shapely.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = get_work(context)
    assert work is not None and work.loaded
    page = work.pages[0]
    parent_key = page_stack_key(page)

    SHAPES = ["rect", "ellipse", "octagon", "cloud", "fluffy", "thorn", "thorn-curve"]
    width_mm = 50.0
    height_mm = 50.0

    # 1: 全形状 × 主線太め + 外側フチ + 内側フチ + 多重線 (count=3, outside)
    for case_name, configure in [
        ("with_fringes_and_multiline_count3", "fringes_multi"),
        ("multiline_only_count4_both", "multi_only"),
    ]:
        _delete_all_balloons(page)
        balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
        objects = []
        for idx, shape in enumerate(SHAPES):
            entry = balloon_op._create_balloon_entry(
                context, page,
                shape=shape,
                x=20.0 + idx * 60.0,
                y=20.0,
                w=width_mm,
                h=height_mm,
                parent_kind="page",
                parent_key=parent_key,
            )
            entry.line_style = "solid"
            entry.line_width_mm = 1.5
            entry.line_color = (0.05, 0.05, 0.05, 1.0)
            entry.fill_color = (1.0, 1.0, 1.0, 1.0)
            entry.fill_opacity = 100.0

            if configure == "fringes_multi":
                entry.outer_white_margin_enabled = True
                entry.outer_white_margin_width_mm = 1.0
                entry.outer_white_margin_color = (1.0, 0.3, 0.3, 1.0)
                entry.inner_white_margin_enabled = True
                entry.inner_white_margin_width_mm = 1.0
                entry.inner_white_margin_color = (0.3, 0.5, 1.0, 1.0)
                entry.line_style = "double"
                entry.multi_line_count = 3
                entry.multi_line_direction = "outside"
                entry.multi_line_width_mm = 0.4
                entry.multi_line_spacing_mm = 0.7
                entry.multi_line_width_scale_percent = 100.0
            elif configure == "multi_only":
                entry.line_style = "double"
                entry.multi_line_count = 4
                entry.multi_line_direction = "both"
                entry.multi_line_width_mm = 0.5
                entry.multi_line_spacing_mm = 1.0
                entry.multi_line_width_scale_percent = 100.0

            obj = balloon_curve_object.ensure_balloon_curve_object(
                scene=context.scene, entry=entry, page=page,
            )
            assert obj is not None and obj.type == "CURVE"
            objects.append((obj, shape))

        xs = [float(obj.location.x) for obj, _ in objects]
        ys = [float(obj.location.y) for obj, _ in objects]
        half = float(width_mm) * 0.0007
        min_x = min(xs) - half
        max_x = max(xs) + half
        min_y = min(ys) - half
        max_y = max(ys) + half
        center_x = (min_x + max_x) * 0.5
        center_y = (min_y + max_y) * 0.5
        scale = max(max_x - min_x, max_y - min_y) + 0.02
        _set_ortho_camera(center_x, center_y, scale)
        out = _OUT_PATH / f"all_shapes_{case_name}.png"
        _render_to(out, width_px=1800, height_px=600)
        print(f"[OUT] {case_name}: {out}")

    # 主線・フチ Mesh が全形状で作られているか確認
    print("[DUMP] mesh per shape:")
    for obj in bpy.data.objects:
        if not obj.name.startswith("balloon_line_mesh_"):
            continue
        owner_id = str(obj.get("bmanga_balloon_line_mesh_owner_id", "") or "")
        print(f"  line mesh: {obj.name}, owner={owner_id}")

    # 多重線本数の確認 (count=4 で 4 リング生成されるか)
    for obj in bpy.data.objects:
        if not obj.name.startswith("balloon_multi_line_mesh_"):
            continue
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        m = evaluated.to_mesh()
        try:
            v_count = len(m.vertices)
            p_count = len(m.polygons)
            print(f"  multi-line: {obj.name}, verts={v_count}, polys={p_count}")
        finally:
            evaluated.to_mesh_clear()

    print(f"[DONE] 出力ディレクトリ: {_OUT_PATH}")


if __name__ == "__main__":
    main()
