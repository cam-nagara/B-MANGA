"""Blender 実機用: 雲フキダシのフチ・多重線を太い線幅で個別レンダリングして
自己交差なしを確認する近接ビュー版.

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_fringe_multiline_closeup_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_FRINGE_CLOSEUP_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_fringe_closeup_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_fringe_closeup",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_fringe_closeup"] = mod
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


def _delete_all_balloons(work, page) -> None:
    while len(page.balloons) > 0:
        page.balloons.remove(0)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_fringe_closeup_work_"))
    _OUT_PATH.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "FringeCloseupCheck.bmanga"))
    assert "FINISHED" in result, result

    from bmanga_dev_fringe_closeup.core.work import get_work
    from bmanga_dev_fringe_closeup.operators import balloon_op
    from bmanga_dev_fringe_closeup.utils import balloon_curve_object
    from bmanga_dev_fringe_closeup.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = get_work(context)
    assert work is not None and work.loaded
    page = work.pages[0]
    parent_key = page_stack_key(page)

    width_mm = 50.0
    height_mm = 50.0

    cases = [
        ("none_lw5",        {"line_w": 5.0}),
        ("outer_lw5",       {"line_w": 5.0, "outer": 2.0}),
        ("inner_lw5",       {"line_w": 5.0, "inner": 2.0}),
        ("outer+inner_lw5", {"line_w": 5.0, "outer": 2.0, "inner": 2.0}),
        ("multi_out3_lw5",  {"line_w": 5.0, "multi": ("outside", 3, 0.6, 1.0)}),
        ("multi_in3_lw5",   {"line_w": 5.0, "multi": ("inside", 3, 0.6, 1.0)}),
        ("multi_both3_lw5", {"line_w": 5.0, "multi": ("both", 3, 0.6, 1.0)}),
        ("valley_sharp_lw5", {"line_w": 5.0, "outer": 2.0, "valley_sharp": True}),
    ]

    out_paths = []
    for case_name, opts in cases:
        _delete_all_balloons(work, page)
        # cleanup orphans so the new balloon isn't surrounded by stale mesh
        balloon_curve_object.cleanup_orphan_balloon_objects(context.scene)
        entry = balloon_op._create_balloon_entry(
            context,
            page,
            shape="cloud",
            x=20.0,
            y=20.0,
            w=width_mm,
            h=height_mm,
            parent_kind="page",
            parent_key=parent_key,
        )
        entry.line_style = "solid"
        entry.line_width_mm = float(opts["line_w"])
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry.fill_opacity = 100.0
        sp = entry.shape_params
        sp.cloud_valley_sharp = bool(opts.get("valley_sharp", False))
        if "outer" in opts:
            entry.outer_white_margin_enabled = True
            entry.outer_white_margin_width_mm = float(opts["outer"])
            entry.outer_white_margin_color = (1.0, 0.2, 0.2, 1.0)
        if "inner" in opts:
            entry.inner_white_margin_enabled = True
            entry.inner_white_margin_width_mm = float(opts["inner"])
            entry.inner_white_margin_color = (0.2, 0.4, 1.0, 1.0)
        if "multi" in opts:
            direction, count, width, spacing = opts["multi"]
            entry.line_style = "double"
            entry.multi_line_count = int(count)
            entry.multi_line_direction = str(direction)
            entry.multi_line_width_mm = float(width)
            entry.multi_line_spacing_mm = float(spacing)
            entry.multi_line_width_scale_percent = 100.0
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        assert obj is not None and obj.type == "CURVE"

        # Tight camera on this single balloon
        cx = float(obj.location.x)
        cy = float(obj.location.y)
        _set_ortho_camera(cx, cy, 0.075)  # 75mm view = balloon + a bit of margin

        out = _OUT_PATH / f"closeup_{case_name}.png"
        _render_to(out, width_px=900, height_px=900)
        out_paths.append((case_name, out))
        print(f"[OUT] {case_name}: {out}")
        # 各メッシュバンドオブジェクトの modifier 構成を dump
        # (画像マスク方式に統一したため、メッシュくり抜き GN modifier は付いていてはいけない)
        for o in bpy.data.objects:
            if not (
                o.name.startswith("balloon_line_mesh_")
                or o.name.startswith("balloon_outer_edge_mesh_")
                or o.name.startswith("balloon_inner_edge_mesh_")
                or o.name.startswith("balloon_multi_line_mesh_")
            ):
                continue
            mods = [m.name for m in getattr(o, "modifiers", []) or []]
            print(f"  [MOD] {o.name}: {mods}")

    print(f"[DONE] 出力ディレクトリ: {_OUT_PATH}")


if __name__ == "__main__":
    main()
