"""Blender実機用: テキストのルビ・選択範囲・保存の微細挙動確認."""

from __future__ import annotations

import importlib.util
import inspect
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(os.environ.get("BMANGA_TEXT_RUBY_UX_OUT", "") or (ROOT / "_verify"))


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


def _new_entry(work_dir: Path):
    result = bpy.ops.bmanga.work_new(filepath=str(work_dir))
    assert result == {"FINISHED"}, result
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]
    entry = page.texts.add()
    entry.id = "text_ruby_ux"
    entry.body = "漢字ABC\n東京"
    entry.x_mm = 0.0
    entry.y_mm = 0.0
    entry.width_mm = 45.0
    entry.height_mm = 36.0
    entry.writing_mode = "vertical"
    entry.font_size_q = 20.0
    page.active_text_index = 0
    return work, page, entry


def _alpha_sum(image) -> int:
    alpha = image.getchannel("A")
    return int(sum(alpha.getdata()))


def _save_on_white(image, path: Path) -> None:
    from PIL import Image

    base = Image.new("RGBA", image.size, (255, 255, 255, 255))
    base.alpha_composite(image)
    base.convert("RGB").save(path)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_text_ruby_ux_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        work, page, entry = _new_entry(temp_root / "Text_Ruby_UX.bmanga")

        from bmanga_dev.io import schema
        from bmanga_dev.io import export_pipeline
        from bmanga_dev.io import text_presets
        from bmanga_dev.operators import text_edit_history, text_edit_runtime
        from bmanga_dev.typography import layout as text_layout, ruby as text_ruby
        from bmanga_dev.utils.geom import q_to_mm
        from bmanga_dev.utils import text_real_object, text_style

        ruby_font = ""
        for candidate in (
            r"C:\Windows\Fonts\meiryo.ttc",
            r"C:\Windows\Fonts\YuGothM.ttc",
            r"C:\Windows\Fonts\msgothic.ttc",
        ):
            if Path(candidate).is_file():
                ruby_font = candidate
                break
        entry.ruby_line_height = 2.2
        entry.ruby_gap_mm = 1.1
        entry.ruby_letter_spacing = 0.35
        entry.ruby_size_percent = 62.0
        entry.ruby_default_style = "jukugo"
        entry.ruby_font = ruby_font
        assert text_style.apply_ruby_span(entry, 0, 2, "かんじ", "group")
        assert text_style.ruby_spans_snapshot(entry) == ((0, 2, "かんじ", "group"),)

        data = schema.text_entry_to_dict(entry)
        assert data["rubyLineHeight"] == 2.2
        assert data["rubyGapMm"] == 1.1
        assert data["rubyLetterSpacing"] == 0.35
        assert data["rubySizePercent"] == 62.0
        assert data["rubyFont"] == ruby_font
        assert data["rubyDefaultStyle"] == "jukugo"
        assert data["rubySpans"][0]["rubyText"] == "かんじ"
        clone = page.texts.add()
        schema.text_entry_from_dict(clone, data)
        assert abs(clone.ruby_line_height - 2.2) < 1.0e-6
        assert abs(clone.ruby_gap_mm - 1.1) < 1.0e-6
        assert abs(clone.ruby_letter_spacing - 0.35) < 1.0e-6
        assert abs(clone.ruby_size_percent - 62.0) < 1.0e-6
        assert clone.ruby_font == ruby_font
        assert clone.ruby_default_style == "jukugo"
        assert text_style.ruby_spans_snapshot(clone) == ((0, 2, "かんじ", "group"),)
        page.texts.remove(len(page.texts) - 1)

        preset_data = text_presets.snapshot_from_entry(entry)
        preset_target = page.texts.add()
        text_presets.apply_to_entry(preset_target, preset_data)
        assert abs(preset_target.ruby_line_height - 2.2) < 1.0e-6
        assert abs(preset_target.ruby_gap_mm - 1.1) < 1.0e-6
        assert abs(preset_target.ruby_letter_spacing - 0.35) < 1.0e-6
        assert abs(preset_target.ruby_size_percent - 62.0) < 1.0e-6
        assert preset_target.ruby_font == ruby_font
        assert preset_target.ruby_default_style == "jukugo"
        page.texts.remove(len(page.texts) - 1)

        entry.body = "A\n漢字"
        text_style.clear_ruby_spans(entry)
        text_style.apply_ruby_span(entry, 2, 4, "かんじ", "group")
        entry.writing_mode = "horizontal"
        entry.line_height = 1.0
        entry.ruby_line_height = 2.2
        entry.ruby_letter_spacing = 0.0
        em_mm = q_to_mm(entry.font_size_q)
        result = text_layout.typeset(entry, 0.0, 0.0, 80.0, 60.0)
        by_char = {g.ch: g for g in result.placements}
        assert abs((by_char["A"].y_mm - by_char["漢"].y_mm) - (em_mm * 2.2)) < 0.01
        ruby_placements = text_ruby.compute_for_entry(result.placements, entry)
        assert len(ruby_placements) == 3
        parent = [g for g in result.placements if g.ch in {"漢", "字"}]
        parent_left = min(g.x_mm for g in parent)
        parent_right = max(g.x_mm + (g.size_pt * 25.4 / 72.0) for g in parent)
        ruby_size_mm = ruby_placements[0].size_pt * 25.4 / 72.0
        leading = ruby_placements[0].x_mm - parent_left
        trailing = parent_right - (ruby_placements[-1].x_mm + ruby_size_mm)
        inner = ruby_placements[1].x_mm - (ruby_placements[0].x_mm + ruby_size_mm)
        assert leading > 0.0
        assert abs(leading - trailing) < 0.01
        assert abs(inner - 2.0 * leading) < 0.01
        parent_top = max(g.y_mm + (g.size_pt * 25.4 / 72.0) for g in parent)
        assert abs(ruby_placements[0].y_mm - (parent_top + entry.ruby_gap_mm)) < 0.01
        assert abs(ruby_placements[0].size_pt - parent[0].size_pt * 0.62) < 0.01
        assert ruby_placements[0].font_path == ruby_font
        assert text_ruby.render_pad_mm_for_entry(entry, minimum=1.5) >= entry.ruby_gap_mm + ruby_size_mm
        export_layer = export_pipeline._render_text_layer(entry, canvas_height_px=2000, dpi=300)
        assert export_layer is not None
        fixed_pad_width = int(round((entry.width_mm + 3.0) * 300 / 25.4))
        assert export_layer.image.size[0] > fixed_pad_width
        entry.ruby_letter_spacing = 0.5
        spaced = text_ruby.compute_for_entry(result.placements, entry)
        assert spaced[1].x_mm - spaced[0].x_mm > ruby_placements[1].x_mm - ruby_placements[0].x_mm

        entry.body = "漢字"
        text_style.clear_ruby_spans(entry)
        text_style.apply_ruby_span(entry, 0, 2, "かんじ", "group")
        entry.writing_mode = "vertical"
        entry.ruby_letter_spacing = 0.0
        result = text_layout.typeset(entry, 0.0, 0.0, 80.0, 60.0)
        ruby_placements = text_ruby.compute_for_entry(result.placements, entry)
        parent = result.placements
        parent_top = max(g.y_mm + (g.size_pt * 25.4 / 72.0) for g in parent)
        parent_bottom = min(g.y_mm for g in parent)
        ruby_size_mm = ruby_placements[0].size_pt * 25.4 / 72.0
        assert ruby_placements[0].y_mm > ruby_placements[-1].y_mm
        leading = parent_top - (ruby_placements[0].y_mm + ruby_size_mm)
        trailing = ruby_placements[-1].y_mm - parent_bottom
        inner = ruby_placements[0].y_mm - (ruby_placements[1].y_mm + ruby_size_mm)
        assert leading > 0.0
        assert abs(leading - trailing) < 0.01
        assert abs(inner - 2.0 * leading) < 0.01

        entry.body = "漢字ABC\n東京"
        entry.writing_mode = "vertical"
        entry.line_height = 1.4
        before = text_real_object._render_entry_to_pillow(entry)[0]
        text_style.clear_ruby_spans(entry)
        without_ruby = text_real_object._render_entry_to_pillow(entry)[0]
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        before.save(OUT_DIR / "text_ruby_ux_with_ruby.png")
        without_ruby.save(OUT_DIR / "text_ruby_ux_without_ruby.png")
        _save_on_white(before, OUT_DIR / "text_ruby_ux_with_ruby_white.png")
        _save_on_white(without_ruby, OUT_DIR / "text_ruby_ux_without_ruby_white.png")
        assert _alpha_sum(before) > _alpha_sum(without_ruby), "ruby did not add visible pixels"

        text_style.apply_ruby_span(entry, 6, 8, "とうきょう", "group")
        region = text_edit_runtime.text_inner_rect(text_edit_runtime.text_rect(entry))
        result = text_layout.typeset(entry, region.x, region.y, region.width, region.height)
        placements = text_ruby.compute_ruby_placements(
            result.placements,
            entry.ruby_spans,
            writing_mode=entry.writing_mode,
        )
        assert placements, "ruby after newline was not placed"
        assert all(p.ch for p in placements)

        entry.body = "漢字"
        text_style.clear_ruby_spans(entry)
        text_style.apply_ruby_span(entry, 0, 2, "かんじ", "group")
        text_edit_runtime.replace_selection(entry, 0, -1, "新")
        assert text_style.ruby_spans_snapshot(entry) == ((1, 3, "かんじ", "group"),)
        text_edit_runtime.replace_selection(entry, 1, 3, "語")
        assert text_style.ruby_spans_snapshot(entry) == (), "ruby survived replacement of parent text"

        entry.body = "ABC 漢字"
        assert text_edit_runtime.word_bounds_at_index(entry, 1) == (0, 3)
        assert text_edit_runtime.word_bounds_at_index(entry, 4) == (4, 5)

        class EditProbe:
            _editing = True
            _cursor_index = 0
            _selection_anchor = -1

            def _current_text_entry(self, _context):
                return page, entry, 0

        text_style.apply_ruby_span(entry, 4, 5, "かん", "group")
        probe = EditProbe()
        text_edit_history.begin(probe, entry)
        text_style.clear_ruby_spans(entry)
        text_edit_history.record(probe, entry)
        assert text_edit_history.restore_previous(probe, bpy.context)
        assert text_style.ruby_spans_snapshot(entry) == ((4, 5, "かん", "group"),)
        assert text_edit_history.restore_next(probe, bpy.context)
        assert text_style.ruby_spans_snapshot(entry) == ()

        bpy.ops.bmanga.text_ruby_add_dialog(
            "EXEC_DEFAULT",
            start=0,
            length=3,
            ruby_text="えーびーしー",
            style="group",
        )
        assert text_style.ruby_spans_snapshot(entry) == ((0, 3, "えーびーしー", "group"),)
        obj = text_real_object.ensure_text_real_object(scene=bpy.context.scene, entry=entry, page=page)
        assert obj is not None and not obj.hide_viewport

        selection_rna = bpy.ops.bmanga.text_selection_style_popup.get_rna_type()
        selection_props = {prop.identifier for prop in selection_rna.properties}
        assert {"ruby_text", "ruby_style"} <= selection_props, selection_props
        legacy_type = bpy.types.BMANGA_OT_text_ruby_add_dialog
        assert "INTERNAL" in legacy_type.bl_options
        legacy_invoke_source = inspect.getsource(legacy_type.invoke)
        assert "text_selection_style_popup" in legacy_invoke_source
        assert "invoke_props_dialog" not in legacy_invoke_source
        for keyconfig_name in ("addon", "user", "default"):
            keyconfig = getattr(bpy.context.window_manager.keyconfigs, keyconfig_name, None)
            if keyconfig is None:
                continue
            for keymap in keyconfig.keymaps:
                assert all(
                    item.idname != "bmanga.text_ruby_add_dialog"
                    for item in keymap.keymap_items
                ), (keyconfig_name, keymap.name)
        _ = work
        print("BMANGA_TEXT_RUBY_UX_OK")
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
