"""線種「図形」用の純幾何ヘルパー (単位非依存).

閉じた輪郭線に沿って ●・★ などの図形を等間隔に並べた多角形列を作る。
ビューポートの Mesh 焼き込みと画像出力 (PIL) の両方から使う。
"""

from __future__ import annotations

import math
import random
from typing import Sequence

_CIRCLE_SEGMENTS = 16


def _unit_circle() -> list[tuple[float, float]]:
    return [
        (0.5 * math.cos(i / _CIRCLE_SEGMENTS * math.tau), 0.5 * math.sin(i / _CIRCLE_SEGMENTS * math.tau))
        for i in range(_CIRCLE_SEGMENTS)
    ]


def _unit_star() -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for i in range(10):
        radius = 0.5 if i % 2 == 0 else 0.2
        angle = math.pi / 2.0 + i * math.pi / 5.0
        pts.append((radius * math.cos(angle), radius * math.sin(angle)))
    return pts


def _unit_triangle() -> list[tuple[float, float]]:
    return [
        (0.0, 0.5),
        (-0.433, -0.25),
        (0.433, -0.25),
    ]


def _unit_diamond() -> list[tuple[float, float]]:
    return [(0.0, 0.5), (-0.35, 0.0), (0.0, -0.5), (0.35, 0.0)]


def _unit_heart() -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for i in range(20):
        t = i / 20.0 * math.tau
        x = 16.0 * math.sin(t) ** 3
        y = 13.0 * math.cos(t) - 5.0 * math.cos(2 * t) - 2.0 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((x / 34.0, y / 34.0))
    return pts


_UNIT_SHAPES = {
    "circle": _unit_circle(),
    "star": _unit_star(),
    "triangle": _unit_triangle(),
    "diamond": _unit_diamond(),
    "heart": _unit_heart(),
}


def unit_shape_points(kind: str) -> list[tuple[float, float]]:
    return list(_UNIT_SHAPES.get(str(kind or "circle"), _UNIT_SHAPES["circle"]))


def _closed_loop(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    loop = [(float(x), float(y)) for x, y in points]
    if len(loop) >= 2 and (abs(loop[0][0] - loop[-1][0]) > 1.0e-9 or abs(loop[0][1] - loop[-1][1]) > 1.0e-9):
        loop.append(loop[0])
    return loop


def _point_at(loop, cum, dist: float) -> tuple[float, float, float]:
    """閉ループ上の距離 dist の位置と接線角を返す."""
    total = cum[-1]
    dist = dist % total if total > 0.0 else 0.0
    for i in range(1, len(loop)):
        if cum[i] >= dist:
            seg = cum[i] - cum[i - 1]
            t = (dist - cum[i - 1]) / seg if seg > 1.0e-9 else 0.0
            x0, y0 = loop[i - 1]
            x1, y1 = loop[i]
            return (
                x0 + (x1 - x0) * t,
                y0 + (y1 - y0) * t,
                math.atan2(y1 - y0, x1 - x0),
            )
    x, y = loop[-1]
    return (x, y, 0.0)


def decorations_along_loop(
    points: Sequence[tuple[float, float]],
    *,
    kind: str,
    size: float,
    spacing: float,
    angle_rad: float = 0.0,
    jitter: float = 0.0,
    seed: int = 0,
    flip_y: bool = False,
    orient: str = "line",
    center: tuple[float, float] | None = None,
) -> list[list[tuple[float, float]]]:
    """閉じた輪郭に沿って図形を並べ、多角形 (点列) のリストを返す.

    - size: 図形の大きさ (線幅と同じ単位)
    - spacing: 図形どうしの間隔 (中心間距離 = size + spacing)
    - angle_rad: 基準の向きに対する図形の追加回転
    - jitter: 0-1。位置・角度・大きさのばらつき
    - flip_y: ピクセル座標系 (Y 下向き) で図形の上下が反転しないようにする
    - orient: "line" = 線の進行方向に沿わせる / "center" = 常に center の方向を向く
    - center: orient="center" のときの基準点 (フキダシの中心点)
    """
    loop = _closed_loop(points)
    if len(loop) < 3 or size <= 1.0e-9:
        return []
    cum = [0.0]
    for p0, p1 in zip(loop, loop[1:]):
        cum.append(cum[-1] + math.hypot(p1[0] - p0[0], p1[1] - p0[1]))
    total = cum[-1]
    if total <= 1.0e-9:
        return []
    step = max(size * 0.25, size + max(0.0, spacing))
    count = max(1, int(total / step))
    # 一周でちょうど割り切れる間隔に丸め、始点と終点で重ならないようにする
    step = total / count
    rng = random.Random(int(seed))
    jitter = max(0.0, min(1.0, float(jitter)))
    base_shape = unit_shape_points(kind)
    if flip_y:
        base_shape = [(x, -y) for x, y in base_shape]
        angle_rad = -angle_rad
    polygons: list[list[tuple[float, float]]] = []
    for i in range(count):
        dist = i * step
        if jitter > 0.0:
            dist += (rng.random() - 0.5) * step * jitter
        cx, cy, tangent = _point_at(loop, cum, dist)
        if jitter > 0.0:
            normal = tangent + math.pi / 2.0
            offset = (rng.random() - 0.5) * size * jitter
            cx += math.cos(normal) * offset
            cy += math.sin(normal) * offset
        scale = size * (1.0 + (rng.random() - 0.5) * jitter if jitter > 0.0 else 1.0)
        if orient == "center" and center is not None:
            # 図形の上方向 (+Y) が常に center を向くような回転角。
            # flip_y (ピクセル座標, Y 下向き) では回転の見た目の向きが反転する
            # ため補正の符号も反転する。
            to_center = math.atan2(float(center[1]) - cy, float(center[0]) - cx)
            base_rotation = to_center + (math.pi * 0.5 if flip_y else -math.pi * 0.5)
        else:
            base_rotation = tangent
        rotation = base_rotation + angle_rad + ((rng.random() - 0.5) * math.pi * jitter if jitter > 0.0 else 0.0)
        cos_r = math.cos(rotation)
        sin_r = math.sin(rotation)
        polygons.append([
            (cx + (x * cos_r - y * sin_r) * scale, cy + (x * sin_r + y * cos_r) * scale)
            for x, y in base_shape
        ])
    return polygons


def resample_loop(points: Sequence[tuple[float, float]], segment_length: float) -> list[tuple[float, float]]:
    """閉じた輪郭を、約 segment_length 間隔の点列に再サンプリングする."""
    loop = _closed_loop(points)
    if len(loop) < 3 or segment_length <= 1.0e-9:
        return [(x, y) for x, y in points]
    cum = [0.0]
    for p0, p1 in zip(loop, loop[1:]):
        cum.append(cum[-1] + math.hypot(p1[0] - p0[0], p1[1] - p0[1]))
    total = cum[-1]
    if total <= 1.0e-9:
        return [(x, y) for x, y in points]
    count = max(8, int(round(total / segment_length)))
    return [(_point_at(loop, cum, total * i / count))[:2] for i in range(count)]
