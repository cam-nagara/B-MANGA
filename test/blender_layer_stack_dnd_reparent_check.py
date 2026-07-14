"""Blender 実機用: レイヤーリスト D&D 親変更のデータ移送確認."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

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


def _stack(context):
    from bmanga_dev.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    assert stack is not None
    layer_stack_utils.remember_layer_stack_signature(context)
    return stack


def _move_uid_below_parent(context, uid: str, parent_uid: str) -> None:
    from bmanga_dev.utils import layer_stack as layer_stack_utils

    stack = _stack(context)
    from_index = next(i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == uid)
    parent_index = next(i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == parent_uid)
    target_index = parent_index + 1
    if from_index < target_index:
        target_index -= 1
    stack.move(from_index, max(0, min(len(stack) - 1, target_index)))
    layer_stack_utils.apply_stack_order_if_ui_changed(context, moved_uid=uid)
    layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)


def _assert_visible_object(obj, label: str) -> None:
    if obj is None:
        raise AssertionError(f"{label}: object missing")
    if getattr(obj, "hide_viewport", False):
        raise AssertionError(f"{label}: object hidden")
    if not list(getattr(obj, "users_collection", []) or []):
        raise AssertionError(f"{label}: object is not linked")


def _add_balloon(page, bid: str, parent_key: str):
    entry = page.balloons.add()
    entry.id = bid
    entry.shape = "rect"
    entry.x_mm = 10.0
    entry.y_mm = 20.0
    entry.width_mm = 30.0
    entry.height_mm = 18.0
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def _add_text(page, tid: str, parent_key: str, parent_balloon_id: str = ""):
    entry = page.texts.add()
    entry.id = tid
    entry.body = tid
    entry.x_mm = 14.0
    entry.y_mm = 24.0
    entry.width_mm = 20.0
    entry.height_mm = 10.0
    entry.parent_balloon_id = parent_balloon_id
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def _add_image(context, image_id: str, parent_key: str):
    entry = context.scene.bmanga_image_layers.add()
    entry.id = image_id
    entry.title = image_id
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def _add_raster(context, raster_id: str, parent_key: str):
    entry = context.scene.bmanga_raster_layers.add()
    entry.id = raster_id
    entry.title = raster_id
    entry.scope = "page"
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key
    return entry


def _add_gp_layer(context, name: str, parent_key: str):
    from bmanga_dev.utils import gp_object_layer, gpencil, layer_object_model

    obj = gp_object_layer.create_layer_gp_object(
        scene=context.scene,
        bmanga_id=layer_object_model.make_stable_id("gp"),
        title=name,
        z_index=210,
        parent_kind="coma" if ":" in parent_key else "page",
        parent_key=parent_key,
    )
    assert obj is not None
    layer = layer_object_model.content_layer(obj)
    assert layer is not None
    if hasattr(layer, "tint_color"):
        tint_values = (0.13, 0.24, 0.35, 0.46)
        layer.tint_color = tint_values[: len(tuple(layer.tint_color))]
    frame = gpencil.ensure_active_frame(layer)
    assert frame is not None and getattr(frame, "drawing", None) is not None
    assert gpencil.add_stroke_to_drawing(
        frame.drawing,
        [(0.012, 0.034, 0.0), (0.024, 0.046, 0.0)],
        curve_type="BEZIER",
        bezier_smooth=True,
    )
    stroke = frame.drawing.strokes[0]
    stroke.aspect_ratio = 1.35
    stroke.fill_opacity = 0.27
    stroke.softness = 0.18
    first = stroke.points[0]
    first.rotation = 0.31
    first.vertex_color = (0.2, 0.3, 0.4, 0.5)
    first.handle_left.position = (0.009, 0.031, 0.0)
    first.handle_left.select = False
    first.handle_right.position = (0.016, 0.037, 0.0)
    first.handle_right.select = True
    return obj


def _first_gp_point_position(obj) -> tuple[float, float, float]:
    from bmanga_dev.utils import gp_layer_parenting, layer_object_model

    layer = layer_object_model.content_layer(obj)
    assert layer is not None
    point = next(iter(gp_layer_parenting.iter_points(layer)), None)
    assert point is not None
    return tuple(float(value) for value in point.position)


def _assert_local_gp_point(obj) -> None:
    actual = _first_gp_point_position(obj)
    expected = (0.012, 0.034, 0.0)
    assert all(abs(value - wanted) <= 1.0e-6 for value, wanted in zip(actual, expected, strict=True))


def _assert_gp_mask(obj) -> None:
    layers = getattr(getattr(obj, "data", None), "layers", None)
    assert layers is not None, f"手描きレイヤーがありません: {obj.name}"
    mask_layer = layers.get("__bmanga_mask")
    assert mask_layer is not None, f"コマの手描きマスクがありません: {obj.name}"
    assert bool(getattr(mask_layer, "hide", False)), f"手描きマスクが表示されています: {obj.name}"
    content_layers = [layer for layer in layers if getattr(layer, "name", "") != "__bmanga_mask"]
    assert content_layers, f"マスク対象の手描きがありません: {obj.name}"
    for layer in content_layers:
        assert bool(getattr(layer, "use_masks", False)), f"手描きマスクが無効です: {obj.name}"
        mask_names = [
            str(getattr(item, "name", "") or "")
            for item in getattr(layer, "mask_layers", []) or []
        ]
        assert "__bmanga_mask" in mask_names, f"手描きマスク参照がありません: {obj.name}"


def _gp_page_relative_signature(context, obj, parent_key: str) -> tuple:
    from mathutils import Matrix, Vector

    from bmanga_dev.utils import layer_object_model, page_grid
    from bmanga_dev.utils.geom import mm_to_m

    work = context.scene.bmanga_work
    page_id = parent_key.split(":", 1)[0]
    page_index = next(i for i, page in enumerate(work.pages) if page.id == page_id)
    ox_mm, oy_mm = page_grid.page_total_offset_mm(work, context.scene, page_index)
    relative = Matrix.Translation((-mm_to_m(ox_mm), -mm_to_m(oy_mm), 0.0)) @ obj.matrix_world
    relative.translation.z = 0.0  # Zは移動先のレイヤー順で再採番される。
    layer = layer_object_model.content_layer(obj)
    stroke = layer.frames[0].drawing.strokes[0]
    point = stroke.points[0]

    def transformed(value) -> tuple[float, float, float]:
        result = relative @ Vector(value)
        return tuple(round(float(component), 6) for component in result)

    return (
        tuple(round(float(value), 6) for row in relative for value in row),
        transformed(point.position),
        transformed(point.handle_left.position),
        transformed(point.handle_right.position),
        round(float(point.rotation), 6),
        tuple(round(float(value), 6) for value in point.vertex_color),
        int(point.handle_left.type),
        bool(point.handle_left.select),
        int(point.handle_right.type),
        bool(point.handle_right.select),
        round(float(stroke.aspect_ratio), 6),
        round(float(stroke.fill_opacity), 6),
        round(float(stroke.softness), 6),
        tuple(round(float(value), 6) for value in getattr(layer, "tint_color", ())),
    )


def _add_effect_layer(context, parent_key: str):
    from bmanga_dev.operators import effect_line_op

    obj, _layer = effect_line_op._create_effect_layer(
        context,
        (10.0, 10.0, 20.0, 20.0),
        parent_key=parent_key,
    )
    return obj


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_layer_stack_dnd_reparent_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "LayerStackDnd.bmanga"))
        assert "FINISHED" in result, result
        assert "FINISHED" in bpy.ops.bmanga.page_add("EXEC_DEFAULT")
        # 移動先を先に一度作成し、後のオープンが「新規作成直後の自動保存」に
        # ならないようにする。これで保存前の復元保持を検証できる。
        assert bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=1) == {"FINISHED"}
        # v0.6.279 以降、レイヤーリストの内容行はページ編集シーンにのみ
        # 並ぶため、ページを開いてから D&D 親変更を検証する
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert result == {"FINISHED"}, result

        from bmanga_dev.utils import layer_stack as layer_stack_utils
        from bmanga_dev.utils.layer_hierarchy import (
            COMA_KIND,
            OUTSIDE_KIND,
            OUTSIDE_STACK_KEY,
            coma_stack_key,
            outside_child_key,
            page_stack_key,
        )

        context = bpy.context
        work = context.scene.bmanga_work
        page1 = work.pages[0]
        page2 = work.pages[1]
        page1_key = page_stack_key(page1)
        page2_key = page_stack_key(page2)
        page1_coma_key = coma_stack_key(page1, page1.comas[0])
        page2_uid = layer_stack_utils.target_uid("page", page2_key)
        outside_uid = layer_stack_utils.target_uid(OUTSIDE_KIND, OUTSIDE_STACK_KEY)
        # 自ページ以外の詳細 (コマ等) は未読込
        assert len(page2.comas) == 0, "ページ用 blend が他ページの詳細を保持しています"

        text = _add_text(page1, "dnd_cross_text", page1_key)
        text_uid = layer_stack_utils.target_uid("text", f"{page1_key}:{text.id}")
        _move_uid_below_parent(context, text_uid, page2_uid)
        assert len(page1.texts) == 0
        # 別ページへの移送で移送先の詳細がその場で読み込まれる (保存消失防止)
        assert bool(page2.detail_loaded), "移送先ページの詳細が読み込まれていません"
        assert len(page2.comas) >= 1, "移送先ページのコマが読み込まれていません"
        moved_text = next(t for t in page2.texts if t.id == "dnd_cross_text")
        assert moved_text.parent_kind == "page" and moved_text.parent_key == page2_key
        page2_coma_key = coma_stack_key(page2, page2.comas[0])
        page2_coma_uid = layer_stack_utils.target_uid(COMA_KIND, page2_coma_key)

        # 別画面相当の同時追記は、移動トランザクション完了後に最新JSONへ合流する。
        from bmanga_dev.utils import cross_page_transfer, json_io
        from bmanga_dev.io.project_content_migration_lock import work_lock

        parallel_entry = _add_text(page1, "dnd_parallel_primary", page1_key)
        work_dir_path = Path(work.work_dir)
        target_meta = work_dir_path / page2_key / "page.json"
        original_read_target = cross_page_transfer._read_target_page_json
        worker_errors: list[Exception] = []
        worker_threads: list[threading.Thread] = []

        def parallel_append() -> None:
            try:
                with work_lock(work_dir_path, blocking=True):
                    latest = json_io.read_json(target_meta)
                    latest.setdefault("texts", []).append(
                        {
                            "id": "dnd_parallel_secondary",
                            "body": "parallel",
                            "parent_kind": "page",
                            "parent_key": page2_key,
                        }
                    )
                    json_io.write_json(target_meta, latest)
            except Exception as exc:  # noqa: BLE001
                worker_errors.append(exc)

        def read_and_start_worker(work_dir, target_page_id):
            data = original_read_target(work_dir, target_page_id)
            worker = threading.Thread(target=parallel_append, daemon=True)
            worker_threads.append(worker)
            worker.start()
            return data

        cross_page_transfer._read_target_page_json = read_and_start_worker
        try:
            moved = cross_page_transfer.transfer_layers_to_page(
                context,
                work,
                page1,
                page2_key,
                [SimpleNamespace(kind="text", key=f"{page1_key}:{parallel_entry.id}")],
            )
        finally:
            cross_page_transfer._read_target_page_json = original_read_target
        assert moved == 1
        for worker in worker_threads:
            worker.join(timeout=10.0)
            assert not worker.is_alive(), "別画面相当の追記が作品ロックで停止したままです"
        assert not worker_errors, worker_errors
        merged_target = json_io.read_json(target_meta)
        merged_ids = {str(item.get("id", "") or "") for item in merged_target.get("texts", [])}
        assert {"dnd_parallel_primary", "dnd_parallel_secondary"} <= merged_ids

        # 同一IDの別画像が移動先にあっても上書きせず、新IDのPNGへ複製する。
        from bmanga_dev.utils import paths
        from bmanga_dev.io.project_content_migration_lock import guard_path_write
        from bmanga_dev.io.project_content_save_baseline import record_successful_write

        collision_id = "abcdef123456"
        existing_png = paths.raster_png_path(work_dir_path, collision_id)
        existing_png.parent.mkdir(parents=True, exist_ok=True)
        with guard_path_write(existing_png):
            existing_png.write_bytes(b"target-raster-content")
            record_successful_write(existing_png)
        legacy_source = work_dir_path / page1_key / "legacy" / "source.png"
        legacy_source.parent.mkdir(parents=True, exist_ok=True)
        with guard_path_write(legacy_source):
            legacy_source.write_bytes(b"source-raster-content")
            record_successful_write(legacy_source)
        raster_dict = {
            "id": collision_id,
            "title": "collision",
            "image_name": f"raster_{collision_id}",
            "filepath_rel": "legacy/source.png",
        }
        raster_target = {
            "rasterLayers": [
                {
                    "id": collision_id,
                    "filepath_rel": f"raster/{collision_id}.png",
                }
            ]
        }
        copied_png = cross_page_transfer._copy_raster_image(
            work_dir_path,
            page1_key,
            raster_dict,
            raster_target,
        )
        assert copied_png is not None and copied_png.read_bytes() == b"source-raster-content"
        assert raster_dict["id"] != collision_id
        assert raster_dict["filepath_rel"] == f"raster/{raster_dict['id']}.png"
        assert existing_png.read_bytes() == b"target-raster-content"
        cross_page_transfer._cleanup_new_rasters([copied_png])
        assert not copied_png.exists(), "転送中止PNGが基準追跡後に削除されていません"

        # target/source/Object削除の各失敗で、JSON・stage・元実体を全て戻す。
        from bmanga_dev.io import page_io
        from bmanga_dev.utils import cross_page_gp_transfer, cross_page_stage, layer_object_model

        failure_text = _add_text(page1, "dnd_transaction_failure_text", page1_key)
        failure_text_id = str(failure_text.id)
        failure_gp = _add_gp_layer(context, "transaction failure gp", page1_key)
        failure_gp_id = layer_object_model.stable_id(failure_gp)
        failure_effect = _add_effect_layer(context, page1_key)
        failure_effect_id = layer_object_model.stable_id(failure_effect)
        failure_items = [
            SimpleNamespace(kind="text", key=f"{page1_key}:{failure_text_id}"),
            SimpleNamespace(kind="effect", key=failure_effect_id),
            SimpleNamespace(kind="gp", key=failure_gp_id),
        ]
        failure_stage = work_dir_path / page2_key / "_staged_imports.json"
        failure_target_before = json_io.read_json(target_meta)
        failure_stage_before = failure_stage.read_bytes() if failure_stage.exists() else None
        raster_files_before = {
            path.name for path in (work_dir_path / "raster").glob("*.png")
        }

        def assert_transaction_rolled_back(label: str) -> None:
            assert any(text.id == failure_text_id for text in page1.texts), label
            current_gp = layer_object_model.find_layer_object("gp", failure_gp_id)
            assert current_gp is not None and layer_object_model.parent_key(current_gp) == page1_key, label
            assert sum(
                layer_object_model.stable_id(obj) == failure_gp_id
                for obj in layer_object_model.iter_layer_objects("gp")
            ) == 1, label
            current_effect = layer_object_model.find_layer_object("effect", failure_effect_id)
            assert current_effect is not None and layer_object_model.parent_key(current_effect) == page1_key, label
            assert sum(
                layer_object_model.stable_id(obj) == failure_effect_id
                for obj in layer_object_model.iter_layer_objects("effect")
            ) == 1, label
            assert json_io.read_json(target_meta) == failure_target_before, label
            current_stage = failure_stage.read_bytes() if failure_stage.exists() else None
            assert current_stage == failure_stage_before, label
            assert {
                path.name for path in (work_dir_path / "raster").glob("*.png")
            } == raster_files_before, label

        original_stage_gp = cross_page_stage.stage_gp
        cross_page_stage.stage_gp = lambda _work_dir, _page_id, _entry: False
        try:
            assert cross_page_transfer.transfer_layers_to_page(
                context, work, page1, page2_key, failure_items
            ) == 0
        finally:
            cross_page_stage.stage_gp = original_stage_gp
        assert_transaction_rolled_back("stage準備失敗で移動元実体が重複しました")

        original_write_target = cross_page_transfer._write_target_page_json
        target_write_calls = 0

        def fail_target_once(work_dir, target_page_id, data):
            nonlocal target_write_calls
            target_write_calls += 1
            if target_write_calls == 1:
                return False
            return original_write_target(work_dir, target_page_id, data)

        cross_page_transfer._write_target_page_json = fail_target_once
        try:
            assert cross_page_transfer.transfer_layers_to_page(
                context, work, page1, page2_key, failure_items
            ) == 0
        finally:
            cross_page_transfer._write_target_page_json = original_write_target
        assert_transaction_rolled_back("target書込失敗で転送状態が残りました")

        original_save_source = page_io.save_page_json
        source_save_calls = 0

        def fail_source_once(work_dir, page):
            nonlocal source_save_calls
            source_save_calls += 1
            if source_save_calls == 1:
                raise OSError("simulated source write failure")
            return original_save_source(work_dir, page)

        page_io.save_page_json = fail_source_once
        try:
            assert cross_page_transfer.transfer_layers_to_page(
                context, work, page1, page2_key, failure_items
            ) == 0
        finally:
            page_io.save_page_json = original_save_source
        assert_transaction_rolled_back("source書込失敗で転送状態が残りました")

        original_remove_gp = cross_page_gp_transfer.remove_object
        cross_page_gp_transfer.remove_object = lambda _bmanga_id: False
        try:
            assert cross_page_transfer.transfer_layers_to_page(
                context, work, page1, page2_key, failure_items
            ) == 0
        finally:
            cross_page_gp_transfer.remove_object = original_remove_gp
        assert_transaction_rolled_back("移動元Object削除失敗が成功扱いになりました")

        original_remove_effect = cross_page_transfer._remove_effect_objects
        cross_page_transfer._remove_effect_objects = lambda _bmanga_id: False
        try:
            assert cross_page_transfer.transfer_layers_to_page(
                context, work, page1, page2_key, failure_items
            ) == 0
        finally:
            cross_page_transfer._remove_effect_objects = original_remove_effect
        assert_transaction_rolled_back("移動元効果線削除失敗が成功扱いになりました")

        balloon = _add_balloon(page1, "dnd_cross_balloon", page1_key)
        child = _add_text(page1, "dnd_cross_child", page1_key, parent_balloon_id=balloon.id)
        balloon_id = str(balloon.id)
        child_id = str(child.id)
        balloon_uid = layer_stack_utils.target_uid("balloon", f"{page1_key}:{balloon_id}")
        _move_uid_below_parent(context, balloon_uid, page2_coma_uid)
        assert len(page1.balloons) == 0
        assert len(page2.balloons) == 1
        assert not any(getattr(t, "id", "") == child_id for t in page1.texts)
        moved_balloon = page2.balloons[0]
        moved_child = next(t for t in page2.texts if t.id == child_id)
        assert moved_balloon.parent_kind == "coma" and moved_balloon.parent_key == page2_coma_key
        assert moved_child.parent_balloon_id == moved_balloon.id
        assert moved_child.parent_kind == "coma" and moved_child.parent_key == page2_coma_key

        # 子テキストだけを別ページへ移した場合、元の親IDと同じ無関係な
        # フキダシが移動先にあっても、そこへ誤接続せず単独テキストにする。
        collision_balloon_id = "dnd_cross_unrelated_balloon"
        _add_balloon(page2, collision_balloon_id, page2_key)
        page_io.save_page_json(work_dir_path, page2)
        source_parent = _add_balloon(page1, collision_balloon_id, page1_key)
        detached_child = _add_text(
            page1,
            "dnd_cross_detached_child",
            page1_key,
            parent_balloon_id=source_parent.id,
        )
        detached_child_id = str(detached_child.id)
        assert cross_page_transfer.transfer_layers_to_page(
            context,
            work,
            page1,
            page2_key,
            [SimpleNamespace(kind="text", key=f"{page1_key}:{detached_child_id}")],
        ) == 1
        detached_target = json_io.read_json(target_meta)
        detached_target_text = next(
            entry
            for entry in detached_target.get("texts", [])
            if entry.get("id") == detached_child_id
        )
        assert detached_target_text.get("parentBalloonId", "") == ""
        assert any(balloon.id == collision_balloon_id for balloon in page1.balloons)
        assert not any(text.id == detached_child_id for text in page1.texts)

        moved_text_id = str(moved_text.id)
        moved_text_uid = layer_stack_utils.target_uid("text", f"{page2_key}:{moved_text_id}")
        _move_uid_below_parent(context, moved_text_uid, outside_uid)
        assert not any(getattr(t, "id", "") == moved_text_id for t in page2.texts)
        assert any(getattr(t, "id", "") == moved_text_id for t in work.shared_texts)
        from bmanga_dev.operators import object_tool_selection
        from bmanga_dev.utils import object_naming as on
        from bmanga_dev.utils import object_selection
        from bmanga_dev.utils import text_real_object

        shared_text = next(t for t in work.shared_texts if t.id == moved_text_id)
        shared_text_bmanga_id = text_real_object.text_object_bmanga_id_for_values(
            text_real_object.OUTSIDE_PAGE_ID,
            moved_text_id,
        )
        shared_text_obj = on.find_object_by_bmanga_id(shared_text_bmanga_id, kind="text")
        _assert_visible_object(shared_text_obj, "shared text after layer-stack D&D")
        shared_key = object_selection.text_key(None, shared_text)
        shared_rect = object_tool_selection.selection_bounds_for_key(context, shared_key)
        assert shared_rect is not None
        assert abs(shared_rect.x - float(shared_text.x_mm)) <= 1.0e-4
        hit = object_tool_selection.hit_shared_text_at_world(
            context,
            float(shared_text.x_mm) + 1.0,
            float(shared_text.y_mm) + 1.0,
        )
        assert hit is not None and hit["key"] == shared_key
        object_selection.set_keys(context, [shared_key])
        object_tool_selection.sync_outliner_selection_for_keys(context, [shared_key])
        assert shared_text_obj.select_get()
        shared_text_uid = layer_stack_utils.target_uid("text", outside_child_key(moved_text_id))
        _move_uid_below_parent(context, shared_text_uid, page2_uid)
        assert not any(getattr(t, "id", "") == moved_text_id for t in work.shared_texts)
        restored_text = next(t for t in page2.texts if t.id == moved_text_id)
        assert restored_text.parent_kind == "page" and restored_text.parent_key == page2_key

        from bmanga_dev.utils import layer_object_model

        gp_obj = _add_gp_layer(context, "dnd_cross_gp", page1_key)
        from bmanga_dev.utils import page_grid

        gp_obj.location.x += 0.017
        gp_obj.location.y -= 0.009
        gp_obj.rotation_euler = (0.12, -0.08, 0.37)
        gp_obj.scale = (1.2, 0.8, 1.1)
        gp_obj[page_grid.SUBPAGE_OFFSET_X_PROP] = 17.0
        gp_obj[page_grid.SUBPAGE_OFFSET_Y_PROP] = -9.0
        context.view_layer.update()
        gp_expected_signature = _gp_page_relative_signature(context, gp_obj, page1_key)
        gp_id = layer_object_model.stable_id(gp_obj)
        gp_uid = layer_stack_utils.target_uid("gp", gp_id)
        _move_uid_below_parent(context, gp_uid, page2_coma_uid)
        gp_obj = layer_object_model.find_layer_object("gp", gp_id)
        assert gp_obj is None, "別ページへ移した手描きが移動元に残っています"
        staged_path = Path(work.work_dir) / page2_key / "_staged_imports.json"
        staged = json.loads(staged_path.read_text(encoding="utf-8"))
        staged_gp = next(
            entry for entry in staged.get("gp_layers", [])
            if entry.get("source_bmanga_id") == gp_id
        )
        moved_gp_id = str(staged_gp.get("bmanga_id", "") or "")
        assert moved_gp_id and moved_gp_id != gp_id
        staged_pos = staged_gp["layers"][0]["frames"][0]["strokes"][0]["points"][0]["pos"]
        assert all(
            abs(float(value) - expected) <= 1.0e-7
            for value, expected in zip(staged_pos, (0.012, 0.034, 0.0), strict=True)
        ), "ページ移動で手描き点が変換されています"

        # 元ページのフォルダーIDは移動先では意味を持たない。Alt+D&D相当の
        # 個別移動で、手描き／効果線とも移動先の直下へ揃える。
        source_folder_id = "folder_source_page_only"
        folder_gp = _add_gp_layer(context, "dnd_cross_folder_gp", page1_key)
        folder_effect = _add_effect_layer(context, page1_key)
        folder_gp_id = layer_object_model.stable_id(folder_gp)
        folder_effect_id = layer_object_model.stable_id(folder_effect)
        assert layer_object_model.set_folder_id(folder_gp, source_folder_id)
        assert layer_object_model.set_folder_id(folder_effect, source_folder_id)
        assert cross_page_transfer.transfer_layers_to_page(
            context,
            work,
            page1,
            page2_key,
            [
                SimpleNamespace(kind="gp", key=folder_gp_id),
                SimpleNamespace(kind="effect", key=folder_effect_id),
            ],
            target_parent_kind="coma",
            target_coma_id=page2.comas[0].id,
        ) == 2
        staged = json.loads(staged_path.read_text(encoding="utf-8"))
        staged_folder_gp = next(
            entry for entry in staged.get("gp_layers", [])
            if entry.get("source_bmanga_id") == folder_gp_id
        )
        staged_folder_effect = next(
            entry for entry in staged.get("effects", [])
            if entry.get("source_bmanga_id") == folder_effect_id
        )
        moved_folder_gp_id = str(staged_folder_gp.get("bmanga_id", "") or "")
        moved_folder_effect_id = str(staged_folder_effect.get("bmanga_id", "") or "")
        assert moved_folder_gp_id and moved_folder_gp_id != folder_gp_id
        assert moved_folder_effect_id and moved_folder_effect_id != folder_effect_id
        assert staged_folder_gp.get("folder_id", "") == ""
        assert staged_folder_effect.get("folder_id", "") == ""

        effect_obj = _add_effect_layer(context, page1_key)
        effect_id = layer_object_model.stable_id(effect_obj)
        effect_uid = layer_stack_utils.target_uid("effect", effect_id)
        _move_uid_below_parent(context, effect_uid, outside_uid)
        effect_obj = layer_object_model.find_layer_object("effect", effect_id)
        assert effect_obj is not None
        assert layer_object_model.parent_key(effect_obj) == ""

        image = _add_image(context, "dnd_cross_image", page1_key)
        image_uid = layer_stack_utils.target_uid("image", image.id)
        _move_uid_below_parent(context, image_uid, page2_coma_uid)
        assert image.parent_kind == "coma" and image.parent_key == page2_coma_key

        raster = _add_raster(context, "dnd_cross_raster", page1_key)
        raster_uid = layer_stack_utils.target_uid("raster", raster.id)
        _move_uid_below_parent(context, raster_uid, page2_uid)
        assert raster.scope == "page" and raster.parent_kind == "page" and raster.parent_key == page2_key
        _move_uid_below_parent(context, raster_uid, outside_uid)
        assert raster.scope == "master" and raster.parent_kind == "none" and raster.parent_key == ""

        coma_balloon = _add_balloon(page1, "dnd_coma_child_balloon", page1_coma_key)
        coma_child = _add_text(
            page1,
            "dnd_coma_child_text",
            page1_coma_key,
            parent_balloon_id=coma_balloon.id,
        )
        coma_text = _add_text(page1, "dnd_coma_direct_text", page1_coma_key)
        coma_image = _add_image(context, "dnd_coma_child_image", page1_coma_key)
        coma_raster = _add_raster(context, "dnd_coma_child_raster", page1_coma_key)
        coma_gp = _add_gp_layer(context, "dnd_coma_child_gp", page1_coma_key)
        coma_effect = _add_effect_layer(context, page1_coma_key)
        coma_gp_id = layer_object_model.stable_id(coma_gp)
        coma_effect_id = layer_object_model.stable_id(coma_effect)
        coma_balloon_id = str(coma_balloon.id)
        coma_child_id = str(coma_child.id)
        coma_text_id = str(coma_text.id)

        page1_coma_uid = layer_stack_utils.target_uid(COMA_KIND, page1_coma_key)
        before_page2_comas = len(page2.comas)
        before_page2_coma_keys = {coma_stack_key(page2, panel) for panel in page2.comas}
        _move_uid_below_parent(context, page1_coma_uid, page2_uid)
        assert len(page1.comas) == 0
        assert len(page2.comas) == before_page2_comas + 1
        moved_coma_key = next(
            coma_stack_key(page2, panel)
            for panel in page2.comas
            if coma_stack_key(page2, panel) not in before_page2_coma_keys
        )
        moved_coma_balloon = next(b for b in page2.balloons if b.id == coma_balloon_id)
        moved_coma_child = next(t for t in page2.texts if t.id == coma_child_id)
        moved_coma_text = next(t for t in page2.texts if t.id == coma_text_id)
        assert not any(getattr(b, "id", "") == coma_balloon_id for b in page1.balloons)
        assert not any(getattr(t, "id", "") == coma_child_id for t in page1.texts)
        assert moved_coma_balloon.parent_kind == "coma" and moved_coma_balloon.parent_key == moved_coma_key
        assert moved_coma_child.parent_balloon_id == moved_coma_balloon.id
        assert moved_coma_child.parent_kind == "coma" and moved_coma_child.parent_key == moved_coma_key
        assert moved_coma_text.parent_kind == "coma" and moved_coma_text.parent_key == moved_coma_key
        assert coma_image.parent_kind == "coma" and coma_image.parent_key == moved_coma_key
        assert coma_raster.scope == "page" and coma_raster.parent_kind == "coma"
        assert coma_raster.parent_key == moved_coma_key
        coma_gp = layer_object_model.find_layer_object("gp", coma_gp_id)
        coma_effect = layer_object_model.find_layer_object("effect", coma_effect_id)
        assert coma_gp is None, "移動済みコマの手描きが移動元に残っています"
        assert coma_effect is None, "移動済みコマの効果線が移動元に残っています"

        staged = json.loads(staged_path.read_text(encoding="utf-8"))
        staged_gp_ids = {entry.get("bmanga_id") for entry in staged.get("gp_layers", [])}
        staged_effect_ids = {entry.get("bmanga_id") for entry in staged.get("effects", [])}
        moved_coma_gp_id = str(next(
            entry.get("bmanga_id", "")
            for entry in staged.get("gp_layers", [])
            if entry.get("source_bmanga_id") == coma_gp_id
        ))
        moved_coma_effect_id = str(next(
            entry.get("bmanga_id", "")
            for entry in staged.get("effects", [])
            if entry.get("source_bmanga_id") == coma_effect_id
        ))
        assert moved_coma_gp_id and moved_coma_gp_id != coma_gp_id
        assert moved_coma_effect_id and moved_coma_effect_id != coma_effect_id
        assert {moved_gp_id, moved_coma_gp_id} <= staged_gp_ids
        assert moved_coma_effect_id in staged_effect_ids

        # 移動先ページを実際に開くと、退避データが同じ安定ID・親で復元される。
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=1)
        assert result == {"FINISHED"}, result
        context = bpy.context
        work = context.scene.bmanga_work
        restored_gp = layer_object_model.find_layer_object("gp", moved_gp_id)
        restored_coma_gp = layer_object_model.find_layer_object("gp", moved_coma_gp_id)
        restored_coma_effect = layer_object_model.find_layer_object("effect", moved_coma_effect_id)
        restored_folder_gp = layer_object_model.find_layer_object("gp", moved_folder_gp_id)
        restored_folder_effect = layer_object_model.find_layer_object("effect", moved_folder_effect_id)
        assert restored_gp is not None
        assert layer_object_model.parent_key(restored_gp) == page2_coma_key
        _assert_local_gp_point(restored_gp)
        _assert_gp_mask(restored_gp)
        restored_signature = _gp_page_relative_signature(context, restored_gp, page2_coma_key)
        assert restored_signature == gp_expected_signature, (gp_expected_signature, restored_signature)
        assert restored_coma_gp is not None
        assert layer_object_model.parent_key(restored_coma_gp) == moved_coma_key
        _assert_local_gp_point(restored_coma_gp)
        _assert_gp_mask(restored_coma_gp)
        assert restored_coma_effect is not None
        assert layer_object_model.parent_key(restored_coma_effect) == moved_coma_key
        assert restored_folder_gp is not None and restored_folder_effect is not None
        assert layer_object_model.parent_key(restored_folder_gp) == page2_coma_key
        assert layer_object_model.parent_key(restored_folder_effect) == page2_coma_key
        assert layer_object_model.folder_id(restored_folder_gp) == ""
        assert layer_object_model.folder_id(restored_folder_effect) == ""
        _assert_gp_mask(restored_folder_gp)
        assert layer_object_model.validate_single_content_layer(restored_gp)[0]
        assert layer_object_model.validate_single_content_layer(restored_coma_gp)[0]
        assert staged_path.exists(), "ページ保存前に退避ファイルが消えています"

        # 同じページ上で復元処理が二重に呼ばれても、同じ安定IDは一つだけ。
        from bmanga_dev.utils import cross_page_transfer
        from bmanga_dev.utils import cross_page_stage

        # 確定待ちの読込後に別画面が追記・同一ID更新しても、最新内容を消さない。
        probe_page_id = "p0999"
        probe_original = {"bmanga_id": "effect_probe", "parent_key": probe_page_id, "value": 1}
        probe_replacement = {"bmanga_id": "effect_probe", "parent_key": probe_page_id, "value": 2}
        probe_append = {"bmanga_id": "effect_append", "parent_key": probe_page_id, "value": 3}
        assert cross_page_stage.stage_effect(Path(work.work_dir), probe_page_id, probe_original)
        processed_token = cross_page_stage._entry_token("effect", probe_original)
        assert cross_page_stage.stage_effect(Path(work.work_dir), probe_page_id, probe_replacement)
        assert cross_page_stage.stage_effect(Path(work.work_dir), probe_page_id, probe_append)
        cross_page_stage._remove_processed_entries(
            Path(work.work_dir),
            probe_page_id,
            {"effect": {processed_token}},
        )
        probe_path = cross_page_stage.staged_path(Path(work.work_dir), probe_page_id)
        probe_latest = json.loads(probe_path.read_text(encoding="utf-8"))
        probe_values = {
            entry["bmanga_id"]: entry["value"]
            for entry in probe_latest.get("effects", [])
        }
        assert probe_values == {"effect_probe": 2, "effect_append": 3}
        with guard_path_write(probe_path):
            probe_path.unlink()
            record_successful_write(probe_path)

        before_ids = {
            (layer_object_model.layer_kind(obj), layer_object_model.stable_id(obj))
            for obj in layer_object_model.iter_layer_objects()
            if layer_object_model.layer_kind(obj) in {"gp", "effect"}
        }
        assert cross_page_transfer.process_staged_imports(context, page_id=page2_key) == 0
        after_ids = {
            (layer_object_model.layer_kind(obj), layer_object_model.stable_id(obj))
            for obj in layer_object_model.iter_layer_objects()
            if layer_object_model.layer_kind(obj) in {"gp", "effect"}
        }
        assert after_ids == before_ids, "復元処理の再呼出しでレイヤーが重複しました"
        assert staged_path.exists(), "ページ保存前の再呼出しで退避ファイルが消えています"

        # 保存せずにページを再読込すると未保存実体は消えるが、退避から一度だけ再試行できる。
        page2_blend = Path(work.work_dir) / page2_key / "page.blend"
        bpy.ops.wm.open_mainfile(filepath=str(page2_blend), load_ui=False)
        context = bpy.context
        work = context.scene.bmanga_work
        restored_gp = layer_object_model.find_layer_object("gp", moved_gp_id)
        restored_coma_gp = layer_object_model.find_layer_object("gp", moved_coma_gp_id)
        restored_coma_effect = layer_object_model.find_layer_object("effect", moved_coma_effect_id)
        assert restored_gp is not None and restored_coma_gp is not None and restored_coma_effect is not None
        assert staged_path.exists(), "保存せず再読込したとき退避ファイルが失われました"

        # 復元後〜保存確定の間に同一IDの内容が差し替わった場合、新しい版は確定対象外。
        before_commit_stage = json.loads(staged_path.read_text(encoding="utf-8"))
        original_gp_stage = next(
            entry
            for entry in before_commit_stage.get("gp_layers", [])
            if entry.get("bmanga_id") == moved_gp_id
        )
        replacement_gp_stage = copy.deepcopy(original_gp_stage)
        replacement_gp_stage["concurrent_revision"] = "newer"
        replacement_gp_token = cross_page_stage._entry_token("gp", replacement_gp_stage)
        assert cross_page_stage.stage_gp(
            Path(work.work_dir),
            page2_key,
            replacement_gp_stage,
        )
        original_effect_stage = next(
            entry
            for entry in before_commit_stage.get("effects", [])
            if entry.get("bmanga_id") == moved_coma_effect_id
        )
        replacement_effect_stage = copy.deepcopy(original_effect_stage)
        replacement_effect_stage["concurrent_revision"] = "newer"
        replacement_effect_token = cross_page_stage._entry_token(
            "effect",
            replacement_effect_stage,
        )
        assert cross_page_stage.stage_effect(
            Path(work.work_dir),
            page2_key,
            replacement_effect_stage,
        )

        # ネイティブ保存成功後にだけ確定し、次回ロードでも同じ安定IDが一つずつ残る。
        bpy.ops.wm.save_as_mainfile(filepath=str(page2_blend), check_existing=False, compress=True)
        assert cross_page_transfer.commit_staged_imports_after_save(
            context,
            blend_path=page2_blend,
            metadata_saved=False,
        ) == 0
        assert staged_path.exists(), "ページ情報の保存失敗時に退避が消えています"
        cross_page_transfer.commit_staged_imports_after_save(
            context,
            blend_path=page2_blend,
            metadata_saved=True,
        )
        committed_stage = json.loads(staged_path.read_text(encoding="utf-8"))
        remaining_gp = committed_stage.get("gp_layers", [])
        assert len(remaining_gp) == 1 and remaining_gp[0].get("concurrent_revision") == "newer"
        remaining_effects = committed_stage.get("effects", [])
        assert len(remaining_effects) == 1 and remaining_effects[0].get("concurrent_revision") == "newer"
        assert cross_page_transfer.process_staged_imports(context, page_id=page2_key) == 2
        replaced_gp = layer_object_model.find_layer_object("gp", moved_gp_id)
        replaced_effect = layer_object_model.find_layer_object("effect", moved_coma_effect_id)
        assert replaced_gp is not None and replaced_effect is not None
        assert replaced_gp.get(cross_page_stage.STAGE_OBJECT_PROP) == replacement_gp_token
        assert replaced_effect.get(cross_page_stage.STAGE_OBJECT_PROP) == replacement_effect_token
        assert staged_path.exists(), "同一IDの新しい版が保存前に消えています"
        bpy.ops.wm.save_as_mainfile(filepath=str(page2_blend), check_existing=False, compress=True)
        replacement_commit_count = cross_page_transfer.commit_staged_imports_after_save(
            context,
            blend_path=page2_blend,
            metadata_saved=True,
        )
        assert replacement_commit_count == 2 or not staged_path.exists()
        assert not staged_path.exists(), "同一IDの新しい版を再処理・保存後も退避が残っています"
        bpy.ops.wm.open_mainfile(filepath=str(page2_blend), load_ui=False)
        assert layer_object_model.find_layer_object("gp", moved_gp_id) is not None
        assert layer_object_model.find_layer_object("gp", moved_coma_gp_id) is not None
        assert layer_object_model.find_layer_object("effect", moved_coma_effect_id) is not None
        for kind, stable_id in (
            ("gp", moved_gp_id),
            ("gp", moved_coma_gp_id),
            ("effect", moved_coma_effect_id),
        ):
            matches = [
                obj for obj in layer_object_model.iter_layer_objects(kind)
                if layer_object_model.stable_id(obj) == stable_id
            ]
            assert len(matches) == 1, f"{kind}:{stable_id} が保存後に重複しています"

        print("BMANGA_LAYER_STACK_DND_REPARENT_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
