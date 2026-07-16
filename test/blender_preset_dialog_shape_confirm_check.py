"""Blender実機用: プリセット一覧の詳細編集ボタンと形状変更の切替確認の検証.

2026-07-17 修正分:

  1. サイドバーのプリセット一覧からの詳細編集で、フキダシだけ合成キー
     ("custom:実名") が実名へ変換されず「プリセットが見つかりません」に
     なっていた → operators/preset_detail_op._load_balloon が "custom:"
     接頭辞を許容し、UI 側 (panels/preset_list_ui.py) は保存済みプリセット
     の行だけ編集ボタンを出す。
  2. フキダシ詳細設定の「形状」フィールド直接編集で「プリセットの切り替え
     確認」が誤発動 (2連続表示) していた → 形状の直接編集は組み込み形状
     プリセットの選択と同義なので、一覧同期 (sync_detail_preset_list) が
     基準値を現在値へ追従させる。カスタム形状の輪郭編集は従来どおり確認の
     対象に残す (operators/detail_preset_apply_op.py)。

実行 (--factory-startup 必須):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python test\\blender_preset_dialog_shape_confirm_check.py
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_preset_shape_confirm"

FAILURES: list[str] = []


def _check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)
        print(f"NG: {message}", flush=True)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _run_check() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_preset_shape_"))
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(temp_root / "config")
    mod = None
    owner_registered = False
    session = None
    context = bpy.context
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PresetShape.bmanga"))
        assert result == {"FINISHED"}, result
        context = bpy.context
        work = context.scene.bmanga_work
        page = work.pages[0]

        from bmanga_dev_preset_shape_confirm.io import balloon_presets
        from bmanga_dev_preset_shape_confirm.operators import (
            detail_dialog_runtime,
            detail_preset_apply_op,
            preset_detail_op,
        )
        from bmanga_dev_preset_shape_confirm.utils.detail_dialog import DetailTarget

        # --- 1. _load_balloon が合成キー "custom:実名" を実名として解決する ---
        balloon_presets.save_local_preset(
            temp_root, "歯車テスト", "", [(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)]
        )
        _check(
            preset_detail_op._load_balloon("歯車テスト") is not None,
            "実名でフキダシプリセットをロードできません",
        )
        _check(
            preset_detail_op._load_balloon("custom:歯車テスト") is not None,
            "合成キー 'custom:実名' でフキダシプリセットをロードできません (プリセットが見つかりません の再発)",
        )
        _check(
            preset_detail_op._load_balloon("shape:cloud") is None,
            "組み込み形状キー 'shape:cloud' が誤ってロードできてしまいます",
        )
        _check(
            preset_detail_op._load_balloon("custom:存在しない名前") is None,
            "存在しないプリセットの合成キーがロードできてしまいます",
        )

        # --- 2. 形状の直接編集は切替確認の対象にしない (基準値の追従) ---
        entry = page.balloons.add()
        entry.id = "shape_confirm_balloon"
        entry.shape = "rect"
        target = DetailTarget(
            kind="balloon",
            stable_id=f"{page.id}:{entry.id}",
            stack_uid=f"balloon:{page.id}:{entry.id}",
            data=entry,
            params=entry,
        )
        session = detail_dialog_runtime.begin_actual_session(
            context,
            target,
            target_validator=lambda identity: identity.stable_id == target.stable_id,
        )

        _check(
            not detail_dialog_runtime.preset_switch_requires_confirmation(
                context, session.token, target, "balloon"
            ),
            "ダイアログ開始直後から未保存扱いになっています",
        )

        # 「形状」フィールドの直接編集に相当
        entry.shape = "cloud"
        _check(
            detail_dialog_runtime.preset_switch_requires_confirmation(
                context, session.token, target, "balloon"
            ),
            "テスト前提の崩れ: 形状変更直後は基準値と差があるはずです",
        )

        # ダイアログ再描画で走る一覧同期 (ここで基準値が追従する)。
        # 関数内クラス + from __future__ annotations では register_class の
        # 型ヒント評価が失敗するため、__annotations__ を直接与えて生成する。
        BMANGA_PG_TestPresetOwner = type(
            "BMANGA_PG_TestPresetOwner",
            (bpy.types.PropertyGroup,),
            {
                "__annotations__": {
                    "detail_preset_items": bpy.props.CollectionProperty(
                        type=detail_preset_apply_op.BMANGA_DetailPresetListItem
                    ),
                    "detail_preset_index": bpy.props.IntProperty(default=-1),
                }
            },
        )
        bpy.utils.register_class(BMANGA_PG_TestPresetOwner)
        bpy.types.WindowManager.bmanga_test_preset_owner = bpy.props.PointerProperty(
            type=BMANGA_PG_TestPresetOwner
        )
        owner_registered = True
        owner = context.window_manager.bmanga_test_preset_owner

        count = detail_preset_apply_op.sync_detail_preset_list(
            owner, context, session, "balloon"
        )
        _check(count > 0, "プリセット一覧の同期が空になりました")
        index = int(owner.detail_preset_index)
        _check(
            0 <= index < len(owner.detail_preset_items)
            and owner.detail_preset_items[index].identifier == "shape:cloud",
            f"一覧の追従選択が形状の行になっていません: index={index}",
        )
        _check(
            not detail_dialog_runtime.preset_switch_requires_confirmation(
                context, session.token, target, "balloon"
            ),
            "形状の直接編集後も「プリセットの切り替え確認」が出る状態のままです (誤発動の再発)",
        )

        # 形状をさらに変えても同期後は常に確認不要
        entry.shape = "thorn"
        detail_preset_apply_op.sync_detail_preset_list(owner, context, session, "balloon")
        _check(
            not detail_dialog_runtime.preset_switch_requires_confirmation(
                context, session.token, target, "balloon"
            ),
            "2回目以降の形状変更で切替確認が誤発動します",
        )

        # --- 3. カスタム形状の輪郭編集は従来どおり保護される ---
        entry.shape = "custom"
        entry.custom_outline_json = "[[0.0,0.0],[10.0,0.0],[10.0,10.0],[0.0,10.0]]"
        detail_preset_apply_op.sync_detail_preset_list(owner, context, session, "balloon")
        _check(
            detail_dialog_runtime.preset_switch_requires_confirmation(
                context, session.token, target, "balloon"
            ),
            "カスタム形状の編集が未保存扱いになりません (保護の退行)",
        )

        if FAILURES:
            for f in FAILURES:
                print(f"FAIL: {f}", flush=True)
            raise AssertionError(f"{len(FAILURES)} 件の検証失敗があります")
        print("BMANGA_PRESET_SHAPE_CONFIRM_OK", flush=True)
    finally:
        if session is not None:
            try:
                from bmanga_dev_preset_shape_confirm.operators import detail_dialog_runtime

                detail_dialog_runtime.cancel_actual_session(context, session)
            except Exception:  # noqa: BLE001
                pass
        if owner_registered:
            try:
                del bpy.types.WindowManager.bmanga_test_preset_owner
            except Exception:  # noqa: BLE001
                pass
        if mod is not None:
            try:
                mod.unregister()
            except Exception:  # noqa: BLE001
                pass
        os.environ.pop("BMANGA_USER_CONFIG_DIR", None)
        shutil.rmtree(temp_root, ignore_errors=True)


def _main() -> None:
    try:
        _run_check()
        sys.stdout.flush()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)
    os._exit(0)


if __name__ == "__main__":
    _main()
