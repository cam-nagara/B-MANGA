"""Blender 実機用: B-MANGA レイヤー / フキダシしっぽのコピー貼り付け確認."""

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


def _create_work(work_dir: Path):
    result = bpy.ops.bmanga.work_new(filepath=str(work_dir))
    assert result == {"FINISHED"}, result
    work = bpy.context.scene.bmanga_work
    page = work.pages[0]

    balloon = page.balloons.add()
    balloon.id = "copy_balloon"
    balloon.x_mm = 20.0
    balloon.y_mm = 20.0
    balloon.width_mm = 30.0
    balloon.height_mm = 20.0
    tail = balloon.tails.add()
    tail.type = "straight"
    tail.direction_deg = 270.0
    tail.length_mm = 10.0
    tail.root_width_mm = 4.0
    tail.tip_width_mm = 1.0

    text = page.texts.add()
    text.id = "copy_text"
    text.body = "コピー"
    text.x_mm = 60.0
    text.y_mm = 20.0
    text.width_mm = 30.0
    text.height_mm = 20.0

    raster_result = bpy.ops.bmanga.raster_layer_add(
        "EXEC_DEFAULT",
        dpi=30,
        bit_depth="gray8",
        enter_paint=False,
    )
    assert "FINISHED" in raster_result, raster_result

    from bmanga_dev.operators import effect_line_op
    from bmanga_dev.utils import gp_layer_parenting as gp_parent
    from bmanga_dev.utils import gpencil as gp_utils
    from bmanga_dev.utils.geom import mm_to_m

    effect_line_op._create_effect_layer(
        bpy.context,
        (20.0, 60.0, 35.0, 35.0),
        parent_key="",
    )

    gp_obj = gp_utils.ensure_master_gpencil(bpy.context.scene)
    gp_layer = gp_obj.data.layers.new("copy_gp")
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

    from bmanga_dev.utils import layer_stack as layer_stack_utils

    layer_stack_utils.sync_layer_stack_after_data_change(bpy.context)
    return work


def _stack():
    from bmanga_dev.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(bpy.context)
    assert stack is not None
    return stack


def _count_kind(kind: str) -> int:
    return sum(1 for item in _stack() if str(getattr(item, "kind", "") or "") == kind)


def _select_first_kind(kind: str):
    from bmanga_dev.utils import layer_stack as layer_stack_utils

    for index, item in enumerate(_stack()):
        if str(getattr(item, "kind", "") or "") == kind:
            assert layer_stack_utils.select_stack_index(bpy.context, index), kind
            return item
    raise AssertionError(f"stack kind not found: {kind}")


def _copy_paste_kind(kind: str) -> None:
    _select_first_kind(kind)
    before = _count_kind(kind)
    result = bpy.ops.bmanga.layer_clipboard_copy("EXEC_DEFAULT")
    assert "FINISHED" in result, (kind, result)
    result = bpy.ops.bmanga.layer_clipboard_paste("EXEC_DEFAULT")
    assert "FINISHED" in result, (kind, result)
    after = _count_kind(kind)
    assert after == before + 1, (kind, before, after)


def _copy_paste_tail() -> None:
    item = _select_first_kind("balloon")
    from bmanga_dev.utils import layer_stack as layer_stack_utils

    resolved = layer_stack_utils.resolve_stack_item(bpy.context, item)
    entry = resolved.get("target")
    assert entry is not None
    before = len(entry.tails)
    assert before > 0
    result = bpy.ops.bmanga.balloon_tail_clipboard_copy("EXEC_DEFAULT")
    assert "FINISHED" in result, result
    result = bpy.ops.bmanga.balloon_tail_clipboard_paste("EXEC_DEFAULT")
    assert "FINISHED" in result, result
    assert len(entry.tails) == before * 2


def _assert_shortcuts_registered() -> None:
    kmaps = list(bpy.context.window_manager.keyconfigs.addon.keymaps)

    def has_kmi(idname: str, key: str, *, ctrl: bool = False, shift: bool = False) -> bool:
        for km in kmaps:
            for kmi in km.keymap_items:
                if (
                    kmi.idname == idname
                    and kmi.type == key
                    and bool(kmi.ctrl) == ctrl
                    and bool(kmi.shift) == shift
                ):
                    return True
        return False

    assert has_kmi("bmanga.layer_clipboard_copy", "C", ctrl=True)
    assert has_kmi("bmanga.layer_clipboard_paste", "V", ctrl=True)
    assert has_kmi("bmanga.balloon_tail_clipboard_copy", "C", ctrl=True, shift=True)
    assert has_kmi("bmanga.balloon_tail_clipboard_paste", "V", ctrl=True, shift=True)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_layer_clipboard_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        _create_work(temp_root / "Layer_Clipboard.bmanga")
        for kind in ("balloon", "text", "raster", "effect", "gp"):
            _copy_paste_kind(kind)
        _copy_paste_tail()
        _assert_shortcuts_registered()
        print("BMANGA_LAYER_CLIPBOARD_OK")
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
