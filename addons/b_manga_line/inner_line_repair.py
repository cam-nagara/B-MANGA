"""Repair saved inner-line node trees."""

from __future__ import annotations

import bpy

from .core import GN_MODIFIER_NAME, VG_INNER_LINE_WIDTH, inner_width_split_angle


_timer_running = False


def _scene_data_available() -> bool:
    try:
        getattr(bpy.data, "objects")
    except AttributeError:
        return False
    return True


def _inner_tree_is_current(tree: bpy.types.NodeTree | None) -> bool:
    from . import inner_lines

    if tree is None:
        return False
    required_sockets = (
        "検出角度",
        "線の太さ",
        inner_lines._OFFSET_SOCKET_NAME,
        inner_lines._MARKED_ONLY_SOCKET_NAME,
        inner_lines._MIDPOINT_FACTOR_SOCKET_NAME,
        inner_lines._MIDPOINT_JITTER_SOCKET_NAME,
        inner_lines._RESAMPLE_COUNT_SOCKET_NAME,
        inner_lines._WIDTH_CURVE_25_SOCKET_NAME,
        inner_lines._WIDTH_CURVE_50_SOCKET_NAME,
        inner_lines._WIDTH_CURVE_75_SOCKET_NAME,
    )
    if any(inner_lines._find_socket_id(tree, name) is None for name in required_sockets):
        return False
    required_labels = (
        inner_lines._GENERATED_LINE_NODE_LABEL,
        inner_lines._RADIUS_HALF_NODE_LABEL,
        inner_lines._MARKED_SELECTION_SWITCH_LABEL,
        inner_lines._CURVE_WIDTH_SCALE_LABEL,
        inner_lines._SUBDIVIDE_CURVE_LABEL,
        inner_lines._SELECTED_EDGE_MESH_LABEL,
        inner_lines._CHAIN_INSTANCE_SPLIT_LABEL,
        inner_lines._EDGE_ANGLE_COMPARE_LABEL,
        inner_lines._CHAIN_SELECTION_COMPARE_LABEL,
        inner_lines._AUTO_EDGE_ALLOWED_LABEL,
        inner_lines._AUTO_ANGLE_FILTER_LABEL,
        inner_lines._CURVE_JITTER_CENTER_LABEL,
    )
    labels = {getattr(node, "label", "") for node in tree.nodes}
    if any(label not in labels for label in required_labels):
        return False
    return (
        inner_lines._uses_named_attribute(tree, VG_INNER_LINE_WIDTH)
        and inner_lines._uses_named_attribute(tree, inner_lines._FREESTYLE_EDGE_ATTR)
        and inner_lines._uses_named_attribute(tree, inner_lines._CHAIN_ID_ATTR)
        and not any(node.bl_idname == "GeometryNodeSetCurveRadius" for node in tree.nodes)
        and not any(node.bl_idname == "GeometryNodeResampleCurve" for node in tree.nodes)
    )


def _modifier_float(mod: bpy.types.Modifier, tree: bpy.types.NodeTree, socket_name: str, fallback: float) -> float:
    from . import inner_lines

    sid = inner_lines._find_socket_id(tree, socket_name)
    if sid is None:
        return fallback
    try:
        return float(mod[sid])
    except (KeyError, TypeError, ValueError):
        return fallback


def _modifier_bool(mod: bpy.types.Modifier, tree: bpy.types.NodeTree, socket_name: str, fallback: bool) -> bool:
    from . import inner_lines

    sid = inner_lines._find_socket_id(tree, socket_name)
    if sid is None:
        return fallback
    try:
        return bool(mod[sid])
    except (KeyError, TypeError, ValueError):
        return fallback


def _refresh_chain_attribute(
    obj: bpy.types.Object,
    mod: bpy.types.Modifier,
    settings,
) -> None:
    from . import inner_line_chains, inner_lines

    tree = mod.node_group
    if tree is None:
        return
    angle = float(getattr(settings, "inner_line_angle", 0.5236))
    use_marked_edges = False
    angle = _modifier_float(mod, tree, "検出角度", angle)
    midpoint_angle = inner_width_split_angle(settings, angle)
    inner_line_chains.update_chain_id_attribute(
        obj,
        angle,
        use_marked_edges,
        midpoint_angle,
    )


def _repair_object(obj: bpy.types.Object) -> bool:
    from . import inner_lines, outline_setup
    from .scale_utils import modifier_thickness_for_world_width

    if obj.type != "MESH" or obj.data is None:
        return False
    mod = obj.modifiers.get(GN_MODIFIER_NAME)
    if mod is None:
        return False
    settings = getattr(obj, "bmanga_line_settings", None)
    if settings is None:
        return False
    if _inner_tree_is_current(mod.node_group):
        _refresh_chain_attribute(obj, mod, settings)
        return False

    show_viewport = bool(mod.show_viewport)
    show_render = bool(mod.show_render)
    midpoint_factor = (
        float(getattr(settings, "inner_edge_smooth_factor", 0.0))
        if bool(getattr(settings, "auto_subdivision_for_midpoint", False))
        else 0.0
    )
    changed = inner_lines.apply_inner_lines(
        obj,
        angle=float(getattr(settings, "inner_line_angle", 0.5236)),
        thickness=modifier_thickness_for_world_width(
            obj,
            float(getattr(settings, "inner_line_thickness", 0.0003)),
        ),
        offset=float(getattr(settings, "inner_line_offset", 0.0)),
        material=outline_setup.get_line_material(obj, "inner"),
        use_marked_edges=False,
        midpoint_factor=midpoint_factor,
        midpoint_angle=inner_width_split_angle(settings),
        midpoint_jitter_percent=float(
            getattr(settings, "inner_edge_midpoint_jitter_percent", 0.0)
        ),
        width_curve_25=float(getattr(settings, "inner_edge_width_curve_25", 0.25)),
        width_curve_50=float(getattr(settings, "inner_edge_width_curve_50", 0.50)),
        width_curve_75=float(getattr(settings, "inner_edge_width_curve_75", 0.75)),
        enable=show_viewport or show_render,
    )
    repaired = obj.modifiers.get(GN_MODIFIER_NAME)
    if repaired is not None:
        repaired.show_viewport = show_viewport
        repaired.show_render = show_render
    return changed


def repair_scene_inner_lines(scene: bpy.types.Scene | None = None) -> int:
    if scene is not None:
        objects = list(scene.objects)
    else:
        if not _scene_data_available():
            return 0
        objects = list(bpy.data.objects)
    repaired = 0
    for obj in objects:
        try:
            if _repair_object(obj):
                repaired += 1
        except RuntimeError:
            continue
    return repaired


def _run_timer():
    global _timer_running
    if not _scene_data_available():
        return 0.1
    try:
        repair_scene_inner_lines()
    finally:
        _timer_running = False
    return None


def _queue() -> None:
    global _timer_running
    if _timer_running:
        return
    timers = getattr(bpy.app, "timers", None)
    if timers is None or getattr(timers, "register", None) is None:
        return
    _timer_running = True
    timers.register(_run_timer, first_interval=0.0)


def repair_now_or_later() -> None:
    if _scene_data_available():
        repair_scene_inner_lines()
        return
    _queue()


@bpy.app.handlers.persistent
def _on_load_post(_dummy):
    repair_now_or_later()


def register() -> None:
    repair_now_or_later()
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister() -> None:
    global _timer_running
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    timers = getattr(bpy.app, "timers", None)
    if timers is not None:
        try:
            if timers.is_registered(_run_timer):
                timers.unregister(_run_timer)
        except (AttributeError, ValueError):
            pass
    _timer_running = False
