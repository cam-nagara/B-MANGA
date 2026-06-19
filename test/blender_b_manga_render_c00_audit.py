"""Blender 実機用: B-MANGA Render と c00.blend の連動監査."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BLEND = Path(r"D:\TM Dropbox\Share\B-MANGA\c_file\c00.blend")


def _load_render_package():
    package_root = ROOT / "addons" / "b_manga_render"
    spec = importlib.util.spec_from_file_location(
        "bmanga_render_audit",
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_render_audit"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _collection_names(collection, out):
    out.add(collection.name)
    for child in collection.children:
        _collection_names(child, out)


def _node_tree_key(node_tree):
    try:
        return int(node_tree.as_pointer())
    except Exception:  # noqa: BLE001
        return id(node_tree)


def _walk_nodes(node_tree, out, seen=None):
    if node_tree is None:
        return
    seen = set() if seen is None else seen
    key = _node_tree_key(node_tree)
    if key in seen:
        return
    seen.add(key)
    for node in getattr(node_tree, "nodes", []):
        parent = getattr(node, "parent", None)
        out.append(
            {
                "tree": node_tree.name,
                "name": node.name,
                "label": node.label,
                "type": node.type,
                "parent": getattr(parent, "label", "") or getattr(parent, "name", ""),
                "group": getattr(getattr(node, "node_tree", None), "name", ""),
                "inputs": [socket.name for socket in getattr(node, "inputs", [])],
                "outputs": [socket.name for socket in getattr(node, "outputs", [])],
                "slots": [slot.path for slot in getattr(node, "file_slots", [])],
                "directory": getattr(node, "directory", ""),
                "file_name": getattr(node, "file_name", ""),
                "file_output_items": [
                    getattr(item, "name", "")
                    for item in getattr(node, "file_output_items", [])
                ],
                "base_path": getattr(node, "base_path", ""),
            }
        )
        if getattr(node, "type", "") == "GROUP":
            _walk_nodes(getattr(node, "node_tree", None), out, seen)


def _required_from_presets(preset_library, core, *, include_legacy: bool = False, legacy_only: bool = False):
    required = {
        "presets": set(),
        "view_layers": set(),
        "collections": set(),
        "node_names": set(),
        "node_groups": set(),
        "aov_targets": set(),
        "inputs": set(),
        "operators": set(),
        "output_labels": set(),
    }
    for preset_name, commands in preset_library.BUILTIN_PRESETS.items():
        is_legacy = core.preset_category_of(preset_name) == "LEGACY"
        if legacy_only and not is_legacy:
            continue
        if not include_legacy and is_legacy:
            continue
        required["presets"].add(preset_name)
        for command in commands:
            kind = command.get("command_type", "")
            if kind == "SET_VIEW_LAYER":
                required["view_layers"].add(command.get("view_layer_name", ""))
            elif kind == "SET_COLLECTION_EXCLUDE":
                required["collections"].add(command.get("collection_name", ""))
            elif kind == "SET_NODE_MUTE":
                required["node_names"].add(command.get("node_name", ""))
            elif kind in {
                "SET_OUTPUT_GROUP",
                "RENDER_LAYER",
                "FISHEYE_RENDER_IMAGE_OR_LAYER",
                "FISHEYE_RENDER_FACES_OR_LAYER",
                "FISHEYE_ASSEMBLE_OR_LAYER",
            }:
                required["node_groups"].add(command.get("node_group_name", ""))
                required["output_labels"].add(command.get("label_contains", ""))
            elif kind == "SET_AOV_INPUT":
                required["aov_targets"].add(command.get("node_group_name", ""))
                required["inputs"].add(command.get("input_name", ""))
            elif kind == "OPERATOR":
                required["operators"].add(command.get("operator_idname", ""))
    return {key: sorted(value for value in values if value) for key, values in required.items()}


def _required_output_pairs(preset_library, core) -> list[dict[str, str]]:
    pairs: list[dict[str, str]] = []
    output_kinds = {
        "SET_OUTPUT_GROUP",
        "RENDER_LAYER",
        "FISHEYE_RENDER_IMAGE_OR_LAYER",
        "FISHEYE_RENDER_FACES_OR_LAYER",
        "FISHEYE_ASSEMBLE_OR_LAYER",
    }
    for preset_name, commands in preset_library.BUILTIN_PRESETS.items():
        if core.preset_category_of(preset_name) == "LEGACY":
            continue
        for command in commands:
            if command.get("command_type", "") not in output_kinds:
                continue
            group = str(command.get("node_group_name", "") or "")
            label = str(command.get("label_contains", "") or "")
            if group:
                pairs.append({"preset": preset_name, "group": group, "label": label})
    return pairs


def _count_named_input(node_tree, input_name: str) -> int:
    count = 0
    nodes = []
    _walk_nodes(node_tree, nodes)
    for item in nodes:
        count += item["inputs"].count(input_name)
    return count


def _count_aov_target(target_name: str, input_name: str) -> int:
    collection = bpy.data.collections.get(target_name)
    if collection is not None:
        count = 0
        for obj in collection.all_objects:
            if getattr(obj, "type", "") != "MESH":
                continue
            for slot in getattr(obj, "material_slots", []):
                material = getattr(slot, "material", None)
                if material is not None and getattr(material, "use_nodes", False):
                    count += _count_named_input(material.node_tree, input_name)
        return count

    count = 0
    for group in bpy.data.node_groups:
        if target_name and target_name not in group.name:
            continue
        count += _count_named_input(group, input_name)
    return count


def _count_template_aov_input(input_name: str) -> int:
    count = 0
    for material in bpy.data.materials:
        if getattr(material, "use_nodes", False):
            count += _count_named_input(material.node_tree, input_name)
    return count


def main() -> None:
    blend_path = Path(os.environ.get("BMANGA_C00_BLEND", str(DEFAULT_BLEND)))
    if not blend_path.exists():
        raise FileNotFoundError(blend_path)

    bpy.ops.wm.open_mainfile(filepath=str(blend_path))
    render = _load_render_package()
    render.register()
    required = _required_from_presets(render.preset_library, render.core)
    legacy_required = _required_from_presets(render.preset_library, render.core, include_legacy=True, legacy_only=True)
    output_pairs = _required_output_pairs(render.preset_library, render.core)

    scene = bpy.context.scene
    collections = set()
    _collection_names(scene.collection, collections)

    nodes = []
    scene_node_tree = getattr(scene, "node_tree", None) or getattr(scene, "compositing_node_group", None)
    if scene_node_tree is not None:
        _walk_nodes(scene_node_tree, nodes)
    for node_group in bpy.data.node_groups:
        _walk_nodes(node_group, nodes)
    for material in bpy.data.materials:
        if getattr(material, "use_nodes", False):
            _walk_nodes(material.node_tree, nodes)

    node_names = {item["name"] for item in nodes} | {item["label"] for item in nodes}
    node_group_names = {group.name for group in bpy.data.node_groups}
    input_names = {name for item in nodes for name in item["inputs"]}
    output_labels = set()
    for item in nodes:
        if item["type"] != "OUTPUT_FILE":
            continue
        output_labels.update({item["name"], item["label"], item["parent"]})
        output_labels.update(item["inputs"])
        output_labels.update(item["file_output_items"])
    view_layer_names = {layer.name for layer in scene.view_layers}
    output_pair_counts = {}
    output_pair_missing = []
    for pair in output_pairs:
        group = bpy.data.node_groups.get(pair["group"])
        count = 0
        if group is not None:
            for node in render.command_runner._iter_nodes_recursive(group):
                if getattr(node, "type", "") == "OUTPUT_FILE" and render.command_runner._node_matches_label(node, pair["label"]):
                    count += 1
        key = f"{pair['preset']} / {pair['group']} / {pair['label']}"
        output_pair_counts[key] = count
        if count <= 0:
            output_pair_missing.append(pair)

    camera = scene.camera
    camera_data = camera.data if camera is not None and camera.type == "CAMERA" else None
    bg_images = []
    if camera_data is not None:
        for bg in camera_data.background_images:
            bg_images.append(
                {
                    "image": getattr(bg.image, "name", ""),
                    "path": bpy.path.abspath(getattr(bg.image, "filepath", "")) if bg.image else "",
                    "show": bool(bg.show_background_image),
                    "opacity": float(bg.alpha),
                    "scale": float(bg.scale),
                    "depth": bg.display_depth,
                }
            )

    audit = {
        "blend": str(blend_path),
        "scene": scene.name,
        "render": {
            "engine": scene.render.engine,
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "use_nodes": bool(scene.use_nodes),
            "use_border": bool(scene.render.use_border),
            "filepath": scene.render.filepath,
        },
        "camera": {
            "object": getattr(camera, "name", ""),
            "data": getattr(camera_data, "name", ""),
            "type": getattr(camera_data, "type", ""),
            "lens": getattr(camera_data, "lens", None),
            "fisheye_fov": getattr(camera_data, "fisheye_fov", None),
            "shift": [getattr(camera_data, "shift_x", None), getattr(camera_data, "shift_y", None)],
            "background_images": bg_images,
        },
        "scene_props": sorted(scene.keys()),
        "has_rna_props": {
            "fisheye_layout_mode": hasattr(scene, "fisheye_layout_mode"),
            "reduction_mode": hasattr(scene, "reduction_mode"),
            "preview_scale_percentage": hasattr(scene, "preview_scale_percentage"),
            "my_tool": hasattr(scene, "my_tool"),
            "eeVR": hasattr(scene, "eeVR"),
        },
        "placeholder_presets": sorted(
            preset_name
            for preset_name, commands in render.preset_library.BUILTIN_PRESETS.items()
            if any(str(command.get("name", "")).startswith("未設定") for command in commands)
        ),
        "counts": {
            "view_layers": len(view_layer_names),
            "collections": len(collections),
            "node_groups": len(node_group_names),
            "nodes": len(nodes),
        },
        "required": required,
        "legacy_required": legacy_required,
        "existing": {
            "view_layers": sorted(view_layer_names),
            "collections": sorted(collections),
            "node_groups": sorted(node_group_names),
            "output_labels": sorted(label for label in output_labels if label),
        },
        "missing": {
            "view_layers": sorted(set(required["view_layers"]) - view_layer_names),
            "collections": sorted(set(required["collections"]) - collections),
            "node_names": sorted(set(required["node_names"]) - node_names),
            "node_groups": sorted(set(required["node_groups"]) - node_group_names),
            "aov_targets": sorted(
                target
                for target in required["aov_targets"]
                if target not in collections and target not in node_group_names
            ),
            "inputs": sorted(set(required["inputs"]) - input_names),
            "output_labels": sorted(
                label
                for label in required["output_labels"]
                if not any(label in existing for existing in output_labels)
            ),
            "output_pairs": output_pair_missing,
        },
        "output_pair_match_counts": output_pair_counts,
        "aov_target_socket_counts": {
            f"{target}/{input_name}": _count_aov_target(target, input_name)
            for target in required["aov_targets"]
            for input_name in required["inputs"]
        },
        "aov_template_socket_counts": {
            input_name: _count_template_aov_input(input_name)
            for input_name in required["inputs"]
        },
        "file_output_count": len([item for item in nodes if item["type"] == "OUTPUT_FILE"]),
    }
    if os.environ.get("BMANGA_AUDIT_FULL") == "1":
        audit["file_outputs"] = [item for item in nodes if item["type"] == "OUTPUT_FILE"]

    print("BMANGA_RENDER_C00_AUDIT_JSON_START")
    print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
    print("BMANGA_RENDER_C00_AUDIT_JSON_END")
    render.unregister()

    blocking_missing = {
        key: audit["missing"][key]
        for key in ("view_layers", "collections", "node_names", "node_groups", "output_labels", "output_pairs")
        if audit["missing"][key]
    }
    assert not blocking_missing, blocking_missing
    assert not audit["placeholder_presets"], audit["placeholder_presets"]
    assert all(count > 0 for count in audit["aov_template_socket_counts"].values()), audit["aov_template_socket_counts"]
    for prop_name in ("fisheye_layout_mode", "reduction_mode", "preview_scale_percentage", "my_tool"):
        assert audit["has_rna_props"][prop_name] is True, prop_name


if __name__ == "__main__":
    main()
