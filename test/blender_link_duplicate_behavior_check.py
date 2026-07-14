"""Blender実機用: フキダシ/効果線の複製とリンク複製を確認."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_link_duplicate"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _select_stack(kind: str, key_suffix: str = ""):
    from bmanga_dev_link_duplicate.utils import layer_stack

    stack = layer_stack.sync_layer_stack(bpy.context)
    assert stack is not None
    for index, item in enumerate(stack):
        if str(getattr(item, "kind", "") or "") != kind:
            continue
        key = str(getattr(item, "key", "") or "")
        if key_suffix and not key.endswith(key_suffix):
            continue
        assert layer_stack.select_stack_index(bpy.context, index)
        return item
    raise AssertionError(f"レイヤー一覧に対象がありません: {kind} {key_suffix}")


def _select_stack_uid(uid: str):
    from bmanga_dev_link_duplicate.utils import layer_stack

    stack = layer_stack.sync_layer_stack(bpy.context)
    assert stack is not None
    for index, item in enumerate(stack):
        if layer_stack.stack_item_uid(item) == uid:
            assert layer_stack.select_stack_index(bpy.context, index)
            return item
    raise AssertionError(f"レイヤー一覧に対象がありません: {uid}")


def _multi_select_stack_uids(uids: list[str], active_uid: str) -> None:
    from bmanga_dev_link_duplicate.utils import layer_stack

    stack = layer_stack.sync_layer_stack(bpy.context)
    assert stack is not None
    layer_stack.clear_all_selection(bpy.context)
    active_index = -1
    for index, item in enumerate(stack):
        uid = layer_stack.stack_item_uid(item)
        if uid == active_uid:
            active_index = index
        if uid in set(uids):
            layer_stack.set_item_selected(bpy.context, item, True)
    if active_index < 0:
        raise AssertionError(f"アクティブにするレイヤーがありません: {active_uid}")
    assert layer_stack.select_stack_index(bpy.context, active_index)
    stack = layer_stack.sync_layer_stack(bpy.context, preserve_active_index=True)
    for item in stack or []:
        uid = layer_stack.stack_item_uid(item)
        if uid in set(uids):
            layer_stack.set_item_selected(bpy.context, item, True)


def _balloon_uid(page, entry) -> str:
    from bmanga_dev_link_duplicate.utils import layer_stack
    from bmanga_dev_link_duplicate.utils.layer_hierarchy import OUTSIDE_STACK_KEY, page_stack_key

    page_key = OUTSIDE_STACK_KEY if page is None else page_stack_key(page)
    return layer_stack.target_uid("balloon", f"{page_key}:{entry.id}")


def _assert_pair(value, expected, label: str) -> None:
    actual = (round(float(value[0]), 6), round(float(value[1]), 6))
    exp = (round(float(expected[0]), 6), round(float(expected[1]), 6))
    if actual != exp:
        raise AssertionError(f"{label}: actual={actual} expected={exp}")


def _effect_key(layer) -> str:
    from bmanga_dev_link_duplicate.utils import layer_object_model

    obj = next(
        (
            candidate
            for candidate in layer_object_model.iter_layer_objects("effect")
            if layer_object_model.content_layer(candidate) == layer
        ),
        None,
    )
    assert obj is not None
    return layer_object_model.stable_id(obj)


def _effect_uid(layer) -> str:
    from bmanga_dev_link_duplicate.utils import layer_stack

    return layer_stack.target_uid("effect", _effect_key(layer))


def _select_effect(obj, layer) -> None:
    from bmanga_dev_link_duplicate.operators import effect_line_op

    _select_stack("effect", _effect_key(layer))
    effect_line_op._select_effect_layer(bpy.context, obj, layer)


def _effect_meta_entry(effect_line_op, obj, layer) -> dict:
    meta = effect_line_op._effect_meta(obj)
    key = effect_line_op._layer_meta_key(layer)
    entry = meta.get(key)
    return entry if isinstance(entry, dict) else {}


def _resolve_effect_by_id(effect_id: str):
    from bmanga_dev_link_duplicate.utils import object_naming as on

    obj = on.find_object_by_bmanga_id(effect_id, kind="effect")
    if obj is None:
        raise AssertionError(f"効果線が見つかりません: {effect_id}")
    layers = getattr(getattr(obj, "data", None), "layers", None)
    layer = getattr(layers, "active", None) if layers is not None else None
    if layer is None and layers is not None and len(layers) > 0:
        layer = layers[0]
    if layer is None:
        raise AssertionError(f"効果線レイヤーが見つかりません: {effect_id}")
    return obj, layer


def _test_balloon_link_duplicate(page) -> None:
    from bmanga_dev_link_duplicate.operators import balloon_op, layer_link_duplicate_op
    from bmanga_dev_link_duplicate.panels import gpencil_panel
    from bmanga_dev_link_duplicate.utils import free_transform, layer_links
    from bmanga_dev_link_duplicate.utils import layer_stack as layer_stack_utils

    source = balloon_op._create_balloon_entry(
        bpy.context,
        page,
        shape="ellipse",
        x=32.0,
        y=44.0,
        w=46.0,
        h=28.0,
        parent_kind="page",
        parent_key="",
    )
    source.center_offset_x_mm = 4.0
    source.center_offset_y_mm = -2.0
    free_transform.set_entry_offsets(
        source,
        {
            free_transform.BOTTOM_LEFT: (1.0, 0.0),
            free_transform.BOTTOM_RIGHT: (0.0, 1.0),
            free_transform.TOP_RIGHT: (-1.0, 0.0),
            free_transform.TOP_LEFT: (0.0, -1.0),
        },
        enabled=True,
    )

    _select_stack("balloon", f":{source.id}")
    assert bpy.ops.bmanga.layer_stack_duplicate("EXEC_DEFAULT") == {"FINISHED"}
    normal = page.balloons[-1]
    if abs(float(normal.x_mm) - 32.0) > 1.0e-6 or abs(float(normal.y_mm) - 44.0) > 1.0e-6:
        raise AssertionError("フキダシの通常複製で位置がずれています")

    _select_stack("balloon", f":{source.id}")
    assert bpy.ops.bmanga.layer_stack_link_duplicate("EXEC_DEFAULT") == {"FINISHED"}
    linked = page.balloons[-1]
    if abs(float(linked.x_mm) - float(source.x_mm)) > 1.0e-6 or abs(float(linked.y_mm) - float(source.y_mm)) > 1.0e-6:
        raise AssertionError("フキダシのリンク複製で位置がずれています")
    linked_uids = layer_links.linked_uids_for_uid(bpy.context, _balloon_uid(page, source))
    if _balloon_uid(page, linked) not in linked_uids:
        raise AssertionError("フキダシのリンク複製でリンク状態が作られていません")
    stack = layer_stack_utils.sync_layer_stack(bpy.context, preserve_active_index=True)
    # 2026-07-12 仕様変更: リンクマークは「選択中レイヤーのリンク相手」にだけ
    # 付く。リンク複製直後は複製先 (linked) がアクティブ選択されているので、
    # そのキャッシュを明示的に更新してからマークを読む (通常はパネル描画の
    # _draw_layer_stack_box が行う)。
    gpencil_panel._refresh_related_link_uids(bpy.context)
    linked_icons = {}
    for item in stack or []:
        uid = layer_stack_utils.stack_item_uid(item)
        if uid in {_balloon_uid(page, source), _balloon_uid(page, linked)}:
            linked_icons[uid] = gpencil_panel._link_state_icon(item)
    if linked_icons.get(_balloon_uid(page, source)) != "LINKED" or linked_icons.get(_balloon_uid(page, linked)) != "LINKED":
        raise AssertionError(f"レイヤーリストにリンク状態が表示されません: {linked_icons}")

    old_linked_x = float(linked.x_mm)
    old_linked_y = float(linked.y_mm)
    balloon_op._move_balloon_with_texts(page, source, float(source.x_mm) + 6.0, float(source.y_mm) - 4.0)
    layer_link_duplicate_op.propagate_linked_balloon_move_delta(bpy.context, page, source, 6.0, -4.0)
    if abs(float(linked.x_mm) - (old_linked_x + 6.0)) > 1.0e-6 or abs(float(linked.y_mm) - (old_linked_y - 4.0)) > 1.0e-6:
        raise AssertionError("リンクフキダシの移動が共有されていません")

    source.center_offset_x_mm = 7.0
    source.center_offset_y_mm = -3.0
    source.free_transform_line_width_scale = 1.75
    free_transform.set_entry_offsets(
        source,
        {
            free_transform.BOTTOM_LEFT: (2.0, 0.5),
            free_transform.BOTTOM_RIGHT: (0.5, 2.0),
            free_transform.TOP_RIGHT: (-2.0, -0.5),
            free_transform.TOP_LEFT: (-0.5, -2.0),
        },
        enabled=True,
    )
    layer_link_duplicate_op.propagate_linked_balloon_center_free(bpy.context, page, source)
    if float(linked.center_offset_x_mm) != 7.0 or float(linked.center_offset_y_mm) != -3.0:
        raise AssertionError("リンクフキダシの中心点が共有されていません")
    _assert_pair(linked.free_transform_bottom_left, (2.0, 0.5), "リンクフキダシの自由変形")
    if abs(float(linked.free_transform_line_width_scale) - 1.75) > 1.0e-6:
        raise AssertionError("リンクフキダシの自由変形線幅が共有されていません")

    _select_stack("balloon", f":{source.id}")
    assert bpy.ops.bmanga.reset_center_point("EXEC_DEFAULT") == {"FINISHED"}
    if float(source.center_offset_x_mm) != 0.0 or float(linked.center_offset_x_mm) != 0.0:
        raise AssertionError("中心点リセットがリンクフキダシに反映されていません")
    source.center_offset_x_mm = 3.0
    source.center_offset_y_mm = 3.0
    layer_link_duplicate_op.propagate_linked_balloon_center_free(bpy.context, page, source)
    assert bpy.ops.bmanga.reset_free_transform("EXEC_DEFAULT") == {"FINISHED"}
    if bool(source.free_transform_enabled) or bool(linked.free_transform_enabled):
        raise AssertionError("自由変形リセットがリンクフキダシに反映されていません")
    if (
        abs(float(source.free_transform_line_width_scale) - 1.0) > 1.0e-6
        or abs(float(linked.free_transform_line_width_scale) - 1.0) > 1.0e-6
    ):
        raise AssertionError("自由変形リセットでリンクフキダシの線幅が戻っていません")

    manual_a = balloon_op._create_balloon_entry(
        bpy.context,
        page,
        shape="ellipse",
        x=120.0,
        y=82.0,
        w=38.0,
        h=24.0,
        parent_kind="page",
        parent_key="",
    )
    manual_b = balloon_op._create_balloon_entry(
        bpy.context,
        page,
        shape="ellipse",
        x=170.0,
        y=112.0,
        w=52.0,
        h=30.0,
        parent_kind="page",
        parent_key="",
    )
    manual_a.rotation_deg = 18.0
    manual_a.center_offset_x_mm = 5.0
    manual_a.center_offset_y_mm = -6.0
    manual_b.rotation_deg = -11.0
    manual_b.center_offset_x_mm = -9.0
    manual_b.center_offset_y_mm = 8.0
    uid_a = _balloon_uid(page, manual_a)
    uid_b = _balloon_uid(page, manual_b)
    _multi_select_stack_uids([uid_a, uid_b], uid_a)
    assert bpy.ops.bmanga.layer_stack_link_selected("EXEC_DEFAULT") == {"FINISHED"}
    if abs(float(manual_b.x_mm) - float(manual_a.x_mm)) > 1.0e-6 or abs(float(manual_b.y_mm) - float(manual_a.y_mm)) > 1.0e-6:
        raise AssertionError("通常リンクしたフキダシの位置が共有されていません")
    if abs(float(manual_b.rotation_deg) - 18.0) > 1.0e-6:
        raise AssertionError("通常リンクしたフキダシの回転が共有されていません")
    if abs(float(manual_b.center_offset_x_mm) - 5.0) > 1.0e-6 or abs(float(manual_b.center_offset_y_mm) + 6.0) > 1.0e-6:
        raise AssertionError("通常リンクしたフキダシの中心点が共有されていません")
    manual_a.x_mm = 133.0
    manual_a.y_mm = 91.0
    manual_a.rotation_deg = 27.0
    manual_a.center_offset_x_mm = 2.5
    manual_a.center_offset_y_mm = -1.5
    if abs(float(manual_b.x_mm) - 133.0) > 1.0e-6 or abs(float(manual_b.y_mm) - 91.0) > 1.0e-6:
        raise AssertionError("通常リンク後のフキダシ位置変更が共有されていません")
    if abs(float(manual_b.rotation_deg) - 27.0) > 1.0e-6:
        raise AssertionError("通常リンク後のフキダシ回転変更が共有されていません")
    if abs(float(manual_b.center_offset_x_mm) - 2.5) > 1.0e-6 or abs(float(manual_b.center_offset_y_mm) + 1.5) > 1.0e-6:
        raise AssertionError("通常リンク後のフキダシ中心点変更が共有されていません")

    work = bpy.context.scene.bmanga_work
    shared = work.shared_balloons.add()
    shared.id = "shared_balloon_link_source"
    shared.title = "ページ外リンク元"
    shared.shape = "ellipse"
    shared.x_mm = 12.0
    shared.y_mm = 18.0
    shared.width_mm = 28.0
    shared.height_mm = 20.0
    shared.parent_kind = "none"
    shared.parent_key = ""
    _select_stack_uid(_balloon_uid(None, shared))
    assert bpy.ops.bmanga.layer_stack_link_duplicate("EXEC_DEFAULT") == {"FINISHED"}
    shared_linked = work.shared_balloons[-1]
    if shared_linked is shared or not str(getattr(shared_linked, "id", "") or "").startswith("shared_balloon"):
        raise AssertionError("ページ外フキダシのリンク複製が作成されていません")
    if abs(float(shared_linked.x_mm) - float(shared.x_mm)) > 1.0e-6 or abs(float(shared_linked.y_mm) - float(shared.y_mm)) > 1.0e-6:
        raise AssertionError("ページ外フキダシのリンク複製で位置がずれています")
    shared_uids = layer_links.linked_uids_for_uid(bpy.context, _balloon_uid(None, shared))
    if _balloon_uid(None, shared_linked) not in shared_uids:
        raise AssertionError("ページ外フキダシのリンク複製でリンク状態が作られていません")


def _test_effect_link_duplicate() -> None:
    from bmanga_dev_link_duplicate.operators import effect_line_op
    from bmanga_dev_link_duplicate.utils import free_transform, layer_links

    obj, source = effect_line_op._create_effect_layer(
        bpy.context,
        (20.0, 60.0, 36.0, 42.0),
        parent_key="",
    )
    effect_line_op._write_effect_strokes(bpy.context, obj, source, (20.0, 60.0, 36.0, 42.0), center_xy_mm=(33.0, 77.0))
    meta = effect_line_op._effect_meta(obj)
    key = effect_line_op._layer_meta_key(source)
    entry = dict(meta.get(key, {}) if isinstance(meta.get(key), dict) else {})
    free_transform.set_effect_payload_on_meta_entry(
        entry,
        {
            "enabled": True,
            "offsets": {
                free_transform.BOTTOM_LEFT: (1.0, 1.0),
                free_transform.BOTTOM_RIGHT: (2.0, 0.0),
                free_transform.TOP_RIGHT: (-1.0, -1.0),
                free_transform.TOP_LEFT: (0.0, -2.0),
            },
        },
    )
    meta[key] = entry
    effect_line_op._write_effect_meta(obj, meta)
    source_effect_id = str(obj.get("bmanga_id", "") or "")
    source_bounds = effect_line_op.effect_layer_bounds(obj, source)

    _select_effect(obj, source)
    assert bpy.ops.bmanga.layer_stack_duplicate("EXEC_DEFAULT") == {"FINISHED"}
    normal_obj, normal, _normal_bounds = effect_line_op.active_effect_layer_bounds(bpy.context)
    normal_bounds = effect_line_op.effect_layer_bounds(normal_obj, normal)
    if normal_bounds != source_bounds:
        debug = []
        for candidate in bpy.data.objects:
            if str(candidate.get("bmanga_kind", "") or "") != "effect":
                continue
            layers = getattr(getattr(candidate, "data", None), "layers", None)
            layer_keys = [effect_line_op._layer_meta_key(layer) for layer in (layers or [])]
            debug.append(
                {
                    "obj": candidate.name,
                    "id": str(candidate.get("bmanga_id", "") or ""),
                    "layers": layer_keys,
                    "meta": list(effect_line_op._effect_meta(candidate).keys()),
                }
            )
        raise AssertionError(
            f"効果線の通常複製で範囲がずれています: actual={normal_bounds} expected={source_bounds} debug={debug}"
        )

    obj, source = _resolve_effect_by_id(source_effect_id)
    _select_effect(obj, source)
    assert bpy.ops.bmanga.layer_stack_link_duplicate("EXEC_DEFAULT") == {"FINISHED"}
    linked_obj, linked, _linked_bounds = effect_line_op.active_effect_layer_bounds(bpy.context)
    linked_effect_id = str(linked_obj.get("bmanga_id", "") or "")
    linked_uids = layer_links.linked_uids_for_uid(bpy.context, _effect_uid(source))
    if _effect_uid(linked) not in linked_uids:
        raise AssertionError("効果線のリンク複製でリンク状態が作られていません")
    source_entry = _effect_meta_entry(effect_line_op, obj, source)
    linked_entry = _effect_meta_entry(effect_line_op, linked_obj, linked)
    for field in ("x", "y", "w", "h", "center_x", "center_y"):
        if abs(float(source_entry[field]) - float(linked_entry[field])) > 1.0e-6:
            raise AssertionError(f"リンク効果線の {field} が共有されていません")
    source_payload = free_transform.effect_payload_from_meta_entry(source_entry)
    linked_payload = free_transform.effect_payload_from_meta_entry(linked_entry)
    if source_payload != linked_payload:
        raise AssertionError("リンク効果線の自由変形が複製時に共有されていません")

    source_entry = dict(source_entry)
    free_transform.set_effect_payload_on_meta_entry(
        source_entry,
        {
            "enabled": True,
            "offsets": {
                free_transform.BOTTOM_LEFT: (4.0, 0.0),
                free_transform.BOTTOM_RIGHT: (0.0, 4.0),
                free_transform.TOP_RIGHT: (-4.0, 0.0),
                free_transform.TOP_LEFT: (0.0, -4.0),
            },
        },
    )
    meta = effect_line_op._effect_meta(obj)
    meta[effect_line_op._layer_meta_key(source)] = source_entry
    effect_line_op._write_effect_meta(obj, meta)
    effect_line_op._write_effect_strokes(
        bpy.context,
        obj,
        source,
        (20.0, 60.0, 36.0, 42.0),
        center_xy_mm=(50.0, 88.0),
    )
    linked_obj, linked = _resolve_effect_by_id(linked_effect_id)
    linked_entry = _effect_meta_entry(effect_line_op, linked_obj, linked)
    linked_payload = free_transform.effect_payload_from_meta_entry(linked_entry)
    if linked_payload != free_transform.effect_payload_from_meta_entry(source_entry):
        raise AssertionError("リンク効果線の自由変形変更が共有されていません")
    if abs(float(linked_entry["center_x"]) - 50.0) > 1.0e-6 or abs(float(linked_entry["center_y"]) - 88.0) > 1.0e-6:
        raise AssertionError("リンク効果線の中心点変更が共有されていません")

    manual_obj_a, manual_layer_a = effect_line_op._create_effect_layer(
        bpy.context,
        (75.0, 20.0, 44.0, 34.0),
        parent_key="",
    )
    effect_line_op._write_effect_strokes(
        bpy.context,
        manual_obj_a,
        manual_layer_a,
        (75.0, 20.0, 44.0, 34.0),
        center_xy_mm=(91.0, 33.0),
    )
    manual_obj_b, manual_layer_b = effect_line_op._create_effect_layer(
        bpy.context,
        (150.0, 95.0, 30.0, 28.0),
        parent_key="",
    )
    effect_line_op._write_effect_strokes(
        bpy.context,
        manual_obj_b,
        manual_layer_b,
        (150.0, 95.0, 30.0, 28.0),
        center_xy_mm=(162.0, 110.0),
    )
    uid_a = _effect_uid(manual_layer_a)
    uid_b = _effect_uid(manual_layer_b)
    _multi_select_stack_uids([uid_a, uid_b], uid_a)
    assert bpy.ops.bmanga.layer_stack_link_selected("EXEC_DEFAULT") == {"FINISHED"}
    manual_entry_a = _effect_meta_entry(effect_line_op, manual_obj_a, manual_layer_a)
    manual_entry_b = _effect_meta_entry(effect_line_op, manual_obj_b, manual_layer_b)
    for field in ("x", "y", "w", "h", "center_x", "center_y"):
        if abs(float(manual_entry_a[field]) - float(manual_entry_b[field])) > 1.0e-6:
            raise AssertionError(f"通常リンクした効果線の {field} が共有されていません")
    manual_entry_a = dict(manual_entry_a)
    params = dict(manual_entry_a.get("params", {}) if isinstance(manual_entry_a.get("params", {}), dict) else {})
    params["rotation_deg"] = 37.0
    manual_entry_a["params"] = params
    meta = effect_line_op._effect_meta(manual_obj_a)
    meta[effect_line_op._layer_meta_key(manual_layer_a)] = manual_entry_a
    effect_line_op._write_effect_meta(manual_obj_a, meta)
    effect_line_op._write_effect_strokes(
        bpy.context,
        manual_obj_a,
        manual_layer_a,
        (88.0, 26.0, 44.0, 34.0),
        center_xy_mm=(101.0, 48.0),
    )
    manual_entry_b = _effect_meta_entry(effect_line_op, manual_obj_b, manual_layer_b)
    for field, expected in {
        "x": 88.0,
        "y": 26.0,
        "w": 44.0,
        "h": 34.0,
        "center_x": 101.0,
        "center_y": 48.0,
    }.items():
        if abs(float(manual_entry_b[field]) - expected) > 1.0e-6:
            raise AssertionError(f"通常リンク後の効果線 {field} 変更が共有されていません")
    manual_params_b = manual_entry_b.get("params", {}) if isinstance(manual_entry_b.get("params", {}), dict) else {}
    if abs(float(manual_params_b.get("rotation_deg", 0.0)) - 37.0) > 1.0e-6:
        raise AssertionError("通常リンク後の効果線回転変更が共有されていません")


def _test_white_outline_order() -> None:
    from bmanga_dev_link_duplicate.utils import balloon_line_mesh

    if not (balloon_line_mesh.FLASH_WHITE_LINE_Z_OFFSET_M < balloon_line_mesh.LINE_Z_OFFSET_M):
        raise AssertionError("フキダシ白抜き線の白線が主線より下になっていません")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_link_duplicate_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "LinkDuplicate.bmanga"))
        assert result == {"FINISHED"}, result
        result = bpy.ops.bmanga.open_page_file(index=0)
        assert result == {"FINISHED"}, result
        page = bpy.context.scene.bmanga_work.pages[0]
        _test_balloon_link_duplicate(page)
        _test_effect_link_duplicate()
        _test_white_outline_order()
        print("BMANGA_LINK_DUPLICATE_BEHAVIOR_OK")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    main()
