"""Blender 実機用: 縦書きテキストツールのカーソル消失リグレッション回帰確認.

背景 (2026-07-12 調査で確定):
  - operators/preset_op.py._on_text_preset_selector_change は、ツール実行中に
    テキストプリセットを切り替えると「op が取得できてもできなくても」
    window.cursor_modal_set("NONE") を直書きで呼んでいた (v0.6.322 の直し漏れ)。
  - v0.6.492〜497 のプリセットUIリニューアル (UIList化等) がすべて
    wm.bmanga_text_tool_preset_selector への代入経由でこのコールバックを
    発火させるため、縦書きプリセット選択中にマウスカーソルが消えたまま
    復帰しない不具合が容易に再現していた。

この回帰テストは以下を確認する:
  1. 縦書きプリセット選択状態で BMANGA_OT_text_tool._apply_tool_cursor を
     通すと、描画ハンドラが登録され OS カーソルが "NONE" になる。
  2. 横書きプリセットへ切り替えると、描画ハンドラが除去され OS カーソルが
     text_tool_cursor_type の返す値 (通常 "TEXT") になる。
  3. 描画ハンドラの登録自体が失敗した場合は "NONE" ではなく "CROSSHAIR" に
     フォールバックし、カーソルが消えたままにならない。
  4. ツール実行中のプリセット切替 (preset_op._on_text_preset_selector_change
     経由、coma_modal_state.set_active でダミー op を登録して発火) でも
     1/2 の性質が壊れず、"NONE" だけ残ってハンドラ未登録という消失状態に
     ならない。また op が取得できない異常系では set_modal_cursor 自体が
     一切呼ばれない。
  5. coma_modal_state.sync_modal_cursor_for_event_region 相当で、VIEW_3D
     外イベント時に cursor_modal_restore が呼ばれ、VIEW_3D 内イベントで
     ツールカーソルが再設定される。
  6. _editing 中 (インライン編集中) でも MOUSEMOVE で縦書きカスタムカーソル
     の描画座標 (_vcur_x/_vcur_y) が更新される。ビューポート外では -1 にして
     非表示化する。
  7. オブジェクト右クリックの詳細設定ダイアログ開始中は通常カーソルへ戻し、
     OK・キャンセル・開始失敗の全終了経路でテキストツールのカーソルへ復帰する。

実行:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" \
      --background --python test\\blender_text_vertical_cursor_check.py
"""

from __future__ import annotations

import importlib
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_dev_text_vertical_cursor"

FAILURES: list[str] = []


def _check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)
        print(f"NG: {message}", flush=True)
    else:
        print(f"ok: {message}", flush=True)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _sub(path: str):
    return importlib.import_module(f"{MOD_NAME}.{path}")


class _CursorWindow:
    """cursor_modal_set/restore の呼び出しを記録するダミー window."""

    def __init__(self) -> None:
        self.set_calls: list[str] = []
        self.restore_count = 0

    def cursor_modal_set(self, cursor: str) -> None:
        self.set_calls.append(str(cursor))

    def cursor_modal_restore(self) -> None:
        self.restore_count += 1


class _CursorContext:
    def __init__(self, window) -> None:
        self.window = window


_TEXT_TOOL_CURSOR_METHODS = (
    "_apply_tool_cursor",
    "_setup_vertical_cursor",
    "_remove_vcur_handler",
    "_update_vcur_position",
    "_tag_redraw_vcur",
)


def _make_cursor_probe(text_op):
    """BMANGA_OT_text_tool からカーソル制御メソッドだけを実体そのまま移植したダミー.

    bpy Operator は register_class 経由でないとインスタンス化できないため、
    素の Python クラスへ同じ関数オブジェクト (クラス辞書の参照) を割り当てて
    振る舞いを検証する。ロジックを書き写すのではなく実コードを直接動かす
    ことで、実装変更に対する追随漏れを防ぐ。
    """
    cls = text_op.BMANGA_OT_text_tool
    attrs = {name: cls.__dict__[name] for name in _TEXT_TOOL_CURSOR_METHODS}
    probe_cls = type("_TextToolCursorProbe", (), attrs)
    probe = probe_cls()
    probe._vcur_draw_handler = None
    probe._vcur_x = -1
    probe._vcur_y = -1
    probe._cursor_modal_set = False
    probe._tool_cursor = ""
    return probe


def _find_preset_names(text_presets_mod, work) -> tuple[str, str]:
    work_dir_str = str(getattr(work, "work_dir", "") or "")
    work_dir = Path(work_dir_str) if work_dir_str else None
    presets = text_presets_mod.list_all_presets(work_dir)
    vertical = next((p.name for p in presets if p.data.get("writing_mode") == "vertical"), "")
    horizontal = next((p.name for p in presets if p.data.get("writing_mode") != "vertical"), "")
    return vertical, horizontal


def _check_apply_tool_cursor_vertical_then_horizontal(
    text_op, preset_op, wm, context, vertical_name: str, horizontal_name: str
) -> None:
    """1, 2: 縦書き<->横書きの _apply_tool_cursor 切替でカーソル状態が一貫すること."""
    probe = _make_cursor_probe(text_op)
    try:
        wm.bmanga_text_tool_preset_selector = vertical_name
        probe._apply_tool_cursor(context)
        _check(probe._vcur_draw_handler is not None, "縦書き: 描画ハンドラが登録される")
        _check(probe._tool_cursor == "NONE", "縦書き: ツールカーソルが NONE になる")
        _check(bool(probe._cursor_modal_set), "縦書き: cursor_modal_set が成功する")

        wm.bmanga_text_tool_preset_selector = horizontal_name
        probe._apply_tool_cursor(context)
        expected = preset_op.text_tool_cursor_type(context)
        _check(probe._vcur_draw_handler is None, "横書き: 描画ハンドラが除去される")
        _check(probe._tool_cursor == expected, f"横書き: ツールカーソルが {expected!r} になる (実際: {probe._tool_cursor!r})")
        _check(probe._vcur_x == -1 and probe._vcur_y == -1, "横書き: vcur 座標が非表示値 (-1) になる")
    finally:
        probe._remove_vcur_handler()


def _check_handler_registration_failure_falls_back_to_crosshair(
    text_op, wm, context, vertical_name: str
) -> None:
    """3: 描画ハンドラ登録が失敗しても NONE のまま残らず CROSSHAIR にフォールバックする."""
    probe = _make_cursor_probe(text_op)
    wm.bmanga_text_tool_preset_selector = vertical_name

    original_add = bpy.types.SpaceView3D.draw_handler_add

    def _boom(*_args, **_kwargs):
        raise RuntimeError("forced failure for regression test")

    try:
        bpy.types.SpaceView3D.draw_handler_add = _boom
        probe._apply_tool_cursor(context)
    finally:
        bpy.types.SpaceView3D.draw_handler_add = original_add
        probe._remove_vcur_handler()

    _check(probe._vcur_draw_handler is None, "ハンドラ登録失敗: ハンドラは None のまま (例外が伝播しない)")
    _check(probe._tool_cursor == "CROSSHAIR", f"ハンドラ登録失敗: CROSSHAIR にフォールバックする (実際: {probe._tool_cursor!r})")
    _check(probe._tool_cursor != "NONE", "ハンドラ登録失敗: NONE のまま消失しない")


def _check_runtime_preset_switch_via_callback(
    text_op, coma_modal_state, wm, vertical_name: str, horizontal_name: str
) -> None:
    """4a: ツール実行中のプリセット切替 (preset_op._on_text_preset_selector_change
    経由) でも 1/2 の性質が壊れないこと."""
    probe = _make_cursor_probe(text_op)
    # ベースラインを横書きにしておく (この時点では is_active が False なので
    # コールバックは probe に触れない)。
    wm.bmanga_text_tool_preset_selector = horizontal_name

    coma_modal_state.set_active("text_tool", probe)
    try:
        wm.bmanga_text_tool_preset_selector = vertical_name
        _check(probe._vcur_draw_handler is not None, "実行中切替(横→縦): ハンドラが登録される")
        _check(probe._tool_cursor == "NONE", "実行中切替(横→縦): ツールカーソルが NONE になる")

        wm.bmanga_text_tool_preset_selector = horizontal_name
        _check(probe._vcur_draw_handler is None, "実行中切替(縦→横): ハンドラが除去される")
        _check(probe._tool_cursor != "NONE", "実行中切替(縦→横): NONE のまま残らない (消失しない)")
    finally:
        coma_modal_state.clear_active("text_tool", probe)
        probe._remove_vcur_handler()


def _check_runtime_preset_switch_without_active_op(
    coma_modal_state, wm, vertical_name: str
) -> None:
    """4b: is_active は True だが get_active が None を返す異常系:
    set_modal_cursor が一切呼ばれず、"NONE" だけが残る消失状態にならないこと."""
    original_is_active = coma_modal_state.is_active
    original_get_active = coma_modal_state.get_active
    original_set_cursor = coma_modal_state.set_modal_cursor
    calls: list[str] = []

    def _tracking_set_modal_cursor(_context, cursor):
        calls.append(str(cursor))
        return True

    try:
        coma_modal_state.is_active = lambda name: name == "text_tool"
        coma_modal_state.get_active = lambda name: None
        coma_modal_state.set_modal_cursor = _tracking_set_modal_cursor
        wm.bmanga_text_tool_preset_selector = vertical_name
    finally:
        coma_modal_state.is_active = original_is_active
        coma_modal_state.get_active = original_get_active
        coma_modal_state.set_modal_cursor = original_set_cursor

    _check(not calls, f"op 未取得時は set_modal_cursor を呼ばない (実際の呼び出し: {calls!r})")


def _check_sync_modal_cursor_for_event_region(coma_modal_state, view_event_region) -> None:
    """5: VIEW_3D 外イベントで一時復帰し、VIEW_3D 内イベントで再設定される."""

    class _CursorOp:
        _cursor_modal_set = True
        _cursor_temporarily_restored = False
        _tool_cursor = "NONE"

    window = _CursorWindow()
    context = _CursorContext(window)
    op = _CursorOp()
    original = view_event_region.is_view3d_window_event
    try:
        view_event_region.is_view3d_window_event = lambda _c, _e: False
        coma_modal_state.sync_modal_cursor_for_event_region(context, object(), op, op._tool_cursor)
        _check(window.restore_count == 1, "ビューポート外: cursor_modal_restore が呼ばれる")
        _check(op._cursor_modal_set is False, "ビューポート外: _cursor_modal_set が False になる")
        _check(op._cursor_temporarily_restored is True, "ビューポート外: 一時復帰フラグが立つ")

        view_event_region.is_view3d_window_event = lambda _c, _e: True
        coma_modal_state.sync_modal_cursor_for_event_region(context, object(), op, op._tool_cursor)
        _check(window.set_calls[-1:] == ["NONE"], "ビューポート内復帰: ツールカーソル (NONE) が再設定される")
        _check(op._cursor_modal_set is True, "ビューポート内復帰: _cursor_modal_set が True になる")
        _check(op._cursor_temporarily_restored is False, "ビューポート内復帰: 一時復帰フラグが解除される")
    finally:
        view_event_region.is_view3d_window_event = original


def _check_vcur_position_follows_mouse_even_while_editing(text_op, view_event_region) -> None:
    """6: _editing 中でも MOUSEMOVE で _vcur 座標が更新され、ビューポート外では非表示化する.

    text_op.modal() 側の「_editing でも vcur 更新を止めない」変更は実際の
    modal ループでしか確認できないため、ここでは更新ロジック本体である
    _update_vcur_position を直接検証する (_editing フラグの値に関係なく
    座標更新自体が機能することを保証すれば、modal() 側の呼び出し条件から
    _editing の絞り込みを外した効果は担保できる)。
    """
    probe = _make_cursor_probe(text_op)
    probe._vcur_draw_handler = object()  # ハンドラ登録済み相当 (実際の値は使わない)

    class _Ctx:
        screen = None

    class _Event:
        type = "MOUSEMOVE"

    original = view_event_region.view3d_window_under_event
    try:
        view_event_region.view3d_window_under_event = lambda _c, _e: (None, None, None, 123, 45)
        probe._update_vcur_position(_Ctx(), _Event())
        _check(probe._vcur_x == 123 and probe._vcur_y == 45, "ビューポート内: region ローカル座標が反映される (編集中フラグに依存しない)")

        view_event_region.view3d_window_under_event = lambda _c, _e: None
        probe._update_vcur_position(_Ctx(), _Event())
        _check(probe._vcur_x == -1 and probe._vcur_y == -1, "ビューポート外: -1 にして非表示化する")
    finally:
        view_event_region.view3d_window_under_event = original


def _check_right_click_detail_dialog_cursor_lifecycle(layer_detail_op, coma_modal_state, context) -> None:
    """7: 右クリック詳細画面の全終了経路でカーソル上書きを解除する。"""

    class _ActiveTextTool:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def begin_dialog_cursor_override(self, _context) -> None:
            self.calls.append("begin")

        def end_dialog_cursor_override(self, _context) -> None:
            self.calls.append("end")

    active = _ActiveTextTool()
    operator_cls = layer_detail_op.BMANGA_OT_layer_detail_open
    probe_cls = type(
        "_LayerDetailCursorProbe",
        (),
        {
            name: operator_cls.__dict__[name]
            for name in (
                "_set_text_dialog_cursor_override",
                "_abort_opening_session",
                "cancel",
                "execute",
            )
        },
    )
    coma_modal_state.set_active("text_tool", active, context)
    try:
        # 開始失敗／invoke_props_dialog の CANCELLED 経路。
        op = probe_cls()
        op._detail_session = None
        op._text_dialog_cursor_override = False
        op._set_text_dialog_cursor_override(context, True)
        op._abort_opening_session(context)
        _check(active.calls == ["begin", "end"], f"詳細画面開始中止: begin/end が対になる (実際: {active.calls!r})")

        # ダイアログのキャンセル経路。
        active.calls.clear()
        op = probe_cls()
        op._detail_session = None
        op._text_dialog_cursor_override = False
        op._set_text_dialog_cursor_override(context, True)
        op.cancel(context)
        _check(active.calls == ["begin", "end"], f"詳細画面キャンセル: begin/end が対になる (実際: {active.calls!r})")

        # OK押下後、対象が既に無い異常終了でもカーソルだけは必ず復帰する。
        active.calls.clear()
        op = probe_cls()
        op._detail_session = None
        op._text_dialog_cursor_override = False
        op._set_text_dialog_cursor_override(context, True)
        result = op.execute(context)
        _check("CANCELLED" in result, "詳細画面の対象消失時は安全にCANCELLEDとなる")
        _check(active.calls == ["begin", "end"], f"詳細画面OK異常終了: begin/end が対になる (実際: {active.calls!r})")
    finally:
        coma_modal_state.clear_active("text_tool", active, context)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_text_vertical_cursor_"))
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "TextVerticalCursor.bmanga"))
        assert "FINISHED" in result, result

        context = bpy.context
        wm = context.window_manager
        work = context.scene.bmanga_work

        text_op = _sub("operators.text_op")
        layer_detail_op = _sub("operators.layer_detail_op")
        preset_op = _sub("operators.preset_op")
        coma_modal_state = _sub("operators.coma_modal_state")
        view_event_region = _sub("operators.view_event_region")
        text_presets = _sub("io.text_presets")

        vertical_name, horizontal_name = _find_preset_names(text_presets, work)
        assert vertical_name, "縦書きプリセットが見つかりません (presets/text/ を確認してください)"
        assert horizontal_name, "横書きプリセットが見つかりません (presets/text/ を確認してください)"
        print(f"PRESETS vertical={vertical_name!r} horizontal={horizontal_name!r}", flush=True)

        _check_apply_tool_cursor_vertical_then_horizontal(
            text_op, preset_op, wm, context, vertical_name, horizontal_name
        )
        _check_handler_registration_failure_falls_back_to_crosshair(text_op, wm, context, vertical_name)
        _check_runtime_preset_switch_via_callback(
            text_op, coma_modal_state, wm, vertical_name, horizontal_name
        )
        _check_runtime_preset_switch_without_active_op(coma_modal_state, wm, vertical_name)
        _check_sync_modal_cursor_for_event_region(coma_modal_state, view_event_region)
        _check_vcur_position_follows_mouse_even_while_editing(text_op, view_event_region)
        _check_right_click_detail_dialog_cursor_lifecycle(layer_detail_op, coma_modal_state, context)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        shutil.rmtree(temp_root, ignore_errors=True)

    if FAILURES:
        for message in FAILURES:
            print(f"FAIL: {message}", flush=True)
        raise AssertionError(f"{len(FAILURES)} 件の検証に失敗しました")
    print("BMANGA_TEXT_VERTICAL_CURSOR_OK", flush=True)


if __name__ == "__main__":
    try:
        main()
        import os

        os._exit(0)
    except Exception:
        import traceback

        traceback.print_exc()
        import os

        os._exit(1)
