"""Blender実機用: コマ/ラスター/GPの素材登録と別ページ移動を確認."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_asset_extended",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_asset_extended"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _drop_collection(context, coll, world_x_mm: float, world_y_mm: float) -> None:
    from bmanga_dev_asset_extended.utils import asset_bundle
    from bmanga_dev_asset_extended.utils.geom import mm_to_m

    inst = bpy.data.objects.new(f"drop_{coll.name}", None)
    inst.instance_type = "COLLECTION"
    inst.instance_collection = coll
    inst.location = (mm_to_m(world_x_mm), mm_to_m(world_y_mm), 0.0)
    context.scene.collection.objects.link(inst)
    if not asset_bundle.process_dropped_collection_instance(context, inst):
        raise AssertionError(f"{coll.name}: 配置復元に失敗しました")


def _stack_index(context, kind: str, key_contains: str) -> int:
    from bmanga_dev_asset_extended.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    for index, item in enumerate(stack):
        if str(getattr(item, "kind", "") or "") != kind:
            continue
        key = str(getattr(item, "key", "") or "")
        label = str(getattr(item, "label", "") or getattr(item, "name", "") or "")
        if key_contains in key or key_contains in label:
            return index
    raise AssertionError(f"{kind}:{key_contains} がレイヤー一覧にありません")


def _register_index(context, index: int, name: str):
    from bmanga_dev_asset_extended.utils import asset_bundle

    coll = asset_bundle.register_selected_layers_as_asset(context, index=index, name=name)
    if coll is None or coll.asset_data is None:
        raise AssertionError(f"{name}: アセット登録されていません")
    return coll


def _make_balloon(context, page, parent_key: str):
    from bmanga_dev_asset_extended.operators import balloon_op
    from bmanga_dev_asset_extended.utils import balloon_curve_object

    entry = page.balloons.add()
    entry.id = balloon_op._allocate_balloon_id(page, context.scene.bmanga_work)
    entry.title = "素材フキダシ"
    entry.shape = "ellipse"
    entry.x_mm = 25.0
    entry.y_mm = 30.0
    entry.width_mm = 45.0
    entry.height_mm = 28.0
    entry.parent_kind = "coma"
    entry.parent_key = parent_key
    balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    return entry


def _make_raster(context, parent_key: str):
    from bmanga_dev_asset_extended.operators import raster_layer_op

    result = bpy.ops.bmanga.raster_layer_add(
        "EXEC_DEFAULT",
        dpi=30,
        bit_depth="gray8",
        enter_paint=False,
    )
    if "FINISHED" not in result:
        raise AssertionError("ラスターを追加できません")
    entry = context.scene.bmanga_raster_layers[context.scene.bmanga_active_raster_layer_index]
    entry.title = "素材ラスター"
    entry.parent_kind = "coma"
    entry.parent_key = parent_key
    entry.scope = "page"
    image = raster_layer_op.ensure_raster_image(context, entry, create_missing=True)
    width, height = int(image.size[0]), int(image.size[1])
    pixels = [0.0] * (width * height * 4)
    for y in range(height // 3, height // 3 + max(1, height // 8)):
        for x in range(width // 4, width // 4 + max(1, width // 3)):
            index = (y * width + x) * 4
            pixels[index:index + 4] = [0.0, 0.0, 0.0, 1.0]
    image.pixels = pixels
    image.update()
    raster_layer_op.save_raster_png(context, entry, force=True)
    raster_layer_op.ensure_raster_plane(context, entry)
    return entry


def _make_gp(context, parent_key: str):
    from bmanga_dev_asset_extended.utils import gp_object_layer, layer_object_model
    from bmanga_dev_asset_extended.utils import gpencil as gp_utils
    from bmanga_dev_asset_extended.utils.geom import mm_to_m

    obj = gp_object_layer.create_layer_gp_object(
        scene=context.scene,
        bmanga_id=layer_object_model.make_stable_id("gp"),
        title="素材GP",
        z_index=210,
        parent_kind="coma" if ":" in parent_key else "page",
        parent_key=parent_key,
    )
    layer = layer_object_model.content_layer(obj)
    assert layer is not None
    gp_utils.ensure_layer_material(obj, layer, activate=True, assign_existing=True)
    frame = gp_utils.ensure_active_frame(layer, frame_number=1)
    points = [
        (mm_to_m(20.0), mm_to_m(20.0), 0.0),
        (mm_to_m(40.0), mm_to_m(45.0), 0.0),
        (mm_to_m(65.0), mm_to_m(25.0), 0.0),
    ]
    if not gp_utils.add_stroke_to_drawing(frame.drawing, points, radius=0.01):
        raise AssertionError("GPストロークを作成できません")
    return obj, layer


def _page_center_world(context, page_index: int) -> tuple[float, float]:
    from bmanga_dev_asset_extended.utils import page_grid

    work = context.scene.bmanga_work
    ox, oy = page_grid.page_total_offset_mm(work, context.scene, page_index)
    paper = work.paper
    return ox + float(paper.canvas_width_mm) * 0.5, oy + float(paper.canvas_height_mm) * 0.5


def _gp_layers_for_parent(context, parent_key: str):
    from bmanga_dev_asset_extended.utils import layer_object_model

    return [
        layer_object_model.content_layer(obj)
        for obj in layer_object_model.iter_layer_objects("gp")
        if layer_object_model.parent_key(obj) == parent_key
    ]


def main() -> None:
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        temp_root = Path(tempfile.mkdtemp(prefix="bmanga_asset_extended_"))
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "AssetExtended.bmanga"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")
        result = bpy.ops.bmanga.page_add("EXEC_DEFAULT")
        if "FINISHED" not in result:
            raise AssertionError(f"ページ追加に失敗しました: {result}")
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=1)
        if "FINISHED" not in result:
            raise AssertionError(f"移動先ページを準備できません: {result}")
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        if "FINISHED" not in result:
            raise AssertionError(f"ページを開けません: {result}")

        from bmanga_dev_asset_extended.utils import layer_stack as layer_stack_utils
        from bmanga_dev_asset_extended.utils.layer_hierarchy import coma_stack_key

        context = bpy.context
        work = context.scene.bmanga_work
        work.active_page_index = 0
        page1 = work.pages[0]
        page2 = work.pages[1]
        panel = page1.comas[0]
        parent_key = coma_stack_key(page1, panel)
        _make_balloon(context, page1, parent_key)
        raster = _make_raster(context, parent_key)
        gp_obj, gp_layer = _make_gp(context, parent_key)
        layer_stack_utils.sync_layer_stack_after_data_change(context)

        # 選択中のラスターを別ページへ移動できること。
        raster_index = _stack_index(context, "raster", raster.id)
        layer_stack_utils.select_stack_index(context, raster_index)
        result = bpy.ops.bmanga.layer_move_to_page("EXEC_DEFAULT", target_page_id=page2.id)
        if "FINISHED" not in result:
            raise AssertionError(f"別ページへ移動できません: {result}")
        if raster.parent_key != page2.id or raster.parent_kind != "page":
            raise AssertionError("ラスターの移動先ページが反映されていません")
        raster.parent_kind = "coma"
        raster.parent_key = parent_key
        work.active_page_index = 0
        layer_stack_utils.sync_layer_stack_after_data_change(context)

        # コマ一式・ラスター単体・GP単体を登録し、別ページへまとめて送る。
        coma_index = _stack_index(context, "coma", panel.coma_id)
        coll = _register_index(context, coma_index, "コマ一式")
        payload = json.loads(str(coll.get("bmanga_asset_payload", "{}") or "{}"))
        payload_kinds = [str(entry.get("kind", "") or "") for entry in payload.get("entries", [])]
        if "gp" not in payload_kinds:
            raise AssertionError(f"コマ素材にGPが含まれていません: {payload_kinds}")
        from bmanga_dev_asset_extended.utils import layer_object_model

        raster_index = _stack_index(context, "raster", raster.id)
        raster_coll = _register_index(context, raster_index, "ラスター単体")
        raster_payload_data = json.loads(
            str(raster_coll.get("bmanga_asset_payload", "{}") or "{}")
        )
        valid_raster_payload_entry = next(
            entry
            for entry in raster_payload_data.get("entries", [])
            if isinstance(entry, dict) and entry.get("kind") == "raster"
        )
        gp_index = _stack_index(context, "gp", layer_object_model.stable_id(gp_obj))
        gp_coll = _register_index(context, gp_index, "GP単体")

        work.active_page_index = 1
        wx, wy = _page_center_world(context, 1)
        _drop_collection(context, coll, wx, wy)
        _drop_collection(context, raster_coll, wx + 15.0, wy)
        _drop_collection(context, gp_coll, wx - 15.0, wy)
        staged_path = Path(work.work_dir) / page2.id / "_staged_imports.json"
        staged = json.loads(staged_path.read_text(encoding="utf-8"))
        asset_stages = staged.get("asset_bundles", [])
        if len(asset_stages) != 3:
            raise AssertionError(f"別ページ向け素材が3件退避されていません: {len(asset_stages)}")
        stage_ids = [str(entry.get("stage_id", "") or "") for entry in asset_stages]
        asset_stage_by_id = {
            str(entry.get("stage_id", "") or ""): entry
            for entry in asset_stages
            if isinstance(entry, dict)
        }
        bundle_stage_id = stage_ids[0]

        # 対象ページを開いた時点では展開するだけで、保存成功までは退避を残す。
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=1)
        if "FINISHED" not in result:
            raise AssertionError(f"移動先ページを開けません: {result}")
        context = bpy.context
        work = context.scene.bmanga_work
        page2 = work.pages[1]
        from bmanga_dev_asset_extended.utils import cross_page_stage, cross_page_transfer

        def staged_entries(kind: str, stage_id: str):
            stage = asset_stage_by_id[stage_id]
            payload = stage.get("payload", {})
            result = []
            for index, entry in enumerate(payload.get("entries", []) or []):
                if not isinstance(entry, dict) or str(entry.get("kind", "") or "") != kind:
                    continue
                value = cross_page_stage.find_asset_created(
                    context,
                    page2,
                    stage_id,
                    index,
                    kind,
                )
                if value is not None:
                    result.append(value)
            return result

        bundle_comas = staged_entries("coma", bundle_stage_id)
        bundle_balloons = staged_entries("balloon", bundle_stage_id)
        bundle_rasters = staged_entries("raster", bundle_stage_id)
        bundle_gp = staged_entries("gp", bundle_stage_id)
        if not (len(bundle_comas) == len(bundle_balloons) == len(bundle_rasters) == len(bundle_gp) == 1):
            raise AssertionError("コマ一式が対象ページへ一度だけ展開されていません")
        new_parent = coma_stack_key(page2, bundle_comas[0])
        if bundle_balloons[0].parent_key != new_parent:
            raise AssertionError("コマ内フキダシの所属が新しいコマに向いていません")
        if bundle_rasters[0].parent_key != new_parent:
            raise AssertionError("コマ内ラスターの所属が新しいコマに向いていません")
        if layer_object_model.parent_key(bundle_gp[0]) != new_parent:
            raise AssertionError("コマ内GPの所属が新しいコマに向いていません")
        if not staged_path.exists():
            raise AssertionError("ページ保存前に素材の退避が消えています")

        # 同じ画面で二重に呼んでも増えず、保存せず再読込しても退避から一度だけ戻る。
        before = tuple(len(staged_entries(kind, sid)) for sid in stage_ids for kind in ("coma", "balloon", "raster", "gp"))
        cross_page_transfer.process_staged_imports(context, page_id=page2.id)
        after = tuple(len(staged_entries(kind, sid)) for sid in stage_ids for kind in ("coma", "balloon", "raster", "gp"))
        if before != after:
            raise AssertionError("素材の復元処理を再呼出しすると内容が重複します")
        page2_blend = Path(work.work_dir) / page2.id / "page.blend"
        bpy.ops.wm.open_mainfile(filepath=str(page2_blend), load_ui=False)
        context = bpy.context
        work = context.scene.bmanga_work
        page2 = work.pages[1]
        if not staged_path.exists():
            raise AssertionError("保存せず再読込したとき素材の退避が消えています")
        for sid in stage_ids:
            matches = sum(len(staged_entries(kind, sid)) for kind in ("coma", "balloon", "raster", "gp"))
            if matches == 0:
                raise AssertionError(f"保存せず再読込した素材を再試行できません: {sid}")

        # 復元後に同じstage_idの内容が差し替わっても、保存した旧版だけを確定する。
        latest_before_commit = json.loads(staged_path.read_text(encoding="utf-8"))
        original_bundle_stage = next(
            entry
            for entry in latest_before_commit.get("asset_bundles", [])
            if entry.get("stage_id") == bundle_stage_id
        )
        replacement_bundle_stage = copy.deepcopy(original_bundle_stage)
        replacement_bundle_stage["payload"]["concurrent_revision"] = "newer"
        replacement_bundle_token = cross_page_stage._entry_token(
            "asset",
            replacement_bundle_stage,
        )
        assert cross_page_stage._append_unique(
            Path(work.work_dir),
            page2.id,
            cross_page_stage.ASSET_ENTRIES_KEY,
            replacement_bundle_stage,
            bundle_stage_id,
        )

        # 保存後にだけ退避を削除し、再オープン後も同じまとまりが一つ残る。
        bpy.ops.wm.save_as_mainfile(filepath=str(page2_blend), check_existing=False, compress=True)
        cross_page_transfer.commit_staged_imports_after_save(
            context,
            blend_path=page2_blend,
            metadata_saved=True,
        )
        committed_stage = json.loads(staged_path.read_text(encoding="utf-8"))
        remaining_bundles = committed_stage.get("asset_bundles", [])
        if len(remaining_bundles) != 1 or (
            remaining_bundles[0].get("payload", {}).get("concurrent_revision") != "newer"
        ):
            raise AssertionError("同一stage_idの新しい素材まで保存確定で消えています")
        if cross_page_transfer.process_staged_imports(context, page_id=page2.id) <= 0:
            raise AssertionError("同一stage_idの新しい素材版を置換復元できません")
        replaced_targets = [
            target
            for kind in ("coma", "balloon", "raster", "gp")
            for target in staged_entries(kind, bundle_stage_id)
        ]
        if not replaced_targets or any(
            str(target.get(cross_page_stage.ASSET_STAGE_TOKEN_PROP, "") or "")
            != replacement_bundle_token
            for target in replaced_targets
        ):
            raise AssertionError("置換後の素材実体が新しい内容トークンに揃っていません")
        bpy.ops.wm.save_as_mainfile(filepath=str(page2_blend), check_existing=False, compress=True)
        committed_replacement = cross_page_transfer.commit_staged_imports_after_save(
            context,
            blend_path=page2_blend,
            metadata_saved=True,
        )
        if staged_path.exists() and committed_replacement != 1:
            raise AssertionError("新しい素材版を保存確定できません")
        if staged_path.exists():
            raise AssertionError("新しい素材版を再処理・保存後も退避が残っています")
        bpy.ops.wm.open_mainfile(filepath=str(page2_blend), load_ui=False)
        context = bpy.context
        work = context.scene.bmanga_work
        page2 = work.pages[1]
        bundle_comas = staged_entries("coma", bundle_stage_id)
        bundle_balloons = staged_entries("balloon", bundle_stage_id)
        bundle_rasters = staged_entries("raster", bundle_stage_id)
        bundle_gp = staged_entries("gp", bundle_stage_id)
        if not (len(bundle_comas) == len(bundle_balloons) == len(bundle_rasters) == len(bundle_gp) == 1):
            raise AssertionError(
                "保存後の素材が欠落または重複しています: "
                f"coma={len(bundle_comas)} balloon={len(bundle_balloons)} "
                f"raster={len(bundle_rasters)} gp={len(bundle_gp)}"
            )

        # 破損payloadとatomic write失敗はラスター項目を残さず、退避を再試行可能に保つ。
        from bmanga_dev_asset_extended.utils import asset_bundle_extended

        corrupt_entry = copy.deepcopy(valid_raster_payload_entry)
        corrupt_entry["png_base64"] = "not-valid-base64***"
        corrupt_payload = {"origin": {"x": 0.0, "y": 0.0}, "entries": [corrupt_entry]}
        corrupt_stage_id = cross_page_stage.stage_asset_bundle(
            Path(work.work_dir),
            page2.id,
            corrupt_payload,
            (20.0, 20.0),
        )
        assert corrupt_stage_id
        before_raster_count = len(context.scene.bmanga_raster_layers)
        cross_page_transfer.process_staged_imports(context, page_id=page2.id)
        assert len(context.scene.bmanga_raster_layers) == before_raster_count

        write_fail_payload = {
            "origin": {"x": 0.0, "y": 0.0},
            "entries": [copy.deepcopy(valid_raster_payload_entry)],
        }
        write_fail_stage_id = cross_page_stage.stage_asset_bundle(
            Path(work.work_dir),
            page2.id,
            write_fail_payload,
            (30.0, 30.0),
        )
        assert write_fail_stage_id
        original_atomic_write = asset_bundle_extended._atomic_write_verified_bytes

        def fail_atomic_write(_path, _payload):
            raise OSError("simulated write failure")

        asset_bundle_extended._atomic_write_verified_bytes = fail_atomic_write
        try:
            cross_page_transfer.process_staged_imports(context, page_id=page2.id)
        finally:
            asset_bundle_extended._atomic_write_verified_bytes = original_atomic_write
        assert len(context.scene.bmanga_raster_layers) == before_raster_count
        failed_stage_data = json.loads(staged_path.read_text(encoding="utf-8"))
        remaining_failed_ids = {
            str(entry.get("stage_id", "") or "")
            for entry in failed_stage_data.get("asset_bundles", [])
            if isinstance(entry, dict)
        }
        assert {corrupt_stage_id, write_fail_stage_id} <= remaining_failed_ids
        from bmanga_dev_asset_extended.io.project_content_migration_lock import guard_path_write
        from bmanga_dev_asset_extended.io.project_content_save_baseline import record_successful_write

        with guard_path_write(staged_path):
            staged_path.unlink()
            record_successful_write(staged_path)

        print("BMANGA_ASSET_EXTENDED_LAYERS_OK")
    finally:
        if mod is not None:
            mod.unregister()


if __name__ == "__main__":
    main()
