"""Blender実機用: B-MANGA Line 交差対象ライン厚みの塗り確認."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import core, intersection_lines, outline_setup  # noqa: E402


OUT_DIR = ROOT / "_verify" / "b_manga_line_intersection_fill"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PNG = OUT_DIR / "intersection_fill.png"


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for collection in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.node_groups,
        bpy.data.cameras,
        bpy.data.lights,
    ):
        for datablock in list(collection):
            if datablock.users == 0:
                collection.remove(datablock)


def _emission_material(name: str, color: tuple[float, float, float, float]):
    mat = bpy.data.materials.new(name)
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


def _make_source_slab(white_mat):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = "交差確認_白い面"
    obj.dimensions = (3.0, 3.0, 0.10)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(white_mat)
    return obj


def _make_target_cylinder(white_mat):
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=96,
        radius=0.50,
        depth=1.20,
        location=(0.0, 0.0, 0.0),
    )
    obj = bpy.context.object
    obj.name = "交差確認_対象"
    obj.data.materials.append(white_mat)
    outline_setup.apply_outline(
        obj,
        thickness=0.24,
        color=(0.0, 0.0, 0.0, 1.0),
        scene=bpy.context.scene,
    )
    return obj


def _evaluated_copy(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    copy = bpy.data.objects.new("交差確認_評価結果", mesh)
    bpy.context.collection.objects.link(copy)
    return copy


def _setup_render() -> int:
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"
    if hasattr(scene, "eevee"):
        scene.eevee.taa_render_samples = 16
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.render.resolution_x = 256
    scene.render.resolution_y = 256
    scene.render.film_transparent = False
    scene.world.color = (1.0, 1.0, 1.0)

    bpy.ops.object.camera_add(location=(0.0, 0.0, 4.0), rotation=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    camera.name = "交差確認_カメラ"
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 3.0
    scene.camera = camera
    scene.render.filepath = str(OUT_PNG)
    return scene.render.resolution_x


def _sample_image(image, world_x: float, world_y: float, *, ortho_scale: float = 3.0):
    width = bpy.context.scene.render.resolution_x
    height = bpy.context.scene.render.resolution_y
    px = int((world_x / ortho_scale + 0.5) * width)
    py = int((world_y / ortho_scale + 0.5) * height)
    px = max(0, min(width - 1, px))
    py = max(0, min(height - 1, py))
    idx = (py * width + px) * 4
    return tuple(image.pixels[idx:idx + 4])


def _luma(rgb):
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def main() -> None:
    b_manga_line.register()
    _clear_scene()

    white_mat = _emission_material("確認用_白", (1.0, 1.0, 1.0, 1.0))
    source = _make_source_slab(white_mat)
    target = _make_target_cylinder(white_mat)
    line_mat = outline_setup.get_or_create_material(source, (0.0, 0.0, 0.0, 1.0))
    assert line_mat is not None, "線の素材が作成されていません"
    source.data.materials.append(line_mat)

    assert intersection_lines.apply_intersection_lines(
        source,
        target=target,
        thickness=0.015,
        material=line_mat,
        method="BOOLEAN",
    )

    tree = bpy.data.node_groups.get(core.INTERSECTION_TREE_BOOLEAN)
    assert tree is not None, "交差線の生成設定が作成されていません"
    assert any(
        getattr(node, "label", "") == "BML_TargetLineFill" for node in tree.nodes
    ), "交差対象のライン厚みを塗る経路がありません"

    result = _evaluated_copy(source)
    source.hide_render = True
    source.hide_viewport = True
    target.hide_render = True
    target.hide_viewport = True
    result.select_set(True)

    _setup_render()
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(OUT_PNG), check_existing=False)

    band = _sample_image(image, 0.62, 0.0)
    outside = _sample_image(image, 1.10, 0.0)
    center = _sample_image(image, 0.00, 0.0)

    assert _luma(band) < 0.20, (
        f"対象ライン厚みの内側が線色で塗られていません: {band}"
    )
    assert _luma(outside) > 0.80, f"線の外側まで黒くなっています: {outside}"
    assert _luma(center) > 0.80, f"対象ラインの内側全体が黒くなっています: {center}"

    print(f"[OK] 交差対象ライン厚みの塗りを確認: {OUT_PNG}", flush=True)


if __name__ == "__main__":
    main()
