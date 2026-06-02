"""Generate a sample image for balloon uni flash / white outline shapes."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = Path(
    os.environ.get(
        "BNAME_BALLOON_FLASH_SAMPLE_OUT",
        str(ROOT / "_verify" / "balloon_flash_shape_samples.png"),
    )
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_balloon_flash_sample",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_balloon_flash_sample"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_camera(objects: list[bpy.types.Object]) -> None:
    xs = [float(obj.location.x) for obj in objects]
    ys = [float(obj.location.y) for obj in objects]
    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)
    unit_scale = 0.001 if (max_x - min_x) < 1.0 else 1.0
    pad_x = 90.0 * unit_scale
    pad_y = 165.0 * unit_scale
    aspect = 1800.0 / 1050.0
    width = (max_x - min_x) + pad_x * 2.0
    height = (max_y - min_y) + pad_y * 2.0
    scale = max(height, width / aspect)
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5

    camera_data = bpy.data.cameras.new("サンプル確認カメラ")
    camera = bpy.data.objects.new("サンプル確認カメラ", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = (center_x, center_y, 2.0)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = scale
    bpy.context.scene.camera = camera


def _material(name: str, color: tuple[float, float, float, float]):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    for node in list(nodes):
        nodes.remove(node)
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = 1.0
    output = nodes.new("ShaderNodeOutputMaterial")
    mat.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return mat


def _add_label(text: str, x: float, y: float, size: float) -> bpy.types.Object:
    obj = bpy.data.objects.new(text, bpy.data.curves.new(text, "FONT"))
    obj.data.body = text
    obj.data.align_x = "CENTER"
    obj.data.align_y = "CENTER"
    obj.data.size = size
    obj.location = (x, y, 0.2)
    obj.data.materials.append(_material(f"mat_{text}", (0.0, 0.0, 0.0, 1.0)))
    bpy.context.collection.objects.link(obj)
    return obj


def _add_background(objects: list[bpy.types.Object]) -> bpy.types.Object:
    xs = [float(obj.location.x) for obj in objects]
    ys = [float(obj.location.y) for obj in objects]
    unit_scale = 0.001 if (max(xs) - min(xs)) < 1.0 else 1.0
    min_x = min(xs) - 90.0 * unit_scale
    max_x = max(xs) + 90.0 * unit_scale
    min_y = min(ys) - 90.0 * unit_scale
    max_y = max(ys) + 90.0 * unit_scale
    mesh = bpy.data.meshes.new("サンプル背景_mesh")
    mesh.from_pydata(
        [(min_x, min_y, -0.15), (max_x, min_y, -0.15), (max_x, max_y, -0.15), (min_x, max_y, -0.15)],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    obj = bpy.data.objects.new("サンプル背景", mesh)
    obj.data.materials.append(_material("mat_サンプル背景", (0.94, 0.94, 0.92, 1.0)))
    bpy.context.collection.objects.link(obj)
    return obj


def _configure_shape_params(entry, *, height: float = 10.0, width: float = 4.0) -> None:
    sp = entry.shape_params
    sp.dynamic_shape_base_kind = "ellipse"
    sp.cloud_bump_width_mm = width
    sp.cloud_bump_height_mm = height
    sp.cloud_offset_percent = 18.0
    sp.cloud_bump_width_jitter = 0.0
    sp.cloud_bump_height_jitter = 0.0
    sp.cloud_sub_width_ratio = 0.0
    sp.cloud_sub_height_ratio = 0.0
    sp.shape_seed = 0


def _create_sample(context, page, parent_key, *, shape: str, x: float, y: float, variant: str):
    from bname_dev_balloon_flash_sample.operators import balloon_op
    from bname_dev_balloon_flash_sample.utils import balloon_curve_object

    entry = balloon_op._create_balloon_entry(
        context,
        page,
        shape=shape,
        x=x,
        y=y,
        w=52.0,
        h=38.0,
        parent_kind="page",
        parent_key=parent_key,
    )
    _configure_shape_params(entry)
    entry.line_width_mm = 1.6
    entry.flash_line_count = 96
    entry.flash_line_spacing_mm = 1.0
    entry.line_color = (0.0, 0.0, 0.0, 1.0)
    if shape == "white_outline":
        entry.fill_color = (0.08, 0.17, 0.34, 1.0)
        entry.line_width_mm = 1.05
        entry.flash_white_line_width_percent = 300.0
        entry.flash_white_line_peak_width_pct = 100.0
        entry.flash_white_outline_count = 5
        entry.flash_white_outline_width_mm = 22.0
        entry.flash_white_outline_white_line_count = 18
        entry.flash_white_outline_black_line_count = 4
    else:
        entry.fill_color = (0.66, 0.76, 0.62, 1.0)
        entry.flash_white_line_width_percent = 140.0
        entry.flash_white_line_peak_width_pct = 100.0
    entry.fill_opacity = 100.0

    if variant == "taper":
        entry.line_width_mm = 2.2 if shape == "uni_flash" else 1.1
        entry.flash_line_count = 120
        entry.flash_line_spacing_mm = 0.8
        entry.line_valley_width_pct = 0.0
        entry.line_peak_width_pct = 100.0
        entry.flash_white_line_width_percent = 135.0 if shape == "uni_flash" else 300.0
        entry.flash_white_line_valley_width_pct = 0.0
        entry.flash_white_line_peak_width_pct = 100.0
        entry.thorn_multi_line_valley_width_pct = 0.0
        entry.thorn_multi_line_peak_width_pct = 100.0
    elif variant == "multi":
        entry.line_width_mm = 1.4 if shape == "uni_flash" else 1.0
        entry.flash_line_count = 180
        entry.flash_line_spacing_mm = 0.45
        entry.flash_white_line_width_percent = 135.0 if shape == "uni_flash" else 300.0
        entry.flash_white_line_valley_width_pct = 0.0
        entry.flash_white_line_peak_width_pct = 100.0
        if shape == "white_outline":
            entry.flash_white_outline_count = 8
            entry.flash_white_outline_width_mm = 26.0
            entry.flash_white_outline_white_line_count = 26
            entry.flash_white_outline_black_line_count = 5
        entry.multi_line_count = 4
        entry.multi_line_direction = "outside"
        entry.multi_line_width_mm = 0.45
        entry.multi_line_spacing_mm = 0.9
        entry.multi_line_width_scale_percent = 100.0
        entry.multi_line_spacing_scale_percent = 100.0
        entry.thorn_multi_line_valley_width_pct = 0.0
        entry.thorn_multi_line_peak_width_pct = 100.0
        entry.thorn_multi_line_length_scale_near_percent = 100.0
        entry.thorn_multi_line_length_scale_far_percent = 100.0

    obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
    assert obj is not None
    print(f"[SAMPLE_OBJECT] {shape} {variant} location={tuple(round(float(v), 6) for v in obj.location)}")
    return obj


def _render(path: Path) -> None:
    scene = bpy.context.scene
    engine_items = {
        item.identifier for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
    }
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engine_items else "BLENDER_EEVEE"
    scene.render.resolution_x = 1800
    scene.render.resolution_y = 1050
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(path)
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    if scene.world is None:
        scene.world = bpy.data.worlds.new("サンプル背景")
    scene.world.color = (0.92, 0.92, 0.92)
    bpy.ops.render.render(write_still=True)


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_balloon_flash_sample_"))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "BalloonFlashSample.bname"))
        assert "FINISHED" in result, result

        from bname_dev_balloon_flash_sample.core.work import get_work
        from bname_dev_balloon_flash_sample.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        parent_key = page_stack_key(page)

        objects = []
        for row_y, shape in ((155.0, "uni_flash"), (20.0, "white_outline")):
            objects.append(_create_sample(context, page, parent_key, shape=shape, x=55.0, y=row_y, variant="default"))
            objects.append(_create_sample(context, page, parent_key, shape=shape, x=130.0, y=row_y, variant="taper"))
            objects.append(_create_sample(context, page, parent_key, shape=shape, x=205.0, y=row_y, variant="multi"))

        xs = [float(obj.location.x) for obj in objects]
        ys = [float(obj.location.y) for obj in objects]
        unit_scale = 0.001 if (max(xs) - min(xs)) < 1.0 else 1.0
        text_size = 5.2 * unit_scale
        top_y = max(ys) + 45.0 * unit_scale
        row_label_x = min(xs) - 42.0 * unit_scale
        col_objects = [
            _add_label("白線あり", float(objects[0].location.x), top_y, text_size),
            _add_label("入り・抜き 0%", float(objects[1].location.x), top_y, text_size),
            _add_label("本数・間隔", float(objects[2].location.x), top_y, text_size),
            _add_label("ウニフラ", row_label_x, float(objects[0].location.y), text_size),
            _add_label("白抜き線", row_label_x, float(objects[3].location.y), text_size),
        ]

        _add_background(objects + col_objects)
        _set_camera(objects + col_objects)
        _render(OUT_PATH)
        print(f"BNAME_BALLOON_FLASH_SAMPLE_OK {OUT_PATH}")
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    main()
