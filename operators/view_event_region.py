"""3D View の WINDOW 領域だけを対象にするイベント判定ヘルパ."""

from __future__ import annotations


def _contains(region, mouse_x: int, mouse_y: int) -> bool:
    x = int(getattr(region, "x", 0))
    y = int(getattr(region, "y", 0))
    width = int(getattr(region, "width", 0))
    height = int(getattr(region, "height", 0))
    return (
        x <= mouse_x < x + width
        and y <= mouse_y < y + height
    )


# ヒットボックスの寸法は Blender の「ナビゲーションギズモサイズ」設定
# (context.preferences.view.gizmo_size_navigate_v3d, 既定 80) が既定値の時に
# 下の係数と一致するよう逆算してある: (80 + 32) * 1.0 = 112 / (80 + 152) * 1.0 = 232。
_NAVIGATION_UI_HITBOX_WIDTH_GIZMO_OFFSET_PX = 32
_NAVIGATION_UI_HITBOX_HEIGHT_GIZMO_OFFSET_PX = 152
_NAVIGATION_UI_HITBOX_MARGIN_BASE_PX = 8

# プリファレンス取得に失敗した場合のフォールバック値。既定環境の 112x232 より
# 大きめに設定してある: ヒットボックスがギズモより小さいと実ギズモがはみ出し
# てモーダルツールにクリックを奪われる (今回の不具合そのもの) が、大きすぎる
# 場合は取りこぼしたクリックがナビゲーションUIパススルー扱いになるだけで実害
# が小さい。「覆いきれないより覆いすぎる方が安全」という方針でこの値を選ぶ。
_NAVIGATION_UI_HITBOX_FALLBACK_WIDTH_PX = 160
_NAVIGATION_UI_HITBOX_FALLBACK_HEIGHT_PX = 280
_NAVIGATION_UI_HITBOX_FALLBACK_MARGIN_PX = 8
_MOUSE_EVENT_TYPES = {
    "LEFTMOUSE",
    "MIDDLEMOUSE",
    "RIGHTMOUSE",
    "MOUSEMOVE",
    "WHEELUPMOUSE",
    "WHEELDOWNMOUSE",
    "WHEELINMOUSE",
    "WHEELOUTMOUSE",
}

# ツール操作には一切使われず、純粋にビューポートのナビゲーションだけに使う
# マウス/トラックパッドイベント。どのモーダルツールを使用中でも (ドラッグ中・
# テキスト入力中も含め) 常にビューポートナビゲーションへ通すため、各ツールの
# modal() 冒頭でこの判定を使い PASS_THROUGH させる。
#   ・MIDDLEMOUSE  = 中ボタンによるオービット/パン
#   ・WHEEL*       = ホイールズーム
#   ・TRACKPAD*    = トラックパッドのパン/ズーム/回転ジェスチャ
# テンキー視点キー (NUMPAD_1〜9 等) はテキスト入力中に数字入力と競合するため
# ここには含めない (文字入力ではないマウス系ナビだけを対象にする)。
_NAVIGATION_MOUSE_EVENT_TYPES = frozenset({
    "MIDDLEMOUSE",
    "WHEELUPMOUSE",
    "WHEELDOWNMOUSE",
    "WHEELINMOUSE",
    "WHEELOUTMOUSE",
    "TRACKPADPAN",
    "TRACKPADZOOM",
    "TRACKPADROTATE",
})


def is_navigation_mouse_event(event) -> bool:
    """ツール操作に使われない純ナビゲーション用マウス/トラックパッドイベントか."""
    return str(getattr(event, "type", "") or "") in _NAVIGATION_MOUSE_EVENT_TYPES


def _mouse_event_type(event) -> bool:
    return str(getattr(event, "type", "") or "") in _MOUSE_EVENT_TYPES


def view3d_window_under_event(context, event):
    """イベント位置にある VIEW_3D の WINDOW region を返す.

    N パネルやツールバーなどの非 WINDOW region が同じ座標を覆っている場合は
    None を返し、モーダルツールが UI 操作を奪わないようにする。
    """
    screen = getattr(context, "screen", None)
    if screen is None:
        return None
    mouse_x = int(getattr(event, "mouse_x", -10_000_000))
    mouse_y = int(getattr(event, "mouse_y", -10_000_000))
    active_area = getattr(context, "area", None)
    active_region = getattr(context, "region", None)
    if (
        _mouse_event_type(event)
        and getattr(active_area, "type", "") == "VIEW_3D"
        and active_region is not None
        and getattr(active_region, "type", "") != "WINDOW"
        and _contains(active_region, mouse_x, mouse_y)
    ):
        return None
    for area in getattr(screen, "areas", []):
        if getattr(area, "type", "") != "VIEW_3D":
            continue
        regions = list(getattr(area, "regions", []) or [])
        for region in regions:
            if (
                getattr(region, "type", "") != "WINDOW"
                and _contains(region, mouse_x, mouse_y)
            ):
                return None
        for region in regions:
            if (
                getattr(region, "type", "") != "WINDOW"
                or not _contains(region, mouse_x, mouse_y)
            ):
                continue
            space = getattr(getattr(area, "spaces", None), "active", None)
            rv3d = getattr(space, "region_3d", None)
            if rv3d is None:
                continue
            return area, region, rv3d, mouse_x - int(region.x), mouse_y - int(region.y)
    return None


def is_view3d_window_event(context, event) -> bool:
    return view3d_window_under_event(context, event) is not None


def _navigation_ui_visible(context, area) -> bool:
    prefs_view = getattr(getattr(context, "preferences", None), "view", None)
    if prefs_view is not None and not bool(getattr(prefs_view, "show_navigate_ui", True)):
        return False
    space = getattr(getattr(area, "spaces", None), "active", None)
    if space is None:
        return True
    if not bool(getattr(space, "show_gizmo", True)):
        return False
    return bool(getattr(space, "show_gizmo_navigate", True))


def _navigation_ui_hitbox_px(context) -> tuple[float, float, float]:
    """Return (width, height, margin) in pixels for the navigation gizmo hitbox.

    Blender's navigation gizmo (top-right corner of the VIEW_3D WINDOW region)
    scales with two independent user preferences: the "Navigation Gizmos" size
    slider (``preferences.view.gizmo_size_navigate_v3d``, factory default 80)
    and the effective UI scale (``preferences.system.ui_scale``, which already
    folds in the OS display-scaling setting on Windows). A hitbox sized only for
    the factory defaults leaves the real gizmo poking out past it under Windows
    125%/150% display scaling or a custom gizmo size, so a modal B-MANGA tool
    ends up stealing clicks meant for the gizmo -- the bug this function exists
    to fix.

    If preferences cannot be read for any reason, fall back to hitbox values
    larger than the factory-default 112x232 box. Over-covering is the safe
    failure mode here: a hitbox that is too big just forwards a few extra
    clicks near the corner down the (harmless) navigation-UI passthrough path,
    while a hitbox that is too small lets a modal tool swallow clicks meant for
    the real gizmo.
    """
    try:
        preferences = context.preferences
        gizmo_size = float(preferences.view.gizmo_size_navigate_v3d)
        ui_scale = float(preferences.system.ui_scale)
        if ui_scale <= 0.0:
            raise ValueError("non-positive ui_scale")
    except Exception:  # noqa: BLE001
        return (
            float(_NAVIGATION_UI_HITBOX_FALLBACK_WIDTH_PX),
            float(_NAVIGATION_UI_HITBOX_FALLBACK_HEIGHT_PX),
            float(_NAVIGATION_UI_HITBOX_FALLBACK_MARGIN_PX),
        )
    width = (gizmo_size + _NAVIGATION_UI_HITBOX_WIDTH_GIZMO_OFFSET_PX) * ui_scale
    height = (gizmo_size + _NAVIGATION_UI_HITBOX_HEIGHT_GIZMO_OFFSET_PX) * ui_scale
    margin = _NAVIGATION_UI_HITBOX_MARGIN_BASE_PX * ui_scale
    return width, height, margin


def is_view3d_navigation_ui_event(context, event) -> bool:
    """Return True when a mouse event is over Blender's top-right navigation UI.

    Modal B-MANGA tools run while the user keeps working in the viewport. Without
    this guard they also consume clicks on Blender's navigation gizmo and the
    zoom/pan buttons because those controls live inside the VIEW_3D WINDOW region.
    """
    event_type = str(getattr(event, "type", "") or "")
    if event_type not in _MOUSE_EVENT_TYPES:
        return False
    view = view3d_window_under_event(context, event)
    if view is None:
        return False
    area, region, _rv3d, mouse_x, mouse_y = view
    if not _navigation_ui_visible(context, area):
        return False
    hitbox_width, hitbox_height, hitbox_margin = _navigation_ui_hitbox_px(context)
    return (
        float(mouse_x) >= float(region.width) - hitbox_width - hitbox_margin
        and float(mouse_y) >= float(region.height) - hitbox_height - hitbox_margin
    )


def modal_navigation_ui_passthrough(modal_operator, context, event) -> bool:
    """Return True while a modal tool should yield to viewport navigation UI.

    Navigation buttons keep handling the drag after the initial press. The mouse
    can leave the top-right hitbox during that drag, so the modal tool must keep
    passing events through until the corresponding left-button release.
    """
    if bool(getattr(modal_operator, "_navigation_drag_passthrough", False)):
        event_type = str(getattr(event, "type", "") or "")
        event_value = str(getattr(event, "value", "") or "")
        if event_type == "LEFTMOUSE" and event_value == "RELEASE":
            setattr(modal_operator, "_navigation_drag_passthrough", False)
        return True
    if not is_view3d_navigation_ui_event(context, event):
        return False
    event_type = str(getattr(event, "type", "") or "")
    event_value = str(getattr(event, "value", "") or "")
    if event_type == "LEFTMOUSE" and event_value == "PRESS":
        setattr(modal_operator, "_navigation_drag_passthrough", True)
    return True


def _unmodified_key_press(event, key_type: str) -> bool:
    return (
        str(getattr(event, "type", "") or "") == key_type
        and str(getattr(event, "value", "") or "") == "PRESS"
        and not bool(getattr(event, "shift", False))
        and not bool(getattr(event, "ctrl", False))
        and not bool(getattr(event, "alt", False))
        and not bool(getattr(event, "oskey", False))
    )


def _view3d_area_for_keyboard_event(context, event):
    area = getattr(context, "area", None)
    if area is not None and getattr(area, "type", "") == "VIEW_3D":
        return area
    screen = getattr(context, "screen", None)
    if screen is None:
        return None
    mouse_x = int(getattr(event, "mouse_x", -10_000_000))
    mouse_y = int(getattr(event, "mouse_y", -10_000_000))
    first_view3d = None
    for candidate in getattr(screen, "areas", []) or []:
        if getattr(candidate, "type", "") != "VIEW_3D":
            continue
        if first_view3d is None:
            first_view3d = candidate
        if _contains(candidate, mouse_x, mouse_y):
            return candidate
    return first_view3d


def _finish_modal_tools_for_sidebar_close(context) -> None:
    try:
        from . import coma_modal_state

        coma_modal_state.finish_all(context)
    except Exception:  # noqa: BLE001
        pass


def toggle_modal_sidebar_if_requested(context, event) -> bool:
    """Handle the standard N sidebar key while a B-MANGA modal tool is active."""
    if not _unmodified_key_press(event, "N"):
        return False
    area = _view3d_area_for_keyboard_event(context, event)
    if area is None:
        return False
    spaces = getattr(area, "spaces", None)
    active_space = getattr(spaces, "active", None)
    target_space = active_space if getattr(active_space, "type", "") == "VIEW_3D" else None
    if target_space is None:
        try:
            space_iter = list(spaces) if spaces is not None else []
        except TypeError:
            space_iter = []
        for space in space_iter:
            if getattr(space, "type", "") == "VIEW_3D":
                target_space = space
                break
    if target_space is None:
        return False
    try:
        target_space.show_region_ui = not bool(getattr(target_space, "show_region_ui", False))
    except Exception:  # noqa: BLE001
        return False
    if not bool(getattr(target_space, "show_region_ui", False)):
        _finish_modal_tools_for_sidebar_close(context)
    try:
        area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass
    return True
