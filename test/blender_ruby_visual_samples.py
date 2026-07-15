"""Blender実機: 様々なルビパターンをPillow描画してPNGに書き出す."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(os.environ.get("BMANGA_RUBY_SAMPLES_OUT", "") or (ROOT / "_verify" / "ruby_samples"))


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
        print(f"  SKIP: {label} (Pillow unavailable)")
        return
    pil_image = rendered[0]
    out_path = OUT_DIR / f"{label}.png"
    _save_on_white(pil_image, out_path)
    print(f"  OK: {label} ({pil_image.size[0]}x{pil_image.size[1]})")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_ruby_samples_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "RubySamples.bmanga"))
        assert result == {"FINISHED"}, result
        work = bpy.context.scene.bmanga_work
        page = work.pages[0]

        from bmanga_dev.utils import text_style

        ja_font = _find_ja_font()
        OUT_DIR.mkdir(parents=True, exist_ok=True)

        # --- Sample 1: 縦書き・グループルビ ---
        entry = page.texts.add()
        entry.id = "sample_v_group"
        entry.body = "東京都庁の展望台"
        entry.writing_mode = "vertical"
        entry.font_size_q = 28.0
        entry.font = ja_font
        entry.width_mm = 50.0
        entry.height_mm = 80.0
        entry.ruby_line_height = 2.2
        entry.ruby_gap_mm = 0.5
        entry.ruby_size_percent = 50.0
        entry.ruby_font = ja_font
        text_style.apply_ruby_span(entry, 0, 2, "とうきょう", "group")
        text_style.apply_ruby_span(entry, 2, 4, "とちょう", "group")
        text_style.apply_ruby_span(entry, 5, 7, "てんぼう", "group")
        text_style.apply_ruby_span(entry, 7, 8, "だい", "group")
        _render_entry(entry, "01_vertical_group_ruby")

        # --- Sample 2: 横書き・グループルビ ---
        entry2 = page.texts.add()
        entry2.id = "sample_h_group"
        entry2.body = "吾輩は猫である"
        entry2.writing_mode = "horizontal"
        entry2.font_size_q = 28.0
        entry2.font = ja_font
        entry2.width_mm = 80.0
        entry2.height_mm = 40.0
        entry2.ruby_line_height = 2.2
        entry2.ruby_gap_mm = 0.5
        entry2.ruby_size_percent = 50.0
        entry2.ruby_font = ja_font
        text_style.apply_ruby_span(entry2, 0, 2, "わがはい", "group")
        text_style.apply_ruby_span(entry2, 3, 4, "ねこ", "group")
        _render_entry(entry2, "02_horizontal_group_ruby")

        # --- Sample 3: 縦書き・モノルビ ---
        entry3 = page.texts.add()
        entry3.id = "sample_v_mono"
        entry3.body = "薔薇の花が咲く"
        entry3.writing_mode = "vertical"
        entry3.font_size_q = 28.0
        entry3.font = ja_font
        entry3.width_mm = 50.0
        entry3.height_mm = 80.0
        entry3.ruby_line_height = 2.2
        entry3.ruby_gap_mm = 0.5
        entry3.ruby_size_percent = 50.0
        entry3.ruby_font = ja_font
        text_style.apply_ruby_span(entry3, 0, 1, "ば", "mono")
        text_style.apply_ruby_span(entry3, 1, 2, "ら", "mono")
        text_style.apply_ruby_span(entry3, 3, 4, "はな", "mono")
        text_style.apply_ruby_span(entry3, 5, 6, "さ", "mono")
        _render_entry(entry3, "03_vertical_mono_ruby")

        # --- Sample 4: 縦書き・複数行 + ルビ ---
        entry4 = page.texts.add()
        entry4.id = "sample_v_multiline"
        entry4.body = "鬼滅の刃\n竈門炭治郎"
        entry4.writing_mode = "vertical"
        entry4.font_size_q = 24.0
        entry4.font = ja_font
        entry4.width_mm = 55.0
        entry4.height_mm = 70.0
        entry4.ruby_line_height = 2.2
        entry4.ruby_gap_mm = 0.5
        entry4.ruby_size_percent = 50.0
        entry4.ruby_font = ja_font
        text_style.apply_ruby_span(entry4, 0, 2, "きめつ", "group")
        text_style.apply_ruby_span(entry4, 3, 4, "やいば", "group")
        text_style.apply_ruby_span(entry4, 5, 7, "かまど", "group")
        text_style.apply_ruby_span(entry4, 7, 10, "たんじろう", "group")
        _render_entry(entry4, "04_vertical_multiline_ruby")

        # --- Sample 5: 横書き・大きめルビ (75%) ---
        entry5 = page.texts.add()
        entry5.id = "sample_h_large"
        entry5.body = "魔法使いの弟子"
        entry5.writing_mode = "horizontal"
        entry5.font_size_q = 28.0
        entry5.font = ja_font
        entry5.width_mm = 90.0
        entry5.height_mm = 40.0
        entry5.ruby_line_height = 2.5
        entry5.ruby_gap_mm = 0.8
        entry5.ruby_size_percent = 75.0
        entry5.ruby_font = ja_font
        text_style.apply_ruby_span(entry5, 0, 3, "まほうつかい", "group")
        text_style.apply_ruby_span(entry5, 4, 6, "でし", "group")
        _render_entry(entry5, "05_horizontal_large_ruby")

        # --- Sample 6: 縦書き・叫びセリフ風 ---
        entry6 = page.texts.add()
        entry6.id = "sample_v_shout"
        entry6.body = "全集中の呼吸！"
        entry6.writing_mode = "vertical"
        entry6.font_size_q = 32.0
        entry6.font = ja_font
        entry6.width_mm = 50.0
        entry6.height_mm = 90.0
        entry6.ruby_line_height = 2.0
        entry6.ruby_gap_mm = 0.3
        entry6.ruby_size_percent = 45.0
        entry6.ruby_font = ja_font
        text_style.apply_ruby_span(entry6, 0, 3, "ぜんしゅうちゅう", "group")
        text_style.apply_ruby_span(entry6, 4, 6, "こきゅう", "group")
        _render_entry(entry6, "06_vertical_shout_ruby")

        print("RUBY_VISUAL_SAMPLES_OK")
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
