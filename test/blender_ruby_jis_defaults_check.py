"""Blender実機: JIS系のルビ既定値と短いグループルビ配置を検証する。"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "_verify" / "2026-07-16_ruby_publisher_rules"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _find_ja_font() -> str:
    for candidate in (
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
    ):
        if Path(candidate).is_file():
            return candidate
    return ""


def _assert_close(actual: float, expected: float, label: str) -> None:
    assert abs(actual - expected) < 1.0e-6, f"{label}: {actual} != {expected}"


def _placement_checks() -> None:
    from bmanga_dev.typography.layout import GlyphPlacement
    from bmanga_dev.typography.ruby import compute_ruby_placements

    # 1em=1mmになるサイズ。親文字2字(2mm)へルビ2字(各0.5mm)を置くと、
    # JIS系の前後1:字間2:前後1は 0.25:0.5:0.25mm になる。
    size_pt = 72.0 / 25.4
    parents_h = [
        GlyphPlacement(ch=ch, x_mm=float(index), y_mm=0.0, size_pt=size_pt,
                       rotation_deg=0.0, index=index)
        for index, ch in enumerate("漢字")
    ]
    span = {"start": 0, "length": 2, "ruby_text": "かな", "style": "group"}
    placed_h = compute_ruby_placements(
        parents_h, [span], ruby_size_ratio=0.5, writing_mode="horizontal",
        ruby_align="center",
    )
    assert len(placed_h) == 2
    _assert_close(placed_h[0].x_mm, 0.25, "横書き・先頭空き")
    _assert_close(placed_h[1].x_mm, 1.25, "横書き・2字目")
    _assert_close(placed_h[1].x_mm - (placed_h[0].x_mm + 0.5), 0.5,
                  "横書き・文字間空き")

    parents_v = [
        GlyphPlacement(ch=ch, x_mm=0.0, y_mm=-float(index), size_pt=size_pt,
                       rotation_deg=0.0, index=index)
        for index, ch in enumerate("漢字")
    ]
    placed_v = compute_ruby_placements(
        parents_v, [span], ruby_size_ratio=0.5, writing_mode="vertical",
        ruby_align="center",
    )
    assert len(placed_v) == 2
    _assert_close(placed_v[0].y_mm, 0.25, "縦書き・先頭空き")
    _assert_close(placed_v[1].y_mm, -0.75, "縦書き・2字目")

    # 1字ルビは親文字列の中央。3字以上でも追加空きの1:2配分を保つ。
    single_span = {"start": 0, "length": 2, "ruby_text": "か", "style": "group"}
    placed_single = compute_ruby_placements(
        parents_h, [single_span], ruby_size_ratio=0.5, writing_mode="horizontal",
        ruby_align="center",
    )
    _assert_close(placed_single[0].x_mm, 0.75, "1字ルビ・中央配置")

    parents_three = [
        GlyphPlacement(ch=ch, x_mm=float(index), y_mm=0.0, size_pt=size_pt,
                       rotation_deg=0.0, index=index)
        for index, ch in enumerate("三文字")
    ]
    three_span = {"start": 0, "length": 3, "ruby_text": "かなみ", "style": "group"}
    placed_three = compute_ruby_placements(
        parents_three, [three_span], ruby_size_ratio=0.5,
        writing_mode="horizontal", ruby_align="center",
    )
    _assert_close(placed_three[0].x_mm, 0.25, "3字ルビ・先頭空き")
    _assert_close(placed_three[1].x_mm, 1.25, "3字ルビ・中央")
    _assert_close(placed_three[2].x_mm, 2.25, "3字ルビ・末尾")

    # 前後空きは親文字の二分を上限とし、残りをルビ字間へ回す。
    parents_wide = [
        GlyphPlacement(ch=ch, x_mm=float(index), y_mm=0.0, size_pt=size_pt,
                       rotation_deg=0.0, index=index)
        for index, ch in enumerate("四文字分")
    ]
    wide_span = {"start": 0, "length": 4, "ruby_text": "かな", "style": "group"}
    placed_wide = compute_ruby_placements(
        parents_wide, [wide_span], ruby_size_ratio=0.5, writing_mode="horizontal",
        ruby_align="center",
    )
    _assert_close(placed_wide[0].x_mm, 0.5, "二分上限・先頭")
    _assert_close(placed_wide[1].x_mm, 3.0, "二分上限・末尾")

    # 肩付きと親文字より長いルビは今回の配分変更の対象外。
    shoulder = compute_ruby_placements(
        parents_h, [span], ruby_size_ratio=0.5, writing_mode="horizontal",
        ruby_align="start",
    )
    _assert_close(shoulder[0].x_mm, 0.0, "肩付き・先頭")
    _assert_close(shoulder[1].x_mm, 0.5, "肩付き・2字目")
    long_span = {"start": 0, "length": 2, "ruby_text": "ながいよみ", "style": "group"}
    long_ruby = compute_ruby_placements(
        parents_h, [long_span], ruby_size_ratio=0.5, writing_mode="horizontal",
        ruby_align="center",
    )
    _assert_close(long_ruby[0].x_mm, -0.25, "長いルビ・中央配置")

    # 欧文読みは語のまとまりを保ち、親文字幅へ字間を広げない。
    latin_span = {"start": 0, "length": 2, "ruby_text": "AB", "style": "group"}
    latin_ruby = compute_ruby_placements(
        parents_h, [latin_span], ruby_size_ratio=0.5, writing_mode="horizontal",
        ruby_align="center",
    )
    _assert_close(latin_ruby[0].x_mm, 0.5, "欧文ルビ・中央配置")
    _assert_close(latin_ruby[1].x_mm, 1.0, "欧文ルビ・ベタ組")

    # 字間マイナスはJIS配分をベタ組（中付き）へ詰め寄せる。-2でベタ到達。
    condensed_half = compute_ruby_placements(
        parents_h, [span], ruby_size_ratio=0.5, writing_mode="horizontal",
        ruby_align="center", ruby_letter_spacing=-1.0,
    )
    _assert_close(condensed_half[0].x_mm, 0.375, "字間-1・先頭")
    _assert_close(condensed_half[1].x_mm, 1.125, "字間-1・2字目")
    condensed_beta = compute_ruby_placements(
        parents_h, [span], ruby_size_ratio=0.5, writing_mode="horizontal",
        ruby_align="center", ruby_letter_spacing=-2.0,
    )
    _assert_close(condensed_beta[0].x_mm, 0.5, "字間-2・ベタ先頭")
    _assert_close(condensed_beta[1].x_mm, 1.0, "字間-2・ベタ2字目")

    # 肩付きの自動圧縮で隣の親文字までに収めたルビは、字間マイナスの
    # 詰め寄せ補間で再び広がらない（次の親文字開始1.0mmの内側を保つ）。
    shoulder_spans = [
        {"start": 0, "length": 1, "ruby_text": "とうきょうと", "style": "group"},
        {"start": 1, "length": 1, "ruby_text": "おおさかし", "style": "group"},
    ]
    squeezed = compute_ruby_placements(
        parents_h, shoulder_spans, ruby_size_ratio=0.5,
        writing_mode="horizontal", ruby_align="start", ruby_letter_spacing=-1.0,
    )
    first_xs = [p.x_mm for p in squeezed[:6]]
    assert all(b > a for a, b in zip(first_xs, first_xs[1:])), first_xs
    assert first_xs[-1] <= 0.71, first_xs


def _render_entry(entry, filename: str) -> None:
    from PIL import Image, ImageChops
    from bmanga_dev.utils import text_real_object

    rendered = text_real_object._render_entry_to_pillow(entry)
    assert rendered is not None, filename
    image = rendered[0]
    white = Image.new("RGBA", image.size, (255, 255, 255, 255))
    white.alpha_composite(image)
    rgb = white.convert("RGB")
    blank = Image.new("RGB", rgb.size, (255, 255, 255))
    assert ImageChops.difference(rgb, blank).getbbox() is not None, filename
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rgb.save(OUT_DIR / filename)


def _visual_checks(page) -> None:
    from bmanga_dev.utils import text_style

    font = _find_ja_font()
    assert font, "日本語フォントが見つかりません"
    for writing_mode, filename in (
        ("horizontal", "jis_group_horizontal.png"),
        ("vertical", "jis_group_vertical.png"),
    ):
        entry = page.texts.add()
        entry.id = f"jis_{writing_mode}"
        entry.body = "四文字分"
        entry.writing_mode = writing_mode
        entry.font = font
        entry.ruby_font = font
        entry.font_size_q = 32.0
        entry.width_mm = 70.0
        entry.height_mm = 70.0
        assert entry.ruby_size_percent == 50.0
        assert entry.ruby_gap_em == 0.0
        assert entry.ruby_letter_spacing == -1.0
        assert entry.ruby_align == "center"
        assert entry.ruby_font_preset == "inherit"
        assert entry.ruby_default_style == "group"
        assert text_style.apply_ruby_span(entry, 0, 4, "かな", "group")
        _render_entry(entry, filename)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_ruby_jis_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "RubyJIS.bmanga"))
        assert result == {"FINISHED"}, result
        _placement_checks()
        _visual_checks(bpy.context.scene.bmanga_work.pages[0])
        print("RUBY_JIS_DEFAULTS_CHECK_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
