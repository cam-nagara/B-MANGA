from __future__ import annotations

import importlib.util
import math
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "_verify" / "effect_line_white_outline_after.png"
REFERENCE_PATH = ROOT / "_verify" / "effect_line_white_outline_reference_like.png"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_white_outline_visual",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_white_outline_visual"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _material(name: str, color: tuple[float, float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    emission = nodes.new(type="ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = 1.0
    output = nodes.new(type="ShaderNodeOutputMaterial")
    mat.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return mat


def _background_plane() -> None:
    bpy.ops.mesh.primitive_plane_add(size=0.40, location=(0.105, 0.105, -0.01))
    plane = bpy.context.object
    plane.name = "スクショ背景"
    plane.data.materials.append(_material("ピンク背景", (1.0, 0.42, 0.88, 1.0)))


def _white_outline_params() -> SimpleNamespace:
    return SimpleNamespace(
        effect_type="white_outline",
        start_shape="rect",
        end_shape="rect",
        rotation_deg=0.0,
        start_rounded_corner_enabled=False,
        end_rounded_corner_enabled=False,
        white_outline_count=7,
        white_outline_spacing_mm=0.45,
        white_outline_width_mm=18.0,
        white_outline_width_jitter_enabled=True,
        white_outline_width_min_percent=82.0,
        white_outline_length_jitter_enabled=True,
        white_outline_length_min_percent=70.0,
        white_outline_white_ratio_percent=72.0,
        white_outline_white_brush_mm=0.45,
        white_outline_white_attenuation=18.0,
        white_outline_black_brush_mm=0.34,
        white_outline_black_attenuation=-8.0,
        white_outline_angle_deg=90.0,
    )


def _white_outline_reference_params() -> SimpleNamespace:
    params = _white_outline_params()
    params.white_outline_count = 1
    params.white_outline_spacing_mm = 0.38
    params.white_outline_width_mm = 42.0
    params.white_outline_width_jitter_enabled = False
    params.white_outline_length_jitter_enabled = False
    params.white_outline_white_ratio_percent = 74.0
    params.white_outline_white_brush_mm = 0.42
    params.white_outline_black_brush_mm = 0.46
    params.white_outline_angle_deg = 270.0
    return params


def _assert_white_outline_strokes(strokes, center: tuple[float, float]) -> None:
    white = [stroke for stroke in strokes if getattr(stroke, "role", "") == "white_outline_white"]
    black = [stroke for stroke in strokes if getattr(stroke, "role", "") == "white_outline_black"]
    if len(white) <= 7:
        raise AssertionError(f"抜きのある白線が複数生成されていません: {len(white)}")
    if len(black) < 14:
        raise AssertionError(f"左右の黒線が不足しています: white={len(white)} black={len(black)}")
    for index, stroke in enumerate(white):
        points = list(getattr(stroke, "points_xyz", []) or [])
        if len(points) != 2 or bool(getattr(stroke, "cyclic", False)):
            raise AssertionError(f"白線が抜きのある直線になっていません: {index}")
        radii = list(getattr(stroke, "radii", None) or [])
        if len(radii) < 2 or not float(radii[0]) > float(radii[-1]):
            raise AssertionError(f"白線の終点が抜けていません: {index}")
        start, end = points
        if math.dist(start[:2], center) <= math.dist(end[:2], center):
            raise AssertionError(f"白線が中心へ向かっていません: {index}")


def _build_display(effect_line_object, strokes, name: str) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name)
    display = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(display)
    display.data.materials.append(_material("黒線", (0.0, 0.0, 0.0, 1.0)))
    display.data.materials.append(_material("白抜き線", (1.0, 1.0, 1.0, 1.0)))
    display.data.materials.append(_material("白抜き線予備", (1.0, 1.0, 1.0, 1.0)))
    effect_line_object._rebuild_effect_display_mesh(mesh, strokes)  # noqa: SLF001
    if len(mesh.polygons) <= 0:
        raise AssertionError("白抜き線の表示メッシュが空です")
    if not any(poly.material_index == 1 for poly in mesh.polygons):
        raise AssertionError("白抜き線の面が表示メッシュにありません")
    if not any(poly.material_index == 0 for poly in mesh.polygons):
        raise AssertionError("左右の黒線の面が表示メッシュにありません")
    return display


def _setup_camera(center: tuple[float, float] = (0.105, 0.105), ortho_scale: float = 0.32) -> None:
    bpy.ops.object.camera_add(location=(center[0], center[1], 1.0), rotation=(0.0, 0.0, 0.0))
    camera = bpy.context.object
    bpy.context.scene.camera = camera
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = float(ortho_scale)
    bpy.ops.object.light_add(type="AREA", location=(0.105, 0.105, 0.7))
    light = bpy.context.object
    light.data.energy = 400.0
    light.data.size = 4.0


def _render_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:
        scene.render.engine = "BLENDER_EEVEE"
    scene.world.color = (1.0, 0.42, 0.88)
    scene.render.resolution_x = 900
    scene.render.resolution_y = 1200
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    if not path.exists() or path.stat().st_size <= 0:
        raise AssertionError(f"スクリーンショットを保存できません: {path}")


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _render_reference_like(effect_line_gen, effect_line_object) -> None:
    _clear_scene()
    center_mm = (105.0, 174.0)
    params = _white_outline_reference_params()
    strokes = effect_line_gen.generate_strokes(
        params,
        center_xy_mm=center_mm,
        radius_xy_mm=(36.0, 82.0),
        seed=18,
    )
    _assert_white_outline_strokes(strokes, (center_mm[0] / 1000.0, center_mm[1] / 1000.0))
    _build_display(effect_line_object, strokes, "B-Name 白抜き線 比較用")
    _background_plane()
    _setup_camera(center=(0.105, 0.095), ortho_scale=0.24)
    _render_png(REFERENCE_PATH)


def main() -> None:
    _load_addon()
    from bname_dev_white_outline_visual.operators import effect_line_gen
    from bname_dev_white_outline_visual.utils import effect_line_object

    _clear_scene()
    params = _white_outline_params()
    strokes = effect_line_gen.generate_strokes(
        params,
        center_xy_mm=(105.0, 105.0),
        radius_xy_mm=(42.0, 66.0),
        seed=12,
    )
    _assert_white_outline_strokes(strokes, (0.105, 0.105))

    _build_display(effect_line_object, strokes, "B-Name 白抜き線 スクショ")

    _background_plane()
    _setup_camera()
    _render_png(OUT_PATH)
    _render_reference_like(effect_line_gen, effect_line_object)
    print(
        "BNAME_EFFECT_LINE_WHITE_OUTLINE_VISUAL_OK "
        f"screenshot={OUT_PATH} reference={REFERENCE_PATH}",
        flush=True,
    )


if __name__ == "__main__":
    main()
