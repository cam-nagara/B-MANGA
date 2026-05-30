"""Interactive redraw/depsgraph probe for the B-Name page list flicker.

Run this from Blender's Text Editor or Python console while the B-Name sidebar
panel is visible and the flicker is happening. The script waits briefly, then
measures draw callbacks, depsgraph updates, and selected B-Name maintenance
calls for a few seconds. It restores all monkey patches automatically.
"""

from __future__ import annotations

import sys
import time
from collections import Counter

import bpy

DURATION_SECONDS = 3.0
START_DELAY_SECONDS = 0.5
KEY = "_bname_page_list_redraw_probe_cleanup"


old_cleanup = bpy.app.driver_namespace.get(KEY)
if callable(old_cleanup):
    old_cleanup()


state = {
    "running": False,
    "done": False,
    "start": 0.0,
    "draw": 0,
    "depsgraph_events": 0,
    "depsgraph_updates": 0,
    "draw_handle": None,
}
updated_ids: Counter[tuple[str, str, str]] = Counter()
internal_calls: Counter[str] = Counter()
wrapped: list[tuple[object, str, object]] = []


def _update_flags(update) -> str:
    flags: list[str] = []
    for attr, label in (
        ("is_updated_transform", "transform"),
        ("is_updated_geometry", "geometry"),
        ("is_updated_shading", "shading"),
    ):
        try:
            if bool(getattr(update, attr, False)):
                flags.append(label)
        except Exception:
            pass
    return "+".join(flags) if flags else "-"


def _id_key(id_block, update) -> tuple[str, str, str]:
    if id_block is None:
        return ("<none>", "<none>", _update_flags(update))
    type_name = type(id_block).__name__
    name = str(getattr(id_block, "name_full", "") or getattr(id_block, "name", "") or "")
    return (type_name, name, _update_flags(update))


def _on_depsgraph_update(scene, depsgraph) -> None:
    if not state["running"] or state["done"]:
        return
    state["depsgraph_events"] += 1
    try:
        for update in depsgraph.updates:
            state["depsgraph_updates"] += 1
            updated_ids[_id_key(getattr(update, "id", None), update)] += 1
    except Exception as exc:
        updated_ids[("<probe-error>", str(exc), "-")] += 1


def _draw_probe() -> None:
    if state["running"] and not state["done"]:
        state["draw"] += 1


def _wrap_module_function(module_suffix: str, func_name: str) -> None:
    for mod_name, mod in list(sys.modules.items()):
        if not mod_name.endswith(module_suffix):
            continue
        original = getattr(mod, func_name, None)
        if original is None or not callable(original):
            continue
        label = f"{module_suffix}.{func_name}"

        def wrapper(*args, __original=original, __label=label, **kwargs):
            if state["running"] and not state["done"]:
                internal_calls[__label] += 1
            return __original(*args, **kwargs)

        try:
            setattr(mod, func_name, wrapper)
            wrapped.append((mod, func_name, original))
        except Exception:
            pass


def _install_wrappers() -> None:
    targets = (
        (".utils.layer_stack", "sync_layer_stack"),
        (".utils.layer_stack", "schedule_layer_stack_draw_maintenance"),
        (".utils.layer_stack", "schedule_layer_stack_sync"),
        (".utils.layer_stack", "tag_view3d_redraw"),
        (".utils.paper_guide_object", "apply_view_constant_thickness"),
        (".utils.paper_guide_object", "repair_loaded_work_paper_guides"),
        (".utils.outliner_watch", "_scan_once"),
        (".utils.active_collection_sync", "_sync"),
        (".core.mode", "get_mode"),
    )
    for suffix, func_name in targets:
        _wrap_module_function(suffix, func_name)


def cleanup() -> None:
    state["done"] = True
    try:
        if _on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    except Exception:
        pass
    handle = state.get("draw_handle")
    if handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
        except Exception:
            pass
        state["draw_handle"] = None
    while wrapped:
        mod, func_name, original = wrapped.pop()
        try:
            setattr(mod, func_name, original)
        except Exception:
            pass
    if bpy.app.driver_namespace.get(KEY) is cleanup:
        bpy.app.driver_namespace.pop(KEY, None)


def _print_results() -> None:
    elapsed = max(0.001, time.perf_counter() - float(state["start"]))
    draw = int(state["draw"])
    deps_events = int(state["depsgraph_events"])
    deps_updates = int(state["depsgraph_updates"])
    print("=== BNAME_PAGE_LIST_REDRAW_PROBE_RESULT ===")
    print(f"elapsed_sec={elapsed:.2f}")
    print(f"draw_calls={draw} ({draw / elapsed:.2f}/sec)")
    print(f"depsgraph_events={deps_events} ({deps_events / elapsed:.2f}/sec)")
    print(f"depsgraph_updates={deps_updates} ({deps_updates / elapsed:.2f}/sec)")
    print("--- updated_ids_top ---")
    if updated_ids:
        for (type_name, name, flags), count in updated_ids.most_common(25):
            print(f"{count:5d}  {type_name}  {flags}  {name}")
    else:
        print("(none)")
    print("--- internal_calls_top ---")
    if internal_calls:
        for label, count in internal_calls.most_common(25):
            print(f"{count:5d}  {label}")
    else:
        print("(none)")
    if deps_events == 0 and draw > 0:
        print("diagnosis=redraw_without_depsgraph_update")
    elif deps_events > 0:
        print("diagnosis=depsgraph_update_loop_possible")
    else:
        print("diagnosis=no_idle_redraw_seen")
    print("=== END_BNAME_PAGE_LIST_REDRAW_PROBE_RESULT ===")


def _finish() -> None:
    try:
        _print_results()
    finally:
        cleanup()
    return None


def _begin() -> None:
    state["start"] = time.perf_counter()
    state["running"] = True
    print("BNAME_PAGE_LIST_REDRAW_PROBE_STARTED")
    bpy.app.timers.register(_finish, first_interval=DURATION_SECONDS)
    return None


_install_wrappers()
bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)
state["draw_handle"] = bpy.types.SpaceView3D.draw_handler_add(
    _draw_probe, (), "WINDOW", "POST_PIXEL"
)
bpy.app.driver_namespace[KEY] = cleanup
bpy.app.timers.register(_begin, first_interval=START_DELAY_SECONDS)
print("BNAME_PAGE_LIST_REDRAW_PROBE_ARMED")
