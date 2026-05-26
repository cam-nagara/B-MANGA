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
# トゲ (直線) は「長さ変化」「谷/山の線幅」など専用ロジックを持つため、
# 現状は cloud のみ Shapely 多重線対応。それ以外は legacy curve 多重線が継続。
SHAPELY_MULTI_LINE_SHAPES = {"cloud"}


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
    miter_limit: float = 2.5,
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
    mitre = float(miter_limit) if valley_sharp else 5.0
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
    miter_limit: float = 2.5,
) -> Optional[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]:
    """主線 (外側アライメント) の線バンドを Shapely buffer で構築する."""
    return build_offset_band_polygon(
        samples,
        signed_offset_m=line_width_m * 0.5,
        band_width_m=line_width_m,
        valley_sharp=valley_sharp,
        miter_limit=miter_limit,
    )


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
    union_result = _stroke_band_outside_union(
        samples,
        line_width_m=line_width_m,
        valley_sharp=_valley_sharp_for_entry(entry),
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
    if count <= 1:
        remove_balloon_multi_line_mesh(balloon_id)
        return None
    line_width_mm = max(0.0, float(getattr(entry, "line_width_mm", 0.3) or 0.0))
    multi_width_mm = max(0.0, float(getattr(entry, "multi_line_width_mm", 0.0) or 0.0))
    spacing_mm = max(0.0, float(getattr(entry, "multi_line_spacing_mm", 0.0) or 0.0))
    width_scale = max(0.0, float(getattr(entry, "multi_line_width_scale_percent", 100.0) or 0.0)) / 100.0
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
    # 多重線のリング中心は「主線中心からの距離 = spacing * ring_index」を満たすように配置する。
    # これによりリング同士の中心間距離も、主線中心とリング1中心の距離も等しく spacing になる。
    # 外側アライメント主線 (body 0 〜 +line_width) なので主線中心は body + line_width/2.
    # 内側方向には主線が無いため、内側多重線は body 境界を基準にリング中心を spacing 刻みで並べる。
    base_outside_mm = line_width_mm * 0.5
    base_inside_mm = 0.0
    polygons: list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]] = []
    # 「線の本数 N」は多重線として描かれるリング数を意味する (主線本体はカウント外)。
    for ring_index in range(1, count + 1):
        ring_width_mm = multi_width_mm * (width_scale ** max(0, ring_index - 1))
        if ring_width_mm <= 1.0e-6:
            continue
        for side in sides:
            if side == "inside":
                signed_offset_mm = -(base_inside_mm + spacing_mm * ring_index)
            else:
                signed_offset_mm = base_outside_mm + spacing_mm * ring_index
            band = build_offset_band_polygon(
                samples,
                signed_offset_m=signed_offset_mm * 0.001,
                band_width_m=ring_width_mm * 0.001,
                valley_sharp=valley_sharp,
                _body_poly=body_poly,
            )
            if band is None:
                continue
            polygons.append(band)
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
