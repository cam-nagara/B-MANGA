from __future__ import annotations

import importlib.util
from pathlib import Path


class Obj:
    def __init__(self, **values) -> None:
        for key, value in values.items():
            setattr(self, key, value)


def _load_view_event_region():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "bmanga_view_event_region",
        root / "operators" / "view_event_region.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _context(*, show_navigate_ui=True, show_gizmo=True, show_gizmo_navigate=True):
    window = Obj(type="WINDOW", x=100, y=50, width=800, height=600)
    space = Obj(
        region_3d=object(),
        show_gizmo=show_gizmo,
        show_gizmo_navigate=show_gizmo_navigate,
    )
    area = Obj(type="VIEW_3D", regions=[window], spaces=Obj(active=space))
    prefs = Obj(view=Obj(show_navigate_ui=show_navigate_ui))
    return Obj(screen=Obj(areas=[area]), preferences=prefs, area=area, region=window), window


def _event(event_type: str, x: int, y: int):
    return Obj(type=event_type, value="PRESS", mouse_x=x, mouse_y=y)


def test_navigation_ui_hitbox_matches_top_right_viewport_controls():
    region_mod = _load_view_event_region()
    context, region = _context()
    assert region_mod.is_view3d_navigation_ui_event(
        context,
        _event("LEFTMOUSE", region.x + region.width - 10, region.y + region.height - 10),
    )
    assert not region_mod.is_view3d_navigation_ui_event(
        context,
        _event("LEFTMOUSE", region.x + region.width - 10, region.y + 50),
    )
    assert not region_mod.is_view3d_navigation_ui_event(
        context,
        _event("A", region.x + region.width - 10, region.y + region.height - 10),
    )


def test_navigation_ui_respects_blender_visibility_settings():
    region_mod = _load_view_event_region()
    for kwargs in (
        {"show_navigate_ui": False},
        {"show_gizmo": False},
        {"show_gizmo_navigate": False},
    ):
        context, region = _context(**kwargs)
        assert not region_mod.is_view3d_navigation_ui_event(
            context,
            _event("LEFTMOUSE", region.x + region.width - 10, region.y + region.height - 10),
        )


def test_navigation_ui_hitbox_shifts_left_of_open_n_panel():
    """Nパネル(UIリージョン)が開いている間、ナビゲーションギズモの当たり判定を
    Nパネルの左隣 (Blenderの実際の可視矩形) へ寄せる (推奨A の回帰防止)。

    Nパネルは region.width を縮めずに WINDOW リージョンの右端へ重なって描画
    されるため、素の region.width 基準の当たり判定は実ギズモの左シフトに
    追従できず、Nパネルの背後の死領域を指し続けてしまう (今回の不具合)。
    """
    region_mod = _load_view_event_region()
    context, region = _context()
    ui_region = Obj(type="UI", x=700, y=50, width=200, height=600)
    context.screen.areas[0].regions.append(ui_region)

    # 実ギズモの位置 (可視矩形の右上隅基準、Nパネルの左隣) はナビゲーションUI
    # として判定される: region.width(800) - Nパネル幅(200) = 可視幅600、
    # 既定(フォールバック)ヒットボックス160+margin8 の範囲は local x>=432
    real_gizmo_event = _event("LEFTMOUSE", region.x + 500, region.y + region.height - 50)
    assert region_mod.is_view3d_navigation_ui_event(context, real_gizmo_event), (
        "Nパネル左隣の実ギズモ位置がナビゲーションUIとして判定されません "
        "(region.width基準のまま = 今回の不具合の再発)"
    )

    # Nパネルより手前 (可視矩形の外) はナビゲーションUIとして判定されない
    outside_event = _event("LEFTMOUSE", region.x + 400, region.y + region.height - 50)
    assert not region_mod.is_view3d_navigation_ui_event(context, outside_event), (
        "可視矩形より内側の位置まで誤ってナビゲーションUI扱いになっています"
    )

    # 旧実装のヒットボックス (region.width基準) が指していた座標は、Nパネル
    # 自身のリージョンに属するため、そもそもウィンドウイベントとして扱われない
    # (Nパネルの背後の死領域であることの確認)
    behind_panel_event = _event("LEFTMOUSE", region.x + 750, region.y + region.height - 50)
    assert region_mod.view3d_window_under_event(context, behind_panel_event) is None, (
        "旧ヒットボックス座標がNパネル自身のリージョンと重ならなくなっています"
        " (テストの前提が崩れています)"
    )


def test_navigation_ui_hitbox_shifts_down_for_overlapping_header():
    """HEADER/TOOL_HEADERがWINDOWの上端に重なる場合も、当たり判定の上限を
    その重なり分だけ下げる (推奨Aの副次対応。通常のタイル配置では重ならず
    無効化されるため、この関数は幾何的な重なりの有無だけで判定する)。
    """
    region_mod = _load_view_event_region()
    context, region = _context()
    header = Obj(type="TOOL_HEADER", x=region.x, y=600, width=800, height=50)
    context.screen.areas[0].regions.append(header)

    # WINDOW上端との重なりは50px (top_inset=50) → 可視高さ 600-50=550
    # フォールバックヒットボックス高さ280+margin8 の範囲は local y>=262 であり、
    # 旧実装 (top_inset無視、閾値312) では判定されなかった位置が判定される
    shifted_event = _event("LEFTMOUSE", region.x + region.width - 50, region.y + 300)
    assert region_mod.is_view3d_navigation_ui_event(context, shifted_event), (
        "上端に重なるヘッダー分だけ当たり判定が下がっていません"
    )


def test_view3d_panel_region_does_not_count_as_viewport_click():
    region_mod = _load_view_event_region()
    context, region = _context()
    ui_region = Obj(type="UI", x=700, y=50, width=200, height=600)
    context.region = ui_region
    context.screen.areas[0].regions.append(ui_region)
    assert region_mod.view3d_window_under_event(
        context,
        _event("LEFTMOUSE", region.x + region.width - 40, region.y + 120),
    ) is None


def test_panel_launched_modal_still_accepts_viewport_drag_events():
    region_mod = _load_view_event_region()
    context, region = _context()
    ui_region = Obj(type="UI", x=700, y=50, width=200, height=600)
    context.region = ui_region
    context.screen.areas[0].regions.append(ui_region)
    result = region_mod.view3d_window_under_event(
        context,
        _event("LEFTMOUSE", region.x + 120, region.y + 120),
    )
    assert result is not None
    assert result[1] is region


def test_modal_navigation_passthrough_stays_active_until_release():
    region_mod = _load_view_event_region()
    context, region = _context()
    operator = Obj()
    assert region_mod.modal_navigation_ui_passthrough(
        operator,
        context,
        _event("LEFTMOUSE", region.x + region.width - 10, region.y + region.height - 10),
    )
    assert operator._navigation_drag_passthrough
    assert region_mod.modal_navigation_ui_passthrough(
        operator,
        context,
        _event("MOUSEMOVE", region.x + 20, region.y + 20),
    )
    assert operator._navigation_drag_passthrough
    release = _event("LEFTMOUSE", region.x + 20, region.y + 20)
    release.value = "RELEASE"
    assert region_mod.modal_navigation_ui_passthrough(operator, context, release)
    assert not operator._navigation_drag_passthrough


def test_unmodified_n_toggles_modal_sidebar():
    region_mod = _load_view_event_region()
    context, region = _context()
    space = context.screen.areas[0].spaces.active
    space.type = "VIEW_3D"
    space.show_region_ui = False
    event = _event("N", region.x + 20, region.y + 20)
    assert region_mod.toggle_modal_sidebar_if_requested(context, event)
    assert space.show_region_ui
    shifted = _event("N", region.x + 20, region.y + 20)
    shifted.shift = True
    assert not region_mod.toggle_modal_sidebar_if_requested(context, shifted)
    assert space.show_region_ui


def test_modal_sidebar_close_finishes_bmanga_tools():
    region_mod = _load_view_event_region()
    context, region = _context()
    space = context.screen.areas[0].spaces.active
    space.type = "VIEW_3D"
    space.show_region_ui = True
    calls = []
    region_mod._finish_modal_tools_for_sidebar_close = lambda ctx: calls.append(ctx)
    event = _event("N", region.x + 20, region.y + 20)
    assert region_mod.toggle_modal_sidebar_if_requested(context, event)
    assert not space.show_region_ui
    assert calls == [context]


if __name__ == "__main__":
    test_navigation_ui_hitbox_matches_top_right_viewport_controls()
    test_navigation_ui_respects_blender_visibility_settings()
    test_navigation_ui_hitbox_shifts_left_of_open_n_panel()
    test_navigation_ui_hitbox_shifts_down_for_overlapping_header()
    test_view3d_panel_region_does_not_count_as_viewport_click()
    test_panel_launched_modal_still_accepts_viewport_drag_events()
    test_modal_navigation_passthrough_stays_active_until_release()
    test_unmodified_n_toggles_modal_sidebar()
    test_modal_sidebar_close_finishes_bmanga_tools()
