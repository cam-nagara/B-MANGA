"""B-MANGA Line: split AOVs can composite inverted-hull outlines into line-only output."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "_verify" / "2026-07-04_bml_line_aov_composite"
sys.path.insert(0, str(ROOT / "addons"))

import b_manga_line  # noqa: E402
from b_manga_line import aov_compositor, core, outline_setup  # noqa: E402


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
        for item in list(collection):
            if item.users == 0:
                collection.remove(item)


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


def _configure_render(scene: bpy.types.Scene) -> None:
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16
    scene.render.resolution_x = 640
    scene.render.resolution_y = 420
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0


def _make_cube_scene() -> bpy.types.Object:
    scene = bpy.context.scene
    _configure_render(scene)
    bpy.ops.object.camera_add(location=(0.0, -5.0, 0.0), rotation=(1.5707963268, 0.0, 0.0))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 2.8
    scene.camera = camera

    bpy.ops.mesh.primitive_cube_add(size=1.4, location=(0.0, 0.0, 0.0))
    cube = bpy.context.object
    cube.name = "線画合成確認_立方体"
    cube.rotation_euler = (0.22, 0.0, 0.38)
    cube.bmanga_line_settings.outline_color = (0.0, 0.0, 1.0, 1.0)
    surface = _emission_material("線画合成確認_白面", (1.0, 1.0, 1.0, 1.0))
    cube.data.materials.append(surface)
    assert outline_setup.apply_outline(
        cube,
        thickness=0.12,
        color=(0.0, 0.0, 1.0, 1.0),
        use_rim=False,
        scene=scene,
    )
    outline_mat = outline_setup.get_outline_material(cube)
    assert outline_mat is not None
    outline_setup._build_outline_nodes(  # noqa: SLF001 - compositor subtraction fixture
        outline_mat,
        (0.0, 0.0, 1.0, 1.0),
        target="outline",
        double_sided=True,
    )
    outline_mat.use_backface_culling = False
    return cube


def _aov_names(scene: bpy.types.Scene) -> set[str]:
    names: set[str] = set()
    for view_layer in scene.view_layers:
        names.update(aov.name for aov in view_layer.aovs)
    return names


def _material_has_aov(mat: bpy.types.Material, name: str) -> bool:
    assert mat.use_nodes and mat.node_tree is not None, mat.name
    return any(getattr(node, "aov_name", "") == name for node in mat.node_tree.nodes)


def _render_composite(scene: bpy.types.Scene) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("bml_line_*.png"):
        old.unlink()
    target = OUT_DIR / "bml_line_composite.png"
    tree = aov_compositor.setup_line_aov_compositor(scene, output_path=target)
    _assert_grouped_compositor(tree)
    _add_debug_render_output(tree, "Image", "bml_line_beauty")
    _add_debug_aov_output(tree, core.AOV_NAME, "bml_line_legacy")
    _add_debug_aov_output(tree, core.AOV_OUTLINE_RAW_NAME, "bml_line_outline_raw")
    _add_debug_aov_output(tree, core.AOV_OBJECT_MASK_NAME, "bml_line_object_mask")
    bpy.context.view_layer.update()
    bpy.ops.render.render(write_still=False)
    candidates = sorted(OUT_DIR.glob("bml_line_composite*.png"))
    assert candidates, "線画合成画像が出力されていません"
    return candidates[-1]


def _assert_grouped_compositor(tree: bpy.types.NodeTree) -> None:
    group_nodes = [
        node for node in tree.nodes
        if node.name == f"{aov_compositor.NODE_PREFIX}_Group"
    ]
    assert len(group_nodes) == 1, [node.name for node in tree.nodes]
    group = group_nodes[0]
    assert group.node_tree is not None
    assert group.node_tree.name == aov_compositor.GROUP_TREE_NAME
    direct_processing = [
        node.name for node in tree.nodes
        if node.name.startswith(aov_compositor.NODE_PREFIX + "_")
        and node.name not in {
            f"{aov_compositor.NODE_PREFIX}_RenderLayers",
            f"{aov_compositor.NODE_PREFIX}_Group",
            f"{aov_compositor.NODE_PREFIX}_Result",
            f"{aov_compositor.NODE_PREFIX}_FileOutput",
        }
    ]
    assert not direct_processing, direct_processing
    assert group.node_tree.nodes.get(
        f"{aov_compositor.NODE_PREFIX}_SetTransparentLineAlpha"
    ) is not None


def _add_debug_aov_output(tree: bpy.types.NodeTree, aov_name: str, stem: str) -> None:
    _add_debug_render_output(tree, aov_name, stem)


def _add_debug_render_output(tree: bpy.types.NodeTree, output_name: str, stem: str) -> None:
    rlayers = next(
        node for node in tree.nodes
        if node.name == f"{aov_compositor.NODE_PREFIX}_RenderLayers"
    )
    socket = rlayers.outputs.get(output_name)
    assert socket is not None, f"{output_name} ソケットがありません"
    out = tree.nodes.new("CompositorNodeOutputFile")
    out.name = f"BML_Test_{stem}"
    out.label = out.name
    out.location = (920.0, -440.0)
    if hasattr(out, "directory"):
        out.directory = str(OUT_DIR)
        out.file_name = ""
    if hasattr(out, "base_path"):
        out.base_path = str(OUT_DIR)
    fmt = getattr(out, "format", None)
    if fmt is not None:
        fmt.media_type = "IMAGE"
        fmt.file_format = "PNG"
        fmt.color_mode = "RGBA"
    items = getattr(out, "file_output_items", None)
    if items is not None:
        for item in list(items):
            items.remove(item)
        items.new("RGBA", stem)
        target_input = out.inputs.get(stem)
    else:
        target_input = next((s for s in out.inputs if getattr(s, "enabled", True)), None)
    assert target_input is not None, f"{stem} 出力ソケットがありません"
    tree.links.new(socket, target_input)


def _assert_line_only_pixels(path: Path) -> None:
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        w, h = image.size
        pixels = list(image.pixels)
        blue_total = 0
        blue_center = 0
        alpha_total = 0
        center_x0 = int(w * 0.38)
        center_x1 = int(w * 0.62)
        center_y0 = int(h * 0.32)
        center_y1 = int(h * 0.68)
        for y in range(h):
            for x in range(w):
                index = (y * w + x) * 4
                r, g, b, a = pixels[index:index + 4]
                if a > 0.01:
                    alpha_total += 1
                is_blue = b > 0.30 and r < 0.18 and g < 0.18 and a > 0.0
                if is_blue:
                    blue_total += 1
                    if center_x0 <= x < center_x1 and center_y0 <= y < center_y1:
                        blue_center += 1
    finally:
        bpy.data.images.remove(image)
    assert blue_total > 200, f"アウトラインが少なすぎます: {blue_total}"
    assert blue_center < 30, f"中央面にアウトラインAOVの塗りが残っています: {blue_center}"
    assert alpha_total < w * h * 0.25, f"線画以外の透明度が残っています: {alpha_total}/{w * h}"


def main() -> None:
    b_manga_line.register()
    _clear_scene()
    scene = bpy.context.scene
    cube = _make_cube_scene()
    outline_setup.ensure_aov_passes(scene)
    outline_setup.repair_scene_line_materials(scene)
    missing = set(core.AOV_NAMES) - _aov_names(scene)
    assert not missing, f"線画AOVが不足しています: {sorted(missing)}"
    assert bpy.ops.bmanga_line.setup_aov_composite() == {"FINISHED"}

    surface_mat = cube.data.materials[0]
    outline_mat = outline_setup.get_outline_material(cube)
    assert outline_mat is not None
    assert _material_has_aov(surface_mat, core.AOV_OBJECT_MASK_NAME)
    assert _material_has_aov(outline_mat, core.AOV_OUTLINE_RAW_NAME)

    output = _render_composite(scene)
    _assert_line_only_pixels(output)
    print(f"[PASS] B-MANGA Line AOV composite outputs line-only image: {output}")
    bpy.ops.wm.quit_blender()


if __name__ == "__main__":
    main()
