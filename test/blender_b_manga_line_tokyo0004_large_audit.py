"""Large phased audit for B-MANGA Line on Japanese Streetscape Tokyo 0004."""

from __future__ import annotations

import argparse
import importlib
import json
import math
import sys
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
ADDONS = ROOT / "addons"
BLEND_PATH = Path(
    r"D:\TM Dropbox\Share\Assets\Japanese Streetscape Tokyo 0004"
    r"\Japanese_Streetscape_Tokyo_0004.blend"
)
OUT_DIR = ROOT / "_verify" / "b_manga_line_tokyo0004_large_audit_2026-07-02"
SCREEN_DIR = OUT_DIR / "screenshots"
LOG_DIR = OUT_DIR / "logs"
EXECUTION_DOC = ROOT / "docs" / "b_manga_line_tokyo0004_large_audit_execution_2026-07-02.md"


PHASE_LIMITS = {
    "render_range_select": 20.0,
    "full_apply": 120.0,
    "toggle": 15.0,
    "setting": 5.0,
}


def _json_default(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    return str(value)


class Audit:
    def __init__(self, phase: str):
        self.phase = phase
        self.result = {
            "phase": phase,
            "blend": str(BLEND_PATH),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "operations": [],
            "screenshots": [],
            "warnings": [],
            "errors": [],
            "counts": {},
        }
        self.modules = {}

    def warn(self, message: str) -> None:
        print(f"[WARN] {message}", flush=True)
        self.result["warnings"].append(message)

    def fail(self, message: str) -> None:
        print(f"[FAIL] {message}", flush=True)
        self.result["errors"].append(message)

    def time_op(self, name: str, limit_key: str | None = None):
        return _TimedOperation(self, name, limit_key)


class _TimedOperation:
    def __init__(self, audit: Audit, name: str, limit_key: str | None):
        self.audit = audit
        self.name = name
        self.limit_key = limit_key
        self.started = 0.0

    def __enter__(self):
        self.started = time.perf_counter()
        print(f"[RUN] {self.name}", flush=True)
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.perf_counter() - self.started
        entry = {"name": self.name, "seconds": round(elapsed, 4), "ok": exc is None}
        if exc is not None:
            entry["error"] = "".join(traceback.format_exception_only(exc_type, exc)).strip()
            self.audit.result["errors"].append(f"{self.name}: {entry['error']}")
        limit = PHASE_LIMITS.get(self.limit_key or "")
        if limit is not None and elapsed > limit:
            msg = f"{self.name}: {elapsed:.2f}s > {limit:.2f}s"
            entry["over_limit"] = True
            self.audit.result["errors"].append(msg)
            print(f"[SLOW] {msg}", flush=True)
        self.audit.result["operations"].append(entry)
        print(f"[DONE] {self.name}: {elapsed:.3f}s", flush=True)
        return False


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["phase1", "phase2", "phase3", "phase4", "phase5", "all"], required=True)
    parser.add_argument("--max-targets", type=int, default=0)
    return parser.parse_args(argv)


def prepare_dirs() -> None:
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_scene(audit: Audit):
    if not BLEND_PATH.exists():
        raise FileNotFoundError(BLEND_PATH)
    bpy.ops.wm.open_mainfile(filepath=str(BLEND_PATH))
    if str(ADDONS) not in sys.path:
        sys.path.insert(0, str(ADDONS))
    import b_manga_line

    b_manga_line = importlib.reload(b_manga_line)
    try:
        b_manga_line.register()
    except ValueError:
        pass
    modules = {
        "b_manga_line": b_manga_line,
        "camera_comp": importlib.import_module("b_manga_line.camera_comp"),
        "core": importlib.import_module("b_manga_line.core"),
        "intersection_lines": importlib.import_module("b_manga_line.intersection_lines"),
        "outline_setup": importlib.import_module("b_manga_line.outline_setup"),
        "presets": importlib.import_module("b_manga_line.presets"),
        "vertex_analysis": importlib.import_module("b_manga_line.vertex_analysis"),
    }
    audit.modules = modules
    scene = bpy.context.scene
    scene.frame_set(scene.frame_current)
    ensure_camera(scene)
    configure_render(scene)
    return scene, modules


def ensure_camera(scene) -> bpy.types.Object:
    camera = scene.camera
    if camera is not None:
        return camera
    bpy.ops.object.camera_add(location=(0.0, -8.0, 3.0), rotation=(math.radians(68), 0.0, 0.0))
    camera = bpy.context.view_layer.objects.active
    scene.camera = camera
    return camera


def configure_render(scene) -> None:
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    eevee = getattr(scene, "eevee", None)
    if eevee is not None:
        for attr, value in (
            ("taa_render_samples", 16),
            ("taa_samples", 16),
        ):
            if hasattr(eevee, attr):
                try:
                    setattr(eevee, attr, value)
                except (TypeError, ValueError):
                    pass
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0


def visible_meshes(scene) -> list[bpy.types.Object]:
    objects = []
    for obj in scene.objects:
        if obj.type != "MESH" or obj.data is None or not obj.data.polygons:
            continue
        try:
            if not obj.visible_get():
                continue
        except RuntimeError:
            continue
        objects.append(obj)
    return objects


def render_range_meshes(scene, modules) -> list[bpy.types.Object]:
    camera_comp = modules["camera_comp"]
    camera = camera_comp.get_line_camera(scene) or scene.camera
    if camera is None:
        return visible_meshes(scene)
    return [
        obj for obj in visible_meshes(scene)
        if camera_comp.object_overlaps_camera_view(obj, scene, camera)
    ]


def select_objects(objects: list[bpy.types.Object]) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    if objects:
        bpy.context.view_layer.objects.active = objects[0]


def active_settings():
    obj = bpy.context.view_layer.objects.active
    if obj is None:
        raise RuntimeError("active object missing")
    return obj.bmanga_line_settings


def count_lines(objects: list[bpy.types.Object], modules) -> dict[str, int]:
    core = modules["core"]
    counts = {
        "mesh": len(objects),
        "outline": 0,
        "inner": 0,
        "intersection": 0,
        "shell_intersection": 0,
        "boolean_or_sdf_intersection": 0,
        "line_hidden": 0,
    }
    for obj in objects:
        if obj.modifiers.get(core.MODIFIER_NAME) is not None:
            counts["outline"] += 1
        if obj.modifiers.get(core.GN_MODIFIER_NAME) is not None:
            counts["inner"] += 1
        mods = list(core.iter_intersection_modifiers(obj))
        if mods:
            counts["intersection"] += len(mods)
        for mod in mods:
            if mod.name.endswith("__Shell"):
                counts["shell_intersection"] += 1
            else:
                counts["boolean_or_sdf_intersection"] += 1
        if bool(obj.get(core.PROP_LINES_HIDDEN, False)):
            counts["line_hidden"] += 1
    return counts


def save_camera_render(audit: Audit, scene, name: str) -> Path:
    path = SCREEN_DIR / f"{audit.phase}_{name}.png"
    scene.render.filepath = str(path)
    try:
        bpy.ops.render.opengl(write_still=True, view_context=False)
    except Exception:
        bpy.ops.render.render(write_still=True)
    audit.result["screenshots"].append(str(path))
    print(f"[SCREEN] {path}", flush=True)
    return path


def write_outputs(audit: Audit) -> None:
    audit.result["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    json_path = OUT_DIR / f"{audit.phase}_result.json"
    md_path = OUT_DIR / f"{audit.phase}_summary.md"
    json_path.write_text(
        json.dumps(audit.result, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    lines = [
        f"# {audit.phase} summary",
        "",
        f"- errors: {len(audit.result['errors'])}",
        f"- warnings: {len(audit.result['warnings'])}",
        f"- screenshots: {len(audit.result['screenshots'])}",
        "",
        "## Counts",
        "",
        "```json",
        json.dumps(audit.result.get("counts", {}), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Operations",
        "",
    ]
    for op in audit.result["operations"]:
        status = "OK" if op.get("ok") and not op.get("over_limit") else "NG"
        lines.append(f"- {status}: {op['name']} ({op['seconds']}s)")
    if audit.result["screenshots"]:
        lines.extend(["", "## Screenshots", ""])
        for path in audit.result["screenshots"]:
            lines.append(f"- `{path}`")
    if audit.result["errors"]:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {item}" for item in audit.result["errors"])
    if audit.result["warnings"]:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in audit.result["warnings"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    update_execution_doc(audit, md_path, json_path)


def update_execution_doc(audit: Audit, md_path: Path, json_path: Path) -> None:
    if EXECUTION_DOC.exists():
        text = EXECUTION_DOC.read_text(encoding="utf-8")
    else:
        text = "# B-MANGA Line Tokyo0004 大規模監査 実行ログ\n\n"
    marker = f"## {audit.phase}\n"
    section = [
        marker.rstrip(),
        "",
        f"- JSON: `{json_path}`",
        f"- Summary: `{md_path}`",
        f"- Errors: {len(audit.result['errors'])}",
        f"- Warnings: {len(audit.result['warnings'])}",
        f"- Screenshots: {len(audit.result['screenshots'])}",
        "",
        "### Screenshots",
        "",
    ]
    section.extend(f"- `{path}`" for path in audit.result["screenshots"])
    section.extend(["", "### Operations", ""])
    for op in audit.result["operations"]:
        status = "OK" if op.get("ok") and not op.get("over_limit") else "NG"
        section.append(f"- {status}: {op['name']} ({op['seconds']}s)")
    if audit.result["errors"]:
        section.extend(["", "### Errors", ""])
        section.extend(f"- {item}" for item in audit.result["errors"])
    section.append("")
    new_section = "\n".join(section)
    if marker in text:
        before = text.split(marker)[0]
        rest = marker + text.split(marker, 1)[1]
        next_index = rest.find("\n## ", 1)
        after = "" if next_index < 0 else rest[next_index + 1:]
        text = before + new_section + "\n" + after
    else:
        text = text.rstrip() + "\n\n" + new_section + "\n"
    EXECUTION_DOC.write_text(text, encoding="utf-8")


@contextmanager
def forbid_auto_targets(audit: Audit):
    intersection_lines = audit.modules["intersection_lines"]
    original = intersection_lines._auto_targets
    calls = {"count": 0}

    def forbidden(*_args, **_kwargs):
        calls["count"] += 1
        raise RuntimeError("SHELL method called intersection target scan")

    intersection_lines._auto_targets = forbidden
    try:
        yield calls
    finally:
        intersection_lines._auto_targets = original
        audit.result["counts"]["auto_target_calls"] = calls["count"]


def set_line_flags(settings, outline: bool, inner: bool, intersection: bool, method: str = "SHELL") -> None:
    settings.intersection_method = method
    settings.use_intersection_creation_limit = True
    settings.use_inner_line_creation_limit = True
    settings.outline_enabled = outline
    settings.inner_line_enabled = inner
    settings.intersection_enabled = intersection


def configure_common_settings(objects: list[bpy.types.Object], method: str = "SHELL") -> None:
    for obj in objects:
        settings = obj.bmanga_line_settings
        settings.outline_thickness_mm = 0.5
        settings.inner_line_thickness_mm = 0.35
        settings.intersection_thickness_mm = 0.5
        settings.outline_color = (0.0, 0.0, 0.0, 1.0)
        settings.inner_line_color = (0.0, 0.0, 0.0, 1.0)
        settings.intersection_color = (0.0, 0.0, 0.0, 1.0)
        settings.intersection_method = method
        settings.use_inner_line_creation_limit = True
        settings.use_intersection_creation_limit = True
        settings.inner_line_creation_max_distance = 10.0
        settings.intersection_creation_max_distance = 10.0


def apply_selected(audit: Audit, objects: list[bpy.types.Object], limit_key: str = "full_apply") -> None:
    select_objects(objects)
    with audit.time_op("ラインを適用", limit_key):
        with bpy.context.temp_override(
            selected_objects=objects,
            active_object=objects[0],
            object=objects[0],
        ):
            result = bpy.ops.bmanga_line.apply("EXEC_DEFAULT")
        if result != {"FINISHED"}:
            raise RuntimeError(f"apply failed: {result}")


def phase1(audit: Audit, max_targets: int) -> None:
    scene, modules = load_scene(audit)
    all_meshes = visible_meshes(scene)
    targets = render_range_meshes(scene, modules)
    if max_targets > 0:
        targets = targets[:max_targets]
    audit.result["counts"]["visible_meshes"] = len(all_meshes)
    audit.result["counts"]["render_range_meshes"] = len(targets)
    save_camera_render(audit, scene, "baseline_camera")
    with audit.time_op("レンダリング範囲内を選択", "render_range_select"):
        select_objects(targets)
    audit.result["counts"]["selected_meshes"] = len([obj for obj in bpy.context.selected_objects if obj.type == "MESH"])
    save_camera_render(audit, scene, "render_range_selection")


def phase2(audit: Audit, max_targets: int) -> None:
    scene, modules = load_scene(audit)
    targets = render_range_meshes(scene, modules)
    if max_targets > 0:
        targets = targets[:max_targets]
    configure_common_settings(targets, "SHELL")
    select_objects(targets)
    settings = active_settings()
    combos = [
        ("000", False, False, False),
        ("100", True, False, False),
        ("010", False, True, False),
        ("001", False, False, True),
        ("110", True, True, False),
        ("101", True, False, True),
        ("011", False, True, True),
        ("111", True, True, True),
    ]
    with forbid_auto_targets(audit):
        for label, outline, inner, intersection in combos:
            with audit.time_op(f"combo_{label}", "toggle"):
                set_line_flags(settings, outline, inner, intersection)
                apply_selected(audit, targets, "toggle")
            audit.result["counts"][f"combo_{label}"] = count_lines(targets, modules)
            save_camera_render(audit, scene, f"combo_{label}")

        set_line_flags(settings, False, False, False)
        apply_selected(audit, targets, "toggle")
        states = {label: (outline, inner, intersection) for label, outline, inner, intersection in combos}
        for label, values in states.items():
            for index, name in enumerate(("O", "I", "X")):
                next_values = list(values)
                next_values[index] = not next_values[index]
                with audit.time_op(f"toggle_{label}_{name}", "toggle"):
                    set_line_flags(settings, *next_values)

        orders = [
            ("OIX", ("O", "I", "X")),
            ("OXI", ("O", "X", "I")),
            ("IOX", ("I", "O", "X")),
            ("IXO", ("I", "X", "O")),
            ("XOI", ("X", "O", "I")),
            ("XIO", ("X", "I", "O")),
        ]
        for label, order in orders:
            flags = {"O": False, "I": False, "X": False}
            set_line_flags(settings, False, False, False)
            for item in order:
                flags[item] = True
                with audit.time_op(f"enable_order_{label}_{item}", "toggle"):
                    set_line_flags(settings, flags["O"], flags["I"], flags["X"])
            for item in order:
                flags[item] = False
                with audit.time_op(f"disable_order_{label}_{item}", "toggle"):
                    set_line_flags(settings, flags["O"], flags["I"], flags["X"])
    audit.result["counts"]["final"] = count_lines(targets, modules)


def phase3(audit: Audit, max_targets: int) -> None:
    scene, modules = load_scene(audit)
    targets = render_range_meshes(scene, modules)
    if max_targets > 0:
        targets = targets[:max_targets]
    configure_common_settings(targets, "SHELL")
    select_objects(targets)
    settings = active_settings()
    set_line_flags(settings, True, True, True)
    apply_selected(audit, targets)
    counters = {"apply_line_settings": 0, "intersection_apply": 0}
    presets = modules["presets"]
    intersection_lines = modules["intersection_lines"]
    original_apply_line_settings = presets.apply_line_settings
    original_intersection_apply = intersection_lines.apply_intersection_lines

    def counted_apply(*args, **kwargs):
        counters["apply_line_settings"] += 1
        return original_apply_line_settings(*args, **kwargs)

    def counted_intersection(*args, **kwargs):
        counters["intersection_apply"] += 1
        return original_intersection_apply(*args, **kwargs)

    presets.apply_line_settings = counted_apply
    intersection_lines.apply_intersection_lines = counted_intersection
    setting_changes = [
        ("outline_thickness_mm", 0.1),
        ("outline_thickness_mm", 1.5),
        ("outline_offset", -1.0),
        ("outline_offset", 1.0),
        ("outline_color", (1.0, 0.0, 0.0, 1.0)),
        ("even_thickness", True),
        ("use_rim", True),
        ("hide_through_transparent", True),
        ("use_vertex_color", True),
        ("edge_smooth_factor", 1.0),
        ("edge_midpoint_jitter_percent", 25.0),
        ("edge_width_curve_25", 0.0),
        ("edge_width_curve_50", 1.0),
        ("edge_width_curve_75", 0.0),
        ("line_width_reference_distance", 0.5),
        ("line_width_reference_distance", 10.0),
        ("use_camera_compensation", True),
        ("camera_compensation_influence", 0.5),
        ("use_uniform_line_width", True),
        ("use_camera_culling", True),
        ("culling_margin", math.radians(10.0)),
        ("inner_line_angle", math.radians(20.0)),
        ("inner_line_angle", math.radians(120.0)),
        ("inner_line_thickness_mm", 0.1),
        ("inner_line_thickness_mm", 1.5),
        ("inner_line_offset", -1.0),
        ("inner_line_offset", 1.0),
        ("inner_line_color", (0.0, 1.0, 0.0, 1.0)),
        ("use_inner_line_creation_limit", False),
        ("inner_line_creation_max_distance", 20.0),
        ("use_inner_line_distance_limit", True),
        ("inner_line_max_distance", 20.0),
        ("inner_edge_smooth_factor", 1.0),
        ("inner_edge_midpoint_jitter_percent", 25.0),
        ("intersection_thickness_mm", 0.1),
        ("intersection_thickness_mm", 1.5),
        ("intersection_line_offset", -1.0),
        ("intersection_line_offset", 1.0),
        ("intersection_color", (0.0, 0.0, 1.0, 1.0)),
        ("use_intersection_creation_limit", False),
        ("intersection_creation_max_distance", 20.0),
        ("use_intersection_distance_limit", True),
        ("intersection_max_distance", 20.0),
        ("intersection_edge_smooth_factor", 1.0),
        ("intersection_edge_midpoint_jitter_percent", 25.0),
        ("selection_line_angle", math.radians(20.0)),
        ("selection_line_angle", math.radians(120.0)),
        ("selection_line_thickness_mm", 0.1),
        ("selection_line_thickness_mm", 1.5),
        ("selection_line_offset", -1.0),
        ("selection_line_offset", 1.0),
        ("selection_line_color", (1.0, 0.0, 1.0, 1.0)),
        ("use_selection_line_creation_limit", False),
        ("selection_line_creation_max_distance", 20.0),
        ("use_selection_line_distance_limit", True),
        ("selection_line_max_distance", 20.0),
        ("selection_edge_smooth_factor", 1.0),
        ("selection_edge_midpoint_jitter_percent", 25.0),
    ]
    try:
        with forbid_auto_targets(audit):
            for prop, value in setting_changes:
                before = dict(counters)
                with audit.time_op(f"setting_{prop}", "setting"):
                    setattr(settings, prop, value)
                after = dict(counters)
                audit.result["counts"][f"setting_{prop}"] = {
                    "apply_line_settings_delta": after["apply_line_settings"] - before["apply_line_settings"],
                    "intersection_apply_delta": after["intersection_apply"] - before["intersection_apply"],
                }
    finally:
        presets.apply_line_settings = original_apply_line_settings
        intersection_lines.apply_intersection_lines = original_intersection_apply
    save_camera_render(audit, scene, "settings_after")


def orient_camera_to(camera: bpy.types.Object, target: Vector) -> None:
    direction = target - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def configure_visual_capture_scene(scene) -> bpy.types.Object:
    for obj in scene.objects:
        if obj.type != "CAMERA":
            obj.hide_render = True
            obj.hide_viewport = True
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    if scene.world is not None:
        scene.world.use_nodes = False
        scene.world.color = (0.78, 0.80, 0.84)
    bpy.ops.object.light_add(type="AREA", location=(0.0, -3.4, 4.0))
    light = bpy.context.view_layer.objects.active
    light.name = "BML_AUDIT_visual_area_light"
    light.data.energy = 450.0
    light.data.size = 5.0
    light.hide_render = False
    light.hide_viewport = False
    bpy.ops.object.camera_add(location=(0.0, -5.2, 2.0))
    camera = bpy.context.view_layer.objects.active
    camera.name = "BML_AUDIT_visual_camera"
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 5.6
    orient_camera_to(camera, Vector((0.0, 0.0, 0.45)))
    scene.camera = camera
    return camera


def create_visual_rig(scene) -> list[bpy.types.Object]:
    configure_visual_capture_scene(scene)
    material = bpy.data.materials.new("BML_AUDIT_surface")
    material.diffuse_color = (0.30, 0.32, 0.34, 1.0)
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (0.30, 0.32, 0.34, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.65
    objects = []
    for index, offset in enumerate((-0.55, 0.0, 0.55)):
        bpy.ops.mesh.primitive_cube_add(size=0.75, location=(offset, 0.0, 0.45))
        obj = bpy.context.view_layer.objects.active
        obj.name = f"BML_AUDIT_visual_cube_{index}"
        obj.dimensions = (0.95, 0.75, 0.75)
        obj.rotation_euler[2] = math.radians((-18.0, 12.0, 28.0)[index])
        obj.data.materials.append(material)
        obj.hide_render = False
        obj.hide_viewport = False
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        objects.append(obj)
    objects[2].rotation_euler[1] = math.radians(10.0)
    return objects


def set_visual_line_settings(
    objects: list[bpy.types.Object],
    outline: bool,
    inner: bool,
    intersection: bool,
) -> None:
    for obj in objects:
        settings = obj.bmanga_line_settings
        set_line_flags(settings, outline, inner, intersection)
        settings.outline_thickness_mm = 80.0
        settings.inner_line_thickness_mm = 65.0
        settings.intersection_thickness_mm = 80.0
        settings.outline_color = (0.0, 0.0, 0.0, 1.0)
        settings.inner_line_color = (1.0, 0.05, 0.0, 1.0)
        settings.intersection_color = (0.0, 0.15, 1.0, 1.0)


def visual_proxy_material(target: str) -> bpy.types.Material:
    colors = {
        "outline": (0.0, 0.0, 0.0, 1.0),
        "inner": (1.0, 0.0, 0.0, 1.0),
        "intersection": (0.0, 0.1, 1.0, 1.0),
    }
    color = colors.get(target, (0.0, 0.0, 0.0, 1.0))
    mat = bpy.data.materials.get(f"BML_AUDIT_proxy_{target}") or bpy.data.materials.new(
        f"BML_AUDIT_proxy_{target}"
    )
    mat.diffuse_color = color
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = 1.0
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return mat


def line_material_target(modules: dict[str, Any], mat: bpy.types.Material | None) -> str | None:
    return modules["outline_setup"]._line_material_target(mat)


def create_line_proxies(
    audit: Audit,
    source_objects: list[bpy.types.Object],
    targets: set[str],
    name: str,
) -> list[bpy.types.Object]:
    modules = audit.modules
    depsgraph = bpy.context.evaluated_depsgraph_get()
    bpy.context.view_layer.update()
    proxies = []
    for obj in source_objects:
        eval_obj = obj.evaluated_get(depsgraph)
        eval_mesh = eval_obj.to_mesh()
        try:
            grouped: dict[str, list[tuple[Vector, Vector]]] = {}
            materials = list(eval_mesh.materials)
            for poly in eval_mesh.polygons:
                mat = materials[poly.material_index] if poly.material_index < len(materials) else None
                target = line_material_target(modules, mat)
                if target not in targets:
                    continue
                world_points = []
                for loop_index in poly.loop_indices:
                    vertex_index = eval_mesh.loops[loop_index].vertex_index
                    world_points.append(obj.matrix_world @ eval_mesh.vertices[vertex_index].co)
                if len(world_points) < 2:
                    continue
                segments = grouped.setdefault(target, [])
                for index, start in enumerate(world_points):
                    end = world_points[(index + 1) % len(world_points)]
                    segments.append((start, end))
            for target in ("outline", "inner", "intersection"):
                segments = grouped.get(target, [])
                if not segments:
                    continue
                curve = bpy.data.curves.new(
                    f"BML_AUDIT_proxy_{name}_{obj.name}_{target}",
                    type="CURVE",
                )
                curve.dimensions = "3D"
                curve.resolution_u = 2
                curve.bevel_depth = 0.012
                curve.bevel_resolution = 2
                for start, end in segments:
                    spline = curve.splines.new("POLY")
                    spline.points.add(1)
                    spline.points[0].co = (start.x, start.y, start.z, 1.0)
                    spline.points[1].co = (end.x, end.y, end.z, 1.0)
                proxy = bpy.data.objects.new(curve.name, curve)
                bpy.context.scene.collection.objects.link(proxy)
                curve.materials.append(visual_proxy_material(target))
                proxies.append(proxy)
        finally:
            eval_obj.to_mesh_clear()
    if not proxies:
        audit.fail(f"{name}: ライン撮影用メッシュを抽出できませんでした")
    return proxies


def save_visual_line_proxy(
    audit: Audit,
    scene,
    source_objects: list[bpy.types.Object],
    targets: set[str],
    name: str,
) -> None:
    proxies = create_line_proxies(audit, source_objects, targets, name)
    hidden = []
    for obj in source_objects:
        hidden.append((obj, obj.hide_viewport, obj.hide_render))
        obj.hide_viewport = True
        obj.hide_render = True
    save_camera_render(audit, scene, name)
    for obj, hide_viewport, hide_render in hidden:
        obj.hide_viewport = hide_viewport
        obj.hide_render = hide_render
    for proxy in proxies:
        data = proxy.data
        bpy.data.objects.remove(proxy, do_unlink=True)
        bpy.data.curves.remove(data)


def phase4(audit: Audit, _max_targets: int) -> None:
    scene, modules = load_scene(audit)
    targets = create_visual_rig(scene)
    configure_common_settings(targets, "SHELL")
    select_objects(targets)
    with forbid_auto_targets(audit):
        set_visual_line_settings(targets, True, False, False)
        for obj in targets:
            settings = obj.bmanga_line_settings
            settings.edge_smooth_factor = 1.0
            settings.edge_midpoint_jitter_percent = 0.0
        apply_selected(audit, targets, "toggle")
        save_visual_line_proxy(audit, scene, targets, {"outline"}, "outline_midpoint_smooth")

        set_visual_line_settings(targets, False, True, False)
        for obj in targets:
            settings = obj.bmanga_line_settings
            settings.inner_line_angle = math.radians(20.0)
            settings.inner_edge_smooth_factor = 1.0
            settings.inner_edge_midpoint_jitter_percent = 0.0
        apply_selected(audit, targets, "toggle")
        save_visual_line_proxy(audit, scene, targets, {"inner"}, "inner_midpoint_smooth")

        set_visual_line_settings(targets, False, False, True)
        for obj in targets:
            settings = obj.bmanga_line_settings
            settings.intersection_edge_smooth_factor = 1.0
            settings.intersection_edge_midpoint_jitter_percent = 0.0
        apply_selected(audit, targets, "toggle")
        save_visual_line_proxy(audit, scene, targets, {"intersection"}, "intersection_midpoint_smooth")

        set_visual_line_settings(targets, True, True, True)
        for obj in targets:
            settings = obj.bmanga_line_settings
            settings.edge_midpoint_jitter_percent = 50.0
            settings.inner_edge_midpoint_jitter_percent = 50.0
            settings.intersection_edge_midpoint_jitter_percent = 50.0
        apply_selected(audit, targets, "toggle")
        save_visual_line_proxy(
            audit,
            scene,
            targets,
            {"outline", "inner", "intersection"},
            "midpoint_jitter_all",
        )
    audit.result["counts"]["visual_rig"] = count_lines(targets, modules)


def phase5(audit: Audit, max_targets: int) -> None:
    scene, modules = load_scene(audit)
    targets = render_range_meshes(scene, modules)
    sample_count = 10 if max_targets <= 0 else min(max_targets, 10)
    sample = targets[:sample_count]
    configure_common_settings(sample, "SHELL")
    select_objects(sample)
    settings = active_settings()
    with forbid_auto_targets(audit):
        set_line_flags(settings, True, False, True, "SHELL")
        apply_selected(audit, sample, "toggle")
        save_camera_render(audit, scene, "intersection_shell_default")

    for method in ("BOOLEAN", "SDF"):
        set_line_flags(settings, True, False, True, method)
        with audit.time_op(f"intersection_method_{method}", "toggle"):
            apply_selected(audit, sample, "toggle")
        save_camera_render(audit, scene, f"intersection_{method.lower()}_sample")

    set_line_flags(settings, True, True, True, "SHELL")
    apply_selected(audit, sample, "toggle")
    with audit.time_op("ラインを非表示", "toggle"):
        bpy.ops.bmanga_line.set_visibility("EXEC_DEFAULT", visible=False)
    with audit.time_op("ラインを表示", "toggle"):
        bpy.ops.bmanga_line.set_visibility("EXEC_DEFAULT", visible=True)
    with audit.time_op("ラインのみを表示", "toggle"):
        bpy.ops.bmanga_line.set_line_only("EXEC_DEFAULT", line_only=True)
    save_camera_render(audit, scene, "line_only")
    with audit.time_op("通常表示に戻す", "toggle"):
        bpy.ops.bmanga_line.set_line_only("EXEC_DEFAULT", line_only=False)
    save_camera_render(audit, scene, "normal_restored")

    with audit.time_op("AOVパスを追加", "setting"):
        bpy.ops.bmanga_line.add_aov("EXEC_DEFAULT")
    with audit.time_op("ウェイトを更新", "setting"):
        bpy.ops.bmanga_line.sync_weights("EXEC_DEFAULT")
    with audit.time_op("線幅を更新", "setting"):
        bpy.ops.bmanga_line.refresh_camera("EXEC_DEFAULT")
    with audit.time_op("現在距離を基準にする", "setting"):
        bpy.ops.bmanga_line.reset_camera_ref("EXEC_DEFAULT")

    scene.bmanga_line_preset_name = "large audit preset"
    with audit.time_op("プリセット保存", "setting"):
        bpy.ops.bmanga_line.preset_save("EXEC_DEFAULT")
    with audit.time_op("プリセット適用", "setting"):
        bpy.ops.bmanga_line.preset_apply_selected("EXEC_DEFAULT")
    with audit.time_op("プリセット削除", "setting"):
        bpy.ops.bmanga_line.preset_delete("EXEC_DEFAULT")

    linked_count = len([obj for obj in scene.objects if getattr(obj, "library", None) is not None])
    audit.result["counts"]["linked_objects"] = linked_count
    if bpy.ops.bmanga_line.refresh_linked.poll():
        with audit.time_op("リンク素材のラインを補正", "setting"):
            bpy.ops.bmanga_line.refresh_linked("EXEC_DEFAULT")
        audit.result["counts"]["linked_refresh_applicable"] = True
    else:
        audit.result["counts"]["linked_refresh_applicable"] = False
    if bpy.ops.bmanga_line.apply_active_to_linked.poll():
        with audit.time_op("リンク素材へ選択設定を上書き", "setting"):
            bpy.ops.bmanga_line.apply_active_to_linked("EXEC_DEFAULT")
        audit.result["counts"]["linked_apply_applicable"] = True
    else:
        audit.result["counts"]["linked_apply_applicable"] = False
    audit.result["counts"]["phase5"] = count_lines(sample, modules)


def run_phase(phase: str, max_targets: int) -> Audit:
    prepare_dirs()
    audit = Audit(phase)
    if phase == "phase1":
        phase1(audit, max_targets)
    elif phase == "phase2":
        phase2(audit, max_targets)
    elif phase == "phase3":
        phase3(audit, max_targets)
    elif phase == "phase4":
        phase4(audit, max_targets)
    elif phase == "phase5":
        phase5(audit, max_targets)
    else:
        raise ValueError(phase)
    write_outputs(audit)
    return audit


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    phases = ["phase1", "phase2", "phase3", "phase4", "phase5"] if args.phase == "all" else [args.phase]
    failed = False
    for phase in phases:
        audit = Audit(phase)
        try:
            audit = run_phase(phase, args.max_targets)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            audit.fail(f"phase crashed: {exc}")
            write_outputs(audit)
        failed = failed or bool(audit.result["errors"])
    return 1 if failed else 0


if __name__ == "__main__":
    exit_code = main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    if not bpy.app.background:
        def _quit_blender():
            bpy.ops.wm.quit_blender()
            return None

        bpy.app.timers.register(_quit_blender, first_interval=0.1)
    else:
        sys.exit(exit_code)
