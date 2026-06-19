"""主線「谷/山の線幅」が外側フチ・内側フチにも反映されることを確認する.

ユーザー報告: 主線の山/谷の線幅を下げると、 角の尖りが フチに覆われて消える。
修正後は フチも 主線と同じ % 係数で 谷/山で消えるはず。

ケース:
  - peak_100_valley_100: 通常 (フチも均一幅)
  - peak_100_valley_0  : 谷で 0 → フチも谷で 0 → 谷の角は元のフキダシ尖り維持
  - peak_0_valley_100  : 山頂で 0 → フチも山頂で 0 → 山の尖り維持
  - peak_0_valley_0    : フチも撤去

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_edge_dynamic_width_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_EDGE_DYN_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_edge_dyn_"))
_SCENARIO = os.environ.get("BMANGA_SCENARIO", "peak_100_valley_100")


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_edge_dyn",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_edge_dyn"] = mod
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_ortho_camera(cx_m: float, cy_m: float, scale_m: float):
    for old in list(bpy.data.objects):
        if old.type == "CAMERA":
            bpy.data.objects.remove(old, do_unlink=True)
    cam_data = bpy.data.cameras.new("確認カメラ")
    cam = bpy.data.objects.new("確認カメラ", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = (cx_m, cy_m, 2.0)
    cam.rotation_euler = (0.0, 0.0, 0.0)
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = scale_m
    bpy.context.scene.camera = cam


def _render(path: Path):
    scene = bpy.context.scene
    engine_items = {item.identifier for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engine_items else "BLENDER_EEVEE"
    scene.render.resolution_x = 900
    scene.render.resolution_y = 900
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(path)
    scene.render.film_transparent = False
    bpy.ops.render.render(write_still=True)


CASES = {
    "peak_100_valley_100": (100.0, 100.0),
    "peak_100_valley_0": (100.0, 0.0),
    "peak_0_valley_100": (0.0, 100.0),
    "peak_0_valley_0": (0.0, 0.0),
}


def main():
    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()

    peak_pct, valley_pct = CASES[_SCENARIO]

    from bmanga_dev_edge_dyn.core.work import get_work
    from bmanga_dev_edge_dyn.utils import balloon_curve_object, coma_plane, coma_border_object
    from bmanga_dev_edge_dyn.utils.layer_hierarchy import coma_stack_key
    from bmanga_dev_edge_dyn.utils import page_grid

    tmp = Path(tempfile.mkdtemp(prefix=f"bmanga_edge_dyn_{_SCENARIO}_"))
    res = bpy.ops.bmanga.work_new(filepath=str(tmp / f"{_SCENARIO}.bmanga"))
    assert "FINISHED" in res, res

    scene = bpy.context.scene
    work = get_work(bpy.context)
    page = work.pages[0]
    coma = page.comas[0]
    coma.shape_type = "rect"
    coma.rect_x_mm = 10.0
    coma.rect_y_mm = 10.0
    coma.rect_width_mm = 140.0
    coma.rect_height_mm = 140.0
    coma.background_color = (0.85, 0.85, 0.85, 1.0)
    coma.border.visible = True
    coma.border.style = "solid"
    coma.border.width_mm = 0.5
    coma.border.color = (0.0, 0.0, 0.0, 1.0)

    parent_key = coma_stack_key(page, coma)
    coma_plane.ensure_coma_plane(scene, work, page, coma)
    coma_plane.ensure_coma_mask(scene, work, page, coma)
    coma_border_object.ensure_coma_border_object(scene, work, page, coma)

    # トゲ (直線) フキダシ、 ユーザー報告 (test108.bmanga) と同じ設定
    entry = page.balloons.add()
    entry.id = f"balloon_{_SCENARIO}"
    entry.title = "尖り検証"
    entry.shape = "thorn"
    entry.x_mm = 30.0
    entry.y_mm = 30.0
    entry.width_mm = 51.67
    entry.height_mm = 63.61
    entry.parent_kind = "coma"
    entry.parent_key = parent_key
    entry.line_style = "solid"
    entry.line_width_mm = 2.40
    entry.line_color = (0.0, 0.0, 0.0, 1.0)
    entry.fill_color = (1.0, 1.0, 1.0, 1.0)
    entry.fill_opacity = 100.0
    # 谷/山の線幅 (ユーザー値)
    entry.line_peak_width_pct = peak_pct
    entry.line_valley_width_pct = valley_pct
    # 外側フチ (薄紫 = ユーザースクショと同じ)
    entry.outer_white_margin_enabled = True
    entry.outer_white_margin_width_mm = 2.20
    entry.outer_white_margin_color = (0.65, 0.55, 0.95, 1.0)
    # 内側フチ (ピンク = ユーザースクショと同じ)
    entry.inner_white_margin_enabled = True
    entry.inner_white_margin_width_mm = 1.78
    entry.inner_white_margin_color = (1.0, 0.45, 0.85, 1.0)
    # 形状パラメータ (ユーザースクショと同じ)
    sp = getattr(entry, "shape_params", None)
    if sp is not None:
        try:
            sp.cloud_bump_width_mm = 12.79
            sp.cloud_bump_height_mm = 15.61
            sp.cloud_offset_percent = 50.0
            sp.cloud_sub_width_ratio = 0.0
            sp.cloud_sub_height_ratio = 0.0
            sp.cloud_valley_sharp = True
        except Exception:
            pass

    obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
    assert obj is not None
    bpy.context.view_layer.update()

    page_off_x, page_off_y = page_grid.page_total_offset_mm(work, scene, 0)
    cx = (coma.rect_x_mm + coma.rect_width_mm * 0.5 + page_off_x) / 1000.0
    cy = (coma.rect_y_mm + coma.rect_height_mm * 0.5 + page_off_y) / 1000.0

    # フキダシ全体 (約 52x64mm) を画面に大きく映すため ortho_scale を絞る。
    # カメラ中心は フキダシ中心へ。
    fcx = (entry.x_mm + entry.width_mm * 0.5 + page_off_x) / 1000.0
    fcy = (entry.y_mm + entry.height_mm * 0.5 + page_off_y) / 1000.0
    _set_ortho_camera(fcx, fcy, 0.10)
    out_path = _OUT_PATH / f"{_SCENARIO}.png"
    _render(out_path)
    print(f"=== {_SCENARIO} peak={peak_pct} valley={valley_pct} ===")
    print(f"  [OUT] {out_path}")

    # メッシュの存在/不在を dump
    for kind in ("line", "outer_edge", "inner_edge"):
        mesh_name = f"balloon_{kind}_mesh_{entry.id}"
        mesh_obj = bpy.data.objects.get(mesh_name)
        if mesh_obj is None:
            print(f"  {kind}: MISSING")
        else:
            mesh = mesh_obj.data
            verts = len(mesh.vertices) if mesh else 0
            print(f"  {kind}: verts={verts}")


if __name__ == "__main__":
    main()
