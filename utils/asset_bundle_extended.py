"""Extended B-MANGA asset payload helpers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from pathlib import Path
import tempfile
import zlib

import bpy

from ..core.work import get_work
from ..io import schema
from . import gp_layer_parenting as gp_parent
from . import gpencil as gp_utils
from . import layer_stack as layer_stack_utils
from . import log
from . import object_naming as on
from . import page_grid
from .geom import m_to_mm, mm_to_m
from .layer_hierarchy import coma_stack_key, page_stack_key, split_child_key


EXTENDED_LAYER_KINDS = {"coma", "raster", "gp"}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_logger = log.get_logger(__name__)


def _decode_png_payload(png_b64: str) -> bytes:
    try:
        payload = base64.b64decode(png_b64.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ValueError("invalid raster base64 payload") from exc
    _validate_png_bytes(payload)
    return payload


def _validate_png_bytes(payload: bytes) -> None:
    """PNGの構造と全chunk CRCを検査する。"""
    if not payload.startswith(_PNG_SIGNATURE):
        raise ValueError("invalid PNG signature")
    offset = len(_PNG_SIGNATURE)
    seen_ihdr = False
    seen_iend = False
    while offset < len(payload):
        if offset + 12 > len(payload):
            raise ValueError("truncated PNG chunk")
        length = int.from_bytes(payload[offset:offset + 4], "big")
        chunk_type = payload[offset + 4:offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > len(payload):
            raise ValueError("truncated PNG payload")
        expected_crc = int.from_bytes(payload[data_end:crc_end], "big")
        actual_crc = zlib.crc32(chunk_type + payload[data_start:data_end]) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ValueError("PNG CRC mismatch")
        if not seen_ihdr:
            if chunk_type != b"IHDR" or length != 13:
                raise ValueError("PNG IHDR is missing")
            seen_ihdr = True
        if chunk_type == b"IEND":
            if length != 0 or crc_end != len(payload):
                raise ValueError("invalid PNG IEND")
            seen_iend = True
            break
        offset = crc_end
    if not seen_ihdr or not seen_iend:
        raise ValueError("incomplete PNG")


def _atomic_write_verified_bytes(path: Path, payload: bytes) -> str:
    """同一フォルダー内でatomic writeし、再読込hashまで確認する。"""
    from ..io.project_content_migration_lock import guard_path_write
    from ..io.project_content_save_baseline import record_successful_write

    _validate_png_bytes(payload)
    expected_hash = hashlib.sha256(payload).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.exists():
        raise OSError("raster destination already exists or is a symbolic link")
    with guard_path_write(path):
        fd, temp_name = tempfile.mkstemp(
            prefix=path.name + ".",
            suffix=".tmp",
            dir=str(path.parent),
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            reread = temp_path.read_bytes()
            _validate_png_bytes(reread)
            if hashlib.sha256(reread).hexdigest() != expected_hash:
                raise OSError("temporary raster hash mismatch")
            os.replace(temp_path, path)
            final = path.read_bytes()
            _validate_png_bytes(final)
            if hashlib.sha256(final).hexdigest() != expected_hash:
                raise OSError("final raster hash mismatch")
            record_successful_write(path)
        except BaseException:
            path.unlink(missing_ok=True)
            record_successful_write(path)
            raise
        finally:
            temp_path.unlink(missing_ok=True)
    return expected_hash


def _remove_raster_entry(coll, raster) -> None:
    for index, current in enumerate(coll):
        if current == raster:
            coll.remove(index)
            return


def remove_staged_raster(context, raster) -> bool:
    """素材ステージが作ったラスター実体とPNGを検証付きで取り除く。"""
    from ..io.project_content_migration_lock import guard_path_write
    from ..io.project_content_save_baseline import record_successful_write

    work = get_work(context)
    coll = getattr(getattr(context, "scene", None), "bmanga_raster_layers", None)
    if work is None or coll is None or raster is None:
        return False
    raster_id = str(getattr(raster, "id", "") or "")
    image_name = str(getattr(raster, "image_name", "") or "")
    path = Path(work.work_dir) / str(getattr(raster, "filepath_rel", "") or "")
    try:
        plane = on.find_object_by_bmanga_id(raster_id, kind="raster")
        if plane is not None:
            bpy.data.objects.remove(plane, do_unlink=True)
        image = bpy.data.images.get(image_name)
        if image is not None:
            bpy.data.images.remove(image)
        with guard_path_write(path):
            path.unlink(missing_ok=True)
            record_successful_write(path)
        _remove_raster_entry(coll, raster)
        return not path.exists() and on.find_object_by_bmanga_id(raster_id, kind="raster") is None
    except Exception:  # noqa: BLE001
        _logger.exception("staged raster removal failed: %s", raster_id)
        return False


def raster_payload_is_durable(context, raster, payload_entry: dict) -> bool:
    """復元PNGがpayloadと同一で、再読込可能な状態かを返す。"""
    work = get_work(context)
    if work is None or raster is None or not getattr(work, "work_dir", ""):
        return False
    rel = str(getattr(raster, "filepath_rel", "") or "")
    if not rel or Path(rel).is_absolute():
        return False
    root = Path(work.work_dir).resolve()
    path = (root / rel).resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError:
        return False
    if path.is_symlink() or not path.is_file():
        return False
    try:
        actual = path.read_bytes()
        _validate_png_bytes(actual)
        encoded = str(payload_entry.get("png_base64", "") or "")
        if encoded:
            expected = _decode_png_payload(encoded)
            if hashlib.sha256(actual).digest() != hashlib.sha256(expected).digest():
                return False
    except (OSError, ValueError):
        return False
    image_name = str(getattr(raster, "image_name", "") or "")
    image = bpy.data.images.get(image_name) if image_name else None
    return image is not None


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


def instantiate_coma(
    context,
    page,
    entry: dict,
    dx: float,
    dy: float,
    *,
    persist_sidecars: bool = True,
):
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
        if not persist_sidecars:
            return panel
        work_dir = Path(work.work_dir)
        coma_io.save_coma_meta(work_dir, page.id, panel)
        page_io.save_page_json(work_dir, page)
        page_io.save_pages_json(work_dir, work)
    except Exception:  # noqa: BLE001
        pass
    return panel


def instantiate_raster(context, page, entry: dict, parent_kind: str, parent_key: str):
    from ..operators import raster_layer_op
    from ..io.project_content_migration_lock import guard_path_write
    from ..io.project_content_save_baseline import record_successful_write

    work = get_work(context)
    coll = getattr(getattr(context, "scene", None), "bmanga_raster_layers", None)
    if work is None or coll is None or not getattr(work, "work_dir", ""):
        return None
    data = dict(entry.get("data") or {})
    png_b64 = str(entry.get("png_base64", "") or "")
    try:
        png_payload = _decode_png_payload(png_b64) if png_b64 else None
    except ValueError:
        return None
    raster = coll.add()
    schema.raster_layer_from_dict(raster, data, opacity_percent=True)
    raster_id = raster_layer_op._allocate_raster_id(context.scene, Path(work.work_dir))
    raster.id = raster_id
    raster.image_name = raster_layer_op.raster_image_name(raster_id)
    raster.filepath_rel = raster_layer_op.raster_filepath_rel(raster_id)
    _set_entry_parent(raster, parent_kind, parent_key)
    path = Path(work.work_dir) / raster.filepath_rel
    path_existed = path.exists()
    try:
        if png_payload is not None:
            _atomic_write_verified_bytes(path, png_payload)
        image = raster_layer_op.ensure_raster_image(context, raster, create_missing=True)
        if image is None:
            raise RuntimeError("raster image reload failed")
        if png_payload is None:
            raster_layer_op.save_raster_png(context, raster, force=True)
            record_successful_write(path)
            saved = path.read_bytes()
            _validate_png_bytes(saved)
        elif not raster_payload_is_durable(context, raster, entry):
            raise RuntimeError("raster payload durability check failed")
        raster_layer_op.ensure_raster_plane(context, raster)
        context.scene.bmanga_active_raster_layer_index = len(coll) - 1
        context.scene.bmanga_active_layer_kind = "raster"
        return raster
    except Exception:  # noqa: BLE001
        plane = on.find_object_by_bmanga_id(raster_id, kind="raster")
        if plane is not None:
            bpy.data.objects.remove(plane, do_unlink=True)
        image = bpy.data.images.get(raster.image_name)
        if image is not None:
            bpy.data.images.remove(image)
        try:
            if not path_existed:
                with guard_path_write(path):
                    path.unlink(missing_ok=True)
                    record_successful_write(path)
        except Exception:  # noqa: BLE001
            _logger.exception("failed staged raster cleanup: %s", path)
        _remove_raster_entry(coll, raster)
        return None


def instantiate_gp_layer(
    context,
    page,
    entry: dict,
    dx: float,
    dy: float,
    parent_kind: str,
    parent_key: str,
):
    from . import gp_object_layer
    from . import layer_object_model

    title = str(entry.get("title", "") or "レイヤー")
    bmanga_id = layer_object_model.make_stable_id("gp")
    z_order = max(
        (layer_object_model.z_index(candidate) for candidate in layer_object_model.iter_layer_objects("gp")),
        default=200,
    ) + 10
    obj = gp_object_layer.create_layer_gp_object(
        scene=context.scene,
        bmanga_id=bmanga_id,
        title=title,
        z_index=z_order,
        parent_kind=parent_kind,
        parent_key="" if parent_kind == "none" else parent_key,
    )
    layer = layer_object_model.content_layer(obj)
    if obj is None or layer is None:
        return None
    _apply_gp_material(obj, layer, entry.get("material") if isinstance(entry.get("material"), dict) else {})
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
                x = float(point.get("x", 0.0) or 0.0) + dx
                y = float(point.get("y", 0.0) or 0.0) + dy
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
        from . import layer_object_model

        return layer_stack_utils.target_uid("gp", layer_object_model.stable_id(obj[0]))
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
    from . import layer_object_model

    parent_key = layer_object_model.parent_key(obj) or str(getattr(item, "parent_key", "") or "")
    frames, bounds = _serialize_gp_frames(layer)
    return {
        "kind": "gp",
        "source_id": layer_object_model.stable_id(obj),
        "source_parent_key": parent_key,
        "title": layer_object_model.display_title(obj) or "レイヤー",
        "bounds": bounds,
        "frames": frames,
        "material": _gp_material_payload(obj, layer),
    }


def _serialize_gp_frames(layer):
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
                # 1 Object = 1 手描きレイヤーでは描画点はObjectローカル座標。
                # ページ原点はObject.locationが担うため、ここで再度減算しない。
                x = m_to_mm(float(pos[0]))
                y = m_to_mm(float(pos[1]))
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
