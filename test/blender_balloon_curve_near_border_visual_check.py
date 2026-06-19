"""Blender実機用: コマ枠近接フキダシでコマ内が黒面化しないことを確認。"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_BALLOON_NEAR_BORDER_VISUAL_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_balloon_near_border_visual_"))
OUTPUT_PATH = _OUT_PATH if _OUT_PATH.suffix.lower() == ".png" else _OUT_PATH / "balloon_near_border_no_black_coma.png"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_balloon_near_border_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_balloon_near_border_visual"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _sample_rgb(path: Path, x: int, y: int, radius: int = 5) -> tuple[float, float, float]:
    from PIL import Image

    with Image.open(path) as opened:
        image = opened.convert("RGB")
        pixels = []
        for py in range(max(0, y - radius), min(image.height, y + radius + 1)):
            for px in range(max(0, x - radius), min(image.width, x + radius + 1)):
                pixels.append(image.getpixel((px, py)))
    if not pixels:
        raise AssertionError(f"sample outside image: {path} ({x}, {y})")
    return tuple(sum(pixel[i] for pixel in pixels) / len(pixels) for i in range(3))


def _set_camera(center_x_m: float, center_y_m: float, scale_m: float) -> bpy.types.Camera:
    camera_data = bpy.data.cameras.new("近接確認カメラ")
    camera = bpy.data.objects.new("近接確認カメラ", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = (center_x_m, center_y_m, 2.0)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = scale_m
    bpy.context.scene.camera = camera
    return camera


def _add_white_coma_reference(*, ox_mm: float, oy_mm: float, coma, geom) -> None:
    x0 = geom.mm_to_m(ox_mm + float(coma.rect_x_mm))
    y0 = geom.mm_to_m(oy_mm + float(coma.rect_y_mm))
    x1 = geom.mm_to_m(ox_mm + float(coma.rect_x_mm) + float(coma.rect_width_mm))
    y1 = geom.mm_to_m(oy_mm + float(coma.rect_y_mm) + float(coma.rect_height_mm))
    mesh = bpy.data.meshes.new("黒面化確認_白いコマ面_mesh")
    mesh.from_pydata(
        [(x0, y0, -0.001), (x1, y0, -0.001), (x1, y1, -0.001), (x0, y1, -0.001)],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    obj = bpy.data.objects.new("黒面化確認_白いコマ面", mesh)
    bpy.context.collection.objects.link(obj)
    material = bpy.data.materials.new("黒面化確認_白")
    material.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    emission.inputs["Strength"].default_value = 1.0
    material.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    mesh.materials.append(material)


def _project_to_pixel(scene, camera, world: Vector) -> tuple[int, int]:
    from bpy_extras.object_utils import world_to_camera_view

    coord = world_to_camera_view(scene, camera, world)
    x = int(coord.x * scene.render.resolution_x)
    y = int((1.0 - coord.y) * scene.render.resolution_y)
    return x, y


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_balloon_near_border_visual_work_"))
    mod = None
    try:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "BalloonNearBorderVisual.bmanga"))
        assert "FINISHED" in result, result

        from bmanga_dev_balloon_near_border_visual.core.work import get_work
        from bmanga_dev_balloon_near_border_visual.operators import balloon_op
        from bmanga_dev_balloon_near_border_visual.utils import balloon_curve_object
        from bmanga_dev_balloon_near_border_visual.utils import coma_plane
        from bmanga_dev_balloon_near_border_visual.utils import geom
        from bmanga_dev_balloon_near_border_visual.utils import page_grid
        from bmanga_dev_balloon_near_border_visual.utils.layer_hierarchy import coma_stack_key

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        coma = page.comas[0]
        coma.shape_type = "rect"
        coma.rect_x_mm = 20.0
        coma.rect_y_mm = 35.0
        coma.rect_width_mm = 120.0
        coma.rect_height_mm = 150.0
        coma.background_color = (1.0, 1.0, 1.0, 1.0)
        parent_key = coma_stack_key(page, coma)
        coma_plane.ensure_coma_plane(scene, work, page, coma)
        coma_plane.ensure_coma_mask(scene, work, page, coma)

        entry = balloon_op._create_balloon_entry(
            context,
            page,
            shape="ellipse",
            x=105.0,
            y=92.0,
            w=35.0,
            h=30.0,
            parent_kind="coma",
            parent_key=parent_key,
        )
        entry.title = "コマ枠近接"
        entry.line_width_mm = 6.0
        entry.line_color = (0.0, 0.0, 0.0, 1.0)
        entry.fill_color = (0.0, 0.0, 0.0, 1.0)
        entry.fill_opacity = 100.0
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
        assert obj is not None, "近接フキダシが作成されていません"

        ox_mm, oy_mm = page_grid.page_total_offset_mm(work, scene, 0)
        _add_white_coma_reference(ox_mm=ox_mm, oy_mm=oy_mm, coma=coma, geom=geom)
        center_x = geom.mm_to_m(ox_mm + coma.rect_x_mm + coma.rect_width_mm * 0.5)
        center_y = geom.mm_to_m(oy_mm + coma.rect_y_mm + coma.rect_height_mm * 0.5)
        camera = _set_camera(center_x, center_y, geom.mm_to_m(190.0))

        scene.render.engine = "BLENDER_EEVEE"
        scene.world = scene.world or bpy.data.worlds.new("World")
        scene.world.color = (0.45, 0.45, 0.45)
        scene.render.resolution_x = 720
        scene.render.resolution_y = 720
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
        scene.render.filepath = str(OUTPUT_PATH)
        render_result = bpy.ops.render.render(write_still=True)
        assert "FINISHED" in render_result, render_result

        white_probe = Vector((geom.mm_to_m(ox_mm + 60.0), geom.mm_to_m(oy_mm + 95.0), 0.02))
        black_probe = Vector((geom.mm_to_m(ox_mm + 122.0), geom.mm_to_m(oy_mm + 107.0), 0.02))
        wx, wy = _project_to_pixel(scene, camera, white_probe)
        bx, by = _project_to_pixel(scene, camera, black_probe)
        white_area = _sample_rgb(OUTPUT_PATH, wx, wy, radius=8)
        balloon_area = _sample_rgb(OUTPUT_PATH, bx, by, radius=8)
        if not (white_area[0] > 210.0 and white_area[1] > 210.0 and white_area[2] > 210.0):
            raise AssertionError(f"コマ内が黒面化しています: rgb={white_area}, out={OUTPUT_PATH}")
        if not (balloon_area[0] < 50.0 and balloon_area[1] < 50.0 and balloon_area[2] < 50.0):
            raise AssertionError(f"黒フキダシ本体が表示されていません: rgb={balloon_area}, out={OUTPUT_PATH}")
        print(
            "BMANGA_BALLOON_NEAR_BORDER_VISUAL_OK "
            f"white={tuple(round(v, 1) for v in white_area)} "
            f"balloon={tuple(round(v, 1) for v in balloon_area)} "
            f"out={OUTPUT_PATH}",
            flush=True,
        )
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
