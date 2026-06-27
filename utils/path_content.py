"""Path content helpers for image and generated-shape strokes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

try:
    import bpy
except ModuleNotFoundError:  # pragma: no cover
    bpy = None  # type: ignore[assignment]

from . import effect_inout_curve, line_decor_geom, material_opacity_mask

COLOR_ATTRIBUTE = "bmanga_path_content_color"
ALPHA_ATTRIBUTE = "bmanga_path_content_alpha"
DEFAULT_COLOR = (1.0, 1.0, 1.0, 1.0)


def value(source, name: str, default=None):
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def bool_value(source, name: str, default: bool = False) -> bool:
    raw = value(source, name, default)
    if isinstance(raw, str):
        return raw.strip().lower() not in {"", "0", "false", "off", "none", "なし"}
    return bool(raw)


def float_value(source, name: str, default: float = 0.0) -> float:
    try:
        return float(value(source, name, default))
    except Exception:  # noqa: BLE001
        return float(default)


def color_value(source, name: str, default=DEFAULT_COLOR) -> tuple[float, float, float, float]:
    raw = value(source, name, default)
    try:
        vals = [float(raw[i]) for i in range(min(4, len(raw)))]
    except Exception:  # noqa: BLE001
        vals = list(default)
    while len(vals) < 4:
        vals.append(1.0)
    return tuple(max(0.0, min(1.0, v)) for v in vals[:4])


def unit_shape_points(kind: str, sides: int = 6) -> list[tuple[float, float]]:
    return line_decor_geom.unit_shape_points(kind, sides=sides)


def inout_profile_value(source, distance: float, total: float) -> float:
    vi, vo = _inout_factors(source, distance, total)
    return _clamp01(min(vi, vo))


def _inout_factors(source, distance: float, total: float) -> tuple[float, float]:
    total = max(0.0, float(total))
    if total <= 1.0e-9:
        return 1.0, 1.0
    distance = max(0.0, min(total, float(distance)))
    in_frac = _clamp01(float_value(source, "in_percent", 100.0) / 100.0)
    out_frac = _clamp01(float_value(source, "out_percent", 100.0) / 100.0)
    d_in = _clamp01(float_value(source, "in_start_percent", 0.0) / 100.0) * total
    d_out = _clamp01(float_value(source, "out_start_percent", 0.0) / 100.0) * total
    if d_in + d_out > total:
        scale = total / max(d_in + d_out, 1.0e-9)
        d_in *= scale
        d_out *= scale
    in_curve = effect_inout_curve.parse_points(
        value(source, "in_easing_curve", effect_inout_curve.DEFAULT_CURVE_TEXT)
    )
    out_curve = effect_inout_curve.parse_points(
        value(source, "out_easing_curve", effect_inout_curve.DEFAULT_CURVE_TEXT)
    )
    if d_in <= 1.0e-9:
        vi = 1.0
    else:
        vi = in_frac + (1.0 - in_frac) * effect_inout_curve.evaluate(in_curve, distance / d_in)
    if d_out <= 1.0e-9:
        vo = 1.0
    else:
        out_t = (total - distance) / d_out
        vo = out_frac + (1.0 - out_frac) * effect_inout_curve.evaluate(out_curve, out_t)
    return _clamp01(vi), _clamp01(vo)


def color_for_path_distance(
    source,
    distance: float,
    total: float,
    *,
    color_field: str = "color",
    start_field: str = "inout_start_color",
    end_field: str = "inout_end_color",
    color_enabled: str = "inout_color_enabled",
    opacity_enabled: str = "inout_opacity_enabled",
) -> tuple[float, float, float, float]:
    factor = inout_profile_value(source, distance, total)
    base = color_value(source, color_field, DEFAULT_COLOR)
    if bool_value(source, color_enabled, False):
        start = color_value(source, start_field, DEFAULT_COLOR)
        end = color_value(source, end_field, DEFAULT_COLOR)
        in_factor, out_factor = _inout_factors(source, distance, total)
        start_weight = 1.0 - in_factor
        end_weight = 1.0 - out_factor
        if start_weight + end_weight > 1.0:
            scale = 1.0 / max(start_weight + end_weight, 1.0e-9)
            start_weight *= scale
            end_weight *= scale
        base_weight = max(0.0, 1.0 - start_weight - end_weight)
        rgba = tuple(
            base[i] * base_weight + start[i] * start_weight + end[i] * end_weight
            for i in range(4)
        )
    else:
        rgba = base
    alpha = rgba[3] * (factor if bool_value(source, opacity_enabled, False) else 1.0)
    return (
        _clamp01(rgba[0]),
        _clamp01(rgba[1]),
        _clamp01(rgba[2]),
        _clamp01(alpha),
    )


def size_factor(source, profile: float, field: str = "inout_size_enabled") -> float:
    return _clamp01(profile) if bool_value(source, field, False) else 1.0


def write_color_attribute(mesh: bpy.types.Mesh, colors: Sequence[tuple[float, float, float, float]] | None) -> None:
    if bpy is None or mesh is None:
        return
    try:
        existing = mesh.attributes.get(COLOR_ATTRIBUTE)
        if existing is not None:
            mesh.attributes.remove(existing)
        existing_alpha = mesh.attributes.get(ALPHA_ATTRIBUTE)
        if existing_alpha is not None:
            mesh.attributes.remove(existing_alpha)
    except Exception:  # noqa: BLE001
        pass
    if not colors:
        return
    try:
        attr = mesh.attributes.new(name=COLOR_ATTRIBUTE, type="FLOAT_COLOR", domain="POINT")
    except Exception:  # noqa: BLE001
        return
    for index, color in enumerate(colors):
        if index >= len(attr.data):
            break
        try:
            attr.data[index].color = color
        except Exception:  # noqa: BLE001
            pass
    try:
        alpha_attr = mesh.attributes.new(name=ALPHA_ATTRIBUTE, type="FLOAT", domain="POINT")
    except Exception:  # noqa: BLE001
        return
    for index, color in enumerate(colors):
        if index >= len(alpha_attr.data):
            break
        try:
            alpha_attr.data[index].value = max(0.0, min(1.0, float(color[3])))
        except Exception:  # noqa: BLE001
            pass


def ensure_material(
    name: str,
    image,
    opacity_percent: float,
    *,
    mask_info=None,
    fallback_alpha: float = 1.0,
) -> bpy.types.Material:
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    try:
        mat.blend_method = "BLEND"
        mat.show_transparent_back = False
    except Exception:  # noqa: BLE001
        pass
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (640, 0)
    transparent = nt.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (170, -150)
    emission = nt.nodes.new("ShaderNodeEmission")
    emission.location = (170, 80)
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (420, 0)
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = COLOR_ATTRIBUTE
    attr.location = (-610, -110)
    attr_color = _socket(attr.outputs, "Color")
    alpha_attr = nt.nodes.new("ShaderNodeAttribute")
    alpha_attr.attribute_name = ALPHA_ATTRIBUTE
    alpha_attr.location = (-610, -300)
    attr_alpha = _socket(alpha_attr.outputs, "Fac", "Value", "Alpha")
    alpha_socket = attr_alpha
    if image is not None:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.location = (-610, 110)
        tex.image = image
        try:
            tex.extension = "REPEAT"
            tex.interpolation = "Linear"
        except Exception:  # noqa: BLE001
            pass
        tex_color = _socket(tex.outputs, "Color")
        tex_alpha = _socket(tex.outputs, "Alpha")
        color_socket = tex_color
        try:
            mult = nt.nodes.new("ShaderNodeMixRGB")
            mult.blend_type = "MULTIPLY"
            mult.inputs["Fac"].default_value = 1.0
            mult.location = (-220, 100)
            nt.links.new(tex_color, mult.inputs["Color1"])
            nt.links.new(attr_color, mult.inputs["Color2"])
            color_socket = mult.outputs["Color"]
        except Exception:  # noqa: BLE001
            pass
        _link(nt, color_socket, emission.inputs["Color"])
        if tex_alpha is not None and attr_alpha is not None:
            alpha_mul = _math_mul(nt, tex_alpha, attr_alpha, (-220, -130))
            alpha_socket = alpha_mul
        else:
            alpha_socket = tex_alpha or attr_alpha
    else:
        _link(nt, attr_color, emission.inputs["Color"])
        if attr_alpha is None:
            value = nt.nodes.new("ShaderNodeValue")
            value.location = (-610, -280)
            value.outputs[0].default_value = max(0.0, min(1.0, float(fallback_alpha)))
            alpha_socket = value.outputs[0]

    if alpha_socket is not None and mask_info is not None:
        try:
            masked = material_opacity_mask.multiply_alpha_by_mask(
                nt,
                alpha_socket,
                mask_object=getattr(mask_info, "space_object", None),
                mask_image=getattr(mask_info, "image", None),
            )
            alpha_socket = masked if masked is not None else alpha_socket
        except Exception:  # noqa: BLE001
            pass
    opacity_node = nt.nodes.new("ShaderNodeValue")
    opacity_node.location = (-220, -330)
    opacity_node.outputs[0].default_value = max(0.0, min(1.0, float(opacity_percent) / 100.0))
    final_alpha = _math_mul(nt, alpha_socket, opacity_node.outputs[0], (90, -250)) if alpha_socket is not None else opacity_node.outputs[0]
    emission.inputs["Strength"].default_value = 1.0
    _link(nt, final_alpha, mix.inputs["Fac"])
    _link(nt, transparent.outputs["BSDF"], mix.inputs[1])
    _link(nt, emission.outputs["Emission"], mix.inputs[2])
    _link(nt, mix.outputs["Shader"], out.inputs["Surface"])
    alpha = max(0.0, min(1.0, float(opacity_percent) / 100.0)) * max(0.0, min(1.0, float(fallback_alpha)))
    mat.diffuse_color = (1.0, 1.0, 1.0, alpha)
    try:
        mat.update_tag()
    except Exception:  # noqa: BLE001
        pass
    return mat


def _socket(outputs, *names: str):
    for name in names:
        try:
            sock = outputs.get(name)
        except Exception:  # noqa: BLE001
            sock = None
        if sock is not None:
            return sock
    return None


def _link(nt, source, target) -> None:
    if source is None or target is None:
        return
    try:
        nt.links.new(source, target)
    except Exception:  # noqa: BLE001
        pass


def _math_mul(nt, left, right, location: tuple[float, float]):
    node = nt.nodes.new("ShaderNodeMath")
    node.operation = "MULTIPLY"
    node.location = location
    _link(nt, left, node.inputs[0])
    _link(nt, right, node.inputs[1])
    return node.outputs["Value"]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
