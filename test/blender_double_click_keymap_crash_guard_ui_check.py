"""Blender UI実機: ダブルクリックのページ開きとキーマップ更新の競合を反復検証.

2026-07-22 実測クラッシュ (EXCEPTION_ACCESS_VIOLATION: WM_keyconfig_update_ex →
WM_keymap_clear → WM_operator_properties_free → IDP_FreeProperty) の再発防止。

検証項目:
1. B-MANGA キーマップに「プロパティ入り kmi」が 1 つも無いこと
   (wm.call_asset_shelf_popover / view_zoom_step の direction 書き込みの根絶)
2. schedule_open_page_file / schedule_enter_coma_mode がクリックイベント内で
   kmi.active を書き換えないこと (キーマップ再構築の誘発をタイマー側へ遅延)
3. ダブルクリック相当のページ開き→作品ファイルへ戻るを反復し、
   キーマップ強制更新を挟んでも新規クラッシュログが出ないこと
4. プロパティ無し化したズーム/アセットシェルフのオペレーターが解決できること
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
MOD_NAME = "bmanga_double_click_keymap_crash_guard_ui"
ITERATIONS = 10
_state = {
    "addon": None,
    "keymap": None,
    "mode_op": None,
    "work_dir": None,
    "completed": 0,
    "crashes_before": set(),
    "waiting_ticks": 0,
}


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.register()
    return module


def _crash_logs() -> set[tuple[str, int]]:
    temp = Path(tempfile.gettempdir())
    result = set()
    for path in temp.glob("*.crash.txt"):
        try:
            result.add((str(path.resolve()), path.stat().st_mtime_ns))
        except OSError:
            pass
    return result


def _fail(exc: BaseException) -> None:
    print("BMANGA_DOUBLE_CLICK_KEYMAP_CRASH_GUARD_ERROR", flush=True)
    traceback.print_exception(type(exc), exc, exc.__traceback__)
    os._exit(1)


def _kmi_stored_property_names(kmi) -> list[str]:
    """kmi.properties に実際に保存された (=IDProperty化した) プロパティ名."""
    props = getattr(kmi, "properties", None)
    if props is None:
        return []
    names: list[str] = []
    try:
        for name in props.keys():
            names.append(str(name))
    except Exception:
        # keys() が無い場合は RNA 定義プロパティの is_property_set で代替
        try:
            for prop in props.bl_rna.properties:
                if prop.identifier == "rna_type":
                    continue
                if props.is_property_set(prop.identifier):
                    names.append(str(prop.identifier))
        except Exception:
            pass
    return names


def _check_no_property_kmis() -> None:
    """B-MANGA キーマップにプロパティ入り kmi が無いことを機械検証."""
    keymap = _state["keymap"]
    kstate = keymap.get_state()
    assert kstate is not None and kstate.bmanga_items, "B-MANGA キーマップ未作成"
    offenders: list[str] = []
    for kmi in kstate.bmanga_items:
        try:
            idname = str(getattr(kmi, "idname", "") or "")
        except ReferenceError:
            continue
        if idname == "wm.call_asset_shelf_popover":
            offenders.append(f"{idname} (直接登録は禁止)")
            continue
        stored = _kmi_stored_property_names(kmi)
        if stored:
            offenders.append(f"{idname}: {stored}")
    assert not offenders, f"プロパティ入り kmi が残っています: {offenders}"
    print("NO_PROPERTY_KMI_OK", f"items={len(kstate.bmanga_items)}", flush=True)


def _check_zoom_and_shelf_operators() -> None:
    """プロパティ無し化した代替オペレーターが解決・実行できること."""
    assert hasattr(bpy.ops.bmanga, "view_zoom_step_in")
    assert hasattr(bpy.ops.bmanga, "view_zoom_step_out")
    assert hasattr(bpy.ops.bmanga, "toggle_asset_shelf")
    view = _view3d_context()
    assert view is not None, "VIEW_3D が見つかりません"
    window, screen, area, region, space, rv3d = view
    with bpy.context.temp_override(
        window=window, screen=screen, area=area, region=region,
        space_data=space, region_data=rv3d,
    ):
        before = float(rv3d.view_distance)
        result_in = bpy.ops.bmanga.view_zoom_step_in("EXEC_DEFAULT")
        after_in = float(rv3d.view_distance)
        result_out = bpy.ops.bmanga.view_zoom_step_out("EXEC_DEFAULT")
        assert result_in == {"FINISHED"}, result_in
        assert result_out == {"FINISHED"}, result_out
        assert after_in < before, f"ズームインで view_distance が縮んでいません: {before} -> {after_in}"
    print("ZOOM_SHELF_OPERATORS_OK", flush=True)


def _view3d_context():
    windows = list(bpy.context.window_manager.windows)
    current = getattr(bpy.context, "window", None)
    if current is not None:
        windows = [current, *[w for w in windows if w != current]]
    for window in windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            space = area.spaces.active
            rv3d = getattr(space, "region_3d", None)
            if region is not None and rv3d is not None:
                return window, screen, area, region, space, rv3d
    return None


def _active_bmanga_items() -> int:
    keymap = _state["keymap"]
    kstate = keymap.get_state()
    count = 0
    for item in kstate.bmanga_items:
        try:
            if bool(getattr(item, "active", False)):
                count += 1
        except ReferenceError:
            pass
    return count


def _check_schedule_keeps_kmi_active() -> None:
    """schedule_* がクリックイベント内で kmi.active を触らないこと."""
    keymap = _state["keymap"]
    mode_op = _state["mode_op"]
    kstate = keymap.get_state()
    kstate.set_bmanga_items_active(True)
    active_before = _active_bmanga_items()
    assert active_before > 0, "検証前提: B-MANGA キーが有効化できません"

    assert mode_op.schedule_open_page_file(1), "schedule_open_page_file 失敗"
    assert keymap.is_visibility_update_suspended(), "watcher 停止 (時間ガード) が効いていません"
    active_after = _active_bmanga_items()
    assert active_after == active_before, (
        "schedule_open_page_file がクリックイベント内で kmi.active を"
        f" 書き換えています: {active_before} -> {active_after}"
    )
    # enter_coma_mode 側の schedule も同様 (予約だけして即クリア)
    assert mode_op.schedule_enter_coma_mode(0, 0), "schedule_enter_coma_mode 失敗"
    active_after2 = _active_bmanga_items()
    assert active_after2 == active_before, (
        "schedule_enter_coma_mode がクリックイベント内で kmi.active を"
        f" 書き換えています: {active_before} -> {active_after2}"
    )
    # 両方の遅延予約 (open_page / enter_coma) をまとめてクリアする実装
    mode_op._clear_deferred_enter_coma_mode()
    print("SCHEDULE_KEEPS_KMI_ACTIVE_OK", f"active={active_before}", flush=True)


def _finish() -> None:
    try:
        new_crashes = _crash_logs() - _state["crashes_before"]
        if new_crashes:
            raise AssertionError(f"新しいBlenderクラッシュログがあります: {new_crashes}")
        print(
            "BMANGA_DOUBLE_CLICK_KEYMAP_CRASH_GUARD_OK",
            f"iterations={_state['completed']}",
            flush=True,
        )
        os._exit(0)
    except BaseException as exc:
        _fail(exc)


def _current_blend_name() -> str:
    return Path(str(bpy.data.filepath or "")).name


def _iterate_open_and_return():
    """ページを開く→作品ファイルへ戻るの1往復をタイマー連鎖で進める."""
    try:
        keymap = _state["keymap"]
        mode_op = _state["mode_op"]
        _state["waiting_ticks"] += 1
        if _state["waiting_ticks"] > 300:
            raise AssertionError(
                f"反復がタイムアウトしました: file={_current_blend_name()!r}"
                f" completed={_state['completed']}"
            )
        name = _current_blend_name()
        if name == "work.blend":
            # 作品ファイル: タブ表示中の状態を模してキーを有効化し、
            # ダブルクリック相当の遅延オープンを予約する
            kstate = keymap.get_state()
            kstate.set_bmanga_items_active(True)
            bpy.context.window_manager.keyconfigs.update()
            assert mode_op.schedule_open_page_file(1)
            assert _active_bmanga_items() > 0, "予約直後に kmi が無効化されています"
            return 0.2
        if name == "page.blend":
            # ページファイルに到達: キーマップ強制更新を挟んで作品へ戻る
            bpy.context.window_manager.keyconfigs.update()
            keymap._SUSPEND_UNTIL = 0.0
            keymap._watch_bmanga_tab()
            bpy.context.window_manager.keyconfigs.update()
            result = bpy.ops.bmanga.exit_page_file("EXEC_DEFAULT")
            assert result == {"FINISHED"}, f"exit_page_file 失敗: {result}"
            _state["completed"] += 1
            if _state["completed"] >= ITERATIONS:
                _finish()
                return None
            return 0.2
        # 遷移待ち (open の遅延タイマー消化中)
        return 0.2
    except BaseException as exc:
        _fail(exc)
        return None


def _start_check() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_dc_keymap_guard_"))
    result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "DcKeymapGuard.bmanga"))
    assert result == {"FINISHED"}, result
    result = bpy.ops.bmanga.page_add()
    assert result == {"FINISHED"}, result
    work = bpy.context.scene.bmanga_work
    work.active_page_index = 0
    bpy.context.scene.bmanga_overview_mode = True
    _state["work_dir"] = str(work.work_dir)

    _check_no_property_kmis()
    _check_zoom_and_shelf_operators()
    _check_schedule_keeps_kmi_active()
    # 予約は上でクリア済み。ここから開く⇄戻るの反復検証を開始する。
    # persistent=True 必須: mainfile 切替 (open_page_file) で非永続タイマーは
    # 消えるため、無いと1往復目のページ読込直後に検証が黙って止まる。
    bpy.app.timers.register(
        _iterate_open_and_return, first_interval=0.3, persistent=True
    )


def main() -> None:
    if bpy.app.background:
        raise RuntimeError("このチェックは --background なしで実行してください")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        bpy.context.preferences.view.show_splash = False
    except Exception:
        pass
    addon = _load_addon()
    _state.update(
        {
            "addon": addon,
            "keymap": importlib.import_module(f"{MOD_NAME}.keymap.keymap"),
            "mode_op": importlib.import_module(f"{MOD_NAME}.operators.mode_op"),
            "crashes_before": _crash_logs(),
        }
    )
    attempts = {"count": 0}

    def _timer():
        attempts["count"] += 1
        if bpy.context.window is None and attempts["count"] < 30:
            return 0.1
        try:
            _start_check()
        except Exception:
            traceback.print_exc()
            os._exit(1)
        return None

    bpy.app.timers.register(_timer, first_interval=0.2)


if __name__ == "__main__":
    main()
