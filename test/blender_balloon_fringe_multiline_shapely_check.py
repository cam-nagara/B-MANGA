"""Blender 実機用: 雲フキダシのフチ・多重線を Shapely buffer 方式へ移行した結果を、
線幅薄/太 × 各オプション (外側フチ・内側フチ・多重線 inside/outside/both・谷を尖らせる)
の比較画像として一括レンダリングする確認用スクリプト.

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-Name/test/blender_balloon_fringe_multiline_shapely_check.py"

出力先 (デフォルト): 一時ディレクトリ。 BNAME_FRINGE_SHAPELY_OUT 環境変数で固定可。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BNAME_FRINGE_SHAPELY_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bname_fringe_shapely_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_fringe_shapely_check",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_fringe_shapely_check"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_ortho_camera(center_x_m: float, center_y_m: float, scale_m: float) -> None:
    camera_data = bpy.data.cameras.new("確認カメラ")
    camera = bpy.data.objects.new("確認カメラ", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = (center_x_m, center_y_m, 2.0)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = scale_m
    bpy.context.scene.camera = camera


def _render_to(path: Path, *, width_px: int = 1800, height_px: int = 1200) -> None:
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


# ============================================================
# 個別ケース構築
# ============================================================

def _setup_entry_basics(entry, *, line_w_mm: float, valley_sharp: bool) -> None:
    entry.line_style = "solid"
    entry.line_width_mm = float(line_w_mm)
    entry.line_color = (0.05, 0.05, 0.05, 1.0)
    entry.fill_color = (1.0, 1.0, 1.0, 1.0)
    entry.fill_opacity = 100.0
    sp = entry.shape_params
    sp.cloud_valley_sharp = bool(valley_sharp)


def _enable_outer_edge(entry, *, width_mm: float, color=(1.0, 0.4, 0.4, 1.0)) -> None:
    entry.outer_white_margin_enabled = True
    entry.outer_white_margin_width_mm = float(width_mm)
    entry.outer_white_margin_color = color


def _enable_inner_edge(entry, *, width_mm: float, color=(0.4, 0.6, 1.0, 1.0)) -> None:
    entry.inner_white_margin_enabled = True
    entry.inner_white_margin_width_mm = float(width_mm)
    entry.inner_white_margin_color = color


def _enable_multi_line(entry, *, count: int, direction: str, width_mm: float, spacing_mm: float) -> None:
    entry.line_style = "double"
    entry.multi_line_count = int(count)
    entry.multi_line_direction = str(direction)
    entry.multi_line_width_mm = float(width_mm)
    entry.multi_line_spacing_mm = float(spacing_mm)
    entry.multi_line_width_scale_percent = 100.0


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_fringe_shapely_work_"))
    _OUT_PATH.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    result = bpy.ops.bname.work_new(filepath=str(temp_root / "FringeShapelyCheck.bname"))
    assert "FINISHED" in result, result

    from bname_dev_fringe_shapely_check.core.work import get_work
    from bname_dev_fringe_shapely_check.operators import balloon_op
    from bname_dev_fringe_shapely_check.utils import balloon_curve_object
    from bname_dev_fringe_shapely_check.utils.layer_hierarchy import page_stack_key

    context = bpy.context
    work = get_work(context)
    assert work is not None and work.loaded
    page = work.pages[0]
    parent_key = page_stack_key(page)

    width_mm = 48.0
    height_mm = 48.0

    # 横軸: 線幅 (thin/thick), 縦軸: 各オプション組合せ
    rows = [
        ("none", {}),
        ("outer", {"outer": (1.5,)}),
        ("inner", {"inner": (1.5,)}),
        ("outer+inner", {"outer": (1.5,), "inner": (1.5,)}),
        ("multi_outside_3", {"multi": ("outside", 3, 0.4, 0.5)}),
        ("multi_inside_3", {"multi": ("inside", 3, 0.4, 0.5)}),
        ("multi_both_3", {"multi": ("both", 3, 0.4, 0.5)}),
        ("valley_sharp_outer", {"outer": (1.5,), "valley_sharp": True}),
    ]
    line_widths = [0.5, 5.0]

    # グリッド配置
    spacing_x = 60.0
    spacing_y = 60.0
    base_x_mm = 30.0
    base_y_mm = 30.0

    objects: list[tuple[bpy.types.Object, str]] = []
    for row_idx, (row_name, opts) in enumerate(rows):
        for col_idx, line_w in enumerate(line_widths):
            x_mm = base_x_mm + col_idx * spacing_x
            y_mm = base_y_mm + row_idx * spacing_y
            entry = balloon_op._create_balloon_entry(
                context,
                page,
                shape="cloud",
                x=x_mm,
                y=y_mm,
                w=width_mm,
                h=height_mm,
                parent_kind="page",
                parent_key=parent_key,
            )
            _setup_entry_basics(entry, line_w_mm=line_w, valley_sharp=bool(opts.get("valley_sharp", False)))
            if "outer" in opts:
                _enable_outer_edge(entry, width_mm=opts["outer"][0])
            if "inner" in opts:
                _enable_inner_edge(entry, width_mm=opts["inner"][0])
            if "multi" in opts:
                direction, count, width, spacing = opts["multi"]
                _enable_multi_line(
                    entry,
                    count=int(count),
                    direction=str(direction),
                    width_mm=float(width),
                    spacing_mm=float(spacing),
                )
            obj = balloon_curve_object.ensure_balloon_curve_object(
                scene=context.scene, entry=entry, page=page,
            )
            assert obj is not None and obj.type == "CURVE"
            objects.append((obj, f"{row_name}_lw{line_w}"))

    # フレーミング
    xs = [float(obj.location.x) for obj, _ in objects]
    ys = [float(obj.location.y) for obj, _ in objects]
    half_w = float(width_mm) * 0.0005 * 1.4
    half_h = float(height_mm) * 0.0005 * 1.4
    min_x = min(xs) - half_w
    max_x = max(xs) + half_w
    min_y = min(ys) - half_h
    max_y = max(ys) + half_h
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5
    scale = max(max_x - min_x, max_y - min_y) + 0.02
    _set_ortho_camera(center_x, center_y, scale)

    out_path = _OUT_PATH / "fringe_multiline_shapely_grid.png"
    _render_to(out_path, width_px=1400, height_px=1600)
    print(f"[OUT] grid: {out_path}")

    # band mesh が実際に作られているかオブジェクト名でチェック
    print("[DUMP] band mesh objects:")
    line_mesh_count = 0
    outer_mesh_count = 0
    inner_mesh_count = 0
    multi_mesh_count = 0
    for obj in bpy.data.objects:
        name = obj.name
        if name.startswith("balloon_line_mesh_"):
            line_mesh_count += 1
        elif name.startswith("balloon_outer_edge_mesh_"):
            outer_mesh_count += 1
        elif name.startswith("balloon_inner_edge_mesh_"):
            inner_mesh_count += 1
        elif name.startswith("balloon_multi_line_mesh_"):
            multi_mesh_count += 1
    print(f"  line:        {line_mesh_count}")
    print(f"  outer_edge:  {outer_mesh_count}")
    print(f"  inner_edge:  {inner_mesh_count}")
    print(f"  multi_line:  {multi_mesh_count}")
    print(f"[DONE] 出力ディレクトリ: {_OUT_PATH}")


if __name__ == "__main__":
    main()
