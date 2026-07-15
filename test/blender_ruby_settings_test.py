"""Blender実機: ルビ新設定（配置方法・小書き仮名・gap=0）のPNG出力テスト."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "_verify" / "ruby_settings"


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


def _save_on_white(image, path: Path) -> None:
    from PIL import Image
    base = Image.new("RGBA", image.size, (255, 255, 255, 255))
    base.alpha_composite(image)
    base.convert("RGB").save(path)


def _render_entry(entry, label: str) -> None:
    from bmanga_dev.utils import text_real_object
    rendered = text_real_object._render_entry_to_pillow(entry)
    if rendered is None:
        print(f"  SKIP: {label}")
        return
    pil_image = rendered[0]
    out_path = OUT_DIR / f"{label}.png"
    _save_on_white(pil_image, out_path)
    print(f"  OK: {label} ({pil_image.size[0]}x{pil_image.size[1]})")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_ruby_settings_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "RubySettings.bmanga"))
        assert result == {"FINISHED"}, result
        work = bpy.context.scene.bmanga_work
        page = work.pages[0]

        from bmanga_dev.utils import text_style

        ja_font = _find_ja_font()
        OUT_DIR.mkdir(parents=True, exist_ok=True)

        def _make_entry(eid, body, **kw):
            e = page.texts.add()
            e.id = eid
            e.body = body
            e.writing_mode = kw.get("writing_mode", "vertical")
            e.font_size_q = kw.get("font_size_q", 28.0)
            e.font = ja_font
            e.width_mm = kw.get("width_mm", 50.0)
            e.height_mm = kw.get("height_mm", 80.0)
            e.ruby_line_height = kw.get("ruby_line_height", 1.8)
            e.ruby_gap_mm = kw.get("ruby_gap_mm", 0.0)
            e.ruby_size_percent = kw.get("ruby_size_percent", 50.0)
            e.ruby_font = ja_font
            e.ruby_align = kw.get("ruby_align", "center")
            e.ruby_small_kana = kw.get("ruby_small_kana", "keep")
            return e

        # --- 1: gap=0 vs gap=0.5 比較（縦書き）---
        e1a = _make_entry("gap0", "東京都庁", ruby_gap_mm=0.0)
        text_style.apply_ruby_span(e1a, 0, 2, "とうきょう", "group")
        text_style.apply_ruby_span(e1a, 2, 4, "とちょう", "group")
        _render_entry(e1a, "01_gap_0mm")

        e1b = _make_entry("gap05", "東京都庁", ruby_gap_mm=0.5)
        text_style.apply_ruby_span(e1b, 0, 2, "とうきょう", "group")
        text_style.apply_ruby_span(e1b, 2, 4, "とちょう", "group")
        _render_entry(e1b, "02_gap_05mm")

        # --- 2: 中付き vs 肩付き ---
        e2a = _make_entry("center", "猫", ruby_align="center")
        text_style.apply_ruby_span(e2a, 0, 1, "ねこ", "group")
        _render_entry(e2a, "03_align_center")

        e2b = _make_entry("start", "猫", ruby_align="start")
        text_style.apply_ruby_span(e2b, 0, 1, "ねこ", "group")
        _render_entry(e2b, "04_align_start")

        # --- 3: 小書き仮名 keep vs fullsize ---
        e3a = _make_entry("kana_keep", "全集中の呼吸", ruby_small_kana="keep")
        text_style.apply_ruby_span(e3a, 0, 3, "ぜんしゅうちゅう", "group")
        text_style.apply_ruby_span(e3a, 4, 6, "こきゅう", "group")
        _render_entry(e3a, "05_small_kana_keep")

        e3b = _make_entry("kana_full", "全集中の呼吸", ruby_small_kana="fullsize")
        text_style.apply_ruby_span(e3b, 0, 3, "ぜんしゅうちゅう", "group")
        text_style.apply_ruby_span(e3b, 4, 6, "こきゅう", "group")
        _render_entry(e3b, "06_small_kana_fullsize")

        # --- 4: 横書き gap=0 ---
        e4 = _make_entry("h_gap0", "吾輩は猫である", writing_mode="horizontal",
                         width_mm=80.0, height_mm=40.0, ruby_gap_mm=0.0)
        text_style.apply_ruby_span(e4, 0, 2, "わがはい", "group")
        text_style.apply_ruby_span(e4, 3, 4, "ねこ", "group")
        _render_entry(e4, "07_horizontal_gap0")

        # --- 5: 新デフォルト全部乗せ ---
        e5 = _make_entry("combo", "鬼滅の刃\n竈門炭治郎",
                         font_size_q=24.0, width_mm=55.0, height_mm=70.0,
                         ruby_gap_mm=0.0, ruby_align="center", ruby_small_kana="fullsize")
        text_style.apply_ruby_span(e5, 0, 2, "きめつ", "group")
        text_style.apply_ruby_span(e5, 3, 4, "やいば", "group")
        text_style.apply_ruby_span(e5, 5, 7, "かまど", "group")
        text_style.apply_ruby_span(e5, 7, 10, "たんじろう", "group")
        _render_entry(e5, "08_combo_fullsize_gap0")

        print("RUBY_SETTINGS_TEST_OK")
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
