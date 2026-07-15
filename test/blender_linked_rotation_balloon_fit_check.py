"""Blender 5.1: リンク回転・テキスト確定時フィット・縦書き再適用の回帰検証。"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_linked_rotation_balloon_fit"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module.register()
    return module


def _sub(path: str):
    __import__(f"{MODULE_NAME}.{path}")
    return sys.modules[f"{MODULE_NAME}.{path}"]


def _close(left: float, right: float) -> bool:
    return abs(float(left) - float(right)) < 1.0e-5


def _make_fixture(context):
    work = context.scene.bmanga_work
    page = work.pages[0]

    balloon = page.balloons.add()
    balloon.id = "linked_balloon_a"
    balloon.title = "リンク元フキダシ"
    balloon.rotation_deg = 10.0

    text = page.texts.add()
    text.id = "linked_text_a"
    text.title = "リンクテキスト"
    text.body = "リンク確認"
    text.parent_balloon_id = balloon.id
    text.rotation_deg = -5.0
    balloon.text_id = text.id

    peer = page.balloons.add()
    peer.id = "linked_balloon_b"
    peer.title = "明示リンク先"
    peer.rotation_deg = 30.0

    layer_stack = _sub("utils.layer_stack")
    layer_links = _sub("utils.layer_links")
    layer_stack.sync_layer_stack_after_data_change(context)
    balloon_uid = layer_stack.target_uid("balloon", f"{page.id}:{balloon.id}")
    peer_uid = layer_stack.target_uid("balloon", f"{page.id}:{peer.id}")
    layer_links.link_uids(context, [balloon_uid, peer_uid])
    layer_stack.sync_layer_stack_after_data_change(context)
    return work, page, balloon, text, peer


def _assert_linked_rotation(context, page, balloon, text, peer) -> None:
    layer_links = _sub("utils.layer_links")
    object_rotation = _sub("operators.object_rotation")
    object_selection = _sub("utils.object_selection")

    source_key = object_selection.balloon_key(page, balloon)
    related_keys = layer_links.related_object_keys_for_key(context, source_key)
    expected = {
        source_key,
        object_selection.text_key(page, text),
        object_selection.balloon_key(page, peer),
    }
    assert expected.issubset(set(related_keys)), related_keys

    snapshots = [object_rotation.capture_rotation_snapshot(context, key) for key in related_keys]
    snapshots = [item for item in snapshots if item is not None]
    assert len(snapshots) >= 3, [item.get("key") for item in snapshots]
    for snapshot in snapshots:
        object_rotation.apply_rotation_snapshot(
            context,
            snapshot,
            float(snapshot["base_rotation_deg"]) + 25.0,
        )
    assert _close(balloon.rotation_deg, 35.0)
    assert _close(text.rotation_deg, 20.0)
    assert _close(peer.rotation_deg, 55.0)


def _assert_linked_effect_rotation(context, page) -> None:
    effect_line_op = _sub("operators.effect_line_op")
    layer_links = _sub("utils.layer_links")
    layer_object_model = _sub("utils.layer_object_model")
    layer_stack = _sub("utils.layer_stack")
    object_rotation = _sub("operators.object_rotation")
    object_selection = _sub("utils.object_selection")
    layer_hierarchy = _sub("utils.layer_hierarchy")

    first_obj, first_layer = effect_line_op._create_effect_layer(
        context,
        (20.0, 20.0, 30.0, 40.0),
        parent_key=layer_hierarchy.page_stack_key(page),
    )
    assert first_obj is not None and first_layer is not None
    context.scene.bmanga_effect_line_params.rotation_deg = 12.0
    second_obj, second_layer = effect_line_op._create_effect_layer(
        context,
        (70.0, 20.0, 30.0, 40.0),
        parent_key=layer_hierarchy.page_stack_key(page),
    )
    assert second_obj is not None and second_layer is not None
    context.scene.bmanga_effect_line_params.rotation_deg = -8.0

    layer_stack.sync_layer_stack_after_data_change(context)
    first_uid = layer_stack.target_uid("effect", layer_object_model.stable_id(first_obj))
    second_uid = layer_stack.target_uid("effect", layer_object_model.stable_id(second_obj))
    layer_links.link_uids(context, [first_uid, second_uid])
    layer_stack.sync_layer_stack_after_data_change(context)

    source_key = object_selection.effect_key(first_obj)
    related_keys = layer_links.related_object_keys_for_key(context, source_key)
    assert object_selection.effect_key(second_obj) in related_keys, related_keys
    snapshots = [object_rotation.capture_rotation_snapshot(context, key) for key in related_keys]
    for snapshot in (item for item in snapshots if item is not None):
        object_rotation.apply_rotation_snapshot(
            context,
            snapshot,
            float(snapshot["base_rotation_deg"]) + 30.0,
        )
    first_data = effect_line_op._layer_params_data(first_obj, first_layer)
    second_data = effect_line_op._layer_params_data(second_obj, second_layer)
    assert _close(first_data["rotation_deg"], 42.0), first_data["rotation_deg"]
    assert _close(second_data["rotation_deg"], 22.0), second_data["rotation_deg"]


def _assert_fit_and_persistence(context, page, balloon, text) -> None:
    schema = _sub("io.schema")
    text_balloon_link = _sub("utils.text_balloon_link")
    text_op = _sub("operators.text_op")
    text_style = _sub("utils.text_style")

    text.x_mm = 20.0
    text.y_mm = 30.0
    text.width_mm = 40.0
    text.height_mm = 60.0
    balloon.linked_text_offset_x_mm = 2.0
    balloon.linked_text_offset_y_mm = -3.0
    balloon.linked_text_padding_x_mm = 7.0
    balloon.linked_text_padding_y_mm = 8.0
    text_balloon_link.fit_linked_balloon_to_text(text, balloon)
    assert _close(balloon.width_mm, 54.0)
    assert _close(balloon.height_mm, 76.0)
    assert _close(balloon.x_mm, 15.0)
    assert _close(balloon.y_mm, 19.0)

    # テキスト編集の確定経路で矩形変更を検知し、その場で親フキダシも更新する。
    original_rect = (text.x_mm, text.y_mm, text.width_mm, text.height_mm)
    text.width_mm = 52.0
    text.height_mm = 44.0
    fake_tool = SimpleNamespace(
        _current_text_entry=lambda _context: (page, text, 0),
        _edit_original_body=text.body,
        _edit_original_font_spans=text_style.all_spans_snapshot(text),
        _edit_original_rect=original_rect,
        _editing_created_new=False,
        _push_undo_step=lambda _label: None,
        _end_inline_input=lambda _context: None,
        _clear_click_state=lambda: None,
        report=lambda _levels, _message: None,
    )
    text_op.BMANGA_OT_text_tool._finish_current_text_edit(
        fake_tool,
        context,
        fit_to_body=False,
    )
    assert _close(balloon.width_mm, 66.0)
    assert _close(balloon.height_mm, 60.0)
    assert _close(balloon.x_mm, 15.0)
    assert _close(balloon.y_mm, 19.0)

    payload = schema.balloon_entry_to_dict(balloon)
    restored = page.balloons.add()
    restored.id = "restored_balloon"
    schema.balloon_entry_from_dict(restored, payload)
    assert _close(restored.linked_text_offset_x_mm, 2.0)
    assert _close(restored.linked_text_offset_y_mm, -3.0)
    assert _close(restored.linked_text_padding_x_mm, 7.0)
    assert _close(restored.linked_text_padding_y_mm, 8.0)


def _assert_vertical_preset_reapply(page) -> None:
    text_presets = _sub("io.text_presets")
    entry = page.texts.add()
    entry.id = "vertical_reapply"
    entry.width_mm = 30.0
    entry.height_mm = 70.0
    data = {
        "writing_mode": "vertical",
        "line_height": 1.5,
        "font_size_value": 20.0,
        "font_size_unit": "q",
    }
    text_presets.apply_to_entry(entry, data)
    first_size = (float(entry.width_mm), float(entry.height_mm))
    assert first_size == (70.0, 30.0), first_size
    assert _close(entry.line_height, 1.5)
    text_presets.apply_to_entry(entry, data)
    assert (float(entry.width_mm), float(entry.height_mm)) == first_size, (
        entry.width_mm,
        entry.height_mm,
    )
    assert entry.writing_mode == "vertical" and _close(entry.line_height, 1.5)


def main() -> None:
    addon = None
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_linked_rotation_fit_"))
    try:
        addon = _load_addon()
        result = bpy.ops.bmanga.work_new(
            filepath=str(temp_root / "LinkedRotationFit.bmanga")
        )
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result
        context = bpy.context
        _work, page, balloon, text, peer = _make_fixture(context)
        _assert_linked_rotation(context, page, balloon, text, peer)
        _assert_linked_effect_rotation(context, page)
        _assert_fit_and_persistence(context, page, balloon, text)
        _assert_vertical_preset_reapply(page)
        print("BMANGA_LINKED_ROTATION_BALLOON_FIT_OK")
    finally:
        if addon is not None:
            addon.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
