"""Blender 実機(背景)用: ボカシブラシを内側アルファテクスチャで生成する確認."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_border_texture", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_border_texture"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _alpha_values(image):
    pixels = list(image.pixels[:])
    return pixels[3::4]


def _rgba_at(image, x: int, y: int) -> tuple[float, float, float, float]:
    pixels = list(image.pixels[:])
    width = int(image.size[0])
    offset = (y * width + x) * 4
    return tuple(float(v) for v in pixels[offset:offset + 4])


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    from bname_dev_border_texture.io import export_pipeline
    from bname_dev_border_texture.utils import coma_border_object, coma_border_texture, coma_plane
    from bname_dev_border_texture.utils.geom import mm_to_m

    scene = bpy.context.scene
    work = scene.bname_work
    work.loaded = True
    page = work.pages.add()
    page.id = "p0001"
    page.title = "1ページ"
    coma = page.comas.add()
    coma.id = "c01"
    coma.coma_id = "c01"
    coma.title = "コマ1"
    coma.rect_width_mm = 80.0
    coma.rect_height_mm = 60.0
    coma.border.style = "brush"
    coma.border.width_mm = 1.2
    coma.border.blur_amount = 0.8
    coma.border.color = (0.0, 0.0, 0.0, 1.0)
    assert not hasattr(coma, "edge_styles"), "コマに辺別線設定が残っています"
    assert not hasattr(coma.border, "edge_top"), "枠線に辺別設定が残っています"
    assert not hasattr(coma.white_margin, "edge_top"), "白フチに辺別設定が残っています"

    coma.border.blur_dither = False
    obj = coma_border_object.ensure_coma_border_object(scene, work, page, coma)
    plane = coma_plane.find_coma_plane_object(page.id, coma.id)
    assert obj is plane and plane is not None, "ボカシブラシがコマ面に適用されていません"
    assert bpy.data.objects.get(coma_border_texture.object_name(page.id, coma.id)) is None, (
        "別体のボカシ枠線オブジェクトが残っています"
    )
    image = bpy.data.images.get(coma_border_texture.plane_alpha_image_name(page.id, coma.id))
    assert image is not None, "コマ面の透明マスク画像が生成されません"
    signature_before = image.get("bname_border_alpha_signature")
    alpha = _alpha_values(image)
    assert max(alpha) > 0.9, "輪郭側のアルファが十分に濃くありません"
    assert min(alpha) < 0.05, "輪郭側の透明部分がありません"
    assert any(0.05 < value < 0.95 for value in alpha), "ボカシの中間アルファがありません"
    center_rgba = _rgba_at(image, int(image.size[0]) // 2, int(image.size[1]) // 2)
    assert center_rgba[3] > 0.95, "コマ内側が不透明になっていません"
    edge_rgba = _rgba_at(image, 0, int(image.size[1]) // 2)
    assert edge_rgba[3] < 0.05, "コマ輪郭が透明になっていません"
    corrupt = [0.0] * (int(image.size[0]) * int(image.size[1]) * 4)
    image.pixels.foreach_set(corrupt)
    image.update()
    image["bname_border_alpha_signature"] = signature_before
    obj_corrupt = coma_border_object.ensure_coma_border_object(scene, work, page, coma)
    image_corrupt = bpy.data.images.get(coma_border_texture.plane_alpha_image_name(page.id, coma.id))
    assert obj_corrupt is obj and image_corrupt is image, "壊れた透明マスク画像の再生成で別オブジェクト化しています"
    center_rgba = _rgba_at(image_corrupt, int(image_corrupt.size[0]) // 2, int(image_corrupt.size[1]) // 2)
    assert center_rgba[0] > 0.95 and center_rgba[1] > 0.95 and center_rgba[2] > 0.95, (
        "壊れた透明マスク画像の色チャンネルが再生成されていません"
    )
    assert center_rgba[3] > 0.95, "壊れた透明マスク画像のアルファが再生成されていません"
    mat = plane.data.materials[0]
    assert getattr(mat, "blend_method", "") != "BLEND", "コマ面素材が半透明ブレンド表示になっています"
    assert not bool(getattr(mat, "show_transparent_back", True)), "コマ面素材の裏面透明表示が残っています"
    assert not any(
        node.bl_idname == "ShaderNodeBsdfTransparent"
        for node in mat.node_tree.nodes
    ), "コマ面素材に透明シェーダーが残っています"
    assert any(
        node.name == "BName_ComaAlphaMask" and node.image is image
        for node in mat.node_tree.nodes
    ), "コマ面素材に透明マスク画像が接続されていません"
    preview_probe = bpy.data.images.new("BName_TestPreviewAlphaProbe", width=2, height=2, alpha=True)
    preview_probe.pixels.foreach_set([
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.5,
        0.0, 0.0, 1.0, 1.0,
        1.0, 1.0, 1.0, 1.0,
    ])
    preview_probe.update()
    probe_mat = bpy.data.materials.new("BName_TestMaskedComaMaterial")
    probe_mat.use_nodes = True
    coma_plane._apply_material(  # noqa: SLF001 - Blender実機で材質ノード構成を確認する
        probe_mat,
        (1.0, 1.0, 1.0, 1.0),
        preview_probe,
        keep_existing_image=False,
        alpha_mask_image=image,
        keep_existing_mask=False,
        dither=True,
    )
    assert getattr(probe_mat, "blend_method", "") != "BLEND", "画像付きボカシ素材が半透明化しています"
    assert not any(
        node.bl_idname == "ShaderNodeBsdfTransparent"
        for node in probe_mat.node_tree.nodes
    ), "画像付きボカシ素材に透明シェーダーが残っています"
    mix_nodes = [node for node in probe_mat.node_tree.nodes if node.bl_idname == "ShaderNodeMixRGB"]
    assert len(mix_nodes) >= 2, "画像アルファと輪郭ボカシが背景色へ合成されていません"
    bpy.data.materials.remove(probe_mat)
    bpy.data.images.remove(preview_probe)
    obj_again = coma_border_object.ensure_coma_border_object(scene, work, page, coma)
    image_again = bpy.data.images.get(coma_border_texture.plane_alpha_image_name(page.id, coma.id))
    assert obj_again is obj and image_again is image, "同じ設定でボカシ画像が再作成されています"
    assert image_again.get("bname_border_alpha_signature") == signature_before, "ボカシ画像のキャッシュ署名が維持されていません"

    coma.border.blur_dither = True
    obj = coma_border_object.ensure_coma_border_object(scene, work, page, coma)
    mat = plane.data.materials[0]
    assert getattr(mat, "blend_method", "") != "BLEND", "ディザ時にコマ面素材が半透明化しています"
    image = bpy.data.images.get(coma_border_texture.plane_alpha_image_name(page.id, coma.id))
    alpha = _alpha_values(image)
    assert max(alpha) > 0.9 and min(alpha) == 0.0, "ディザ画像の濃淡範囲が不正です"
    rounded_alpha = {round(float(value), 4) for value in alpha}
    assert rounded_alpha <= {0.0, 1.0}, "ディザ画像が誤差拡散の二値アルファになっていません"
    coma.white_margin.enabled = True
    coma.white_margin.width_mm = 4.0
    obj = coma_border_object.ensure_coma_border_object(scene, work, page, coma)
    white_obj = bpy.data.objects.get(
        f"{coma_border_object.COMA_WHITE_MARGIN_NAME_PREFIX}{page.id}_{coma.id}"
    )
    assert white_obj is not None and white_obj.hide_viewport, "ボカシブラシ時は白フチを表示しないべきです"
    assert export_pipeline._draw_coma_white_margin_layer(coma, 1000, 300) is None, (
        "ボカシブラシ時は書き出し用の白フチも生成しないべきです"
    )

    coma.border.style = "solid"
    coma.shape_type = "polygon"
    coma.vertices.clear()
    for x_mm, y_mm in [(0.0, 0.0), (70.0, 0.0), (60.0, 40.0), (10.0, 50.0)]:
        vertex = coma.vertices.add()
        vertex.x_mm = x_mm
        vertex.y_mm = y_mm
    obj = coma_border_object.ensure_coma_border_object(scene, work, page, coma)
    assert obj is not None and obj.type == "CURVE", "実線へ戻したときカーブ枠線に戻りません"
    white_obj = bpy.data.objects.get(
        f"{coma_border_object.COMA_WHITE_MARGIN_NAME_PREFIX}{page.id}_{coma.id}"
    )
    assert white_obj is not None and not white_obj.hide_viewport, "多角形コマの白フチが表示されていません"
    coords = {(round(float(v.co.x), 5), round(float(v.co.y), 5)) for v in white_obj.data.vertices}
    assert (round(mm_to_m(60.0), 5), round(mm_to_m(40.0), 5)) in coords, (
        "白フチがコマ形状ではなく外接矩形に沿っています"
    )
    assert (round(mm_to_m(10.0), 5), round(mm_to_m(50.0), 5)) in coords, (
        "白フチが斜め辺の頂点に追従していません"
    )
    white_layer = export_pipeline._draw_coma_white_margin_layer(coma, 1000, 300)
    assert white_layer is not None, "多角形コマの書き出し用白フチが生成されていません"
    alpha_values = [px[3] for px in white_layer.image.getdata()]
    assert max(alpha_values) > 0 and min(alpha_values) == 0, (
        "書き出し用白フチが外接矩形で塗りつぶされています"
    )
    assert bpy.data.images.get(coma_border_texture.plane_alpha_image_name(page.id, coma.id)) is None, (
        "実線へ戻したあとコマ面の透明マスク画像が残っています"
    )

    print("BNAME_COMA_BORDER_TEXTURE_CHECK_OK")


main()
