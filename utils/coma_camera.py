"""コマ編集モード用カメラ・下絵管理ヘルパ."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

import bpy

from ..core.mode import MODE_COMA, get_mode
from ..core.work import find_page_by_id, get_work
from ..io import export_pipeline
from . import log, page_browser, paths, percentage
from .geom import mm_to_px
from .coma_camera_constants import (
    DEFAULT_CAMERA_DISTANCE,
    DEFAULT_REF_DPI,
    KOMA_REF_PREFIX,
    MANAGED_IMAGE_PROP,
    NAME_REF_PREFIX,
    PANEL_CAMERA_NAME,
    REFERENCE_DIR_NAME,
)
from .coma_camera_refs import (
    ReferenceImage,
    ensure_reference_images,
    reference_dir,
    _collect_existing_reference_images,
    _compose_page_reference_pair,
    _ensure_page_reference,
    _find_spread_mate_page,
    _has_master_gpencil,
    _is_page_left_half,
    _koma_ref_path,
    _page_ref_path,
    _coma_bbox,
    _coma_bbox_size,
    _coma_mask_is_stale,
    _coma_points_mm,
    _coma_points_px,
    _reference_frame_info,
    _reference_is_stale,
    _render_current_coma_page_mask,
    _render_page_reference,
    _resolve_coma,
    _spread_coma_side,
    _path_mtime,
)

_logger = log.get_logger(__name__)
_OPACITY_PERCENT_MIGRATION_PROP = "bmanga_coma_camera_opacity_percent_units_v1"
HATCHING_IMAGE_NAME = "ハッチング間隔.png"
HATCHING_ASSET_PATH = Path(__file__).resolve().parents[1] / "assets" / HATCHING_IMAGE_NAME


def ensure_opacity_percent_units(scene) -> None:
    """旧ファイルの下絵不透明度 0..1 値を UI の % 値へ一度だけ移行する。"""
    if scene is None:
        return
    try:
        if bool(scene.get(_OPACITY_PERCENT_MIGRATION_PROP, False)):
            return
    except Exception:  # noqa: BLE001
        return
    settings = getattr(scene, "bmanga_coma_camera_settings", None)
    if settings is None:
        return
    for attr in (
        "bg_images_opacity",
        "name_bg_images_opacity",
        "koma_bg_images_opacity",
    ):
        try:
            value = float(getattr(settings, attr))
        except Exception:  # noqa: BLE001
            continue
        if 0.0 <= value <= 1.0:
            try:
                setattr(settings, attr, value * 100.0)
            except Exception:  # noqa: BLE001
                pass
    try:
        scene[_OPACITY_PERCENT_MIGRATION_PROP] = True
    except Exception:  # noqa: BLE001
        pass


def ensure_coma_camera_scene(
    context,
    work=None,
    page_id: str = "",
    coma_id: str = "",
    *,
    generate_references: bool = False,
) -> None:
    """cNN.blend 内にカメラと表示設定を整備する."""
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    if scene is None:
        return
    if work is None:
        work = get_work(context)
    if not page_id:
        page_id = str(getattr(scene, "bmanga_current_coma_page_id", "") or "")
    if not coma_id:
        coma_id = str(getattr(scene, "bmanga_current_coma_id", "") or "")

    camera = ensure_coma_camera(scene)
    scene.camera = camera
    ensure_opacity_percent_units(scene)
    settings = getattr(scene, "bmanga_coma_camera_settings", None)
    white_bg_on = bool(getattr(scene, "bmanga_coma_white_background", False))
    if white_bg_on:
        scene.render.film_transparent = False
    elif settings is not None:
        scene.render.film_transparent = bool(getattr(settings, "white_background", True))
    # コマ用blendファイルの色管理 (ビュー変換/露出/ルック) はユーザーに
    # 委ねる。以前はここで毎回 Standard へ戻していたため、コマで設定した
    # 色管理が開く/閉じるたびに失われていた。
    configure_render_for_current_coma(scene, work, page_id, coma_id)
    capture_camera_runtime_settings(context, prefer_camera_fisheye=False)
    if white_bg_on:
        apply_white_world_background(scene)
    else:
        sync_world_background_color(context, work=work, page_id=page_id, coma_id=coma_id)

    refs: list[ReferenceImage] = []
    if generate_references and work is not None and getattr(work, "work_dir", ""):
        refs = ensure_reference_images(work, page_id, coma_id)
    _restore_scene_camera(scene, camera)
    if generate_references:
        configure_camera_backgrounds(scene, camera, refs, page_id, coma_id)
    ensure_hatching_background(context)
    _restore_scene_camera(scene, camera)
    resync_coma_camera_output_layout(context)
    view_camera_in_viewports(context)
    schedule_coma_view_camera()
    _add_page_overview_backgrounds(scene, work)


def _restore_scene_camera(scene, camera) -> None:
    if scene is None or camera is None or getattr(camera, "type", "") != "CAMERA":
        return
    try:
        if camera.name not in scene.objects:
            scene.collection.objects.link(camera)
    except Exception:  # noqa: BLE001
        pass
    try:
        if scene.camera != camera:
            scene.camera = camera
    except Exception:  # noqa: BLE001
        pass


def ensure_coma_camera(scene):
    """コマ用 Camera オブジェクトを取得または作成する."""
    cam_obj = scene.camera
    created = False
    if cam_obj is None or getattr(cam_obj, "type", "") != "CAMERA":
        cam_obj = bpy.data.objects.get(PANEL_CAMERA_NAME)
    if cam_obj is None or getattr(cam_obj, "type", "") != "CAMERA":
        # 名前を変更されていても識別できるよう custom property で探す
        # (見つからないと毎回新規カメラを作って重複する)。
        cam_obj = next(
            (
                o
                for o in getattr(scene, "objects", [])
                if getattr(o, "type", "") == "CAMERA" and o.get("bmanga_coma_camera")
            ),
            None,
        )
    if cam_obj is None or getattr(cam_obj, "type", "") != "CAMERA":
        cam_data = bpy.data.cameras.new(PANEL_CAMERA_NAME)
        cam_obj = bpy.data.objects.new(PANEL_CAMERA_NAME, cam_data)
        scene.collection.objects.link(cam_obj)
        created = True
    cam_obj["bmanga_coma_camera"] = True
    cam_data = cam_obj.data
    if created:
        cam_obj.name = PANEL_CAMERA_NAME
        try:
            cam_obj.location = (0.0, -DEFAULT_CAMERA_DISTANCE, 0.0)
            cam_obj.rotation_euler = (math.radians(90.0), 0.0, 0.0)
        except Exception:  # noqa: BLE001
            pass
        cam_data.clip_start = 0.01
        cam_data.clip_end = max(float(getattr(cam_data, "clip_end", 100.0)), 1000.0)
    else:
        # 既存カメラの名前/クリップ範囲はユーザー設定を尊重する。
        # ただし clip_start が 0 以下だと描画されないため、不正値のみ補正。
        if getattr(cam_data, "clip_start", 0.0) <= 0.0:
            cam_data.clip_start = 0.01
    if hasattr(cam_data, "show_limits"):
        cam_data.show_limits = True
    if hasattr(cam_data, "show_passepartout"):
        cam_data.show_passepartout = False
    if hasattr(cam_data, "passepartout_alpha"):
        cam_data.passepartout_alpha = 0.0
    return cam_obj


def capture_camera_runtime_settings(context, *, prefer_camera_fisheye: bool = True) -> None:
    """カメラ本体だけで変更された値を、保存用の Scene 設定へ反映する."""
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    if scene is None:
        return
    cam = getattr(scene, "camera", None)
    cam_data = getattr(cam, "data", None)
    if cam_data is None:
        return
    if bool(getattr(scene, "bmanga_coma_camera_fisheye_layout_mode", False)):
        if hasattr(cam_data, "fisheye_fov") and hasattr(scene, "bmanga_coma_camera_fisheye_fov"):
            camera_value = max(math.radians(100.0), min(math.radians(360.0), float(cam_data.fisheye_fov)))
            scene_value = float(getattr(scene, "bmanga_coma_camera_fisheye_fov", math.pi))
            scene_is_default = abs(scene_value - math.pi) <= 1.0e-6
            if prefer_camera_fisheye or scene_is_default:
                if abs(scene_value - camera_value) > 1.0e-6:
                    scene.bmanga_coma_camera_fisheye_fov = camera_value
            else:
                cam_data.fisheye_fov = scene_value
        return
    if hasattr(cam_data, "lens") and hasattr(scene, "bmanga_coma_camera_lens"):
        try:
            scene.bmanga_coma_camera_lens = float(cam_data.lens)
        except (TypeError, ValueError):
            pass


def apply_fisheye_fov(context) -> None:
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    if scene is None:
        return
    cam = getattr(scene, "camera", None)
    if cam is None or getattr(cam, "type", "") != "CAMERA":
        cam = ensure_coma_camera(scene)
        scene.camera = cam
    cam_data = getattr(cam, "data", None)
    if cam_data is None or not hasattr(cam_data, "fisheye_fov"):
        return
    cam_data.fisheye_fov = float(getattr(scene, "bmanga_coma_camera_fisheye_fov", math.pi))


def configure_render_for_current_coma(scene, work, page_id: str, coma_id: str) -> None:
    """ページ一覧ファイルの用紙設定に合わせてカメラ出力解像度を設定する."""
    has_saved_resolution = (
        int(getattr(scene, "bmanga_coma_camera_original_resolution_x", 0) or 0) > 0
        and int(getattr(scene, "bmanga_coma_camera_original_resolution_y", 0) or 0) > 0
    )
    paper = getattr(work, "paper", None) if work is not None else None
    width_mm = float(getattr(paper, "canvas_width_mm", 0.0) or 0.0) if paper is not None else 0.0
    height_mm = float(getattr(paper, "canvas_height_mm", 0.0) or 0.0) if paper is not None else 0.0
    dpi = int(getattr(paper, "dpi", 0) or 0) if paper is not None else 0
    if paper is None and has_saved_resolution:
        return
    if width_mm <= 0.0 or height_mm <= 0.0:
        _page_count, _render_side, width_mm, height_mm = _reference_frame_info(work, page_id, coma_id)
    if width_mm <= 0.0 or height_mm <= 0.0:
        width_mm, height_mm = 16.0, 9.0
    if dpi <= 0:
        dpi = DEFAULT_REF_DPI
    res_x = max(1, int(round(mm_to_px(width_mm, dpi))))
    res_y = max(1, int(round(mm_to_px(height_mm, dpi))))
    if hasattr(scene, "bmanga_coma_camera_original_resolution_x"):
        scene.bmanga_coma_camera_original_resolution_x = int(res_x)
    if hasattr(scene, "bmanga_coma_camera_original_resolution_y"):
        scene.bmanga_coma_camera_original_resolution_y = int(res_y)
    scene.render.resolution_percentage = 100
    scene.render.resolution_x = int(res_x)
    scene.render.resolution_y = int(res_y)


def ensure_default_resolution_settings(scene) -> None:
    settings = getattr(scene, "bmanga_coma_camera_resolution_settings", None)
    if settings is None or len(settings) > 0:
        return
    item = settings.add()
    item.name = "現在のコマ"
    item.resolution_x = int(getattr(scene.render, "resolution_x", 1920))
    item.resolution_y = int(getattr(scene.render, "resolution_y", 1080))


def configure_camera_backgrounds(scene, camera, refs: Iterable[ReferenceImage], page_id: str, coma_id: str) -> None:
    ensure_opacity_percent_units(scene)
    ref_list = list(refs)
    if not ref_list:
        # 下絵生成に失敗した場合でも、既存のカメラ下絵を消さない。
        return
    settings = getattr(scene, "bmanga_coma_camera_settings", None)
    name_visible = bool(getattr(settings, "name_visible", False))
    name_show_all_pages = bool(getattr(settings, "name_show_all_pages", False))
    koma_visible = bool(getattr(settings, "koma_visible", True))
    own_page_vis = bool(getattr(settings, "own_page_visible", True))
    name_alpha = percentage.percent_to_factor(getattr(settings, "name_bg_images_opacity", 50.0), 50.0)
    koma_alpha = percentage.percent_to_factor(getattr(settings, "koma_bg_images_opacity", 100.0), 100.0)
    own_page_alpha = percentage.percent_to_factor(getattr(settings, "own_page_opacity", 50.0), 50.0)
    scale = float(getattr(settings, "bg_images_scale", 1.0))
    koma_depth_back = bool(getattr(settings, "koma_depth", False))

    data = getattr(camera, "data", None)
    if data is None:
        return
    _clear_managed_backgrounds(data)
    for ref in ref_list:
        img = _load_reference_image(ref.path, ref.label)
        if img is None:
            continue
        try:
            img["bmanga_kind"] = ref.kind
            img["bmanga_page_id"] = ref.page_id
            img["bmanga_coma_id"] = coma_id if ref.kind in {"koma", "own_page"} else ""
            img["bmanga_full_page_mask"] = bool(ref.full_page_mask)
            img["bmanga_page_count"] = int(ref.page_count)
            img["bmanga_render_side"] = ref.render_side
        except Exception:  # noqa: BLE001
            pass
        bg = data.background_images.new()
        bg.image = img
        is_page_image = _ref_is_page_image(ref)
        if ref.kind == "own_page":
            alpha = own_page_alpha
            visible = own_page_vis and ref.visible
        elif ref.kind == "koma" and not is_page_image:
            alpha = koma_alpha
            visible = koma_visible and ref.visible
        else:
            alpha = name_alpha
            visible = name_visible and (ref.visible or name_show_all_pages)
        depth = "BACK" if ref.kind == "koma" and not is_page_image and koma_depth_back else "FRONT"
        bg_scale, bg_offset = _background_scale_offset_for_ref(ref, scale if is_page_image or ref.kind == "own_page" else 1.0)
        _set_bg_attr(bg, "alpha", alpha)
        _set_bg_attr(bg, "scale", bg_scale)
        _set_bg_attr(bg, "rotation", 0.0)
        _set_bg_attr(bg, "offset", bg_offset)
        _set_bg_attr(bg, "display_depth", depth)
        _set_bg_attr(bg, "frame_method", "FIT")
        _set_bg_attr(bg, "show_background_image", bool(visible))
    if hasattr(data, "show_background_images"):
        data.show_background_images = True


def sync_world_background_color(context, *, panel=None, work=None, page_id: str = "", coma_id: str = "") -> None:
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    if scene is None:
        return
    if bool(getattr(scene, "bmanga_coma_white_background", False)):
        apply_white_world_background(scene)
        return
    if panel is None:
        if work is None:
            work = get_work(context)
        if not page_id:
            page_id = str(getattr(scene, "bmanga_current_coma_page_id", "") or "")
        if not coma_id:
            coma_id = str(getattr(scene, "bmanga_current_coma_id", "") or "")
        panel = _resolve_coma(work, page_id, coma_id)
    if panel is None:
        return
    color = getattr(panel, "background_color", None)
    if color is None or len(color) < 3:
        return
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
        try:
            scene.world["bmanga_managed"] = True
        except Exception:  # noqa: BLE001
            pass
    world = scene.world
    # ユーザーが用意したワールド (空 HDRI など) は B-MANGA が作った
    # 管理用ワールドではないので、ノードツリーを作り直して上書きしない。
    # これをしないと、コマ用blendファイルでワールドを編集して保存しても、
    # 次にコマを開く/閉じる際にここで強度や接続が初期化され、編集が
    # 毎回失われてしまう (保存はされているが保存前にここで戻される)。
    if world is not None and world.get("bmanga_managed") is not True:
        return
    settings = getattr(scene, "bmanga_coma_camera_settings", None)
    camera_only = bool(getattr(settings, "world_background_camera_only", False))
    rgba = (
        float(color[0]),
        float(color[1]),
        float(color[2]),
        1.0,
    )
    try:
        world.color = rgba[:3]
    except Exception:  # noqa: BLE001
        pass
    _configure_world_background_nodes(world, rgba, camera_only)


def apply_white_world_background(scene) -> None:
    """ワールドの Background ノードを白色 (1,1,1) に設定する.

    既存ワールドが管理外の場合、ユーザーのワールドを保持したまま
    管理用ワールドを新規作成して差し替える。
    """
    if scene is None:
        return
    _SAVED_WORLD_KEY = "bmanga_saved_world_before_white"
    world = scene.world
    if world is not None and world.get("bmanga_managed") is not True:
        try:
            scene[_SAVED_WORLD_KEY] = world.name
        except Exception:  # noqa: BLE001
            pass
        managed = bpy.data.worlds.get("B-MANGA White BG")
        if managed is None:
            managed = bpy.data.worlds.new("B-MANGA White BG")
        try:
            managed["bmanga_managed"] = True
        except Exception:  # noqa: BLE001
            pass
        scene.world = managed
        world = managed
    elif world is None:
        world = bpy.data.worlds.new("B-MANGA White BG")
        try:
            world["bmanga_managed"] = True
        except Exception:  # noqa: BLE001
            pass
        scene.world = world
    settings = getattr(scene, "bmanga_coma_camera_settings", None)
    camera_only = bool(getattr(settings, "world_background_camera_only", False))
    white = (1.0, 1.0, 1.0, 1.0)
    try:
        world.color = white[:3]
    except Exception:  # noqa: BLE001
        pass
    _configure_world_background_nodes(world, white, camera_only)


def _restore_world_before_white(scene) -> None:
    """apply_white_world_background で退避したワールドを復元する."""
    _SAVED_WORLD_KEY = "bmanga_saved_world_before_white"
    saved_name = None
    try:
        saved_name = str(scene.get(_SAVED_WORLD_KEY, "") or "")
    except Exception:  # noqa: BLE001
        pass
    if saved_name:
        original = bpy.data.worlds.get(saved_name)
        if original is not None:
            scene.world = original
    try:
        del scene[_SAVED_WORLD_KEY]
    except Exception:  # noqa: BLE001
        pass


def _configure_world_background_nodes(world, rgba, camera_only: bool) -> None:
    try:
        # ここで作り直すワールドは B-MANGA 管理用と明示しておく
        # (次回以降の sync で再構築対象だと判定できるように)。
        world["bmanga_managed"] = True
    except Exception:  # noqa: BLE001
        pass
    try:
        world.use_nodes = True
    except Exception:  # noqa: BLE001
        return
    node_tree = getattr(world, "node_tree", None)
    if node_tree is None:
        return
    nodes = node_tree.nodes
    links = node_tree.links
    try:
        nodes.clear()
    except Exception:  # noqa: BLE001
        return
    out = nodes.new("ShaderNodeOutputWorld")
    out.location = (420, 0)
    if not camera_only:
        bg = nodes.new("ShaderNodeBackground")
        bg.location = (160, 0)
        bg.inputs["Color"].default_value = rgba
        bg.inputs["Strength"].default_value = 1.0
        links.new(bg.outputs["Background"], out.inputs["Surface"])
        return
    light_path = nodes.new("ShaderNodeLightPath")
    light_path.location = (-520, 0)
    bg_neutral = nodes.new("ShaderNodeBackground")
    bg_neutral.location = (-120, 120)
    bg_neutral.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    bg_neutral.inputs["Strength"].default_value = 0.0
    bg_camera = nodes.new("ShaderNodeBackground")
    bg_camera.location = (-120, -80)
    bg_camera.inputs["Color"].default_value = rgba
    bg_camera.inputs["Strength"].default_value = 1.0
    mix = nodes.new("ShaderNodeMixShader")
    mix.location = (160, 0)
    links.new(light_path.outputs["Is Camera Ray"], mix.inputs["Fac"])
    links.new(bg_neutral.outputs["Background"], mix.inputs[1])
    links.new(bg_camera.outputs["Background"], mix.inputs[2])
    links.new(mix.outputs["Shader"], out.inputs["Surface"])


def _clear_managed_backgrounds(camera_data) -> None:
    for bg in reversed(tuple(getattr(camera_data, "background_images", []))):
        if not _is_managed_background(bg):
            continue
        try:
            camera_data.background_images.remove(bg)
        except Exception:  # noqa: BLE001
            pass


def _is_managed_background(bg) -> bool:
    img = getattr(bg, "image", None)
    try:
        return bool(img and img.get(MANAGED_IMAGE_PROP, False))
    except Exception:  # noqa: BLE001
        return False


def _load_reference_image(path: Path, label: str):
    abspath = str(Path(path).resolve())
    try:
        img = bpy.data.images.load(abspath, check_existing=True)
        try:
            img.reload()
        except Exception:  # noqa: BLE001
            pass
        img.name = label
        img[MANAGED_IMAGE_PROP] = True
        return img
    except Exception:  # noqa: BLE001
        _logger.warning("panel camera reference load failed: %s", path, exc_info=True)
        return None


def _background_scale_offset_for_ref(ref: ReferenceImage, base_scale: float) -> tuple[float, tuple[float, float]]:
    if ref.full_page_mask and ref.page_count >= 2 and ref.render_side in {"left", "right"}:
        return float(base_scale) * 2.0, (0.5 if ref.render_side == "left" else -0.5, 0.0)
    return float(base_scale), (0.0, 0.0)


def _background_scale_offset_for_image(img, base_scale: float) -> tuple[float, tuple[float, float]]:
    page_count = 1
    side = "full"
    try:
        if img is not None:
            page_count = int(img.get("bmanga_page_count", 1))
            side = str(img.get("bmanga_render_side", "full") or "full")
    except Exception:  # noqa: BLE001
        pass
    if page_count >= 2 and side in {"left", "right"}:
        return float(base_scale) * 2.0, (0.5 if side == "left" else -0.5, 0.0)
    return float(base_scale), (0.0, 0.0)


def _ref_is_page_image(ref: ReferenceImage) -> bool:
    if str(ref.kind or "") == "own_page":
        return True
    if bool(ref.full_page_mask):
        return True
    if str(ref.kind or "") == "koma":
        return False
    return str(ref.kind or "") == "name"


def _image_is_page_image(img) -> bool:
    if img is None:
        return False
    try:
        kind = str(img.get("bmanga_kind", "") or "")
        if kind == "own_page":
            return True
        if bool(img.get("bmanga_full_page_mask", False)):
            return True
        if kind == "koma":
            return False
        if kind == "name":
            return True
    except Exception:  # noqa: BLE001
        pass
    return "ネーム" in getattr(img, "name", "")


def _background_matches_kind(bg, kind: str) -> bool:
    img = getattr(bg, "image", None)
    if img is None:
        return False
    try:
        img_kind = str(img.get("bmanga_kind", "") or "")
    except Exception:  # noqa: BLE001
        img_kind = ""
    if kind == "own_page":
        return img_kind == "own_page"
    is_page_image = _image_is_page_image(img)
    if kind == "name":
        if img_kind == "own_page":
            return False
        return is_page_image
    if kind == "koma":
        if img_kind == "koma":
            return True
        return not is_page_image and "コマ" in getattr(img, "name", "")
    if kind == "hatching":
        return img_kind == "hatching" or HATCHING_IMAGE_NAME in getattr(img, "name", "")
    return False


def set_background_images_opacity(context, opacity: float) -> None:
    for bg in _iter_camera_backgrounds(context):
        if _background_matches_kind(bg, "own_page") or _background_matches_kind(bg, "koma"):
            continue
        _set_bg_attr(bg, "alpha", opacity)


def set_background_images_scale(context, scale: float, *, kind_filter: str = "") -> None:
    for bg in _iter_camera_backgrounds(context):
        if kind_filter and not _background_matches_kind(bg, kind_filter):
            continue
        img = getattr(bg, "image", None)
        if img is not None and _is_overview_background(img):
            continue
        bg_scale, bg_offset = _background_scale_offset_for_image(img, float(scale))
        _set_bg_attr(bg, "scale", bg_scale)
        _set_bg_attr(bg, "offset", bg_offset)


def set_background_kind_visibility(context, kind: str, visible: bool) -> None:
    for bg in _iter_camera_backgrounds(context):
        if _background_matches_kind(bg, kind):
            _set_bg_attr(bg, "show_background_image", bool(visible))


def camera_background_count(context) -> int:
    return len(_iter_camera_backgrounds(context))


def can_render_references() -> bool:
    return export_pipeline.has_pillow()


def capture_managed_background_visibility(context) -> list[tuple[object, bool]]:
    state: list[tuple[object, bool]] = []
    for bg in _iter_camera_backgrounds(context):
        if _is_managed_background(bg):
            state.append((bg, bool(getattr(bg, "show_background_image", False))))
    return state


def set_managed_background_visibility(context, visible: bool) -> None:
    for bg in _iter_camera_backgrounds(context):
        if _is_managed_background(bg):
            _set_bg_attr(bg, "show_background_image", bool(visible))


def restore_background_visibility(state: Iterable[tuple[object, bool]]) -> None:
    for bg, visible in state:
        _set_bg_attr(bg, "show_background_image", bool(visible))


def toggle_all_backgrounds(context) -> bool:
    backgrounds = _iter_camera_backgrounds(context)
    visible = not all(bool(getattr(bg, "show_background_image", False)) for bg in backgrounds)
    for bg in backgrounds:
        _set_bg_attr(bg, "show_background_image", visible)
    return visible


def set_background_images_properties(context, name_filter: str, *, opacity=None, scale=None, kind_filter: str = "") -> None:
    for bg in _iter_camera_backgrounds(context):
        img = getattr(bg, "image", None)
        if img is None:
            continue
        if kind_filter:
            if not _background_matches_kind(bg, kind_filter):
                continue
        elif name_filter not in getattr(img, "name", ""):
            continue
        if opacity is not None:
            _set_bg_attr(bg, "alpha", float(opacity))
        if scale is not None and not _is_overview_background(img):
            bg_scale, bg_offset = _background_scale_offset_for_image(img, float(scale))
            _set_bg_attr(bg, "scale", bg_scale)
            _set_bg_attr(bg, "offset", bg_offset)


def set_background_image_visibility(context, name_filter: str, visible: bool) -> None:
    for bg in _iter_camera_backgrounds(context):
        img = getattr(bg, "image", None)
        if img is not None and name_filter in getattr(img, "name", ""):
            _set_bg_attr(bg, "show_background_image", bool(visible))


def set_background_image_rotation(context, name_filter: str, rotation: float) -> None:
    for bg in _iter_camera_backgrounds(context):
        img = getattr(bg, "image", None)
        if img is not None and name_filter in getattr(img, "name", ""):
            _set_bg_attr(bg, "rotation", float(rotation))


def _ensure_hatching_image():
    img = bpy.data.images.get(HATCHING_IMAGE_NAME)
    if img is not None:
        if HATCHING_ASSET_PATH.is_file():
            try:
                img.filepath = str(HATCHING_ASSET_PATH)
                img.source = "FILE"
                img.reload()
            except Exception:  # noqa: BLE001
                pass
        try:
            img[MANAGED_IMAGE_PROP] = True
            img["bmanga_kind"] = "hatching"
        except Exception:  # noqa: BLE001
            pass
        return img
    if HATCHING_ASSET_PATH.is_file():
        try:
            img = bpy.data.images.load(str(HATCHING_ASSET_PATH), check_existing=True)
            img.name = HATCHING_IMAGE_NAME
            img[MANAGED_IMAGE_PROP] = True
            img["bmanga_kind"] = "hatching"
            try:
                img.colorspace_settings.name = "sRGB"
            except Exception:  # noqa: BLE001
                pass
            return img
        except Exception:  # noqa: BLE001
            _logger.warning("hatching image load failed: %s", HATCHING_ASSET_PATH, exc_info=True)
    width = 256
    height = 256
    img = bpy.data.images.new(HATCHING_IMAGE_NAME, width=width, height=height, alpha=True)
    pixels: list[float] = []
    for y in range(height):
        wave = int(round(math.sin(y / 11.0) * 5.0))
        for x in range(width):
            phase = (x + y + wave) % 24
            alpha = 0.58 if phase <= 1 or phase >= 23 else 0.0
            pixels.extend((0.02, 0.02, 0.02, alpha))
    try:
        img.pixels.foreach_set(pixels)
        img.update()
    except Exception:  # noqa: BLE001
        pass
    try:
        img[MANAGED_IMAGE_PROP] = True
        img["bmanga_kind"] = "hatching"
    except Exception:  # noqa: BLE001
        pass
    try:
        img.colorspace_settings.name = "sRGB"
    except Exception:  # noqa: BLE001
        pass
    return img


def ensure_hatching_background(context):
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    if scene is None:
        return None
    cam = getattr(scene, "camera", None)
    if cam is None or getattr(cam, "type", "") != "CAMERA":
        cam = ensure_coma_camera(scene)
        scene.camera = cam
    data = getattr(cam, "data", None)
    if data is None:
        return None
    img = _ensure_hatching_image()
    bg = None
    for candidate in getattr(data, "background_images", []) or []:
        candidate_img = getattr(candidate, "image", None)
        if candidate_img is img or (
            candidate_img is not None
            and HATCHING_IMAGE_NAME in getattr(candidate_img, "name", "")
        ):
            bg = candidate
            break
    if bg is None:
        bg = data.background_images.new()
    bg.image = img
    settings = getattr(scene, "bmanga_coma_camera_settings", None)
    visible = bool(getattr(settings, "hatching_visible", False))
    rotation = float(getattr(settings, "hatching_rotation", 0.0) or 0.0)
    _set_bg_attr(bg, "alpha", 0.72)
    _set_bg_attr(bg, "scale", 1.0)
    _set_bg_attr(bg, "offset", (0.0, 0.0))
    _set_bg_attr(bg, "rotation", rotation)
    _set_bg_attr(bg, "display_depth", "FRONT")
    _set_bg_attr(bg, "frame_method", "CROP")
    _set_bg_attr(bg, "show_background_image", visible)
    if hasattr(data, "show_background_images"):
        data.show_background_images = True
    return bg


def set_koma_background_depth(context, *, back: bool) -> None:
    depth = "BACK" if back else "FRONT"
    for bg in _iter_camera_backgrounds(context):
        if _background_matches_kind(bg, "koma"):
            _set_bg_attr(bg, "display_depth", depth)


def toggle_backgrounds_by_kind(context, kind: str) -> bool:
    settings = getattr(context.scene, "bmanga_coma_camera_settings", None)
    if settings is None:
        return False
    if kind == "name":
        scene = getattr(context, "scene", None)
        if scene is not None and hasattr(scene, "bmanga_page_preview_enabled"):
            scene.bmanga_page_preview_enabled = not bool(scene.bmanga_page_preview_enabled)
            visible = bool(scene.bmanga_page_preview_enabled)
        else:
            settings.name_visible = not bool(settings.name_visible)
            visible = bool(settings.name_visible)
    else:
        settings.koma_visible = not bool(settings.koma_visible)
        visible = settings.koma_visible
    for bg in _iter_camera_backgrounds(context):
        if _background_matches_kind(bg, kind):
            _set_bg_attr(bg, "show_background_image", bool(visible))
    return visible


def set_page_reference_visibility(context, *, show_all: bool) -> None:
    settings = getattr(context.scene, "bmanga_coma_camera_settings", None)
    name_visible = bool(getattr(settings, "name_visible", False))
    current_page_id = str(getattr(context.scene, "bmanga_current_coma_page_id", "") or "")
    for bg in _iter_camera_backgrounds(context):
        img = getattr(bg, "image", None)
        if img is None or not _background_matches_kind(bg, "name"):
            continue
        page_id = ""
        try:
            page_id = str(img.get("bmanga_page_id", "") or "")
        except Exception:  # noqa: BLE001
            page_id = ""
        _set_bg_attr(
            bg,
            "show_background_image",
            bool(name_visible and (show_all or page_id == current_page_id)),
        )


def reload_background_images(context) -> int:
    count = 0
    for bg in _iter_camera_backgrounds(context):
        img = getattr(bg, "image", None)
        if img is None:
            continue
        try:
            img.reload()
            count += 1
        except Exception:  # noqa: BLE001
            pass
    return count


def update_view(context) -> None:
    for mat in bpy.data.materials:
        node_tree = getattr(mat, "node_tree", None)
        if node_tree is not None:
            _update_node_tree(node_tree)
    try:
        context.view_layer.update()
    except Exception:  # noqa: BLE001
        pass
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    except Exception:  # noqa: BLE001
        pass


def update_render_border_from_current_coma(context) -> None:
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    if scene is None or scene.camera is None:
        return
    target = _find_render_border_background(context, scene)
    if target is None or target.image is None:
        _disable_render_border(scene)
        _set_render_border_source(scene, "")
        return
    _set_render_border_source(scene, getattr(target.image, "name", ""))
    if bool(getattr(scene, "bmanga_coma_camera_fisheye_layout_mode", False)):
        _disable_render_border(scene)
        return
    try:
        if bool(target.image.get("bmanga_full_page_mask", False)):
            _apply_page_side_render_border(scene, target.image)
            return
    except Exception:  # noqa: BLE001
        pass
    scale = float(getattr(target, "scale", 1.0))
    image_width = max(1, int(target.image.size[0]))
    image_height = max(1, int(target.image.size[1]))
    res_x = max(1, int(scene.render.resolution_x))
    res_y = max(1, int(scene.render.resolution_y))
    aspect_image = image_width / image_height
    aspect_render = res_x / res_y
    if aspect_image > aspect_render:
        border_width = scale
        border_height = scale * (res_x / image_width) * (image_height / res_y)
    else:
        border_height = scale
        border_width = scale * (res_y / image_height) * (image_width / res_x)
    border_width = max(0.0, min(1.0, border_width))
    border_height = max(0.0, min(1.0, border_height))
    scene.render.use_border = True
    if hasattr(scene.render, "use_crop_to_border"):
        scene.render.use_crop_to_border = False
    scene.render.border_min_x = (1.0 - border_width) * 0.5
    scene.render.border_max_x = scene.render.border_min_x + border_width
    scene.render.border_min_y = (1.0 - border_height) * 0.5
    scene.render.border_max_y = scene.render.border_min_y + border_height


def _find_render_border_background(context, scene):
    backgrounds = _iter_camera_backgrounds(context)
    current_page_id = str(getattr(scene, "bmanga_current_coma_page_id", "") or "")
    current_coma_id = str(getattr(scene, "bmanga_current_coma_id", "") or "")
    managed_koma = [bg for bg in backgrounds if _is_managed_background(bg) and _background_matches_kind(bg, "koma")]
    for bg in managed_koma:
        img = getattr(bg, "image", None)
        if img is None:
            continue
        try:
            page_id = str(img.get("bmanga_page_id", "") or "")
            coma_id = str(img.get("bmanga_coma_id", "") or "")
        except Exception:  # noqa: BLE001
            page_id = ""
            coma_id = ""
        if (not current_page_id or page_id == current_page_id) and (not current_coma_id or coma_id == current_coma_id):
            return bg
    if managed_koma:
        return managed_koma[0]
    return None


def _set_render_border_source(scene, name: str) -> None:
    try:
        scene["bmanga_coma_camera_render_border_source"] = str(name or "")
    except Exception:  # noqa: BLE001
        pass


def _disable_render_border(scene) -> None:
    scene.render.use_border = False
    if hasattr(scene.render, "use_crop_to_border"):
        scene.render.use_crop_to_border = False
    scene.render.border_min_x = 0.0
    scene.render.border_max_x = 1.0
    scene.render.border_min_y = 0.0
    scene.render.border_max_y = 1.0


def _apply_page_side_render_border(scene, image) -> None:
    page_count = 1
    side = "full"
    try:
        page_count = int(image.get("bmanga_page_count", 1))
        side = str(image.get("bmanga_render_side", "full") or "full")
    except Exception:  # noqa: BLE001
        pass
    if page_count < 2 or side not in {"left", "right"}:
        _disable_render_border(scene)
        return
    _disable_render_border(scene)


def apply_selected_resolution_setting(context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    coll = getattr(scene, "bmanga_coma_camera_resolution_settings", None)
    idx = int(getattr(scene, "bmanga_coma_camera_resolution_settings_index", 0))
    if coll is None or not (0 <= idx < len(coll)):
        return
    item = coll[idx]
    scene.bmanga_coma_camera_original_resolution_x = int(item.resolution_x)
    scene.bmanga_coma_camera_original_resolution_y = int(item.resolution_y)
    scene.render.resolution_x = int(item.resolution_x)
    scene.render.resolution_y = int(item.resolution_y)
    if bool(getattr(scene, "bmanga_coma_camera_fisheye_layout_mode", False)):
        _apply_fisheye_layout(scene)
    elif bool(getattr(scene, "bmanga_coma_camera_reduction_mode", False)):
        _apply_reduction_layout(scene)
    else:
        scene.render.resolution_percentage = 100


def apply_fisheye_mode(context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    cam = scene.camera
    if cam is None or getattr(cam, "type", "") != "CAMERA":
        cam = ensure_coma_camera(scene)
        scene.camera = cam
    enabled = bool(getattr(scene, "bmanga_coma_camera_fisheye_layout_mode", False))
    if enabled:
        if int(getattr(scene, "bmanga_coma_camera_original_resolution_x", 0)) <= 0:
            scene.bmanga_coma_camera_original_resolution_x = int(scene.render.resolution_x)
        if int(getattr(scene, "bmanga_coma_camera_original_resolution_y", 0)) <= 0:
            scene.bmanga_coma_camera_original_resolution_y = int(scene.render.resolution_y)
        scene.bmanga_coma_camera_lens = float(cam.data.lens)
        cam.data.type = "PANO"
        if hasattr(cam.data, "panorama_type"):
            try:
                current = str(getattr(cam.data, "panorama_type", "") or "")
                if current not in {"FISHEYE_EQUIDISTANT", "FISHEYE_EQUISOLID"}:
                    cam.data.panorama_type = "FISHEYE_EQUISOLID"
            except TypeError:
                pass
        cam.data.fisheye_fov = float(getattr(scene, "bmanga_coma_camera_fisheye_fov", math.pi))
        scene.render.engine = "CYCLES"
        _apply_fisheye_layout(scene)
    else:
        scene.bmanga_coma_camera_fisheye_fov = float(getattr(cam.data, "fisheye_fov", math.pi))
        cam.data.type = "PERSP"
        cam.data.lens = float(getattr(scene, "bmanga_coma_camera_lens", cam.data.lens))
        if bool(getattr(scene, "bmanga_coma_camera_reduction_mode", False)):
            _apply_reduction_layout(scene)
        else:
            _restore_original_resolution(scene)
    settings = getattr(scene, "bmanga_coma_camera_settings", None)
    if settings is not None:
        set_background_images_scale(context, float(settings.bg_images_scale), kind_filter="name")
    update_render_border_from_current_coma(context)


def apply_reduction_mode(context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    if int(getattr(scene, "bmanga_coma_camera_original_resolution_x", 0)) <= 0:
        scene.bmanga_coma_camera_original_resolution_x = int(scene.render.resolution_x)
    if int(getattr(scene, "bmanga_coma_camera_original_resolution_y", 0)) <= 0:
        scene.bmanga_coma_camera_original_resolution_y = int(scene.render.resolution_y)
    if bool(getattr(scene, "bmanga_coma_camera_reduction_mode", False)):
        from ..core.fisheye import pencil4_link

        pencil4_link.apply_scale(
            float(scene.bmanga_coma_camera_preview_scale_percentage) / 100.0,
            ensure_saved=True,
        )
        _apply_reduction_layout(scene)
    else:
        from ..core.fisheye import pencil4_link

        pencil4_link.restore()
        if bool(getattr(scene, "bmanga_coma_camera_fisheye_layout_mode", False)):
            _apply_fisheye_layout(scene)
        else:
            _restore_original_resolution(scene)
    update_render_border_from_current_coma(context)
    update_view(context)


def resync_coma_camera_output_layout(context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None:
        return
    if bool(getattr(scene, "bmanga_coma_camera_fisheye_layout_mode", False)):
        apply_fisheye_mode(context)
    elif bool(getattr(scene, "bmanga_coma_camera_reduction_mode", False)):
        apply_reduction_mode(context)
    else:
        update_render_border_from_current_coma(context)


def view_camera_in_viewports(context) -> None:
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    if scene is None or get_mode(context) != MODE_COMA:
        return
    for space in _iter_view3d_spaces(context):
        _configure_coma_camera_view(space, scene)


def schedule_coma_view_camera(retries: int = 8, interval: float = 0.15) -> None:
    """Re-apply camera view after Blender has rebuilt UI areas on file load."""
    state = {"left": max(1, int(retries))}

    def _tick():
        try:
            view_camera_in_viewports(bpy.context)
        except Exception:  # noqa: BLE001
            pass
        state["left"] -= 1
        return interval if state["left"] > 0 else None

    try:
        bpy.app.timers.register(_tick, first_interval=interval)
    except Exception:  # noqa: BLE001
        pass


def _iter_view3d_spaces(context):
    seen: set[int] = set()
    screens = []
    screen = getattr(context, "screen", None) if context is not None else None
    if screen is not None:
        screens.append(screen)
    wm = getattr(bpy.context, "window_manager", None)
    if wm is not None:
        for window in getattr(wm, "windows", []):
            screen = getattr(window, "screen", None)
            if screen is not None:
                screens.append(screen)
    for screen in screens:
        sid = id(screen)
        if sid in seen:
            continue
        seen.add(sid)
        for area in getattr(screen, "areas", []):
            if area.type != "VIEW_3D":
                continue
            window = next(
                (
                    candidate
                    for candidate in getattr(wm, "windows", [])
                    if getattr(candidate, "screen", None) == screen
                ),
                None,
            ) if wm is not None else None
            if window is not None and page_browser.is_page_browser_area_for_window(window, area):
                page_browser.apply_page_browser_view_settings(area)
                continue
            for space in area.spaces:
                if space.type == "VIEW_3D" and getattr(space, "region_3d", None) is not None:
                    yield space


def _configure_coma_camera_view(space, scene=None) -> None:
    if scene is None:
        scene = bpy.context.scene
    camera = getattr(scene, "camera", None) if scene is not None else None
    if camera is not None:
        try:
            space.camera = camera
        except Exception:  # noqa: BLE001
            pass
    try:
        space.region_3d.view_perspective = "CAMERA"
    except Exception:  # noqa: BLE001
        pass
    try:
        space.lock_camera = True
    except Exception:  # noqa: BLE001
        pass
    shading = getattr(space, "shading", None)
    if shading is None:
        return
    # コマ用blendファイルはレンダーモード表示で開く (ユーザー要望)。
    # この関数はコマ編集モードでのみ呼ばれるため常に RENDERED でよい。
    try:
        shading.type = "RENDERED"
    except Exception:  # noqa: BLE001
        pass
    _apply_coma_solid_background(space, scene)


def _apply_coma_solid_background(space, scene) -> None:
    shading = getattr(space, "shading", None)
    settings = getattr(scene, "bmanga_coma_camera_settings", None) if scene is not None else None
    if shading is None or settings is None:
        return
    if bool(getattr(settings, "use_solid_background_color", False)):
        try:
            shading.background_type = "VIEWPORT"
            color = getattr(settings, "solid_background_color", (0.05, 0.05, 0.05))
            shading.background_color = (float(color[0]), float(color[1]), float(color[2]))
        except Exception:  # noqa: BLE001
            pass
    else:
        try:
            shading.background_type = "THEME"
        except Exception:  # noqa: BLE001
            pass


def _apply_fisheye_layout(scene) -> None:
    ox = int(getattr(scene, "bmanga_coma_camera_original_resolution_x", 0)) or int(scene.render.resolution_x)
    oy = int(getattr(scene, "bmanga_coma_camera_original_resolution_y", 0)) or int(scene.render.resolution_y)
    edge = max(1, ox, oy)
    if bool(getattr(scene, "bmanga_coma_camera_reduction_mode", False)):
        scene.render.resolution_percentage = _preview_resolution_percentage(scene)
    else:
        scene.render.resolution_percentage = 100
    scene.render.resolution_x = edge
    scene.render.resolution_y = edge


def _apply_reduction_layout(scene) -> None:
    ox = int(getattr(scene, "bmanga_coma_camera_original_resolution_x", 0)) or int(scene.render.resolution_x)
    oy = int(getattr(scene, "bmanga_coma_camera_original_resolution_y", 0)) or int(scene.render.resolution_y)
    if bool(getattr(scene, "bmanga_coma_camera_fisheye_layout_mode", False)):
        edge = max(1, ox, oy)
        scene.render.resolution_x = edge
        scene.render.resolution_y = edge
    else:
        scene.render.resolution_x = ox
        scene.render.resolution_y = oy
    scene.render.resolution_percentage = _preview_resolution_percentage(scene)


def _preview_resolution_percentage(scene) -> int:
    try:
        percentage = float(getattr(scene, "bmanga_coma_camera_preview_scale_percentage", 100.0) or 100.0)
    except (TypeError, ValueError):
        percentage = 100.0
    percentage = max(1.0, min(100.0, percentage))
    return max(1, min(32767, int(math.floor(percentage + 0.5))))


def _restore_original_resolution(scene) -> None:
    ox = int(getattr(scene, "bmanga_coma_camera_original_resolution_x", 0))
    oy = int(getattr(scene, "bmanga_coma_camera_original_resolution_y", 0))
    if ox > 0 and oy > 0:
        scene.render.resolution_x = ox
        scene.render.resolution_y = oy
    scene.render.resolution_percentage = 100


def _iter_camera_backgrounds(context):
    scene = getattr(context, "scene", None) if context is not None else bpy.context.scene
    cam = getattr(scene, "camera", None) if scene is not None else None
    data = getattr(cam, "data", None)
    if data is None:
        return []
    return list(getattr(data, "background_images", []))


def _set_bg_attr(bg, attr: str, value) -> None:
    try:
        setattr(bg, attr, value)
    except Exception:  # noqa: BLE001
        pass


def _update_node_tree(node_tree) -> None:
    for node in getattr(node_tree, "nodes", []):
        child_tree = getattr(node, "node_tree", None)
        if child_tree is not None:
            _update_node_tree(child_tree)
    try:
        node_tree.update_tag()
    except Exception:  # noqa: BLE001
        pass


_COMA_OVERLAY_GENERATED_KEY = "_bmanga_coma_overlay_generated"


def _ensure_coma_overlay_objects(scene, work) -> None:
    """コマモード時にオーバーレイ再描画をタグ付けする."""
    if scene is None or work is None or not bool(getattr(work, "loaded", False)):
        return
    if scene.get(_COMA_OVERLAY_GENERATED_KEY):
        return
    scene[_COMA_OVERLAY_GENERATED_KEY] = True


# ── コマファイル ページ概要表示（カメラ下絵方式） ─────────────────
_PAGE_OVERVIEW_BG_PROP = "_bmanga_page_overview_bg"


def _add_page_overview_backgrounds(scene, work) -> None:
    """ページプレビュー画像を個別カメラ下絵として追加する.

    各ページ画像を scale=1.0 で追加し、出力解像度にピッタリ合わせる。
    offset でグリッド位置に配置する。カメラの設定は一切変更しない。
    現在ページはコマ領域を透明にした自ページ画像 (own_page) と
    コマ内レイヤー画像 (koma) に分離して追加する。
    """
    cam = getattr(scene, "camera", None)
    if cam is None:
        return
    cam_data = getattr(cam, "data", None)
    if cam_data is None:
        return
    _remove_page_overview_backgrounds(scene)
    from . import page_preview_object

    try:
        page_preview_object.sync_page_previews(bpy.context, work, force=False)
    except Exception:  # noqa: BLE001
        _logger.exception("page overview preview image sync failed")

    rects = page_preview_object.preview_rects_mm(scene, work)
    if not rects:
        return
    _role, current_page_id = page_preview_object._preview_scene_role(scene)
    current_rect = rects.get(current_page_id)
    if current_rect is None:
        return
    _ci, cx0, cy0, cx1, cy1 = current_rect
    current_cx = (cx0 + cx1) * 0.5
    current_cy = (cy0 + cy1) * 0.5
    paper = getattr(work, "paper", None)
    canvas_w_mm = max(1.0, float(getattr(paper, "canvas_width_mm", 182) or 182)) if paper else 182.0
    canvas_h_mm = max(1.0, float(getattr(paper, "canvas_height_mm", 257) or 257)) if paper else 257.0

    settings = getattr(scene, "bmanga_coma_camera_settings", None)
    alpha = percentage.percent_to_factor(
        getattr(settings, "name_bg_images_opacity", 50.0), 50.0,
    ) if settings else 0.5
    user_scale = max(0.1, float(getattr(settings, "bg_images_scale", 1.0))) if settings else 1.0
    name_visible = bool(getattr(settings, "name_visible", True)) if settings else True

    own_page_alpha = percentage.percent_to_factor(
        getattr(settings, "own_page_opacity", 50.0), 50.0,
    ) if settings else 0.5
    own_page_visible = bool(getattr(settings, "own_page_visible", True)) if settings else True
    koma_alpha = percentage.percent_to_factor(
        getattr(settings, "koma_bg_images_opacity", 100.0), 100.0,
    ) if settings else 1.0
    koma_visible = bool(getattr(settings, "koma_visible", True)) if settings else True

    coma_id = str(getattr(scene, "bmanga_current_coma_id", "") or "")
    coma_points_mm = _resolve_coma_points_mm(work, current_page_id, coma_id)

    for page_id, (idx, x0, y0, x1, y1) in rects.items():
        if page_id == current_page_id:
            _add_own_page_backgrounds(
                cam_data, work, page_id, coma_id, coma_points_mm,
                canvas_w_mm, canvas_h_mm, user_scale,
                own_page_alpha, own_page_visible,
            )
            continue
        png_path = page_preview_object._preview_png_path(work, page_id)
        if png_path is None or not png_path.is_file():
            continue
        try:
            img = bpy.data.images.load(str(png_path.resolve()), check_existing=True)
            img.reload()
        except Exception:  # noqa: BLE001
            continue
        try:
            img[_PAGE_OVERVIEW_BG_PROP] = True
            img["bmanga_kind"] = "name"
            img["bmanga_page_id"] = page_id
        except Exception:  # noqa: BLE001
            pass
        page_cx = (x0 + x1) * 0.5
        page_cy = (y0 + y1) * 0.5
        page_w_mm = max(1.0, x1 - x0)
        page_scale = (page_w_mm / canvas_w_mm) * user_scale
        offset_x = ((page_cx - current_cx) / canvas_w_mm) * user_scale
        offset_y = ((page_cy - current_cy) / canvas_h_mm) * user_scale
        bg = cam_data.background_images.new()
        bg.image = img
        _set_bg_attr(bg, "alpha", float(alpha))
        _set_bg_attr(bg, "scale", float(page_scale))
        _set_bg_attr(bg, "rotation", 0.0)
        _set_bg_attr(bg, "offset", (offset_x, offset_y))
        _set_bg_attr(bg, "display_depth", "FRONT")
        _set_bg_attr(bg, "frame_method", "FIT")
        _set_bg_attr(bg, "show_background_image", bool(name_visible))
    if hasattr(cam_data, "show_background_images"):
        cam_data.show_background_images = True


def _resolve_coma_points_mm(work, page_id: str, coma_id: str) -> list[tuple[float, float]]:
    """コマのポリゴン頂点を mm 座標で返す."""
    if not work or not page_id or not coma_id:
        return []
    page = find_page_by_id(work, page_id)
    if page is None:
        return []
    for panel in getattr(page, "comas", []):
        if getattr(panel, "coma_id", "") != coma_id:
            continue
        if getattr(panel, "shape_type", "") == "rect":
            x = float(getattr(panel, "rect_x_mm", 0.0))
            y = float(getattr(panel, "rect_y_mm", 0.0))
            w = float(getattr(panel, "rect_width_mm", 0.0))
            h = float(getattr(panel, "rect_height_mm", 0.0))
            if w <= 0.0 or h <= 0.0:
                return []
            return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        return [(float(v.x_mm), float(v.y_mm)) for v in getattr(panel, "vertices", [])]
    return []


def _add_own_page_backgrounds(
    cam_data, work, page_id, coma_id, coma_points_mm,
    canvas_w_mm, canvas_h_mm, user_scale,
    own_page_alpha, own_page_visible,
) -> None:
    """現在ページをコマ領域透明にして自ページ画像として追加."""
    from . import page_preview_object

    png_path = page_preview_object._preview_png_path(work, page_id)
    if png_path is None or not png_path.is_file():
        return
    if not export_pipeline.has_pillow():
        _add_own_page_fallback(cam_data, png_path, page_id, user_scale, own_page_alpha, own_page_visible)
        return
    Image = export_pipeline.Image
    ImageDraw = export_pipeline.ImageDraw
    if Image is None or ImageDraw is None:
        _add_own_page_fallback(cam_data, png_path, page_id, user_scale, own_page_alpha, own_page_visible)
        return
    try:
        with Image.open(str(png_path)) as opened:
            src = opened.convert("RGBA")
    except Exception:  # noqa: BLE001
        _add_own_page_fallback(cam_data, png_path, page_id, user_scale, own_page_alpha, own_page_visible)
        return
    w, h = src.size
    points_px = _mm_to_image_px(coma_points_mm, canvas_w_mm, canvas_h_mm, w, h)
    work_dir = Path(str(getattr(work, "work_dir", "") or ""))
    cache_dir = paths.assets_dir(work_dir) / "_coma_bg_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    if points_px and len(points_px) >= 3:
        masked_path = cache_dir / f"own_page_{page_id}_{coma_id}.png"
        masked = src.copy()
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).polygon(points_px, fill=255)
        alpha_ch = masked.getchannel("A")
        alpha_ch.paste(0, mask=mask)
        masked.putalpha(alpha_ch)
        masked.save(str(masked_path))
        _load_overview_bg(cam_data, masked_path, page_id, "own_page", user_scale, own_page_alpha, own_page_visible)
    else:
        _add_own_page_fallback(cam_data, png_path, page_id, user_scale, own_page_alpha, own_page_visible)


def _add_own_page_fallback(cam_data, png_path, page_id, user_scale, alpha, visible) -> None:
    """コマ座標が取得できない時はフル画像をそのまま追加."""
    _load_overview_bg(cam_data, png_path, page_id, "own_page", user_scale, alpha, visible)


def _load_overview_bg(cam_data, png_path, page_id, kind, scale, alpha, visible, *, depth="FRONT") -> None:
    """カメラ下絵として画像を追加するユーティリティ."""
    try:
        img = bpy.data.images.load(str(Path(png_path).resolve()), check_existing=True)
        img.reload()
    except Exception:  # noqa: BLE001
        return
    try:
        img[_PAGE_OVERVIEW_BG_PROP] = True
        img["bmanga_kind"] = kind
        img["bmanga_page_id"] = page_id
    except Exception:  # noqa: BLE001
        pass
    bg = cam_data.background_images.new()
    bg.image = img
    _set_bg_attr(bg, "alpha", float(alpha))
    _set_bg_attr(bg, "scale", float(scale))
    _set_bg_attr(bg, "rotation", 0.0)
    _set_bg_attr(bg, "offset", (0.0, 0.0))
    _set_bg_attr(bg, "display_depth", depth)
    _set_bg_attr(bg, "frame_method", "FIT")
    _set_bg_attr(bg, "show_background_image", bool(visible))


def _mm_to_image_px(
    points_mm: list[tuple[float, float]],
    canvas_w_mm: float, canvas_h_mm: float,
    img_w: int, img_h: int,
) -> list[tuple[int, int]]:
    """mm 座標を画像ピクセル座標に変換する."""
    out: list[tuple[int, int]] = []
    for x_mm, y_mm in points_mm:
        px = int(round(x_mm / canvas_w_mm * img_w))
        py = img_h - int(round(y_mm / canvas_h_mm * img_h))
        out.append((px, py))
    return out


def _is_overview_background(img) -> bool:
    try:
        return bool(img.get(_PAGE_OVERVIEW_BG_PROP, False))
    except Exception:  # noqa: BLE001
        return False


def refresh_coma_page_overview(context) -> None:
    """概要下絵を再構築する (スケール・表示範囲変更時用)."""
    scene = getattr(context, "scene", None)
    if scene is None or get_mode(context) != MODE_COMA:
        return
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return
    _add_page_overview_backgrounds(scene, work)


def _remove_page_overview_backgrounds(scene) -> None:
    """ページ概要の下絵を除去する."""
    cam = getattr(scene, "camera", None)
    if cam is None:
        return
    cam_data = getattr(cam, "data", None)
    if cam_data is None:
        return
    for bg in reversed(tuple(getattr(cam_data, "background_images", []))):
        img = getattr(bg, "image", None)
        if img is not None:
            try:
                is_overview = bool(img.get(_PAGE_OVERVIEW_BG_PROP, False))
            except Exception:  # noqa: BLE001
                is_overview = False
            if is_overview:
                try:
                    cam_data.background_images.remove(bg)
                except Exception:  # noqa: BLE001
                    pass


def _any_view3d_in_camera_view(context) -> bool:
    for space in _iter_view3d_spaces(context):
        rv3d = getattr(space, "region_3d", None)
        if rv3d is not None and getattr(rv3d, "view_perspective", "") == "CAMERA":
            return True
    return False


# ── ページファイル ページ概要表示（カメラ下絵方式） ─────────────────


def add_page_file_overview_backgrounds(scene, work) -> None:
    """ページファイルの他ページプレビューをカメラ下絵として追加する.

    ページファイル (ROLE_PAGE) 用。overview カメラの ortho_scale に合わせて
    各ページ画像の scale / offset を計算し、カメラ下絵として配置する。
    現在編集中のページはスキップする。
    """
    from .geom import m_to_mm

    cam = getattr(scene, "camera", None)
    if cam is None:
        return
    cam_data = getattr(cam, "data", None)
    if cam_data is None:
        return
    _remove_page_overview_backgrounds(scene)

    from . import page_preview_object

    rects = page_preview_object.preview_rects_mm(scene, work)
    if not rects:
        return

    _role, current_page_id = page_preview_object._preview_scene_role(scene)

    paper = getattr(work, "paper", None)
    canvas_w_mm = max(1.0, float(getattr(paper, "canvas_width_mm", 182) or 182)) if paper else 182.0
    canvas_h_mm = max(1.0, float(getattr(paper, "canvas_height_mm", 257) or 257)) if paper else 257.0

    cam_cx_mm = m_to_mm(float(cam.location.x))
    cam_cy_mm = m_to_mm(float(cam.location.y))
    cam_ortho_mm = m_to_mm(float(cam_data.ortho_scale))
    if cam_ortho_mm <= 0:
        return
    base_scale = canvas_h_mm / cam_ortho_mm

    settings = getattr(scene, "bmanga_coma_camera_settings", None)
    alpha = percentage.percent_to_factor(
        getattr(settings, "name_bg_images_opacity", 50.0), 50.0,
    ) if settings else 0.5
    name_visible = bool(getattr(settings, "name_visible", True)) if settings else True

    for page_id, (idx, x0, y0, x1, y1) in rects.items():
        if page_id == current_page_id:
            continue
        png_path = page_preview_object._preview_png_path(work, page_id)
        if png_path is None or not png_path.is_file():
            continue
        try:
            img = bpy.data.images.load(str(png_path.resolve()), check_existing=True)
            img.reload()
        except Exception:  # noqa: BLE001
            continue
        try:
            img[_PAGE_OVERVIEW_BG_PROP] = True
            img["bmanga_kind"] = "name"
            img["bmanga_page_id"] = page_id
        except Exception:  # noqa: BLE001
            pass
        page_cx = (x0 + x1) * 0.5
        page_cy = (y0 + y1) * 0.5
        page_w_mm = max(1.0, x1 - x0)
        page_scale = (page_w_mm / canvas_w_mm) * base_scale
        offset_x = (page_cx - cam_cx_mm) / canvas_w_mm * base_scale
        offset_y = (page_cy - cam_cy_mm) / canvas_h_mm * base_scale
        bg = cam_data.background_images.new()
        bg.image = img
        _set_bg_attr(bg, "alpha", float(alpha))
        _set_bg_attr(bg, "scale", float(page_scale))
        _set_bg_attr(bg, "rotation", 0.0)
        _set_bg_attr(bg, "offset", (offset_x, offset_y))
        _set_bg_attr(bg, "display_depth", "BACK")
        _set_bg_attr(bg, "frame_method", "FIT")
        _set_bg_attr(bg, "show_background_image", bool(name_visible))
    if hasattr(cam_data, "show_background_images"):
        cam_data.show_background_images = True


def remove_page_file_overview_backgrounds(scene) -> None:
    """ページファイルの概要下絵を除去する (公開 API)."""
    _remove_page_overview_backgrounds(scene)


def refresh_page_file_overview(context) -> None:
    """ページファイルの概要下絵を再構築する."""
    from ..core.mode import MODE_PAGE

    scene = getattr(context, "scene", None)
    if scene is None or get_mode(context) != MODE_PAGE:
        return
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return
    add_page_file_overview_backgrounds(scene, work)


