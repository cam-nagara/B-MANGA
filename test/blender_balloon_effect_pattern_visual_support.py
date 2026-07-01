"""Support helpers for the balloon/effect-line visual audit."""

from __future__ import annotations

from pathlib import Path

import bpy


def font(ImageFont, *, size: int):
    for path in (
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
    ):
        try:
            if Path(path).is_file():
                return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def material(name: str, color: tuple[float, float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf is not None:
        try:
            bsdf.inputs["Base Color"].default_value = color
            bsdf.inputs["Alpha"].default_value = color[3]
            bsdf.inputs["Roughness"].default_value = 0.7
        except Exception:
            pass
    mat.blend_method = "BLEND" if color[3] < 1.0 else "OPAQUE"
    return mat


def emission_material(name: str, color: tuple[float, float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    mat.blend_method = "BLEND" if color[3] < 1.0 else "OPAQUE"
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    emission = nodes.new(type="ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = 1.0
    output = nodes.new(type="ShaderNodeOutputMaterial")
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return mat


def make_line_image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = bpy.data.images.new("監査用線画像", width=16, height=16, alpha=True)
    pixels = []
    for y in range(16):
        for x in range(16):
            if (x - 7.5) ** 2 + (y - 7.5) ** 2 < 28.0:
                pixels.extend((0.05, 0.05, 0.05, 1.0))
            elif x == y or x + y == 15:
                pixels.extend((0.85, 0.10, 0.10, 1.0))
            else:
                pixels.extend((1.0, 1.0, 1.0, 0.0))
    image.pixels = pixels
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()
    return path


def hide_existing_scene_objects() -> None:
    for obj in bpy.data.objects:
        obj.hide_render = True
        obj.hide_viewport = True


def render_to(path: Path, *, width_px: int, height_px: int, center_mm: tuple[float, float], scale_mm: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if "監査カメラ" in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects["監査カメラ"], do_unlink=True)
    camera_data = bpy.data.cameras.new("監査カメラ")
    camera = bpy.data.objects.new("監査カメラ", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = (center_mm[0] * 0.001, center_mm[1] * 0.001, 2.0)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = scale_mm * 0.001
    bpy.context.scene.camera = camera
    light_name = "監査ライト"
    if light_name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[light_name], do_unlink=True)
    light_data = bpy.data.lights.new(light_name, type="AREA")
    light = bpy.data.objects.new(light_name, light_data)
    bpy.context.collection.objects.link(light)
    light.location = (center_mm[0] * 0.001, center_mm[1] * 0.001, 2.6)
    light.rotation_euler = (0.0, 0.0, 0.0)
    light_data.energy = 900.0
    light_data.size = max(1.0, scale_mm * 0.001 * 1.8)
    bg_name = "監査背景"
    if bg_name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[bg_name], do_unlink=True)
    bg_mesh = bpy.data.meshes.new(f"{bg_name}_mesh")
    cx = center_mm[0] * 0.001
    cy = center_mm[1] * 0.001
    h = scale_mm * 0.001 * 1.02
    w = h * max(1.0, float(width_px) / max(1.0, float(height_px))) * 1.02
    z = -1.0
    bg_mesh.from_pydata(
        [
            (cx - w * 0.5, cy - h * 0.5, z),
            (cx + w * 0.5, cy - h * 0.5, z),
            (cx + w * 0.5, cy + h * 0.5, z),
            (cx - w * 0.5, cy + h * 0.5, z),
        ],
        [],
        [(0, 1, 2, 3)],
    )
    bg_mesh.update()
    bg = bpy.data.objects.new(bg_name, bg_mesh)
    bpy.context.collection.objects.link(bg)
    bg.data.materials.append(emission_material("監査背景_白", (0.97, 0.97, 0.94, 1.0)))

    scene = bpy.context.scene
    engine_items = {item.identifier for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engine_items else "BLENDER_EEVEE"
    if hasattr(scene, "eevee"):
        scene.eevee.taa_render_samples = 64
    scene.render.resolution_x = int(width_px)
    scene.render.resolution_y = int(height_px)
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    if scene.world:
        scene.world.color = (1.0, 1.0, 1.0)
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.render.filepath = str(path)
    bpy.context.view_layer.update()
    bpy.ops.render.render(write_still=True)
    if not path.exists() or path.stat().st_size <= 1000:
        raise AssertionError(f"画像を保存できません: {path}")


def label_sheet(
    source: Path,
    target: Path,
    *,
    title: str,
    cols: list[str],
    rows: list[str],
    results: list[dict],
    cell_px: int,
) -> Path:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return source

    src = Image.open(source).convert("RGB")
    left = 150
    top = 108
    right = 18
    bottom = 34
    sheet = Image.new("RGB", (left + src.width + right, top + src.height + bottom), "white")
    sheet.paste(src, (left, top))
    draw = ImageDraw.Draw(sheet)
    title_font = font(ImageFont, size=22)
    regular = font(ImageFont, size=12)
    small = font(ImageFont, size=10)
    ok_count = sum(1 for item in results if item.get("ok"))
    draw.text((20, 18), title, fill=(0, 0, 0), font=title_font)
    draw.text(
        (22, 52),
        f"OK {ok_count} / {len(results)}",
        fill=(0, 100, 0) if ok_count == len(results) else (180, 0, 0),
        font=regular,
    )
    for c, label in enumerate(cols):
        x = left + c * cell_px + 4
        draw.text((x, 82), label, fill=(0, 0, 0), font=small)
    for r, label in enumerate(rows):
        y = top + r * cell_px + cell_px // 2 - 7
        draw.text((16, y), label, fill=(0, 0, 0), font=small)
    for item in results:
        c = int(item["col"])
        r = int(item["row"])
        x0 = left + c * cell_px
        y0 = top + r * cell_px
        outline = (0, 150, 0) if item.get("ok") else (210, 0, 0)
        draw.rectangle((x0, y0, x0 + cell_px - 1, y0 + cell_px - 1), outline=outline, width=2)
        if not item.get("ok"):
            draw.text((x0 + 5, y0 + 5), "NG", fill=(210, 0, 0), font=regular)
    for c in range(len(cols) + 1):
        x = left + c * cell_px
        draw.line((x, top, x, top + src.height), fill=(210, 210, 210), width=1)
    for r in range(len(rows) + 1):
        y = top + r * cell_px
        draw.line((left, y, left + src.width, y), fill=(210, 210, 210), width=1)
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target)
    return target


def mesh_polygon_count(obj: bpy.types.Object | None) -> int:
    if obj is None:
        return 0
    if getattr(obj, "type", "") != "MESH":
        data = getattr(obj, "data", None)
        return len(getattr(data, "splines", []) or [])
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return len(mesh.polygons)
    finally:
        evaluated.to_mesh_clear()


def merge_bbox(
    base: tuple[float, float, float, float] | None,
    other: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float] | None:
    if other is None:
        return base
    if base is None:
        return other
    return (
        min(base[0], other[0]),
        min(base[1], other[1]),
        max(base[2], other[2]),
        max(base[3], other[3]),
    )


def object_world_bbox_mm(obj: bpy.types.Object | None) -> tuple[float, float, float, float] | None:
    if obj is None:
        return None
    points = []
    if getattr(obj, "type", "") == "MESH":
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            points = [obj.matrix_world @ vert.co for vert in mesh.vertices]
        finally:
            evaluated.to_mesh_clear()
    else:
        data = getattr(obj, "data", None)
        for spline in getattr(data, "splines", []) or []:
            if str(getattr(spline, "type", "") or "") == "BEZIER":
                points.extend(obj.matrix_world @ point.co for point in getattr(spline, "bezier_points", []) or [])
            else:
                points.extend(obj.matrix_world @ point.co.xyz for point in getattr(spline, "points", []) or [])
    if not points:
        return None
    xs = [float(point.x) * 1000.0 for point in points]
    ys = [float(point.y) * 1000.0 for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def owner_world_bbox_mm(owner_id: str, balloon_line_mesh) -> tuple[float, float, float, float] | None:
    bbox = None
    owner_prop = balloon_line_mesh.PROP_BALLOON_LINE_MESH_OWNER_ID
    for obj in bpy.data.objects:
        is_line_owner = str(obj.get(owner_prop, "") or "") == str(owner_id)
        is_fill_owner = str(obj.get("bmanga_balloon_fill_mesh_owner_id", "") or "") == str(owner_id)
        if is_line_owner or is_fill_owner:
            bbox = merge_bbox(bbox, object_world_bbox_mm(obj))
    for prefix in (
        "balloon_line_shape_",
        "balloon_line_image_",
        "balloon_tail_ellipse_line_",
        "balloon_tail_stroke_",
    ):
        bbox = merge_bbox(bbox, object_world_bbox_mm(bpy.data.objects.get(f"{prefix}{owner_id}")))
    return bbox


def force_render_visible(obj: bpy.types.Object | None) -> None:
    if obj is None:
        return
    obj.hide_render = False
    obj.hide_viewport = False
    for attr in ("visible_camera", "visible_diffuse", "visible_glossy", "visible_transmission", "visible_shadow"):
        try:
            setattr(obj, attr, True)
        except Exception:
            pass
    try:
        if obj.name not in bpy.context.scene.collection.objects.keys():
            bpy.context.scene.collection.objects.link(obj)
    except Exception:
        pass


def force_owner_objects_visible(owner_id: str, balloon_line_mesh) -> None:
    owner_prop = balloon_line_mesh.PROP_BALLOON_LINE_MESH_OWNER_ID
    for obj in bpy.data.objects:
        is_line_owner = str(obj.get(owner_prop, "") or "") == str(owner_id)
        is_fill_owner = str(obj.get("bmanga_balloon_fill_mesh_owner_id", "") or "") == str(owner_id)
        if is_line_owner or is_fill_owner:
            force_render_visible(obj)
    for prefix in (
        "balloon_line_shape_",
        "balloon_line_image_",
        "balloon_tail_ellipse_line_",
        "balloon_tail_stroke_",
    ):
        force_render_visible(bpy.data.objects.get(f"{prefix}{owner_id}"))


def replace_object_materials(obj: bpy.types.Object | None, mat: bpy.types.Material) -> None:
    if obj is None or getattr(obj, "data", None) is None:
        return
    try:
        obj.data.materials.clear()
    except Exception:
        while len(getattr(obj.data, "materials", []) or []) > 0:
            obj.data.materials.pop(index=len(obj.data.materials) - 1)
    obj.data.materials.append(mat)
    for poly in getattr(obj.data, "polygons", []) or []:
        try:
            poly.material_index = 0
        except Exception:
            pass
    for spline in getattr(obj.data, "splines", []) or []:
        try:
            spline.material_index = 0
        except Exception:
            pass


def audit_material_for_kind(kind: str) -> bpy.types.Material:
    colors = {
        "balloon_fill_mesh": (0.82, 0.82, 0.82, 0.28),
        "balloon_outer_edge_mesh": (1.0, 0.62, 0.16, 1.0),
        "balloon_inner_edge_mesh": (0.10, 0.45, 1.0, 1.0),
        "balloon_multi_line_mesh": (0.0, 0.0, 0.0, 1.0),
        "balloon_line_shape_mesh": (0.0, 0.0, 0.0, 1.0),
        "balloon_line_image_mesh": (0.0, 0.0, 0.0, 1.0),
        "balloon_tail_ellipse_fill_mesh": (0.82, 0.82, 0.82, 0.28),
        "balloon_tail_ellipse_line_mesh": (0.0, 0.0, 0.0, 1.0),
        "balloon_tail_stroke": (0.0, 0.0, 0.0, 1.0),
        "balloon_tail_main_line_mesh": (0.0, 0.0, 0.0, 1.0),
        "balloon_flash_effect_line_mesh": (0.0, 0.0, 0.0, 1.0),
        "balloon_flash_white_line_mesh": (0.0, 0.0, 0.0, 1.0),
    }
    return emission_material(f"監査表示_{kind}", colors.get(kind, (0.0, 0.0, 0.0, 1.0)))


def apply_audit_display_materials(owner_id: str, body_object: bpy.types.Object | None, balloon_line_mesh) -> None:
    owner_prop = balloon_line_mesh.PROP_BALLOON_LINE_MESH_OWNER_ID
    kind_prop = balloon_line_mesh.PROP_BALLOON_LINE_MESH_KIND
    if body_object is not None:
        replace_object_materials(body_object, audit_material_for_kind("balloon_line_mesh"))
    for obj in bpy.data.objects:
        is_line_owner = str(obj.get(owner_prop, "") or "") == str(owner_id)
        is_fill_owner = str(obj.get("bmanga_balloon_fill_mesh_owner_id", "") or "") == str(owner_id)
        if not is_line_owner and not is_fill_owner:
            continue
        kind = str(obj.get(kind_prop, "") or obj.get("bmanga_balloon_fill_mesh_kind", "") or obj.name)
        replace_object_materials(obj, audit_material_for_kind(kind))
    for prefix, kind in (
        ("balloon_line_shape_", "balloon_line_shape_mesh"),
        ("balloon_line_image_", "balloon_line_image_mesh"),
        ("balloon_tail_ellipse_line_", "balloon_tail_ellipse_line_mesh"),
        ("balloon_tail_stroke_", "balloon_tail_stroke"),
    ):
        replace_object_materials(bpy.data.objects.get(f"{prefix}{owner_id}"), audit_material_for_kind(kind))


def owner_display_objects(owner_id: str, body_object: bpy.types.Object | None, balloon_line_mesh):
    seen: set[str] = set()
    owner_prop = balloon_line_mesh.PROP_BALLOON_LINE_MESH_OWNER_ID
    kind_prop = balloon_line_mesh.PROP_BALLOON_LINE_MESH_KIND

    def add(obj: bpy.types.Object | None, kind: str):
        if obj is None or obj.name in seen:
            return
        seen.add(obj.name)
        yield obj, kind

    for obj in bpy.data.objects:
        is_line_owner = str(obj.get(owner_prop, "") or "") == str(owner_id)
        is_fill_owner = str(obj.get("bmanga_balloon_fill_mesh_owner_id", "") or "") == str(owner_id)
        if not is_line_owner and not is_fill_owner:
            continue
        kind = str(obj.get(kind_prop, "") or obj.get("bmanga_balloon_fill_mesh_kind", "") or obj.name)
        yield from add(obj, kind)
    for prefix, kind in (
        ("balloon_line_shape_", "balloon_line_shape_mesh"),
        ("balloon_line_image_", "balloon_line_image_mesh"),
        ("balloon_tail_ellipse_line_", "balloon_tail_ellipse_line_mesh"),
        ("balloon_tail_stroke_", "balloon_tail_stroke"),
    ):
        yield from add(bpy.data.objects.get(f"{prefix}{owner_id}"), kind)
    yield from add(body_object, "balloon_body_curve")


def hide_owner_sources(owner_id: str, body_object: bpy.types.Object | None, balloon_line_mesh) -> None:
    for obj, _kind in owner_display_objects(owner_id, body_object, balloon_line_mesh):
        obj.hide_render = True
        obj.hide_viewport = True


def clone_material_for_kind(kind: str) -> bpy.types.Material:
    colors = {
        "balloon_fill_mesh": (0.82, 0.82, 0.82, 1.0),
        "balloon_tail_ellipse_fill_mesh": (0.82, 0.82, 0.82, 1.0),
        "balloon_outer_edge_mesh": (1.0, 0.58, 0.12, 1.0),
        "balloon_inner_edge_mesh": (0.08, 0.42, 1.0, 1.0),
        "balloon_body_curve": (0.0, 0.0, 0.0, 1.0),
    }
    return emission_material(f"監査複製_{kind}", colors.get(kind, (0.0, 0.0, 0.0, 1.0)))


def clone_z_for_kind(kind: str, rank: int) -> float:
    z_base = 0.12 + float(rank) * 0.00002
    if kind in {"balloon_fill_mesh", "balloon_tail_ellipse_fill_mesh"}:
        return z_base
    if kind == "balloon_outer_edge_mesh":
        return z_base + 0.003
    if kind == "balloon_inner_edge_mesh":
        return z_base + 0.006
    return z_base + 0.010


def clone_object_geometry_for_sheet(
    obj: bpy.types.Object,
    *,
    kind: str,
    owner_id: str,
    target_center_mm: tuple[float, float],
    source_bbox_mm: tuple[float, float, float, float],
    rank: int,
) -> bpy.types.Object | None:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    try:
        source_mesh = evaluated.to_mesh()
    except Exception:
        return None
    try:
        if source_mesh is None or not getattr(source_mesh, "vertices", None) or not getattr(source_mesh, "polygons", None):
            return None
        faces = [tuple(int(index) for index in poly.vertices) for poly in source_mesh.polygons]
        if not faces:
            return None
        src_cx = (float(source_bbox_mm[0]) + float(source_bbox_mm[2])) * 0.5
        src_cy = (float(source_bbox_mm[1]) + float(source_bbox_mm[3])) * 0.5
        dst_cx, dst_cy = target_center_mm
        z = clone_z_for_kind(kind, rank)
        verts = []
        for vert in source_mesh.vertices:
            world = obj.matrix_world @ vert.co
            x_mm = float(world.x) * 1000.0 - src_cx + dst_cx
            y_mm = float(world.y) * 1000.0 - src_cy + dst_cy
            verts.append((x_mm * 0.001, y_mm * 0.001, z))
        mesh = bpy.data.meshes.new(f"監査複製_{owner_id}_{kind}_mesh")
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        mesh.materials.append(clone_material_for_kind(kind))
        for poly in mesh.polygons:
            poly.material_index = 0
        clone = bpy.data.objects.new(f"監査複製_{owner_id}_{kind}", mesh)
        bpy.context.scene.collection.objects.link(clone)
        force_render_visible(clone)
        return clone
    finally:
        try:
            evaluated.to_mesh_clear()
        except Exception:
            pass


def clone_owner_visuals_for_sheet(
    owner_id: str,
    body_object: bpy.types.Object | None,
    balloon_line_mesh,
    *,
    target_center_mm: tuple[float, float],
    source_bbox_mm: tuple[float, float, float, float] | None,
    rank: int,
) -> None:
    if source_bbox_mm is None:
        hide_owner_sources(owner_id, body_object, balloon_line_mesh)
        return
    for obj, kind in owner_display_objects(owner_id, body_object, balloon_line_mesh):
        clone_object_geometry_for_sheet(
            obj,
            kind=kind,
            owner_id=owner_id,
            target_center_mm=target_center_mm,
            source_bbox_mm=source_bbox_mm,
            rank=rank,
        )
    hide_owner_sources(owner_id, body_object, balloon_line_mesh)


def clear_audit_clones() -> None:
    for obj in list(bpy.data.objects):
        if obj.name.startswith("監査複製_"):
            bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.name.startswith("監査複製_") and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
