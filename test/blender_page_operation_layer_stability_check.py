"""Blender実機用: ページ操作で各レイヤーのページ内位置が変わらないことを確認。"""

from __future__ import annotations

import importlib.util
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


def _assert_close(actual: float, expected: float, label: str, eps: float = 1.0e-4) -> None:
    if abs(float(actual) - float(expected)) > eps:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def _page_index(work, page_id: str) -> int:
    for index, page in enumerate(work.pages):
        if str(getattr(page, "id", "") or "") == page_id:
            return index
    raise AssertionError(f"ページが見つかりません: {page_id}")


def _page_by_id(work, page_id: str):
    return work.pages[_page_index(work, page_id)]


def _page_offset(context, work, page_id: str) -> tuple[float, float]:
    from bmanga_dev.utils import page_grid

    return page_grid.page_total_offset_mm(work, context.scene, _page_index(work, page_id))


def _object_page_local_mm(context, work, page_id: str, obj) -> tuple[float, float]:
    ox, oy = _page_offset(context, work, page_id)
    return float(obj.location.x) * 1000.0 - ox, float(obj.location.y) * 1000.0 - oy


def _first_gp_point(layer):
    from bmanga_dev.utils import gp_layer_parenting as gp_parent

    for point in gp_parent.iter_points(layer):
        return point
    raise AssertionError("GPレイヤーに点がありません")


def _gp_point_page_local_mm(context, work, page_id: str, gp_obj, layer) -> tuple[float, float]:
    point = _first_gp_point(layer)
    ox, oy = _page_offset(context, work, page_id)
    return (
        float(gp_obj.location.x + point.position.x) * 1000.0 - ox,
        float(gp_obj.location.y + point.position.y) * 1000.0 - oy,
    )


def _raster_marker_alpha(image, marker: tuple[int, int]) -> float:
    x, y = marker
    width = int(image.size[0])
    return float(image.pixels[(y * width + x) * 4 + 3])


def _write_test_image(path: Path) -> None:
    image = bpy.data.images.new("bmanga_page_stability_image_src", width=8, height=8, alpha=True)
    pixels = [0.0] * (8 * 8 * 4)
    for y in range(2, 6):
        for x in range(2, 6):
            i = (y * 8 + x) * 4
            pixels[i:i + 4] = (0.0, 0.0, 0.0, 1.0)
    image.pixels[:] = pixels
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()


def _add_gp(context, work, page, local_xy: tuple[float, float]):
    from bmanga_dev.utils import gp_object_layer, layer_object_model
    from bmanga_dev.utils import gpencil as gp_utils
    from bmanga_dev.utils.geom import mm_to_m
    from bmanga_dev.utils.layer_hierarchy import page_stack_key

    page_id = str(getattr(page, "id", "") or "")
    obj = gp_object_layer.create_layer_gp_object(
        scene=context.scene,
        bmanga_id=layer_object_model.make_stable_id("gp"),
        title=f"stability_gp_{page_id}",
        z_index=210,
        parent_kind="page",
        parent_key=page_stack_key(page),
    )
    layer = layer_object_model.content_layer(obj)
    assert layer is not None
    frame = gp_utils.ensure_active_frame(layer)
    assert frame is not None and getattr(frame, "drawing", None) is not None
    ok = gp_utils.add_stroke_to_drawing(
        frame.drawing,
        [
            (mm_to_m(local_xy[0]), mm_to_m(local_xy[1]), 0.0),
            (mm_to_m(local_xy[0] + 12.0), mm_to_m(local_xy[1] + 6.0), 0.0),
        ],
    )
    assert ok
    return obj, layer


def _add_balloon(context, page, local_xy: tuple[float, float]):
    from bmanga_dev.operators import balloon_op
    from bmanga_dev.utils import balloon_curve_object
    from bmanga_dev.utils.layer_hierarchy import page_stack_key

    page_id = str(getattr(page, "id", "") or "")
    entry = balloon_op._create_balloon_entry(
        context,
        page,
        shape="ellipse",
        x=local_xy[0],
        y=local_xy[1],
        w=30.0,
        h=18.0,
        parent_kind="page",
        parent_key=page_stack_key(page),
    )
    entry.id = f"stability_balloon_{page_id}"
    obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    assert obj is not None
    return entry, obj


def _add_text(context, page, local_xy: tuple[float, float]):
    from bmanga_dev.utils import text_real_object
    from bmanga_dev.utils.layer_hierarchy import page_stack_key

    page_id = str(getattr(page, "id", "") or "")
    entry = page.texts.add()
    entry.id = f"stability_text_{page_id}"
    entry.body = "テスト"
    entry.x_mm = local_xy[0]
    entry.y_mm = local_xy[1]
    entry.width_mm = 26.0
    entry.height_mm = 20.0
    entry.parent_kind = "page"
    entry.parent_key = page_stack_key(page)
    obj = text_real_object.ensure_text_real_object(scene=context.scene, entry=entry, page=page)
    assert obj is not None
    return entry, obj


def _add_image(context, image_path: Path, page, local_xy: tuple[float, float]):
    from bmanga_dev.utils import image_real_object
    from bmanga_dev.utils.layer_hierarchy import page_stack_key

    page_id = str(getattr(page, "id", "") or "")
    entry = context.scene.bmanga_image_layers.add()
    entry.id = f"stability_image_{page_id}"
    entry.title = "画像"
    entry.filepath = str(image_path)
    entry.x_mm = local_xy[0]
    entry.y_mm = local_xy[1]
    entry.width_mm = 24.0
    entry.height_mm = 16.0
    entry.parent_kind = "page"
    entry.parent_key = page_stack_key(page)
    obj = image_real_object.ensure_image_real_object(scene=context.scene, entry=entry, page=page)
    assert obj is not None
    return entry, obj


def _add_raster(context, page, marker: tuple[int, int]):
    from bmanga_dev.operators import raster_layer_op
    from bmanga_dev.utils.layer_hierarchy import page_stack_key

    result = bpy.ops.bmanga.raster_layer_add("EXEC_DEFAULT", dpi=30, bit_depth="gray8", enter_paint=False)
    assert "FINISHED" in result, result
    entry = context.scene.bmanga_raster_layers[context.scene.bmanga_active_raster_layer_index]
    page_id = str(getattr(page, "id", "") or "")
    entry.id = f"stability_raster_{page_id}"
    entry.image_name = raster_layer_op.raster_image_name(entry.id)
    entry.filepath_rel = raster_layer_op.raster_filepath_rel(entry.id)
    entry.parent_kind = "page"
    entry.parent_key = page_stack_key(page)
    image = raster_layer_op.ensure_raster_image(context, entry, create_missing=True)
    assert image is not None
    pixels = [0.0] * (int(image.size[0]) * int(image.size[1]) * 4)
    x, y = marker
    pixels[(y * int(image.size[0]) + x) * 4:(y * int(image.size[0]) + x) * 4 + 4] = [0.0, 0.0, 0.0, 1.0]
    image.pixels[:] = pixels
    image.update()
    obj = raster_layer_op.ensure_raster_plane(context, entry)
    assert obj is not None
    return entry, obj, image


def _add_effect(context, page, bounds: tuple[float, float, float, float]):
    from bmanga_dev.operators import effect_line_op
    from bmanga_dev.utils.layer_hierarchy import page_stack_key

    obj, layer = effect_line_op._create_effect_layer(context, bounds, parent_key=page_stack_key(page))
    assert obj is not None and layer is not None
    return obj, layer


def _make_page_content(context, work, page, image_path: Path, base_x: float):
    page_id = str(getattr(page, "id", "") or "")
    content = {
        "page_id": page_id,
        "gp": _add_gp(context, work, page, (base_x + 6.0, 24.0)),
        "balloon": _add_balloon(context, page, (base_x + 12.0, 42.0)),
        "text": _add_text(context, page, (base_x + 18.0, 68.0)),
        "image": _add_image(context, image_path, page, (base_x + 24.0, 96.0)),
        "raster": _add_raster(context, page, (4, 4)),
        "effect": _add_effect(context, page, (base_x + 30.0, 128.0, 38.0, 26.0)),
    }
    context.view_layer.update()
    return content


def _content_refs(content) -> dict:
    """ファイル切替後も追跡できるよう、実体への参照を ID/名前に落とす."""
    return {
        "page_id": content["page_id"],
        "gp_id": str(content["gp"][0].get("bmanga_id", "") or ""),
        "balloon_id": str(content["balloon"][0].id),
        "text_id": str(content["text"][0].id),
        "image_id": str(content["image"][0].id),
        "raster_id": str(content["raster"][0].id),
        "raster_image": str(content["raster"][2].name),
    }


def _entry_by_id(collection, entry_id: str):
    for entry in collection:
        if str(getattr(entry, "id", "") or "") == entry_id:
            return entry
    return None


def _snapshot_content(context, work, refs):
    """ページ編集シーン上で、各レイヤーの実体を ID から取り直して位置を採取する."""
    from bmanga_dev.operators import effect_line_op
    from bmanga_dev.utils import balloon_curve_object, effect_line_object
    from bmanga_dev.utils import layer_object_model
    from bmanga_dev.utils import object_naming as on

    page_id = refs["page_id"]
    page = _page_by_id(work, page_id)

    gp_obj = layer_object_model.find_layer_object("gp", refs["gp_id"])
    assert gp_obj is not None, "下書きの実体がありません"
    gp_layer = layer_object_model.content_layer(gp_obj)
    assert gp_layer is not None, f"下書きレイヤーがありません: {refs['gp_id']}"

    balloon_entry = _entry_by_id(page.balloons, refs["balloon_id"])
    assert balloon_entry is not None, "フキダシのデータがありません"
    balloon_obj = balloon_curve_object.find_balloon_object(refs["balloon_id"])
    assert balloon_obj is not None, "フキダシの実体がありません"

    text_entry = _entry_by_id(page.texts, refs["text_id"])
    assert text_entry is not None, "テキストのデータがありません"
    from bmanga_dev.utils import text_real_object

    text_obj = text_real_object.find_text_object(page_id, refs["text_id"])
    assert text_obj is not None, "テキストの実体がありません"

    image_entry = _entry_by_id(context.scene.bmanga_image_layers, refs["image_id"])
    assert image_entry is not None, "画像レイヤーのデータがありません"
    image_obj = on.find_object_by_bmanga_id(refs["image_id"], kind="image")
    assert image_obj is not None, "画像レイヤーの実体がありません"

    raster_obj = on.find_object_by_bmanga_id(refs["raster_id"], kind="raster")
    assert raster_obj is not None, "ラスターレイヤーの実体がありません"
    raster_image = bpy.data.images.get(refs["raster_image"])
    assert raster_image is not None, "ラスターレイヤーの画像がありません"

    effect_obj = next(
        (
            o
            for o in bpy.data.objects
            if str(o.get("bmanga_kind", "") or "") == "effect"
            and str(o.get("bmanga_parent_key", "") or "").split(":", 1)[0] == page_id
        ),
        None,
    )
    assert effect_obj is not None, "効果線の実体がありません"
    effect_layer = effect_obj.data.layers[0]
    effect_display = effect_line_object.find_effect_display_object(effect_obj)
    return {
        "gp": _gp_point_page_local_mm(context, work, page_id, gp_obj, gp_layer),
        "balloon_entry": (
            float(balloon_entry.x_mm),
            float(balloon_entry.y_mm),
            float(balloon_entry.width_mm),
            float(balloon_entry.height_mm),
        ),
        "balloon_obj": _object_page_local_mm(context, work, page_id, balloon_obj),
        "text_entry": (
            float(text_entry.x_mm),
            float(text_entry.y_mm),
            float(text_entry.width_mm),
            float(text_entry.height_mm),
        ),
        "text_obj": _object_page_local_mm(context, work, page_id, text_obj),
        "image_entry": (
            float(image_entry.x_mm),
            float(image_entry.y_mm),
            float(image_entry.width_mm),
            float(image_entry.height_mm),
        ),
        "image_obj": _object_page_local_mm(context, work, page_id, image_obj),
        "raster_obj": _object_page_local_mm(context, work, page_id, raster_obj),
        "raster_alpha": _raster_marker_alpha(raster_image, (4, 4)),
        "effect_obj": _object_page_local_mm(context, work, page_id, effect_obj),
        "effect_bounds": effect_line_op.effect_layer_bounds(effect_obj, effect_layer),
        "effect_display": (
            None if effect_display is None else _object_page_local_mm(context, work, page_id, effect_display)
        ),
    }


def _assert_tuple_close(actual, expected, label: str) -> None:
    if actual is None or expected is None:
        if actual != expected:
            raise AssertionError(f"{label}: expected {expected}, got {actual}")
        return
    if len(actual) != len(expected):
        raise AssertionError(f"{label}: tuple length mismatch")
    for index, (a, e) in enumerate(zip(actual, expected, strict=False)):
        _assert_close(a, e, f"{label}[{index}]")


def _assert_stable(context, work, refs, expected, label: str) -> None:
    current = _snapshot_content(context, work, refs)
    for key, value in expected.items():
        if isinstance(value, tuple):
            _assert_tuple_close(current[key], value, f"{label} {key}")
        else:
            _assert_close(current[key], value, f"{label} {key}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_page_ops_stability_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PageOpsStability.bmanga"))
        assert result == {"FINISHED"}, result

        from bmanga_dev.core.work import get_work
        from bmanga_dev.utils import page_file_scene

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        work.work_info.page_number_end = 3
        context.view_layer.update()
        if len(work.pages) != 3:
            raise AssertionError(f"ページ数の準備に失敗しました: {len(work.pages)}")
        image_path = temp_root / "source.png"
        _write_test_image(image_path)
        page_a_id = str(work.pages[0].id)
        page_b_id = str(work.pages[1].id)

        # v0.6.279 以降、コマ・フキダシ等の実体はページ用 blend のみが持つ。
        # ページを開いて内容を作り、ページ操作のたびに開き直して
        # ページ内位置が変わっていないことを検証する。
        def _open_page(page_id: str):
            work_now = get_work(bpy.context)
            index = _page_index(work_now, page_id)
            result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=index)
            assert result == {"FINISHED"}, f"ページを開けません: {page_id} {result}"
            assert page_file_scene.is_page_edit_scene(bpy.context.scene)
            return bpy.context, get_work(bpy.context)

        def _close_page() -> None:
            result = bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT")
            assert "FINISHED" in result, f"ページ一覧へ戻れません: {result}"

        def _build(page_id: str, base_x: float):
            ctx, work_now = _open_page(page_id)
            page = _page_by_id(work_now, page_id)
            content = _make_page_content(ctx, work_now, page, image_path, base_x)
            refs = _content_refs(content)
            ctx.view_layer.update()
            _close_page()
            # 基準は保存→再読込後の状態で採取する (作成直後の一時値を避ける)
            ctx, work_now = _open_page(page_id)
            expected = _snapshot_content(ctx, work_now, refs)
            _close_page()
            # 基準そのものが設定値どおりであることを確認しておく
            _assert_close(expected["balloon_entry"][0], base_x + 12.0, "基準値 フキダシX")
            _assert_close(expected["text_entry"][0], base_x + 18.0, "基準値 テキストX")
            _assert_close(expected["image_entry"][0], base_x + 24.0, "基準値 画像X")
            return refs, expected

        def _verify(refs, expected, label: str) -> None:
            ctx, work_now = _open_page(refs["page_id"])
            _assert_stable(ctx, work_now, refs, expected, label)
            _close_page()

        refs_a, expected_a = _build(page_a_id, 10.0)
        refs_b, expected_b = _build(page_b_id, 70.0)
        _verify(refs_a, expected_a, "保存再読込 1ページ目")
        _verify(refs_b, expected_b, "保存再読込 2ページ目")

        work = get_work(bpy.context)
        work.work_info.page_number_end = 5
        bpy.context.view_layer.update()
        _verify(refs_a, expected_a, "ページ追加 1ページ目")
        _verify(refs_b, expected_b, "ページ追加 2ページ目")

        work = get_work(bpy.context)
        work.active_page_index = _page_index(work, page_a_id)
        assert bpy.ops.bmanga.page_move("EXEC_DEFAULT", direction=1) == {"FINISHED"}
        bpy.context.view_layer.update()
        _verify(refs_a, expected_a, "ページ入れ替え 元1ページ目")
        _verify(refs_b, expected_b, "ページ入れ替え 元2ページ目")

        work = get_work(bpy.context)
        work.active_page_index = 0
        assert bpy.ops.bmanga.page_duplicate("EXEC_DEFAULT") == {"FINISHED"}
        bpy.context.view_layer.update()
        _verify(refs_a, expected_a, "ページ複製後 元1ページ目")
        _verify(refs_b, expected_b, "ページ複製後 元2ページ目")

        work = get_work(bpy.context)
        work.active_page_index = len(work.pages) - 1
        assert bpy.ops.bmanga.page_remove("EXEC_DEFAULT") == {"FINISHED"}
        bpy.context.view_layer.update()
        _verify(refs_a, expected_a, "ページ削除後 元1ページ目")
        _verify(refs_b, expected_b, "ページ削除後 元2ページ目")

        print("BMANGA_PAGE_OPERATION_LAYER_STABILITY_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
