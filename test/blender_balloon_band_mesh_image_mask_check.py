"""Blender 実機用: 雲フキダシの主線・フチ・多重線 Mesh が画像マスク方式のみで
コマ内に収まることを確認する.

メッシュくり抜き modifier は撤去済みであるため、material のアルファ画像マスクだけで
コマ外の部分が透過されるはず。

走らせ方:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --python ^
    "d:/Develop/Blender/B-MANGA/test/blender_balloon_band_mesh_image_mask_check.py"
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_BAND_MASK_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_band_image_mask_"))


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_band_image_mask",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_band_image_mask"] = mod
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


def _render_to(path: Path, *, width_px: int = 900, height_px: int = 900) -> None:
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


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_band_image_mask_work_"))
    _OUT_PATH.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "BandImageMask.bmanga"))
    assert "FINISHED" in result, result

    from bmanga_dev_band_image_mask.core.work import get_work
    from bmanga_dev_band_image_mask.utils import balloon_curve_object
    from bmanga_dev_band_image_mask.utils import coma_plane
    from bmanga_dev_band_image_mask.utils.layer_hierarchy import coma_stack_key

    context = bpy.context
    scene = context.scene
    work = get_work(context)
    assert work is not None and work.loaded
    page = work.pages[0]
    coma = page.comas[0]
    # コマを小さめに設定して、フキダシが右側にはみ出すように配置する
    coma.shape_type = "rect"
    coma.rect_x_mm = 20.0
    coma.rect_y_mm = 30.0
    coma.rect_width_mm = 80.0
    coma.rect_height_mm = 80.0
    coma.background_color = (0.85, 0.85, 0.85, 1.0)
    parent_key = coma_stack_key(page, coma)
    coma_plane.ensure_coma_plane(scene, work, page, coma)
    coma_plane.ensure_coma_mask(scene, work, page, coma)

    # コマ右上端に重ねるように、コマ外までかなりはみ出す雲フキダシを作る
    entry = page.balloons.add()
    entry.id = "balloon_overflow"
    entry.title = "はみ出し検証"
    entry.shape = "cloud"
    entry.x_mm = 75.0  # コマ右端 (100mm) に被るように左 75mm から
    entry.y_mm = 65.0  # コマ上端 (110mm) に被るように下 65mm から
    entry.width_mm = 60.0  # 75..135mm 横 (コマ右端 100 を超過)
    entry.height_mm = 60.0  # 65..125mm 縦 (コマ上端 110 を超過)
    entry.parent_kind = "coma"
    entry.parent_key = parent_key
    entry.line_style = "double"
    entry.line_width_mm = 3.0
    entry.line_color = (0.05, 0.05, 0.05, 1.0)
    entry.fill_color = (1.0, 1.0, 1.0, 1.0)
    entry.fill_opacity = 100.0
    entry.opacity = 100.0
    entry.outer_white_margin_enabled = True
    entry.outer_white_margin_width_mm = 1.5
    entry.outer_white_margin_color = (1.0, 0.3, 0.3, 1.0)
    entry.inner_white_margin_enabled = True
    entry.inner_white_margin_width_mm = 1.5
    entry.inner_white_margin_color = (0.3, 0.5, 1.0, 1.0)
    entry.multi_line_count = 3
    entry.multi_line_direction = "outside"
    entry.multi_line_width_mm = 0.5
    entry.multi_line_spacing_mm = 0.8
    entry.multi_line_width_scale_percent = 100.0

    obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
    assert obj is not None and obj.type == "CURVE"
    bpy.context.view_layer.update()

    # band mesh の modifier 一覧を dump (画像マスク方式なので空であるべき)
    expected_mesh_names = (
        f"balloon_line_mesh_{entry.id}",
        f"balloon_outer_edge_mesh_{entry.id}",
        f"balloon_inner_edge_mesh_{entry.id}",
        f"balloon_multi_line_mesh_{entry.id}",
    )
    for name in expected_mesh_names:
        mesh_obj = bpy.data.objects.get(name)
        assert mesh_obj is not None, f"Mesh が作られていません: {name}"
        mods = list(getattr(mesh_obj, "modifiers", []) or [])
        assert not mods, f"{name} に modifier が残っています: {[m.name for m in mods]}"
        # material の image_mask 接続も確認
        mesh_data = getattr(mesh_obj, "data", None)
        assert mesh_data is not None, f"{name} に mesh data がありません"
        mats = list(getattr(mesh_data, "materials", []) or [])
        assert mats, f"{name} に material がありません"
        mat = mats[0]
        assert getattr(mat, "use_nodes", False), f"{name} の material にノードがありません"
        mask_node_found = False
        for node in mat.node_tree.nodes:
            if str(getattr(node, "label", "") or "") == "コマ内容マスク":
                mask_node_found = True
                break
        assert mask_node_found, f"{name} の material にコマ内容マスクが接続されていません"
        print(f"  [OK] {name}: modifiers={[]} image-mask=接続済")

    # ページの world offset を取得し、コマ + フキダシ全体が収まる範囲でレンダリング
    from bmanga_dev_band_image_mask.utils import page_grid
    page_off_x_mm, page_off_y_mm = page_grid.page_total_offset_mm(work, scene, 0)
    print(f"[DUMP] page offset: ({page_off_x_mm}, {page_off_y_mm}) mm")

    coma_center_m = (
        (coma.rect_x_mm + coma.rect_width_mm * 0.5 + page_off_x_mm) / 1000.0,
        (coma.rect_y_mm + coma.rect_height_mm * 0.5 + page_off_y_mm) / 1000.0,
    )
    print(f"[DUMP] coma center (world m): {coma_center_m}")

    # 各 band mesh の評価後の world bbox を dump
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for name in expected_mesh_names:
        mesh_obj = bpy.data.objects.get(name)
        evaluated = mesh_obj.evaluated_get(depsgraph)
        m = evaluated.to_mesh()
        try:
            if not m.vertices:
                print(f"[DUMP] {name}: no verts")
                continue
            coords = [mesh_obj.matrix_world @ v.co for v in m.vertices]
            print(
                f"[DUMP] {name}: x=[{min(c.x for c in coords):.4f}, {max(c.x for c in coords):.4f}], "
                f"y=[{min(c.y for c in coords):.4f}, {max(c.y for c in coords):.4f}], "
                f"verts={len(coords)}"
            )
        finally:
            evaluated.to_mesh_clear()

    _set_ortho_camera(coma_center_m[0], coma_center_m[1], 0.16)
    out_path = _OUT_PATH / "band_image_mask_overflow.png"
    _render_to(out_path)
    print(f"[OUT] render: {out_path}")
    print(f"[DONE] 出力ディレクトリ: {_OUT_PATH}")


if __name__ == "__main__":
    main()
