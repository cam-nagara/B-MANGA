"""ユーザー報告: トゲ(直線)・外側4本・線幅変化での多重線崩れ再現.

スクリーンショットの設定:
  - 形状: トゲ(直線), ベース: 楕円, 角を尖らせる=ON
  - 山の幅 22.87mm (乱れ0.27), 山の高さ 11.53mm (乱れ0.05)
  - ズラし量 50%, シード 27, 小山幅 31.17% (乱れ0.04), 小山高 0% (乱れ0.03)
  - 線: 多重線, 線幅 0.30mm, 主線 100%/100%
  - 線の本数 4, 重ね 外側
  - 多重線幅 0.75mm, 多重線間隔 0.30mm
  - 線幅変 74.47%, 間隔 100%
  - 長さ 100%, 長さ変 22.34%
  - 谷の線幅 100%, 山の線幅 0%
  - 外側フチ 1.00mm, 内側フチ 1.00mm
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_MLBREAK_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_mlbreak_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_mlbreak",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_mlbreak"] = mod
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


def _render_to(path, *, w=1500, h=1500):
    scene = bpy.context.scene
    items = {item.identifier for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in items else "BLENDER_EEVEE"
    scene.render.resolution_x = w
    scene.render.resolution_y = h
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(path)
    scene.render.film_transparent = False
    w = scene.world or bpy.data.worlds.new("w")
    scene.world = w
    w.use_nodes = True
    bg = w.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.75, 0.75, 0.78, 1.0)
        bg.inputs[1].default_value = 1.0
    bpy.ops.render.render(write_still=True)


def _reset_work():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_mlbreak_work_"))
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "MLBreak.bmanga"))  # type: ignore[attr-defined]
    assert "FINISHED" in result, result
    return bpy.context


def main() -> int:
    _OUT_PATH.mkdir(parents=True, exist_ok=True)

    context = _reset_work()
    from bmanga_dev_mlbreak.utils import balloon_curve_object as bco
    from bmanga_dev_mlbreak.utils.layer_hierarchy import page_stack_key
    from bmanga_dev_mlbreak.utils import page_grid

    scene = context.scene
    work = scene.bmanga_work
    page = work.pages[0]
    pk = page_stack_key(page)

    entry = page.balloons.add()
    entry.id = "thorn_break"
    entry.title = "thorn_break"
    entry.shape = "thorn"
    entry.x_mm = 50.0
    entry.y_mm = 60.0
    entry.width_mm = 90.74
    entry.height_mm = 101.38
    entry.parent_kind = "page"
    entry.parent_key = pk
    entry.line_style = "double"
    entry.line_width_mm = 0.30
    entry.line_color = (0.02, 0.02, 0.02, 1.0)
    entry.fill_color = (0.4, 1.0, 0.4, 1.0)
    entry.fill_opacity = 100.0
    entry.opacity = 100.0
    entry.line_valley_width_pct = 100.0
    entry.line_peak_width_pct = 100.0
    entry.multi_line_count = 4
    entry.multi_line_direction = "outside"
    entry.multi_line_width_mm = 0.75
    entry.multi_line_spacing_mm = 0.30
    entry.multi_line_width_scale_percent = 74.47
    entry.multi_line_spacing_scale_percent = 100.0
    entry.thorn_multi_line_length_scale_near_percent = 100.0
    entry.thorn_multi_line_length_scale_far_percent = 22.34
    entry.thorn_multi_line_valley_width_pct = 100.0
    entry.thorn_multi_line_peak_width_pct = 0.0
    entry.thorn_multi_line_cross_enabled = False
    entry.outer_white_margin_enabled = True
    entry.outer_white_margin_width_mm = 1.0
    entry.outer_white_margin_color = (0.7, 0.6, 1.0, 1.0)
    entry.inner_white_margin_enabled = True
    entry.inner_white_margin_width_mm = 1.0
    entry.inner_white_margin_color = (1.0, 0.6, 0.9, 1.0)
    sp = entry.shape_params
    sp.dynamic_shape_base_kind = "ellipse"
    sp.cloud_bump_width_mm = 22.87
    sp.cloud_bump_width_jitter = 0.27
    sp.cloud_bump_height_mm = 11.53
    sp.cloud_bump_height_jitter = 0.05
    sp.cloud_offset_percent = 50.0
    sp.shape_seed = 27
    sp.cloud_sub_width_ratio = 31.17
    sp.cloud_sub_width_jitter = 0.04
    sp.cloud_sub_height_ratio = 0.0
    sp.cloud_sub_height_jitter = 0.03
    sp.cloud_valley_sharp = True

    bco.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)

    # 多重線メッシュの統計を出力
    from bmanga_dev_mlbreak.utils import balloon_line_mesh as blm
    ml_name = blm._multi_line_mesh_object_name(entry.id)
    ml_obj = bpy.data.objects.get(ml_name)
    if ml_obj is not None and ml_obj.type == "MESH":
        me = ml_obj.data
        print(f"=== 多重線メッシュ: 頂点 {len(me.vertices)} / 面 {len(me.polygons)} ===")
    else:
        print("=== 多重線メッシュが見つかりません ===")

    # 多重線メッシュ頂点のワールド centroid から中心を取る
    import mathutils
    bpy.context.view_layer.update()
    if ml_obj is not None and ml_obj.type == "MESH" and len(ml_obj.data.vertices) > 0:
        mw = ml_obj.matrix_world
        ws = [mw @ v.co for v in ml_obj.data.vertices]
        xs = [w.x for w in ws]
        ys = [w.y for w in ws]
        cx = (min(xs) + max(xs)) * 0.5
        cy = (min(ys) + max(ys)) * 0.5
        span = max(max(xs) - min(xs), max(ys) - min(ys))
        scale = span * 1.15
        print(f"=== centroid=({cx:.4f},{cy:.4f}) span={span:.4f} ===")
    else:
        cx = entry.x_mm / 1000.0
        cy = entry.y_mm / 1000.0
        scale = 0.14
    _set_ortho_camera("cam", cx, cy, scale)
    _render_to(_OUT_PATH / "thorn_multiline_break.png")
    print(f"=== 出力: {_OUT_PATH / 'thorn_multiline_break.png'} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
