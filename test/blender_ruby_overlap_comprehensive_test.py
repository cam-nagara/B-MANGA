"""ルビ重なり回避の包括的テスト — 様々な状況でのAI目視チェック用."""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "_verify" / "ruby_overlap_v2"


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


def _save_on_white(image, path: Path):
    from PIL import Image
    base = Image.new("RGBA", image.size, (255, 255, 255, 255))
    base.alpha_composite(image)
    result = base.convert("RGB")
    result.save(path)
    return result


def _render_entry(entry, label: str) -> str:
    from PIL import Image, ImageChops
    from bmanga_dev.utils import text_real_object
    rendered = text_real_object._render_entry_to_pillow(entry)
    assert rendered is not None, f"Pillow描画が利用できません: {label}"
    pil_image = rendered[0]
    out_path = OUT_DIR / f"{label}.png"
    rgb = _save_on_white(pil_image, out_path)
    blank = Image.new("RGB", rgb.size, (255, 255, 255))
    assert ImageChops.difference(rgb, blank).getbbox() is not None, f"空画像です: {label}"
    print(f"  OK: {label} ({pil_image.size[0]}x{pil_image.size[1]})")
    return hashlib.sha256(rgb.tobytes()).hexdigest()


def _assert_adjacent_ruby_no_overlap(entry, ruby_texts: list[str]) -> None:
    from bmanga_dev.typography import layout, ruby

    result = layout.typeset(entry, 0.0, 0.0, entry.width_mm, entry.height_mm)
    placements = ruby.compute_for_entry(result.placements, entry)
    assert len(placements) == sum(map(len, ruby_texts)), (
        len(placements), ruby_texts,
    )
    intervals = []
    cursor = 0
    horizontal = entry.writing_mode == "horizontal"
    for text in ruby_texts:
        chunk = placements[cursor:cursor + len(text)]
        cursor += len(text)
        if horizontal:
            lo = min(item.x_mm for item in chunk)
            hi = max(item.x_mm + item.size_pt * 25.4 / 72.0 for item in chunk)
        else:
            lo = min(item.y_mm for item in chunk)
            hi = max(item.y_mm + item.size_pt * 25.4 / 72.0 for item in chunk)
        intervals.append((lo, hi))
    intervals.sort()
    for current, following in zip(intervals, intervals[1:]):
        assert current[1] <= following[0] + 1.0e-6, (intervals, ruby_texts)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_ruby_v2_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "Test.bmanga"))
        assert result == {"FINISHED"}, result
        work = bpy.context.scene.bmanga_work
        page = work.pages[0]

        from bmanga_dev.utils import text_style

        ja_font = _find_ja_font()
        OUT_DIR.mkdir(parents=True, exist_ok=True)

        idx = [0]

        def _make(eid, body, **kw):
            e = page.texts.add()
            e.id = eid
            e.body = body
            e.writing_mode = kw.get("writing_mode", "vertical")
            e.font_size_q = kw.get("font_size_q", 28.0)
            e.font = ja_font
            e.width_mm = kw.get("width_mm", 55.0)
            e.height_mm = kw.get("height_mm", 90.0)
            e.ruby_line_height = 1.8
            e.ruby_gap_mm = 0.0
            e.ruby_size_percent = 50.0
            e.ruby_font = ja_font
            e.ruby_align = kw.get("ruby_align", "center")
            e.ruby_small_kana = "keep"
            e.ruby_letter_spacing = kw.get("ruby_letter_spacing", 0.0)
            return e

        def _case(label_suffix, body, rubies, **kw):
            idx[0] += 1
            num = f"{idx[0]:02d}"
            eid = f"t{num}"
            e = _make(eid, body, **kw)
            style = kw.get("ruby_style", "mono")
            for start, end, ruby_text in rubies:
                text_style.apply_ruby_span(e, start, end, ruby_text, style)
            _render_entry(e, f"{num}_{label_suffix}")
            if kw.get("expect_no_overlap", False):
                _assert_adjacent_ruby_no_overlap(e, [item[2] for item in rubies])

        # ── A: ルビ文字数が多い漢字が続く（縦書き） ──
        _case("v_many_2chars",
              "梔椿が咲く",
              [(0, 1, "くちなし"), (1, 2, "つばき")])

        _case("v_many_3chars",
              "蝸牛蟲が棲む",
              [(0, 1, "かたつむり"), (1, 2, "ぎゅう"), (2, 3, "むし")])

        _case("v_many_extreme",
              "蝸牛が這う",
              [(0, 1, "かたつむり"), (1, 2, "うし")])

        _case("v_many_5x3",
              "薔薇園",
              [(0, 1, "しょうび"), (1, 2, "び"), (2, 3, "えん")])

        # ── B: ルビ文字数が少ない漢字が続く ──
        _case("v_short_2chars",
              "猫犬が走る",
              [(0, 1, "ねこ"), (1, 2, "いぬ")])

        _case("v_short_3chars",
              "春夏秋が来た",
              [(0, 1, "はる"), (1, 2, "なつ"), (2, 3, "あき")])

        _case("v_short_1char",
              "木火水が",
              [(0, 1, "き"), (1, 2, "ひ"), (2, 3, "みず")])

        # ── C: 多い＋少ないが混在 ──
        _case("v_mixed",
              "蝸牛と猫",
              [(0, 1, "かたつむり"), (1, 2, "うし"), (3, 4, "ねこ")])

        _case("v_mixed_alt",
              "猫と蝸牛",
              [(0, 1, "ねこ"), (2, 3, "かたつむり"), (3, 4, "うし")])

        # ── D: 改行を含むケース（高さを小さくして改行を発生させる） ──
        _case("v_linebreak_many",
              "梔椿が咲き乱れる",
              [(0, 1, "くちなし"), (1, 2, "つばき")],
              height_mm=40.0)

        _case("v_linebreak_short",
              "春夏秋冬が巡る季節",
              [(0, 1, "はる"), (1, 2, "なつ"), (2, 3, "あき"), (3, 4, "ふゆ")],
              height_mm=40.0)

        _case("v_linebreak_mixed",
              "蝸牛と猫犬が遊ぶ庭",
              [(0, 1, "かたつむり"), (1, 2, "うし"),
               (3, 4, "ねこ"), (4, 5, "いぬ")],
              height_mm=40.0)

        # ── E: 横書き ──
        _case("h_many_2chars",
              "梔椿が咲く",
              [(0, 1, "くちなし"), (1, 2, "つばき")],
              writing_mode="horizontal", width_mm=90.0, height_mm=45.0)

        _case("h_short_3chars",
              "春夏秋が来た",
              [(0, 1, "はる"), (1, 2, "なつ"), (2, 3, "あき")],
              writing_mode="horizontal", width_mm=90.0, height_mm=45.0)

        _case("h_linebreak_many",
              "梔椿が咲き乱れる",
              [(0, 1, "くちなし"), (1, 2, "つばき")],
              writing_mode="horizontal", width_mm=50.0, height_mm=60.0)

        # ── F: 肩付き（start align） ──
        _case("v_start_many",
              "梔椿が咲く",
              [(0, 1, "くちなし"), (1, 2, "つばき")],
              ruby_align="start", ruby_style="group", expect_no_overlap=True)

        _case("v_start_short",
              "猫犬が走る",
              [(0, 1, "ねこ"), (1, 2, "いぬ")],
              ruby_align="start", ruby_style="group", expect_no_overlap=True)

        # ── G: ルビ字間をユーザーが広げた場合（圧縮効果の確認） ──
        _case("v_ls03_many",
              "梔椿が咲く",
              [(0, 1, "くちなし"), (1, 2, "つばき")],
              ruby_letter_spacing=0.3)

        _case("v_ls05_short",
              "猫犬が走る",
              [(0, 1, "ねこ"), (1, 2, "いぬ")],
              ruby_letter_spacing=0.5)

        _case("v_ls05_many",
              "蝸牛が這う",
              [(0, 1, "かたつむり"), (1, 2, "うし")],
              ruby_letter_spacing=0.5)

        # ── H: グループルビ（圧縮対象外の確認） ──
        _case("v_group",
              "梔椿が咲く",
              [(0, 2, "くちなしつばき")])

        # ── I: ルビなし文字を挟むケース ──
        _case("v_gap_between",
              "蝸と牛が",
              [(0, 1, "かたつむり"), (2, 3, "うし")])

        # ── J: style × align × ruby length の決定的な隣接マトリクス ──
        matrix = (
            ("short", "猫犬", [(0, 1, "ねこ"), (1, 2, "いぬ")]),
            ("long", "梔椿", [(0, 1, "くちなし"), (1, 2, "つばき")]),
        )
        for style in ("mono", "group"):
            for align in ("center", "start"):
                for length_label, body, rubies in matrix:
                    _case(
                        f"matrix_{style}_{align}_{length_label}_adjacent",
                        body,
                        rubies,
                        ruby_style=style,
                        ruby_align=align,
                        expect_no_overlap=(style == "group" and align == "start"),
                    )

        print("\nRUBY_OVERLAP_V2_TEST_OK")
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
