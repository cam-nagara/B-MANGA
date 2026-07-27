"""Blender 5.2実機: 2D合成、前後分割、LRU、非保存Imageの回帰テスト."""

from __future__ import annotations

import importlib.util
import math
from dataclasses import replace
from pathlib import Path
import shutil
import sys
import tempfile
import time

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_preview_composite"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("B-MANGA addon spec could not be created")
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    module.register()
    return module


def _expected_image(export_pipeline, export_group_masks, work, page, options):
    layers = export_pipeline.build_page_layers(work, page, options)
    masks = export_pipeline._coma_group_masks(work, page, options)
    layers = export_group_masks.apply_group_masks_to_layers(
        layers,
        masks,
        export_pipeline.Image,
        export_pipeline.ImageChops,
    )
    size = export_pipeline._page_canvas_size_px(work, page, options)
    return export_pipeline._flatten_layers(layers, size).convert("RGBA")


def _assert_same(ImageChops, actual, expected, label: str) -> None:
    if actual.size != expected.size:
        raise AssertionError(f"{label}: size mismatch {actual.size} != {expected.size}")
    difference = ImageChops.difference(actual, expected)
    if any(maximum > 0 for _minimum, maximum in difference.getextrema()):
        extrema = difference.getextrema()
        raise AssertionError(f"{label}: pixel mismatch extrema={extrema}")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_preview_composite_"))
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        result = bpy.ops.bmanga.work_new(
            filepath=str(temp_root / "PreviewComposite.bmanga")
        )
        assert "FINISHED" in result, result
        assert bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0) == {"FINISHED"}

        from bmanga_dev_preview_composite.io import (
            export_group_masks,
            export_pipeline,
        )
        from bmanga_dev_preview_composite.operators import raster_layer_op
        from bmanga_dev_preview_composite.utils import (
            fill_real_object,
            layer_stack,
            preview_composite,
        )
        from bmanga_dev_preview_composite.ui import overlay_page_preview

        context = bpy.context
        scene = context.scene
        work = scene.bmanga_work
        page = work.pages[0]
        work.paper.canvas_width_mm = 50.0
        work.paper.canvas_height_mm = 70.0
        work.paper.dpi = 144
        scene.bmanga_page_preview_resolution_percentage = 100.0
        for coma in page.comas:
            coma.visible = False

        fill = scene.bmanga_fill_layers.add()
        fill.id = "preview_gradient"
        fill.title = "前後分割用グラデーション"
        fill.fill_type = "gradient"
        fill.gradient_type = "linear"
        fill.gradient_angle = math.pi * 0.5
        fill.color = (1.0, 0.0, 0.0, 1.0)
        fill.color2 = (0.0, 0.0, 1.0, 0.75)
        fill.opacity = 70.0
        fill.parent_kind = "page"
        fill.parent_key = str(page.id)
        fill_obj = fill_real_object.ensure_fill_real_object(
            scene=scene,
            entry=fill,
            page=page,
        )
        assert fill_obj is not None
        assert bpy.ops.bmanga.raster_layer_add(
            dpi_preset="custom",
            dpi=72,
            bit_depth="gray8",
            enter_paint=False,
        ) == {"FINISHED"}
        raster = scene.bmanga_raster_layers[-1]
        raster.parent_kind = "page"
        raster.parent_key = str(page.id)
        raster_image = raster_layer_op.ensure_raster_image(
            context,
            raster,
            create_missing=False,
        )
        assert raster_image is not None
        pixel_count = int(raster_image.size[0]) * int(raster_image.size[1])
        raster_image.pixels.foreach_set([1.0, 0.0, 0.0, 0.5] * pixel_count)
        raster_image.update()
        stack = layer_stack.sync_layer_stack(context, preserve_active_index=False)
        fill_uid = layer_stack.target_uid("fill", fill.id)
        fill_index = next(
            index
            for index, item in enumerate(stack)
            if layer_stack.stack_item_uid(item) == fill_uid
        )
        assert layer_stack.select_stack_index(context, fill_index)

        scene.bmanga_composite_preview_enabled = True
        service = preview_composite.get_service()
        service.mark_dirty(context=context)
        frame = service.render_now(context, quality="high", force=True)
        assert frame is not None
        assert frame.full_image is not None
        assert not str(getattr(frame.full_image, "filepath", "") or "")
        assert frame.full_image.source == "GENERATED"

        options = service._options(frame.dpi)
        expected = _expected_image(
            export_pipeline,
            export_group_masks,
            work,
            page,
            options,
        )
        _assert_same(
            export_pipeline.ImageChops,
            frame.full_pil,
            expected,
            "2D合成とPNG書き出し経路",
        )
        disk_options = replace(
            service._options(frame.dpi),
            prefer_memory_raster=False,
        )
        raster_uid = layer_stack.target_uid("raster", raster.id)
        memory_raster_layer = next(
            layer for layer in frame.layers if layer.stack_uid == raster_uid
        )
        disk_raster_layer = next(
            layer
            for layer in export_pipeline.build_page_layers(
                work,
                page,
                disk_options,
            )
            if layer.stack_uid == raster_uid
        )
        assert export_pipeline.ImageChops.difference(
            memory_raster_layer.image,
            disk_raster_layer.image,
        ).getbbox() is not None, "未保存ラスター画素が2D合成へ反映されていません"
        revision_before_raster_dirty = service._revision.get(page.id, 0)
        raster_layer_op.mark_raster_dirty(raster)
        assert service._revision.get(page.id, 0) > revision_before_raster_dirty
        frame = service.render_now(context, quality="high", force=True)
        assert frame is not None
        draw_calls = []
        original_draw = overlay_page_preview._draw_textured_image
        overlay_page_preview._draw_textured_image = (
            lambda *args, **kwargs: draw_calls.append((args, kwargs))
        )
        try:
            assert overlay_page_preview.draw_composite_for_page(
                context,
                work,
                page,
                0,
                0.0,
                0.0,
            )
            assert len(draw_calls) == 1
            assert draw_calls[0][1]["depth_test"] == "NONE"
        finally:
            overlay_page_preview._draw_textured_image = original_draw

        render_count = service.cache_stats()["renders"]
        assert service.render_now(context, quality="high", force=True) is frame
        stats = service.cache_stats()
        assert stats["renders"] == render_count
        assert stats["hits"] >= 1
        assert stats["bytes"] <= service.max_cache_bytes

        service.mark_dirty(context=context)
        assert service.run_low_timer() is None
        low_frame = service.frame_for_page(page.id)
        assert low_frame is not None
        assert low_frame.dpi == 72
        service._last_dirty_at = time.monotonic()
        remaining = service.run_high_timer()
        assert remaining is not None and 0.0 < remaining <= 0.150
        service._last_dirty_at -= 0.200
        assert service.run_high_timer() is None
        frame = service.frame_for_page(page.id)
        assert frame is not None and frame.dpi == 144

        stack = layer_stack.sync_layer_stack(context, preserve_active_index=True)
        assert any(layer_stack.stack_item_uid(item) == fill_uid for item in stack)
        z_probe = bpy.data.objects.new("CompositeZRangeProbe", None)
        scene.collection.objects.link(z_probe)
        z_probe.location.z = float(fill_obj.matrix_world.translation.z) + 0.2
        assert service.begin_drag(
            context,
            anchor_uid=fill_uid,
            exclude_uids={fill_uid},
            objects={fill_obj, z_probe},
        )
        assert frame.mode == "split"
        assert frame.back_pil is not None and frame.front_pil is not None
        assert frame.active_z_min < frame.active_z_max
        split = frame.back_pil.copy()
        for active_layer in frame.active_layers:
            export_pipeline._composite_layer(split, active_layer)
        split = export_pipeline.Image.alpha_composite(split, frame.front_pil)
        _assert_same(
            export_pipeline.ImageChops,
            split,
            frame.full_pil,
            "背面・操作対象・前面の再合成",
        )
        fill_xy = (float(fill.region_x_mm), float(fill.region_y_mm))
        fill_matrix = fill_obj.matrix_world.copy()
        drag_timings = []
        for index in range(1, 121):
            started = time.perf_counter()
            service.update_drag(
                context,
                dx_mm=index * 0.1,
                dy_mm=index * -0.05,
            )
            drag_timings.append((time.perf_counter() - started) * 1000.0)
            assert (float(fill.region_x_mm), float(fill.region_y_mm)) == fill_xy
            assert fill_obj.matrix_world == fill_matrix
        drag_p95_ms = sorted(drag_timings)[int(len(drag_timings) * 0.95) - 1]
        assert drag_p95_ms <= 16.0, drag_p95_ms
        draw_calls.clear()
        overlay_page_preview._draw_textured_image = (
            lambda *args, **kwargs: draw_calls.append((args, kwargs))
        )
        try:
            assert overlay_page_preview.draw_composite_for_page(
                context,
                work,
                page,
                0,
                0.0,
                0.0,
            )
            assert len(draw_calls) == 3
            assert all(
                kwargs["depth_test"] == "LESS_EQUAL"
                for _args, kwargs in draw_calls
            )
            assert draw_calls[0][1]["z_m"] < draw_calls[1][1]["z_m"]
            assert draw_calls[1][1]["z_m"] < draw_calls[2][1]["z_m"]
            assert draw_calls[0][1]["z_m"] < frame.active_z_min
            assert draw_calls[2][1]["z_m"] > frame.active_z_max
            assert abs(float(draw_calls[1][0][1]) - 12.0) < 1.0e-6
            assert abs(float(draw_calls[1][0][2]) + 6.0) < 1.0e-6
        finally:
            overlay_page_preview._draw_textured_image = original_draw
        assert fill_obj.hide_get()
        service.end_drag(context, committed=False)
        assert frame.mode == "full"
        assert fill_obj.hide_get()
        bpy.data.objects.remove(z_probe, do_unlink=True)

        pattern = export_pipeline.Image.new("RGBA", (2, 2))
        pattern.putdata(
            [
                (255, 0, 0, 255),
                (0, 255, 0, 255),
                (0, 0, 255, 255),
                (255, 255, 255, 255),
            ]
        )
        uploaded = service._upload(page.id, 1, "orientation_test", pattern)
        values = list(uploaded.pixels[:])
        first_rgba = tuple(round(values[index], 4) for index in range(4))
        assert first_rgba == (0.0, 0.0, 1.0, 1.0), first_rgba
        bpy.data.images.remove(uploaded)
        middle = export_pipeline.Image.new("RGBA", (1, 1), (128, 128, 128, 255))
        uploaded = service._upload(page.id, 1, "color_space_test", middle)
        values = list(uploaded.pixels[:])
        expected_linear = 0.2159
        assert all(
            abs(float(values[index]) - expected_linear) < 1.0e-3
            for index in range(3)
        ), tuple(values)
        assert abs(float(values[3]) - 1.0) < 1.0e-6
        bpy.data.images.remove(uploaded)

        original_budget = service.max_cache_bytes
        service.max_cache_bytes = 2 * 1024 * 1024
        service.mark_dirty(context=context)
        frame = service.render_now(context, quality="high", force=True)
        assert frame is not None and frame.dpi < 144
        assert service.cache_stats()["bytes"] <= service.max_cache_bytes
        service.max_cache_bytes = original_budget

        service.before_save()
        assert not any(
            image.name.startswith(preview_composite.IMAGE_PREFIX)
            for image in bpy.data.images
        )
        assert not fill_obj.hide_get()
        preview_composite._on_save_post_fail()
        assert frame.full_image is not None
        assert fill_obj.hide_get()
        save_post_fail = getattr(bpy.app.handlers, "save_post_fail", None)
        if save_post_fail is not None:
            assert any(
                getattr(handler, "__name__", "") == "_on_save_post_fail"
                for handler in save_post_fail
            )

        scene.bmanga_composite_preview_enabled = False
        assert not fill_obj.hide_get()
        print(
            "BMANGA_PREVIEW_COMPOSITE_OK",
            f"size={frame.size[0]}x{frame.size[1]}",
            f"dpi={frame.dpi}",
            f"cache_bytes={stats['bytes']}",
            f"drag_p95_ms={drag_p95_ms:.3f}",
            flush=True,
        )
    finally:
        if module is not None:
            try:
                module.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
