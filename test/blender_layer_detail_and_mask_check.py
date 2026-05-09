"""Blender実機用: 右クリック詳細設定と作成直後マスク適用の確認."""

from __future__ import annotations

import base64
import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYPgPAAEDAQCW"
    "A0r4AAAAAElFTkSuQmCC"
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_layer_mask",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_layer_mask"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _write_png(path: Path) -> None:
    path.write_bytes(base64.b64decode(PNG_1PX))


def _stack_index(context, kind: str, key: str) -> int:
    from bname_dev_layer_mask.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    assert stack is not None
    uid = layer_stack_utils.target_uid(kind, key)
    for index, item in enumerate(stack):
        if layer_stack_utils.stack_item_uid(item) == uid:
            return index
    return -1


def _assert_detail_menu(context, kind: str, key: str, selection_key: str) -> None:
    from bname_dev_layer_mask.operators import object_tool_selection
    from bname_dev_layer_mask.ui import context_menu
    from bname_dev_layer_mask.utils import layer_stack as layer_stack_utils

    index = _stack_index(context, kind, key)
    assert index >= 0, f"レイヤー一覧に見つかりません: {kind} {key}"
    assert layer_stack_utils.select_stack_index(context, index)
    object_tool_selection.sync_outliner_selection_for_keys(
        context,
        [selection_key],
    )
    items = context_menu.selection_command_items(context)
    assert items, f"右クリックメニューが空です: {kind}"
    detail = items[0]
    assert detail.get("label") == "詳細設定", detail
    assert detail.get("enabled"), f"詳細設定が無効です: {kind}"
    op_id = str(detail.get("operator", "") or "")
    assert op_id in {"bname.layer_detail_open", "bname.layer_stack_detail"}, (kind, op_id)
    namespace, name = op_id.split(".", 1)
    op = getattr(getattr(bpy.ops, namespace), name)
    assert op.poll(), f"詳細設定オペレーターが実行不能です: {kind} {op_id}"


def _assert_mesh_mask(obj, mod_name: str) -> None:
    mod = obj.modifiers.get(mod_name)
    assert mod is not None, f"マスクがありません: {obj.name} {mod_name}"
    assert getattr(mod, "object", None) is not None, f"マスク参照が空です: {obj.name}"
    assert str(getattr(mod, "operation", "")) == "INTERSECT", obj.name


def _assert_gp_mask(obj) -> None:
    layers = getattr(getattr(obj, "data", None), "layers", None)
    assert layers is not None, f"GPレイヤーがありません: {obj.name}"
    mask_layer = layers.get("__bname_mask")
    assert mask_layer is not None, f"GPマスクレイヤーがありません: {obj.name}"
    assert bool(getattr(mask_layer, "hide", False)), f"GPマスクレイヤーが表示されています: {obj.name}"
    content_layers = [layer for layer in layers if getattr(layer, "name", "") != "__bname_mask"]
    assert content_layers, f"マスク対象レイヤーがありません: {obj.name}"
    for layer in content_layers:
        assert bool(getattr(layer, "use_masks", False)), f"GPマスクが無効です: {obj.name}/{layer.name}"
        names = [str(getattr(item, "name", "") or "") for item in getattr(layer, "mask_layers", []) or []]
        assert "__bname_mask" in names, f"GPマスク参照がありません: {obj.name}/{layer.name} {names}"


def _assert_no_gp_mask(obj) -> None:
    layers = getattr(getattr(obj, "data", None), "layers", None)
    assert layers is not None, f"GPレイヤーがありません: {obj.name}"
    assert layers.get("__bname_mask") is None, f"不要なGPマスクがあります: {obj.name}"
    for layer in layers:
        assert not bool(getattr(layer, "use_masks", False)), (
            f"不要なGPマスク参照が有効です: {obj.name}/{layer.name}"
        )


def _gp_mask_bounds_mm(obj) -> tuple[float, float, float, float]:
    from bname_dev_layer_mask.utils.geom import m_to_mm

    layers = getattr(getattr(obj, "data", None), "layers", None)
    assert layers is not None
    mask_layer = layers.get("__bname_mask")
    assert mask_layer is not None
    xs: list[float] = []
    ys: list[float] = []
    for frame in getattr(mask_layer, "frames", []) or []:
        drawing = getattr(frame, "drawing", None)
        for stroke in getattr(drawing, "strokes", []) or []:
            for point in getattr(stroke, "points", []) or []:
                pos = getattr(point, "position", None)
                if pos is None:
                    continue
                xs.append(m_to_mm(float(pos[0])))
                ys.append(m_to_mm(float(pos[1])))
    assert xs and ys, f"GPマスク座標がありません: {obj.name}"
    return min(xs), min(ys), max(xs), max(ys)


def _assert_bounds_close(actual, expected, label: str, tol: float = 0.25) -> None:
    for index, (a, e) in enumerate(zip(actual, expected, strict=True)):
        assert abs(float(a) - float(e)) <= tol, f"{label}[{index}] expected {e}, got {a}"


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_layer_mask_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "LayerMask.bname"))
        assert "FINISHED" in result, result

        from bname_dev_layer_mask.operators import effect_line_op, raster_layer_op, text_op
        from bname_dev_layer_mask.utils import active_target
        from bname_dev_layer_mask.utils import balloon_curve_object
        from bname_dev_layer_mask.utils import gp_object_layer
        from bname_dev_layer_mask.utils import image_real_object
        from bname_dev_layer_mask.utils import layer_stack as layer_stack_utils
        from bname_dev_layer_mask.utils import mask_apply
        from bname_dev_layer_mask.utils import object_selection
        from bname_dev_layer_mask.utils import text_real_object
        from bname_dev_layer_mask.utils.layer_hierarchy import coma_stack_key, page_stack_key

        context = bpy.context
        scene = context.scene
        work = scene.bname_work
        page = work.pages[0]
        coma = page.comas[0]
        coma.shape_type = "rect"
        coma.rect_x_mm = 20.0
        coma.rect_y_mm = 40.0
        coma.rect_width_mm = 120.0
        coma.rect_height_mm = 160.0
        page_key = page_stack_key(page)
        ckey = coma_stack_key(page, coma)
        from bname_dev_layer_mask.utils import coma_plane

        coma_plane.ensure_coma_mask(scene, work, page, coma)

        active_target.focus_active_coma(scene, work, 0, 0)

        gp_obj = gp_object_layer.create_layer_gp_object(
            scene=scene,
            bname_id="mask_gp",
            title="GP",
            z_index=100,
            parent_kind="coma",
            parent_key=ckey,
        )
        assert gp_obj is not None

        effect_obj, effect_layer = effect_line_op._create_effect_layer(
            context,
            (25.0, 45.0, 30.0, 30.0),
            parent_key=ckey,
        )
        assert effect_obj is not None and effect_layer is not None

        result = bpy.ops.bname.raster_layer_add("EXEC_DEFAULT", dpi=30, bit_depth="gray8", enter_paint=False)
        assert "FINISHED" in result, result
        raster = scene.bname_raster_layers[scene.bname_active_raster_layer_index]
        raster_obj = raster_layer_op.ensure_raster_plane(context, raster)
        assert raster_obj is not None

        image_path = temp_root / "source.png"
        _write_png(image_path)
        image = scene.bname_image_layers.add()
        image.id = "mask_image"
        image.title = "画像"
        image.filepath = str(image_path)
        image.x_mm = 25.0
        image.y_mm = 45.0
        image.width_mm = 40.0
        image.height_mm = 30.0
        image.parent_kind = "coma"
        image.parent_key = ckey
        image_obj = image_real_object.ensure_image_real_object(scene=scene, entry=image, page=page)
        assert image_obj is not None

        text, _missing = text_op._create_text_entry(
            context,
            page,
            body="詳細",
            speaker_type="normal",
            x_mm=25.0,
            y_mm=45.0,
            width_mm=35.0,
            height_mm=20.0,
            parent_kind="coma",
            parent_key=ckey,
        )
        text_obj = text_real_object.find_text_object(page.id, text.id)
        assert text_obj is not None

        balloon = page.balloons.add()
        balloon.id = "mask_balloon"
        balloon.title = "フキダシ"
        balloon.shape = "ellipse"
        balloon.x_mm = 10.0
        balloon.y_mm = 35.0
        balloon.width_mm = 170.0
        balloon.height_mm = 80.0
        balloon.parent_kind = "coma"
        balloon.parent_key = ckey
        balloon_obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=balloon, page=page)
        assert balloon_obj is not None
        balloon_fill_obj = bpy.data.objects.get(
            f"{balloon_curve_object.BALLOON_FILL_NAME_PREFIX}{balloon.id}"
        )
        assert balloon_fill_obj is not None

        page_text, _missing = text_op._create_text_entry(
            context,
            page,
            body="ページ",
            speaker_type="normal",
            x_mm=150.0,
            y_mm=180.0,
            width_mm=35.0,
            height_mm=20.0,
            parent_kind="page",
            parent_key=page_key,
        )
        page_text_obj = text_real_object.find_text_object(page.id, page_text.id)
        assert page_text_obj is not None

        page_balloon = page.balloons.add()
        page_balloon.id = "mask_page_balloon"
        page_balloon.title = "ページフキダシ"
        page_balloon.shape = "ellipse"
        page_balloon.x_mm = -30.0
        page_balloon.y_mm = 40.0
        page_balloon.width_mm = 120.0
        page_balloon.height_mm = 60.0
        page_balloon.parent_kind = "page"
        page_balloon.parent_key = page_key
        page_balloon_obj = balloon_curve_object.ensure_balloon_curve_object(
            scene=scene,
            entry=page_balloon,
            page=page,
        )
        assert page_balloon_obj is not None
        page_balloon_fill_obj = bpy.data.objects.get(
            f"{balloon_curve_object.BALLOON_FILL_NAME_PREFIX}{page_balloon.id}"
        )
        assert page_balloon_fill_obj is not None

        layer_stack_utils.sync_layer_stack_after_data_change(context)

        _assert_gp_mask(gp_obj)
        _assert_no_gp_mask(effect_obj)
        _assert_bounds_close(_gp_mask_bounds_mm(gp_obj), (20.0, 40.0, 140.0, 200.0), "gp coma mask bounds")
        _assert_mesh_mask(raster_obj, mask_apply.MOD_NAME_COMA_MASK)
        _assert_mesh_mask(image_obj, mask_apply.MOD_NAME_COMA_MASK)
        _assert_mesh_mask(text_obj, mask_apply.MOD_NAME_COMA_MASK)
        _assert_mesh_mask(balloon_obj, mask_apply.MOD_NAME_COMA_MASK)
        _assert_mesh_mask(balloon_fill_obj, mask_apply.MOD_NAME_COMA_MASK)
        _assert_mesh_mask(page_text_obj, mask_apply.MOD_NAME_PAGE_MASK)
        _assert_mesh_mask(page_balloon_obj, mask_apply.MOD_NAME_PAGE_MASK)
        _assert_mesh_mask(page_balloon_fill_obj, mask_apply.MOD_NAME_PAGE_MASK)

        effect_key = layer_stack_utils._node_stack_key(effect_layer)
        _assert_detail_menu(context, "effect", effect_key, object_selection.effect_key(effect_layer))
        _assert_detail_menu(context, "raster", raster.id, object_selection.raster_key(raster))
        _assert_detail_menu(context, "image", image.id, object_selection.image_key(image))
        _assert_detail_menu(context, "text", f"{page.id}:{text.id}", object_selection.text_key(page, text))
        _assert_detail_menu(
            context,
            "balloon",
            f"{page.id}:{balloon.id}",
            object_selection.balloon_key(page, balloon),
        )
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
    print("BNAME_LAYER_DETAIL_AND_MASK_OK")
