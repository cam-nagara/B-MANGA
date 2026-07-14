"""手描きレイヤーのページファイル間シリアライズと再構築。"""

from __future__ import annotations

import bpy
import math
from mathutils import Matrix


_STROKE_ATTRIBUTES = (
    "aspect_ratio",
    "end_cap",
    "fill_id",
    "fill_opacity",
    "hide_stroke",
    "select",
    "softness",
    "start_cap",
    "time_start",
)
_POINT_ATTRIBUTES = ("delta_time", "rotation", "select")


def _rounded_vector(value, digits: int = 9) -> list[float]:
    return [round(float(component), digits) for component in tuple(value)]


def _json_rna_value(value):
    """RNAの単純値をJSON化し、Pointer/Collectionは対象外にする。"""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 9) if math.isfinite(value) else None
    if isinstance(value, str):
        return value
    if isinstance(value, set):
        return sorted(str(item) for item in value)
    if isinstance(value, (list, tuple)) or type(value).__name__ == "bpy_prop_array":
        out = [_json_rna_value(item) for item in value]
        return out if all(item is not None for item in out) else None
    return None


def _serialize_rna_properties(value, *, exclude: set[str] | None = None) -> dict:
    """書込可能な単純RNA値を一括保全する。"""
    rna = getattr(getattr(value, "bl_rna", None), "properties", None)
    if rna is None:
        return {}
    excluded = {"rna_type", *(exclude or set())}
    result = {}
    for prop in rna:
        name = str(getattr(prop, "identifier", "") or "")
        if not name or name in excluded or bool(getattr(prop, "is_readonly", False)):
            continue
        if str(getattr(prop, "type", "") or "") not in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
            continue
        if bool(getattr(prop, "is_array", False)) and str(getattr(prop, "type", "")) not in {
            "BOOLEAN",
            "INT",
            "FLOAT",
        }:
            continue
        try:
            encoded = _json_rna_value(getattr(value, name))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
        if encoded is not None:
            result[name] = encoded
    return result


def _restore_rna_properties(value, properties: dict) -> None:
    """現行Blenderに存在する書込可能値だけ復元する。"""
    rna = getattr(getattr(value, "bl_rna", None), "properties", None)
    if rna is None or not isinstance(properties, dict):
        return
    for name, raw in properties.items():
        prop = rna.get(name)
        if prop is None or bool(getattr(prop, "is_readonly", False)):
            continue
        prop_type = str(getattr(prop, "type", "") or "")
        is_array = bool(getattr(prop, "is_array", False))
        if not _rna_value_is_compatible(prop_type, is_array, prop, raw):
            continue
        restored = raw
        if prop_type == "ENUM" and bool(
            getattr(prop, "is_enum_flag", False)
        ):
            restored = set(raw) if isinstance(raw, list) else raw
        elif is_array and isinstance(raw, list):
            restored = tuple(raw)
        try:
            setattr(value, name, restored)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue


def _rna_value_is_compatible(prop_type: str, is_array: bool, prop, raw) -> bool:
    if is_array:
        expected = int(getattr(prop, "array_length", 0) or 0)
        if not isinstance(raw, list) or (expected and len(raw) != expected):
            return False
        checks = {
            "BOOLEAN": lambda item: isinstance(item, bool),
            "INT": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "FLOAT": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        }
        return prop_type in checks and all(checks[prop_type](item) for item in raw)
    if prop_type == "BOOLEAN":
        return isinstance(raw, bool)
    if prop_type == "INT":
        return isinstance(raw, int) and not isinstance(raw, bool)
    if prop_type == "FLOAT":
        return isinstance(raw, (int, float)) and not isinstance(raw, bool)
    if prop_type == "STRING":
        return isinstance(raw, str)
    if prop_type == "ENUM":
        return isinstance(raw, str) or (
            bool(getattr(prop, "is_enum_flag", False))
            and isinstance(raw, list)
            and all(isinstance(item, str) for item in raw)
        )
    return False


def _page_origin_m(scene, parent_key: str) -> tuple[float, float]:
    page_id = str(parent_key or "").split(":", 1)[0]
    work = getattr(scene, "bmanga_work", None) if scene is not None else None
    if not page_id or work is None:
        return 0.0, 0.0
    try:
        from . import page_grid
        from .geom import mm_to_m

        index = next(
            i
            for i, page in enumerate(work.pages)
            if str(getattr(page, "id", "") or "") == page_id
        )
        ox_mm, oy_mm = page_grid.page_total_offset_mm(work, scene, index)
        return mm_to_m(ox_mm), mm_to_m(oy_mm)
    except (StopIteration, AttributeError, TypeError, ValueError):
        return 0.0, 0.0


def _serialize_object_transform(obj, parent_key: str) -> dict:
    ox, oy = _page_origin_m(bpy.context.scene, parent_key)
    relative = Matrix.Translation((-ox, -oy, 0.0)) @ obj.matrix_world
    return {
        "page_relative_matrix": [
            [round(float(value), 9) for value in row]
            for row in relative
        ],
    }


def _serialize_material(material) -> dict:
    gp_style = getattr(material, "grease_pencil", None) if material else None
    if gp_style is None:
        return {
            "color": [0.0, 0.0, 0.0, 1.0],
            "show_stroke": True,
            "show_fill": False,
            "material_rna": _serialize_rna_properties(material, exclude={"name"}),
            "style_rna": {},
        }
    color = list(getattr(gp_style, "color", (0.0, 0.0, 0.0, 1.0)))
    fill_color = list(getattr(gp_style, "fill_color", (0.0, 0.0, 0.0, 0.0)))
    return {
        "color": [round(channel, 6) for channel in color],
        "fill_color": [round(channel, 6) for channel in fill_color],
        "show_stroke": bool(getattr(gp_style, "show_stroke", True)),
        "show_fill": bool(getattr(gp_style, "show_fill", False)),
        "material_rna": _serialize_rna_properties(material, exclude={"name"}),
        "style_rna": _serialize_rna_properties(gp_style),
    }


def _serialize_stroke(stroke) -> dict:
    points = []
    for point in getattr(stroke, "points", []):
        position = tuple(getattr(point, "position", (0, 0, 0)))
        point_data = {
            "pos": _rounded_vector(position),
            "r": round(float(getattr(point, "radius", 0.01)), 9),
            "o": round(float(getattr(point, "opacity", 1.0)), 9),
            "vertex_color": _rounded_vector(
                getattr(point, "vertex_color", (0.0, 0.0, 0.0, 0.0))
            ),
            "rna": _serialize_rna_properties(
                point,
                exclude={"position", "radius", "opacity", "vertex_color"},
            ),
        }
        for attr in _POINT_ATTRIBUTES:
            value = getattr(point, attr, None)
            if value is not None:
                point_data[attr] = value if isinstance(value, bool) else round(float(value), 9)
        handle_left = getattr(point, "handle_left", None)
        handle_right = getattr(point, "handle_right", None)
        if handle_left is not None:
            point_data["hl"] = {
                "position": _rounded_vector(getattr(handle_left, "position", (0.0, 0.0, 0.0))),
                "type": int(getattr(handle_left, "type", 0)),
                "select": bool(getattr(handle_left, "select", False)),
            }
        if handle_right is not None:
            point_data["hr"] = {
                "position": _rounded_vector(getattr(handle_right, "position", (0.0, 0.0, 0.0))),
                "type": int(getattr(handle_right, "type", 0)),
                "select": bool(getattr(handle_right, "select", False)),
            }
        points.append(point_data)
    result = {
        "points": points,
        "cyclic": bool(getattr(stroke, "cyclic", False)),
        "material_index": int(getattr(stroke, "material_index", 0)),
        "curve_type": int(getattr(stroke, "curve_type", 0)),
        "fill_color": _rounded_vector(getattr(stroke, "fill_color", (0.0, 0.0, 0.0, 0.0))),
        "rna": _serialize_rna_properties(
            stroke,
            exclude={"cyclic", "material_index", "curve_type", "fill_color"},
        ),
    }
    for attr in _STROKE_ATTRIBUTES:
        value = getattr(stroke, attr, None)
        if value is None:
            continue
        result[attr] = value if isinstance(value, bool) else round(float(value), 9)
    return result


def serialize_object(bmanga_id: str) -> dict | None:
    """安定IDに対応する手描きObjectをステージング用dictへ変換する。"""
    from . import layer_object_model
    from .object_naming import PROP_ID, find_object_by_bmanga_id

    obj = find_object_by_bmanga_id(bmanga_id, kind="gp")
    gp_data = getattr(obj, "data", None) if obj is not None else None
    if gp_data is None:
        return None
    parent_key = layer_object_model.parent_key(obj)
    result = {
        "bmanga_id": str(obj.get(PROP_ID, "") or ""),
        "title": str(obj.get("bmanga_title", "") or ""),
        "z_index": int(obj.get("bmanga_z_index", 0) or 0),
        "folder_id": str(obj.get("bmanga_folder_id", "") or ""),
        "parent_key": parent_key,
        "visible": layer_object_model.user_visible(obj),
        "locked": layer_object_model.user_locked(obj),
        "object_transform": _serialize_object_transform(obj, parent_key),
        "materials": [
            {"name": material.name, **_serialize_material(material)} if material is not None else None
            for material in getattr(gp_data, "materials", [])
        ],
    }
    layers_data = []
    content = layer_object_model.content_layer(obj)
    for layer in (content,) if content is not None else ():
        layer_info = {
            "name": layer_object_model.CONTENT_LAYER_NAME,
            "opacity": float(getattr(layer, "opacity", 1.0)),
            "blend_mode": str(getattr(layer, "blend_mode", "REGULAR")),
            "tint_color": _rounded_vector(
                getattr(layer, "tint_color", (0.0, 0.0, 0.0, 0.0))
            ),
            "hide": bool(getattr(layer, "hide", False)),
            "lock": bool(getattr(layer, "lock", False)),
            "rna": _serialize_rna_properties(
                layer,
                exclude={"name", "opacity", "blend_mode", "tint_color", "hide", "lock"},
            ),
        }
        frames_data = []
        for frame in getattr(layer, "frames", []):
            drawing = getattr(frame, "drawing", None)
            if drawing is None:
                continue
            frames_data.append({
                "frame_number": int(getattr(frame, "frame_number", 0)),
                "strokes": [_serialize_stroke(stroke) for stroke in getattr(drawing, "strokes", [])],
            })
        layer_info["frames"] = frames_data
        layers_data.append(layer_info)
    result["layers"] = layers_data
    return result


def _create_material(material_info: dict):
    material = bpy.data.materials.new(name=material_info.get("name", "") or "BManga_GP_Material")
    gp_style = getattr(material, "grease_pencil", None)
    if gp_style is None:
        try:
            bpy.data.materials.create_gpencil_data(material)
        except (AttributeError, RuntimeError):
            pass
        gp_style = getattr(material, "grease_pencil", None)
    _restore_rna_properties(material, material_info.get("material_rna", {}))
    if gp_style is not None:
        _restore_rna_properties(gp_style, material_info.get("style_rna", {}))
        try:
            gp_style.show_stroke = material_info.get("show_stroke", True)
            gp_style.color = tuple(material_info.get("color", (0, 0, 0, 1)))
            gp_style.show_fill = material_info.get("show_fill", False)
            gp_style.fill_color = tuple(material_info.get("fill_color", (0, 0, 0, 0)))
        except Exception:  # noqa: BLE001
            pass
    return material


def _restore_materials(obj, materials_data: list) -> None:
    from . import gpencil as gp_utils

    slots = getattr(getattr(obj, "data", None), "materials", None)
    if slots is None:
        return
    try:
        slots.clear()
    except Exception:  # noqa: BLE001
        pass
    for material_info in materials_data:
        try:
            slots.append(_create_material(material_info) if material_info is not None else None)
        except Exception:  # noqa: BLE001
            pass
    gp_utils.ensure_unique_object_materials(obj)


def _curve_type_name(value) -> str:
    return {
        0: "POLY",
        1: "CATMULL_ROM",
        2: "BEZIER",
        3: "NURBS",
    }.get(int(value or 0), "POLY")


def _restore_stroke_attributes(stroke, stroke_data: dict) -> None:
    _restore_rna_properties(stroke, stroke_data.get("rna", {}))
    for attr in _STROKE_ATTRIBUTES:
        if attr not in stroke_data or not hasattr(stroke, attr):
            continue
        try:
            setattr(stroke, attr, stroke_data[attr])
        except (AttributeError, TypeError, ValueError):
            pass
    if "fill_color" in stroke_data and hasattr(stroke, "fill_color"):
        try:
            stroke.fill_color = tuple(stroke_data["fill_color"])
        except (AttributeError, TypeError, ValueError):
            pass


def _restore_handle(handle, data) -> None:
    if handle is None or not isinstance(data, dict):
        return
    for attr, value in (
        ("position", tuple(data.get("position", handle.position))),
        ("type", int(data.get("type", handle.type))),
        ("select", bool(data.get("select", handle.select))),
    ):
        try:
            setattr(handle, attr, value)
        except (AttributeError, TypeError, ValueError):
            pass


def _restore_point_attributes(point, point_data: dict) -> None:
    _restore_rna_properties(point, point_data.get("rna", {}))
    if "vertex_color" in point_data and hasattr(point, "vertex_color"):
        try:
            point.vertex_color = tuple(point_data["vertex_color"])
        except (AttributeError, TypeError, ValueError):
            pass
    for attr in _POINT_ATTRIBUTES:
        if attr not in point_data or not hasattr(point, attr):
            continue
        try:
            setattr(point, attr, point_data[attr])
        except (AttributeError, TypeError, ValueError):
            pass
    _restore_handle(getattr(point, "handle_left", None), point_data.get("hl"))
    _restore_handle(getattr(point, "handle_right", None), point_data.get("hr"))


def _restore_object_transform(obj, transform_data: dict, parent_key: str) -> None:
    rows = transform_data.get("page_relative_matrix") if isinstance(transform_data, dict) else None
    if not isinstance(rows, list) or len(rows) != 4:
        return
    try:
        relative = Matrix(rows)
        ox, oy = _page_origin_m(bpy.context.scene, parent_key)
        obj.matrix_world = Matrix.Translation((ox, oy, 0.0)) @ relative
        from . import page_grid
        from .geom import m_to_mm

        obj[page_grid.SUBPAGE_OFFSET_X_PROP] = float(m_to_mm(relative.translation.x))
        obj[page_grid.SUBPAGE_OFFSET_Y_PROP] = float(m_to_mm(relative.translation.y))
    except (TypeError, ValueError):
        return


def _restore_content(obj, layers_data: list) -> None:
    from . import gpencil as gp_utils
    from . import layer_object_model

    gp_data = obj.data
    layer_info = next((item for item in layers_data if isinstance(item, dict)), {})
    while len(gp_data.layers) > 0:
        try:
            gp_data.layers.remove(gp_data.layers[0])
        except Exception:  # noqa: BLE001
            break
    layer = gp_data.layers.new(layer_object_model.CONTENT_LAYER_NAME)
    try:
        _restore_rna_properties(layer, layer_info.get("rna", {}))
        layer.opacity = float(layer_info.get("opacity", 1.0))
        layer.blend_mode = layer_info.get("blend_mode", "REGULAR")
        if "tint_color" in layer_info and hasattr(layer, "tint_color"):
            layer.tint_color = tuple(layer_info["tint_color"])
        layer.hide = bool(layer_info.get("hide", False))
        layer.lock = bool(layer_info.get("lock", False))
    except Exception:  # noqa: BLE001
        pass
    for frame_info in layer_info.get("frames", []):
        frame = gp_utils.ensure_active_frame(layer, frame_number=int(frame_info.get("frame_number", 0)))
        drawing = getattr(frame, "drawing", None) if frame is not None else None
        if drawing is None:
            continue
        for stroke_data in frame_info.get("strokes", []):
            points = stroke_data.get("points", [])
            if not points:
                continue
            stroke_index = len(getattr(drawing, "strokes", []))
            curve_type = _curve_type_name(stroke_data.get("curve_type", 0))
            restored = gp_utils.add_stroke_to_drawing(
                drawing,
                [tuple(point["pos"]) for point in points],
                radii=[float(point.get("r", 0.01)) for point in points],
                opacities=[float(point.get("o", 1.0)) for point in points],
                cyclic=stroke_data.get("cyclic", False),
                material_index=stroke_data.get("material_index", 0),
                curve_type=curve_type,
            )
            if not restored or stroke_index >= len(getattr(drawing, "strokes", [])):
                continue
            if curve_type not in {"POLY", "BEZIER"}:
                try:
                    drawing.set_types(type=curve_type, indices=(stroke_index,))
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass
            stroke = drawing.strokes[stroke_index]
            _restore_stroke_attributes(stroke, stroke_data)
            for point, point_data in zip(stroke.points, points, strict=False):
                _restore_point_attributes(point, point_data)


def create_object(context, gp_data_dict: dict, parent_key: str) -> bpy.types.Object | None:
    """ステージング用dictから専用材質を持つ手描きObjectを再構築する。"""
    from . import gp_object_layer, layer_object_model

    parent_kind = "coma" if parent_key and ":" in parent_key else "page"
    stable_id = str(gp_data_dict.get("bmanga_id", "") or "").strip()
    existing = layer_object_model.find_layer_object("gp", stable_id) if stable_id else None
    if existing is not None:
        return existing if layer_object_model.parent_key(existing) == parent_key else None
    obj = gp_object_layer.create_layer_gp_object(
        scene=context.scene,
        bmanga_id=stable_id or layer_object_model.make_stable_id("gp"),
        title=gp_data_dict.get("title", "GP Layer"),
        z_index=int(gp_data_dict.get("z_index", 200)),
        parent_kind=parent_kind,
        parent_key=parent_key,
        folder_id=gp_data_dict.get("folder_id", ""),
    )
    if obj is None or getattr(obj, "data", None) is None:
        return None
    _restore_materials(obj, gp_data_dict.get("materials", []))
    _restore_content(obj, gp_data_dict.get("layers", []))
    _restore_object_transform(obj, gp_data_dict.get("object_transform", {}), parent_key)
    # create_layer_gp_object() が最初に作ったコマ／ページマスクは、上の
    # _restore_content() が内容を入れ替える際にいったん削除される。復元後の
    # content に対して改めて生成しないと、別ページのコマへ移した線が枠外へ
    # はみ出すため、内容と変形が確定してから再適用する。
    from . import mask_apply

    mask_apply.apply_mask_to_layer_object(obj)
    layer_object_model.set_user_visible(obj, bool(gp_data_dict.get("visible", True)))
    layer_object_model.set_user_locked(obj, bool(gp_data_dict.get("locked", False)))
    return obj


def remove_object(bmanga_id: str) -> bool:
    """安定IDに対応する手描きObjectを削除し、残存しないことを確認する。"""
    from . import layer_object_model
    from .object_naming import find_object_by_bmanga_id

    obj = find_object_by_bmanga_id(bmanga_id, kind="gp")
    if obj is None:
        return True
    if not layer_object_model.remove_layer_object(obj):
        return False
    return find_object_by_bmanga_id(bmanga_id, kind="gp") is None
