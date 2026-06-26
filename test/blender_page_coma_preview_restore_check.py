"""Blender実機チェック: ページ/コマファイルのページ一覧プレビュー復旧."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_page_coma_preview_restore"
OUT_DIR = ROOT / ".codex" / "test_artifacts" / "page_coma_preview_restore"
STAGE_FILE = OUT_DIR / "stage.txt"


def _mark(stage: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STAGE_FILE.write_text(stage, encoding="utf-8")


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


def _count_pixels(path: Path, predicate) -> int:
    from PIL import Image

    with Image.open(str(path)) as opened:
        image = opened.convert("RGBA")
        step = max(1, min(image.width, image.height) // 160)
        count = 0
        for y in range(0, image.height, step):
            for x in range(0, image.width, step):
                if predicate(image.getpixel((x, y))):
                    count += 1
        return count


def _metadata(path: Path) -> dict[str, str]:
    from PIL import Image

    with Image.open(str(path)) as opened:
        return {str(k): str(v) for k, v in opened.info.items()}


def _safe_guide_pixel_exists(path: Path, work, page=None) -> bool:
    from PIL import Image
    from bmanga_dev_page_coma_preview_restore.ui import overlay_shared
    from bmanga_dev_page_coma_preview_restore.utils import page_grid, spread_merge_geometry

    paper = work.paper
    if page is not None and bool(getattr(page, "spread", False)):
        rects = spread_merge_geometry.combined_spread_rects(paper, page)
    else:
        rects = overlay_shared.compute_paper_rects(paper, is_left_half=False)
    content_width_mm = float(paper.canvas_width_mm)
    if page is not None:
        content_width_mm = page_grid.spread_content_width_mm(
            page,
            float(paper.canvas_width_mm),
            float(paper.finish_width_mm),
        )
    with Image.open(str(path)) as opened:
        image = opened.convert("RGBA")
        width, height = image.size
        x = int(round(rects.safe.x / content_width_mm * width))
        y = int(round(height - (rects.safe.y + rects.safe.height * 0.5) / float(paper.canvas_height_mm) * height))
        found = False
        for yy in range(max(0, y - 3), min(height, y + 4)):
            for xx in range(max(0, x - 3), min(width, x + 4)):
                r, g, b, a = image.getpixel((xx, yy))
                if a > 180 and g > 170 and b > 170 and r < 150:
                    return True
    return False


def _assert_safe_guide_pixel(path: Path, work, page=None) -> None:
    if not _safe_guide_pixel_exists(path, work, page):
        raise AssertionError("ページ一覧プレビュー画像に用紙ガイド線が見つかりません")


def _assert_no_safe_guide_pixel(path: Path, work, page=None) -> None:
    if _safe_guide_pixel_exists(path, work, page):
        raise AssertionError("ページ一覧プレビュー画像に用紙ガイド線が残っています")


def _assert_preview_image_is_spread(path: Path, work, page) -> None:
    from PIL import Image
    from bmanga_dev_page_coma_preview_restore.utils import page_grid

    paper = work.paper
    expected_w_mm = page_grid.spread_content_width_mm(
        page,
        float(paper.canvas_width_mm),
        float(paper.finish_width_mm),
    )
    expected_ratio = expected_w_mm / float(paper.canvas_height_mm)
    with Image.open(str(path)) as opened:
        width, height = opened.size
    if width <= height:
        raise AssertionError(f"見開きページ一覧プレビュー画像が横長で作られていません: {width}x{height}")
    actual_ratio = float(width) / float(height)
    if abs(actual_ratio - expected_ratio) > 0.03:
        raise AssertionError(
            f"見開きページ一覧プレビュー画像の比率が違います: "
            f"expected={expected_ratio:.4f}, actual={actual_ratio:.4f}"
        )


def _background_for_page(camera, page_id: str):
    cam_data = camera.data
    for bg in getattr(cam_data, "background_images", []):
        image = getattr(bg, "image", None)
        if image is None:
            continue
        if (
            str(image.get("bmanga_kind", "") or "") == "name"
            and str(image.get("bmanga_page_id", "") or "") == page_id
        ):
            return bg
    return None


def _assert_spread_overview_background_aligned(scene, work, spread_page_id: str) -> None:
    from bmanga_dev_page_coma_preview_restore.utils import page_preview_object

    camera = scene.camera
    if camera is None:
        raise AssertionError("コマファイルのカメラが見つかりません")
    bg = _background_for_page(camera, spread_page_id)
    if bg is None:
        raise AssertionError("見開きページのページ一覧下絵が見つかりません")
    rects = page_preview_object.preview_rects_mm(scene, work)
    _role, current_page_id = page_preview_object._preview_scene_role(scene)
    current_rect = rects.get(current_page_id)
    spread_rect = rects.get(spread_page_id)
    if current_rect is None or spread_rect is None:
        raise AssertionError("ページ一覧下絵の配置計算に現在ページまたは見開きページがありません")
    _ci, cx0, cy0, cx1, cy1 = current_rect
    _si, sx0, sy0, sx1, sy1 = spread_rect
    current_cx = (cx0 + cx1) * 0.5
    current_cy = (cy0 + cy1) * 0.5
    spread_cx = (sx0 + sx1) * 0.5
    spread_cy = (sy0 + sy1) * 0.5
    canvas_w = float(work.paper.canvas_width_mm)
    canvas_h = float(work.paper.canvas_height_mm)
    settings = getattr(scene, "bmanga_coma_camera_settings", None)
    user_scale = max(0.1, float(getattr(settings, "bg_images_scale", 1.0))) if settings else 1.0
    expected_scale = ((sx1 - sx0) / canvas_w) * user_scale
    expected_offset_x = ((spread_cx - current_cx) / canvas_w) * user_scale
    expected_offset_y = ((spread_cy - current_cy) / canvas_h) * user_scale
    if expected_offset_y >= -0.1:
        raise AssertionError("見開き下絵を下段に置く検証条件になっていません")
    actual_scale = float(getattr(bg, "scale", 0.0) or 0.0)
    actual_offset_x = float(bg.offset[0])
    actual_offset_y = float(bg.offset[1])
    if abs(actual_scale - expected_scale) > 0.001:
        raise AssertionError(
            f"見開きページ一覧下絵の大きさが違います: "
            f"expected={expected_scale:.4f}, actual={actual_scale:.4f}"
        )
    if abs(actual_offset_x - expected_offset_x) > 0.001 or abs(actual_offset_y - expected_offset_y) > 0.001:
        raise AssertionError(
            f"見開きページ一覧下絵の位置が違います: "
            f"expected=({expected_offset_x:.4f}, {expected_offset_y:.4f}), "
            f"actual=({actual_offset_x:.4f}, {actual_offset_y:.4f})"
        )


def _visible_border_owner_ids() -> set[str]:
    return {
        str(obj.get("bmanga_coma_border_owner_id", "") or "")
        for obj in bpy.data.objects
        if str(obj.get("bmanga_coma_border_owner_id", "") or "")
        and not bool(getattr(obj, "hide_viewport", False))
    }


def _assert_current_page_runtime_aligned(work) -> None:
    from bmanga_dev_page_coma_preview_restore.utils import page_grid

    scene = bpy.context.scene
    page = work.pages[0]
    page_id = str(getattr(page, "id", "") or "")
    ox_mm, oy_mm = page_grid.page_total_offset_mm(work, scene, 0)
    for coma in getattr(page, "comas", []) or []:
        owner = f"{page_id}:{getattr(coma, 'id', '')}"
        local_x = float(getattr(coma, "rect_x_mm", 0.0) or 0.0)
        local_y = float(getattr(coma, "rect_y_mm", 0.0) or 0.0)
        expected_x = ox_mm + local_x
        expected_y = oy_mm + local_y
        objs = [
            obj
            for obj in bpy.data.objects
            if str(obj.get("bmanga_coma_plane_owner_id", "") or "") == owner
            or str(obj.get("bmanga_coma_border_owner_id", "") or "") == owner
            or str(obj.get("bmanga_coma_white_margin_owner_id", "") or "") == owner
        ]
        if not objs:
            raise AssertionError(f"{owner} のコマ実体が見つかりません")
        for obj in objs:
            actual_x = float(obj.location.x) * 1000.0
            actual_y = float(obj.location.y) * 1000.0
            if abs(actual_x - expected_x) > 0.01 or abs(actual_y - expected_y) > 0.01:
                raise AssertionError(
                    f"{obj.name} の位置がページ一覧プレビューとずれています: "
                    f"expected=({expected_x:.3f}, {expected_y:.3f}), "
                    f"actual=({actual_x:.3f}, {actual_y:.3f})"
                )


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_page_coma_preview_restore_"))
    mod = None
    success = False
    try:
        _mark("factory_settings")
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _mark("load_addon")
        mod = _load_addon()
        work_dir = temp_root / "PreviewRestore.bmanga"
        _mark("work_new")
        result = bpy.ops.bmanga.work_new(filepath=str(work_dir))
        if result != {"FINISHED"}:
            raise AssertionError(f"作品作成に失敗しました: {result}")
        for _ in range(2):
            result = bpy.ops.bmanga.page_add()
            if result != {"FINISHED"}:
                raise AssertionError(f"ページ追加に失敗しました: {result}")
        result = bpy.ops.bmanga.pages_merge_spread("EXEC_DEFAULT", left_index=1)
        if result != {"FINISHED"}:
            raise AssertionError(f"見開きページ作成に失敗しました: {result}")

        work = bpy.context.scene.bmanga_work
        if len(work.pages) < 2 or not bool(getattr(work.pages[1], "spread", False)):
            raise AssertionError("2-3ページ目が見開きページになっていません")
        spread_page_id = str(getattr(work.pages[1], "id", "") or "")
        if not spread_page_id:
            raise AssertionError("見開きページIDが取得できません")
        work.work_info.work_name = "MAGENTA_PREVIEW_INFO"
        work.work_info.display_work_name.enabled = True
        work.work_info.display_work_name.position = "top-left"
        work.work_info.display_work_name.font_size_q = 64.0
        work.work_info.display_work_name.color = (1.0, 0.0, 1.0, 1.0)
        work.safe_area_overlay.enabled = True
        work.safe_area_overlay.opacity = 100.0
        work.safe_area_overlay.color = (0.0, 1.0, 0.0)
        work.safe_area_overlay.bleed_outer_enabled = False
        work.paper.show_guides = True
        work.paper.show_safe_line = True
        work.paper.show_inner_frame = True

        _mark("work_save")
        result = bpy.ops.bmanga.work_save()
        if result != {"FINISHED"}:
            raise AssertionError(f"作品保存に失敗しました: {result}")

        _mark("open_page_file")
        result = bpy.ops.bmanga.open_page_file(index=0)
        if result != {"FINISHED"}:
            raise AssertionError(f"ページファイルを開けません: {result}")

        _mark("page_file_assertions")
        _mark("page_file_import_preview_module")
        from bmanga_dev_page_coma_preview_restore.utils import page_preview_object

        _mark("page_file_sync_previews")
        scene = bpy.context.scene
        work = scene.bmanga_work
        page_preview_object.sync_page_previews(bpy.context, work, force=True)
        spread_page = work.pages[1]
        spread_page_id = str(getattr(spread_page, "id", "") or spread_page_id)
        preview_path = work_dir / spread_page_id / "page_preview.png"
        _mark("page_file_check_preview_file")
        if not preview_path.is_file():
            raise AssertionError("ページ一覧プレビュー画像が作られていません")
        _assert_preview_image_is_spread(preview_path, work, spread_page)
        _mark("page_file_check_metadata")
        meta = _metadata(preview_path)
        if meta.get(page_preview_object.PREVIEW_RENDER_VARIANT_KEY) != page_preview_object.PREVIEW_RENDER_VARIANT_DETAIL:
            raise AssertionError("ページ/コマ用のページ一覧プレビュー画像として保存されていません")
        _mark("page_file_check_work_info_pixels")
        magenta = _count_pixels(preview_path, lambda px: px[3] > 180 and px[0] > 180 and px[1] < 90 and px[2] > 180)
        if magenta <= 0:
            raise AssertionError("ページ一覧プレビュー画像に作品情報が表示されていません")
        _mark("page_file_check_fill_pixels")
        green = _count_pixels(preview_path, lambda px: px[3] > 180 and px[0] < 90 and px[1] > 160 and px[2] < 90)
        if green <= 0:
            raise AssertionError("ページ一覧プレビュー画像にセーフライン外の塗りが表示されていません")

        _mark("page_file_regenerate_guides_only")
        work.safe_area_overlay.enabled = False
        page_preview_object.sync_page_previews(bpy.context, work, force=True)
        _mark("page_file_check_guide_pixels")
        _assert_safe_guide_pixel(preview_path, work, spread_page)
        work.safe_area_overlay.enabled = True
        page_preview_object.sync_page_previews(bpy.context, work, force=True)

        _mark("page_file_toggle_work_info")
        scene.bmanga_page_work_info_visible = False
        magenta = _count_pixels(preview_path, lambda px: px[3] > 180 and px[0] > 180 and px[1] < 90 and px[2] > 180)
        if magenta > 0:
            raise AssertionError("作品情報OFFでもページ一覧プレビュー画像に作品情報が残っています")
        scene.bmanga_page_work_info_visible = True

        _mark("page_file_toggle_guides")
        scene.bmanga_page_guides_visible = False
        green = _count_pixels(preview_path, lambda px: px[3] > 180 and px[0] < 90 and px[1] > 160 and px[2] < 90)
        if green > 0:
            raise AssertionError("用紙ガイドOFFでもページ一覧プレビュー画像にセーフライン外の塗りが残っています")
        _assert_no_safe_guide_pixel(preview_path, work, spread_page)
        scene.bmanga_page_guides_visible = True

        _mark("page_file_check_border_objects")
        if not any(owner.startswith("p0001:") for owner in _visible_border_owner_ids()):
            raise AssertionError("ページファイルを開いた直後にコマ枠線が表示されていません")
        _assert_current_page_runtime_aligned(work)

        work.pages[0].active_coma_index = 0
        _mark("enter_coma_mode")
        result = bpy.ops.bmanga.enter_coma_mode("EXEC_DEFAULT")
        if result != {"FINISHED"}:
            raise AssertionError(f"コマファイルを開けません: {result}")
        _mark("coma_file_assertions")
        scene = bpy.context.scene
        camera = scene.camera
        if camera is None or getattr(camera, "type", "") != "CAMERA":
            raise AssertionError("コマファイルのカメラが見つかりません")
        cam_data = camera.data
        if bool(getattr(cam_data, "show_passepartout", False)):
            raise AssertionError("コマファイルのカメラで外枠がオンになっています")
        if abs(float(getattr(cam_data, "passepartout_alpha", 0.0) or 0.0)) > 0.001:
            raise AssertionError("コマファイルのカメラ外枠の濃さが残っています")
        page_backgrounds = [
            bg
            for bg in getattr(cam_data, "background_images", [])
            if getattr(bg, "image", None) is not None
            and str(bg.image.get("bmanga_kind", "") or "") == "name"
        ]
        if not page_backgrounds:
            raise AssertionError("コマファイルのページ一覧プレビュー下絵が作られていません")
        work = scene.bmanga_work
        spread_page = work.pages[1]
        spread_page_id = str(getattr(spread_page, "id", "") or spread_page_id)
        preview_path = Path(str(work.work_dir)) / spread_page_id / "page_preview.png"
        _assert_preview_image_is_spread(preview_path, work, spread_page)
        from bmanga_dev_page_coma_preview_restore.utils import coma_camera

        scene.bmanga_overview_cols = 1
        scene.bmanga_overview_gap_y_mm = 42.0
        coma_camera.refresh_coma_page_overview(bpy.context)
        _assert_spread_overview_background_aligned(scene, work, spread_page_id)
        _mark("coma_file_toggle_guides")
        scene.bmanga_page_guides_visible = False
        green = _count_pixels(preview_path, lambda px: px[3] > 180 and px[0] < 90 and px[1] > 160 and px[2] < 90)
        if green > 0:
            raise AssertionError("コマファイルの用紙ガイドOFF後もページ一覧下絵に塗りが残っています")
        _assert_no_safe_guide_pixel(preview_path, work, spread_page)
        _mark("coma_file_toggle_work_info")
        scene.bmanga_page_work_info_visible = False
        magenta = _count_pixels(preview_path, lambda px: px[3] > 180 and px[0] > 180 and px[1] < 90 and px[2] > 180)
        if magenta > 0:
            raise AssertionError("コマファイルの作品情報OFF後もページ一覧下絵に作品情報が残っています")

        print(f"BMANGA_PAGE_COMA_PREVIEW_RESTORE_OK work={work_dir}", flush=True)
        _mark("done")
        success = True
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)
        os._exit(0 if success else 1)


if __name__ == "__main__":
    main()
