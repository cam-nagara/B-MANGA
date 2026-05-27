"""フキダシ主線をメッシュバンド方式で直接構築する.

雲・モフモフ・トゲ曲線のような滑らか形状で、外周と内周の点ペアから
四角形ストリップのメッシュを直接構築する。

雲: 線形状を本体カーブと独立した滑らかな閉曲線として直接生成する。
本体カーブが谷で鋭角を持っていても、線形状側は谷で楕円接線方向に
handles を揃えるため、必ず滑らかにつながる (offset 方式で必ず生じる
谷の重なりを根本回避)。

モフモフ・トゲ曲線: 本体カーブの中心線サンプル点列から、谷の鋭角を半径 R の
小さな円弧で事前に丸めてから外周/内周にオフセットする (offset 方式)。

線幅は本体 Bezier の per-point radius を補間で反映する。
コマ枠 (coma_border) と同じ作り方で、Curve+FillCurve ではなく Mesh 直接構築。
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

import bpy

from . import balloon_shapes
from . import log
from . import object_naming as on
from . import python_deps
from .geom import Rect, mm_to_m

_logger = log.get_logger(__name__)

BALLOON_LINE_MESH_NAME_PREFIX = "balloon_line_mesh_"
BALLOON_OUTER_EDGE_MESH_NAME_PREFIX = "balloon_outer_edge_mesh_"
BALLOON_INNER_EDGE_MESH_NAME_PREFIX = "balloon_inner_edge_mesh_"
BALLOON_MULTI_LINE_MESH_NAME_PREFIX = "balloon_multi_line_mesh_"
PROP_BALLOON_LINE_MESH_KIND = "bname_balloon_line_mesh_kind"
PROP_BALLOON_LINE_MESH_OWNER_ID = "bname_balloon_line_mesh_owner_id"

# kind タグ
_KIND_LINE = "balloon_line_mesh"
_KIND_OUTER_EDGE = "balloon_outer_edge_mesh"
_KIND_INNER_EDGE = "balloon_inner_edge_mesh"
_KIND_MULTI_LINE = "balloon_multi_line_mesh"
_ALL_KINDS = {_KIND_LINE, _KIND_OUTER_EDGE, _KIND_INNER_EDGE, _KIND_MULTI_LINE}

SAMPLES_PER_SEGMENT = 24
SHARP_THRESHOLD_RAD = math.radians(30.0)
ARC_STEP_DEG = 12.0
LINE_Z_OFFSET_M = 0.00010

# 「角を尖らせる」(mitre join) で鋭角頂点が太線時に bevel 切りされないよう、
# Shapely の mitre_limit を十分大きく取る。50 で約 1.15° まで保持される。
_SHARP_MITRE_LIMIT = 50.0
_ROUND_MITRE_LIMIT = 5.0
OUTER_EDGE_Z_OFFSET_M = 0.000020
INNER_EDGE_Z_OFFSET_M = 0.000040
MULTI_LINE_Z_OFFSET_M = 0.000080

_EDGE_OVERLAP_RATIO = 0.06

# コマ内マスク用 Geometry Nodes group
MASK_CLIP_GROUP_NAME = "BName_GN_BalloonLineMeshClip"
MASK_CLIP_GROUP_VERSION = 1
PROP_GROUP_VERSION = "bname_group_version"

# 主線・外側フチ・内側フチを Shapely buffer + earcut で外部 Mesh として描画する形状。
# 全ての Meldex フキダシ形状で同じ方式に統一する。
SHAPELY_LINE_SHAPES = set(balloon_shapes.MELDEX_CARD_SHAPES)

# 後方互換 (Mesh 直接構築方式で主線が描画される形状)
MESH_BAND_LINE_SHAPES = set(SHAPELY_LINE_SHAPES)

# 多重線も Shapely buffer 方式で外部 Mesh として描画する形状。
# 全 Meldex 形状で統一: 角の鋭い形状でもオフセット曲線の自己交差/ごちゃつきや
# 意図しないトゲが出ないように Shapely buffer に統一する。
# (トゲ直線専用の「長さ変化」「谷/山の線幅」は本経路では適用されない — 形状が
# 谷で自己交差しないリングを優先する設計判断)
SHAPELY_MULTI_LINE_SHAPES = set(balloon_shapes.MELDEX_CARD_SHAPES)


def is_mesh_band_shape(entry) -> bool:
    """主線を Mesh 直接構築方式で描画する形状か."""
    shape = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect"))
    return shape in MESH_BAND_LINE_SHAPES


def is_shapely_line_shape(entry) -> bool:
    """主線・外側フチ・内側フチを Shapely buffer 方式で描画する形状か."""
    shape = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect"))
    return shape in SHAPELY_LINE_SHAPES


def is_shapely_multi_line_shape(entry) -> bool:
    """多重線を Shapely buffer 方式で描画する形状か."""
    shape = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect"))
    return shape in SHAPELY_MULTI_LINE_SHAPES


def is_shapely_band_shape(entry) -> bool:
    """後方互換 alias. 主線・フチ・多重線のどれかが Shapely 化される形状か."""
    return is_shapely_line_shape(entry) or is_shapely_multi_line_shape(entry)


def _cubic_bezier_point(p0, p1, p2, p3, t):
    u = 1.0 - t
    return (
        u * u * u * p0[0] + 3.0 * u * u * t * p1[0] + 3.0 * u * t * t * p2[0] + t * t * t * p3[0],
        u * u * u * p0[1] + 3.0 * u * u * t * p1[1] + 3.0 * u * t * t * p2[1] + t * t * t * p3[1],
    )


def _entry_local_offset_mm(entry) -> tuple[float, float]:
    """entry の本体カーブ origin から見た rect ローカル原点のオフセット (mm).

    balloon_curve_object._entry_curve_offset と同じ計算. rect 内ローカル mm 座標に
    このオフセットを加えると balloon-local mm 座標になる.
    """
    return (
        float(getattr(entry, "center_offset_x_mm", 0.0) or 0.0)
        - max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0)) * 0.5,
        float(getattr(entry, "center_offset_y_mm", 0.0) or 0.0)
        - max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0)) * 0.5,
    )


def _body_per_anchor_radii(spline) -> list[float]:
    """本体 Bezier の各 anchor の radius (per-point) を順に返す."""
    pts = list(getattr(spline, "bezier_points", []) or [])
    return [max(0.0, float(getattr(p, "radius", 1.0) or 0.0)) for p in pts]


def _sample_anchor_loop_to_local_m(
    anchors: Sequence[balloon_shapes.BezierAnchor],
    offset_mm: tuple[float, float],
    samples_per_segment: int,
) -> list[tuple[float, float]]:
    """閉じた BezierAnchor 列を samples_per_segment 段でサンプリングし、
    rect-local mm → balloon-local m に変換した (x, y) 列を返す."""
    n = len(anchors)
    if n < 3:
        return []
    steps = max(4, int(samples_per_segment))
    ox, oy = offset_mm
    out: list[tuple[float, float]] = []
    for index, anchor in enumerate(anchors):
        nxt = anchors[(index + 1) % n]
        p0 = anchor.co
        p1 = anchor.handle_right if anchor.handle_right is not None else anchor.co
        p2 = nxt.handle_left if nxt.handle_left is not None else nxt.co
        p3 = nxt.co
        for step in range(steps):
            t = step / steps
            x_mm, y_mm = _cubic_bezier_point(p0, p1, p2, p3, t)
            out.append((mm_to_m(x_mm + ox), mm_to_m(y_mm + oy)))
    return out


def _sample_body_bezier(spline, samples_per_segment: int) -> list[tuple[float, float, float]]:
    """Bezier 閉スプラインをサンプリングして、(x, y, per_point_radius) のタプル列を返す."""
    samples: list[tuple[float, float, float]] = []
    if str(getattr(spline, "type", "") or "") != "BEZIER":
        return samples
    if not bool(getattr(spline, "use_cyclic_u", False)):
        return samples
    points = list(getattr(spline, "bezier_points", []) or [])
    n = len(points)
    if n < 3:
        return samples
    steps = max(4, int(samples_per_segment))
    for i in range(n):
        a = points[i]
        b = points[(i + 1) % n]
        p0 = (float(a.co.x), float(a.co.y))
        p1 = (float(a.handle_right.x), float(a.handle_right.y))
        p2 = (float(b.handle_left.x), float(b.handle_left.y))
        p3 = (float(b.co.x), float(b.co.y))
        r0 = max(0.0, float(getattr(a, "radius", 1.0) or 0.0))
        r1 = max(0.0, float(getattr(b, "radius", 1.0) or 0.0))
        for step in range(steps):
            t = step / steps
            pos = _cubic_bezier_point(p0, p1, p2, p3, t)
            radius = r0 * (1.0 - t) + r1 * t
            samples.append((pos[0], pos[1], radius))
    return samples


def _smooth_sharp_corners(
    samples: Sequence[tuple[float, float, float]],
    *,
    smooth_radius_m: float,
    sharp_threshold_rad: float,
    arc_step_deg: float,
) -> list[tuple[float, float, float]]:
    """サンプル点列の鋭角を、半径 smooth_radius_m のフィレット円弧で置き換える.

    各円弧点の radius は元の鋭角点の radius を引き継ぐ。
    """
    n = len(samples)
    if n < 3 or smooth_radius_m <= 1.0e-9:
        return list(samples)
    arc_step = math.radians(max(1.0, arc_step_deg))
    result: list[tuple[float, float, float]] = []
    for i in range(n):
        prev_s = samples[(i - 1) % n]
        curr_s = samples[i]
        next_s = samples[(i + 1) % n]
        ax, ay = curr_s[0] - prev_s[0], curr_s[1] - prev_s[1]
        bx, by = next_s[0] - curr_s[0], next_s[1] - curr_s[1]
        la = math.hypot(ax, ay)
        lb = math.hypot(bx, by)
        if la <= 1.0e-9 or lb <= 1.0e-9:
            result.append(curr_s)
            continue
        cross = ax * by - ay * bx
        dot = ax * bx + ay * by
        delta = math.atan2(cross, dot)
        if abs(delta) <= sharp_threshold_rad:
            result.append(curr_s)
            continue
        ext = abs(delta)
        if ext >= math.pi - 0.02:
            result.append(curr_s)
            continue
        td = smooth_radius_m / math.tan(ext / 2.0)
        td = min(td, la * 0.49, lb * 0.49)
        if td <= 1.0e-9:
            result.append(curr_s)
            continue
        r_eff = td * math.tan(ext / 2.0)
        a_dx, a_dy = ax / la, ay / la
        b_dx, b_dy = bx / lb, by / lb
        tp_prev = (curr_s[0] - a_dx * td, curr_s[1] - a_dy * td)
        if delta > 0:
            n_inside = (-a_dy, a_dx)
        else:
            n_inside = (a_dy, -a_dx)
        center = (tp_prev[0] + n_inside[0] * r_eff, tp_prev[1] + n_inside[1] * r_eff)
        v0 = (tp_prev[0] - center[0], tp_prev[1] - center[1])
        steps = max(2, int(math.ceil(ext / arc_step)))
        for s in range(steps + 1):
            t = s / steps
            theta = delta * t
            cos_t = math.cos(theta)
            sin_t = math.sin(theta)
            vx = v0[0] * cos_t - v0[1] * sin_t
            vy = v0[0] * sin_t + v0[1] * cos_t
            result.append((center[0] + vx, center[1] + vy, curr_s[2]))
    return result


def _build_body_polygon(samples):
    """サンプル列を shapely Polygon に変換する (失敗時 None)."""
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import Polygon  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    if len(samples) < 3:
        return None
    body_pts = [(float(s[0]), float(s[1])) for s in samples]
    try:
        body_poly = Polygon(body_pts)
        if not body_poly.is_valid:
            body_poly = body_poly.buffer(0)
        if body_poly.is_empty or body_poly.area <= 0:
            return None
        return body_poly
    except Exception:  # noqa: BLE001
        return None


def build_offset_band_polygon(
    body_samples: Sequence,
    *,
    signed_offset_m: float,
    band_width_m: float,
    valley_sharp: bool,
    miter_limit: float = _SHARP_MITRE_LIMIT,
    _body_poly=None,
) -> Optional[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]:
    """本体多角形から signed_offset_m を中心に幅 band_width_m の帯を構築する.

    signed_offset_m: 正=本体の外側へ、負=本体の内側へ。
    band_width_m: 帯の幅 (常に正)。
    valley_sharp=True で mitre join (谷で鋭角), False で round join (谷で丸み).

    戻り値: (outer_ring, holes) の対。失敗時 None。
    """
    if band_width_m <= 1.0e-9:
        return None
    body_poly = _body_poly if _body_poly is not None else _build_body_polygon(body_samples)
    if body_poly is None:
        return None

    join = 2 if valley_sharp else 1  # 1=round, 2=mitre, 3=bevel
    mitre = float(miter_limit) if valley_sharp else _ROUND_MITRE_LIMIT
    half = band_width_m * 0.5
    try:
        outer_buf = body_poly.buffer(
            signed_offset_m + half,
            join_style=join,
            mitre_limit=mitre,
        )
        inner_buf = body_poly.buffer(
            signed_offset_m - half,
            join_style=join,
            mitre_limit=mitre,
        )
        band = outer_buf.difference(inner_buf)
    except Exception:  # noqa: BLE001
        return None

    if band.is_empty:
        return None
    if band.geom_type == "Polygon":
        geoms = [band]
    elif band.geom_type == "MultiPolygon":
        geoms = list(band.geoms)
    else:
        return None
    # 最大面積のポリゴンを採用 (通常は band 全体が単一)
    main = max(geoms, key=lambda g: g.area)
    outer_ring = list(main.exterior.coords)
    holes = [list(r.coords) for r in main.interiors]
    return outer_ring, holes


def _stroke_band_outside_union(
    samples: Sequence[tuple[float, float, float]],
    *,
    line_width_m: float,
    valley_sharp: bool,
    miter_limit: float = _SHARP_MITRE_LIMIT,
) -> Optional[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]:
    """主線 (外側アライメント) の線バンドを Shapely buffer で構築する."""
    return build_offset_band_polygon(
        samples,
        signed_offset_m=line_width_m * 0.5,
        band_width_m=line_width_m,
        valley_sharp=valley_sharp,
        miter_limit=miter_limit,
    )


def _polyline_subset_by_arc_length(
    pts_closed: Sequence[tuple[float, float]],
    cum: Sequence[float],
    start_len: float,
    end_len: float,
) -> list[tuple[float, float]]:
    """閉じた点列 (`pts_closed`: 末尾に先頭を再付加した、cum: 累積長) のうち、
    `start_len` 〜 `end_len` の区間を切り出して返す.

    端点では線分上を補間し、滑らかに区間が始まる/終わるようにする。
    閉じた線で `end_len` が一周を超えるケースは呼び出し側で個別に処理する想定。
    """
    n = len(pts_closed)
    if n < 2:
        return []
    total = cum[-1]
    if total <= 1.0e-9:
        return []
    start_len = max(0.0, float(start_len))
    end_len = max(start_len, float(end_len))
    if end_len > total:
        end_len = total
    out: list[tuple[float, float]] = []
    for i in range(n - 1):
        seg_start = cum[i]
        seg_end = cum[i + 1]
        if seg_end < start_len or seg_start > end_len:
            continue
        seg_len = seg_end - seg_start
        if seg_len <= 1.0e-12:
            continue
        p0 = pts_closed[i]
        p1 = pts_closed[i + 1]
        # 区間と segment の交差点を計算
        t_in = (max(seg_start, start_len) - seg_start) / seg_len
        t_out = (min(seg_end, end_len) - seg_start) / seg_len
        if t_in > 0.0:
            x = p0[0] + (p1[0] - p0[0]) * t_in
            y = p0[1] + (p1[1] - p0[1]) * t_in
            if not out or math.hypot(out[-1][0] - x, out[-1][1] - y) > 1.0e-9:
                out.append((x, y))
        else:
            if not out or math.hypot(out[-1][0] - p0[0], out[-1][1] - p0[1]) > 1.0e-9:
                out.append((float(p0[0]), float(p0[1])))
        if t_out < 1.0:
            x = p0[0] + (p1[0] - p0[0]) * t_out
            y = p0[1] + (p1[1] - p0[1]) * t_out
            if not out or math.hypot(out[-1][0] - x, out[-1][1] - y) > 1.0e-9:
                out.append((x, y))
    return out


def _build_dashed_band_polygons(
    body_samples: Sequence[tuple[float, float, float]],
    *,
    line_width_m: float,
    line_style: str,
    valley_sharp: bool,
) -> list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]:
    """主線を破線または点線として、複数の独立バンドポリゴンとして構築する.

    実装手順:
    1. body 多角形を `line_width/2` だけ外側 buffer して、主線中心線のリングを得る
    2. リング外周を arc length 軸でサンプリング
    3. dash 周期に従って区間を切り出し、各区間を LineString として `line_width/2` で
       buffer すれば、外側アライメント主線の dash バンドが得られる
    4. 各 dash バンドを (outer_ring, holes) のタプルとして返す
    """
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import LineString  # type: ignore
    except Exception:  # noqa: BLE001
        return []
    if line_width_m <= 1.0e-9 or len(body_samples) < 3:
        return []
    body_poly = _build_body_polygon(body_samples)
    if body_poly is None:
        return []
    join = 2 if valley_sharp else 1
    try:
        centerline_poly = body_poly.buffer(
            line_width_m * 0.5,
            join_style=join,
            mitre_limit=_SHARP_MITRE_LIMIT if valley_sharp else _ROUND_MITRE_LIMIT,
        )
    except Exception:  # noqa: BLE001
        return []
    if centerline_poly.is_empty:
        return []
    # 最大ポリゴンの外周をセンターラインとして採用
    if centerline_poly.geom_type == "Polygon":
        geom = centerline_poly
    elif centerline_poly.geom_type == "MultiPolygon":
        geom = max(centerline_poly.geoms, key=lambda g: g.area)
    else:
        return []
    ring_coords = list(geom.exterior.coords)
    if len(ring_coords) < 4:
        return []
    # 閉じた列に変形 (末尾を先頭に重ねる)
    pts = [(float(x), float(y)) for x, y in ring_coords]
    if math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) > 1.0e-9:
        pts.append(pts[0])
    cum = [0.0]
    for i in range(1, len(pts)):
        dx = pts[i][0] - pts[i - 1][0]
        dy = pts[i][1] - pts[i - 1][1]
        cum.append(cum[-1] + math.hypot(dx, dy))
    total_len = cum[-1]
    if total_len <= 1.0e-9:
        return []

    line_width_mm = line_width_m * 1000.0
    if line_style == "dotted":
        # 点線: 小さな丸ドット (直径 ≈ line_width)。周期は line_width の 2 倍前後。
        target_period_mm = max(line_width_mm * 2.0, 0.5)
        dash_ratio = 0.15
        cap_style = 1  # round
    else:  # dashed
        # 破線: 線幅の 8 倍 (最低 6mm) を 1 周期にし、その 6 割を dash として
        # はっきりした「線・空白・線・空白」のパターンになるようにする。
        target_period_mm = max(line_width_mm * 8.0, 6.0)
        dash_ratio = 0.6
        cap_style = 2  # flat
    target_period_m = target_period_mm * 0.001
    num_periods = max(1, int(round(total_len / target_period_m)))
    period_len = total_len / num_periods
    dash_len = period_len * dash_ratio

    half_width_m = line_width_m * 0.5
    polygons: list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]] = []
    for k in range(num_periods):
        start_len = k * period_len
        end_len = start_len + dash_len
        sub_pts = _polyline_subset_by_arc_length(pts, cum, start_len, end_len)
        if len(sub_pts) < 2:
            continue
        try:
            line = LineString(sub_pts)
            if line.length <= 1.0e-9:
                continue
            band = line.buffer(half_width_m, cap_style=cap_style, join_style=1)
        except Exception:  # noqa: BLE001
            continue
        if band.is_empty:
            continue
        if band.geom_type == "Polygon":
            sub_polys = [band]
        elif band.geom_type == "MultiPolygon":
            sub_polys = list(band.geoms)
        else:
            continue
        for sub in sub_polys:
            if sub.area <= 1.0e-12:
                continue
            outer_ring = list(sub.exterior.coords)
            holes = [list(r.coords) for r in sub.interiors]
            polygons.append((outer_ring, holes))
    return polygons


def _polyline_outward_normals(
    pts: Sequence[tuple[float, float]],
    *,
    closed: bool,
    balloon_center: tuple[float, float] | None = None,
) -> list[tuple[float, float]]:
    """各点での外向き法線 (bisector の単位ベクトル) を返す.

    `closed=True` の場合は閉ループとして前後の点を環状に参照する。
    `balloon_center` が与えられたら radial が外向きを示すように符号を揃える。
    """
    n = len(pts)
    normals: list[tuple[float, float]] = []
    if n < 2:
        return [(1.0, 0.0)] * n
    for i in range(n):
        if closed:
            prev_p = pts[(i - 1) % n]
            next_p = pts[(i + 1) % n]
        else:
            prev_p = pts[max(0, i - 1)]
            next_p = pts[min(n - 1, i + 1)]
        dx_prev = pts[i][0] - prev_p[0]
        dy_prev = pts[i][1] - prev_p[1]
        dx_next = next_p[0] - pts[i][0]
        dy_next = next_p[1] - pts[i][1]
        l_prev = math.hypot(dx_prev, dy_prev)
        l_next = math.hypot(dx_next, dy_next)
        if l_prev > 1.0e-12:
            dx_prev /= l_prev
            dy_prev /= l_prev
        if l_next > 1.0e-12:
            dx_next /= l_next
            dy_next /= l_next
        # 接線の平均を取り、それに垂直な単位ベクトルを法線とする
        tx = dx_prev + dx_next
        ty = dy_prev + dy_next
        tlen = math.hypot(tx, ty)
        if tlen < 1.0e-12:
            # 折り返しに近い (両接線がほぼ反対): 適当な垂直
            tx, ty = dx_next, dy_next
            tlen = math.hypot(tx, ty) or 1.0
        tx /= tlen
        ty /= tlen
        nx = -ty
        ny = tx
        if balloon_center is not None:
            rx = pts[i][0] - balloon_center[0]
            ry = pts[i][1] - balloon_center[1]
            if nx * rx + ny * ry < 0.0:
                nx = -nx
                ny = -ny
        normals.append((nx, ny))
    return normals


def _detect_centerline_peaks_valleys(
    pts: Sequence[tuple[float, float]],
    balloon_center: tuple[float, float],
    *,
    expected_count: int,
) -> tuple[list[int], list[int]]:
    """閉じた centerline 点列上で、balloon 中心からの radial 距離の局所最大 (山) と
    局所最小 (谷) のインデックスを返す。

    `expected_count` はおおよそ予想される山数 (= 多重線でのリング期待値)。これに基づき
    検出ウィンドウ幅を決める。
    """
    n = len(pts)
    if n < 6:
        return [], []
    cx, cy = balloon_center
    radii = [math.hypot(p[0] - cx, p[1] - cy) for p in pts]
    if expected_count > 0:
        # 1 ピーク区間あたりの推定インデックス幅 / 3 を比較ウィンドウとする
        window = max(2, int(n / max(1, expected_count) / 3))
    else:
        window = max(2, n // 30)
    peaks: list[int] = []
    valleys: list[int] = []
    for i in range(n):
        is_peak = True
        is_valley = True
        for j in range(1, window + 1):
            r_minus = radii[(i - j) % n]
            r_plus = radii[(i + j) % n]
            if radii[i] < r_minus or radii[i] < r_plus:
                is_peak = False
            if radii[i] > r_minus or radii[i] > r_plus:
                is_valley = False
            if not is_peak and not is_valley:
                break
        if is_peak:
            peaks.append(i)
        elif is_valley:
            valleys.append(i)
    return peaks, valleys


def _circular_dist(a: int, b: int, n: int) -> int:
    d = abs(a - b) % n
    return min(d, n - d)


def _build_variable_width_band_segment(
    pts: Sequence[tuple[float, float]],
    widths: Sequence[float],
    normals: Sequence[tuple[float, float]],
    indices: Sequence[int],
    *,
    closed: bool,
) -> tuple[list[tuple[float, float]], list[list[tuple[float, float]]]] | None:
    """指定インデックス列 (`indices`) の centerline に沿って、外側 = +normal*width/2,
    内側 = -normal*width/2 の帯ポリゴンを構築する。

    closed=True: outer (CCW) と inner (CW = hole) のホール付きポリゴン。
    closed=False: outer + 逆順 inner を 1 つの閉曲線として返す (キャップは平らな端)。
    """
    m = len(indices)
    if m < 2:
        return None
    outer: list[tuple[float, float]] = []
    inner: list[tuple[float, float]] = []
    for idx in indices:
        p = pts[idx]
        nx, ny = normals[idx]
        half = widths[idx] * 0.5
        outer.append((p[0] + nx * half, p[1] + ny * half))
        inner.append((p[0] - nx * half, p[1] - ny * half))
    if closed:
        # outer (CCW), inner (CW = hole)
        outer_ring = outer
        hole = list(reversed(inner))
        return outer_ring, [hole]
    # Open segment: outer + reversed inner で 1 つの閉ポリゴンを作る (平キャップ)
    polygon = list(outer) + list(reversed(inner))
    return polygon, []


def _ring_kept_index_segments(
    n: int,
    peak_indices: Sequence[int],
    valley_indices: Sequence[int],
    length_scale: float,
) -> list[list[int]]:
    """length_scale 適用後、centerline 上で残すインデックス区間群を返す。

    谷を基準とし、山の頂点側を削る方式 (設計意図書 7.1.1):
      - length_scale=1.0: 全周 1 区間として返す
      - length_scale<1.0: 各山の頂点を中心に (1-length_scale)×隣接谷までの距離 だけ
        切り取り、残った範囲を区間として返す。隣接する谷から山方向へ length_scale ぶん
        だけ伸びる帯になる。
    """
    if length_scale >= 0.999 or not peak_indices:
        return [list(range(n))]
    cut_factor = max(0.0, 1.0 - float(length_scale))
    cut_mask = [True] * n  # True = keep
    for peak in peak_indices:
        if valley_indices:
            left_distances = [(peak - v) % n for v in valley_indices]
            right_distances = [(v - peak) % n for v in valley_indices]
            left_d = min((d for d in left_distances if d > 0), default=n // 4)
            right_d = min((d for d in right_distances if d > 0), default=n // 4)
        else:
            others = [p for p in peak_indices if p != peak]
            if others:
                left_distances = [(peak - p) % n for p in others]
                right_distances = [(p - peak) % n for p in others]
                left_d = min((d for d in left_distances if d > 0), default=n // 2) // 2
                right_d = min((d for d in right_distances if d > 0), default=n // 2) // 2
            else:
                left_d = right_d = n // 4
        cut_left = int(round(left_d * cut_factor))
        cut_right = int(round(right_d * cut_factor))
        for j in range(cut_left + 1):
            cut_mask[(peak - j) % n] = False
        for j in range(cut_right + 1):
            cut_mask[(peak + j) % n] = False
    if not any(cut_mask):
        return []
    if all(cut_mask):
        return [list(range(n))]
    # 連続する keep 範囲を取り出す (ラップ対応)
    start = 0
    while start < n and not cut_mask[start]:
        start += 1
    # start は最初の "keep" インデックス
    # ただし全周走査でラップする可能性があるので、start 以前の連続 keep を探して合流させる
    pre = 0
    while pre < n and cut_mask[(start - 1 - pre) % n]:
        pre += 1
    # ラップ込みの実際の start
    real_start = (start - pre) % n
    segments: list[list[int]] = []
    current: list[int] = []
    for k in range(n):
        idx = (real_start + k) % n
        if cut_mask[idx]:
            current.append(idx)
        else:
            if current:
                segments.append(current)
                current = []
    if current:
        segments.append(current)
    return segments


def _build_dynamic_multi_line_polygons(
    *,
    body_samples: Sequence[tuple[float, float, float]],
    signed_offset_m: float,
    base_width_m: float,
    valley_width_m: float,
    peak_width_m: float,
    length_scale: float,
    valley_sharp: bool,
    balloon_center_m: tuple[float, float],
    cross_extension_m: float = 0.0,
    peak_extension_m: float = 0.0,
) -> list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]:
    """動的形状 (cloud/fluffy/thorn/thorn-curve) の主線/多重線 1 リング分を、
    谷/山可変幅 + 長さ変化を反映した複数バンドポリゴンとして構築する。

    手順:
    1. body samples (= 本体カーブそのもの) を centerline として使う。これにより
       本体カーブが谷で鋭く尖っていればそのまま尖り、buffer 経由で発生していた
       「主線が外側にビョーンと飛び出す + 谷が丸まる」現象を回避する。
    2. samples の各点で前後 bisector に垂直な外向き法線を求める。
    3. samples を法線方向に `signed_offset_m` だけシフトしてオフセット centerline。
    4. peaks/valleys を元 samples 上の radial 距離で検出 (大山だけでなく小山も拾う)。
    5. 各点での line width = lerp(valley_w, peak_w, t) を radial peak/valley 距離で計算。
    6. centerline ± normal*width/2 で外周/内周を構築。
    7. length_scale<1.0 なら 谷を起点に keep 区間を切り出し open polygon にする。
    """
    if base_width_m <= 1.0e-9:
        return []
    if len(body_samples) < 6:
        return []
    pts = [(float(s[0]), float(s[1])) for s in body_samples]
    n = len(pts)
    # 外向き法線 (samples 上)
    normals = _polyline_outward_normals(pts, closed=True, balloon_center=balloon_center_m)
    # 期待される山数は **body anchor 数** を上限の目安にする。本体カーブは
    # 山/谷 anchor が交互に並ぶ構造なので、anchor 数 = (山数 + 谷数) に近い。
    # 周長ベース (base_width 単位) で見積もると偽の局所最大が大量に拾われ、
    # 「長さ変化 < 100%」で各偽 peak ごとに細かい切れ目が入って線が破片化していた。
    # 小山も拾えるよう anchor の 2 倍までを許容するが、それ以上はノイズとして抑える。
    samples_per_segment = max(1, SAMPLES_PER_SEGMENT)
    anchor_count = max(2, n // samples_per_segment)
    expected_count = max(8, anchor_count * 2)
    peaks_all, valleys_all = _detect_centerline_peaks_valleys(
        pts,
        balloon_center_m,
        expected_count=expected_count,
    )

    # 鋭角コーナーで法線が急変する sample-direct 方式は帯エッジが「ヒゲ状」に
    # 飛び出してしまうため、length<100% または width 非一様のリングは Shapely
    # buffer ベースの centerline + 主山頂のくさび形差し引き方式で構築する。
    # buffer 自体は鋭角コーナーで自然なミテレ延長を持つため、帯が滑らかに繋がる。
    if length_scale < 0.999 or abs(valley_width_m - peak_width_m) > 1.0e-6:
        polys = _build_shapely_band_with_peak_cuts(
            pts,
            balloon_center_m=balloon_center_m,
            signed_offset_m=signed_offset_m,
            valley_width_m=valley_width_m,
            peak_width_m=peak_width_m,
            length_scale=length_scale,
            valley_sharp=valley_sharp,
            peaks_all=peaks_all,
            valleys_all=valleys_all,
            cross_extension_m=cross_extension_m,
        )
        if polys is not None:
            return polys
        # 失敗時は従来の sample-direct 経路に fallback

    # 長さ変化のカット中心は **大山のみ** (= 主トゲの先端) に絞る。サブバンプ
    # (小山) の anchor が peaks_all に大量に含まれると、length<100% で各サブ
    # バンプ周辺で細かい切れ目が無数に入り、線が破片化する。
    # 大山/小山の判定は radial 値の閾値で行う: 最大 peak radial と最小 valley
    # radial の中間より上を「大山」、下を「大谷」とする。
    cx_m, cy_m = balloon_center_m
    radii = [math.hypot(p[0] - cx_m, p[1] - cy_m) for p in pts]
    if peaks_all and valleys_all:
        max_peak_r = max(radii[p] for p in peaks_all)
        min_valley_r = min(radii[v] for v in valleys_all)
        if max_peak_r - min_valley_r > 1.0e-6:
            half_r = (max_peak_r + min_valley_r) * 0.5
            peaks = [p for p in peaks_all if radii[p] >= half_r]
            valleys = [v for v in valleys_all if radii[v] <= half_r]
        else:
            peaks = list(peaks_all)
            valleys = list(valleys_all)
    else:
        peaks = list(peaks_all)
        valleys = list(valleys_all)

    # 「角を尖らせる」相当の peak 延長: peak_extension_m > 0 のとき、各 peak
    # 頂点を外向き法線方向へ延ばす。延長は peak 周辺の数サンプルに対して
    # smoothstep で滑らかに減衰させる (急な段差を避ける)。
    pts_eff = list(pts)
    if peak_extension_m > 1.0e-9 and peaks:
        # 隣接 peak/valley 間の距離 (samples 単位) を基準に半幅 falloff を決める
        half_span = max(2, anchor_count and (n // max(1, anchor_count * 2)) or 4)
        for peak in peaks:
            for offset in range(-half_span, half_span + 1):
                idx = (peak + offset) % n
                # smoothstep に近い局所重み (= 0 at edge, 1 at peak)
                u = 1.0 - (abs(offset) / float(half_span))
                if u <= 0.0:
                    continue
                w = u * u * (3.0 - 2.0 * u)
                ex = peak_extension_m * w
                nx, ny = normals[idx]
                pts_eff[idx] = (pts_eff[idx][0] + nx * ex, pts_eff[idx][1] + ny * ex)

    # オフセット centerline = pts_eff + normal * signed_offset_m
    centerline = [
        (pts_eff[i][0] + normals[i][0] * signed_offset_m,
         pts_eff[i][1] + normals[i][1] * signed_offset_m)
        for i in range(n)
    ]

    # 各サンプル点の line width: 谷と山の頂点 (大山/小山どちらでも) から
    # 線形補間する。大山だけで補間するとサブバンプ箇所が谷扱いになって帯が
    # 細くなりすぎるため、ここは peaks_all / valleys_all (= 全 anchor 極値) を使う。
    width_peaks = peaks_all if peaks_all else peaks
    width_valleys = valleys_all if valleys_all else valleys
    widths: list[float] = []
    if not width_peaks and not width_valleys:
        widths = [base_width_m] * n
    else:
        for i in range(n):
            if width_peaks:
                d_peak = min(_circular_dist(i, p, n) for p in width_peaks)
            else:
                d_peak = n  # peak が無いケース: 全体を valley_width とみなす
            if width_valleys:
                d_valley = min(_circular_dist(i, v, n) for v in width_valleys)
            else:
                d_valley = n
            total = d_peak + d_valley
            if total <= 0:
                t = 0.5
            else:
                t = d_valley / total  # 0 at valley, 1 at peak
            widths.append(valley_width_m + (peak_width_m - valley_width_m) * t)

    # length cut は大山ベースだけで行う (= 大山周りで切って valley から伸ばす)
    segments = _ring_kept_index_segments(n, peaks, valleys, length_scale)

    out_polygons: list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]] = []
    # cross_extension_m > 0: 各 keep segment の山頂方向 (= 端点) を法線方向へ延ばし、
    # 谷をまたいで隣接 segment と交差する形に。length_scale < 1.0 のときのみ有効。
    if length_scale >= 0.999:
        # 閉じた全周帯 (ホール付きポリゴン): cross_extension は無関係
        if segments:
            seg = segments[0]
            result = _build_variable_width_band_segment(centerline, widths, normals, seg, closed=True)
            if result is not None:
                out_polygons.append(result)
    else:
        for seg in segments:
            if len(seg) < 2:
                continue
            if cross_extension_m > 1.0e-9:
                centerline_ext, widths_ext, normals_ext, seg_ext = _extend_segment_for_cross(
                    centerline, widths, normals, seg, cross_extension_m
                )
                result = _build_variable_width_band_segment(centerline_ext, widths_ext, normals_ext, seg_ext, closed=False)
            else:
                result = _build_variable_width_band_segment(centerline, widths, normals, seg, closed=False)
            if result is not None:
                out_polygons.append(result)
    return out_polygons


def _build_shapely_band_with_peak_cuts(
    pts: Sequence[tuple[float, float]],
    *,
    balloon_center_m: tuple[float, float],
    signed_offset_m: float,
    valley_width_m: float,
    peak_width_m: float,
    length_scale: float,
    valley_sharp: bool,
    peaks_all: Sequence[int],
    valleys_all: Sequence[int],
    cross_extension_m: float = 0.0,
) -> Optional[list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]]:
    """Shapely 上で滑らかな centerline リングを作り、主山頂のくさび差し引きで length 変化を、
    centerline 上のリサンプル + per-point 幅で width 変化を表現する."""
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import Polygon  # type: ignore
        from shapely.ops import unary_union  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    if max(valley_width_m, peak_width_m) <= 1.0e-9:
        return []
    body_poly = _build_body_polygon([(p[0], p[1], 1.0) for p in pts])
    if body_poly is None:
        return None
    join = 2 if valley_sharp else 1
    mitre = _SHARP_MITRE_LIMIT if valley_sharp else _ROUND_MITRE_LIMIT

    if abs(valley_width_m - peak_width_m) < 1.0e-6:
        # 幅一様: 二つの buffer の差で帯を作る (最速・最も綺麗)
        half = max(valley_width_m, peak_width_m) * 0.5
        try:
            outer_buf = body_poly.buffer(signed_offset_m + half, join_style=join, mitre_limit=mitre)
            inner_buf = body_poly.buffer(signed_offset_m - half, join_style=join, mitre_limit=mitre)
            band = outer_buf.difference(inner_buf)
        except Exception:  # noqa: BLE001
            return None
    else:
        # 幅可変: buffer 由来の centerline をリサンプリングし、各点の幅を peaks/valleys
        # からの radial 距離に基づき線形補間して、独自に帯ポリゴンを構築。
        band = _build_variable_width_band_from_buffer(
            body_poly=body_poly,
            pts=pts,
            balloon_center_m=balloon_center_m,
            signed_offset_m=signed_offset_m,
            valley_width_m=valley_width_m,
            peak_width_m=peak_width_m,
            valley_sharp=valley_sharp,
            peaks_all=peaks_all,
            valleys_all=valleys_all,
        )
        if band is None or band.is_empty:
            return None
    if band.is_empty:
        return []

    # 大山頂を radial 閾値で抽出
    cx_m, cy_m = balloon_center_m
    radii = [math.hypot(p[0] - cx_m, p[1] - cy_m) for p in pts]
    if peaks_all and valleys_all:
        max_peak_r = max(radii[p] for p in peaks_all)
        min_valley_r = min(radii[v] for v in valleys_all)
        if max_peak_r - min_valley_r > 1.0e-6:
            half_r = (max_peak_r + min_valley_r) * 0.5
            main_peaks = [p for p in peaks_all if radii[p] >= half_r]
        else:
            main_peaks = list(peaks_all)
    else:
        main_peaks = list(peaks_all)
    if not main_peaks:
        # 山が無い → そのまま閉ループを返す
        return _shapely_band_to_polygons(band)

    # 各 main peak の角度位置
    n = len(pts)
    num_peaks = max(1, len(main_peaks))
    full_period_angle = 2.0 * math.pi / num_peaks
    cut_factor = max(0.0, 1.0 - float(length_scale))
    # cross_enabled (= cross_extension_m > 0) は cut_factor をマイナスにして "重ねる" 方向に。
    if cross_extension_m > 1.0e-9:
        # cross の場合は cut を抜くのではなく、山頂を「外側に伸ばす」ようにしたいが
        # Shapely 経路では難しいため、ここでは cut_factor を 0 にして全閉ループを返す。
        # 将来的に専用ロジックで対応する。
        cut_factor = 0.0
    if cut_factor <= 1.0e-6:
        return _shapely_band_to_polygons(band)

    # 各 main peak の角度を計算し、その周辺 cut_factor × half_period_angle 分を抜く
    cut_half_angle = cut_factor * full_period_angle * 0.5
    # くさび形は body 中心 → 大半径 (band 外側より大きい) → 中心 の三角形
    max_outer_r = max(radii) + abs(signed_offset_m) + band_width_m * 2.0 + 0.05  # 余裕を持たせる
    wedges = []
    for peak_idx in main_peaks:
        peak_x = pts[peak_idx][0] - cx_m
        peak_y = pts[peak_idx][1] - cy_m
        peak_angle = math.atan2(peak_y, peak_x)
        a0 = peak_angle - cut_half_angle
        a1 = peak_angle + cut_half_angle
        # くさび形の 3 頂点 (中心 → 弧上 2 点)
        steps = 8
        vertices = [(cx_m, cy_m)]
        for s in range(steps + 1):
            t = s / steps
            a = a0 + (a1 - a0) * t
            vertices.append((cx_m + math.cos(a) * max_outer_r, cy_m + math.sin(a) * max_outer_r))
        try:
            wedge_poly = Polygon(vertices)
            if not wedge_poly.is_valid:
                wedge_poly = wedge_poly.buffer(0)
            if not wedge_poly.is_empty and wedge_poly.area > 0:
                wedges.append(wedge_poly)
        except Exception:  # noqa: BLE001
            continue
    if wedges:
        try:
            cuts = unary_union(wedges)
            band = band.difference(cuts)
        except Exception:  # noqa: BLE001
            pass
    if band.is_empty:
        return []
    return _shapely_band_to_polygons(band)


def _build_variable_width_band_from_buffer(
    *,
    body_poly,
    pts: Sequence[tuple[float, float]],
    balloon_center_m: tuple[float, float],
    signed_offset_m: float,
    valley_width_m: float,
    peak_width_m: float,
    valley_sharp: bool,
    peaks_all: Sequence[int],
    valleys_all: Sequence[int],
):
    """幅可変リングを Shapely buffer 由来の centerline で構築する。

    1. body_poly を `signed_offset_m` で buffer して滑らかな centerline 多角形を得る
    2. centerline の外周をリサンプル (各サンプル点で外向き法線を計算)
    3. 各 centerline 点について、最も近い body sample のインデックスを推定し、
       そのインデックスから peaks/valleys の radial 距離で per-point 幅を線形補間
    4. centerline ± normal * width/2 で outer/inner ループを作り、Shapely Polygon に
    """
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import Polygon  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    # centerline 用 buffer は mitre 爆発を避けるため穏やかな mitre_limit (4.0) を使う。
    # 角を尖らせる効果は per-point 幅補間 と outer/inner offset で表現する。
    join = 2 if valley_sharp else 1
    mitre = 4.0 if valley_sharp else _ROUND_MITRE_LIMIT
    try:
        center_buf = body_poly.buffer(signed_offset_m, join_style=join, mitre_limit=mitre)
    except Exception:  # noqa: BLE001
        return None
    if center_buf.is_empty:
        return None
    if center_buf.geom_type == "Polygon":
        geom = center_buf
    elif center_buf.geom_type == "MultiPolygon":
        geom = max(center_buf.geoms, key=lambda g: g.area)
    else:
        return None
    coords = list(geom.exterior.coords)
    if len(coords) < 6:
        return None
    cl_pts = [(float(x), float(y)) for x, y in coords]
    if math.hypot(cl_pts[0][0] - cl_pts[-1][0], cl_pts[0][1] - cl_pts[-1][1]) <= 1.0e-9:
        cl_pts = cl_pts[:-1]
    m = len(cl_pts)
    if m < 6:
        return None
    cl_normals = _polyline_outward_normals(cl_pts, closed=True, balloon_center=balloon_center_m)

    # 各 centerline 点から最も近い body sample を見つける (角度比較で簡略化)
    cx, cy = balloon_center_m
    body_angles = [math.atan2(p[1] - cy, p[0] - cx) for p in pts]
    n = len(pts)

    # peaks_all/valleys_all の radial 値を取っておく
    radii = [math.hypot(p[0] - cx, p[1] - cy) for p in pts]

    def _circ_dist_int(a: int, b: int) -> int:
        d = abs(a - b) % n
        return min(d, n - d)

    widths: list[float] = []
    for cl in cl_pts:
        cl_angle = math.atan2(cl[1] - cy, cl[0] - cx)
        # 角度的に最も近い body sample
        best_i = 0
        best_da = float("inf")
        for i, ba in enumerate(body_angles):
            da = abs(ba - cl_angle)
            if da > math.pi:
                da = 2.0 * math.pi - da
            if da < best_da:
                best_da = da
                best_i = i
        # 大山/大谷の radial 閾値で main peaks/valleys に絞る
        if peaks_all and valleys_all:
            max_r = max(radii[p] for p in peaks_all)
            min_r = min(radii[v] for v in valleys_all)
            if max_r - min_r > 1.0e-6:
                half_r = (max_r + min_r) * 0.5
                main_peaks = [p for p in peaks_all if radii[p] >= half_r]
                main_valleys = [v for v in valleys_all if radii[v] <= half_r]
            else:
                main_peaks = list(peaks_all)
                main_valleys = list(valleys_all)
        else:
            main_peaks = list(peaks_all)
            main_valleys = list(valleys_all)
        d_peak = min((_circ_dist_int(best_i, p) for p in main_peaks), default=n) if main_peaks else n
        d_valley = min((_circ_dist_int(best_i, v) for v in main_valleys), default=n) if main_valleys else n
        total = d_peak + d_valley
        t = 0.5 if total <= 0 else (d_valley / total)
        widths.append(valley_width_m + (peak_width_m - valley_width_m) * t)

    # outer/inner ループを構築
    outer_ring = []
    inner_ring = []
    for i in range(m):
        cx_pt, cy_pt = cl_pts[i]
        nx, ny = cl_normals[i]
        half_w = widths[i] * 0.5
        outer_ring.append((cx_pt + nx * half_w, cy_pt + ny * half_w))
        inner_ring.append((cx_pt - nx * half_w, cy_pt - ny * half_w))

    try:
        outer_poly = Polygon(outer_ring)
        inner_poly = Polygon(inner_ring)
        if not outer_poly.is_valid:
            outer_poly = outer_poly.buffer(0)
        if not inner_poly.is_valid:
            inner_poly = inner_poly.buffer(0)
        band = outer_poly.difference(inner_poly)
    except Exception:  # noqa: BLE001
        return None
    return band


def _shapely_band_to_polygons(band) -> list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]:
    """Shapely band を [(outer_ring, holes), ...] のリストに変換."""
    polys = []
    if band.geom_type == "Polygon":
        geoms = [band]
    elif band.geom_type == "MultiPolygon":
        geoms = list(band.geoms)
    else:
        return []
    for geom in geoms:
        if geom.is_empty or geom.area <= 1.0e-12:
            continue
        outer_ring = list(geom.exterior.coords)
        holes = [list(r.coords) for r in geom.interiors]
        polys.append((outer_ring, holes))
    return polys


def _extend_segment_for_cross(
    centerline: Sequence[tuple[float, float]],
    widths: Sequence[float],
    normals: Sequence[tuple[float, float]],
    seg: Sequence[int],
    cross_extension_m: float,
) -> tuple[list[tuple[float, float]], list[float], list[tuple[float, float]], list[int]]:
    """seg の両端 (= peak 寄り) を centerline 上の接線方向に延ばす。

    seg は 谷→山→谷 (3 連) の形をしている想定。両端は「peak の手前で切られた点」。
    その端点をさらに接線方向へ `cross_extension_m` だけ伸ばし、新しい仮想頂点を
    centerline の末尾に追加して seg に組み込む。これで隣接 segment の端点同士が
    谷をまたいで交差する見た目になる。
    """
    cl = list(centerline)
    wd = list(widths)
    nm = list(normals)
    new_seg = list(seg)
    if len(seg) < 2:
        return cl, wd, nm, new_seg
    # 前端: seg[0] と seg[1] の接線方向 (反転) で延ばす
    a0 = cl[seg[0]]
    a1 = cl[seg[1]]
    dx = a0[0] - a1[0]
    dy = a0[1] - a1[1]
    dlen = math.hypot(dx, dy)
    if dlen > 1.0e-9:
        scale = cross_extension_m / dlen
        head_pt = (a0[0] + dx * scale, a0[1] + dy * scale)
        cl.append(head_pt)
        wd.append(wd[seg[0]])
        nm.append(nm[seg[0]])
        new_seg.insert(0, len(cl) - 1)
    # 後端: seg[-1] と seg[-2] の接線方向 (反転) で延ばす
    b0 = cl[seg[-1]]
    b1 = cl[seg[-2]]
    dx = b0[0] - b1[0]
    dy = b0[1] - b1[1]
    dlen = math.hypot(dx, dy)
    if dlen > 1.0e-9:
        scale = cross_extension_m / dlen
        tail_pt = (b0[0] + dx * scale, b0[1] + dy * scale)
        cl.append(tail_pt)
        wd.append(wd[seg[-1]])
        nm.append(nm[seg[-1]])
        new_seg.append(len(cl) - 1)
    return cl, wd, nm, new_seg


def _stroke_band_outside_union_OLD_QUAD_BASED__UNUSED(
    samples: Sequence[tuple[float, float, float]],
    *,
    line_width_m: float,
    valley_sharp: bool,
    cusp_threshold_rad: float = math.radians(100.0),
    miter_factor: float = 2.5,
    arc_samples_per_cusp: int = 12,
) -> Optional[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]:
    """フキダシ主線を「カーブの外側へ太くする」(outside alignment) 方式で構築する.

    手順:
    1. 本体サンプル列に対し外向き垂直オフセット (line_width_m) で外周点列を作る
    2. cusp (鋭角谷) では round join (円弧) または sharp (miter) で外周を繋ぐ
    3. オフセットが自己交差する場合があっても、shapely で **クワッド群の UNION**
       を計算してから外側の輪郭ポリゴンを抽出する
    4. 戻り値は (outer_ring, hole_rings)。outer_ring は外側の閉ループ、
       hole_rings は内側のホール (通常は本体カーブ = 0 個か 1 個)

    self-intersection が幾何的に必要 (谷間が線幅より狭い) でもクリーンな輪郭が
    得られる。三角分割は呼び出し側で行う。
    """
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import Polygon  # type: ignore
        from shapely.ops import unary_union  # type: ignore
    except Exception:  # noqa: BLE001
        return None

    n = len(samples)
    if n < 3 or line_width_m <= 1.0e-9:
        return None
    pts = [(s[0], s[1]) for s in samples]
    area = _polygon_area(pts)
    if abs(area) <= 1.0e-12:
        return None
    ccw = area > 0.0
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n

    W = line_width_m  # 外向きオフセット距離 = 線幅そのもの (band 全幅)

    # 外周/内周点列を構築。inner = サンプル位置そのもの (本体カーブ)。
    outer: list[tuple[float, float]] = []
    inner: list[tuple[float, float]] = []
    for i in range(n):
        prev_s = samples[(i - 1) % n]
        curr_s = samples[i]
        next_s = samples[(i + 1) % n]
        ax, ay = curr_s[0] - prev_s[0], curr_s[1] - prev_s[1]
        bx, by = next_s[0] - curr_s[0], next_s[1] - curr_s[1]
        la = math.hypot(ax, ay)
        lb = math.hypot(bx, by)
        if la <= 1.0e-9 or lb <= 1.0e-9:
            continue
        a_dx, a_dy = ax / la, ay / la
        b_dx, b_dy = bx / lb, by / lb
        cross = a_dx * b_dy - a_dy * b_dx
        dot = a_dx * b_dx + a_dy * b_dy
        bend = math.atan2(abs(cross), dot)
        rs = max(0.0, float(curr_s[2]))
        # 外向き法線 = CCW なら接線の右側
        if ccw:
            perp_a = (a_dy, -a_dx)
            perp_b = (b_dy, -b_dx)
        else:
            perp_a = (-a_dy, a_dx)
            perp_b = (-b_dy, b_dx)
        if bend < cusp_threshold_rad:
            # 通常 anchor: miter 二等分線オフセット
            dot_pp = perp_a[0] * perp_b[0] + perp_a[1] * perp_b[1]
            denom = 1.0 + dot_pp
            if denom <= 1.0e-3:
                continue
            sx = perp_a[0] + perp_b[0]
            sy = perp_a[1] + perp_b[1]
            ox = sx * W * rs / denom
            oy = sy * W * rs / denom
            off_len = math.hypot(ox, oy)
            if off_len > 3.0 * W and off_len > 0:
                ox *= 3.0 * W / off_len
                oy *= 3.0 * W / off_len
            outer.append((curr_s[0] + ox, curr_s[1] + oy))
            inner.append((curr_s[0], curr_s[1]))
        else:
            # cusp 処理
            rad_x = curr_s[0] - cx
            rad_y = curr_s[1] - cy
            r_len = math.hypot(rad_x, rad_y)
            if r_len <= 1.0e-9:
                continue
            r_out = (rad_x / r_len, rad_y / r_len)
            if valley_sharp:
                # miter: perp_a 端 → 外向き先端 → perp_b 端
                miter_len = W * miter_factor
                miter_out = (curr_s[0] + r_out[0] * miter_len, curr_s[1] + r_out[1] * miter_len)
                outer.append((curr_s[0] + perp_a[0] * W * rs, curr_s[1] + perp_a[1] * W * rs))
                outer.append(miter_out)
                outer.append((curr_s[0] + perp_b[0] * W * rs, curr_s[1] + perp_b[1] * W * rs))
                inner.append((curr_s[0], curr_s[1]))
                inner.append((curr_s[0], curr_s[1]))
                inner.append((curr_s[0], curr_s[1]))
            else:
                # round join: 半径 W の円弧で滑らかに繋ぐ
                ang_a = math.atan2(perp_a[1], perp_a[0])
                ang_b = math.atan2(perp_b[1], perp_b[0])
                ang_mid = math.atan2(r_out[1], r_out[0])

                def _norm_pi(x: float) -> float:
                    while x > math.pi:
                        x -= 2.0 * math.pi
                    while x < -math.pi:
                        x += 2.0 * math.pi
                    return x

                sweep_ccw = _norm_pi(ang_b - ang_a)
                if sweep_ccw < 0:
                    sweep_ccw += 2.0 * math.pi
                pos_mid = _norm_pi(ang_mid - ang_a)
                if pos_mid < 0:
                    pos_mid += 2.0 * math.pi
                use_ccw = 0.0 <= pos_mid <= sweep_ccw
                steps = max(2, int(arc_samples_per_cusp))
                for k in range(steps):
                    t = k / (steps - 1)
                    if use_ccw:
                        a = ang_a + sweep_ccw * t
                    else:
                        sweep_cw = _norm_pi(ang_a - ang_b)
                        if sweep_cw < 0:
                            sweep_cw += 2.0 * math.pi
                        a = ang_a - sweep_cw * t
                    outer.append((curr_s[0] + math.cos(a) * W * rs, curr_s[1] + math.sin(a) * W * rs))
                    inner.append((curr_s[0], curr_s[1]))

    if len(outer) < 3 or len(inner) != len(outer):
        return None

    # クワッド群を shapely で UNION
    polys = []
    nq = len(outer)
    for i in range(nq):
        quad = [outer[i], outer[(i + 1) % nq], inner[(i + 1) % nq], inner[i]]
        # Skip degenerate (zero area)
        a = 0.0
        prev = quad[-1]
        for p in quad:
            a += prev[0] * p[1] - p[0] * prev[1]
            prev = p
        if abs(a * 0.5) < 1.0e-12:
            continue
        try:
            poly = Polygon(quad)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.area > 0:
                polys.append(poly)
        except Exception:  # noqa: BLE001
            continue
    if not polys:
        return None
    try:
        union = unary_union(polys)
    except Exception:  # noqa: BLE001
        return None
    # 外側 outline + 内側 hole を抽出
    rings: list[list[tuple[float, float]]] = []
    holes_all: list[list[tuple[float, float]]] = []
    if union.geom_type == "Polygon":
        rings.append(list(union.exterior.coords))
        for ring in union.interiors:
            holes_all.append(list(ring.coords))
    elif union.geom_type == "MultiPolygon":
        # 最大面積のポリゴンを採用 (通常は band 全体が 1 つに繋がる)
        biggest = max(union.geoms, key=lambda g: g.area)
        rings.append(list(biggest.exterior.coords))
        for ring in biggest.interiors:
            holes_all.append(list(ring.coords))
    else:
        return None
    if not rings:
        return None
    return rings[0], holes_all


def _open_ring(ring: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(ring) >= 2 and ring[0] == ring[-1]:
        return list(ring[:-1])
    return list(ring)


def _ring_signed_area(ring: Sequence[tuple[float, float]]) -> float:
    n = len(ring)
    if n < 3:
        return 0.0
    a = 0.0
    for i in range(n):
        j = (i + 1) % n
        a += ring[i][0] * ring[j][1] - ring[j][0] * ring[i][1]
    return a * 0.5


def _orient_ring(ring: list[tuple[float, float]], want_ccw: bool) -> list[tuple[float, float]]:
    is_ccw = _ring_signed_area(ring) > 0
    if is_ccw != want_ccw:
        return list(reversed(ring))
    return ring


def _triangulate_polygon(
    outer_ring: Sequence[tuple[float, float]],
    holes: Sequence[Sequence[tuple[float, float]]],
) -> tuple[list[tuple[float, float]], list[tuple[int, int, int]]]:
    """ホール付きポリゴンを earcut で三角分割し、(verts2d, triangles) を返す."""
    python_deps.ensure_bundled_wheels_on_path()
    try:
        import numpy as np  # type: ignore
        import mapbox_earcut as earcut  # type: ignore
    except Exception:  # noqa: BLE001
        return [], []
    outer_open = _orient_ring(_open_ring(outer_ring), want_ccw=True)
    holes_open = [_orient_ring(_open_ring(h), want_ccw=False) for h in holes if len(_open_ring(h)) >= 3]
    if len(outer_open) < 3:
        return [], []
    all_pts: list[tuple[float, float]] = list(outer_open)
    ring_ends: list[int] = [len(all_pts)]
    for h in holes_open:
        all_pts.extend(h)
        ring_ends.append(len(all_pts))
    coords = np.array(all_pts, dtype=np.float64)
    ring_ends_arr = np.array(ring_ends, dtype=np.uint32)
    try:
        tris = earcut.triangulate_float64(coords, ring_ends_arr)
    except Exception:  # noqa: BLE001
        return [], []
    triangles: list[tuple[int, int, int]] = []
    for i in range(0, len(tris) - 2, 3):
        a = int(tris[i]); b = int(tris[i + 1]); c = int(tris[i + 2])
        if a == b or b == c or a == c:
            continue
        triangles.append((a, b, c))
    return all_pts, triangles


def _build_band_mesh_from_union(
    mesh: bpy.types.Mesh,
    outer_ring: Sequence[tuple[float, float]],
    holes: Sequence[Sequence[tuple[float, float]]],
    z_m: float,
) -> None:
    """単一ポリゴン (ホール込み) を三角分割して mesh に流し込む."""
    pts, faces = _triangulate_polygon(outer_ring, holes)
    mesh.clear_geometry()
    if not faces or len(pts) < 3:
        mesh.update()
        return
    verts = [(float(x), float(y), float(z_m)) for x, y in pts]
    mesh.from_pydata(verts, [], faces)
    mesh.update()


def _build_band_mesh_from_polygons(
    mesh: bpy.types.Mesh,
    polygons: Sequence[tuple[Sequence[tuple[float, float]], Sequence[Sequence[tuple[float, float]]]]],
    z_m: float,
) -> None:
    """複数のホール付きポリゴンを 1 つの mesh に統合して流し込む.

    各ポリゴンを個別に三角分割し、頂点インデックスをオフセットして連結する。
    """
    all_verts: list[tuple[float, float, float]] = []
    all_faces: list[tuple[int, int, int]] = []
    for outer_ring, holes in polygons:
        pts, faces = _triangulate_polygon(outer_ring, holes)
        if not faces or len(pts) < 3:
            continue
        base = len(all_verts)
        all_verts.extend((float(x), float(y), float(z_m)) for x, y in pts)
        all_faces.extend((a + base, b + base, c + base) for a, b, c in faces)
    mesh.clear_geometry()
    if not all_faces or len(all_verts) < 3:
        mesh.update()
        return
    mesh.from_pydata(all_verts, [], all_faces)
    mesh.update()


def _stroke_band_body_centerline_round_join(
    samples: Sequence[tuple[float, float, float]],
    *,
    half_width_m: float,
    cusp_threshold_rad: float,
    arc_samples_per_cusp: int,
    valley_sharp: bool = False,
) -> Optional[tuple[list[tuple[float, float]], list[tuple[float, float]]]]:
    """本体カーブをそのまま中心線とし、垂直オフセットでメッシュバンドを構築する.

    谷の cusp (近 180° 反転) の処理:
    - valley_sharp=False (既定, round join): 外周/内周とも半径 d の半円弧で滑らかに繋ぐ
    - valley_sharp=True (miter join): 隣接 cubic の外周を伸ばして交差点 (miter point) で
      合流させ、谷を「曲線同士の交点としての自然な鋭角」にする。クランプして無限大化を防ぐ。
    """
    n = len(samples)
    if n < 3 or half_width_m <= 1.0e-9:
        return None
    pts = [(s[0], s[1]) for s in samples]
    area = _polygon_area(pts)
    if abs(area) <= 1.0e-12:
        return None
    ccw = area > 0.0

    # Estimate polygon centroid for outward direction at cusps
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n

    outer: list[tuple[float, float]] = []
    inner: list[tuple[float, float]] = []
    for i in range(n):
        prev_s = samples[(i - 1) % n]
        curr_s = samples[i]
        next_s = samples[(i + 1) % n]
        ax, ay = curr_s[0] - prev_s[0], curr_s[1] - prev_s[1]
        bx, by = next_s[0] - curr_s[0], next_s[1] - curr_s[1]
        la = math.hypot(ax, ay)
        lb = math.hypot(bx, by)
        if la <= 1.0e-9 or lb <= 1.0e-9:
            continue
        a_dx, a_dy = ax / la, ay / la
        b_dx, b_dy = bx / lb, by / lb
        cross = a_dx * b_dy - a_dy * b_dx
        dot = a_dx * b_dx + a_dy * b_dy
        bend = math.atan2(abs(cross), dot)  # 0 (straight) to π (full reversal)

        radius_scale = max(0.0, float(curr_s[2]))
        d = half_width_m * radius_scale

        # 外向き法線 = CCW なら接線右側, CW なら左側
        if ccw:
            perp_a = (a_dy, -a_dx)
            perp_b = (b_dy, -b_dx)
        else:
            perp_a = (-a_dy, a_dx)
            perp_b = (-b_dy, b_dx)

        if bend < cusp_threshold_rad:
            # 通常 anchor: 両隣接接線の miter join (bisector 方向の perpendicular offset)
            # offset_vec = (perp_a + perp_b) * d / (1 + perp_a·perp_b)
            # = bisector_unit * (d / cos(bend/2))
            # 鋭角 corner で miter が長くなり過ぎないよう、3*d を上限にする (miter limit).
            dot_pp = perp_a[0] * perp_b[0] + perp_a[1] * perp_b[1]
            denom = 1.0 + dot_pp
            if denom <= 1.0e-3:
                # bend が 90°以上なら cusp 扱いに回す (実質ここに来ない、threshold 100° なので)
                continue
            sx = perp_a[0] + perp_b[0]
            sy = perp_a[1] + perp_b[1]
            ox = sx * d / denom
            oy = sy * d / denom
            # miter limit: |offset| <= 3 * d
            off_len = math.hypot(ox, oy)
            if off_len > 3.0 * d and off_len > 0:
                ox *= 3.0 * d / off_len
                oy *= 3.0 * d / off_len
            outer.append((curr_s[0] + ox, curr_s[1] + oy))
            inner.append((curr_s[0] - ox, curr_s[1] - oy))
        else:
            # cusp: valley_sharp が True なら miter join (鋭角)、False なら round join
            # 外向き radial (cloud 重心からの方向) を arc 中央方向にする
            rad_x = curr_s[0] - cx
            rad_y = curr_s[1] - cy
            r_len = math.hypot(rad_x, rad_y)
            if r_len <= 1.0e-9:
                continue
            r_out = (rad_x / r_len, rad_y / r_len)

            if valley_sharp:
                # 谷を尖らせる: 外周エッジのみ radial outward 方向に伸ばして
                # 隣接 cubic の外周オフセット同士が交わる点 (miter tip) を作る。
                # 飛び出し量は半幅 × 2.5 でクランプ。
                # 内周は本体 cusp 頂点 (= curr_s) に置く (= 本体形状にぴったり追従、
                # 内側にスパイクを出さない)。
                miter_len = half_width_m * 2.5
                miter_out = (curr_s[0] + r_out[0] * miter_len, curr_s[1] + r_out[1] * miter_len)
                outer.append((curr_s[0] + perp_a[0] * d, curr_s[1] + perp_a[1] * d))
                outer.append(miter_out)
                outer.append((curr_s[0] + perp_b[0] * d, curr_s[1] + perp_b[1] * d))
                inner.append((curr_s[0], curr_s[1]))
                inner.append((curr_s[0], curr_s[1]))
                inner.append((curr_s[0], curr_s[1]))
                continue
            # arc の開始/終了 angle (perp_a と perp_b の方向)
            ang_a = math.atan2(perp_a[1], perp_a[0])
            ang_b = math.atan2(perp_b[1], perp_b[0])
            ang_mid = math.atan2(r_out[1], r_out[0])
            # ang_a → ang_mid → ang_b の方向に並ぶよう調整 (短い方の弧)
            # 範囲 [-π, π] に正規化したうえで、ang_a から ang_b へ ang_mid を通る向きに sweep する
            def _norm_pi(x: float) -> float:
                while x > math.pi:
                    x -= 2.0 * math.pi
                while x < -math.pi:
                    x += 2.0 * math.pi
                return x
            # try CCW (increasing) and CW (decreasing) directions, pick the one passing near ang_mid
            def _on_arc(start: float, end: float, mid: float, ccw_arc: bool) -> bool:
                # arc from start to end going ccw (if ccw_arc) or cw, check if mid is on the arc
                if ccw_arc:
                    sweep = _norm_pi(end - start)
                    if sweep < 0:
                        sweep += 2.0 * math.pi
                    pos = _norm_pi(mid - start)
                    if pos < 0:
                        pos += 2.0 * math.pi
                    return 0.0 <= pos <= sweep
                else:
                    sweep = _norm_pi(start - end)
                    if sweep < 0:
                        sweep += 2.0 * math.pi
                    pos = _norm_pi(start - mid)
                    if pos < 0:
                        pos += 2.0 * math.pi
                    return 0.0 <= pos <= sweep
            use_ccw = _on_arc(ang_a, ang_b, ang_mid, True)
            if not use_ccw:
                # fallback to CW arc
                pass
            steps = max(2, int(arc_samples_per_cusp))
            for k in range(steps):
                t = k / (steps - 1)
                if use_ccw:
                    sweep = _norm_pi(ang_b - ang_a)
                    if sweep < 0:
                        sweep += 2.0 * math.pi
                    a = ang_a + sweep * t
                else:
                    sweep = _norm_pi(ang_a - ang_b)
                    if sweep < 0:
                        sweep += 2.0 * math.pi
                    a = ang_a - sweep * t
                # 外周: cusp 中心から半径 d の円弧
                outer.append((curr_s[0] + math.cos(a) * d, curr_s[1] + math.sin(a) * d))
                # 内周: cusp の内側へ半径 d 入った位置 (= 円弧の反対側)
                # 線幅を cusp でも一定に保つため body 内へ d 入り込ませる (body 塗りで隠れる)
                inner.append((curr_s[0] - math.cos(a) * d, curr_s[1] - math.sin(a) * d))
    if len(outer) < 3 or len(inner) != len(outer):
        return None
    return outer, inner


def _offset_perp_tangent(
    samples: Sequence[tuple[float, float, float]],
    *,
    half_width_m: float,
) -> Optional[tuple[list[tuple[float, float]], list[tuple[float, float]]]]:
    """各サンプル点で「前後サンプルから求めた局所接線」に垂直な向きで ±half_width
    オフセットする (per-point radius でスケール).

    bisector 方式と違い、鋭角部でも offset 量が局所接線距離=半幅 ぴったりに
    なるため line band の厚みが一定になる (入力が事前に smooth されていれば
    self-intersection は起きない).
    """
    n = len(samples)
    if n < 3 or half_width_m <= 1.0e-9:
        return None
    pts = [(s[0], s[1]) for s in samples]
    area = _polygon_area(pts)
    if abs(area) <= 1.0e-12:
        return None
    ccw = area > 0.0
    outer: list[tuple[float, float]] = []
    inner: list[tuple[float, float]] = []
    for i in range(n):
        prev_s = samples[(i - 1) % n]
        next_s = samples[(i + 1) % n]
        tx = next_s[0] - prev_s[0]
        ty = next_s[1] - prev_s[1]
        tlen = math.hypot(tx, ty)
        if tlen <= 1.0e-9:
            continue
        tx /= tlen
        ty /= tlen
        # 外向き法線: CCW 周回なら接線を 90° 右回し (ty, -tx), CW なら逆
        if ccw:
            nx, ny = ty, -tx
        else:
            nx, ny = -ty, tx
        radius_scale = max(0.0, float(samples[i][2]))
        d = half_width_m * radius_scale
        cx, cy = samples[i][0], samples[i][1]
        outer.append((cx + nx * d, cy + ny * d))
        inner.append((cx - nx * d, cy - ny * d))
    if len(outer) < 3 or len(inner) != len(outer):
        return None
    return outer, inner


def _polygon_area(points: Sequence[tuple[float, float]]) -> float:
    area = 0.0
    if not points:
        return 0.0
    prev = points[-1]
    for curr in points:
        area += prev[0] * curr[1] - curr[0] * prev[1]
        prev = curr
    return area * 0.5


def _band_loops(
    samples: Sequence[tuple[float, float, float]],
    *,
    half_width_m: float,
) -> Optional[tuple[list[tuple[float, float]], list[tuple[float, float]]]]:
    """各サンプル点の per-point radius を反映した外周/内周点列を返す.

    各点で、両隣の segment 法線の bisector 方向にオフセットする (per-point
    radius でスケール)。bisector が小さい (折り返しに近い) 点はスキップする。
    """
    n = len(samples)
    if n < 3 or half_width_m <= 1.0e-9:
        return None
    pts = [(s[0], s[1]) for s in samples]
    area = _polygon_area(pts)
    if abs(area) <= 1.0e-12:
        return None
    ccw = area > 0.0
    outer: list[tuple[float, float]] = []
    inner: list[tuple[float, float]] = []
    for i in range(n):
        prev_s = samples[(i - 1) % n]
        curr_s = samples[i]
        next_s = samples[(i + 1) % n]
        ax, ay = curr_s[0] - prev_s[0], curr_s[1] - prev_s[1]
        bx, by = next_s[0] - curr_s[0], next_s[1] - curr_s[1]
        la = math.hypot(ax, ay)
        lb = math.hypot(bx, by)
        if la <= 1.0e-9 or lb <= 1.0e-9:
            continue
        a_dx, a_dy = ax / la, ay / la
        b_dx, b_dy = bx / lb, by / lb
        left_prev = (-a_dy, a_dx)
        left_next = (-b_dy, b_dx)
        if ccw:
            inner_n = (left_prev[0] + left_next[0], left_prev[1] + left_next[1])
            outer_n = (-inner_n[0], -inner_n[1])
        else:
            outer_n = (left_prev[0] + left_next[0], left_prev[1] + left_next[1])
            inner_n = (-outer_n[0], -outer_n[1])
        on_len = math.hypot(*outer_n)
        in_len = math.hypot(*inner_n)
        if on_len <= 1.0e-9 or in_len <= 1.0e-9:
            continue
        outer_n = (outer_n[0] / on_len, outer_n[1] / on_len)
        inner_n = (inner_n[0] / in_len, inner_n[1] / in_len)
        radius_scale = max(0.0, float(curr_s[2]))
        d = half_width_m * radius_scale
        outer.append((curr_s[0] + outer_n[0] * d, curr_s[1] + outer_n[1] * d))
        inner.append((curr_s[0] + inner_n[0] * d, curr_s[1] + inner_n[1] * d))
    if len(outer) < 3 or len(inner) != len(outer):
        return None
    return outer, inner


def _cloud_band_via_independent_line_loops(
    entry,
    body_spline,
    line_width_mm: float,
) -> Optional[tuple[list[tuple[float, float]], list[tuple[float, float]]]]:
    """雲フキダシ用: 線形状を本体カーブと独立した滑らかな閉曲線として直接生成し、
    外周/内周点列のペアを返す.

    本体カーブの per-anchor radius を反映して valley ごとに線幅を伸縮する。
    成功時 (outer, inner) を返し、形状非対応や生成失敗時は None を返す
    (呼び出し側で従来のオフセット方式へ fallback)。
    """
    width_mm = max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0))
    height_mm = max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0))
    if width_mm <= 1.0e-3 or height_mm <= 1.0e-3:
        return None
    rect = Rect(0.0, 0.0, width_mm, height_mm)
    body_radii = _body_per_anchor_radii(body_spline)
    half_width_mm = line_width_mm * 0.5
    loops = balloon_shapes.bezier_line_loops_for_entry(
        entry,
        rect,
        half_width_mm,
        body_radii=body_radii,
    )
    if loops is None:
        return None
    outer_anchors, inner_anchors = loops
    if len(outer_anchors) < 3 or len(inner_anchors) != len(outer_anchors):
        return None
    offset_mm = _entry_local_offset_mm(entry)
    outer_pts = _sample_anchor_loop_to_local_m(outer_anchors, offset_mm, SAMPLES_PER_SEGMENT)
    inner_pts = _sample_anchor_loop_to_local_m(inner_anchors, offset_mm, SAMPLES_PER_SEGMENT)
    if len(outer_pts) < 3 or len(inner_pts) != len(outer_pts):
        return None
    return outer_pts, inner_pts


def _rebuild_band_mesh(
    mesh: bpy.types.Mesh,
    outer: Sequence[tuple[float, float]],
    inner: Sequence[tuple[float, float]],
    z_m: float,
) -> None:
    if len(outer) < 3 or len(inner) != len(outer):
        mesh.clear_geometry()
        mesh.update()
        return
    count = len(outer)
    verts: list[tuple[float, float, float]] = []
    verts.extend((float(x), float(y), float(z_m)) for x, y in outer)
    verts.extend((float(x), float(y), float(z_m)) for x, y in inner)
    faces: list[tuple[int, int, int, int]] = [
        (i, (i + 1) % count, count + (i + 1) % count, count + i)
        for i in range(count)
    ]
    mesh.clear_geometry()
    mesh.from_pydata(verts, [], faces)
    mesh.update()


def _line_mesh_object_name(balloon_id: str) -> str:
    return f"{BALLOON_LINE_MESH_NAME_PREFIX}{balloon_id}"


def _line_mesh_data_name(balloon_id: str) -> str:
    return f"{BALLOON_LINE_MESH_NAME_PREFIX}{balloon_id}_mesh"


def _outer_edge_mesh_object_name(balloon_id: str) -> str:
    return f"{BALLOON_OUTER_EDGE_MESH_NAME_PREFIX}{balloon_id}"


def _outer_edge_mesh_data_name(balloon_id: str) -> str:
    return f"{BALLOON_OUTER_EDGE_MESH_NAME_PREFIX}{balloon_id}_mesh"


def _inner_edge_mesh_object_name(balloon_id: str) -> str:
    return f"{BALLOON_INNER_EDGE_MESH_NAME_PREFIX}{balloon_id}"


def _inner_edge_mesh_data_name(balloon_id: str) -> str:
    return f"{BALLOON_INNER_EDGE_MESH_NAME_PREFIX}{balloon_id}_mesh"


def _multi_line_mesh_object_name(balloon_id: str) -> str:
    return f"{BALLOON_MULTI_LINE_MESH_NAME_PREFIX}{balloon_id}"


def _multi_line_mesh_data_name(balloon_id: str) -> str:
    return f"{BALLOON_MULTI_LINE_MESH_NAME_PREFIX}{balloon_id}_mesh"


def _attach_band_mesh_object(
    *,
    obj_name: str,
    mesh: bpy.types.Mesh,
    material: bpy.types.Material,
    body_object: bpy.types.Object,
    scene,
    kind: str,
    balloon_id: str,
    visible: bool,
    mask_info=None,
) -> bpy.types.Object:
    """Mesh をフキダシ本体に連結し、コレクション/親子を ensure する.

    コマ内マスクは material 側のアルファ画像マスク (画像マスク方式) に一本化し、
    ジオメトリ側のメッシュくり抜き modifier は使わない方針。古いビルドから残った
    クリップ modifier があれば撤去する。
    """
    obj = bpy.data.objects.get(obj_name)
    if obj is not None and getattr(obj, "type", "") != "MESH":
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
        obj = None
    if obj is None:
        obj = bpy.data.objects.new(obj_name, mesh)
    elif obj.data is not mesh:
        obj.data = mesh

    if material is not None:
        if not mesh.materials:
            mesh.materials.append(material)
        elif mesh.materials[0] is not material:
            mesh.materials[0] = material

    obj[PROP_BALLOON_LINE_MESH_KIND] = kind
    obj[PROP_BALLOON_LINE_MESH_OWNER_ID] = balloon_id
    obj[on.PROP_MANAGED] = False
    obj.hide_select = True

    target_collections = list(getattr(body_object, "users_collection", []) or [])
    if not target_collections:
        target_collections = [scene.collection] if scene is not None else []
    current_collections = set(getattr(obj, "users_collection", []) or [])
    for coll in target_collections:
        if coll not in current_collections:
            try:
                coll.objects.link(obj)
            except Exception:  # noqa: BLE001
                pass
    for coll in list(current_collections):
        if coll not in target_collections:
            try:
                coll.objects.unlink(obj)
            except Exception:  # noqa: BLE001
                pass

    if obj.parent is not body_object:
        obj.parent = body_object
        obj.matrix_parent_inverse.identity()
    obj.location = (0.0, 0.0, 0.0)
    obj.rotation_euler = (0.0, 0.0, 0.0)
    obj.scale = (1.0, 1.0, 1.0)

    obj.hide_viewport = not visible
    obj.hide_render = not visible

    # 旧来のメッシュくり抜き modifier は画像マスク方式に切り替えたため撤去する
    _sync_mask_clip_modifier(obj, None)
    return obj


def _resolve_body_spline(body_object: bpy.types.Object | None):
    """フキダシ本体カーブの閉じた Bezier spline を返す。無ければ None。"""
    if body_object is None or getattr(body_object, "type", "") != "CURVE":
        return None
    body_curve = getattr(body_object, "data", None)
    if body_curve is None:
        return None
    for spline in list(getattr(body_curve, "splines", []) or []):
        if str(getattr(spline, "type", "") or "") == "BEZIER" and bool(getattr(spline, "use_cyclic_u", False)):
            return spline
    return None


def ensure_balloon_line_mesh(
    *,
    scene,
    work,
    page,
    entry,
    body_object: bpy.types.Object,
    line_material: bpy.types.Material,
    mask_info=None,
) -> Optional[bpy.types.Object]:
    """フキダシ主線のメッシュバンドオブジェクトを生成・更新する.

    対象形状でない場合や線が無効な場合は既存のメッシュを撤去する。
    """
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return None

    if not is_mesh_band_shape(entry):
        remove_balloon_line_mesh(balloon_id)
        return None

    line_style = str(getattr(entry, "line_style", "") or "")
    line_width_mm = max(0.0, float(getattr(entry, "line_width_mm", 0.3) or 0.0))
    if line_style == "none" or line_width_mm <= 1.0e-6:
        remove_balloon_line_mesh(balloon_id)
        return None

    body_spline = _resolve_body_spline(body_object)
    if body_spline is None:
        remove_balloon_line_mesh(balloon_id)
        return None

    line_width_m = line_width_mm * 0.001

    mesh_name = _line_mesh_data_name(balloon_id)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)

    # 主線は全形状で「外側アライメント + Shapely buffer + earcut」方式に統一。
    samples = _sample_body_bezier(body_spline, SAMPLES_PER_SEGMENT)
    if len(samples) < 3:
        remove_balloon_line_mesh(balloon_id)
        return None
    valley_sharp = _valley_sharp_for_entry(entry)

    # 主線の谷/山の線幅: % 指定 (100% = base line_width, 0% = その頂点で消える)。
    # 辺全体で線形補間。動的形状のみ有効。両方 0% のとき主線全体不可視。
    shape_norm = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect"))
    # `or N` 形式は値 0.0 のとき N にフォールバックしてしまうため使わない (FloatProperty
    # は常に float を返すため getattr のデフォルトに頼ればよい)。
    line_valley_width_pct = max(0.0, min(100.0, float(getattr(entry, "line_valley_width_pct", 100.0))))
    line_peak_width_pct = max(0.0, min(100.0, float(getattr(entry, "line_peak_width_pct", 100.0))))
    line_valley_width_mm = line_width_mm * line_valley_width_pct / 100.0
    line_peak_width_mm = line_width_mm * line_peak_width_pct / 100.0
    main_line_dynamic = (
        shape_norm in {"cloud", "fluffy", "thorn", "thorn-curve"}
        and (
            abs(line_valley_width_pct - 100.0) > 1.0e-3
            or abs(line_peak_width_pct - 100.0) > 1.0e-3
        )
    )
    main_line_both_zero = (
        main_line_dynamic
        and line_valley_width_pct <= 1.0e-3
        and line_peak_width_pct <= 1.0e-3
    )

    if line_style in {"dashed", "dotted"}:
        dash_polys = _build_dashed_band_polygons(
            samples,
            line_width_m=line_width_m,
            line_style=line_style,
            valley_sharp=valley_sharp,
        )
        if not dash_polys:
            remove_balloon_line_mesh(balloon_id)
            return None
        _build_band_mesh_from_polygons(mesh, dash_polys, LINE_Z_OFFSET_M)
    elif main_line_both_zero:
        # 両方 0 = 主線全体を非表示
        remove_balloon_line_mesh(balloon_id)
        return None
    elif main_line_dynamic:
        # 主線を可変幅で構築 (谷/山の line width を辺全体で線形補間)。
        # body samples をそのまま centerline に使うため、body の鋭い谷/山がそのまま残る。
        body_center_m = (
            sum(s[0] for s in samples) / len(samples),
            sum(s[1] for s in samples) / len(samples),
        )
        sub_polys = _build_dynamic_multi_line_polygons(
            body_samples=samples,
            signed_offset_m=line_width_m * 0.5,  # 外側アライメント主線の中心線
            base_width_m=line_width_m,
            valley_width_m=line_valley_width_mm * 0.001,
            peak_width_m=line_peak_width_mm * 0.001,
            length_scale=1.0,  # 主線は length_scale 非適用 (常に閉ループ)
            valley_sharp=valley_sharp,
            balloon_center_m=body_center_m,
            peak_extension_m=0.0,
        )
        if not sub_polys:
            remove_balloon_line_mesh(balloon_id)
            return None
        _build_band_mesh_from_polygons(mesh, sub_polys, LINE_Z_OFFSET_M)
    else:
        union_result = _stroke_band_outside_union(
            samples,
            line_width_m=line_width_m,
            valley_sharp=valley_sharp,
        )
        if union_result is None:
            remove_balloon_line_mesh(balloon_id)
            return None
        outer_ring, holes = union_result
        _build_band_mesh_from_union(mesh, outer_ring, holes, LINE_Z_OFFSET_M)

    return _attach_band_mesh_object(
        obj_name=_line_mesh_object_name(balloon_id),
        mesh=mesh,
        material=line_material,
        body_object=body_object,
        scene=scene,
        kind=_KIND_LINE,
        balloon_id=balloon_id,
        visible=bool(getattr(entry, "visible", True)),
        mask_info=mask_info,
    )


def _valley_sharp_for_entry(entry) -> bool:
    sp = getattr(entry, "shape_params", None)
    return bool(getattr(sp, "cloud_valley_sharp", False))


def ensure_balloon_outer_edge_mesh(
    *,
    scene,
    work,
    page,
    entry,
    body_object: bpy.types.Object,
    outer_edge_material: bpy.types.Material,
    mask_info=None,
) -> Optional[bpy.types.Object]:
    """フキダシ外側フチのメッシュバンドを Shapely buffer で生成する."""
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return None
    if not is_shapely_line_shape(entry):
        remove_balloon_outer_edge_mesh(balloon_id)
        return None
    if not bool(getattr(entry, "outer_white_margin_enabled", False)):
        remove_balloon_outer_edge_mesh(balloon_id)
        return None
    edge_width_mm = max(0.0, float(getattr(entry, "outer_white_margin_width_mm", 0.0) or 0.0))
    if edge_width_mm <= 1.0e-6:
        remove_balloon_outer_edge_mesh(balloon_id)
        return None
    line_style = str(getattr(entry, "line_style", "") or "")
    line_width_mm = 0.0 if line_style == "none" else max(0.0, float(getattr(entry, "line_width_mm", 0.3) or 0.0))
    body_spline = _resolve_body_spline(body_object)
    if body_spline is None:
        remove_balloon_outer_edge_mesh(balloon_id)
        return None
    samples = _sample_body_bezier(body_spline, SAMPLES_PER_SEGMENT)
    if len(samples) < 3:
        remove_balloon_outer_edge_mesh(balloon_id)
        return None

    # 雲フキダシ主線は外側アライメント (body から +line_width まで body の外を太らせる)。
    # 外側フチは主線の外側に沿って張り付くため、near=line_width-overlap, far=line_width+edge_width.
    overlap_mm = min(line_width_mm, edge_width_mm) * _EDGE_OVERLAP_RATIO
    near_mm = max(0.0, line_width_mm - overlap_mm)
    far_mm = line_width_mm + edge_width_mm
    center_mm = (near_mm + far_mm) * 0.5
    band_mm = max(0.0, far_mm - near_mm)
    band_polygon = build_offset_band_polygon(
        samples,
        signed_offset_m=center_mm * 0.001,
        band_width_m=band_mm * 0.001,
        valley_sharp=_valley_sharp_for_entry(entry),
    )
    if band_polygon is None:
        remove_balloon_outer_edge_mesh(balloon_id)
        return None
    outer_ring, holes = band_polygon

    mesh_name = _outer_edge_mesh_data_name(balloon_id)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    _build_band_mesh_from_union(mesh, outer_ring, holes, OUTER_EDGE_Z_OFFSET_M)

    return _attach_band_mesh_object(
        obj_name=_outer_edge_mesh_object_name(balloon_id),
        mesh=mesh,
        material=outer_edge_material,
        body_object=body_object,
        scene=scene,
        kind=_KIND_OUTER_EDGE,
        balloon_id=balloon_id,
        visible=bool(getattr(entry, "visible", True)),
        mask_info=mask_info,
    )


def ensure_balloon_inner_edge_mesh(
    *,
    scene,
    work,
    page,
    entry,
    body_object: bpy.types.Object,
    inner_edge_material: bpy.types.Material,
    mask_info=None,
) -> Optional[bpy.types.Object]:
    """フキダシ内側フチのメッシュバンドを Shapely buffer で生成する."""
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return None
    if not is_shapely_line_shape(entry):
        remove_balloon_inner_edge_mesh(balloon_id)
        return None
    if not bool(getattr(entry, "inner_white_margin_enabled", False)):
        remove_balloon_inner_edge_mesh(balloon_id)
        return None
    edge_width_mm = max(0.0, float(getattr(entry, "inner_white_margin_width_mm", 0.0) or 0.0))
    if edge_width_mm <= 1.0e-6:
        remove_balloon_inner_edge_mesh(balloon_id)
        return None
    line_style = str(getattr(entry, "line_style", "") or "")
    line_width_mm = 0.0 if line_style == "none" else max(0.0, float(getattr(entry, "line_width_mm", 0.3) or 0.0))
    body_spline = _resolve_body_spline(body_object)
    if body_spline is None:
        remove_balloon_inner_edge_mesh(balloon_id)
        return None
    samples = _sample_body_bezier(body_spline, SAMPLES_PER_SEGMENT)
    if len(samples) < 3:
        remove_balloon_inner_edge_mesh(balloon_id)
        return None

    # 雲フキダシ主線は外側アライメントなので body 内側には主線が無い。内側フチは
    # body 境界 (+overlap だけ外向きにオーバーラップ) から内向きに edge_width 入った
    # 領域を帯にする。
    overlap_mm = min(line_width_mm, edge_width_mm) * _EDGE_OVERLAP_RATIO
    # 帯は signed offset = +overlap から -edge_width まで → 中央 = (overlap - edge_width)/2.
    center_mm = (overlap_mm - edge_width_mm) * 0.5
    band_mm = overlap_mm + edge_width_mm
    band_polygon = build_offset_band_polygon(
        samples,
        signed_offset_m=center_mm * 0.001,
        band_width_m=band_mm * 0.001,
        valley_sharp=_valley_sharp_for_entry(entry),
    )
    if band_polygon is None:
        remove_balloon_inner_edge_mesh(balloon_id)
        return None
    outer_ring, holes = band_polygon

    mesh_name = _inner_edge_mesh_data_name(balloon_id)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    _build_band_mesh_from_union(mesh, outer_ring, holes, INNER_EDGE_Z_OFFSET_M)

    return _attach_band_mesh_object(
        obj_name=_inner_edge_mesh_object_name(balloon_id),
        mesh=mesh,
        material=inner_edge_material,
        body_object=body_object,
        scene=scene,
        kind=_KIND_INNER_EDGE,
        balloon_id=balloon_id,
        visible=bool(getattr(entry, "visible", True)),
        mask_info=mask_info,
    )


def ensure_balloon_multi_line_mesh(
    *,
    scene,
    work,
    page,
    entry,
    body_object: bpy.types.Object,
    line_material: bpy.types.Material,
    mask_info=None,
) -> Optional[bpy.types.Object]:
    """フキダシ多重線 (全リング統合) のメッシュを Shapely buffer で生成する."""
    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return None
    if not is_shapely_multi_line_shape(entry):
        remove_balloon_multi_line_mesh(balloon_id)
        return None
    if str(getattr(entry, "line_style", "") or "") != "double":
        remove_balloon_multi_line_mesh(balloon_id)
        return None
    count = max(1, min(12, int(getattr(entry, "multi_line_count", 3) or 3)))
    if count < 1:
        remove_balloon_multi_line_mesh(balloon_id)
        return None
    line_width_mm = max(0.0, float(getattr(entry, "line_width_mm", 0.3) or 0.0))
    multi_width_mm = max(0.0, float(getattr(entry, "multi_line_width_mm", 0.0) or 0.0))
    spacing_mm = max(0.0, float(getattr(entry, "multi_line_spacing_mm", 0.0) or 0.0))
    width_scale = max(0.0, float(getattr(entry, "multi_line_width_scale_percent", 100.0) or 0.0)) / 100.0
    spacing_scale = max(0.0, float(getattr(entry, "multi_line_spacing_scale_percent", 100.0) or 0.0)) / 100.0
    # 谷/山の線幅は % で指定 (100% = base 多重線幅と同じ, 0% = その頂点で消える)。
    # 辺全体に渡って valley 頂点 → peak 頂点 で線形補間する。
    valley_width_pct = max(0.0, min(100.0, float(getattr(entry, "thorn_multi_line_valley_width_pct", 100.0))))
    peak_width_pct = max(0.0, min(100.0, float(getattr(entry, "thorn_multi_line_peak_width_pct", 100.0))))
    valley_width_mm = multi_width_mm * valley_width_pct / 100.0
    peak_width_mm = multi_width_mm * peak_width_pct / 100.0
    # 「長さ変化」は 主線寄り (near) と 遠い側 (far) を別々に %。リング 1 (= 主線寄り)
    # を near、リング N (= 最も遠い) を far として、リング間は線形補間。
    # 旧 `thorn_multi_line_length_scale_percent` は far のフォールバックとして扱う。
    length_near_pct = float(getattr(entry, "thorn_multi_line_length_scale_near_percent", 100.0))
    length_far_pct = float(getattr(entry, "thorn_multi_line_length_scale_far_percent", 100.0))
    legacy_length_pct = float(getattr(entry, "thorn_multi_line_length_scale_percent", 100.0))
    # legacy 値が既定でないとき far に反映 (新規ファイル互換)
    if abs(length_far_pct - 100.0) < 1.0e-3 and abs(legacy_length_pct - 100.0) > 1.0e-3:
        length_far_pct = legacy_length_pct
    length_near = max(0.0, min(1.0, length_near_pct / 100.0))
    length_far = max(0.0, min(1.0, length_far_pct / 100.0))
    cross_enabled = bool(getattr(entry, "thorn_multi_line_cross_enabled", False))
    shape_norm = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect"))
    dynamic_features_active = (
        shape_norm in {"cloud", "fluffy", "thorn", "thorn-curve"}
        and (
            length_near < 0.999
            or length_far < 0.999
            or abs(valley_width_pct - 100.0) > 1.0e-3
            or abs(peak_width_pct - 100.0) > 1.0e-3
            or cross_enabled
        )
    )
    if multi_width_mm <= 1.0e-6:
        remove_balloon_multi_line_mesh(balloon_id)
        return None
    direction = str(getattr(entry, "multi_line_direction", "outside") or "outside")
    if direction == "both":
        sides = ("inside", "outside")
    elif direction == "inside":
        sides = ("inside",)
    else:
        sides = ("outside",)

    body_spline = _resolve_body_spline(body_object)
    if body_spline is None:
        remove_balloon_multi_line_mesh(balloon_id)
        return None
    samples = _sample_body_bezier(body_spline, SAMPLES_PER_SEGMENT)
    if len(samples) < 3:
        remove_balloon_multi_line_mesh(balloon_id)
        return None

    body_poly = _build_body_polygon(samples)
    if body_poly is None:
        remove_balloon_multi_line_mesh(balloon_id)
        return None

    valley_sharp = _valley_sharp_for_entry(entry)
    # 多重線は「主線/body の外側 (内側) アウトラインから spacing 隙間 → 幅 ring_width の帯」
    # を順に並べる "edge-to-edge gap = spacing" 方式。これにより線幅が太くてもリング 1 が
    # 主線に隠れず、隣接ライン間の見た目の隙間が常に spacing で揃う。
    # spacing_scale で各リングの spacing を順番にスケールできる (例 80% で間隔が縮む)。
    # 動的形状 (cloud/fluffy/thorn/thorn-curve) で valley/peak 幅 or length_scale が
    # 非デフォルトの場合は、ring polyline を可変幅で帯化するパスに切り替える。
    # 谷幅・山幅が両方 0 のときは多重線全体を非表示にする (= polygons 追加しない)。
    both_widths_zero = (
        dynamic_features_active
        and valley_width_pct <= 1.0e-3
        and peak_width_pct <= 1.0e-3
    )
    body_center_m = (
        (sum(s[0] for s in samples) / len(samples)),
        (sum(s[1] for s in samples) / len(samples)),
    )
    polygons: list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]] = []
    running_outside_mm = line_width_mm  # 主線外側エッジ (body curve からの距離)
    running_inside_mm = 0.0  # body 境界 (body curve からの絶対距離; 内側方向では本体に主線無し)
    for ring_index in range(1, count + 1):
        ring_width_mm = multi_width_mm * (width_scale ** max(0, ring_index - 1))
        ring_spacing_mm = spacing_mm * (spacing_scale ** max(0, ring_index - 1))
        if ring_width_mm <= 1.0e-6:
            continue
        if dynamic_features_active:
            ring_valley_width_mm = valley_width_mm * (width_scale ** max(0, ring_index - 1))
            ring_peak_width_mm = peak_width_mm * (width_scale ** max(0, ring_index - 1))
            ring_extent_mm = max(ring_width_mm, ring_valley_width_mm, ring_peak_width_mm)
            # 「長さ変化」を near (主線寄り) と far (最も遠い) で別々に。リング 1 = near、
            # リング N = far として線形補間。
            if count <= 1:
                ring_length_scale = length_near
            else:
                t = float(ring_index - 1) / float(count - 1)
                ring_length_scale = length_near + (length_far - length_near) * t
            ring_length_scale = max(0.0, min(1.0, ring_length_scale))
        else:
            ring_valley_width_mm = ring_width_mm
            ring_peak_width_mm = ring_width_mm
            ring_extent_mm = ring_width_mm
            ring_length_scale = 1.0
        for side in sides:
            if side == "inside":
                ring_inner_mm = running_inside_mm + ring_spacing_mm
                ring_center_mm = ring_inner_mm + ring_extent_mm * 0.5
                signed_offset_mm = -ring_center_mm
            else:
                ring_inner_mm = running_outside_mm + ring_spacing_mm
                ring_center_mm = ring_inner_mm + ring_extent_mm * 0.5
                signed_offset_mm = ring_center_mm
            if both_widths_zero:
                # 谷幅・山幅が両方 0 → このリングは描かない (= 多重線全体が消える)
                pass
            elif dynamic_features_active:
                # 「山谷を延ばして交差」: cross_enabled かつ length < 100% のとき
                # 端点を山頂方向に延ばして、隣接 segment の端点同士が谷をまたいで
                # 交差する形に。延長量は ring 幅と spacing を合わせた程度。
                if cross_enabled and ring_length_scale < 0.999:
                    cross_extension_m = (ring_spacing_mm + ring_width_mm) * 0.001
                else:
                    cross_extension_m = 0.0
                sub_polys = _build_dynamic_multi_line_polygons(
                    body_samples=samples,
                    signed_offset_m=signed_offset_mm * 0.001,
                    base_width_m=ring_width_mm * 0.001,
                    valley_width_m=ring_valley_width_mm * 0.001,
                    peak_width_m=ring_peak_width_mm * 0.001,
                    length_scale=ring_length_scale,
                    valley_sharp=valley_sharp,
                    balloon_center_m=body_center_m,
                    cross_extension_m=cross_extension_m,
                    peak_extension_m=0.0,
                )
                polygons.extend(sub_polys)
            else:
                band = build_offset_band_polygon(
                    samples,
                    signed_offset_m=signed_offset_mm * 0.001,
                    band_width_m=ring_width_mm * 0.001,
                    valley_sharp=valley_sharp,
                    _body_poly=body_poly,
                )
                if band is not None:
                    polygons.append(band)
            # 双方向 (both) なら片方しか進めないように side ごとに running を更新
            if side == "inside":
                running_inside_mm += ring_spacing_mm + ring_extent_mm
            else:
                running_outside_mm += ring_spacing_mm + ring_extent_mm
    if not polygons:
        remove_balloon_multi_line_mesh(balloon_id)
        return None

    mesh_name = _multi_line_mesh_data_name(balloon_id)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    _build_band_mesh_from_polygons(mesh, polygons, MULTI_LINE_Z_OFFSET_M)

    return _attach_band_mesh_object(
        obj_name=_multi_line_mesh_object_name(balloon_id),
        mesh=mesh,
        material=line_material,
        body_object=body_object,
        scene=scene,
        kind=_KIND_MULTI_LINE,
        balloon_id=balloon_id,
        visible=bool(getattr(entry, "visible", True)),
        mask_info=mask_info,
    )


def _ensure_mask_clip_group() -> bpy.types.NodeTree:
    """コマ内マスクで line mesh をクリップする Geometry Nodes group を ensure する.

    入力: Geometry, Mask Object (None ならクリップ無し)
    挙動: Mask Object のジオメトリへ +Z から -Z 方向にレイキャストし、ヒットした
    face だけ残す (= マスク矩形の内側だけ残す)。本体カーブの
    `_clip_geometry_by_mask_hit` と同じロジック。
    """
    group = bpy.data.node_groups.get(MASK_CLIP_GROUP_NAME)
    if group is not None and int(group.get(PROP_GROUP_VERSION, 0) or 0) == MASK_CLIP_GROUP_VERSION:
        return group
    if group is None:
        group = bpy.data.node_groups.new(MASK_CLIP_GROUP_NAME, "GeometryNodeTree")
    group.use_fake_user = True
    # Reset interface
    try:
        for item in list(group.interface.items_tree):
            group.interface.remove(item)
    except Exception:  # noqa: BLE001
        pass
    group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket(name="マスク対象", in_out="INPUT", socket_type="NodeSocketObject")
    # Clear nodes
    for node in list(group.nodes):
        group.nodes.remove(node)
    input_node = group.nodes.new("NodeGroupInput")
    input_node.location = (-700, 0)
    output_node = group.nodes.new("NodeGroupOutput")
    output_node.location = (700, 0)
    obj_info = group.nodes.new("GeometryNodeObjectInfo")
    obj_info.label = "マスク矩形"
    obj_info.location = (-460, -180)
    try:
        obj_info.transform_space = "RELATIVE"
    except Exception:  # noqa: BLE001
        pass
    group.links.new(input_node.outputs["マスク対象"], obj_info.inputs["Object"])
    position = group.nodes.new("GeometryNodeInputPosition")
    position.location = (-460, 200)
    add = group.nodes.new("ShaderNodeVectorMath")
    add.operation = "ADD"
    add.location = (-260, 200)
    try:
        add.inputs[1].default_value = (0.0, 0.0, 1.0)
    except Exception:  # noqa: BLE001
        pass
    group.links.new(position.outputs["Position"], add.inputs[0])
    raycast = group.nodes.new("GeometryNodeRaycast")
    raycast.label = "内外判定"
    raycast.location = (-40, -80)
    group.links.new(obj_info.outputs["Geometry"], raycast.inputs["Target Geometry"])
    group.links.new(add.outputs["Vector"], raycast.inputs["Source Position"])
    try:
        raycast.inputs["Ray Direction"].default_value = (0.0, 0.0, -1.0)
        raycast.inputs["Ray Length"].default_value = 2.0
    except Exception:  # noqa: BLE001
        pass
    # マスク対象が無い場合は何もしないため、Object Info が valid かを Switch で判定する
    delete = group.nodes.new("GeometryNodeDeleteGeometry")
    delete.label = "マスク外を削除"
    delete.location = (240, 60)
    try:
        delete.domain = "FACE"
        delete.mode = "ALL"
    except Exception:  # noqa: BLE001
        pass
    bnot = group.nodes.new("FunctionNodeBooleanMath")
    bnot.operation = "NOT"
    bnot.location = (120, -120)
    group.links.new(raycast.outputs["Is Hit"], bnot.inputs[0])
    group.links.new(input_node.outputs["Geometry"], delete.inputs["Geometry"])
    group.links.new(bnot.outputs["Boolean"], delete.inputs["Selection"])
    # If mask object is None, bypass clipping
    bypass_switch = group.nodes.new("GeometryNodeSwitch")
    bypass_switch.input_type = "GEOMETRY"
    bypass_switch.label = "マスク有無で切替"
    bypass_switch.location = (480, 0)
    # When obj_info has no object, raycast Is Hit is False → bnot True → delete all faces (bad)
    # So we need a way to detect mask object presence. Use a Compare node with Object Info's Location?
    # Simpler: check raycast result; if mask object is None, raycast always returns False (no hit)
    # In that case bnot is True for every face, delete removes everything. WRONG.
    # We need to ALWAYS clip ONLY when mask is present. Use a Compare on obj_info to check if it's set.
    # GeometryNodeObjectInfo outputs Geometry which is empty if no object. We can check by
    # the geometry's domain size, but simpler: just don't add this modifier when mask is None.
    # So no bypass needed; the caller decides whether to add this modifier.
    # Remove the unused bypass_switch.
    group.nodes.remove(bypass_switch)
    group.links.new(delete.outputs["Geometry"], output_node.inputs["Geometry"])
    group[PROP_GROUP_VERSION] = MASK_CLIP_GROUP_VERSION
    return group


def _sync_mask_clip_modifier(obj: bpy.types.Object, mask_object: Optional[bpy.types.Object]) -> None:
    """line mesh にコマ内マスククリップの Geometry Nodes modifier を ensure する.

    mask_object が None の場合は modifier を撤去する。
    """
    modifier_name = "BName コマ内マスククリップ"
    existing = obj.modifiers.get(modifier_name)
    if mask_object is None:
        if existing is not None:
            try:
                obj.modifiers.remove(existing)
            except Exception:  # noqa: BLE001
                pass
        return
    group = _ensure_mask_clip_group()
    if existing is None:
        existing = obj.modifiers.new(modifier_name, "NODES")
    existing.node_group = group
    # Set mask_object input via interface identifier
    target_identifier = None
    for item in getattr(group.interface, "items_tree", []) or []:
        if getattr(item, "in_out", "") != "INPUT":
            continue
        if str(getattr(item, "name", "") or "") == "マスク対象":
            target_identifier = getattr(item, "identifier", None)
            break
    if target_identifier:
        try:
            existing[target_identifier] = mask_object
        except Exception:  # noqa: BLE001
            pass


def _remove_named_band_mesh(obj_name: str) -> None:
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        return
    data = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:  # noqa: BLE001
        _logger.exception("balloon band mesh removal failed: %s", obj_name)
        return
    if data is not None and getattr(data, "users", 0) == 0:
        try:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
        except Exception:  # noqa: BLE001
            pass


def remove_balloon_line_mesh(balloon_id: str) -> None:
    if not balloon_id:
        return
    _remove_named_band_mesh(_line_mesh_object_name(balloon_id))


def remove_balloon_outer_edge_mesh(balloon_id: str) -> None:
    if not balloon_id:
        return
    _remove_named_band_mesh(_outer_edge_mesh_object_name(balloon_id))


def remove_balloon_inner_edge_mesh(balloon_id: str) -> None:
    if not balloon_id:
        return
    _remove_named_band_mesh(_inner_edge_mesh_object_name(balloon_id))


def remove_balloon_multi_line_mesh(balloon_id: str) -> None:
    if not balloon_id:
        return
    _remove_named_band_mesh(_multi_line_mesh_object_name(balloon_id))


def remove_all_balloon_band_meshes(balloon_id: str) -> None:
    """主線・フチ・多重線の Mesh をまとめて撤去する."""
    remove_balloon_line_mesh(balloon_id)
    remove_balloon_outer_edge_mesh(balloon_id)
    remove_balloon_inner_edge_mesh(balloon_id)
    remove_balloon_multi_line_mesh(balloon_id)


def cleanup_orphan_line_meshes(valid_balloon_ids: set[str]) -> int:
    """主線・フチ・多重線の Mesh のうち、有効な balloon id を持たないものを撤去する."""
    removed = 0
    for obj in list(bpy.data.objects):
        kind = str(obj.get(PROP_BALLOON_LINE_MESH_KIND, "") or "")
        if kind not in _ALL_KINDS:
            continue
        owner_id = str(obj.get(PROP_BALLOON_LINE_MESH_OWNER_ID, "") or "")
        if owner_id and owner_id not in valid_balloon_ids:
            data = getattr(obj, "data", None)
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:  # noqa: BLE001
                pass
            if data is not None and getattr(data, "users", 0) == 0:
                try:
                    if isinstance(data, bpy.types.Mesh):
                        bpy.data.meshes.remove(data)
                except Exception:  # noqa: BLE001
                    pass
            removed += 1
    return removed
