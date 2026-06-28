"""B-MANGA Line edge width curve UI helpers."""

from __future__ import annotations

from collections.abc import Sequence

import bpy

MATERIAL_NAME = "BML_EdgeWidthCurve_UI"
NODE_NAME = "BML_EdgeWidthCurve"
SOURCE_PROP = "bml_edge_width_curve_source"
DEFAULT_POINTS = (
    (0.0, 0.0),
    (0.25, 0.25),
    (0.50, 0.50),
    (0.75, 0.75),
    (1.0, 1.0),
)


def points_from_settings(settings) -> tuple[tuple[float, float], ...]:
    return (
        (0.0, 0.0),
        (0.25, _clamp01(getattr(settings, "edge_width_curve_25", 0.25))),
        (0.50, _clamp01(getattr(settings, "edge_width_curve_50", 0.50))),
        (0.75, _clamp01(getattr(settings, "edge_width_curve_75", 0.75))),
        (1.0, 1.0),
    )


def ensure_node(settings):
    mat = bpy.data.materials.get(MATERIAL_NAME) or bpy.data.materials.new(MATERIAL_NAME)
    mat.use_nodes = True
    nt = mat.node_tree
    if nt is None:
        return None
    node = nt.nodes.get(NODE_NAME)
    if node is not None and node.bl_idname != "ShaderNodeFloatCurve":
        nt.nodes.remove(node)
        node = None
    if node is None:
        node = nt.nodes.new("ShaderNodeFloatCurve")
        node.name = NODE_NAME
    node.label = "中間頂点への変化グラフ"
    source = _points_text(points_from_settings(settings))
    last_source = str(mat.get(SOURCE_PROP, "") or "")
    if last_source != source:
        _apply_points_to_node(node, points_from_settings(settings))
        mat[SOURCE_PROP] = source
    return node


def sync_node_to_settings(settings) -> bool:
    mat = bpy.data.materials.get(MATERIAL_NAME)
    nt = getattr(mat, "node_tree", None) if mat is not None else None
    node = nt.nodes.get(NODE_NAME) if nt is not None else None
    if node is None or node.bl_idname != "ShaderNodeFloatCurve":
        return False
    source = _points_text(points_from_settings(settings))
    if str(mat.get(SOURCE_PROP, "") or "") != source:
        return False
    points = _read_node_points(node)
    changed = False
    changed |= _set_attr(settings, "edge_width_curve_25", _evaluate(points, 0.25))
    changed |= _set_attr(settings, "edge_width_curve_50", _evaluate(points, 0.50))
    changed |= _set_attr(settings, "edge_width_curve_75", _evaluate(points, 0.75))
    mat[SOURCE_PROP] = _points_text(points_from_settings(settings))
    return changed


def _apply_points_to_node(node, points: Sequence[tuple[float, float]]) -> None:
    try:
        mapping = node.mapping
        mapping.initialize()
        curve = mapping.curves[0]
        while len(curve.points) > 2:
            curve.points.remove(curve.points[-2])
        normalized = _normalize_points(points)
        curve.points[0].location = normalized[0]
        curve.points[-1].location = normalized[-1]
        for x, y in normalized[1:-1]:
            curve.points.new(x, y)
        for point in curve.points:
            point.handle_type = "AUTO"
        mapping.update()
    except Exception:  # noqa: BLE001
        pass


def _read_node_points(node) -> tuple[tuple[float, float], ...]:
    try:
        curve = node.mapping.curves[0]
        return _normalize_points(
            [(float(point.location.x), float(point.location.y)) for point in curve.points]
        )
    except Exception:  # noqa: BLE001
        return DEFAULT_POINTS


def _normalize_points(points: Sequence[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    cleaned = [(_clamp01(x), _clamp01(y)) for x, y in points]
    if len(cleaned) < 2:
        return DEFAULT_POINTS
    cleaned.sort(key=lambda point: point[0])
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
    return tuple(deduped)


def _evaluate(points: Sequence[tuple[float, float]], x_value: float) -> float:
    pts = _normalize_points(points)
    x_value = _clamp01(x_value)
    if x_value <= pts[0][0]:
        return pts[0][1]
    for index in range(1, len(pts)):
        x0, y0 = pts[index - 1]
        x1, y1 = pts[index]
        if x_value <= x1:
            span = x1 - x0
            u = 0.0 if span <= 1.0e-8 else (x_value - x0) / span
            return _clamp01(y0 + (y1 - y0) * u)
    return pts[-1][1]


def _points_text(points: Sequence[tuple[float, float]]) -> str:
    return ";".join(f"{x:.4f},{y:.4f}" for x, y in _normalize_points(points))


def _set_attr(settings, attr: str, value: float) -> bool:
    value = _clamp01(value)
    old = float(getattr(settings, attr, value))
    if abs(old - value) < 1.0e-4:
        return False
    setattr(settings, attr, value)
    return True


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
