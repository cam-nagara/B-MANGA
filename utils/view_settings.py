"""作品内で共有するビュー設定の同期."""

from __future__ import annotations

DEFAULT_PAGE_PREVIEW_RESOLUTION_PERCENTAGE = 25.0


def _clamp_float(value, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _page_browser_position(value) -> str:
    text = str(value or "LEFT").upper()
    return text if text in {"LEFT", "RIGHT", "TOP", "BOTTOM"} else "LEFT"


def default_page_preview_resolution_percentage(context=None) -> float:
    try:
        from ..preferences import get_preferences

        prefs = get_preferences(context)
        value = getattr(
            prefs,
            "page_preview_resolution_percentage",
            DEFAULT_PAGE_PREVIEW_RESOLUTION_PERCENTAGE,
        )
    except Exception:  # noqa: BLE001
        value = DEFAULT_PAGE_PREVIEW_RESOLUTION_PERCENTAGE
    return _clamp_float(
        value,
        DEFAULT_PAGE_PREVIEW_RESOLUTION_PERCENTAGE,
        5.0,
        200.0,
    )


def default_coma_thumb_scale_percentage(context=None) -> float:
    try:
        from ..preferences import get_preferences

        prefs = get_preferences(context)
        value = getattr(prefs, "coma_thumb_scale_percentage", 12.5)
    except Exception:  # noqa: BLE001
        value = 12.5
    return _clamp_float(value, 12.5, 1.0, 100.0)


def apply_preferences_to_work_defaults(work, context=None) -> None:
    if work is None:
        return
    if hasattr(work, "view_page_preview_resolution_percentage"):
        work.view_page_preview_resolution_percentage = default_page_preview_resolution_percentage(
            context
        )
    if hasattr(work, "page_preview_scale_percentage"):
        work.page_preview_scale_percentage = default_coma_thumb_scale_percentage(context)


def copy_scene_to_work(scene, work) -> None:
    """現在の画面側ビュー設定を作品データへ保存する."""
    if scene is None or work is None:
        return
    if hasattr(work, "view_overlay_enabled"):
        work.view_overlay_enabled = bool(getattr(scene, "bmanga_overlay_enabled", True))
    if hasattr(work, "view_overview_cols"):
        work.view_overview_cols = _clamp_int(
            getattr(scene, "bmanga_overview_cols", 4), 4, 2, 200
        )
    if hasattr(work, "view_overview_gap_mm"):
        work.view_overview_gap_mm = _clamp_float(
            getattr(scene, "bmanga_overview_gap_mm", 30.0), 30.0, 0.0, 1000.0
        )
    if hasattr(work, "view_overview_gap_x_mm"):
        work.view_overview_gap_x_mm = _clamp_float(
            getattr(scene, "bmanga_overview_gap_x_mm", 30.0), 30.0, 0.0, 1000.0
        )
    if hasattr(work, "view_overview_gap_y_mm"):
        work.view_overview_gap_y_mm = _clamp_float(
            getattr(scene, "bmanga_overview_gap_y_mm", 30.0), 30.0, 0.0, 1000.0
        )
    if hasattr(work, "view_page_preview_enabled"):
        work.view_page_preview_enabled = bool(
            getattr(scene, "bmanga_page_preview_enabled", True)
        )
    if hasattr(work, "view_page_preview_page_radius"):
        work.view_page_preview_page_radius = _clamp_int(
            getattr(scene, "bmanga_page_preview_page_radius", 3), 3, 0, 200
        )
    if hasattr(work, "view_page_preview_resolution_percentage"):
        default_resolution = default_page_preview_resolution_percentage()
        work.view_page_preview_resolution_percentage = _clamp_float(
            getattr(scene, "bmanga_page_preview_resolution_percentage", default_resolution),
            default_resolution,
            5.0,
            200.0,
        )
    if hasattr(work, "view_page_browser_position"):
        work.view_page_browser_position = _page_browser_position(
            getattr(scene, "bmanga_page_browser_position", "LEFT")
        )
    if hasattr(work, "view_page_browser_size"):
        work.view_page_browser_size = _clamp_float(
            getattr(scene, "bmanga_page_browser_size", 0.28), 0.28, 0.12, 0.5
        )
    if hasattr(work, "view_page_browser_fit"):
        work.view_page_browser_fit = bool(getattr(scene, "bmanga_page_browser_fit", True))


def apply_work_to_scene(scene, work) -> None:
    """作品データに保存されたビュー設定を現在の画面へ反映する."""
    if scene is None or work is None:
        return
    default_resolution = default_page_preview_resolution_percentage()
    assignments = (
        ("bmanga_overlay_enabled", "view_overlay_enabled", True),
        ("bmanga_overview_cols", "view_overview_cols", 4),
        ("bmanga_overview_gap_mm", "view_overview_gap_mm", 30.0),
        ("bmanga_overview_gap_x_mm", "view_overview_gap_x_mm", 30.0),
        ("bmanga_overview_gap_y_mm", "view_overview_gap_y_mm", 30.0),
        ("bmanga_page_preview_enabled", "view_page_preview_enabled", True),
        ("bmanga_page_preview_page_radius", "view_page_preview_page_radius", 3),
        (
            "bmanga_page_preview_resolution_percentage",
            "view_page_preview_resolution_percentage",
            default_resolution,
        ),
        ("bmanga_page_browser_position", "view_page_browser_position", "LEFT"),
        ("bmanga_page_browser_size", "view_page_browser_size", 0.28),
        ("bmanga_page_browser_fit", "view_page_browser_fit", True),
    )
    resolved = []
    for scene_attr, work_attr, default in assignments:
        if not hasattr(scene, scene_attr) or not hasattr(work, work_attr):
            continue
        value = getattr(work, work_attr, default)
        if scene_attr == "bmanga_page_browser_position":
            value = _page_browser_position(value)
        resolved.append((scene_attr, value))

    for scene_attr, value in resolved:
        try:
            if getattr(scene, scene_attr) != value:
                setattr(scene, scene_attr, value)
        except Exception:  # noqa: BLE001
            pass
