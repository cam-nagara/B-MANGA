"""Blender実機用: 作品/ページ/コマ編集のプレビュー反映を画像で監査する."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_preview_propagation"
OUT_DIR = ROOT / ".codex" / "visual" / "preview_propagation"
SHEET_PATH = OUT_DIR / "preview_propagation_sheet.png"
SUMMARY_PATH = OUT_DIR / "preview_propagation_summary.json"
PROGRESS_PATH = OUT_DIR / "preview_propagation_progress.txt"


def _mark(text: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with PROGRESS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(text, flush=True)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _submodule(name: str):
    return sys.modules[f"{MOD_NAME}.{name}"]


def _configure_fast_render(scene, work) -> None:
    scene.render.resolution_x = 96
    scene.render.resolution_y = 96
    scene.render.resolution_percentage = 100
    try:
        scene.render.engine = "BLENDER_WORKBENCH"
    except Exception:  # noqa: BLE001
        pass
    try:
        scene.display.shading.light = "FLAT"
        scene.display.shading.color_type = "MATERIAL"
    except Exception:  # noqa: BLE001
        pass
    try:
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
    except Exception:  # noqa: BLE001
        pass
    work.page_preview_scale_percentage = 100.0


def _set_first_coma_color(work, rgba: tuple[float, float, float, float]) -> None:
    page = work.pages[0]
    coma = page.comas[0]
    coma.rect_x_mm = 18.0
    coma.rect_y_mm = 24.0
    coma.rect_width_mm = 92.0
    coma.rect_height_mm = 118.0
    coma.background_color = rgba


def _add_camera_marker(name: str, rgba: tuple[float, float, float, float]) -> None:
    from mathutils import Vector

    scene = bpy.context.scene
    cam = scene.camera
    if cam is None:
        bpy.ops.object.camera_add(location=(0.0, -4.0, 0.0), rotation=(1.5708, 0.0, 0.0))
        cam = bpy.context.object
        scene.camera = cam
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 2.2
    loc = cam.matrix_world @ Vector((0.0, 0.0, -2.0))
    bpy.ops.mesh.primitive_plane_add(size=1.55, location=loc, rotation=cam.rotation_euler)
    obj = bpy.context.object
    obj.name = name
    mat = bpy.data.materials.new(f"{name}_mat")
    mat.diffuse_color = rgba
    obj.data.materials.append(mat)


def _image_stats(path: Path) -> dict:
    from PIL import Image

    with Image.open(path) as opened:
        image = opened.convert("RGBA")
        alpha_bbox = image.getchannel("A").getbbox()
        colors = image.getcolors(maxcolors=1_000_000) or []
        flat_data = getattr(image, "get_flattened_data", None)
        pixels = list(flat_data() if flat_data is not None else image.getdata())
    red_pixels = sum(1 for r, g, b, a in pixels if a > 64 and r > 170 and g < 100 and b < 100)
    green_pixels = sum(1 for r, g, b, a in pixels if a > 64 and g > 150 and r < 140 and b < 140)
    blue_pixels = sum(1 for r, g, b, a in pixels if a > 64 and b > 150 and r < 140 and g < 160)
    yellow_pixels = sum(1 for r, g, b, a in pixels if a > 64 and r > 180 and g > 140 and b < 210)
    cyan_pixels = sum(1 for r, g, b, a in pixels if a > 64 and r < 190 and g > 180 and b > 170)
    return {
        "path": str(path),
        "size": [image.width, image.height],
        "alpha_bbox": list(alpha_bbox) if alpha_bbox else None,
        "unique_colors": len(colors),
        "red_pixels": red_pixels,
        "green_pixels": green_pixels,
        "blue_pixels": blue_pixels,
        "yellow_pixels": yellow_pixels,
        "cyan_pixels": cyan_pixels,
    }


def _copy_image(src: Path, dst_name: str) -> Path:
    dst = OUT_DIR / dst_name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def _make_sheet(entries: list[tuple[str, Path]]) -> None:
    from PIL import Image, ImageDraw, ImageFont

    cell_w, cell_h = 260, 230
    rows = max(1, (len(entries) + 1) // 2)
    sheet = Image.new("RGBA", (cell_w * 2, cell_h * rows), (245, 245, 245, 255))
    draw = ImageDraw.Draw(sheet)
    font = None
    for font_path in (
        Path("C:/Windows/Fonts/meiryo.ttc"),
        Path("C:/Windows/Fonts/YuGothM.ttc"),
        Path("C:/Windows/Fonts/msgothic.ttc"),
    ):
        if not font_path.is_file():
            continue
        try:
            font = ImageFont.truetype(str(font_path), 12)
            break
        except Exception:  # noqa: BLE001
            font = None
    for index, (label, path) in enumerate(entries):
        x = (index % 2) * cell_w
        y = (index // 2) * cell_h
        draw.rectangle((x + 8, y + 8, x + cell_w - 8, y + cell_h - 8), outline=(120, 120, 120, 255))
        draw.text((x + 16, y + 16), label, fill=(0, 0, 0, 255), font=font)
        with Image.open(path) as opened:
            img = opened.convert("RGBA")
        img.thumbnail((cell_w - 40, cell_h - 56))
        sheet.alpha_composite(img, (x + (cell_w - img.width) // 2, y + 48))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sheet.save(SHEET_PATH)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text("", encoding="utf-8")
    _mark("STEP load_addon")
    mod = _load_addon()
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_preview_propagation_"))
    results: dict[str, object] = {}
    try:
        work_path = temp_root / "PreviewPropagation.bmanga"
        _mark("STEP work_new")
        assert bpy.ops.bmanga.work_new(filepath=str(work_path)) == {"FINISHED"}
        work = bpy.context.scene.bmanga_work
        _configure_fast_render(bpy.context.scene, work)
        _set_first_coma_color(work, (0.62, 0.93, 0.88, 1.0))

        page_preview_object = _submodule("utils.page_preview_object")
        _mark("STEP work_preview_sync")
        page_preview_object.sync_page_previews(bpy.context, work, force=True)
        work_preview_1 = _copy_image(work_path / "p0001" / "page_preview.png", "01_work_edit_page_preview.png")

        _mark("STEP open_page_file")
        assert bpy.ops.bmanga.open_page_file(index=0) == {"FINISHED"}
        work_page_preview = _copy_image(work_path / "p0001" / "page_preview.png", "02_work_edit_page_file_preview.png")
        work = bpy.context.scene.bmanga_work
        _configure_fast_render(bpy.context.scene, work)
        _set_first_coma_color(work, (0.97, 0.82, 0.28, 1.0))
        page_preview_object = _submodule("utils.page_preview_object")
        _mark("STEP page_preview_sync")
        page_preview_object.sync_page_previews(bpy.context, work, force=True)
        page_preview_1 = _copy_image(work_path / "p0001" / "page_preview.png", "03_page_edit_page_preview.png")

        _mark("STEP page_edit_exit_to_work")
        assert bpy.ops.bmanga.exit_page_file() == {"FINISHED"}
        work = bpy.context.scene.bmanga_work
        page_preview_object = _submodule("utils.page_preview_object")
        page_preview_object.sync_page_previews(bpy.context, work, force=True)
        page_work_preview = _copy_image(work_path / "p0001" / "page_preview.png", "04_page_edit_work_preview.png")

        _mark("STEP reopen_page_file")
        assert bpy.ops.bmanga.open_page_file(index=0) == {"FINISHED"}
        work = bpy.context.scene.bmanga_work
        page_coma_reference = _copy_image(work_path / "p0001" / "page_preview.png", "05_page_edit_coma_reference.png")

        work.active_page_index = 0
        work.pages[0].active_coma_index = 0
        _mark("STEP enter_coma_mode")
        assert bpy.ops.bmanga.enter_coma_mode() == {"FINISHED"}
        work = bpy.context.scene.bmanga_work
        _configure_fast_render(bpy.context.scene, work)
        _add_camera_marker("preview_propagation_red_marker", (1.0, 0.02, 0.01, 1.0))
        _mark("STEP work_save_in_coma")
        assert bpy.ops.bmanga.work_save() == {"FINISHED"}
        paths = _submodule("utils.paths")
        page = work.pages[0]
        coma = page.comas[0]
        saved_thumb_path = paths.coma_thumb_path(Path(work.work_dir), page.id, coma.coma_id)
        saved_thumb_stats = _image_stats(saved_thumb_path)
        if saved_thumb_stats["red_pixels"] < 800:
            raise AssertionError("コマ用blend保存時に赤い配置物がコマ画像へ反映されていません")
        _mark("STEP exit_coma_mode_safe")
        assert bpy.ops.bmanga.exit_coma_mode_safe("EXEC_DEFAULT") == {"FINISHED"}
        work = bpy.context.scene.bmanga_work
        page = work.pages[0]
        coma = page.comas[0]
        coma_plane = _submodule("utils.coma_plane")
        thumb_path = paths.coma_thumb_path(Path(work.work_dir), page.id, coma.coma_id)
        assert thumb_path.is_file(), f"コマ画像が生成されていません: {thumb_path}"
        thumb_copy = _copy_image(thumb_path, "06_coma_file_thumb.png")

        _mark("STEP page_resolve_coma_thumb")
        image = coma_plane._resolve_preview_image(work, page, coma)
        assert image is not None, "ページファイル側でコマ画像を解決できません"
        page_preview_object = _submodule("utils.page_preview_object")
        _mark("STEP page_after_coma_sync")
        page_preview_object.sync_page_previews(bpy.context, work, force=True)
        page_preview_2 = _copy_image(work_path / "p0001" / "page_preview.png", "07_page_after_coma_preview.png")

        _mark("STEP exit_page_file")
        assert bpy.ops.bmanga.exit_page_file() == {"FINISHED"}
        work = bpy.context.scene.bmanga_work
        page_preview_object = _submodule("utils.page_preview_object")
        _mark("STEP work_after_coma_sync")
        page_preview_object.sync_page_previews(bpy.context, work, force=True)
        work_preview_2 = _copy_image(work_path / "p0001" / "page_preview.png", "08_work_after_coma_preview.png")

        entries = [
            ("作品ファイル編集 -> ページ一覧", work_preview_1),
            ("作品ファイル編集 -> ページファイル", work_page_preview),
            ("ページファイル編集 -> ページ画像", page_preview_1),
            ("ページファイル編集 -> 作品ファイル", page_work_preview),
            ("ページファイル編集 -> コマ参照", page_coma_reference),
            ("コマファイル編集 -> コマ画像", thumb_copy),
            ("コマファイル編集 -> ページファイル", page_preview_2),
            ("ページファイルから戻る -> 作品ファイル", work_preview_2),
        ]
        _make_sheet(entries)
        stats = {label: _image_stats(path) for label, path in entries}
        results.update(
            {
                "work_dir": str(work_path),
                "sheet": str(SHEET_PATH),
                "entries": [{"label": label, "path": str(path)} for label, path in entries],
                "stats": stats,
            }
        )
        if stats["作品ファイル編集 -> ページ一覧"]["cyan_pixels"] < 1000:
            raise AssertionError("作品ファイル編集がページ一覧に反映されていません")
        if stats["作品ファイル編集 -> ページファイル"]["cyan_pixels"] < 1000:
            raise AssertionError("作品ファイル編集がページファイルに反映されていません")
        if stats["ページファイル編集 -> ページ画像"]["yellow_pixels"] < 1000:
            raise AssertionError("ページファイル編集がページ画像に反映されていません")
        if stats["ページファイル編集 -> 作品ファイル"]["yellow_pixels"] < 1000:
            raise AssertionError("ページファイル編集が作品ファイルに反映されていません")
        if stats["ページファイル編集 -> コマ参照"]["yellow_pixels"] < 1000:
            raise AssertionError("ページファイル編集がコマファイル参照に反映されていません")
        if stats["コマファイル編集 -> コマ画像"]["red_pixels"] < 800:
            raise AssertionError("コマ画像に赤い配置物が十分に反映されていません")
        if stats["コマファイル編集 -> ページファイル"]["red_pixels"] < 80:
            raise AssertionError("ページファイル側プレビューに赤い配置物が反映されていません")
        if stats["ページファイルから戻る -> 作品ファイル"]["red_pixels"] < 80:
            raise AssertionError("作品ファイル側プレビューに赤い配置物が反映されていません")
        results["saved_thumb_stats"] = saved_thumb_stats
        _mark("STEP done")
    finally:
        SUMMARY_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        shutil.rmtree(temp_root, ignore_errors=True)
    print(f"BMANGA_PREVIEW_PROPAGATION_VISUAL_OK sheet={SHEET_PATH}", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
