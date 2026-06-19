"""複雑な設定のフキダシ + 指定コマシナリオで画像マスク効果を検証する.

ユーザー報告: フキダシのコマ内画像マスクが効いていない。
複雑な設定のフキダシで、線無しや輪郭ぼかしのコマでテストを行い、
コマ範囲外がマスクで切られているか確認する。

シナリオは BMANGA_SCENARIO 環境変数で切替 (1 シナリオ = 1 プロセス).

走らせ方:
  BMANGA_SCENARIO=01_solid_thin & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_image_mask_complex_repro.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_COMPLEX_MASK_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_complex_mask_"))
_SCENARIO_NAME = os.environ.get("BMANGA_SCENARIO", "01_solid_thin")


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_complex_mask",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_complex_mask"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_ortho_camera(name: str, center_x_m: float, center_y_m: float, scale_m: float) -> None:
    for old in list(bpy.data.objects):
        if old.type == "CAMERA":
            bpy.data.objects.remove(old, do_unlink=True)
    camera_data = bpy.data.cameras.new(name)
    camera = bpy.data.objects.new(name, camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = (center_x_m, center_y_m, 2.0)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = scale_m
    bpy.context.scene.camera = camera


def _render_to(path: Path, *, width_px: int = 900, height_px: int = 900, low_sample: bool = False) -> None:
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
    # ビューポート レンダー表示と同等の低サンプリングを再現するためのオプション
    eevee = getattr(scene, "eevee", None)
    if eevee is not None and low_sample:
        try:
            eevee.taa_render_samples = 1
            eevee.taa_samples = 1
        except (AttributeError, TypeError):
            pass
    bpy.ops.render.render(write_still=True)


def _setup_complex_balloon(entry, parent_key: str) -> None:
    """全部入りの複雑なフキダシ。コマ右上端をしっぽも含めて大きくはみ出させる。"""
    entry.id = "balloon_complex"
    entry.title = "複雑なフキダシ"
    entry.shape = "cloud"
    # コマ rect_x=20, rect_y=30, w=80, h=80 → コマ右端x=100, 上端y=110
    entry.x_mm = 80.0
    entry.y_mm = 70.0
    entry.width_mm = 70.0
    entry.height_mm = 70.0
    entry.parent_kind = "coma"
    entry.parent_key = parent_key
    entry.line_style = "double"
    entry.line_width_mm = 2.5
    entry.line_color = (0.0, 0.0, 0.0, 1.0)
    entry.fill_color = (1.0, 1.0, 1.0, 1.0)
    entry.fill_opacity = 100.0
    entry.fill_gradient_enabled = True
    entry.fill_gradient_start_color = (1.0, 1.0, 1.0, 1.0)
    entry.fill_gradient_end_color = (1.0, 0.85, 0.85, 1.0)
    entry.fill_gradient_angle_deg = 60.0
    entry.fill_blur_amount = 0.4
    entry.opacity = 100.0
    entry.outer_white_margin_enabled = True
    entry.outer_white_margin_width_mm = 2.0
    entry.outer_white_margin_color = (1.0, 0.2, 0.2, 1.0)
    entry.inner_white_margin_enabled = True
    entry.inner_white_margin_width_mm = 1.5
    entry.inner_white_margin_color = (0.2, 0.4, 1.0, 1.0)
    entry.multi_line_count = 3
    entry.multi_line_direction = "outside"
    entry.multi_line_width_mm = 0.7
    entry.multi_line_spacing_mm = 1.2
    entry.multi_line_width_scale_percent = 100.0
    # しっぽ複数
    tail = entry.tails.add()
    tail.length_mm = 25.0
    tail.width_mm = 12.0
    tail.direction_deg = 315.0
    tail2 = entry.tails.add()
    tail2.length_mm = 18.0
    tail2.width_mm = 8.0
    tail2.direction_deg = 45.0


def case_solid_thin(coma):
    coma.border.visible = True
    coma.border.style = "solid"
    coma.border.width_mm = 0.5
    coma.border.color = (0.0, 0.0, 0.0, 1.0)
    coma.border.blur_amount = 0.0


def case_no_border(coma):
    """線無しコマ: border.visible=False。"""
    coma.border.visible = False
    coma.border.width_mm = 0.0


def case_blur_border(coma):
    """輪郭ぼかしコマ: brush style + blur_amount 大きめ。"""
    coma.border.visible = True
    coma.border.style = "brush"
    coma.border.width_mm = 12.0
    coma.border.blur_amount = 0.85
    coma.border.color = (0.0, 0.0, 0.0, 1.0)


def case_solid_blur(coma):
    """solid style + blur_amount 設定 (brush style ではない)。soft mask は適用されない仕様。"""
    coma.border.visible = True
    coma.border.style = "solid"
    coma.border.width_mm = 1.0
    coma.border.blur_amount = 0.8
    coma.border.color = (0.0, 0.0, 0.0, 1.0)


def case_rounded_corner_no_border(coma):
    """角丸 + 線無し。"""
    coma.border.visible = False
    coma.border.width_mm = 0.0
    coma.border.corner_type = "round"
    coma.border.corner_radius_mm = 10.0


CASES = {
    "01_solid_thin": case_solid_thin,
    "02_no_border": case_no_border,
    "03_blur_border": case_blur_border,
    "04_solid_blur_falsy": case_solid_blur,
    "05_round_corner_no_border": case_rounded_corner_no_border,
}


def main():
    _OUT_PATH.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()

    case_fn = CASES.get(_SCENARIO_NAME)
    assert case_fn is not None, f"unknown scenario: {_SCENARIO_NAME}"

    from bmanga_dev_complex_mask.core.work import get_work
    from bmanga_dev_complex_mask.utils import balloon_curve_object
    from bmanga_dev_complex_mask.utils import coma_plane, coma_border_object
    from bmanga_dev_complex_mask.utils.layer_hierarchy import coma_stack_key
    from bmanga_dev_complex_mask.utils import page_grid

    temp_root = Path(tempfile.mkdtemp(prefix=f"bmanga_complex_{_SCENARIO_NAME}_"))
    res = bpy.ops.bmanga.work_new(filepath=str(temp_root / f"{_SCENARIO_NAME}.bmanga"))
    assert "FINISHED" in res, res

    scene = bpy.context.scene
    work = get_work(bpy.context)
    assert work is not None and work.loaded
    page = work.pages[0]
    coma = page.comas[0]
    coma.shape_type = "rect"
    coma.rect_x_mm = 20.0
    coma.rect_y_mm = 30.0
    coma.rect_width_mm = 80.0
    coma.rect_height_mm = 80.0
    coma.background_color = (0.85, 0.85, 0.85, 1.0)
    case_fn(coma)

    parent_key = coma_stack_key(page, coma)
    coma_plane.ensure_coma_plane(scene, work, page, coma)
    coma_plane.ensure_coma_mask(scene, work, page, coma)
    coma_border_object.ensure_coma_border_object(scene, work, page, coma)

    entry = page.balloons.add()
    _setup_complex_balloon(entry, parent_key)

    obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
    assert obj is not None and obj.type == "CURVE"
    bpy.context.view_layer.update()

    expected_mesh_names = (
        f"balloon_line_mesh_{entry.id}",
        f"balloon_outer_edge_mesh_{entry.id}",
        f"balloon_inner_edge_mesh_{entry.id}",
        f"balloon_multi_line_mesh_{entry.id}",
        f"balloon_fill_mesh_{entry.id}",
    )

    print(f"=== {_SCENARIO_NAME} ===")
    print(f"  coma rect: x={coma.rect_x_mm} y={coma.rect_y_mm} w={coma.rect_width_mm} h={coma.rect_height_mm} mm")
    print(f"  border: visible={coma.border.visible} style={coma.border.style} width={coma.border.width_mm}mm blur={coma.border.blur_amount}")

    for mesh_name in expected_mesh_names:
        mesh_obj = bpy.data.objects.get(mesh_name)
        if mesh_obj is None:
            print(f"  [MISSING] {mesh_name}")
            continue
        mat = (mesh_obj.data.materials[0] if mesh_obj.data.materials else None) if mesh_obj.data else None
        has_mask = False
        mask_image = None
        if mat is not None and getattr(mat, "use_nodes", False):
            for n in mat.node_tree.nodes:
                if str(getattr(n, "label", "") or "") == "コマ内容マスク":
                    has_mask = True
                    mask_image = getattr(n, "image", None)
                    break
        print(f"  {'masked' if has_mask else 'UNMASKED':>10}  {mesh_name}  image={getattr(mask_image, 'name', None)}")

    # Coma の関連オブジェクト一覧
    print("  関連オブジェクト:")
    for obj_x in list(bpy.data.objects):
        if obj_x.name.startswith("balloon_") or obj_x.name.startswith("コマ") or "paper" in obj_x.name.lower() or "border" in obj_x.name.lower() or "mask" in obj_x.name.lower() or "coma" in obj_x.name.lower():
            print(f"    {obj_x.name}  type={obj_x.type}  hide_viewport={obj_x.hide_viewport}  hide_render={obj_x.hide_render}  loc=({obj_x.location.x:.4f}, {obj_x.location.y:.4f}, {obj_x.location.z:.4f}) scale=({obj_x.scale.x:.4f}, {obj_x.scale.y:.4f}, {obj_x.scale.z:.4f})")

    page_off_x_mm, page_off_y_mm = page_grid.page_total_offset_mm(work, scene, 0)
    coma_center_m = (
        (coma.rect_x_mm + coma.rect_width_mm * 0.5 + page_off_x_mm) / 1000.0,
        (coma.rect_y_mm + coma.rect_height_mm * 0.5 + page_off_y_mm) / 1000.0,
    )

    _set_ortho_camera(f"確認カメラ_{_SCENARIO_NAME}", coma_center_m[0], coma_center_m[1], 0.16)

    # F12 相当 (高サンプリング)
    out_path = _OUT_PATH / f"{_SCENARIO_NAME}_f12.png"
    _render_to(out_path)
    print(f"  [OUT F12] {out_path}")

    # ビューポート レンダー表示相当 (sample=1)
    out_low = _OUT_PATH / f"{_SCENARIO_NAME}_viewport.png"
    _render_to(out_low, low_sample=True)
    print(f"  [OUT viewport] {out_low}")


if __name__ == "__main__":
    main()
