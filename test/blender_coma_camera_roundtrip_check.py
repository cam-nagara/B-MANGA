"""Blender実機用: コマ編集カメラ設定とページ一覧プレビューの保存復元確認."""

from __future__ import annotations

import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(
    os.environ.get(
        "BMANGA_COMA_CAMERA_ROUNDTRIP_OUT",
        str(ROOT / ".codex" / "visual" / "coma_camera_roundtrip"),
    )
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_coma_camera_roundtrip",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_coma_camera_roundtrip"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _assert_close(actual: float, expected: float, label: str) -> None:
    assert abs(float(actual) - float(expected)) < 1.0e-5, f"{label}: {actual} != {expected}"


def _image_evidence(path: Path) -> dict:
    from bmanga_dev_coma_camera_roundtrip.io import export_pipeline

    Image = export_pipeline.Image
    assert Image is not None
    with Image.open(path) as opened:
        image = opened.convert("RGBA")
        alpha = image.getchannel("A")
        bbox = alpha.getbbox()
        colors = image.getcolors(maxcolors=1_000_000) or []
    return {
        "path": str(path),
        "size": list(image.size),
        "alpha_bbox": list(bbox) if bbox else None,
        "unique_colors": len(colors),
    }


def _write_visual_sheet(evidence: dict, preview_path: Path) -> Path:
    from bmanga_dev_coma_camera_roundtrip.io import export_pipeline

    Image = export_pipeline.Image
    ImageDraw = export_pipeline.ImageDraw
    assert Image is not None and ImageDraw is not None
    sheet = Image.new("RGBA", (920, 520), (248, 248, 248, 255))
    draw = ImageDraw.Draw(sheet)
    draw.text((24, 20), "B-MANGA coma camera roundtrip visual check", fill=(0, 0, 0, 255))
    y = 58
    for label, value in (
        ("fisheye FOV", evidence["fisheye_fov_deg"]),
        ("preview scale", evidence["preview_scale_percentage"]),
        ("render resolution", evidence["render_resolution"]),
        ("camera shift", evidence["camera_shift"]),
        ("background scale", evidence["background_scale"]),
        ("page preview image", evidence["preview"]["size"]),
    ):
        draw.text((24, y), f"{label}: {value}", fill=(20, 20, 20, 255))
        y += 28
    with Image.open(preview_path) as opened:
        preview = opened.convert("RGBA")
    scale = max(1, min(12, 320 // max(1, max(preview.width, preview.height))))
    if scale > 1:
        resampling = getattr(Image, "Resampling", Image)
        preview = preview.resize(
            (preview.width * scale, preview.height * scale),
            resample=getattr(resampling, "NEAREST", 0),
        )
    preview.thumbnail((360, 360))
    frame = Image.new("RGBA", (380, 380), (230, 230, 230, 255))
    frame.alpha_composite(preview, ((380 - preview.width) // 2, (380 - preview.height) // 2))
    sheet.alpha_composite(frame, (500, 78))
    out = OUT_DIR / "coma_camera_roundtrip_sheet.png"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def _current_page_and_coma(work, scene):
    page_id = str(getattr(scene, "bmanga_current_coma_page_id", "") or "")
    coma_id = str(getattr(scene, "bmanga_current_coma_id", "") or "")
    for page in getattr(work, "pages", []):
        if str(getattr(page, "id", "") or "") != page_id:
            continue
        for entry in getattr(page, "comas", []):
            if str(getattr(entry, "coma_id", "") or getattr(entry, "id", "") or "") == coma_id:
                return page, entry
    raise AssertionError(f"編集中コマを解決できません: {page_id}/{coma_id}")


def _create_roundtrip_work(temp_root: Path, get_work):
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "Roundtrip.bmanga"))
    assert result == {"FINISHED"}, result
    work = get_work(bpy.context)
    assert work is not None
    work.page_preview_scale_percentage = 10.0
    page = work.pages[0]
    entry = page.comas[0]
    entry.shape_type = "rect"
    entry.rect_x_mm = 18.0
    entry.rect_y_mm = 20.0
    entry.rect_width_mm = 80.0
    entry.rect_height_mm = 90.0
    entry.background_color = (1.0, 1.0, 1.0, 1.0)
    work.paper.canvas_width_mm = 148.0
    work.paper.canvas_height_mm = 210.0
    work.paper.dpi = 120


def _enter_configured_coma(get_mode, get_work, coma_camera):
    result = bpy.ops.bmanga.enter_coma_mode("EXEC_DEFAULT")
    assert result == {"FINISHED"}, result
    assert get_mode(bpy.context) == "COMA"
    scene = bpy.context.scene
    work = get_work(bpy.context)
    assert work is not None
    page, entry = _current_page_and_coma(work, scene)
    cam = scene.camera
    assert cam is not None
    settings = scene.bmanga_coma_camera_settings
    scene.bmanga_coma_camera_original_resolution_x = 1200
    scene.bmanga_coma_camera_original_resolution_y = 900
    scene.bmanga_coma_camera_fisheye_layout_mode = True
    scene.bmanga_coma_camera_reduction_mode = True
    scene.bmanga_coma_camera_preview_scale_percentage = 28.0
    scene.bmanga_coma_camera_fisheye_fov = math.radians(330.0)
    cam.data.fisheye_fov = math.radians(360.0)
    cam.data.shift_x = 0.019
    cam.data.shift_y = 0.060
    cam.data.clip_start = 0.001
    cam.data.clip_end = 10000.0
    cam.rotation_euler[1] = math.radians(8.881)
    settings.bg_images_scale = 0.28
    migration_prop = getattr(coma_camera, "_OPACITY_PERCENT_MIGRATION_PROP")
    if migration_prop in scene:
        del scene[migration_prop]
    settings.bg_images_opacity = 0.5
    settings.name_bg_images_opacity = 0.37
    settings.koma_bg_images_opacity = 0.82
    coma_camera.ensure_opacity_percent_units(scene)
    _assert_close(settings.bg_images_opacity, 50.0, "下絵不透明度の旧値移行")
    _assert_close(settings.name_bg_images_opacity, 37.0, "ページ画像不透明度の旧値移行")
    _assert_close(settings.koma_bg_images_opacity, 82.0, "コマ下絵不透明度の旧値移行")
    settings.name_bg_images_opacity = 37.0
    settings.koma_bg_images_opacity = 82.0
    settings.name_visible = True
    settings.koma_visible = True
    settings.hatching_visible = True
    settings.hatching_rotation = 0.125
    settings.koma_depth = True
    settings.white_background = True
    settings.world_background_camera_only = True
    settings.use_solid_background_color = True
    settings.solid_background_color = (0.12, 0.18, 0.24)
    coma_camera.capture_camera_runtime_settings(bpy.context)
    _assert_close(scene.bmanga_coma_camera_fisheye_fov, math.radians(360.0), "保存用FOV同期")
    coma_camera.resync_coma_camera_output_layout(bpy.context)
    return work, page, entry


def _write_thumb(paths, work, page, entry) -> tuple[Path, dict]:
    from bmanga_dev_coma_camera_roundtrip.io import export_pipeline

    Image = export_pipeline.Image
    ImageDraw = export_pipeline.ImageDraw
    assert Image is not None and ImageDraw is not None
    preview_path = paths.coma_thumb_path(Path(work.work_dir), page.id, entry.coma_id)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (73, 19), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 72, 18), outline=(40, 40, 40, 255), width=2)
    draw.ellipse((4, 2, 42, 18), fill=(40, 180, 230, 210))
    draw.polygon([(18, 17), (36, 2), (68, 17)], fill=(255, 120, 40, 255))
    draw.line((6, 15, 66, 3), fill=(255, 255, 255, 180), width=1)
    image.save(preview_path)
    evidence = _image_evidence(preview_path)
    assert evidence["alpha_bbox"] is not None, evidence
    assert evidence["unique_colors"] > 4, evidence
    return preview_path, evidence


def _assert_thumb_output_node(coma_thumb_output) -> None:
    scene = bpy.context.scene
    before = (
        scene.render.engine,
        int(scene.render.resolution_x),
        int(scene.render.resolution_y),
        int(scene.render.resolution_percentage),
        str(scene.render.filepath),
    )
    assert coma_thumb_output.ensure_thumb_output_node(bpy.context.scene)
    after = (
        scene.render.engine,
        int(scene.render.resolution_x),
        int(scene.render.resolution_y),
        int(scene.render.resolution_percentage),
        str(scene.render.filepath),
    )
    assert after == before, (before, after)
    tree = scene.compositing_node_group
    assert tree is not None
    nodes = [
        node for node in tree.nodes
        if node.bl_idname == "CompositorNodeOutputFile" and node.name == "thumb.png"
    ]
    assert len(nodes) == 1
    node = nodes[0]
    assert node.label == "thumb.png"
    assert node.directory == "//"
    assert node.file_name == ""
    assert node.format.media_type == "IMAGE"
    assert node.format.file_format == "PNG"
    assert node.inputs.get("thumb") is not None
    assert any(link.to_node == node and link.to_socket == node.inputs["thumb"] for link in tree.links)


def _assert_thumb_output_renders(paths, work, page, entry) -> None:
    scene = bpy.context.scene
    thumb = paths.coma_thumb_path(Path(work.work_dir), page.id, entry.coma_id)
    if thumb.exists():
        thumb.unlink()
    before = (
        scene.render.engine,
        int(scene.render.resolution_x),
        int(scene.render.resolution_y),
        int(scene.render.resolution_percentage),
        str(scene.render.filepath),
        float(work.page_preview_scale_percentage),
    )
    try:
        # thumb.png は「コマ画像縮小率」を Blender の解像度スケールとして
        # 適用して出力する。ここではレンダー成否を検証したいだけなので、
        # 100% に固定して縮尺の影響を外す。
        work.page_preview_scale_percentage = 100.0
        from bmanga_dev_coma_camera_roundtrip.utils import coma_thumb_output as _cto

        scene.render.resolution_x = 16
        scene.render.resolution_y = 16
        scene.render.resolution_percentage = 100
        assert _cto.render_thumb_png(bpy.context)
        assert thumb.is_file(), f"thumb.png was not rendered: {thumb}"
        evidence = _image_evidence(thumb)
        assert evidence["size"] == [16, 16], evidence
    finally:
        (
            scene.render.engine,
            scene.render.resolution_x,
            scene.render.resolution_y,
            scene.render.resolution_percentage,
            scene.render.filepath,
            work.page_preview_scale_percentage,
        ) = before


def _save_and_reopen_coma() -> None:
    coma_path = Path(bpy.data.filepath).resolve()
    result = bpy.ops.bmanga.work_save()
    assert result == {"FINISHED"}, result
    bpy.ops.wm.open_mainfile(filepath=str(coma_path))


def _collect_reopened_checks(preview_evidence: dict) -> dict:
    scene = bpy.context.scene
    settings = scene.bmanga_coma_camera_settings
    cam = scene.camera
    return {
        "fisheye_fov_deg": round(math.degrees(float(scene.bmanga_coma_camera_fisheye_fov)), 3),
        "camera_fov_deg": round(math.degrees(float(cam.data.fisheye_fov)), 3),
        "fisheye_layout_mode": bool(scene.bmanga_coma_camera_fisheye_layout_mode),
        "reduction_mode": bool(scene.bmanga_coma_camera_reduction_mode),
        "preview_scale_percentage": float(scene.bmanga_coma_camera_preview_scale_percentage),
        "original_resolution": [
            int(scene.bmanga_coma_camera_original_resolution_x),
            int(scene.bmanga_coma_camera_original_resolution_y),
        ],
        "render_resolution": [int(scene.render.resolution_x), int(scene.render.resolution_y)],
        "render_resolution_percentage": int(scene.render.resolution_percentage),
        "camera_shift": [round(float(cam.data.shift_x), 3), round(float(cam.data.shift_y), 3)],
        "camera_rotation_y_deg": round(math.degrees(float(cam.rotation_euler[1])), 3),
        "clip": [round(float(cam.data.clip_start), 3), round(float(cam.data.clip_end), 3)],
        "background_scale": round(float(settings.bg_images_scale), 3),
        "name_opacity": round(float(settings.name_bg_images_opacity), 3),
        "koma_opacity": round(float(settings.koma_bg_images_opacity), 3),
        "hatching_visible": bool(settings.hatching_visible),
        "hatching_rotation": round(float(settings.hatching_rotation), 3),
        "koma_depth": bool(settings.koma_depth),
        "white_background": bool(settings.white_background),
        "world_background_camera_only": bool(settings.world_background_camera_only),
        "solid_background": bool(settings.use_solid_background_color),
        "preview": preview_evidence,
    }


def _assert_reopened_checks(checks: dict) -> None:
    assert checks["fisheye_layout_mode"] is True, checks
    assert checks["reduction_mode"] is True, checks
    _assert_close(checks["fisheye_fov_deg"], 360.0, "再読込後FOV")
    _assert_close(checks["camera_fov_deg"], 360.0, "再読込後カメラFOV")
    _assert_close(checks["preview_scale_percentage"], 28.0, "再読込後縮小率")
    assert checks["original_resolution"] == [1200, 900], checks
    assert checks["render_resolution"] == [1200, 1200], checks
    assert checks["render_resolution_percentage"] == 28, checks
    assert checks["camera_shift"] == [0.019, 0.06], checks
    _assert_close(checks["camera_rotation_y_deg"], 8.881, "再読込後カメラ回転")
    assert checks["clip"] == [0.001, 10000.0], checks
    _assert_close(checks["background_scale"], 0.28, "再読込後下絵スケール")
    _assert_close(checks["name_opacity"], 37.0, "再読込後ページ画像不透明度")
    _assert_close(checks["koma_opacity"], 82.0, "再読込後コマ下絵不透明度")
    assert checks["hatching_visible"] is True, checks
    _assert_close(checks["hatching_rotation"], 0.125, "再読込後ハッチング回転")
    assert checks["koma_depth"] is True, checks
    assert checks["white_background"] is True, checks
    assert checks["world_background_camera_only"] is True, checks
    assert checks["solid_background"] is True, checks


def _verify_page_list_preview(get_work, coma_plane, checks: dict, preview_path: Path) -> None:
    result = bpy.ops.bmanga.exit_coma_mode_safe("EXEC_DEFAULT")
    assert result == {"FINISHED"}, result
    work = get_work(bpy.context)
    page = work.pages[0]
    entry = page.comas[0]
    image = coma_plane._resolve_preview_image(work, page, entry)
    assert image is not None, "ページ一覧でコマプレビュー画像を読み込めません"
    checks["resolved_preview_image"] = image.name
    checks["visual_sheet"] = str(_write_visual_sheet(checks, preview_path))


def main() -> None:
    mod = _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_camera_roundtrip_"))
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        from bmanga_dev_coma_camera_roundtrip.core.mode import get_mode
        from bmanga_dev_coma_camera_roundtrip.core.work import get_work
        from bmanga_dev_coma_camera_roundtrip.utils import (
            coma_camera,
            coma_plane,
            coma_thumb_output,
            paths,
        )

        _create_roundtrip_work(temp_root, get_work)
        work, page, entry = _enter_configured_coma(get_mode, get_work, coma_camera)
        _assert_thumb_output_node(coma_thumb_output)
        _assert_thumb_output_renders(paths, work, page, entry)
        preview_path, preview_evidence = _write_thumb(paths, work, page, entry)
        _save_and_reopen_coma()
        checks = _collect_reopened_checks(preview_evidence)
        _assert_reopened_checks(checks)
        _verify_page_list_preview(get_work, coma_plane, checks, preview_path)
        (OUT_DIR / "coma_camera_roundtrip.json").write_text(
            json.dumps(checks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    finally:
        try:
            mod.unregister()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
    print(f"BMANGA_COMA_CAMERA_ROUNDTRIP_OK visual={OUT_DIR / 'coma_camera_roundtrip_sheet.png'}")


if __name__ == "__main__":
    main()
