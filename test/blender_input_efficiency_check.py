"""Blender 5.2実機用: テキスト・フキダシ・効果線の重複処理回帰。"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_input_efficiency",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_input_efficiency"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


class _CounterPatch:
    def __init__(self, owner, name: str):
        self.owner = owner
        self.name = name
        self.original = getattr(owner, name)
        self.count = 0

    def install(self):
        original = self.original

        def _counted(*args, **kwargs):
            self.count += 1
            return original(*args, **kwargs)

        setattr(self.owner, self.name, _counted)
        return self

    def reset(self) -> None:
        self.count = 0

    def restore(self) -> None:
        setattr(self.owner, self.name, self.original)


def _assert_text_updates_once(context, page):
    from bmanga_dev_input_efficiency.operators import text_caret_layout, text_op
    from bmanga_dev_input_efficiency.typography import layout as typography_layout
    from bmanga_dev_input_efficiency.utils import text_real_object, text_style
    from bmanga_dev_input_efficiency.utils.geom import Rect

    ensure = _CounterPatch(text_real_object, "ensure_text_real_object").install()
    render = _CounterPatch(text_real_object, "_render_entry_to_pillow").install()
    try:
        entry, missing_parent = text_op._create_text_entry(
            context,
            page,
            body="入力確認",
            x_mm=20.0,
            y_mm=30.0,
            width_mm=42.0,
            height_mm=18.0,
            parent_kind="page",
            parent_key=str(page.id),
        )
        assert not missing_parent
        assert ensure.count == 1, ("text create ensure", ensure.count)
        assert render.count == 1, ("text create render", render.count)

        ensure.reset()
        render.reset()
        entry.writing_mode = (
            "horizontal"
            if str(getattr(entry, "writing_mode", "vertical") or "vertical") == "vertical"
            else "vertical"
        )
        assert ensure.count == 1, ("writing mode ensure", ensure.count)
        assert render.count == 1, ("writing mode render", render.count)

        ensure.reset()
        render.reset()
        with text_real_object.suspend_auto_sync():
            text_op._set_text_rect(entry, 24.0, 33.0, 50.0, 26.0)
        text_op._sync_text_real_object(context, page, entry)
        assert ensure.count == 1, ("text rect ensure", ensure.count)
        assert render.count == 1, ("text rect render", render.count)

        with text_real_object.suspend_auto_sync():
            entry.body = "文字スタイル確認" * 12
            span = entry.style_spans.add()
            span.start = 3
            span.length = 24
            span.font_size_q = 28.0
            span.font_bold = True
        style_norm = _CounterPatch(text_style, "_normalized_style_segments").install()
        font_norm = _CounterPatch(text_style, "_normalized_segments").install()
        try:
            text_real_object.ensure_text_real_object(scene=context.scene, entry=entry, page=page)
            assert style_norm.count <= 6, ("style normalization", style_norm.count)
            assert font_norm.count <= 6, ("font normalization", font_norm.count)
        finally:
            style_norm.restore()
            font_norm.restore()

        typeset = _CounterPatch(typography_layout, "typeset").install()
        try:
            text_caret_layout._LAYOUT_CACHE.clear()
            rect = Rect(entry.x_mm, entry.y_mm, entry.width_mm, entry.height_mm)
            text_caret_layout.typeset_result(entry, rect)
            text_caret_layout.selection_rects(entry, rect, 1, 5)
            text_caret_layout.caret_rect(entry, rect, 5)
            assert typeset.count == 1, ("shared text layout", typeset.count)
        finally:
            typeset.restore()
        return entry
    finally:
        ensure.restore()
        render.restore()


def _assert_balloon_updates_once(context, page):
    from bmanga_dev_input_efficiency.operators import balloon_op
    from bmanga_dev_input_efficiency.utils import balloon_curve_object, layer_stack

    stack_sync = _CounterPatch(layer_stack, "sync_layer_stack").install()
    try:
        entry = balloon_op._create_balloon_entry(
            context,
            page,
            shape="ellipse",
            x=70.0,
            y=80.0,
            w=55.0,
            h=32.0,
            parent_kind="page",
            parent_key=str(page.id),
        )
        assert entry is not None
        assert stack_sync.count == 1, ("balloon create stack sync", stack_sync.count)
    finally:
        stack_sync.restore()

    ensure = _CounterPatch(balloon_curve_object, "ensure_balloon_curve_object").install()
    try:
        entry.line_style = "uni_flash"
        assert ensure.count == 1, ("balloon line style ensure", ensure.count)
        ensure.reset()
        entry.corner_type = "rounded"
        assert ensure.count == 1, ("balloon corner ensure", ensure.count)
    finally:
        ensure.restore()
    return entry


def _assert_effect_updates_once(context, page):
    from bmanga_dev_input_efficiency.operators import effect_line_gen, effect_line_op
    from bmanga_dev_input_efficiency.utils import (
        effect_line_object,
        layer_stack,
        material_opacity_mask,
        object_naming,
    )

    stack_sync = _CounterPatch(layer_stack, "sync_layer_stack").install()
    try:
        obj, layer = effect_line_op._create_effect_layer(
            context,
            (82.0, 92.0, 62.0, 48.0),
            parent_key=str(page.id),
        )
        assert obj is not None and layer is not None
        assert stack_sync.count == 1, ("effect create stack sync", stack_sync.count)
    finally:
        stack_sync.restore()

    # Outlinerミラーが制御Objectを正規実体へ差し替える場合があるため、
    # 作成APIの一時参照ではなく永続ID付きの現行実体を取り直す。
    obj = next(
        candidate
        for candidate in bpy.data.objects
        if str(candidate.get(object_naming.PROP_KIND, "") or "") == "effect"
    )
    layer = obj.data.layers[0]
    context.scene.bmanga_active_layer_kind = "effect"
    write = _CounterPatch(effect_line_op, "_write_effect_strokes").install()
    mesh = _CounterPatch(effect_line_object, "_rebuild_effect_display_mesh").install()
    shape = _CounterPatch(effect_line_gen, "_shape_outline").install()
    line_nodes = _CounterPatch(effect_line_object, "_configure_line_material_nodes").install()
    flat_nodes = _CounterPatch(material_opacity_mask, "setup_flat_emission_material").install()
    params = context.scene.bmanga_effect_line_params
    try:
        params.line_color = (0.15, 0.3, 0.6, 1.0)
        assert write.count == 0, ("material-only write", write.count)
        assert mesh.count == 0, ("material-only mesh", mesh.count)
        assert line_nodes.count == 0, ("material-only line nodes", line_nodes.count)
        assert flat_nodes.count == 0, ("material-only flat nodes", flat_nodes.count)
        saved = effect_line_op._layer_params_data(obj, layer)
        assert all(
            abs(float(actual) - expected) < 1.0e-5
            for actual, expected in zip(saved["line_color"], (0.15, 0.3, 0.6, 1.0))
        ), saved["line_color"]

        # 旧blend由来の未ラベルMaterialは、Meshを触らず一度だけ現行ノードへ更新する。
        display = effect_line_object.find_effect_display_object(obj)
        assert display is not None
        line_mat = display.data.materials[0]
        alpha_node = next(
            node for node in line_mat.node_tree.nodes if node.label == "効果線不透明度"
        )
        alpha_node.label = ""
        write.reset()
        mesh.reset()
        shape.reset()
        line_nodes.reset()
        flat_nodes.reset()
        params.line_color = (0.35, 0.2, 0.55, 1.0)
        assert write.count == 0, ("legacy material write", write.count)
        assert mesh.count == 0, ("legacy material mesh", mesh.count)
        assert line_nodes.count == 1, ("legacy line nodes", line_nodes.count)
        assert flat_nodes.count == 2, ("legacy flat nodes", flat_nodes.count)

        write.reset()
        mesh.reset()
        shape.reset()
        line_nodes.reset()
        flat_nodes.reset()
        params.start_corner_type = "rounded"
        assert write.count == 1, ("corner write", write.count)
        assert mesh.count == 1, ("corner mesh", mesh.count)
        assert shape.count == 2, ("precomputed outlines", shape.count)
        assert bool(params.start_rounded_corner_enabled)
    finally:
        write.restore()
        mesh.restore()
        shape.restore()
        line_nodes.restore()
        flat_nodes.restore()


def main() -> None:
    mod = None
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_input_efficiency_"))
    try:
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "InputEfficiency.bmanga"))
        assert result == {"FINISHED"}, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert result == {"FINISHED"}, result
        work = bpy.context.scene.bmanga_work
        page = work.pages[0]
        _assert_text_updates_once(bpy.context, page)
        _assert_balloon_updates_once(bpy.context, page)
        _assert_effect_updates_once(bpy.context, page)
        print("BMANGA_INPUT_EFFICIENCY_OK")
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
