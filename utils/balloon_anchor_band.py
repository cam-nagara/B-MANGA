# -*- coding: utf-8 -*-
"""「角を尖らせる」新方式J (頂点距離方式) の帯構築.

2026-07-23 ユーザー承認仕様。山と谷の頂点 (アンカー) を外向き二等分線方向へ
「オフセット量 × アンカー別倍率」だけ変位し、頂点間の曲線区間は元の輪郭の
区間を相似変換 (回転 + 拡縮) して新しい頂点間に張り直す。

- カーブの角はアンカー (山・谷の頂点) にしか現れない (交差・途中の折れなし)
- 角の位置では「外側輪郭の角 ↔ 内側輪郭の角」の距離が 帯幅 × 倍率 になる
- 帯の途中の太さはカーブなりに変わる (均一幅ではない。ユーザー了承済み)
- 倍率の既定値: 山 = 1.5 / 谷 = 0.5 (筆圧のように山で太く谷で細く)

座標単位に依存しない (ビューポートの m / 書き出しの mm どちらでも動く。
検出窓は周長比で決める)。アンカーが検出できない形状 (楕円など) では None を
返し、呼び出し側は従来のミター方式へフォールバックする。
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

# 山・谷とみなす最小の方向転換角 (度)。周長の 1/400 の窓で測る。
_TURN_THRESHOLD_DEG = 18.0
# 検出窓の周長比 (窓 ≈ 周長 / 400。60mm 級フキダシで約 1mm)
_WINDOW_PERIMETER_RATIO = 1.0 / 400.0


def anchor_cfg_for_entry(entry) -> Optional[tuple[float, float]]:
    """entry から新方式Jの設定 (山倍率, 谷倍率) を返す。標準方式なら None.

    ビューポート (balloon_line_mesh) と書き出し (export_balloon) の両方から
    参照される正典ヘルパー。
    """
    sp = getattr(entry, "shape_params", None)
    if sp is None or not bool(getattr(sp, "cloud_valley_sharp", False)):
        return None
    if str(getattr(sp, "sharp_corner_method", "miter") or "miter") != "anchor":
        return None
    peak = float(getattr(sp, "sharp_peak_width_scale", 1.5) or 1.5)
    valley = float(getattr(sp, "sharp_valley_width_scale", 0.5) or 0.5)
    return (max(0.05, peak), max(0.05, valley))


def _dedupe_closed(points: Sequence) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for p in points:
        q = (float(p[0]), float(p[1]))
        if not pts or math.hypot(q[0] - pts[-1][0], q[1] - pts[-1][1]) > 1.0e-12:
            pts.append(q)
    if len(pts) >= 2 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) <= 1.0e-12:
        pts.pop()
    return pts


def _ensure_ccw(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    area2 = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        area2 += x0 * y1 - x1 * y0
    if area2 < 0.0:
        return list(reversed(pts))
    return pts


def _dir(a: tuple[float, float], b: tuple[float, float]) -> Optional[tuple[float, float]]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    h = math.hypot(dx, dy)
    if h <= 1.0e-15:
        return None
    return (dx / h, dy / h)


def detect_anchors(points: Sequence) -> Optional[dict]:
    """輪郭から山・谷の頂点 (アンカー) を検出する.

    戻り値: {"pts": CCW点列, "anchors": [index...], "bis": {index: 外向き二等分線},
             "is_peak": {index: bool}} または None (アンカー不足)。
    """
    pts = _ensure_ccw(_dedupe_closed(points))
    n = len(pts)
    if n < 12:
        return None
    perimeter = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        perimeter += math.hypot(x1 - x0, y1 - y0)
    if perimeter <= 1.0e-12:
        return None
    avg_spacing = perimeter / n
    window = perimeter * _WINDOW_PERIMETER_RATIO
    k = max(1, int(round(window / avg_spacing)))
    k2 = max(2, int(round(window * 1.33 / avg_spacing)))

    turns: list[tuple[float, float]] = []
    for i in range(n):
        d1 = _dir(pts[(i - k) % n], pts[i])
        d2 = _dir(pts[i], pts[(i + k) % n])
        if d1 is None or d2 is None:
            turns.append((0.0, 0.0))
            continue
        cross = d1[0] * d2[1] - d1[1] * d2[0]
        dot = d1[0] * d2[0] + d1[1] * d2[1]
        turns.append((math.degrees(math.atan2(abs(cross), dot)), cross))

    def cluster(pred) -> list[int]:
        out: list[int] = []
        cl: list[tuple[int, float]] = []
        prev: Optional[int] = None
        for i in range(n):
            if pred(i):
                if prev is not None and i - prev > k and cl:
                    out.append(max(cl, key=lambda t: t[1])[0])
                    cl = []
                cl.append((i, turns[i][0]))
                prev = i
        if cl:
            out.append(max(cl, key=lambda t: t[1])[0])
        return out

    peaks = cluster(lambda i: turns[i][0] > _TURN_THRESHOLD_DEG and turns[i][1] > 0.0)
    valleys = cluster(lambda i: turns[i][0] > _TURN_THRESHOLD_DEG and turns[i][1] < 0.0)
    anchors = sorted(set(peaks) | set(valleys))
    if len(anchors) < 3:
        return None

    bis: dict[int, tuple[float, float]] = {}
    is_peak: dict[int, bool] = {}
    peak_set = set(peaks)
    for vi in anchors:
        d1 = _dir(pts[(vi - k2) % n], pts[vi])
        d2 = _dir(pts[vi], pts[(vi + k2) % n])
        if d1 is None or d2 is None:
            return None
        n1 = (d1[1], -d1[0])
        n2 = (d2[1], -d2[0])
        sx, sy = n1[0] + n2[0], n1[1] + n2[1]
        h = math.hypot(sx, sy)
        bis[vi] = (sx / h, sy / h) if h > 1.0e-9 else n1
        is_peak[vi] = vi in peak_set
    return {"pts": pts, "anchors": anchors, "bis": bis, "is_peak": is_peak}


def anchor_offset_outline(
    detected: dict,
    delta: float,
    peak_scale: float,
    valley_scale: float,
) -> Optional[list[tuple[float, float]]]:
    """検出済みアンカー情報から、符号付きオフセット delta の輪郭カーブを生成する.

    delta: 正 = 外側へ / 負 = 内側へ。アンカーは delta × (山なら peak_scale,
    谷なら valley_scale) だけ二等分線方向へ変位し、区間は相似変換で追従する。
    """
    pts = detected["pts"]
    anchors = detected["anchors"]
    bis = detected["bis"]
    is_peak = detected["is_peak"]
    n = len(pts)
    m = len(anchors)
    if m < 3:
        return None
    out: list[tuple[float, float]] = []
    for a_idx in range(m):
        vi = anchors[a_idx]
        vj = anchors[(a_idx + 1) % m]
        f1 = peak_scale if is_peak[vi] else valley_scale
        f2 = peak_scale if is_peak[vj] else valley_scale
        b1 = bis[vi]
        b2 = bis[vj]
        p0 = complex(*pts[vi])
        p1 = complex(*pts[vj])
        q0 = p0 + delta * f1 * complex(*b1)
        q1 = p1 + delta * f2 * complex(*b2)
        chord = p1 - p0
        if abs(chord) < 1.0e-12:
            continue
        M = (q1 - q0) / chord
        j = vi
        while j != vj:
            z = complex(*pts[j])
            w = q0 + M * (z - p0)
            out.append((w.real, w.imag))
            j = (j + 1) % n
    if len(out) < 3:
        return None
    return out


def _heal_polygon(coords: list[tuple[float, float]]):
    from shapely.geometry import Polygon  # type: ignore

    poly = Polygon(coords)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty:
        return None
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    if poly.geom_type != "Polygon":
        return None
    return poly


def anchor_band_geometry(
    points: Sequence,
    d_lo: float,
    d_hi: float,
    peak_scale: float,
    valley_scale: float,
    *,
    detected: Optional[dict] = None,
):
    """[d_lo, d_hi] (符号付き。d_hi > d_lo) の帯の Shapely ジオメトリを返す.

    アンカーが検出できない場合や構築に失敗した場合は None (呼び出し側で
    従来方式へフォールバックする)。
    """
    det = detected if detected is not None else detect_anchors(points)
    if det is None:
        return None
    hi = anchor_offset_outline(det, d_hi, peak_scale, valley_scale)
    lo = anchor_offset_outline(det, d_lo, peak_scale, valley_scale)
    if hi is None or lo is None:
        return None
    try:
        hi_poly = _heal_polygon(hi)
        lo_poly = _heal_polygon(lo)
        if hi_poly is None or lo_poly is None:
            return None
        band = hi_poly.difference(lo_poly)
    except Exception:  # noqa: BLE001
        return None
    if band is None or band.is_empty:
        return None
    return band


def anchor_band_rings(
    points: Sequence,
    d_lo: float,
    d_hi: float,
    peak_scale: float,
    valley_scale: float,
    *,
    detected: Optional[dict] = None,
) -> Optional[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]:
    """帯を (outer_ring, holes) 形式で返す (build_offset_band_polygon と同じ契約)."""
    band = anchor_band_geometry(
        points, d_lo, d_hi, peak_scale, valley_scale, detected=detected
    )
    if band is None:
        return None
    if band.geom_type == "Polygon":
        main = band
    elif band.geom_type == "MultiPolygon":
        main = max(band.geoms, key=lambda g: g.area)
    else:
        return None
    outer_ring = list(main.exterior.coords)
    holes = [list(r.coords) for r in main.interiors]
    return outer_ring, holes


def anchor_band_outer_holes_list(
    points: Sequence,
    d_lo: float,
    d_hi: float,
    peak_scale: float,
    valley_scale: float,
    *,
    detected: Optional[dict] = None,
) -> list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]:
    """帯を [(outer_ring, holes), ...] 形式で返す (複数ピース対応)."""
    band = anchor_band_geometry(
        points, d_lo, d_hi, peak_scale, valley_scale, detected=detected
    )
    if band is None:
        return []
    geoms = []
    if band.geom_type == "Polygon":
        geoms = [band]
    elif band.geom_type == "MultiPolygon":
        geoms = list(band.geoms)
    out = []
    for g in geoms:
        if g.is_empty or g.area <= 1.0e-14:
            continue
        out.append((list(g.exterior.coords), [list(r.coords) for r in g.interiors]))
    return out
