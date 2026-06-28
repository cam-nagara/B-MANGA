"""Write standard Blender object edits back to B-MANGA data when possible."""

from __future__ import annotations

import bpy

from . import object_naming as on
from . import object_preserve


def is_sync_candidate(obj: bpy.types.Object | None) -> bool:
    if obj is None:
        return False
    kind = str(obj.get(on.PROP_KIND, "") or "")
    if kind in {"image", "image_path", "image_path_curve", "text", "balloon"} or kind.startswith("effect_"):
        return True
    if kind == "balloon_group":
        return True
    if str(obj.get("bmanga_balloon_fill_mesh_owner_id", "") or ""):
        return True
    if str(obj.get("bmanga_balloon_line_mesh_owner_id", "") or ""):
        return True
    if str(obj.get("bmanga_balloon_fill_owner_id", "") or "") and str(obj.get("bmanga_balloon_fill_kind", "") or ""):
        return True
    if str(obj.get("bmanga_balloon_merge_display_kind", "") or ""):
        return True
    return False


def sync_from_blender_object(scene: bpy.types.Scene, obj: bpy.types.Object | None) -> bool:
    if scene is None or obj is None:
        return False
    if object_preserve.is_preserved(obj):
        return False
    if not is_sync_candidate(obj):
        return False
    kind = str(obj.get(on.PROP_KIND, "") or "")
    if not kind:
        from . import balloon_curve_object

        if balloon_curve_object.sync_generated_display_transform_from_object(scene, obj):
            return True
    if kind in {"image_path", "image_path_curve"}:
        from . import image_path_object

        return image_path_object.sync_entry_points_from_object(scene, obj)
    if kind in {"image", "text"}:
        from . import empty_layer_object

        return empty_layer_object.sync_entry_position_from_object(scene, obj)
    if kind == "balloon":
        from . import balloon_object_writeback

        return balloon_object_writeback.sync_entry_transform_from_object(scene, obj)
    if kind == "balloon_group":
        from . import balloon_merge_object

        return balloon_merge_object.sync_display_transform_from_object(scene, obj)
    if kind == "effect_base_path":
        from . import effect_line_path

        return effect_line_path.sync_from_base_path_object(scene, obj)
    if kind.startswith("effect_"):
        from . import effect_line_object

        return effect_line_object.sync_controller_transform_from_display(obj)
    from . import balloon_curve_object

    return balloon_curve_object.sync_generated_display_transform_from_object(scene, obj)
