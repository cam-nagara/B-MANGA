"""コマのレンダーカメラサムネイル/高品質プレビュー生成 Operator.

計画書 3.4.3 / 8.8 参照。コマ編集モード終了時に cNN_thumb.png を
カメラ基準の「レンダーモード表示」のコマ領域切り出しで更新。
ユーザー手動で cNN_preview.png を高解像度プレビュー画像として生成。
まずカメラから通常レンダーを出し、透明背景のアルファを保持する。
通常レンダーが失敗した場合だけ、画面撮影や OpenGL 撮影へ戻す。
"""

from __future__ import annotations

from pathlib import Path
import math
import tempfile

import bpy
from bpy.types import Operator

from ..core.mode import MODE_COMA, get_mode
from ..core.work import get_active_page, get_work
from ..utils import image_transparency, log, paths

_logger = log.get_logger(__name__)


def _is_coma_mode(context) -> bool:
    return get_mode(context) == MODE_COMA


def take_area_screenshot(context, out_path: Path) -> bool:
    """選択コマのレンダーカメラ画像をページ座標で切り出して保存する.

    UI込みの VIEW_3D スクリーンショットでもソリッド(OpenGL)でもなく、
    カメラからシーンのレンダーエンジンでレンダリングした画像を出し、
    対象コマbboxだけを切る。これによりビューポート操作状態に依存せず、
    紙面座標と一致し、見た目はレンダー結果になる。
    """
    work = get_work(context)
    return render_coma_camera_crop(
        context,
        out_path,
        resolution_percentage=100,
        output_scale_percentage=_page_preview_scale_percentage(work),
    )


def render_coma_camera_crop(
    context,
    out_path: Path,
    *,
    resolution_percentage: int = 100,
    output_scale_percentage: float | None = None,
) -> bool:
    if not _is_coma_mode(context):
        return False
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return False
    page, entry = _resolve_coma_entry(context, work)
    if page is None or entry is None:
        return False
    if not getattr(work, "work_dir", ""):
        return False
    scene = getattr(context, "scene", None)
    if scene is None:
        return False
    prev_filepath = scene.render.filepath
    prev_res_x = int(scene.render.resolution_x)
    prev_res_y = int(scene.render.resolution_y)
    prev_percent = int(scene.render.resolution_percentage)
    prev_engine = scene.render.engine
    prev_format = scene.render.image_settings.file_format
    prev_color_mode = scene.render.image_settings.color_mode
    prev_film_transparent = bool(getattr(scene.render, "film_transparent", False))
    prev_use_border = bool(getattr(scene.render, "use_border", False))
    prev_use_crop = bool(getattr(scene.render, "use_crop_to_border", False))
    prev_border = (
        float(getattr(scene.render, "border_min_x", 0.0)),
        float(getattr(scene.render, "border_max_x", 1.0)),
        float(getattr(scene.render, "border_min_y", 0.0)),
        float(getattr(scene.render, "border_max_y", 1.0)),
    )
    try:
        from ..utils import coma_camera

        coma_camera.ensure_coma_camera_scene(
            context,
            work=work,
            page_id=str(getattr(page, "id", "") or ""),
            coma_id=str(getattr(entry, "coma_id", "") or ""),
            generate_references=False,
        )
    except Exception:  # noqa: BLE001
        _logger.exception("panel camera setup failed before preview render")
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    bg_state = None
    try:
        from ..io import export_pipeline
        from ..utils import coma_camera

        if not export_pipeline.has_pillow():
            return False
        bg_state = coma_camera.capture_managed_background_visibility(context)
        coma_camera.set_managed_background_visibility(context, False)
        with tempfile.TemporaryDirectory() as td:
            full_path = Path(td) / "coma_full.png"
            scene.render.filepath = str(full_path)
            scene.render.image_settings.file_format = "PNG"
            scene.render.image_settings.color_mode = "RGBA"
            scene.render.resolution_percentage = max(1, min(100, int(resolution_percentage)))
            scene.render.film_transparent = image_transparency.coma_background_is_transparent(entry)
            scene.render.use_border = False
            if hasattr(scene.render, "use_crop_to_border"):
                scene.render.use_crop_to_border = False
            if not _render_camera_image(context, scene):
                return False
            source = _resolve_render_output_path(full_path)
            if source is None:
                return False
            if not _crop_render_to_panel(
                source,
                out_path,
                work,
                page,
                entry,
                output_scale_percentage=output_scale_percentage,
            ):
                return False
            return True
    except Exception as exc:  # noqa: BLE001
        _logger.warning("panel camera crop render failed: %s", exc, exc_info=True)
        return False
    finally:
        if bg_state is not None:
            try:
                from ..utils import coma_camera

                coma_camera.restore_background_visibility(bg_state)
            except Exception:  # noqa: BLE001
                pass
        scene.render.filepath = prev_filepath
        scene.render.resolution_x = prev_res_x
        scene.render.resolution_y = prev_res_y
        scene.render.resolution_percentage = prev_percent
        scene.render.engine = prev_engine
        scene.render.image_settings.file_format = prev_format
        scene.render.image_settings.color_mode = prev_color_mode
        scene.render.film_transparent = prev_film_transparent
        scene.render.use_border = prev_use_border
        if hasattr(scene.render, "use_crop_to_border"):
            scene.render.use_crop_to_border = prev_use_crop
        scene.render.border_min_x = prev_border[0]
        scene.render.border_max_x = prev_border[1]
        scene.render.border_min_y = prev_border[2]
        scene.render.border_max_y = prev_border[3]


def _find_preview_view3d(context):
    """プレビュー撮影に使う VIEW_3D エリアを 1 つ返す.

    Returns ``(window, area, space, region)``。見つからなければ area=None。
    """
    win = getattr(context, "window", None)
    if win is None:
        wm = getattr(context, "window_manager", None)
        windows = list(getattr(wm, "windows", []) or [])
        win = windows[0] if windows else None
    screen = getattr(win, "screen", None)
    if win is None or screen is None:
        return win, None, None, None
    best = None
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        if best is None or area.width * area.height > best.width * best.height:
            best = area
    if best is None:
        return win, None, None, None
    space = best.spaces.active
    region = next((r for r in best.regions if r.type == "WINDOW"), None)
    return win, best, space, region


def _capture_screen_camera_frame(context, scene, target: Path) -> bool:
    """画面に表示中のコマ 3D ビューをそのまま撮影し、カメラ枠で切り出す.

    最終レンダーは行わない。コマ編集モードの 3D ビューは既にレンダーモード
    表示 (Cycles 等) で画面に描かれているので、その画面ピクセルを
    ``screen.screenshot`` で取得し、カメラ枠 (出力範囲) の矩形だけを切り出す
    ことで「画面で見えているレンダーの見た目」をそのままプレビューにする。
    魚眼/カラー/ラインも画面に出ているものがそのまま反映される。
    """
    win, area, space, region = _find_preview_view3d(context)
    if win is None or area is None or space is None or region is None:
        return False
    r3d = getattr(space, "region_3d", None)
    cam = getattr(scene, "camera", None)
    if r3d is None or cam is None:
        return False
    if getattr(r3d, "view_perspective", "") != "CAMERA":
        return False
    overlay = space.overlay
    # サイドパネル / ツールバー / ヘッダーは「重なり表示」が既定なので、
    # 一時的に隠しても 3D WINDOW 領域はリサイズされず、Cycles 表示は
    # 収束したまま再計算されない。これでカメラ枠がパネルに重なっていても
    # パネルを画面に焼き込まずクリーンに撮れる (撮影後に元へ戻す)。
    _region_flags = (
        "show_region_ui",
        "show_region_toolbar",
        "show_region_header",
        "show_region_tool_header",
        "show_region_asset_shelf",
    )
    prev_state = {"overlays": getattr(overlay, "show_overlays", True),
                  "gizmo": getattr(space, "show_gizmo", True)}
    for _flag in _region_flags:
        if hasattr(space, _flag):
            prev_state[_flag] = getattr(space, _flag)
    from bpy_extras.view3d_utils import location_3d_to_region_2d

    import tempfile as _tempfile

    # 一時フォルダはコンテキストマネージャで必ず後始末する
    # (フルウィンドウ PNG が毎回のプレビュー生成で溜まらないように)。
    with _tempfile.TemporaryDirectory() as _td:
        shot = Path(_td) / "screen.png"
        geom: dict = {}
        try:
            try:
                overlay.show_overlays = False
                space.show_gizmo = False
                for _flag in _region_flags:
                    if hasattr(space, _flag):
                        setattr(space, _flag, False)
            except Exception:  # noqa: BLE001
                pass
            with context.temp_override(window=win, area=area, region=region):
                # パネル非表示 / オーバーレイ非表示を画面へ反映してから
                # キャプチャする (これらの切替は UI のみで、重なり表示なら
                # 3D 領域はリサイズされず Cycles 表示は再計算されない)。
                try:
                    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
                except Exception:  # noqa: BLE001
                    pass
                # カメラ枠 (出力範囲) と region/window 座標は、パネル非表示
                # の状態 (= スクショと同じ状態) で確定させる。view_frame の
                # 投影はカメラ視点のとき魚眼/パノラマでも出力矩形と一致する。
                try:
                    mat = cam.matrix_world.normalized()
                    corners = cam.data.view_frame(scene=scene)
                    pts = [
                        location_3d_to_region_2d(region, r3d, mat @ v)
                        for v in corners
                    ]
                    if not any(p is None for p in pts):
                        xs = [p.x for p in pts]
                        ys = [p.y for p in pts]
                        geom = {
                            "bx1": min(xs), "by1": min(ys),
                            "bx2": max(xs), "by2": max(ys),
                            "rx": region.x, "ry": region.y,
                            "ww": int(win.width), "wh": int(win.height),
                        }
                except Exception:  # noqa: BLE001
                    geom = {}
                try:
                    bpy.ops.screen.screenshot(filepath=str(shot))
                except Exception as exc:  # noqa: BLE001
                    _logger.warning(
                        "screen.screenshot failed: %s", exc, exc_info=True
                    )
                    return False
        finally:
            try:
                overlay.show_overlays = prev_state["overlays"]
                space.show_gizmo = prev_state["gizmo"]
                for _flag in _region_flags:
                    if _flag in prev_state:
                        setattr(space, _flag, prev_state[_flag])
            except Exception:  # noqa: BLE001
                pass
        if not shot.is_file() or not geom:
            return False

        bx1, by1, bx2, by2 = geom["bx1"], geom["by1"], geom["bx2"], geom["by2"]

        from ..io import export_pipeline

        Image = export_pipeline.Image
        if Image is None:
            return False
        try:
            with Image.open(str(shot)) as opened:
                img = opened.convert("RGBA")
        except Exception:  # noqa: BLE001
            return False
        sx = img.width / max(1, geom["ww"])
        sy = img.height / max(1, geom["wh"])
        # region 座標 (原点=左下) → ウィンドウ → 画像座標 (原点=左上)
        wx1 = geom["rx"] + bx1
        wx2 = geom["rx"] + bx2
        wy1 = geom["ry"] + by1
        wy2 = geom["ry"] + by2
        ix1 = int(round(wx1 * sx))
        ix2 = int(round(wx2 * sx))
        iy1 = int(round((geom["wh"] - wy2) * sy))
        iy2 = int(round((geom["wh"] - wy1) * sy))
        ix1 = max(0, min(img.width - 1, ix1))
        ix2 = max(ix1 + 1, min(img.width, ix2))
        iy1 = max(0, min(img.height - 1, iy1))
        iy2 = max(iy1 + 1, min(img.height, iy2))
        try:
            img.crop((ix1, iy1, ix2, iy2)).save(str(target))
        except Exception:  # noqa: BLE001
            return False
        return target.is_file()


def _render_camera_image(context, scene) -> bool:
    """コマプレビュー用の「カメラ枠の画像」を ``scene.render.filepath`` に書く.

    方針: まずカメラから通常レンダーを書き出し、透明背景のアルファを
    保持する。失敗した場合だけ、画面撮影や OpenGL 撮影へフォールバック
    する。
    """
    target = Path(bpy.path.abspath(scene.render.filepath))

    def _written() -> bool:
        return _resolve_render_output_path(target) is not None

    try:
        with context.temp_override(scene=scene):
            bpy.ops.render.render(write_still=True)
        if _written():
            return True
    except Exception as exc:  # noqa: BLE001
        _logger.warning("camera render failed: %s", exc, exc_info=True)

    try:
        if _capture_screen_camera_frame(context, scene, target):
            return True
    except Exception as exc:  # noqa: BLE001
        _logger.warning("screen camera-frame capture failed: %s", exc, exc_info=True)

    # 異常時のみ: カメラ基準のソリッド OpenGL 撮影 (見た目は簡易)。
    try:
        with context.temp_override(scene=scene):
            bpy.ops.render.opengl(write_still=True, view_context=False)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("solid opengl fallback failed: %s", exc, exc_info=True)
        return False
    return _written()


def _resolve_coma_entry(context, work):
    scene = getattr(context, "scene", None)
    page_id = str(getattr(scene, "bname_current_coma_page_id", "") or "") if scene else ""
    stem = str(getattr(scene, "bname_current_coma_id", "") or "") if scene else ""
    if page_id and stem:
        for page in getattr(work, "pages", []):
            if str(getattr(page, "id", "") or "") != page_id:
                continue
            for entry in getattr(page, "comas", []):
                if str(getattr(entry, "coma_id", "") or "") == stem:
                    return page, entry
    page = get_active_page(context)
    if page is None:
        return None, None
    idx = int(getattr(page, "active_coma_index", -1))
    if not (0 <= idx < len(page.comas)):
        return None, None
    return page, page.comas[idx]


def _resolve_render_output_path(path: Path) -> Path | None:
    if path.is_file():
        return path
    matches = sorted(path.parent.glob(f"{path.stem}*.png"))
    return matches[-1] if matches else None


def _page_preview_scale_percentage(work) -> float:
    if work is None:
        return 10.0
    try:
        value = float(getattr(work, "page_preview_scale_percentage", 10.0) or 10.0)
    except (TypeError, ValueError):
        value = 10.0
    return max(1.0, min(100.0, value))


def _resize_for_page_preview(image, percentage: float | None):
    if image is None or percentage is None:
        return image
    try:
        scale = max(1.0, min(100.0, float(percentage))) / 100.0
    except (TypeError, ValueError):
        scale = 0.1
    if scale >= 0.9999:
        return image
    width = max(1, int(round(image.width * scale)))
    height = max(1, int(round(image.height * scale)))
    try:
        from PIL import Image as PILImage

        resampling = getattr(PILImage, "Resampling", PILImage)
        resample = getattr(resampling, "LANCZOS", 1)
    except Exception:  # noqa: BLE001
        resample = 1
    return image.resize((width, height), resample=resample)


def _crop_render_to_panel(
    source: Path,
    out_path: Path,
    work,
    page,
    entry,
    *,
    output_scale_percentage: float | None = None,
) -> bool:
    from ..io import export_pipeline

    Image = export_pipeline.Image
    if Image is None:
        return False
    bbox = _coma_bbox_on_camera_page(work, page, entry)
    if bbox is None:
        return False
    try:
        with Image.open(str(source)) as opened:
            image = opened.convert("RGBA")
    except Exception:  # noqa: BLE001
        return False
    page_width = max(0.001, float(getattr(work.paper, "canvas_width_mm", 0.0) or 0.0))
    page_height = max(0.001, float(getattr(work.paper, "canvas_height_mm", 0.0) or 0.0))
    min_x, min_y, max_x, max_y = bbox
    px_per_mm_x = image.width / page_width
    px_per_mm_y = image.height / page_height
    left = int(math.floor(min_x * px_per_mm_x))
    right = int(math.ceil(max_x * px_per_mm_x))
    top = image.height - int(math.ceil(max_y * px_per_mm_y))
    bottom = image.height - int(math.floor(min_y * px_per_mm_y))
    left = max(0, min(image.width - 1, left))
    right = max(left + 1, min(image.width, right))
    top = max(0, min(image.height - 1, top))
    bottom = max(top + 1, min(image.height, bottom))
    cropped = image.crop((left, top, right, bottom))
    if image_transparency.coma_background_is_transparent(entry):
        cropped = image_transparency.make_background_transparent(cropped)
    cropped = _resize_for_page_preview(cropped, output_scale_percentage)
    cropped.save(str(out_path))
    return True


def _coma_bbox_on_camera_page(work, page, entry) -> tuple[float, float, float, float] | None:
    bbox = _coma_bbox(entry)
    if bbox is None:
        return None
    page_width = float(getattr(work.paper, "canvas_width_mm", 0.0) or 0.0)
    if bool(getattr(page, "spread", False)) and page_width > 0.0:
        center_x = (bbox[0] + bbox[2]) * 0.5
        if center_x >= page_width:
            return (bbox[0] - page_width, bbox[1], bbox[2] - page_width, bbox[3])
    return bbox


def _coma_bbox(entry) -> tuple[float, float, float, float] | None:
    if getattr(entry, "shape_type", "") == "rect":
        x = float(getattr(entry, "rect_x_mm", 0.0))
        y = float(getattr(entry, "rect_y_mm", 0.0))
        w = float(getattr(entry, "rect_width_mm", 0.0))
        h = float(getattr(entry, "rect_height_mm", 0.0))
        if w <= 0.0 or h <= 0.0:
            return None
        return x, y, x + w, y + h
    verts = [(float(v.x_mm), float(v.y_mm)) for v in getattr(entry, "vertices", [])]
    if not verts:
        return None
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    return min(xs), min(ys), max(xs), max(ys)


class BNAME_OT_coma_update_thumb(Operator):
    """選択中コマのソリッドカメラサムネを生成."""

    bl_idname = "bname.coma_update_thumb"
    bl_label = "コマサムネイルを更新"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return _is_coma_mode(context) and page is not None and 0 <= page.active_coma_index < len(page.comas)

    def execute(self, context):
        work = get_work(context)
        page = get_active_page(context)
        if work is None or page is None:
            return {"CANCELLED"}
        entry = page.comas[page.active_coma_index]
        paths.validate_coma_id(entry.coma_id)
        out = paths.coma_thumb_path(Path(work.work_dir), page.id, entry.coma_id)
        if take_area_screenshot(context, out):
            self.report({"INFO"}, f"サムネイル保存: {out.name}")
            return {"FINISHED"}
        self.report({"WARNING"}, "サムネイル取得に失敗しました")
        return {"CANCELLED"}


class BNAME_OT_coma_generate_preview(Operator):
    """選択中コマのソリッドカメラ画像から高品質プレビューを生成."""

    bl_idname = "bname.coma_generate_preview"
    bl_label = "高品質プレビュー生成"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        page = get_active_page(context)
        return _is_coma_mode(context) and page is not None and 0 <= page.active_coma_index < len(page.comas)

    def execute(self, context):
        work = get_work(context)
        page = get_active_page(context)
        if work is None or page is None:
            return {"CANCELLED"}
        entry = page.comas[page.active_coma_index]
        paths.validate_coma_id(entry.coma_id)
        out = paths.coma_preview_path(Path(work.work_dir), page.id, entry.coma_id)
        out.parent.mkdir(parents=True, exist_ok=True)

        scene = context.scene
        prev_filepath = scene.render.filepath
        prev_percent = scene.render.resolution_percentage
        try:
            if not render_coma_camera_crop(
                context,
                out,
                resolution_percentage=100,
            ):
                raise RuntimeError("カメラプレビューの生成に失敗しました")
        except Exception as exc:  # noqa: BLE001
            _logger.exception("coma_generate_preview failed")
            self.report({"ERROR"}, f"プレビュー生成失敗: {exc}")
            return {"CANCELLED"}
        finally:
            scene.render.filepath = prev_filepath
            scene.render.resolution_percentage = prev_percent

        self.report({"INFO"}, f"プレビュー保存: {out.name}")
        return {"FINISHED"}


_CLASSES = (
    BNAME_OT_coma_update_thumb,
    BNAME_OT_coma_generate_preview,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
