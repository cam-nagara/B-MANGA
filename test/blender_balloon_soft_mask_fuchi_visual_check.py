"""Blender実機用: 輪郭ぼかしコマ内フキダシのマスク・フチ・多重線を目視画像で検証。"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
_OUT_ENV = os.environ.get("BNAME_BALLOON_SOFT_MASK_FUCHI_VISUAL_OUT", "")
_OUT_PATH = Path(_OUT_ENV) if _OUT_ENV else Path(tempfile.mkdtemp(prefix="bname_balloon_soft_mask_fuchi_"))
OUTPUT_PATH = _OUT_PATH if _OUT_PATH.suffix.lower() == ".png" else _OUT_PATH / "balloon_soft_mask_fuchi_visual.png"


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_balloon_soft_mask_fuchi",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_balloon_soft_mask_fuchi"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


def _set_camera(center_x_m: float, center_y_m: float, scale_m: float):
    camera_data = bpy.data.cameras.new("輪郭ぼかし確認カメラ")
    camera = bpy.data.objects.new("輪郭ぼかし確認カメラ", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = (center_x_m, center_y_m, 2.0)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = scale_m
    bpy.context.scene.camera = camera
    return camera


def _project_to_pixel(scene, camera, world: Vector) -> tuple[int, int]:
    from bpy_extras.object_utils import world_to_camera_view

    coord = world_to_camera_view(scene, camera, world)
    return (int(coord.x * scene.render.resolution_x), int((1.0 - coord.y) * scene.render.resolution_y))


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


def _set_polygon(coma, points: list[tuple[float, float]]) -> None:
    coma.shape_type = "polygon"
    coma.vertices.clear()
    for x_mm, y_mm in points:
        vertex = coma.vertices.add()
        vertex.x_mm = float(x_mm)
        vertex.y_mm = float(y_mm)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    coma.rect_x_mm = min(xs)
    coma.rect_y_mm = min(ys)
    coma.rect_width_mm = max(xs) - min(xs)
    coma.rect_height_mm = max(ys) - min(ys)


def _sync_coma(scene, work, page, coma) -> None:
    from bname_dev_balloon_soft_mask_fuchi.utils import coma_border_object, coma_plane

    coma_plane.ensure_coma_plane(scene, work, page, coma)
    coma_plane.ensure_coma_mask(scene, work, page, coma)
    coma_border_object.ensure_coma_border_object(scene, work, page, coma)


def _create_extra_coma(page, coma_id: str, points: list[tuple[float, float]], color) -> object:
    coma = page.comas.add()
    coma.id = coma_id
    coma.coma_id = coma_id
    coma.title = coma_id
    _set_polygon(coma, points)
    coma.background_color = color
    coma.border.visible = True
    coma.border.style = "solid"
    coma.border.width_mm = 0.6
    coma.border.color = (0.0, 0.0, 0.0, 1.0)
    return coma


def _assert_material_masked(obj) -> None:
    found_by_material: dict[str, bool] = {}
    for mat in getattr(getattr(obj, "data", None), "materials", []) or []:
        if mat is None:
            continue
        found = False
        if getattr(mat, "node_tree", None) is not None:
            for node in mat.node_tree.nodes:
                if getattr(node, "label", "") == "コマ内容マスク":
                    found = True
                    break
        found_by_material[str(mat.name)] = found
    missing = [name for name, found in found_by_material.items() if not found]
    assert not missing, f"コマ内容マスクが未接続の素材があります: {missing}"


def _evaluated_material_z_ranges(obj) -> dict[str, tuple[float, float]]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        materials = list(getattr(mesh, "materials", []) or [])
        coords = [vertex.co.copy() for vertex in getattr(mesh, "vertices", []) or []]
        ranges: dict[str, list[float]] = {}
        for poly in getattr(mesh, "polygons", []) or []:
            material_index = int(getattr(poly, "material_index", 0) or 0)
            if not (0 <= material_index < len(materials)):
                continue
            mat = materials[material_index]
            if mat is None:
                continue
            name = str(mat.name)
            ranges.setdefault(name, [])
            for vertex_index in poly.vertices:
                ranges[name].append(float(coords[vertex_index].z))
        return {name: (min(values), max(values)) for name, values in ranges.items() if values}
    finally:
        evaluated.to_mesh_clear()


def _evaluated_material_world_z_ranges(obj) -> dict[str, tuple[float, float]]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        materials = list(getattr(mesh, "materials", []) or [])
        coords = [evaluated.matrix_world @ vertex.co for vertex in getattr(mesh, "vertices", []) or []]
        ranges: dict[str, list[float]] = {}
        for poly in getattr(mesh, "polygons", []) or []:
            material_index = int(getattr(poly, "material_index", 0) or 0)
            if not (0 <= material_index < len(materials)):
                continue
            mat = materials[material_index]
            if mat is None:
                continue
            name = str(mat.name)
            ranges.setdefault(name, [])
            for vertex_index in poly.vertices:
                ranges[name].append(float(coords[vertex_index].z))
        return {name: (min(values), max(values)) for name, values in ranges.items() if values}
    finally:
        evaluated.to_mesh_clear()


def _multi_line_visible_lengths(obj) -> list[float]:
    lengths: list[float] = []
    for spline in getattr(obj.data, "splines", []) or []:
        if getattr(spline, "type", "") != "POLY":
            continue
        points = list(getattr(spline, "points", []) or [])
        if not points:
            continue
        max_radius = max(float(getattr(point, "radius", 0.0) or 0.0) for point in points)
        if max_radius < 100.0 or max_radius >= 200.0:
            continue
        total = 0.0
        count = len(points)
        for index in range(count):
            p0 = points[index]
            p1 = points[(index + 1) % count]
            r0 = float(getattr(p0, "radius", 0.0) or 0.0) - 100.0
            r1 = float(getattr(p1, "radius", 0.0) or 0.0) - 100.0
            if r0 <= 1.0e-6 or r1 <= 1.0e-6:
                continue
            dx = float(p1.co.x) - float(p0.co.x)
            dy = float(p1.co.y) - float(p0.co.y)
            total += (dx * dx + dy * dy) ** 0.5
        lengths.append(total)
    return lengths


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_balloon_soft_mask_fuchi_work_"))
    mod = None
    try:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        mod = _load_addon()
        result = bpy.ops.bname.work_new(filepath=str(temp_root / "BalloonSoftMaskFuchi.bname"))
        assert "FINISHED" in result, result

        from bname_dev_balloon_soft_mask_fuchi.core.work import get_work
        from bname_dev_balloon_soft_mask_fuchi.operators import balloon_op
        from bname_dev_balloon_soft_mask_fuchi.utils import balloon_curve_object, geom, page_grid
        from bname_dev_balloon_soft_mask_fuchi.utils.layer_hierarchy import coma_stack_key

        context = bpy.context
        scene = context.scene
        work = get_work(context)
        assert work is not None and work.loaded
        page = work.pages[0]

        main_coma = page.comas[0]
        main_coma.id = "c04"
        main_coma.coma_id = "c04"
        main_coma.title = "輪郭ぼかし"
        _set_polygon(main_coma, [(45, 42), (138, 30), (128, 170), (34, 150)])
        main_coma.background_color = (1.0, 0.55, 0.88, 1.0)
        main_coma.border.visible = True
        main_coma.border.style = "brush"
        main_coma.border.width_mm = 35.0
        main_coma.border.blur_amount = 0.85
        main_coma.border.blur_dither = False
        main_coma.border.color = (1.0, 0.0, 0.55, 1.0)
        _sync_coma(scene, work, page, main_coma)

        side_coma = _create_extra_coma(page, "c05", [(130, 35), (175, 35), (170, 180), (122, 170)], (1.0, 1.0, 1.0, 1.0))
        lower_coma = _create_extra_coma(page, "c06", [(40, 150), (130, 170), (116, 238), (32, 222)], (1.0, 1.0, 1.0, 1.0))
        for coma in (side_coma, lower_coma):
            _sync_coma(scene, work, page, coma)

        entry = balloon_op._create_balloon_entry(
            context,
            page,
            shape="thorn",
            x=20.0,
            y=30.0,
            w=118.0,
            h=118.0,
            parent_kind="coma",
            parent_key=coma_stack_key(page, main_coma),
        )
        entry.title = "見切れ確認"
        entry.line_style = "double"
        entry.line_width_mm = 6.0
        entry.line_color = (0.0, 0.0, 0.0, 1.0)
        entry.fill_color = (1.0, 0.96, 0.35, 1.0)
        entry.fill_opacity = 100.0
        entry.opacity = 100.0
        entry.multi_line_count = 5
        entry.multi_line_width_mm = 0.75
        entry.multi_line_spacing_mm = 0.0
        entry.multi_line_width_scale_percent = 100.0
        entry.multi_line_direction = "outside"
        entry.thorn_multi_line_valley_width_mm = 0.3
        entry.thorn_multi_line_peak_width_mm = 0.3
        entry.thorn_multi_line_length_scale_percent = 82.0
        entry.thorn_multi_line_cross_enabled = False
        entry.outer_white_margin_enabled = True
        entry.outer_white_margin_width_mm = 1.0
        entry.outer_white_margin_color = (0.35, 1.0, 0.22, 1.0)
        entry.inner_white_margin_enabled = True
        entry.inner_white_margin_width_mm = 1.0
        entry.inner_white_margin_color = (0.35, 0.55, 1.0, 1.0)
        obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=entry, page=page)
        assert obj is not None and obj.type == "CURVE", "見切れ確認フキダシが作成されていません"
        _assert_material_masked(obj)

        front_entry = balloon_op._create_balloon_entry(
            context,
            page,
            shape="ellipse",
            x=62.0,
            y=72.0,
            w=42.0,
            h=36.0,
            parent_kind="coma",
            parent_key=coma_stack_key(page, main_coma),
        )
        front_entry.title = "前面確認"
        front_entry.line_width_mm = 0.3
        front_entry.line_color = (0.0, 0.0, 0.0, 1.0)
        front_entry.fill_color = (1.0, 1.0, 1.0, 1.0)
        front_entry.fill_opacity = 100.0
        front_entry.opacity = 100.0
        front_obj = balloon_curve_object.ensure_balloon_curve_object(scene=scene, entry=front_entry, page=page)
        assert front_obj is not None and front_obj.type == "CURVE", "前面確認フキダシが作成されていません"
        _assert_material_masked(front_obj)

        with balloon_curve_object.suspend_auto_sync():
            entry.fill_opacity = 96.0
            balloon_curve_object.on_balloon_entry_changed(entry)
        clip_masks = [obj for obj in bpy.data.objects if obj.name.startswith("balloon_clip_mask_")]
        assert not clip_masks, f"透明度マスク方式なのに古い切り抜きメッシュが残っています: {[obj.name for obj in clip_masks]}"

        ranges = _evaluated_material_z_ranges(obj)
        line_z = max(value[1] for name, value in ranges.items() if "BName_Balloon_Curve_" in name)
        edge_z = max(value[1] for name, value in ranges.items() if "BName_Balloon_Outer_Edge_" in name or "BName_Balloon_Inner_Edge_" in name)
        assert line_z > edge_z, f"主線がフチより前面になっていません: line={line_z}, edge={edge_z}"
        fill_top = max(value[1] for name, value in ranges.items() if "BName_Balloon_Fill_" in name)
        multi_bottom = min(value[0] for name, value in ranges.items() if "BName_Balloon_Curve_" in name)
        assert fill_top < edge_z < line_z, f"塗り・フチ・主線の前後関係が不正です: {ranges}"
        assert fill_top < multi_bottom, f"塗りが多重線より前面にあります: {ranges}"
        assert max(value[1] - value[0] for value in ranges.values()) < 0.0007, f"フキダシ内部の前後差が大きすぎます: {ranges}"

        back_world = _evaluated_material_world_z_ranges(obj)
        front_world = _evaluated_material_world_z_ranges(front_obj)
        back_line_z = max(value[1] for name, value in back_world.items() if "BName_Balloon_Curve_" in name)
        front_fill_z = min(value[0] for name, value in front_world.items() if "BName_Balloon_Fill_" in name)
        assert front_fill_z > back_line_z, (
            f"前面フキダシの塗りが背面フキダシの線を隠せる前後関係になっていません: "
            f"front_fill={front_fill_z}, back_line={back_line_z}"
        )

        lengths = _multi_line_visible_lengths(obj)
        assert len(lengths) >= 3, f"多重線の距離別検証に必要な線がありません: {lengths}"
        assert lengths[0] > lengths[-1] * 3.0, f"主線から離れた多重線の長さ変化が弱すぎます: {lengths}"

        ox_mm, oy_mm = page_grid.page_total_offset_mm(work, scene, 0)
        camera = _set_camera(geom.mm_to_m(ox_mm + 95.0), geom.mm_to_m(oy_mm + 115.0), geom.mm_to_m(190.0))
        scene.render.engine = "BLENDER_EEVEE"
        scene.world = scene.world or bpy.data.worlds.new("World")
        scene.world.color = (0.38, 0.38, 0.38)
        scene.render.resolution_x = 1200
        scene.render.resolution_y = 900
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
        scene.render.filepath = str(OUTPUT_PATH)
        render_result = bpy.ops.render.render(write_still=True)
        assert "FINISHED" in render_result, render_result

        inside_world = Vector((geom.mm_to_m(ox_mm + 52.0), geom.mm_to_m(oy_mm + 96.0), 0.02))
        outside_world = Vector((geom.mm_to_m(ox_mm + 36.0), geom.mm_to_m(oy_mm + 105.0), 0.02))
        side_world = Vector((geom.mm_to_m(ox_mm + 154.0), geom.mm_to_m(oy_mm + 105.0), 0.02))
        ix, iy = _project_to_pixel(scene, camera, inside_world)
        ox, oy = _project_to_pixel(scene, camera, outside_world)
        sx, sy = _project_to_pixel(scene, camera, side_world)
        inside = _sample_rgb(OUTPUT_PATH, ix, iy, radius=8)
        outside = _sample_rgb(OUTPUT_PATH, ox, oy, radius=8)
        side = _sample_rgb(OUTPUT_PATH, sx, sy, radius=8)
        assert inside[0] > 180.0 and inside[1] > 160.0 and inside[2] < 180.0, (
            f"コマ内フキダシの塗りが確認できません: rgb={inside}, out={OUTPUT_PATH}"
        )
        for label, color in (("コマ外", outside), ("隣接コマ", side)):
            assert not (color[0] > 170.0 and color[1] > 150.0 and color[2] < 150.0), (
                f"{label}へフキダシの塗りがはみ出しています: rgb={color}, out={OUTPUT_PATH}"
            )
            assert not (color[1] > color[0] + 35.0 and color[1] > color[2] + 35.0), (
                f"{label}へ外側フチがはみ出しています: rgb={color}, out={OUTPUT_PATH}"
            )
            assert not (color[2] > color[0] + 30.0 and color[2] > color[1] + 30.0), (
                f"{label}へ内側フチがはみ出しています: rgb={color}, out={OUTPUT_PATH}"
            )

        print(
            "BNAME_BALLOON_SOFT_MASK_FUCHI_VISUAL_OK "
            f"inside={tuple(round(v, 1) for v in inside)} "
            f"outside={tuple(round(v, 1) for v in outside)} "
            f"side={tuple(round(v, 1) for v in side)} "
            f"lengths={[round(v, 5) for v in lengths]} "
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
