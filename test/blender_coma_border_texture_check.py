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


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon()
    from bname_dev_border_texture.utils import coma_border_object, coma_border_texture

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

    coma.border.blur_dither = False
    obj = coma_border_object.ensure_coma_border_object(scene, work, page, coma)
    assert obj is not None, "ボカシブラシ枠線オブジェクトが生成されません"
    assert obj.type == "MESH", f"ボカシブラシが Mesh ではありません: {obj.type}"
    image = bpy.data.images.get(coma_border_texture.image_name(page.id, coma.id))
    assert image is not None, "ボカシブラシのアルファ画像が生成されません"
    alpha = _alpha_values(image)
    assert max(alpha) > 0.9, "輪郭側のアルファが十分に濃くありません"
    assert min(alpha) == 0.0, "コマ内側の透明部分がありません"
    assert any(0.05 < value < 0.95 for value in alpha), "ボカシの中間アルファがありません"

    coma.border.blur_dither = True
    obj = coma_border_object.ensure_coma_border_object(scene, work, page, coma)
    mat = obj.data.materials[0]
    assert getattr(mat, "surface_render_method", "") == "DITHERED", "ディザ表示素材になっていません"
    image = bpy.data.images.get(coma_border_texture.image_name(page.id, coma.id))
    alpha = _alpha_values(image)
    assert max(alpha) > 0.9 and min(alpha) == 0.0, "ディザ画像の濃淡範囲が不正です"

    coma.border.style = "solid"
    obj = coma_border_object.ensure_coma_border_object(scene, work, page, coma)
    assert obj is not None and obj.type == "CURVE", "実線へ戻したときカーブ枠線に戻りません"
    assert bpy.data.images.get(coma_border_texture.image_name(page.id, coma.id)) is None, (
        "実線へ戻したあとボカシブラシ画像が残っています"
    )

    print("BNAME_COMA_BORDER_TEXTURE_CHECK_OK")


main()
