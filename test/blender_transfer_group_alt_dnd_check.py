"""Blender 5.2実機: Alt+D&DのTransferGroupと全子レイヤー移送."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]


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


def _add_children(context, page, parent_key: str):
    from bmanga_transfer_group_test.utils import (
        balloon_curve_object,
        fill_real_object,
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
    for entry in (balloon, text, fill, path):
        entry.parent_kind = "coma"
        entry.parent_key = parent_key
        entry.folder_key = folder.id
    return folder, balloon, text, fill, path


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

        from bmanga_transfer_group_test.utils import (
            layer_stack,
            layer_transfer_group,
            page_grid,
        )
        from bmanga_transfer_group_test.utils.layer_hierarchy import coma_stack_key
        from bmanga_transfer_group_test.utils.layer_reparent import ClickTarget

        context = bpy.context
        work = context.scene.bmanga_work
        source = work.pages[0]
        target = work.pages[1]
        panel = source.comas[0]
        parent_key = coma_stack_key(source, panel)
        folder, balloon, text, fill, path = _add_children(context, source, parent_key)
        layer_stack.sync_layer_stack_after_data_change(context)
        from bmanga_transfer_group_test.utils import image_path_object

        with image_path_object.suspend_auto_sync():
            path.parent_kind = "coma"
            path.parent_key = parent_key
            path.folder_key = folder.id

        # テキスト片側だけの選択でも、フキダシとの明示リンク閉包を含む。
        text_index, _text_item = _stack_item(context, "text", text.id)
        layer_stack.select_stack_index(context, text_index)
        group = layer_transfer_group.build_transfer_group(context)
        assert group is not None
        kinds = {str(getattr(item, "kind", "") or "") for item in group.items}
        assert {"balloon", "text"} <= kinds, kinds

        target_index = 1
        ox, oy = page_grid.page_total_offset_mm(work, context.scene, target_index)
        drop = (ox + 115.0, oy + 145.0)
        click_target = ClickTarget("page", target, None, target_index, drop, (115.0, 145.0))

        # ソースblend保存を強制失敗させ、ページデータ・リンク・退避ステージが
        # すべて元へ戻ることを確認する。
        page_json_path = Path(work.work_dir) / source.id / "page.json"
        page_json_before = page_json_path.read_bytes()
        original_save_page_blend = layer_transfer_group.blend_io.save_page_blend
        layer_transfer_group.blend_io.save_page_blend = lambda *_args, **_kwargs: False
        try:
            rolled_back = layer_transfer_group.transfer_group_to_page(
                context,
                click_target,
                drop_world_xy_mm=drop,
            )
        finally:
            layer_transfer_group.blend_io.save_page_blend = original_save_page_blend
        assert rolled_back == 0
        assert page_json_path.read_bytes() == page_json_before
        stage_path = Path(work.work_dir) / target.id / "_staged_imports.json"
        if stage_path.is_file():
            rolled_back_stage = json.loads(stage_path.read_text(encoding="utf-8"))
            assert not rolled_back_stage.get("asset_bundles")

        # PropertyGroupはロールバックで再構築されるため参照を取り直す。
        work = context.scene.bmanga_work
        source = work.pages[0]
        target = work.pages[1]
        panel = source.comas[0]
        parent_key = coma_stack_key(source, panel)
        folder = next(item for item in work.layer_folders if item.id == "folder_transfer_nested")
        balloon = next(item for item in source.balloons if item.id == "balloon_transfer")
        text = next(item for item in source.texts if item.id == "text_transfer")
        fill = next(item for item in context.scene.bmanga_fill_layers if item.id == "fill_transfer")
        path = next(item for item in context.scene.bmanga_image_path_layers if item.id == "path_transfer")
        assert text.parent_balloon_id == balloon.id
        assert balloon.parent_key == parent_key

        # 移動元blend保存後・移動先ready化前の強制終了を模擬する。例外処理を
        # 通らなくても、次回起動用ジャーナルからファイルとprepared stageを戻す。
        text_index, _text_item = _stack_item(context, "text", text.id)
        layer_stack.clear_all_selection(context)
        layer_stack.select_stack_index(context, text_index)
        source_blend_path = Path(work.work_dir) / source.id / "page.blend"
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
        recovery_root = Path(work.work_dir) / source.id / "_transfer_recovery"
        assert any(recovery_root.glob("*/transaction.json"))
        restored_paths = layer_transfer_group.recover_interrupted_transfers(
            Path(work.work_dir)
        )
        assert source_blend_path in restored_paths
        recovered_page_json = json.loads(page_json_path.read_text(encoding="utf-8"))
        assert any(
            item.get("id") == "balloon_transfer"
            for item in recovered_page_json.get("balloons", [])
        )
        assert any(
            item.get("id") == "text_transfer"
            for item in recovered_page_json.get("texts", [])
        )
        assert not recovery_root.exists()
        if stage_path.is_file():
            recovered_stage = json.loads(stage_path.read_text(encoding="utf-8"))
            assert not recovered_stage.get("asset_bundles")
        # 実際の次回起動と同様、復旧済みblendを直接読み直す。B-MANGAの
        # ページ切替Operatorは現在メモリを保存してから開くため、強制終了後の
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
        # テスト直書きのパターンカーブはOutliner操作を経由しないため、再読込後
        # の所属を実際の作成Operatorと同じ確定状態へ戻す。
        with image_path_object.suspend_auto_sync():
            restored_path.parent_kind = "coma"
            restored_path.parent_key = parent_key
            restored_path.folder_key = "folder_transfer_nested"

        # コマを選べば入れ子フォルダーと全子種別をまとめて移す。
        coma_index, _coma_item = _stack_item(context, "coma", panel.coma_id)
        layer_stack.clear_all_selection(context)
        layer_stack.select_stack_index(context, coma_index)
        group = layer_transfer_group.build_transfer_group(context)
        assert group is not None
        kinds = {str(getattr(item, "kind", "") or "") for item in group.items}
        expected = {"coma", "layer_folder", "balloon", "text", "fill", "image_path"}
        assert expected <= kinds, (expected, kinds)

        click_target = ClickTarget("page", target, None, target_index, drop, (115.0, 145.0))
        moved = layer_transfer_group.transfer_group_to_page(
            context,
            click_target,
            drop_world_xy_mm=drop,
        )
        assert moved is not None and moved >= len(expected), moved
        assert len(source.comas) == 0
        staged = json.loads(stage_path.read_text(encoding="utf-8"))
        assert staged["asset_bundles"][0].get("state") == "ready"

        assert "FINISHED" in bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=1)
        context = bpy.context
        work = context.scene.bmanga_work
        target = work.pages[1]
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
        assert any(item.title == "移送パターンカーブ" for item in context.scene.bmanga_image_path_layers)

        # 復旧台帳自体が壊れている場合はprepared stageを孤児として消さない。
        # 自動復旧できなくても、再試行・手動救出用の両資料を残す。
        broken_stage_id = "broken_journal_stage"
        source_page_id = str(work.pages[0].id)
        target_page_id = str(work.pages[1].id)
        broken_recovery = (
            Path(work.work_dir)
            / source_page_id
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
        layer_transfer_group.recover_interrupted_transfers(Path(work.work_dir))
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
