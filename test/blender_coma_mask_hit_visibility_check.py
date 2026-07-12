"""Blender実機用: コマ外へ隠れた部分をクリック対象にしない確認."""

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


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_coma_hit_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "ComaHit.bmanga"))
        assert result == {"FINISHED"}, result

        from bmanga_dev.operators import balloon_op, effect_line_op, object_tool_selection, text_op
        from bmanga_dev.utils import gp_layer_parenting as gp_parent
        from bmanga_dev.utils import gpencil as gp_utils
        from bmanga_dev.utils import layer_hierarchy, object_selection, page_grid

        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]
        panel = page.comas[0]
        panel.shape_type = "rect"
        panel.rect_x_mm = 40.0
        panel.rect_y_mm = 40.0
        panel.rect_width_mm = 50.0
        panel.rect_height_mm = 50.0
        coma_key = layer_hierarchy.coma_stack_key(page, panel)
        ox, oy = page_grid.page_total_offset_mm(work, context.scene, 0)
        inside = (ox + 45.0, oy + 60.0)
        hidden = (ox + 35.0, oy + 60.0)

        balloon = balloon_op._create_balloon_entry(
            context,
            page,
            shape="ellipse",
            x=30.0,
            y=50.0,
            w=50.0,
            h=24.0,
            parent_kind="coma",
            parent_key=coma_key,
        )
        text, missing = text_op._create_text_entry(
            context,
            page,
            body="hit",
            x_mm=30.0,
            y_mm=55.0,
            width_mm=50.0,
            height_mm=16.0,
            parent_kind="coma",
            parent_key=coma_key,
        )
        assert not missing
        effect_obj, effect_layer = effect_line_op._create_effect_layer(
            context,
            (30.0, 55.0, 50.0, 18.0),
            parent_key=coma_key,
        )
        assert effect_obj is not None and effect_layer is not None
        assert gp_parent.parent_key(effect_layer) == coma_key
        image = context.scene.bmanga_image_layers.add()
        image.id = "image_hit"
        image.parent_kind = "coma"
        image.parent_key = coma_key
        raster = context.scene.bmanga_raster_layers.add()
        raster.id = "raster_hit"
        raster.parent_kind = "coma"
        raster.parent_key = coma_key
        gp_obj = gp_utils.ensure_page_gpencil(context.scene, page.id)
        gp_layer = gp_obj.data.layers.new("gp_hit")
        gp_parent.set_parent_key(gp_layer, coma_key)

        hits = [
            {"key": object_selection.balloon_key(page, balloon), "kind": "balloon"},
            {"key": object_selection.text_key(page, text), "kind": "text"},
            {"key": object_selection.effect_key(effect_layer), "kind": "effect"},
            {"key": object_selection.image_key(image), "kind": "image"},
            {"key": object_selection.raster_key(raster), "kind": "raster"},
            {"key": object_selection.gp_key(gp_layer), "kind": "gp"},
        ]
        for hit in hits:
            assert object_tool_selection.hit_visible_at_world(context, hit, *inside), hit
            assert not object_tool_selection.hit_visible_at_world(context, hit, *hidden), hit
        assert text_op._hit_text_entry(page, 35.0, 60.0)[1] is None
        assert balloon_op._hit_balloon_entry(page, 35.0, 60.0)[1] is None
        assert effect_line_op._hit_effect_layer(context, hidden[0], hidden[1])[1] is None
        print("BMANGA_COMA_MASK_HIT_VISIBILITY_OK")
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
