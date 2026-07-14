"""Blender実機用: 詳細画面の子操作・キャンセル・プリセット独立境界。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_detail_transaction_action"
_BORDER_REFERENCE_TEST_PREFIX = "__detail_border_ref_"
_BALLOON_REFERENCE_TEST_PREFIX = "__detail_balloon_ref_"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[MOD_NAME] = module
    spec.loader.exec_module(module)
    module.register()
    return module


def _sub(path: str):
    __import__(f"{MOD_NAME}.{path}")
    return sys.modules[f"{MOD_NAME}.{path}"]


def _new_work(temp_root: Path):
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "DetailAction.bmanga"))
    assert "FINISHED" in result, result
    result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
    assert "FINISHED" in result, result
    work = _sub("core.work").get_work(bpy.context)
    assert work is not None and len(work.pages)
    return work, work.pages[int(work.active_page_index)]


def _new_balloon(page):
    entry = page.balloons.add()
    entry.id = f"detail_balloon_{len(page.balloons):04d}"
    entry.title = "子操作確認"
    entry.x_mm, entry.y_mm = 10.0, 10.0
    entry.width_mm, entry.height_mm = 30.0, 20.0
    return entry


def _new_text(page):
    entry = page.texts.add()
    entry.id = f"detail_text_{len(page.texts):04d}"
    entry.title = "ルビ確認"
    entry.body = "漢字"
    entry.x_mm, entry.y_mm = 10.0, 10.0
    entry.width_mm, entry.height_mm = 30.0, 20.0
    return entry


def _new_coma(page):
    entry = page.comas.add()
    entry.id = f"detail_coma_{len(page.comas):04d}"
    entry.coma_id = f"c{len(page.comas):02d}"
    entry.title = "枠線参照確認"
    entry.shape_type = "rect"
    entry.rect_width_mm = 80.0
    entry.rect_height_mm = 60.0
    entry.border.visible = True
    return entry


def _target(kind: str, page, entry):
    contract = _sub("utils.detail_dialog")
    return contract.DetailTarget(
        kind=kind,
        stable_id=f"{page.id}:{entry.id}",
        stack_uid=None,
        data=entry,
        params={"page": page, "page_id": str(page.id)},
    )


def _begin(target):
    runtime = _sub("operators.detail_dialog_runtime")
    return runtime.begin_actual_session(
        bpy.context,
        target,
        target_validator=lambda identity: identity.stable_id == target.stable_id,
    )


def _opening_preset_name(session):
    fragment = next(
        item
        for item in session.opening_snapshot.fragments
        if item.adapter.name == "preset_reference"
    )
    return str(fragment.payload.get("preset_name", "") or "")


def _assert_transaction_children_have_no_undo():
    module = _sub("operators.detail_transaction_action_op")
    classes = (
        module.BMANGA_OT_detail_tail_add,
        module.BMANGA_OT_detail_tail_remove,
        module.BMANGA_OT_detail_tail_preset_apply,
        module.BMANGA_OT_detail_text_linked_balloon_set,
        module.BMANGA_OT_detail_text_ruby_add,
        module.BMANGA_OT_detail_text_ruby_clear,
    )
    for cls in classes:
        assert set(cls.bl_options) == {"INTERNAL"}, (cls.bl_idname, cls.bl_options)


def _test_tail_cancel(page, entry):
    runtime = _sub("operators.detail_dialog_runtime")
    target = _target("balloon", page, entry)
    session = _begin(target)
    before = len(entry.tails)
    result = bpy.ops.bmanga.detail_tail_add(
        "EXEC_DEFAULT",
        session_token=session.token,
        target_id=target.stable_id,
        page_id=str(page.id),
        balloon_id=str(entry.id),
    )
    assert "FINISHED" in result and len(entry.tails) == before + 1
    wrong = bpy.ops.bmanga.detail_tail_add(
        "EXEC_DEFAULT",
        session_token=session.token,
        target_id=target.stable_id,
        page_id="wrong-page",
        balloon_id=str(entry.id),
    )
    assert "CANCELLED" in wrong and len(entry.tails) == before + 1
    runtime.cancel_actual_session(bpy.context, session)
    assert len(entry.tails) == before


def _test_tail_regeneration_failure_rolls_back(page, entry):
    runtime = _sub("operators.detail_dialog_runtime")
    actions = _sub("operators.detail_transaction_action_op")
    target = _target("balloon", page, entry)
    session = _begin(target)
    before = len(entry.tails)
    before_records = len(session.transactional_actions)
    original_sync = actions._sync_tail

    def fail_regeneration(*_args, **_kwargs):
        raise RuntimeError("forced tail regeneration failure")

    actions._sync_tail = fail_regeneration
    try:
        result = bpy.ops.bmanga.detail_tail_add(
            "EXEC_DEFAULT",
            session_token=session.token,
            target_id=target.stable_id,
            page_id=str(page.id),
            balloon_id=str(entry.id),
        )
        assert "CANCELLED" in result
        assert len(entry.tails) == before, "再生成失敗後に追加途中のしっぽが残りました"
        assert len(session.transactional_actions) == before_records, (
            "失敗したしっぽ操作が成功履歴へ記録されました"
        )
    finally:
        actions._sync_tail = original_sync
        runtime.cancel_actual_session(bpy.context, session)


def _test_ruby_cancel(page, entry):
    runtime = _sub("operators.detail_dialog_runtime")
    target = _target("text", page, entry)
    session = _begin(target)
    before = _sub("utils.text_style").ruby_spans_snapshot(entry)
    result = bpy.ops.bmanga.detail_text_ruby_add(
        "EXEC_DEFAULT",
        session_token=session.token,
        target_id=target.stable_id,
        page_id=str(page.id),
        text_id=str(entry.id),
        start=0,
        length=2,
        ruby_text="かんじ",
        style="group",
    )
    assert "FINISHED" in result
    assert _sub("utils.text_style").ruby_spans_snapshot(entry) != before
    runtime.cancel_actual_session(bpy.context, session)
    assert _sub("utils.text_style").ruby_spans_snapshot(entry) == before


def _test_linked_balloon_fixed_target_and_cancel(
    page,
    entry,
    parent_balloon,
    *,
    same_id_other_page_balloon=None,
):
    runtime = _sub("operators.detail_dialog_runtime")
    actions = _sub("operators.detail_transaction_action_op")
    presets = _sub("io.balloon_presets").list_all_presets(None)
    assert presets, "リンク先として選べるフキダシプリセットがありません"
    preset_name = str(presets[0].name)

    other = _new_text(page)
    other.title = "非アクティブ側"
    other.linked_balloon_preset = "非アクティブ維持"
    page.active_text_index = len(page.texts) - 1
    entry.parent_balloon_id = str(parent_balloon.id)
    entry.linked_balloon_preset = ""
    parent_balloon.shape = "rect"
    parent_balloon.custom_preset_name = ""
    opening_parent = (
        str(parent_balloon.shape),
        str(parent_balloon.custom_preset_name),
    )
    other_opening = None
    if same_id_other_page_balloon is not None:
        same_id_other_page_balloon.shape = "ellipse"
        same_id_other_page_balloon.custom_preset_name = "別ページ維持"
        other_opening = (
            str(same_id_other_page_balloon.shape),
            str(same_id_other_page_balloon.custom_preset_name),
        )

    target = _target("text", page, entry)
    session = _begin(target)
    result = bpy.ops.bmanga.detail_text_linked_balloon_set(
        "EXEC_DEFAULT",
        session_token=session.token,
        target_id=target.stable_id,
        preset_name=f"{actions._LINKED_BALLOON_PRESET_PREFIX}{preset_name}",
    )
    assert "FINISHED" in result
    assert str(entry.linked_balloon_preset) == preset_name
    assert str(other.linked_balloon_preset) == "非アクティブ維持", (
        "非アクティブな別テキストへリンク先が誤適用されました"
    )
    assert str(parent_balloon.shape) == "custom"
    assert str(parent_balloon.custom_preset_name) == preset_name
    if other_opening is not None:
        assert (
            str(same_id_other_page_balloon.shape),
            str(same_id_other_page_balloon.custom_preset_name),
        ) == other_opening, "同じIDの別ページフキダシへ誤適用されました"

    runtime.cancel_actual_session(bpy.context, session)
    assert str(entry.linked_balloon_preset) == ""
    assert str(other.linked_balloon_preset) == "非アクティブ維持"
    assert (
        str(parent_balloon.shape),
        str(parent_balloon.custom_preset_name),
    ) == opening_parent, "親キャンセルでリンク先フキダシが開始時状態へ戻りませんでした"
    if other_opening is not None:
        assert (
            str(same_id_other_page_balloon.shape),
            str(same_id_other_page_balloon.custom_preset_name),
        ) == other_opening, "キャンセル時に別ページフキダシが変更されました"


def _test_preset_regeneration_failure_rolls_back(page, entry):
    runtime = _sub("operators.detail_dialog_runtime")
    presets = _sub("io.text_presets")
    preset_action = _sub("operators.detail_preset_apply_op")
    text_object = _sub("utils.text_real_object")
    target = _target("text", page, entry)
    before = float(entry.line_height)
    name = f"__detail_failed_apply_{time.time_ns()}__"
    presets.save_local_preset(None, name, "", {"line_height": before + 1.25})
    session = _begin(target)
    before_records = len(session.transactional_actions)
    original_apply = preset_action._apply_text
    original_sync = text_object.on_text_entry_changed
    sync_calls = 0

    def fail_before_regeneration(_context, fixed_target, preset_name):
        preset = presets.load_preset_by_name(preset_name)
        assert preset is not None
        with text_object.suspend_auto_sync():
            presets.apply_to_entry(fixed_target.data, preset.data)
        raise RuntimeError("forced text regeneration failure")

    def count_resync(target_entry):
        nonlocal sync_calls
        sync_calls += 1
        return original_sync(target_entry)

    preset_action._apply_text = fail_before_regeneration
    text_object.on_text_entry_changed = count_resync
    try:
        result = bpy.ops.bmanga.detail_preset_apply(
            "EXEC_DEFAULT",
            session_token=session.token,
            preset_type="text",
            preset_name=name,
            target_kind="text",
            target_id=target.stable_id,
            stable_id=target.stable_id,
        )
        assert "CANCELLED" in result
        assert abs(float(entry.line_height) - before) < 1.0e-6, (
            "プリセット再生成失敗後に適用途中の値が残りました"
        )
        assert len(session.transactional_actions) == before_records, (
            "失敗したプリセット適用が成功履歴へ記録されました"
        )
        assert sync_calls >= 1, "失敗後に表示実体の再同期が実行されませんでした"
    finally:
        preset_action._apply_text = original_apply
        text_object.on_text_entry_changed = original_sync
        runtime.cancel_actual_session(bpy.context, session)


def _preset_op_fields(session, target, name: str, preset_type: str = "text") -> dict:
    return {
        "session_token": session.token,
        "target_kind": target.kind,
        "target_id": target.stable_id,
        "preset_type": preset_type,
        "preset_name": name,
    }


def _cleanup_reference_test_presets(work) -> None:
    border_presets = _sub("io.border_presets")
    balloon_presets = _sub("io.balloon_presets")
    work_dir = Path(work.work_dir)
    for preset in tuple(border_presets.list_all_presets(work_dir)):
        if str(preset.name).startswith(_BORDER_REFERENCE_TEST_PREFIX):
            border_presets.delete_preset(work_dir, str(preset.name))
    for preset in tuple(balloon_presets.list_all_presets(work_dir)):
        if str(preset.name).startswith(_BALLOON_REFERENCE_TEST_PREFIX):
            balloon_presets.delete_preset(str(preset.name))


def _test_preset_json_survives_parent_cancel(page, entry):
    runtime = _sub("operators.detail_dialog_runtime")
    management = _sub("operators.detail_preset_management_op")
    presets = _sub("io.text_presets")
    target = _target("text", page, entry)
    session = _begin(target)
    original_selector = str(bpy.context.window_manager.bmanga_text_tool_preset_selector or "")
    base = f"__detail_action_{time.time_ns()}__"
    result = bpy.ops.bmanga.detail_preset_add(
        "EXEC_DEFAULT",
        **_preset_op_fields(session, target, base),
    )
    assert "FINISHED" in result
    added = str(session.preset_selection or "")
    assert added and presets.load_preset_by_name(added) is not None
    draft = management.detail_preset_draft(bpy.context, session, "text", added)
    assert draft is not None
    renamed = f"{added}_renamed"
    draft.rename_name = renamed
    result = bpy.ops.bmanga.detail_preset_rename(
        "EXEC_DEFAULT",
        **_preset_op_fields(session, target, added),
        new_name=draft.rename_name,
    )
    assert "FINISHED" in result and session.preset_selection == renamed

    # 親ダイアログ中は子props dialogを開かず、現在表示中の設定を
    # 選択プリセットへ即時上書きする。
    entry.font_size_value = 37.0
    result = bpy.ops.bmanga.preset_detail_edit(
        "INVOKE_DEFAULT",
        preset_type="text",
        preset_name=renamed,
        parent_session_token=session.token,
        parent_target_kind=target.kind,
        parent_target_id=target.stable_id,
    )
    assert "FINISHED" in result
    overwritten = presets.load_preset_by_name(renamed)
    assert overwritten is not None
    assert abs(float(overwritten.data.get("font_size_value", 0.0)) - 37.0) < 1.0e-6

    draft = management.detail_preset_draft(bpy.context, session, "text", renamed)
    duplicated = f"{renamed}_copy"
    draft.duplicate_name = duplicated
    result = bpy.ops.bmanga.detail_preset_duplicate(
        "EXEC_DEFAULT",
        **_preset_op_fields(session, target, renamed),
        new_name=draft.duplicate_name,
    )
    assert "FINISHED" in result
    duplicated = str(session.preset_selection or "")
    assert presets.load_preset_by_name(duplicated) is not None

    runtime.cancel_actual_session(bpy.context, session)
    assert presets.load_preset_by_name(renamed) is not None
    assert presets.load_preset_by_name(duplicated) is not None
    assert str(bpy.context.window_manager.bmanga_text_tool_preset_selector or "") == original_selector

    delete_session = _begin(target)
    result = bpy.ops.bmanga.detail_preset_delete(
        "EXEC_DEFAULT",
        **_preset_op_fields(delete_session, target, renamed),
    )
    assert "FINISHED" in result and delete_session.preset_selection is None
    result = bpy.ops.bmanga.detail_preset_delete(
        "EXEC_DEFAULT",
        **_preset_op_fields(delete_session, target, duplicated),
    )
    assert "FINISHED" in result
    runtime.cancel_actual_session(bpy.context, delete_session)
    assert presets.load_preset_by_name(renamed) is None
    assert presets.load_preset_by_name(duplicated) is None


def _test_border_reference_survives_preset_management(work, page):
    runtime = _sub("operators.detail_dialog_runtime")
    presets = _sub("io.border_presets")
    work_dir = Path(work.work_dir)
    entry = _new_coma(page)
    unrelated = _new_coma(page)
    old_name = f"{_BORDER_REFERENCE_TEST_PREFIX}{time.time_ns()}__"
    new_name = f"{old_name}_renamed"
    presets.save_local_preset(work_dir, entry, old_name, "")
    entry.border.preset_name = old_name
    unrelated.border.preset_name = old_name

    session = _begin(_target("coma", page, entry))
    opening_width = float(entry.border.width_mm)
    entry.border.width_mm = opening_width + 1.0
    result = bpy.ops.bmanga.detail_preset_rename(
        "EXEC_DEFAULT",
        **_preset_op_fields(session, session.target, old_name, "border"),
        new_name=new_name,
    )
    assert "FINISHED" in result and entry.border.preset_name == new_name
    assert unrelated.border.preset_name == old_name
    assert _opening_preset_name(session) == new_name
    runtime.cancel_actual_session(bpy.context, session)
    assert entry.border.preset_name == new_name
    assert abs(float(entry.border.width_mm) - opening_width) < 1.0e-6

    session = _begin(_target("coma", page, entry))
    entry.border.width_mm = opening_width + 2.0
    result = bpy.ops.bmanga.detail_preset_delete(
        "EXEC_DEFAULT",
        **_preset_op_fields(session, session.target, new_name, "border"),
    )
    assert "FINISHED" in result and entry.border.preset_name == ""
    assert unrelated.border.preset_name == old_name
    runtime.cancel_actual_session(bpy.context, session)
    assert entry.border.preset_name == ""
    assert abs(float(entry.border.width_mm) - opening_width) < 1.0e-6


def _test_balloon_reference_survives_preset_management(work, page, entry):
    runtime = _sub("operators.detail_dialog_runtime")
    presets = _sub("io.balloon_presets")
    unrelated = _new_balloon(page)
    old_name = f"{_BALLOON_REFERENCE_TEST_PREFIX}{time.time_ns()}__"
    new_name = f"{old_name}_renamed"
    vertices = [(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)]
    presets.save_local_preset(Path(work.work_dir), old_name, "", vertices, False)
    entry.shape = "custom"
    entry.custom_preset_name = old_name
    entry.custom_outline_json = ""
    unrelated.custom_preset_name = old_name

    session = _begin(_target("balloon", page, entry))
    opening_width = float(entry.width_mm)
    entry.width_mm = opening_width + 1.0
    result = bpy.ops.bmanga.detail_preset_rename(
        "EXEC_DEFAULT",
        **_preset_op_fields(session, session.target, old_name, "balloon"),
        new_name=new_name,
    )
    assert "FINISHED" in result and entry.custom_preset_name == new_name
    assert unrelated.custom_preset_name == old_name
    assert _opening_preset_name(session) == new_name
    runtime.cancel_actual_session(bpy.context, session)
    assert entry.custom_preset_name == new_name
    assert abs(float(entry.width_mm) - opening_width) < 1.0e-6

    session = _begin(_target("balloon", page, entry))
    opening_height = float(entry.height_mm)
    entry.height_mm = opening_height + 2.0
    result = bpy.ops.bmanga.detail_preset_delete(
        "EXEC_DEFAULT",
        **_preset_op_fields(session, session.target, new_name, "balloon"),
    )
    cached_outline = json.loads(str(entry.custom_outline_json or "[]"))
    assert "FINISHED" in result and entry.custom_preset_name == ""
    assert len(cached_outline) >= 3 and unrelated.custom_preset_name == old_name
    runtime.cancel_actual_session(bpy.context, session)
    assert entry.custom_preset_name == ""
    assert json.loads(str(entry.custom_outline_json or "[]")) == cached_outline
    assert abs(float(entry.height_mm) - opening_height) < 1.0e-6


def _test_unregister_cleanup_restores_and_releases(page, balloon, text):
    runtime = _sub("operators.detail_dialog_runtime")
    state = _sub("utils.detail_dialog_state")
    adapters = _sub("utils.detail_state_adapters")
    contract = _sub("utils.detail_dialog")
    curves = _sub("utils.effect_inout_curve")
    balloon.line_style = "uni_flash"
    actual = _begin(_target("balloon", page, balloon))
    preset_target = _target("text", page, text)
    preset = state.begin_detail_session(
        preset_target,
        contract.DetailMode.PRESET,
        registry=adapters.ACTUAL_DETAIL_STATE_REGISTRY,
        target_validator=lambda identity: identity.stable_id == preset_target.stable_id,
    )
    runtime.register_preset_session(preset)
    opening_width = float(balloon.width_mm)
    opening_font_size = float(text.font_size_value)
    balloon.width_mm = opening_width + 4.0
    text.font_size_value = opening_font_size + 5.0
    runtime._PREPARING_EFFECT_TARGET_IDS.add("detail-cleanup-probe")
    assert any(request[0] is balloon for request in curves._LIVE_PROFILE_REQUESTS.values())

    failures = runtime.cleanup_all_sessions(bpy.context)

    assert failures == ()
    assert actual.status is contract.DetailSessionStatus.CANCELLED
    assert preset.status is contract.DetailSessionStatus.CANCELLED
    assert abs(float(balloon.width_mm) - opening_width) < 1.0e-6
    assert abs(float(text.font_size_value) - opening_font_size) < 1.0e-6
    assert not runtime._OPEN_ACTUAL_SESSIONS
    assert not runtime._OPEN_ACTUAL_SCENE_KEYS
    assert not runtime._OPEN_PRESET_SESSIONS
    assert not runtime._PREPARING_EFFECT_TARGET_IDS
    assert not any(request[0] is balloon for request in curves._LIVE_PROFILE_REQUESTS.values())


def _test_balloon_preset_keeps_the_actual_outline(page, entry):
    runtime = _sub("operators.detail_dialog_runtime")
    presets = _sub("io.balloon_presets")
    entry.shape = "ellipse"
    target = _target("balloon", page, entry)
    session = _begin(target)
    name = f"__detail_balloon_outline_{time.time_ns()}__"
    result = bpy.ops.bmanga.detail_preset_add(
        "EXEC_DEFAULT",
        **_preset_op_fields(session, target, name, "balloon"),
    )
    assert "FINISHED" in result
    selected = str(session.preset_selection or "")
    saved = presets.load_preset_by_name(selected)
    assert saved is not None
    assert len(saved.data.get("vertices", ())) > 4, "楕円輪郭が矩形4点へ劣化しました"
    runtime.cancel_actual_session(bpy.context, session)
    assert presets.load_preset_by_name(selected) is not None

    cleanup = _begin(target)
    assert "FINISHED" in bpy.ops.bmanga.detail_preset_delete(
        "EXEC_DEFAULT",
        **_preset_op_fields(cleanup, target, selected, "balloon"),
    )
    runtime.cancel_actual_session(bpy.context, cleanup)


def main():
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_detail_transaction_"))
    addon = None
    work = None
    try:
        addon = _load_addon()
        _assert_transaction_children_have_no_undo()
        work, page = _new_work(temp_root)
        _cleanup_reference_test_presets(work)
        balloon = _new_balloon(page)
        _test_tail_cancel(page, balloon)
        _test_tail_regeneration_failure_rolls_back(page, balloon)
        _test_balloon_preset_keeps_the_actual_outline(page, balloon)
        _test_border_reference_survives_preset_management(work, page)
        _test_balloon_reference_survives_preset_management(work, page, balloon)
        text = _new_text(page)
        second_page = work.pages.add()
        second_page.id = "p0002"
        second_page.title = "2ページ"
        second_balloon = _new_balloon(second_page)
        second_text = _new_text(second_page)
        assert second_balloon.id == balloon.id, "テスト前提のページ内ID重複を作れませんでした"
        _test_linked_balloon_fixed_target_and_cancel(
            second_page,
            second_text,
            second_balloon,
            same_id_other_page_balloon=balloon,
        )
        _test_ruby_cancel(page, text)
        _test_preset_regeneration_failure_rolls_back(page, text)
        _test_preset_json_survives_parent_cancel(page, text)
        _test_unregister_cleanup_restores_and_releases(page, balloon, text)
        print("PASS: 詳細画面の子操作・キャンセル・独立プリセット管理")
    finally:
        if addon is not None and work is not None:
            try:
                _cleanup_reference_test_presets(work)
            except Exception as exc:
                print(f"WARN: テスト用プリセットの後始末に失敗しました: {exc}")
        if addon is not None:
            addon.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
