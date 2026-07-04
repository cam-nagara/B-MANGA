"""B-MANGA Line midpoint subdivision setup."""

from __future__ import annotations

import math

import bmesh
import bpy
from bpy.app.handlers import persistent


AUTO_SUBSURF_MODIFIER_NAME = "BML_MidpointSubsurf"
AUTO_SUBSURF_CREASE_EDGES_PROP = "bml_auto_midpoint_subsurf_crease_edges"
CREASE_EDGE_ATTR = "crease_edge"
SHARP_EDGE_ANGLE = math.radians(60.0)
MAX_RENDER_LEVELS = 4
DISTANCE_STEP_METERS = 5.0
DEFAULT_LINE_RESAMPLE_COUNT = 17
_MIN_LINE_RESAMPLE_COUNT = 3
_ROUND_LOOP_RESAMPLE_CAP = 96
_pending_sync_names: set[str] = set()
_sync_timer_running = False


def is_auto_subsurf_modifier(mod: bpy.types.Modifier | None) -> bool:
    return (
        mod is not None
        and mod.type == "SUBSURF"
        and (
            mod.name == AUTO_SUBSURF_MODIFIER_NAME
            or mod.name.startswith(AUTO_SUBSURF_MODIFIER_NAME + ".")
        )
    )


def render_levels_for_distance(distance: float) -> int:
    if distance < 0.0:
        distance = 0.0
    level = MAX_RENDER_LEVELS - int(distance // DISTANCE_STEP_METERS)
    return max(0, min(MAX_RENDER_LEVELS, level))


def sync_viewport_levels_to_render(obj: bpy.types.Object) -> int:
    """選択メッシュのSubsurfのビューポートレベルをレンダーレベルへ揃える."""
    if obj.type != "MESH":
        return 0
    changed = 0
    for mod in obj.modifiers:
        if mod.type != "SUBSURF":
            continue
        render_levels = max(0, int(getattr(mod, "render_levels", 0)))
        if int(getattr(mod, "levels", 0)) == render_levels:
            continue
        mod.levels = render_levels
        changed += 1
    if changed:
        sync_generated_line_subdivision(obj)
        try:
            from . import intersection_shell

            intersection_shell.sync_proxy_subdivision_for_target(obj)
        except Exception:  # noqa: BLE001 - 交差線プロキシが無い場合も通常操作を止めない
            pass
    return changed


def reset_viewport_levels_to_zero(obj: bpy.types.Object) -> int:
    """選択メッシュのSubsurfのビューポートレベルを0へ戻す."""
    if obj.type != "MESH":
        return 0
    changed = 0
    for mod in obj.modifiers:
        if mod.type != "SUBSURF":
            continue
        if int(getattr(mod, "levels", 0)) == 0:
            continue
        mod.levels = 0
        changed += 1
    if changed:
        sync_generated_line_subdivision(obj)
        try:
            from . import intersection_shell

            intersection_shell.sync_proxy_subdivision_for_target(obj)
        except Exception:  # noqa: BLE001 - 交差線プロキシが無い場合も通常操作を止めない
            pass
    return changed


def _line_camera(scene) -> bpy.types.Object | None:
    if scene is None:
        return None
    try:
        from . import camera_comp

        return camera_comp.get_line_camera(scene)
    except Exception:  # noqa: BLE001 - カメラ取得失敗時は既定密度で続行
        return getattr(scene, "camera", None)


def _distance_to_camera(obj: bpy.types.Object, scene) -> float:
    camera = _line_camera(scene)
    if camera is None:
        return 0.0
    return float((camera.matrix_world.translation - obj.matrix_world.translation).length)


def auto_subsurf_modifier(obj: bpy.types.Object) -> bpy.types.Modifier | None:
    for mod in obj.modifiers:
        if is_auto_subsurf_modifier(mod):
            return mod
    return None


def _ensure_crease_attribute(mesh: bpy.types.Mesh):
    attr = mesh.attributes.get(CREASE_EDGE_ATTR)
    if attr is None:
        attr = mesh.attributes.new(CREASE_EDGE_ATTR, "FLOAT", "EDGE")
    return attr


def mark_sharp_edges_for_subsurf(
    obj: bpy.types.Object,
    threshold: float = SHARP_EDGE_ANGLE,
) -> int:
    """Set edge crease 1.0 for mesh edges sharper than the threshold."""
    if obj.type != "MESH" or obj.data is None:
        return 0
    mesh = obj.data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.edges.ensure_lookup_table()
        sharp_indices: list[int] = []
        for edge in bm.edges:
            if len(edge.link_faces) < 2:
                sharp_indices.append(edge.index)
                continue
            try:
                if edge.calc_face_angle() >= threshold:
                    sharp_indices.append(edge.index)
            except ValueError:
                continue
    finally:
        bm.free()

    if not sharp_indices:
        obj[AUTO_SUBSURF_CREASE_EDGES_PROP] = []
        return 0

    attr = _ensure_crease_attribute(mesh)
    for edge_index in sharp_indices:
        if edge_index < len(attr.data):
            attr.data[edge_index].value = 1.0
    obj[AUTO_SUBSURF_CREASE_EDGES_PROP] = sharp_indices
    mesh.update()
    return len(sharp_indices)


def ensure_auto_subdivision(obj: bpy.types.Object, scene) -> bpy.types.Modifier | None:
    """Create/update the auto Subdivision Surface modifier used by midpoint widths."""
    if obj.type != "MESH" or obj.data is None:
        return None

    mark_sharp_edges_for_subsurf(obj)
    mod = auto_subsurf_modifier(obj)
    if mod is None:
        mod = obj.modifiers.new(AUTO_SUBSURF_MODIFIER_NAME, "SUBSURF")

    if hasattr(mod, "subdivision_type"):
        mod.subdivision_type = "SIMPLE"
    mod.levels = 0
    mod.render_levels = render_levels_for_distance(_distance_to_camera(obj, scene))
    mod.show_viewport = True
    mod.show_render = True
    try:
        from . import modifier_stack

        modifier_stack.reorder_line_modifiers(obj)
    except Exception:  # noqa: BLE001 - 順序修復に失敗しても設定自体は残す
        pass
    sync_generated_line_subdivision(obj)
    return mod


def remove_auto_subdivision(obj: bpy.types.Object) -> bool:
    if obj.type != "MESH":
        return False
    removed = False
    for mod in list(obj.modifiers):
        if is_auto_subsurf_modifier(mod):
            obj.modifiers.remove(mod)
            removed = True
    if AUTO_SUBSURF_CREASE_EDGES_PROP in obj:
        del obj[AUTO_SUBSURF_CREASE_EDGES_PROP]
    sync_generated_line_subdivision(obj)
    return removed


def line_resample_count(obj: bpy.types.Object, *, for_render: bool = False) -> int:
    mod = auto_subsurf_modifier(obj)
    if mod is None:
        return max(DEFAULT_LINE_RESAMPLE_COUNT, _closed_loop_min_resample_count(obj))
    level = int(getattr(mod, "render_levels", 0) if for_render else getattr(mod, "levels", 0))
    if for_render and not bool(getattr(mod, "show_render", True)):
        level = 0
    if not for_render and not bool(getattr(mod, "show_viewport", True)):
        level = 0
    level = max(0, min(MAX_RENDER_LEVELS, level))
    return max(_MIN_LINE_RESAMPLE_COUNT, (2 ** level) + 1, _closed_loop_min_resample_count(obj))


def _closed_loop_min_resample_count(obj: bpy.types.Object) -> int:
    """Prevent round rim loops from being resampled into triangles at low LOD."""
    if obj.type != "MESH" or obj.data is None:
        return _MIN_LINE_RESAMPLE_COUNT
    try:
        from . import inner_line_chains
    except Exception:  # noqa: BLE001
        return _MIN_LINE_RESAMPLE_COUNT
    mesh = obj.data
    attr = mesh.attributes.get(inner_line_chains.CHAIN_ID_ATTR)
    if attr is None or getattr(attr, "domain", None) != "EDGE":
        return _MIN_LINE_RESAMPLE_COUNT
    chains: dict[int, list[bpy.types.MeshEdge]] = {}
    for edge in mesh.edges:
        if edge.index >= len(attr.data):
            continue
        chain_id = int(getattr(attr.data[edge.index], "value", -1))
        if chain_id < 0:
            continue
        chains.setdefault(chain_id, []).append(edge)
    minimum = _MIN_LINE_RESAMPLE_COUNT
    for edges in chains.values():
        if len(edges) < 3:
            continue
        degrees: dict[int, int] = {}
        for edge in edges:
            for vertex_index in edge.vertices:
                degrees[vertex_index] = degrees.get(vertex_index, 0) + 1
        if degrees and all(degree == 2 for degree in degrees.values()):
            minimum = max(minimum, min(_ROUND_LOOP_RESAMPLE_CAP, len(edges) + 1))
    return minimum


def sync_generated_line_subdivision(
    obj: bpy.types.Object,
    *,
    for_render: bool = False,
) -> bool:
    if obj.type != "MESH":
        return False
    try:
        from . import core, inner_lines

        mod = obj.modifiers.get(core.GN_MODIFIER_NAME)
        if mod is None or mod.node_group is None:
            return False
        sid = inner_lines._find_socket_id(mod.node_group, "線の分割数")
        if sid is None:
            return False
        count = line_resample_count(obj, for_render=for_render)
        try:
            current = int(mod[sid])
        except (KeyError, TypeError, ValueError):
            current = -1
        if current == count:
            return False
        return bool(inner_lines.update_parameters(obj, resample_count=count))
    except Exception:  # noqa: BLE001 - 同期失敗時も通常操作を止めない
        return False


def sync_scene_generated_line_subdivision(
    scene: bpy.types.Scene | None,
    *,
    for_render: bool = False,
) -> int:
    if scene is None:
        return 0
    changed = 0
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        if sync_generated_line_subdivision(obj, for_render=for_render):
            changed += 1
    return changed


def _queue_sync(obj: bpy.types.Object) -> None:
    global _sync_timer_running
    if obj.type != "MESH":
        return
    _pending_sync_names.add(obj.name_full)
    if not _sync_timer_running:
        _sync_timer_running = True
        bpy.app.timers.register(_run_sync_timer, first_interval=0.0)


def _run_sync_timer():
    global _sync_timer_running
    names = list(_pending_sync_names)
    _pending_sync_names.clear()
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != "MESH":
            continue
        sync_generated_line_subdivision(obj)
        try:
            from . import intersection_shell

            intersection_shell.sync_proxy_subdivision_for_target(obj)
        except Exception:  # noqa: BLE001
            pass
    _sync_timer_running = False
    return None


@persistent
def _on_depsgraph_update(_scene, depsgraph=None):
    if depsgraph is None:
        return
    for update in getattr(depsgraph, "updates", ()):
        item = getattr(update, "id", None)
        if isinstance(item, bpy.types.Object) and item.type == "MESH":
            if auto_subsurf_modifier(item) is not None:
                _queue_sync(item)


@persistent
def _on_render_pre(scene, _depsgraph=None):
    sync_scene_generated_line_subdivision(scene, for_render=True)


@persistent
def _on_render_done(scene, _depsgraph=None):
    sync_scene_generated_line_subdivision(scene, for_render=False)


def _append_once(handler_list, handler) -> None:
    if handler not in handler_list:
        handler_list.append(handler)


def _remove(handler_list, handler) -> None:
    if handler in handler_list:
        handler_list.remove(handler)


def register() -> None:
    _append_once(bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update)
    _append_once(bpy.app.handlers.render_pre, _on_render_pre)
    _append_once(bpy.app.handlers.render_post, _on_render_done)
    _append_once(bpy.app.handlers.render_cancel, _on_render_done)


def unregister() -> None:
    global _sync_timer_running
    _remove(bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update)
    _remove(bpy.app.handlers.render_pre, _on_render_pre)
    _remove(bpy.app.handlers.render_post, _on_render_done)
    _remove(bpy.app.handlers.render_cancel, _on_render_done)
    if bpy.app.timers.is_registered(_run_sync_timer):
        bpy.app.timers.unregister(_run_sync_timer)
    _pending_sync_names.clear()
    _sync_timer_running = False
