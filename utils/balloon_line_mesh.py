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

from . import object_preserve

from . import balloon_tail_boolean
from . import free_transform
from . import balloon_shapes
from . import line_pattern
from . import log
from . import object_naming as on
from . import python_deps
from .geom import Rect, mm_to_m

_logger = log.get_logger(__name__)

BALLOON_LINE_MESH_NAME_PREFIX = "balloon_line_mesh_"
BALLOON_OUTER_EDGE_MESH_NAME_PREFIX = "balloon_outer_edge_mesh_"
BALLOON_INNER_EDGE_MESH_NAME_PREFIX = "balloon_inner_edge_mesh_"
BALLOON_MULTI_LINE_MESH_NAME_PREFIX = "balloon_multi_line_mesh_"
BALLOON_FLASH_WHITE_LINE_MESH_NAME_PREFIX = "balloon_flash_white_line_mesh_"
PROP_BALLOON_LINE_MESH_KIND = "bname_balloon_line_mesh_kind"
PROP_BALLOON_LINE_MESH_OWNER_ID = "bname_balloon_line_mesh_owner_id"

# kind タグ
_KIND_LINE = "balloon_line_mesh"
_KIND_OUTER_EDGE = "balloon_outer_edge_mesh"
_KIND_INNER_EDGE = "balloon_inner_edge_mesh"
_KIND_MULTI_LINE = "balloon_multi_line_mesh"
_KIND_FLASH_WHITE_LINE = "balloon_flash_white_line_mesh"
_KIND_TAIL_MAIN_LINE = "balloon_tail_main_line_mesh"
_ALL_KINDS = {
    _KIND_LINE,
    _KIND_OUTER_EDGE,
    _KIND_INNER_EDGE,
    _KIND_MULTI_LINE,
    _KIND_FLASH_WHITE_LINE,
    _KIND_TAIL_MAIN_LINE,
    # 連続楕円しっぽ (balloon_tail_ellipse_mesh) / 線種図形・画像 (balloon_line_decor_mesh)
    "balloon_tail_ellipse_fill_mesh",
    "balloon_tail_ellipse_line_mesh",
    "balloon_line_shape_mesh",
    "balloon_line_image_mesh",
}

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
FLASH_WHITE_LINE_Z_OFFSET_M = 0.000050

_EDGE_OVERLAP_RATIO = 0.06

# コマ内マスク用 Geometry Nodes group
MASK_CLIP_GROUP_NAME = "BName_GN_BalloonLineMeshClip"
MASK_CLIP_GROUP_VERSION = 1
PROP_GROUP_VERSION = "bname_group_version"

# 主線・外側フチ・内側フチを Shapely buffer + earcut で外部 Mesh として描画する形状。
# 全ての Meldex フキダシ形状とカスタム形状で同じ方式に統一する。
# (custom は preset の頂点列ベースの polygon。 rect/octagon と同様に Shapely
# buffer で band 化できる)
SHAPELY_LINE_SHAPES = set(balloon_shapes.MELDEX_CARD_SHAPES) | {"custom"}

# 後方互換 (Mesh 直接構築方式で主線が描画される形状)
MESH_BAND_LINE_SHAPES = set(SHAPELY_LINE_SHAPES)

# 多重線も Shapely buffer 方式で外部 Mesh として描画する形状。
# 全形状で統一: 角の鋭い形状でもオフセット曲線の自己交差/ごちゃつきや
# 意図しないトゲが出ないように Shapely buffer に統一する。
# (トゲ直線専用の「長さ変化」「谷/山の線幅」は本経路では適用されない — 形状が
# 谷で自己交差しないリングを優先する設計判断)
SHAPELY_MULTI_LINE_SHAPES = set(balloon_shapes.MELDEX_CARD_SHAPES) | {"custom"}

_DYNAMIC_WIDTH_SHAPES = {"cloud", "fluffy", "thorn", "thorn-curve"}
_ROUNDED_PEAK_SHAPES = {"cloud", "fluffy"}


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


def entry_line_width_scale(entry) -> float:
    try:
        return max(0.01, float(getattr(entry, "free_transform_line_width_scale", 1.0) or 1.0))
    except Exception:  # noqa: BLE001
        return 1.0


def scaled_entry_width_mm(entry, attr: str, default: float = 0.0) -> float:
    try:
        value = float(getattr(entry, attr, default) or 0.0)
    except Exception:  # noqa: BLE001
        value = float(default)
    return max(0.0, value) * entry_line_width_scale(entry)


def _body_per_anchor_radii(spline) -> list[float]:
    """本体 Bezier の各 anchor の radius (per-point) を順に返す."""
    pts = list(getattr(spline, "bezier_points", []) or [])
    return [max(0.0, float(getattr(p, "radius", 1.0) or 0.0)) for p in pts]


def _sample_anchor_loop_to_local_m(
    anchors: Sequence[balloon_shapes.BezierAnchor],
    offset_mm: tuple[float, float],
    samples_per_segment: int,
    entry=None,
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
            if entry is not None:
                x_mm, y_mm = free_transform.transform_entry_local_point(entry, x_mm, y_mm)
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


def _sample_body_nurbs(spline, samples_per_segment: int) -> list[tuple[float, float, float]]:
    """NURBS 閉スプラインをサンプリングして、(x, y, per_point_radius) のタプル列を返す.

    閉じた一様 3 次 B-spline として評価する (重みは等しい前提)。
    """
    samples: list[tuple[float, float, float]] = []
    if str(getattr(spline, "type", "") or "") != "NURBS":
        return samples
    if not bool(getattr(spline, "use_cyclic_u", False)):
        return samples
    points = list(getattr(spline, "points", []) or [])
    n = len(points)
    if n < 3:
        return samples
    coords = [(float(p.co.x), float(p.co.y)) for p in points]
    radii = [max(0.0, float(getattr(p, "radius", 1.0) or 0.0)) for p in points]
    steps = max(4, int(samples_per_segment))
    for i in range(n):
        p0 = coords[(i - 1) % n]
        p1 = coords[i]
        p2 = coords[(i + 1) % n]
        p3 = coords[(i + 2) % n]
        r1 = radii[i]
        r2 = radii[(i + 1) % n]
        for step in range(steps):
            t = step / steps
            t2 = t * t
            t3 = t2 * t
            # 一様 3 次 B-spline 基底
            b0 = (1.0 - 3.0 * t + 3.0 * t2 - t3) / 6.0
            b1 = (4.0 - 6.0 * t2 + 3.0 * t3) / 6.0
            b2 = (1.0 + 3.0 * t + 3.0 * t2 - 3.0 * t3) / 6.0
            b3 = t3 / 6.0
            x = b0 * p0[0] + b1 * p1[0] + b2 * p2[0] + b3 * p3[0]
            y = b0 * p0[1] + b1 * p1[1] + b2 * p2[1] + b3 * p3[1]
            samples.append((x, y, r1 * (1.0 - t) + r2 * t))
    return samples


def sample_body_spline(spline, samples_per_segment: int) -> list[tuple[float, float, float]]:
    """本体スプライン (BEZIER / NURBS) をサンプリングする."""
    spline_type = str(getattr(spline, "type", "") or "")
    if spline_type == "NURBS":
        return _sample_body_nurbs(spline, samples_per_segment)
    return _sample_body_bezier(spline, samples_per_segment)


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


def _tail_polygons_for_entry_local_m(entry, tail_filter=None) -> list[list[tuple[float, float]]]:
    """entry のしっぽを balloon-local m の polygon 点列に変換する."""
    from . import balloon_tail_geom

    tails = list(getattr(entry, "tails", []) or [])
    if not tails:
        return []
    rect = Rect(
        0.0,
        0.0,
        max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0)),
        max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0)),
    )
    ox_mm, oy_mm = _entry_local_offset_mm(entry)
    out: list[list[tuple[float, float]]] = []
    for tail in tails:
        if tail_filter is not None and not tail_filter(tail):
            continue
        try:
            pts_mm = balloon_tail_geom.joined_polygon_for_tail(rect, tail)
            pts_mm = free_transform.transform_entry_local_points(entry, pts_mm)
        except Exception:  # noqa: BLE001
            continue
        if len(pts_mm) < 3:
            continue
        out.append([(mm_to_m(x + ox_mm), mm_to_m(y + oy_mm)) for x, y in pts_mm])
    return out


def ellipse_polygons_for_entry_local_m(entry) -> list[list[tuple[float, float]]]:
    """連続楕円しっぽの全楕円を balloon-local m の polygon 点列で返す."""
    from . import balloon_tail_ellipse_mesh

    return balloon_tail_ellipse_mesh._ellipse_polygons_local_m(entry)


def sharp_tail_tip_infos_local_m(entry):
    """「角を尖らせる」くさびしっぽの (中心線, 半幅, くさび多角形) を balloon-local m で返す.

    主線の帯の先端を「抜き」のように細く絞る加工 (apply_sharp_tail_tips) に使う。
    """
    from . import balloon_tail_geom

    rect = Rect(
        0.0,
        0.0,
        max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0)),
        max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0)),
    )
    ox_mm, oy_mm = _entry_local_offset_mm(entry)
    out = []
    for tail in getattr(entry, "tails", []) or []:
        if not bool(getattr(tail, "sharp_corners", False)):
            continue
        try:
            region_mm = balloon_tail_geom.polygon_for_tail(rect, tail)
            if len(region_mm) < 3:
                continue  # 楕円・線しっぽは対象外
            centerline_mm, halves_mm = balloon_tail_geom.centerline_with_halfwidths(rect, tail)
            if len(centerline_mm) < 2:
                continue
            centerline_mm = free_transform.transform_entry_local_points(entry, centerline_mm)
            region_mm = free_transform.transform_entry_local_points(entry, region_mm)
        except Exception:  # noqa: BLE001
            continue
        out.append((
            [(mm_to_m(x + ox_mm), mm_to_m(y + oy_mm)) for x, y in centerline_mm],
            [h * 0.001 for h in halves_mm],
            [(mm_to_m(x + ox_mm), mm_to_m(y + oy_mm)) for x, y in region_mm],
        ))
    return out


def _body_union_with_tails(entry, body_samples):
    """本体としっぽを結合した Shapely Polygon を返す。結合できない場合は None.

    くさびしっぽに加え、連続楕円しっぽの「本体に重なる楕円」も結合する
    (重ならない楕円は従来どおり個別メッシュで描く)。
    """
    tail_pts = _tail_polygons_for_entry_local_m(entry)
    ellipse_pts = ellipse_polygons_for_entry_local_m(entry)
    if not tail_pts and not ellipse_pts:
        return None
    body_pts = [(float(s[0]), float(s[1])) for s in body_samples]
    merged, changed = balloon_tail_boolean.combine_body_with_tail_polygons(
        body_pts, tail_pts, union_only_points_list=ellipse_pts
    )
    if not changed:
        return None
    return merged


def _outline_samples_with_tails(entry, body_samples) -> tuple[list[tuple[float, float, float]], bool]:
    """本体+しっぽの外周サンプルと、しっぽを結合できたかを返す."""
    merged = _body_union_with_tails(entry, body_samples)
    if merged is None:
        return list(body_samples), False
    try:
        coords = [(float(x), float(y)) for x, y in merged.exterior.coords]
    except Exception:  # noqa: BLE001
        return list(body_samples), False
    if len(coords) >= 2 and coords[0] == coords[-1]:
        coords = coords[:-1]
    if len(coords) < 3:
        return list(body_samples), False
    return [(x, y, 1.0) for x, y in coords], True


def build_offset_band_polygon(
    body_samples: Sequence,
    *,
    signed_offset_m: float,
    band_width_m: float,
    valley_sharp: bool,
    miter_limit: float = _SHARP_MITRE_LIMIT,
    peaks_rounded: bool = False,
    _body_poly=None,
) -> Optional[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]:
    """本体多角形から signed_offset_m を中心に幅 band_width_m の帯を構築する.

    signed_offset_m: 正=本体の外側へ、負=本体の内側へ。
    band_width_m: 帯の幅 (常に正)。
    valley_sharp=True で mitre join (谷で鋭角), False で round join (谷で丸み).
    peaks_rounded=True かつ外向き (signed_offset_m>0) のときは凸の山頂を round join
    で丸める (雲/フワフワのように山頂が丸い形状で、 外側へ広げた帯の山頂が mitre で
    尖るのを防ぐ)。 谷は外向きオフセットでは交点として鋭く残るので valley_sharp は保つ。

    戻り値: (outer_ring, holes) の対。失敗時 None。
    """
    if band_width_m <= 1.0e-9:
        return None
    body_poly = _body_poly if _body_poly is not None else _build_body_polygon(body_samples)
    if body_poly is None:
        return None

    if signed_offset_m > 0.0 and peaks_rounded:
        join = 1  # round (山頂を丸める)
        mitre = _ROUND_MITRE_LIMIT
    else:
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


def _resample_clean_offset(
    body_poly,
    signed_offset_m: float,
    n: int,
    valley_sharp: bool,
    peaks_rounded: bool = False,
) -> list[tuple[float, float, float]] | None:
    """body を signed_offset_m だけ Shapely buffer し、 その外周を n 点に等間隔再サンプルする.

    多重線リングの centerline を「法線方向の単純オフセット」(凹谷で自己交差) ではなく
    Shapely のクリーンなオフセット曲線にするために使う。 これで曲線形状でも
    リングが自己交差せず、 谷/山可変幅でももつれない。
    """
    if body_poly is None or n < 6:
        return None
    # 外向きオフセット (signed_offset_m>0) では凸の山頂が join_style の対象になる。
    # 山頂が丸い形状 (雲/フワフワ) では、 主線が本体カーブに沿って丸い山頂を持つので
    # 多重線も round join で山頂を丸いままにする。 mitre だと山頂が鋭いスパイクに
    # 尖ってしまい、 主線は丸いのに多重線だけ尖る不具合になる。 凹の谷は外向き
    # オフセットでは join 対象にならず、 オフセット辺の交点として自然に鋭く残るため
    # valley_sharp は保たれる。 山頂が尖る形状 (トゲ曲線) は主線が尖っているので
    # 多重線も mitre のまま尖らせて主線と揃える。 内向き (signed_offset_m<0) は谷が
    # join 対象になるので、 谷の鋭さを valley_sharp に従って mitre/round で決める。
    if signed_offset_m > 0.0 and peaks_rounded:
        join = 1
        mitre = _ROUND_MITRE_LIMIT
    else:
        join = 2 if valley_sharp else 1
        mitre = _SHARP_MITRE_LIMIT if valley_sharp else _ROUND_MITRE_LIMIT
    try:
        off = body_poly.buffer(signed_offset_m, join_style=join, mitre_limit=mitre)
    except Exception:  # noqa: BLE001
        return None
    if off is None or off.is_empty:
        return None
    if off.geom_type == "MultiPolygon":
        off = max(off.geoms, key=lambda g: g.area)
    if off.geom_type != "Polygon":
        return None
    ext = off.exterior
    length = ext.length
    if length <= 1.0e-9:
        return None
    out: list[tuple[float, float, float]] = []
    for i in range(n):
        pt = ext.interpolate(length * (i / n))
        out.append((float(pt.x), float(pt.y), 0.0))
    return out


def _stroke_band_outside_union(
    samples: Sequence[tuple[float, float, float]],
    *,
    line_width_m: float,
    valley_sharp: bool,
    miter_limit: float = _SHARP_MITRE_LIMIT,
    peaks_rounded: bool = False,
) -> Optional[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]:
    """主線 (外側アライメント) の線バンドを Shapely buffer で構築する."""
    return build_offset_band_polygon(
        samples,
        signed_offset_m=line_width_m * 0.5,
        band_width_m=line_width_m,
        valley_sharp=valley_sharp,
        miter_limit=miter_limit,
        peaks_rounded=peaks_rounded,
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


def _point_on_polyline_by_arc_length(
    pts_closed: Sequence[tuple[float, float]],
    cum: Sequence[float],
    target_len: float,
) -> tuple[float, float] | None:
    if len(pts_closed) < 2 or len(cum) != len(pts_closed):
        return None
    total = float(cum[-1])
    if total <= 1.0e-9:
        return None
    target = max(0.0, min(float(target_len), total))
    for i in range(len(pts_closed) - 1):
        seg_start = float(cum[i])
        seg_end = float(cum[i + 1])
        if target > seg_end and i < len(pts_closed) - 2:
            continue
        seg_len = seg_end - seg_start
        if seg_len <= 1.0e-12:
            continue
        p0 = pts_closed[i]
        p1 = pts_closed[i + 1]
        t = (target - seg_start) / seg_len
        return (
            p0[0] + (p1[0] - p0[0]) * t,
            p0[1] + (p1[1] - p0[1]) * t,
        )
    return pts_closed[-1]


def _build_dashed_band_polygons(
    body_samples: Sequence,
    *,
    line_width_m: float,
    line_style: str,
    valley_sharp: bool,
    dash_segment_mm: float = 0.0,
    dash_gap_mm: float = 0.0,
    dotted_gap_mm: float = 0.0,
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
        from shapely.geometry import LineString, Point  # type: ignore
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

    half_width_m = line_width_m * 0.5
    polygons: list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]] = []
    if line_style == "dotted":
        spacing_m = max(
            line_width_m + max(0.0, float(dotted_gap_mm)) * 0.001,
            line_width_m * 1.05,
            1.0e-6,
        )
        num_dots = max(1, int(round(total_len / spacing_m)))
        spacing_m = total_len / num_dots
        for k in range(num_dots):
            center = _point_on_polyline_by_arc_length(pts, cum, k * spacing_m)
            if center is None:
                continue
            try:
                dot = Point(center).buffer(half_width_m, resolution=16)
            except Exception:  # noqa: BLE001
                continue
            if dot.is_empty or dot.area <= 1.0e-12:
                continue
            outer_ring = list(dot.exterior.coords)
            holes = [list(r.coords) for r in dot.interiors]
            polygons.append((outer_ring, holes))
        return polygons

    dash_len_m = max(0.05, float(dash_segment_mm)) * 0.001
    gap_len_m = max(0.0, float(dash_gap_mm)) * 0.001
    period_len = max(dash_len_m + gap_len_m, dash_len_m, 1.0e-6)
    start_len = 0.0
    while start_len < total_len - 1.0e-9:
        end_len = min(total_len, start_len + dash_len_m)
        sub_pts = _polyline_subset_by_arc_length(pts, cum, start_len, end_len)
        if len(sub_pts) < 2:
            start_len += period_len
            continue
        try:
            line = LineString(sub_pts)
            if line.length <= 1.0e-9:
                start_len += period_len
                continue
            # 端はしっぽの線と同じ丸キャップ (cap_style=1)
            band = line.buffer(half_width_m, cap_style=1, join_style=1)
        except Exception:  # noqa: BLE001
            start_len += period_len
            continue
        if band.is_empty:
            start_len += period_len
            continue
        if band.geom_type == "Polygon":
            sub_polys = [band]
        elif band.geom_type == "MultiPolygon":
            sub_polys = list(band.geoms)
        else:
            start_len += period_len
            continue
        for sub in sub_polys:
            if sub.area <= 1.0e-12:
                continue
            outer_ring = list(sub.exterior.coords)
            holes = [list(r.coords) for r in sub.interiors]
            polygons.append((outer_ring, holes))
        start_len += period_len
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


def _detect_anchor_peaks_valleys(
    pts: Sequence[tuple[float, float]],
    balloon_center: tuple[float, float],
    *,
    samples_per_segment: int,
) -> tuple[list[int], list[int]]:
    """Bezier anchor 単位で peak/valley を検出する。

    body sample は anchor が `samples_per_segment` ごとに並んでいる構造を
    持つ。各 anchor の radial を 隣接 anchor と比較して、ある anchor が
    両隣より radial が大きければ peak、小さければ valley とする。

    この方式は:
      - 山の高さがバラつく (jitter) 場合でも、すべての主山頂を正しく検出できる
      - サブバンプ (小山) が anchor として挿入されても、その小山は両隣の主山/谷
        より radial が小さい/大きい一方なので、anchor-level の local max/min に
        ならず、主山/主谷だけが検出される
    """
    n = len(pts)
    if n < 6 or samples_per_segment <= 0:
        return [], []
    cx, cy = balloon_center
    anchor_count = max(2, n // samples_per_segment)
    radii: list[float] = []
    indices: list[int] = []
    for k in range(anchor_count):
        idx = k * samples_per_segment
        if idx >= n:
            break
        indices.append(idx)
        radii.append(math.hypot(pts[idx][0] - cx, pts[idx][1] - cy))
    if len(indices) < 3:
        return [], []
    m = len(indices)
    peaks: list[int] = []
    valleys: list[int] = []
    for k in range(m):
        prev_r = radii[(k - 1) % m]
        next_r = radii[(k + 1) % m]
        if radii[k] > prev_r and radii[k] > next_r:
            peaks.append(indices[k])
        elif radii[k] < prev_r and radii[k] < next_r:
            valleys.append(indices[k])
    return peaks, valleys


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


def _detect_radial_peaks_valleys(
    pts: Sequence[tuple[float, float]],
    balloon_center: tuple[float, float],
    *,
    window: int,
) -> tuple[list[int], list[int]]:
    """サンプル列上で balloon 中心からの radial 距離の局所最大(山頂)・最小(谷底)を返す.

    雲/フワフワ/トゲ曲線は bezier アンカーが山頂(バンプ先)や谷底に無い (アンカーは
    谷や曲線途中)。 そのため谷/山の線幅の基準点が取れない。 本関数は曲線そのものの
    radial 極値を検出することで、 アンカーの有無に依らず「山の先・谷の底・小山の先」を
    頂点として扱う (= ユーザー指示)。 滑らかな bezier サンプル前提なので、 ±window の
    局所比較で十分。 近接する極値はクラスタ統合して 1 点に集約する。
    """
    n = len(pts)
    if n < 6 or window < 1:
        return [], []
    cx, cy = balloon_center
    r = [math.hypot(p[0] - cx, p[1] - cy) for p in pts]
    raw_p: list[int] = []
    raw_v: list[int] = []
    for i in range(n):
        ge = True
        le = True
        strict_hi = False
        strict_lo = False
        for j in range(1, window + 1):
            a = r[(i - j) % n]
            b = r[(i + j) % n]
            if r[i] < a or r[i] < b:
                ge = False
            if r[i] > a or r[i] > b:
                le = False
            if r[i] > a or r[i] > b:
                strict_hi = True
            if r[i] < a or r[i] < b:
                strict_lo = True
        if ge and strict_hi:
            raw_p.append(i)
        elif le and strict_lo:
            raw_v.append(i)

    def _cluster(idxs: list[int], want_max: bool) -> list[int]:
        if not idxs:
            return []
        idxs = sorted(idxs)
        clusters: list[list[int]] = [[idxs[0]]]
        for k in idxs[1:]:
            if k - clusters[-1][-1] <= window:
                clusters[-1].append(k)
            else:
                clusters.append([k])
        # 円環ラップ: 先頭と末尾クラスタが近ければ統合
        if len(clusters) >= 2 and (clusters[0][0] + n - clusters[-1][-1]) <= window:
            clusters[0] = clusters.pop() + clusters[0]
        pick = (lambda c: max(c, key=lambda x: r[x])) if want_max else (lambda c: min(c, key=lambda x: r[x]))
        return sorted(pick(c) for c in clusters)

    return _cluster(raw_p, True), _cluster(raw_v, False)


def _circular_dist(a: int, b: int, n: int) -> int:
    d = abs(a - b) % n
    return min(d, n - d)


def _is_straight_edged(pts: Sequence[tuple[float, float]], samples_per_segment: int) -> bool:
    """body サンプルが「アンカー間が直線」(= トゲ直線) かどうかを判定する.

    各 bezier セグメント (アンカー間 = samples_per_segment 点) の中点サンプルが、
    両端アンカーを結ぶ直線上に乗っていれば直線辺。 雲/フワフワ/トゲ曲線のような
    曲線セグメントでは中点が大きく膨らむため False。 anchor-only サンプリングは
    直線辺でのみ使う (曲線を多角形化しないため) 判定に用いる。
    """
    n = len(pts)
    if samples_per_segment < 3 or n < samples_per_segment * 3:
        return False
    total = 0
    for k in range(0, n - samples_per_segment + 1, samples_per_segment):
        ax, ay = pts[k]
        bx, by = pts[(k + samples_per_segment) % n]
        abx, aby = bx - ax, by - ay
        ablen = math.hypot(abx, aby)
        if ablen < 1.0e-9:
            continue
        mx, my = pts[(k + samples_per_segment // 2) % n]
        perp = abs((mx - ax) * aby - (my - ay) * abx) / ablen
        total += 1
        if perp > ablen * 0.02:
            return False
    return total > 0


def _line_intersection(p1, p2, p3, p4):
    """無限直線 p1p2 と p3p4 の交点を返す。 平行/退化なら None。"""
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1.0e-12:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def stroke_variable_width(
    centerline: Sequence[tuple[float, float]],
    half_widths: Sequence[float],
    *,
    closed: bool,
    round_joins: bool = True,
    quad_segs: int = 8,
    mitre_limit: float = 8.0,
):
    """中心線に沿った可変幅ストロークを Shapely の union だけで構築する (堅牢プリミティブ).

    各セグメントを「セグメント法線」(頂点 bisector ではない=鋭い谷でも縮退しない) で
    台形にして `unary_union` する。union 結果は常に valid なので、自己交差トゲ (バーブ)
    も太さの暴れ (ガタガタ) も原理的に発生しない。半幅 0 の頂点では台形が中心線の 1 点へ
    潰れ、きれいな鋭いピンチになる。
    - round_joins=True : 頂点に半幅の円を足す → 丸い山 (雲・フワフワ向き)。
    - round_joins=False: 隣接セグメントのオフセット辺の交点 (mitre) を頂点コーナーに使い、
      鋭い山頂・谷を保つ (トゲ・トゲ曲線向き)。凸角の隙間も mitre で埋まる。
    戻り値は Shapely geometry (Polygon/MultiPolygon) または None。
    """
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import Polygon, Point  # type: ignore
        from shapely.ops import unary_union  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    n = len(centerline)
    if n < 2:
        return None
    seg_count = n if closed else n - 1

    def _seg_normal(i, j):
        dx = centerline[j][0] - centerline[i][0]
        dy = centerline[j][1] - centerline[i][1]
        L = math.hypot(dx, dy)
        if L < 1.0e-12:
            return None
        return (-dy / L, dx / L)

    # 各頂点の ±side コーナー (round=半幅オフセット点, sharp=隣接辺オフセット交点=mitre)
    def _corner(i, side):
        p = centerline[i]
        h = max(0.0, float(half_widths[i]))
        nb_prev = _seg_normal((i - 1) % n, i) if (closed or i > 0) else None
        nb_next = _seg_normal(i, (i + 1) % n) if (closed or i < n - 1) else None
        n_use = nb_next or nb_prev
        if n_use is None:
            return p
        if round_joins or nb_prev is None or nb_next is None:
            return (p[0] + side * n_use[0] * h, p[1] + side * n_use[1] * h)
        # mitre: 2 本のオフセット辺の交点
        a0 = (p[0] + side * nb_prev[0] * h, p[1] + side * nb_prev[1] * h)
        a1 = (centerline[(i - 1) % n][0] + side * nb_prev[0] * h,
              centerline[(i - 1) % n][1] + side * nb_prev[1] * h)
        b0 = (p[0] + side * nb_next[0] * h, p[1] + side * nb_next[1] * h)
        b1 = (centerline[(i + 1) % n][0] + side * nb_next[0] * h,
              centerline[(i + 1) % n][1] + side * nb_next[1] * h)
        hit = _line_intersection(a1, a0, b0, b1)
        if hit is None:
            return (p[0] + side * n_use[0] * h, p[1] + side * n_use[1] * h)
        if math.hypot(hit[0] - p[0], hit[1] - p[1]) > h * mitre_limit + 1.0e-9:
            # mitre が伸びすぎ → bevel (頂点で2点に分けるが、ここでは近い方の辺点)
            return (p[0] + side * n_use[0] * h, p[1] + side * n_use[1] * h)
        return hit

    pieces = []
    for i in range(seg_count):
        j = (i + 1) % n
        ha = max(0.0, float(half_widths[i]))
        hb = max(0.0, float(half_widths[j]))
        if ha < 1.0e-9 and hb < 1.0e-9:
            continue
        if _seg_normal(i, j) is None:
            continue
        pa_plus = _corner(i, +1) if ha >= 1.0e-9 else centerline[i]
        pa_minus = _corner(i, -1) if ha >= 1.0e-9 else centerline[i]
        pb_plus = _corner(j, +1) if hb >= 1.0e-9 else centerline[j]
        pb_minus = _corner(j, -1) if hb >= 1.0e-9 else centerline[j]
        trap = Polygon([pa_plus, pb_plus, pb_minus, pa_minus])
        if not trap.is_valid:
            trap = trap.buffer(0)
        if (not trap.is_empty) and trap.area > 1.0e-12:
            pieces.append(trap)
        if round_joins:
            a = centerline[i]
            b = centerline[j]
            if ha >= 1.0e-9:
                pieces.append(Point(a).buffer(ha, quad_segs=quad_segs))
            if hb >= 1.0e-9:
                pieces.append(Point(b).buffer(hb, quad_segs=quad_segs))
    if not pieces:
        return None
    geom = unary_union(pieces)
    if geom.is_empty:
        return None
    return geom


def _geom_to_ring_holes_list(
    geom,
) -> list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]:
    """Shapely geometry を [(outer_ring, [holes...]), ...] のリストへ展開する."""
    if geom is None or getattr(geom, "is_empty", True):
        return []
    geoms = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    out: list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]] = []
    for g in geoms:
        if g.geom_type != "Polygon" or g.area <= 1.0e-12:
            continue
        outer = [(float(x), float(y)) for x, y in g.exterior.coords]
        holes = [[(float(x), float(y)) for x, y in r.coords] for r in g.interiors]
        out.append((outer, holes))
    return out


def _build_variable_width_band_segment(
    pts: Sequence[tuple[float, float]],
    widths: Sequence[float],
    normals: Sequence[tuple[float, float]],
    indices: Sequence[int],
    *,
    closed: bool,
    outside_align: bool = False,
    round_joins: bool = True,
) -> list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]:
    """指定インデックス列の centerline に沿って、可変幅帯を構築する (堅牢版).

    `stroke_variable_width` (Shapely union) で帯を作る。`normals` はもう使わない
    (セグメント法線を内部で計算)。
    - outside_align=True: centerline=body 境界とみなし、全幅で centered ストローク後、
      閉ループなら `difference(Polygon(centerline))` で外側半分だけ残す。これで内側
      エッジは body のまま=本体の鋭い谷/山の角がそのまま保たれ、外周に余計なトゲが出ない。
    - outside_align=False: 半幅で centered ストローク (帯は中心線を挟む)。
    戻り値は [(outer, holes), ...]。
    """
    m = len(indices)
    if m < 2:
        return []
    centerline = [pts[idx] for idx in indices]
    # outside_align + 閉ループのみ「全幅 centered ストローク → body 差し引き」方式。
    # それ以外 (中心アライメント / 開セグメント) は半幅 centered ストローク。
    use_outside_diff = outside_align and closed
    if use_outside_diff:
        half = [max(0.0, float(widths[idx])) for idx in indices]
    else:
        half = [max(0.0, float(widths[idx])) * 0.5 for idx in indices]
    geom = stroke_variable_width(centerline, half, closed=closed, round_joins=round_joins)
    if geom is None:
        return []
    if use_outside_diff:
        python_deps.ensure_bundled_wheels_on_path()
        try:
            from shapely.geometry import Polygon  # type: ignore

            inner = Polygon(centerline)
            if not inner.is_valid:
                inner = inner.buffer(0)
            geom = geom.difference(inner)
        except Exception:  # noqa: BLE001
            pass
    return _geom_to_ring_holes_list(geom)


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


def _pinch_eff_widths(
    idxs: Sequence[int],
    opp_idxs: Sequence[int],
    radii: Sequence[float],
    n: int,
    pinch_w: float,
    plateau_w: float,
    *,
    is_peak: bool,
) -> dict[int, float]:
    """各極値 (山 or 谷) の実効 pinch 幅を radial prominence (突出量) で決める。

    小山/小谷 (prominence が小さい極値) を完全に 0 幅へ潰すと、 帯が鋭いトゲ
    (細い三角スパイク) になる。 そこで prominence が小さいほど pinch_w から
    plateau_w 側へ寄せ、 小さな突起では帯が 0 まで潰れず控えめに細くなるだけに
    する。 prominence 最大の主山/主谷は pinch_w のまま (= 従来通り 0% まで細く)。

    pinch_w: その極値で目標とする線幅 (山なら peak_width, 谷なら valley_width)。
    plateau_w: 反対側の線幅 (寄せる先)。
    """
    if not idxs:
        return {}
    # この側が「0 へすぼまる pinch 側」(pinch_w < plateau_w) のときだけ浅くする。
    # この側が広い方 (pinch_w >= plateau_w) のときは満幅のまま (例: 山0% のとき谷は
    # 満幅であるべきで、 小谷まで細めてはいけない)。
    if not opp_idxs or pinch_w >= plateau_w:
        return {i: pinch_w for i in idxs}
    proms: dict[int, float] = {}
    for i in idxs:
        nearest_opp = min(opp_idxs, key=lambda o: _circular_dist(i, o, n))
        if is_peak:
            proms[i] = max(0.0, radii[i] - radii[nearest_opp])
        else:
            proms[i] = max(0.0, radii[nearest_opp] - radii[i])
    mx = max(proms.values()) if proms else 0.0
    if mx <= 1.0e-9:
        return {i: pinch_w for i in idxs}
    eff: dict[int, float] = {}
    for i in idxs:
        ratio = min(1.0, proms[i] / mx)
        eff[i] = pinch_w + (plateau_w - pinch_w) * (1.0 - ratio)
    return eff


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
    outside_align: bool = False,
    peaks_rounded: bool = False,
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

    outside_align=True + valley_sharp=True + length_scale=1.0 (= 主線 dynamic + 角を
    尖らせる) のときは、 anchor-only サンプル (= bezier 制御点だけ) で構築する。
    こうすると body の peak / valley が単一頂点として残り、 outer ring も peak で
    1 つの 鋭い頂点に集約されて、 滑らかな円弧で丸まらない。
    """
    if base_width_m <= 1.0e-9:
        return []
    if len(body_samples) < 6:
        return []

    pts = [(float(s[0]), float(s[1])) for s in body_samples]
    straight_edged = _is_straight_edged(pts, SAMPLES_PER_SEGMENT)
    # 曲線形状 (雲/フワフワ/トゲ曲線) の多重線リング (signed_offset!=0) は、 centerline を
    # 法線方向に単純オフセットすると凹の谷で自己交差し、 谷/山可変幅でもつれる。 Shapely の
    # clean buffer によるオフセット曲線を centerline にして自己交差を防ぐ。 主線 (signed_offset
    # =0) と トゲ直線 (straight=anchor-only 経路) は対象外。 畳み込んだら signed_offset=0 に
    # して二重オフセットを避ける。 曲線多重線は呼び出し側で outside_align=True にしてあるので
    # (内側オフセットの凸頂点くさび回避)、 outside_align の有無では分岐しない。
    did_clean_offset = False
    if (
        abs(signed_offset_m) > 1.0e-9
        and len(pts) >= 6
    ):
        # 多重線リング (signed_offset!=0) は全形状で Shapely クリーンオフセットを中心線に
        # する。法線方向の単純オフセットは鋭い凹谷で自己交差し、満幅バンドがもつれる
        # (トゲ直線の山0%でも発生)。クリーンオフセットは valley_sharp=mitre で角を保ったまま
        # 自己交差しない中心線を返す。
        clean = _resample_clean_offset(
            _build_body_polygon(body_samples), signed_offset_m, len(pts), valley_sharp,
            peaks_rounded=peaks_rounded,
        )
        if clean is not None and len(clean) >= 6:
            pts = [(c[0], c[1]) for c in clean]
            signed_offset_m = 0.0
            did_clean_offset = True
    # 角を尖らせる + 全周ループ + 「アンカー間が直線」(= トゲ直線) のときだけ anchor-only
    # で構築する。 アンカー (= 山頂/谷の頂点) 間を 1 本の直線セグメントで結ぶことで、
    # 中間サンプル由来のテーパー線のズレ (= 先端の折れ) を防ぎ、 山頂を mitre 点に
    # 置けば基準 (均一線) と同じ鋭い直線アウトラインになる。 雲/フワフワ/トゲ曲線の
    # ような曲線辺では anchor-only は形状を多角形化してしまうため使わない (full
    # サンプリングのまま丸みを保つ)。
    use_anchor_only = (
        valley_sharp
        and length_scale >= 0.999
        and len(pts) >= SAMPLES_PER_SEGMENT * 6
        and straight_edged
        and not did_clean_offset  # クリーンオフセット後は anchor 構造が無いので無効
    )
    if use_anchor_only:
        anchor_pts = pts[::SAMPLES_PER_SEGMENT]
        if len(anchor_pts) >= 6:
            pts = anchor_pts
    n = len(pts)
    # 外向き法線 (samples 上)。 thorn (直線) なら 各セグメントが直線で、 outer ring も
    # 直線セグメントで構成される。
    normals = _polyline_outward_normals(pts, closed=True, balloon_center=balloon_center_m)
    # 谷/山の線幅の基準となる山頂・谷底を検出する。
    # - トゲ直線 (anchor-only): アンカー = 山頂/谷頂なので anchor 単位検出。
    # - 雲/フワフワ/トゲ曲線 (曲線辺): 山頂(バンプ先)・谷底・小山先にアンカーが無い
    #   ため、 曲線そのものの radial 極値を頂点として検出する。
    if use_anchor_only:
        samples_per_segment = 1
        peaks_all, valleys_all = _detect_anchor_peaks_valleys(
            pts, balloon_center_m, samples_per_segment=samples_per_segment,
        )
    else:
        samples_per_segment = max(1, SAMPLES_PER_SEGMENT)
        peaks_all, valleys_all = _detect_radial_peaks_valleys(
            pts, balloon_center_m, window=max(2, SAMPLES_PER_SEGMENT // 6),
        )

    # 構築は sample-direct (= body サンプルを法線方向にオフセットした centerline)
    # を基本にする。 凸の山頂では法線が扇状に開くため、 buffer 経由で出ていた
    # 「山頂から外へ飛び出す mitre スパイク (ヒゲ)」 が発生しない。 凹の谷を外側へ
    # オフセットすると centerline が収束して帯が自己交差するが、 これは最後に
    # `_sanitize_band_polygon` (Shapely buffer(0)) で valid 化して解消する。
    # sanitize=False で済むのは「トゲ直線の主線」(anchor-only + outside_align、 clean-offset
    # 無し) だけ。 アンカー間が直線で pinch が単一頂点に収束するため自己接触しない。
    # それ以外 (曲線の主線/多重線、 中心アライメント多重線、 clean-offset 適用リング) は、
    # 幅0 の pinch 点で帯が自己接触するので Shapely buffer(0) でローブ分割して三角を防ぐ。
    sanitize = (not outside_align) or did_clean_offset or (outside_align and not use_anchor_only)

    # peaks_all/valleys_all はすでに anchor-level の主山/主谷だけが入っているので、
    # ここでの radial 閾値フィルタは不要 (高さがバラつく場合も均等に処理される)。
    peaks = list(peaks_all)
    valleys = list(valleys_all)
    cx_m, cy_m = balloon_center_m
    radii = [math.hypot(p[0] - cx_m, p[1] - cy_m) for p in pts]
    # (旧: 谷の bisector 法線が縮退してバーブ化するのを radial 上書きで補正していたが、
    #  新ストローク (stroke_variable_width) はセグメント法線のみ使い縮退しないため不要。)
    # length_scale で正規化した t_segment で 幅補間 (cut endpoint で peak_w に達するよう)
    length_scale_clamped = max(0.001, min(1.0, float(length_scale)))

    # (旧: peak_extension_m>0 で peak を外向き延長するブロックがあったが、未定義
    #  anchor_count を参照する死にコードで全呼び出し元が 0 を渡すため削除。)
    pts_eff = list(pts)

    # オフセット centerline = pts_eff + normal * signed_offset_m
    # outside_align=True (= 外側アライメント主線) のときは body samples (= pts_eff)
    # そのものを centerline に使う。 これにより width=0 の頂点で outer=inner=body と
    # なり、 body の鋭い谷/山頂に直接 pinch off して 帯終端が鋭く尖る。
    if outside_align:
        centerline = list(pts_eff)
    else:
        centerline = [
            (pts_eff[i][0] + normals[i][0] * signed_offset_m,
             pts_eff[i][1] + normals[i][1] * signed_offset_m)
            for i in range(n)
        ]

    # 各サンプル点の line width: 谷と山の頂点 (大山/小山どちらでも) から
    # 線形補間する。length_scale で正規化した t_segment を使うことで、cut endpoint
    # (= t_geom が length_scale に達する位置) で width=peak_w に達する。
    # → 山の線幅=0% + length<100% で多重線の cut endpoint が綺麗に 0 に収束する。
    width_peaks = peaks_all if peaks_all else peaks
    width_valleys = valleys_all if valleys_all else valleys
    # 山頂が丸い形状 (雲/フワフワ) のみ: 小山/小谷を prominence に応じて pinch を
    # 浅くし、 0 幅の鋭いトゲを防ぐ。 主山/主谷 (prominence 最大) は従来通り
    # peak_w/valley_w まで細くなる。 トゲ/トゲ曲線 (peaks_rounded=False) は小山も
    # 鋭く尖るのが正しいので、 従来通り peak_w/valley_w をそのまま使う (回帰防止)。
    if peaks_rounded:
        peak_eff = _pinch_eff_widths(
            width_peaks, width_valleys, radii, n, peak_width_m, valley_width_m, is_peak=True
        )
        valley_eff = _pinch_eff_widths(
            width_valleys, width_peaks, radii, n, valley_width_m, peak_width_m, is_peak=False
        )
    else:
        peak_eff = {i: peak_width_m for i in width_peaks}
        valley_eff = {i: valley_width_m for i in width_valleys}
    widths: list[float] = []
    if not width_peaks and not width_valleys:
        widths = [base_width_m] * n
    else:
        for i in range(n):
            if width_peaks:
                np_idx = min(width_peaks, key=lambda p: _circular_dist(i, p, n))
                d_peak = _circular_dist(i, np_idx, n)
                pw = peak_eff.get(np_idx, peak_width_m)
            else:
                d_peak = n  # peak が無いケース: 全体を valley_width とみなす
                pw = peak_width_m
            if width_valleys:
                nv_idx = min(width_valleys, key=lambda v: _circular_dist(i, v, n))
                d_valley = _circular_dist(i, nv_idx, n)
                vw = valley_eff.get(nv_idx, valley_width_m)
            else:
                d_valley = n
                vw = valley_width_m
            total = d_peak + d_valley
            if total <= 0:
                t_geom = 0.5
            else:
                t_geom = d_valley / total  # 0 at valley, 1 at peak
            # length_scale 正規化: cut endpoint で peak_w に達する
            t_segment = min(1.0, t_geom / length_scale_clamped)
            # outside_align=True (= 主線 dynamic) では「ピンチ側の anchor から ±N
            # サンプル以内だけが pinch、 それ以外は 大きい方の幅で plateau」とする。
            # 通常 (山 100% / 谷 100%) と比べて 「主線の太さは保ったまま、 ピンチ
            # 側の頂点だけが 0% に下がる」 挙動を実現。 valley/peak の大小で
            # ピンチ側を切り替え、 N=2 サンプル distance で 0→100% へ線形補間。
            # 谷↔山は線形補間 (t_segment)。 直線辺では幅が線形に変わるだけなので
            # 帯のアウトラインも直線のまま (曲がらない)。 vw/pw は小山/小谷の
            # prominence で浅くした実効幅 (主山/主谷は peak_w/valley_w のまま)。
            widths.append(vw + (pw - vw) * t_segment)

    # 凸の山頂・凹の谷とも、 頂点を「隣接2辺を頂点幅ぶん外側へ平行移動した直線の
    # 交点 (= mitre)」に置く。 anchor-only では各辺が単一直線セグメントなので、 両隣の
    # 辺がこの mitre 点を共有し、 先端が折れずに 基準 (均一線 = body.buffer の mitre)
    # と同じ鋭さ・同じ垂直線幅になる。 単純な法線オフセットだと頂点が mitre より内側
    # に来て、 辺に対する垂直線幅が base×cos(φ) (≈半分) に痩せる。 山頂だけでなく谷
    # にも適用しないと、 山を 0% にしたとき太いはずの谷まで細く見える。 幅 0 の頂点
    # (pinch) は対象外。 過剰スパイクは mitre_limit で頭打ち。
    if outside_align and use_anchor_only and (width_peaks or width_valleys) and len(widths) == n:
        cbx, cby = balloon_center_m

        def _edge_offset_line(i, j, w):
            ax, ay = centerline[i]
            bx, by = centerline[j]
            ex, ey = bx - ax, by - ay
            elen = math.hypot(ex, ey)
            if elen < 1.0e-12:
                return None
            nx, ny = -ey / elen, ex / elen
            mx, my = (ax + bx) * 0.5 - cbx, (ay + by) * 0.5 - cby
            if nx * mx + ny * my < 0.0:
                nx, ny = -nx, -ny
            return ((ax + nx * w, ay + ny * w), (bx + nx * w, by + ny * w))

        for ci in (*width_peaks, *width_valleys):
            w_ci = widths[ci]
            if w_ci <= 1.0e-9:
                continue
            la = _edge_offset_line(ci, (ci + 1) % n, w_ci)
            lb = _edge_offset_line(ci, (ci - 1) % n, w_ci)
            if la is None or lb is None:
                continue
            hit = _line_intersection(la[0], la[1], lb[0], lb[1])
            if hit is None:
                continue
            px, py = centerline[ci]
            dx = hit[0] - px
            dy = hit[1] - py
            dist = math.hypot(dx, dy)
            if dist <= 1.0e-9:
                continue
            # balloon 中心から離れる外向きでなければ無視
            if dx * (px - cbx) + dy * (py - cby) <= 0.0:
                continue
            normals[ci] = (dx / dist, dy / dist)
            widths[ci] = min(dist, w_ci * _SHARP_MITRE_LIMIT)

    # length cut は大山ベースだけで行う (= 大山周りで切って valley から伸ばす)
    segments = _ring_kept_index_segments(n, peaks, valleys, length_scale)

    out_polygons: list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]] = []

    def _emit(result_list):
        # result_list: [(outer, holes), ...] (堅牢ストロークは複数ポリゴンを返し得る)
        if not result_list:
            return
        for outer, holes in result_list:
            if sanitize:
                out_polygons.extend(_sanitize_band_polygon(outer, holes))
            else:
                out_polygons.append((outer, holes))

    # cross_extension_m > 0: 各 keep segment の山頂方向 (= 端点) を法線方向へ延ばし、
    # 谷をまたいで隣接 segment と交差する形に。length_scale < 1.0 のときのみ有効。
    if length_scale >= 0.999:
        # 閉じた全周帯 (ホール付きポリゴン): cross_extension は無関係
        if segments:
            seg = segments[0]
            result = _build_variable_width_band_segment(
                centerline, widths, normals, seg,
                closed=True, outside_align=outside_align, round_joins=peaks_rounded,
            )
            _emit(result)
    else:
        for seg in segments:
            if len(seg) < 2:
                continue
            if cross_extension_m > 1.0e-9:
                centerline_ext, widths_ext, normals_ext, seg_ext = _extend_segment_for_cross(
                    centerline, widths, normals, seg, cross_extension_m
                )
                result = _build_variable_width_band_segment(
                    centerline_ext, widths_ext, normals_ext, seg_ext,
                    closed=False, outside_align=outside_align, round_joins=peaks_rounded,
                )
            else:
                result = _build_variable_width_band_segment(
                    centerline, widths, normals, seg,
                    closed=False, outside_align=outside_align, round_joins=peaks_rounded,
                )
            _emit(result)

    # sample-direct が何も出せなかったとき (= 退化形状) のみ buffer 経由へ fallback。
    if not out_polygons and sanitize:
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
        if polys:
            return polys
    return out_polygons


def _extend_body_peaks_outward(
    pts: Sequence[tuple[float, float]],
    balloon_center_m: tuple[float, float],
    main_peaks: Sequence[int],
    cross_extension_m: float,
) -> list[tuple[float, float]]:
    """主山頂を外側に押し出した body 多角形を返す。smoothstep 局所減衰で滑らかに延ばす。

    各 main peak の周辺 ±half_span サンプルに対して、smoothstep の重みで外側方向に
    `cross_extension_m` だけ位置をオフセットする。peak ぴったりで最大延長、隣接 valley
    に向かって 0 に減衰する。
    """
    if cross_extension_m <= 1.0e-9 or not main_peaks:
        return list(pts)
    cx_m, cy_m = balloon_center_m
    n = len(pts)
    # 隣接する main peak 間のサンプル距離の半分を falloff 範囲とする
    sorted_peaks = sorted(main_peaks)
    half_spans: list[int] = []
    for i, p in enumerate(sorted_peaks):
        prev_p = sorted_peaks[(i - 1) % len(sorted_peaks)]
        next_p = sorted_peaks[(i + 1) % len(sorted_peaks)]
        # 周回距離を計算
        d_prev = (p - prev_p) % n
        d_next = (next_p - p) % n
        half_spans.append(max(1, min(d_prev, d_next) // 2))
    peak_to_span = dict(zip(sorted_peaks, half_spans))

    pts_out = list(pts)
    for peak_idx in sorted_peaks:
        half_span = peak_to_span[peak_idx]
        peak_x, peak_y = pts[peak_idx]
        rx = peak_x - cx_m
        ry = peak_y - cy_m
        rl = math.hypot(rx, ry)
        if rl < 1.0e-9:
            continue
        nx = rx / rl
        ny = ry / rl
        for off in range(-half_span, half_span + 1):
            idx = (peak_idx + off) % n
            # smoothstep weight: 1 at peak, 0 at edge of falloff
            u = 1.0 - abs(off) / float(half_span)
            if u <= 0.0:
                continue
            w = u * u * (3.0 - 2.0 * u)
            ext = cross_extension_m * w
            px, py = pts_out[idx]
            pts_out[idx] = (px + nx * ext, py + ny * ext)
    return pts_out


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
        # length_scale を渡して、cut endpoint で peak_w に達するように補間する。
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
            length_scale=length_scale,
        )
        if band is None or band.is_empty:
            return None
    if band.is_empty:
        return []

    # peaks_all は anchor-level の主山が全部入っている (= radial 閾値フィルタは不要)。
    # 高さが jitter している主山もすべて均等に処理される。
    cx_m, cy_m = balloon_center_m
    radii = [math.hypot(p[0] - cx_m, p[1] - cy_m) for p in pts]
    main_peaks = list(peaks_all)
    if not main_peaks:
        # 山が無い → そのまま閉ループを返す
        return _shapely_band_to_polygons(band)

    # 各 main peak の角度位置
    n = len(pts)
    num_peaks = max(1, len(main_peaks))
    full_period_angle = 2.0 * math.pi / num_peaks
    cut_factor = max(0.0, 1.0 - float(length_scale))
    cross_enabled = cross_extension_m > 1.0e-9
    # cross 有効時: band の外側エッジに細い "舌" を生やす形で延長する。
    # body を押し出す方式だと buffer mitre が増幅されて過剰延長になっていたが、
    # band 完成後に固定角度幅の舌を union する方式に切り替え (= 延長量が直接的)。
    if cross_enabled:
        max_peak_r_pts = max(radii[p] for p in main_peaks) if main_peaks else 0.0
        avg_peak_r = (sum(radii[p] for p in main_peaks) / len(main_peaks)) if main_peaks else 0.0
        # 主谷の平均 radial も計算 (主山の高さ参照)
        if valleys_all:
            main_valleys = [v for v in valleys_all if radii[v] <= avg_peak_r]
            avg_valley_r = sum(radii[v] for v in main_valleys) / max(1, len(main_valleys)) if main_valleys else avg_peak_r * 0.7
        else:
            avg_valley_r = avg_peak_r * 0.7
        spike_height = max(0.5e-3, avg_peak_r - avg_valley_r)
        # 延長量: 山高 × (0.18 + cut_factor)。length=100% でも 0.18 倍だけ baseline 延長。
        actual_ext = spike_height * (0.18 + cut_factor)
        # 舌の半角: 周期の 1/10 にしつつ、最大 4° で頭打ち。
        # 主山が少ない形状 (例: thorn-curve 3 peak) で周期が大きくなって舌が「アロー型」に
        # 太くなる問題を防ぐため、4° で cap する。
        tongue_half_angle = min(math.radians(4.0), full_period_angle * 0.1)
        # 舌の根元: band 外側エッジ付近 (= avg_peak_r + signed_offset + 半幅 まで)
        max_w = max(valley_width_m, peak_width_m)
        base_radial_peak = avg_peak_r + signed_offset_m + max_w * 0.5
        # 舌の根元は外側 buffer 上に置きたいので、avg_peak_r からの距離を保つ
        tongues = []
        for peak_idx in main_peaks:
            peak_x = pts[peak_idx][0] - cx_m
            peak_y = pts[peak_idx][1] - cy_m
            peak_angle = math.atan2(peak_y, peak_x)
            apex = (cx_m + math.cos(peak_angle) * (base_radial_peak + actual_ext),
                    cy_m + math.sin(peak_angle) * (base_radial_peak + actual_ext))
            a0 = peak_angle - tongue_half_angle
            a1 = peak_angle + tongue_half_angle
            # 舌の根元 2 点は band 内側エッジ付近に置いて、band と確実に繋がるよう
            inner_radial = max(0.0, avg_peak_r + signed_offset_m - max_w * 1.5)
            base0 = (cx_m + math.cos(a0) * inner_radial, cy_m + math.sin(a0) * inner_radial)
            base1 = (cx_m + math.cos(a1) * inner_radial, cy_m + math.sin(a1) * inner_radial)
            # 反時計回りで作る
            cross_p = (base0[0] - apex[0]) * (base1[1] - apex[1]) - (base0[1] - apex[1]) * (base1[0] - apex[0])
            if cross_p < 0:
                base0, base1 = base1, base0
            try:
                tongue_poly = Polygon([apex, base0, base1])
                if not tongue_poly.is_valid:
                    tongue_poly = tongue_poly.buffer(0)
                if not tongue_poly.is_empty and tongue_poly.area > 0:
                    tongues.append(tongue_poly)
            except Exception:  # noqa: BLE001
                continue
        if tongues:
            try:
                ext_union = unary_union(tongues)
                # 舌は body 内部にも伸びるが、band と union すれば band 領域外の部分だけが追加される
                # → 実際には band と舌の合計から body 内部を除いたものになる
                band = band.union(ext_union)
                # body 内部は除く (= band の外側にだけ舌を残す)
                band = band.difference(body_poly)
            except Exception:  # noqa: BLE001
                pass
        return _shapely_band_to_polygons(band)
    if cut_factor <= 1.0e-6:
        return _shapely_band_to_polygons(band)

    band_extent = max(valley_width_m, peak_width_m) * 2.0 + 0.05
    cut_half_angle = cut_factor * full_period_angle * 0.5
    max_outer_r = max(radii) + abs(signed_offset_m) + band_extent  # 余裕を持たせる
    wedges = []

    # 各 main peak の角度を計算し、その周辺 cut_factor × half_period_angle 分を抜く
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
    length_scale: float = 1.0,
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
    # centerline 用 buffer の mitre_limit:
    # - 低すぎる (4.0): フキダシ形状の鋭い谷/山が bevel カットされて丸く見える
    # - 高すぎる (50.0): 山頂が外向きに過剰なヒゲ状スパイクとして mitre 延長される
    # 中間値 (10.0) で、 thorn の典型的な鋭角 (30〜60 度) を sharp に保ちつつ、
    # 過剰スパイクを抑える。 角を尖らせる OFF では _ROUND_MITRE_LIMIT を使う。
    join = 2 if valley_sharp else 1
    mitre = 10.0 if valley_sharp else _ROUND_MITRE_LIMIT
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

    # 幅補間: 「kept segment 内の position」で valley→peak をマップする (length_scale 適用)。
    # t_geom = 谷からの幾何位置 (0=valley, 1=peak)。
    # t_segment = t_geom / length_scale (cap 1.0)。これにより cut endpoint (= t_geom が
    # length_scale に達する位置) で width = peak_w に達する。
    length_scale_clamped = max(0.001, min(1.0, float(length_scale)))
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
        # anchor-level なので主山/主谷だけが入っている (= radial 閾値フィルタは不要)
        d_peak = min((_circ_dist_int(best_i, p) for p in peaks_all), default=n) if peaks_all else n
        d_valley = min((_circ_dist_int(best_i, v) for v in valleys_all), default=n) if valleys_all else n
        total = d_peak + d_valley
        t_geom = 0.5 if total <= 0 else (d_valley / total)
        # t_segment: kept segment 内の位置 (length_scale で正規化)
        t_segment = min(1.0, t_geom / length_scale_clamped)
        widths.append(valley_width_m + (peak_width_m - valley_width_m) * t_segment)

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


def _sanitize_band_polygon(
    outer: Sequence[tuple[float, float]],
    holes: Sequence[Sequence[tuple[float, float]]],
) -> list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]:
    """sample-direct で組んだ帯ポリゴンが自己交差していたら Shapely で valid 化する。

    谷 (凹) を外側へオフセットすると centerline が収束して帯が自己交差する。
    earcut は自己交差を扱えないため、 buffer(0) で正規化してから渡す。
    valid な (= 交差していない) ポリゴンはそのまま返すので、 鋭い角は保たれる。
    """
    outer_list = [(float(x), float(y)) for x, y in outer]
    holes_list = [[(float(x), float(y)) for x, y in h] for h in holes]
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import Polygon  # type: ignore
    except Exception:  # noqa: BLE001
        return [(outer_list, holes_list)]
    try:
        poly = Polygon(outer_list, holes_list)
        if poly.is_valid:
            return [(outer_list, holes_list)]
        fixed = poly.buffer(0)
        if fixed.is_empty:
            return []
        cleaned = _shapely_band_to_polygons(fixed)
        return cleaned if cleaned else [(outer_list, holes_list)]
    except Exception:  # noqa: BLE001
        return [(outer_list, holes_list)]


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
    outer_pts = _sample_anchor_loop_to_local_m(outer_anchors, offset_mm, SAMPLES_PER_SEGMENT, entry)
    inner_pts = _sample_anchor_loop_to_local_m(inner_anchors, offset_mm, SAMPLES_PER_SEGMENT, entry)
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


RIBBON_UV_LAYER_NAME = "BNameRibbon"


def _line_material_texture_size(entry) -> tuple[int, int]:
    """線マテリアルの最初の画像テクスチャのピクセルサイズを返す (無ければ 0,0)."""
    name = str(getattr(entry, "line_material_name", "") or "").strip()
    if not name:
        return 0, 0
    mat = bpy.data.materials.get(name)
    if mat is None or not getattr(mat, "use_nodes", False) or mat.node_tree is None:
        return 0, 0
    for node in mat.node_tree.nodes:
        image = getattr(node, "image", None)
        if getattr(node, "bl_idname", "") == "ShaderNodeTexImage" and image is not None:
            try:
                return int(image.size[0]), int(image.size[1])
            except Exception:  # noqa: BLE001
                return 0, 0
    return 0, 0


def _apply_ribbon_uv(mesh: bpy.types.Mesh, entry, loops_m, line_width_m: float) -> None:
    """貼り方「線に沿う (リボン)」: 帯メッシュへ輪郭弧長ベースの UV を設定する.

    各頂点を最寄りの輪郭ループへ投影し、u = 弧長 × 整数タイル数 / 周長、
    v = 輪郭からの距離 / 帯幅 とする。周長をタイルの整数枚に合わせるため、
    閉ループ一周で u がちょうど整数になり、始点終点で模様が連続する
    (出力 PNG のリボン貼りと同じ規則)。
    """
    try:
        if str(getattr(entry, "line_style", "") or "") != "material":
            return
        if str(getattr(entry, "line_material_mapping", "tile") or "tile") != "ribbon":
            return
        tex_w, tex_h = _line_material_texture_size(entry)
        if tex_w <= 0 or tex_h <= 0 or line_width_m <= 1.0e-9 or len(mesh.vertices) == 0:
            return
        import numpy as np

        from . import ribbon_mapping

        seg_list = []
        for loop in loops_m or ():
            segs = ribbon_mapping.loop_segments([(float(p[0]), float(p[1])) for p in loop])
            if segs is not None:
                seg_list.append(segs)
        if not seg_list:
            return
        count = len(mesh.vertices)
        co = np.empty(count * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", co)
        xs = co[0::3].astype(np.float64)
        ys = co[1::3].astype(np.float64)
        stretch_single = bool(getattr(entry, "line_material_stretch_single", False))
        seam_fix = str(getattr(entry, "line_material_seam_fix", "none") or "none")
        best_d = best_u = best_n = None
        for segs in seg_list:
            n_tiles = (
                1.0
                if stretch_single
                else float(ribbon_mapping.tile_count(segs["total"], float(line_width_m), tex_w, tex_h))
            )
            s_arr, d_arr = ribbon_mapping.project_points(segs, xs, ys)
            t_arr = s_arr / segs["total"]
            if stretch_single and seam_fix == "mirror":
                # ミラー往復: 行きは普通に、帰りは鏡像 (始点終点とも同じ端で連続)
                u_arr = 1.0 - np.abs(1.0 - 2.0 * t_arr)
            else:
                u_arr = t_arr * n_tiles
            if best_d is None:
                best_d, best_u = d_arr, u_arr
                best_n = np.full(len(u_arr), n_tiles)
            else:
                take = d_arr < best_d
                best_u = np.where(take, u_arr, best_u)
                best_n = np.where(take, n_tiles, best_n)
                best_d = np.where(take, d_arr, best_d)
        v_arr = 1.0 - np.clip(best_d / float(line_width_m), 0.0, 1.0)
        loop_count = len(mesh.loops)
        if loop_count == 0:
            return
        vidx = np.empty(loop_count, dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", vidx)
        u_corner = best_u[vidx]
        n_corner = best_n[vidx]
        # ループ閉合をまたぐ面 (u が n 付近と 0 付近の頂点が混在) は、面ごとに
        # 小さい側へ -n して連続させる (テクスチャは繰り返しなので負でもよい)
        poly_count = len(mesh.polygons)
        if poly_count:
            loop_start = np.empty(poly_count, dtype=np.int32)
            loop_total = np.empty(poly_count, dtype=np.int32)
            mesh.polygons.foreach_get("loop_start", loop_start)
            mesh.polygons.foreach_get("loop_total", loop_total)
            face_min = np.minimum.reduceat(u_corner, loop_start)
            face_min_rep = np.repeat(face_min, loop_total)
            wrap = (u_corner - face_min_rep) > (n_corner * 0.5)
            u_corner = np.where(wrap, u_corner - n_corner, u_corner)
        uv_layer = mesh.uv_layers.get(RIBBON_UV_LAYER_NAME)
        if uv_layer is None:
            uv_layer = mesh.uv_layers.new(name=RIBBON_UV_LAYER_NAME, do_init=False)
        if uv_layer is None:
            return
        uv = np.empty(loop_count * 2, dtype=np.float32)
        uv[0::2] = u_corner
        uv[1::2] = v_arr[vidx]
        uv_layer.data.foreach_set("uv", uv)
        try:
            uv_layer.active_render = True
            mesh.uv_layers.active = uv_layer
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        _logger.exception("ribbon uv failed")


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


def _flash_white_line_mesh_object_name(balloon_id: str) -> str:
    return f"{BALLOON_FLASH_WHITE_LINE_MESH_NAME_PREFIX}{balloon_id}"


def _flash_white_line_mesh_data_name(balloon_id: str) -> str:
    return f"{BALLOON_FLASH_WHITE_LINE_MESH_NAME_PREFIX}{balloon_id}_mesh"


# 帯メッシュのジオメトリ署名: 一致すれば shapely/earcut の再構築を丸ごとスキップする
PROP_BAND_GEOMETRY_SIG = "bname_band_geometry_sig"


def band_geometry_cache_hit(obj_name: str, geometry_sig) -> Optional[bpy.types.Object]:
    """署名一致なら既存の帯メッシュオブジェクトを返す (再構築スキップ用)."""
    if not geometry_sig:
        return None
    obj = bpy.data.objects.get(obj_name)
    if obj is None or getattr(obj, "type", "") != "MESH" or getattr(obj, "data", None) is None:
        return None
    if str(obj.get(PROP_BAND_GEOMETRY_SIG, "") or "") != str(geometry_sig):
        return None
    return obj


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
    geometry_sig=None,
) -> bpy.types.Object:
    """Mesh をフキダシ本体に連結し、コレクション/親子を ensure する.

    コマ内マスクは material 側のアルファ画像マスク (画像マスク方式) に一本化し、
    ジオメトリ側のメッシュくり抜き modifier は使わない方針。古いビルドから残った
    クリップ modifier があれば撤去する。
    """
    obj = bpy.data.objects.get(obj_name)
    if obj is not None and object_preserve.is_preserved(obj):
        obj = None
    if obj is not None and getattr(obj, "type", "") != "MESH":
        object_preserve.preserve_object(obj, "古いフキダシ線メッシュを保持")
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

    # ジオメトリ署名の更新 (署名なし経路で作られた場合は古い署名を消す)
    if geometry_sig:
        obj[PROP_BAND_GEOMETRY_SIG] = str(geometry_sig)
    elif PROP_BAND_GEOMETRY_SIG in obj:
        try:
            del obj[PROP_BAND_GEOMETRY_SIG]
        except Exception:  # noqa: BLE001
            pass

    # 旧来のメッシュくり抜き modifier は画像マスク方式に切り替えたため撤去する
    _sync_mask_clip_modifier(obj, None)
    return obj


def _resolve_body_spline(body_object: bpy.types.Object | None):
    """フキダシ本体カーブの閉じた spline (Bezier または NURBS) を返す。無ければ None。"""
    if body_object is None or getattr(body_object, "type", "") != "CURVE":
        return None
    body_curve = getattr(body_object, "data", None)
    if body_curve is None:
        return None
    nurbs_fallback = None
    for spline in list(getattr(body_curve, "splines", []) or []):
        if not bool(getattr(spline, "use_cyclic_u", False)):
            continue
        spline_type = str(getattr(spline, "type", "") or "")
        if spline_type == "BEZIER":
            return spline
        if spline_type == "NURBS" and nurbs_fallback is None:
            nurbs_fallback = spline
    return nurbs_fallback


def _body_samples_for_line_mesh(entry, body_object: bpy.types.Object) -> list[tuple[float, float, float]]:
    body_spline = _resolve_body_spline(body_object)
    if body_spline is None:
        return []
    return sample_body_spline(body_spline, SAMPLES_PER_SEGMENT)


def ensure_balloon_line_mesh(
    *,
    scene,
    work,
    page,
    entry,
    body_object: bpy.types.Object,
    line_material: bpy.types.Material,
    mask_info=None,
    geometry_sig=None,
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
    line_width_mm = scaled_entry_width_mm(entry, "line_width_mm", 0.3)
    if line_style == "none" or line_width_mm <= 1.0e-6:
        remove_balloon_line_mesh(balloon_id)
        return None

    cached = band_geometry_cache_hit(_line_mesh_object_name(balloon_id), geometry_sig)
    if cached is not None:
        return _attach_band_mesh_object(
            obj_name=_line_mesh_object_name(balloon_id),
            mesh=cached.data,
            material=line_material,
            body_object=body_object,
            scene=scene,
            kind=_KIND_LINE,
            balloon_id=balloon_id,
            visible=bool(getattr(entry, "visible", True)),
            mask_info=mask_info,
            geometry_sig=geometry_sig,
        )

    line_width_m = line_width_mm * 0.001

    mesh_name = _line_mesh_data_name(balloon_id)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)

    # 主線は全形状で「外側アライメント + Shapely buffer + earcut」方式に統一。
    samples = _body_samples_for_line_mesh(entry, body_object)
    if len(samples) < 3:
        remove_balloon_line_mesh(balloon_id)
        return None
    samples, tails_merged = _outline_samples_with_tails(entry, samples)
    valley_sharp = _valley_sharp_for_entry(entry)
    # 山頂が丸い形状 (雲/フワフワ) は、 外側へ広げた均一バンドの山頂を round join で
    # 丸める。 トゲ/トゲ曲線は山頂が尖る形状なので従来通り mitre のまま。
    peaks_rounded = balloon_shapes.normalize_shape(
        str(getattr(entry, "shape", "rect") or "rect")
    ) in _ROUNDED_PEAK_SHAPES

    # 主線の谷/山の線幅: % 指定 (100% = base line_width, 0% = その頂点で消える)。
    # 辺全体で線形補間。動的形状のみ有効。両方 0% のとき主線全体不可視。
    # 外側フチ・内側フチでも同じ係数を流用する (フチが山頂を覆って尖りを潰さない
    # ようにするため)。
    main_line_dynamic, line_valley_width_pct, line_peak_width_pct, main_line_both_zero = (
        _line_dynamic_width_params(entry)
    )
    line_valley_width_mm = line_width_mm * line_valley_width_pct / 100.0
    line_peak_width_mm = line_width_mm * line_peak_width_pct / 100.0

    if line_style in {"dashed", "dotted"}:
        dash_polys = _build_dashed_band_polygons(
            samples,
            line_width_m=line_width_m,
            line_style=line_style,
            valley_sharp=valley_sharp,
            dash_segment_mm=line_pattern.dashed_segment_mm(entry, line_width_mm),
            dash_gap_mm=line_pattern.dashed_gap_mm(entry, line_width_mm),
            dotted_gap_mm=line_pattern.dotted_gap_mm(entry, line_width_mm),
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
        # outside_align=True で外側アライメント (inner=body, outer=body+width)。
        # width=0 の頂点で outer=inner=body となり、 body の鋭い谷/山頂で
        # 鋭く pinch off して 帯終端が尖る (中心アライメントだと body から
        # line_width/2 離れた位置で円弧状に丸まってしまう)。
        body_center_m = _balloon_center_m_from_samples(samples)
        sub_polys = _build_dynamic_multi_line_polygons(
            body_samples=samples,
            signed_offset_m=0.0,
            base_width_m=line_width_m,
            valley_width_m=line_valley_width_mm * 0.001,
            peak_width_m=line_peak_width_mm * 0.001,
            length_scale=1.0,
            valley_sharp=valley_sharp,
            balloon_center_m=body_center_m,
            peak_extension_m=0.0,
            outside_align=True,
            peaks_rounded=peaks_rounded,
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
            peaks_rounded=peaks_rounded,
        )
        if union_result is None:
            remove_balloon_line_mesh(balloon_id)
            return None
        outer_ring, holes = union_result
        rings = [(outer_ring, holes)]
        # 「角を尖らせる」しっぽ: 結合された帯の折れ角を尖らせ、先端を
        # ペンの抜きのように細く絞る
        if tails_merged:
            sharp_tails = sharp_tail_tip_infos_local_m(entry)
            if sharp_tails:
                rings = balloon_tail_boolean.apply_sharp_tail_tips(
                    rings,
                    [(float(s[0]), float(s[1])) for s in samples],
                    line_width_m,
                    sharp_tails,
                    add_bend_mitre=not valley_sharp,
                )
        _build_band_mesh_from_polygons(mesh, rings, LINE_Z_OFFSET_M)

    _apply_ribbon_uv(mesh, entry, [samples], line_width_m)

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
        geometry_sig=geometry_sig,
    )


def _valley_sharp_for_entry(entry) -> bool:
    sp = getattr(entry, "shape_params", None)
    return bool(getattr(sp, "cloud_valley_sharp", False))


def _line_dynamic_width_params(entry) -> tuple[bool, float, float, bool]:
    """主線の動的幅パラメータを返す.

    戻り値: (is_dynamic, valley_pct, peak_pct, both_zero)
    - is_dynamic: 谷/山の線幅 % が 100% 以外で、 動的形状 (cloud/fluffy/thorn/thorn-curve) のとき True
    - valley_pct / peak_pct: 0..100
    - both_zero: 両方 0% (= 帯全体が消える) のとき True

    外側フチ・内側フチでも主線と同じ係数を流用し、 主線が山頂で消えるときは
    フチも山頂で消えて尖りを残す挙動にする。
    """
    shape_norm = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect"))
    if shape_norm not in _DYNAMIC_WIDTH_SHAPES:
        return (False, 100.0, 100.0, False)
    valley_pct = max(0.0, min(100.0, float(getattr(entry, "line_valley_width_pct", 100.0))))
    peak_pct = max(0.0, min(100.0, float(getattr(entry, "line_peak_width_pct", 100.0))))
    is_dynamic = abs(valley_pct - 100.0) > 1.0e-3 or abs(peak_pct - 100.0) > 1.0e-3
    both_zero = is_dynamic and valley_pct <= 1.0e-3 and peak_pct <= 1.0e-3
    return (is_dynamic, valley_pct, peak_pct, both_zero)


def _balloon_center_m_from_samples(samples: Sequence[tuple[float, float, float]]) -> tuple[float, float]:
    return (
        sum(s[0] for s in samples) / len(samples),
        sum(s[1] for s in samples) / len(samples),
    )


def _compute_main_line_polygon(
    entry,
    samples,
    balloon_center_m: tuple[float, float],
    line_width_m: float,
    valley_sharp: bool,
):
    """主線の Shapely polygon を返す (dynamic / 均一どちらも対応).

    フキダシのアウトラインを 主線が太く描いた領域として算出するため、 外側フチ
    の buffer 基準として使う。 主線が dynamic で 谷で 0% / 山頂で 0% のときも
    主線が実際に塗る範囲を返す。 帯全体が無効化される設定では None。
    """
    if line_width_m <= 1.0e-9:
        return None
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import Polygon  # type: ignore
        from shapely.ops import unary_union  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    is_dynamic, valley_pct, peak_pct, both_zero = _line_dynamic_width_params(entry)
    if is_dynamic and both_zero:
        return None
    if is_dynamic:
        line_valley_m = line_width_m * (valley_pct / 100.0)
        line_peak_m = line_width_m * (peak_pct / 100.0)
        peaks_rounded = balloon_shapes.normalize_shape(
            str(getattr(entry, "shape", "rect") or "rect")
        ) in _ROUNDED_PEAK_SHAPES
        sub_polys = _build_dynamic_multi_line_polygons(
            body_samples=samples,
            signed_offset_m=0.0,
            base_width_m=line_width_m,
            valley_width_m=line_valley_m,
            peak_width_m=line_peak_m,
            length_scale=1.0,
            valley_sharp=valley_sharp,
            balloon_center_m=balloon_center_m,
            peak_extension_m=0.0,
            outside_align=True,
            peaks_rounded=peaks_rounded,
        )
        if not sub_polys:
            return None
        polys = []
        for outer, holes in sub_polys:
            try:
                p = Polygon(outer, holes)
                if not p.is_valid:
                    p = p.buffer(0)
                if not p.is_empty and p.area > 0:
                    polys.append(p)
            except Exception:  # noqa: BLE001
                continue
        if not polys:
            return None
        try:
            return unary_union(polys)
        except Exception:  # noqa: BLE001
            return None
    # 均一幅主線: body の外側に line_width_m まで膨らんだ帯
    body_poly = _build_body_polygon(samples)
    if body_poly is None:
        return None
    join = 2 if valley_sharp else 1
    mitre = _SHARP_MITRE_LIMIT if valley_sharp else _ROUND_MITRE_LIMIT
    try:
        outline = body_poly.buffer(line_width_m, join_style=join, mitre_limit=mitre)
        return outline.difference(body_poly)
    except Exception:  # noqa: BLE001
        return None


def _compute_balloon_outer_outline(
    entry,
    samples,
    balloon_center_m: tuple[float, float],
    line_width_m: float,
    valley_sharp: bool,
):
    """body + 主線 polygon の union = 「主線が描く変わったアウトライン」を返す.

    外側フチは このアウトラインを edge_width だけ外側に均一 buffer して作る。
    """
    body_poly = _build_body_polygon(samples)
    if body_poly is None:
        return None
    line_poly = _compute_main_line_polygon(entry, samples, balloon_center_m, line_width_m, valley_sharp)
    if line_poly is None or line_poly.is_empty:
        return body_poly
    try:
        union = body_poly.union(line_poly)
        if union.is_empty:
            return body_poly
        return union
    except Exception:  # noqa: BLE001
        return body_poly


def _shapely_geom_to_outer_holes_list(geom):
    """Shapely Polygon/MultiPolygon を [(outer_ring, holes), ...] に変換する."""
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        geoms = [geom]
    elif geom.geom_type == "MultiPolygon":
        geoms = list(geom.geoms)
    else:
        return []
    out = []
    for g in geoms:
        if g.is_empty or g.area <= 1.0e-12:
            continue
        outer = [(float(x), float(y)) for x, y in g.exterior.coords]
        if len(outer) >= 2 and outer[0] == outer[-1]:
            outer = outer[:-1]
        if len(outer) < 3:
            continue
        holes = []
        for interior in g.interiors:
            hole = [(float(x), float(y)) for x, y in interior.coords]
            if len(hole) >= 2 and hole[0] == hole[-1]:
                hole = hole[:-1]
            if len(hole) >= 3:
                holes.append(hole)
        out.append((outer, holes))
    return out


def ensure_balloon_outer_edge_mesh(
    *,
    scene,
    work,
    page,
    entry,
    body_object: bpy.types.Object,
    outer_edge_material: bpy.types.Material,
    mask_info=None,
    geometry_sig=None,
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
    edge_width_mm = scaled_entry_width_mm(entry, "outer_white_margin_width_mm", 0.0)
    if edge_width_mm <= 1.0e-6:
        remove_balloon_outer_edge_mesh(balloon_id)
        return None
    cached = band_geometry_cache_hit(_outer_edge_mesh_object_name(balloon_id), geometry_sig)
    if cached is not None:
        return _attach_band_mesh_object(
            obj_name=_outer_edge_mesh_object_name(balloon_id),
            mesh=cached.data,
            material=outer_edge_material,
            body_object=body_object,
            scene=scene,
            kind=_KIND_OUTER_EDGE,
            balloon_id=balloon_id,
            visible=bool(getattr(entry, "visible", True)),
            mask_info=mask_info,
            geometry_sig=geometry_sig,
        )
    line_style = str(getattr(entry, "line_style", "") or "")
    line_width_mm = 0.0 if line_style == "none" else scaled_entry_width_mm(entry, "line_width_mm", 0.3)
    samples = _body_samples_for_line_mesh(entry, body_object)
    if len(samples) < 3:
        remove_balloon_outer_edge_mesh(balloon_id)
        return None
    samples, _tails_merged = _outline_samples_with_tails(entry, samples)

    valley_sharp = _valley_sharp_for_entry(entry)
    line_width_m = line_width_mm * 0.001
    edge_width_m = edge_width_mm * 0.001
    body_center_m = _balloon_center_m_from_samples(samples)

    # 「主線が描く変わったアウトライン」 (body + 主線 polygon の union) を取得し、
    # その外側に均一幅 edge_width で buffer する。 主線が dynamic (谷/山幅変動) でも
    # アウトラインの形状追従だけが反映され、 フチ自身は常に均一幅で描かれる。
    outline = _compute_balloon_outer_outline(entry, samples, body_center_m, line_width_m, valley_sharp)
    if outline is None or outline.is_empty:
        remove_balloon_outer_edge_mesh(balloon_id)
        return None

    # mitre_limit は 主線アウトラインの細いスパイク先端で過剰延長を起こさないよう、
    # 主線 dynamic と同じ 10.0 を上限とする (sharp は保ちつつ、 外向きにヒゲ状に
    # 飛び出さない)。
    join = 2 if valley_sharp else 1
    mitre = 10.0 if valley_sharp else _ROUND_MITRE_LIMIT
    try:
        outer_buffer = outline.buffer(edge_width_m, join_style=join, mitre_limit=mitre)
        outer_band = outer_buffer.difference(outline)
    except Exception:  # noqa: BLE001
        outer_band = None
    if outer_band is None or outer_band.is_empty:
        remove_balloon_outer_edge_mesh(balloon_id)
        return None

    polys = _shapely_geom_to_outer_holes_list(outer_band)
    if not polys:
        remove_balloon_outer_edge_mesh(balloon_id)
        return None

    mesh_name = _outer_edge_mesh_data_name(balloon_id)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    _build_band_mesh_from_polygons(mesh, polys, OUTER_EDGE_Z_OFFSET_M)

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
        geometry_sig=geometry_sig,
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
    geometry_sig=None,
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
    edge_width_mm = scaled_entry_width_mm(entry, "inner_white_margin_width_mm", 0.0)
    if edge_width_mm <= 1.0e-6:
        remove_balloon_inner_edge_mesh(balloon_id)
        return None
    cached = band_geometry_cache_hit(_inner_edge_mesh_object_name(balloon_id), geometry_sig)
    if cached is not None:
        return _attach_band_mesh_object(
            obj_name=_inner_edge_mesh_object_name(balloon_id),
            mesh=cached.data,
            material=inner_edge_material,
            body_object=body_object,
            scene=scene,
            kind=_KIND_INNER_EDGE,
            balloon_id=balloon_id,
            visible=bool(getattr(entry, "visible", True)),
            mask_info=mask_info,
            geometry_sig=geometry_sig,
        )
    line_style = str(getattr(entry, "line_style", "") or "")
    line_width_mm = 0.0 if line_style == "none" else scaled_entry_width_mm(entry, "line_width_mm", 0.3)
    samples = _body_samples_for_line_mesh(entry, body_object)
    if len(samples) < 3:
        remove_balloon_inner_edge_mesh(balloon_id)
        return None
    samples, _tails_merged = _outline_samples_with_tails(entry, samples)

    valley_sharp = _valley_sharp_for_entry(entry)
    edge_width_m = edge_width_mm * 0.001

    # 主線は外側アライメントなので body 内側には主線が無い。 内側フチは body の内側に
    # 均一幅 edge_width で描く。 主線の谷/山幅変動とは独立 (フチは常に均一幅)。
    body_poly = _build_body_polygon(samples)
    if body_poly is None:
        remove_balloon_inner_edge_mesh(balloon_id)
        return None

    join = 2 if valley_sharp else 1
    mitre = _SHARP_MITRE_LIMIT if valley_sharp else _ROUND_MITRE_LIMIT
    try:
        inner_shrunk = body_poly.buffer(-edge_width_m, join_style=join, mitre_limit=mitre)
        if inner_shrunk.is_empty:
            inner_band = body_poly
        else:
            inner_band = body_poly.difference(inner_shrunk)
    except Exception:  # noqa: BLE001
        inner_band = None
    if inner_band is None or inner_band.is_empty:
        remove_balloon_inner_edge_mesh(balloon_id)
        return None

    polys = _shapely_geom_to_outer_holes_list(inner_band)
    if not polys:
        remove_balloon_inner_edge_mesh(balloon_id)
        return None

    mesh_name = _inner_edge_mesh_data_name(balloon_id)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    _build_band_mesh_from_polygons(mesh, polys, INNER_EDGE_Z_OFFSET_M)

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
        geometry_sig=geometry_sig,
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
    geometry_sig=None,
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
    line_width_mm = scaled_entry_width_mm(entry, "line_width_mm", 0.3)
    multi_width_mm = scaled_entry_width_mm(entry, "multi_line_width_mm", 0.0)
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
        shape_norm in _DYNAMIC_WIDTH_SHAPES
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
    cached = band_geometry_cache_hit(_multi_line_mesh_object_name(balloon_id), geometry_sig)
    if cached is not None:
        return _attach_band_mesh_object(
            obj_name=_multi_line_mesh_object_name(balloon_id),
            mesh=cached.data,
            material=line_material,
            body_object=body_object,
            scene=scene,
            kind=_KIND_MULTI_LINE,
            balloon_id=balloon_id,
            visible=bool(getattr(entry, "visible", True)),
            mask_info=mask_info,
            geometry_sig=geometry_sig,
        )
    direction = str(getattr(entry, "multi_line_direction", "outside") or "outside")
    if direction == "both":
        sides = ("inside", "outside")
    elif direction == "inside":
        sides = ("inside",)
    else:
        sides = ("outside",)

    samples = _body_samples_for_line_mesh(entry, body_object)
    if len(samples) < 3:
        remove_balloon_multi_line_mesh(balloon_id)
        return None
    samples, _tails_merged = _outline_samples_with_tails(entry, samples)

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
    # 曲線形状 (雲/フワフワ/トゲ曲線) は外側アライメントで帯を作る (内側オフセットの
    # 凸頂点自己交差 = くさび を避ける)。 トゲ直線は従来の中心アライメント。
    ml_straight = _is_straight_edged(
        [(float(s[0]), float(s[1])) for s in samples], SAMPLES_PER_SEGMENT
    )
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
                # 曲線 + 外向きリングは外側アライメント。 中心線をリング内側エッジに置き、
                # 帯は外向きにのみ展開する。 こうすると凸バンプ先で内側オフセットが無く、
                # くさびアーティファクトが出ない。 トゲ直線/内向きは従来の中心アライメント。
                if (not ml_straight) and side == "outside":
                    ml_outside_align = True
                    ml_offset_mm = ring_inner_mm
                else:
                    ml_outside_align = False
                    ml_offset_mm = signed_offset_mm
                sub_polys = _build_dynamic_multi_line_polygons(
                    body_samples=samples,
                    signed_offset_m=ml_offset_mm * 0.001,
                    base_width_m=ring_width_mm * 0.001,
                    valley_width_m=ring_valley_width_mm * 0.001,
                    peak_width_m=ring_peak_width_mm * 0.001,
                    length_scale=ring_length_scale,
                    valley_sharp=valley_sharp,
                    balloon_center_m=body_center_m,
                    cross_extension_m=cross_extension_m,
                    peak_extension_m=0.0,
                    outside_align=ml_outside_align,
                    peaks_rounded=(shape_norm in _ROUNDED_PEAK_SHAPES),
                )
                polygons.extend(sub_polys)
            else:
                band = build_offset_band_polygon(
                    samples,
                    signed_offset_m=signed_offset_mm * 0.001,
                    band_width_m=ring_width_mm * 0.001,
                    valley_sharp=valley_sharp,
                    peaks_rounded=(shape_norm in _ROUNDED_PEAK_SHAPES),
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
        geometry_sig=geometry_sig,
    )


# --- しっぽ主線フチ (各しっぽ polygon の外周/内周オフセット band) -----------

BALLOON_TAIL_MAIN_LINE_MESH_NAME_PREFIX = "balloon_tail_main_line_mesh_"
# 「結合済みで独立しっぽ線メッシュ無し」を記憶する署名 (本体オブジェクトに保持)
_PROP_TAIL_MAIN_ABSENT_SIG = "bname_tail_main_absent_sig"


def _tail_main_line_mesh_data_name(balloon_id: str) -> str:
    return f"{BALLOON_TAIL_MAIN_LINE_MESH_NAME_PREFIX}{balloon_id}"


def _tail_main_line_mesh_object_name(balloon_id: str) -> str:
    return f"{BALLOON_TAIL_MAIN_LINE_MESH_NAME_PREFIX}{balloon_id}"


def remove_balloon_tail_main_line_mesh(balloon_id: str) -> None:
    if not balloon_id:
        return
    obj_name = _tail_main_line_mesh_object_name(balloon_id)
    obj = bpy.data.objects.get(obj_name)
    if obj is not None:
        if object_preserve.is_preserved(obj):
            return
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:  # noqa: BLE001
            pass
    mesh_name = _tail_main_line_mesh_data_name(balloon_id)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is not None and mesh.users == 0:
        try:
            bpy.data.meshes.remove(mesh)
        except Exception:  # noqa: BLE001
            pass


def ensure_balloon_tail_main_line_mesh(
    *,
    scene,
    work,
    page,
    entry,
    body_object: bpy.types.Object,
    line_material: bpy.types.Material,
    mask_info=None,
    geometry_sig=None,
) -> Optional[bpy.types.Object]:
    """各しっぽの輪郭に沿った主線フチ Mesh をひとつにまとめて生成する.

    フキダシ本体の主線 (= `ensure_balloon_line_mesh`) と同じ規則で、各しっぽ
    polygon の外周 +line_width/2 までと内側 -line_width/2 までの帯を Shapely
    buffer で構築する。
    """
    from . import balloon_tail_geom
    from .geom import Rect

    balloon_id = str(getattr(entry, "id", "") or "")
    if not balloon_id:
        return None

    line_style = str(getattr(entry, "line_style", "") or "")
    line_width_mm = 0.0 if line_style == "none" else scaled_entry_width_mm(entry, "line_width_mm", 0.3)
    if line_width_mm <= 1.0e-6:
        remove_balloon_tail_main_line_mesh(balloon_id)
        return None

    tails = list(getattr(entry, "tails", []) or [])
    if not tails:
        remove_balloon_tail_main_line_mesh(balloon_id)
        return None

    # 前回と同じジオメトリで「しっぽは本体に結合済み (独立メッシュ無し)」だった
    # 場合は、結合判定 (shapely union) ごとスキップする
    if (
        geometry_sig
        and body_object is not None
        and str(body_object.get(_PROP_TAIL_MAIN_ABSENT_SIG, "") or "") == str(geometry_sig)
    ):
        return None
    cached = band_geometry_cache_hit(_tail_main_line_mesh_object_name(balloon_id), geometry_sig)
    if cached is not None:
        return _attach_band_mesh_object(
            obj_name=_tail_main_line_mesh_object_name(balloon_id),
            mesh=cached.data,
            material=line_material,
            body_object=body_object,
            scene=scene,
            kind=_KIND_TAIL_MAIN_LINE,
            balloon_id=balloon_id,
            visible=bool(getattr(entry, "visible", True)),
            mask_info=mask_info,
            geometry_sig=geometry_sig,
        )

    body_spline = _resolve_body_spline(body_object)
    if body_spline is not None:
        body_samples = sample_body_spline(body_spline, SAMPLES_PER_SEGMENT)
        if len(body_samples) >= 3:
            _merged_samples, tails_merged = _outline_samples_with_tails(entry, body_samples)
            if tails_merged:
                # 結合済みしっぽの主線は本体側の帯に含まれる。「角を尖らせる」
                # しっぽの折れ角・先端の絞りも本体主線側 (ensure_balloon_line_mesh)
                # で加工されるため、ここでは独立メッシュを持たない。
                remove_balloon_tail_main_line_mesh(balloon_id)
                if geometry_sig and body_object is not None:
                    body_object[_PROP_TAIL_MAIN_ABSENT_SIG] = str(geometry_sig)
                return None

    rect = Rect(
        0.0,
        0.0,
        max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0)),
        max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0)),
    )
    # rect-local mm → balloon-local mm の平行移動量
    ox_mm = (
        float(getattr(entry, "center_offset_x_mm", 0.0) or 0.0)
        - max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0)) * 0.5
    )
    oy_mm = (
        float(getattr(entry, "center_offset_y_mm", 0.0) or 0.0)
        - max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0)) * 0.5
    )
    line_width_m = line_width_mm * 0.001

    polygons: list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]] = []
    tail_sample_loops: list[list[tuple[float, float]]] = []
    for tail in tails:
        try:
            pts_mm = balloon_tail_geom.polygon_for_tail(rect, tail)
        except Exception:  # noqa: BLE001
            continue
        if not pts_mm or len(pts_mm) < 3:
            continue
        pts_mm = free_transform.transform_entry_local_points(entry, pts_mm)
        samples = [(mm_to_m(x + ox_mm), mm_to_m(y + oy_mm)) for x, y in pts_mm]
        tail_sharp = bool(getattr(tail, "sharp_corners", False))
        if line_style in {"dashed", "dotted"}:
            polygons.extend(
                _build_dashed_band_polygons(
                    samples,
                    line_width_m=line_width_m,
                    line_style=line_style,
                    valley_sharp=tail_sharp,
                    dash_segment_mm=line_pattern.dashed_segment_mm(entry, line_width_mm),
                    dash_gap_mm=line_pattern.dashed_gap_mm(entry, line_width_mm),
                    dotted_gap_mm=line_pattern.dotted_gap_mm(entry, line_width_mm),
                )
            )
        else:
            band = build_offset_band_polygon(
                samples,
                signed_offset_m=0.0,
                band_width_m=line_width_m,
                valley_sharp=tail_sharp,
            )
            if band is not None:
                polygons.append(band)
                tail_sample_loops.append(samples)

    if not polygons:
        remove_balloon_tail_main_line_mesh(balloon_id)
        return None

    mesh_name = _tail_main_line_mesh_data_name(balloon_id)
    mesh = bpy.data.meshes.get(mesh_name)
    if mesh is None:
        mesh = bpy.data.meshes.new(mesh_name)
    _build_band_mesh_from_polygons(mesh, polygons, LINE_Z_OFFSET_M)
    _apply_ribbon_uv(mesh, entry, tail_sample_loops, line_width_m)

    return _attach_band_mesh_object(
        obj_name=_tail_main_line_mesh_object_name(balloon_id),
        mesh=mesh,
        material=line_material,
        body_object=body_object,
        scene=scene,
        kind=_KIND_TAIL_MAIN_LINE,
        balloon_id=balloon_id,
        visible=bool(getattr(entry, "visible", True)),
        mask_info=mask_info,
        geometry_sig=geometry_sig,
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
    if object_preserve.is_preserved(obj):
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


def remove_balloon_flash_white_line_mesh(balloon_id: str) -> None:
    if not balloon_id:
        return
    _remove_named_band_mesh(_flash_white_line_mesh_object_name(balloon_id))


def remove_all_balloon_band_meshes(balloon_id: str) -> None:
    """主線・フチ・多重線の Mesh をまとめて撤去する."""
    remove_balloon_line_mesh(balloon_id)
    remove_balloon_outer_edge_mesh(balloon_id)
    remove_balloon_inner_edge_mesh(balloon_id)
    remove_balloon_multi_line_mesh(balloon_id)
    remove_balloon_flash_white_line_mesh(balloon_id)


def cleanup_orphan_line_meshes(valid_balloon_ids: set[str]) -> int:
    """主線・フチ・多重線の Mesh のうち、有効な balloon id を持たないものを撤去する."""
    removed = 0
    for obj in list(bpy.data.objects):
        if object_preserve.is_preserved(obj):
            continue
        kind = str(obj.get(PROP_BALLOON_LINE_MESH_KIND, "") or "")
        if kind not in _ALL_KINDS:
            continue
        owner_id = str(obj.get(PROP_BALLOON_LINE_MESH_OWNER_ID, "") or "")
        if owner_id and owner_id not in valid_balloon_ids:
            object_preserve.preserve_object(obj, "作品データにないフキダシ線メッシュを保持")
            removed += 1
    return removed
