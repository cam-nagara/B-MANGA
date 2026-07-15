"""Blender UI実機用: コマファイルのB-MANGAオーバーレイ表示分離を確認."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import traceback
from pathlib import Path

import bpy
from bpy.app.handlers import persistent


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_coma_overlay_visibility"
OUT_DIR = ROOT / ".codex" / "visual" / "coma_overlay_visibility"
WORK_DIR = OUT_DIR / "ComaOverlayVisibility.bmanga"
SCREENSHOT = OUT_DIR / "fisheye_overlay_follow.png"
GUIDE_SCREENSHOT = OUT_DIR / "paper_guide_front.png"
SUMMARY = OUT_DIR / "summary.json"
STAGE = OUT_DIR / "stage.txt"


def _mark(stage: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STAGE.write_text(stage, encoding="utf-8")


def _fail(stage: str, exc: BaseException) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "ok": False,
        "stage": stage,
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc(),
        "screenshot": str(SCREENSHOT),
        "screenshot_exists": SCREENSHOT.is_file(),
        "guide_screenshot": str(GUIDE_SCREENSHOT),
        "guide_screenshot_exists": GUIDE_SCREENSHOT.is_file(),
    }
    SUMMARY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BMANGA_COMA_OVERLAY_VISIBILITY_ERROR", json.dumps(data, ensure_ascii=False), flush=True)
    os._exit(1)


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


def _camera_backgrounds(scene) -> list:
    camera = getattr(scene, "camera", None)
    cam_data = getattr(camera, "data", None)
    return list(getattr(cam_data, "background_images", []) or []) if cam_data is not None else []


def _kind(bg) -> str:
    image = getattr(bg, "image", None)
    if image is None:
        return ""
    try:
        return str(image.get("bmanga_kind", "") or "")
    except Exception:
        return ""


def _visible_counts(scene) -> dict[str, int]:
    counts = {"name": 0, "own_page": 0, "koma": 0}
    for bg in _camera_backgrounds(scene):
        kind = _kind(bg)
        if kind in counts and bool(getattr(bg, "show_background_image", False)):
            counts[kind] += 1
    return counts


def _display_depths(scene, kind_name: str) -> list[str]:
    depths = []
    for bg in _camera_backgrounds(scene):
        if _kind(bg) == kind_name and bool(getattr(bg, "show_background_image", False)):
            depths.append(str(getattr(bg, "display_depth", "") or ""))
    return depths


def _assert_visible(scene, label: str, *, own: bool, koma: bool, name: bool) -> dict[str, int]:
    counts = _visible_counts(scene)
    checks = {
        "own_page": (counts["own_page"] > 0) == own,
        "koma": (counts["koma"] > 0) == koma,
        "name": (counts["name"] > 0) == name,
    }
    if not all(checks.values()):
        raise AssertionError(f"{label}: 表示状態が不正です counts={counts} expected own={own} koma={koma} name={name}")
    return counts


def _assert_koma_overlay_is_content_only(scene) -> dict[str, float | str]:
    """The current-coma overlay must not be an opaque stale 3D page preview."""
    from PIL import Image

    backgrounds = [bg for bg in _camera_backgrounds(scene) if _kind(bg) == "koma"]
    if len(backgrounds) != 1:
        raise AssertionError(f"コマ内レイヤー背景は1件必要です: {len(backgrounds)}")
    image = getattr(backgrounds[0], "image", None)
    path = Path(bpy.path.abspath(str(getattr(image, "filepath", "") or "")))
    if not path.is_file():
        raise AssertionError(f"コマ内レイヤー背景PNGがありません: {path}")
    with Image.open(path) as opened:
        alpha = opened.convert("RGBA").getchannel("A")
        histogram = alpha.histogram()
        total = max(1, alpha.width * alpha.height)
        nonzero_ratio = sum(histogram[1:]) / total
        opaque_ratio = histogram[255] / total
    if not 0.0 < nonzero_ratio < 0.5:
        raise AssertionError(
            f"コマ内レイヤー背景が透明な作品要素だけではありません: {nonzero_ratio:.6f}"
        )
    return {
        "path": str(path),
        "nonzero_alpha_ratio": nonzero_ratio,
        "opaque_alpha_ratio": opaque_ratio,
    }


def _first_view3d():
    for window in bpy.context.window_manager.windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            space = area.spaces.active
            rv3d = getattr(space, "region_3d", None)
            if region is not None and rv3d is not None:
                return window, screen, area, region, space, rv3d
    raise AssertionError("3Dビューが見つかりません")


def _redraw(iterations: int = 4) -> None:
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=iterations)
    except Exception:
        pass


def _capture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _redraw(4)
    result = bpy.ops.screen.screenshot("EXEC_DEFAULT", filepath=str(path), check_existing=False)
    if "FINISHED" not in result:
        raise AssertionError(f"スクリーンショット保存に失敗しました: {result}")


def _exercise_visibility(scene, coma_camera) -> dict[str, dict[str, int]]:
    settings = scene.bmanga_coma_camera_settings
    settings.own_page_visible = True
    settings.koma_visible = True
    settings.name_visible = True
    scene.bmanga_page_preview_enabled = True
    scene.bmanga_coma_content_visible = True
    scene.bmanga_overlay_enabled = True
    coma_camera.apply_coma_overlay_background_visibility(bpy.context, scene=scene)
    states = {"all_on": _assert_visible(scene, "全表示", own=True, koma=True, name=True)}

    scene.bmanga_page_preview_enabled = False
    coma_camera.refresh_coma_page_overview(bpy.context)
    states["page_list_off"] = _assert_visible(scene, "ページ一覧OFF", own=True, koma=True, name=False)

    scene.bmanga_page_preview_enabled = True
    settings.own_page_visible = False
    coma_camera.apply_coma_overlay_background_visibility(bpy.context, scene=scene)
    states["page_image_off"] = _assert_visible(scene, "ページ画像OFF", own=False, koma=True, name=True)

    settings.own_page_visible = True
    scene.bmanga_coma_content_visible = False
    coma_camera.apply_coma_overlay_background_visibility(bpy.context, scene=scene)
    states["coma_content_off"] = _assert_visible(scene, "コマ内レイヤーOFF", own=True, koma=False, name=True)

    scene.bmanga_coma_content_visible = True
    scene.bmanga_overlay_enabled = False
    coma_camera.apply_coma_overlay_background_visibility(bpy.context, scene=scene)
    states["bmanga_overlay_off"] = _assert_visible(scene, "B-MANGAオーバーレイOFF", own=False, koma=False, name=False)

    scene.bmanga_overlay_enabled = True
    coma_camera.apply_coma_overlay_background_visibility(bpy.context, scene=scene)
    states["bmanga_overlay_on_again"] = _assert_visible(scene, "B-MANGAオーバーレイ再ON", own=True, koma=True, name=True)
    name_depths = _display_depths(scene, "name")
    if not name_depths or not all(depth == "FRONT" for depth in name_depths):
        raise AssertionError(f"ページ一覧が前面になっていません: {name_depths}")
    depths_front = _display_depths(scene, "koma")
    own_depths_front = _display_depths(scene, "own_page")
    settings.koma_depth = True
    coma_camera.apply_coma_overlay_background_visibility(bpy.context, scene=scene)
    depths_back = _display_depths(scene, "koma")
    own_depths_back = _display_depths(scene, "own_page")
    settings.koma_depth = False
    coma_camera.apply_coma_overlay_background_visibility(bpy.context, scene=scene)
    depths_front_again = _display_depths(scene, "koma")
    own_depths_front_again = _display_depths(scene, "own_page")
    if not depths_front or not all(depth == "FRONT" for depth in depths_front):
        raise AssertionError(f"コマ内レイヤーが既定で前面になっていません: {depths_front}")
    if not own_depths_front or not all(depth == "FRONT" for depth in own_depths_front):
        raise AssertionError(f"ページ画像が既定で前面になっていません: {own_depths_front}")
    if not depths_back or not all(depth == "BACK" for depth in depths_back):
        raise AssertionError(f"コマを後ろにするONでコマ内レイヤーが背面になっていません: {depths_back}")
    if not own_depths_back or not all(depth == "BACK" for depth in own_depths_back):
        raise AssertionError(f"コマを後ろにするONでページ画像が背面になっていません: {own_depths_back}")
    if not depths_front_again or not all(depth == "FRONT" for depth in depths_front_again):
        raise AssertionError(f"コマを後ろにするOFFでコマ内レイヤーが前面へ戻っていません: {depths_front_again}")
    if not own_depths_front_again or not all(depth == "FRONT" for depth in own_depths_front_again):
        raise AssertionError(f"コマを後ろにするOFFでページ画像が前面へ戻っていません: {own_depths_front_again}")
    states["koma_depth"] = {
        "name_front": len(name_depths),
        "own_page_front": len(own_depths_front),
        "own_page_back": len(own_depths_back),
        "own_page_front_again": len(own_depths_front_again),
        "front": len(depths_front),
        "back": len(depths_back),
        "front_again": len(depths_front_again),
    }
    return states


def _exercise_fisheye(scene, coma_camera, fisheye_overlay) -> dict:
    scene.bmanga_coma_camera_fisheye_layout_mode = True
    coma_camera.apply_fisheye_mode(bpy.context)
    coma_camera.view_camera_in_viewports(bpy.context)
    window, screen, area, region, space, rv3d = _first_view3d()
    with bpy.context.temp_override(
        window=window,
        screen=screen,
        area=area,
        region=region,
        space_data=space,
        region_data=rv3d,
    ):
        space.overlay.show_overlays = True
        space.show_gizmo = True
        rv3d.view_perspective = "CAMERA"
        rv3d.view_camera_zoom = -35.0
        rv3d.view_camera_offset = (0.22, 0.0)
        _redraw(4)
        rect_right = fisheye_overlay._camera_frame_pixel_rect(scene, scene.camera, region, rv3d)
        rv3d.view_camera_offset = (-0.22, 0.0)
        _redraw(4)
        rect_left = fisheye_overlay._camera_frame_pixel_rect(scene, scene.camera, region, rv3d)
        _capture(SCREENSHOT)
    if rect_right is None or rect_left is None:
        raise AssertionError("魚眼モードのカメラ枠を画面座標へ変換できません")
    if abs(float(rect_right[0]) - float(rect_left[0])) < 5.0:
        raise AssertionError(f"魚眼モードの灰色帯がカメラビュー移動に追従していません: {rect_right} / {rect_left}")
    return {
        "rect_right_offset": [round(float(v), 2) for v in rect_right],
        "rect_left_offset": [round(float(v), 2) for v in rect_left],
        "screenshot": str(SCREENSHOT),
    }


def _find_page_and_coma(work, page_id: str, coma_id: str):
    for page in getattr(work, "pages", []) or []:
        if str(getattr(page, "id", "") or "") != str(page_id):
            continue
        for coma in getattr(page, "comas", []) or []:
            if str(getattr(coma, "coma_id", "") or "") == str(coma_id):
                return page, coma
    raise AssertionError("現在のページとコマが見つかりません")


def _expand_coma_to_canvas(work, page, coma) -> None:
    paper = work.paper
    coma.shape_type = "rect"
    coma.rect_x_mm = 0.0
    coma.rect_y_mm = 0.0
    coma.rect_width_mm = float(getattr(paper, "canvas_width_mm", 0.0) or 0.0)
    coma.rect_height_mm = float(getattr(paper, "canvas_height_mm", 0.0) or 0.0)
    try:
        page.active_coma_index = max(0, list(getattr(page, "comas", []) or []).index(coma))
    except Exception:
        pass


def _add_black_camera_plane(scene) -> None:
    camera = getattr(scene, "camera", None)
    if camera is None:
        raise AssertionError("確認用カメラがありません")
    mesh = bpy.data.meshes.new("BManga_GuideFront_BlackPlaneMesh")
    mesh.from_pydata(
        [(-3.0, -3.0, 0.0), (3.0, -3.0, 0.0), (3.0, 3.0, 0.0), (-3.0, 3.0, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    mat = bpy.data.materials.new("BManga_GuideFront_Black")
    mat.diffuse_color = (0.0, 0.0, 0.0, 1.0)
    try:
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1.0)
            bsdf.inputs["Roughness"].default_value = 1.0
    except Exception:
        pass
    obj = bpy.data.objects.new("BManga_GuideFront_BlackPlane", mesh)
    scene.collection.objects.link(obj)
    obj.parent = camera
    obj.location = (0.0, 0.0, -2.0)
    obj.rotation_euler = (0.0, 0.0, 0.0)
    obj.data.materials.append(mat)


def _own_page_screen_rect(scene, region, rv3d, overlay_labels, page_id: str):
    frame_rect = overlay_labels._camera_frame_pixel_rect(scene, region, rv3d)
    if frame_rect is None:
        raise AssertionError("カメラ枠を画面座標へ変換できません")
    for bg in _camera_backgrounds(scene):
        image = getattr(bg, "image", None)
        if image is None or not bool(getattr(bg, "show_background_image", False)):
            continue
        try:
            kind = str(image.get("bmanga_kind", "") or "")
            bg_page_id = str(image.get("bmanga_page_id", "") or "")
        except Exception:
            continue
        if kind == "own_page" and bg_page_id == str(page_id):
            rect = overlay_labels._background_screen_rect(frame_rect, bg)
            if rect is not None:
                return rect
    raise AssertionError("ページ画像の画面範囲を取得できません")


def _green_pixels_in_region(path: Path, region, rect_px) -> int:
    from PIL import Image

    with Image.open(str(path)) as opened:
        image = opened.convert("RGB")
        width, height = image.size
        x0, y0, x1, y1 = rect_px
        sx0 = max(0, int(region.x + max(0.0, min(x0, x1))))
        sx1 = min(width, int(region.x + min(float(region.width), max(x0, x1))))
        sy0 = max(0, int(height - (region.y + min(float(region.height), max(y0, y1)))))
        sy1 = min(height, int(height - (region.y + max(0.0, min(y0, y1)))))
        green = 0
        for py in range(sy0, sy1):
            for px in range(sx0, sx1):
                r, g, b = image.getpixel((px, py))
                if g > 150 and r < 100 and b < 100:
                    green += 1
    return green


def _exercise_paper_guide_front(scene, work, page_id: str, coma_id: str, coma_camera, overlay_labels) -> dict:
    page, coma = _find_page_and_coma(work, page_id, coma_id)
    _expand_coma_to_canvas(work, page, coma)
    overlay = work.safe_area_overlay
    overlay.enabled = True
    overlay.opacity = 100.0
    overlay.color = (0.0, 1.0, 0.0)
    overlay.bleed_outer_enabled = False
    paper = work.paper
    paper.show_guides = True
    paper.show_safe_line = True
    scene.bmanga_page_guides_visible = True
    scene.bmanga_overlay_enabled = True
    scene.bmanga_page_preview_enabled = True
    scene.bmanga_coma_content_visible = True
    settings = scene.bmanga_coma_camera_settings
    settings.own_page_visible = True
    settings.name_visible = True
    settings.koma_visible = True
    settings.own_page_opacity = 0.0
    settings.koma_depth = False
    coma_camera.ensure_coma_camera_scene(
        bpy.context,
        work=work,
        page_id=page_id,
        coma_id=coma_id,
        generate_references=True,
    )
    coma_camera.refresh_coma_page_overview(bpy.context)
    coma_camera.apply_coma_overlay_background_visibility(bpy.context, scene=scene)
    _add_black_camera_plane(scene)
    coma_camera.view_camera_in_viewports(bpy.context)
    window, screen, area, region, space, rv3d = _first_view3d()
    with bpy.context.temp_override(
        window=window,
        screen=screen,
        area=area,
        region=region,
        space_data=space,
        region_data=rv3d,
    ):
        space.overlay.show_overlays = True
        space.show_gizmo = False
        try:
            space.shading.type = "MATERIAL"
            space.shading.color_type = "MATERIAL"
        except Exception:
            pass
        rv3d.view_perspective = "CAMERA"
        rv3d.view_camera_zoom = -35.0
        rv3d.view_camera_offset = (0.0, 0.0)
        _redraw(6)
        page_rect = _own_page_screen_rect(scene, region, rv3d, overlay_labels, page_id)
        _capture(GUIDE_SCREENSHOT)
    green = _green_pixels_in_region(GUIDE_SCREENSHOT, region, page_rect)
    if green < 100:
        raise AssertionError(f"用紙ガイドがコマファイルのオブジェクトより前面に表示されていません: green={green}")
    return {
        "green_pixels": green,
        "page_rect": [round(float(v), 2) for v in page_rect],
        "screenshot": str(GUIDE_SCREENSHOT),
    }


def _after_coma_open() -> None:
    try:
        _mark("after_coma_open")
        scene = bpy.context.scene
        work = scene.bmanga_work
        from bmanga_dev_coma_overlay_visibility.utils import coma_camera, page_file_scene
        from bmanga_dev_coma_overlay_visibility.ui import coma_fisheye_overlay, overlay_coma_page_labels

        role, page_id, coma_id = page_file_scene.current_role(bpy.context)
        if role != page_file_scene.ROLE_COMA:
            raise AssertionError(f"コマファイルではありません: {role}")
        scene.bmanga_page_preview_enabled = True
        scene.bmanga_page_preview_range_mode = "ALL"
        scene.bmanga_overview_cols = 3
        settings = scene.bmanga_coma_camera_settings
        settings.name_bg_images_opacity = 100.0
        settings.own_page_opacity = 100.0
        settings.koma_bg_images_opacity = 100.0
        settings.bg_images_scale = 1.0
        settings.name_visible = True
        settings.own_page_visible = True
        settings.koma_visible = True
        coma_camera.ensure_coma_camera_scene(
            bpy.context,
            work=work,
            page_id=page_id,
            coma_id=coma_id,
            generate_references=True,
        )
        coma_camera.refresh_coma_page_overview(bpy.context)
        coma_camera.apply_coma_overlay_background_visibility(bpy.context, scene=scene)
        if not any(_kind(bg) == "koma" for bg in _camera_backgrounds(scene)):
            raise AssertionError("本番経路がコマ内レイヤー背景を生成していません")
        content_only = _assert_koma_overlay_is_content_only(scene)
        states = _exercise_visibility(scene, coma_camera)
        guide = _exercise_paper_guide_front(
            scene, work, page_id, coma_id, coma_camera, overlay_coma_page_labels,
        )
        fisheye = _exercise_fisheye(scene, coma_camera, coma_fisheye_overlay)
        data = {
            "ok": True,
            "work": str(WORK_DIR),
            "role": role,
            "page_id": page_id,
            "coma_id": coma_id,
            "content_only": content_only,
            "states": states,
            "guide": guide,
            "fisheye": fisheye,
            "screenshot_exists": SCREENSHOT.is_file(),
            "guide_screenshot_exists": GUIDE_SCREENSHOT.is_file(),
        }
        SUMMARY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("BMANGA_COMA_OVERLAY_VISIBILITY_OK", json.dumps(data, ensure_ascii=False), flush=True)
        os._exit(0)
    except BaseException as exc:  # noqa: BLE001
        stage = STAGE.read_text(encoding="utf-8") if STAGE.is_file() else "after_coma_open"
        _fail(stage, exc)


def _run() -> None:
    if bpy.app.background:
        raise RuntimeError("このチェックは --background なしで実行してください")
    _mark("start")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    _load_addon()
    _mark("work_new")
    result = bpy.ops.bmanga.work_new(filepath=str(WORK_DIR))
    if result != {"FINISHED"}:
        raise AssertionError(f"作品作成に失敗しました: {result}")
    for _ in range(3):
        result = bpy.ops.bmanga.page_add()
        if result != {"FINISHED"}:
            raise AssertionError(f"ページ追加に失敗しました: {result}")
    work = bpy.context.scene.bmanga_work
    work.active_page_index = 1
    work.pages[1].active_coma_index = 0
    bpy.context.scene.bmanga_page_preview_range_mode = "ALL"
    result = bpy.ops.bmanga.work_save()
    if result != {"FINISHED"}:
        raise AssertionError(f"作品保存に失敗しました: {result}")
    _mark("enter_coma")
    if _after_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_after_load_post)
    result = bpy.ops.bmanga.enter_coma_mode("EXEC_DEFAULT")
    if result != {"FINISHED"}:
        raise AssertionError(f"コマファイルを開けません: {result}")


@persistent
def _after_load_post(_dummy=None) -> None:
    try:
        bpy.app.handlers.load_post.remove(_after_load_post)
    except ValueError:
        pass
    bpy.app.timers.register(_after_coma_open, first_interval=1.0)


def _timer():
    try:
        _run()
    except BaseException as exc:  # noqa: BLE001
        stage = STAGE.read_text(encoding="utf-8") if STAGE.is_file() else "timer"
        _fail(stage, exc)
    return None


bpy.app.timers.register(_timer, first_interval=0.5)
