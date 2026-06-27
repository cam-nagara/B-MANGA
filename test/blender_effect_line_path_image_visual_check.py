"""Blender実機用: 効果線の基準パスと画像線を目視確認用にレンダーする。"""

from __future__ import annotations

import importlib.util
import json
import math
import struct
import sys
import tempfile
import zlib
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "_verify" / "effect_line_path_image_visual"
OUTPUT_PATH = OUTPUT_DIR / "effect_line_path_image_visual.png"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_effect_path_image_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_effect_path_image_visual"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _write_stripe_png(path: Path, width: int = 96, height: int = 24) -> None:
    rows = []
    for y in range(height):
        raw = bytearray([0])
        for x in range(width):
            if x < width // 3:
                color = (255, 40 + y * 3, 40, 255)
            elif x < width * 2 // 3:
                color = (255, 230, 30 + y * 2, 255)
            else:
                color = (20, 90 + y * 4, 255, 255)
            raw.extend(color)
        rows.append(bytes(raw))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + _png_chunk(b"IEND", b"")
    )


def _set_curve_points(source, points_mm: list[tuple[float, float]]) -> None:
    curve = source.data
    while len(curve.splines):
        curve.splines.remove(curve.splines[0])
    spline = curve.splines.new("POLY")
    spline.points.add(len(points_mm) - 1)
    for point, (x_mm, y_mm) in zip(spline.points, points_mm, strict=False):
        point.co = (x_mm * 0.001, y_mm * 0.001, 0.0, 1.0)


def _target_bounds(scene) -> tuple[float, float, float, float] | None:
    points = []
    for obj in scene.objects:
        if getattr(obj, "type", "") != "MESH" or obj.hide_render:
            continue
        if "画像線" not in obj.name:
            continue
        if len(getattr(getattr(obj, "data", None), "vertices", [])) == 0:
            continue
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    if not points:
        return None
    return (
        min(float(point.x) for point in points),
        min(float(point.y) for point in points),
        max(float(point.x) for point in points),
        max(float(point.y) for point in points),
    )


def _target_view(scene) -> tuple[float, float, float]:
    bounds = _target_bounds(scene)
    if bounds is None:
        return 0.105, 0.1485, 0.25
    min_x, min_y, max_x, max_y = bounds
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5
    height = max(max_y - min_y, 0.05)
    width = max(max_x - min_x, 0.05)
    aspect = 1200.0 / 1500.0
    ortho_scale = max(height * 1.25, width * 1.25 / aspect, 0.08)
    return center_x, center_y, ortho_scale


def _prepare_camera(scene) -> None:
    try:
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
    except Exception:  # noqa: BLE001
        pass
    camera_data = bpy.data.cameras.new("B-MANGA_目視確認カメラ")
    camera = bpy.data.objects.new("B-MANGA_目視確認カメラ", camera_data)
    scene.collection.objects.link(camera)
    bpy.context.view_layer.update()
    center_x, center_y, ortho_scale = _target_view(scene)
    camera.location = (center_x, center_y, 1.0)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = ortho_scale
    scene.camera = camera
    scene.render.resolution_x = 1200
    scene.render.resolution_y = 1500
    scene.render.film_transparent = False
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:  # noqa: BLE001
        pass
    if scene.world is not None:
        scene.world.color = (1.0, 1.0, 1.0)
    bpy.context.view_layer.update()


def _view3d_context():
    for window in bpy.context.window_manager.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for region in area.regions:
                if region.type == "WINDOW":
                    return window, screen, area, region, area.spaces.active
    raise RuntimeError("VIEW_3D が見つかりません")


def _set_viewport_for_capture() -> None:
    window, screen, area, region, space = _view3d_context()
    rv3d = space.region_3d
    center_x, center_y, _ortho_scale = _target_view(bpy.context.scene)
    with bpy.context.temp_override(
        window=window,
        screen=screen,
        area=area,
        region=region,
        space_data=space,
        region_data=rv3d,
    ):
        bpy.ops.view3d.view_axis(type="TOP", align_active=False)
        rv3d.view_perspective = "ORTHO"
        rv3d.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
        rv3d.view_location = Vector((center_x, center_y, 0.0))
        rv3d.view_distance = 0.7
        space.overlay.show_floor = False
        space.overlay.show_axis_x = False
        space.overlay.show_axis_y = False
        space.overlay.show_object_origins = False
        space.overlay.show_overlays = False
        space.shading.type = "MATERIAL"
        try:
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=8)
        except Exception:  # noqa: BLE001
            pass


def _capture_viewport(scene) -> None:
    window, screen, area, region, space = _view3d_context()
    rv3d = space.region_3d
    previous_path = str(getattr(scene.render, "filepath", "") or "")
    scene.render.filepath = str(OUTPUT_PATH)
    scene.render.image_settings.file_format = "PNG"
    scene.render.resolution_x = 1200
    scene.render.resolution_y = 1500
    scene.render.resolution_percentage = 100
    with bpy.context.temp_override(
        window=window,
        screen=screen,
        area=area,
        region=region,
        space_data=space,
        region_data=rv3d,
    ):
        result = bpy.ops.render.opengl("EXEC_DEFAULT", write_still=True, view_context=True)
    scene.render.filepath = previous_path
    if "FINISHED" not in result:
        raise RuntimeError(f"viewport render failed: {result}")


def _assert_png_has_foreground(path: Path) -> None:
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        pixels = image.pixels[:]
        bg = pixels[0:3]
        step = max(1, (width * height) // 250000)
        foreground = 0
        generated_shape = 0
        sample_count = 0
        for pixel_index in range(0, width * height, step):
            base = pixel_index * 4
            rgb = pixels[base : base + 3]
            alpha = pixels[base + 3]
            if alpha > 0.1 and sum(abs(float(rgb[i]) - float(bg[i])) for i in range(3)) > 0.12:
                foreground += 1
            if alpha > 0.1 and rgb[1] > 0.5 and rgb[0] < 0.35 and rgb[2] < 0.45:
                generated_shape += 1
            sample_count += 1
        ratio = foreground / max(sample_count, 1)
        shape_ratio = generated_shape / max(sample_count, 1)
        assert ratio > 0.01, f"目視確認画像に表示対象がほとんど写っていません foreground={ratio:.4f}"
        assert shape_ratio > 0.0002, f"生成形状の表示色が目視確認画像に見つかりません shape={shape_ratio:.4f}"
    finally:
        bpy.data.images.remove(image)


def _create_coma(scene, work, page) -> object:
    from bmanga_dev_effect_path_image_visual.utils import coma_border_object, coma_plane

    if len(page.comas) == 0:
        assert "FINISHED" in bpy.ops.bmanga.coma_add()
    coma = page.comas[0]
    coma.shape_type = "rect"
    coma.rect_x_mm = 22.0
    coma.rect_y_mm = 28.0
    coma.rect_width_mm = 166.0
    coma.rect_height_mm = 220.0
    coma_plane.ensure_coma_plane(scene, work, page, coma)
    coma_plane.ensure_coma_mask(scene, work, page, coma)
    coma_border_object.ensure_coma_border_object(scene, work, page, coma)
    return coma


def _create_ribbon_effect(context, image_path: Path, parent_key: str):
    from bmanga_dev_effect_path_image_visual.operators import effect_line_op
    from bmanga_dev_effect_path_image_visual.utils import effect_line_path

    scene = context.scene
    params = scene.bmanga_effect_line_params
    effect_line_op._set_scene_params_syncing(scene, True)
    try:
        params.effect_type = "focus"
        params.spacing_mode = "angle"
        params.spacing_angle_deg = 22.0
        params.max_line_count = 24
        params.brush_size_mm = 0.7
        params.base_path_enabled = True
        params.base_path_points_json = ""
        params.line_image_path = str(image_path)
        params.line_image_draw_mode = "ribbon"
        params.line_image_brush_size_mm = 4.0
        params.line_image_aspect_ratio = 5.0
        params.line_image_angle_deg = 0.0
        params.line_image_spacing_percent = 100.0
        params.line_image_ribbon_repeat_mode = "repeat"
    finally:
        effect_line_op._set_scene_params_syncing(scene, False)
    obj, layer = effect_line_op._create_effect_layer(context, (42.0, 72.0, 96.0, 70.0), parent_key=parent_key)
    assert obj is not None and layer is not None
    source = effect_line_path.find_effect_base_path_object(obj)
    assert source is not None
    points = json.loads(params.base_path_points_json)
    start = tuple(points[0])
    end = tuple(points[-1])
    _set_curve_points(
        source,
        [
            start,
            ((start[0] + end[0]) * 0.5, (start[1] + end[1]) * 0.5 + 22.0),
            end,
        ],
    )
    assert effect_line_path.sync_from_base_path_object(scene, source)
    return obj, layer


def _create_stamp_effect(context, image_path: Path, parent_key: str):
    from bmanga_dev_effect_path_image_visual.operators import effect_line_op

    scene = context.scene
    direction = bpy.data.objects.new("目視確認_画像線方向", None)
    scene.collection.objects.link(direction)
    direction.rotation_euler[2] = math.radians(35.0)
    params = scene.bmanga_effect_line_params
    effect_line_op._set_scene_params_syncing(scene, True)
    try:
        params.effect_type = "speed"
        params.speed_angle_deg = 0.0
        params.speed_line_count = 12
        params.brush_size_mm = 0.7
        params.base_path_enabled = False
        params.base_path_points_json = ""
        params.line_image_path = str(image_path)
        params.line_image_draw_mode = "stamp"
        params.line_image_brush_size_mm = 5.0
        params.line_image_aspect_ratio = 3.2
        params.line_image_angle_deg = 10.0
        params.line_image_spacing_percent = 110.0
        params.line_image_stamp_angle_mode = "object"
        params.line_image_stamp_angle_object_name = direction.name
    finally:
        effect_line_op._set_scene_params_syncing(scene, False)
    obj, layer = effect_line_op._create_effect_layer(context, (38.0, 162.0, 104.0, 48.0), parent_key=parent_key)
    assert obj is not None and layer is not None
    return obj, layer


def _create_shape_effect(context, parent_key: str):
    from bmanga_dev_effect_path_image_visual.operators import effect_line_op

    scene = context.scene
    params = scene.bmanga_effect_line_params
    effect_line_op._set_scene_params_syncing(scene, True)
    try:
        params.effect_type = "speed"
        params.speed_angle_deg = 0.0
        params.speed_line_count = 10
        params.brush_size_mm = 0.7
        params.base_path_enabled = False
        params.base_path_points_json = ""
        params.line_image_source = "shape"
        params.line_image_shape_kind = "star"
        params.line_image_shape_sides = 6
        params.line_image_color = (0.0, 1.0, 0.25, 1.0)
        params.line_image_draw_mode = "stamp"
        params.line_image_brush_size_mm = 6.0
        params.line_image_aspect_ratio = 1.0
        params.line_image_angle_deg = 0.0
        params.line_image_spacing_percent = 120.0
        params.line_image_inout_size_enabled = True
        params.line_image_inout_opacity_enabled = True
        params.in_percent = 20.0
        params.out_percent = 20.0
        params.in_start_percent = 45.0
        params.out_start_percent = 45.0
    finally:
        effect_line_op._set_scene_params_syncing(scene, False)
    obj, layer = effect_line_op._create_effect_layer(context, (38.0, 202.0, 104.0, 34.0), parent_key=parent_key)
    assert obj is not None and layer is not None
    return obj, layer


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_effect_path_image_visual_"))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    image_path = temp_root / "effect_line_visual_texture.png"
    _write_stripe_png(image_path)
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "EffectPathImageVisual.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        from bmanga_dev_effect_path_image_visual.core.work import get_work
        from bmanga_dev_effect_path_image_visual.utils.layer_hierarchy import coma_stack_key

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        coma = _create_coma(scene, work, page)
        parent_key = coma_stack_key(page, coma)
        _create_ribbon_effect(context, image_path, parent_key)
        _create_stamp_effect(context, image_path, parent_key)
        _create_shape_effect(context, parent_key)
        _prepare_camera(scene)
        if bpy.app.background:
            scene.render.filepath = str(OUTPUT_PATH)
            bpy.ops.render.render(write_still=True)
        else:
            _set_viewport_for_capture()
            _capture_viewport(scene)
        assert OUTPUT_PATH.is_file(), "目視確認画像が生成されていません"
        _assert_png_has_foreground(OUTPUT_PATH)
        print(f"BMANGA_EFFECT_LINE_PATH_IMAGE_VISUAL_OK {OUTPUT_PATH}", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
