"""コマ枠編集の内部処理: 枠線の辺/頂点選択 → ドラッグ移動 + 個別スタイル編集.

CLIP STUDIO PAINT の「枠線分割ツール (移動モード)」相当の操作感:
- LMB シングルクリック: クリック地点の最寄りの **辺** を選択 (ページに依存しない)
- LMB ダブルクリック: その辺を含む **枠線全体 (panel)** を選択
- ドラッグ: 選択した辺/頂点を移動 (隣接 panel と連動して gap を維持)
- 辺の中点に **三角ハンドル 2 つ** を表示 → クリックで隣接枠線/基本枠/裁ち落とし枠まで拡張
- 選択中の辺/枠線の **色・線幅** を N パネルから編集可能
  (辺選択 → 個別 edge_style 上書き、枠線選択 → panel.border 全体)
- ESC / RMB / Enter: ツール終了
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Optional

import bpy
import gpu
from bpy.types import Operator
from bpy_extras.view3d_utils import location_3d_to_region_2d, region_2d_to_location_3d
from gpu_extras.batch import batch_for_shader

from ..core.work import get_work
from ..io import page_io, coma_io
from . import coma_modal_state, selection_context_menu, view_event_region
from ..utils import (
    edge_selection,
    geom,
    log,
    page_browser,
    page_grid,
    page_range,
    coma_gutter_snap,
    coma_edge_adjacency,
    coma_vertex_edit,
    object_selection,
    polygon_geom,
    viewport_colors,
)

_logger = log.get_logger(__name__)

# ---- 定数 ----
EDGE_PICK_TOLERANCE_PX = 12.0  # 辺をクリックしたとみなす距離 (px)
VERTEX_PICK_TOLERANCE_PX = 14.0  # 頂点をクリックしたとみなす距離 (px)
HANDLE_SIZE_PX = 28.0  # 三角ハンドルの一辺 (px)
HANDLE_OFFSET_PX = 26.0  # 辺中点からハンドル中心までの距離 (px)
HANDLE_HIT_RADIUS_PX = 28.0
ADJACENCY_GAP_TOLERANCE_MM = 0.2  # 隣接判定: 対応辺との垂直距離が gap ± この値以内
ADJACENCY_OVERLAP_RATIO = 0.2  # 隣接判定: 重なり比率がこの値以上で連動
DOUBLE_CLICK_INTERVAL = 0.4  # シングル/ダブル判定の閾値 (秒)

COLOR_SELECTED_EDGE = viewport_colors.SELECTION_STRONG
COLOR_SELECTED_BORDER = viewport_colors.SELECTION
COLOR_SELECTED_VERTEX = viewport_colors.HANDLE_OUTLINE
COLOR_HANDLE = viewport_colors.HANDLE_FILL
COLOR_SELECTED_EDGE_FILL = (1.0, 0.0, 0.68, 0.42)
EDGE_SELECTED_BAND_WIDTH_PX = 10.0
# hover 中の ▲ ハンドルは黄色寄りに明るく強調 (alpha=1)
COLOR_HANDLE_HIGHLIGHT = (1.0, 0.85, 0.2, 1.0)
COLOR_VERTEX_HIGHLIGHT = (1.0, 0.85, 0.2, 1.0)
COLOR_VERTEX_ACTIVE = viewport_colors.SELECTION_STRONG


def _snap_gutter_to_finish_enabled(context=None) -> bool:
    try:
        from ..preferences import get_preferences

        prefs = get_preferences(context)
        if prefs is None:
            return True
        return bool(getattr(prefs, "snap_gutter_to_finish", True))
    except Exception:  # noqa: BLE001
        return True


def _find_view3d(context):
    area = context.area if context.area and context.area.type == "VIEW_3D" else None
    if area is None:
        screen = context.screen
        if screen is None:
            return None
        for a in screen.areas:
            if a.type == "VIEW_3D":
                area = a
                break
        else:
            return None
    region = None
    for r in area.regions:
        if r.type == "WINDOW":
            region = r
            break
    if region is None:
        return None
    space = area.spaces.active
    rv3d = getattr(space, "region_3d", None)
    if rv3d is None:
        return None
    return area, region, rv3d


def _tag_view3d_redraw(context) -> None:
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


def _region_to_world_mm(region, rv3d, mx, my) -> tuple[float, float] | None:
    loc = region_2d_to_location_3d(region, rv3d, (mx, my), (0.0, 0.0, 0.0))
    if loc is None:
        return None
    return geom.m_to_mm(loc.x), geom.m_to_mm(loc.y)


def _world_mm_to_region(region, rv3d, x_mm, y_mm) -> tuple[float, float] | None:
    p = location_3d_to_region_2d(
        region, rv3d, (geom.mm_to_m(x_mm), geom.mm_to_m(y_mm), 0.0)
    )
    if p is None:
        return None
    return float(p.x), float(p.y)


def _focus_active_coma(work, page_index: int, coma_index: int) -> None:
    """コマ枠編集でコマを選択したら active 階層にも反映 (薄い委譲)."""
    from ..utils import active_target as _at

    scene = bpy.context.scene if bpy.context is not None else None
    _at.focus_active_coma(scene, work, page_index, coma_index)


# 旧 _refresh_coma_masks_for_pages はリファクタで撤去。
# coma 形状の mask Mesh への追従は core/coma.py の rect_*_mm / vertices
# update callback 経由で coma_plane Mesh が自動的に行う。


def _coma_polygon(panel) -> list[tuple[float, float]]:
    if panel.shape_type == "rect":
        x, y = panel.rect_x_mm, panel.rect_y_mm
        w, h = panel.rect_width_mm, panel.rect_height_mm
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    if panel.shape_type == "polygon":
        return [(v.x_mm, v.y_mm) for v in panel.vertices]
    return []


def _set_coma_polygon(panel, poly: list[tuple[float, float]]) -> None:
    panel.shape_type = "polygon"
    panel.vertices.clear()
    for x, y in poly:
        v = panel.vertices.add()
        v.x_mm = float(x)
        v.y_mm = float(y)
    if poly:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        panel.rect_x_mm = min(xs)
        panel.rect_y_mm = min(ys)
        panel.rect_width_mm = max(xs) - min(xs)
        panel.rect_height_mm = max(ys) - min(ys)


def _distance_point_to_segment(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> tuple[float, float]:
    """点 p から線分 a-b への距離と、線分上の最近点パラメータ t を返す."""
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    L_sq = dx * dx + dy * dy
    if L_sq < 1e-12:
        return math.hypot(p[0] - a[0], p[1] - a[1]), 0.0
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L_sq))
    cx = a[0] + t * dx
    cy = a[1] + t * dy
    return math.hypot(p[0] - cx, p[1] - cy), t


def _line_intersect(
    p1: tuple[float, float], p2: tuple[float, float],
    p3: tuple[float, float], p4: tuple[float, float],
    fallback: tuple[float, float],
) -> tuple[float, float]:
    """直線 p1-p2 と直線 p3-p4 の交点を返す (ほぼ平行なら fallback)."""
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-9:
        return fallback
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


# drag 中のスナップしきい値 (mm)
DRAG_SNAP_TOL_MM = 1.5
VERTEX_DIRECTION_SNAP_TOL_MM = 4.0
MIN_PANEL_AREA_MM2 = 0.01


def _snap_drag_line(
    work, page, coma_idx: int,
    a_new: tuple[float, float], b_new: tuple[float, float],
    tx: float, ty: float, nx: float, ny: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """drag 中の new line を「隣接コマ辺 / 基本枠 / 裁ち落とし枠 1mm 外側」に snap.

    平行な target line のみ対象。最寄り target との法線方向距離が
    ``DRAG_SNAP_TOL_MM`` 以下なら、その line に乗るよう法線方向に追加シフトする。
    元の角度は維持される。
    """
    from ..utils.geom import bleed_rect, inner_frame_rect
    paper = work.paper
    br = bleed_rect(paper)
    ifr = inner_frame_rect(paper)

    targets: list[tuple[tuple[float, float], tuple[float, float]]] = []
    # 裁ち落とし枠の 1mm 外側 (4 辺)
    targets.extend([
        ((br.x - 1.0, br.y - 1.0), (br.x2 + 1.0, br.y - 1.0)),
        ((br.x2 + 1.0, br.y - 1.0), (br.x2 + 1.0, br.y2 + 1.0)),
        ((br.x2 + 1.0, br.y2 + 1.0), (br.x - 1.0, br.y2 + 1.0)),
        ((br.x - 1.0, br.y2 + 1.0), (br.x - 1.0, br.y - 1.0)),
    ])
    # 基本枠の 4 辺
    targets.extend([
        ((ifr.x, ifr.y), (ifr.x2, ifr.y)),
        ((ifr.x2, ifr.y), (ifr.x2, ifr.y2)),
        ((ifr.x2, ifr.y2), (ifr.x, ifr.y2)),
        ((ifr.x, ifr.y2), (ifr.x, ifr.y)),
    ])
    # 同ページの他コマの辺
    for panel_i2, p2 in enumerate(page.comas):
        if panel_i2 == coma_idx:
            continue
        poly2 = _coma_polygon(p2)
        for ei2 in range(len(poly2)):
            targets.append(
                (poly2[ei2], poly2[(ei2 + 1) % len(poly2)])
            )

    new_mid = ((a_new[0] + b_new[0]) * 0.5, (a_new[1] + b_new[1]) * 0.5)
    best_dist = DRAG_SNAP_TOL_MM
    best_offset = 0.0
    for ta, tb in targets:
        ux2 = tb[0] - ta[0]
        uy2 = tb[1] - ta[1]
        l2 = math.hypot(ux2, uy2)
        if l2 < 1e-6:
            continue
        # 平行性 (target が new と平行)
        dot = (ux2 / l2) * tx + (uy2 / l2) * ty
        if abs(abs(dot) - 1.0) > 0.05:
            continue
        # 法線方向の符号付き距離 (target ← new)
        tmid = ((ta[0] + tb[0]) * 0.5, (ta[1] + tb[1]) * 0.5)
        d = (tmid[0] - new_mid[0]) * nx + (tmid[1] - new_mid[1]) * ny
        if abs(d) < best_dist:
            best_dist = abs(d)
            best_offset = d

    if best_dist >= DRAG_SNAP_TOL_MM:
        return a_new, b_new

    # snap: 法線方向に best_offset だけ追加シフト
    sx = best_offset * nx
    sy = best_offset * ny
    return (a_new[0] + sx, a_new[1] + sy), (b_new[0] + sx, b_new[1] + sy)


def _build_shifted_edge_polygon(
    poly: list[tuple[float, float]],
    edge_idx: int,
    a_new_line: tuple[float, float],
    b_new_line: tuple[float, float],
) -> list[tuple[float, float]] | None:
    n = len(poly)
    if n < 3:
        return None
    a = poly[edge_idx]
    b = poly[(edge_idx + 1) % n]
    prev_idx = (edge_idx - 1 + n) % n
    next_idx = (edge_idx + 2) % n
    a_prev = poly[prev_idx]
    b_next = poly[next_idx]
    new_a = _line_intersect(a_prev, a, a_new_line, b_new_line, fallback=a_new_line)
    new_b = _line_intersect(b, b_next, a_new_line, b_new_line, fallback=b_new_line)
    new_poly = list(poly)
    new_poly[edge_idx] = new_a
    new_poly[(edge_idx + 1) % n] = new_b
    return new_poly


def _is_valid_coma_polygon(
    poly: list[tuple[float, float]],
    *,
    reference_poly: list[tuple[float, float]] | None = None,
) -> bool:
    area = polygon_geom.signed_polygon_area(poly)
    if abs(area) < MIN_PANEL_AREA_MM2:
        return False
    if not polygon_geom.is_simple_polygon(poly):
        return False
    if reference_poly is None:
        return True
    ref_area = polygon_geom.signed_polygon_area(reference_poly)
    if abs(ref_area) < MIN_PANEL_AREA_MM2:
        return True
    return area * ref_area > 0.0


# ---------- 隣接 panel との連動 ----------


def _all_coma_edges_world(
    work,
    *,
    context=None,
    area=None,
) -> list[tuple[int, int, int, tuple[float, float], tuple[float, float]]]:
    """全ページの全 panel の全 edge を world (mm) 座標で返す.

    返値: [(page_idx, coma_idx, edge_idx, (x1,y1), (x2,y2)), ...]
    """
    scene = getattr(context, "scene", None) or bpy.context.scene
    cols = max(1, int(getattr(scene, "bmanga_overview_cols", 4)))
    gap_x, gap_y = page_grid.resolve_gap_mm(scene)
    cw = work.paper.canvas_width_mm
    ch = work.paper.canvas_height_mm
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")

    out: list = []
    for pi, page in enumerate(work.pages):
        if not page_range.page_in_range(page):
            continue
        if area is not None:
            ox, oy = _page_offset_for_area(context or bpy.context, work, area, pi)
        else:
            ox, oy = page_grid.page_grid_offset_mm(
                pi, cols, gap_x, cw, ch, start_side, read_direction,
                work=work, gap_y_mm=gap_y,
            )
            add_x, add_y = page_grid.page_manual_offset_mm(page)
            ox += add_x
            oy += add_y
        for panel_i, panel in enumerate(page.comas):
            poly = _coma_polygon(panel)
            if len(poly) < 2:
                continue
            for ei in range(len(poly)):
                a = (poly[ei][0] + ox, poly[ei][1] + oy)
                b = (poly[(ei + 1) % len(poly)][0] + ox, poly[(ei + 1) % len(poly)][1] + oy)
                out.append((pi, panel_i, ei, a, b))
    return out


def _page_offset(work, page_idx: int) -> tuple[float, float]:
    scene = bpy.context.scene
    cols = max(1, int(getattr(scene, "bmanga_overview_cols", 4)))
    gap_x, gap_y = page_grid.resolve_gap_mm(scene)
    cw = work.paper.canvas_width_mm
    ch = work.paper.canvas_height_mm
    start_side = getattr(work.paper, "start_side", "right")
    read_direction = getattr(work.paper, "read_direction", "left")
    ox, oy = page_grid.page_grid_offset_mm(
        page_idx, cols, gap_x, cw, ch, start_side, read_direction,
        work=work, gap_y_mm=gap_y,
    )
    if 0 <= page_idx < len(work.pages):
        add_x, add_y = page_grid.page_manual_offset_mm(work.pages[page_idx])
        ox += add_x
        oy += add_y
    return ox, oy


def _gap_for_edge(work, panel, edge: tuple[tuple[float, float], tuple[float, float]]) -> float:
    """edge の方向に応じた gap (mm) を返す (knife_cut の _effective_gap_mm と同じ規則)."""
    a, b = edge
    pgv = float(getattr(panel, "coma_gap_vertical_mm", -1.0))
    pgh = float(getattr(panel, "coma_gap_horizontal_mm", -1.0))
    gap_v = pgv if pgv >= 0.0 else float(work.coma_gap.vertical_mm)
    gap_h = pgh if pgh >= 0.0 else float(work.coma_gap.horizontal_mm)
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    # 辺が水平に近ければ → 上下スキマ (gap_v)、垂直に近ければ → 左右スキマ (gap_h)
    if abs(dx) >= abs(dy):
        return gap_v
    return gap_h


def _border_width_for_panel(panel) -> float:
    border = getattr(panel, "border", None)
    try:
        return max(0.0, float(getattr(border, "width_mm", 0.0) or 0.0))
    except Exception:  # noqa: BLE001
        return 0.0


def _finish_outer_offset_for_panel(panel) -> float:
    # 枠線は輪郭線を中心に描かれるため、仕上がり枠から線幅分外へ出すには
    # 中心線を半分だけ外へ置く。
    return _border_width_for_panel(panel) * 0.5


def _find_adjacent_edges(
    work, page_idx: int, coma_idx: int, edge_idx: int,
) -> list[tuple[int, int, int]]:
    """対象 edge と隣接 (= ほぼ平行で gap 距離内、重なる) する全 edge を返す.

    返値: [(page_idx, coma_idx, edge_idx), ...] (自分自身は含まない)
    """
    page = work.pages[page_idx]
    panel = page.comas[coma_idx]
    poly = _coma_polygon(panel)
    if len(poly) < 2:
        return []
    pox, poy = _page_offset(work, page_idx)
    a = (poly[edge_idx][0] + pox, poly[edge_idx][1] + poy)
    b = (poly[(edge_idx + 1) % len(poly)][0] + pox, poly[(edge_idx + 1) % len(poly)][1] + poy)
    edge_len = math.hypot(b[0] - a[0], b[1] - a[1])
    if edge_len < 1e-6:
        return []
    ux = (b[0] - a[0]) / edge_len
    uy = (b[1] - a[1]) / edge_len
    nx = -uy
    ny = ux
    target_gap = _gap_for_edge(work, panel, (a, b))

    adj: list[tuple[int, int, int]] = []
    for entry in _all_coma_edges_world(work):
        pi2, panel_i2, ei2, a2, b2 = entry
        # 同じ panel 内の他 edge は除外 (細い panel の対辺が偶然 gap 距離だと
        # 連動して panel が反転するバグを防ぐ)
        if (pi2, panel_i2) == (page_idx, coma_idx):
            continue
        # 平行性: 単位ベクトルの内積が ±1 に近い
        l2 = math.hypot(b2[0] - a2[0], b2[1] - a2[1])
        if l2 < 1e-6:
            continue
        ux2 = (b2[0] - a2[0]) / l2
        uy2 = (b2[1] - a2[1]) / l2
        dot = ux * ux2 + uy * uy2
        if abs(abs(dot) - 1.0) > 0.05:  # 約 ±18° 以上の角度差は不適
            continue
        # 法線距離: a2 から自分の line への符号付き距離
        d = (a2[0] - a[0]) * nx + (a2[1] - a[1]) * ny
        if abs(abs(d) - target_gap) > ADJACENCY_GAP_TOLERANCE_MM:
            continue
        # 重なり: a2, b2 を自分の line 接線上に投影し、[0, edge_len] と交わる比率
        t1 = ((a2[0] - a[0]) * ux + (a2[1] - a[1]) * uy)
        t2 = ((b2[0] - a[0]) * ux + (b2[1] - a[1]) * uy)
        lo = max(0.0, min(t1, t2))
        hi = min(edge_len, max(t1, t2))
        overlap = max(0.0, hi - lo)
        if overlap < ADJACENCY_OVERLAP_RATIO * min(edge_len, l2):
            continue
        adj.append((pi2, panel_i2, ei2))
    return adj


def _find_overlapping_coma_edges(
    page, coma_idx: int, edge_idx: int,
    *,
    max_distance_mm: float = 0.05,
    min_overlap_ratio: float = 0.8,
) -> list[tuple[int, int]]:
    """対象 edge とほぼ同一直線上で大きく重なる他 panel edge を返す.

    ▲ハンドルの「gap を空ける」特殊ケース専用。
    「近くに平行な線がある」だけではなく、現在の枠線が実際に隣接コマと
    同じ継ぎ目を共有している場合にだけ反応させたいので、距離 0 近傍かつ
    十分な重なり率を要求する。
    """
    panel = page.comas[coma_idx]
    poly = _coma_polygon(panel)
    if len(poly) < 2:
        return []
    a = poly[edge_idx]
    b = poly[(edge_idx + 1) % len(poly)]
    edge_len = math.hypot(b[0] - a[0], b[1] - a[1])
    if edge_len < 1e-6:
        return []
    ux = (b[0] - a[0]) / edge_len
    uy = (b[1] - a[1]) / edge_len
    nx = -uy
    ny = ux

    overlaps: list[tuple[int, int]] = []
    for panel_i2, p2 in enumerate(page.comas):
        if panel_i2 == coma_idx:
            continue
        poly2 = _coma_polygon(p2)
        for ei2 in range(len(poly2)):
            a2 = poly2[ei2]
            b2 = poly2[(ei2 + 1) % len(poly2)]
            l2 = math.hypot(b2[0] - a2[0], b2[1] - a2[1])
            if l2 < 1e-6:
                continue
            ux2 = (b2[0] - a2[0]) / l2
            uy2 = (b2[1] - a2[1]) / l2
            dot = ux * ux2 + uy * uy2
            if abs(abs(dot) - 1.0) > 0.05:
                continue
            d = (a2[0] - a[0]) * nx + (a2[1] - a[1]) * ny
            if abs(d) > max_distance_mm:
                continue
            t1 = ((a2[0] - a[0]) * ux + (a2[1] - a[1]) * uy)
            t2 = ((b2[0] - a[0]) * ux + (b2[1] - a[1]) * uy)
            lo = max(0.0, min(t1, t2))
            hi = min(edge_len, max(t1, t2))
            overlap = max(0.0, hi - lo)
            if overlap < min(edge_len, l2) * min_overlap_ratio:
                continue
            overlaps.append((panel_i2, ei2))
    return overlaps


# ---------- ピック ----------


def _pick_edge_or_vertex(
    work, region, rv3d, mx: int, my: int, *, context=None, area=None,
) -> Optional[dict]:
    """画面 (mx, my) 直下の最寄り辺 or 頂点を返す.

    返値: {"type": "edge" or "vertex",
           "page": pi, "coma": panel_i,
           "edge": ei (edge type only),
           "vertex": vi (vertex type only)}
    """
    best: Optional[dict] = None
    best_dist = float("inf")

    # 頂点を優先 (辺より priority 高く判定)
    for entry in _all_coma_edges_world(work, context=context, area=area):
        pi, panel_i, ei, a, b = entry
        # 各 edge の始点を vertex として
        ap = _world_mm_to_region(region, rv3d, a[0], a[1])
        if ap is None:
            continue
        d = math.hypot(ap[0] - mx, ap[1] - my)
        if d < VERTEX_PICK_TOLERANCE_PX and d < best_dist:
            best = {
                "type": "vertex",
                "page": pi, "coma": panel_i, "vertex": ei,
            }
            best_dist = d

    if best is not None:
        return best

    # 辺
    for entry in _all_coma_edges_world(work, context=context, area=area):
        pi, panel_i, ei, a, b = entry
        ap = _world_mm_to_region(region, rv3d, a[0], a[1])
        bp = _world_mm_to_region(region, rv3d, b[0], b[1])
        if ap is None or bp is None:
            continue
        d, t = _distance_point_to_segment((mx, my), ap, bp)
        if d < EDGE_PICK_TOLERANCE_PX and d < best_dist:
            best = {
                "type": "edge",
                "page": pi, "coma": panel_i, "edge": ei,
            }
            best_dist = d
    return best


# ---------- ハンドル ----------


def _compute_handle_centers_px(
    region, rv3d, edge_a_mm: tuple[float, float], edge_b_mm: tuple[float, float],
) -> tuple[Optional[tuple[float, float]], Optional[tuple[float, float]]] | None:
    """辺中点から法線方向 ±HANDLE_OFFSET_PX に三角ハンドル 2 つの中心を返す."""
    ap = _world_mm_to_region(region, rv3d, *edge_a_mm)
    bp = _world_mm_to_region(region, rv3d, *edge_b_mm)
    if ap is None or bp is None:
        return None, None
    mx = (ap[0] + bp[0]) * 0.5
    my = (ap[1] + bp[1]) * 0.5
    dx = bp[0] - ap[0]
    dy = bp[1] - ap[1]
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return None, None
    nx = -dy / L  # 画面上の法線
    ny = dx / L
    h1 = (mx + nx * HANDLE_OFFSET_PX, my + ny * HANDLE_OFFSET_PX)
    h2 = (mx - nx * HANDLE_OFFSET_PX, my - ny * HANDLE_OFFSET_PX)
    return h1, h2


def _event_view_context(context, event):
    return view_event_region.view3d_window_under_event(context, event)


def _page_offset_for_area(context, work, area, page_index: int) -> tuple[float, float]:
    scene = getattr(context, "scene", None)
    if page_browser.is_marked_area(area) or page_browser.page_browser_area(context) == area:
        if page_browser.fit_enabled(scene):
            return page_browser.page_offset_mm(work, scene, area, page_index)
    return _page_offset(work, page_index)


def _iter_selection_edge_refs(work, selection: dict | None):
    """選択中の辺/枠線を yield する (描画用).

    描画では kind による narrowness を尊重 (edge 選択中は 1 辺、 border は
    全辺)。 ▲ ハンドルの hit テストには ``_iter_panel_edge_refs_for_handles``
    を使うこと。
    """
    if selection is None:
        return
    kind = selection.get("type")
    if kind not in {"edge", "border"}:
        return
    page_index = int(selection.get("page", -1))
    coma_index = int(selection.get("coma", -1))
    if not (0 <= page_index < len(work.pages)):
        return
    page = work.pages[page_index]
    if not page_range.page_in_range(page):
        return
    if not (0 <= coma_index < len(page.comas)):
        return
    panel = page.comas[coma_index]
    poly = _coma_polygon(panel)
    if len(poly) < 2:
        return
    if kind == "edge":
        edge_index = int(selection.get("edge", -1))
        if 0 <= edge_index < len(poly):
            yield page_index, coma_index, edge_index, poly[edge_index], poly[(edge_index + 1) % len(poly)]
        return
    for edge_index in range(len(poly)):
        yield page_index, coma_index, edge_index, poly[edge_index], poly[(edge_index + 1) % len(poly)]


def _iter_panel_edge_refs_for_handles(work, selection: dict | None):
    """▲ ハンドル hit テスト用: 選択中の (page, coma) の **全 4 辺** を yield する.

    描画用 ``_iter_selection_edge_refs`` と異なり、 kind が ``edge`` (= 1 辺
    だけ選択) でも、 vertex でも、 panel 全 4 辺の ▲ をクリック対象にする。
    これにより ``edge`` 状態のまま連続して別辺の ▲ をクリックできる
    (枠線カットツールでの ``_pick_edge_or_vertex`` 直後など)。
    """
    if selection is None:
        return
    kind = selection.get("type")
    if kind not in {"edge", "border", "vertex"}:
        return
    page_index = int(selection.get("page", -1))
    coma_index = int(selection.get("coma", -1))
    if not (0 <= page_index < len(work.pages)):
        return
    page = work.pages[page_index]
    if not page_range.page_in_range(page):
        return
    if not (0 <= coma_index < len(page.comas)):
        return
    panel = page.comas[coma_index]
    poly = _coma_polygon(panel)
    if len(poly) < 2:
        return
    for edge_index in range(len(poly)):
        yield page_index, coma_index, edge_index, poly[edge_index], poly[(edge_index + 1) % len(poly)]


def _hit_selection_handle(
    context,
    work,
    selection: dict | None,
    area,
    region,
    rv3d,
    mx: int,
    my: int,
) -> dict | None:
    best: dict | None = None
    best_dist = HANDLE_HIT_RADIUS_PX
    for page_index, coma_index, edge_index, a, b in _iter_panel_edge_refs_for_handles(work, selection):
        ox, oy = _page_offset_for_area(context, work, area, page_index)
        edge_a = (a[0] + ox, a[1] + oy)
        edge_b = (b[0] + ox, b[1] + oy)
        h1, h2 = _compute_handle_centers_px(region, rv3d, edge_a, edge_b) or (None, None)
        for direction, handle in ((1, h1), (2, h2)):
            if handle is None:
                continue
            dist = math.hypot(handle[0] - mx, handle[1] - my)
            if dist <= best_dist:
                best_dist = dist
                best = {
                    "page": page_index,
                    "coma": coma_index,
                    "edge": edge_index,
                    "direction": direction,
                }
    return best


def find_selected_handle_at_event(context, event) -> dict | None:
    """現在の枠線/コマ選択から ▲ ハンドルをイベント位置で解決する.

    解決の優先順位:
    1. WM の ``bmanga_edge_select_*`` (kind: edge / border / vertex)
    2. ``page.active_coma_index`` (専用ツールを通っていなくても、
       オブジェクトツールやアウトライナーでコマがアクティブなら ▲ を反応させる)

    どの kind でも hit テストは ``_iter_panel_edge_refs_for_handles`` を経由
    して **コマ全 4 辺の ▲** を走査するため、 1 度 ▲ を押したあと kind が
    edge / vertex に変わっていても連続して別辺の ▲ をクリックできる。
    """
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return None
    wm = getattr(context, "window_manager", None)
    if wm is None:
        return None
    view = _event_view_context(context, event)
    if view is None:
        return None
    area, region, rv3d, mx, my = view

    candidates: list[dict] = []
    kind = getattr(wm, "bmanga_edge_select_kind", "none")
    page_index = int(getattr(wm, "bmanga_edge_select_page", -1))
    coma_index = int(getattr(wm, "bmanga_edge_select_coma", -1))
    if kind in {"edge", "border", "vertex"} and 0 <= page_index < len(work.pages):
        page_obj = work.pages[page_index]
        comas = list(getattr(page_obj, "comas", []) or [])
        if 0 <= coma_index < len(comas):
            candidates.append(
                {
                    "type": kind if kind in {"edge", "border", "vertex"} else "border",
                    "page": page_index,
                    "coma": coma_index,
                    "edge": int(getattr(wm, "bmanga_edge_select_edge", -1)),
                }
            )
    # フォールバック: page.active_coma_index (オブジェクトツール / アウト
    # ライナー / レイヤースタック でコマを active にしただけのケース)
    try:
        active_page_index = int(getattr(work, "active_page_index", -1))
        if 0 <= active_page_index < len(work.pages):
            page_obj = work.pages[active_page_index]
            active_coma_index = int(getattr(page_obj, "active_coma_index", -1))
            comas = list(getattr(page_obj, "comas", []) or [])
            if 0 <= active_coma_index < len(comas):
                already_listed = any(
                    int(c.get("page", -1)) == active_page_index
                    and int(c.get("coma", -1)) == active_coma_index
                    for c in candidates
                )
                if not already_listed:
                    candidates.append(
                        {
                            "type": "border",
                            "page": active_page_index,
                            "coma": active_coma_index,
                        }
                    )
    except Exception:  # noqa: BLE001
        pass

    for selection in candidates:
        hit = _hit_selection_handle(
            context, work, selection, area, region, rv3d, mx, my
        )
        if hit is not None:
            return hit
    return None


# ---------- Modal Operator ----------


class BMANGA_OT_coma_edge_move(Operator):
    """コマ枠編集の内部オペレーター: 辺/頂点を選択 → ドラッグ移動 + 色/太さ編集.

    シングルクリックで辺、ダブルクリックで枠線全体を選択する。
    """

    bl_idname = "bmanga.coma_edge_move"
    bl_label = "コマ枠編集（内部）"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return work is not None and work.loaded

    def invoke(self, context, event):
        target = _find_view3d(context)
        if target is None:
            return {"PASS_THROUGH"}
        if coma_modal_state.get_active("edge_move") is not None:
            return {"FINISHED"}
        coma_modal_state.exit_drawing_mode(context)
        coma_modal_state.finish_active("coma_vertex_edit", context, keep_selection=True)
        coma_modal_state.finish_active("knife_cut", context, keep_selection=False)
        coma_modal_state.finish_active("layer_move", context, keep_selection=False)
        coma_modal_state.finish_active("balloon_tool", context, keep_selection=True)
        coma_modal_state.finish_active("text_tool", context, keep_selection=True)
        coma_modal_state.finish_active("effect_line_tool", context, keep_selection=True)
        coma_modal_state.finish_active("balloon_tail_tool", context, keep_selection=True)
        coma_modal_state.finish_active("balloon_nurbs_tool", context, keep_selection=True)
        self._area, self._region, self._rv3d = target
        self._work = get_work(context)
        if self._work is None or not self._work.loaded:
            return {"CANCELLED"}

        # 状態
        self._selection: Optional[dict] = None  # {"type":..., "page":..., ...}
        self._dragging = False
        self._drag_moved = False
        self._drag_start_world: Optional[tuple[float, float]] = None
        self._original_geometry: Optional[dict] = None
        self._externally_finished = False
        self._navigation_drag_passthrough = False
        self._cursor_modal_set = False
        # シングル/ダブルクリック判定用
        self._last_press_time = 0.0
        self._last_press_edge: Optional[tuple[int, int, int]] = None

        self._draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, (self,), "WINDOW", "POST_PIXEL"
        )
        context.window_manager.modal_handler_add(self)
        self._cursor_modal_set = coma_modal_state.set_modal_cursor(context, "CROSSHAIR")
        coma_modal_state.set_active("edge_move", self, context)
        self._update_wm_selection(context)
        self._tag_redraw()
        self.report(
            {"INFO"},
            "枠線選択: 辺=シングル / 枠線全体=ダブル | ドラッグで移動 | ESC 終了",
        )
        return {"RUNNING_MODAL"}

    def _update_wm_selection(self, context) -> None:
        """WindowManager のグローバル選択状態を更新 (N パネル UI が読む)."""
        sel = self._selection
        if sel is None:
            edge_selection.clear_selection(context)
            return
        t = sel.get("type")
        page_index = int(sel.get("page", -1))
        coma_index = int(sel.get("coma", -1))
        if t == "edge":
            edge_selection.set_selection(
                context,
                "edge",
                page_index=page_index,
                coma_index=coma_index,
                edge_index=int(sel.get("edge", -1)),
            )
        elif t == "border":
            edge_selection.set_selection(
                context,
                "border",
                page_index=page_index,
                coma_index=coma_index,
            )
        elif t == "vertex":
            edge_selection.set_selection(
                context,
                "vertex",
                page_index=page_index,
                coma_index=coma_index,
                vertex_index=int(sel.get("vertex", -1)),
                vertex_indices=sel.get("vertices"),
            )
        else:
            edge_selection.clear_selection(context)

    def _to_window(self, ev):
        return ev.mouse_x - self._region.x, ev.mouse_y - self._region.y

    def _region_at_mouse(self, ev):
        for region in self._area.regions:
            if (
                region.x <= ev.mouse_x < region.x + region.width
                and region.y <= ev.mouse_y < region.y + region.height
            ):
                return region
        return None

    def _is_inside_region(self, ev) -> bool:
        mouse_x = int(getattr(ev, "mouse_x", -10_000_000))
        mouse_y = int(getattr(ev, "mouse_y", -10_000_000))
        for region in self._area.regions:
            if region.type == "WINDOW":
                continue
            if (
                region.x <= mouse_x < region.x + region.width
                and region.y <= mouse_y < region.y + region.height
            ):
                return False
        region = self._region
        return (
            region.x <= mouse_x < region.x + region.width
            and region.y <= mouse_y < region.y + region.height
        )

    def _is_over_navigation_gizmo(self, ev) -> bool:
        return view_event_region.is_view3d_navigation_ui_event(bpy.context, ev)

    def _tag_redraw(self) -> None:
        if self._region is not None:
            self._region.tag_redraw()

    def _cleanup(self, context=None) -> None:
        if getattr(self, "_cursor_modal_set", False):
            coma_modal_state.restore_modal_cursor(context)
            self._cursor_modal_set = False
        h = getattr(self, "_draw_handler", None)
        if h is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(h, "WINDOW")
            except Exception:  # noqa: BLE001
                pass
            self._draw_handler = None
        self._tag_redraw()

    def finish_from_external(self, context, *, keep_selection: bool) -> None:
        if getattr(self, "_externally_finished", False):
            return
        self._externally_finished = True
        if not keep_selection:
            self._selection = None
        self._cleanup(context)
        try:
            self._update_wm_selection(context)
        except Exception:  # noqa: BLE001
            pass
        edge_selection.clear_overlay_pointer(context)
        coma_modal_state.clear_active("edge_move", self, context)

    def _push_undo_step(self, message: str) -> None:
        """modal 中の 1 操作を独立した undo step として記録."""
        try:
            bpy.ops.ed.undo_push(message=message)
        except Exception:  # noqa: BLE001
            _logger.exception("edge_move: undo_push failed")

    def modal(self, context, event):
        if getattr(self, "_externally_finished", False):
            coma_modal_state.clear_active("edge_move", self, context)
            return {"FINISHED", "PASS_THROUGH"}
        from . import handle_intercept
        if handle_intercept.is_dragging(self):
            if event.type == "MOUSEMOVE":
                handle_intercept.update_drag(context, event, self)
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                handle_intercept.finish_drag(context, event, self)
                return {"RUNNING_MODAL"}
            if event.type == "ESC" and event.value == "PRESS":
                handle_intercept.cancel_drag(context, self)
                return {"RUNNING_MODAL"}
            return {"RUNNING_MODAL"}
        if view_event_region.toggle_modal_sidebar_if_requested(context, event):
            return {"RUNNING_MODAL"}
        if getattr(self, "_navigation_drag_passthrough", False):
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                self._navigation_drag_passthrough = False
            return {"PASS_THROUGH"}
        # Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y (undo / redo) はモーダルが保持する
        # PropertyGroup 参照を stale 化させて C レベル crash を起こすため、
        # 検知したら即座に modal を終了して event を本来の undo に譲る。
        if event.value == "PRESS" and event.type in {"Z", "Y"} and event.ctrl:
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED", "PASS_THROUGH"}

        if (
            event.value == "PRESS"
            and event.type == "F"
            and not event.ctrl
            and not event.alt
            and not event.shift
        ):
            if not self._is_inside_region(event):
                return {"PASS_THROUGH"}
            self.finish_from_external(context, keep_selection=True)
            try:
                with context.temp_override(area=self._area, region=self._region):
                    bpy.ops.bmanga.coma_knife_cut("INVOKE_DEFAULT")
            except Exception:  # noqa: BLE001
                _logger.exception("edge_move: failed to switch to knife_cut")
            return {"FINISHED"}

        if (
            event.value == "PRESS"
            and event.type == "K"
            and not event.ctrl
            and not event.alt
            and not event.shift
        ):
            if not self._is_inside_region(event):
                return {"PASS_THROUGH"}
            self.finish_from_external(context, keep_selection=True)
            try:
                with context.temp_override(area=self._area, region=self._region):
                    bpy.ops.bmanga.layer_move_tool("INVOKE_DEFAULT")
            except Exception:  # noqa: BLE001
                _logger.exception("edge_move: failed to switch to layer_move")
            return {"FINISHED"}

        # B-MANGA の他ツール/モード切替ショートカットで modal を終了して譲る
        if (
            event.value == "PRESS"
            and event.type in {"O", "P", "COMMA", "PERIOD", "Z", "X"}
            and not event.ctrl
            and not event.alt
        ):
            if not self._is_inside_region(event):
                return {"PASS_THROUGH"}
            self.finish_from_external(context, keep_selection=True)
            return {"FINISHED", "PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            # ▲ ハンドル hover ハイライト用にカーソル位置を WM に記録
            if self._region is not None:
                edge_selection.update_overlay_pointer(context, self._region, event)
            if not self._dragging and self._is_over_navigation_gizmo(event):
                return {"PASS_THROUGH"}
            if not self._dragging and not self._is_inside_region(event):
                return {"PASS_THROUGH"}
            if self._dragging and self._selection is not None:
                self._apply_drag(event)
                self._tag_redraw()
            else:
                self._tag_redraw()  # ▲ hover の再描画
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                if self._is_over_navigation_gizmo(event):
                    self._navigation_drag_passthrough = True
                    return {"PASS_THROUGH"}
                if not self._is_inside_region(event):
                    return {"PASS_THROUGH"}
                if handle_intercept.try_intercept_press(context, event, self):
                    return {"RUNNING_MODAL"}
                mx, my = self._to_window(event)
                # 既存選択の▲ハンドルを、辺/枠線全体選択のどちらでも最優先で拾う。
                handle_hit = _hit_selection_handle(
                    context,
                    self._work,
                    self._selection,
                    self._area,
                    self._region,
                    self._rv3d,
                    mx,
                    my,
                )
                if handle_hit is not None:
                    # 拡張前の selection kind を保持。 ``_do_extend`` 後に
                    # border 状態へ戻すことで、 連続して別辺の ▲ ハンドルを
                    # クリックできるようにする (border のままだと 4 辺すべての
                    # ▲ が表示されるが、 edge に絞ったままだと 1 辺の ▲ しか
                    # hit テストされず 2 回目以降が反応しない問題を回避)。
                    pre_selection_snapshot = (
                        dict(self._selection) if isinstance(self._selection, dict) else None
                    )
                    self._selection = {
                        "type": "edge",
                        "page": int(handle_hit["page"]),
                        "coma": int(handle_hit["coma"]),
                        "edge": int(handle_hit["edge"]),
                    }
                    self._update_wm_selection(context)
                    self._do_extend(int(handle_hit["direction"]))
                    # 元が border 選択 (= コマ全体) なら border に戻す
                    if (
                        pre_selection_snapshot is not None
                        and pre_selection_snapshot.get("type") == "border"
                        and int(pre_selection_snapshot.get("coma", -1)) == int(handle_hit["coma"])
                        and int(pre_selection_snapshot.get("page", -1)) == int(handle_hit["page"])
                    ):
                        self._selection = {
                            "type": "border",
                            "page": int(handle_hit["page"]),
                            "coma": int(handle_hit["coma"]),
                        }
                        self._update_wm_selection(context)
                    self._tag_redraw()
                    return {"RUNNING_MODAL"}
                # 新規ピック
                hit = _pick_edge_or_vertex(
                    self._work,
                    self._region,
                    self._rv3d,
                    mx,
                    my,
                    context=context,
                    area=self._area,
                )
                now = time.time()
                if hit is None:
                    self._selection = None
                    self._dragging = False
                    self._drag_moved = False
                    self._last_press_time = 0.0
                    self._last_press_edge = None
                    if not (event.ctrl or event.shift):
                        object_selection.clear(context)
                elif hit.get("type") == "edge":
                    page_for_hit = self._work.pages[int(hit["page"])]
                    panel_for_hit = page_for_hit.comas[int(hit["coma"])]
                    if event.ctrl or event.shift:
                        self._selection = hit
                        self._dragging = False
                        self._drag_moved = False
                        self._last_press_time = 0.0
                        self._last_press_edge = None
                        mode = "toggle" if event.ctrl else "add"
                        object_selection.select_key(
                            context,
                            object_selection.coma_key(page_for_hit, panel_for_hit),
                            mode=mode,
                        )
                        self._update_wm_selection(context)
                        self._tag_redraw()
                        return {"RUNNING_MODAL"}
                    edge_key = (hit["page"], hit["coma"], hit["edge"])
                    is_double = (
                        self._last_press_edge == edge_key
                        and (now - self._last_press_time) < DOUBLE_CLICK_INTERVAL
                    )
                    if is_double:
                        # ダブルクリック → 枠線全体 (panel 単位) を選択
                        self._selection = {
                            "type": "border",
                            "page": hit["page"],
                            "coma": hit["coma"],
                        }
                        self._dragging = False
                        self._last_press_time = 0.0
                        self._last_press_edge = None
                        object_selection.select_key(
                            context,
                            object_selection.coma_key(page_for_hit, panel_for_hit),
                            mode="single",
                        )
                        _focus_active_coma(self._work, int(hit["page"]), int(hit["coma"]))
                    else:
                        # シングルクリック → 単一辺選択 + ドラッグ開始
                        self._selection = hit
                        self._dragging = True
                        self._drag_moved = False
                        self._drag_start_world = _region_to_world_mm(
                            self._region, self._rv3d, mx, my,
                        )
                        self._capture_original_geometry()
                        self._last_press_time = now
                        self._last_press_edge = edge_key
                        object_selection.select_key(
                            context,
                            object_selection.coma_key(page_for_hit, panel_for_hit),
                            mode="single",
                        )
                        _focus_active_coma(self._work, int(hit["page"]), int(hit["coma"]))
                else:
                    # vertex
                    page_for_hit = self._work.pages[int(hit["page"])]
                    panel_for_hit = page_for_hit.comas[int(hit["coma"])]
                    same_vertex_scope = (
                        isinstance(self._selection, dict)
                        and self._selection.get("type") == "vertex"
                        and int(self._selection.get("page", -1)) == int(hit["page"])
                        and int(self._selection.get("coma", -1)) == int(hit["coma"])
                    )
                    if event.ctrl or event.shift:
                        current = set(self._selection.get("vertices", [])) if same_vertex_scope else set()
                        vi = int(hit.get("vertex", -1))
                        if event.ctrl:
                            if vi in current:
                                current.remove(vi)
                            else:
                                current.add(vi)
                        else:
                            current.add(vi)
                        if not current:
                            self._selection = None
                            self._dragging = False
                        else:
                            self._selection = {
                                "type": "vertex",
                                "page": int(hit["page"]),
                                "coma": int(hit["coma"]),
                                "vertex": vi if vi in current else min(current),
                                "vertices": sorted(current),
                            }
                            self._dragging = False
                    else:
                        current = set(self._selection.get("vertices", [])) if same_vertex_scope else set()
                        vi = int(hit.get("vertex", -1))
                        if vi in current and len(current) > 1:
                            vertices = sorted(current)
                        else:
                            vertices = [vi]
                        self._selection = {
                            "type": "vertex",
                            "page": int(hit["page"]),
                            "coma": int(hit["coma"]),
                            "vertex": vi,
                            "vertices": vertices,
                        }
                        self._dragging = True
                    self._drag_moved = False
                    if self._dragging:
                        self._drag_start_world = _region_to_world_mm(
                            self._region, self._rv3d, mx, my,
                        )
                        self._capture_original_geometry()
                    mode = "toggle" if event.ctrl else "add" if event.shift else "single"
                    object_mode = "add" if (mode in {"toggle", "add"} and same_vertex_scope) else mode
                    object_selection.select_key(
                        context,
                        object_selection.coma_key(page_for_hit, panel_for_hit),
                        mode=object_mode,
                    )
                    if mode == "single":
                        _focus_active_coma(
                            self._work, int(hit["page"]), int(hit["coma"])
                        )
                    self._last_press_time = 0.0
                    self._last_press_edge = None
                self._update_wm_selection(context)
                self._tag_redraw()
                return {"RUNNING_MODAL"}
            if event.value == "RELEASE":
                if not self._dragging and not self._is_inside_region(event):
                    return {"PASS_THROUGH"}
                if not self._dragging:
                    self._tag_redraw()
                    return {"RUNNING_MODAL"}
                if self._dragging:
                    changed = self._geometry_changed()
                    moved = bool(getattr(self, "_drag_moved", False))
                    self._dragging = False
                    # 形状が実際に変わった (= ドラッグした) 場合のみ保存
                    # 単純クリック (PRESS-RELEASE) では save を走らせない
                    if changed:
                        self._save_changes()
                        self._push_undo_step("B-MANGA: 枠線移動")
                    elif not moved:
                        self._tag_redraw()
                    self._tag_redraw()
                return {"RUNNING_MODAL"}

        if event.type == "RIGHTMOUSE" and event.value == "PRESS":
            if not self._is_inside_region(event):
                return {"PASS_THROUGH"}
            if not self._dragging and selection_context_menu.open_for_coma_edge_tool(self, context, event):
                return {"RUNNING_MODAL"}
            self.finish_from_external(context, keep_selection=True)
            self.report({"INFO"}, "コマ枠編集を終了")
            return {"FINISHED"}

        if event.type in {"ESC", "RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            if not self._is_inside_region(event):
                return {"PASS_THROUGH"}
            self.finish_from_external(context, keep_selection=True)
            self.report({"INFO"}, "コマ枠編集を終了")
            return {"FINISHED"}
        return {"PASS_THROUGH"}

    # ---- 選択中の辺の world 座標 ----
    def _get_selected_edge_world(self) -> Optional[tuple[tuple[float, float], tuple[float, float]]]:
        sel = self._selection
        if sel is None or sel.get("type") != "edge":
            return None
        page = self._work.pages[sel["page"]]
        panel = page.comas[sel["coma"]]
        poly = _coma_polygon(panel)
        if len(poly) < 2:
            return None
        ei = sel["edge"]
        ox, oy = _page_offset_for_area(bpy.context, self._work, self._area, sel["page"])
        a = (poly[ei][0] + ox, poly[ei][1] + oy)
        b = (poly[(ei + 1) % len(poly)][0] + ox, poly[(ei + 1) % len(poly)][1] + oy)
        return a, b

    # ---- ドラッグ前の形状をスナップショット ----
    def _capture_original_geometry(self) -> None:
        sel = self._selection
        if sel is None:
            return
        page = self._work.pages[sel["page"]]
        panel = page.comas[sel["coma"]]
        # 自分の polygon
        snapshot = {"poly": _coma_polygon(panel)}
        # 隣接 (edge 選択時のみ): 対応 edge の vertex index を計算しておく
        if sel["type"] == "edge":
            adj = _find_adjacent_edges(
                self._work, sel["page"], sel["coma"], sel["edge"]
            )
            adj_states = []
            for pi, panel_i, ei in adj:
                p = self._work.pages[pi].comas[panel_i]
                adj_states.append(
                    {"page": pi, "coma": panel_i, "edge": ei, "poly": _coma_polygon(p)}
                )
            snapshot["adjacent_edges"] = adj_states
        elif sel["type"] == "vertex":
            # 頂点を共有する隣接 panel (位置一致 ± tolerance) を集める
            poly = _coma_polygon(panel)
            vertex_indices = coma_vertex_edit.selection_vertex_indices(sel, len(poly))
            snapshot["vertex_indices"] = vertex_indices
            vertex_adjacent_edges: list[dict] = []
            seen_edges: set[tuple[int, int, int, int]] = set()
            for vi in vertex_indices:
                captured = coma_edge_adjacency.capture_vertex_adjacent_edge_states(
                    self._work,
                    sel["page"],
                    sel["coma"],
                    vi,
                    poly,
                    page_offset_fn=_page_offset,
                    coma_polygon_fn=_coma_polygon,
                    find_adjacent_edges_fn=_find_adjacent_edges,
                    find_overlapping_edges_fn=_find_overlapping_coma_edges,
                    adjacency_gap_tolerance_mm=ADJACENCY_GAP_TOLERANCE_MM,
                    adjacency_overlap_ratio=ADJACENCY_OVERLAP_RATIO,
                )
                for state in captured:
                    key = (
                        int(state.get("selected_edge", -1)),
                        int(state.get("page", -1)),
                        int(state.get("coma", -1)),
                        int(state.get("edge", -1)),
                    )
                    if key in seen_edges:
                        continue
                    seen_edges.add(key)
                    vertex_adjacent_edges.append(state)
                vertex_adjacent_edges.extend(
                    coma_vertex_edit.capture_extended_vertex_edge_states(
                        self._work,
                        sel["page"],
                        sel["coma"],
                        vi,
                        poly,
                        seen_edges,
                        page_offset_fn=_page_offset,
                        coma_polygon_fn=_coma_polygon,
                        all_edges_world_fn=_all_coma_edges_world,
                        edge_projection_params_fn=coma_edge_adjacency.edge_projection_params,
                        gap_tolerance_mm=ADJACENCY_GAP_TOLERANCE_MM,
                    )
                )
            snapshot["vertex_adjacent_edges"] = vertex_adjacent_edges
            shared = []
            seen_shared: set[tuple[int, int, int]] = set()
            selected_self = {(sel["page"], sel["coma"], vi) for vi in vertex_indices}
            selected_world = []
            ox, oy = _page_offset(self._work, sel["page"])
            for vi in vertex_indices:
                selected_world.append((vi, (poly[vi][0] + ox, poly[vi][1] + oy)))
            for pi, page2 in enumerate(self._work.pages):
                if not page_range.page_in_range(page2):
                    continue
                ox2, oy2 = _page_offset(self._work, pi)
                for panel_i, p in enumerate(page2.comas):
                    poly2 = _coma_polygon(p)
                    for vi2 in range(len(poly2)):
                        if (pi, panel_i, vi2) in selected_self:
                            continue
                        wp = (poly2[vi2][0] + ox2, poly2[vi2][1] + oy2)
                        for source_vi, v_world in selected_world:
                            if math.hypot(wp[0] - v_world[0], wp[1] - v_world[1]) < ADJACENCY_GAP_TOLERANCE_MM * 5:
                                key = (pi, panel_i, vi2)
                                if key in seen_shared:
                                    continue
                                seen_shared.add(key)
                                shared.append({
                                    "page": pi, "coma": panel_i, "vertex": vi2,
                                    "source_vertex": source_vi,
                                    "poly": poly2,
                                })
                                break
            snapshot["shared_vertices"] = shared
        self._original_geometry = snapshot

    # ---- ドラッグ適用 ----
    def _apply_drag(self, event) -> None:
        sel = self._selection
        if sel is None or self._original_geometry is None or self._drag_start_world is None:
            return
        mx, my = self._to_window(event)
        cur_world = _region_to_world_mm(self._region, self._rv3d, mx, my)
        if cur_world is None:
            return
        dx = cur_world[0] - self._drag_start_world[0]
        dy = cur_world[1] - self._drag_start_world[1]
        if abs(dx) > 0.05 or abs(dy) > 0.05:
            self._drag_moved = True

        if sel["type"] == "edge":
            # 辺を法線方向にシフト + 共有頂点を「隣接辺の line と新 line の交点」に
            # 補正することで、隣接辺の **角度を維持** したまま selected edge を動かす
            orig_poly = self._original_geometry["poly"]
            ei = sel["edge"]
            n = len(orig_poly)
            a = orig_poly[ei]
            b = orig_poly[(ei + 1) % n]
            ex = b[0] - a[0]
            ey = b[1] - a[1]
            L = math.hypot(ex, ey)
            if L < 1e-6:
                return
            nx = -ey / L
            ny = ex / L
            shift = dx * nx + dy * ny
            sx = nx * shift
            sy = ny * shift

            # 新 selected edge の line 上の 2 点
            a_new_line = (a[0] + sx, a[1] + sy)
            b_new_line = (b[0] + sx, b[1] + sy)

            # drag 中スナップ: 隣接コマ辺 / 基本枠 / 裁ち落とし枠 1mm 外側に
            # 1.5mm 以内なら吸着 (角度は維持)
            tx = ex / L
            ty = ey / L
            page_for_snap = self._work.pages[sel["page"]]
            a_new_line, b_new_line = _snap_drag_line(
                self._work, page_for_snap, sel["coma"],
                a_new_line, b_new_line, tx, ty, nx, ny,
            )
            new_poly = _build_shifted_edge_polygon(
                orig_poly, ei, a_new_line, b_new_line
            )
            if new_poly is None or not _is_valid_coma_polygon(
                new_poly, reference_poly=orig_poly
            ):
                return

            # 隣接 edge も同じ shift で動かす (snap 後の実シフトを反映、gap 維持)
            actual_sx = a_new_line[0] - a[0]
            actual_sy = a_new_line[1] - a[1]
            adjacent_updates = []
            for adj_st in self._original_geometry.get("adjacent_edges", []):
                op2 = adj_st["poly"]
                ei2 = adj_st["edge"]
                a2 = op2[ei2]
                b2 = op2[(ei2 + 1) % len(op2)]
                a2_line = (a2[0] + actual_sx, a2[1] + actual_sy)
                b2_line = (b2[0] + actual_sx, b2[1] + actual_sy)
                np2 = _build_shifted_edge_polygon(op2, ei2, a2_line, b2_line)
                if np2 is None or not _is_valid_coma_polygon(
                    np2, reference_poly=op2
                ):
                    return
                adjacent_updates.append((adj_st["page"], adj_st["coma"], np2))

            page = self._work.pages[sel["page"]]
            panel = page.comas[sel["coma"]]
            _set_coma_polygon(panel, new_poly)
            for page_i2, panel_i2, poly2 in adjacent_updates:
                p2 = self._work.pages[page_i2].comas[panel_i2]
                _set_coma_polygon(p2, poly2)

        elif sel["type"] == "vertex":
            orig_poly = self._original_geometry["poly"]
            vi = int(sel.get("vertex", -1))
            moving_vertices = set(
                coma_vertex_edit.selection_vertex_indices(
                    {"vertex": vi, "vertices": self._original_geometry.get("vertex_indices", sel.get("vertices"))},
                    len(orig_poly),
                )
            )
            dx, dy = coma_vertex_edit.snap_vertex_delta_to_guides(
                orig_poly,
                moving_vertices,
                vi,
                dx,
                dy,
                self._original_geometry.get("vertex_adjacent_edges", []),
                snap_tolerance_mm=DRAG_SNAP_TOL_MM,
                direction_snap_tolerance_mm=VERTEX_DIRECTION_SNAP_TOL_MM,
            )
            new_poly = list(orig_poly)
            for moving_vi in moving_vertices:
                new_poly[moving_vi] = (
                    orig_poly[moving_vi][0] + dx,
                    orig_poly[moving_vi][1] + dy,
                )
            if not _is_valid_coma_polygon(new_poly, reference_poly=orig_poly):
                return
            panel_updates: dict[tuple[int, int], list[tuple[float, float]]] = {}
            updated_vertices: set[tuple[int, int, int]] = set()
            sel_ox, sel_oy = _page_offset(self._work, sel["page"])
            for adj_st in self._original_geometry.get("vertex_adjacent_edges", []):
                selected_edge = adj_st["selected_edge"]
                sel_a_world = (
                    new_poly[selected_edge][0] + sel_ox,
                    new_poly[selected_edge][1] + sel_oy,
                )
                sel_b_world = (
                    new_poly[(selected_edge + 1) % len(new_poly)][0] + sel_ox,
                    new_poly[(selected_edge + 1) % len(new_poly)][1] + sel_oy,
                )
                adj_line = coma_edge_adjacency.line_from_projection_params(
                    sel_a_world, sel_b_world, adj_st["params"]
                )
                if adj_line is None:
                    continue
                page_i2 = adj_st["page"]
                panel_i2 = adj_st["coma"]
                key = (page_i2, panel_i2)
                base_poly = panel_updates.get(key, adj_st["poly"])
                ox2, oy2 = _page_offset(self._work, page_i2)
                a2_local = (adj_line[0][0] - ox2, adj_line[0][1] - oy2)
                b2_local = (adj_line[1][0] - ox2, adj_line[1][1] - oy2)
                np2 = _build_shifted_edge_polygon(
                    base_poly, adj_st["edge"], a2_local, b2_local
                )
                if np2 is None or not _is_valid_coma_polygon(
                    np2, reference_poly=adj_st["poly"]
                ):
                    continue
                panel_updates[key] = np2
                updated_vertices.add((page_i2, panel_i2, adj_st["edge"]))
                updated_vertices.add(
                    (page_i2, panel_i2, (adj_st["edge"] + 1) % len(np2))
                )
            page = self._work.pages[sel["page"]]
            panel = page.comas[sel["coma"]]
            # edge 連動対象外の共有頂点は従来通り同量シフトする。
            for sh in self._original_geometry.get("shared_vertices", []):
                key = (sh["page"], sh["coma"])
                vertex_key = (sh["page"], sh["coma"], sh["vertex"])
                if vertex_key in updated_vertices:
                    continue
                op2 = sh["poly"]
                vi2 = sh["vertex"]
                np2 = list(panel_updates.get(key, op2))
                np2[vi2] = (op2[vi2][0] + dx, op2[vi2][1] + dy)
                if not _is_valid_coma_polygon(np2, reference_poly=op2):
                    continue
                panel_updates[key] = np2
            _set_coma_polygon(panel, new_poly)
            for (page_i2, panel_i2), poly2 in panel_updates.items():
                p2 = self._work.pages[page_i2].comas[panel_i2]
                _set_coma_polygon(p2, poly2)

    # ---- ハンドルアクション (拡張) ----
    def _do_extend(self, direction: int) -> None:
        """選択辺を direction (1=正側、2=負側) 方向に拡張.

        スナップ仕様:
        - 拡張先候補は同ページの他コマ辺 / 基本枠 / 裁ち落とし枠外
        - **辺の角度は維持したまま**、平行移動で候補 line に重ねる
        - 共有頂点は prev/next 辺の line と新 line の交点で補正 (= 隣接辺の角度維持)
        - スナップ位置のオフセット:
          - 裁ち落とし枠外: 枠線の半幅分外側
          - 他コマ辺: ピッタリ重ねる (gap=0)
          - 基本枠: ピッタリ
        - **特殊ケース** (ピッタリ重なり状態 → 離れる方向▲): 法線方向と無関係に
          隣接コマ辺と元 selected edge の距離がほぼ 0 のとき、▲を sign 方向に
          押すと辺を sign 方向に gap_v / gap_h 分平行移動して隙間を空ける。
        """
        sel = self._selection
        if sel is None or sel.get("type") != "edge":
            return
        page = self._work.pages[sel["page"]]
        panel = page.comas[sel["coma"]]
        poly = _coma_polygon(panel)
        if len(poly) < 2:
            return
        ei = sel["edge"]
        n = len(poly)
        a = poly[ei]
        b = poly[(ei + 1) % n]
        ex = b[0] - a[0]
        ey = b[1] - a[1]
        L = math.hypot(ex, ey)
        if L < 1e-6:
            return
        nx = -ey / L
        ny = ex / L
        # direction 1: 法線正方向に拡張、direction 2: 法線負方向に拡張
        sign = 1.0 if direction == 1 else -1.0

        # 拡張先候補: 裁ち落とし枠外 / 基本枠 / 他 panel の辺
        from ..utils.geom import bleed_rect, inner_frame_rect
        paper = self._work.paper
        br = bleed_rect(paper)
        ifr = inner_frame_rect(paper)
        border_width_mm = _border_width_for_panel(panel)
        finish_offset_mm = _finish_outer_offset_for_panel(panel)

        # 候補となる「線」のリスト (page-local 座標) と種別を保持。
        # 種別: "bleed_outer" (裁ち落とし枠の **枠線幅分外側**) / "inner" (基本枠) /
        #       "coma" (他コマ辺)
        # bleed_outer は最初から線幅分外側位置を candidate に登録することで、
        # 後段の offset 計算が不要になり、edge が既に線幅分外側にいる場合の
        # 誤スナップを防ぐ。
        candidate_lines: list[
            tuple[tuple[float, float], tuple[float, float], str]
        ] = []
        # 裁ち落とし枠の 4 辺 (枠線幅分外側位置)
        candidate_lines.extend([
            ((br.x - finish_offset_mm, br.y - finish_offset_mm), (br.x2 + finish_offset_mm, br.y - finish_offset_mm), "bleed_outer"),
            ((br.x2 + finish_offset_mm, br.y - finish_offset_mm), (br.x2 + finish_offset_mm, br.y2 + finish_offset_mm), "bleed_outer"),
            ((br.x2 + finish_offset_mm, br.y2 + finish_offset_mm), (br.x - finish_offset_mm, br.y2 + finish_offset_mm), "bleed_outer"),
            ((br.x - finish_offset_mm, br.y2 + finish_offset_mm), (br.x - finish_offset_mm, br.y - finish_offset_mm), "bleed_outer"),
        ])
        # 基本枠の 4 辺
        candidate_lines.extend([
            ((ifr.x, ifr.y), (ifr.x2, ifr.y), "inner"),
            ((ifr.x2, ifr.y), (ifr.x2, ifr.y2), "inner"),
            ((ifr.x2, ifr.y2), (ifr.x, ifr.y2), "inner"),
            ((ifr.x, ifr.y2), (ifr.x, ifr.y), "inner"),
        ])
        if (
            _snap_gutter_to_finish_enabled(getattr(self, "_context", None))
            and coma_gutter_snap.is_gutter_extension_direction(
                self._work,
                int(sel["page"]),
                nx,
                ny,
                sign,
            )
        ):
            fg_a, fg_b = coma_gutter_snap.finish_gutter_line_for_page(
                self._work,
                int(sel["page"]),
            )
            candidate_lines.append((fg_a, fg_b, "finish_gutter"))
        # 他 panel の edge (同 panel の対辺は拡張先として不適切なので除外)
        for panel_i2, p2 in enumerate(page.comas):
            if panel_i2 == sel["coma"]:
                continue
            poly2 = _coma_polygon(p2)
            for ei2 in range(len(poly2)):
                candidate_lines.append(
                    (poly2[ei2], poly2[(ei2 + 1) % len(poly2)], "coma")
                )

        # 重なり判定ヘルパ (端点を tangent 軸に投影し edge と被るか)
        tx = ex / L
        ty = ey / L

        def _has_tangent_overlap(ca_, cb_) -> bool:
            t_a = (ca_[0] - a[0]) * tx + (ca_[1] - a[1]) * ty
            t_b = (cb_[0] - a[0]) * tx + (cb_[1] - a[1]) * ty
            lo = max(0.0, min(t_a, t_b))
            hi = min(L, max(t_a, t_b))
            overlap = max(0.0, hi - lo)
            return overlap >= L * ADJACENCY_OVERLAP_RATIO

        # コマ間隔 (現拡張軸に応じた値)
        gap_v = float(self._work.coma_gap.vertical_mm)
        gap_h = float(self._work.coma_gap.horizontal_mm)
        target_gap_axis = gap_v if abs(ny) >= abs(nx) else gap_h

        OVERLAP_TOL_MM = 0.5  # 拡張候補から除外するしきい値 (snap 後の最小距離)
        # 「辺がほぼ重なっている」判定。 0.05mm では浮動小数誤差や軽微な手操作
        # ズレで外れて通常分岐の「遠い辺へスナップ」が走り、 線幅分どころか
        # コマ幅分だけ拡張してしまうため、 OVERLAP_TOL_MM と同じ 0.5mm まで
        # 緩めて視覚的に重なっているケースを確実に拾う。
        OVERLAP_NEAR_TOL_MM = 0.5

        # ===== 特殊ケース: 現在の継ぎ目を実際に共有している隣接コマがあり、
        # かつ ▲sign 方向が「その panel から離れる方向」の場合 → gap を空ける =====
        # 「近くに平行な線がある」だけでは発火させず、十分な重なり率を持つ
        # 実際の隣接枠線だけを対象にする。
        overlap_move_width = False
        has_panel_overlap_opposite = False
        overlapping_edges = _find_overlapping_coma_edges(
            page,
            sel["coma"],
            ei,
            max_distance_mm=OVERLAP_NEAR_TOL_MM,
            min_overlap_ratio=0.8,
        )
        for panel_i2, _ei2 in overlapping_edges:
            p2 = page.comas[panel_i2]
            poly2 = _coma_polygon(p2)
            if len(poly2) < 3:
                continue
            cx_avg = sum(v[0] for v in poly2) / len(poly2)
            cy_avg = sum(v[1] for v in poly2) / len(poly2)
            d_center = (cx_avg - a[0]) * nx + (cy_avg - a[1]) * ny
            if sign * d_center > 0:
                overlap_move_width = True
                break
            # panel 中心が -sign 側にあるとき only (= ▲sign が「離れる方向」)
            if -sign * d_center <= 0:
                continue
            has_panel_overlap_opposite = True
            break

        has_panel_overlap = has_panel_overlap_opposite

        if overlap_move_width:
            total_shift = border_width_mm * 0.5
            if total_shift < 0.001:
                self.report({"INFO"}, "枠線の線幅が 0 のため移動できません")
                return
            sx_ext = sign * total_shift * nx
            sy_ext = sign * total_shift * ny
            a_new_line = (a[0] + sx_ext, a[1] + sy_ext)
            b_new_line = (b[0] + sx_ext, b[1] + sy_ext)
            new_poly = _build_shifted_edge_polygon(poly, ei, a_new_line, b_new_line)
            if new_poly is None or not _is_valid_coma_polygon(
                new_poly, reference_poly=poly
            ):
                self.report({"WARNING"}, "拡張するとコマ形状が破綻するため中止しました")
                return
            kind_label = "隣接コマ辺から線幅の半分だけ外側"
        elif has_panel_overlap:
            # ピッタリ重なっている隣接コマ辺がある → ▲sign 方向に gap 分平行移動
            # (角度は元のまま維持、スナップ先 line に合わせる必要なし)
            total_shift = target_gap_axis
            if total_shift < 0.05:
                self.report({"INFO"}, "コマ間隔が 0 のため移動できません")
                return
            sx_ext = sign * total_shift * nx
            sy_ext = sign * total_shift * ny
            a_new_line = (a[0] + sx_ext, a[1] + sy_ext)
            b_new_line = (b[0] + sx_ext, b[1] + sy_ext)
            new_poly = _build_shifted_edge_polygon(poly, ei, a_new_line, b_new_line)
            if new_poly is None or not _is_valid_coma_polygon(
                new_poly, reference_poly=poly
            ):
                self.report({"WARNING"}, "拡張するとコマ形状が破綻するため中止しました")
                return
            kind_label = "隣接コマからスキマを空けました"
        else:
            # ===== 通常: sign 方向の最寄り候補を探索 =====
            # selected edge 自体の角度は保ちたいので、平行な候補だけを対象にする。
            # 近い順に試し、自己交差や面積 0 を起こさない案だけ採用する。
            candidates: list[
                tuple[float, float, tuple[float, float], tuple[float, float], str]
            ] = []
            for ca, cb, kind in candidate_lines:
                cdx = cb[0] - ca[0]
                cdy = cb[1] - ca[1]
                c_len = math.hypot(cdx, cdy)
                if c_len < 1e-6:
                    continue
                dot = (cdx / c_len) * tx + (cdy / c_len) * ty
                if abs(abs(dot) - 1.0) > 0.05:
                    continue
                mid = ((ca[0] + cb[0]) * 0.5, (ca[1] + cb[1]) * 0.5)
                d = (mid[0] - a[0]) * nx + (mid[1] - a[1]) * ny
                d_signed = sign * d
                if d_signed <= OVERLAP_TOL_MM:
                    continue
                if not _has_tangent_overlap(ca, cb):
                    continue
                candidates.append((d_signed, d, ca, cb, kind))
            candidates.sort(key=lambda item: item[0])
            if not candidates:
                self.report({"INFO"}, "拡張先が見つかりません")
                return
            new_poly = None
            kind_label = ""
            for _d_signed, d, _ca, _cb, kind in candidates:
                test_a_line = (a[0] + nx * d, a[1] + ny * d)
                test_b_line = (b[0] + nx * d, b[1] + ny * d)
                test_poly = _build_shifted_edge_polygon(
                    poly, ei, test_a_line, test_b_line
                )
                if test_poly is None or not _is_valid_coma_polygon(
                    test_poly, reference_poly=poly
                ):
                    continue
                a_new_line = test_a_line
                b_new_line = test_b_line
                new_poly = test_poly
                kind_label = {
                    "bleed_outer": "裁ち落とし枠の線幅分外側",
                    "inner": "基本枠",
                    "finish_gutter": "ノド側の仕上がり枠",
                    "coma": "隣接コマ辺にピッタリ",
                }.get(kind, "拡張先")
                break
            if new_poly is None:
                self.report({"WARNING"}, "拡張するとコマ形状が破綻するため中止しました")
                return

        _set_coma_polygon(panel, new_poly)
        try:
            self._save_changes()
        except Exception:  # noqa: BLE001
            _logger.exception("edge_move: extend save failed")
        self._push_undo_step("B-MANGA: 枠線拡張")
        self.report({"INFO"}, f"枠線を拡張: {kind_label}")

    # ---- 形状変化検出 ----
    def _geometry_changed(self) -> bool:
        """ドラッグ前のスナップショットと現在の形状を比較.

        浮動小数点誤差を考慮し、いずれかの頂点が 0.001mm 以上動いていれば True。
        """
        if self._original_geometry is None or self._selection is None:
            return False
        sel = self._selection
        try:
            page = self._work.pages[sel["page"]]
            panel = page.comas[sel["coma"]]
        except (IndexError, KeyError):
            return False
        current = _coma_polygon(panel)
        original = self._original_geometry.get("poly", [])
        if len(current) != len(original):
            return True
        for (cx, cy), (ox, oy) in zip(current, original):
            if abs(cx - ox) > 1e-3 or abs(cy - oy) > 1e-3:
                return True
        return False

    # ---- 保存 ----
    def _save_changes(self) -> None:
        work = self._work
        if work is None or work.work_dir == "":
            return
        work_dir = Path(work.work_dir)
        # 影響を受けたページの panel を保存
        sel = self._selection
        affected_pages: set[int] = set()
        if sel is not None:
            affected_pages.add(sel["page"])
            for st in self._original_geometry.get("adjacent_edges", []) if self._original_geometry else []:
                affected_pages.add(st["page"])
            for st in self._original_geometry.get("vertex_adjacent_edges", []) if self._original_geometry else []:
                affected_pages.add(st["page"])
            for st in self._original_geometry.get("shared_vertices", []) if self._original_geometry else []:
                affected_pages.add(st["page"])
        for pi in affected_pages:
            page = work.pages[pi]
            try:
                for panel in page.comas:
                    coma_io.save_coma_meta(work_dir, page.id, panel)
                page_io.save_page_json(work_dir, page)
            except Exception:  # noqa: BLE001
                _logger.exception("edge_move: save page %s failed", page.id)
        try:
            page_io.save_pages_json(work_dir, work)
        except Exception:  # noqa: BLE001
            _logger.exception("edge_move: save pages.json failed")
        # NOTE: コマ形状の mask Mesh への追従は core/coma.py の rect_*_mm
        # update callback (_on_coma_geometry_changed → coma_plane.on_coma_geometry_changed)
        # で自動的に行われる。 ここでの明示同期は不要。


class _EdgeExtendShim:
    """モーダル外のオブジェクトツールから既存の枠線拡張処理を呼ぶための薄い実行文脈."""

    def __init__(self, context, work, selection: dict) -> None:
        self._context = context
        self._work = work
        self._selection = selection
        self._original_geometry = None

    def _save_changes(self) -> None:
        work = self._work
        if work is None or work.work_dir == "":
            return
        page_index = int(self._selection.get("page", -1))
        if not (0 <= page_index < len(work.pages)):
            return
        work_dir = Path(work.work_dir)
        page = work.pages[page_index]
        try:
            for panel in page.comas:
                coma_io.save_coma_meta(work_dir, page.id, panel)
            page_io.save_page_json(work_dir, page)
            page_io.save_pages_json(work_dir, work)
        except Exception:  # noqa: BLE001
            _logger.exception("edge_move: save page %s failed", getattr(page, "id", ""))
        # NOTE: 三角ハンドル拡張で変わった rect_*_mm は update callback
        # (_on_coma_geometry_changed) が coma_plane Mesh を即時追従させるので
        # ここでの明示同期は不要。

    def _push_undo_step(self, message: str) -> None:
        try:
            bpy.ops.ed.undo_push(message=message)
        except Exception:  # noqa: BLE001
            _logger.exception("edge_move: undo_push failed")

    def report(self, _levels, message: str) -> None:
        _logger.info(message)


def extend_selected_handle_at_event(context, event) -> bool:
    """オブジェクトツール等の通常クリックから、表示中の▲ハンドルを実行する.

    ``_do_extend`` 実行後は edge selection を **元の kind に戻す** こと。
    具体的には:
    - クリック前 ``border`` (= コマ全体選択。 4 辺すべての ▲ ハンドルが
      表示されている) なら、 ``border`` のまま維持。 そうしないと連続して
      別辺の ▲ をクリックできない (絞り込まれた 1 辺のハンドルしか hit
      テストされず、 他 3 辺の ▲ が反応しなくなる現象を解消する)。
    - クリック前 ``edge`` (= 既に 1 辺だけ選択中) なら、 その辺を維持。
    - その他 (none 等) の場合は安全側として ``border`` にしておく。
    """
    hit = find_selected_handle_at_event(context, event)
    if hit is None:
        return False
    work = get_work(context)
    if work is None or not getattr(work, "loaded", False):
        return False
    page_index = int(hit["page"])
    coma_index = int(hit["coma"])
    edge_index = int(hit["edge"])
    if not (0 <= page_index < len(work.pages)):
        return False
    page = work.pages[page_index]
    if not (0 <= coma_index < len(page.comas)):
        return False
    panel = page.comas[coma_index]
    # クリック前の選択 kind を保持 (extend 後にこの kind へ戻す)
    wm = getattr(context, "window_manager", None)
    pre_kind = str(getattr(wm, "bmanga_edge_select_kind", "none") or "none") if wm is not None else "none"
    pre_edge_index = int(getattr(wm, "bmanga_edge_select_edge", -1)) if wm is not None else -1
    work.active_page_index = page_index
    page.active_coma_index = coma_index
    scene = getattr(context, "scene", None)
    if scene is not None and hasattr(scene, "bmanga_active_layer_kind"):
        scene.bmanga_active_layer_kind = "coma"
    object_selection.select_key(
        context,
        object_selection.coma_key(page, panel),
        mode="single",
    )
    # _do_extend は selection["edge"] (= ヒットした辺) を起点に処理するので、
    # 一時的に edge_selection を hit edge へ切替える。
    edge_selection.set_selection(
        context,
        "edge",
        page_index=page_index,
        coma_index=coma_index,
        edge_index=edge_index,
    )
    selection = {
        "type": "edge",
        "page": page_index,
        "coma": coma_index,
        "edge": edge_index,
    }
    shim = _EdgeExtendShim(context, work, selection)
    BMANGA_OT_coma_edge_move._do_extend(shim, int(hit["direction"]))
    # クリック前の kind を復元 (border 選択中なら border に戻し、 4 辺全ての
    # ▲ ハンドルを再表示できるようにする → 連続で別辺をクリック可能)
    if pre_kind == "edge" and 0 <= pre_edge_index:
        edge_selection.set_selection(
            context,
            "edge",
            page_index=page_index,
            coma_index=coma_index,
            edge_index=pre_edge_index,
        )
    else:
        # border / none / unknown はすべて border に正規化
        # (オブジェクトツールでコマをクリックして選択した直後は border 状態)
        edge_selection.set_selection(
            context,
            "border",
            page_index=page_index,
            coma_index=coma_index,
        )
    _tag_view3d_redraw(context)
    return True


# ---------- POST_PIXEL 描画 ----------


def _hover_pointer_for_modal(op: "BMANGA_OT_coma_edge_move") -> tuple[int, int] | None:
    """modal の draw_callback 用に WM 経由で hover カーソル位置を取得."""
    try:
        return edge_selection.get_overlay_pointer(bpy.context)
    except Exception:  # noqa: BLE001
        return None


def _is_handle_hovered_modal(handle_px, pointer) -> bool:
    if pointer is None or handle_px is None:
        return False
    dx = handle_px[0] - pointer[0]
    dy = handle_px[1] - pointer[1]
    return (dx * dx + dy * dy) <= (HANDLE_HIT_RADIUS_PX * HANDLE_HIT_RADIUS_PX)


def _is_vertex_hovered_modal(vertex_px, pointer) -> bool:
    if pointer is None or vertex_px is None:
        return False
    dx = vertex_px[0] - pointer[0]
    dy = vertex_px[1] - pointer[1]
    return (dx * dx + dy * dy) <= (VERTEX_PICK_TOLERANCE_PX * VERTEX_PICK_TOLERANCE_PX)


def _vertex_marker_color(point, pointer, *, selected: bool) -> tuple[float, float, float, float]:
    if _is_vertex_hovered_modal(point, pointer):
        return COLOR_VERTEX_HIGHLIGHT
    return COLOR_VERTEX_ACTIVE if selected else COLOR_SELECTED_VERTEX


def _draw_callback(op: "BMANGA_OT_coma_edge_move") -> None:
    sel = op._selection
    if sel is None:
        return
    region = op._region
    rv3d = op._rv3d
    work = op._work
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    pointer = _hover_pointer_for_modal(op)

    if sel["type"] == "border":
        # 枠線全体ハイライト (panel の全 edge を強調表示)
        page = work.pages[sel["page"]]
        panel = page.comas[sel["coma"]]
        poly = _coma_polygon(panel)
        if len(poly) < 2:
            return
        ox, oy = _page_offset_for_area(bpy.context, work, op._area, sel["page"])
        try:
            gpu.state.line_width_set(4.0)
        except Exception:  # noqa: BLE001
            pass
        verts: list[tuple[float, float]] = []
        screen_edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
        n = len(poly)
        for i in range(n):
            a = poly[i]
            b = poly[(i + 1) % n]
            ap = _world_mm_to_region(region, rv3d, a[0] + ox, a[1] + oy)
            bp = _world_mm_to_region(region, rv3d, b[0] + ox, b[1] + oy)
            if ap is None or bp is None:
                continue
            verts.append(ap)
            verts.append(bp)
            screen_edges.append((ap, bp))
        for ap, bp in screen_edges:
            _draw_screen_segment_band(
                shader,
                ap,
                bp,
                color=COLOR_SELECTED_EDGE_FILL,
                width_px=EDGE_SELECTED_BAND_WIDTH_PX,
            )
        if verts:
            batch = batch_for_shader(shader, "LINES", {"pos": verts})
            shader.bind()
            shader.uniform_float("color", COLOR_SELECTED_BORDER)
            batch.draw(shader)
        try:
            gpu.state.line_width_set(1.0)
        except Exception:  # noqa: BLE001
            pass
        for ap, bp in screen_edges:
            _draw_square_marker(shader, ap, _vertex_marker_color(ap, pointer, selected=False))
            _draw_square_marker(shader, bp, _vertex_marker_color(bp, pointer, selected=False))
            h1 = h2 = None
            # screen_edges はすでに画面座標なので、描画用の中心計算だけ直接行う。
            mx = (ap[0] + bp[0]) * 0.5
            my = (ap[1] + bp[1]) * 0.5
            dx = bp[0] - ap[0]
            dy = bp[1] - ap[1]
            length = math.hypot(dx, dy)
            if length >= 1.0e-6:
                nx = -dy / length
                ny = dx / length
                h1 = (mx + nx * HANDLE_OFFSET_PX, my + ny * HANDLE_OFFSET_PX)
                h2 = (mx - nx * HANDLE_OFFSET_PX, my - ny * HANDLE_OFFSET_PX)
            for handle, dir_idx in ((h1, 1), (h2, 2)):
                if handle is None:
                    continue
                _draw_triangle_handle(
                    shader, handle, ap, bp, dir_idx,
                    highlighted=_is_handle_hovered_modal(handle, pointer),
                )
        return

    if sel["type"] == "edge":
        edge_world = op._get_selected_edge_world()
        if edge_world is None:
            return
        a, b = edge_world
        ap = _world_mm_to_region(region, rv3d, a[0], a[1])
        bp = _world_mm_to_region(region, rv3d, b[0], b[1])
        if ap is None or bp is None:
            return
        # 選択辺ハイライト
        _draw_screen_segment_band(
            shader,
            ap,
            bp,
            color=COLOR_SELECTED_EDGE_FILL,
            width_px=EDGE_SELECTED_BAND_WIDTH_PX,
        )
        try:
            gpu.state.line_width_set(4.0)
        except Exception:  # noqa: BLE001
            pass
        batch = batch_for_shader(shader, "LINES", {"pos": [ap, bp]})
        shader.bind()
        shader.uniform_float("color", COLOR_SELECTED_EDGE)
        batch.draw(shader)
        _draw_square_marker(shader, ap, _vertex_marker_color(ap, pointer, selected=False))
        _draw_square_marker(shader, bp, _vertex_marker_color(bp, pointer, selected=False))
        try:
            gpu.state.line_width_set(1.0)
        except Exception:  # noqa: BLE001
            pass

        # 三角ハンドル (選択辺の法線 ±)
        h1, h2 = _compute_handle_centers_px(region, rv3d, a, b) or (None, None)
        for handle, dir_idx in ((h1, 1), (h2, 2)):
            if handle is None:
                continue
            _draw_triangle_handle(
                shader, handle, ap, bp, dir_idx,
                highlighted=_is_handle_hovered_modal(handle, pointer),
            )

        # 他 3 辺の ▲ ハンドルも描画 (連続クリック対応 / hit テストと一致)
        try:
            page = work.pages[sel["page"]]
            panel = page.comas[sel["coma"]]
            poly = _coma_polygon(panel)
            if len(poly) >= 2:
                ox, oy = _page_offset_for_area(bpy.context, work, op._area, sel["page"])
                sel_edge_idx = int(sel.get("edge", -1))
                for ei in range(len(poly)):
                    if ei == sel_edge_idx:
                        continue
                    other_a = (poly[ei][0] + ox, poly[ei][1] + oy)
                    other_b = (poly[(ei + 1) % len(poly)][0] + ox, poly[(ei + 1) % len(poly)][1] + oy)
                    other_ap = _world_mm_to_region(region, rv3d, other_a[0], other_a[1])
                    other_bp = _world_mm_to_region(region, rv3d, other_b[0], other_b[1])
                    if other_ap is None or other_bp is None:
                        continue
                    other_h1, other_h2 = (
                        _compute_handle_centers_px(region, rv3d, other_a, other_b)
                        or (None, None)
                    )
                    for handle, dir_idx in ((other_h1, 1), (other_h2, 2)):
                        if handle is None:
                            continue
                        _draw_triangle_handle(
                            shader, handle, other_ap, other_bp, dir_idx,
                            highlighted=_is_handle_hovered_modal(handle, pointer),
                        )
        except Exception:  # noqa: BLE001
            pass

    elif sel["type"] == "vertex":
        page = work.pages[sel["page"]]
        panel = page.comas[sel["coma"]]
        poly = _coma_polygon(panel)
        selected_vertices = coma_vertex_edit.selection_vertex_indices(sel, len(poly))
        if not selected_vertices:
            return
        ox, oy = _page_offset_for_area(bpy.context, work, op._area, sel["page"])
        for vertex_index in selected_vertices:
            v = poly[vertex_index]
            vp = _world_mm_to_region(region, rv3d, v[0] + ox, v[1] + oy)
            if vp is None:
                continue
            _draw_square_marker(
                shader,
                vp,
                _vertex_marker_color(vp, pointer, selected=True),
                size_px=7.5,
            )


def _draw_triangle_handle(
    shader, center: tuple[float, float],
    edge_a_px: tuple[float, float], edge_b_px: tuple[float, float],
    direction_idx: int,
    *,
    highlighted: bool = False,
) -> None:
    """edge の法線方向 (direction_idx=1 or 2) を向く三角形を描画.

    ``highlighted=True`` のときカーソル hover 用のハイライト色で塗る。
    形状は「横に長くて低い」プロポーション (apex 0.7s 突き出し / base 半幅
    1.0s の squat triangle)。
    """
    cx, cy = center
    ex = edge_b_px[0] - edge_a_px[0]
    ey = edge_b_px[1] - edge_a_px[1]
    L = math.hypot(ex, ey)
    if L < 1e-6:
        return
    # 法線
    nx = -ey / L
    ny = ex / L
    if direction_idx == 2:
        nx, ny = -nx, -ny
    # tangent
    tx = ex / L
    ty = ey / L
    s = HANDLE_SIZE_PX
    # 三角形 (横長プロポーション):
    # - apex: 法線方向 +0.7s
    # - base: 法線方向 -0.3s, tangent 方向 ±1.0s (= 全幅 2.0s)
    apex = (cx + nx * s * 0.7, cy + ny * s * 0.7)
    base_l = (cx - tx * s * 1.0 - nx * s * 0.3, cy - ty * s * 1.0 - ny * s * 0.3)
    base_r = (cx + tx * s * 1.0 - nx * s * 0.3, cy + ty * s * 1.0 - ny * s * 0.3)
    verts = [apex, base_l, base_r]
    batch = batch_for_shader(
        shader, "TRIS", {"pos": verts}, indices=[(0, 1, 2)],
    )
    shader.bind()
    shader.uniform_float(
        "color",
        COLOR_HANDLE_HIGHLIGHT if highlighted else COLOR_HANDLE,
    )
    batch.draw(shader)


def _draw_screen_segment_band(
    shader,
    a: tuple[float, float],
    b: tuple[float, float],
    *,
    color: tuple[float, float, float, float],
    width_px: float,
) -> None:
    dx = float(b[0]) - float(a[0])
    dy = float(b[1]) - float(a[1])
    length = math.hypot(dx, dy)
    if length < 1.0e-6:
        return
    half = max(1.0, float(width_px) * 0.5)
    nx = -dy / length * half
    ny = dx / length * half
    verts = [
        (a[0] + nx, a[1] + ny),
        (b[0] + nx, b[1] + ny),
        (b[0] - nx, b[1] - ny),
        (a[0] - nx, a[1] - ny),
    ]
    batch = batch_for_shader(
        shader,
        "TRIS",
        {"pos": verts},
        indices=[(0, 1, 2), (0, 2, 3)],
    )
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_square_marker(
    shader,
    center: tuple[float, float],
    color: tuple[float, float, float, float],
    size_px: float = 6.0,
) -> None:
    cx, cy = center
    verts = [
        (cx - size_px, cy - size_px), (cx + size_px, cy - size_px),
        (cx + size_px, cy + size_px), (cx - size_px, cy + size_px),
    ]
    batch = batch_for_shader(
        shader, "TRIS", {"pos": verts},
        indices=[(0, 1, 2), (0, 2, 3)],
    )
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


_CLASSES = (
    BMANGA_OT_coma_edge_move,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
