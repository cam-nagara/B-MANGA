"""Blender 5.1実機: 縦記号の中央揃えと本文・ルビ共通フチを検証する."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_text_fringe_symbol"
OUTPUT_DIR = ROOT / "_verify" / "2026-07-17_text_fringe_symbol_visual"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module.register()
    return module


def _font_paths() -> list[Path]:
    paths = [
        Path(r"C:\Windows\Fonts\YuGothM.ttc"),
        Path(r"C:\Windows\Fonts\meiryo.ttc"),
        Path(r"C:\Windows\Fonts\msgothic.ttc"),
    ]
    found = [path for path in paths if path.is_file()]
    assert len(found) >= 2, "複数の日本語フォントを検証できません"
    return found


def _ink_center_em(export_renderer, layout, Image, font_path: Path, ch: str) -> float:
    size_pt = 40.0
    px_per_mm = 18.0
    em_mm = size_pt * 25.4 / 72.0
    pad_mm = em_mm * 2.0
    result = layout.TypesetResult([
        layout.GlyphPlacement(ch, pad_mm, pad_mm, size_pt, 0.0, 0),
    ], False)
    size = int(round((em_mm + pad_mm * 2.0) * px_per_mm))
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    export_renderer.render_to_image(
        result, image, font_path=str(font_path), px_per_mm=px_per_mm,
        color=(0, 0, 0, 255), writing_mode="vertical",
    )
    bbox = image.getchannel("A").getbbox()
    assert bbox is not None, (font_path, ch, result.placements, size)
    glyph = result.placements[0]
    size_px = int(glyph.size_pt * px_per_mm * 25.4 / 72.0)
    cell_left = glyph.x_mm * px_per_mm
    ink_center = (bbox[0] + bbox[2]) * 0.5
    return (ink_center - (cell_left + size_px * 0.5)) / size_px


def _local_alpha_bbox(image, left: int, right: int):
    bbox = image.crop((left, 0, right, image.height)).getchannel("A").getbbox()
    assert bbox is not None
    return bbox


def _assert_uniform_fringe(export_renderer, layout, ruby, Image, font_path: Path) -> None:
    result = layout.TypesetResult([
        layout.GlyphPlacement("本", 14.0, 18.0, 40.0, 0.0, 0),
    ], False)
    ruby_placements = [ruby.RubyPlacement("ル", 66.0, 18.0, 20.0, str(font_path))]

    def render(stroke_width: int):
        image = Image.new("RGBA", (900, 260), (0, 0, 0, 0))
        export_renderer.render_to_image(
            result, image, font_path=str(font_path), px_per_mm=7.0,
            color=(20, 20, 20, 255), stroke_width_px=stroke_width,
            stroke_color=(245, 120, 45, 255), ruby_placements=ruby_placements,
        )
        return image

    fill = render(0)
    fringe = render(9)
    body_fill = _local_alpha_bbox(fill, 0, 420)
    body_fringe = _local_alpha_bbox(fringe, 0, 420)
    ruby_fill = _local_alpha_bbox(fill, 420, 900)
    ruby_fringe = _local_alpha_bbox(fringe, 420, 900)
    body_expand = ((body_fringe[2] - body_fringe[0]) - (body_fill[2] - body_fill[0])) * 0.5
    ruby_expand = ((ruby_fringe[2] - ruby_fringe[0]) - (ruby_fill[2] - ruby_fill[0])) * 0.5
    assert abs(body_expand - ruby_expand) <= 1.0, (
        f"本文とルビでフチ幅が異なります: body={body_expand} ruby={ruby_expand}"
    )


def _assert_fringe_does_not_cover_previous_fill(export_renderer, layout, Image, font_path: Path) -> None:
    result = layout.TypesetResult([
        layout.GlyphPlacement("あ", 14.0, 16.0, 40.0, 0.0, 0),
        layout.GlyphPlacement("い", 25.0, 16.0, 40.0, 0.0, 1),
    ], False)
    colors = ((220, 25, 25, 255), (30, 70, 220, 255))

    def render(stroke_width: int):
        image = Image.new("RGBA", (520, 250), (0, 0, 0, 0))
        export_renderer.render_to_image(
            result, image, font_path=str(font_path), px_per_mm=7.0,
            color_for_index=lambda index: colors[index],
            stroke_width_px=stroke_width,
            stroke_color=(20, 210, 60, 255),
        )
        return image

    baseline = render(0)
    fringed = render(10)
    baseline_pixels = baseline.load()
    fringed_pixels = fringed.load()
    hidden = 0
    for y in range(baseline.height):
        for x in range(baseline.width):
            before = baseline_pixels[x, y]
            after = fringed_pixels[x, y]
            was_first_fill = before[3] == 255 and before[0] > 180 and before[1] < 80 and before[2] < 80
            became_fringe = after[1] > 190 and after[0] < 60 and after[2] < 100
            hidden += int(was_first_fill and became_fringe)
    assert hidden == 0, f"後続文字のフチが前の文字を隠しています: {hidden} pixels"


def _visual_panel(export_renderer, layout, ruby, Image, ImageDraw, font_path: Path):
    panel = Image.new("RGBA", (570, 740), (250, 250, 250, 255))
    draw = ImageDraw.Draw(panel)
    draw.text((18, 14), font_path.name, fill=(20, 20, 20, 255))
    result = layout.typeset_vertical(
        "記号・！？：；", 60.0, 18.0, 80.0, 110.0, font_size_pt=34.0,
    )
    export_renderer.render_to_image(
        result, panel, font_path=str(font_path), px_per_mm=4.6,
        origin_xy_px=(-210.0, 90.0), color=(15, 30, 80, 255), writing_mode="vertical",
    )
    body = layout.TypesetResult([
        layout.GlyphPlacement("本", 18.0, 18.0, 40.0, 0.0, 0),
        layout.GlyphPlacement("文", 31.0, 18.0, 40.0, 0.0, 1),
    ], False)
    rubies = [
        ruby.RubyPlacement("ル", 52.0, 18.0, 20.0, str(font_path)),
        ruby.RubyPlacement("ビ", 59.0, 18.0, 20.0, str(font_path)),
    ]
    export_renderer.render_to_image(
        body, panel, font_path=str(font_path), px_per_mm=4.6,
        origin_xy_px=(30.0, 70.0), color=(25, 25, 25, 255),
        stroke_width_px=7, stroke_color=(245, 120, 45, 255), ruby_placements=rubies,
    )
    draw.text((18, 690), "vertical symbols / same-width fringe", fill=(20, 20, 20, 255))
    return panel


def main() -> None:
    addon = _load_addon()
    try:
        from PIL import Image, ImageDraw
        from bmanga_dev_text_fringe_symbol.typography import export_renderer, layout, ruby

        fonts = _font_paths()
        for font_path in fonts:
            for ch in "・！？":
                dx_em = _ink_center_em(export_renderer, layout, Image, font_path, ch)
                assert abs(dx_em) <= 0.035, (
                    f"{font_path.name} の {ch!r} が縦セル中央にありません: dx_em={dx_em:.3f}"
                )
            _assert_uniform_fringe(export_renderer, layout, ruby, Image, font_path)
        _assert_fringe_does_not_cover_previous_fill(export_renderer, layout, Image, fonts[0])

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        board = Image.new("RGBA", (len(fonts) * 590 + 20, 780), (225, 225, 225, 255))
        for index, font_path in enumerate(fonts):
            panel = _visual_panel(export_renderer, layout, ruby, Image, ImageDraw, font_path)
            board.alpha_composite(panel, (20 + index * 590, 20))
        output = OUTPUT_DIR / "vertical_symbols_and_uniform_fringe.png"
        board.save(output)
        print(f"BMANGA_TEXT_FRINGE_SYMBOL_VISUAL_OK {output}")
    finally:
        addon.unregister()


if __name__ == "__main__":
    main()
