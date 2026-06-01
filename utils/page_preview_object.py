"""Lightweight page preview images for page edit files."""

from __future__ import annotations

from pathlib import Path

import bpy

from . import log, object_naming as on, page_grid, page_range, paths
from .geom import mm_to_m

_logger = log.get_logger(__name__)

PREVIEW_KIND = "page_preview"
PREVIEW_COLLECTION_NAME = "ページ一覧プレビュー"
PREVIEW_IMAGE_PREFIX = "BName_PagePreview_"
PREVIEW_MESH_PREFIX = "page_preview_mesh_"
PREVIEW_OBJECT_PREFIX = "page_preview_"
PREVIEW_MATERIAL_PREFIX = "BName_PagePreview_"
PREVIEW_PAGE_ID_PROP = "bname_page_preview_page_id"
PREVIEW_SCALE = 0.25
PREVIEW_MAX_PX = 384
PREVIEW_Z_M = 0.25
PREVIEW_FILENAME = "page_preview.png"


def preview_enabled(scene=None) -> bool:
    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None:
        return False
    return bool(getattr(scene, "bname_page_preview_enabled", True))


def _is_page_edit_scene(scene) -> tuple[bool, str]:
    try:
        from . import page_file_scene

        role, page_id, _coma_id = page_file_scene.current_role(bpy.context)
        if role == page_file_scene.ROLE_PAGE and paths.is_valid_page_id(page_id):
            return True, page_id
        page_id = page_file_scene.current_page_id(scene)
        return bool(page_id and page_file_scene.is_page_edit_scene(scene)), page_id
    except Exception:  # noqa: BLE001
        return False, ""


def _preview_collection(scene: bpy.types.Scene) -> bpy.types.Collection:
    coll = bpy.data.collections.get(PREVIEW_COLLECTION_NAME)
    if coll is None:
        coll = bpy.data.collections.new(PREVIEW_COLLECTION_NAME)
    if not any(child is coll for child in scene.collection.children):
        try:
            scene.collection.children.link(coll)
        except Exception:  # noqa: BLE001
            pass
    coll.hide_render = True
    coll[on.PROP_KIND] = PREVIEW_KIND
    coll[on.PROP_MANAGED] = False
    coll[on.PROP_NO_NORMALIZE] = True
    return coll


def _iter_preview_objects():
    for obj in list(bpy.data.objects):
        if str(obj.get(on.PROP_KIND, "") or "") == PREVIEW_KIND:
            yield obj


def hide_page_previews(scene=None) -> None:
    for obj in _iter_preview_objects():
        obj.hide_viewport = True
        obj.hide_render = True


def remove_page_previews() -> int:
    removed = 0
    for obj in list(_iter_preview_objects()):
        data = getattr(obj, "data", None)
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
        except Exception:  # noqa: BLE001
            continue
        if data is not None and getattr(data, "users", 0) == 0:
            try:
                bpy.data.meshes.remove(data)
            except Exception:  # noqa: BLE001
                pass
    return removed


def _linear_to_srgb(value: float) -> float:
    v = max(0.0, min(1.0, float(value)))
    if v <= 0.0031308:
        return v * 12.92
    return 1.055 * (v ** (1.0 / 2.4)) - 0.055


def _rgba255(rgba, fallback=(255, 255, 255, 255)) -> tuple[int, int, int, int]:
    try:
        r, g, b, a = rgba[:4]
        return (
            int(round(_linear_to_srgb(r) * 255.0)),
            int(round(_linear_to_srgb(g) * 255.0)),
            int(round(_linear_to_srgb(b) * 255.0)),
            int(round(max(0.0, min(1.0, float(a))) * 255.0)),
        )
    except Exception:  # noqa: BLE001
        return fallback


def _image_size(work) -> tuple[int, int]:
    cw = max(1.0, float(getattr(work.paper, "canvas_width_mm", 1.0) or 1.0))
    ch = max(1.0, float(getattr(work.paper, "canvas_height_mm", 1.0) or 1.0))
    if cw >= ch:
        width = PREVIEW_MAX_PX
        height = max(1, int(round(PREVIEW_MAX_PX * ch / cw)))
    else:
        height = PREVIEW_MAX_PX
        width = max(1, int(round(PREVIEW_MAX_PX * cw / ch)))
    return width, height


def _page_number(work, page_index: int) -> str:
    info = getattr(work, "work_info", None)
    start = int(getattr(info, "page_number_start", 1) or 1) if info is not None else 1
    return f"{start + int(page_index):03d}"


def _coma_polygon_mm(coma) -> list[tuple[float, float]]:
    shape = str(getattr(coma, "shape_type", "rect") or "rect")
    if shape == "rect":
        x = float(getattr(coma, "rect_x_mm", 0.0) or 0.0)
        y = float(getattr(coma, "rect_y_mm", 0.0) or 0.0)
        w = max(0.1, float(getattr(coma, "rect_width_mm", 0.1) or 0.1))
        h = max(0.1, float(getattr(coma, "rect_height_mm", 0.1) or 0.1))
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    vertices = list(getattr(coma, "vertices", []) or [])
    if len(vertices) >= 3:
        return [(float(v.x_mm), float(v.y_mm)) for v in vertices]
    return []


def _bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _preview_png_path(work, page_id: str) -> Path | None:
    work_dir_text = str(getattr(work, "work_dir", "") or "")
    if not work_dir_text:
        return None
    work_dir = Path(work_dir_text)
    return work_dir / page_id / PREVIEW_FILENAME


def _draw_coma_thumb(draw, image, work, page, coma, points_px, bbox_px) -> None:
    try:
        from PIL import Image
        from . import coma_preview

        src = coma_preview.coma_preview_source_path(Path(work.work_dir), page.id, coma)
        if src is None or not Path(src).is_file():
            return
        x0, y0, x1, y1 = bbox_px
        width = max(1, int(round(x1 - x0)))
        height = max(1, int(round(y1 - y0)))
        thumb = Image.open(src).convert("RGBA").resize((width, height))
        mask_draw = Image.new("L", (width, height), 0)
        local_points = [(int(round(x - x0)), int(round(y - y0))) for x, y in points_px]
        from PIL import ImageDraw

        ImageDraw.Draw(mask_draw).polygon(local_points, fill=255)
        image.paste(thumb, (int(round(x0)), int(round(y0))), mask_draw)
    except Exception:  # noqa: BLE001
        return


def _render_preview_image(work, page, page_index: int, *, current: bool):
    from PIL import Image, ImageDraw

    width, height = _image_size(work)
    cw = max(1.0, float(getattr(work.paper, "canvas_width_mm", 1.0) or 1.0))
    ch = max(1.0, float(getattr(work.paper, "canvas_height_mm", 1.0) or 1.0))
    img = Image.new("RGBA", (width, height), (250, 250, 250, 255))
    draw = ImageDraw.Draw(img)

    def point_px(pt: tuple[float, float]) -> tuple[float, float]:
        x, y = pt
        return (x / cw * width, height - (y / ch * height))

    paper_color = _rgba255(getattr(work.paper, "paper_color", (1, 1, 1, 1)))
    draw.rectangle((0, 0, width - 1, height - 1), fill=paper_color)

    for coma in getattr(page, "comas", []) or []:
        if not bool(getattr(coma, "visible", True)):
            continue
        pts = _coma_polygon_mm(coma)
        if len(pts) < 3:
            continue
        pts_px = [point_px(p) for p in pts]
        fill = _rgba255(getattr(coma, "background_color", (1, 1, 1, 1)))
        if not bool(getattr(coma, "paper_visible", True)):
            fill = (255, 255, 255, 0)
        draw.polygon(pts_px, fill=fill)
        bbox = _bbox(pts_px)
        if bbox is not None:
            _draw_coma_thumb(draw, img, work, page, coma, pts_px, bbox)
        border = getattr(coma, "border", None)
        border_color = _rgba255(getattr(border, "color", (0, 0, 0, 1)), (0, 0, 0, 255))
        line_w_mm = max(0.2, float(getattr(border, "width_mm", 0.5) or 0.5))
        px_per_mm = max(width / cw, height / ch)
        line_w = max(1, int(round(line_w_mm * px_per_mm)))
        closed = pts_px + [pts_px[0]]
        draw.line(closed, fill=border_color, width=line_w, joint="curve")

    outline = (72, 190, 222, 255)
    if current:
        outline = (64, 140, 255, 255)
    draw.rectangle((0, 0, width - 1, height - 1), outline=outline, width=3 if current else 2)
    draw.text((8, 6), _page_number(work, page_index), fill=(40, 40, 40, 255))
    return img


def ensure_preview_png(work, page, page_index: int, *, current: bool) -> Path | None:
    page_id = str(getattr(page, "id", "") or "")
    if not paths.is_valid_page_id(page_id):
        return None
    path = _preview_png_path(work, page_id)
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _render_preview_image(work, page, page_index, current=current).save(path)
        return path
    except Exception:  # noqa: BLE001
        _logger.exception("page preview render failed: %s", page_id)
        return None


def _load_image(path: Path) -> bpy.types.Image | None:
    try:
        abspath = str(path.resolve())
        mtime = path.stat().st_mtime
    except OSError:
        return None
    img = None
    for candidate in bpy.data.images:
        try:
            if str(Path(bpy.path.abspath(candidate.filepath)).resolve()) == abspath:
                img = candidate
                break
        except Exception:  # noqa: BLE001
            continue
    if img is None:
        try:
            img = bpy.data.images.load(abspath, check_existing=True)
        except Exception:  # noqa: BLE001
            return None
    else:
        try:
            if float(img.get("_bname_page_preview_mtime", -1.0)) != mtime:
                img.reload()
        except Exception:  # noqa: BLE001
            pass
    img.name = f"{PREVIEW_IMAGE_PREFIX}{path.parent.name}"
    img["_bname_page_preview_mtime"] = mtime
    try:
        img.colorspace_settings.name = "sRGB"
    except Exception:  # noqa: BLE001
        pass
    return img


def _ensure_material(page_id: str, image: bpy.types.Image | None) -> bpy.types.Material:
    mat = bpy.data.materials.get(f"{PREVIEW_MATERIAL_PREFIX}{page_id}")
    if mat is None:
        mat = bpy.data.materials.new(f"{PREVIEW_MATERIAL_PREFIX}{page_id}")
    mat.use_nodes = True
    try:
        mat.blend_method = "OPAQUE"
        mat.show_transparent_back = False
    except Exception:  # noqa: BLE001
        pass
    nt = mat.node_tree
    for node in list(nt.nodes):
        nt.nodes.remove(node)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    try:
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    except Exception:  # noqa: BLE001
        _logger.exception("page preview material link failed")
    mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    return mat


def _ensure_plane_mesh(page_id: str, width_mm: float, height_mm: float) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.get(f"{PREVIEW_MESH_PREFIX}{page_id}")
    if mesh is None:
        mesh = bpy.data.meshes.new(f"{PREVIEW_MESH_PREFIX}{page_id}")
    hw = mm_to_m(width_mm) * 0.5
    hh = mm_to_m(height_mm) * 0.5
    mesh.clear_geometry()
    mesh.from_pydata(
        [(-hw, -hh, 0.0), (hw, -hh, 0.0), (hw, hh, 0.0), (-hw, hh, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    uv = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
    for loop_index, uv_value in zip(
        mesh.polygons[0].loop_indices,
        ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)),
        strict=False,
    ):
        uv.data[loop_index].uv = uv_value
    return mesh


def preview_rects_mm(scene, work) -> dict[str, tuple[int, float, float, float, float]]:
    if scene is None or work is None or not getattr(work, "loaded", False):
        return {}
    cw = max(1.0, float(getattr(work.paper, "canvas_width_mm", 1.0) or 1.0))
    ch = max(1.0, float(getattr(work.paper, "canvas_height_mm", 1.0) or 1.0))
    thumb_w = cw * PREVIEW_SCALE
    thumb_h = ch * PREVIEW_SCALE
    cols = max(1, int(getattr(scene, "bname_overview_cols", 4) or 4))
    gap = max(6.0, float(getattr(scene, "bname_overview_gap_mm", 30.0) or 30.0) * PREVIEW_SCALE)
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")
    offsets: list[tuple[int, str, float, float]] = []
    for i, page in enumerate(getattr(work, "pages", []) or []):
        page_id = str(getattr(page, "id", "") or "")
        if not page_id or not page_range.page_in_range(page):
            continue
        ox, oy = page_grid.page_grid_offset_mm(
            i,
            cols,
            gap,
            thumb_w,
            thumb_h,
            start_side,
            read_direction,
        )
        offsets.append((i, page_id, ox, oy))
    if not offsets:
        return {}
    min_x = min(ox for _i, _pid, ox, _oy in offsets)
    origin_x = cw + max(20.0, gap * 2.0)
    origin_y = ch - thumb_h
    rects: dict[str, tuple[int, float, float, float, float]] = {}
    for i, page_id, ox, oy in offsets:
        x0 = origin_x + (ox - min_x)
        y0 = origin_y + oy
        rects[page_id] = (i, x0, y0, x0 + thumb_w, y0 + thumb_h)
    return rects


def page_index_at_world_mm(scene, work, x_mm: float, y_mm: float) -> int | None:
    if not preview_enabled(scene):
        return None
    for _page_id, (index, x0, y0, x1, y1) in preview_rects_mm(scene, work).items():
        if x0 <= x_mm <= x1 and y0 <= y_mm <= y1:
            return index
    return None


def _ensure_preview_object(scene, work, page, page_index: int, rect, *, current: bool) -> None:
    page_id = str(getattr(page, "id", "") or "")
    path = ensure_preview_png(work, page, page_index, current=current)
    image = _load_image(path) if path is not None else None
    _index, x0, y0, x1, y1 = rect
    mesh = _ensure_plane_mesh(page_id, x1 - x0, y1 - y0)
    mat = _ensure_material(page_id, image)
    if not mesh.materials:
        mesh.materials.append(mat)
    elif mesh.materials[0] is not mat:
        mesh.materials[0] = mat
    obj = bpy.data.objects.get(f"{PREVIEW_OBJECT_PREFIX}{page_id}")
    if obj is None:
        obj = bpy.data.objects.new(f"{PREVIEW_OBJECT_PREFIX}{page_id}", mesh)
    elif obj.data is not mesh:
        obj.data = mesh
    obj.location.x = mm_to_m((x0 + x1) * 0.5)
    obj.location.y = mm_to_m((y0 + y1) * 0.5)
    obj.location.z = PREVIEW_Z_M
    obj.hide_viewport = False
    obj.hide_render = True
    obj.hide_select = True
    obj.show_name = False
    obj[on.PROP_KIND] = PREVIEW_KIND
    obj[on.PROP_ID] = page_id
    obj[PREVIEW_PAGE_ID_PROP] = page_id
    obj[on.PROP_MANAGED] = False
    obj[on.PROP_NO_NORMALIZE] = True
    coll = _preview_collection(scene)
    if not any(o is obj for o in coll.objects):
        try:
            coll.objects.link(obj)
        except RuntimeError:
            pass
    for users_coll in list(getattr(obj, "users_collection", ()) or ()):
        if users_coll is coll:
            continue
        try:
            users_coll.objects.unlink(obj)
        except Exception:  # noqa: BLE001
            pass


def sync_page_previews(context=None, work=None) -> int:
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is None:
        return 0
    if work is None:
        work = getattr(scene, "bname_work", None)
    is_page_scene, current_page_id = _is_page_edit_scene(scene)
    if not is_page_scene or not preview_enabled(scene):
        hide_page_previews(scene)
        return 0
    if work is None or not getattr(work, "loaded", False):
        hide_page_previews(scene)
        return 0
    rects = preview_rects_mm(scene, work)
    valid_page_ids = set(rects)
    for obj in _iter_preview_objects():
        page_id = str(obj.get(PREVIEW_PAGE_ID_PROP, "") or "")
        if page_id not in valid_page_ids:
            obj.hide_viewport = True
            obj.hide_render = True
    updated = 0
    for page in getattr(work, "pages", []) or []:
        page_id = str(getattr(page, "id", "") or "")
        rect = rects.get(page_id)
        if rect is None:
            continue
        _ensure_preview_object(
            scene,
            work,
            page,
            int(rect[0]),
            rect,
            current=page_id == current_page_id,
        )
        updated += 1
    try:
        for area in getattr(context, "screen", None).areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    except Exception:  # noqa: BLE001
        pass
    return updated
