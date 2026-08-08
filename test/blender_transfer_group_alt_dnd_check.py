"""Blender 5.2実機: Alt+D&DのTransferGroupと全子レイヤー移送."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _assert_exclusive_work_lock(work_dir: Path) -> None:
    from bmanga_transfer_group_test.io import project_file_lock

    result: list[str] = []

    def contend() -> None:
        try:
            with project_file_lock.work_lock(work_dir, blocking=False):
                result.append("acquired")
        except project_file_lock.WorkLockError:
            result.append("blocked")

    thread = threading.Thread(target=contend)
    thread.start()
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert result == ["blocked"], result


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_transfer_group_test",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_transfer_group_test"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _stack_item(context, kind: str, marker: str):
    from bmanga_transfer_group_test.utils import layer_stack

    stack = layer_stack.sync_layer_stack(context, preserve_active_index=True)
    for index, item in enumerate(stack):
        if str(getattr(item, "kind", "") or "") != kind:
            continue
        if marker in str(getattr(item, "key", "") or ""):
            return index, item
    raise AssertionError(f"stack item missing: {kind}/{marker}")


def _add_children(context, page, parent_key: str, image_file: Path):
    from bmanga_transfer_group_test.utils import (
        balloon_curve_object,
        fill_real_object,
        image_real_object,
        image_path_object,
        layer_object_sync,
    )

    work = context.scene.bmanga_work
    folder = work.layer_folders.add()
    folder.id = "folder_transfer_nested"
    folder.title = "入れ子"
    folder.parent_key = parent_key
    from bmanga_transfer_group_test.utils import layer_stack

    layer_stack.sync_layer_stack_after_data_change(context)
    layer_object_sync.mirror_work_to_outliner(
        context.scene,
        work,
        allow_object_writeback=False,
    )

    balloon = page.balloons.add()
    balloon.id = "balloon_transfer"
    balloon.title = "移送フキダシ"
    balloon.x_mm = 20.0
    balloon.y_mm = 30.0
    balloon.width_mm = 40.0
    balloon.height_mm = 24.0
    balloon.parent_kind = "coma"
    balloon.parent_key = parent_key
    balloon.folder_key = folder.id
    balloon_curve_object.ensure_balloon_curve_object(
        scene=context.scene,
        entry=balloon,
        page=page,
        folder_id=folder.id,
    )

    text = page.texts.add()
    text.id = "text_transfer"
    text.title = "移送テキスト"
    text.body = "リンク"
    text.x_mm = 27.0
    text.y_mm = 35.0
    text.width_mm = 24.0
    text.height_mm = 12.0
    text.parent_kind = "coma"
    text.parent_key = parent_key
    text.parent_balloon_id = balloon.id
    text.folder_key = folder.id

    fill = context.scene.bmanga_fill_layers.add()
    fill.id = "fill_transfer"
    fill.title = "移送グラデーション"
    fill.fill_type = "gradient"
    fill.use_region = True
    fill.region_x_mm = 10.0
    fill.region_y_mm = 12.0
    fill.region_width_mm = 70.0
    fill.region_height_mm = 50.0
    fill.use_gradient_endpoints = True
    fill.gradient_start_x_mm = 10.0
    fill.gradient_start_y_mm = 12.0
    fill.gradient_end_x_mm = 80.0
    fill.gradient_end_y_mm = 62.0
    fill.parent_kind = "coma"
    fill.parent_key = parent_key
    fill.folder_key = folder.id
    fill_real_object.ensure_fill_real_object(
        scene=context.scene,
        entry=fill,
        page=page,
        folder_id=folder.id,
    )

    image = context.scene.bmanga_image_layers.add()
    image.id = "image_transfer"
    image.title = "移送画像"
    image.filepath = str(image_file)
    image.x_mm = 18.0
    image.y_mm = 22.0
    image.width_mm = 26.0
    image.height_mm = 18.0
    image.parent_kind = "coma"
    image.parent_key = parent_key
    image.folder_key = folder.id
    image_real_object.ensure_image_real_object(
        scene=context.scene,
        entry=image,
        page=page,
        folder_id=folder.id,
    )

    assert "FINISHED" in bpy.ops.bmanga.raster_layer_add(
        "EXEC_DEFAULT",
        dpi_preset="150",
        enter_paint=False,
    )
    raster = context.scene.bmanga_raster_layers[-1]
    raster.title = "移送ラスター"
    raster.parent_kind = "coma"
    raster.parent_key = parent_key
    raster.folder_key = folder.id

    path = context.scene.bmanga_image_path_layers.add()
    path.id = "path_transfer"
    path.title = "移送パターンカーブ"
    path.content_source = "shape"
    path.path_points_json = json.dumps([[15.0, 15.0], [35.0, 28.0], [60.0, 18.0]])
    path.parent_kind = "coma"
    path.parent_key = parent_key
    path.folder_key = folder.id
    image_path_object.ensure_image_path_object(
        scene=context.scene,
        entry=path,
        page=page,
        folder_id=folder.id,
    )
    # ヘッドレス初期化直後はOutlinerのCollection反映が遅れる場合があるため、
    # テスト正本の所属を表示実体生成後に再確定する。
    for entry in (balloon, text, fill, image, path, raster):
        entry.parent_kind = "coma"
        entry.parent_key = parent_key
        entry.folder_key = folder.id
    return folder, balloon, text, fill, image, path, raster


def _force_uid_order(context, ordered_uids) -> None:
    from bmanga_transfer_group_test.utils import layer_stack

    stack = layer_stack.sync_layer_stack(context, preserve_active_index=True)
    current = [layer_stack.stack_item_uid(item) for item in stack]
    wanted = [uid for uid in ordered_uids if uid in current]
    slots = [index for index, uid in enumerate(current) if uid in set(wanted)]
    assert len(slots) == len(wanted)
    for slot, uid in zip(slots, wanted, strict=True):
        current = [layer_stack.stack_item_uid(item) for item in stack]
        index = current.index(uid)
        if index != slot:
            stack.move(index, slot)
    layer_stack.remember_layer_stack_signature(context)


def _moved_label_order(context):
    from bmanga_transfer_group_test.utils import layer_stack

    labels = {
        ("balloon", "移送フキダシ"): "balloon",
        ("text", "移送テキスト"): "text",
        ("fill", "移送グラデーション"): "fill",
        ("image", "移送画像"): "image",
        ("image_path", "移送パターンカーブ"): "image_path",
        ("raster", "移送ラスター"): "raster",
    }
    result = []
    for item in layer_stack.sync_layer_stack(context, preserve_active_index=True):
        resolved = layer_stack.resolve_stack_item(context, item)
        target = resolved.get("target") if resolved is not None else None
        key = (
            str(getattr(item, "kind", "") or ""),
            str(getattr(target, "title", "") or ""),
        )
        label = labels.get(key)
        if label:
            result.append(label)
    return result


def main() -> None:
    module = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        module = _load_addon()
        temp_root = Path(tempfile.mkdtemp(prefix="bmanga_transfer_group_"))
        assert "FINISHED" in bpy.ops.bmanga.work_new(
            filepath=str(temp_root / "TransferGroup.bmanga")
        )
        assert "FINISHED" in bpy.ops.bmanga.page_add("EXEC_DEFAULT")
        assert "FINISHED" in bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=1)
        assert "FINISHED" in bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        image_file = temp_root / "transfer_source.png"
        fixture_image = bpy.data.images.new(
            "BManga_Transfer_Fixture",
            width=2,
            height=2,
            alpha=True,
        )
        fixture_image.pixels = (
            1.0, 0.0, 0.0, 1.0,
            0.0, 1.0, 0.0, 1.0,
            0.0, 0.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0,
        )
        fixture_image.file_format = "PNG"
        fixture_image.filepath_raw = str(image_file)
        fixture_image.save()
        bpy.data.images.remove(fixture_image)
        assert image_file.is_file()

        from bmanga_transfer_group_test.utils import (
            layer_stack,
            layer_transfer_group,
            page_grid,
            paths,
        )
        from bmanga_transfer_group_test.io import save_baseline
        from bmanga_transfer_group_test.utils.layer_hierarchy import coma_stack_key
        from bmanga_transfer_group_test.utils.layer_reparent import ClickTarget

        context = bpy.context
        work = context.scene.bmanga_work
        source = work.pages[0]
        target = work.pages[1]
        panel = source.comas[0]
        parent_key = coma_stack_key(source, panel)
        folder, balloon, text, fill, image, path, raster = _add_children(
            context,
            source,
            parent_key,
            image_file,
        )
        layer_stack.sync_layer_stack_after_data_change(context)
        from bmanga_transfer_group_test.utils import image_path_object, layer_links

        with image_path_object.suspend_auto_sync():
            path.parent_kind = "coma"
            path.parent_key = parent_key
            path.folder_key = folder.id

        # semantic partnerとして後から加わるフキダシのリンク先も、固定点まで
        # 閉包する。T→B、B↔fill の時にfillを置き去りにしない。
        _balloon_index, balloon_item = _stack_item(
            context,
            "balloon",
            balloon.id,
        )
        _fill_index, fill_item = _stack_item(context, "fill", fill.id)
        link_group, linked_count = layer_links.link_uids(
            context,
            (
                layer_stack.stack_item_uid(balloon_item),
                layer_stack.stack_item_uid(fill_item),
            ),
        )
        assert link_group and linked_count == 2

        # テキスト片側だけの選択でも、フキダシとのsemantic関係と、その
        # フキダシから辿れるリンク閉包を含む。
        text_index, _text_item = _stack_item(context, "text", text.id)
        layer_stack.select_stack_index(context, text_index)
        group = layer_transfer_group.build_transfer_group(context)
        assert group is not None
        kinds = {str(getattr(item, "kind", "") or "") for item in group.items}
        assert {"balloon", "text", "fill"} <= kinds, kinds

        target_index = 1
        ox, oy = page_grid.page_total_offset_mm(work, context.scene, target_index)
        drop = (ox + 115.0, oy + 145.0)
        click_target = ClickTarget("page", target, None, target_index, drop, (115.0, 145.0))

        # 最新ラスターPNGの作成に失敗した場合は、古いPNGへfallbackせず
        # sourceを一切変更しない。
        raster_index, _raster_item = _stack_item(context, "raster", raster.id)
        layer_stack.clear_all_selection(context)
        layer_stack.select_stack_index(context, raster_index)
        from bmanga_transfer_group_test.operators import raster_layer_op

        original_save_raster_png = raster_layer_op.save_raster_png

        def fail_raster_save(*_args, **_kwargs):
            raise OSError("phase5 injected raster save failure")

        raster_layer_op.save_raster_png = fail_raster_save
        try:
            assert layer_transfer_group.transfer_group_to_page(
                context,
                click_target,
                drop_world_xy_mm=drop,
            ) == 0
        finally:
            raster_layer_op.save_raster_png = original_save_raster_png
        assert any(
            item.id == raster.id
            for item in context.scene.bmanga_raster_layers
        )

        # ソースblend保存を強制失敗させ、ページデータ・リンク・退避ステージが
        # すべて元へ戻ることを確認する。
        page_json_path = paths.page_meta_path(Path(work.work_dir), source.id)
        page_json_before = page_json_path.read_bytes()
        original_save_page_blend = layer_transfer_group.blend_io.save_page_blend
        original_transfer_rollback = layer_transfer_group._rollback

        def assert_locked_transfer_rollback(*args, **kwargs):
            from bmanga_transfer_group_test.io import project_file_lock

            assert project_file_lock.owns_work_lock(Path(work.work_dir))
            _assert_exclusive_work_lock(Path(work.work_dir))
            return original_transfer_rollback(*args, **kwargs)

        layer_transfer_group.blend_io.save_page_blend = lambda *_args, **_kwargs: False
        layer_transfer_group._rollback = assert_locked_transfer_rollback
        try:
            rolled_back = layer_transfer_group.transfer_group_to_page(
                context,
                click_target,
                drop_world_xy_mm=drop,
            )
        finally:
            layer_transfer_group.blend_io.save_page_blend = original_save_page_blend
            layer_transfer_group._rollback = original_transfer_rollback
        assert rolled_back == 0
        assert page_json_path.read_bytes() == page_json_before
        stage_path = layer_transfer_group.cross_page_stage.staged_path(
            Path(work.work_dir),
            target.id,
        )
        if stage_path.is_file():
            rolled_back_stage = json.loads(stage_path.read_text(encoding="utf-8"))
            assert not rolled_back_stage.get("asset_bundles")

        work = context.scene.bmanga_work
        source = work.pages[0]
        target = work.pages[1]
        panel = source.comas[0]
        parent_key = coma_stack_key(source, panel)
        folder = next(item for item in work.layer_folders if item.id == "folder_transfer_nested")
        balloon = next(item for item in source.balloons if item.id == "balloon_transfer")
        text = next(item for item in source.texts if item.id == "text_transfer")
        fill = next(item for item in context.scene.bmanga_fill_layers if item.id == "fill_transfer")
        image = next(item for item in context.scene.bmanga_image_layers if item.id == "image_transfer")
        path = next(item for item in context.scene.bmanga_image_path_layers if item.id == "path_transfer")
        raster = next(
            item
            for item in context.scene.bmanga_raster_layers
            if item.title == "移送ラスター"
        )
        assert text.parent_balloon_id == balloon.id
        assert balloon.parent_key == parent_key

        text_index, _text_item = _stack_item(context, "text", text.id)
        layer_stack.clear_all_selection(context)
        layer_stack.select_stack_index(context, text_index)
        click_target = ClickTarget(
            "page",
            target,
            None,
            1,
            drop,
            (115.0, 145.0),
        )
        original_mark_ready_fail_closed = (
            layer_transfer_group.cross_page_stage.mark_asset_bundle_ready
        )
        original_restore_source_files = layer_transfer_group._restore_source_files
        restore_source_calls = 0

        def fail_transaction_restore_source_files(*args, **kwargs):
            nonlocal restore_source_calls
            restore_source_calls += 1
            if restore_source_calls == 1:
                return original_restore_source_files(*args, **kwargs)
            return False

        layer_transfer_group.cross_page_stage.mark_asset_bundle_ready = (
            lambda *_args, **_kwargs: False
        )
        layer_transfer_group._restore_source_files = (
            fail_transaction_restore_source_files
        )
        try:
            try:
                layer_transfer_group.transfer_group_to_page(
                    context,
                    click_target,
                    drop_world_xy_mm=drop,
                )
                raise AssertionError("rollback failure was not propagated")
            except layer_transfer_group.LayerTransferRollbackError:
                pass
        finally:
            layer_transfer_group.cross_page_stage.mark_asset_bundle_ready = (
                original_mark_ready_fail_closed
            )
            layer_transfer_group._restore_source_files = original_restore_source_files
        assert not work.loaded
        assert restore_source_calls >= 2
        from bmanga_transfer_group_test.utils import handlers

        assert handlers.save_scene_work_to_disk(
            context,
            reason="phase5 rollback fail-closed probe",
        ) is False
        recovery_root = (
            paths.page_dir(Path(work.work_dir), source.id)
            / "_transfer_recovery"
        )
        assert any(recovery_root.glob("*/transaction.json"))
        restored_paths = layer_transfer_group.recover_interrupted_transfers(
            Path(work.work_dir)
        )
        assert restored_paths
        recovered_page_json = json.loads(page_json_path.read_text(encoding="utf-8"))
        recovered_nodes = recovered_page_json["tree"]["nodes"].values()
        assert any(
            item.get("kind") == "balloon"
            and item.get("displayId") == "balloon_transfer"
            for item in recovered_nodes
        )
        recovered_nodes = recovered_page_json["tree"]["nodes"].values()
        assert any(
            item.get("kind") == "text"
            and item.get("displayId") == "text_transfer"
            for item in recovered_nodes
        )
        assert not recovery_root.exists()
        assert "FINISHED" in bpy.ops.wm.open_mainfile(
            filepath=str(paths.page_blend_path(Path(work.work_dir), source.id)),
            load_ui=False,
        )
        context = bpy.context
        work = context.scene.bmanga_work
        assert work.loaded
        source = work.pages[0]
        target = work.pages[1]
        panel = source.comas[0]
        parent_key = coma_stack_key(source, panel)
        text = next(item for item in source.texts if item.id == "text_transfer")
        target_index = 1
        ox, oy = page_grid.page_total_offset_mm(work, context.scene, target_index)
        drop = (ox + 115.0, oy + 145.0)
        click_target = ClickTarget(
            "page",
            target,
            None,
            target_index,
            drop,
            (115.0, 145.0),
        )

        # 移動元blend保存後・移動先ready化前の強制終了を模擬する。例外処理を
        # 通らなくても、次回起動用ジャーナルからファイルとprepared stageを戻す。
        text_index, _text_item = _stack_item(context, "text", text.id)
        layer_stack.clear_all_selection(context)
        layer_stack.select_stack_index(context, text_index)
        source_blend_path = paths.page_blend_path(Path(work.work_dir), source.id)
        original_mark_ready = layer_transfer_group.cross_page_stage.mark_asset_bundle_ready

        def simulate_process_exit(*_args, **_kwargs):
            raise SystemExit("forced transfer process exit")

        layer_transfer_group.cross_page_stage.mark_asset_bundle_ready = simulate_process_exit
        try:
            try:
                layer_transfer_group.transfer_group_to_page(
                    context,
                    click_target,
                    drop_world_xy_mm=drop,
                )
                raise AssertionError("forced process exit was not propagated")
            except SystemExit as exc:
                assert "forced transfer process exit" in str(exc)
        finally:
            layer_transfer_group.cross_page_stage.mark_asset_bundle_ready = original_mark_ready
        recovery_root = (
            paths.page_dir(Path(work.work_dir), source.id)
            / "_transfer_recovery"
        )
        assert any(recovery_root.glob("*/transaction.json"))
        assert not any(
            "_transfer_recovery" in tracked.parts
            for tracked in save_baseline.tracked_paths(work.work_dir)
        )
        restored_paths = layer_transfer_group.recover_interrupted_transfers(
            Path(work.work_dir)
        )
        assert source_blend_path in restored_paths
        recovered_page_json = json.loads(page_json_path.read_text(encoding="utf-8"))
        recovered_nodes = recovered_page_json["tree"]["nodes"].values()
        assert any(
            item.get("kind") == "balloon"
            and item.get("displayId") == "balloon_transfer"
            for item in recovered_nodes
        )
        assert any(
            item.get("kind") == "text"
            and item.get("displayId") == "text_transfer"
            for item in recovered_nodes
        )
        assert not recovery_root.exists()
        if stage_path.is_file():
            recovered_stage = json.loads(stage_path.read_text(encoding="utf-8"))
            assert not recovered_stage.get("asset_bundles")
        # staleメモリを意図的に残すこのテストでは使わない。
        assert "FINISHED" in bpy.ops.wm.open_mainfile(
            filepath=str(source_blend_path),
            load_ui=False,
        )

        context = bpy.context
        work = context.scene.bmanga_work
        source = work.pages[0]
        target = work.pages[1]
        panel = source.comas[0]
        parent_key = coma_stack_key(source, panel)
        restored_path = next(
            item
            for item in context.scene.bmanga_image_path_layers
            if item.id == "path_transfer"
        )
        restored_image = next(
            item
            for item in context.scene.bmanga_image_layers
            if item.id == "image_transfer"
        )
        # テスト直書きのパターンカーブはOutliner操作を経由しないため、再読込後
        # の所属を実際の作成Operatorと同じ確定状態へ戻す。
        with image_path_object.suspend_auto_sync():
            restored_path.parent_kind = "coma"
            restored_path.parent_key = parent_key
            restored_path.folder_key = "folder_transfer_nested"
        restored_raster = next(
            item
            for item in context.scene.bmanga_raster_layers
            if item.title == "移送ラスター"
        )

        # 生成依存順とは異なる兄弟順を明示し、payload復元後にも同じ
        # parent-owned orderが残ることを確認する。
        moved_uids = {}
        for kind, marker in (
            ("text", "text_transfer"),
            ("raster", restored_raster.id),
            ("fill", "fill_transfer"),
            ("image", restored_image.id),
            ("image_path", "path_transfer"),
            ("balloon", "balloon_transfer"),
        ):
            _index, item = _stack_item(context, kind, marker)
            moved_uids[kind] = layer_stack.stack_item_uid(item)
        _force_uid_order(
            context,
            (
                moved_uids["text"],
                moved_uids["raster"],
                moved_uids["fill"],
                moved_uids["image"],
                moved_uids["image_path"],
                moved_uids["balloon"],
            ),
        )
        source_label_order = _moved_label_order(context)
        assert set(source_label_order) == {
            "text",
            "raster",
            "fill",
            "image",
            "image_path",
            "balloon",
        }, source_label_order
        # text→balloonの生成依存順とは逆の兄弟順が実際に作れていること。
        assert source_label_order.index("text") < source_label_order.index("balloon")

        # コマを選べば入れ子フォルダーと全子種別をまとめて移す。
        coma_index, _coma_item = _stack_item(context, "coma", panel.coma_id)
        layer_stack.clear_all_selection(context)
        layer_stack.select_stack_index(context, coma_index)
        group = layer_transfer_group.build_transfer_group(context)
        assert group is not None
        kinds = {str(getattr(item, "kind", "") or "") for item in group.items}
        expected = {
            "coma",
            "layer_folder",
            "balloon",
            "text",
            "fill",
            "image",
            "image_path",
            "raster",
        }
        assert expected <= kinds, (expected, kinds)

        click_target = ClickTarget("page", target, None, target_index, drop, (115.0, 145.0))
        moved = layer_transfer_group.transfer_group_to_page(
            context,
            click_target,
            drop_world_xy_mm=drop,
        )
        assert moved is not None and moved >= len(expected), moved
        assert len(source.comas) == 0
        assert not any(
            "_transfer_recovery" in tracked.parts
            for tracked in save_baseline.tracked_paths(work.work_dir)
        )
        staged = json.loads(stage_path.read_text(encoding="utf-8"))
        assert staged["asset_bundles"][0].get("state") == "ready"

        # Blender Undo handlerが使うtoken差分を直接往復させ、source Domain/
        # sidecar/native comaとtarget stageが同じ履歴境界で戻ることを検証する。
        from bmanga_transfer_group_test.utils import layer_transfer_history

        history_tokens = layer_transfer_history._tokens(context.scene)
        assert history_tokens
        history_record = layer_transfer_history._records[history_tokens[-1]]
        history_pre_files = layer_transfer_group._load_recovery_files(
            Path(work.work_dir),
            history_record.recovery_dir,
            history_record.manifest,
        )
        expected_pre_page_json = history_pre_files[page_json_path].read_bytes()
        original_undo_files = layer_transfer_history._undo_files
        original_redo_files = layer_transfer_history._redo_files
        original_reload_domain = layer_transfer_history._reload_domain

        def assert_locked_history_step(original):
            def wrapped(record):
                from bmanga_transfer_group_test.io import project_file_lock

                assert project_file_lock.owns_work_lock(record.work_dir)
                _assert_exclusive_work_lock(record.work_dir)
                return original(record)

            return wrapped

        layer_transfer_history._undo_files = assert_locked_history_step(
            original_undo_files
        )
        layer_transfer_history._redo_files = assert_locked_history_step(
            original_redo_files
        )
        layer_transfer_history._reload_domain = assert_locked_history_step(
            original_reload_domain
        )
        try:
            layer_transfer_history.begin_restore(context)
            layer_transfer_history._set_tokens(context.scene, history_tokens[:-1])
            assert layer_transfer_history.reconcile(context)
            assert page_json_path.read_bytes() == expected_pre_page_json
            assert not layer_transfer_group._recovery_stage_state(
                Path(work.work_dir),
                target.id,
                history_tokens[-1],
            )
            layer_transfer_history.begin_restore(context)
            layer_transfer_history._set_tokens(context.scene, history_tokens)
            assert layer_transfer_history.reconcile(context)
        finally:
            layer_transfer_history._undo_files = original_undo_files
            layer_transfer_history._redo_files = original_redo_files
            layer_transfer_history._reload_domain = original_reload_domain
        post_undo_redo_page = json.loads(page_json_path.read_text(encoding="utf-8"))
        assert not any(
            node.get("kind") == "coma"
            for node in post_undo_redo_page["tree"]["nodes"].values()
        )
        assert layer_transfer_group._recovery_stage_state(
            Path(work.work_dir),
            target.id,
            history_tokens[-1],
        ) == "ready"

        # target生成後のDomain確定だけを失敗させる。全生成ID/Object/Image/PNGと
        # stage sidecarを処理前へ戻し、同じstageを再試行できなければならない。
        target_stage_before = stage_path.read_bytes()
        raster_files_before = {
            path.resolve()
            for path in paths.raster_dir(Path(work.work_dir)).glob("*.png")
        }
        from bmanga_transfer_group_test.utils import (
            asset_bundle,
            asset_instantiation_transaction,
            cross_page_stage,
            cross_page_stage_command,
            cross_page_transfer,
            layer_command_runtime,
        )

        # 先に移動先ページだけを開き、ステージ適用前のBlenderデータブロック
        # 集合を基準化する。通常のページ切替によるデータ再読込と、素材生成の
        # rollbackを混同しない。
        original_process_staged = cross_page_transfer.process_staged_imports
        cross_page_transfer.process_staged_imports = lambda *_args, **_kwargs: 0
        try:
            assert "FINISHED" in bpy.ops.bmanga.open_page_file(
                "EXEC_DEFAULT",
                index=1,
            )
        finally:
            cross_page_transfer.process_staged_imports = original_process_staged
        context = bpy.context
        work = context.scene.bmanga_work
        target = work.pages[1]
        images_before = {int(item.as_pointer()) for item in bpy.data.images}
        materials_before = {
            int(item.as_pointer()) for item in bpy.data.materials
        }

        original_commit_projection = layer_command_runtime.commit_projection
        original_stage_log_exception = cross_page_stage._logger.exception
        original_stage_restore = cross_page_stage_command.restore
        original_asset_rollback = asset_bundle._rollback_instantiated_asset

        def fail_target_commit(_context, *, operation):
            if operation == "transfer.target":
                raise RuntimeError("phase5 injected target Domain failure")
            return original_commit_projection(_context, operation=operation)

        def fail_first_asset_rollback(*_args, **_kwargs):
            raise RuntimeError("phase5 injected rollback cleanup failure")

        def assert_locked_stage_restore(_context, snapshot):
            from bmanga_transfer_group_test.io import project_file_lock

            assert project_file_lock.owns_work_lock(snapshot.work_dir)
            _assert_exclusive_work_lock(snapshot.work_dir)
            return original_stage_restore(_context, snapshot)

        original_asset_instantiate = asset_bundle.instantiate_payload
        restore_calls: list[str] = []

        def fail_asset_instantiate(*_args, **_kwargs):
            raise asset_instantiation_transaction.AssetInstantiationRollbackError(
                RuntimeError("phase5 injected asset creation failure"),
                RuntimeError("phase5 injected asset rollback failure"),
            )

        def record_locked_stage_restore(_context, snapshot):
            restore_calls.append(str(snapshot.work_dir))
            return assert_locked_stage_restore(_context, snapshot)

        asset_bundle.instantiate_payload = fail_asset_instantiate
        cross_page_stage._logger.exception = lambda *_args, **_kwargs: None
        cross_page_stage_command.restore = record_locked_stage_restore
        try:
            assert cross_page_stage.process_staged_imports(
                context,
                page_id=target.id,
            ) == 0
        finally:
            asset_bundle.instantiate_payload = original_asset_instantiate
            cross_page_stage._logger.exception = original_stage_log_exception
            cross_page_stage_command.restore = original_stage_restore
        assert len(restore_calls) == 1
        assert stage_path.read_bytes() == target_stage_before
        assert {
            int(item.as_pointer()) for item in bpy.data.images
        } == images_before
        assert {
            int(item.as_pointer()) for item in bpy.data.materials
        } == materials_before

        failed_stage_snapshots = []

        def fail_outer_stage_restore(_context, snapshot):
            failed_stage_snapshots.append(snapshot)
            raise RuntimeError("phase5 injected outer stage rollback failure")

        layer_command_runtime.commit_projection = fail_target_commit
        cross_page_stage._logger.exception = lambda *_args, **_kwargs: None
        cross_page_stage_command.restore = fail_outer_stage_restore
        try:
            try:
                cross_page_stage.process_staged_imports(
                    context,
                    page_id=target.id,
                )
                raise AssertionError("outer rollback failure was not propagated")
            except cross_page_stage.StagedImportRollbackError:
                pass
        finally:
            layer_command_runtime.commit_projection = original_commit_projection
            cross_page_stage._logger.exception = original_stage_log_exception
            cross_page_stage_command.restore = original_stage_restore
        assert len(failed_stage_snapshots) == 1
        assert not work.loaded
        assert handlers.save_scene_work_to_disk(
            context,
            reason="phase5 staged rollback fail-closed probe",
        ) is False
        from bmanga_transfer_group_test.io import project_file_lock

        with project_file_lock.work_lock(Path(work.work_dir), blocking=True):
            original_stage_restore(context, failed_stage_snapshots[0])
        work.loaded = True
        assert stage_path.read_bytes() == target_stage_before
        assert {
            int(item.as_pointer()) for item in bpy.data.images
        } == images_before
        assert {
            int(item.as_pointer()) for item in bpy.data.materials
        } == materials_before

        layer_command_runtime.commit_projection = fail_target_commit
        cross_page_stage._logger.exception = lambda *_args, **_kwargs: None
        cross_page_stage_command.restore = assert_locked_stage_restore
        asset_bundle._rollback_instantiated_asset = fail_first_asset_rollback
        try:
            try:
                cross_page_stage.process_staged_imports(context, page_id=target.id)
                raise AssertionError("asset rollback failure was not propagated")
            except cross_page_stage.StagedImportRollbackError:
                pass
        finally:
            layer_command_runtime.commit_projection = original_commit_projection
            cross_page_stage._logger.exception = original_stage_log_exception
            cross_page_stage_command.restore = original_stage_restore
            asset_bundle._rollback_instantiated_asset = original_asset_rollback
        assert not work.loaded
        work.loaded = True
        assert stage_path.read_bytes() == target_stage_before
        assert {
            int(item.as_pointer()) for item in bpy.data.images
        } == images_before
        assert {
            int(item.as_pointer()) for item in bpy.data.materials
        } == materials_before
        assert not cross_page_stage._asset_stage_targets(
            context,
            target,
            history_tokens[-1],
        )
        assert {
            path.resolve()
            for path in paths.raster_dir(Path(work.work_dir)).glob("*.png")
        } == raster_files_before
        assert cross_page_stage.process_staged_imports(
            context,
            page_id=target.id,
        ) >= len(expected)

        moved_panel = max(target.comas, key=lambda item: int(getattr(item, "z_order", 0)))
        center = (
            float(moved_panel.rect_x_mm) + float(moved_panel.rect_width_mm) * 0.5,
            float(moved_panel.rect_y_mm) + float(moved_panel.rect_height_mm) * 0.5,
        )
        assert abs(center[0] - 115.0) < 0.01, center
        assert abs(center[1] - 145.0) < 0.01, center
        new_parent = coma_stack_key(target, moved_panel)
        new_text = next(item for item in target.texts if item.body == "リンク")
        new_balloon = next(
            item for item in target.balloons
            if item.id == new_text.parent_balloon_id
        )
        assert new_balloon.parent_key == new_parent
        assert new_text.parent_key == new_parent
        assert new_text.parent_balloon_id == new_balloon.id
        assert any(item.title == "移送グラデーション" for item in context.scene.bmanga_fill_layers)
        assert any(item.title == "移送画像" for item in context.scene.bmanga_image_layers)
        assert any(item.title == "移送パターンカーブ" for item in context.scene.bmanga_image_path_layers)
        assert any(item.title == "移送ラスター" for item in context.scene.bmanga_raster_layers)
        assert _moved_label_order(context) == source_label_order

        # 移送直後も、ページ間移動と作品ファイルへの復帰を連続して実行できる。
        assert "FINISHED" in bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in bpy.ops.bmanga.page_file_next("EXEC_DEFAULT")
        context = bpy.context
        work = context.scene.bmanga_work
        target = work.pages[work.active_page_index]
        assert len(target.comas) > 0
        tracked = {
            path.resolve()
            for path in save_baseline.tracked_paths(work.work_dir)
        }
        target_coma_blends = [
            paths.coma_blend_path(work.work_dir, target.id, coma.coma_id)
            for coma in target.comas
            if str(getattr(coma, "coma_id", "") or "")
        ]
        untracked_existing = [
            path
            for path in target_coma_blends
            if path.is_file() and path.resolve() not in tracked
        ]
        assert not untracked_existing, (
            [coma.coma_id for coma in target.comas],
            untracked_existing,
            sorted(path.name for path in tracked),
        )
        target.active_coma_index = 0
        assert "FINISHED" in bpy.ops.bmanga.enter_coma_mode("EXEC_DEFAULT")
        assert "FINISHED" in bpy.ops.bmanga.exit_coma_mode("EXEC_DEFAULT")
        assert "FINISHED" in bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT")
        assert Path(bpy.data.filepath).name == "work.blend"
        context = bpy.context
        work = context.scene.bmanga_work

        broken_stage_id = "broken_journal_stage"
        source_page_id = str(work.pages[0].id)
        target_page_id = str(work.pages[1].id)
        broken_recovery = (
            paths.page_dir(Path(work.work_dir), source_page_id)
            / "_transfer_recovery"
            / broken_stage_id
        )
        broken_recovery.mkdir(parents=True)
        (broken_recovery / "transaction.json").write_text(
            "{broken",
            encoding="utf-8",
        )
        broken_stage_path = layer_transfer_group.cross_page_stage.staged_path(
            Path(work.work_dir),
            target_page_id,
        )
        broken_stage_data = layer_transfer_group.cross_page_stage._read(
            broken_stage_path
        )
        broken_stage_data.setdefault("asset_bundles", []).append(
            {"stage_id": broken_stage_id, "state": "prepared"}
        )
        layer_transfer_group.json_io.write_json(
            broken_stage_path,
            broken_stage_data,
        )
        try:
            layer_transfer_group.recover_interrupted_transfers(Path(work.work_dir))
        except layer_transfer_group.LayerTransferRecoveryError:
            pass
        else:
            raise AssertionError("broken recovery journal was accepted")
        assert broken_recovery.is_dir()
        assert layer_transfer_group._recovery_stage_state(
            Path(work.work_dir),
            target_page_id,
            broken_stage_id,
        ) == "prepared"
        print("BMANGA_TRANSFER_GROUP_ALT_DND_OK")
    finally:
        if module is not None:
            try:
                module.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
