"""ページ／コマのファイル切替と、切替元の表示内容変更を追跡する。"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib

import bpy
from bpy.app.handlers import persistent


_switch_depth = 0
_armed_scene_key = 0
_dirty_scene_keys: set[int] = set()
_dirty_reasons: dict[int, str] = {}
_clean_fingerprints: dict[int, str] = {}

_IGNORED_OBJECT_KINDS = {
    "page_preview",
    "paper_bg",
    "paper_guide",
    "coma_mask",
    "coma_border",
    "coma_plane",
}
_IGNORED_NAME_PREFIXES = (
    "BManga_PagePreview_",
    "page_preview_",
    "BManga_CompositePreview_",
)


def _scene_key(scene) -> int:
    if scene is None:
        return 0
    try:
        return int(scene.as_pointer())
    except Exception:  # noqa: BLE001
        return id(scene)


@contextmanager
def blend_switch():
    """save_pre/load_postを含む一連のファイル切替中であることを示す。"""

    global _switch_depth
    _switch_depth += 1
    try:
        yield
    finally:
        _switch_depth = max(0, _switch_depth - 1)


def switch_in_progress() -> bool:
    return _switch_depth > 0


def tracking_armed(scene=None) -> bool:
    scene = scene or getattr(bpy.context, "scene", None)
    return bool(_armed_scene_key and _armed_scene_key == _scene_key(scene))


def scene_content_dirty(scene=None) -> bool:
    """切替元の表示内容が読込完了後に変更されたか。未追跡時は安全側でTrue。"""

    scene = scene or getattr(bpy.context, "scene", None)
    key = _scene_key(scene)
    if not key or not tracking_armed(scene):
        return True
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        reason = _content_update_reason(scene, depsgraph)
        if reason:
            mark_scene_dirty(scene, reason=reason)
    except Exception:  # noqa: BLE001
        pass
    clean_fingerprint = _clean_fingerprints.get(key, "")
    current_fingerprint = _fast_content_fingerprint(scene)
    if (
        clean_fingerprint
        and current_fingerprint
        and clean_fingerprint != current_fingerprint
    ):
        mark_scene_dirty(scene, reason="content_fingerprint")
    return key in _dirty_scene_keys


def scene_dirty_reason(scene=None) -> str:
    scene = scene or getattr(bpy.context, "scene", None)
    return _dirty_reasons.get(_scene_key(scene), "")


def mark_scene_dirty(scene=None, *, reason: str = "") -> None:
    scene = scene or getattr(bpy.context, "scene", None)
    key = _scene_key(scene)
    if key:
        _dirty_scene_keys.add(key)
        if reason:
            _dirty_reasons[key] = str(reason)


def mark_scene_clean(scene=None) -> None:
    scene = scene or getattr(bpy.context, "scene", None)
    key = _scene_key(scene)
    if key:
        _dirty_scene_keys.discard(key)
        _dirty_reasons.pop(key, None)
        fingerprint = _fast_content_fingerprint(scene)
        if fingerprint:
            _clean_fingerprints[key] = fingerprint


def arm_scene(scene=None) -> None:
    """読込後のsceneを基準状態として追跡開始する。"""

    global _armed_scene_key
    scene = scene or getattr(bpy.context, "scene", None)
    _dirty_scene_keys.clear()
    _dirty_reasons.clear()
    _clean_fingerprints.clear()
    _armed_scene_key = _scene_key(scene)
    mark_scene_clean(scene)


def _simple_rna_value(value):
    if isinstance(value, (bool, int, float, str)) or value is None:
        return round(value, 9) if isinstance(value, float) else value
    try:
        converted = tuple(_simple_rna_value(item) for item in value)
        return converted if all(item is not None for item in converted) else None
    except TypeError:
        return None


def _material_state(material) -> tuple:
    nodes = getattr(getattr(material, "node_tree", None), "nodes", ()) or ()
    inputs = []
    for node in nodes:
        for socket in getattr(node, "inputs", ()) or ():
            if not hasattr(socket, "default_value"):
                continue
            value = _simple_rna_value(socket.default_value)
            if value is not None:
                inputs.append((node.name, socket.name, value))
    return (
        material.name,
        _simple_rna_value(getattr(material, "diffuse_color", None)),
        tuple(inputs),
    )


def _fast_content_fingerprint(scene) -> str:
    """巨大メッシュ本体を走査せず、即時変更される表示状態を要約する。"""

    if scene is None:
        return ""
    try:
        objects = []
        used_materials = set()
        for obj in getattr(scene, "objects", ()) or ():
            if _is_ignored_id(obj):
                continue
            material_names = tuple(
                getattr(slot.material, "name", "")
                for slot in getattr(obj, "material_slots", ()) or ()
            )
            used_materials.update(name for name in material_names if name)
            objects.append(
                (
                    obj.name,
                    obj.type,
                    _simple_rna_value(obj.location),
                    _simple_rna_value(obj.rotation_euler),
                    _simple_rna_value(obj.scale),
                    bool(getattr(obj, "hide_viewport", False)),
                    bool(getattr(obj, "hide_render", False)),
                    material_names,
                )
            )
        materials = tuple(
            _material_state(material)
            for material in getattr(bpy.data, "materials", ()) or ()
            if material.name in used_materials
        )
        raw = repr((tuple(objects), materials)).encode("utf-8")
        return hashlib.blake2b(raw, digest_size=16).hexdigest()
    except Exception:  # noqa: BLE001
        return ""


def _is_ignored_id(updated) -> bool:
    name = str(getattr(updated, "name", "") or "")
    if name.startswith(_IGNORED_NAME_PREFIXES):
        return True
    if isinstance(updated, bpy.types.Image):
        filepath = str(getattr(updated, "filepath", "") or "").replace("\\", "/")
        if (
            filepath.endswith("/page_preview.png")
            or filepath.endswith("/page_preview.detail.png")
            or "/_coma_bg_cache/" in filepath
        ):
            return True
    if isinstance(updated, bpy.types.Object):
        kind = str(updated.get("bmanga_kind", "") or "")
        if kind in _IGNORED_OBJECT_KINDS:
            return True
        if any(str(updated.get(prop, "") or "") for prop in (
            "bmanga_paper_bg_kind",
            "bmanga_paper_guide_kind",
            "bmanga_coma_mask_kind",
            "bmanga_coma_plane_kind",
        )):
            return True
    return False


def _content_update_reason(scene, depsgraph) -> str:
    try:
        from . import layer_object_sync, preview_composite

        if layer_object_sync.is_sync_in_progress() or preview_composite.get_service().rendering:
            return ""
    except Exception:  # noqa: BLE001
        pass
    for update in getattr(depsgraph, "updates", ()) or ():
        updated = getattr(update, "id", None)
        if updated is None or _is_ignored_id(updated):
            continue
        if isinstance(
            updated,
            (
                bpy.types.Object,
                bpy.types.Mesh,
                bpy.types.Curve,
                bpy.types.Camera,
                bpy.types.Light,
                bpy.types.Material,
                bpy.types.World,
                bpy.types.Image,
            ),
        ):
            return f"{type(updated).__name__}:{getattr(updated, 'name', '')}"
        for type_name in ("GreasePencil", "GreasePencilv3"):
            grease_pencil_type = getattr(bpy.types, type_name, None)
            if grease_pencil_type is not None and isinstance(
                updated,
                grease_pencil_type,
            ):
                return f"{type(updated).__name__}:{getattr(updated, 'name', '')}"
    return ""


@persistent
def _on_load_post(*_args) -> None:
    arm_scene()


@persistent
def _on_depsgraph_update_post(scene, depsgraph) -> None:
    if switch_in_progress() or not tracking_armed(scene):
        return
    reason = _content_update_reason(scene, depsgraph)
    if reason:
        mark_scene_dirty(scene, reason=reason)


@persistent
def _on_save_post(*_args) -> None:
    if not switch_in_progress():
        mark_scene_clean()


def _remove_named_handler(handlers, name: str) -> None:
    for handler in list(handlers):
        if getattr(handler, "__name__", "") == name:
            handlers.remove(handler)


def register() -> None:
    _remove_named_handler(bpy.app.handlers.load_post, _on_load_post.__name__)
    _remove_named_handler(
        bpy.app.handlers.depsgraph_update_post,
        _on_depsgraph_update_post.__name__,
    )
    _remove_named_handler(bpy.app.handlers.save_post, _on_save_post.__name__)
    bpy.app.handlers.load_post.append(_on_load_post)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update_post)
    bpy.app.handlers.save_post.append(_on_save_post)


def unregister() -> None:
    global _armed_scene_key, _switch_depth
    _remove_named_handler(bpy.app.handlers.load_post, _on_load_post.__name__)
    _remove_named_handler(
        bpy.app.handlers.depsgraph_update_post,
        _on_depsgraph_update_post.__name__,
    )
    _remove_named_handler(bpy.app.handlers.save_post, _on_save_post.__name__)
    _armed_scene_key = 0
    _switch_depth = 0
    _dirty_scene_keys.clear()
    _dirty_reasons.clear()
    _clean_fingerprints.clear()
