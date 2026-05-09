"""Blender実機用: コマ内の効果線作成でコマ表示を覆わないことを確認."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_effect_mask_visibility",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_effect_mask_visibility"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _assert_coma_objects_visible(page) -> None:
    from bname_dev_effect_mask_visibility.utils import coma_border_object, coma_plane

    for coma in page.comas:
        border = bpy.data.objects.get(
            f"{coma_border_object.COMA_BORDER_NAME_PREFIX}{page.id}_{coma.id}"
        )
        plane = coma_plane.find_coma_plane_object(page.id, coma.id)
        assert border is not None, f"コマ枠がありません: {coma.id}"
        assert plane is not None, f"コマ面がありません: {coma.id}"
        assert not border.hide_viewport, f"コマ枠が非表示です: {coma.id}"
        assert not plane.hide_viewport, f"コマ面が非表示です: {coma.id}"


def _assert_page_background_not_promoted(page) -> None:
    from bname_dev_effect_mask_visibility.utils import mask_apply, paper_bg_object

    bg = bpy.data.objects.get(f"{paper_bg_object.PAPER_BG_NAME_PREFIX}{page.id}")
    assert bg is not None, "用紙背景がありません"
    assert bg.modifiers.get(mask_apply.MOD_NAME_PAGE_MASK_VOLUME) is None, (
        "表示用の用紙背景にマスク用の厚みが残っています"
    )
    for obj in bpy.data.objects:
        if not obj.name.startswith(mask_apply.PAGE_MASK_VOLUME_NAME_PREFIX):
            continue
        assert obj.hide_viewport and obj.hide_render and obj.hide_select, (
            f"ページマスク用オブジェクトが表示対象になっています: {obj.name}"
        )


def _assert_effect_not_masked(obj) -> None:
    layers = getattr(getattr(obj, "data", None), "layers", None)
    assert layers is not None
    mask_layer = layers.get("__bname_mask")
    assert mask_layer is None, "効果線に不要なコマ内マスクがあります"
    content_layers = list(layers)
    assert content_layers, "効果線本体のレイヤーがありません"
    assert any(not bool(getattr(layer, "hide", False)) for layer in content_layers), (
        "効果線本体が非表示です"
    )
    stroke_count = 0
    for layer in content_layers:
        assert not bool(getattr(layer, "use_masks", False)), "効果線に不要なマスク参照があります"
        for frame in getattr(layer, "frames", []) or []:
            drawing = getattr(frame, "drawing", None)
            stroke_count += len(getattr(drawing, "strokes", []) or []) if drawing is not None else 0
    assert stroke_count > 0, "効果線本体の線がありません"


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_effect_mask_visibility_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "EffectMask.bname"))
        assert "FINISHED" in result, result

        from bname_dev_effect_mask_visibility.operators import coma_op, effect_line_op
        from bname_dev_effect_mask_visibility.utils import coma_border_object, coma_plane
        from bname_dev_effect_mask_visibility.utils.layer_hierarchy import coma_stack_key

        context = bpy.context
        scene = context.scene
        work = scene.bname_work
        page = work.pages[0]
        first = page.comas[0]
        first.shape_type = "rect"
        first.rect_x_mm = 20.0
        first.rect_y_mm = 40.0
        first.rect_width_mm = 120.0
        first.rect_height_mm = 135.0

        result = bpy.ops.bname.coma_add()
        assert "FINISHED" in result, result
        second = page.comas[1]
        second.shape_type = "rect"
        second.rect_x_mm = 35.0
        second.rect_y_mm = 190.0
        second.rect_width_mm = 95.0
        second.rect_height_mm = 70.0

        for coma in page.comas:
            coma_plane.ensure_coma_plane(scene, work, page, coma)
            coma_plane.ensure_coma_mask(scene, work, page, coma)
            coma_border_object.ensure_coma_border_object(scene, work, page, coma)

        _assert_coma_objects_visible(page)
        _assert_page_background_not_promoted(page)
        from bname_dev_effect_mask_visibility.utils import mask_apply, paper_bg_object

        bg = bpy.data.objects.get(f"{paper_bg_object.PAPER_BG_NAME_PREFIX}{page.id}")
        assert bg is not None
        bg.modifiers.new(name=mask_apply.MOD_NAME_PAGE_MASK_VOLUME, type="SOLIDIFY")
        assert bg.modifiers.get(mask_apply.MOD_NAME_PAGE_MASK_VOLUME) is not None
        parent_key = coma_stack_key(page, first)
        obj, layer = effect_line_op._create_effect_layer(
            context,
            (45.0, 70.0, 2.0, 2.0),
            parent_key=parent_key,
        )
        assert obj is not None and layer is not None
        effect_line_op._write_effect_strokes(context, obj, layer, (45.0, 70.0, 55.0, 45.0))
        effect_line_op.layer_stack_utils.sync_layer_stack_after_data_change(context)
        _assert_coma_objects_visible(page)
        _assert_page_background_not_promoted(page)
        _assert_effect_not_masked(obj)
        print("BNAME_EFFECT_LINE_MASK_VISIBILITY_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
