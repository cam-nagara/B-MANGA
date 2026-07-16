"""Blender 5.1: 本文・ルビの記号／濁音／半濁音／小書き仮名を縦横で描画確認する。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "_verify" / "2026-07-16_ruby_symbols_visual"
REPORT = OUT_DIR / "result.json"
BOARD = OUT_DIR / "ruby_symbols_vertical_horizontal.png"
MODULE_NAME = "bmanga_dev_ruby_symbols_visual"


NORMAL_LINES = (
    "句読：、。・，．？！‼⁉…‥〜ー―",
    "括弧：「」『』（）［］【】〈〉《》〔〕｛｝",
    "記号：※〒〆々ゝゞヽヾ○●◎☆★♡♥♪♫♬",
    "算数：＋−×÷＝≠≦≧∞％‰℃￥＄€£¢",
    "濁音：がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽゔ",
    "小書：ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶ",
    "欧数：!?　#$　%&　()　+-　=/　\\_　|~　01　AB",
)
RUBY_READINGS = (
    "、。・，．？！‼⁉…‥〜ー―",
    "「」『』（）［］【】〈〉《》〔〕｛｝",
    "※〒〆々ゝゞヽヾ○●◎☆★♡♥♪♫♬",
    "＋−×÷＝≠≦≧∞％‰℃￥＄€£¢",
    "がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽゔ",
    "ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶ",
    "!?#$%&()+-=/\\_|~01AB",
)
BASE_PATTERN = "天地玄黄宇宙洪荒日月盈昃辰宿列張寒来暑往秋収冬蔵"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module.register()
    return module


def _base_text_and_ranges() -> tuple[str, list[tuple[int, int, str]]]:
    lines = []
    ranges = []
    cursor = 0
    for reading in RUBY_READINGS:
        repeats = (len(reading) + len(BASE_PATTERN) - 1) // len(BASE_PATTERN)
        base = (BASE_PATTERN * repeats)[:len(reading)]
        lines.append(base)
        ranges.append((cursor, len(base), reading))
        cursor += len(base) + 1
    return "\n".join(lines), ranges


def _configure(entry, *, writing_mode: str, with_ruby: bool, small_kana: str) -> None:
    from bmanga_dev_ruby_symbols_visual.utils import text_real_object

    with text_real_object.suspend_auto_sync():
        entry.id = f"symbols_{writing_mode}_{'ruby' if with_ruby else 'normal'}_{small_kana}"
        entry.title = entry.id
        entry.writing_mode = writing_mode
        entry.font_size_q = 16.0
        entry.line_height = 1.45
        entry.letter_spacing = 0.04
        entry.ruby_size_percent = 55.0
        entry.ruby_gap_em = 0.18
        entry.ruby_letter_spacing = 0.08
        entry.ruby_line_height = 1.9
        entry.ruby_align = "center"
        entry.ruby_small_kana = small_kana
        entry.ruby_font_preset = "inherit"
        entry.stroke_enabled = False
        entry.tatechuyoko_auto = True
        if writing_mode == "vertical":
            entry.width_mm = 78.0
            entry.height_mm = 170.0
        else:
            entry.width_mm = 190.0
            entry.height_mm = 74.0
        entry.ruby_spans.clear()
        if with_ruby:
            body, ranges = _base_text_and_ranges()
            entry.body = body
            for start, length, reading in ranges:
                span = entry.ruby_spans.add()
                span.start = start
                span.length = length
                span.ruby_text = reading
                span.style = "group"
        else:
            entry.body = "\n".join(NORMAL_LINES)


def _layout_and_ruby(entry):
    from bmanga_dev_ruby_symbols_visual.typography import layout as text_layout
    from bmanga_dev_ruby_symbols_visual.typography import ruby as ruby_layout
    from bmanga_dev_ruby_symbols_visual.utils import text_layout_bounds
    from bmanga_dev_ruby_symbols_visual.utils.geom import Rect

    inner = text_layout_bounds.text_inner_rect(
        Rect(0.0, 0.0, float(entry.width_mm), float(entry.height_mm))
    )
    result = text_layout.typeset(entry, inner.x, inner.y, inner.width, inner.height)
    assert not result.overflow, f"本文が枠からあふれました: {entry.id}"
    ruby = ruby_layout.compute_for_entry(result.placements, entry)
    return result, ruby


def _save_render(entry, text_real_object) -> tuple[Path, dict]:
    from PIL import Image

    result, ruby = _layout_and_ruby(entry)
    rendered = text_real_object._render_entry_to_pillow(entry)
    assert rendered is not None
    image, pad_mm, width_mm, height_mm = rendered
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    assert bbox is not None, f"描画が空です: {entry.id}"
    assert bbox[0] > 0 and bbox[1] > 0 and bbox[2] < image.width and bbox[3] < image.height, (
        entry.id, bbox, image.size,
    )
    paper = Image.new("RGBA", image.size, (248, 247, 243, 255))
    paper.alpha_composite(image)
    output = OUT_DIR / f"{entry.id}.png"
    paper.convert("RGB").save(output)
    expected_main = len(entry.body.replace("\n", ""))
    assert len(result.placements) == expected_main, (entry.id, len(result.placements), expected_main)
    if len(entry.ruby_spans):
        expected_ruby = sum(len(span.ruby_text) for span in entry.ruby_spans)
        assert len(ruby) == expected_ruby, (entry.id, len(ruby), expected_ruby)
    if entry.writing_mode == "horizontal":
        assert all(item.rotation_deg == 0.0 for item in ruby)
        assert all(item.offset_x_mm == 0.0 and item.offset_y_mm == 0.0 for item in ruby)
    elif ruby:
        ruby_map = {item.ch: item for item in ruby}
        assert ruby_map["「"].rotation_deg == -90.0
        assert ruby_map["A"].rotation_deg == 0.0, "縦書きルビのASCIIはuprightでなければならない"
        assert ruby_map["、"].offset_x_mm > 0.0 and ruby_map["、"].offset_y_mm > 0.0
        if entry.ruby_small_kana == "keep":
            assert ruby_map["ゃ"].offset_x_mm > 0.0 and ruby_map["っ"].offset_y_mm > 0.0
    ruby_text = "".join(item.ch for item in ruby)
    if entry.ruby_small_kana == "fullsize" and ruby:
        assert not any(
            ch in ruby_text
            for ch in "ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶ"
        )
        assert all(ch in ruby_text for ch in "あいうえおつやゆよわかけアイウエオツヤユヨワカケ")
    return output, {
        "id": entry.id,
        "writingMode": entry.writing_mode,
        "rubySmallKana": entry.ruby_small_kana,
        "mainGlyphs": len(result.placements),
        "rubyGlyphs": len(ruby),
        "rotatedRubyGlyphs": sum(item.rotation_deg != 0.0 for item in ruby),
        "offsetRubyGlyphs": sum(item.offset_x_mm != 0.0 or item.offset_y_mm != 0.0 for item in ruby),
        "bbox": list(bbox),
        "imageSize": list(image.size),
        "padMm": pad_mm,
        "frameMm": [width_mm, height_mm],
        "image": str(output),
    }


def _compose_board(items: list[dict]) -> None:
    from PIL import Image, ImageDraw, ImageFont

    font_path = Path(r"C:\Windows\Fonts\YuGothM.ttc")
    font = ImageFont.truetype(str(font_path), 20) if font_path.is_file() else ImageFont.load_default()
    thumb_size = (540, 430)
    canvas = Image.new("RGB", (thumb_size[0] * 2 + 54, thumb_size[1] * 3 + 110), (31, 32, 35))
    draw = ImageDraw.Draw(canvas)
    draw.text((22, 18), "B-MANGA Blender 5.1.2 — 本文・ルビ記号 縦横実機", fill="white", font=font)
    for index, item in enumerate(items):
        row, column = divmod(index, 2)
        x = 18 + column * (thumb_size[0] + 18)
        y = 66 + row * (thumb_size[1] + 12)
        image = Image.open(item["image"]).convert("RGB")
        image.thumbnail((thumb_size[0] - 20, thumb_size[1] - 52), Image.Resampling.LANCZOS)
        draw.rounded_rectangle((x, y, x + thumb_size[0], y + thumb_size[1]), radius=8,
                               fill=(44, 45, 48), outline=(94, 96, 101))
        draw.text((x + 10, y + 8), item["id"], fill=(230, 230, 230), font=font)
        canvas.paste(image, (x + 10, y + 42))
    canvas.save(BOARD)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _load_addon()
    try:
        from bmanga_dev_ruby_symbols_visual.utils import text_real_object

        work = bpy.context.scene.bmanga_work
        page = work.pages.add()
        reports = []
        for writing_mode in ("vertical", "horizontal"):
            for with_ruby, small_kana in ((False, "keep"), (True, "keep"), (True, "fullsize")):
                entry = page.texts.add()
                _configure(
                    entry,
                    writing_mode=writing_mode,
                    with_ruby=with_ruby,
                    small_kana=small_kana,
                )
                _output, report = _save_render(entry, text_real_object)
                reports.append(report)
        _compose_board(reports)
        REPORT.write_text(
            json.dumps({"cases": reports, "board": str(BOARD)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"BMANGA_RUBY_SYMBOLS_VISUAL_OK {BOARD}", flush=True)
    finally:
        addon.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
