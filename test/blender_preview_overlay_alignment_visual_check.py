"""Blender実機用: コマプレビューの明るさとページずれオーバーレイを目視/数値確認."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(
    os.environ.get("BMANGA_PREVIEW_OVERLAY_VISUAL_OUT", "")
    or tempfile.mkdtemp(prefix="bmanga_preview_overlay_visual_")
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_preview_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_preview_visual"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _font(ImageFont, *, size: int):
    for path in (
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
    ):
        try:
            if Path(path).is_file():
                return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _view3d_context():
    screen = bpy.context.screen
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type == "WINDOW":
                return area, region, area.spaces.active.region_3d
    raise RuntimeError("VIEW_3D が見つかりません")


def _screen_point_for_mm(region, rv3d, x_mm: float, y_mm: float):
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from bmanga_dev_preview_visual.utils.geom import mm_to_m

    return location_3d_to_region_2d(
        region,
        rv3d,
        (mm_to_m(x_mm), mm_to_m(y_mm), 0.0),
    )


def _screenshot(name: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=4)
    except Exception:
        pass
    result = bpy.ops.screen.screenshot("EXEC_DEFAULT", filepath=str(path), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"screenshot failed: {result}")
    return path


def _create_preview_png(path: Path, *, fill: int = 220) -> None:
    from PIL import Image, ImageDraw, ImageFont

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (360, 240), (fill, fill, fill, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 359, 239), outline=(35, 35, 35, 255), width=8)
    draw.rectangle((40, 40, 160, 120), fill=(245, 245, 245, 255))
    draw.rectangle((190, 40, 320, 120), fill=(190, 190, 190, 255))
    draw.line((24, 190, 336, 190), fill=(0, 0, 0, 255), width=6)
    font = _font(ImageFont, size=34)
    draw.text((46, 148), "PREVIEW 220", fill=(20, 20, 20, 255), font=font)
    image.save(path)


def _sample_rgb(path: Path, x: int, y: int, radius: int = 5) -> tuple[float, float, float]:
    from PIL import Image

    with Image.open(path) as opened:
        image = opened.convert("RGB")
        width, height = image.size
        pixels = []
        for py in range(max(0, y - radius), min(height, y + radius + 1)):
            for px in range(max(0, x - radius), min(width, x + radius + 1)):
                pixels.append(image.getpixel((px, py)))
    if not pixels:
        return 0.0, 0.0, 0.0
    return tuple(sum(p[i] for p in pixels) / len(pixels) for i in range(3))


def _draw_contact_sheet(items: list[dict]) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    font = _font(ImageFont, size=18)
    label_font = _font(ImageFont, size=15)
    thumbs = []
    for item in items:
        with Image.open(item["screenshot"]) as opened:
            img = opened.convert("RGB")
            img.thumbnail((700, 410))
            thumbs.append(img.copy())
    width = 760
    row_h = 480
    sheet = Image.new("RGB", (width, 80 + row_h * len(items)), "white")
    draw = ImageDraw.Draw(sheet)
    draw.text((24, 20), "B-MANGA コマプレビュー/ハンドル位置 AI目視シート", fill=(0, 0, 0), font=font)
    y = 70
    for item, thumb in zip(items, thumbs):
        draw.text(
            (24, y),
            f"{item['label']}  sample RGB={item['sample_rgb']}  OK={item['ok']}",
            fill=(0, 0, 0),
            font=label_font,
        )
        sheet.paste(thumb, (24, y + 30))
        y += row_h
    path = OUT_DIR / "preview_overlay_alignment_contact_sheet.png"
    sheet.save(path)
    return path


def _setup_scene(temp_root: Path):
    mod = _load_addon()
    from bmanga_dev_preview_visual.ui import overlay_image

    image_shader = overlay_image._get_image_layer_shader()
    if image_shader is None:
        raise AssertionError("画像レイヤーの描画シェーダーを作成できません")

    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PreviewOverlayVisual.bmanga"))
    assert result == {"FINISHED"}, result
    for _ in range(2):
        result = bpy.ops.bmanga.page_add()
        assert result == {"FINISHED"}, result

    from bmanga_dev_preview_visual.core.work import get_work
    from bmanga_dev_preview_visual.operators import balloon_op, effect_line_op, text_op
    from bmanga_dev_preview_visual.utils import layer_hierarchy, object_selection, page_grid, paths

    context = bpy.context
    work = get_work(context)
    assert work is not None
    work.paper.canvas_width_mm = 210.0
    work.paper.canvas_height_mm = 297.0
    context.scene.bmanga_overview_cols = 4
    context.scene.bmanga_overview_gap_mm = 32.0
    context.scene.bmanga_overview_mode = True

    page_index = 2
    page = work.pages[page_index]
    panel = page.comas[0]
    panel.rect_x_mm = 32.0
    panel.rect_y_mm = 72.0
    panel.rect_width_mm = 122.0
    panel.rect_height_mm = 142.0
    panel.background_color = (1.0, 1.0, 1.0, 1.0)
    thumb = paths.coma_thumb_path(Path(work.work_dir), page.id, panel.coma_id)
    _create_preview_png(thumb)

    coma_key = layer_hierarchy.coma_stack_key(page, panel)
    effect_obj, effect_layer = effect_line_op._create_effect_layer(
        context,
        (70.0, 154.0, 55.0, 42.0),
        parent_key=coma_key,
    )
    balloon = balloon_op._create_balloon_entry(
        context,
        page,
        shape="ellipse",
        x=96.0,
        y=94.0,
        w=42.0,
        h=24.0,
        parent_kind="coma",
        parent_key=coma_key,
    )
    text, missing = text_op._create_text_entry(
        context,
        page,
        body="ハンドル",
        x_mm=45.0,
        y_mm=94.0,
        width_mm=34.0,
        height_mm=22.0,
        parent_kind="coma",
        parent_key=coma_key,
    )
    assert not missing

    object_selection.set_keys(
        context,
        [
            object_selection.effect_key(effect_layer),
            object_selection.balloon_key(page, balloon),
            object_selection.text_key(page, text),
        ],
    )
    page_grid.apply_page_collection_transforms(context, work)
    return mod, work, page_index, page, panel, effect_obj, effect_layer


def _check_case(context, work, page_index: int, panel, *, start_side: str, read_direction: str) -> dict:
    from bmanga_dev_preview_visual.operators import effect_line_op, object_tool_selection
    from bmanga_dev_preview_visual.utils import object_selection, page_grid

    work.paper.start_side = start_side
    work.paper.read_direction = read_direction
    page_grid.apply_page_collection_transforms(context, work)
    work.active_page_index = page_index
    result = bpy.ops.bmanga.view_fit_page("EXEC_DEFAULT")
    assert "FINISHED" in result, result
    area, region, rv3d = _view3d_context()
    _ = area
    screenshot = _screenshot(f"preview_overlay_{start_side}_{read_direction}.png")

    ox, oy = page_grid.page_total_offset_mm(work, context.scene, page_index)
    sample_x_mm = ox + float(panel.rect_x_mm) + 20.0
    sample_y_mm = oy + float(panel.rect_y_mm) + 42.0
    point = _screen_point_for_mm(region, rv3d, sample_x_mm, sample_y_mm)
    if point is None:
        raise AssertionError("サンプル地点が画面外です")
    from PIL import Image

    with Image.open(screenshot) as opened:
        image_h = opened.height
    px = int(round(region.x + float(point.x)))
    py = int(round(image_h - (region.y + float(point.y))))
    rgb = _sample_rgb(screenshot, px, py)
    mean = sum(rgb) / 3.0
    if mean < 185.0:
        raise AssertionError(
            f"コマプレビューが暗すぎます: {start_side}/{read_direction} RGB={rgb}"
        )

    selected = object_selection.get_keys(context)
    for key in selected:
        rect = object_tool_selection.selection_bounds_for_key(context, key)
        if rect is None:
            raise AssertionError(f"ハンドル範囲がありません: {key}")
        cx = float(rect.x) + float(rect.width) * 0.5
        cy = float(rect.y) + float(rect.height) * 0.5
        local_x = cx - ox
        local_y = cy - oy
        if not (0.0 <= local_x <= work.paper.canvas_width_mm and 0.0 <= local_y <= work.paper.canvas_height_mm):
            raise AssertionError(
                f"ハンドルが対象ページからずれています: {key} local=({local_x:.3f},{local_y:.3f})"
            )

    effect_obj, effect_layer, bounds = effect_line_op.active_effect_layer_bounds(context)
    world_bounds = effect_line_op.effect_layer_world_bounds(context, effect_obj, effect_layer, bounds)
    if world_bounds is None:
        raise AssertionError("効果線ハンドル範囲が取得できません")
    if abs(float(world_bounds[0]) - (ox + 70.0)) > 0.01 or abs(float(world_bounds[1]) - (oy + 154.0)) > 0.01:
        raise AssertionError(
            f"効果線ハンドルがページずれしています: {start_side}/{read_direction} {world_bounds}"
        )

    return {
        "label": f"開始ページ={start_side} / 読む方向={read_direction}",
        "screenshot": str(screenshot),
        "sample_rgb": tuple(round(v, 1) for v in rgb),
        "ok": True,
    }


def _run_visual_check() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        bpy.context.preferences.view.show_splash = False
    except Exception:
        pass
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_preview_overlay_visual_"))
    mod = None
    try:
        mod, work, page_index, _page, panel, _effect_obj, _effect_layer = _setup_scene(temp_root)
        context = bpy.context
        items = []
        for start_side, read_direction in (
            ("right", "left"),
            ("left", "right"),
            ("right", "down"),
        ):
            items.append(
                _check_case(
                    context,
                    work,
                    page_index,
                    panel,
                    start_side=start_side,
                    read_direction=read_direction,
                )
            )
        contact = _draw_contact_sheet(items)
        json_path = OUT_DIR / "preview_overlay_alignment_visual.json"
        json_path.write_text(
            json.dumps({"contact_sheet": str(contact), "items": items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"BMANGA_PREVIEW_OVERLAY_VISUAL_OK visual={contact}", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


def _has_view3d_context() -> bool:
    try:
        _view3d_context()
        return True
    except Exception:
        return False


def _visual_check_tick():
    if not _has_view3d_context():
        return 0.25
    try:
        _run_visual_check()
        sys.stdout.flush()
        os._exit(0)
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)
    return None


def main() -> None:
    bpy.app.timers.register(_visual_check_tick, first_interval=0.25)


if __name__ == "__main__":
    main()
