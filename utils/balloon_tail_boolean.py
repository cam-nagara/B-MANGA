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


def _geom_to_ring_list(geom):
    """Shapely Polygon/MultiPolygon を [(outer, holes), ...] に変換する."""
    if geom is None or geom.is_empty:
        return []
    geoms = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
    out = []
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


def apply_sharp_tail_tips(
    band_rings,
    outline_points: Sequence[tuple[float, float]],
    line_width: float,
    sharp_tails,
    *,
    add_bend_mitre: bool = True,
):
    """「角を尖らせる」しっぽの先端を、ペンの抜きのように細く絞った帯へ加工する。

    band_rings: 主線の帯 [(outer, holes), ...] (本体輪郭の外側 0..line_width)。
    sharp_tails: [(centerline_pts, halfwidths, region_pts), ...]
      - centerline_pts: しっぽ中心線 (band と同じ座標系・単位)
      - halfwidths: 各点のくさび半幅 (同単位)
      - region_pts: しっぽのくさび多角形 (折れ角を mitre で尖らせる範囲)
    add_bend_mitre=True なら、しっぽ周辺の折れ角・付け根を mitre で尖らせる
    (本体側の「角を尖らせる」が ON なら帯全体が mitre 済みのため不要)。

    失敗時は band_rings をそのまま返す。
    """
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import LineString, Polygon  # type: ignore
        from shapely.ops import unary_union  # type: ignore
    except Exception:  # noqa: BLE001
        return band_rings
    try:
        from .balloon_tail_geom import _variable_width_stroke_polygon
    except Exception:  # noqa: BLE001
        return band_rings
    w = float(line_width)
    if w <= 0.0 or not sharp_tails:
        return band_rings
    try:
        polys = []
        for outer, holes in band_rings:
            p = Polygon(outer, holes)
            if not p.is_valid:
                p = p.buffer(0)
            if not p.is_empty:
                polys.append(p)
        if not polys:
            return band_rings
        band = unary_union(polys)
    except Exception:  # noqa: BLE001
        return band_rings
    outline_poly = polygon_from_points(outline_points)
    mitre_band_geom = None
    changed = False
    for centerline, halfwidths, region_pts in sharp_tails:
        pts = [(float(x), float(y)) for x, y in centerline]
        if len(pts) < 2:
            continue
        tip_half = float(halfwidths[-1]) if halfwidths else 0.0
        tip = pts[-1]
        prev = pts[-2]
        seg = ((tip[0] - prev[0]) ** 2 + (tip[1] - prev[1]) ** 2) ** 0.5
        if seg <= 1.0e-12:
            continue
        dir_x = (tip[0] - prev[0]) / seg
        dir_y = (tip[1] - prev[1]) / seg
        try:
            # 先端の平面 (先端点を通る進行方向に垂直な面) より先の帯を除去する。
            # round join の丸キャップも mitre のトゲ状の延長もここで消える。
            corridor = LineString(
                [tip, (tip[0] + dir_x * w * 60.0, tip[1] + dir_y * w * 60.0)]
            ).buffer(w * 4.0, cap_style=2)
            band = band.difference(corridor)
            if add_bend_mitre and outline_poly is not None and len(region_pts) >= 3:
                region_poly = polygon_from_points(region_pts)
                if region_poly is not None:
                    if mitre_band_geom is None:
                        mitre_band_geom = outline_poly.buffer(
                            w, join_style=2, mitre_limit=50.0
                        ).difference(outline_poly)
                    band = band.union(
                        mitre_band_geom.intersection(
                            region_poly.buffer(w * 2.0, join_style=1).difference(corridor)
                        )
                    )
            # 先端から短く「抜く」: 切断面の幅 → 0 へ絞る延長を付ける (ペンの抜き)
            ext_len = w * 2.5
            start_half = tip_half + w
            ext_pts = [tip, (tip[0] + dir_x * ext_len, tip[1] + dir_y * ext_len)]
            taper_pts = _variable_width_stroke_polygon(
                ext_pts, [0.0, ext_len], ext_len, lambda t: max(0.0, start_half * (1.0 - t))
            )
            if len(taper_pts) >= 3:
                taper_poly = Polygon(taper_pts)
                if not taper_poly.is_valid:
                    taper_poly = taper_poly.buffer(0)
                if not taper_poly.is_empty:
                    band = band.union(taper_poly)
            changed = True
        except Exception:  # noqa: BLE001
            continue
    if not changed:
        return band_rings
    rings = _geom_to_ring_list(band)
    return rings if rings else band_rings


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
