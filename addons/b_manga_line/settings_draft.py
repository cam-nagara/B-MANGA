"""Viewport-safe draft settings for B-MANGA Liner panels."""

from __future__ import annotations

from dataclasses import dataclass, field

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    PointerProperty,
)

from . import core, registration


_DRAFT_PROP = "bmanga_line_settings_draft"
_THICKNESS_ALIASES = {
    "outline_thickness_mm": "outline_thickness",
    "inner_line_thickness_mm": "inner_line_thickness",
    "intersection_thickness_mm": "intersection_thickness",
    "selection_line_thickness_mm": "selection_line_thickness",
}

# Only controls drawn in the line settings, detail dialog, and camera panel belong
# here. Global display controls intentionally keep their immediate behavior.
DRAFT_FIELDS = (
    "auto_subdivision_for_midpoint",
    "outline_enabled",
    "outline_thickness_mm",
    "outline_color",
    "use_outline_creation_limit",
    "outline_creation_max_distance",
    "outline_offset",
    "use_outline_distance_limit",
    "outline_max_distance",
    "even_thickness",
    "use_rim",
    "hide_through_transparent",
    "use_vertex_color",
    "weld_mesh_for_outline",
    "inner_line_enabled",
    "inner_line_thickness_mm",
    "inner_line_color",
    "use_inner_line_creation_limit",
    "inner_line_creation_max_distance",
    "inner_line_offset",
    "use_inner_line_distance_limit",
    "inner_line_max_distance",
    "inner_line_angle",
    "intersection_enabled",
    "intersection_thickness_mm",
    "intersection_color",
    "use_intersection_creation_limit",
    "intersection_creation_max_distance",
    "intersection_line_offset",
    "use_intersection_distance_limit",
    "intersection_max_distance",
    "intersection_edge_midpoint_angle",
    "selection_line_enabled",
    "selection_line_thickness_mm",
    "selection_line_color",
    "use_selection_line_creation_limit",
    "selection_line_creation_max_distance",
    "selection_line_offset",
    "use_selection_line_distance_limit",
    "selection_line_max_distance",
    "selection_line_angle",
    "edge_smooth_factor",
    "edge_midpoint_jitter_percent",
    "edge_midpoint_angle",
    "edge_width_curve_25",
    "edge_width_curve_50",
    "edge_width_curve_75",
    "inner_edge_smooth_factor",
    "inner_edge_midpoint_jitter_percent",
    "inner_edge_width_curve_25",
    "inner_edge_width_curve_50",
    "inner_edge_width_curve_75",
    "intersection_edge_smooth_factor",
    "intersection_edge_midpoint_jitter_percent",
    "intersection_edge_width_curve_25",
    "intersection_edge_width_curve_50",
    "intersection_edge_width_curve_75",
    "selection_edge_smooth_factor",
    "selection_edge_midpoint_jitter_percent",
    "selection_edge_midpoint_angle",
    "selection_edge_width_curve_25",
    "selection_edge_width_curve_50",
    "selection_edge_width_curve_75",
    "bump_line_enabled",
    "bump_line_color",
    "bump_line_thickness",
    "bump_line_threshold",
    "line_width_reference_distance",
    "use_camera_compensation",
    "camera_compensation_influence",
    "use_uniform_line_width",
    "line_width_distance_falloff",
    "limit_uniform_width_to_setting",
    "use_camera_culling",
    "culling_margin",
)
CAMERA_FIELD = "line_camera_override"


@dataclass
class _DraftState:
    source: bpy.types.Object | None = None
    scene: bpy.types.Scene | None = None
    targets: tuple[bpy.types.Object, ...] = ()
    selection_signature: tuple[int, ...] = ()
    dirty: set[str] = field(default_factory=set)
    initialized: bool = False


_states: dict[int, _DraftState] = {}
_sync_depth = 0
_draft_class = None


def _wm_key(wm) -> int:
    try:
        return int(wm.as_pointer())
    except (AttributeError, ReferenceError):
        return id(wm)


def _state_for(wm) -> _DraftState:
    return _states.setdefault(_wm_key(wm), _DraftState())


def _selected_meshes(context, active) -> tuple[bpy.types.Object, ...]:
    items: list[bpy.types.Object] = []
    seen: set[int] = set()
    for obj in getattr(context, "selected_objects", ()) or ():
        if obj is None or getattr(obj, "type", None) != "MESH":
            continue
        pointer = int(obj.as_pointer())
        if pointer not in seen:
            seen.add(pointer)
            items.append(obj)
    if active is not None and getattr(active, "type", None) == "MESH":
        pointer = int(active.as_pointer())
        if pointer not in seen:
            items.insert(0, active)
    return tuple(items)


def _selection_signature(targets) -> tuple[int, ...]:
    return tuple(sorted(int(obj.as_pointer()) for obj in targets))


def _valid_object(obj, scene) -> bool:
    if obj is None or scene is None:
        return False
    try:
        return obj.name in scene.objects and obj.type == "MESH"
    except (ReferenceError, RuntimeError):
        return False


def _setting_field(field_name: str) -> str:
    return _THICKNESS_ALIASES.get(field_name, field_name)


def _copy_value(value):
    if hasattr(value, "__iter__") and not isinstance(value, str):
        return tuple(value)
    return value


def _mark_dirty(self, context, field_name: str) -> None:
    if _sync_depth:
        return
    wm = getattr(self, "id_data", None) or getattr(context, "window_manager", None)
    if wm is None:
        return
    state = _state_for(wm)
    if not state.initialized:
        active = getattr(context, "active_object", None)
        state.source = active if getattr(active, "type", None) == "MESH" else None
        state.scene = getattr(context, "scene", None)
        state.targets = _selected_meshes(context, state.source)
        state.selection_signature = _selection_signature(state.targets)
        state.initialized = state.source is not None
    state.dirty.add(field_name)


def _draft_update(field_name: str):
    def update(self, context):
        _mark_dirty(self, context, field_name)

    update.__name__ = f"_on_draft_{field_name}_changed"
    return update


def _camera_poll(_self, obj) -> bool:
    return obj is None or getattr(obj, "type", None) == "CAMERA"


def _clone_property(prop, field_name: str):
    update = _draft_update(field_name)
    common = {
        "name": prop.name,
        "description": prop.description,
        "update": update,
    }
    if prop.type == "BOOLEAN":
        return BoolProperty(default=bool(prop.default), **common)
    if prop.type == "ENUM":
        items = [
            (item.identifier, item.name, item.description)
            for item in prop.enum_items
        ]
        return EnumProperty(items=items, default=prop.default, **common)
    if prop.type == "FLOAT" and prop.is_array:
        return FloatVectorProperty(
            size=prop.array_length,
            default=tuple(prop.default_array),
            min=float(prop.hard_min),
            max=float(prop.hard_max),
            subtype=prop.subtype,
            **common,
        )
    if prop.type == "FLOAT":
        return FloatProperty(
            default=float(prop.default),
            min=float(prop.hard_min),
            max=float(prop.hard_max),
            soft_min=float(prop.soft_min),
            soft_max=float(prop.soft_max),
            step=float(prop.step),
            precision=int(prop.precision),
            subtype=prop.subtype,
            **common,
        )
    raise TypeError(f"Unsupported draft property: {field_name} ({prop.type})")


def _build_draft_class():
    source_props = core.BMangaLineSettings.bl_rna.properties
    annotations = {
        name: _clone_property(source_props[name], name)
        for name in DRAFT_FIELDS
    }
    annotations[CAMERA_FIELD] = PointerProperty(
        type=bpy.types.Object,
        name="別カメラ指定",
        description=(
            "通常はカメラビューのカメラを使います。"
            "別カメラで判定したい場合だけ指定します"
        ),
        poll=_camera_poll,
        update=_draft_update(CAMERA_FIELD),
    )
    return type(
        "BMangaLineSettingsDraft",
        (bpy.types.PropertyGroup,),
        {"__module__": __name__, "__annotations__": annotations},
    )


def _load_state(draft, state: _DraftState, active, scene, targets) -> None:
    global _sync_depth
    settings = active.bmanga_line_settings
    _sync_depth += 1
    try:
        for field_name in DRAFT_FIELDS:
            setattr(draft, field_name, _copy_value(getattr(settings, field_name)))
        setattr(draft, CAMERA_FIELD, getattr(scene, "bmanga_line_camera", None))
    finally:
        _sync_depth -= 1
    state.source = active
    state.scene = scene
    state.targets = tuple(targets)
    state.selection_signature = _selection_signature(targets)
    state.dirty.clear()
    state.initialized = True


def _pending_groups(changed_fields: set[str]) -> tuple[set[str], set[str]]:
    from . import update_state

    create_targets: set[str] = set()
    visual_targets: set[str] = set()
    for field_name in changed_fields:
        setting_field = _setting_field(field_name)
        targets = set(update_state.targets_for_property(setting_field))
        if update_state.kind_for_property(setting_field) == "visual":
            visual_targets.update(targets)
        else:
            create_targets.update(targets)
    return create_targets, visual_targets


def _flush_state(draft, state: _DraftState) -> int:
    if not state.initialized or not state.dirty:
        return 0
    from . import update_state

    dirty = set(state.dirty)
    camera_dirty = CAMERA_FIELD in dirty
    dirty.discard(CAMERA_FIELD)
    changed_objects = 0
    old_propagating = core._propagating
    core._propagating = True
    try:
        for obj in state.targets:
            if not _valid_object(obj, state.scene) or core.is_settings_locked(obj):
                continue
            settings = getattr(obj, "bmanga_line_settings", None)
            if settings is None:
                continue
            changed_fields: set[str] = set()
            for field_name in dirty:
                value = _copy_value(getattr(draft, field_name))
                if core._setting_values_equal(getattr(settings, field_name), value):
                    continue
                try:
                    setattr(settings, field_name, value)
                except (AttributeError, TypeError, RuntimeError):
                    continue
                changed_fields.add(field_name)
            if not changed_fields:
                continue
            core.record_override_edits(obj)
            create_targets, visual_targets = _pending_groups(changed_fields)
            if create_targets:
                update_state.mark_pending(obj, create_targets, kind="create")
            if visual_targets:
                update_state.mark_pending(obj, visual_targets, kind="visual")
            changed_objects += 1
    finally:
        core._propagating = old_propagating

    if camera_dirty and state.scene is not None:
        camera = getattr(draft, CAMERA_FIELD, None)
        if getattr(state.scene, "bmanga_line_camera", None) != camera:
            state.scene.bmanga_line_camera = camera
    state.dirty.clear()
    return changed_objects


def ensure(context):
    """Return the current draft, flushing once when the edit target changes."""
    wm = getattr(context, "window_manager", None)
    active = getattr(context, "active_object", None)
    scene = getattr(context, "scene", None)
    if (
        wm is None
        or active is None
        or getattr(active, "type", None) != "MESH"
        or scene is None
        or not hasattr(wm, _DRAFT_PROP)
    ):
        return None
    draft = getattr(wm, _DRAFT_PROP)
    state = _state_for(wm)
    targets = _selected_meshes(context, active)
    signature = _selection_signature(targets)
    same_source = state.source == active and state.scene == scene
    if state.initialized and same_source and state.selection_signature == signature:
        return draft
    if state.initialized and state.dirty:
        _flush_state(draft, state)
    _load_state(draft, state, active, scene, targets)
    return draft


def flush(context) -> int:
    """Commit the current draft to its selected meshes exactly once."""
    draft = ensure(context)
    wm = getattr(context, "window_manager", None)
    if draft is None or wm is None:
        return 0
    return _flush_state(draft, _state_for(wm))


def discard(context) -> None:
    """Discard uncommitted panel edits and reload from the active mesh."""
    wm = getattr(context, "window_manager", None)
    if wm is None:
        return
    state = _state_for(wm)
    state.dirty.clear()
    state.initialized = False
    ensure(context)


def invalidate(context=None) -> None:
    if context is None:
        _states.clear()
        return
    wm = getattr(context, "window_manager", None)
    if wm is not None:
        _states.pop(_wm_key(wm), None)


def snapshot(context) -> dict | None:
    draft = ensure(context)
    wm = getattr(context, "window_manager", None)
    if draft is None or wm is None:
        return None
    state = _state_for(wm)
    values = {
        name: _copy_value(getattr(draft, name))
        for name in (*DRAFT_FIELDS, CAMERA_FIELD)
    }
    return {"values": values, "dirty": set(state.dirty)}


def restore_snapshot(context, saved: dict | None) -> None:
    global _sync_depth
    if not saved:
        return
    draft = ensure(context)
    wm = getattr(context, "window_manager", None)
    if draft is None or wm is None:
        return
    _sync_depth += 1
    try:
        for name, value in saved["values"].items():
            setattr(draft, name, value)
    finally:
        _sync_depth -= 1
    _state_for(wm).dirty = set(saved["dirty"])


def dirty_fields(context) -> frozenset[str]:
    wm = getattr(context, "window_manager", None)
    if wm is None:
        return frozenset()
    return frozenset(_state_for(wm).dirty)


def get_line_camera(context):
    """Resolve the camera shown in the draft-aware camera panel."""
    draft = ensure(context)
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    override = getattr(draft, CAMERA_FIELD, None) if draft is not None else None
    if override is not None and getattr(override, "type", None) == "CAMERA":
        return override
    return scene.camera


def flush_all() -> int:
    changed = 0
    for wm in getattr(bpy.data, "window_managers", ()):
        if not hasattr(wm, _DRAFT_PROP):
            continue
        state = _states.get(_wm_key(wm))
        if state is not None:
            changed += _flush_state(getattr(wm, _DRAFT_PROP), state)
    return changed


@persistent
def _on_save_pre(_dummy) -> None:
    flush_all()


@persistent
def _on_state_reset(_dummy) -> None:
    invalidate()


def _append_once(handler, callback) -> None:
    _remove(handler, callback)
    handler.append(callback)


def _remove(handler, callback) -> None:
    for registered in tuple(handler):
        same_callback = registered is callback or (
            getattr(registered, "__module__", None) == callback.__module__
            and getattr(registered, "__name__", None) == callback.__name__
        )
        if same_callback:
            handler.remove(registered)


def register() -> None:
    global _draft_class
    unregister()
    _draft_class = _build_draft_class()
    registration.register_class(_draft_class)
    setattr(
        bpy.types.WindowManager,
        _DRAFT_PROP,
        PointerProperty(type=_draft_class),
    )
    _append_once(bpy.app.handlers.save_pre, _on_save_pre)
    _append_once(bpy.app.handlers.load_post, _on_state_reset)
    _append_once(bpy.app.handlers.undo_post, _on_state_reset)
    _append_once(bpy.app.handlers.redo_post, _on_state_reset)


def unregister() -> None:
    global _draft_class
    _remove(bpy.app.handlers.save_pre, _on_save_pre)
    _remove(bpy.app.handlers.load_post, _on_state_reset)
    _remove(bpy.app.handlers.undo_post, _on_state_reset)
    _remove(bpy.app.handlers.redo_post, _on_state_reset)
    invalidate()
    if hasattr(bpy.types.WindowManager, _DRAFT_PROP):
        delattr(bpy.types.WindowManager, _DRAFT_PROP)
    if _draft_class is not None:
        registration.unregister_class(_draft_class)
        _draft_class = None
