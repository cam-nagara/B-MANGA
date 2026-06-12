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


def split_indices_touching_body(
    body_points: Sequence[tuple[float, float]],
    polys_points_list: Sequence[Sequence[tuple[float, float]]],
) -> tuple[list[int], list[int]]:
    """本体に重なる polygon の index 一覧と、重ならない index 一覧を返す。

    連続楕円しっぽで「本体に重なる楕円は本体へ結合し、残りは個別に描く」
    切り分けに使う。判定不能のときは全件「重ならない」扱い (従来描画)。
    """
    body_poly = polygon_from_points(body_points)
    touching: list[int] = []
    separate: list[int] = []
    for index, points in enumerate(polys_points_list):
        poly = polygon_from_points(points)
        if body_poly is None or poly is None:
            separate.append(index)
            continue
        try:
            (touching if poly.intersects(body_poly) else separate).append(index)
        except Exception:  # noqa: BLE001
            separate.append(index)
    return touching, separate


def mitre_band_polygons(
    outline_points: Sequence[tuple[float, float]],
    outer_offset: float,
    inner_offset: float,
    *,
    sharp: bool = True,
):
    """輪郭から outer_offset / inner_offset (符号付き) のオフセット帯を返す。

    sharp=True で mitre join (角が尖る)、False で round join (角が丸い)。
    「角を尖らせる」やフチをページ出力・サムネイルへ正確に反映するために使う。
    戻り値: [(outer_ring, holes), ...] (失敗時は空リスト)
    """
    python_deps.ensure_bundled_wheels_on_path()
    poly = polygon_from_points(outline_points)
    if poly is None or outer_offset <= inner_offset:
        return []
    join = 2 if sharp else 1
    try:
        outer = (
            poly.buffer(outer_offset, join_style=join, mitre_limit=50.0)
            if abs(outer_offset) > 1.0e-12
            else poly
        )
        inner = (
            poly.buffer(inner_offset, join_style=join, mitre_limit=50.0)
            if abs(inner_offset) > 1.0e-12
            else poly
        )
        band = outer.difference(inner)
        if band.is_empty:
            return []
    except Exception:  # noqa: BLE001
        return []
    out = []
    geoms = [band] if band.geom_type == "Polygon" else list(getattr(band, "geoms", []))
    for g in geoms:
        if getattr(g, "geom_type", "") != "Polygon" or g.is_empty or g.area <= 0.0:
            continue
        outer_ring = [(float(x), float(y)) for x, y in g.exterior.coords[:-1]]
        if len(outer_ring) < 3:
            continue
        holes = []
        for interior in g.interiors:
            ring = [(float(x), float(y)) for x, y in interior.coords[:-1]]
            if len(ring) >= 3:
                holes.append(ring)
        out.append((outer_ring, holes))
    return out


def sharp_corner_patch_polygons(
    outline_points: Sequence[tuple[float, float]],
    region_points_list: Sequence[Sequence[tuple[float, float]]],
    line_width: float,
    *,
    centered: bool = True,
):
    """「角を尖らせる」しっぽ用の mitre 差分パッチを返す。

    round join で描いた主線の上に重ねると、しっぽ周辺の角 (先端・折れ角・
    本体との付け根) だけが鋭く尖って見える。座標と線幅は同一単位なら何でもよい。

    centered=True: 主線が輪郭の中心に乗る描き方 (出力側)。
    centered=False: 主線が輪郭の外側へ広がる描き方 (ビューポート側)。

    戻り値: [(outer_ring, holes), ...] (失敗・対象なしのときは空リスト)
    """
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.ops import unary_union  # type: ignore
    except Exception:  # noqa: BLE001
        return []
    width = float(line_width)
    if width <= 0.0:
        return []
    poly = polygon_from_points(outline_points)
    if poly is None:
        return []
    outer_off = width * 0.5 if centered else width
    inner_off = -width * 0.5 if centered else 0.0
    try:
        mitre_outer = poly.buffer(outer_off, join_style=2, mitre_limit=50.0)
        if abs(inner_off) > 1.0e-12:
            mitre_inner = poly.buffer(inner_off, join_style=2, mitre_limit=50.0)
        else:
            mitre_inner = poly
        # round join との「差分スライバー」ではなく、しっぽ周辺の mitre 帯全体を
        # 返して round join の主線の上へそのまま重ねる。差分方式だとラスタライズの
        # 丸め誤差で主線との間に白い継ぎ目が出るため。
        band_mitre = mitre_outer.difference(mitre_inner)
        regions = []
        for points in region_points_list:
            region_poly = polygon_from_points(points)
            if region_poly is not None:
                regions.append(region_poly.buffer(width * 2.0, join_style=1))
        patch = band_mitre.intersection(unary_union(regions)) if regions else band_mitre
        if patch.is_empty:
            return []
    except Exception:  # noqa: BLE001
        return []
    out = []
    geoms = [patch] if patch.geom_type == "Polygon" else list(getattr(patch, "geoms", []))
    for g in geoms:
        if getattr(g, "geom_type", "") != "Polygon" or g.is_empty or g.area <= 0.0:
            continue
        outer = [(float(x), float(y)) for x, y in g.exterior.coords[:-1]]
        if len(outer) < 3:
            continue
        holes = []
        for interior in g.interiors:
            ring = [(float(x), float(y)) for x, y in interior.coords[:-1]]
            if len(ring) >= 3:
                holes.append(ring)
        out.append((outer, holes))
    return out


def combine_body_with_tail_polygons(
    body_points: Sequence[tuple[float, float]],
    tail_points_list: Sequence[Sequence[tuple[float, float]]],
    union_only_points_list: Sequence[Sequence[tuple[float, float]]] = (),
):
    """本体としっぽを合成した Polygon と、合成が行われたかを返す。

    しっぽの大半が本体の内側へ入る場合は、本体から差し引いて凹みとして扱う。
    外側へ伸びる通常のしっぽは従来どおり本体へ結合する。

    union_only_points_list (連続楕円しっぽの楕円など) は内外の判定をせず、
    本体に重なっているものだけを常に結合する (重ならないものは無視)。
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

    for points in union_only_points_list:
        poly = polygon_from_points(points)
        if poly is None:
            continue
        try:
            if poly.intersects(body_poly):
                outward.append(poly)
        except Exception:  # noqa: BLE001
            continue

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
