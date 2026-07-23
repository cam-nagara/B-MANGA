"""Blender実機用: ナビゲーションギズモ ヒットボックスの動的化検証.

対応した不具合: フキダシツールなど常駐モーダルツール使用中に、ビューポート
右上のナビゲーションギズモ (視点切替キューブ等) が操作できないことがあった。

根本原因: operators/view_event_region.py のヒットボックス判定
(is_view3d_navigation_ui_event) が 112x232px の固定値で、Blender の
「ナビゲーションギズモサイズ」設定 (preferences.view.gizmo_size_navigate_v3d,
既定 80) と UIスケール (preferences.system.ui_scale, DPI込みの実効スケール)
に追従しなかった。Windows のディスプレイスケーリング 125%/150% 環境やギズモ
サイズ変更時に、実ギズモがヒットボックスからはみ出し、モーダルツールがクリ
ックを横取りしていた。

修正内容:
  1. view_event_region._navigation_ui_hitbox_px() でヒットボックスを動的計算
     するよう変更。既定値 (gizmo_size=80, ui_scale=1.0) では従来の
     112x232 (margin 8) と一致し、ui_scale に比例して拡大する。プリファレン
     ス取得に失敗した場合は安全側 (覆いすぎる方向) に大きめの固定値
     (160x280, margin 8) へフォールバックする。
  2. coma_edge_move_op.py / coma_knife_cut_op.py に複製されていた独自の
     ナビゲーションドラッグ判定 (_is_over_navigation_gizmo + 手書きの
     _navigation_drag_passthrough 管理) を、他の常駐モーダルツール
     (text_op 等) と同じ view_event_region.modal_navigation_ui_passthrough()
     呼び出しへ統一。
  3. brush_size_op.py (Ctrl+Alt+ドラッグ) / coma_camera_op.py
     (coma_camera_shift_drag) の invoke に、ナビゲーションUI領域での
     LEFTMOUSE PRESS を PASS_THROUGH するガードを追加。

実行 (--factory-startup 必須):
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup --python test\\blender_navigation_ui_hitbox_check.py
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_dev_nav_hitbox"

FAILURES: list[str] = []


def _check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)
        print(f"NG: {message}", flush=True)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _mock_context(
    *,
    gizmo_size: float | None = None,
    ui_scale: float | None = None,
    raise_on_prefs: bool = False,
    region_width: int = 800,
    region_height: int = 600,
    n_panel_width: int = 0,
):
    window = SimpleNamespace(type="WINDOW", x=100, y=50, width=region_width, height=region_height)
    space = SimpleNamespace(region_3d=object(), show_gizmo=True, show_gizmo_navigate=True)
    regions = [window]
    if n_panel_width > 0:
        # Nパネル (UIリージョン) は WINDOW の右端に重なって描画され、
        # region.width 自体は縮まない (実測に基づく再現構成)。
        regions.append(SimpleNamespace(
            type="UI",
            x=window.x + region_width - n_panel_width,
            y=window.y,
            width=n_panel_width,
            height=region_height,
        ))
    area = SimpleNamespace(type="VIEW_3D", regions=regions, spaces=SimpleNamespace(active=space))

    if raise_on_prefs:
        class _BoomView:
            show_navigate_ui = True

            @property
            def gizmo_size_navigate_v3d(self):
                raise RuntimeError("boom: preferences unavailable")

        prefs_view = _BoomView()
        prefs_system = SimpleNamespace(ui_scale=1.0)
    else:
        prefs_view = SimpleNamespace(
            show_navigate_ui=True,
            gizmo_size_navigate_v3d=float(gizmo_size if gizmo_size is not None else 80.0),
        )
        prefs_system = SimpleNamespace(ui_scale=float(ui_scale if ui_scale is not None else 1.0))

    preferences = SimpleNamespace(view=prefs_view, system=prefs_system)
    context = SimpleNamespace(
        screen=SimpleNamespace(areas=[area]),
        preferences=preferences,
        area=area,
        region=window,
    )
    return context, window


def _event(event_type: str, x: int, y: int, *, value: str = "PRESS"):
    return SimpleNamespace(
        type=event_type,
        value=value,
        mouse_x=x,
        mouse_y=y,
        shift=False,
        ctrl=False,
        alt=False,
        oskey=False,
    )


def _run_check() -> None:
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()

        view_event_region = sys.modules[f"{PACKAGE}.operators.view_event_region"]
        coma_edge_move_op = sys.modules[f"{PACKAGE}.operators.coma_edge_move_op"]
        coma_knife_cut_op = sys.modules[f"{PACKAGE}.operators.coma_knife_cut_op"]

        # --- 1. 既定環境 (gizmo=80, ui_scale=1.0) で従来の 112x232 (margin 8) と一致 ---
        context, region = _mock_context(gizmo_size=80.0, ui_scale=1.0)
        inside = _event(
            "LEFTMOUSE", region.x + region.width - 120, region.y + region.height - 240,
        )
        _check(
            view_event_region.is_view3d_navigation_ui_event(context, inside),
            "既定環境でヒットボックス境界の内側 (width-120, height-240) が判定されません",
        )
        just_outside_x = _event(
            "LEFTMOUSE", region.x + region.width - 121, region.y + region.height - 240,
        )
        _check(
            not view_event_region.is_view3d_navigation_ui_event(context, just_outside_x),
            "既定環境でヒットボックス幅が従来の 112 (+margin8) より広がっています",
        )
        just_outside_y = _event(
            "LEFTMOUSE", region.x + region.width - 120, region.y + region.height - 241,
        )
        _check(
            not view_event_region.is_view3d_navigation_ui_event(context, just_outside_y),
            "既定環境でヒットボックス高さが従来の 232 (+margin8) より広がっています",
        )
        width, height, margin = view_event_region._navigation_ui_hitbox_px(context)
        _check(
            abs(width - 112.0) < 1e-6 and abs(height - 232.0) < 1e-6 and abs(margin - 8.0) < 1e-6,
            f"既定環境のヒットボックス寸法が 112x232 margin8 と一致しません: {(width, height, margin)!r}",
        )

        # --- 2. ui_scale=2.0 でヒットボックスが約2倍に拡大 ---
        context2, region2 = _mock_context(gizmo_size=80.0, ui_scale=2.0)
        width2, height2, margin2 = view_event_region._navigation_ui_hitbox_px(context2)
        _check(
            abs(width2 - width * 2.0) < 1e-6,
            f"ui_scale=2.0 でヒットボックス幅が2倍になっていません: {width2!r} (期待値 {width * 2.0!r})",
        )
        _check(
            abs(height2 - height * 2.0) < 1e-6,
            f"ui_scale=2.0 でヒットボックス高さが2倍になっていません: {height2!r} (期待値 {height * 2.0!r})",
        )
        _check(
            abs(margin2 - margin * 2.0) < 1e-6,
            f"ui_scale=2.0 でヒットボックス余白が2倍になっていません: {margin2!r} (期待値 {margin * 2.0!r})",
        )
        scaled_inside = _event(
            "LEFTMOUSE",
            region2.x + region2.width - int(width2 + margin2),
            region2.y + region2.height - int(height2 + margin2),
        )
        _check(
            view_event_region.is_view3d_navigation_ui_event(context2, scaled_inside),
            "ui_scale=2.0 で拡大後の境界がナビゲーションUIとして判定されません",
        )

        # --- 3. プリファレンス取得が例外を投げる場合は安全側フォールバック値を使う ---
        context3, region3 = _mock_context(raise_on_prefs=True)
        width3, height3, margin3 = view_event_region._navigation_ui_hitbox_px(context3)
        _check(
            width3 == 160.0 and height3 == 280.0 and margin3 == 8.0,
            f"プリファレンス取得失敗時のフォールバック値が想定と異なります: {(width3, height3, margin3)!r}",
        )
        fallback_inside = _event(
            "LEFTMOUSE",
            region3.x + region3.width - 160,
            region3.y + region3.height - 280,
        )
        _check(
            view_event_region.is_view3d_navigation_ui_event(context3, fallback_inside),
            "フォールバック時にヒットボックス境界の内側が判定されません",
        )

        # --- 4. coma_edge_move_op / coma_knife_cut_op が独自ロジックを持たず
        #        共通関数 (modal_navigation_ui_passthrough) を参照している ---
        edge_move_cls = coma_edge_move_op.BMANGA_OT_coma_edge_move
        knife_cut_cls = coma_knife_cut_op.BMANGA_OT_coma_knife_cut
        _check(
            not hasattr(edge_move_cls, "_is_over_navigation_gizmo"),
            "coma_edge_move_op に独自の _is_over_navigation_gizmo が残っています",
        )
        _check(
            not hasattr(knife_cut_cls, "_is_over_navigation_gizmo"),
            "coma_knife_cut_op に独自の _is_over_navigation_gizmo が残っています",
        )
        edge_move_src = (ROOT / "operators" / "coma_edge_move_op.py").read_text(encoding="utf-8")
        knife_cut_src = (ROOT / "operators" / "coma_knife_cut_op.py").read_text(encoding="utf-8")
        _check(
            "view_event_region.modal_navigation_ui_passthrough" in edge_move_src,
            "coma_edge_move_op が共通関数 modal_navigation_ui_passthrough を参照していません",
        )
        _check(
            "view_event_region.modal_navigation_ui_passthrough" in knife_cut_src,
            "coma_knife_cut_op が共通関数 modal_navigation_ui_passthrough を参照していません",
        )
        _check(
            "_is_over_navigation_gizmo" not in edge_move_src,
            "coma_edge_move_op に独自の _is_over_navigation_gizmo の定義/参照が残っています",
        )
        _check(
            "_is_over_navigation_gizmo" not in knife_cut_src,
            "coma_knife_cut_op に独自の _is_over_navigation_gizmo の定義/参照が残っています",
        )
        _check(
            "_navigation_drag_passthrough = False" not in edge_move_src,
            "coma_edge_move_op に独自の _navigation_drag_passthrough 初期化が残っています",
        )
        _check(
            "_navigation_drag_passthrough = False" not in knife_cut_src,
            "coma_knife_cut_op に独自の _navigation_drag_passthrough 初期化が残っています",
        )

        # --- 5. brush_size_op / coma_camera_op にナビゲーションUIガードが
        #        追加されていること ---
        brush_size_src = (ROOT / "operators" / "brush_size_op.py").read_text(encoding="utf-8")
        coma_camera_src = (ROOT / "operators" / "coma_camera_op.py").read_text(encoding="utf-8")
        _check(
            "view_event_region.is_view3d_navigation_ui_event" in brush_size_src,
            "brush_size_op にナビゲーションUIガードが追加されていません",
        )
        _check(
            "view_event_region.is_view3d_navigation_ui_event" in coma_camera_src,
            "coma_camera_op にナビゲーションUIガードが追加されていません",
        )

        # --- 6. Nパネル展開時、当たり判定がNパネルの左隣 (実ギズモの実際の
        #        位置) へ寄ること (根本原因報告の実測値: ui_scale=1.5,
        #        gizmo_size=80, region_width=3150, Nパネル幅420px オーバー
        #        ラップ時、実ギズモは region-local x≈[2555, 2710] にあった) ---
        context6, region6 = _mock_context(
            gizmo_size=80.0, ui_scale=1.5, region_width=3150, region_height=1400,
            n_panel_width=420,
        )
        width6, height6, margin6 = view_event_region._navigation_ui_hitbox_px(context6)
        _check(
            abs(width6 - 168.0) < 1e-6 and abs(height6 - 348.0) < 1e-6 and abs(margin6 - 12.0) < 1e-6,
            f"ui_scale=1.5環境のヒットボックス寸法が想定と一致しません: {(width6, height6, margin6)!r}",
        )
        # 実ギズモの実測位置 (Nパネルの左隣) はナビゲーションUIとして判定される
        real_gizmo_event = _event(
            "LEFTMOUSE", region6.x + 2650, region6.y + region6.height - 50,
        )
        _check(
            view_event_region.is_view3d_navigation_ui_event(context6, real_gizmo_event),
            "Nパネル展開時、実ギズモの実測位置 (Nパネル左隣) がナビゲーションUI"
            "として判定されません (region.width基準のままの不具合が再発)",
        )
        # 旧実装のヒットボックス (region.width基準、Nパネルの背後) は、
        # Nパネル自身のリージョンに属するためウィンドウイベントとして扱われない
        behind_panel_event = _event(
            "LEFTMOUSE", region6.x + 3000, region6.y + region6.height - 50,
        )
        _check(
            view_event_region.view3d_window_under_event(context6, behind_panel_event) is None,
            "旧ヒットボックス座標がNパネル自身のリージョンと重ならなくなっています"
            " (テスト前提の崩れ)",
        )
        # Nパネルが無い場合は従来どおり region.width 基準で判定される (回帰なし)
        context6b, region6b = _mock_context(gizmo_size=80.0, ui_scale=1.5, region_width=3150, region_height=1400)
        no_panel_event = _event(
            "LEFTMOUSE", region6b.x + region6b.width - 50, region6b.y + region6b.height - 50,
        )
        _check(
            view_event_region.is_view3d_navigation_ui_event(context6b, no_panel_event),
            "Nパネル非展開時に region.width 基準の判定が回帰しています",
        )

        if FAILURES:
            for f in FAILURES:
                print(f"FAIL: {f}", flush=True)
            raise AssertionError(f"{len(FAILURES)} 件の検証失敗があります")
        print("BMANGA_NAV_HITBOX_OK", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:  # noqa: BLE001
                pass


def _main() -> None:
    try:
        _run_check()
        sys.stdout.flush()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        try:
            bpy.ops.wm.quit_blender()
        except Exception:  # noqa: BLE001
            pass
        sys.exit(1)
    try:
        bpy.ops.wm.quit_blender()
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    _main()
