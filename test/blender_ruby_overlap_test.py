"""ルビ文字数が多い漢字が隣接する場合の表示テスト."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "_verify" / "ruby_overlap"


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
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_ruby_overlap_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "Overlap.bmanga"))
        assert result == {"FINISHED"}, result
        work = bpy.context.scene.bmanga_work
        page = work.pages[0]

        from bmanga_dev.utils import text_style

        ja_font = _find_ja_font()
        OUT_DIR.mkdir(parents=True, exist_ok=True)

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
            return e

        # 1: グループルビ — 梔椿（くちなしつばき）2文字に7文字
        e1 = _make("group", "梔椿が咲く")
        text_style.apply_ruby_span(e1, 0, 2, "くちなしつばき", "group")
        _render_entry(e1, "01_group_ruby")

        # 2: モノルビ — 梔(くちなし) 椿(つばき) 各文字に個別ルビ
        e2 = _make("mono", "梔椿が咲く")
        text_style.apply_ruby_span(e2, 0, 1, "くちなし", "mono")
        text_style.apply_ruby_span(e2, 1, 2, "つばき", "mono")
        _render_entry(e2, "02_mono_ruby")

        # 3: モノルビ 3文字連続 — 薔薇園(ばらえん) ← ルビ短い比較用
        e3 = _make("short", "薔薇園に行く")
        text_style.apply_ruby_span(e3, 0, 1, "ばら", "mono")
        text_style.apply_ruby_span(e3, 1, 2, "ば", "mono")
        text_style.apply_ruby_span(e3, 2, 3, "えん", "mono")
        _render_entry(e3, "03_mono_short")

        # 4: 極端なケース — 1文字に5文字以上のルビが連続
        e4 = _make("extreme", "蝸牛が這う")
        text_style.apply_ruby_span(e4, 0, 1, "かたつむり", "mono")
        text_style.apply_ruby_span(e4, 1, 2, "ぎゅう", "mono")
        _render_entry(e4, "04_extreme_mono")

        # 5: 横書きでも同様
        e5 = _make("h_mono", "梔椿が咲く", writing_mode="horizontal",
                    width_mm=90.0, height_mm=45.0)
        text_style.apply_ruby_span(e5, 0, 1, "くちなし", "mono")
        text_style.apply_ruby_span(e5, 1, 2, "つばき", "mono")
        _render_entry(e5, "05_horizontal_mono")

        # 6: 肩付きでの同ケース
        e6 = _make("mono_start", "梔椿が咲く", ruby_align="start")
        text_style.apply_ruby_span(e6, 0, 1, "くちなし", "mono")
        text_style.apply_ruby_span(e6, 1, 2, "つばき", "mono")
        _render_entry(e6, "06_mono_start_align")

        print("RUBY_OVERLAP_TEST_OK")
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
