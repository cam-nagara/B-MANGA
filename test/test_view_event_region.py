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
    test_view3d_panel_region_does_not_count_as_viewport_click()
    test_panel_launched_modal_still_accepts_viewport_drag_events()
    test_modal_navigation_passthrough_stays_active_until_release()
    test_unmodified_n_toggles_modal_sidebar()
    test_modal_sidebar_close_finishes_bmanga_tools()
