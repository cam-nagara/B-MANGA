"""Blender実機用: ページ右クリックメニューの見開き化を画像で確認."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(os.environ.get("BMANGA_SPREAD_CONTEXT_VISUAL_OUT", "") or ROOT / "_verify" / "spread_context_menu")
CONTACT_PATH = OUT_DIR / "spread_context_menu_visual.png"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_spread_context_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_spread_context_visual"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _draw_work_state(path: Path, work, title: str) -> None:
    from PIL import Image, ImageDraw, ImageFont

    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 720, 360
    image = Image.new("RGB", (width, height), (245, 245, 245))
    draw = ImageDraw.Draw(image)
    try:
        title_font = ImageFont.truetype("arial.ttf", 22)
        label_font = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        title_font = ImageFont.load_default()
        label_font = ImageFont.load_default()

    pages = list(getattr(work, "pages", []) or [])
    units = [2 if bool(getattr(page, "spread", False)) else 1 for page in pages]
    total_units = max(1, sum(units))
    gap = 24
    page_w = min(150, int((width - 80 - gap * (len(pages) - 1)) / total_units))
    page_h = int(page_w * 1.42)
    total_w = sum(page_w * u for u in units) + gap * max(0, len(pages) - 1)
    x = (width - total_w) // 2
    y = 112

    draw.text((24, 24), title, fill=(0, 0, 0), font=title_font)
    draw.text((24, 58), "右クリックメニュー項目から実行したページ状態", fill=(64, 64, 64), font=label_font)
    for index, page in enumerate(pages):
        is_spread = bool(getattr(page, "spread", False))
        rect_w = page_w * (2 if is_spread else 1)
        fill = (232, 242, 255) if is_spread else (255, 255, 255)
        outline = (219, 0, 157) if index == int(getattr(work, "active_page_index", -1)) else (100, 180, 190)
        draw.rectangle((x, y, x + rect_w, y + page_h), fill=fill, outline=outline, width=4)
        draw.rectangle((x + 14, y + 18, x + rect_w - 14, y + page_h - 18), outline=(150, 190, 200), width=2)
        if is_spread:
            mid = x + rect_w // 2
            draw.line((mid, y + 5, mid, y + page_h - 5), fill=(120, 170, 190), width=2)
        label = str(getattr(page, "id", "") or getattr(page, "title", "") or f"{index + 1:03d}")
        draw.text((x + 10, y - 30), label, fill=(0, 0, 0), font=label_font)
        x += rect_w + gap
    image.save(path)


def _make_contact_sheet(paths: list[Path]) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    thumbs = [Image.open(path).convert("RGB") for path in paths]
    thumb_w, thumb_h = 480, 240
    canvas = Image.new("RGB", (thumb_w * len(thumbs), thumb_h + 50), "white")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("arial.ttf", 17)
    except Exception:
        font = ImageFont.load_default()
    labels = ["単ページ", "見開きに変更", "見開きを解除"]
    for index, (thumb, label) in enumerate(zip(thumbs, labels, strict=True)):
        thumb.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        x0 = index * thumb_w + (thumb_w - thumb.width) // 2
        y0 = 42 + (thumb_h - thumb.height) // 2
        canvas.paste(thumb, (x0, y0))
        draw.text((index * thumb_w + 16, 12), label, fill=(0, 0, 0), font=font)
    CONTACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(CONTACT_PATH)
    return CONTACT_PATH


def _context_menu_item(label: str) -> dict:
    from bmanga_dev_spread_context_visual.ui import context_menu

    items = context_menu.selection_command_items(bpy.context)
    for item in items:
        if str(item.get("label", "") or "") == label:
            return item
    raise AssertionError(f"右クリックメニュー項目がありません: {label}")


def _select_page_via_context_menu(work, page_index: int) -> None:
    from bmanga_dev_spread_context_visual.operators import object_tool_op, selection_context_menu
    from bmanga_dev_spread_context_visual.utils import object_selection

    class _Event:
        ctrl = False
        shift = False

    original_hit = object_tool_op.hit_object_at_event
    original_call = selection_context_menu._call_selection_menu
    try:
        object_tool_op.hit_object_at_event = lambda _context, _event: {
            "kind": "page",
            "page": page_index,
            "part": "body",
            "key": object_selection.page_key(work.pages[page_index]),
        }
        selection_context_menu._call_selection_menu = lambda _context, _event=None: True
        assert selection_context_menu.open_for_viewport_object(bpy.context, _Event())
    finally:
        object_tool_op.hit_object_at_event = original_hit
        selection_context_menu._call_selection_menu = original_call
    if int(work.active_page_index) != page_index:
        raise AssertionError(f"右クリック対象ページが操作対象になっていません: {work.active_page_index}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_spread_context_visual_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "SpreadContextVisual.bmanga"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")
        work = bpy.context.scene.bmanga_work
        while len(work.pages) < 3:
            result = bpy.ops.bmanga.page_add()
            if "FINISHED" not in result:
                raise AssertionError(f"ページ追加に失敗しました: {result}")
        for index, page in enumerate(work.pages[:3], start=1):
            page.title = f"{index:03d}"

        before = OUT_DIR / "01_before.png"
        _draw_work_state(before, work, "右クリック前: 001 / 002 / 003")

        _select_page_via_context_menu(work, 0)
        merge_item = _context_menu_item("見開きに変更")
        if not bool(merge_item.get("enabled", False)):
            raise AssertionError("右クリックメニューの「見開きに変更」が有効ではありません")
        result = bpy.ops.bmanga.pages_merge_spread("EXEC_DEFAULT", **dict(merge_item.get("props", {}) or {}))
        if "FINISHED" not in result:
            raise AssertionError(f"右クリックメニュー経由の見開き化に失敗しました: {result}")
        if not bool(work.pages[0].spread) or "-" not in str(work.pages[0].id):
            raise AssertionError("見開きページになっていません")
        spread = OUT_DIR / "02_spread.png"
        _draw_work_state(spread, work, "見開きに変更: 001-002 / 003")

        _select_page_via_context_menu(work, 0)
        split_item = _context_menu_item("見開きを解除")
        if not bool(split_item.get("enabled", False)):
            raise AssertionError("右クリックメニューの「見開きを解除」が有効ではありません")
        result = bpy.ops.bmanga.pages_split_spread("EXEC_DEFAULT", **dict(split_item.get("props", {}) or {}))
        if "FINISHED" not in result:
            raise AssertionError(f"右クリックメニュー経由の見開き解除に失敗しました: {result}")
        if bool(work.pages[0].spread):
            raise AssertionError("見開き解除後も見開きページのままです")
        page_ids = [str(page.id) for page in work.pages[:3]]
        if page_ids != ["p0001", "p0002", "p0003"]:
            raise AssertionError(f"見開き解除後のページ並びが戻っていません: {page_ids}")
        split = OUT_DIR / "03_split.png"
        _draw_work_state(split, work, "見開きを解除: 001 / 002 / 003")

        contact = _make_contact_sheet([before, spread, split])
        print(f"BMANGA_SPREAD_CONTEXT_MENU_VISUAL_OK visual={contact}", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
