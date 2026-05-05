"""Blender 実機用: B-Name 右クリックメニュー項目の確認."""

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
        "bname_dev",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _create_work(work_dir: Path):
    result = bpy.ops.bname.work_new(filepath=str(work_dir))
    assert result == {"FINISHED"}, result
    work = bpy.context.scene.bname_work
    page = work.pages[0]
    result = bpy.ops.bname.coma_add()
    assert result == {"FINISHED"}, result

    balloon = page.balloons.add()
    balloon.id = "menu_balloon"
    balloon.x_mm = 20.0
    balloon.y_mm = 20.0
    balloon.width_mm = 30.0
    balloon.height_mm = 20.0

    text = page.texts.add()
    text.id = "menu_text"
    text.body = "右クリック"
    text.x_mm = 60.0
    text.y_mm = 20.0
    text.width_mm = 30.0
    text.height_mm = 20.0

    from bname_dev.operators import effect_line_op
    from bname_dev.utils import gp_layer_parenting as gp_parent
    from bname_dev.utils import gpencil as gp_utils
    from bname_dev.utils.geom import mm_to_m

    effect_line_op._create_effect_layer(
        bpy.context,
        (20.0, 60.0, 35.0, 35.0),
        parent_key="",
    )

    gp_obj = gp_utils.ensure_master_gpencil(bpy.context.scene)
    gp_layer = gp_obj.data.layers.new("menu_gp")
    gp_parent.set_parent_key(gp_layer, "")
    frame = gp_utils.ensure_active_frame(gp_layer)
    assert frame is not None and getattr(frame, "drawing", None) is not None
    assert gp_utils.add_stroke_to_drawing(
        frame.drawing,
        [
            (mm_to_m(100.0), mm_to_m(40.0), 0.0),
            (mm_to_m(120.0), mm_to_m(60.0), 0.0),
        ],
    )

    raster_result = bpy.ops.bname.raster_layer_add("EXEC_DEFAULT", dpi=30, bit_depth="gray8", enter_paint=False)
    assert "FINISHED" in raster_result, raster_result

    image = bpy.context.scene.bname_image_layers.add()
    image.id = "menu_image"
    image.title = "画像"
    image.x_mm = 100.0
    image.y_mm = 70.0
    image.width_mm = 20.0
    image.height_mm = 15.0

    from bname_dev.utils import layer_stack as layer_stack_utils

    layer_stack_utils.sync_layer_stack_after_data_change(bpy.context)
    return work


def _stack_index_for_kind(kind: str) -> int:
    from bname_dev.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(bpy.context)
    assert stack is not None
    for index, item in enumerate(stack):
        if str(getattr(item, "kind", "") or "") == kind:
            return index
    raise AssertionError(f"stack kind not found: {kind}")


def _assert_menu_for_kind(kind: str) -> None:
    from bname_dev.ui import context_menu
    from bname_dev.utils import layer_stack as layer_stack_utils

    index = _stack_index_for_kind(kind)
    assert layer_stack_utils.select_stack_index(bpy.context, index)
    items = context_menu.selection_command_items(bpy.context)
    labels = [str(item.get("label", "")) for item in items]
    assert labels == ["詳細設定", "複製", "リンク複製", "削除"], (kind, labels)
    for item in items:
        op_id = str(item.get("operator", "") or "")
        namespace, name = op_id.split(".", 1)
        assert getattr(getattr(bpy.ops, namespace), name, None) is not None, (kind, op_id)
    enabled = {str(item.get("label", "")): bool(item.get("enabled", False)) for item in items}
    assert enabled["詳細設定"]
    assert enabled["複製"]
    assert enabled["削除"]
    assert enabled["リンク複製"] is (kind == "effect"), (kind, enabled)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_context_menu_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        _create_work(temp_root / "Context_Menu.bname")
        for kind in ("page", "coma", "gp", "effect", "raster", "image", "balloon", "text"):
            _assert_menu_for_kind(kind)
        print("BNAME_CONTEXT_MENU_COMMANDS_OK")
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
