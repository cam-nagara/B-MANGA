"""Blender実機チェック: 見開き化後のセーフライン外/裁ち落とし枠外の塗り."""

from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_spread_overlay_fill"
OUT_DIR = Path(
    os.environ.get("BMANGA_SPREAD_OVERLAY_FILL_OUT", "")
    or ROOT / "_verify" / "spread_overlay_fill"
)
STAGE_LOG = OUT_DIR / "stage.log"


def _mark(message: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with STAGE_LOG.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


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


def _sub(path: str):
    return importlib.import_module(f"{MOD_NAME}.{path}")


def _assert_close(actual: float, expected: float, label: str, eps: float = 0.01) -> None:
    if abs(float(actual) - float(expected)) > eps:
        raise AssertionError(f"{label}: actual={actual:.4f} expected={expected:.4f}")


def _ensure_two_pages(work) -> None:
    while len(work.pages) < 2:
        result = bpy.ops.bmanga.page_add("EXEC_DEFAULT")
        if "FINISHED" not in result:
            raise AssertionError(f"ページ追加に失敗しました: {result}")


def _configure_work(work) -> None:
    paper = work.paper
    paper.canvas_width_mm = 120.0
    paper.canvas_height_mm = 170.0
    paper.finish_width_mm = 100.0
    paper.finish_height_mm = 150.0
    paper.bleed_mm = 5.0
    paper.inner_frame_width_mm = 80.0
    paper.inner_frame_height_mm = 120.0
    paper.inner_frame_offset_x_mm = 0.0
    paper.inner_frame_offset_y_mm = 0.0
    paper.safe_top_mm = 25.0
    paper.safe_bottom_mm = 25.0
    paper.safe_gutter_mm = 18.0
    paper.safe_fore_edge_mm = 12.0
    paper.show_guides = True
    paper.show_safe_line = True
    overlay = work.safe_area_overlay
    overlay.enabled = True
    overlay.opacity = 100.0
    overlay.color = (1.0, 0.0, 0.0)
    overlay.bleed_outer_enabled = True
    overlay.bleed_outer_opacity = 100.0
    overlay.bleed_outer_color = (0.25, 0.25, 0.25)


def _configure_basic_frame_comas(work) -> None:
    overlay_shared = _sub("ui.overlay_shared")
    rect = overlay_shared.compute_paper_rects(work.paper, is_left_half=False).inner_frame
    for page in list(getattr(work, "pages", []) or [])[:2]:
        if len(page.comas) == 0:
            continue
        coma = page.comas[0]
        coma.shape_type = "rect"
        coma.rect_x_mm = float(rect.x)
        coma.rect_y_mm = float(rect.y)
        coma.rect_width_mm = float(rect.width)
        coma.rect_height_mm = float(rect.height)
        coma.border.visible = True
        coma.border.style = "solid"
        coma.border.width_mm = 0.8
        coma.border.color = (0.0, 0.0, 0.0, 1.0)


def _mesh_bounds_mm(obj) -> tuple[float, float, float, float]:
    xs = [float(vertex.co.x) * 1000.0 for vertex in obj.data.vertices]
    ys = [float(vertex.co.y) * 1000.0 for vertex in obj.data.vertices]
    if not xs or not ys:
        raise AssertionError(f"塗りメッシュに頂点がありません: {obj.name}")
    return min(xs), max(xs), min(ys), max(ys)


def _assert_spread_fill_meshes(work, page_index: int, right_offset: float) -> tuple[object, object]:
    paper_guide_object = _sub("utils.paper_guide_object")
    scene = bpy.context.scene
    paper_guide_object.ensure_paper_guides_for_page(scene, work, page_index)
    spread = work.pages[page_index]
    safe_obj = bpy.data.objects.get(f"{paper_guide_object.PAPER_SAFE_FILL_PREFIX}{spread.id}")
    bleed_obj = bpy.data.objects.get(f"{paper_guide_object.PAPER_BLEED_OUTER_FILL_PREFIX}{spread.id}")
    if safe_obj is None:
        raise AssertionError("見開き化後のセーフライン外塗りが作られていません")
    if bleed_obj is None:
        raise AssertionError("見開き化後の裁ち落とし枠外塗りが作られていません")
    if bool(safe_obj.hide_viewport):
        raise AssertionError("見開き化後のセーフライン外塗りが非表示です")
    if bool(bleed_obj.hide_viewport):
        raise AssertionError("見開き化後の裁ち落とし枠外塗りが非表示です")
    if len(getattr(safe_obj.data, "polygons", []) or []) != 4:
        raise AssertionError("見開きのセーフライン外塗りが1つの合体矩形として作られていません")
    if len(getattr(bleed_obj.data, "polygons", []) or []) != 4:
        raise AssertionError("見開きの裁ち落とし枠外塗りが1つの合体矩形として作られていません")

    canvas_width = float(work.paper.canvas_width_mm)
    expected_x2 = right_offset + canvas_width
    for label, obj in (("セーフライン外", safe_obj), ("裁ち落とし枠外", bleed_obj)):
        x1, x2, y1, y2 = _mesh_bounds_mm(obj)
        _assert_close(x1, 0.0, f"{label}塗りの左端")
        _assert_close(x2, expected_x2, f"{label}塗りの右端")
        _assert_close(y1, 0.0, f"{label}塗りの下端")
        _assert_close(y2, float(work.paper.canvas_height_mm), f"{label}塗りの上端")
    if not (float(bleed_obj.location.z) > float(safe_obj.location.z)):
        raise AssertionError("裁ち落とし枠外塗りがセーフライン外塗りより手前にありません")
    return safe_obj, bleed_obj


def _world_bounds_xy(objects) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for obj in objects:
        for vertex in obj.data.vertices:
            co = obj.matrix_world @ vertex.co
            xs.append(float(co.x))
            ys.append(float(co.y))
    if not xs or not ys:
        raise AssertionError("レンダー範囲を決める頂点がありません")
    return min(xs), max(xs), min(ys), max(ys)


def _draw_mesh_object(draw, meta: dict[str, float], obj, color: tuple[int, int, int]) -> None:
    for poly in obj.data.polygons:
        points = []
        for index in poly.vertices:
            co = obj.matrix_world @ obj.data.vertices[index].co
            points.append(_world_to_pixel(meta, co))
        if len(points) >= 3:
            draw.polygon(points, fill=color)


def _draw_curve_object(draw, meta: dict[str, float], obj, color: tuple[int, int, int], width: int) -> None:
    for spline in getattr(obj.data, "splines", []) or []:
        points = []
        for point in spline.points:
            co = obj.matrix_world @ point.co.to_3d()
            points.append(_world_to_pixel(meta, co))
        if len(points) >= 2:
            if bool(getattr(spline, "use_cyclic_u", False)):
                points = points + [points[0]]
            draw.line(points, fill=color, width=width)


def _write_spread_fill_mesh_preview(safe_obj, bleed_obj, guide_obj, border_objs, name: str) -> tuple[Path, dict[str, float]]:
    from PIL import Image, ImageDraw

    bounds_objects = [safe_obj, bleed_obj]
    bounds_objects.extend(obj for obj in border_objs if getattr(obj, "type", "") == "MESH")
    x1, x2, y1, y2 = _world_bounds_xy(bounds_objects)
    margin_x = max((x2 - x1) * 0.04, 0.01)
    margin_y = max((y2 - y1) * 0.08, 0.01)
    meta = {
        "x_min": x1 - margin_x,
        "x_max": x2 + margin_x,
        "y_min": y1 - margin_y,
        "y_max": y2 + margin_y,
        "width": 1800.0,
        "height": 900.0,
    }
    image = Image.new("RGB", (int(meta["width"]), int(meta["height"])), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for obj in (safe_obj, bleed_obj):
        rgba = tuple(float(c) for c in getattr(obj, "color", (0.0, 0.0, 0.0, 1.0)))
        color = tuple(max(0, min(255, int(round(c * 255.0)))) for c in rgba[:3])
        _draw_mesh_object(draw, meta, obj, color)
    if guide_obj is not None:
        _draw_curve_object(draw, meta, guide_obj, (90, 210, 225), 3)
    for obj in border_objs:
        if getattr(obj, "type", "") == "MESH":
            _draw_mesh_object(draw, meta, obj, (0, 0, 0))
        elif getattr(obj, "type", "") == "CURVE":
            _draw_curve_object(draw, meta, obj, (0, 0, 0), 5)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    image.save(path)
    return path, meta


def _sample_rgb(path: Path, px: int, py: int, radius: int = 4) -> tuple[float, float, float]:
    from PIL import Image

    with Image.open(path) as opened:
        image = opened.convert("RGB")
        pixels = []
        for y in range(max(0, py - radius), min(image.height, py + radius + 1)):
            for x in range(max(0, px - radius), min(image.width, px + radius + 1)):
                pixels.append(image.getpixel((x, y)))
    if not pixels:
        raise AssertionError(f"画像サンプル位置が範囲外です: {path} ({px}, {py})")
    return tuple(sum(pixel[i] for pixel in pixels) / len(pixels) for i in range(3))


def _world_to_pixel(meta: dict[str, float], co: Vector) -> tuple[int, int]:
    px = (float(co.x) - meta["x_min"]) / (meta["x_max"] - meta["x_min"]) * meta["width"]
    py = (meta["y_max"] - float(co.y)) / (meta["y_max"] - meta["y_min"]) * meta["height"]
    return int(round(px)), int(round(py))


def _local_mm_to_world(obj, x_mm: float, y_mm: float) -> Vector:
    return obj.matrix_world @ Vector((x_mm / 1000.0, y_mm / 1000.0, 0.0))


def _assert_render_samples(path: Path, meta: dict[str, float], safe_obj, right_offset: float) -> dict[str, tuple[float, float, float]]:
    samples_mm = {
        "left_safe_top": (60.0, 155.0),
        "right_safe_top": (right_offset + 60.0, 155.0),
        "left_bleed_outer": (60.0, 2.5),
        "right_bleed_outer": (right_offset + 60.0, 2.5),
        "left_inside": (60.0, 85.0),
        "right_inside": (right_offset + 60.0, 85.0),
        "spread_center_inside": (right_offset * 0.5 + 60.0, 85.0),
    }
    samples = {}
    for label, (x_mm, y_mm) in samples_mm.items():
        px, py = _world_to_pixel(meta, _local_mm_to_world(safe_obj, x_mm, y_mm))
        samples[label] = _sample_rgb(path, px, py)

    for label in ("left_safe_top", "right_safe_top"):
        rgb = samples[label]
        if not (rgb[0] > 180.0 and rgb[1] < 90.0 and rgb[2] < 90.0):
            raise AssertionError(f"{label} にセーフライン外塗りの赤が出ていません: {rgb} image={path}")
    for label in ("left_bleed_outer", "right_bleed_outer"):
        rgb = samples[label]
        if not (35.0 <= rgb[0] <= 110.0 and 35.0 <= rgb[1] <= 110.0 and 35.0 <= rgb[2] <= 110.0):
            raise AssertionError(f"{label} に裁ち落とし枠外塗りのグレーが出ていません: {rgb} image={path}")
    for label in ("left_inside", "right_inside", "spread_center_inside"):
        rgb = samples[label]
        if not (rgb[0] > 210.0 and rgb[1] > 210.0 and rgb[2] > 210.0):
            raise AssertionError(f"{label} のセーフライン内側まで塗られています: {rgb} image={path}")
    return samples


def _assert_basic_frame_merged(path: Path, meta: dict[str, float], safe_obj, right_offset: float) -> None:
    left_old_inner_edge = 20.0 + 80.0
    right_old_inner_edge = right_offset + 20.0
    for label, x_mm in (("left_old_inner_edge", left_old_inner_edge), ("right_old_inner_edge", right_old_inner_edge)):
        px, py = _world_to_pixel(meta, _local_mm_to_world(safe_obj, x_mm, 85.0))
        rgb = _sample_rgb(path, px, py, radius=2)
        if not (rgb[0] > 180.0 and rgb[1] > 180.0 and rgb[2] > 180.0):
            raise AssertionError(f"基本枠が中央側で分断されています: {label}={rgb} image={path}")


def _assert_spread_fill_case(work, *, gap_mm: float, render: bool) -> Path | None:
    page_grid = _sub("utils.page_grid")
    paper_guide_object = _sub("utils.paper_guide_object")
    coma_border_object = _sub("utils.coma_border_object")

    _mark(f"case_start gap={gap_mm} render={render}")
    print(f"BMANGA_SPREAD_OVERLAY_FILL_CASE_START gap={gap_mm} render={render}", flush=True)
    _ensure_two_pages(work)
    _configure_basic_frame_comas(work)
    work.active_page_index = 0
    result = bpy.ops.bmanga.pages_merge_spread(
        "EXEC_DEFAULT",
        left_index=0,
        tombo_aligned=True,
        tombo_gap_mm=gap_mm,
    )
    if "FINISHED" not in result:
        raise AssertionError(f"見開き化に失敗しました: {result}")

    spread = work.pages[0]
    _mark(f"after_merge gap={gap_mm}")
    print(f"BMANGA_SPREAD_OVERLAY_FILL_AFTER_MERGE gap={gap_mm}", flush=True)
    right_offset = page_grid.spread_right_page_offset_mm(spread, float(work.paper.canvas_width_mm))
    safe_obj, bleed_obj = _assert_spread_fill_meshes(work, 0, right_offset)
    signature = str(safe_obj.get(paper_guide_object.PROP_GUIDE_SIGNATURE, "") or "")
    if "paper_guide_spread_fill_v3" not in signature:
        raise AssertionError("見開き塗りの再生成署名が更新されていません")

    image_path = None
    if render:
        coma_border_object.regenerate_all_coma_borders(bpy.context.scene, work)
        guide_obj = bpy.data.objects.get(f"{paper_guide_object.PAPER_GUIDE_PREFIX}{spread.id}")
        border_objs = [
            obj
            for obj in bpy.data.objects
            if obj.name.startswith(f"{coma_border_object.COMA_BORDER_NAME_PREFIX}{spread.id}_")
            and not bool(getattr(obj, "hide_viewport", False))
        ]
        image_path, meta = _write_spread_fill_mesh_preview(
            safe_obj,
            bleed_obj,
            guide_obj,
            border_objs,
            "spread_overlay_fill.png",
        )
        samples = _assert_render_samples(image_path, meta, safe_obj, right_offset)
        _assert_basic_frame_merged(image_path, meta, safe_obj, right_offset)
        print(
            "BMANGA_SPREAD_OVERLAY_FILL_VISUAL_SAMPLES "
            + " ".join(f"{key}={tuple(round(v, 1) for v in rgb)}" for key, rgb in samples.items()),
            flush=True,
        )

    _mark(f"before_split gap={gap_mm}")
    print(f"BMANGA_SPREAD_OVERLAY_FILL_BEFORE_SPLIT gap={gap_mm}", flush=True)
    result = bpy.ops.bmanga.pages_split_spread("EXEC_DEFAULT", spread_index=0)
    if "FINISHED" not in result:
        raise AssertionError(f"見開き解除に失敗しました: {result}")
    _mark(f"after_split gap={gap_mm}")
    print(f"BMANGA_SPREAD_OVERLAY_FILL_AFTER_SPLIT gap={gap_mm}", flush=True)
    return image_path


def main() -> None:
    mod = None
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_spread_overlay_fill_"))
    success = False
    try:
        if STAGE_LOG.exists():
            STAGE_LOG.unlink()
        _mark("main_start")
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "SpreadOverlayFill.bmanga"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")
        work = bpy.context.scene.bmanga_work
        _configure_work(work)

        image_path = _assert_spread_fill_case(work, gap_mm=0.0, render=True)
        _assert_spread_fill_case(work, gap_mm=-10.0, render=False)
        _assert_spread_fill_case(work, gap_mm=15.0, render=False)
        print(f"BMANGA_SPREAD_OVERLAY_FILL_VISUAL_OK image={image_path}", flush=True)
        success = True
    finally:
        _mark("finally_start")
        if mod is not None:
            try:
                _mark("unregister_start")
                mod.unregister()
                _mark("unregister_done")
            except Exception:
                _mark("unregister_failed")
                pass
        _mark("factory_reset_start")
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _mark("factory_reset_done")
        shutil.rmtree(temp_root, ignore_errors=True)
        _mark("finally_done")
        _mark(f"force_exit success={success}")
        os._exit(0 if success else 1)


if __name__ == "__main__":
    main()
