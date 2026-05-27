"""ユーザー報告の多重線崩れを再現するテスト.

スクリーンショットから読み取れる設定:
  - 雲 (cloud), 角を尖らせる=True
  - 線幅 1.77mm, 多重線
  - 線の本数=3, 重ね方向=両方向
  - 多重線幅=0.30mm, 間隔=0.40mm
  - 線幅変化=106.38%, 間隔変化=32.98%
  - 谷 100% / 山 100% (主線)
  - 外側フチ 3.55mm, 内側フチ 1.63mm

また、トゲ形状で同様の設定だと本体スパイクが多重線リングから飛び出す。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BNAME_BREAK_REPRO_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bname_break_repro_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_break_repro",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_break_repro"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_ortho_camera(name, cx, cy, scale):
    if name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
    if name in bpy.data.cameras:
        bpy.data.cameras.remove(bpy.data.cameras[name])
    cam_data = bpy.data.cameras.new(name)
    cam = bpy.data.objects.new(name, cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = (cx, cy, 2.0)
    cam.rotation_euler = (0.0, 0.0, 0.0)
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = scale
    bpy.context.scene.camera = cam


def _render_to(path, *, w=1024, h=1024):
    scene = bpy.context.scene
    items = {item.identifier for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in items else "BLENDER_EEVEE"
    scene.render.resolution_x = w
    scene.render.resolution_y = h
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(path)
    scene.render.film_transparent = False
    bpy.ops.render.render(write_still=True)


def _reset_work():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bname_break_work_"))
    result = bpy.ops.bname.work_new(filepath=str(temp_root / "BreakRepro.bname"))  # type: ignore[attr-defined]
    assert "FINISHED" in result, result
    return bpy.context


def main() -> int:
    _OUT_PATH.mkdir(parents=True, exist_ok=True)

    context = _reset_work()
    from bname_dev_break_repro.utils import balloon_curve_object as bco
    from bname_dev_break_repro.utils.layer_hierarchy import page_stack_key
    from bname_dev_break_repro.utils import page_grid

    scene = context.scene
    work = scene.bname_work
    page = work.pages[0]
    pk = page_stack_key(page)

    # ユーザー報告の cloud 設定で再現
    def add_cloud(x, label_id, sharp_valley=True):
        entry = page.balloons.add()
        entry.id = label_id
        entry.title = label_id
        entry.shape = "cloud"
        entry.x_mm = x
        entry.y_mm = 50.0
        entry.width_mm = 37.77
        entry.height_mm = 45.15
        entry.parent_kind = "page"
        entry.parent_key = pk
        entry.line_style = "double"
        entry.line_width_mm = 1.77
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (0.6, 1.0, 0.6, 1.0)  # 緑系
        entry.fill_opacity = 100.0
        entry.opacity = 100.0
        entry.multi_line_count = 3
        entry.multi_line_direction = "both"
        entry.multi_line_width_mm = 0.30
        entry.multi_line_spacing_mm = 0.40
        entry.multi_line_width_scale_percent = 106.38
        entry.multi_line_spacing_scale_percent = 32.98
        # スクリーンショットの長さ変化値
        entry.thorn_multi_line_length_scale_near_percent = 106.38
        entry.thorn_multi_line_length_scale_far_percent = 32.98
        entry.outer_white_margin_enabled = True
        entry.outer_white_margin_width_mm = 3.55
        entry.outer_white_margin_color = (0.7, 0.4, 1.0, 1.0)
        entry.inner_white_margin_enabled = True
        entry.inner_white_margin_width_mm = 1.63
        entry.inner_white_margin_color = (1.0, 0.7, 0.9, 1.0)
        sp = entry.shape_params
        sp.cloud_bump_width_mm = 10.0
        sp.cloud_bump_width_jitter = 0.38
        sp.cloud_bump_height_mm = 7.33
        sp.cloud_bump_height_jitter = 0.32
        sp.cloud_offset_percent = 54.17
        sp.shape_seed = 119
        sp.cloud_sub_width_ratio = 54.10
        sp.cloud_sub_width_jitter = 0.38
        sp.cloud_sub_height_ratio = 53.31
        sp.cloud_sub_height_jitter = 0.13
        sp.cloud_valley_sharp = sharp_valley
        return entry

    # 同様にトゲ
    def add_thorn(x, label_id):
        entry = page.balloons.add()
        entry.id = label_id
        entry.title = label_id
        entry.shape = "thorn"
        entry.x_mm = x
        entry.y_mm = 110.0
        entry.width_mm = 50.0
        entry.height_mm = 50.0
        entry.parent_kind = "page"
        entry.parent_key = pk
        entry.line_style = "double"
        entry.line_width_mm = 1.77
        entry.line_color = (0.05, 0.05, 0.05, 1.0)
        entry.fill_color = (0.2, 0.6, 0.2, 1.0)  # 緑系
        entry.fill_opacity = 100.0
        entry.opacity = 100.0
        entry.multi_line_count = 3
        entry.multi_line_direction = "both"
        entry.multi_line_width_mm = 0.30
        entry.multi_line_spacing_mm = 0.40
        entry.multi_line_width_scale_percent = 106.38
        entry.multi_line_spacing_scale_percent = 32.98
        entry.thorn_multi_line_length_scale_near_percent = 106.38
        entry.thorn_multi_line_length_scale_far_percent = 32.98
        entry.outer_white_margin_enabled = True
        entry.outer_white_margin_width_mm = 3.55
        entry.outer_white_margin_color = (0.7, 0.4, 1.0, 1.0)
        entry.inner_white_margin_enabled = True
        entry.inner_white_margin_width_mm = 1.63
        entry.inner_white_margin_color = (1.0, 0.7, 0.9, 1.0)
        return entry

    add_cloud(40.0, "c_sharp_true", sharp_valley=True)
    add_cloud(110.0, "c_sharp_false", sharp_valley=False)
    add_thorn(180.0, "t_user_cfg")

    for entry in page.balloons:
        bco.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)

    ox_m, oy_m = page_grid.page_total_offset_mm(work, scene, 0)
    ox_m = ox_m / 1000.0; oy_m = oy_m / 1000.0
    cx = (40 + 180 + 50) * 0.5 / 1000.0 + ox_m
    cy = (50 + 110 + 50) * 0.5 / 1000.0 + oy_m
    _set_ortho_camera("cam", cx, cy, 0.30)
    _render_to(_OUT_PATH / "break_repro.png", w=1500, h=1500)
    print(f"=== 出力: {_OUT_PATH / 'break_repro.png'} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
