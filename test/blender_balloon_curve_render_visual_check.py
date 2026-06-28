"""Blender実機用: フキダシの塗りと輪郭が実表示で分離していることを確認。"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BMANGA_BALLOON_CURVE_RENDER_VISUAL_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bmanga_balloon_curve_render_visual_"))
OUTPUT_PATH = _OUT_PATH if _OUT_PATH.suffix.lower() == ".png" else _OUT_PATH / "balloon_curve_white_fill_black_line.png"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bmanga_dev_balloon_curve_render_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bmanga_dev_balloon_curve_render_visual"] = mod
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


def _darkest_rgb(path: Path, x0: int, y0: int, x1: int, y1: int) -> tuple[float, float, float]:
    from PIL import Image

    with Image.open(path) as opened:
        image = opened.convert("RGB")
        left = max(0, min(x0, x1))
        right = min(image.width, max(x0, x1))
        top = max(0, min(y0, y1))
        bottom = min(image.height, max(y0, y1))
        if left >= right or top >= bottom:
            raise AssertionError(f"sample outside image: {path} ({x0}, {y0}, {x1}, {y1})")
        darkest = None
        darkest_luma = 999.0
        for py in range(top, bottom):
            for px in range(left, right):
                rgb = image.getpixel((px, py))
                luma = sum(rgb) / 3.0
                if luma < darkest_luma:
                    darkest = rgb
                    darkest_luma = luma
        assert darkest is not None
        return tuple(float(v) for v in darkest)


def _evaluated_material_names(obj) -> set[str]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    names = set()
    for candidate in bpy.context.scene.objects:
        data = getattr(candidate, "data", None)
        if data is None or not hasattr(data, "materials"):
            continue
        evaluated = candidate.evaluated_get(depsgraph)
        try:
            mesh = evaluated.to_mesh()
        except RuntimeError:
            for material in getattr(data, "materials", []) or []:
                if material is not None:
                    names.add(str(getattr(material, "name", "") or ""))
            continue
        try:
            materials = list(getattr(mesh, "materials", []) or [])
            for poly in getattr(mesh, "polygons", []) or []:
                index = int(getattr(poly, "material_index", 0) or 0)
                if 0 <= index < len(materials):
                    names.add(str(getattr(materials[index], "name", "") or ""))
        finally:
            evaluated.to_mesh_clear()
    return names


def _assert_close(actual: float, expected: float, label: str, eps: float = 1.0e-6) -> None:
    if abs(float(actual) - float(expected)) > eps:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def _material_node_types(obj, material_prefix: str) -> set[str]:
    del obj
    for material in bpy.data.materials:
        if material is None or not str(material.name).startswith(material_prefix):
            continue
        if material.node_tree is None:
            return set()
        return {str(getattr(node, "bl_idname", "") or "") for node in material.node_tree.nodes}
    raise AssertionError(f"material not found: {material_prefix}")


def _material_has_blur_attribute(obj, material_prefix: str, attribute_name: str) -> bool:
    del obj
    for material in bpy.data.materials:
        if material is None or not str(material.name).startswith(material_prefix):
            continue
        if material.node_tree is None:
            return False
        for node in material.node_tree.nodes:
            if getattr(node, "bl_idname", "") == "ShaderNodeAttribute" and getattr(node, "attribute_name", "") == attribute_name:
                return True
        return False
    return False


def _set_camera_for_object(obj, width_mm: float, height_mm: float) -> None:
    from bmanga_dev_balloon_curve_render_visual.utils.geom import mm_to_m

    center_x = obj.location.x
    center_y = obj.location.y
    camera_data = bpy.data.cameras.new("フキダシ確認カメラ")
    camera = bpy.data.objects.new("フキダシ確認カメラ", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = (center_x, center_y, obj.location.z + 2.0)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = mm_to_m(max(width_mm, height_mm) * 1.55)
    bpy.context.scene.camera = camera


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_balloon_curve_render_visual_work_"))
    mod = None
    try:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "BalloonCurveRenderVisual.bmanga"))
        assert "FINISHED" in result, result

        from bmanga_dev_balloon_curve_render_visual.core.work import get_work
        from bmanga_dev_balloon_curve_render_visual.operators import balloon_op
        from bmanga_dev_balloon_curve_render_visual.utils import balloon_curve_object
        from bmanga_dev_balloon_curve_render_visual.utils import balloon_curve_render_nodes
        from bmanga_dev_balloon_curve_render_visual.utils.layer_hierarchy import page_stack_key

        context = bpy.context
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        entry = balloon_op._create_balloon_entry(
            context,
            page,
            shape="ellipse",
            x=80.0,
            y=90.0,
            w=58.0,
            h=38.0,
            parent_kind="page",
            parent_key=page_stack_key(page),
        )
        entry.title = "白塗り確認"
        entry.line_width_mm = 3.0
        entry.line_color = (0.0, 0.0, 0.0, 1.0)
        entry.fill_color = (1.0, 1.0, 1.0, 1.0)
        entry.fill_opacity = 100.0
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        assert obj is not None, "フキダシ実体がありません"
        modifier = obj.modifiers.get(balloon_curve_render_nodes.MODIFIER_NAME)
        assert modifier is None, "撤去済みの旧GNモディファイアが残っています"
        material_names = _evaluated_material_names(obj)
        assert any(name.startswith(balloon_curve_object.BALLOON_FILL_MATERIAL_PREFIX) for name in material_names), (
            f"表示結果に塗り素材がありません: {sorted(material_names)}"
        )
        assert any(name.startswith(balloon_curve_object.BALLOON_CURVE_MATERIAL_PREFIX) for name in material_names), (
            f"表示結果に線素材がありません: {sorted(material_names)}"
        )

        _set_camera_for_object(obj, float(entry.width_mm), float(entry.height_mm))
        scene = context.scene
        scene.render.engine = "BLENDER_EEVEE"
        scene.world = scene.world or bpy.data.worlds.new("World")
        scene.world.color = (0.45, 0.45, 0.45)
        scene.render.resolution_x = 640
        scene.render.resolution_y = 480
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
        bpy.ops.object.select_all(action="DESELECT")
        output = OUTPUT_PATH
        scene.render.filepath = str(output)
        result = bpy.ops.render.render(write_still=True)
        assert "FINISHED" in result, result

        center = _sample_rgb(output, scene.render.resolution_x // 2, scene.render.resolution_y // 2, radius=10)
        right_line = _darkest_rgb(
            output,
            int(scene.render.resolution_x * 0.70),
            int(scene.render.resolution_y * 0.42),
            int(scene.render.resolution_x * 0.88),
            int(scene.render.resolution_y * 0.58),
        )
        if not (center[0] > 225.0 and center[1] > 225.0 and center[2] > 225.0):
            raise AssertionError(f"フキダシの塗りが白く表示されていません: center={center}")
        if not (right_line[0] < 80.0 and right_line[1] < 80.0 and right_line[2] < 80.0):
            raise AssertionError(f"フキダシの輪郭線が黒く表示されていません: line={right_line}")

        entry.fill_gradient_enabled = True
        entry.fill_gradient_start_color = (1.0, 0.25, 0.25, 1.0)
        entry.fill_gradient_end_color = (0.25, 0.25, 1.0, 0.55)
        entry.fill_gradient_angle_deg = 0.0
        entry.fill_blur_amount = 0.6
        entry.fill_blur_dither = True
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=context.scene, entry=entry, page=page)
        assert obj is not None, "フキダシ実体がありません"
        modifier = obj.modifiers.get(balloon_curve_render_nodes.MODIFIER_NAME)
        assert modifier is None, "撤去済みの旧GNモディファイアが残っています"
        _assert_close(float(entry.fill_blur_amount), 0.6, "塗り輪郭ぼかし")
        assert bool(entry.fill_blur_dither), "塗りぼかしをディザ化が設定されていません"
        assert _material_has_blur_attribute(
            obj,
            balloon_curve_object.BALLOON_FILL_MATERIAL_PREFIX,
            balloon_curve_render_nodes.FILL_BLUR_ALPHA_ATTRIBUTE,
        ), "塗り輪郭ぼかし用の不透明度が塗り素材に接続されていません"
        fill_node_types = _material_node_types(obj, balloon_curve_object.BALLOON_FILL_MATERIAL_PREFIX)
        assert "ShaderNodeTexWhiteNoise" in fill_node_types, "塗りぼかしのディザ用ノイズがありません"
        assert "ShaderNodeTexCoord" in fill_node_types and "ShaderNodeMapping" in fill_node_types, (
            "塗りグラデーション用の座標変換がありません"
        )
        print(
            "BMANGA_BALLOON_CURVE_RENDER_VISUAL_OK "
            f"center={tuple(round(v, 1) for v in center)} "
            f"line={tuple(round(v, 1) for v in right_line)} "
            f"out={output}",
            flush=True,
        )
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
        os._exit(0)
    except Exception:
        import traceback

        traceback.print_exc()
        os._exit(1)
