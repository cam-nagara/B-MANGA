from __future__ import annotations

import importlib
import itertools
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = ROOT / "addons" / "b_manga_line"
OUT_DIR = ROOT / "_verify" / "2026-07-06_bml_width_all_shapes_final"

SHAPES = (
    "平面",
    "立方体",
    "UV球",
    "円錐",
    "円柱",
    "六角柱",
    "トーラス",
)

LINE_TYPES = ("outline", "inner", "intersection", "selection")
LINE_TYPE_LABELS = {
    "outline": "アウトライン",
    "inner": "稜谷線",
    "intersection": "交差線",
    "selection": "選択線",
}

LINE_WIDTH_MM = 1.0
DPI = 600
EXPECTED_PX = LINE_WIDTH_MM * DPI / 25.4


def _load_addon_modules():
    if str(ADDON_DIR.parent) not in sys.path:
        sys.path.insert(0, str(ADDON_DIR.parent))
    pkg = importlib.import_module("b_manga_line")
    importlib.reload(pkg)
    pkg.register()

    from b_manga_line import camera_comp, core, presets, scale_utils

    return pkg, camera_comp, core, presets, scale_utils


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _look_at(obj: bpy.types.Object, target: Vector) -> None:
    direction = target - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _make_material(name: str, color: tuple[float, float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    return mat


def _setup_camera() -> bpy.types.Object:
    bpy.ops.object.camera_add(location=(0.0, -18.0, 18.0))
    cam = bpy.context.object
    _look_at(cam, Vector((0.0, 0.0, 0.0)))
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 34.0
    bpy.context.scene.camera = cam
    return cam


def _setup_render() -> None:
    scene = bpy.context.scene
    scene.render.resolution_x = 3200
    scene.render.resolution_y = 1800
    scene.render.resolution_percentage = 100
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:
        scene.render.engine = "BLENDER_EEVEE"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.world = scene.world or bpy.data.worlds.new("World")
    scene.world.color = (1.0, 1.0, 1.0)
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        background.inputs["Strength"].default_value = 1.0
    scene.render.film_transparent = False

    bpy.ops.object.light_add(type="AREA", location=(0.0, -6.0, 10.0))
    light = bpy.context.object
    light.name = "Audit_Light"
    light.data.energy = 700.0
    light.data.size = 7.0


def _append_material(obj: bpy.types.Object, mat: bpy.types.Material) -> None:
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def _mark_selection_edges(obj: bpy.types.Object) -> None:
    mesh = obj.data
    attr = mesh.attributes.get("freestyle_edge")
    if attr is None:
        attr = mesh.attributes.new("freestyle_edge", "BOOLEAN", "EDGE")
    for item in attr.data:
        item.value = True
    for edge in mesh.edges:
        if hasattr(edge, "use_freestyle_mark"):
            edge.use_freestyle_mark = True


def _set_flat_faces(obj: bpy.types.Object) -> None:
    for poly in obj.data.polygons:
        poly.use_smooth = False


def _create_shape(shape: str, name: str, location: tuple[float, float, float], mat: bpy.types.Material) -> bpy.types.Object:
    if shape == "平面":
        bpy.ops.mesh.primitive_plane_add(size=2.35, location=location)
    elif shape == "立方体":
        bpy.ops.mesh.primitive_cube_add(size=1.8, location=location)
    elif shape == "UV球":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0, location=location)
    elif shape == "円錐":
        bpy.ops.mesh.primitive_cone_add(vertices=32, radius1=1.05, radius2=0.0, depth=2.1, location=location)
    elif shape == "円柱":
        bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1.0, depth=2.1, location=location)
    elif shape == "六角柱":
        bpy.ops.mesh.primitive_cylinder_add(vertices=6, radius=1.05, depth=2.1, location=location)
    elif shape == "トーラス":
        bpy.ops.mesh.primitive_torus_add(
            major_segments=48,
            minor_segments=12,
            major_radius=0.72,
            minor_radius=0.32,
            location=location,
        )
    else:
        raise ValueError(shape)

    obj = bpy.context.object
    obj.name = name
    obj.data.name = f"{name}_Mesh"
    _append_material(obj, mat)
    _set_flat_faces(obj)
    _mark_selection_edges(obj)
    return obj


def _create_label(text: str, location: tuple[float, float, float], mat: bpy.types.Material) -> bpy.types.Object:
    bpy.ops.object.text_add(location=location, rotation=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = f"Label_{text[:8]}"
    obj.data.body = text
    obj.data.align_x = "CENTER"
    obj.data.align_y = "CENTER"
    obj.data.size = 0.22
    _append_material(obj, mat)
    return obj


def _create_pair_scene() -> list[dict[str, object]]:
    surface_mats = [
        _make_material("Audit_Surface_A", (0.78, 0.78, 0.78, 1.0)),
        _make_material("Audit_Surface_B", (0.92, 0.92, 0.92, 1.0)),
    ]
    label_mat = _make_material("Audit_Label", (0.05, 0.05, 0.05, 1.0))

    pairs: list[dict[str, object]] = []
    combinations = list(itertools.combinations(SHAPES, 2))
    cols = 7
    spacing_x = 4.35
    spacing_y = 4.6
    x_origin = -((cols - 1) * spacing_x) / 2.0
    y_origin = spacing_y

    for index, (shape_a, shape_b) in enumerate(combinations, start=1):
        col = (index - 1) % cols
        row = (index - 1) // cols
        center = Vector((x_origin + col * spacing_x, y_origin - row * spacing_y, 0.0))
        offset = Vector((0.34, 0.0, 0.0))

        obj_a = _create_shape(
            shape_a,
            f"{index:02d}_{shape_a}",
            tuple(center - offset),
            surface_mats[(index + 0) % 2],
        )
        obj_b = _create_shape(
            shape_b,
            f"{index:02d}_{shape_b}",
            tuple(center + offset),
            surface_mats[(index + 1) % 2],
        )
        _create_label(f"{index:02d} {shape_a}x{shape_b}", (center.x, center.y - 1.9, 1.35), label_mat)
        pairs.append({"index": index, "shapes": (shape_a, shape_b), "objects": (obj_a, obj_b)})

    bpy.ops.object.select_all(action="DESELECT")
    return pairs


def _configure_line_settings(objects: list[bpy.types.Object], camera: bpy.types.Object, core) -> None:
    ref_distance = (camera.location - Vector((0.0, 0.0, 0.0))).length
    old_propagating = getattr(core, "_propagating", False)
    core._propagating = True
    try:
        for obj in objects:
            settings = obj.bmanga_line_settings
            settings.outline_enabled = True
            settings.inner_line_enabled = True
            settings.intersection_enabled = True
            settings.selection_line_enabled = True

            settings.outline_thickness_mm = LINE_WIDTH_MM
            settings.inner_line_thickness_mm = LINE_WIDTH_MM
            settings.intersection_thickness_mm = LINE_WIDTH_MM
            settings.selection_line_thickness_mm = LINE_WIDTH_MM

            settings.outline_offset = 0.0
            settings.inner_line_offset = 0.0
            settings.intersection_line_offset = 0.0
            settings.selection_line_offset = 0.0

            settings.use_outline_distance_limit = False
            settings.use_inner_line_distance_limit = False
            settings.use_intersection_distance_limit = False
            settings.use_selection_line_distance_limit = False
            settings.use_camera_culling = False

            settings.even_thickness = True
            settings.use_uniform_line_width = True
            settings.use_camera_compensation = True
            settings.camera_compensation_influence = 1.0
            settings.line_width_reference_distance = ref_distance

            settings.edge_smooth_factor = 0.0
            settings.inner_line_smooth_factor = 0.0
            settings.intersection_line_smooth_factor = 0.0
            settings.selection_line_smooth_factor = 0.0
            settings.midpoint_jitter_strength = 0.0
            settings.inner_line_midpoint_jitter_strength = 0.0
            settings.intersection_line_midpoint_jitter_strength = 0.0
            settings.selection_line_midpoint_jitter_strength = 0.0

            settings.edge_angle = math.radians(1.0)
            settings.inner_line_angle = math.radians(1.0)
            settings.intersection_line_angle = math.radians(1.0)
            settings.selection_line_angle = math.radians(1.0)

            settings.outline_color = (0.0, 0.0, 0.0, 1.0)
            settings.inner_line_color = (0.0, 0.2, 1.0, 1.0)
            settings.intersection_line_color = (0.0, 0.85, 0.0, 1.0)
            settings.selection_line_color = (1.0, 0.0, 1.0, 1.0)
    finally:
        core._propagating = old_propagating


def _apply_lines(objects: list[bpy.types.Object], presets) -> None:
    for obj in objects:
        presets.apply_line_settings(obj, bpy.context, refresh_scene=False)
    presets._refresh_after_line_settings(bpy.context)
    bpy.context.view_layer.update()


def _line_modifier_kind(obj: bpy.types.Object, mod: bpy.types.Modifier, core) -> str | None:
    if mod.name in {core.MODIFIER_NAME, core.SHEET_OUTLINE_MODIFIER_NAME}:
        return "outline"
    if mod.name == core.GN_MODIFIER_NAME:
        return "inner"
    if mod.name == core.SELECTION_LINE_MODIFIER_NAME:
        return "selection"
    if mod.name.startswith(core.INTERSECTION_MODIFIER_PREFIX):
        return "intersection"
    return None


def _set_line_visibility(objects: list[bpy.types.Object], core, active_kind: str | None) -> None:
    for obj in objects:
        for mod in obj.modifiers:
            kind = _line_modifier_kind(obj, mod, core)
            if kind is None:
                continue
            visible = active_kind is None or kind == active_kind
            mod.show_viewport = visible
            mod.show_render = visible
    bpy.context.view_layer.update()


def _render_image(path: Path) -> None:
    scene = bpy.context.scene
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def _modifier_socket_value(mod: bpy.types.Modifier, preferred_names: tuple[str, ...]) -> float | None:
    node_group = getattr(mod, "node_group", None)
    if node_group is None:
        return None
    for item in node_group.interface.items_tree:
        if getattr(item, "item_type", None) != "SOCKET":
            continue
        if getattr(item, "in_out", None) != "INPUT":
            continue
        if getattr(item, "name", "") not in preferred_names:
            continue
        identifier = getattr(item, "identifier", "")
        try:
            return float(mod[identifier])
        except Exception:
            return None
    return None


def _world_to_px(scene, camera_comp, scale_utils, obj: bpy.types.Object, width_world: float | None) -> float | None:
    if width_world is None:
        return None
    wpp = camera_comp._world_per_pixel(scene, bpy.context.scene.camera, obj.location)
    if wpp <= 0.0:
        return None
    return width_world / wpp


def _modifier_width_px(scene, camera_comp, scale_utils, obj: bpy.types.Object, mod: bpy.types.Modifier, kind: str) -> float | None:
    if kind == "outline":
        width_world = scale_utils.world_width_from_modifier(obj, getattr(mod, "thickness", 0.0))
        return _world_to_px(scene, camera_comp, scale_utils, obj, width_world)
    value = _modifier_socket_value(mod, ("線の太さ", "Width", "Thickness"))
    if value is None:
        return None
    width_world = scale_utils.world_width_from_modifier(obj, value)
    return _world_to_px(scene, camera_comp, scale_utils, obj, width_world)


def _collect_metrics(pairs: list[dict[str, object]], objects: list[bpy.types.Object], camera_comp, core, scale_utils) -> dict[str, object]:
    scene = bpy.context.scene
    ref_distance = (scene.camera.location - Vector((0.0, 0.0, 0.0))).length
    missing: list[dict[str, object]] = []
    pair_results: list[dict[str, object]] = []

    for pair in pairs:
        pair_objects = list(pair["objects"])
        type_counts = {line_type: 0 for line_type in LINE_TYPES}
        sampled_px = {line_type: [] for line_type in LINE_TYPES}
        for obj in pair_objects:
            for mod in obj.modifiers:
                kind = _line_modifier_kind(obj, mod, core)
                if kind is None:
                    continue
                type_counts[kind] += 1
                px = _modifier_width_px(scene, camera_comp, scale_utils, obj, mod, kind)
                if px is not None:
                    sampled_px[kind].append(px)

        missing_types = [line_type for line_type, count in type_counts.items() if count == 0]
        if missing_types:
            missing.append(
                {
                    "index": pair["index"],
                    "shapes": list(pair["shapes"]),
                    "missing_types": [LINE_TYPE_LABELS[t] for t in missing_types],
                }
            )
        pair_results.append(
            {
                "index": pair["index"],
                "shapes": list(pair["shapes"]),
                "type_counts": type_counts,
                "sampled_modifier_base_px": {
                    key: [round(value, 3) for value in values[:8]]
                    for key, values in sampled_px.items()
                },
            }
        )

    settings_failures: list[dict[str, object]] = []
    for obj in objects:
        settings = obj.bmanga_line_settings
        values = {
            "outline": settings.outline_thickness_mm,
            "inner": settings.inner_line_thickness_mm,
            "intersection": settings.intersection_thickness_mm,
            "selection": settings.selection_line_thickness_mm,
        }
        offsets = {
            "outline": settings.outline_offset,
            "inner": settings.inner_line_offset,
            "intersection": settings.intersection_line_offset,
            "selection": settings.selection_line_offset,
        }
        if any(abs(v - LINE_WIDTH_MM) > 1e-6 for v in values.values()):
            settings_failures.append({"object": obj.name, "line_widths": values})
        if any(abs(v) > 1e-6 for v in offsets.values()):
            settings_failures.append({"object": obj.name, "offsets": offsets})
        if not (settings.even_thickness and settings.use_uniform_line_width and settings.use_camera_compensation):
            settings_failures.append(
                {
                    "object": obj.name,
                    "even_thickness": settings.even_thickness,
                    "use_uniform_line_width": settings.use_uniform_line_width,
                    "use_camera_compensation": settings.use_camera_compensation,
                }
            )
        if abs(settings.line_width_reference_distance - ref_distance) > 1e-5:
            settings_failures.append(
                {
                    "object": obj.name,
                    "line_width_reference_distance": settings.line_width_reference_distance,
                    "expected": ref_distance,
                }
            )

    return {
        "expected_px_at_600dpi": round(EXPECTED_PX, 3),
        "line_width_mm": LINE_WIDTH_MM,
        "dpi": DPI,
        "reference_distance_origin": round(ref_distance, 6),
        "pair_count": len(pairs),
        "shape_pairs": [{"index": p["index"], "shapes": list(p["shapes"])} for p in pairs],
        "pair_results": pair_results,
        "missing": missing,
        "settings_failures": settings_failures,
    }


def main() -> None:
    _, camera_comp, core, presets, scale_utils = _load_addon_modules()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _clear_scene()
    _setup_render()
    camera = _setup_camera()
    pairs = _create_pair_scene()
    objects = [obj for pair in pairs for obj in pair["objects"]]

    _configure_line_settings(objects, camera, core)
    _apply_lines(objects, presets)

    metrics = _collect_metrics(pairs, objects, camera_comp, core, scale_utils)
    (OUT_DIR / "all_shapes_width_audit.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _set_line_visibility(objects, core, None)
    _render_image(OUT_DIR / "all_lines_overview.png")
    for line_type in LINE_TYPES:
        _set_line_visibility(objects, core, line_type)
        _render_image(OUT_DIR / f"{line_type}_only.png")

    if metrics["missing"] or metrics["settings_failures"]:
        raise AssertionError(json.dumps({"missing": metrics["missing"], "settings_failures": metrics["settings_failures"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
