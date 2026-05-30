"""Write Blender object transforms back to B-Name balloon entries."""

from __future__ import annotations

import math

import bpy

from . import balloon_curve_object
from . import balloon_curve_source_state
from . import layer_object_sync as los
from . import object_naming as on


def _page_offset_mm(scene: bpy.types.Scene, work, page) -> tuple[float, float]:
    if scene is None or work is None or page is None:
        return (0.0, 0.0)
    page_id = str(getattr(page, "id", "") or "")
    page_index = -1
    for index, candidate in enumerate(getattr(work, "pages", []) or []):
        if str(getattr(candidate, "id", "") or "") == page_id:
            page_index = index
            break
    if page_index < 0:
        return (0.0, 0.0)
    try:
        from . import page_grid

        return page_grid.page_total_offset_mm(work, scene, page_index)
    except Exception:  # noqa: BLE001
        return (0.0, 0.0)


def sync_entry_transform_from_object(scene: bpy.types.Scene, obj: bpy.types.Object) -> bool:
    if scene is None or obj is None or str(obj.get(on.PROP_KIND, "") or "") != "balloon":
        return False
    balloon_id = str(obj.get(on.PROP_ID, "") or "")
    if not balloon_id:
        return False
    page, entry = balloon_curve_object.find_balloon_entry(scene, balloon_id)
    if entry is None:
        return False
    work = getattr(scene, "bname_work", None)
    ox_mm, oy_mm = _page_offset_mm(scene, work, page)
    old_w = max(0.1, float(getattr(entry, "width_mm", 0.1) or 0.1))
    old_h = max(0.1, float(getattr(entry, "height_mm", 0.1) or 0.1))
    sx = float(getattr(obj.scale, "x", 1.0) or 1.0)
    sy = float(getattr(obj.scale, "y", 1.0) or 1.0)
    new_w = max(0.1, old_w * max(1.0e-6, abs(sx)))
    new_h = max(0.1, old_h * max(1.0e-6, abs(sy)))
    center_x = float(obj.location.x) * 1000.0 - ox_mm
    center_y = float(obj.location.y) * 1000.0 - oy_mm
    new_x = center_x - new_w * 0.5
    new_y = center_y - new_h * 0.5
    new_rotation = math.degrees(float(obj.rotation_euler[2]))
    new_flip_h = sx < 0.0
    new_flip_v = sy < 0.0
    old_rect = (
        float(getattr(entry, "x_mm", 0.0) or 0.0),
        float(getattr(entry, "y_mm", 0.0) or 0.0),
        old_w,
        old_h,
    )
    new_rect = (new_x, new_y, new_w, new_h)
    changed = (
        abs(old_rect[0] - new_x) > 1.0e-4
        or abs(old_rect[1] - new_y) > 1.0e-4
        or abs(old_w - new_w) > 1.0e-4
        or abs(old_h - new_h) > 1.0e-4
        or abs(float(getattr(entry, "rotation_deg", 0.0) or 0.0) - new_rotation) > 1.0e-4
        or bool(getattr(entry, "flip_h", False)) != new_flip_h
        or bool(getattr(entry, "flip_v", False)) != new_flip_v
    )
    if not changed:
        return False
    state = balloon_curve_source_state.detect_state(obj)
    if state in {balloon_curve_source_state.STATE_MANUAL, balloon_curve_source_state.STATE_FREEFORM}:
        if abs(abs(sx) - 1.0) > 1.0e-5 or abs(abs(sy) - 1.0) > 1.0e-5:
            balloon_curve_object.transform_manual_curve_to_rect(entry, old_rect, new_rect)
    with los.suppress_sync(), balloon_curve_object.suspend_auto_sync():
        entry.x_mm = new_x
        entry.y_mm = new_y
        entry.width_mm = new_w
        entry.height_mm = new_h
        entry.rotation_deg = new_rotation
        entry.flip_h = new_flip_h
        entry.flip_v = new_flip_v
    obj.scale.x = -1.0 if new_flip_h else 1.0
    obj.scale.y = -1.0 if new_flip_v else 1.0
    obj.scale.z = 1.0
    balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
    return True
