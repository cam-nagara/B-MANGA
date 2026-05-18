"""Blender runtime check: page list coma preview scale setting."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_coma_preview_scale",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_coma_preview_scale"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def main() -> None:
    mod = _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bname_coma_preview_scale_"))
    try:
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "PreviewScale.bname"))
        assert result == {"FINISHED"}, result

        from bname_dev_coma_preview_scale.core.work import get_work
        from bname_dev_coma_preview_scale.io import export_pipeline, schema
        from bname_dev_coma_preview_scale.operators import thumbnail_op
        from bname_dev_coma_preview_scale.utils import coma_plane

        work = get_work(bpy.context)
        assert work is not None
        assert abs(float(work.page_preview_scale_percentage) - 10.0) < 0.001

        work.page_preview_scale_percentage = 25.0
        data = schema.work_to_dict(work)
        assert abs(float(data["pagePreviewScalePercentage"]) - 25.0) < 0.001

        schema.work_from_dict(work, {"pagePreviewScalePercentage": 33.3})
        assert abs(float(work.page_preview_scale_percentage) - 33.3) < 0.001
        schema.work_from_dict(work, {"pagePreviewScalePercentage": 999.0})
        assert abs(float(work.page_preview_scale_percentage) - 100.0) < 0.001
        schema.work_from_dict(work, {"pagePreviewScalePercentage": "invalid"})
        assert abs(float(work.page_preview_scale_percentage) - 10.0) < 0.001

        Image = export_pipeline.Image
        assert Image is not None
        work.paper.canvas_width_mm = 100.0
        work.paper.canvas_height_mm = 100.0
        page = work.pages[0]
        entry = page.comas[0]
        entry.shape_type = "rect"
        entry.rect_x_mm = 10.0
        entry.rect_y_mm = 20.0
        entry.rect_width_mm = 30.0
        entry.rect_height_mm = 40.0
        entry.background_color = (1.0, 1.0, 1.0, 0.0)

        source = temp_root / "source.png"
        out = temp_root / "out.png"
        image = Image.new("RGBA", (1000, 1000), (255, 255, 255, 255))
        image.save(source)
        ok = thumbnail_op._crop_render_to_panel(
            source,
            out,
            work,
            page,
            entry,
            output_scale_percentage=10.0,
        )
        assert ok and out.is_file()
        with Image.open(out) as opened:
            assert opened.size == (30, 40), opened.size
            assert opened.convert("RGBA").getpixel((0, 0))[3] == 0
        full_out = temp_root / "full.png"
        ok = thumbnail_op._crop_render_to_panel(
            source,
            full_out,
            work,
            page,
            entry,
            output_scale_percentage=None,
        )
        assert ok and full_out.is_file()
        with Image.open(full_out) as opened:
            assert opened.size == (300, 400), opened.size

        render_scene = bpy.data.scenes.new("BName_TestComaPreviewTransparentRender")
        render_camera_data = bpy.data.cameras.new("BName_TestComaPreviewTransparentCamera")
        render_camera = bpy.data.objects.new("BName_TestComaPreviewTransparentCamera", render_camera_data)
        render_scene.collection.objects.link(render_camera)
        render_scene.camera = render_camera
        render_scene.render.filepath = str(temp_root / "transparent_render.png")
        render_scene.render.resolution_x = 16
        render_scene.render.resolution_y = 16
        render_scene.render.resolution_percentage = 100
        render_scene.render.image_settings.file_format = "PNG"
        render_scene.render.image_settings.color_mode = "RGBA"
        render_scene.render.film_transparent = True
        try:
            assert thumbnail_op._render_camera_image(bpy.context, render_scene), (
                "ページ一覧コマ画像用のRGBAレンダーが生成されません"
            )
            with Image.open(temp_root / "transparent_render.png") as opened:
                assert opened.convert("RGBA").getpixel((0, 0))[3] == 0, (
                    "ページ一覧コマ画像の空部分が透明になっていません"
                )
        finally:
            bpy.data.objects.remove(render_camera, do_unlink=True)
            bpy.data.cameras.remove(render_camera_data, do_unlink=True)
            bpy.data.scenes.remove(render_scene)

        preview_probe = bpy.data.images.new("BName_TestComaPreviewTransparent", width=2, height=2, alpha=True)
        preview_probe.pixels.foreach_set([
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 1.0,
            0.0, 0.0, 1.0, 0.5,
            1.0, 1.0, 1.0, 1.0,
        ])
        preview_probe.update()
        mat = bpy.data.materials.new("BName_TestComaPreviewTransparentMaterial")
        mat.use_nodes = True
        coma_plane._apply_material(  # noqa: SLF001 - material node contract check
            mat,
            (1.0, 1.0, 1.0, 0.0),
            preview_probe,
            keep_existing_image=False,
            use_soft_mask=False,
        )
        assert getattr(mat, "blend_method", "") == "BLEND", "ページ一覧コマ画像が透明表示になっていません"
        assert any(
            node.bl_idname == "ShaderNodeBsdfTransparent"
            for node in mat.node_tree.nodes
        ), "ページ一覧コマ画像の透明シェーダーがありません"
        bpy.data.materials.remove(mat)
        bpy.data.images.remove(preview_probe)
    finally:
        try:
            mod.unregister()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    print("BNAME_COMA_PREVIEW_SCALE_CHECK_OK")


main()
