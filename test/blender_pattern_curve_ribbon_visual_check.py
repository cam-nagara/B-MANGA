"""Blender実機用: パターンカーブのリボン表示を目視確認用にレンダーする。"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import struct
import sys
import tempfile
import zlib
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "bmanga_dev_pattern_curve_ribbon_visual"
OUTPUT_DIR = ROOT / "_verify" / "pattern_curve_ribbon_visual"
OUTPUT_PATH = OUTPUT_DIR / "pattern_curve_ribbon_visual.png"
MANIFEST_PATH = OUTPUT_DIR / "pattern_curve_ribbon_visual_cases.json"

CASES = (
    {
        "id": "R1",
        "label": "repeat straight",
        "description": "ブラシサイズの画像を直線上で連続表示",
    },
    {
        "id": "R2",
        "label": "stretch bend",
        "description": "始点から終点まで画像ひとつを曲線状に伸ばして表示",
    },
    {
        "id": "R3",
        "label": "repeat angle",
        "description": "画像角度を付けた繰り返しリボン",
    },
    {
        "id": "R4",
        "label": "repeat inout",
        "description": "サイズ・不透明度・色の入り抜きを同時適用した繰り返しリボン",
    },
    {
        "id": "R5",
        "label": "stretch mask",
        "description": "コマ内マスク付きの伸ばしリボン",
    },
)


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _write_pattern_png(path: Path, width: int = 96, height: int = 24) -> None:
    rows = []
    for y in range(height):
        raw = bytearray([0])
        for x in range(width):
            stripe = (x // 12) % 4
            if stripe == 0:
                color = (240, 40, 40, 255)
            elif stripe == 1:
                color = (255, 230, 30, 255)
            elif stripe == 2:
                color = (30, 110, 255, 255)
            else:
                color = (255, 255, 255, 255)
            if (x + y * 2) % 24 in {0, 1, 2}:
                color = (20, 20, 20, 255)
            raw.extend(color)
        rows.append(bytes(raw))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + _png_chunk(b"IEND", b"")
    )


def _uv_values(obj) -> list[tuple[float, float]]:
    uv_layer = getattr(obj.data, "uv_layers", None)
    assert uv_layer is not None and uv_layer.active is not None, "UV がありません"
    return [tuple(data.uv) for data in uv_layer.active.data]


def _point_colors(obj) -> list[tuple[float, float, float, float]]:
    attr = getattr(obj.data, "attributes", None)
    assert attr is not None, "頂点属性がありません"
    layer = attr.get("bmanga_path_content_color")
    assert layer is not None, "色の頂点属性がありません"
    return [tuple(data.color) for data in layer.data]


def _strip_widths(obj) -> list[float]:
    verts = obj.data.vertices
    return [
        (verts[i].co - verts[i + 1].co).length
        for i in range(0, max(0, len(verts) - 1), 2)
    ]


def _material_has_content_mask(mat) -> bool:
    nt = getattr(mat, "node_tree", None)
    if nt is None:
        return False
    has_tex = False
    has_coord = False
    for node in nt.nodes:
        if (
            node.bl_idname == "ShaderNodeTexImage"
            and node.label == "コマ内容マスク"
            and getattr(node, "image", None) is not None
        ):
            has_tex = True
        if (
            node.bl_idname == "ShaderNodeTexCoord"
            and node.label == "コマ内容マスク座標"
            and getattr(node, "object", None) is not None
        ):
            has_coord = True
    return has_tex and has_coord


def _material_has_image(mat) -> bool:
    nt = getattr(mat, "node_tree", None)
    if nt is None:
        return False
    return any(
        node.bl_idname == "ShaderNodeTexImage" and getattr(node, "image", None) is not None
        for node in nt.nodes
    )


def _emission_material(name: str, color: tuple[float, float, float, float]):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = 1.0
    nt.links.new(emission.outputs["Emission"], out.inputs["Surface"])
    return mat


def _add_label(text: str, x_mm: float, y_mm: float) -> None:
    mat = _emission_material("BManga_Test_Label", (0.02, 0.02, 0.02, 1.0))
    bpy.ops.object.text_add(location=(x_mm * 0.001, y_mm * 0.001, 0.14), rotation=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = f"目視確認_{text}"
    obj.data.body = text
    obj.data.align_x = "LEFT"
    obj.data.align_y = "CENTER"
    obj.data.size = 0.0045
    obj.data.materials.append(mat)


def _visual_bounds(objects) -> tuple[float, float, float, float]:
    points = []
    for obj in objects:
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    if not points:
        return 0.0, 0.0, 0.2, 0.23
    return (
        min(float(point.x) for point in points),
        min(float(point.y) for point in points),
        max(float(point.x) for point in points),
        max(float(point.y) for point in points),
    )


def _add_backdrop(bounds: tuple[float, float, float, float]) -> None:
    mat = _emission_material("BManga_Test_Backdrop", (1.0, 1.0, 1.0, 1.0))
    min_x, min_y, max_x, max_y = bounds
    pad = 0.025
    mesh = bpy.data.meshes.new("BManga_Test_Backdrop_mesh")
    mesh.from_pydata(
        [
            (min_x - pad, min_y - pad, 0.06),
            (max_x + pad, min_y - pad, 0.06),
            (max_x + pad, max_y + pad, 0.06),
            (min_x - pad, max_y + pad, 0.06),
        ],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.materials.append(mat)
    obj = bpy.data.objects.new("BManga_Test_Backdrop", mesh)
    bpy.context.scene.collection.objects.link(obj)


def _make_entry(scene, case_id: str, image_path: Path, page_key: str, points: list[list[float]], **kwargs):
    entry = scene.bmanga_image_path_layers.add()
    entry.id = f"pattern_curve_{case_id.lower()}"
    entry.title = f"{case_id} {kwargs.pop('title')}"
    entry.parent_kind = kwargs.pop("parent_kind", "page")
    entry.parent_key = kwargs.pop("parent_key", page_key)
    entry.filepath = str(image_path)
    entry.content_source = "image"
    entry.draw_mode = "ribbon"
    entry.path_points_json = json.dumps(points)
    for name, value in kwargs.items():
        setattr(entry, name, value)
    return entry


def _entry_by_id(scene, entry_id: str):
    for entry in getattr(scene, "bmanga_image_path_layers", []) or []:
        if str(getattr(entry, "id", "") or "") == entry_id:
            return entry
    raise AssertionError(f"パターンカーブが見つかりません: {entry_id}")


def _ensure_entry_object(scene, work, page, entry):
    from bmanga_dev_pattern_curve_ribbon_visual.utils import image_path_object

    obj = image_path_object.ensure_image_path_object(scene=scene, entry=entry, page=page)
    assert obj is not None, f"{entry.title} の実体が作成されません"
    assert len(obj.data.polygons) >= len(json.loads(entry.path_points_json)) - 1, (
        f"{entry.title} のリボン面数が不足しています"
    )
    assert obj.data.materials and _material_has_image(obj.data.materials[0]), (
        f"{entry.title} の画像素材が読み込まれていません"
    )
    return obj


def _create_coma(scene, work, page):
    from bmanga_dev_pattern_curve_ribbon_visual.utils import coma_border_object, coma_plane

    if len(page.comas) == 0:
        assert "FINISHED" in bpy.ops.bmanga.coma_add()
    coma = page.comas[0]
    coma.shape_type = "rect"
    coma.rect_x_mm = 45.0
    coma.rect_y_mm = 166.0
    coma.rect_width_mm = 105.0
    coma.rect_height_mm = 42.0
    coma_plane.ensure_coma_plane(scene, work, page, coma)
    coma_plane.ensure_coma_mask(scene, work, page, coma)
    coma_border_object.ensure_coma_border_object(scene, work, page, coma)
    return coma


def _prepare_camera(scene, bounds: tuple[float, float, float, float]) -> None:
    try:
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
    except Exception:
        pass
    camera_data = bpy.data.cameras.new("B-MANGA_パターンカーブ目視確認カメラ")
    camera = bpy.data.objects.new("B-MANGA_パターンカーブ目視確認カメラ", camera_data)
    scene.collection.objects.link(camera)
    min_x, min_y, max_x, max_y = bounds
    width = max(max_x - min_x, 0.05)
    height = max(max_y - min_y, 0.05)
    aspect = 1400.0 / 1600.0
    camera.location = ((min_x + max_x) * 0.5, (min_y + max_y) * 0.5, 1.0)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = max(height * 1.35, width * 1.35 / aspect, 0.08)
    scene.camera = camera
    scene.render.resolution_x = 1400
    scene.render.resolution_y = 1600
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:
        pass
    if scene.world is not None:
        scene.world.color = (1.0, 1.0, 1.0)
    bpy.context.view_layer.update()


def _render(scene) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(OUTPUT_PATH)
    scene.render.image_settings.file_format = "PNG"
    result = bpy.ops.render.render(write_still=True)
    if "FINISHED" not in result:
        raise RuntimeError(f"render failed: {result}")


def _prepare_visual_render_objects(objects, mask_apply) -> tuple[float, float, float, float]:
    for index, obj in enumerate(objects):
        obj.location.z = 0.10 + index * 0.002
        for mod in obj.modifiers:
            if mod.name in {mask_apply.MOD_NAME_PAGE_MASK, mask_apply.MOD_NAME_COMA_MASK}:
                mod.show_render = False
    bpy.context.view_layer.update()
    bounds = _visual_bounds(objects)
    _add_backdrop(bounds)
    bpy.context.view_layer.update()
    return bounds


def _assert_png_has_expected_colors(path: Path) -> None:
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        pixels = image.pixels[:]
        step = max(1, (width * height) // 250000)
        foreground = red = yellow = blue = dark = 0
        sample_count = 0
        for pixel_index in range(0, width * height, step):
            base = pixel_index * 4
            r, g, b, a = pixels[base : base + 4]
            if a > 0.1 and (r < 0.96 or g < 0.96 or b < 0.96):
                foreground += 1
            if a > 0.1 and r > 0.65 and g < 0.35 and b < 0.35:
                red += 1
            if a > 0.1 and r > 0.75 and g > 0.55 and b < 0.30:
                yellow += 1
            if a > 0.1 and b > 0.55 and r < 0.35:
                blue += 1
            if a > 0.1 and r < 0.35 and g < 0.35 and b < 0.35:
                dark += 1
            sample_count += 1
        total = max(sample_count, 1)
        assert foreground / total > 0.006, "目視確認画像にリボンがほとんど写っていません"
        assert red > 8, "赤い画像パターンが目視確認画像に見つかりません"
        assert yellow > 8, "黄色い画像パターンが目視確認画像に見つかりません"
        assert blue > 8, "青い画像パターンが目視確認画像に見つかりません"
        assert dark > 8, "斜線パターンまたはラベルが目視確認画像に見つかりません"
    finally:
        bpy.data.images.remove(image)


def _write_manifest() -> None:
    payload = {
        "image": str(OUTPUT_PATH),
        "patterns": list(CASES),
    }
    MANIFEST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_pattern_curve_ribbon_"))
    os.environ["BMANGA_USER_CONFIG_DIR"] = str(temp_root / "config")
    image_path = temp_root / "pattern_curve_texture.png"
    _write_pattern_png(image_path)
    mod = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bmanga.work_new(filepath=str(temp_root / "PatternCurveRibbon.bmanga"))
        assert "FINISHED" in result, result
        result = bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0)
        assert "FINISHED" in result, result

        from bmanga_dev_pattern_curve_ribbon_visual.core.work import get_work
        from bmanga_dev_pattern_curve_ribbon_visual.utils import image_path_object
        from bmanga_dev_pattern_curve_ribbon_visual.utils import layer_object_sync, layer_stack, mask_apply
        from bmanga_dev_pattern_curve_ribbon_visual.utils.layer_hierarchy import coma_stack_key, page_stack_key

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]
        page_key = page_stack_key(page)
        coma = _create_coma(scene, work, page)
        coma_key = coma_stack_key(page, coma)

        for index, case in enumerate(CASES):
            _add_label(f"{case['id']} {case['label']}", 7.0, 30.0 + index * 39.0)

        with image_path_object.suspend_auto_sync():
            entry_ids = [
                _make_entry(
                    scene,
                    "R1",
                    image_path,
                    page_key,
                    [[35.0, 30.0], [175.0, 30.0]],
                    title="repeat straight",
                    brush_size_mm=8.0,
                    aspect_ratio=3.0,
                    spacing_percent=75.0,
                    ribbon_repeat_mode="repeat",
                    image_angle_deg=0.0,
                ).id,
                _make_entry(
                    scene,
                    "R2",
                    image_path,
                    page_key,
                    [[35.0, 58.0], [80.0, 72.0], [125.0, 52.0], [175.0, 68.0]],
                    title="stretch bend",
                    brush_size_mm=8.0,
                    aspect_ratio=2.5,
                    spacing_percent=100.0,
                    ribbon_repeat_mode="stretch",
                    image_angle_deg=0.0,
                ).id,
                _make_entry(
                    scene,
                    "R3",
                    image_path,
                    page_key,
                    [[35.0, 100.0], [85.0, 100.0], [135.0, 100.0], [175.0, 100.0]],
                    title="repeat angle",
                    brush_size_mm=8.0,
                    aspect_ratio=2.8,
                    spacing_percent=50.0,
                    ribbon_repeat_mode="repeat",
                    image_angle_deg=35.0,
                ).id,
                _make_entry(
                    scene,
                    "R4",
                    image_path,
                    page_key,
                    [[35.0, 135.0], [70.0, 122.0], [120.0, 146.0], [175.0, 135.0]],
                    title="repeat inout",
                    brush_size_mm=11.0,
                    aspect_ratio=2.2,
                    spacing_percent=70.0,
                    ribbon_repeat_mode="repeat",
                    inout_size_enabled=True,
                    inout_opacity_enabled=True,
                    inout_color_enabled=True,
                    in_percent=20.0,
                    out_percent=25.0,
                    in_start_percent=35.0,
                    out_start_percent=35.0,
                    inout_start_color=(1.0, 0.0, 0.0, 1.0),
                    inout_end_color=(0.0, 0.0, 1.0, 0.25),
                ).id,
                _make_entry(
                    scene,
                    "R5",
                    image_path,
                    page_key,
                    [[20.0, 186.0], [80.0, 170.0], [130.0, 202.0], [178.0, 186.0]],
                    title="stretch mask",
                    parent_kind="coma",
                    parent_key=coma_key,
                    brush_size_mm=10.0,
                    aspect_ratio=3.0,
                    spacing_percent=100.0,
                    ribbon_repeat_mode="stretch",
                ).id,
            ]
        entries = [_entry_by_id(scene, entry_id) for entry_id in entry_ids]

        objects = [_ensure_entry_object(scene, work, page, entry) for entry in entries]
        layer_stack.sync_layer_stack_after_data_change(context)
        layer_object_sync.assign_per_page_z_ranks(scene, work)
        bpy.context.view_layer.update()

        repeat_uvs = _uv_values(objects[0])
        assert max(u for u, _v in repeat_uvs) > 5.0, "繰り返しリボンのUVが連続していません"

        stretch_uvs = _uv_values(objects[1])
        assert abs(max(u for u, _v in stretch_uvs) - 1.0) <= 1.0e-4, "伸ばしリボンのUV終点が1ではありません"
        assert min(u for u, _v in stretch_uvs) >= -1.0e-4, "伸ばしリボンのUV始点が0未満です"

        angle_uvs = _uv_values(objects[2])
        assert min(v for _u, v in angle_uvs) < -0.05, "角度付きリボンのUV回転が反映されていません"

        inout_colors = _point_colors(objects[3])
        assert min(c[3] for c in inout_colors) < max(c[3] for c in inout_colors), (
            "リボンの不透明度入り抜きが効いていません"
        )
        assert any(c[0] > c[2] + 0.2 for c in inout_colors), "リボンの入り色が反映されていません"
        assert any(c[2] > c[0] + 0.2 for c in inout_colors), "リボンの抜き色が反映されていません"
        inout_widths = _strip_widths(objects[3])
        assert min(inout_widths) < max(inout_widths) * 0.6, "リボンのサイズ入り抜きが効いていません"

        mask_obj = objects[4]
        coma_mod = mask_obj.modifiers.get(mask_apply.MOD_NAME_COMA_MASK)
        assert coma_mod is not None and getattr(coma_mod, "object", None) is not None, (
            "コマ内リボンにコママスクがありません"
        )
        assert mask_obj.data.materials and _material_has_content_mask(mask_obj.data.materials[0]), (
            "コマ内リボンにコマ内容マスクがありません"
        )

        bounds = _prepare_visual_render_objects(objects, mask_apply)
        _prepare_camera(scene, bounds)
        _render(scene)
        assert OUTPUT_PATH.is_file(), "目視確認画像が生成されていません"
        _assert_png_has_expected_colors(OUTPUT_PATH)
        _write_manifest()
        print(f"BMANGA_PATTERN_CURVE_RIBBON_VISUAL_OK {OUTPUT_PATH}", flush=True)
    finally:
        if mod is not None:
            try:
                mod.unregister()
            except Exception:
                pass
        bpy.ops.wm.read_factory_settings(use_empty=True)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
        os._exit(0)
    except Exception:
        import traceback

        traceback.print_exc()
        os._exit(1)
