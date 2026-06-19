"""Extended B-MANGA asset payload helpers."""

from __future__ import annotations

import base64
from pathlib import Path

import bpy

from ..core.work import get_work
from ..io import schema
from . import gp_layer_parenting as gp_parent
from . import gpencil as gp_utils
from . import layer_stack as layer_stack_utils
from . import object_naming as on
from . import page_grid
from .geom import m_to_mm, mm_to_m
from .layer_hierarchy import coma_stack_key, page_stack_key, split_child_key


EXTENDED_LAYER_KINDS = {"coma", "raster", "gp"}


def expand_asset_uids(context, stack, uids: list[str]) -> list[str]:
    """Add direct children when a panel is registered as an asset."""

    out = list(dict.fromkeys(uid for uid in uids if uid))
    selected_coma_keys = {
        str(getattr(item, "key", "") or "")
        for item in stack
        if layer_stack_utils.stack_item_uid(item) in out
        and str(getattr(item, "kind", "") or "") == "coma"
    }
    if not selected_coma_keys:
        return out
    for item in stack:
        if str(getattr(item, "parent_key", "") or "") not in selected_coma_keys:
            continue
        if str(getattr(item, "kind", "") or "") not in {
            "balloon",
            "text",
            "effect",
            "raster",
            "gp",
        }:
            continue
        uid = layer_stack_utils.stack_item_uid(item)
        if uid and uid not in out:
            out.append(uid)
    return out


def serialize_stack_item(context, item) -> dict | None:
    kind = str(getattr(item, "kind", "") or "")
    if kind == "coma":
        return _serialize_coma(context, item)
    if kind == "raster":
        return _serialize_raster(context, item)
    if kind == "gp":
        return _serialize_gp_layer(context, item)
    return None


def preview_objects_for_entry(entry: dict) -> list[bpy.types.Object]:
    kind = str(entry.get("kind", "") or "")
    source_id = str(entry.get("source_id", "") or "")
    if kind == "raster":
        obj = on.find_object_by_bmanga_id(source_id, kind="raster")
        return [obj] if obj is not None else []
    if kind != "coma":
        return []
    owner = str(entry.get("source_parent_key", "") or "")
    objects: list[bpy.types.Object] = []
    try:
        from . import coma_border_object, coma_plane

        for obj in bpy.data.objects:
            if str(obj.get(coma_plane.PROP_COMA_PLANE_OWNER_ID, "") or "") == owner:
                objects.append(obj)
            elif str(obj.get(coma_border_object.PROP_COMA_BORDER_OWNER_ID, "") or "") == owner:
                objects.append(obj)
    except Exception:  # noqa: BLE001
        return objects
    return objects


def instantiate_coma(context, page, entry: dict, dx: float, dy: float):
    from ..io import coma_io, page_io

    work = get_work(context)
    if work is None or page is None or not getattr(work, "work_dir", ""):
        return None
    data = dict(entry.get("data") or {})
    panel = page.comas.add()
    schema.coma_entry_from_dict(panel, data)
    stem = coma_io.allocate_new_coma_id(Path(work.work_dir), page.id)
    panel.coma_id = stem
    panel.id = stem
    _offset_coma_geometry(panel, dx, dy)
    panel.z_order = max((int(getattr(c, "z_order", 0)) for c in page.comas if c is not panel), default=-1) + 1
    page.active_coma_index = len(page.comas) - 1
    page.coma_count = len(page.comas)
    try:
        from . import coma_border_object, coma_plane, page_file_scene

        if page_file_scene.is_current_page_edit_scene(context.scene, getattr(page, "id", "")):
            coma_plane.ensure_coma_plane(context.scene, work, page, panel)
            coma_border_object.ensure_coma_border_object(context.scene, work, page, panel)
    except Exception:  # noqa: BLE001
        pass
    try:
        work_dir = Path(work.work_dir)
        coma_io.save_coma_meta(work_dir, page.id, panel)
        page_io.save_page_json(work_dir, page)
        page_io.save_pages_json(work_dir, work)
    except Exception:  # noqa: BLE001
        pass
    return panel


def instantiate_raster(context, page, entry: dict, parent_kind: str, parent_key: str):
    from ..operators import raster_layer_op

    work = get_work(context)
    coll = getattr(getattr(context, "scene", None), "bmanga_raster_layers", None)
    if work is None or coll is None or not getattr(work, "work_dir", ""):
        return None
    data = dict(entry.get("data") or {})
    raster = coll.add()
    schema.raster_layer_from_dict(raster, data, opacity_percent=True)
    raster_id = raster_layer_op._allocate_raster_id(context.scene, Path(work.work_dir))
    raster.id = raster_id
    raster.image_name = raster_layer_op.raster_image_name(raster_id)
    raster.filepath_rel = raster_layer_op.raster_filepath_rel(raster_id)
    _set_entry_parent(raster, parent_kind, parent_key)
    png_b64 = str(entry.get("png_base64", "") or "")
    if png_b64:
        try:
            path = Path(work.work_dir) / raster.filepath_rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(png_b64.encode("ascii")))
        except Exception:  # noqa: BLE001
            pass
    image = raster_layer_op.ensure_raster_image(context, raster, create_missing=True)
    if image is not None and not png_b64:
        raster_layer_op.save_raster_png(context, raster, force=True)
    raster_layer_op.ensure_raster_plane(context, raster)
    context.scene.bmanga_active_raster_layer_index = len(coll) - 1
    context.scene.bmanga_active_layer_kind = "raster"
    return raster


def instantiate_gp_layer(
    context,
    page,
    entry: dict,
    dx: float,
    dy: float,
    parent_kind: str,
    parent_key: str,
):
    obj = gp_utils.ensure_master_gpencil(context.scene)
    layers = getattr(getattr(obj, "data", None), "layers", None)
    if layers is None:
        return None
    name = _unique_gp_layer_name(layers, str(entry.get("title", "") or "レイヤー"))
    layer = layers.new(name)
    gp_parent.set_parent_key(layer, "" if parent_kind == "none" else parent_key)
    _apply_gp_material(obj, layer, entry.get("material") if isinstance(entry.get("material"), dict) else {})
    dst_ox, dst_oy = _page_offset(context, page)
    for frame_data in entry.get("frames", []) or []:
        if not isinstance(frame_data, dict):
            continue
        frame_number = int(frame_data.get("frame", getattr(context.scene, "frame_current", 1)) or 1)
        frame = gp_utils.ensure_active_frame(layer, frame_number=frame_number)
        drawing = getattr(frame, "drawing", None) if frame is not None else None
        if drawing is None:
            continue
        for stroke_data in frame_data.get("strokes", []) or []:
            if not isinstance(stroke_data, dict):
                continue
            points = []
            radii = []
            opacities = []
            for point in stroke_data.get("points", []) or []:
                if not isinstance(point, dict):
                    continue
                x = float(point.get("x", 0.0) or 0.0) + dx + dst_ox
                y = float(point.get("y", 0.0) or 0.0) + dy + dst_oy
                z = float(point.get("z", 0.0) or 0.0)
                points.append((mm_to_m(x), mm_to_m(y), z))
                radii.append(float(point.get("radius", 0.01) or 0.01))
                opacities.append(float(point.get("opacity", 1.0) or 1.0))
            if points:
                gp_utils.add_stroke_to_drawing(
                    drawing,
                    points,
                    radii=radii,
                    opacities=opacities,
                    cyclic=bool(stroke_data.get("cyclic", False)),
                )
    try:
        obj.data.layers.active = layer
    except Exception:  # noqa: BLE001
        pass
    context.scene.bmanga_active_layer_kind = "gp"
    return obj, layer


def source_parent_key(entry: dict) -> str:
    data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
    return str(entry.get("source_parent_key", "") or data.get("parent_key", "") or data.get("parentKey", "") or "")


def new_uid_for_created(kind: str, page, obj) -> str:
    if kind == "coma":
        return layer_stack_utils.target_uid("coma", coma_stack_key(page, obj))
    if kind == "raster":
        return layer_stack_utils.target_uid("raster", getattr(obj, "id", ""))
    if kind == "gp" and isinstance(obj, tuple):
        return layer_stack_utils.target_uid("gp", layer_stack_utils._node_stack_key(obj[1]))
    return ""


def _serialize_coma(context, item) -> dict | None:
    resolved = layer_stack_utils.resolve_stack_item(context, item)
    panel = resolved.get("target") if resolved is not None else None
    page = resolved.get("page") if resolved is not None else None
    if panel is None or page is None:
        return None
    parent_key = coma_stack_key(page, panel)
    return {
        "kind": "coma",
        "source_id": str(getattr(panel, "coma_id", "") or getattr(panel, "id", "") or ""),
        "source_parent_key": parent_key,
        "data": schema.coma_entry_to_dict(panel),
        "bounds": _coma_bounds(panel),
    }


def _serialize_raster(context, item) -> dict | None:
    from ..operators import raster_layer_op

    resolved = layer_stack_utils.resolve_stack_item(context, item)
    raster = resolved.get("target") if resolved is not None else None
    if raster is None:
        return None
    try:
        raster_layer_op.save_raster_png(context, raster, force=True)
    except Exception:  # noqa: BLE001
        pass
    png_b64 = ""
    try:
        work = get_work(context)
        rel = str(getattr(raster, "filepath_rel", "") or raster_layer_op.raster_filepath_rel(raster.id))
        path = Path(work.work_dir) / rel if work is not None else None
        if path is not None and path.is_file():
            png_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    except Exception:  # noqa: BLE001
        png_b64 = ""
    return {
        "kind": "raster",
        "source_id": str(getattr(raster, "id", "") or ""),
        "source_parent_key": str(getattr(raster, "parent_key", "") or ""),
        "data": schema.raster_layer_to_dict(raster),
        "bounds": _raster_bounds(context),
        "png_base64": png_b64,
    }


def _serialize_gp_layer(context, item) -> dict | None:
    resolved = layer_stack_utils.resolve_stack_item(context, item)
    obj = resolved.get("object") if resolved is not None else None
    layer = resolved.get("target") if resolved is not None else None
    if obj is None or layer is None:
        return None
    parent_key = gp_parent.parent_key(layer) or str(getattr(item, "parent_key", "") or "")
    source_page = _page_for_parent_key(context, parent_key)
    source_ox, source_oy = _page_offset(context, source_page)
    frames, bounds = _serialize_gp_frames(layer, source_ox, source_oy)
    return {
        "kind": "gp",
        "source_id": layer_stack_utils._node_stack_key(layer),
        "source_parent_key": parent_key,
        "title": str(getattr(layer, "name", "") or "レイヤー"),
        "bounds": bounds,
        "frames": frames,
        "material": _gp_material_payload(obj, layer),
    }


def _serialize_gp_frames(layer, source_ox: float, source_oy: float):
    frames = []
    bounds_points: list[tuple[float, float]] = []
    for frame in getattr(layer, "frames", []) or []:
        drawing = getattr(frame, "drawing", None)
        strokes = getattr(drawing, "strokes", None) if drawing is not None else None
        if strokes is None:
            continue
        frame_payload = {"frame": int(getattr(frame, "frame_number", 1) or 1), "strokes": []}
        for stroke in strokes:
            stroke_payload = {
                "cyclic": bool(getattr(stroke, "cyclic", False)),
                "points": [],
            }
            for point in getattr(stroke, "points", []) or []:
                pos = getattr(point, "position", None)
                if pos is None:
                    continue
                x = m_to_mm(float(pos[0])) - source_ox
                y = m_to_mm(float(pos[1])) - source_oy
                bounds_points.append((x, y))
                stroke_payload["points"].append(
                    {
                        "x": x,
                        "y": y,
                        "z": float(pos[2]),
                        "radius": float(getattr(point, "radius", 0.01) or 0.01),
                        "opacity": float(getattr(point, "opacity", 1.0) or 1.0),
                    }
                )
            if stroke_payload["points"]:
                frame_payload["strokes"].append(stroke_payload)
        if frame_payload["strokes"]:
            frames.append(frame_payload)
    return frames, _bounds_from_points(bounds_points)


def _gp_material_payload(obj, layer) -> dict:
    mat = gp_utils.ensure_layer_material(obj, layer, activate=False, assign_existing=False)
    style = getattr(mat, "grease_pencil", None) if mat is not None else None
    if style is None:
        return {}
    return {
        "color": [float(v) for v in getattr(style, "color", (0.0, 0.0, 0.0, 1.0))[:4]],
        "fill_color": [float(v) for v in getattr(style, "fill_color", (1.0, 1.0, 1.0, 1.0))[:4]],
        "show_stroke": bool(getattr(style, "show_stroke", True)),
        "show_fill": bool(getattr(style, "show_fill", False)),
    }


def _apply_gp_material(obj, layer, payload: dict) -> None:
    mat = gp_utils.ensure_layer_material(obj, layer, activate=True, assign_existing=True)
    style = getattr(mat, "grease_pencil", None) if mat is not None else None
    if style is None:
        return
    for attr in ("color", "fill_color"):
        value = payload.get(attr)
        if isinstance(value, (list, tuple)) and len(value) >= 4:
            try:
                setattr(style, attr, tuple(float(v) for v in value[:4]))
            except Exception:  # noqa: BLE001
                pass
    for attr in ("show_stroke", "show_fill"):
        if attr in payload:
            try:
                setattr(style, attr, bool(payload[attr]))
            except Exception:  # noqa: BLE001
                pass


def _offset_coma_geometry(panel, dx: float, dy: float) -> None:
    if str(getattr(panel, "shape_type", "rect") or "rect") == "rect":
        panel.rect_x_mm = float(getattr(panel, "rect_x_mm", 0.0) or 0.0) + dx
        panel.rect_y_mm = float(getattr(panel, "rect_y_mm", 0.0) or 0.0) + dy
        return
    for vertex in getattr(panel, "vertices", []) or []:
        vertex.x_mm = float(vertex.x_mm) + dx
        vertex.y_mm = float(vertex.y_mm) + dy


def _set_entry_parent(entry, parent_kind: str, parent_key: str) -> None:
    if parent_kind == "none" or not parent_key:
        if hasattr(entry, "scope"):
            entry.scope = "master"
        entry.parent_kind = "none"
        entry.parent_key = ""
        return
    if hasattr(entry, "scope"):
        entry.scope = "page"
    entry.parent_kind = "coma" if ":" in parent_key else "page"
    entry.parent_key = parent_key


def _page_for_parent_key(context, parent_key: str):
    page_id = split_child_key(parent_key)[0] if parent_key else ""
    work = get_work(context)
    if work is None or not page_id:
        return None
    for page in getattr(work, "pages", []) or []:
        if page_stack_key(page) == page_id:
            return page
    return None


def _page_offset(context, page) -> tuple[float, float]:
    work = get_work(context)
    if work is None or page is None:
        return 0.0, 0.0
    for index, candidate in enumerate(getattr(work, "pages", []) or []):
        if page_stack_key(candidate) == page_stack_key(page):
            return page_grid.page_total_offset_mm(work, context.scene, index)
    return 0.0, 0.0


def _coma_bounds(panel) -> list[float]:
    if str(getattr(panel, "shape_type", "rect") or "rect") == "rect":
        return [
            float(getattr(panel, "rect_x_mm", 0.0) or 0.0),
            float(getattr(panel, "rect_y_mm", 0.0) or 0.0),
            float(getattr(panel, "rect_width_mm", 1.0) or 1.0),
            float(getattr(panel, "rect_height_mm", 1.0) or 1.0),
        ]
    points = [
        (float(v.x_mm), float(v.y_mm))
        for v in getattr(panel, "vertices", []) or []
    ]
    return _bounds_from_points(points)


def _raster_bounds(context) -> list[float]:
    work = get_work(context)
    paper = getattr(work, "paper", None) if work is not None else None
    return [
        0.0,
        0.0,
        float(getattr(paper, "canvas_width_mm", 210.0) or 210.0),
        float(getattr(paper, "canvas_height_mm", 297.0) or 297.0),
    ]


def _bounds_from_points(points: list[tuple[float, float]]) -> list[float]:
    if not points:
        return [0.0, 0.0, 30.0, 30.0]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    left = min(xs)
    bottom = min(ys)
    return [left, bottom, max(1.0, max(xs) - left), max(1.0, max(ys) - bottom)]


def _unique_gp_layer_name(layers, base: str) -> str:
    existing = {str(getattr(layer, "name", "") or "") for layer in layers}
    name = str(base or "レイヤー")
    if name not in existing:
        return name
    index = 1
    while True:
        candidate = f"{name}.{index:03d}"
        if candidate not in existing:
            return candidate
        index += 1
