"""トゲ（曲線）の鋭い線端を、先端位置を保った曲線へ整える。

確定仕様 (2026-07-15 ユーザー指示 + 手描き理想図):
- 各線 (主線・多重線・フチ) は v0.6.166 承認形状のとおり平行・等間隔のまま、
  それぞれ自分の鋭いミター角で折り返す。先端の位置 (角の長さ) は変えない。
- ミター結合が作る「直線のくさび」の輪郭だけを、肩で接線連続な曲線に置換する
  (線の外側輪郭が途中でカクッと直線に折れてはならない)。内側輪郭 (穴) も
  同じ規則で曲線化し、線の内外で非対称な折れを残さない。
- 曲線化は全ての山・全ての線で同一の固定式で行う (パラメータ探索で山ごとに
  違う曲率を採用すると、線ごとに歪んで見える — v0.6.505 の不具合の原因)。
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import statistics
from typing import Sequence

from . import python_deps

Point2D = tuple[float, float]
BandPolygon = tuple[list[Point2D], list[list[Point2D]]]

_REFERENCE_TURN_RAD = math.radians(25.0)
_MITRE_APEX_TURN_RAD = math.radians(45.0)
_MITRE_LEG_RATIO = 2.2
_CURVE_STEPS = 24
# 肩の接線方向へ伸ばす制御点距離 (ミター脚長に対する比)。肩で接線連続を作る。
_SHOULDER_TANGENT_RATIO = 0.40
# 先端側の制御点距離 (ミター脚長に対する比)。先端はミター脚方向に接して
# 鋭さを保ったまま、脚の直線を曲線に置き換える。
_TIP_TANGENT_RATIO = 0.40
# ミター脚の直線を「内向きに膨らむ弧」(側面の曲率の続き) に置換する深さ。
#   深さ = min(オフセット距離 × _INWARD_OFFSET_RATIO,
#              楔の左右肩の距離 × _INWARD_WEDGE_RATIO)
# 内向き (楔の内側 = トゲ軸側) に曲げるので、隣の線との隙間は決して潰れず、
# 線幅は先端に向かって自然に細くなる (ユーザー許容済み)。オフセット距離
# 基準は内在的な尺度なので、同じ物理輪郭 (例: 外側フチの内周 = 主線の外周)
# が別の帯として処理されても同じ曲線になる。
_INWARD_OFFSET_RATIO = 0.05
_INWARD_WEDGE_RATIO = 0.35
# 極端に狭い線間隔でも隣の帯と融合しないための保険上限 (隣接帯距離に対する比)
_BULGE_GAP_RATIO = 0.25
# 3次ベジェで制御点オフセット→最大逸脱の換算係数 (t=2/3 での重み 3t²(1-t))
_CUBIC_DEPTH_GAIN = 0.444


@dataclass(frozen=True)
class _ReferencePeak:
    point: Point2D
    angle: float
    incoming: Point2D
    outgoing: Point2D
    offset_factor: float


@dataclass(frozen=True)
class _MitreCandidate:
    index: int
    point: Point2D
    angle: float


@dataclass
class _PreparedRing:
    points: list[Point2D]
    was_closed: bool
    apexes: set[int]
    # 山頂ごとの正規化オフセット距離 (≒ この輪郭が本体から何mm外か)
    offsets: dict[int, float]


def _distance(a: Point2D, b: Point2D) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _unit(dx: float, dy: float) -> Point2D | None:
    length = math.hypot(dx, dy)
    if length <= 1.0e-15:
        return None
    return (dx / length, dy / length)


def _direction(a: Point2D, b: Point2D) -> Point2D | None:
    return _unit(b[0] - a[0], b[1] - a[1])


def _dot(a: Point2D, b: Point2D) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _vector_angle(a: Point2D, b: Point2D) -> float:
    return math.acos(max(-1.0, min(1.0, _dot(a, b))))


def _open_ring(points: Sequence[Point2D]) -> tuple[list[Point2D], bool]:
    ring = [(float(point[0]), float(point[1])) for point in points]
    closed = len(ring) >= 2 and _distance(ring[0], ring[-1]) <= 1.0e-15
    if closed:
        ring.pop()
    return ring, closed


def _polygon_center(points: Sequence[Point2D]) -> Point2D:
    area2 = 0.0
    cx6 = 0.0
    cy6 = 0.0
    for index, point in enumerate(points):
        nxt = points[(index + 1) % len(points)]
        cross = point[0] * nxt[1] - nxt[0] * point[1]
        area2 += cross
        cx6 += (point[0] + nxt[0]) * cross
        cy6 += (point[1] + nxt[1]) * cross
    if abs(area2) <= 1.0e-15:
        return (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )
    return (cx6 / (3.0 * area2), cy6 / (3.0 * area2))


def _turn_radians(previous: Point2D, point: Point2D, following: Point2D) -> float:
    incoming = _direction(previous, point)
    outgoing = _direction(point, following)
    if incoming is None or outgoing is None:
        return 0.0
    return _vector_angle(incoming, outgoing)


def _reference_peaks(reference: Sequence[Point2D], center: Point2D) -> list[_ReferencePeak]:
    peaks: list[_ReferencePeak] = []
    count = len(reference)
    for index, point in enumerate(reference):
        previous = reference[(index - 1) % count]
        following = reference[(index + 1) % count]
        if _turn_radians(previous, point, following) < _REFERENCE_TURN_RAD:
            continue
        radius = _distance(center, point)
        if radius + 1.0e-12 < max(_distance(center, previous), _distance(center, following)):
            continue
        incoming = _direction(previous, point)
        outgoing = _direction(point, following)
        if incoming is None or outgoing is None:
            continue
        # `_turn_radians` は進行方向どうしの角度なので、鋭い山ほど pi に近い。
        # mitre先端までの距離へ cos(turn/2) を掛けると、山ごとの鋭さに依存せず
        # 元のbuffer offsetへ正規化できる。
        offset_factor = max(1.0e-6, math.cos(_vector_angle(incoming, outgoing) * 0.5))
        peaks.append(_ReferencePeak(
            point=point,
            angle=math.atan2(point[1] - center[1], point[0] - center[0]),
            incoming=incoming,
            outgoing=outgoing,
            offset_factor=offset_factor,
        ))
    return peaks


def _angle_distance(a: float, b: float) -> float:
    return abs((a - b + math.pi) % (2.0 * math.pi) - math.pi)


def _mitre_candidates(ring: Sequence[Point2D], center: Point2D) -> list[_MitreCandidate]:
    candidates: list[_MitreCandidate] = []
    count = len(ring)
    for index, point in enumerate(ring):
        previous2 = ring[(index - 2) % count]
        previous = ring[(index - 1) % count]
        following = ring[(index + 1) % count]
        following2 = ring[(index + 2) % count]
        incoming_leg = _distance(previous, point)
        outgoing_leg = _distance(point, following)
        incoming_shoulder = max(_distance(previous2, previous), 1.0e-15)
        outgoing_shoulder = max(_distance(following, following2), 1.0e-15)
        if incoming_leg < incoming_shoulder * _MITRE_LEG_RATIO:
            continue
        if outgoing_leg < outgoing_shoulder * _MITRE_LEG_RATIO:
            continue
        if _turn_radians(previous, point, following) < _MITRE_APEX_TURN_RAD:
            continue
        incoming_tangent = _direction(previous2, previous)
        outgoing_tangent = _direction(following, following2)
        incoming_leg_direction = _direction(previous, point)
        outgoing_leg_direction = _direction(point, following)
        if None in (
            incoming_tangent,
            outgoing_tangent,
            incoming_leg_direction,
            outgoing_leg_direction,
        ):
            continue
        # 両側に長い脚と独立した肩接線があるmitreだけを候補にする。
        # referenceとの向き・距離照合は1対1対応を作る段階で行う。
        candidates.append(_MitreCandidate(
            index=index,
            point=point,
            angle=math.atan2(point[1] - center[1], point[0] - center[0]),
        ))
    return candidates


def _peak_angle_tolerance(peaks: Sequence[_ReferencePeak], index: int) -> float:
    """隣の山へ誤対応しない範囲で、この山の候補角度許容値を返す。"""
    if len(peaks) <= 1:
        return math.radians(10.0)
    nearest = min(
        _angle_distance(peaks[index].angle, other.angle)
        for other_index, other in enumerate(peaks)
        if other_index != index
    )
    return max(math.radians(3.0), min(math.radians(12.0), nearest * 0.47))


def _matched_apex_indices(
    ring: Sequence[Point2D], reference: Sequence[Point2D], center: Point2D
) -> set[int]:
    return set(_matched_apex_offsets(ring, reference, center))


def _matched_apex_offsets(
    ring: Sequence[Point2D], reference: Sequence[Point2D], center: Point2D
) -> dict[int, float]:
    """本体山頂と1対1対応したミター先端の {ring頂点index: 正規化オフセット}。"""
    peaks = _reference_peaks(reference, center)
    candidates = _mitre_candidates(ring, center)
    if not peaks or not candidates:
        return {}
    # 同じoffsetで作ったbodyのmitre先端は、山ごとに生の延長距離が大きく
    # 異なっても `distance * cos(turn/2)` がほぼ同じになる。これを使うと、
    # bodyへ結合したしっぽ先端を角度だけで誤選択せずに除外できる。
    pairs: list[tuple[float, float, float, int, int]] = []
    for peak_index, peak in enumerate(peaks):
        angle_tolerance = _peak_angle_tolerance(peaks, peak_index)
        for candidate_index, candidate in enumerate(candidates):
            angle_error = _angle_distance(candidate.angle, peak.angle)
            if angle_error > angle_tolerance:
                continue
            distance = _distance(candidate.point, peak.point)
            normalized_offset = distance * peak.offset_factor
            pairs.append((normalized_offset, angle_error, angle_tolerance, peak_index, candidate_index))
    if not pairs:
        return {}
    offsets = [normalized for normalized, _angle, _tol, _peak, _candidate in pairs]
    median_offset = statistics.median(offsets)
    mad = statistics.median(abs(offset - median_offset) for offset in offsets)
    offset_tolerance = max(4.0 * mad, median_offset * 0.08, 1.0e-9)
    filtered = [
        pair for pair in pairs
        if abs(pair[0] - median_offset) <= offset_tolerance
    ]
    filtered.sort(key=lambda pair: (
        abs(pair[0] - median_offset) / offset_tolerance,
        pair[1] / pair[2],
    ))
    matched_peaks: set[int] = set()
    matched_candidates: set[int] = set()
    matches: dict[int, float] = {}
    for normalized_offset, _angle, _tolerance, peak_index, candidate_index in filtered:
        if peak_index in matched_peaks or candidate_index in matched_candidates:
            continue
        matched_peaks.add(peak_index)
        matched_candidates.add(candidate_index)
        matches[candidates[candidate_index].index] = normalized_offset
    return matches


def _cubic_point(p0: Point2D, p1: Point2D, p2: Point2D, p3: Point2D, t: float) -> Point2D:
    one = 1.0 - t
    a = one * one * one
    b = 3.0 * one * one * t
    c = 3.0 * one * t * t
    d = t * t * t
    return (
        a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
        a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
    )


def _curve_parameter(step: int) -> float:
    """肩と先端へ点を寄せ、少ない頂点でも接線の折れを見せない。"""
    unit = step / _CURVE_STEPS
    incoming = unit * unit * unit
    outgoing = (1.0 - unit) ** 3
    return incoming / (incoming + outgoing)


def _control1_length(
    tangent: Point2D, leg_direction: Point2D, leg: float, depth: float
) -> float:
    """肩側制御点の長さ。接線方向は維持しつつ、離散サンプリング由来の
    接線とミター脚の角度差による横ズレが depth を超えないよう制限する。"""
    length = leg * _SHOULDER_TANGENT_RATIO
    lateral = math.sin(_vector_angle(tangent, leg_direction))
    if lateral > 1.0e-9:
        length = min(length, depth / lateral)
    return length


def _inward_normal(
    leg_direction: Point2D, apex: Point2D, other_shoulder: Point2D
) -> Point2D:
    """ミター脚に直交し、楔の内側 (反対側の肩) を向く単位ベクトル。"""
    normal = (-leg_direction[1], leg_direction[0])
    toward = (other_shoulder[0] - apex[0], other_shoulder[1] - apex[1])
    if _dot(normal, toward) < 0.0:
        return (-normal[0], -normal[1])
    return normal


def _apex_depth(
    apex_offset: float, left_shoulder: Point2D, right_shoulder: Point2D
) -> float:
    """この山の内向き膨らみの深さ。"""
    return min(
        apex_offset * _INWARD_OFFSET_RATIO,
        _distance(left_shoulder, right_shoulder) * _INWARD_WEDGE_RATIO,
    )


def _incoming_curve(
    ring: Sequence[Point2D], apex_index: int, depth: float
) -> list[Point2D]:
    """肩→先端の3次ベジェ。肩で側面に接線連続、先端位置は不変。

    ミター脚 (肩→先端の直線) を、楔の内側へ depth だけ膨らむ弧に置換する。
    側面の曲率の続きとして内向きに曲がり、先端に向かって線幅が自然に細く
    なる。制御点1は肩の接線上 (接線連続)、制御点2は脚から内向きにずらす。
    """
    count = len(ring)
    previous = ring[(apex_index - 2) % count]
    shoulder = ring[(apex_index - 1) % count]
    apex = ring[apex_index]
    other_shoulder = ring[(apex_index + 1) % count]
    leg = _distance(shoulder, apex)
    tangent = _direction(previous, shoulder)
    leg_direction = _direction(shoulder, apex)
    if tangent is None or leg_direction is None or leg <= 1.0e-15:
        return [apex]
    inward = _inward_normal(leg_direction, apex, other_shoulder)
    control_offset = depth / _CUBIC_DEPTH_GAIN
    control1_len = _control1_length(tangent, leg_direction, leg, depth)
    control1 = (
        shoulder[0] + tangent[0] * control1_len,
        shoulder[1] + tangent[1] * control1_len,
    )
    control2 = (
        apex[0] - leg_direction[0] * leg * _TIP_TANGENT_RATIO + inward[0] * control_offset,
        apex[1] - leg_direction[1] * leg * _TIP_TANGENT_RATIO + inward[1] * control_offset,
    )
    return [
        _cubic_point(shoulder, control1, control2, apex, _curve_parameter(step))
        for step in range(1, _CURVE_STEPS + 1)
    ]


def _outgoing_curve(
    ring: Sequence[Point2D], apex_index: int, depth: float
) -> list[Point2D]:
    """先端→肩の3次ベジェ (_incoming_curve の鏡映)。"""
    count = len(ring)
    apex = ring[apex_index]
    shoulder = ring[(apex_index + 1) % count]
    following = ring[(apex_index + 2) % count]
    other_shoulder = ring[(apex_index - 1) % count]
    leg = _distance(apex, shoulder)
    tangent = _direction(following, shoulder)
    leg_direction = _direction(apex, shoulder)
    if tangent is None or leg_direction is None or leg <= 1.0e-15:
        return [shoulder]
    # 内向き = 楔の反対側の肩 (進行方向の逆側) を向く法線
    inward = _inward_normal(leg_direction, apex, other_shoulder)
    control_offset = depth / _CUBIC_DEPTH_GAIN
    control2_len = _control1_length(tangent, leg_direction, leg, depth)
    control1 = (
        apex[0] + leg_direction[0] * leg * _TIP_TANGENT_RATIO + inward[0] * control_offset,
        apex[1] + leg_direction[1] * leg * _TIP_TANGENT_RATIO + inward[1] * control_offset,
    )
    control2 = (
        shoulder[0] + tangent[0] * control2_len,
        shoulder[1] + tangent[1] * control2_len,
    )
    return [
        _cubic_point(apex, control1, control2, shoulder, _curve_parameter(step))
        for step in range(1, _CURVE_STEPS + 1)
    ]


def _curve_ring_apexes(
    ring: Sequence[Point2D], apexes: set[int], depths: dict[int, float]
) -> list[Point2D]:
    curved = [ring[0]]
    for index in range(len(ring)):
        following_index = (index + 1) % len(ring)
        if following_index in apexes:
            curved.extend(_incoming_curve(
                ring, following_index, depths.get(following_index, 0.0)
            ))
        elif index in apexes:
            curved.extend(_outgoing_curve(
                ring, index, depths.get(index, 0.0)
            ))
        else:
            curved.append(ring[following_index])
    if len(curved) >= 2 and _distance(curved[0], curved[-1]) <= 1.0e-15:
        curved.pop()
    return curved


def _valid_ring(points: Sequence[Point2D]) -> bool:
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import LinearRing  # type: ignore

        ring = LinearRing(points)
        return bool(ring.is_valid and ring.is_simple and not ring.is_empty)
    except Exception:  # noqa: BLE001
        return False


def _valid_polygon(outer: Sequence[Point2D], holes: Sequence[Sequence[Point2D]]) -> bool:
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import Polygon  # type: ignore

        polygon = Polygon(outer, holes)
        return bool(polygon.is_valid and not polygon.is_empty and polygon.area > 1.0e-15)
    except Exception:  # noqa: BLE001
        return False


def _prepare_ring(
    points: Sequence[Point2D], reference: Sequence[Point2D], center: Point2D
) -> _PreparedRing | None:
    ring, was_closed = _open_ring(points)
    if len(ring) < 7:
        return None
    offsets = _matched_apex_offsets(ring, reference, center)
    apexes = set(offsets)
    # sanitize後の極端な可変幅帯では、二つのmitre先端が隣接したり同じ肩を
    # 共有したりすることがある。この状態で両方を独立した曲線へ置換すると、
    # 一方の先端を他方の肩として二重利用してUターンが生じる。前後2点以内に
    # 別先端があるclusterだけを従来形状のまま残し、独立した先端は曲線化する。
    blocked = {
        index
        for index in apexes
        if any(
            (index + delta) % len(ring) in apexes
            for delta in (-2, -1, 1, 2)
        )
    }
    apexes -= blocked
    if not apexes:
        return None
    return _PreparedRing(
        ring, was_closed, apexes,
        {index: offsets[index] for index in apexes},
    )


def _curve_single_ring(
    points: list[Point2D],
    reference: Sequence[Point2D],
    center: Point2D,
    gap_cap: float,
) -> list[Point2D]:
    """1本の輪郭 (帯の外周または穴) のミター楔を曲線化する。失敗時は原型を返す。

    山頂ごとの内向き深さは、その山のオフセット距離と楔の肩距離で決まる
    (輪郭に内在的な尺度なので、同じ物理輪郭が別の帯として処理されても同じ
    曲線になる)。gap_cap は極端に狭い線間隔の設定でも隣の線と融合しない
    ための保険の上限 (通常設定では効かない)。
    """
    prepared = _prepare_ring(points, reference, center)
    if prepared is None:
        return list(points)
    count = len(prepared.points)
    depths = {
        index: min(
            _apex_depth(
                offset,
                prepared.points[(index - 1) % count],
                prepared.points[(index + 1) % count],
            ),
            gap_cap,
        )
        for index, offset in prepared.offsets.items()
    }
    curved = _curve_ring_apexes(prepared.points, prepared.apexes, depths)
    if not _valid_ring(curved):
        return list(points)
    if prepared.was_closed:
        curved.append(curved[0])
    return curved


def _polygon_gap_caps(originals: Sequence[BandPolygon]) -> list[float]:
    """各帯の内向き深さの保険上限 (_BULGE_GAP_RATIO × 隣接する別の帯までの
    最短距離)。隣の帯が無い呼び出し (主線単体・フチ) では制限しない。"""
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import LineString  # type: ignore
    except Exception:  # noqa: BLE001
        return [float("inf")] * len(originals)
    boundary_lines: list[list] = []
    for outer, holes in originals:
        lines = []
        try:
            if len(outer) >= 3:
                lines.append(LineString(list(outer) + [list(outer)[0]]))
            for hole in holes:
                if len(hole) >= 3:
                    lines.append(LineString(list(hole) + [list(hole)[0]]))
        except Exception:  # noqa: BLE001
            pass
        boundary_lines.append(lines)
    caps: list[float] = []
    for index, lines in enumerate(boundary_lines):
        distances: list[float] = []
        for other_index, other_lines in enumerate(boundary_lines):
            if other_index == index:
                continue
            for line in lines:
                for other in other_lines:
                    try:
                        distances.append(line.distance(other))
                    except Exception:  # noqa: BLE001
                        continue
        positive = [d for d in distances if d > 1.0e-12]
        caps.append(min(positive) * _BULGE_GAP_RATIO if positive else float("inf"))
    return caps


def curve_thorn_peak_band_polygons(
    polygons: Sequence[tuple[Sequence[Point2D], Sequence[Sequence[Point2D]]]],
    reference_outline: Sequence[Point2D],
    *,
    curve_holes: bool = False,
) -> list[BandPolygon]:
    """各線帯のミター先端の「直線くさび」を、先端位置を保った曲線に置換する。

    外周・穴 (内側輪郭) の両方を同一の固定式で曲線化する。全ての線・全ての
    山で同じ曲率になるため、多重線は平行・等間隔のまま先端だけが曲線化される。
    `curve_holes` は旧API互換のため残している (現行は常に両方を曲線化)。
    """
    del curve_holes  # 旧API互換 (常に外周と穴の両方を曲線化する)
    reference, _closed = _open_ring(reference_outline)
    originals = [
        ([(float(x), float(y)) for x, y in outer],
         [[(float(x), float(y)) for x, y in hole] for hole in holes])
        for outer, holes in polygons
    ]
    if len(reference) < 7:
        return originals
    center = _polygon_center(reference)
    gap_caps = _polygon_gap_caps(originals)
    result: list[BandPolygon] = []
    for (original_outer, original_holes), gap_cap in zip(originals, gap_caps):
        curved_outer = _curve_single_ring(original_outer, reference, center, gap_cap)
        curved_holes = [
            _curve_single_ring(hole, reference, center, gap_cap)
            for hole in original_holes
        ]
        # 帯として成立しない組み合わせになった場合は、成立する側だけ採用する
        if _valid_polygon(curved_outer, curved_holes):
            result.append((curved_outer, curved_holes))
        elif _valid_polygon(curved_outer, original_holes):
            result.append((curved_outer, original_holes))
        elif _valid_polygon(original_outer, curved_holes):
            result.append((original_outer, curved_holes))
        else:
            result.append((original_outer, original_holes))
    return result
