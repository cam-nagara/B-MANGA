"""Blender runtime check: page list coma thumb loading and fitting."""

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
        from bname_dev_coma_preview_scale.utils import coma_plane, coma_preview, paths

        work = get_work(bpy.context)
        assert work is not None
        assert abs(float(work.page_preview_scale_percentage) - 12.5) < 0.001

        work.page_preview_scale_percentage = 25.0
        data = schema.work_to_dict(work)
        assert abs(float(data["pagePreviewScalePercentage"]) - 25.0) < 0.001

        schema.work_from_dict(work, {"pagePreviewScalePercentage": 33.3})
        assert abs(float(work.page_preview_scale_percentage) - 33.3) < 0.001
        schema.work_from_dict(work, {"pagePreviewScalePercentage": 999.0})
        assert abs(float(work.page_preview_scale_percentage) - 100.0) < 0.001
        schema.work_from_dict(work, {"pagePreviewScalePercentage": "invalid"})
        assert abs(float(work.page_preview_scale_percentage) - 12.5) < 0.001

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

        work_dir = Path(work.work_dir)
        thumb = paths.coma_thumb_path(work_dir, page.id, entry.coma_id)
        old_preview = paths.coma_preview_path(work_dir, page.id, entry.coma_id)
        old_preview.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (1024, 512), (255, 0, 0, 255)).save(old_preview)
        assert coma_preview.coma_preview_source_path(work_dir, page.id, entry) is None
        Image.new("RGBA", (73, 19), (0, 120, 255, 255)).save(thumb)
        assert coma_preview.coma_preview_source_path(work_dir, page.id, entry) == thumb

        resolved = coma_plane._resolve_preview_image(work, page, entry)  # noqa: SLF001
        assert resolved is not None
        assert tuple(int(v) for v in resolved.size) == (73, 19)

        mesh = bpy.data.meshes.new("BName_TestComaPreviewFitMesh")
        coma_plane._build_mesh_geometry(mesh, entry)  # noqa: SLF001
        uv_layer = mesh.uv_layers.get(coma_plane.COMA_PLANE_UV_NAME)
        assert uv_layer is not None
        xs = [float(loop.uv.x) for loop in uv_layer.data]
        ys = [float(loop.uv.y) for loop in uv_layer.data]
        assert min(xs) == 0.0 and max(xs) == 1.0
        assert min(ys) == 0.0 and max(ys) == 1.0
        bpy.data.meshes.remove(mesh)

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
