"""B-MANGA Line: transparent surfaces do not reveal the far-side outline fill."""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, outline_setup, presets  # noqa: E402


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _configure_scene() -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16
    scene.cycles.transparent_max_bounces = 32
    scene.render.resolution_x = 96
    scene.render.resolution_y = 96
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    if scene.world:
        scene.world.color = (1.0, 1.0, 1.0)

    bpy.ops.object.camera_add(location=(0.0, 0.0, 4.0), rotation=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 2.8
    scene.camera = camera


def _make_transparent_cube() -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = "BML_transparent_cube"
    mat = bpy.data.materials.new("BML_fully_transparent_surface")
    mat.use_nodes = True
    mat.diffuse_color = (1.0, 1.0, 1.0, 0.0)
    try:
        mat.blend_method = "BLEND"
    except (AttributeError, TypeError):
        pass
    try:
        mat.surface_render_method = "BLENDED"
    except (AttributeError, TypeError):
        pass
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    links.new(transparent.outputs["BSDF"], output.inputs["Surface"])
    obj.data.materials.append(mat)
    return obj


def _select(obj: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _render_pixels() -> tuple[int, int, list[float]]:
    path = Path(tempfile.gettempdir()) / "bml_transparent_surface_check.png"
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = int(image.size[0]), int(image.size[1])
        return width, height, list(image.pixels)
    finally:
        bpy.data.images.remove(image)
        try:
            path.unlink()
        except OSError:
            pass


def _pixel_rgb(width: int, pixels: list[float], x: int, y: int) -> tuple[float, float, float]:
    index = (y * width + x) * 4
    alpha = float(pixels[index + 3])
    return tuple(
        float(pixels[index + offset]) * alpha + (1.0 - alpha)
        for offset in range(3)
    )


def _dark_pixels(width: int, height: int, pixels: list[float]) -> int:
    count = 0
    for y in range(height):
        for x in range(width):
            r, g, b = _pixel_rgb(width, pixels, x, y)
            if max(r, g, b) < 0.2:
                count += 1
    return count


def _luma(rgb: tuple[float, float, float]) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def main() -> None:
    b_manga_line.register()
    _clear_scene()
    _configure_scene()
    obj = _make_transparent_cube()
    _select(obj)

    settings = obj.bmanga_line_settings
    # 奥側アウトラインの透過面保護だけを分離して検証するため、前後頂点で
    # 厚みが変わる遠近減衰は無効化する（遠近減衰は専用テストで検証）。
    settings.line_width_distance_falloff = 0.0
    settings.outline_thickness = 0.18
    settings.outline_color = (0.0, 0.0, 0.0, 1.0)
    settings.hide_through_transparent = False
    assert presets.apply_line_settings(obj, bpy.context)

    width, height, pixels = _render_pixels()
    dark_without = _dark_pixels(width, height, pixels)
    center_without = _pixel_rgb(width, pixels, width // 2, height // 2)
    assert dark_without > 50, dark_without

    settings.hide_through_transparent = True
    assert presets.apply_line_settings(obj, bpy.context)
    mat = outline_setup.get_outline_material(obj)
    assert mat is not None
    assert bool(mat.get(outline_setup.PROP_HIDE_THROUGH_TRANSPARENT, False))

    width, height, pixels = _render_pixels()
    center_with = _pixel_rgb(width, pixels, width // 2, height // 2)
    assert _luma(center_with) > _luma(center_without) + 0.10, (
        center_with,
        center_without,
    )
    dark_count = _dark_pixels(width, height, pixels)
    assert dark_count < dark_without, (dark_count, dark_without)
    assert core.has_outline(obj)

    # 初期設定の組み合わせでも、実用線幅が透明面の中央を覆わず、
    # 外周線だけが残ることを確認する。
    settings.outline_thickness_mm = 0.5
    settings.use_uniform_line_width = True
    settings.line_width_distance_falloff = 1.0
    assert presets.apply_line_settings(obj, bpy.context)
    width, height, pixels = _render_pixels()
    center_default = _pixel_rgb(width, pixels, width // 2, height // 2)
    background_default = _pixel_rgb(width, pixels, 2, 2)
    assert max(
        abs(center_default[index] - background_default[index])
        for index in range(3)
    ) < 0.02, (center_default, background_default)

    print("[PASS] transparent surfaces hide far-side B-MANGA Line fill")


if __name__ == "__main__":
    main()
