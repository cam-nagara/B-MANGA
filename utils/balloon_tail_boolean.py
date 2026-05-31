from __future__ import annotations

from typing import Sequence

from . import python_deps


_OVERLAP_M = 1.0e-6
_BOUNDARY_TOUCH_EPS_M = 2.0e-4


def _validate_polygon(poly):
    if poly is None:
        return None
    try:
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.area <= 0.0:
            return None
        return poly
    except Exception:  # noqa: BLE001
        return None


def polygon_from_points(points: Sequence[tuple[float, float]]):
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import Polygon  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    if len(points) < 3:
        return None
    try:
        return _validate_polygon(Polygon(points))
    except Exception:  # noqa: BLE001
        return None


def _largest_polygon(geom):
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "Polygon":
        return geom
    if geom.geom_type == "MultiPolygon":
        geoms = [g for g in geom.geoms if not g.is_empty and g.area > 0.0]
        return max(geoms, key=lambda p: p.area) if geoms else None
    try:
        geoms = [g for g in geom.geoms if getattr(g, "geom_type", "") == "Polygon" and not g.is_empty]
    except Exception:  # noqa: BLE001
        geoms = []
    return max(geoms, key=lambda p: p.area) if geoms else None


def _tail_tip_point(points: Sequence[tuple[float, float]]) -> tuple[float, float] | None:
    if len(points) >= 4:
        mid = len(points) // 2
        p0 = points[max(1, mid - 1)]
        p1 = points[min(len(points) - 2, mid)]
        return ((float(p0[0]) + float(p1[0])) * 0.5, (float(p0[1]) + float(p1[1])) * 0.5)
    if len(points) == 3:
        return (float(points[1][0]), float(points[1][1]))
    return None


def _is_inward_tail(body_poly, tail_poly, points: Sequence[tuple[float, float]]) -> bool:
    tip = _tail_tip_point(points)
    if tip is None:
        return False
    try:
        from shapely.geometry import Point  # type: ignore

        if not body_poly.buffer(_BOUNDARY_TOUCH_EPS_M, join_style=2, mitre_limit=50.0).contains(Point(tip)):
            return False
        if tail_poly.area <= 0.0:
            return False
        intersection_area = body_poly.intersection(tail_poly).area
        outside_area = max(0.0, tail_poly.area - intersection_area)
        touches_boundary = tail_poly.distance(body_poly.boundary) <= _BOUNDARY_TOUCH_EPS_M
        crosses_boundary = outside_area > tail_poly.area * 0.015
        return touches_boundary or crosses_boundary
    except Exception:  # noqa: BLE001
        return False


def combine_body_with_tail_polygons(
    body_points: Sequence[tuple[float, float]],
    tail_points_list: Sequence[Sequence[tuple[float, float]]],
):
    """本体としっぽを合成した Polygon と、合成が行われたかを返す。

    しっぽの大半が本体の内側へ入る場合は、本体から差し引いて凹みとして扱う。
    外側へ伸びる通常のしっぽは従来どおり本体へ結合する。
    """
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.ops import unary_union  # type: ignore
    except Exception:  # noqa: BLE001
        return None, False

    body_poly = polygon_from_points(body_points)
    if body_poly is None:
        return None, False

    outward = []
    inward = []
    for points in tail_points_list:
        tail_poly = polygon_from_points(points)
        if tail_poly is None:
            continue
        if _is_inward_tail(body_poly, tail_poly, points):
            inward.append(tail_poly)
        else:
            outward.append(tail_poly)

    if not outward and not inward:
        return body_poly, False

    result = body_poly
    try:
        if outward:
            parts = [result.buffer(_OVERLAP_M, join_style=2, mitre_limit=50.0)]
            parts.extend(poly.buffer(_OVERLAP_M, join_style=2, mitre_limit=50.0) for poly in outward)
            result = unary_union(parts)
            if result.is_empty:
                return None, False
            result = result.buffer(-_OVERLAP_M, join_style=2, mitre_limit=50.0)
            result = _largest_polygon(result)
            if result is None:
                return None, False
        if inward:
            cuts = unary_union([
                poly.buffer(_OVERLAP_M, join_style=2, mitre_limit=50.0)
                for poly in inward
            ])
            result = result.difference(cuts)
            result = _largest_polygon(result)
            if result is None:
                return None, False
        return result, True
    except Exception:  # noqa: BLE001
        return None, False
