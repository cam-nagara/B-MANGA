"""Blender 実機用: 起動後の遅延自己修復タイマー確認.

Blender 起動時は「アドオン登録 → userpref.blend のキーマップカスタマイズ
適用」の順で初期化されるため、register 内の自己修復だけでは、過去セッション
で無効化されたまま焼き付いた標準キー (N サイドバー開閉 / F 等) を取りこぼす
(2026-07-18 N キー実測)。register 後に登録される遅延修復タイマーが、
「register より後に適用された無効化状態」を復元することを確認する。

実行:
'/c/Program Files/Blender Foundation/Blender 5.1/blender.exe' --background \
    --factory-startup --python-exit-code 1 \
    --python test/blender_startup_shortcut_repair_timer_check.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_startup_repair",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_startup_repair"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _get_or_create_keymap(kc, name: str, space_type: str = "EMPTY"):
    km = kc.keymaps.get(name)
    if km is None:
        km = kc.keymaps.new(name=name, space_type=space_type, region_type="WINDOW")
    return km


def main() -> None:
    mod = _load_addon()
    from bmanga_dev_startup_repair.keymap import keymap as keymap_mod
    from bmanga_dev_startup_repair.keymap import startup_repair

    wm = bpy.context.window_manager
    kc_user = wm.keyconfigs.user
    kc_default = wm.keyconfigs.default

    km_user_generic = _get_or_create_keymap(kc_user, "3D View Generic", "VIEW_3D")
    km_user_mesh = _get_or_create_keymap(kc_user, "Mesh")
    km_default_mesh = _get_or_create_keymap(kc_default, "Mesh")

    created = []
    try:
        # register がタイマーを登録していること
        assert bpy.app.timers.is_registered(startup_repair._repair_tick), (
            "遅延修復タイマーが register で登録されていません"
        )

        # ---- register より「後」に無効化状態が適用された状況を再現 ----
        # (起動時に userpref.blend のキーマップカスタマイズが遅れて適用される
        #  タイミングの穴。register 内の修復では拾えない)
        # 1) N サイドバー開閉が inactive のまま焼き付いたケース
        kmi_n = km_user_generic.keymap_items.new("wm.context_toggle", "N", "PRESS")
        created.append((km_user_generic, kmi_n))
        kmi_n.properties.data_path = "space_data.show_region_ui"
        kmi_n.active = False

        # 2) B-MANGA 予約キー (F) が default では active なのに user で
        #    inactive のまま焼き付いたケース
        kmi_default_f = km_default_mesh.keymap_items.new(
            "wm.context_toggle", "F", "PRESS"
        )
        created.append((km_default_mesh, kmi_default_f))
        kmi_user_f = km_user_mesh.keymap_items.new("wm.context_toggle", "F", "PRESS")
        created.append((km_user_mesh, kmi_user_f))
        kmi_user_f.active = False

        # ---- 1 パス目: 両方とも復旧すること ----
        interval = startup_repair._repair_tick()
        assert bool(kmi_n.active), "遅延修復で N サイドバー開閉が復旧しません"
        assert bool(kmi_user_f.active), "遅延修復で F キーが復旧しません"
        assert interval == 8.0, f"2 パス目までの間隔が想定外です: {interval}"

        # ---- キーマップ操作の一時停止中はパスを消化せず再試行すること ----
        keymap_mod.suspend_visibility_updates(60.0, reason="test", disable_now=False)
        try:
            interval = startup_repair._repair_tick()
            assert interval == startup_repair._SUSPEND_RETRY_INTERVAL, (
                f"一時停止中の再試行間隔が想定外です: {interval}"
            )
        finally:
            keymap_mod._SUSPEND_UNTIL = 0.0

        # ---- 残りパスを消化するとタイマーが終了すること ----
        interval = startup_repair._repair_tick()
        assert interval == 20.0, f"3 パス目までの間隔が想定外です: {interval}"
        interval = startup_repair._repair_tick()
        assert interval is None, f"全パス消化後に終了しません: {interval}"

        # ---- 再度焼き付いても次回 register (=次回起動) で再修復されること ----
        kmi_n.active = False
        startup_repair.register()
        assert bpy.app.timers.is_registered(startup_repair._repair_tick)
        interval = startup_repair._repair_tick()
        assert bool(kmi_n.active), "再 register 後の遅延修復が働きません"
        assert interval == 8.0
    finally:
        mod.unregister()
        for km, kmi in created:
            try:
                km.keymap_items.remove(kmi)
            except Exception:  # noqa: BLE001
                pass

    # unregister でタイマーが確実に消えていること
    assert not bpy.app.timers.is_registered(startup_repair._repair_tick), (
        "unregister 後も遅延修復タイマーが残っています"
    )

    print("BMANGA_STARTUP_SHORTCUT_REPAIR_TIMER_CHECK_OK")


main()
