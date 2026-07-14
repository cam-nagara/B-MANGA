"""Blender実機用: レイヤーリスト順とページ画像更新の同期確認."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_z_order_refresh",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_z_order_refresh"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _write_png(path: Path) -> None:
    image = bpy.data.images.new("bmanga_z_order_src", width=8, height=8, alpha=True)
    pixels = [1.0] * (8 * 8 * 4)
    for y in range(8):
        for x in range(8):
            i = (y * 8 + x) * 4
            pixels[i:i + 4] = (0.02, 0.02, 0.02, 1.0)
    image.pixels[:] = pixels
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()


def _page_and_coma(context):
    from bmanga_dev_z_order_refresh.utils import layer_hierarchy

    work = context.scene.bmanga_work
    page = work.pages[0]
    if len(page.comas) == 0:
        result = bpy.ops.bmanga.coma_add("EXEC_DEFAULT")
        assert "FINISHED" in result, result
    coma = page.comas[0]
    coma.shape_type = "rect"
    coma.rect_x_mm = 20.0
    coma.rect_y_mm = 30.0
    coma.rect_width_mm = 130.0
    coma.rect_height_mm = 150.0
    return work, page, coma, layer_hierarchy.page_stack_key(page), layer_hierarchy.coma_stack_key(page, coma)


def _create_image(context, page, parent_kind: str, parent_key: str, suffix: str, image_path: Path):
    from bmanga_dev_z_order_refresh.utils import image_real_object

    entry = context.scene.bmanga_image_layers.add()
    entry.id = f"z_image_{suffix}"
    entry.title = "画像"
    entry.filepath = str(image_path)
    entry.x_mm = 50.0
    entry.y_mm = 80.0
    entry.width_mm = 60.0
    entry.height_mm = 45.0
    entry.parent_kind = parent_kind
    entry.parent_key = parent_key
    obj = image_real_object.ensure_image_real_object(scene=context.scene, entry=entry, page=page)
    assert obj is not None
    return "画像", "image", entry.id, obj


def _create_image_path(context, page, parent_kind: str, parent_key: str, suffix: str):
    from bmanga_dev_z_order_refresh.utils import image_path_object

    entry = context.scene.bmanga_image_path_layers.add()
    entry.id = f"z_image_path_{suffix}"
    entry.title = "パターンカーブ"
    entry.content_source = "shape"
    entry.shape_kind = "circle"
    entry.draw_mode = "stamp"
    entry.brush_size_mm = 12.0
    entry.path_points_json = json.dumps([[30.0, 30.0], [90.0, 90.0], [140.0, 40.0]])
    entry.parent_kind = parent_kind
    entry.parent_key = parent_key
    obj = image_path_object.ensure_image_path_object(scene=context.scene, entry=entry, page=page)
    assert obj is not None
    return "パターンカーブ", "image_path", entry.id, obj


def _create_raster(context, parent_kind: str, parent_key: str, suffix: str):
    from bmanga_dev_z_order_refresh.operators import raster_layer_op

    entry = context.scene.bmanga_raster_layers.add()
    entry.id = f"z_raster_{suffix}"
    entry.title = "ラスター"
    entry.scope = "page"
    entry.parent_kind = parent_kind
    entry.parent_key = parent_key
    entry.width_mm = 70.0
    entry.height_mm = 50.0
    obj = raster_layer_op.ensure_raster_plane(context, entry)
    assert obj is not None
    return "ラスター", "raster", entry.id, obj


def _create_fill(context, page, parent_kind: str, parent_key: str, suffix: str):
    from bmanga_dev_z_order_refresh.utils import fill_real_object

    entry = context.scene.bmanga_fill_layers.add()
    entry.id = f"z_fill_{suffix}"
    entry.title = "グラデーション"
    entry.fill_type = "gradient"
    entry.gradient_type = "linear"
    entry.use_gradient_endpoints = True
    entry.gradient_start_x_mm = 20.0
    entry.gradient_start_y_mm = 20.0
    entry.gradient_end_x_mm = 150.0
    entry.gradient_end_y_mm = 170.0
    entry.parent_kind = parent_kind
    entry.parent_key = parent_key
    obj = fill_real_object.ensure_fill_real_object(scene=context.scene, entry=entry, page=page)
    assert obj is not None
    return "グラデーション", "fill", entry.id, obj


def _create_balloon(context, page, parent_kind: str, parent_key: str, suffix: str):
    from bmanga_dev_z_order_refresh.operators import balloon_op
    from bmanga_dev_z_order_refresh.utils import balloon_curve_object

    entry = balloon_op._create_balloon_entry(
        context,
        page,
        shape="ellipse",
        x=82.0,
        y=92.0,
        w=72.0,
        h=44.0,
        parent_kind=parent_kind,
        parent_key=parent_key,
    )
    entry.id = f"z_balloon_{suffix}"
    entry.title = "フキダシ"
    obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    assert obj is not None
    return "フキダシ", "balloon", f"{page.id}:{entry.id}", obj


def _create_text(context, page, parent_kind: str, parent_key: str, suffix: str):
    from bmanga_dev_z_order_refresh.operators import text_op
    from bmanga_dev_z_order_refresh.utils import text_real_object

    entry, missing = text_op._create_text_entry(
        context,
        page,
        body="順序",
        x_mm=82.0,
        y_mm=92.0,
        width_mm=55.0,
        height_mm=30.0,
        parent_kind=parent_kind,
        parent_key=parent_key,
    )
    assert not missing
    entry.id = f"z_text_{suffix}"
    entry.title = "テキスト"
    obj = text_real_object.ensure_text_real_object(scene=context.scene, entry=entry, page=page)
    assert obj is not None
    return "テキスト", "text", f"{page.id}:{entry.id}", obj


def _create_effect(context, parent_key: str, suffix: str):
    from bmanga_dev_z_order_refresh.operators import effect_line_op
    from bmanga_dev_z_order_refresh.utils import effect_line_object

    obj, layer = effect_line_op._create_effect_layer(
        context,
        (45.0, 55.0, 95.0, 85.0),
        parent_key=parent_key,
    )
    assert obj is not None and layer is not None
    obj["bmanga_title"] = f"効果線 {suffix}"
    display = effect_line_object.find_effect_display_object(obj) or obj
    return "効果線", "effect", "", display


def _stack_uid_for_target(kind: str, key: str, obj) -> str:
    from bmanga_dev_z_order_refresh.utils import layer_stack

    if kind == "effect":
        from bmanga_dev_z_order_refresh.utils import layer_object_model

        controller = obj if layer_object_model.is_layer_object(obj, "effect") else None
        if controller is None:
            controller_id = str(obj.get("bmanga_effect_controller_id", "") or "")
            if controller_id:
                from bmanga_dev_z_order_refresh.utils import object_naming as on

                controller = on.find_object_by_bmanga_id(controller_id, kind="effect")
        assert controller is not None
        return layer_stack.target_uid("effect", layer_object_model.stable_id(controller))
    return layer_stack.target_uid(kind, key)


def _move_uid(stack, uid: str, target_index: int) -> None:
    from bmanga_dev_z_order_refresh.utils import layer_stack

    current = next((i for i, item in enumerate(stack) if layer_stack.stack_item_uid(item) == uid), -1)
    assert current >= 0, f"レイヤーリストにありません: {uid}"
    stack.move(current, target_index)


def _assert_pair_order(context, parent_key: str, a, b) -> None:
    from bmanga_dev_z_order_refresh.utils import layer_stack

    stack = layer_stack.sync_layer_stack(context)
    assert stack is not None
    parent_index = next((i for i, item in enumerate(stack) if str(getattr(item, "key", "") or "") == parent_key), -1)
    assert parent_index >= 0, f"親行がありません: {parent_key}"
    uid_a = _stack_uid_for_target(a[1], a[2], a[3])
    uid_b = _stack_uid_for_target(b[1], b[2], b[3])
    _move_uid(stack, uid_b, parent_index + 1)
    _move_uid(stack, uid_a, parent_index + 1)
    layer_stack.apply_stack_order(context)
    context.view_layer.update()
    za = float(a[3].location.z)
    zb = float(b[3].location.z)
    if za <= zb:
        raise AssertionError(f"{a[0]} を {b[0]} より前面にできません: z={za} <= {zb}")


def _create_targets(context, page, parent_kind: str, parent_key: str, suffix: str, image_path: Path):
    return [
        _create_image(context, page, parent_kind, parent_key, suffix, image_path),
        _create_image_path(context, page, parent_kind, parent_key, suffix),
        _create_raster(context, parent_kind, parent_key, suffix),
        _create_fill(context, page, parent_kind, parent_key, suffix),
        _create_balloon(context, page, parent_kind, parent_key, suffix),
        _create_text(context, page, parent_kind, parent_key, suffix),
        _create_effect(context, parent_key, suffix),
    ]


def _assert_all_order_pairs(context, parent_key: str, targets: list[tuple]) -> int:
    count = 0
    for i, front in enumerate(targets):
        for j, back in enumerate(targets):
            if i == j:
                continue
            _assert_pair_order(context, parent_key, front, back)
            count += 1
    return count


def _assert_page_preview_refresh(context, work, page, preview_path: Path) -> None:
    before = preview_path.stat().st_mtime if preview_path.is_file() else 0.0
    result = bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT")
    assert "FINISHED" in result, result
    assert preview_path.is_file(), f"ページ画像が作成されていません: {preview_path}"
    after = preview_path.stat().st_mtime
    if after <= before:
        raise AssertionError("作品ファイルへ戻った後、ページ画像の更新時刻が進んでいません")
    from bmanga_dev_z_order_refresh.utils import page_file_scene, page_preview_object

    assert page_file_scene.is_work_list_scene(bpy.context.scene)
    page_preview_object.sync_page_previews(bpy.context, bpy.context.scene.bmanga_work, force=False)
    if preview_path.stat().st_mtime != after:
        raise AssertionError("作品ファイル側の同期でページ画像が再生成されています")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_z_order_refresh_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "ZOrder.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        context = bpy.context
        work, page, _coma, page_key, coma_key = _page_and_coma(context)
        image_path = temp_root / "source.png"
        _write_png(image_path)

        page_targets = _create_targets(context, page, "page", page_key, "page", image_path)
        coma_targets = _create_targets(context, page, "coma", coma_key, "coma", image_path)

        from bmanga_dev_z_order_refresh.utils import layer_stack

        layer_stack.sync_layer_stack_after_data_change(context)
        page_pairs = _assert_all_order_pairs(context, page_key, page_targets)
        coma_pairs = _assert_all_order_pairs(context, coma_key, coma_targets)

        preview_path = temp_root / "ZOrder.bmanga" / str(page.id) / "page_preview.png"
        _assert_page_preview_refresh(context, work, page, preview_path)

        print(f"BMANGA_LAYER_STACK_Z_ORDER_REFRESH_OK page_pairs={page_pairs} coma_pairs={coma_pairs}")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
