"""Blender 5.1: unified ruby defaults, migration, styles, and persistence."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_ruby_unification"


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


def _positions(items):
    return tuple((round(item.x_mm, 4), round(item.y_mm, 4), item.ch) for item in items)


def main() -> None:
    addon = _load_addon()
    try:
        from bmanga_dev_ruby_unification.io import schema
        from bmanga_dev_ruby_unification.typography import ruby, ruby_presentation
        from bmanga_dev_ruby_unification.utils import auto_ruby
        from bmanga_dev_ruby_unification.typography.layout import GlyphPlacement

        work = bpy.context.scene.bmanga_work
        page = work.pages.add()
        entry = page.texts.add()
        entry.body = "東京"
        assert entry.writing_mode == "horizontal"
        assert abs(entry.ruby_size_percent - 50.0) < 1.0e-6
        assert abs(entry.ruby_line_height - 1.8) < 1.0e-6
        assert entry.ruby_align == "center" and entry.ruby_small_kana == "keep"
        assert entry.ruby_font_preset == "inherit"
        assert entry.ruby_default_style == "group"
        default_span = entry.ruby_spans.add()
        assert default_span.style == "group"
        entry.ruby_spans.clear()

        entry.font_size_q = 20.0
        entry.ruby_gap_mm = 1.1
        assert abs(ruby.ruby_gap_mm_from_entry(entry) - 1.1) < 1.0e-6
        entry.ruby_gap_em = 0.25
        entry.ruby_default_style = "jukugo"
        assert abs(ruby.ruby_gap_mm_from_entry(entry) - 1.25) < 1.0e-6
        entry.ruby_font = r"Z:\missing\ruby-font.ttf"
        assert Path(ruby.ruby_font_path_from_entry(entry)).is_file(), "無効フォントは本文フォントへ戻す"
        entry.ruby_font = ""
        for preset, expected_name in (
            ("sans-jp", "NotoSansJP-VF.ttf"),
            ("serif-jp", "NotoSerifJP-VF.ttf"),
            ("gothic-jp", "BIZ-UDGothicR.ttc"),
        ):
            entry.ruby_font_preset = preset
            resolved = Path(ruby_presentation.resolve_font_path(entry))
            if (Path(r"C:\Windows\Fonts") / expected_name).is_file():
                assert resolved.name == expected_name, (preset, resolved)
        entry.ruby_font_preset = "inherit"

        auto_entry = page.texts.add()
        auto_entry.body = "東京"
        auto_entry.ruby_default_style = "jukugo"
        assert auto_ruby.apply_auto_ruby(auto_entry, [("東京", "とうきょう")]) == 1
        assert auto_entry.ruby_spans[0].style == "jukugo"

        size_pt = 20.0 * 0.25 * 72.0 / 25.4
        parents = [
            GlyphPlacement("東", 0.0, 0.0, size_pt, 0.0, 0),
            GlyphPlacement("京", 5.0, 0.0, size_pt, 0.0, 1),
        ]
        segments = [
            SimpleNamespace(start=0, length=1, ruby_text="とうきょう"),
            SimpleNamespace(start=1, length=1, ruby_text="きょうと"),
        ]
        common = dict(
            start=0, length=2, ruby_text="とうきょうきょうと", segments=segments,
        )
        group = ruby.compute_ruby_placements(
            parents, [SimpleNamespace(**common, style="group")], writing_mode="horizontal", ruby_letter_spacing=1.0
        )
        mono = ruby.compute_ruby_placements(
            parents, [SimpleNamespace(**common, style="mono")], writing_mode="horizontal", ruby_letter_spacing=1.0
        )
        jukugo = ruby.compute_ruby_placements(
            parents, [SimpleNamespace(**common, style="jukugo")], writing_mode="horizontal", ruby_letter_spacing=1.0
        )
        assert _positions(group) != _positions(mono), "グループとモノの組版差がない"
        assert _positions(mono) != _positions(jukugo), "モノと熟語の組版差がない"
        converted = ruby.compute_ruby_placements(
            parents,
            [SimpleNamespace(start=0, length=1, ruby_text="きょう", style="group", segments=[])],
            writing_mode="horizontal",
            ruby_small_kana="fullsize",
        )
        assert "ょ" not in "".join(item.ch for item in converted)

        span = entry.ruby_spans.add()
        span.start = 0
        span.length = 2
        span.ruby_text = "とうきょう"
        span.style = "jukugo"
        span.origin = "document-rule"
        span.priority = 7
        for start, text in ((0, "とう"), (1, "きょう")):
            segment = span.segments.add()
            segment.start = start
            segment.length = 1
            segment.ruby_text = text
        saved = schema.text_entry_to_dict(entry)
        clone = page.texts.add()
        schema.text_entry_from_dict(clone, saved)
        assert abs(clone.ruby_gap_em - 0.25) < 1.0e-6
        assert clone.ruby_default_style == "jukugo"
        assert clone.ruby_spans[0].origin == "document-rule" and clone.ruby_spans[0].priority == 7
        assert len(clone.ruby_spans[0].segments) == 2

        from bmanga_dev_ruby_unification.utils import text_style

        history_snapshot = text_style.all_spans_snapshot(clone)
        clone.ruby_spans.clear()
        text_style.restore_all_spans(clone, history_snapshot)
        restored = clone.ruby_spans[0]
        assert restored.origin == "document-rule" and restored.priority == 7
        assert [(part.start, part.length, part.ruby_text) for part in restored.segments] == [
            (0, 1, "とう"),
            (1, 1, "きょう"),
        ]
        text_style.adjust_spans_for_replace(clone, 1, 1, 1)
        assert len(clone.ruby_spans) == 0, "親文字途中の挿入でルビ内訳を残してはならない"
        print("BMANGA_RUBY_UNIFICATION_OK")
    finally:
        addon.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
