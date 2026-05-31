"""Blender実機用: B-Nameアセット登録と配置復元を確認."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_asset_bundle",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_asset_bundle"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _item_by_uid(context):
    from bname_dev_asset_bundle.utils import layer_stack as layer_stack_utils

    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    return {layer_stack_utils.stack_item_uid(item): item for item in stack}


def _register_payload(context, uids: list[str], name: str):
    from bname_dev_asset_bundle.utils import asset_bundle

    items = _item_by_uid(context)
    payload = asset_bundle.build_payload(context, [items[uid] for uid in uids], name=name)
    coll = asset_bundle.create_collection_asset(
        context,
        payload,
        target=asset_bundle.AssetBrowserTarget("LOCAL"),
    )
    if coll.asset_data is None:
        raise AssertionError(f"{name}: アセット登録されていません")
    return coll


def _drop_collection(context, coll, world_x_mm: float, world_y_mm: float) -> None:
    from bname_dev_asset_bundle.utils import asset_bundle
    from bname_dev_asset_bundle.utils.geom import mm_to_m

    inst = bpy.data.objects.new(f"drop_{coll.name}", None)
    inst.instance_type = "COLLECTION"
    inst.instance_collection = coll
    inst.location = (mm_to_m(world_x_mm), mm_to_m(world_y_mm), 0.0)
    context.scene.collection.objects.link(inst)
    inst_name = inst.name
    if not asset_bundle.process_dropped_collection_instance(context, inst):
        raise AssertionError(f"{coll.name}: 3Dビューへの配置復元に失敗しました")
    if inst_name in bpy.data.objects:
        raise AssertionError(f"{coll.name}: 配置用インスタンスが残っています")


def _effect_objects():
    from bname_dev_asset_bundle.utils import object_naming

    return [
        obj
        for obj in bpy.data.objects
        if str(obj.get(object_naming.PROP_KIND, "") or "") == "effect"
    ]


def _effect_uid(obj) -> str:
    from bname_dev_asset_bundle.utils import layer_stack as layer_stack_utils

    layer = obj.data.layers[0]
    return layer_stack_utils.target_uid("effect", layer_stack_utils._node_stack_key(layer))


def _assert_linked(context, uid_a: str, uid_b: str, label: str) -> None:
    from bname_dev_asset_bundle.utils import layer_links

    linked = layer_links.linked_uids_for_uid(context, uid_a)
    if uid_b not in linked:
        raise AssertionError(f"{label}: リンク状態が復元されていません")


def _make_balloon(context, page, x, y, w=42.0, h=24.0):
    from bname_dev_asset_bundle.operators import balloon_op
    from bname_dev_asset_bundle.utils import balloon_curve_object

    work = context.scene.bname_work
    entry = page.balloons.add()
    entry.id = balloon_op._allocate_balloon_id(page, work)
    entry.shape = "ellipse"
    entry.x_mm = x
    entry.y_mm = y
    entry.width_mm = w
    entry.height_mm = h
    entry.parent_kind = "page"
    entry.parent_key = page.id
    balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    return entry


def _make_text(context, page, body, x, y, w=34.0, h=18.0, parent_balloon_id=""):
    from bname_dev_asset_bundle.operators import text_op
    from bname_dev_asset_bundle.utils import text_real_object

    entry = page.texts.add()
    entry.id = text_op._allocate_text_id(page)
    entry.body = body
    entry.x_mm = x
    entry.y_mm = y
    entry.width_mm = w
    entry.height_mm = h
    entry.parent_kind = "page"
    entry.parent_key = page.id
    entry.parent_balloon_id = parent_balloon_id
    text_real_object.ensure_text_real_object(scene=context.scene, entry=entry, page=page)
    return entry


def _make_effect(context, page, x, y, w=58.0, h=46.0):
    from bname_dev_asset_bundle.operators import effect_line_op

    obj, layer = effect_line_op._create_effect_layer(
        context,
        (float(x), float(y), float(w), float(h)),
        parent_key=page.id,
    )
    if obj is None or layer is None:
        raise AssertionError("効果線を作成できません")
    return obj, layer


def main() -> None:
    mod = None
    temp_root = Path(tempfile.mkdtemp(prefix="bname_asset_bundle_"))
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        from bname_dev_asset_bundle.utils import asset_bundle

        pending_coll = asset_bundle.create_collection_asset(
            bpy.context,
            {"version": 1, "name": "未読込配置", "origin": {"x": 0.0, "y": 0.0}, "entries": []},
            target=asset_bundle.AssetBrowserTarget("LOCAL"),
        )
        pending = bpy.data.objects.new("drop_before_work_loaded", None)
        pending.instance_type = "COLLECTION"
        pending.instance_collection = pending_coll
        bpy.context.scene.collection.objects.link(pending)
        if asset_bundle.process_dropped_collection_instance(bpy.context, pending):
            raise AssertionError("ページ一覧を開く前の配置が取り込まれています")
        if bool(pending.get(asset_bundle.ASSET_INSTANCE_DONE_PROP, False)):
            raise AssertionError("ページ一覧を開く前の配置が取り込み済みにされています")
        bpy.data.objects.remove(pending, do_unlink=True)

        result = bpy.ops.bname.work_new(filepath=str(temp_root / "AssetBundle.bname"))
        if "FINISHED" not in result:
            raise AssertionError(f"作品作成に失敗しました: {result}")

        from bname_dev_asset_bundle.utils import layer_links
        from bname_dev_asset_bundle.utils import layer_stack as layer_stack_utils
        from bname_dev_asset_bundle.utils import page_grid

        context = bpy.context
        work = context.scene.bname_work
        page = work.pages[0]

        source_balloon = _make_balloon(context, page, 20.0, 30.0)
        source_text = _make_text(context, page, "セリフ", 24.0, 34.0, parent_balloon_id=source_balloon.id)
        source_effect_a = _make_effect(context, page, 80.0, 40.0)
        source_effect_b = _make_effect(context, page, 126.0, 46.0)
        source_balloon_b = _make_balloon(context, page, 72.0, 82.0)
        source_lonely_text = _make_text(context, page, "ここへ落とす", 138.0, 110.0)

        uid_balloon = layer_stack_utils.target_uid("balloon", f"{page.id}:{source_balloon.id}")
        uid_text = layer_stack_utils.target_uid("text", f"{page.id}:{source_text.id}")
        uid_effect_a = _effect_uid(source_effect_a[0])
        uid_effect_b = _effect_uid(source_effect_b[0])
        uid_balloon_b = layer_stack_utils.target_uid("balloon", f"{page.id}:{source_balloon_b.id}")

        layer_links.link_uids(context, [uid_balloon, uid_text])
        layer_links.link_uids(context, [uid_effect_a, uid_effect_b])
        layer_links.link_uids(context, [uid_balloon, uid_balloon_b])
        layer_stack_utils.sync_layer_stack_after_data_change(context)

        items_by_uid = _item_by_uid(context)
        payload = asset_bundle.build_payload(context, [items_by_uid[uid_balloon]], name="フキダシ/登録:*確認")
        external_dir = temp_root / "AssetLibrary"
        external_target = asset_bundle.AssetBrowserTarget("BNameTest", "", str(external_dir), True)
        asset_bundle.create_collection_asset(context, payload, target=external_target)
        asset_bundle.create_collection_asset(context, payload, target=external_target)
        blend_files = sorted(external_dir.glob("*.blend"))
        if len(blend_files) != 2 or len({path.name for path in blend_files}) != 2:
            raise AssertionError("アセットライブラリ登録が既存ファイルを上書きしています")
        if any(any(ch in path.name for ch in '<>:"/\\|?*') for path in blend_files):
            raise AssertionError("アセットライブラリ登録のファイル名が安全化されていません")

        before_effects = set(_effect_objects())
        coll = _register_payload(context, [uid_effect_a], "効果線")
        _drop_collection(context, coll, 42.0, 170.0)
        if len(set(_effect_objects()) - before_effects) != 1:
            raise AssertionError("効果線アセットの配置復元数が正しくありません")

        before_balloons = len(page.balloons)
        coll = _register_payload(context, [uid_balloon], "フキダシ")
        _drop_collection(context, coll, 96.0, 170.0)
        if len(page.balloons) != before_balloons + 1:
            raise AssertionError("フキダシアセットの配置復元数が正しくありません")

        before_texts = len(page.texts)
        coll = _register_payload(context, [uid_text], "テキスト")
        _drop_collection(context, coll, 148.0, 170.0)
        if len(page.texts) != before_texts + 1:
            raise AssertionError("テキストアセットの配置復元数が正しくありません")

        before_balloons = len(page.balloons)
        before_texts = len(page.texts)
        coll = _register_payload(context, [uid_balloon, uid_text], "フキダシ＆テキスト")
        _drop_collection(context, coll, 58.0, 224.0)
        new_balloon = page.balloons[-1]
        new_text = page.texts[-1]
        if len(page.balloons) != before_balloons + 1 or len(page.texts) != before_texts + 1:
            raise AssertionError("フキダシ＆テキストの配置復元数が正しくありません")
        if new_text.parent_balloon_id != new_balloon.id:
            raise AssertionError("フキダシ＆テキストの親子リンクが復元されていません")
        new_balloon_uid = layer_stack_utils.target_uid("balloon", f"{page.id}:{new_balloon.id}")
        new_text_uid = layer_stack_utils.target_uid("text", f"{page.id}:{new_text.id}")
        _assert_linked(context, new_balloon_uid, new_text_uid, "フキダシ＆テキスト")

        before_effects = set(_effect_objects())
        coll = _register_payload(context, [uid_effect_a, uid_effect_b], "効果線＆効果線")
        _drop_collection(context, coll, 126.0, 224.0)
        created_effects = list(set(_effect_objects()) - before_effects)
        if len(created_effects) != 2:
            raise AssertionError("効果線＆効果線の配置復元数が正しくありません")
        _assert_linked(context, _effect_uid(created_effects[0]), _effect_uid(created_effects[1]), "効果線＆効果線")

        before_balloons = len(page.balloons)
        coll = _register_payload(context, [uid_balloon, uid_balloon_b], "フキダシ＆フキダシ")
        _drop_collection(context, coll, 190.0, 224.0)
        if len(page.balloons) != before_balloons + 2:
            raise AssertionError("フキダシ＆フキダシの配置復元数が正しくありません")
        new_a = page.balloons[-2]
        new_b = page.balloons[-1]
        _assert_linked(
            context,
            layer_stack_utils.target_uid("balloon", f"{page.id}:{new_a.id}"),
            layer_stack_utils.target_uid("balloon", f"{page.id}:{new_b.id}"),
            "フキダシ＆フキダシ",
        )

        before_balloons = len(page.balloons)
        coll = _register_payload(context, [uid_balloon_b], "フキダシをテキストへ")
        text_cx = source_lonely_text.x_mm + source_lonely_text.width_mm * 0.5
        text_cy = source_lonely_text.y_mm + source_lonely_text.height_mm * 0.5
        page_ox, page_oy = page_grid.page_total_offset_mm(work, context.scene, int(work.active_page_index))
        _drop_collection(context, coll, text_cx + page_ox, text_cy + page_oy)
        if len(page.balloons) != before_balloons + 1:
            raise AssertionError("テキスト上に落としたフキダシが復元されていません")
        if source_lonely_text.parent_balloon_id != page.balloons[-1].id:
            raise AssertionError("テキスト上に落としたフキダシが自動リンクされていません")

        print("BNAME_ASSET_BUNDLE_ROUNDTRIP_OK")
    finally:
        if mod is not None:
            mod.unregister()


if __name__ == "__main__":
    main()
