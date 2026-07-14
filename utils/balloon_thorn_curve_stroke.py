"""トゲ（曲線）の鋭い線端を、先端位置を保った曲線へ整える。"""

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
_SHOULDER_TURN_LIMIT_RAD = math.radians(1.0)
_CURVE_STEPS = 48
_TIP_PULL_LEVELS = (
    0.52, 0.42, 0.32, 0.24, 0.16, 0.10, 0.06, 0.03, 0.015, 0.008, 0.004, 0.002,
)
_SHOULDER_HANDLE_LEVELS = (
    0.50, 0.42, 0.34, 0.27, 0.20, 0.14, 0.09, 0.05, 0.03, 0.01,
    0.007, 0.004, 0.002,
)


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
    peaks = _reference_peaks(reference, center)
    candidates = _mitre_candidates(ring, center)
    if not peaks or not candidates:
        return set()
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
        return set()
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
    matches: list[int] = []
    for _offset, _angle, _tolerance, peak_index, candidate_index in filtered:
        if peak_index in matched_peaks or candidate_index in matched_candidates:
            continue
        matched_peaks.add(peak_index)
        matched_candidates.add(candidate_index)
        matches.append(candidate_index)
    return {candidates[candidate_index].index for candidate_index in matches}


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


def _lerp(a: Point2D, b: Point2D, factor: float) -> Point2D:
    return (a[0] + (b[0] - a[0]) * factor, a[1] + (b[1] - a[1]) * factor)


def _tip_control(ring: Sequence[Point2D], apex_index: int, pull: float) -> Point2D:
    count = len(ring)
    left = ring[(apex_index - 1) % count]
    apex = ring[apex_index]
    right = ring[(apex_index + 1) % count]
    base_midpoint = ((left[0] + right[0]) * 0.5, (left[1] + right[1]) * 0.5)
    return _lerp(apex, base_midpoint, pull)


def _shoulder_control(
    shoulder: Point2D,
    neighboring: Point2D,
    apex: Point2D,
    handle_ratio: float,
    *,
    incoming: bool,
) -> Point2D:
    tangent = _direction(neighboring, shoulder) if incoming else _direction(shoulder, neighboring)
    if tangent is None:
        return _lerp(shoulder, apex, 1.0 / 3.0)
    handle = _distance(shoulder, apex) * handle_ratio
    direction = 1.0 if incoming else -1.0
    return (
        shoulder[0] + tangent[0] * handle * direction,
        shoulder[1] + tangent[1] * handle * direction,
    )


def _curve_parameter(step: int) -> float:
    """肩と先端へ点を寄せ、少ない頂点でも接線の折れを見せない。"""
    unit = step / _CURVE_STEPS
    incoming = unit * unit * unit
    outgoing = (1.0 - unit) ** 3
    return incoming / (incoming + outgoing)


def _incoming_curve(
    ring: Sequence[Point2D], apex_index: int, pull: float, handle_ratio: float
) -> list[Point2D]:
    count = len(ring)
    previous = ring[(apex_index - 2) % count]
    shoulder = ring[(apex_index - 1) % count]
    apex = ring[apex_index]
    control1 = _shoulder_control(
        shoulder, previous, apex, handle_ratio, incoming=True
    )
    control2 = _tip_control(ring, apex_index, pull)
    return [
        _cubic_point(shoulder, control1, control2, apex, _curve_parameter(step))
        for step in range(1, _CURVE_STEPS + 1)
    ]


def _outgoing_curve(
    ring: Sequence[Point2D], apex_index: int, pull: float, handle_ratio: float
) -> list[Point2D]:
    count = len(ring)
    apex = ring[apex_index]
    shoulder = ring[(apex_index + 1) % count]
    following = ring[(apex_index + 2) % count]
    control1 = _tip_control(ring, apex_index, pull)
    control2 = _shoulder_control(
        shoulder, following, apex, handle_ratio, incoming=False
    )
    return [
        _cubic_point(apex, control1, control2, shoulder, _curve_parameter(step))
        for step in range(1, _CURVE_STEPS + 1)
    ]


def _curve_ring_with_pull(
    ring: Sequence[Point2D], apexes: set[int], pull: float, handle_ratio: float
) -> list[Point2D]:
    curved = [ring[0]]
    for index in range(len(ring)):
        following_index = (index + 1) % len(ring)
        if following_index in apexes:
            curved.extend(_incoming_curve(ring, following_index, pull, handle_ratio))
        elif index in apexes:
            curved.extend(_outgoing_curve(ring, index, pull, handle_ratio))
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


def _parameters_keep_shoulders_smooth(
    ring: Sequence[Point2D],
    apexes: set[int],
    pull: float,
    handle_ratio: float,
) -> bool:
    """離散化後の肩接続角を、端点隣接サンプルだけで確認する。"""
    first_t = _curve_parameter(1)
    last_t = _curve_parameter(_CURVE_STEPS - 1)
    count = len(ring)
    for apex_index in apexes:
        previous = ring[(apex_index - 2) % count]
        left = ring[(apex_index - 1) % count]
        apex = ring[apex_index]
        right = ring[(apex_index + 1) % count]
        following = ring[(apex_index + 2) % count]
        tip_control = _tip_control(ring, apex_index, pull)
        left_sample = _cubic_point(
            left,
            _shoulder_control(left, previous, apex, handle_ratio, incoming=True),
            tip_control,
            apex,
            first_t,
        )
        right_sample = _cubic_point(
            apex,
            tip_control,
            _shoulder_control(right, following, apex, handle_ratio, incoming=False),
            right,
            last_t,
        )
        if _turn_radians(previous, left, left_sample) > _SHOULDER_TURN_LIMIT_RAD:
            return False
        if _turn_radians(right_sample, right, following) > _SHOULDER_TURN_LIMIT_RAD:
            return False
    return True


def _prepare_ring(
    points: Sequence[Point2D], reference: Sequence[Point2D], center: Point2D
) -> _PreparedRing | None:
    ring, was_closed = _open_ring(points)
    if len(ring) < 7:
        return None
    apexes = _matched_apex_indices(ring, reference, center)
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
    return _PreparedRing(ring, was_closed, apexes)


def _curve_prepared_ring(
    prepared: _PreparedRing,
    pull: float,
    handle_ratio: float,
    *,
    validate_ring: bool,
) -> list[Point2D]:
    if not _parameters_keep_shoulders_smooth(
        prepared.points, prepared.apexes, pull, handle_ratio
    ):
        return list(prepared.points)
    curved = _curve_ring_with_pull(
        prepared.points, prepared.apexes, pull, handle_ratio
    )
    if validate_ring and not _valid_ring(curved):
        return list(prepared.points)
    if prepared.was_closed:
        curved.append(curved[0])
    return curved


def _curve_ring_at_parameters(
    points: Sequence[Point2D],
    reference: Sequence[Point2D],
    center: Point2D,
    pull: float,
    handle_ratio: float,
) -> list[Point2D]:
    prepared = _prepare_ring(points, reference, center)
    if prepared is None:
        return list(points)
    return _curve_prepared_ring(
        prepared, pull, handle_ratio, validate_ring=True
    )


def _valid_polygon(outer: Sequence[Point2D], holes: Sequence[Sequence[Point2D]]) -> bool:
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import Polygon  # type: ignore

        polygon = Polygon(outer, holes)
        return bool(polygon.is_valid and not polygon.is_empty and polygon.area > 1.0e-15)
    except Exception:  # noqa: BLE001
        return False


def _expanded_outer_limit(original: Sequence[Point2D]):
    """許容する外側張り出し領域を一度だけ生成する。"""
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import Polygon  # type: ignore

        xs = [point[0] for point in original]
        ys = [point[1] for point in original]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0e-12)
        allowance = span * 3.5e-4
        return Polygon(original).buffer(allowance)
    except Exception:  # noqa: BLE001
        return None


def _candidate_fits_band(
    candidate: Sequence[Point2D],
    holes: Sequence[Sequence[Point2D]],
    expanded_limit,
) -> bool:
    """候補が張り出し上限内かつ有効な線帯かを同時に確認する。"""
    if expanded_limit is None:
        return False
    python_deps.ensure_bundled_wheels_on_path()
    try:
        from shapely.geometry import Polygon  # type: ignore

        outer = Polygon(candidate)
        band = Polygon(candidate, holes)
        return bool(
            expanded_limit.covers(outer)
            and band.is_valid
            and not band.is_empty
            and band.area > 1.0e-15
        )
    except Exception:  # noqa: BLE001
        return False


def _expansion_within_limit(
    original: Sequence[Point2D], curved: Sequence[Point2D]
) -> bool:
    """診断用: 曲線が外側張り出し上限内かを返す。"""
    expanded_limit = _expanded_outer_limit(original)
    if expanded_limit is None:
        return False
    try:
        from shapely.geometry import Polygon  # type: ignore

        return bool(expanded_limit.covers(Polygon(curved)))
    except Exception:  # noqa: BLE001
        return False


def _curve_outer_preserving_holes(
    outer: list[Point2D],
    holes: list[list[Point2D]],
    reference: Sequence[Point2D],
    center: Point2D,
) -> BandPolygon:
    """内周を動かさず、帯として有効な範囲で外周だけを曲線化する。"""
    prepared = _prepare_ring(outer, reference, center)
    if prepared is None:
        return outer, holes
    expanded_limit = _expanded_outer_limit(outer)
    for pull in _TIP_PULL_LEVELS:
        for handle_ratio in _SHOULDER_HANDLE_LEVELS:
            candidate = _curve_prepared_ring(
                prepared, pull, handle_ratio, validate_ring=False
            )
            if (
                candidate != outer
                and _candidate_fits_band(candidate, holes, expanded_limit)
            ):
                return candidate, holes
    return outer, holes


def _curve_outer_and_holes(
    outer: list[Point2D],
    holes: list[list[Point2D]],
    reference: Sequence[Point2D],
    center: Point2D,
) -> BandPolygon:
    """外側フチ用に、外周と主線に接する内周を同じ規則で曲線化する。"""
    reference_hole = list(reference)
    curved_holes = [
        _curve_outer_preserving_holes(
            hole, [reference_hole], reference, center
        )[0]
        for hole in holes
    ]
    prepared = _prepare_ring(outer, reference, center)
    if prepared is None:
        return outer, (
            curved_holes if _valid_polygon(outer, curved_holes) else holes
        )
    expanded_limit = _expanded_outer_limit(outer)
    for pull in _TIP_PULL_LEVELS:
        for handle_ratio in _SHOULDER_HANDLE_LEVELS:
            candidate_outer = _curve_prepared_ring(
                prepared, pull, handle_ratio, validate_ring=False
            )
            if (
                candidate_outer != outer
                and _candidate_fits_band(
                    candidate_outer, curved_holes, expanded_limit
                )
            ):
                return candidate_outer, curved_holes
    return outer, (curved_holes if _valid_polygon(outer, curved_holes) else holes)


def curve_thorn_peak_band_polygons(
    polygons: Sequence[tuple[Sequence[Point2D], Sequence[Sequence[Point2D]]]],
    reference_outline: Sequence[Point2D],
    *,
    curve_holes: bool = False,
) -> list[BandPolygon]:
    """mitre先端を動かさず、線帯の外側輪郭を曲線へ置換する。

    `curve_holes` は外側フチの内周を主線外周へ一致させる場合だけ使う。
    主線・多重線では穴を維持し、曲線化による線間隔の縮小を防ぐ。
    """
    reference, _closed = _open_ring(reference_outline)
    originals = [
        ([(float(x), float(y)) for x, y in outer],
         [[(float(x), float(y)) for x, y in hole] for hole in holes])
        for outer, holes in polygons
    ]
    if len(reference) < 7:
        return originals
    center = _polygon_center(reference)
    result: list[BandPolygon] = []
    for original_outer, original_holes in originals:
        curve = _curve_outer_and_holes if curve_holes else _curve_outer_preserving_holes
        result.append(curve(original_outer, original_holes, reference, center))
    return result
