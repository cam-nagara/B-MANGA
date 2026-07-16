"""Blender実機: リンクテキスト余白の即時反映とプリセット切替を検証する。"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_linked_balloon_preset_padding"


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


def _close(actual: float, expected: float) -> None:
    assert abs(float(actual) - float(expected)) < 1.0e-5, (actual, expected)


def _save_preset(balloon_presets, name: str, *, offset_x: float, offset_y: float,
                 padding_x: float, padding_y: float) -> None:
    balloon_presets.save_local_preset(
        None,
        name,
        "リンクテキスト余白テスト",
        [(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)],
        extras={
            "linkedTextOffsetXMm": offset_x,
            "linkedTextOffsetYMm": offset_y,
            "linkedTextPaddingXMm": padding_x,
            "linkedTextPaddingYMm": padding_y,
        },
    )


def _make_pair(context):
    work = context.scene.bmanga_work
    page = work.pages[0]
    balloon = page.balloons.add()
    balloon.id = "linked_padding_balloon"
    balloon.shape = "rect"
    balloon.x_mm = 0.0
    balloon.y_mm = 0.0
    balloon.width_mm = 42.0
    balloon.height_mm = 22.0

    text = page.texts.add()
    text.id = "linked_padding_text"
    text.body = "余白"
    text.x_mm = 20.0
    text.y_mm = 30.0
    text.width_mm = 30.0
    text.height_mm = 10.0
    text.parent_balloon_id = balloon.id
    balloon.text_id = text.id

    page.active_balloon_index = len(page.balloons) - 1
    context.scene.bmanga_active_layer_kind = "balloon"
    _sub("utils.layer_stack").sync_layer_stack_after_data_change(context)
    _sub("utils.balloon_curve_object").on_balloon_entry_changed(balloon)
    return work, page, balloon, text


def _assert_fit(balloon, *, offset_x: float, offset_y: float,
                padding_x: float, padding_y: float,
                text_center_x: float = 35.0,
                text_center_y: float = 35.0) -> None:
    expected_width = 30.0 + padding_x * 2.0
    expected_height = 10.0 + padding_y * 2.0
    _close(balloon.linked_text_offset_x_mm, offset_x)
    _close(balloon.linked_text_offset_y_mm, offset_y)
    _close(balloon.linked_text_padding_x_mm, padding_x)
    _close(balloon.linked_text_padding_y_mm, padding_y)
    _close(balloon.width_mm, expected_width)
    _close(balloon.height_mm, expected_height)
    _close(balloon.x_mm, text_center_x + offset_x - expected_width * 0.5)
    _close(balloon.y_mm, text_center_y + offset_y - expected_height * 0.5)


def _assert_direct_setting_refits(balloon, text) -> None:
    text_balloon_link = _sub("utils.text_balloon_link")
    text_balloon_link.fit_linked_balloon_to_text(text, balloon)
    balloon.linked_text_padding_x_mm = 10.0
    balloon.linked_text_padding_y_mm = 4.0
    _assert_fit(
        balloon,
        offset_x=0.0,
        offset_y=0.0,
        padding_x=10.0,
        padding_y=4.0,
    )


def _assert_single_fallback_and_ambiguous_links(context, page) -> None:
    balloon = page.balloons.add()
    balloon.id = "single_fallback_balloon"
    text = page.texts.add()
    text.id = "single_fallback_text"
    text.x_mm = 20.0
    text.y_mm = 30.0
    text.width_mm = 30.0
    text.height_mm = 10.0
    text.parent_balloon_id = balloon.id
    text_balloon_link = _sub("utils.text_balloon_link")
    fitted = text_balloon_link.fit_balloon_to_linked_text(
        context.scene.bmanga_work,
        balloon,
        page=page,
    )
    assert str(getattr(fitted, "id", "") or "") == text.id
    balloon.linked_text_padding_x_mm = 5.0
    balloon.linked_text_padding_y_mm = 6.0
    _assert_fit(
        balloon,
        offset_x=0.0,
        offset_y=0.0,
        padding_x=5.0,
        padding_y=6.0,
    )

    second = page.texts.add()
    second.id = "ambiguous_second_text"
    second.parent_balloon_id = balloon.id
    original_width = float(balloon.width_mm)
    balloon.linked_text_padding_x_mm = 9.0
    _close(balloon.width_mm, original_width)


def _assert_shared_pair_refits(context) -> None:
    work = context.scene.bmanga_work
    balloon = work.shared_balloons.add()
    balloon.id = "shared_linked_padding_balloon"
    text = work.shared_texts.add()
    text.id = "shared_linked_padding_text"
    text.x_mm = 20.0
    text.y_mm = 30.0
    text.width_mm = 30.0
    text.height_mm = 10.0
    text.parent_balloon_id = balloon.id
    balloon.text_id = text.id
    _sub("utils.text_balloon_link").fit_linked_balloon_to_text(text, balloon)
    balloon.linked_text_padding_x_mm = 4.0
    balloon.linked_text_padding_y_mm = 5.0
    _assert_fit(
        balloon,
        offset_x=0.0,
        offset_y=0.0,
        padding_x=4.0,
        padding_y=5.0,
    )


def _assert_tool_selector_switches_values(context, balloon) -> None:
    wm = context.window_manager
    wm.bmanga_balloon_tool_preset_selector = "custom:余白A"
    _assert_fit(
        balloon,
        offset_x=1.0,
        offset_y=-2.0,
        padding_x=2.0,
        padding_y=3.0,
    )
    wm.bmanga_balloon_tool_preset_selector = "custom:余白B"
    _assert_fit(
        balloon,
        offset_x=-4.0,
        offset_y=5.0,
        padding_x=7.0,
        padding_y=8.0,
    )


def _assert_new_balloon_refits_after_auto_link(context, page) -> None:
    text = page.texts.add()
    text.id = "new_balloon_auto_link_text"
    text.body = "自動リンク"
    text.x_mm = 40.0
    text.y_mm = 50.0
    text.width_mm = 30.0
    text.height_mm = 10.0

    context.window_manager.bmanga_balloon_tool_preset_selector = "custom:余白A"
    balloon_op = _sub("operators.balloon_op")
    tool_class = balloon_op.BMANGA_OT_balloon_tool

    class ToolProbe:
        _drag_page_for_create = tool_class._drag_page_for_create
        _clear_drag_state = tool_class._clear_drag_state
        _clear_tail_polyline_state = tool_class._clear_tail_polyline_state
        _push_undo_step = tool_class._push_undo_step

    operator = ToolProbe()
    operator._drag_moved = True
    operator._drag_page_id = str(page.id)
    operator._drag_start_x = 35.0
    operator._drag_start_y = 45.0
    operator._drag_last_x = 75.0
    operator._drag_last_y = 65.0
    operator._drag_parent_kind = "page"
    operator._drag_parent_key = ""
    tool_class._finish_create_preview(operator, context)

    balloon = page.balloons[-1]
    assert text.parent_balloon_id == balloon.id
    _assert_fit(
        balloon,
        offset_x=1.0,
        offset_y=-2.0,
        padding_x=2.0,
        padding_y=3.0,
        text_center_x=55.0,
        text_center_y=55.0,
    )


def _assert_detail_switch_refits(context, page, balloon) -> None:
    detail_preset_apply_op = _sub("operators.detail_preset_apply_op")
    detail_dialog = _sub("utils.detail_dialog")
    target = detail_dialog.DetailTarget(
        kind="balloon",
        stable_id=f"{page.id}:{balloon.id}",
        stack_uid=None,
        data=balloon,
    )
    assert detail_preset_apply_op.apply_preset_to_target(
        context,
        target,
        "balloon",
        "余白A",
    ) == "余白A"
    _assert_fit(
        balloon,
        offset_x=1.0,
        offset_y=-2.0,
        padding_x=2.0,
        padding_y=3.0,
    )


def _assert_preset_storage(context, page, balloon_presets, balloon) -> None:
    data = balloon_presets.load_preset_by_name("余白B").data
    assert float(data["linkedTextOffsetXMm"]) == -4.0
    assert float(data["linkedTextOffsetYMm"]) == 5.0
    assert float(data["linkedTextPaddingXMm"]) == 7.0
    assert float(data["linkedTextPaddingYMm"]) == 8.0
    snapshot = balloon_presets.linked_text_settings_from_entry(balloon)
    assert snapshot == {
        "linkedTextOffsetXMm": 1.0,
        "linkedTextOffsetYMm": -2.0,
        "linkedTextPaddingXMm": 2.0,
        "linkedTextPaddingYMm": 3.0,
    }
    detail_dialog = _sub("utils.detail_dialog")
    detail_preset_management_op = _sub("operators.detail_preset_management_op")
    target = detail_dialog.DetailTarget(
        kind="balloon",
        stable_id=f"{page.id}:{balloon.id}",
        stack_uid=None,
        data=balloon,
    )
    balloon.linked_text_offset_x_mm = 6.0
    balloon.linked_text_offset_y_mm = -7.0
    balloon.linked_text_padding_x_mm = 9.0
    balloon.linked_text_padding_y_mm = 10.0
    detail_preset_management_op._save_new_preset(
        context,
        "balloon",
        target,
        "余白保存確認",
        "",
    )
    stored = balloon_presets.load_preset_by_name("余白保存確認").data
    assert float(stored["linkedTextOffsetXMm"]) == 6.0
    assert float(stored["linkedTextOffsetYMm"]) == -7.0
    assert float(stored["linkedTextPaddingXMm"]) == 9.0
    assert float(stored["linkedTextPaddingYMm"]) == 10.0


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_linked_padding_"))
    old_config = os.environ.get("BMANGA_USER_CONFIG_DIR")
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(temp_root / "config")
    addon = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        addon = _load_addon()
        assert bpy.ops.bmanga.work_new(
            filepath=str(temp_root / "LinkedPadding.bmanga")
        ) == {"FINISHED"}
        assert bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0) == {"FINISHED"}
        context = bpy.context
        balloon_presets = _sub("io.balloon_presets")
        _save_preset(
            balloon_presets,
            "余白A",
            offset_x=1.0,
            offset_y=-2.0,
            padding_x=2.0,
            padding_y=3.0,
        )
        _save_preset(
            balloon_presets,
            "余白B",
            offset_x=-4.0,
            offset_y=5.0,
            padding_x=7.0,
            padding_y=8.0,
        )
        _work, page, balloon, text = _make_pair(context)
        _assert_direct_setting_refits(balloon, text)
        _assert_single_fallback_and_ambiguous_links(context, page)
        _assert_shared_pair_refits(context)
        _assert_tool_selector_switches_values(context, balloon)
        _assert_new_balloon_refits_after_auto_link(context, page)
        _assert_detail_switch_refits(context, page, balloon)
        _assert_preset_storage(context, page, balloon_presets, balloon)
        print("BMANGA_LINKED_BALLOON_PRESET_PADDING_OK")
    finally:
        if addon is not None:
            addon.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if old_config is None:
            os.environ.pop("BMANGA_USER_CONFIG_DIR", None)
        else:
            os.environ["BMANGA_USER_CONFIG_DIR"] = old_config
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
