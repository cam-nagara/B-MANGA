"""フキダシのラスタ書き出しヘルパ."""

from __future__ import annotations

import math
from typing import Sequence

from ..utils import balloon_shapes, balloon_tail_geom, free_transform, line_pattern, percentage
from ..utils.geom import Rect, mm_to_px


def _ep():
    from . import export_pipeline

    return export_pipeline


def _outline_rect(rect: Rect) -> list[tuple[float, float]]:
    return [(rect.x, rect.y), (rect.x2, rect.y), (rect.x2, rect.y2), (rect.x, rect.y2)]


def _outline_rounded_rect(rect: Rect, radius_mm: float, segments: int = 8) -> list[tuple[float, float]]:
    radius = max(0.0, min(float(radius_mm), rect.width * 0.5, rect.height * 0.5))
    if radius <= 0.0:
        return _outline_rect(rect)
    corners = (
        (rect.x2 - radius, rect.y2 - radius, 0.0),
        (rect.x + radius, rect.y2 - radius, math.pi * 0.5),
        (rect.x + radius, rect.y + radius, math.pi),
        (rect.x2 - radius, rect.y + radius, math.pi * 1.5),
    )
    pts: list[tuple[float, float]] = []
    for cx, cy, start in corners:
        for step in range(segments + 1):
            angle = start + (math.pi * 0.5) * (step / segments)
            pts.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return pts


def _outline_ellipse(rect: Rect, segments: int = 64) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = rect.width * 0.5
    ry = rect.height * 0.5
    return [
        (cx + rx * math.cos(2 * math.pi * i / segments),
         cy + ry * math.sin(2 * math.pi * i / segments))
        for i in range(segments)
    ]


def _outline_cloud(rect: Rect, wave_count: int, amplitude_mm: float,
                   segments_per_wave: int = 6) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = max(1.0, rect.width * 0.5 - amplitude_mm)
    ry = max(1.0, rect.height * 0.5 - amplitude_mm)
    total = max(8, int(wave_count) * max(1, int(segments_per_wave)))
    pts: list[tuple[float, float]] = []
    for i in range(total):
        angle = 2 * math.pi * i / total
        bump = amplitude_mm * (0.5 + 0.5 * math.cos(wave_count * angle))
        radius_factor = 1.0 + bump / max(1.0, min(rx, ry))
        pts.append((cx + rx * math.cos(angle) * radius_factor, cy + ry * math.sin(angle) * radius_factor))
    return pts


def _outline_spike(rect: Rect, spike_count: int, depth_mm: float, *, smooth: bool) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    rx = max(1.0, rect.width * 0.5)
    ry = max(1.0, rect.height * 0.5)
    total = max(6, int(spike_count) * 2)
    pts: list[tuple[float, float]] = []
    for i in range(total):
        angle = 2 * math.pi * i / total
        factor = 1.0 if i % 2 == 0 else max(0.05, 1.0 - depth_mm / max(rx, ry))
        pts.append((cx + rx * math.cos(angle) * factor, cy + ry * math.sin(angle) * factor))
    if smooth and len(pts) >= 3:
        smoothed = []
        for i in range(len(pts)):
            prev_pt = pts[(i - 1) % len(pts)]
            cur_pt = pts[i]
            next_pt = pts[(i + 1) % len(pts)]
            smoothed.append(((prev_pt[0] + 2 * cur_pt[0] + next_pt[0]) * 0.25,
                             (prev_pt[1] + 2 * cur_pt[1] + next_pt[1]) * 0.25))
        pts = smoothed
    return pts


def _outline_polygon_pct(rect: Rect, pct_pts: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    return [
        (rect.x + rect.width * (px / 100.0),
         rect.y + rect.height * ((100.0 - py) / 100.0))
        for px, py in pct_pts
    ]


def _outline_pill(rect: Rect, segments: int = 16) -> list[tuple[float, float]]:
    radius = min(rect.width, rect.height) * 0.5
    if radius <= 0.0:
        return _outline_rect(rect)
    cx_left = rect.x + radius
    cx_right = rect.x2 - radius
    cy = (rect.y + rect.y2) * 0.5
    pts: list[tuple[float, float]] = []
    for step in range(segments + 1):
        angle = -math.pi * 0.5 + math.pi * (step / segments)
        pts.append((cx_right + radius * math.cos(angle), cy + radius * math.sin(angle)))
    for step in range(segments + 1):
        angle = math.pi * 0.5 + math.pi * (step / segments)
        pts.append((cx_left + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return pts


def _outline_diamond(rect: Rect) -> list[tuple[float, float]]:
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    return [(cx, rect.y2), (rect.x2, cy), (cx, rect.y), (rect.x, cy)]


def _outline_hexagon(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(rect, [(25, 0), (75, 0), (100, 50), (75, 100), (25, 100), (0, 50)])


def _outline_octagon(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(rect, [(12, 0), (88, 0), (100, 12), (100, 88), (88, 100), (12, 100), (0, 88), (0, 12)])


def _outline_star(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(
        rect,
        [(50, 0), (61, 35), (98, 35), (68, 57), (79, 91),
         (50, 70), (21, 91), (32, 57), (2, 35), (39, 35)],
    )


def _outline_fluffy(rect: Rect) -> list[tuple[float, float]]:
    return _outline_polygon_pct(
        rect,
        [(50, 3), (70, 8), (88, 16), (96, 30), (92, 50), (96, 70),
         (88, 84), (70, 92), (50, 97), (30, 92), (12, 84), (4, 70),
         (8, 50), (4, 30), (12, 16), (30, 8)],
    )


def _balloon_outline_mm(entry, rect: Rect) -> list[tuple[float, float]]:
    return balloon_shapes.outline_for_entry(entry, rect)


def _balloon_fill_outline_mm(entry, rect: Rect) -> list[tuple[float, float]]:
    return _balloon_outline_mm(entry, rect)


def _apply_balloon_transforms(
    pts: Sequence[tuple[float, float]],
    rect: Rect,
    flip_h: bool,
    flip_v: bool,
    rotation_deg: float,
) -> list[tuple[float, float]]:
    if not (flip_h or flip_v or abs(rotation_deg) > 1e-6):
        return list(pts)
    cx = (rect.x + rect.x2) * 0.5
    cy = (rect.y + rect.y2) * 0.5
    sx = -1.0 if flip_h else 1.0
    sy = -1.0 if flip_v else 1.0
    cos_r = math.cos(math.radians(rotation_deg))
    sin_r = math.sin(math.radians(rotation_deg))
    out = []
    for x, y in pts:
        dx = (x - cx) * sx
        dy = (y - cy) * sy
        rx = dx * cos_r - dy * sin_r
        ry = dx * sin_r + dy * cos_r
        out.append((cx + rx, cy + ry))
    return out


def _apply_entry_free_transform(
    entry,
    pts: Sequence[tuple[float, float]],
    rect: Rect,
) -> list[tuple[float, float]]:
    if not free_transform.entry_enabled(entry):
        return list(pts)
    out = []
    for x, y in pts:
        local_x = float(x) - rect.x
        local_y = float(y) - rect.y
        tx, ty = free_transform.transform_entry_local_point(entry, local_x, local_y)
        out.append((rect.x + tx, rect.y + ty))
    return out


def _entry_center_point_mm(
    entry,
    rect: Rect,
    flip_h: bool,
    flip_v: bool,
    rotation_deg: float,
) -> tuple[float, float]:
    local_x = rect.width * 0.5
    local_y = rect.height * 0.5
    if free_transform.entry_enabled(entry):
        local_x, local_y = free_transform.transform_entry_local_point(entry, local_x, local_y)
    center = (
        rect.x + local_x + float(getattr(entry, "center_offset_x_mm", 0.0) or 0.0),
        rect.y + local_y + float(getattr(entry, "center_offset_y_mm", 0.0) or 0.0),
    )
    return _apply_balloon_transforms([center], rect, flip_h, flip_v, rotation_deg)[0]


def _balloon_tail_polygon(rect: Rect, tail) -> list[tuple[float, float]]:
    return balloon_tail_geom.polygon_for_tail(rect, tail)


def _merged_outline_with_tails(
    outline: Sequence[tuple[float, float]],
    tail_outlines: Sequence[Sequence[tuple[float, float]]],
    union_only_outlines: Sequence[Sequence[tuple[float, float]]] = (),
) -> list[tuple[float, float]] | None:
    """本体としっぽの輪郭を、ビューポート描画と同じ結合方式で 1 つにする.

    外へ伸びるしっぽは本体へ結合し、内側へえぐるしっぽは本体から
    差し引く。連続楕円しっぽの「本体に重なる楕円」(union_only_outlines)
    は常に結合する。結合できない場合は None を返し、呼び出し側は従来の
    個別描画へフォールバックする。
    """
    tails = [list(pts) for pts in tail_outlines if len(pts) >= 3]
    union_only = [list(pts) for pts in union_only_outlines if len(pts) >= 3]
    if len(outline) < 3 or (not tails and not union_only):
        return None
    try:
        from ..utils import balloon_tail_boolean
    except Exception:  # noqa: BLE001
        return None
    # 結合判定のしきい値が m 基準のため mm -> m へ揃える
    scale = 0.001
    body_m = [(x * scale, y * scale) for x, y in outline]
    tails_m = [[(x * scale, y * scale) for x, y in pts] for pts in tails]
    union_only_m = [[(x * scale, y * scale) for x, y in pts] for pts in union_only]
    merged, changed = balloon_tail_boolean.combine_body_with_tail_polygons(
        body_m, tails_m, union_only_points_list=union_only_m
    )
    if merged is None or not changed:
        return None
    try:
        coords = list(merged.exterior.coords)
    except Exception:  # noqa: BLE001
        return None
    if len(coords) < 4:
        return None
    return [(float(x) / scale, float(y) / scale) for x, y in coords[:-1]]


def _split_ellipse_outlines_by_body(
    outline: Sequence[tuple[float, float]],
    ellipse_outlines: Sequence[Sequence[tuple[float, float]]],
) -> tuple[list[list[tuple[float, float]]], list[list[tuple[float, float]]]]:
    """楕円列を「本体に重なる (結合対象)」と「重ならない (個別描画)」に分ける."""
    polys = [list(pts) for pts in ellipse_outlines]
    if not polys or len(outline) < 3:
        return [], polys
    try:
        from ..utils import balloon_tail_boolean

        touching, separate = balloon_tail_boolean.split_indices_touching_body(list(outline), polys)
        return [polys[i] for i in touching], [polys[i] for i in separate]
    except Exception:  # noqa: BLE001
        return [], polys


def _densify_closed_outline_mm(outline, *, min_total: int = 120):
    """閉じたアウトラインを、頂点 (谷/山) を保ったまま各辺を線形分割して密度を上げる.

    トゲ直線のように ``outline_for_entry`` が谷/山頂点だけの疎なポリゴンを返す形状でも、
    ビューポートのメッシュ (spline を SAMPLES_PER_SEGMENT 分割した密サンプル) と同程度の
    密度にそろえ、動的多重線生成器の谷/山検出とリング再サンプルを安定させる。既に十分
    密なら (曲線形状など) そのまま返す。線形分割なので直線辺の形状は変わらない。
    """
    from ..utils.balloon_line_mesh import SAMPLES_PER_SEGMENT

    src = [(float(x), float(y)) for x, y in outline]
    if len(src) >= 3 and math.hypot(src[0][0] - src[-1][0], src[0][1] - src[-1][1]) <= 1.0e-9:
        src = src[:-1]
    n = len(src)
    if n < 3 or n >= min_total:
        return src
    out: list[tuple[float, float]] = []
    for i in range(n):
        a = src[i]
        b = src[(i + 1) % n]
        out.append(a)
        for k in range(1, SAMPLES_PER_SEGMENT):
            t = k / SAMPLES_PER_SEGMENT
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def _multi_ring_band_polygons(
    outline,
    entry,
    *,
    sharp: bool,
) -> list[list]:
    """多重線の各リングを、画面のメッシュと同じ帯のまとまりで返す.

    本体の線の外側 (または内側) に「隙間 = 間隔」で帯を順に並べる。
    幅スケール・間隔スケール・方向 (外側/内側/両方向) に対応する。

    動的形状 (雲/フワフワ/トゲ/トゲ曲線) で「長さ変化 (主線寄り/遠い側)」「谷/山の
    線幅」「山谷を延ばして交差」が効いているときは、ビューポートのメッシュと同一の
    生成器 (``balloon_line_mesh._build_dynamic_multi_line_polygons``) をメートル単位で
    呼び、画面と出力を一致させる。新方式J・非動的形状は従来のミター帯 (全長リング)。
    """
    line_w_mm = _scaled_width_mm(entry, "line_width_mm", 0.3)
    ring_w_base = _scaled_width_mm(entry, "multi_line_width_mm", 0.3)
    spacing_base = max(0.0, float(getattr(entry, "multi_line_spacing_mm", 0.4) or 0.0))
    count = max(1, min(12, int(getattr(entry, "multi_line_count", 3) or 3)))
    width_scale = max(0.0, float(getattr(entry, "multi_line_width_scale_percent", 100.0) or 0.0)) / 100.0
    spacing_scale = max(0.0, float(getattr(entry, "multi_line_spacing_scale_percent", 100.0) or 0.0)) / 100.0
    direction = str(getattr(entry, "multi_line_direction", "outside") or "outside")
    if direction == "both":
        sides = ("inside", "outside")
    elif direction == "inside":
        sides = ("inside",)
    else:
        sides = ("outside",)
    # 2026-07-23: 主線は中心アライメント (body ± line_w_mm/2) に統一されたため、
    # 主線の外側/内側エッジはどちらも body から line_w_mm/2 (絶対距離)。
    running_outside = line_w_mm * 0.5
    running_inside = line_w_mm * 0.5
    anchor_cfg = _anchor_cfg_for_export(entry)

    # --- 動的形状で 長さ変化 / 谷山線幅 / 交差 が効いているか (メッシュ側と同一条件) ---
    from ..utils import balloon_line_mesh as blm

    shape_norm = balloon_shapes.normalize_shape(str(getattr(entry, "shape", "rect") or "rect"))
    valley_pct = max(0.0, min(100.0, float(getattr(entry, "thorn_multi_line_valley_width_pct", 100.0) or 0.0)))
    peak_pct = max(0.0, min(100.0, float(getattr(entry, "thorn_multi_line_peak_width_pct", 100.0) or 0.0)))
    # 「長さ変化」は 主線寄り(near) / 遠い側(far)。旧 _percent は far のフォールバック。
    length_near_pct = float(getattr(entry, "thorn_multi_line_length_scale_near_percent", 100.0))
    length_far_pct = float(getattr(entry, "thorn_multi_line_length_scale_far_percent", 100.0))
    legacy_len_pct = float(getattr(entry, "thorn_multi_line_length_scale_percent", 100.0))
    if abs(length_far_pct - 100.0) < 1.0e-3 and abs(legacy_len_pct - 100.0) > 1.0e-3:
        length_far_pct = legacy_len_pct
    length_near = max(0.0, min(1.0, length_near_pct / 100.0))
    length_far = max(0.0, min(1.0, length_far_pct / 100.0))
    cross_enabled = bool(getattr(entry, "thorn_multi_line_cross_enabled", False))
    dynamic = (
        anchor_cfg is None
        and shape_norm in blm._DYNAMIC_WIDTH_SHAPES
        and (
            length_near < 0.999
            or length_far < 0.999
            or abs(valley_pct - 100.0) > 1.0e-3
            or abs(peak_pct - 100.0) > 1.0e-3
            or cross_enabled
        )
    )

    band_groups: list[list] = []

    if dynamic:
        dense_mm = _densify_closed_outline_mm(outline)
        if len(dense_mm) < 6:
            return []
        pts_m = [(x * 0.001, y * 0.001, 1.0) for (x, y) in dense_mm]
        center_m = (
            sum(p[0] for p in pts_m) / len(pts_m),
            sum(p[1] for p in pts_m) / len(pts_m),
        )
        # 動的形状で直線辺なのは「トゲ (直線)」のみ。密度補完後の線形サブセグメントで
        # _is_straight_edged が曲線形状 (雲/フワフワ/トゲ曲線) を直線と誤判定するのを避け、
        # 形状種別で判定する (メッシュ側の実効結果と一致)。
        ml_straight = (shape_norm == "thorn")
        peaks_rounded = shape_norm in blm._ROUNDED_PEAK_SHAPES
        valley_sharp = bool(sharp)  # = cloud_valley_sharp (_body_sharp_corners と同一)
        valley_w_base = ring_w_base * valley_pct / 100.0
        peak_w_base = ring_w_base * peak_pct / 100.0
        for ring_index in range(1, count + 1):
            ring_w = ring_w_base * (width_scale ** max(0, ring_index - 1))
            ring_spacing = spacing_base * (spacing_scale ** max(0, ring_index - 1))
            if ring_w <= 1.0e-6:
                continue
            ring_valley = valley_w_base * (width_scale ** max(0, ring_index - 1))
            ring_peak = peak_w_base * (width_scale ** max(0, ring_index - 1))
            ring_extent = max(ring_w, ring_valley, ring_peak)
            if count <= 1:
                ring_len = length_near
            else:
                t = float(ring_index - 1) / float(count - 1)
                ring_len = length_near + (length_far - length_near) * t
            ring_len = max(0.0, min(1.0, ring_len))
            for side in sides:
                if side == "inside":
                    ring_inner = running_inside + ring_spacing
                    ring_center = ring_inner + ring_extent * 0.5
                    signed_offset_mm = -ring_center
                else:
                    ring_inner = running_outside + ring_spacing
                    ring_center = ring_inner + ring_extent * 0.5
                    signed_offset_mm = ring_center
                if cross_enabled and ring_len < 0.999:
                    cross_ext_mm = ring_spacing + ring_w
                else:
                    cross_ext_mm = 0.0
                # 曲線 + 外向きリングは外側アライメント (内側オフセットの凸頂点くさび回避)。
                if (not ml_straight) and side == "outside":
                    outside_align = True
                    offset_mm = ring_inner
                else:
                    outside_align = False
                    offset_mm = signed_offset_mm
                polys_m = None
                raised = False
                try:
                    polys_m = blm._build_dynamic_multi_line_polygons(
                        body_samples=pts_m,
                        signed_offset_m=offset_mm * 0.001,
                        base_width_m=ring_w * 0.001,
                        valley_width_m=ring_valley * 0.001,
                        peak_width_m=ring_peak * 0.001,
                        length_scale=ring_len,
                        valley_sharp=valley_sharp,
                        balloon_center_m=center_m,
                        cross_extension_m=cross_ext_mm * 0.001,
                        peak_extension_m=0.0,
                        outside_align=outside_align,
                        peaks_rounded=peaks_rounded,
                    )
                except Exception:  # noqa: BLE001
                    raised = True
                if polys_m:
                    band_groups.append([
                        (
                            [(px * 1000.0, py * 1000.0) for (px, py) in outer],
                            [[(hx * 1000.0, hy * 1000.0) for (hx, hy) in hole] for hole in holes],
                        )
                        for (outer, holes) in polys_m
                    ])
                elif raised:
                    # 生成器が想定外に失敗しても書き出し全体を落とさない。従来のミター帯
                    # (全長リング) にフォールバック。None/[] (谷山幅0 等の意図的な空) は尊重する。
                    if side == "inside":
                        fb = _mitre_band_polygons_mm(outline, -ring_inner, -(ring_inner + ring_w), sharp=sharp)
                    else:
                        fb = _mitre_band_polygons_mm(outline, ring_inner + ring_w, ring_inner, sharp=sharp)
                    if fb:
                        band_groups.append(fb)
                if side == "inside":
                    running_inside += ring_spacing + ring_extent
                else:
                    running_outside += ring_spacing + ring_extent
        return band_groups

    # --- 非動的 / 新方式J: ミター帯 (全長リング) + J の長さ変化/交差ピース ---
    # 谷/山の線幅% は J でも有効: リング帯のエッジ倍率補正として乗せる
    # (balloon_line_mesh.ensure_balloon_multi_line_mesh と同一規則)。動的形状で
    # ないか pct が既定 100% のときは edge_scale_for_width_pct が恒等変換になる
    # ため、常時計算してよい (shape 分岐を複製する必要がない)。
    # 「長さ変化」「山谷を延ばして交差」も J で有効 (2026-07-23): リング別の
    # 長さ% が 100% 未満なら keep 区間ピース (_j_kept_ring_band_mm) で描く。
    ml_shape_dynamic = shape_norm in blm._DYNAMIC_WIDTH_SHAPES
    ml_eff_peak_pct = peak_pct if ml_shape_dynamic else 100.0
    ml_eff_valley_pct = valley_pct if ml_shape_dynamic else 100.0
    ml_both_zero = (
        anchor_cfg is not None
        and ml_shape_dynamic
        and valley_pct <= 1.0e-3
        and peak_pct <= 1.0e-3
    )
    j_len_active = (
        anchor_cfg is not None
        and ml_shape_dynamic
        and (length_near < 0.999 or length_far < 0.999)
    )
    j_detected = None
    if j_len_active:
        try:
            from ..utils import balloon_anchor_band

            dense_mm = _densify_closed_outline_mm(outline)
            if len(dense_mm) >= 6:
                j_detected = balloon_anchor_band.detect_anchors(dense_mm)
        except Exception:  # noqa: BLE001
            j_detected = None
    for ring_index in range(1, count + 1):
        ring_w = ring_w_base * (width_scale ** max(0, ring_index - 1))
        spacing = spacing_base * (spacing_scale ** max(0, ring_index - 1))
        if ring_w <= 1.0e-6:
            continue
        # 「長さ変化」near/far のリング別補間 (メッシュ側と同一)
        if count <= 1:
            ring_len = length_near
        else:
            t = float(ring_index - 1) / float(count - 1)
            ring_len = length_near + (length_far - length_near) * t
        ring_len = max(0.0, min(1.0, ring_len))
        for side in sides:
            band = None
            if side == "inside":
                inner = running_inside + spacing
                if not ml_both_zero:
                    center_mm = -(inner + ring_w * 0.5)
                    ring_anchor_cfg = anchor_cfg
                    ring_anchor_cfg_lo = None
                    if anchor_cfg is not None:
                        ring_anchor_cfg, ring_anchor_cfg_lo = _multi_ring_anchor_scales(
                            anchor_cfg, center_mm, ring_w, ml_eff_peak_pct, ml_eff_valley_pct,
                        )
                    j_band = None
                    if anchor_cfg is not None and j_len_active:
                        j_band = _j_kept_ring_band_mm(
                            j_detected, center_mm, ring_w, spacing, ring_len,
                            cross_enabled, ring_anchor_cfg, ring_anchor_cfg_lo,
                        )
                    if j_band is not None:
                        band = j_band
                    else:
                        band = _mitre_band_polygons_mm(
                            outline, -inner, -(inner + ring_w), sharp=sharp,
                            anchor_cfg=ring_anchor_cfg, anchor_cfg_lo=ring_anchor_cfg_lo,
                        )
                running_inside = inner + ring_w
            else:
                inner = running_outside + spacing
                if not ml_both_zero:
                    center_mm = inner + ring_w * 0.5
                    ring_anchor_cfg = anchor_cfg
                    ring_anchor_cfg_lo = None
                    if anchor_cfg is not None:
                        ring_anchor_cfg, ring_anchor_cfg_lo = _multi_ring_anchor_scales(
                            anchor_cfg, center_mm, ring_w, ml_eff_peak_pct, ml_eff_valley_pct,
                        )
                    j_band = None
                    if anchor_cfg is not None and j_len_active:
                        j_band = _j_kept_ring_band_mm(
                            j_detected, center_mm, ring_w, spacing, ring_len,
                            cross_enabled, ring_anchor_cfg, ring_anchor_cfg_lo,
                        )
                    if j_band is not None:
                        band = j_band
                    else:
                        band = _mitre_band_polygons_mm(
                            outline, inner + ring_w, inner, sharp=sharp,
                            anchor_cfg=ring_anchor_cfg, anchor_cfg_lo=ring_anchor_cfg_lo,
                        )
                running_outside = inner + ring_w
            if band:
                band_groups.append(band)
    return band_groups


def _patches_mask_px(canvas, patches):
    """穴つき多角形パッチ群の領域マスク (L, 255=塗る) を作る."""
    ep = _ep()
    mask = ep.Image.new("L", canvas.image.size, 0)
    mask_draw = ep.ImageDraw.Draw(mask)
    for outer, holes in patches:
        outer_px = canvas.points_px(outer)
        if len(outer_px) < 3:
            continue
        mask_draw.polygon(outer_px, fill=255)
        for hole in holes:
            hole_px = canvas.points_px(hole)
            if len(hole_px) >= 3:
                mask_draw.polygon(hole_px, fill=0)
    return mask


def _composite_patches_px(canvas, patches, color, clip_mask=None, fill_image=None) -> None:
    """穴つき多角形パッチを指定色で合成する (穴は透過のまま残す).

    ``fill_image`` を渡すと、色の代わりにその画像 (キャンバスと同サイズ) を
    パッチ領域へ貼り込む (線種「マテリアル」用。領域基準で貼るため、閉じた
    形でも継ぎ目が出ない)。
    """
    ep = _ep()
    if ep.Image is None or ep.ImageDraw is None or not patches:
        return
    if fill_image is not None:
        mask = _patches_mask_px(canvas, patches)
        if clip_mask is not None and ep.ImageChops is not None:
            mask = ep.ImageChops.multiply(mask, clip_mask)
        temp = fill_image.copy()
        if temp.size != canvas.image.size:
            temp = temp.resize(canvas.image.size)
        alpha = mask
        if ep.ImageChops is not None:
            alpha = ep.ImageChops.multiply(temp.getchannel("A"), mask)
        temp.putalpha(alpha)
        canvas.image.alpha_composite(temp)
        return
    temp = ep.Image.new("RGBA", canvas.image.size, (0, 0, 0, 0))
    temp_draw = ep.ImageDraw.Draw(temp)
    for outer, holes in patches:
        outer_px = canvas.points_px(outer)
        if len(outer_px) < 3:
            continue
        temp_draw.polygon(outer_px, fill=color)
        for hole in holes:
            hole_px = canvas.points_px(hole)
            if len(hole_px) >= 3:
                temp_draw.polygon(hole_px, fill=(0, 0, 0, 0))
    if clip_mask is not None and ep.ImageChops is not None:
        alpha = ep.ImageChops.multiply(temp.getchannel("A"), clip_mask)
        temp.putalpha(alpha)
    canvas.image.alpha_composite(temp)


def _line_material_texture(entry):
    """線種「マテリアル」のテクスチャ画像と単色フォールバックを返す.

    返り値は (PIL画像 or None, RGBA色 or None)。マテリアル未指定なら (None, None)。
    """
    import os

    name = str(getattr(entry, "line_material_name", "") or "").strip()
    if not name:
        return None, None
    try:
        import bpy as _bpy

        mat = _bpy.data.materials.get(name)
    except Exception:  # noqa: BLE001
        mat = None
    if mat is None:
        return None, None
    ep = _ep()
    if ep.Image is None:
        return None, None
    src = None
    try:
        if getattr(mat, "use_nodes", False) and mat.node_tree is not None:
            for node in mat.node_tree.nodes:
                image = getattr(node, "image", None)
                if getattr(node, "bl_idname", "") == "ShaderNodeTexImage" and image is not None:
                    path = _bpy.path.abspath(image.filepath) if image.filepath else ""
                    if path and os.path.isfile(path):
                        from pathlib import Path as _Path

                        src = ep._safe_load_image(_Path(path))
                    break
    except Exception:  # noqa: BLE001
        src = None
    if src is not None:
        return src.convert("RGBA"), None
    try:
        return None, ep._rgb255(tuple(mat.diffuse_color), alpha=1.0)
    except Exception:  # noqa: BLE001
        return None, None


def _line_material_fill_image(entry, size: tuple[int, int]):
    """線種「マテリアル」の帯を塗る画像 (キャンバスサイズ) を作る.

    マテリアルの最初の画像テクスチャをページ空間でタイルして使う。
    画像が無ければマテリアルのビューポート表示色で塗る。どちらも領域基準の
    貼り込みのため、閉じた形 (円・矩形など) でも始点終点の継ぎ目が出ない。
    """
    ep = _ep()
    src, color = _line_material_texture(entry)
    if src is not None:
        out = ep.Image.new("RGBA", size, (0, 0, 0, 0))
        for y in range(0, size[1], max(1, src.height)):
            for x in range(0, size[0], max(1, src.width)):
                out.paste(src, (x, y))
        return out
    if color is not None:
        return ep.Image.new("RGBA", size, color)
    return None


def _line_material_ribbon_image(entry, canvas, outline_mm, band_rings, line_w_mm: float, dpi: float):
    """貼り方「線に沿う (リボン)」の帯画像 (キャンバスサイズ) を作る.

    テクスチャの高さを帯幅に合わせ、輪郭の弧長方向へ「周長 ÷ タイル幅」の
    整数枚で敷く。閉ループ一周でちょうど割り切れるため、始点終点の継ぎ目が
    構造的に出ない (テクスチャ自体が横方向にループする柄なら模様も連続する)。
    テクスチャ画像が無いマテリアルではタイル貼りと同じ結果のため None を返す
    (呼び出し側が領域基準の塗りへフォールバックする)。
    """
    ep = _ep()
    src, _color = _line_material_texture(entry)
    if src is None or not band_rings:
        return None
    try:
        import numpy as np

        from ..utils import ribbon_mapping
    except Exception:  # noqa: BLE001
        return None
    loop_px = canvas.points_px(outline_mm)
    segs = ribbon_mapping.loop_segments(loop_px)
    if segs is None:
        return None
    band_w_px = max(1.0, mm_to_px(float(line_w_mm), dpi))
    stretch_single = bool(getattr(entry, "line_material_stretch_single", False))
    seam_fix = str(getattr(entry, "line_material_seam_fix", "none") or "none")
    if stretch_single:
        # 1枚を始点から終点まで引き伸ばす (左右がつながらない柄では接続点で途切れる)
        n_tiles = 1
    else:
        n_tiles = ribbon_mapping.tile_count(segs["total"], band_w_px, src.width, src.height)
    mask = _patches_mask_px(canvas, band_rings)
    mask_arr = np.asarray(mask)
    ys, xs = np.nonzero(mask_arr)
    if len(xs) == 0:
        return None
    s_arr, d_arr = ribbon_mapping.project_points(segs, xs + 0.5, ys + 0.5)
    src_arr = np.asarray(src, dtype=np.uint8)
    v = np.clip(np.floor(d_arr / band_w_px * src.height), 0, src.height - 1).astype(np.int64)
    out_arr = np.zeros((canvas.image.size[1], canvas.image.size[0], 4), dtype=np.uint8)
    t_arr = np.clip(s_arr / segs["total"], 0.0, 1.0)
    if stretch_single and seam_fix == "mirror":
        # 行きは普通に、帰りは鏡像: u(t) = 1-|1-2t| → 始点終点とも左端で一致
        m = 1.0 - np.abs(1.0 - 2.0 * t_arr)
        u = np.clip(np.floor(m * src.width), 0, src.width - 1).astype(np.int64)
        out_arr[ys, xs] = src_arr[v, u]
    elif stretch_single and seam_fix == "crossfade":
        # 始点側の短い区間で「画像の先頭」と「画像の末尾」を重ねて馴染ませる。
        # 使用幅を W-F に縮め、u<F の区間で tex(u) と tex(u+W-F) をブレンドする
        # と、一周の終わり tex(W-F) と始まりが連続する。
        fade_px = max(1, int(round(src.width * 0.15)))
        usable = max(1, src.width - fade_px)
        u_base = t_arr * usable
        u1 = np.clip(np.floor(u_base), 0, src.width - 1).astype(np.int64)
        u2 = np.clip(np.floor(u_base + usable), 0, src.width - 1).astype(np.int64)
        beta = np.clip(1.0 - u_base / fade_px, 0.0, 1.0)[:, None]
        blended = (1.0 - beta) * src_arr[v, u1].astype(np.float64) + beta * src_arr[v, u2].astype(np.float64)
        out_arr[ys, xs] = np.clip(np.round(blended), 0, 255).astype(np.uint8)
    else:
        u = np.floor(t_arr * n_tiles * src.width).astype(np.int64) % src.width
        out_arr[ys, xs] = src_arr[v, u]
    return ep.Image.fromarray(out_arr, "RGBA")


def _mitre_band_polygons_mm(
    outline,
    outer_off_mm: float,
    inner_off_mm: float,
    *,
    sharp: bool = True,
    anchor_cfg: tuple[float, float] | None = None,
    anchor_cfg_lo: tuple[float, float] | None = None,
):
    """輪郭のオフセット帯 (mm 座標) を返す。sharp=True で角が尖る.

    anchor_cfg 指定時は新方式J (頂点距離方式) で構築する (ビューポートと同一)。
    anchor_cfg_lo は inner_off_mm 側だけ別倍率にしたいとき (谷/山の線幅%を
    フチ・多重線リングの外縁/内縁で別々に効かせる場合) に指定する。
    """
    if len(outline) < 3:
        return []
    try:
        from ..utils import balloon_tail_boolean

        return balloon_tail_boolean.mitre_band_polygons(
            list(outline),
            float(outer_off_mm),
            float(inner_off_mm),
            sharp=sharp,
            anchor_cfg=anchor_cfg,
            anchor_cfg_lo=anchor_cfg_lo,
        )
    except Exception:  # noqa: BLE001
        return []


def _anchor_cfg_for_export(entry) -> tuple[float, float] | None:
    """新方式J (頂点距離方式) の設定。標準方式なら None (正典は balloon_anchor_band)."""
    try:
        from ..utils import balloon_anchor_band

        return balloon_anchor_band.anchor_cfg_for_entry(entry)
    except Exception:  # noqa: BLE001
        return None


def _main_line_anchor_scale(
    anchor_cfg: tuple[float, float],
    half_width_mm: float,
    peak_pct: float,
    valley_pct: float,
) -> tuple[float, float]:
    """主線帯 ([-half, +half] 対称) のアンカー倍率 (山, 谷) を返す.

    両端が同じ shrink_ref=half_width_mm を使うため hi/lo で同じ値になり
    (edge_scale_for_width_pct 参照)、呼び出し側は anchor_cfg 一つだけ渡せばよい。
    balloon_line_mesh.ensure_balloon_line_mesh の新方式J分岐と同一規則。
    """
    from ..utils import balloon_anchor_band

    return (
        balloon_anchor_band.edge_scale_for_width_pct(half_width_mm, half_width_mm, anchor_cfg[0], peak_pct),
        balloon_anchor_band.edge_scale_for_width_pct(half_width_mm, half_width_mm, anchor_cfg[1], valley_pct),
    )


def _edge_fringe_anchor_scales(
    anchor_cfg: tuple[float, float],
    base_mm: float,
    width_mm: float,
    peak_pct: float,
    valley_pct: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """フチ帯 (主線の外端/内端に接する near 側、さらに width ぶん離れた far 側) の
    アンカー倍率を返す。 (near, far) それぞれ (山, 谷)。

    near 側は主線の縮み比 (pct/100) に一致させて密着させ、far 側はフチ自身の
    幅をJの比例則のまま保つ (balloon_line_mesh.ensure_balloon_outer/inner_edge_mesh
    と同一規則。ビューポートと書き出しの一致に必須)。
    """
    from ..utils import balloon_anchor_band

    near = (
        balloon_anchor_band.edge_scale_for_width_pct(base_mm, base_mm, anchor_cfg[0], peak_pct),
        balloon_anchor_band.edge_scale_for_width_pct(base_mm, base_mm, anchor_cfg[1], valley_pct),
    )
    far = (
        balloon_anchor_band.edge_scale_for_width_pct(base_mm + width_mm, base_mm, anchor_cfg[0], peak_pct),
        balloon_anchor_band.edge_scale_for_width_pct(base_mm + width_mm, base_mm, anchor_cfg[1], valley_pct),
    )
    return near, far


def _multi_ring_anchor_scales(
    anchor_cfg: tuple[float, float],
    center_mm: float,
    ring_width_mm: float,
    peak_pct: float,
    valley_pct: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """多重線リング帯 (中心 center_mm, 全幅 ring_width_mm) のアンカー倍率を返す.

    リング中心の位置はJの比例則のまま保ち、リング幅だけをpctぶん細らせる:
    本体から遠い縁 shrink_ref=+半幅 / 近い縁 shrink_ref=-半幅 (どちらもリング
    中心へ寄る向き)。balloon_line_mesh.ensure_balloon_multi_line_mesh の
    新方式J分岐と同一規則。戻り値は (hi, lo) で hi=本体から遠い縁 / lo=近い縁、
    各要素は (山, 谷)。
    """
    from ..utils import balloon_anchor_band

    half = ring_width_mm * 0.5
    scales = []
    for delta in (center_mm + half, center_mm - half):
        shrink = half if abs(delta) >= abs(center_mm) else -half
        scales.append((
            balloon_anchor_band.edge_scale_for_width_pct(delta, shrink, anchor_cfg[0], peak_pct),
            balloon_anchor_band.edge_scale_for_width_pct(delta, shrink, anchor_cfg[1], valley_pct),
        ))
    return scales[0], scales[1]


def _j_kept_ring_band_mm(
    j_detected,
    center_mm: float,
    ring_w_mm: float,
    spacing_mm: float,
    ring_len: float,
    cross_enabled: bool,
    ring_anchor_cfg: tuple[float, float],
    ring_anchor_cfg_lo: tuple[float, float] | None,
):
    """新方式J + 長さ変化/交差 のリング帯 (keep 区間ピース) を mm 座標で返す.

    balloon_line_mesh.ensure_balloon_multi_line_mesh のJ分岐と同一規則
    (谷を基準に山の頂点側を削り、交差ONなら両端を接線方向へ延長)。
    戻り値: [(outer, holes), ...] / [] = 意図的にこのリングを描かない /
    None = 全周ミター帯で描くべき (長さ100%・山なし・構築失敗)。
    """
    if j_detected is None or ring_len >= 0.999:
        return None
    try:
        from ..utils import balloon_anchor_band
        from ..utils import balloon_line_mesh as blm

        peaks, valleys = balloon_anchor_band.peaks_valleys_from_detected(j_detected)
        n_det = len(j_detected["pts"])
        kept = blm._ring_kept_index_segments(n_det, peaks, valleys, ring_len)
        if not kept:
            return []  # 全カット (長さ 0% 近傍)
        if len(kept) == 1 and len(kept[0]) >= n_det:
            return None  # 山が無い等で全周のまま
        half = ring_w_mm * 0.5
        lo_cfg = ring_anchor_cfg_lo if ring_anchor_cfg_lo is not None else ring_anchor_cfg
        built = balloon_anchor_band.anchor_band_kept_pieces(
            [],
            center_mm - half,
            center_mm + half,
            ring_anchor_cfg[0],
            ring_anchor_cfg[1],
            kept,
            peak_scale_lo=lo_cfg[0],
            valley_scale_lo=lo_cfg[1],
            detected=j_detected,
            cross_extension=(spacing_mm + ring_w_mm) if cross_enabled else 0.0,
        )
        return built if built else None
    except Exception:  # noqa: BLE001
        return None


def _body_sharp_corners(entry) -> bool:
    """フキダシ本体の「角を尖らせる」(形状パラメータ) が ON か."""
    sp = getattr(entry, "shape_params", None)
    return bool(getattr(sp, "cloud_valley_sharp", False))


def _flash_strokes_page_mm(entry, rect: Rect, flip_h: bool, flip_v: bool, rotation_deg: float):
    """ウニフラ/白抜き線のストローク列をページ座標 mm で返す.

    ビューポートのメッシュ焼き込みと同じ生成器を使い、出力 (サムネイル/
    ページ出力/PSD) にも同じ放射線を描けるようにする。
    戻り値: [(role, pts_mm, radii_mm, opacities, side, cyclic), ...]
    """
    try:
        from ..utils import balloon_flash_effect_line_mesh as flash_mesh

        strokes = flash_mesh.generate_flash_strokes_rect_local(entry)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for stroke in strokes:
        raw = list(getattr(stroke, "points_xyz", None) or [])
        if len(raw) < 2:
            continue
        pts = [(rect.x + float(p[0]) * 1000.0, rect.y + float(p[1]) * 1000.0) for p in raw]
        pts = _apply_entry_free_transform(entry, pts, rect)
        pts = _apply_balloon_transforms(pts, rect, flip_h, flip_v, rotation_deg)
        radii = list(getattr(stroke, "radii", None) or [])
        base_r_mm = float(getattr(stroke, "radius", 0.0) or 0.0) * 1000.0
        radii_mm = [
            float(radii[i]) * 1000.0 if i < len(radii) else base_r_mm
            for i in range(len(raw))
        ]
        out.append((
            str(getattr(stroke, "role", "") or "line"),
            pts,
            radii_mm,
            list(getattr(stroke, "opacities", None) or []),
            float(getattr(stroke, "side", 0.0) or 0.0),
            bool(getattr(stroke, "cyclic", False)),
        ))
    return out


def _draw_flash_strokes(canvas, entry, flash_strokes, dpi: int) -> None:
    """ウニフラ/白抜き線のストローク列を可変幅の多角形として描く."""
    ep = _ep()
    if ep.Image is None or ep.ImageDraw is None or not flash_strokes:
        return
    style = str(getattr(entry, "line_style", "") or "")
    line_rgb = ep._rgb255(entry.line_color, alpha=1.0)[:3]
    white_rgb = (255, 255, 255)
    if style == "uni_flash":
        # スロット 1 (終点形状の塗り) は塗り色、下地はウニフラの下地色
        slot1_rgb = ep._rgb255(getattr(entry, "fill_color", (1.0, 1.0, 1.0, 1.0)), alpha=1.0)[:3]
        underlay_rgb = ep._rgb255(getattr(entry, "white_underlay_color", (1.0, 1.0, 1.0, 1.0)), alpha=1.0)[:3]
    else:
        slot1_rgb = white_rgb
        underlay_rgb = white_rgb
    z_order = {"end_fill": 0, "underlay": 1, "white_outline_white": 2}
    temp = ep.Image.new("RGBA", canvas.image.size, (0, 0, 0, 0))
    draw = ep.ImageDraw.Draw(temp)
    for role, pts_mm, radii_mm, opacities, side, cyclic in sorted(
        flash_strokes, key=lambda item: z_order.get(item[0], 3)
    ):
        if role == "underlay":
            rgb = underlay_rgb
        elif role in {"end_fill", "white_outline_white"}:
            rgb = slot1_rgb
        else:
            rgb = line_rgb
        if (role == "end_fill" or (role == "white_outline_white" and cyclic)) and len(pts_mm) >= 3:
            poly_px = canvas.points_px(pts_mm)
            if len(poly_px) >= 3:
                draw.polygon(poly_px, fill=(*rgb, 255))
            continue
        n = len(pts_mm)
        seg_count = n if cyclic else n - 1
        # 端と折れ目を点ごとの半径の円で丸める (しっぽと同じ円ベースのストローク)。
        # 下地 (underlay) は片側オフセットの帯なので円を打たない。
        if not (role == "underlay" and abs(side) > 1.0e-9):
            for k in range(n):
                rk = radii_mm[k] if k < len(radii_mm) else 0.0
                if rk <= 1.0e-9:
                    continue
                ak = float(opacities[k]) if k < len(opacities) else 1.0
                alpha_k = int(round(255.0 * max(0.0, min(1.0, ak))))
                if alpha_k <= 0:
                    continue
                cx, cy = pts_mm[k]
                corner_px = canvas.points_px([(cx - rk, cy - rk), (cx + rk, cy + rk)])
                if len(corner_px) == 2:
                    (x0_px, y0_px), (x1_px, y1_px) = corner_px
                    draw.ellipse(
                        (
                            min(x0_px, x1_px),
                            min(y0_px, y1_px),
                            max(x0_px, x1_px),
                            max(y0_px, y1_px),
                        ),
                        fill=(*rgb, alpha_k),
                    )
        for i in range(seg_count):
            j = (i + 1) % n
            x0, y0 = pts_mm[i]
            x1, y1 = pts_mm[j]
            dx = x1 - x0
            dy = y1 - y0
            seg = math.hypot(dx, dy)
            if seg <= 1.0e-9:
                continue
            r0 = radii_mm[i] if i < len(radii_mm) else 0.0
            r1 = radii_mm[j] if j < len(radii_mm) else 0.0
            if r0 <= 1.0e-9 and r1 <= 1.0e-9:
                continue
            a0 = float(opacities[i]) if i < len(opacities) else 1.0
            a1 = float(opacities[j]) if j < len(opacities) else 1.0
            alpha = int(round(255.0 * max(0.0, min(1.0, (a0 + a1) * 0.5))))
            if alpha <= 0:
                continue
            nx = -dy / seg
            ny = dx / seg
            if role == "underlay" and abs(side) > 1.0e-9:
                sign = 1.0 if side >= 0.0 else -1.0
                quad_mm = [
                    (x0, y0),
                    (x0 + nx * sign * r0, y0 + ny * sign * r0),
                    (x1 + nx * sign * r1, y1 + ny * sign * r1),
                    (x1, y1),
                ]
            else:
                quad_mm = [
                    (x0 + nx * r0, y0 + ny * r0),
                    (x0 - nx * r0, y0 - ny * r0),
                    (x1 - nx * r1, y1 - ny * r1),
                    (x1 + nx * r1, y1 + ny * r1),
                ]
            quad_px = canvas.points_px(quad_mm)
            if len(quad_px) >= 3:
                draw.polygon(quad_px, fill=(*rgb, alpha))
    opacity = _entry_opacity(entry)
    if opacity < 0.999:
        alpha_ch = temp.getchannel("A").point([int(i * opacity) for i in range(256)])
        temp.putalpha(alpha_ch)
    canvas.image.alpha_composite(temp)


def _entry_opacity(entry) -> float:
    return percentage.percent_to_factor(getattr(entry, "opacity", 100.0), 100.0)


def _line_width_scale(entry) -> float:
    try:
        return max(0.01, float(getattr(entry, "free_transform_line_width_scale", 1.0) or 1.0))
    except Exception:  # noqa: BLE001
        return 1.0


def _scaled_width_mm(entry, attr: str, default: float) -> float:
    try:
        value = float(getattr(entry, attr, default) or 0.0)
    except Exception:  # noqa: BLE001
        value = float(default)
    return max(0.0, value) * _line_width_scale(entry)


def _fill_opacity(entry) -> float:
    return _entry_opacity(entry) * percentage.percent_to_factor(getattr(entry, "fill_opacity", 100.0), 100.0)


def _fill_source_image(size: tuple[int, int], entry):
    ep = _ep()
    if not bool(getattr(entry, "fill_gradient_enabled", False)):
        color = ep._rgb255(getattr(entry, "fill_color", (1.0, 1.0, 1.0, 1.0)), alpha=1.0)
        return ep.Image.new("RGBA", size, color)
    start = ep._rgb255(getattr(entry, "fill_gradient_start_color", getattr(entry, "fill_color", (1, 1, 1, 1))), alpha=1.0)
    end = ep._rgb255(getattr(entry, "fill_gradient_end_color", getattr(entry, "fill_color", (1, 1, 1, 1))), alpha=1.0)
    width, height = size
    image = ep.Image.new("RGBA", size, start)
    if width <= 0 or height <= 0:
        return image
    angle_value = getattr(entry, "fill_gradient_angle_deg", None)
    # 0 度は有効値なので `or 90.0` で潰さない
    angle = math.radians(90.0 if angle_value is None else float(angle_value))
    ux = math.cos(angle)
    uy = -math.sin(angle)
    corners = [(0.0, 0.0), (float(width - 1), 0.0), (0.0, float(height - 1)), (float(width - 1), float(height - 1))]
    dots = [x * ux + y * uy for x, y in corners]
    mn = min(dots)
    span = max(1.0e-6, max(dots) - mn)
    try:
        # 全画素 Python ループは大きいページで秒単位かかるため NumPy で生成する
        import numpy as np

        xs = np.arange(width, dtype=np.float32) * ux
        ys = np.arange(height, dtype=np.float32) * uy
        t = np.clip((xs[None, :] + ys[:, None] - mn) / span, 0.0, 1.0)
        start_arr = np.asarray(start, dtype=np.float32)
        end_arr = np.asarray(end, dtype=np.float32)
        arr = start_arr[None, None, :] + (end_arr - start_arr)[None, None, :] * t[:, :, None]
        return ep.Image.fromarray(np.rint(arr).astype(np.uint8), "RGBA")
    except Exception:  # noqa: BLE001
        pass
    pixels = []
    for y in range(height):
        for x in range(width):
            t = max(0.0, min(1.0, ((x * ux + y * uy) - mn) / span))
            pixels.append(tuple(int(round(start[i] + (end[i] - start[i]) * t)) for i in range(4)))
    image.putdata(pixels)
    return image


def _fill_mask(canvas, polygons_px: list[list[tuple[int, int]]]):
    ep = _ep()
    if ep.Image is None or ep.ImageDraw is None:
        return None
    mask = ep.Image.new("L", canvas.image.size, 0)
    draw_mask = ep.ImageDraw.Draw(mask)
    for pts in polygons_px:
        if len(pts) >= 3:
            draw_mask.polygon(pts, fill=255)
    return mask


def _draw_fill_layer(canvas, entry, polygons_px: list[list[tuple[int, int]]], dpi: int, *, composite: bool = True):
    ep = _ep()
    if ep.Image is None or ep.ImageDraw is None:
        return None
    hard = _fill_mask(canvas, polygons_px)
    if hard is None:
        return None
    if not composite:
        # クリップ用のマスクだけ返す (ウニフラ/白抜き線では本体の塗りを描かない)
        return hard
    mask = hard
    blur = max(0.0, min(1.0, float(getattr(entry, "fill_blur_amount", 0.0) or 0.0)))
    if blur > 0.0 and ep.ImageFilter is not None and ep.ImageChops is not None:
        line_w = max(0.3, _scaled_width_mm(entry, "line_width_mm", 0.3))
        radius_px = max(1, int(round(mm_to_px(max(0.15, line_w * (0.65 + 3.35 * blur)), dpi) * 0.35)))
        blurred = hard.filter(ep.ImageFilter.GaussianBlur(radius=radius_px))
        axis = str(getattr(entry, "fill_blur_axis", "inside") or "inside")
        if axis == "outside":
            mask = ep.ImageChops.lighter(hard, blurred)
        elif axis == "center":
            mask = blurred
        else:
            mask = ep.ImageChops.multiply(blurred, hard)
        if bool(getattr(entry, "fill_blur_dither", False)):
            mask = mask.convert("1", dither=ep.Image.FLOYDSTEINBERG).convert("L")
    fill_alpha = float(getattr(entry, "fill_color", (1, 1, 1, 1))[3])
    alpha_scale = max(0, min(255, int(round(255.0 * _fill_opacity(entry) * fill_alpha))))
    if alpha_scale < 255:
        mask = mask.point([int(round(i * (alpha_scale / 255.0))) for i in range(256)])
    fill_image = _fill_source_image(canvas.image.size, entry)
    fill_image.putalpha(mask)
    canvas.image.alpha_composite(fill_image)
    return hard


def _draw_white_loop(draw, pts, color, width_px: int, style: str) -> None:
    if width_px <= 0 or len(pts) < 2:
        return
    _ep()._draw_styled_loop(draw, pts, color, width_px, style)


def _draw_inner_white_loop(canvas, clip_mask, pts, color, width_px: int, style: str) -> None:
    ep = _ep()
    if clip_mask is None or ep.Image is None or ep.ImageDraw is None:
        return
    temp = ep.Image.new("RGBA", canvas.image.size, (0, 0, 0, 0))
    draw = ep.ImageDraw.Draw(temp)
    _draw_white_loop(draw, pts, color, width_px, style)
    alpha = temp.getchannel("A")
    if ep.ImageChops is not None:
        alpha = ep.ImageChops.multiply(alpha, clip_mask)
    else:
        alpha = alpha.point(lambda px: px)
    temp.putalpha(alpha)
    canvas.image.alpha_composite(temp)


def _flash_white_line_width_px(entry, line_w_mm: float, dpi: int) -> int:
    # 内周の白帯はウニフラ専用。白抜き線は放射状の白線そのものを描くため、
    # 旧・非表示設定からページ出力だけへ白帯を足さない。
    if balloon_shapes.normalize_line_style(getattr(entry, "line_style", "")) != "uni_flash":
        return 0
    if not bool(getattr(entry, "flash_white_line_enabled", True)):
        return 0
    white_width_pct = max(0.0, min(300.0, float(getattr(entry, "flash_white_line_width_percent", 100.0) or 0.0)))
    white_peak_pct = max(0.0, min(200.0, float(getattr(entry, "flash_white_line_peak_width_pct", 100.0) or 0.0)))
    width_mm = max(0.0, float(line_w_mm)) * white_width_pct * white_peak_pct / 10000.0
    if width_mm <= 1.0e-6:
        return 0
    return max(1, int(round(mm_to_px(width_mm, dpi) * 2.0)))


def _entry_fill_rgb255(entry):
    return _ep()._rgb255(getattr(entry, "fill_color", (1.0, 1.0, 1.0, 1.0)), alpha=_fill_opacity(entry))


def _loop_cumulative_px(pts) -> tuple[list[tuple[float, float]], list[float]]:
    loop = [(float(x), float(y)) for x, y in pts]
    if len(loop) >= 2 and math.hypot(loop[0][0] - loop[-1][0], loop[0][1] - loop[-1][1]) > 1.0e-6:
        loop.append(loop[0])
    cum = [0.0]
    for index in range(1, len(loop)):
        cum.append(cum[-1] + math.hypot(loop[index][0] - loop[index - 1][0], loop[index][1] - loop[index - 1][1]))
    return loop, cum


def _point_on_loop_px(loop, cum, target: float) -> tuple[float, float] | None:
    if len(loop) < 2 or len(cum) != len(loop) or cum[-1] <= 1.0e-6:
        return None
    target = max(0.0, min(float(target), float(cum[-1])))
    for index in range(len(loop) - 1):
        start = float(cum[index])
        end = float(cum[index + 1])
        if target > end and index < len(loop) - 2:
            continue
        seg_len = end - start
        if seg_len <= 1.0e-6:
            continue
        p0 = loop[index]
        p1 = loop[index + 1]
        t = (target - start) / seg_len
        return (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)
    return loop[-1]


def _loop_subset_px(loop, cum, start_len: float, end_len: float) -> list[tuple[float, float]]:
    if len(loop) < 2 or len(cum) != len(loop):
        return []
    total = float(cum[-1])
    if total <= 1.0e-6:
        return []
    start_len = max(0.0, float(start_len))
    end_len = min(total, max(start_len, float(end_len)))
    out: list[tuple[float, float]] = []
    for index in range(len(loop) - 1):
        seg_start = float(cum[index])
        seg_end = float(cum[index + 1])
        if seg_end < start_len or seg_start > end_len:
            continue
        seg_len = seg_end - seg_start
        if seg_len <= 1.0e-6:
            continue
        p0 = loop[index]
        p1 = loop[index + 1]
        t0 = (max(seg_start, start_len) - seg_start) / seg_len
        t1 = (min(seg_end, end_len) - seg_start) / seg_len
        x0 = p0[0] + (p1[0] - p0[0]) * t0
        y0 = p0[1] + (p1[1] - p0[1]) * t0
        x1 = p0[0] + (p1[0] - p0[0]) * t1
        y1 = p0[1] + (p1[1] - p0[1]) * t1
        if not out or math.hypot(out[-1][0] - x0, out[-1][1] - y0) > 1.0e-6:
            out.append((x0, y0))
        if math.hypot(out[-1][0] - x1, out[-1][1] - y1) > 1.0e-6:
            out.append((x1, y1))
    return out


def _draw_pattern_loop(draw, pts, entry, color, width_px: int, dpi: int, style: str) -> None:
    loop, cum = _loop_cumulative_px(pts)
    if len(loop) < 2 or cum[-1] <= 1.0e-6:
        return
    line_width_mm = _scaled_width_mm(entry, "line_width_mm", 0.3)
    if style == "dotted":
        diameter_px = max(1.0, float(width_px))
        gap_px = max(0.0, float(mm_to_px(line_pattern.dotted_gap_mm(entry, line_width_mm), dpi)))
        spacing_px = max(diameter_px + gap_px, diameter_px * 1.05, 1.0)
        count = max(1, int(round(cum[-1] / spacing_px)))
        spacing_px = cum[-1] / count
        radius = diameter_px * 0.5
        for index in range(count):
            center = _point_on_loop_px(loop, cum, index * spacing_px)
            if center is None:
                continue
            x, y = center
            draw.ellipse(
                (
                    int(round(x - radius)),
                    int(round(y - radius)),
                    int(round(x + radius)),
                    int(round(y + radius)),
                ),
                fill=color,
            )
        return

    dash_px = max(1.0, float(mm_to_px(line_pattern.dashed_segment_mm(entry, line_width_mm), dpi)))
    gap_px = max(0.0, float(mm_to_px(line_pattern.dashed_gap_mm(entry, line_width_mm), dpi)))
    period_px = max(dash_px + gap_px, dash_px, 1.0)
    cap_radius = max(0.5, float(width_px) * 0.5)
    start = 0.0
    while start < cum[-1] - 1.0e-6:
        sub = _loop_subset_px(loop, cum, start, min(cum[-1], start + dash_px))
        if len(sub) >= 2:
            draw.line(
                [(int(round(x)), int(round(y))) for x, y in sub],
                fill=color,
                width=width_px,
                joint="curve",
            )
            # 端はしっぽの線と同じ丸キャップ (PIL の line は端が四角)
            for cx, cy in (sub[0], sub[-1]):
                draw.ellipse(
                    (
                        int(round(cx - cap_radius)),
                        int(round(cy - cap_radius)),
                        int(round(cx + cap_radius)),
                        int(round(cy + cap_radius)),
                    ),
                    fill=color,
                )
        start += period_px


def _draw_shape_line_loop(draw, pts, entry, color, width_px: int, dpi: int, center_px=None) -> None:
    """線種「図形」: 図形を輪郭に沿って連続配置して描く."""
    from ..utils import line_decor_geom

    polygons = line_decor_geom.decorations_along_loop(
        [(float(x), float(y)) for x, y in pts],
        kind=str(getattr(entry, "line_shape_kind", "circle") or "circle"),
        size=float(width_px),
        spacing=mm_to_px(max(0.0, float(getattr(entry, "line_shape_spacing_mm", 1.5) or 0.0)), dpi),
        angle_rad=math.radians(float(getattr(entry, "line_shape_angle_deg", 0.0) or 0.0)),
        jitter=float(getattr(entry, "line_shape_jitter", 0.0) or 0.0),
        seed=balloon_shapes.unified_seed_for_entry(entry),
        flip_y=True,
        orient=str(getattr(entry, "line_shape_orient", "line") or "line"),
        center=center_px,
    )
    for poly in polygons:
        draw.polygon([(int(round(x)), int(round(y))) for x, y in poly], fill=color)


def _wrapped_strip(src, u0: float, width_ratio: float):
    """画像を横方向 u0..u0+width_ratio (画像幅 1.0 で折り返し) で切り出す."""
    ep = _ep()
    src_w, src_h = src.size
    x0 = int(round((u0 % 1.0) * src_w))
    take = max(1, int(round(width_ratio * src_w)))
    strip = ep.Image.new("RGBA", (take, src_h), (0, 0, 0, 0))
    copied = 0
    while copied < take:
        chunk = min(src_w - x0, take - copied)
        strip.paste(src.crop((x0, 0, x0 + chunk, src_h)), (copied, 0))
        copied += chunk
        x0 = 0
    return strip


def _draw_image_line_loop(canvas, pts, entry, width_px: int, dpi: int) -> None:
    """線種「画像」: 画像を輪郭に沿って引き延ばして描く (区間パッチ近似)."""
    from pathlib import Path as _Path

    from ..utils import line_decor_geom

    ep = _ep()
    raw = str(getattr(entry, "line_image_path", "") or "").strip()
    if not raw or width_px <= 0 or len(pts) < 3:
        return
    try:
        import bpy

        path = bpy.path.abspath(raw)
    except Exception:  # noqa: BLE001
        path = raw
    if not _Path(path).is_file():
        return
    try:
        src = ep.Image.open(path).convert("RGBA")
    except Exception:  # noqa: BLE001
        return
    angle_deg = float(getattr(entry, "line_image_angle_deg", 0.0) or 0.0)
    if abs(angle_deg) > 1.0e-3:
        src = src.rotate(-angle_deg, expand=True)
    # フキダシの不透明度を画像線にも反映する
    opacity = _entry_opacity(entry)
    if opacity < 0.999:
        alpha = src.getchannel("A").point([int(i * opacity) for i in range(256)])
        src.putalpha(alpha)
    interval_px = max(2.0, mm_to_px(max(0.5, float(getattr(entry, "line_image_interval_mm", 20.0) or 20.0)), dpi))
    jitter = max(0.0, min(1.0, float(getattr(entry, "line_image_jitter", 0.0) or 0.0)))
    loop = line_decor_geom.resample_loop(
        [(float(x), float(y)) for x, y in pts],
        max(4.0, float(width_px) * 2.0),
    )
    if len(loop) < 3:
        return
    resampling = getattr(getattr(ep.Image, "Resampling", ep.Image), "BICUBIC", 3)
    arc = 0.0
    n = len(loop)
    for i in range(n):
        p0 = loop[i]
        p1 = loop[(i + 1) % n]
        seg_len = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        if seg_len < 0.5:
            continue
        strip = _wrapped_strip(src, arc / interval_px, seg_len / interval_px)
        patch = strip.resize((max(1, int(round(seg_len + 1))), max(1, int(round(width_px)))), resampling)
        rotation = math.degrees(math.atan2(-(p1[1] - p0[1]), p1[0] - p0[0]))
        patch = patch.rotate(rotation, expand=True, resample=resampling)
        cx = (p0[0] + p1[0]) * 0.5
        cy = (p0[1] + p1[1]) * 0.5
        if jitter > 0.0:
            wobble = math.sin(arc / interval_px * math.tau) * width_px * 0.5 * jitter
            normal = math.atan2(p1[0] - p0[0], -(p1[1] - p0[1]))
            cx += math.cos(normal) * wobble
            cy += math.sin(normal) * wobble
        pos = (int(round(cx - patch.width * 0.5)), int(round(cy - patch.height * 0.5)))
        canvas.image.alpha_composite(patch, dest=pos)
        arc += seg_len


def _draw_balloon_line_loop(draw, pts, entry, color, width_px: int, dpi: int, shape_center_px=None) -> None:
    if width_px <= 0 or len(pts) < 2:
        return
    style = str(getattr(entry, "line_style", "solid") or "solid")
    if style in {"dashed", "dotted"}:
        _draw_pattern_loop(draw, pts, entry, color, width_px, dpi, style)
        return
    if style == "shape":
        _draw_shape_line_loop(draw, pts, entry, color, width_px, dpi, shape_center_px)
        return
    if style != "double":
        _ep()._draw_styled_loop(draw, pts, color, width_px, style)
        return

    count = max(1, int(getattr(entry, "multi_line_count", 3) or 3))
    spacing_mm = max(0.0, float(getattr(entry, "multi_line_spacing_mm", 0.4) or 0.0))
    line_width_mm = _scaled_width_mm(entry, "multi_line_width_mm", 0.3)
    scale = max(0.0, float(getattr(entry, "multi_line_width_scale_percent", 100.0) or 0.0)) / 100.0
    fill_color = _entry_fill_rgb255(entry)
    rings: list[tuple[int, int]] = []
    inner_mm = _scaled_width_mm(entry, "line_width_mm", 0.3) * 0.5 + spacing_mm
    for index in range(1, min(12, count)):
        extra_width_mm = line_width_mm * (scale ** (index - 1))
        if extra_width_mm <= 0.0:
            continue
        outer_mm = inner_mm + extra_width_mm
        rings.append((max(1, int(round(mm_to_px(outer_mm * 2.0, dpi)))),
                      max(1, int(round(mm_to_px(inner_mm * 2.0, dpi))))))
        inner_mm = outer_mm + spacing_mm
    for outer_width_px, inner_width_px in reversed(rings):
        _ep()._draw_styled_loop(draw, pts, color, outer_width_px, "solid")
        _ep()._draw_styled_loop(draw, pts, fill_color, inner_width_px, "solid")
    _ep()._draw_styled_loop(draw, pts, color, width_px, "solid")


def render_balloon_layer(entry, canvas_height_px: int, dpi: int):
    if getattr(entry, "shape", "rect") == "none":
        return None
    ep = _ep()
    rect = Rect(float(entry.x_mm), float(entry.y_mm), float(entry.width_mm), float(entry.height_mm))
    flip_h = bool(getattr(entry, "flip_h", False))
    flip_v = bool(getattr(entry, "flip_v", False))
    rotation_deg = float(getattr(entry, "rotation_deg", 0.0))
    outline = _balloon_outline_mm(entry, rect)
    fill_outline = _balloon_fill_outline_mm(entry, rect)
    outline = _apply_entry_free_transform(entry, outline, rect)
    fill_outline = _apply_entry_free_transform(entry, fill_outline, rect)
    outline = _apply_balloon_transforms(outline, rect, flip_h, flip_v, rotation_deg)
    fill_outline = _apply_balloon_transforms(fill_outline, rect, flip_h, flip_v, rotation_deg)
    tail_outlines = []
    sharp_tail_regions: list[list[tuple[float, float]]] = []
    sharp_tail_infos: list[tuple[list, list, list]] = []
    for tail in entry.tails:
        tail_outline = _apply_entry_free_transform(entry, _balloon_tail_polygon(rect, tail), rect)
        tail_outline = _apply_balloon_transforms(tail_outline, rect, flip_h, flip_v, rotation_deg)
        tail_outlines.append(tail_outline)
        if bool(getattr(tail, "sharp_corners", False)) and len(tail_outline) >= 3:
            sharp_tail_regions.append(tail_outline)
            # 先端を「抜き」のように絞るための中心線と半幅
            centerline_mm, halves_mm = balloon_tail_geom.centerline_with_halfwidths(rect, tail)
            if len(centerline_mm) >= 2:
                centerline_mm = _apply_entry_free_transform(entry, centerline_mm, rect)
                centerline_mm = _apply_balloon_transforms(centerline_mm, rect, flip_h, flip_v, rotation_deg)
                sharp_tail_infos.append((centerline_mm, list(halves_mm), tail_outline))
    # 線しっぽ (線種「線」): 1本のストローク線として線色で塗る
    line_stroke_outlines: list[list[tuple[float, float]]] = []
    for tail in entry.tails:
        if not balloon_tail_geom.is_line_stroke(tail):
            continue
        pts = balloon_tail_geom.line_stroke_polygon_for_tail(rect, tail)
        pts = _apply_entry_free_transform(entry, pts, rect)
        pts = _apply_balloon_transforms(pts, rect, flip_h, flip_v, rotation_deg)
        if len(pts) >= 3:
            line_stroke_outlines.append(pts)
    # 連続楕円しっぽ (線種「楕円」): 本体に重なる楕円は本体と結合し、
    # 重ならない楕円だけ独立した楕円列として描く
    ellipse_outlines: list[list[tuple[float, float]]] = []
    for tail in entry.tails:
        if not balloon_tail_geom.is_ellipse_chain(tail):
            continue
        for ellipse in balloon_tail_geom.ellipse_chain_for_tail(rect, tail):
            pts = balloon_tail_geom.ellipse_polygon(ellipse)
            pts = _apply_entry_free_transform(entry, pts, rect)
            pts = _apply_balloon_transforms(pts, rect, flip_h, flip_v, rotation_deg)
            if len(pts) >= 3:
                ellipse_outlines.append(pts)
    merged_ellipses, ellipse_outlines = _split_ellipse_outlines_by_body(outline, ellipse_outlines)
    # 「中心点」向き図形の基準: 輪郭平均ではなく、ユーザーが動かす中心点。
    body_center_mm = _entry_center_point_mm(entry, rect, flip_h, flip_v, rotation_deg)
    # ビューポートと同じ結合 (外しっぽは結合 / 内しっぽはえぐり) を出力側にも適用
    merged_outline = _merged_outline_with_tails(outline, tail_outlines, merged_ellipses)
    if merged_outline is not None:
        outline = merged_outline
        fill_outline = list(merged_outline)
        tail_outlines = []
    all_pts = list(outline)
    all_pts.extend(fill_outline)
    for tail_outline in tail_outlines:
        all_pts.extend(tail_outline)
    for ellipse_outline in ellipse_outlines:
        all_pts.extend(ellipse_outline)
    for stroke_outline in line_stroke_outlines:
        all_pts.extend(stroke_outline)
    # ウニフラ/白抜き線: ビューポートと同じ生成器で放射線を計算し、
    # キャンバス範囲にも含める (線はフキダシの外へ大きく伸びるため)
    is_flash = balloon_shapes.is_flash_line_style(str(getattr(entry, "line_style", "") or ""))
    flash_strokes = (
        _flash_strokes_page_mm(entry, rect, flip_h, flip_v, rotation_deg) if is_flash else []
    )
    flash_pad_mm = 0.0
    for _role, flash_pts, flash_radii, _ops, _side, _cyc in flash_strokes:
        all_pts.extend(flash_pts)
        if flash_radii:
            flash_pad_mm = max(flash_pad_mm, max(flash_radii))
    line_style = getattr(entry, "line_style", "solid")
    line_w_mm = (
        0.0
        if str(line_style or "") == "none"
        else _scaled_width_mm(entry, "line_width_mm", 0.3)
    )
    # 2026-07-23: 主線・フチ・多重線は本体輪郭を中心に ±line_w_mm/2 で対称展開する
    # 中心アライメントに統一 (旧: 本体の外側にのみ line_w_mm 成長させる外側アライン
    # メントで、山頂が幅0まで痩せる針状になっていた)。
    half_line_w_mm = line_w_mm * 0.5
    outer_enabled = bool(getattr(entry, "outer_white_margin_enabled", False))
    inner_enabled = bool(getattr(entry, "inner_white_margin_enabled", False))
    outer_w_mm = (
        _scaled_width_mm(entry, "outer_white_margin_width_mm", 0.0)
        if outer_enabled
        else 0.0
    )
    inner_w_mm = (
        _scaled_width_mm(entry, "inner_white_margin_width_mm", 0.0)
        if inner_enabled
        else 0.0
    )
    body_sharp = _body_sharp_corners(entry)
    export_anchor_cfg = _anchor_cfg_for_export(entry)
    # 新方式Jの谷/山線幅%: ビューポート (balloon_line_mesh) と共用の正典ヘルパーで
    # 主線・フチのアンカー倍率へ適用する (2026-07-23。標準方式・非J形状では
    # 100/100 が返るため以下の計算は恒等変換になる)。
    line_valley_pct = line_peak_pct = 100.0
    if export_anchor_cfg is not None:
        from ..utils import balloon_line_mesh as blm

        line_valley_pct, line_peak_pct, _ml_valley_pct, _ml_peak_pct = blm.dynamic_width_pcts(entry)
    band_line_styles = {"solid", "double", "material"}
    outer_band_rings = []
    inner_band_rings = []
    multi_band_groups = []
    main_band_rings = []
    if line_w_mm > 1.0e-6 and not is_flash:
        if outer_enabled and outer_w_mm > 1.0e-6:
            outer_anchor_cfg = export_anchor_cfg
            outer_anchor_cfg_lo = None
            if export_anchor_cfg is not None:
                near, far = _edge_fringe_anchor_scales(
                    export_anchor_cfg, half_line_w_mm, outer_w_mm, line_peak_pct, line_valley_pct
                )
                outer_anchor_cfg, outer_anchor_cfg_lo = far, near
            outer_band_rings = _mitre_band_polygons_mm(
                outline,
                half_line_w_mm + outer_w_mm,
                half_line_w_mm,
                sharp=body_sharp,
                anchor_cfg=outer_anchor_cfg,
                anchor_cfg_lo=outer_anchor_cfg_lo,
            )
        if inner_enabled and inner_w_mm > 1.0e-6:
            inner_anchor_cfg = export_anchor_cfg
            inner_anchor_cfg_lo = None
            if export_anchor_cfg is not None:
                near, far = _edge_fringe_anchor_scales(
                    export_anchor_cfg, half_line_w_mm, inner_w_mm, line_peak_pct, line_valley_pct
                )
                inner_anchor_cfg, inner_anchor_cfg_lo = near, far
            inner_band_rings = _mitre_band_polygons_mm(
                outline,
                -half_line_w_mm,
                -half_line_w_mm - inner_w_mm,
                sharp=body_sharp,
                anchor_cfg=inner_anchor_cfg,
                anchor_cfg_lo=inner_anchor_cfg_lo,
            )
        if str(line_style or "") in band_line_styles:
            if str(line_style or "") == "double":
                multi_band_groups = _multi_ring_band_polygons(
                    outline,
                    entry,
                    sharp=body_sharp,
                )
            # 谷/山の線幅%が両方0% (新方式Jのみ): 主線全体を非表示にする。J の帯
            # 構築へ落とすと帯が空になり従来方式の均一幅へフォールバックして
            # しまうため、mitre_band_polygons を呼ぶ前に抜ける (balloon_line_mesh
            # の main_line_both_zero ガードと同一の理由)。
            main_both_zero = (
                export_anchor_cfg is not None
                and line_valley_pct <= 1.0e-3
                and line_peak_pct <= 1.0e-3
            )
            if main_both_zero:
                main_band_rings = []
            else:
                main_anchor_cfg = export_anchor_cfg
                if export_anchor_cfg is not None:
                    main_anchor_cfg = _main_line_anchor_scale(
                        export_anchor_cfg, half_line_w_mm, line_peak_pct, line_valley_pct
                    )
                main_band_rings = _mitre_band_polygons_mm(
                    outline,
                    half_line_w_mm,
                    -half_line_w_mm,
                    sharp=body_sharp,
                    anchor_cfg=main_anchor_cfg,
                )
                # 新方式J ではしっぽ先端もアンカーとして頂点距離規則で処理される
                if merged_outline is not None and sharp_tail_infos and export_anchor_cfg is None:
                    try:
                        from ..utils import balloon_tail_boolean

                        main_band_rings = balloon_tail_boolean.apply_sharp_tail_tips(
                            main_band_rings,
                            list(outline),
                            line_w_mm,
                            sharp_tail_infos,
                            add_bend_mitre=not body_sharp,
                        )
                    except Exception:  # noqa: BLE001
                        pass
        # 尖角は線幅より大きく張り出すため、固定padでは切れる。実際に描く
        # 主線・フチ・多重線の全頂点をbboxへ含め、ページ端でも先端を欠かさない。
        for patches in (outer_band_rings, inner_band_rings, main_band_rings):
            for outer, holes in patches:
                all_pts.extend(outer)
                for hole in holes:
                    all_pts.extend(hole)
        for patches in multi_band_groups:
            for outer, holes in patches:
                all_pts.extend(outer)
                for hole in holes:
                    all_pts.extend(hole)
    bbox = ep._points_bbox(all_pts)
    if bbox is None:
        return None
    blur = max(0.0, min(1.0, float(getattr(entry, "fill_blur_amount", 0.0) or 0.0)))
    blur_pad = line_w_mm * (0.65 + 3.35 * blur) if blur > 0.0 else 0.0
    pad_mm = max(2.0, line_w_mm * 4.0 + outer_w_mm * 2.0 + blur_pad, flash_pad_mm + 1.0)
    canvas = ep._canvas_for_bbox(bbox, canvas_height_px, dpi, pad_mm=pad_mm)
    if canvas is None:
        return None
    line_color = ep._rgb255(entry.line_color, alpha=_entry_opacity(entry))
    outer_color = ep._rgb255(getattr(entry, "outer_white_margin_color", (1.0, 1.0, 1.0, 1.0)), alpha=_entry_opacity(entry))
    inner_color = ep._rgb255(getattr(entry, "inner_white_margin_color", (1.0, 1.0, 1.0, 1.0)), alpha=_entry_opacity(entry))
    line_width_px = max(0, int(round(mm_to_px(line_w_mm, dpi))))
    outer_width_px = int(round(mm_to_px(_scaled_width_mm(entry, "outer_white_margin_width_mm", 0.0), dpi)))
    inner_width_px = int(round(mm_to_px(_scaled_width_mm(entry, "inner_white_margin_width_mm", 0.0), dpi)))
    draw = ep.ImageDraw.Draw(canvas.image)
    outline_px = canvas.points_px(outline)
    fill_outline_px = canvas.points_px(fill_outline)
    body_center_px = None
    if body_center_mm is not None:
        center_pts = canvas.points_px([body_center_mm])
        if center_pts:
            body_center_px = (float(center_pts[0][0]), float(center_pts[0][1]))
    fill_clip_mask = None
    if len(fill_outline_px) >= 3:
        fill_polygons = [fill_outline_px]
        fill_polygons.extend(canvas.points_px(tail_outline) for tail_outline in tail_outlines)
        # ウニフラ/白抜き線では本体の塗りは描かない (下地は「終点形状を下地と
        # して塗る」が担当)。クリップ用マスクだけ作る
        fill_clip_mask = _draw_fill_layer(
            canvas, entry, [pts for pts in fill_polygons if len(pts) >= 3], dpi,
            composite=not is_flash,
        )
    line_clip_mask = fill_clip_mask
    draw_line = str(line_style or "") != "none" and line_width_px > 0
    flash_white_width_px = _flash_white_line_width_px(entry, line_w_mm, dpi) if draw_line else 0
    flash_white_color = ep._rgb255((1.0, 1.0, 1.0, 1.0), alpha=_entry_opacity(entry))
    if flash_white_width_px > 0:
        _draw_inner_white_loop(canvas, line_clip_mask, outline_px, flash_white_color, flash_white_width_px, "solid")
    # 実線・多重線の主線とフチは、画面のメッシュと同じ「輪郭の外側に乗る」
    # オフセット帯で描く。「角を尖らせる」ON なら mitre join で角まで尖らせる。
    line_material_mapping = str(getattr(entry, "line_material_mapping", "tile") or "tile")
    _tile_fill_cache: list = []

    def _line_band_fill_for(loop_outline, loop_band_rings):
        """この帯 (本体/しっぽ) を塗るマテリアル画像を貼り方に応じて返す."""
        if str(line_style or "") != "material":
            return None
        if line_material_mapping == "ribbon":
            ribbon = _line_material_ribbon_image(
                entry, canvas, loop_outline, loop_band_rings, line_w_mm, dpi
            )
            if ribbon is not None:
                return ribbon
        if not _tile_fill_cache:
            _tile_fill_cache.append(_line_material_fill_image(entry, canvas.image.size))
        return _tile_fill_cache[0]
    if draw_line and not is_flash and outer_band_rings:
        # 外フチ: 線の外側にだけ付く帯 (画面のメッシュと同じ付き方)。
        # 「角を尖らせる」ON のときは mitre join で角まで尖らせる。
        _composite_patches_px(
            canvas,
            outer_band_rings,
            outer_color,
        )
    if draw_line and not is_flash and inner_band_rings:
        # 内フチ: 本体の内側に付く帯
        _composite_patches_px(
            canvas,
            inner_band_rings,
            inner_color,
            clip_mask=line_clip_mask,
        )
    if draw_line:
        if is_flash:
            # ウニフラ/白抜き線: 本体輪郭の線は描かず、放射線群を描く
            # (ビューポートでも主線の帯は無く、放射線メッシュだけが見える)
            _draw_flash_strokes(canvas, entry, flash_strokes, dpi)
        elif str(line_style or "") == "image":
            _draw_image_line_loop(canvas, outline_px, entry, line_width_px, dpi)
        elif str(line_style or "") in band_line_styles:
            # 入れ子のリングを一つの一時画像へ描くと、外側リングの穴が
            # 先に描いた内側リングを消す。リングごとに個別合成して残す。
            for band_rings in multi_band_groups:
                _composite_patches_px(canvas, band_rings, line_color)
            band_rings = main_band_rings
            _composite_patches_px(
                canvas, band_rings, line_color, fill_image=_line_band_fill_for(outline, band_rings)
            )
        else:
            _draw_balloon_line_loop(draw, outline_px, entry, line_color, line_width_px, dpi, body_center_px)
    for tail_outline in tail_outlines:
        tail_px = canvas.points_px(tail_outline)
        if len(tail_px) >= 3:
            if flash_white_width_px > 0:
                _draw_inner_white_loop(canvas, line_clip_mask, tail_px, flash_white_color, flash_white_width_px, "solid")
            tail_sharp = body_sharp or tail_outline in sharp_tail_regions
            if draw_line and not is_flash and bool(getattr(entry, "outer_white_margin_enabled", False)):
                _composite_patches_px(
                    canvas,
                    _mitre_band_polygons_mm(
                        tail_outline, half_line_w_mm + outer_w_mm, half_line_w_mm, sharp=tail_sharp
                    ),
                    outer_color,
                )
            if draw_line and not is_flash and bool(getattr(entry, "inner_white_margin_enabled", False)):
                _composite_patches_px(
                    canvas,
                    _mitre_band_polygons_mm(
                        tail_outline, -half_line_w_mm, -half_line_w_mm - inner_w_mm, sharp=tail_sharp
                    ),
                    inner_color,
                    clip_mask=line_clip_mask,
                )
            if draw_line and not is_flash:
                if str(line_style or "") == "image":
                    _draw_image_line_loop(canvas, tail_px, entry, line_width_px, dpi)
                elif str(line_style or "") in band_line_styles:
                    band_rings = _mitre_band_polygons_mm(
                        tail_outline, half_line_w_mm, -half_line_w_mm, sharp=tail_sharp
                    )
                    tail_info = next(
                        (info for info in sharp_tail_infos if info[2] is tail_outline), None
                    )
                    if tail_info is not None:
                        try:
                            from ..utils import balloon_tail_boolean

                            band_rings = balloon_tail_boolean.apply_sharp_tail_tips(
                                band_rings,
                                list(tail_outline),
                                line_w_mm,
                                [tail_info],
                                add_bend_mitre=False,
                            )
                        except Exception:  # noqa: BLE001
                            pass
                    _composite_patches_px(
                        canvas,
                        band_rings,
                        line_color,
                        fill_image=_line_band_fill_for(tail_outline, band_rings),
                    )
                else:
                    _draw_balloon_line_loop(draw, tail_px, entry, line_color, line_width_px, dpi, body_center_px)
    # 線しっぽ (線種「線」): ストローク多角形を線色で塗る
    if line_stroke_outlines and draw_line:
        for stroke_outline in line_stroke_outlines:
            stroke_px = canvas.points_px(stroke_outline)
            if len(stroke_px) >= 3:
                draw.polygon(stroke_px, fill=line_color)
    # 連続楕円しっぽ: 親フキダシの塗り色・線色・線幅で各楕円を描く
    if ellipse_outlines:
        ellipse_fill = _entry_fill_rgb255(entry)
        for ellipse_outline in ellipse_outlines:
            ellipse_px = canvas.points_px(ellipse_outline)
            if len(ellipse_px) < 3:
                continue
            draw.polygon(ellipse_px, fill=ellipse_fill)
            if draw_line:
                ep._draw_styled_loop(draw, ellipse_px, line_color, line_width_px, "solid")
    return ep.ExportLayer(
        str(getattr(entry, "id", "") or "balloon"),
        canvas.image,
        canvas.left,
        canvas.top,
        blend_mode="normal",
        group_path=(
            "balloons",
            str(getattr(entry, "merge_group_id", "") or ""),
        )
        if getattr(entry, "merge_group_id", "")
        else ("balloons",),
    )
