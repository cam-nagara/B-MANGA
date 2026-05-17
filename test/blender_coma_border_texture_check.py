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
    from bname_dev_border_texture.utils import coma_border_object, coma_border_texture, coma_plane

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

    edge_style = coma.edge_styles.add()
    edge_style.edge_index = 0
    edge_style.width_mm = 0.6
    coma.border.edge_top.use_override = True
    coma.border.blur_dither = False
    obj = coma_border_object.ensure_coma_border_object(scene, work, page, coma)
    plane = coma_plane.find_coma_plane_object(page.id, coma.id)
    assert obj is plane and plane is not None, "ボカシブラシがコマ面に適用されていません"
    assert bpy.data.objects.get(coma_border_texture.object_name(page.id, coma.id)) is None, (
        "別体のボカシ枠線オブジェクトが残っています"
    )
    assert len(coma.edge_styles) == 0, "ボカシブラシ切替後に辺ごとの個別設定が残っています"
    assert not bool(coma.border.edge_top.use_override), "ボカシブラシ切替後に辺ごとの設定が残っています"
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
    mat = plane.data.materials[0]
    assert getattr(mat, "blend_method", "") == "BLEND", "コマ面素材が透明表示になっていません"
    assert any(
        node.name == "BName_ComaAlphaMask" and node.image is image
        for node in mat.node_tree.nodes
    ), "コマ面素材に透明マスク画像が接続されていません"
    obj_again = coma_border_object.ensure_coma_border_object(scene, work, page, coma)
    image_again = bpy.data.images.get(coma_border_texture.plane_alpha_image_name(page.id, coma.id))
    assert obj_again is obj and image_again is image, "同じ設定でボカシ画像が再作成されています"
    assert image_again.get("bname_border_alpha_signature") == signature_before, "ボカシ画像のキャッシュ署名が維持されていません"

    coma.border.blur_dither = True
    obj = coma_border_object.ensure_coma_border_object(scene, work, page, coma)
    mat = plane.data.materials[0]
    assert getattr(mat, "surface_render_method", "") == "DITHERED", "ディザ表示素材になっていません"
    image = bpy.data.images.get(coma_border_texture.plane_alpha_image_name(page.id, coma.id))
    alpha = _alpha_values(image)
    assert max(alpha) > 0.9 and min(alpha) == 0.0, "ディザ画像の濃淡範囲が不正です"

    coma.border.style = "solid"
    obj = coma_border_object.ensure_coma_border_object(scene, work, page, coma)
    assert obj is not None and obj.type == "CURVE", "実線へ戻したときカーブ枠線に戻りません"
    assert bpy.data.images.get(coma_border_texture.plane_alpha_image_name(page.id, coma.id)) is None, (
        "実線へ戻したあとコマ面の透明マスク画像が残っています"
    )

    print("BNAME_COMA_BORDER_TEXTURE_CHECK_OK")


main()
