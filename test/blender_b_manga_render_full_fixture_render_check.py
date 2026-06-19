"""Blender実機用: 全プリセットを専用fixtureで実レンダー確認する."""

from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import shutil
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BLEND = Path(r"D:\TM Dropbox\Share\B-MANGA\c_file\c00.blend")
_OUT_DIR_RAW = Path(os.environ.get("BMANGA_RENDER_FULL_FIXTURE_OUT", "") or ROOT / ".codex" / "ai_audit" / "bmanga_render_full_fixture")
OUT_DIR = _OUT_DIR_RAW if _OUT_DIR_RAW.is_absolute() else ROOT / _OUT_DIR_RAW
RESOLUTION = int(os.environ.get("BMANGA_RENDER_FULL_FIXTURE_RES", "48"))
SAMPLES = int(os.environ.get("BMANGA_RENDER_FULL_FIXTURE_SAMPLES", "1"))
PRESET_REGEX = os.environ.get("BMANGA_RENDER_FULL_FIXTURE_PRESET_REGEX", "").strip()
MODE_FILTER = {
    item.strip().lower()
    for item in os.environ.get("BMANGA_RENDER_FULL_FIXTURE_MODES", "normal,fisheye").split(",")
    if item.strip()
}
FLOW_ENABLED = os.environ.get("BMANGA_RENDER_FULL_FIXTURE_FLOW", "1").strip() != "0"
PREFIX = "BMANGA_RENDER_FIXTURE"
RENDER_COMMANDS = {
    "RENDER",
    "RENDER_LAYER",
    "FISHEYE_RENDER_IMAGE_OR_LAYER",
    "FISHEYE_RENDER_FACES_OR_LAYER",
    "FISHEYE_ASSEMBLE_OR_LAYER",
    "EEVR_RENDER_IMAGE",
    "EEVR_RENDER_FACES",
    "EEVR_ASSEMBLE",
}
FISHEYE_COMMANDS = {
    "FISHEYE_RENDER_IMAGE_OR_LAYER",
    "FISHEYE_RENDER_FACES_OR_LAYER",
    "FISHEYE_ASSEMBLE_OR_LAYER",
    "EEVR_RENDER_IMAGE",
    "EEVR_RENDER_FACES",
    "EEVR_ASSEMBLE",
}


def _load_render_package():
    package_root = ROOT / "addons" / "b_manga_render"
    spec = importlib.util.spec_from_file_location(
        "bmanga_render_full_fixture",
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_render_full_fixture"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _ensure_pillow_path() -> None:
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    for candidate in (
        ROOT / "wheels" / "_installed" / f"pillow-12.2.0-{tag}-{tag}-win_amd64",
        ROOT / "wheels" / f"pillow-12.2.0-{tag}-{tag}-win_amd64.whl",
        ROOT / "wheels" / "_installed" / "pillow-12.2.0-cp313-cp313-win_amd64",
        ROOT / "wheels" / "pillow-12.2.0-cp313-cp313-win_amd64.whl",
        ROOT / "wheels" / "_installed" / "pillow-12.2.0-cp312-cp312-win_amd64",
        ROOT / "wheels" / "pillow-12.2.0-cp312-cp312-win_amd64.whl",
        ROOT / "wheels" / "_installed" / "pillow-12.2.0-cp311-cp311-win_amd64",
        ROOT / "wheels" / "pillow-12.2.0-cp311-cp311-win_amd64.whl",
    ):
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
            return


def _safe_name(value: str) -> str:
    return re.sub(r"[\\/:*?\"<>|\s]+", "_", str(value or "").strip()) or "preset"


def _preset_command_types(preset) -> set[str]:
    return {str(getattr(command, "command_type", "") or "") for command in preset.commands if bool(getattr(command, "enabled", True))}


def _required_names(preset_library) -> tuple[set[str], set[str], set[str]]:
    view_layers: set[str] = set()
    collections: set[str] = set()
    node_names: set[str] = set()
    for commands in preset_library.BUILTIN_PRESETS.values():
        for command in commands:
            kind = command.get("command_type", "")
            if kind == "SET_VIEW_LAYER" and command.get("view_layer_name"):
                view_layers.add(command["view_layer_name"])
            elif kind == "SET_COLLECTION_EXCLUDE" and command.get("collection_name"):
                collections.add(command["collection_name"])
            elif kind == "SET_NODE_MUTE" and command.get("node_name"):
                node_names.add(command["node_name"])
            elif kind in {"SET_AOV_INPUT"} and command.get("node_group_name"):
                collections.add(command["node_group_name"])
    collections.update(view_layers)
    collections.update(
        {
            "キャラ",
            "キャラアルファ",
            "背景",
            "背景MH",
            "効果",
            "効果アルファ",
            "レイアウト",
            "アタリ",
            "空",
            "植物",
            "エフェクト",
            "フォグ",
            "雲",
            "グラデ_白",
            "グラデ_黒",
            "コマ枠",
            "Zパース",
            "Xパース",
            "Yパース",
        }
    )
    return view_layers, collections, node_names


def _collection(name: str):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if hasattr(coll, "hide_render"):
        coll.hide_render = False
    if hasattr(coll, "hide_viewport"):
        coll.hide_viewport = False
    scene = bpy.context.scene
    if coll.name not in scene.collection.children:
        try:
            scene.collection.children.link(coll)
        except RuntimeError:
            pass
    return coll


def _set_layer_collection_visible(layer_collection) -> None:
    if hasattr(layer_collection, "exclude"):
        layer_collection.exclude = False
    if hasattr(layer_collection, "hide_viewport"):
        layer_collection.hide_viewport = False
    if hasattr(layer_collection, "holdout"):
        layer_collection.holdout = False
    if hasattr(layer_collection, "indirect_only"):
        layer_collection.indirect_only = False
    for child in getattr(layer_collection, "children", []):
        _set_layer_collection_visible(child)


def _show_all_view_layers() -> None:
    scene = bpy.context.scene
    for layer in scene.view_layers:
        if hasattr(layer, "use"):
            layer.use = True
        if hasattr(layer, "use_pass_combined"):
            layer.use_pass_combined = True
        _set_layer_collection_visible(layer.layer_collection)


def _ensure_view_layers(view_layer_names: set[str]) -> None:
    scene = bpy.context.scene
    if view_layer_names and scene.view_layers[0].name not in view_layer_names:
        scene.view_layers[0].name = sorted(view_layer_names)[0]
    for name in sorted(view_layer_names):
        if scene.view_layers.get(name) is None:
            scene.view_layers.new(name=name)
    _show_all_view_layers()


def _link_to_scene_root(obj) -> None:
    scene = bpy.context.scene
    if obj.name not in scene.collection.objects:
        try:
            scene.collection.objects.link(obj)
        except RuntimeError:
            pass


def _make_object_render_visible(obj) -> None:
    obj.hide_render = False
    obj.hide_viewport = False
    for attr in ("visible_camera", "visible_diffuse", "visible_glossy", "visible_transmission", "visible_volume_scatter", "visible_shadow"):
        if hasattr(obj, attr):
            try:
                setattr(obj, attr, True)
            except Exception:  # noqa: BLE001
                pass


def _clear_fixture_objects() -> None:
    for obj in list(bpy.data.objects):
        if obj.name.startswith(PREFIX):
            bpy.data.objects.remove(obj, do_unlink=True)
        else:
            try:
                obj.hide_render = True
                obj.hide_viewport = True
            except Exception:  # noqa: BLE001
                pass
    for data_block in (bpy.data.meshes, bpy.data.curves, bpy.data.lights):
        for item in list(data_block):
            if item.name.startswith(PREFIX):
                data_block.remove(item)


def _new_emission_material(name: str, color: tuple[float, float, float, float]):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    for node in list(nodes):
        nodes.remove(node)
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = 1.6
    mat.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return mat


def _add_aov_inputs_to_material(mat) -> None:
    group = bpy.data.node_groups.get(f"{PREFIX}_AOV_INPUTS")
    if group is None:
        group = bpy.data.node_groups.new(f"{PREFIX}_AOV_INPUTS", "ShaderNodeTree")
        try:
            group.interface.new_socket(name="落ち影切替", in_out="INPUT", socket_type="NodeSocketFloat")
            group.interface.new_socket(name="透過切替", in_out="INPUT", socket_type="NodeSocketFloat")
        except Exception:  # noqa: BLE001
            group.inputs.new("NodeSocketFloat", "落ち影切替")
            group.inputs.new("NodeSocketFloat", "透過切替")
    node = mat.node_tree.nodes.new("ShaderNodeGroup")
    node.name = f"{PREFIX}_AOV"
    node.label = "AOV切替"
    node.node_tree = group


def _material_map() -> dict[str, object]:
    colors = {
        "レイアウト": (1.00, 0.20, 0.20, 1.0),
        "アタリ": (1.00, 0.55, 0.10, 1.0),
        "キャラ": (0.15, 0.80, 0.25, 1.0),
        "キャラアルファ": (0.15, 0.80, 0.25, 0.45),
        "背景": (0.20, 0.45, 1.00, 1.0),
        "背景MH": (0.10, 0.35, 0.95, 1.0),
        "効果": (0.90, 0.20, 1.00, 1.0),
        "効果アルファ": (0.90, 0.20, 1.00, 0.45),
        "空": (0.25, 0.85, 1.00, 1.0),
        "植物": (0.05, 0.65, 0.16, 1.0),
        "エフェクト": (0.80, 0.25, 1.00, 1.0),
        "フォグ": (0.70, 0.72, 0.85, 0.75),
        "雲": (0.90, 0.94, 1.00, 1.0),
        "グラデ_白": (1.00, 1.00, 1.00, 1.0),
        "グラデ_黒": (0.04, 0.04, 0.04, 1.0),
        "コマ枠": (0.00, 0.00, 0.00, 1.0),
        "Zパース": (1.00, 0.15, 0.15, 1.0),
        "Xパース": (0.15, 1.00, 0.15, 1.0),
        "Yパース": (0.15, 0.30, 1.00, 1.0),
    }
    mats = {name: _new_emission_material(f"{PREFIX}_MAT_{name}", color) for name, color in colors.items()}
    _add_aov_inputs_to_material(mats["キャラ"])
    _add_aov_inputs_to_material(mats["背景MH"])
    return mats


def _add_cube(collection_name: str, mat, location: tuple[float, float, float], scale: tuple[float, float, float]) -> None:
    mesh = bpy.data.meshes.new(f"{PREFIX}_MESH_{collection_name}")
    sx, sy, sz = scale
    verts = [
        (-sx, -sy, -sz),
        (sx, -sy, -sz),
        (sx, sy, -sz),
        (-sx, sy, -sz),
        (-sx, -sy, sz),
        (sx, -sy, sz),
        (sx, sy, sz),
        (-sx, sy, sz),
    ]
    faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(f"{PREFIX}_{collection_name}", mesh)
    obj.location = location
    obj.data.materials.append(mat)
    _make_object_render_visible(obj)
    _collection(collection_name).objects.link(obj)
    _link_to_scene_root(obj)


def _add_label(collection_name: str, mat, text: str, location: tuple[float, float, float]) -> None:
    curve = bpy.data.curves.new(f"{PREFIX}_TEXT_{collection_name}", "FONT")
    curve.body = text
    curve.align_x = "CENTER"
    curve.align_y = "CENTER"
    curve.size = 0.28
    obj = bpy.data.objects.new(f"{PREFIX}_LABEL_{collection_name}", curve)
    obj.location = location
    obj.rotation_euler = (math.radians(67), 0.0, 0.0)
    obj.data.materials.append(mat)
    _make_object_render_visible(obj)
    _collection(collection_name).objects.link(obj)
    _link_to_scene_root(obj)


def _add_fisheye_marker(name: str, mat, location: tuple[float, float, float]) -> None:
    mesh = bpy.data.meshes.new(f"{PREFIX}_FISHEYE_MESH_{name}")
    size = 1.35
    verts = [(0, 0, size), (-size, -size, -size), (size, -size, -size), (size, size, -size), (-size, size, -size)]
    faces = [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 1), (1, 4, 3, 2)]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(f"{PREFIX}_FISHEYE_{name}", mesh)
    obj.location = location
    obj.data.materials.append(mat)
    _make_object_render_visible(obj)
    for coll_name in ("キャラ", "キャラアルファ", "背景", "背景MH", "効果", "効果アルファ", "レイアウト", "アタリ"):
        _collection(coll_name).objects.link(obj)
    _link_to_scene_root(obj)


def _build_fixture_scene(render_mod) -> Path:
    view_layers, collections, _node_names = _required_names(render_mod.preset_library)
    for name in collections:
        _collection(name)
    _ensure_view_layers(view_layers)
    _clear_fixture_objects()
    mats = _material_map()
    ordered = [
        "レイアウト",
        "アタリ",
        "キャラ",
        "キャラアルファ",
        "背景",
        "背景MH",
        "効果",
        "効果アルファ",
        "空",
        "植物",
        "エフェクト",
        "フォグ",
        "雲",
        "グラデ_白",
        "グラデ_黒",
        "コマ枠",
        "Zパース",
        "Xパース",
        "Yパース",
    ]
    for index, name in enumerate(ordered):
        x = (index % 7 - 3) * 1.15
        y = (index // 7 - 1) * 1.05
        z = 0.12 + 0.06 * (index % 3)
        _add_cube(name, mats[name], (x, y, z), (0.34, 0.34, 0.34))
        _add_label(name, mats[name], name, (x, y, z + 0.65))
    _add_fisheye_marker("FRONT", mats["キャラ"], (0.0, -2.5, 0.0))
    _add_fisheye_marker("RIGHT", mats["背景"], (2.5, 0.0, 0.0))
    _add_fisheye_marker("BACK", mats["効果"], (0.0, 2.5, 0.0))
    _add_fisheye_marker("LEFT", mats["レイアウト"], (-2.5, 0.0, 0.0))
    _add_fisheye_marker("TOP", mats["グラデ_白"], (0.0, 0.0, 2.5))
    _add_fisheye_marker("BOTTOM", mats["グラデ_黒"], (0.0, 0.0, -2.5))
    _ensure_view_layers(view_layers)
    _ensure_fixture_camera()
    _configure_render_settings()
    fixture_path = OUT_DIR / "b_manga_render_full_fixture.blend"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(fixture_path))
    return fixture_path


def _ensure_fixture_camera() -> None:
    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new(f"{PREFIX}_CAMERA_DATA")
    cam = bpy.data.objects.new(f"{PREFIX}_CAMERA", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.location = (0.0, -9.0, 4.2)
    direction = Vector((0.0, 0.0, 0.0)) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = 28.0
    cam.data.clip_end = 1000.0
    light_data = bpy.data.lights.new(f"{PREFIX}_LIGHT_DATA", "AREA")
    light = bpy.data.objects.new(f"{PREFIX}_LIGHT", light_data)
    light.location = (0.0, -4.5, 7.0)
    light_data.energy = 500.0
    light_data.size = 5.0
    scene.collection.objects.link(light)


def _configure_render_settings() -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.render.resolution_x = RESOLUTION
    scene.render.resolution_y = RESOLUTION
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.use_border = False
    if hasattr(scene.render, "use_crop_to_border"):
        scene.render.use_crop_to_border = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    if hasattr(scene, "cycles"):
        scene.cycles.samples = SAMPLES
        scene.cycles.use_denoising = False
        scene.cycles.max_bounces = 1
        scene.cycles.diffuse_bounces = 1
        scene.cycles.glossy_bounces = 1
        scene.cycles.transparent_max_bounces = 1
    if hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = SAMPLES
    _show_all_view_layers()
    scene.original_resolution_x = RESOLUTION
    scene.original_resolution_y = RESOLUTION
    scene.preview_scale_percentage = 100.0
    scene.reduction_mode = False
    scene.fisheye_layout_mode = False
    scene.fisheye_fov = math.pi


def _set_fisheye_camera(enabled: bool) -> None:
    scene = bpy.context.scene
    cam = scene.camera
    if cam is None:
        return
    if enabled:
        cam.location = (0.0, 0.0, 0.0)
        cam.rotation_euler = (math.radians(90.0), 0.0, 0.0)
        cam.data.type = "PANO"
        if hasattr(cam.data, "panorama_type"):
            cam.data.panorama_type = "FISHEYE_EQUISOLID"
        if hasattr(cam.data, "fisheye_fov"):
            cam.data.fisheye_fov = math.pi
    else:
        cam.data.type = "PERSP"
        cam.location = (0.0, -9.0, 4.2)
        direction = Vector((0.0, 0.0, 0.0)) - cam.location
        cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _render_fixture_probe(name: str, *, fisheye: bool) -> dict:
    scene = bpy.context.scene
    cam = scene.camera
    previous_use_nodes = bool(getattr(scene, "use_nodes", False)) if hasattr(scene, "use_nodes") else None
    previous_compositing_node_group = getattr(scene, "compositing_node_group", None) if hasattr(scene, "compositing_node_group") else None
    previous_path = str(scene.render.filepath or "")
    previous_format = str(scene.render.image_settings.file_format or "")
    previous_camera_state = None
    if cam is not None:
        previous_camera_state = (
            cam.location.copy(),
            cam.rotation_euler.copy(),
            getattr(cam.data, "type", ""),
            getattr(cam.data, "panorama_type", ""),
            float(getattr(cam.data, "fisheye_fov", 0.0) or 0.0),
        )
    try:
        _configure_render_settings()
        _set_fisheye_camera(fisheye)
        _show_all_view_layers()
        if previous_use_nodes is not None:
            scene.use_nodes = False
        if hasattr(scene, "compositing_node_group"):
            scene.compositing_node_group = None
        path = OUT_DIR / "fixture_probe" / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        scene.render.filepath = str(path)
        scene.render.image_settings.file_format = "PNG"
        bpy.ops.render.render(write_still=True)
        return _image_stats(path)
    finally:
        scene.render.filepath = previous_path
        if previous_format:
            scene.render.image_settings.file_format = previous_format
        if previous_use_nodes is not None:
            scene.use_nodes = previous_use_nodes
        if hasattr(scene, "compositing_node_group"):
            scene.compositing_node_group = previous_compositing_node_group
        if cam is not None and previous_camera_state is not None:
            location, rotation, camera_type, panorama_type, fov = previous_camera_state
            cam.location = location
            cam.rotation_euler = rotation
            cam.data.type = camera_type
            if panorama_type and hasattr(cam.data, "panorama_type"):
                cam.data.panorama_type = panorama_type
            if fov and hasattr(cam.data, "fisheye_fov"):
                cam.data.fisheye_fov = fov


def _set_command_outputs(preset, output_dir: Path) -> None:
    for command in preset.commands:
        if hasattr(command, "folder_path"):
            command.folder_path = str(output_dir)
        if hasattr(command, "text_value") and command.command_type in FISHEYE_COMMANDS and not str(getattr(command, "text_value", "") or "").strip():
            command.text_value = _safe_name(preset.name)


def _snapshot_files(directory: Path) -> set[str]:
    directory = directory.resolve()
    if not directory.exists():
        return set()
    return {str(path.resolve()) for path in directory.rglob("*.png")}


def _snapshot_file_info(directory: Path) -> dict[str, tuple[int, int]]:
    directory = directory.resolve()
    if not directory.exists():
        return {}
    result = {}
    for path in directory.rglob("*.png"):
        try:
            stat = path.stat()
        except OSError:
            continue
        result[str(path.resolve())] = (stat.st_size, stat.st_mtime_ns)
    return result


def _changed_png_paths(before: dict[str, tuple[int, int]], directory: Path) -> list[Path]:
    after = _snapshot_file_info(directory)
    changed = [Path(path) for path, info in after.items() if before.get(path) != info]
    return sorted(changed)


def _save_render_result(path: Path) -> Path | None:
    image = bpy.data.images.get("Render Result")
    if image is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        image.save_render(str(path), scene=bpy.context.scene)
    except Exception:  # noqa: BLE001
        return None
    return path.resolve() if path.exists() and path.stat().st_size > 0 else None


def _image_stats(path: Path) -> dict:
    _ensure_pillow_path()
    from PIL import Image, ImageStat

    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        rgb = rgba.convert("RGB")
        stat = ImageStat.Stat(rgb)
        alpha = rgba.getchannel("A")
        alpha_extrema = alpha.getextrema()
        extrema = rgb.getextrema()
        ranges = [hi - lo for lo, hi in extrema]
        width, height = rgba.size
        center = rgba.getpixel((width // 2, height // 2))
        corners = [rgba.getpixel((0, 0)), rgba.getpixel((width - 1, 0)), rgba.getpixel((0, height - 1)), rgba.getpixel((width - 1, height - 1))]
    rgb_blank = all(lo == hi for lo, hi in extrema)
    visible_alpha = alpha_extrema[1] > 0
    alpha_varies = alpha_extrema[0] != alpha_extrema[1]
    has_signal = (not rgb_blank) or visible_alpha or alpha_varies
    visual_signal = visible_alpha and (max(ranges) >= 4 or max(stat.mean) >= 4)
    return {
        "path": str(path),
        "size": [width, height],
        "mean": [round(value, 3) for value in stat.mean],
        "range": ranges,
        "blank": not bool(has_signal),
        "rgb_blank": bool(rgb_blank),
        "alpha": list(alpha_extrema),
        "visible_alpha": bool(visible_alpha),
        "has_signal": bool(has_signal),
        "visual_signal": bool(visual_signal),
        "center": list(center),
        "corners": [list(pixel) for pixel in corners],
    }


def _run_preset(render_mod, preset, index: int, *, fisheye: bool) -> dict:
    from bmanga_render_full_fixture import command_runner

    scene = bpy.context.scene
    mode_name = "fisheye" if fisheye else "normal"
    preset_dir = OUT_DIR / mode_name / f"{index:02d}_{_safe_name(preset.name)}"
    if preset_dir.exists():
        shutil.rmtree(preset_dir)
    preset_dir.mkdir(parents=True, exist_ok=True)
    _configure_render_settings()
    scene.fisheye_layout_mode = bool(fisheye)
    _set_fisheye_camera(bool(fisheye))
    _show_all_view_layers()
    _set_command_outputs(preset, preset_dir / "passes")
    command_runner._set_output_folder(scene, str(preset_dir / "node_outputs"))
    scene.render.filepath = str(preset_dir / "render_result")
    before = _snapshot_files(preset_dir)
    command_types = _preset_command_types(preset)
    started = time.perf_counter()
    error = ""
    count = 0
    try:
        count = command_runner.run_active_preset(bpy.context)
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        command_runner._restore_session(scene)
    elapsed = time.perf_counter() - started
    render_result = None
    if command_types & RENDER_COMMANDS:
        render_result = _save_render_result(preset_dir / "render_result.png")
    after = _snapshot_files(preset_dir)
    generated_paths = sorted(Path(path) for path in after - before)
    if render_result is not None and render_result not in generated_paths:
        generated_paths.append(render_result)
    stats = []
    for path in generated_paths:
        try:
            item = _image_stats(path)
        except Exception as exc:  # noqa: BLE001
            item = {"path": str(path), "error": str(exc), "blank": True, "visible_alpha": False, "has_signal": False, "visual_signal": False}
        stats.append(item)
    expected_render = bool(command_types & RENDER_COMMANDS)
    expected_fisheye = bool(command_types & FISHEYE_COMMANDS)
    nonblank = [item for item in stats if item.get("has_signal", not item.get("blank", True))]
    visible = [item for item in stats if item.get("visual_signal", False)]
    result = {
        "preset": preset.name,
        "mode": mode_name,
        "command_count": count,
        "command_types": sorted(command_types),
        "seconds": round(elapsed, 3),
        "error": error,
        "expected_render": expected_render,
        "expected_fisheye": expected_fisheye,
        "rendered_count": len(stats),
        "nonblank_count": len(nonblank),
        "visible_count": len(visible),
        "camera_type": getattr(getattr(scene.camera, "data", None), "type", ""),
        "panorama_type": getattr(getattr(scene.camera, "data", None), "panorama_type", ""),
        "fisheye_fov": round(float(getattr(getattr(scene.camera, "data", None), "fisheye_fov", 0.0) or 0.0), 6),
        "images": stats,
    }
    if error:
        result["ok"] = False
    elif expected_render and not nonblank:
        result["ok"] = False
        result["error"] = "レンダー画像が生成されないか、全て空画像です"
    elif fisheye and expected_fisheye and not visible:
        result["ok"] = False
        result["error"] = "魚眼モードの実画像に目視可能な出力がありません"
    elif fisheye and expected_fisheye and result["camera_type"] != "PANO":
        result["ok"] = False
        result["error"] = "魚眼出力時にカメラが魚眼投影になっていません"
    else:
        result["ok"] = True
    return result


def _run_shared_flow(render_mod, preset_pairs: list[tuple[int, object]]) -> dict:
    from bmanga_render_full_fixture import command_runner

    scene = bpy.context.scene
    state = scene.bmanga_render_state
    flow_dir = OUT_DIR / "flow"
    if flow_dir.exists():
        shutil.rmtree(flow_dir)
    pass_dir = flow_dir / "passes"
    pass_dir.mkdir(parents=True, exist_ok=True)
    before = _snapshot_files(flow_dir)
    reloads = []
    stages = []
    composite_stats = []
    errors = []
    for index, preset in preset_pairs:
        state.active_preset_index = index - 1
        command_types = _preset_command_types(preset)
        fisheye = bool(command_types & FISHEYE_COMMANDS)
        _configure_render_settings()
        scene.fisheye_layout_mode = fisheye
        _set_fisheye_camera(fisheye)
        _show_all_view_layers()
        _set_command_outputs(preset, pass_dir)
        command_runner._set_output_folder(scene, str(pass_dir))
        scene.render.filepath = str(flow_dir / "render_results" / f"{index:02d}_{_safe_name(preset.name)}")
        stage_before = _snapshot_file_info(flow_dir)
        try:
            command_runner.run_active_preset(bpy.context)
        except Exception as exc:  # noqa: BLE001
            errors.append({"preset": preset.name, "error": str(exc)})
            command_runner._restore_session(scene)
        stage_stats = []
        for path in _changed_png_paths(stage_before, flow_dir):
            try:
                stage_stats.append(_image_stats(path))
            except Exception as exc:  # noqa: BLE001
                stage_stats.append({"path": str(path), "error": str(exc), "has_signal": False, "visual_signal": False})
        is_composite_stage = "RELOAD_IMAGES" in command_types and bool(command_types & RENDER_COMMANDS)
        if is_composite_stage:
            composite_stats.extend(stage_stats)
        if "RELOAD_IMAGES" in command_types:
            reloads.append(
                {
                    "preset": preset.name,
                    "candidates": int(scene.get("bmanga_render_reload_candidate_count", 0) or 0),
                    "matches": int(scene.get("bmanga_render_reload_match_count", 0) or 0),
                    "images": int(scene.get("bmanga_render_reload_image_count", 0) or 0),
                }
            )
        if command_types & RENDER_COMMANDS:
            stages.append(
                {
                    "preset": preset.name,
                    "role": "composite" if is_composite_stage else "path",
                    "outputs": len(stage_stats),
                    "visible": sum(1 for item in stage_stats if item.get("visual_signal", False)),
                    "nonblank": sum(1 for item in stage_stats if item.get("has_signal", False)),
                }
            )
    after = _snapshot_files(flow_dir)
    generated = sorted(Path(path) for path in after - before)
    stats = []
    for path in generated:
        try:
            stats.append(_image_stats(path))
        except Exception as exc:  # noqa: BLE001
            stats.append({"path": str(path), "error": str(exc), "has_signal": False, "visual_signal": False})
    flow_contact = _write_flat_sheet(
        stats,
        OUT_DIR / "b_manga_render_full_fixture_flow.png",
        "B-MANGA Render 共有passes パス→再読み込み→合成 検証",
    )
    composite_contact = _write_flat_sheet(
        composite_stats,
        OUT_DIR / "b_manga_render_full_fixture_flow_composites.png",
        "B-MANGA Render 共有passes 合成段 AI目視",
    )
    missing_reload = [item for item in reloads if item["candidates"] > 0 and item["matches"] <= 0]
    empty_composite_stage = [item for item in stages if item["role"] == "composite" and item["outputs"] <= 0]
    composite_nonblank = [item for item in composite_stats if item.get("has_signal", False)]
    composite_visible = [item for item in composite_stats if item.get("visual_signal", False)]
    return {
        "directory": str(flow_dir),
        "contact_sheet": str(flow_contact),
        "composite_contact_sheet": str(composite_contact),
        "generated_count": len(stats),
        "visible_count": sum(1 for item in stats if item.get("visual_signal", False)),
        "composite_count": len(composite_stats),
        "composite_visible_count": len(composite_visible),
        "stages": stages,
        "reloads": reloads,
        "errors": errors,
        "missing_reload": missing_reload,
        "empty_composite_stage": empty_composite_stage,
        "ok": not errors
        and not missing_reload
        and not empty_composite_stage
        and bool(stats)
        and bool(composite_stats)
        and bool(composite_nonblank)
        and bool(composite_visible),
    }


def _write_flat_sheet(images: list[dict], out_path: Path, title: str) -> Path:
    _ensure_pillow_path()
    from PIL import Image, ImageDraw, ImageFont

    def font(size: int):
        for font_path in (r"C:\Windows\Fonts\YuGothM.ttc", r"C:\Windows\Fonts\meiryo.ttc", r"C:\Windows\Fonts\msgothic.ttc"):
            if Path(font_path).is_file():
                try:
                    return ImageFont.truetype(font_path, size=size)
                except Exception:
                    pass
        return ImageFont.load_default()

    thumb = 96
    cols = 10
    rows = max(1, math.ceil(len(images) / cols))
    sheet = Image.new("RGB", (cols * 150 + 40, rows * 132 + 76), "white")
    draw = ImageDraw.Draw(sheet)
    draw.text((20, 18), title, fill=(0, 0, 0), font=font(18))
    draw.text((20, 44), f"画像 {len(images)}", fill=(0, 0, 0), font=font(12))
    body = font(10)
    for index, image in enumerate(images):
        col = index % cols
        row = index // cols
        x = 20 + col * 150
        y = 72 + row * 132
        try:
            with Image.open(image["path"]) as source:
                rgba = source.convert("RGBA")
                back = Image.new("RGBA", rgba.size, (238, 238, 238, 255))
                pic = Image.alpha_composite(back, rgba).convert("RGB")
                pic.thumbnail((thumb, thumb))
                sheet.paste(pic, (x, y))
        except Exception:
            draw.rectangle((x, y, x + thumb, y + thumb), outline=(180, 0, 0))
        draw.text((x, y + thumb + 3), Path(str(image.get("path", ""))).name[:20], fill=(0, 0, 0), font=body)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return out_path


def _write_category_sheets(results: list[dict]) -> dict[str, str]:
    images = [image for result in results for image in result.get("images", [])]
    path_images = [image for image in images if "node_outputs" in str(image.get("path", ""))]
    fisheye_images = [image for image in images if "\\fisheye\\" in str(image.get("path", "")) or "/fisheye/" in str(image.get("path", ""))]
    composite_images = [
        image
        for image in images
        if Path(str(image.get("path", ""))).name == "render_result.png" or any(word in Path(str(image.get("path", ""))).name for word in ("統合", "線画", "AOV", "assembled"))
    ]
    return {
        "paths": str(_write_flat_sheet(path_images, OUT_DIR / "b_manga_render_full_fixture_paths.png", "B-MANGA Render パス画像 AI目視")),
        "composites": str(_write_flat_sheet(composite_images, OUT_DIR / "b_manga_render_full_fixture_composites.png", "B-MANGA Render コンポジット済み画像 AI目視")),
        "fisheye": str(_write_flat_sheet(fisheye_images, OUT_DIR / "b_manga_render_full_fixture_fisheye.png", "B-MANGA Render 魚眼画像 AI目視")),
    }


def _write_contact_sheet(results: list[dict]) -> Path:
    _ensure_pillow_path()
    from PIL import Image, ImageDraw, ImageFont

    def font(size: int):
        for font_path in (r"C:\Windows\Fonts\YuGothM.ttc", r"C:\Windows\Fonts\meiryo.ttc", r"C:\Windows\Fonts\msgothic.ttc"):
            if Path(font_path).is_file():
                try:
                    return ImageFont.truetype(font_path, size=size)
                except Exception:
                    pass
        return ImageFont.load_default()

    thumb = 84
    row_h = 108
    width = 1760
    height = 96 + row_h * len(results)
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    title_font = font(18)
    body_font = font(12)
    ok_count = sum(1 for item in results if item["ok"])
    draw.text((24, 18), "B-MANGA Render 全プリセット fixture 実エンジン/魚眼オン検証", fill=(0, 0, 0), font=title_font)
    draw.text((24, 50), f"結果: {ok_count}/{len(results)} OK / 解像度 {RESOLUTION}px / サンプル {SAMPLES}", fill=(0, 0, 0), font=body_font)
    y = 86
    for index, item in enumerate(results, 1):
        fill = (235, 249, 235) if item["ok"] else (255, 236, 224)
        draw.rectangle((18, y, width - 18, y + row_h - 8), fill=fill, outline=(170, 185, 170))
        draw.text((30, y + 10), f"{index:02d}. {item['mode']} / {item['preset']}", fill=(0, 0, 0), font=body_font)
        draw.text(
            (30, y + 33),
            f"カード {item['command_count']} / 画像 {item['rendered_count']} / 非空 {item['nonblank_count']} / 目視 {item.get('visible_count', 0)} / {item['seconds']}s",
            fill=(0, 0, 0),
            font=body_font,
        )
        if item.get("error"):
            draw.text((30, y + 56), str(item["error"])[:95], fill=(165, 40, 0), font=body_font)
        for thumb_index, image in enumerate(item["images"][:12]):
            x = 420 + thumb_index * (thumb + 18)
            try:
                with Image.open(image["path"]) as source:
                    rgba = source.convert("RGBA")
                    back = Image.new("RGBA", rgba.size, (238, 238, 238, 255))
                    pic = Image.alpha_composite(back, rgba).convert("RGB")
                    pic.thumbnail((thumb, thumb))
                    sheet.paste(pic, (x, y + 8))
            except Exception:
                draw.rectangle((x, y + 8, x + thumb, y + 8 + thumb), outline=(180, 0, 0))
                draw.text((x + 8, y + 42), "画像不可", fill=(180, 0, 0), font=body_font)
            label = Path(str(image.get("path", ""))).name[:16]
            draw.text((x, y + 8 + thumb), label, fill=(0, 0, 0), font=body_font)
        y += row_h
    out_path = OUT_DIR / "b_manga_render_full_fixture_contact.png"
    sheet.save(out_path)
    return out_path


def main() -> None:
    blend_path = Path(os.environ.get("BMANGA_C00_BLEND", str(DEFAULT_BLEND)))
    if not blend_path.exists():
        raise FileNotFoundError(blend_path)
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.open_mainfile(filepath=str(blend_path))
    render_mod = _load_render_package()
    try:
        fixture_path = _build_fixture_scene(render_mod)
        fixture_probes = [
            _render_fixture_probe("perspective", fisheye=False),
            _render_fixture_probe("fisheye", fisheye=True),
        ]
        bpy.ops.bmanga_render.load_builtin_presets(reset=True)
        state = bpy.context.scene.bmanga_render_state
        presets = [(index, preset) for index, preset in enumerate(state.presets, 1)]
        if PRESET_REGEX:
            pattern = re.compile(PRESET_REGEX)
            presets = [(index, preset) for index, preset in presets if pattern.search(str(preset.name))]
        if not presets:
            raise RuntimeError("検証対象プリセットがありません")
        normal_results = []
        fisheye_results = []
        for index, preset in presets:
            if "normal" not in MODE_FILTER:
                continue
            state.active_preset_index = index - 1
            normal_results.append(_run_preset(render_mod, preset, index, fisheye=False))
        for index, preset in presets:
            if "fisheye" not in MODE_FILTER:
                continue
            if not (_preset_command_types(preset) & FISHEYE_COMMANDS):
                continue
            state.active_preset_index = index - 1
            fisheye_results.append(_run_preset(render_mod, preset, index, fisheye=True))
        results = normal_results + fisheye_results
        contact = _write_contact_sheet(results)
        category_sheets = _write_category_sheets(results)
        flow_result = _run_shared_flow(render_mod, presets) if FLOW_ENABLED else {"ok": True, "skipped": True}
        payload = {
            "fixture": str(fixture_path),
            "source_blend": str(blend_path),
            "contact_sheet": str(contact),
            "category_sheets": category_sheets,
            "resolution": RESOLUTION,
            "samples": SAMPLES,
            "normal_count": len(normal_results),
            "fisheye_count": len(fisheye_results),
            "fixture_probes": fixture_probes,
            "flow": flow_result,
            "results": results,
        }
        json_path = OUT_DIR / "b_manga_render_full_fixture_results.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        failures = [item for item in results if not item["ok"]]
        if not flow_result.get("ok", False):
            failures.append({"preset": "共有passesフロー", "error": json.dumps(flow_result, ensure_ascii=False)})
        print(f"BMANGA_RENDER_FULL_FIXTURE_DONE visual={contact} json={json_path} presets={len(results)} errors={len(failures)}")
        assert not failures, json.dumps(failures[:5], ensure_ascii=False, indent=2)
    finally:
        try:
            render_mod.unregister()
        except Exception:
            pass
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
