"""効果線の入り抜きカーブ共有ヘルパー."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

try:
    import bpy
except ModuleNotFoundError:  # pragma: no cover
    bpy = None  # type: ignore[assignment]

DEFAULT_CURVE_TEXT = "0.0000,0.0000;1.0000,1.0000"
DEFAULT_POINTS: tuple[tuple[float, float], ...] = ((0.0, 0.0), (1.0, 1.0))
MATERIAL_NAME = "BManga_EffectLine_InOutCurve"
IN_NODE_NAME = "BManga_EffectLine_InCurve"
OUT_NODE_NAME = "BManga_EffectLine_OutCurve"
IN_SOURCE_PROP = "bmanga_effect_in_curve_source"
OUT_SOURCE_PROP = "bmanga_effect_out_curve_source"


def parse_points(value: object) -> tuple[tuple[float, float], ...]:
    raw: list[tuple[float, float]] = []
    if isinstance(value, str):
        for part in value.split(";"):
            bits = [b.strip() for b in part.split(",")]
            if len(bits) != 2:
                continue
            try:
                raw.append((float(bits[0]), float(bits[1])))
            except ValueError:
                continue
    elif isinstance(value, Iterable):
        for item in value:
            try:
                x, y = item
                raw.append((float(x), float(y)))
            except Exception:  # noqa: BLE001
                continue
    return normalize_points(raw)


def normalize_points(points: Sequence[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    cleaned = [(_clamp01(x), _clamp01(y)) for x, y in points]
    if len(cleaned) < 2:
        return DEFAULT_POINTS
    cleaned.sort(key=lambda p: p[0])
    if cleaned[0][0] > 1.0e-4:
        cleaned.insert(0, (0.0, cleaned[0][1]))
    else:
        cleaned[0] = (0.0, cleaned[0][1])
    if cleaned[-1][0] < 1.0 - 1.0e-4:
        cleaned.append((1.0, cleaned[-1][1]))
    else:
        cleaned[-1] = (1.0, cleaned[-1][1])
    deduped: list[tuple[float, float]] = []
    for x, y in cleaned:
        if deduped and abs(deduped[-1][0] - x) < 1.0e-4:
            deduped[-1] = (x, y)
        else:
            deduped.append((x, y))
    return tuple(deduped[:16]) if len(deduped) >= 2 else DEFAULT_POINTS


def points_to_text(points: Sequence[tuple[float, float]]) -> str:
    return ";".join(f"{x:.4f},{y:.4f}" for x, y in normalize_points(points))


def evaluate(points: Sequence[tuple[float, float]], t: float) -> float:
    pts = normalize_points(points)
    t = _clamp01(t)
    if t <= pts[0][0]:
        return _clamp01(pts[0][1])
    for index in range(1, len(pts)):
        x0, y0 = pts[index - 1]
        x1, y1 = pts[index]
        if t <= x1:
            span = x1 - x0
            u = 0.0 if span <= 1.0e-9 else (t - x0) / span
            return _clamp01(y0 + (y1 - y0) * u)
    return _clamp01(pts[-1][1])


def ensure_ui_nodes(params):
    if bpy is None:
        return None, None
    mat = bpy.data.materials.get(MATERIAL_NAME) or bpy.data.materials.new(MATERIAL_NAME)
    mat.use_nodes = True
    nt = mat.node_tree
    if nt is None:
        return None, None
    in_node = _ensure_node(nt, IN_NODE_NAME, "入りカーブ", getattr(params, "in_easing_curve", DEFAULT_CURVE_TEXT), mat, IN_SOURCE_PROP)
    out_node = _ensure_node(nt, OUT_NODE_NAME, "抜きカーブ", getattr(params, "out_easing_curve", DEFAULT_CURVE_TEXT), mat, OUT_SOURCE_PROP)
    return in_node, out_node


def get_ui_nodes():
    if bpy is None:
        return None, None
    mat = bpy.data.materials.get(MATERIAL_NAME)
    nt = getattr(mat, "node_tree", None) if mat is not None else None
    if nt is None:
        return None, None
    return _get_curve_node(nt, IN_NODE_NAME), _get_curve_node(nt, OUT_NODE_NAME)


def sync_ui_nodes_to_params(params) -> bool:
    if bpy is None or params is None:
        return False
    mat = bpy.data.materials.get(MATERIAL_NAME)
    nt = getattr(mat, "node_tree", None) if mat is not None else None
    if nt is None:
        return False
    changed = False
    for node_name, attr, prop_name in (
        (IN_NODE_NAME, "in_easing_curve", IN_SOURCE_PROP),
        (OUT_NODE_NAME, "out_easing_curve", OUT_SOURCE_PROP),
    ):
        node = nt.nodes.get(node_name)
        if node is None or node.bl_idname != "ShaderNodeFloatCurve":
            continue
        text = points_to_text(read_node_points(node))
        if str(getattr(params, attr, "") or "") != text:
            setattr(params, attr, text)
            changed = True
        mat[prop_name] = text
    return changed


def _get_curve_node(nt, node_name: str):
    node = nt.nodes.get(node_name)
    if node is None or node.bl_idname != "ShaderNodeFloatCurve":
        return None
    return node


def read_node_points(node) -> tuple[tuple[float, float], ...]:
    try:
        curve = node.mapping.curves[0]
        return normalize_points([(float(point.location.x), float(point.location.y)) for point in curve.points])
    except Exception:  # noqa: BLE001
        return DEFAULT_POINTS


def _ensure_node(nt, node_name: str, label: str, stored_points: object, mat, source_prop: str):
    node = nt.nodes.get(node_name)
    if node is not None and node.bl_idname != "ShaderNodeFloatCurve":
        nt.nodes.remove(node)
        node = None
    if node is None:
        node = nt.nodes.new("ShaderNodeFloatCurve")
        node.name = node_name
    node.label = label
    stored_text = points_to_text(parse_points(stored_points))
    last_source = str(mat.get(source_prop, "") or "")
    points = read_node_points(node) if last_source == stored_text else parse_points(stored_text)
    _apply_points_to_node(node, points)
    mat[source_prop] = stored_text
    return node


def _apply_points_to_node(node, points: Sequence[tuple[float, float]]) -> None:
    normalized = normalize_points(points)
    try:
        mapping = node.mapping
        mapping.initialize()
        curve = mapping.curves[0]
        while len(curve.points) > 2:
            curve.points.remove(curve.points[-2])
        curve.points[0].location = normalized[0]
        curve.points[-1].location = normalized[-1]
        for x, y in normalized[1:-1]:
            curve.points.new(x, y)
        for point in curve.points:
            point.handle_type = "AUTO"
        mapping.update()
    except Exception:  # noqa: BLE001
        pass


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
