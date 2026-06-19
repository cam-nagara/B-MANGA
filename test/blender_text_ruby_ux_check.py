"""Blender実機用: テキストのルビ・選択範囲・保存の微細挙動確認."""

from __future__ import annotations

import importlib.util
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
        from bmanga_dev.operators import text_edit_history, text_edit_runtime
        from bmanga_dev.typography import layout as text_layout, ruby as text_ruby
        from bmanga_dev.utils import text_real_object, text_style

        assert text_style.apply_ruby_span(entry, 0, 2, "かんじ", "group")
        assert text_style.ruby_spans_snapshot(entry) == ((0, 2, "かんじ", "group"),)

        data = schema.text_entry_to_dict(entry)
        assert data["rubySpans"][0]["rubyText"] == "かんじ"
        clone = page.texts.add()
        schema.text_entry_from_dict(clone, data)
        assert text_style.ruby_spans_snapshot(clone) == ((0, 2, "かんじ", "group"),)
        page.texts.remove(len(page.texts) - 1)

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
