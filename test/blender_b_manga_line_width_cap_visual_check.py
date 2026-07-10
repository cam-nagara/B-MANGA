"""Blender実機用: 設定線幅上限のレンダー結果を画素で検証する."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "_verify" / "2026-07-10_bml_width_cap_visual"
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import camera_comp, core, presets  # noqa: E402


WIDTH = 800
HEIGHT = 400


def _material(name: str, color) -> bpy.types.Material:
    material = bpy.data.materials.new(name)
    material.diffuse_color = color
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = 1.0
    material.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return material


def _configure_scene() -> bpy.types.Object:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = WIDTH
    scene.render.resolution_y = HEIGHT
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.render.image_settings.color_mode = "RGBA"
    world = bpy.data.worlds.new("BML_width_cap_world")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    background.inputs["Strength"].default_value = 1.0
    scene.world = world
    bpy.ops.object.camera_add(location=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 4.0
    camera.data.clip_start = 0.01
    camera.data.clip_end = 100.0
    scene.camera = camera
    return camera


def _make_cube(name: str, x: float, depth: float, material) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.4, location=(x, 0.0, -depth))
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(material)
    return obj


def _configure_lines(objects: list[bpy.types.Object]) -> None:
    old = core._propagating
    core._propagating = True
    try:
        for obj in objects:
            settings = obj.bmanga_line_settings
            settings.outline_enabled = True
            settings.outline_thickness_mm = 1.0
            settings.outline_color = (0.0, 0.0, 0.0, 1.0)
            settings.exclude_sheet_meshes = False
            settings.use_outline_creation_limit = False
            settings.use_outline_distance_limit = False
            settings.use_camera_culling = False
            settings.use_uniform_line_width = True
            settings.line_width_reference_distance = 4.0
            settings.line_width_distance_falloff = 1.0
            settings.limit_uniform_width_to_setting = False
            presets.apply_line_settings(
                obj,
                bpy.context,
                refresh_scene=False,
            )
    finally:
        core._propagating = old
    camera_comp.refresh_objects(
        bpy.context,
        objects,
        width_targets=("outline",),
    )


def _render(path: Path) -> list[float]:
    scene = bpy.context.scene
    scene.render.filepath = str(path)
    assert bpy.ops.render.render(write_still=True) == {"FINISHED"}
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        assert tuple(image.size) == (WIDTH, HEIGHT), tuple(image.size)
        pixels = list(image.pixels[:])
        assert len(pixels) == WIDTH * HEIGHT * 4, len(pixels)
        return pixels
    finally:
        bpy.data.images.remove(image)


def _dark_at(pixels: list[float], x: int, y: int) -> bool:
    index = (y * WIDTH + x) * 4
    return max(pixels[index:index + 3]) < 0.15


def _boundary_run(pixels: list[float], expected_x: int) -> int:
    y = HEIGHT // 2
    candidates = [
        x for x in range(expected_x - 55, expected_x + 56)
        if _dark_at(pixels, x, y)
    ]
    assert candidates, expected_x
    groups = []
    current = [candidates[0]]
    for x in candidates[1:]:
        if x == current[-1] + 1:
            current.append(x)
        else:
            groups.append(current)
            current = [x]
    groups.append(current)
    group = min(groups, key=lambda item: abs(sum(item) / len(item) - expected_x))
    return len(group)


def _line_widths(pixels: list[float]) -> dict[str, float]:
    # ortho_scale=4、横幅800/縦400なので、x=-1/+1の1.4m立方体境界は
    # おおむね60/340/460/740pxに来る。
    return {
        "near": (
            _boundary_run(pixels, 60) + _boundary_run(pixels, 340)
        ) / 2.0,
        "far": (
            _boundary_run(pixels, 460) + _boundary_run(pixels, 740)
        ) / 2.0,
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    b_manga_line.register()
    try:
        _configure_scene()
        near = _make_cube(
            "BML_width_cap_near",
            -1.0,
            2.0,
            _material("BML_width_cap_near_surface", (0.42, 0.72, 0.95, 1.0)),
        )
        far = _make_cube(
            "BML_width_cap_far",
            1.0,
            8.0,
            _material("BML_width_cap_far_surface", (0.95, 0.55, 0.58, 1.0)),
        )
        objects = [near, far]
        _configure_lines(objects)
        off_pixels = _render(OUTPUT_DIR / "cap_off.png")
        off_widths = _line_widths(off_pixels)

        old = core._propagating
        core._propagating = True
        try:
            for obj in objects:
                obj.bmanga_line_settings.limit_uniform_width_to_setting = True
        finally:
            core._propagating = old
        camera_comp.refresh_objects(
            bpy.context,
            objects,
            width_targets=("outline",),
        )
        on_pixels = _render(OUTPUT_DIR / "cap_on.png")
        on_widths = _line_widths(on_pixels)

        assert off_widths["near"] > on_widths["near"] * 1.45, (
            off_widths,
            on_widths,
        )
        assert abs(off_widths["far"] - on_widths["far"]) <= 1.0, (
            off_widths,
            on_widths,
        )
        assert on_widths["near"] > on_widths["far"] * 1.45, on_widths
        result = {"cap_off": off_widths, "cap_on": on_widths}
        (OUTPUT_DIR / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("BML_WIDTH_CAP_VISUAL_OK", json.dumps(result))
    finally:
        b_manga_line.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)


if __name__ == "__main__":
    main()
