"""Blender実機用: レイヤー移動ドラッグの二相処理と性能ゲート."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_layer_move_transaction"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("B-MANGA addon spec could not be created")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    module.register()
    return module


def _mover(layer_move_op, resolved):
    mover = SimpleNamespace(
        _target=resolved,
        _snapshots=[],
        _last_applied_total=(0.0, 0.0),
        _effect_meta_origin=None,
    )
    mover._capture_snapshot = lambda context, kind, value: (
        layer_move_op.BMANGA_OT_layer_move_tool._capture_snapshot(
            mover,
            context,
            kind,
            value,
        )
    )
    mover._apply_delta = lambda context, dx_mm, dy_mm: (
        layer_move_op.BMANGA_OT_layer_move_tool._apply_delta(
            mover,
            context,
            dx_mm,
            dy_mm,
        )
    )
    mover._restore_snapshots = lambda context: (
        layer_move_op.BMANGA_OT_layer_move_tool._restore_snapshots(
            mover,
            context,
        )
    )
    mover._can_apply_total = lambda context, dx_mm, dy_mm: (
        layer_move_op.BMANGA_OT_layer_move_tool._can_apply_total(
            mover,
            context,
            dx_mm,
            dy_mm,
        )
    )
    return mover


def _select_coma(context, page, coma):
    from bmanga_dev_layer_move_transaction.utils import layer_stack
    from bmanga_dev_layer_move_transaction.utils.layer_hierarchy import coma_stack_key

    stack = layer_stack.sync_layer_stack(context, preserve_active_index=True)
    expected_key = coma_stack_key(page, coma)
    for index, item in enumerate(stack):
        if str(getattr(item, "kind", "") or "") != "coma":
            continue
        if str(getattr(item, "key", "") or "") != expected_key:
            continue
        assert layer_stack.select_stack_index(context, index)
        resolved = layer_stack.resolve_stack_item(context, item)
        assert resolved is not None
        return resolved
    raise AssertionError("coma stack item not found")


def _add_children(context, page, parent_key: str):
    balloon = page.balloons.add()
    balloon.id = "drag_balloon"
    balloon.parent_kind = "coma"
    balloon.parent_key = parent_key
    balloon.x_mm = 15.0
    balloon.y_mm = 25.0
    balloon.width_mm = 20.0
    balloon.height_mm = 15.0
    text = page.texts.add()
    text.id = "drag_text"
    text.parent_kind = "coma"
    text.parent_key = parent_key
    text.parent_balloon_id = balloon.id
    text.x_mm = 16.0
    text.y_mm = 26.0
    text.width_mm = 10.0
    text.height_mm = 8.0
    image = context.scene.bmanga_image_layers.add()
    image.id = "drag_image"
    image.parent_kind = "coma"
    image.parent_key = parent_key
    image.x_mm = 18.0
    image.y_mm = 28.0
    image_path = context.scene.bmanga_image_path_layers.add()
    image_path.id = "drag_image_path"
    image_path.parent_kind = "coma"
    image_path.parent_key = parent_key
    image_path.path_points_json = "[[20.0,30.0],[25.0,35.0]]"
    fill = context.scene.bmanga_fill_layers.add()
    fill.id = "drag_fill"
    fill.parent_kind = "coma"
    fill.parent_key = parent_key
    fill.use_region = True
    fill.region_x_mm = 10.0
    fill.region_y_mm = 20.0
    fill.gradient_start_x_mm = 11.0
    fill.gradient_start_y_mm = 21.0
    fill.gradient_end_x_mm = 30.0
    fill.gradient_end_y_mm = 40.0
    return balloon, text, image, image_path, fill


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_layer_move_transaction_"))
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        result = bpy.ops.bmanga.work_new(
            filepath=str(temp_root / "LayerMoveTransaction.bmanga")
        )
        assert "FINISHED" in result, result
        assert bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0) == {"FINISHED"}

        from bmanga_dev_layer_move_transaction.operators import (
            coma_modal_state,
            layer_drag_transaction,
            layer_move_op,
            object_tool_op,
        )
        from bmanga_dev_layer_move_transaction.utils import (
            layer_object_sync,
            layer_stack,
            object_selection,
            page_grid,
        )
        from bmanga_dev_layer_move_transaction.utils import fill_real_object
        from bmanga_dev_layer_move_transaction.ui import overlay
        from bmanga_dev_layer_move_transaction.utils.layer_hierarchy import (
            coma_stack_key,
        )

        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]
        coma = page.comas[0]
        parent_key = coma_stack_key(page, coma)
        balloon, text, image, image_path, fill = _add_children(
            context,
            page,
            parent_key,
        )
        resolved = _select_coma(context, page, coma)
        mover = _mover(layer_move_op, resolved)

        counters = {"stack": 0, "page": 0}
        original_stack = layer_stack.apply_stack_order
        original_page = page_grid.apply_page_collection_transforms

        def counted_stack(*args, **kwargs):
            counters["stack"] += 1
            return original_stack(*args, **kwargs)

        def counted_page(*args, **kwargs):
            counters["page"] += 1
            return original_page(*args, **kwargs)

        layer_stack.apply_stack_order = counted_stack
        page_grid.apply_page_collection_transforms = counted_page
        try:
            transaction = layer_drag_transaction.DragTransaction(
                context,
                mover,
                "coma",
                resolved,
            )
            coma_x = float(coma.rect_x_mm)
            coma_y = float(coma.rect_y_mm)
            balloon_xy = (float(balloon.x_mm), float(balloon.y_mm))
            text_xy = (float(text.x_mm), float(text.y_mm))
            image_xy = (float(image.x_mm), float(image.y_mm))
            image_path_json = str(image_path.path_points_json)
            fill_xy = (float(fill.region_x_mm), float(fill.region_y_mm))

            timings = []
            for index in range(1, 121):
                started = time.perf_counter()
                assert transaction.update_overlay(
                    context,
                    index * 0.1,
                    index * 0.05,
                )
                timings.append((time.perf_counter() - started) * 1000.0)
                assert float(coma.rect_x_mm) == coma_x
                assert float(coma.rect_y_mm) == coma_y
                assert (float(balloon.x_mm), float(balloon.y_mm)) == balloon_xy
                assert (float(text.x_mm), float(text.y_mm)) == text_xy
                assert (float(image.x_mm), float(image.y_mm)) == image_xy
                assert str(image_path.path_points_json) == image_path_json
                assert (float(fill.region_x_mm), float(fill.region_y_mm)) == fill_xy
                assert counters == {"stack": 0, "page": 0}

            p95_ms = sorted(timings)[int(len(timings) * 0.95) - 1]
            assert p95_ms <= 16.0, f"drag P95 exceeded 16ms: {p95_ms:.3f}ms"
            mover._last_applied_total = transaction.total
            assert transaction.commit(context)
            layer_move_op.BMANGA_OT_layer_move_tool._finalize_committed_drag(
                mover,
                context,
            )
            assert counters["stack"] == 1
            assert abs(float(coma.rect_x_mm) - (coma_x + 12.0)) < 1.0e-5
            assert abs(float(coma.rect_y_mm) - (coma_y + 6.0)) < 1.0e-5
            assert abs(float(balloon.x_mm) - (balloon_xy[0] + 12.0)) < 1.0e-5
            assert abs(float(text.x_mm) - (text_xy[0] + 12.0)) < 1.0e-5
            assert abs(float(image.x_mm) - (image_xy[0] + 12.0)) < 1.0e-5
            assert "[32.0,36.0]" in str(image_path.path_points_json)
            assert abs(float(fill.region_x_mm) - (fill_xy[0] + 12.0)) < 1.0e-5
            assert abs(float(fill.gradient_end_y_mm) - 46.0) < 1.0e-5
            assert not layer_object_sync.is_sync_in_progress()

            # 確定処理の途中で例外が起きても、データと同期抑止を開始前へ戻す。
            resolved = _select_coma(context, page, coma)
            failure_mover = _mover(layer_move_op, resolved)
            failure_origin = (float(coma.rect_x_mm), float(coma.rect_y_mm))

            def fail_layer_commit(_context, dx_mm, dy_mm):
                coma.rect_x_mm = failure_origin[0] + float(dx_mm)
                coma.rect_y_mm = failure_origin[1] + float(dy_mm)
                raise RuntimeError("forced layer commit failure")

            failure_mover._apply_delta = fail_layer_commit
            failure_transaction = layer_drag_transaction.DragTransaction(
                context,
                failure_mover,
                "coma",
                resolved,
            )
            assert failure_transaction.update_overlay(context, 4.0, 3.0)
            try:
                failure_transaction.commit(context)
                raise AssertionError("layer commit failure was not propagated")
            except RuntimeError as exc:
                assert "forced layer commit failure" in str(exc)
            assert (float(coma.rect_x_mm), float(coma.rect_y_mm)) == failure_origin
            assert not layer_object_sync.is_sync_in_progress()

            resolved = _select_coma(context, page, coma)
            cancel_mover = _mover(layer_move_op, resolved)
            cancel_transaction = layer_drag_transaction.DragTransaction(
                context,
                cancel_mover,
                "coma",
                resolved,
            )
            committed_xy = (float(coma.rect_x_mm), float(coma.rect_y_mm))
            assert cancel_transaction.update_overlay(context, 50.0, 40.0)
            cancel_transaction.cancel()
            assert (float(coma.rect_x_mm), float(coma.rect_y_mm)) == committed_xy
            assert not layer_object_sync.is_sync_in_progress()

            # オブジェクトツール経由も同じ二相処理を使い、Property更新は
            # 120イベント中0回、確定時1回だけであることを確認する。
            fill_obj = fill_real_object.ensure_fill_real_object(
                scene=context.scene,
                entry=fill,
                page=page,
            )
            assert fill_obj is not None
            object_apply_calls = 0
            object_origin = (
                float(fill.region_x_mm),
                float(fill.region_y_mm),
            )

            def apply_object_snapshots(_context, dx_mm, dy_mm):
                nonlocal object_apply_calls
                object_apply_calls += 1
                fill.region_x_mm = object_origin[0] + float(dx_mm)
                fill.region_y_mm = object_origin[1] + float(dy_mm)

            object_owner = SimpleNamespace(
                _apply_snapshots=apply_object_snapshots,
            )
            object_transaction = layer_drag_transaction.ObjectMoveTransaction(
                context,
                object_owner,
                [object_selection.fill_key(fill)],
            )
            fill_key = object_selection.fill_key(fill)

            class _ObjectToolState:
                pass

            object_tool_state = _ObjectToolState()
            object_tool_state._dragging = True
            object_tool_state._drag_action = "move"
            object_tool_state._drag_keys = [fill_key]
            object_tool_state._object_move_drag = object_transaction
            coma_modal_state.set_active("object_tool", object_tool_state, context)
            object_timings = []
            try:
                for index in range(1, 121):
                    started = time.perf_counter()
                    assert object_transaction.update_overlay(
                        context,
                        index * 0.08,
                        index * -0.04,
                    )
                    object_timings.append((time.perf_counter() - started) * 1000.0)
                    assert object_apply_calls == 0
                    assert (
                        float(fill.region_x_mm),
                        float(fill.region_y_mm),
                    ) == object_origin
                    assert object_tool_op.object_move_overlay_offset_for_key(fill_key) == (
                        index * 0.08,
                        index * -0.04,
                    )
                assert object_tool_op.object_move_overlay_offset_for_key(
                    fill_key,
                    source_follows_object=True,
                ) == (
                    object_transaction.total
                    if object_transaction.composite_drag
                    else (0.0, 0.0)
                )
                base_gradient = fill_real_object.gradient_handle_positions_mm(
                    context,
                    fill.id,
                )
                assert base_gradient is not None
                captured_segments = []
                original_draw_segments = overlay._draw_segments_mm
                overlay._draw_segments_mm = (
                    lambda segments, color, width_mm: captured_segments.extend(segments)
                )
                try:
                    overlay._draw_gradient_lines(context, {fill_key})
                finally:
                    overlay._draw_segments_mm = original_draw_segments
                assert captured_segments == [(
                    (
                        base_gradient[0] + object_transaction.total[0],
                        base_gradient[1] + object_transaction.total[1],
                    ),
                    (
                        base_gradient[2] + object_transaction.total[0],
                        base_gradient[3] + object_transaction.total[1],
                    ),
                )]
            finally:
                coma_modal_state.clear_active("object_tool", object_tool_state, context)
            composite_state = _ObjectToolState()
            composite_state._dragging = True
            composite_state._drag_action = "move"
            composite_state._drag_keys = [fill_key]
            composite_state._object_move_drag = SimpleNamespace(
                total=(4.0, -3.0),
                composite_drag=True,
            )
            coma_modal_state.set_active("object_tool", composite_state, context)
            try:
                assert object_tool_op.object_move_overlay_offset_for_key(
                    fill_key,
                    source_follows_object=True,
                ) == (4.0, -3.0)
            finally:
                coma_modal_state.clear_active("object_tool", composite_state, context)
            object_p95_ms = sorted(object_timings)[
                int(len(object_timings) * 0.95) - 1
            ]
            assert object_p95_ms <= 16.0, (
                f"object drag P95 exceeded 16ms: {object_p95_ms:.3f}ms"
            )
            assert object_transaction.finish(context)
            assert object_apply_calls == 1
            assert abs(float(fill.region_x_mm) - (object_origin[0] + 9.6)) < 1.0e-5
            assert abs(float(fill.region_y_mm) - (object_origin[1] - 4.8)) < 1.0e-5
            assert not layer_object_sync.is_sync_in_progress()

            failed_object_origin = (
                float(fill.region_x_mm),
                float(fill.region_y_mm),
            )

            def fail_object_snapshots(_context, dx_mm, dy_mm):
                fill.region_x_mm = failed_object_origin[0] + float(dx_mm)
                fill.region_y_mm = failed_object_origin[1] + float(dy_mm)
                if dx_mm or dy_mm:
                    raise RuntimeError("forced object commit failure")

            failed_object_owner = SimpleNamespace(
                _apply_snapshots=fail_object_snapshots,
            )
            failed_object_transaction = layer_drag_transaction.ObjectMoveTransaction(
                context,
                failed_object_owner,
                [object_selection.fill_key(fill)],
            )
            assert failed_object_transaction.update_overlay(context, 2.0, -1.0)
            try:
                failed_object_transaction.finish(context)
                raise AssertionError("object commit failure was not propagated")
            except RuntimeError as exc:
                assert "forced object commit failure" in str(exc)
            assert (
                float(fill.region_x_mm),
                float(fill.region_y_mm),
            ) == failed_object_origin
            assert not layer_object_sync.is_sync_in_progress()
        finally:
            layer_stack.apply_stack_order = original_stack
            page_grid.apply_page_collection_transforms = original_page

        print(
            "BMANGA_LAYER_MOVE_DRAG_TRANSACTION_OK "
            f"p95_ms={p95_ms:.3f} object_p95_ms={object_p95_ms:.3f} "
            f"stack_calls={counters['stack']}"
        )
    finally:
        if module is not None:
            try:
                module.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
