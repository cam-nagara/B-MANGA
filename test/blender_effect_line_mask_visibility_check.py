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
    assert stroke_count == 0, "効果線の制御用レイヤーにB-Name生成ストロークが残っています"


def _assert_display_uses_opacity_mask(display) -> None:
    from bname_dev_effect_mask_visibility.utils import mask_apply

    assert display.modifiers.get(mask_apply.MOD_NAME_COMA_MASK) is None, "効果線表示に古いコマ切り抜きが残っています"
    assert display.modifiers.get(mask_apply.MOD_NAME_PAGE_MASK) is None, "効果線表示に古いページ切り抜きが残っています"
    found = False
    for mat in getattr(getattr(display, "data", None), "materials", []) or []:
        if mat is None or not getattr(mat, "use_nodes", False) or mat.node_tree is None:
            continue
        for node in mat.node_tree.nodes:
            if getattr(node, "label", "") == "コマ内容マスク":
                found = True
                break
    assert found, "効果線表示にコマ内容マスクが接続されていません"


def _evaluated_polygon_count(obj) -> int:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return len(getattr(mesh, "polygons", []) or [])
    finally:
        evaluated.to_mesh_clear()


def _evaluated_world_bounds(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        coords = [evaluated.matrix_world @ vertex.co for vertex in getattr(mesh, "vertices", []) or []]
        assert coords, f"評価後メッシュが空です: {getattr(obj, 'name', '')}"
        return (
            min(coord.x for coord in coords),
            min(coord.y for coord in coords),
            max(coord.x for coord in coords),
            max(coord.y for coord in coords),
        )
    finally:
        evaluated.to_mesh_clear()


def _assert_bounds_inside(inner, outer, label: str, eps: float = 1.0e-5) -> None:
    assert inner[0] >= outer[0] - eps, f"{label} が左にはみ出しています: {inner} outside {outer}"
    assert inner[1] >= outer[1] - eps, f"{label} が下にはみ出しています: {inner} outside {outer}"
    assert inner[2] <= outer[2] + eps, f"{label} が右にはみ出しています: {inner} outside {outer}"
    assert inner[3] <= outer[3] + eps, f"{label} が上にはみ出しています: {inner} outside {outer}"


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_effect_mask_visibility_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "EffectMask.bname"))
        assert "FINISHED" in result, result
        # 現行仕様: コマ・効果線の編集と用紙背景の実体はページファイル側にある
        # (作品ファイルはページ一覧のみ)。ページファイルを開いてから検証する。
        result = bpy.ops.bname.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        from bname_dev_effect_mask_visibility.operators import coma_op, effect_line_op
        from bname_dev_effect_mask_visibility.utils import coma_border_object, coma_plane
        from bname_dev_effect_mask_visibility.utils import effect_line_object
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
        display = effect_line_object.find_effect_display_object(obj)
        assert display is not None, "効果線の表示実体がありません"
        assert obj.hide_viewport, "効果線の制御用レイヤーが表示対象のままです"
        assert not display.hide_viewport, "効果線の表示実体が非表示です"
        assert display.modifiers.get("B-Name Geometry Nodes") is None, "効果線の表示実体に重い生成ノードが残っています"
        _assert_display_uses_opacity_mask(display)
        assert len(display.data.polygons) > 0, "効果線の表示実体メッシュが空です"
        assert _evaluated_polygon_count(display) > 0, "効果線の表示結果が空です"
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
