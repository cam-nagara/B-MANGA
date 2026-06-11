"""ビューポート上の原稿オーバーレイ描画 (draw_handler_add + gpu).

計画書 3.4.3a に従い、以下を gpu + blf でオーバーレイ描画する:
- 作画中の補助表示
- 各ページ上部のページ識別番号 (001 形式、ビューポート用ガイド)

書き出し結果には焼き込まれない (書き出し時は export_renderer が同じ
overlay_shared ロジックを Pillow で再実装する、Phase 6 で実装)。

座標系:
- 原稿座標は mm 基準 (キャンバス左下が原点)
- Blender ビューポートへの描画は 3D ワールド空間の XY 平面上 (z=0)
- 1 mm = 0.001 Blender unit で配置。カメラリグは Phase 2 で実装予定のため、
  現段階ではワールド XY 平面への配置のみで、カメラがこの平面を写す想定。
"""

from __future__ import annotations

from typing import Optional

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ..core.mode import MODE_PAGE, MODE_COMA, get_mode
from ..core.work import get_active_page, get_work
from ..utils import (
    balloon_shapes,
    border_geom,
    color_space,
    free_transform,
    log,
    object_selection,
    page_browser,
    page_file_scene,
    stroke_style,
    text_layout_bounds,
    page_preview_object,
    text_style,
    viewport_colors,
)
from ..utils.geom import Rect, bleed_rect, mm_to_m
from . import overlay_effect_line
from . import overlay_image
from . import overlay_coma_selection
from . import overlay_creation_range
from . import overlay_shared
from . import overlay_text
from . import overlay_visibility

_logger = log.get_logger(__name__)

# draw_handler_add の戻り値 (ハンドラ識別子)
_handle: Optional[object] = None
# blf テキスト描画は POST_VIEW では view/projection matrix が適用されて
# screen 座標が world 座標扱いになり画面外に飛ぶため、POST_PIXEL で別 handler。
_handle_pixel: Optional[object] = None
# 用紙塗りは PRE_VIEW で 3D 描画前に描く (BLENDED ラスター材質は depth 書込
# しないため、POST_VIEW で塗ると raster Mesh が用紙背景に隠される)。
_handle_pre: Optional[object] = None

# 日本語対応フォントの font_id キャッシュ (起動時 1 回ロード).
# blf.draw で font_id=0 を使うと ASCII しか描けず日本語が文字化けるため、
# OS のシステムフォントから日本語対応フォントを load しておく。
# 値が None = 未試行、-1 = ロード失敗 (font_id=0 fallback)、0 以上 = ロード済み。
_JP_FONT_ID: Optional[int] = None
_FONT_ID_BY_PATH: dict[str, int] = {}

# 作品情報とは独立した、ビューポート用のページ識別番号。
_PAGE_HEADER_GAP_MM = 6.0
_PAGE_HEADER_FONT_SIZE_PX = 34
_PAGE_HEADER_COLOR = (0.0, 0.0, 0.0, 0.95)
_PAGE_HEADER_OUTLINE_COLOR = (1.0, 1.0, 1.0, 0.9)


def _get_jp_font_id() -> int:
    """日本語表示用 blf font_id を返す (load 失敗時は 0).

    起動時に Windows / macOS / Linux の代表的な日本語フォントから 1 つ
    ロードを試みる。失敗なら font_id=0 (ASCII のみ) を返す。
    """
    global _JP_FONT_ID
    if _JP_FONT_ID is not None:
        return _JP_FONT_ID if _JP_FONT_ID >= 0 else 0
    import os
    candidates = []
    if os.name == "nt":
        candidates.extend([
            r"C:\Windows\Fonts\YuGothM.ttc",
            r"C:\Windows\Fonts\meiryo.ttc",
            r"C:\Windows\Fonts\msgothic.ttc",
            r"C:\Windows\Fonts\YuGothR.ttc",
        ])
    else:
        # macOS / Linux 候補
        candidates.extend([
            "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
        ])
    for path in candidates:
        try:
            if not os.path.isfile(path):
                continue
            fid = blf.load(path)
            if fid is not None and fid != -1:
                _JP_FONT_ID = int(fid)
                _logger.info("blf JP font loaded: %s -> id=%d", path, fid)
                return _JP_FONT_ID
        except Exception:  # noqa: BLE001
            continue
    _JP_FONT_ID = -1
    _logger.warning("blf JP font load failed; falling back to font_id=0 (ASCII only)")
    return 0


def _get_font_id_for_path(font_path: str) -> int:
    resolved = text_style.resolve_font_path(font_path)
    if not resolved:
        return _get_jp_font_id()
    key = resolved.lower()
    cached = _FONT_ID_BY_PATH.get(key)
    if cached is not None:
        return cached if cached >= 0 else _get_jp_font_id()
    try:
        fid = blf.load(resolved)
        if fid is not None and fid != -1:
            _FONT_ID_BY_PATH[key] = int(fid)
            return int(fid)
    except Exception:  # noqa: BLE001
        pass
    _FONT_ID_BY_PATH[key] = -1
    return _get_jp_font_id()


# ---------- 低レベル描画ヘルパ ----------


def _draw_rect_fill(rect: Rect, color: tuple[float, float, float, float]) -> None:
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts = [
        (mm_to_m(rect.x), mm_to_m(rect.y), 0.0),
        (mm_to_m(rect.x2), mm_to_m(rect.y), 0.0),
        (mm_to_m(rect.x2), mm_to_m(rect.y2), 0.0),
        (mm_to_m(rect.x), mm_to_m(rect.y2), 0.0),
    ]
    indices = [(0, 1, 2), (0, 2, 3)]
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_rect_outline(
    rect: Rect,
    color: tuple[float, float, float, float],
    line_width: float = 1.0,
    width_mm: float | None = None,
) -> None:
    """矩形の枠線を描画.

    ``width_mm`` を指定すると mm 単位の太さで 4 本の塗り帯を描画する
    (= ズームに連動して画面上の太さが変わる、紙に追従する線)。
    既定 (None) は ``line_width`` (px 単位) で従来の LINE_STRIP 描画
    (画面上一定の太さ、紙に追従しない)。
    """
    if width_mm is not None and width_mm > 0.0:
        _draw_rect_outline_mm(rect, color, width_mm)
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts = [
        (mm_to_m(rect.x), mm_to_m(rect.y), 0.0),
        (mm_to_m(rect.x2), mm_to_m(rect.y), 0.0),
        (mm_to_m(rect.x2), mm_to_m(rect.y2), 0.0),
        (mm_to_m(rect.x), mm_to_m(rect.y2), 0.0),
        (mm_to_m(rect.x), mm_to_m(rect.y), 0.0),
    ]
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", color)
    try:
        gpu.state.line_width_set(max(1.0, float(line_width)))
        batch.draw(shader)
    finally:
        gpu.state.line_width_set(1.0)


def _draw_rect_outline_mm(
    rect: Rect,
    color: tuple[float, float, float, float],
    width_mm: float,
) -> None:
    """mm 単位の太さで矩形枠を 4 本の塗り帯として描画 (ズーム連動)."""
    w = max(0.001, float(width_mm))
    half = w * 0.5
    # 4 本の帯 (上下左右、コーナーで矩形を共有して overlap)
    top = Rect(rect.x - half, rect.y2 - half, rect.width + w, w)
    bottom = Rect(rect.x - half, rect.y - half, rect.width + w, w)
    left = Rect(rect.x - half, rect.y - half, w, rect.height + w)
    right = Rect(rect.x2 - half, rect.y - half, w, rect.height + w)
    for r in (top, bottom, left, right):
        if r.width > 0 and r.height > 0:
            _draw_rect_fill(r, color)


def _selection_handle_rects(rect: Rect, size_mm: float = 2.0) -> list[Rect]:
    half = size_mm * 0.5
    points = (
        (rect.x, rect.y),
        (rect.x + rect.width * 0.5, rect.y),
        (rect.x2, rect.y),
        (rect.x, rect.y + rect.height * 0.5),
        (rect.x2, rect.y + rect.height * 0.5),
        (rect.x, rect.y2),
        (rect.x + rect.width * 0.5, rect.y2),
        (rect.x2, rect.y2),
    )
    return [Rect(x - half, y - half, size_mm, size_mm) for x, y in points]


def _draw_quad_outline(
    quad: dict[str, tuple[float, float]],
    color: tuple[float, float, float, float],
    line_width: float = 1.0,
) -> None:
    points = free_transform.ordered_quad_points(quad)
    verts = [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in [*points, points[0]]]
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", color)
    try:
        gpu.state.line_width_set(max(1.0, float(line_width)))
        batch.draw(shader)
    finally:
        gpu.state.line_width_set(1.0)


def _quad_handle_rects(quad: dict[str, tuple[float, float]], size_mm: float = 2.0) -> list[Rect]:
    half = size_mm * 0.5
    return [
        Rect(x - half, y - half, size_mm, size_mm)
        for x, y in free_transform.ordered_quad_points(quad)
    ]


def _free_transform_quad_for_key(context, key: str, rect: Rect):
    try:
        from ..operators import object_tool_selection
        from ..utils.layer_hierarchy import OUTSIDE_STACK_KEY
    except Exception:  # noqa: BLE001
        return None
    kind, page_id, item_id = object_selection.parse_key(key)
    if kind in {"balloon", "text"}:
        work = getattr(context.scene, "bname_work", None)
        if kind == "balloon":
            if page_id == OUTSIDE_STACK_KEY:
                _idx, entry = object_tool_selection.find_shared_balloon_by_key(work, item_id)
            else:
                _pi, _page, _idx, entry = object_tool_selection.find_balloon_by_key(work, page_id, item_id)
        else:
            if page_id == OUTSIDE_STACK_KEY:
                _idx, entry = object_tool_selection.find_shared_text_by_key(work, item_id)
            else:
                _pi, _page, _idx, entry = object_tool_selection.find_text_by_key(work, page_id, item_id)
        return free_transform.entry_quad(entry, rect) if entry is not None else None
    if kind == "effect":
        obj, layer = object_tool_selection.find_effect_layer(item_id)
        payload = free_transform.effect_payload_for_layer(obj, layer)
        if free_transform.effect_payload_enabled(payload):
            return free_transform.quad_from_rect_offsets(rect, payload.get("offsets"))
    return None


def _balloon_flash_center_xy(entry, rect: Rect) -> tuple[float, float] | None:
    if entry is None or not balloon_shapes.is_flash_line_style(getattr(entry, "line_style", "")):
        return None
    local_x = max(0.0, float(getattr(entry, "width_mm", 0.0) or 0.0)) * 0.5
    local_y = max(0.0, float(getattr(entry, "height_mm", 0.0) or 0.0)) * 0.5
    local_x += float(getattr(entry, "center_offset_x_mm", 0.0) or 0.0)
    local_y += float(getattr(entry, "center_offset_y_mm", 0.0) or 0.0)
    local_x, local_y = free_transform.transform_entry_local_point(entry, local_x, local_y)
    return rect.x + local_x, rect.y + local_y


def _draw_object_tool_layer_bounds(context) -> None:
    try:
        from ..operators import object_tool_op
        from ..utils import balloon_tail_geom
    except Exception:  # noqa: BLE001
        return
    keys = set(object_selection.get_keys(context))
    active_key = object_tool_op.active_selection_key(context)
    if active_key:
        keys.add(active_key)
    for key in sorted(keys):
        kind = object_selection.parse_key(key)[0]
        if kind not in {"page", "coma", "balloon", "text", "effect", "image", "raster", "gp"}:
            continue
        if kind == "coma":
            continue
        rect = object_tool_op.selection_bounds_for_key(context, key)
        if rect is None:
            continue
        quad = _free_transform_quad_for_key(context, key, rect)
        if quad:
            _draw_quad_outline(quad, viewport_colors.SELECTION, line_width=2.0)
            handle_rects = _quad_handle_rects(quad)
        else:
            _draw_rect_outline(rect.inset(-1.0), viewport_colors.SELECTION, width_mm=0.50)
            handle_rects = _selection_handle_rects(rect)
        for handle in handle_rects:
            _draw_rect_fill(handle, viewport_colors.HANDLE_FILL)
            _draw_rect_outline(handle, viewport_colors.HANDLE_OUTLINE, width_mm=0.25)
        if kind == "balloon":
            _kind, page_id, item_id = object_selection.parse_key(key)
            try:
                work = getattr(context.scene, "bname_work", None)
                _page_index, _page, _idx, entry = object_tool_op._find_balloon_by_key(work, page_id, item_id)
            except Exception:  # noqa: BLE001
                entry = None
            if entry is not None:
                center_xy = _balloon_flash_center_xy(entry, rect)
                if center_xy is not None:
                    overlay_effect_line._draw_center_cross(
                        rect,
                        center_xy=center_xy,
                        draw_rect_fill=_draw_rect_fill,
                        draw_rect_outline=_draw_rect_outline,
                    )
                for tail in getattr(entry, "tails", []) or []:
                    tail_points = balloon_tail_geom.tail_world_points(rect, tail)
                    if len(tail_points) < 2:
                        continue
                    for px, py in (tail_points[0], tail_points[-1]):
                        handle = Rect(px - 1.0, py - 1.0, 2.0, 2.0)
                        _draw_rect_fill(handle, viewport_colors.HANDLE_FILL)
                        _draw_rect_outline(handle, viewport_colors.HANDLE_OUTLINE, width_mm=0.25)
                    for px, py in tail_points[1:-1]:
                        handle = Rect(px - 0.8, py - 0.8, 1.6, 1.6)
                        _draw_rect_fill(handle, viewport_colors.HANDLE_FILL)
                        _draw_rect_outline(handle, viewport_colors.HANDLE_OUTLINE, width_mm=0.20)


def _draw_segments_mm(
    segs: list[tuple[tuple[float, float], tuple[float, float]]],
    color: tuple[float, float, float, float],
    width_mm: float,
) -> None:
    """mm 単位の太さで線分群を塗りポリゴンとして描画 (ズーム連動).

    各線分は太さ ``width_mm`` の細長い矩形 (端は square cap、両端で
    width_mm/2 ずつ伸びる) として描画する。
    """
    if not segs:
        return
    import math as _math
    w = max(0.001, float(width_mm))
    half = w * 0.5
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts: list[tuple[float, float, float]] = []
    indices: list[tuple[int, int, int]] = []
    for (x1, y1), (x2, y2) in segs:
        dx = x2 - x1
        dy = y2 - y1
        length = _math.hypot(dx, dy)
        if length <= 0.0:
            continue
        # 単位ベクトルと法線
        ux, uy = dx / length, dy / length
        nx, ny = -uy, ux
        # square cap で両端を half だけ延長
        ex1, ey1 = x1 - ux * half, y1 - uy * half
        ex2, ey2 = x2 + ux * half, y2 + uy * half
        # 4 頂点
        p0 = (ex1 + nx * half, ey1 + ny * half)
        p1 = (ex2 + nx * half, ey2 + ny * half)
        p2 = (ex2 - nx * half, ey2 - ny * half)
        p3 = (ex1 - nx * half, ey1 - ny * half)
        base = len(verts)
        for px, py in (p0, p1, p2, p3):
            verts.append((mm_to_m(px), mm_to_m(py), 0.0))
        indices.append((base, base + 1, base + 2))
        indices.append((base, base + 2, base + 3))
    if not verts:
        return
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_tapered_segments_mm(
    segs: list[tuple[tuple[float, float], tuple[float, float]]],
    color: tuple[float, float, float, float],
    start_width_mm: float,
    end_width_mm: float,
) -> None:
    if not segs:
        return
    import math as _math

    start_half = max(0.0, float(start_width_mm)) * 0.5
    end_half = max(0.0, float(end_width_mm)) * 0.5
    if start_half <= 0.0 and end_half <= 0.0:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts: list[tuple[float, float, float]] = []
    indices: list[tuple[int, int, int]] = []
    for (x1, y1), (x2, y2) in segs:
        dx = x2 - x1
        dy = y2 - y1
        length = _math.hypot(dx, dy)
        if length <= 0.0:
            continue
        nx, ny = -dy / length, dx / length
        base = len(verts)
        if start_half <= 0.0:
            for px, py in (
                (x1, y1),
                (x2 + nx * end_half, y2 + ny * end_half),
                (x2 - nx * end_half, y2 - ny * end_half),
            ):
                verts.append((mm_to_m(px), mm_to_m(py), 0.0))
            indices.append((base, base + 1, base + 2))
        elif end_half <= 0.0:
            for px, py in (
                (x1 + nx * start_half, y1 + ny * start_half),
                (x1 - nx * start_half, y1 - ny * start_half),
                (x2, y2),
            ):
                verts.append((mm_to_m(px), mm_to_m(py), 0.0))
            indices.append((base, base + 1, base + 2))
        else:
            for px, py in (
                (x1 + nx * start_half, y1 + ny * start_half),
                (x1 - nx * start_half, y1 - ny * start_half),
                (x2 - nx * end_half, y2 - ny * end_half),
                (x2 + nx * end_half, y2 + ny * end_half),
            ):
                verts.append((mm_to_m(px), mm_to_m(py), 0.0))
            indices.append((base, base + 1, base + 2))
            indices.append((base, base + 2, base + 3))
    if not verts:
        return
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_styled_segment_mm(
    p0: tuple[float, float],
    p1: tuple[float, float],
    color: tuple[float, float, float, float],
    width_mm: float,
    style: str = "solid",
) -> None:
    for start, end, width in stroke_style.styled_segments_for_line(p0, p1, width_mm, style):
        _draw_segments_mm([(start, end)], color, width_mm=width)


def _draw_styled_path_mm(
    pts: list[tuple[float, float]],
    color: tuple[float, float, float, float],
    width_mm: float,
    style: str = "solid",
    *,
    closed: bool = True,
) -> None:
    for start, end, width in stroke_style.styled_segments_for_path(
        pts,
        width_mm,
        style,
        closed=closed,
    ):
        _draw_segments_mm([(start, end)], color, width_mm=width)


def _draw_line_segments(
    segs: list[tuple[tuple[float, float], tuple[float, float]]],
    color: tuple[float, float, float, float],
    line_width: float = 1.0,
    width_mm: float | None = None,
    start_width_mm: float | None = None,
    end_width_mm: float | None = None,
) -> None:
    """複数の独立した線分 ((x1,y1)-(x2,y2) の集合、mm 単位) を一括描画."""
    if not segs:
        return
    if start_width_mm is not None or end_width_mm is not None:
        base = max(0.001, float(width_mm if width_mm is not None else 0.3))
        _draw_tapered_segments_mm(
            segs,
            color,
            start_width_mm=base if start_width_mm is None else float(start_width_mm),
            end_width_mm=base if end_width_mm is None else float(end_width_mm),
        )
        return
    if width_mm is not None:
        _draw_segments_mm(segs, color, width_mm=max(0.001, float(width_mm)))
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts: list[tuple[float, float, float]] = []
    for (x1, y1), (x2, y2) in segs:
        verts.append((mm_to_m(x1), mm_to_m(y1), 0.0))
        verts.append((mm_to_m(x2), mm_to_m(y2), 0.0))
    batch = batch_for_shader(shader, "LINES", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", color)
    try:
        gpu.state.line_width_set(max(1.0, float(line_width)))
        batch.draw(shader)
    finally:
        gpu.state.line_width_set(1.0)


def _draw_trim_marks(
    finish: Rect,
    bleed: Rect,
    color: tuple[float, float, float, float] = viewport_colors.PAPER_GUIDE_LIGHT,
    corner_arm_mm: float = 10.0,
    center_size_mm: float = 10.0,
    center_gap_mm: float = 5.0,
    line_width: float = 1.0,
) -> None:
    """トンボ (コーナー + センタートンボ) を CLIP STUDIO PAINT と同じ仕様で描画.

    コーナートンボ (4 隅): 二重 L 字 (裁ち落とし枠の角の外側にのみ描画)
      - 内側 L: 仕上がり枠の辺の延長線。裁ち落とし枠の角から外側へ
        ``corner_arm_mm`` 伸びる横線・縦線で、座標は finish 辺と同じ
      - 外側 L: 裁ち落とし枠の辺の延長線。bleed 角から外側へ ``corner_arm_mm``
        伸びる横線・縦線で、座標は bleed 辺と同じ
      - 仕上がり枠と裁ち落とし枠の間 (= 裁ち落とし領域内側) には線を描かない

    センタートンボ (4 辺中央): 十字 (+) マーク
      - 各辺中央に + 字、裁ち落とし枠の外側 ``center_gap_mm`` 離れた位置に
        配置。十字の腕長 = ``center_size_mm`` の半分。
    """
    fr, br = finish, bleed
    A = corner_arm_mm
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []

    # --- コーナートンボ (4 隅) ---
    # 各コーナーで 4 本: 内側 L (仕上がり延長 H/V) + 外側 L (裁ち落とし延長 H/V)
    # Bottom-Left: 仕上がり線を左/下方向に延長、裁ち落とし線も左/下方向に延長
    segs.append(((br.x - A, fr.y), (br.x, fr.y)))    # 内 L 横 (仕上がり Y = fr.y)
    segs.append(((fr.x, br.y - A), (fr.x, br.y)))    # 内 L 縦 (仕上がり X = fr.x)
    segs.append(((br.x - A, br.y), (br.x, br.y)))    # 外 L 横 (裁ち落とし Y = br.y)
    segs.append(((br.x, br.y - A), (br.x, br.y)))    # 外 L 縦 (裁ち落とし X = br.x)
    # Bottom-Right
    segs.append(((br.x2, fr.y), (br.x2 + A, fr.y)))
    segs.append(((fr.x2, br.y - A), (fr.x2, br.y)))
    segs.append(((br.x2, br.y), (br.x2 + A, br.y)))
    segs.append(((br.x2, br.y - A), (br.x2, br.y)))
    # Top-Left
    segs.append(((br.x - A, fr.y2), (br.x, fr.y2)))
    segs.append(((fr.x, br.y2), (fr.x, br.y2 + A)))
    segs.append(((br.x - A, br.y2), (br.x, br.y2)))
    segs.append(((br.x, br.y2), (br.x, br.y2 + A)))
    # Top-Right
    segs.append(((br.x2, fr.y2), (br.x2 + A, fr.y2)))
    segs.append(((fr.x2, br.y2), (fr.x2, br.y2 + A)))
    segs.append(((br.x2, br.y2), (br.x2 + A, br.y2)))
    segs.append(((br.x2, br.y2), (br.x2, br.y2 + A)))

    # --- センタートンボ (4 辺中央の十字) ---
    cx_mid = (fr.x + fr.x2) * 0.5
    cy_mid = (fr.y + fr.y2) * 0.5
    half = center_size_mm * 0.5
    gap = center_gap_mm
    # 上辺中央: 裁ち落とし枠の上側に + 字
    cy_top = br.y2 + gap + half
    segs.append(((cx_mid, cy_top - half), (cx_mid, cy_top + half)))
    segs.append(((cx_mid - half, cy_top), (cx_mid + half, cy_top)))
    # 下辺中央
    cy_bot = br.y - gap - half
    segs.append(((cx_mid, cy_bot - half), (cx_mid, cy_bot + half)))
    segs.append(((cx_mid - half, cy_bot), (cx_mid + half, cy_bot)))
    # 左辺中央
    cx_left = br.x - gap - half
    segs.append(((cx_left, cy_mid - half), (cx_left, cy_mid + half)))
    segs.append(((cx_left - half, cy_mid), (cx_left + half, cy_mid)))
    # 右辺中央
    cx_right = br.x2 + gap + half
    segs.append(((cx_right, cy_mid - half), (cx_right, cy_mid + half)))
    segs.append(((cx_right - half, cy_mid), (cx_right + half, cy_mid)))

    _draw_line_segments(segs, color, line_width=line_width)


def _draw_frame_with_hole(outer: Rect, inner: Rect, color: tuple[float, float, float, float]) -> None:
    """外側 outer を塗って内側 inner を穴抜きした「額縁」形状を描画."""
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    # 4 本の帯で外周を塗る (上下左右)
    top = Rect(outer.x, inner.y2, outer.width, outer.y2 - inner.y2)
    bottom = Rect(outer.x, outer.y, outer.width, inner.y - outer.y)
    left = Rect(outer.x, inner.y, inner.x - outer.x, inner.height)
    right = Rect(inner.x2, inner.y, outer.x2 - inner.x2, inner.height)
    verts: list[tuple[float, float, float]] = []
    indices: list[tuple[int, int, int]] = []
    for r in (top, bottom, left, right):
        if r.width <= 0 or r.height <= 0:
            continue
        base = len(verts)
        verts.extend(
            [
                (mm_to_m(r.x), mm_to_m(r.y), 0.0),
                (mm_to_m(r.x2), mm_to_m(r.y), 0.0),
                (mm_to_m(r.x2), mm_to_m(r.y2), 0.0),
                (mm_to_m(r.x), mm_to_m(r.y2), 0.0),
            ]
        )
        indices.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])
    if not verts:
        return
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


# ---------- draw_handler 本体 ----------


class _SharedLayerProxy:
    def __init__(self, work):
        self.id = "__outside__"
        self.balloons = getattr(work, "shared_balloons", [])
        self.texts = getattr(work, "shared_texts", [])
        self.active_balloon_index = -1
        self.active_text_index = -1


def _shared_coma_polygon(entry) -> list[tuple[float, float]]:
    if getattr(entry, "shape_type", "") == "rect":
        x = float(getattr(entry, "rect_x_mm", 0.0))
        y = float(getattr(entry, "rect_y_mm", 0.0))
        w = float(getattr(entry, "rect_width_mm", 0.0))
        h = float(getattr(entry, "rect_height_mm", 0.0))
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    vertices = getattr(entry, "vertices", [])
    return [(float(v.x_mm), float(v.y_mm)) for v in vertices]


def _draw_shared_layers(work) -> None:
    """ページ外レイヤーを world mm 座標のまま描画する."""
    for entry in sorted(
        list(getattr(work, "shared_comas", [])),
        key=lambda panel: int(getattr(panel, "z_order", 0)),
    ):
        if not getattr(entry, "visible", True):
            continue
        poly = _shared_coma_polygon(entry)
        if len(poly) < 3:
            continue
        _draw_polyline_loop(poly, viewport_colors.PAPER_GUIDE, line_width=2.0, width_mm=0.5)

    proxy = _SharedLayerProxy(work)
    overlay_text.draw_text_guides(
        proxy,
        context=bpy.context,
        ox_mm=0.0,
        oy_mm=0.0,
        active=getattr(bpy.context.scene, "bname_active_layer_kind", "") == "text",
        entry_visible=lambda entry: bool(getattr(entry, "visible", True)),
        draw_rect_fill=_draw_rect_fill,
        draw_rect_outline=_draw_rect_outline,
    )


def _draw_polygon_fill(pts: list[tuple[float, float]], color) -> None:
    if len(pts) < 3:
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts = [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in pts]
    indices = [(0, i, i + 1) for i in range(1, len(pts) - 1)]
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_stroke_band_fill(
    outer_pts: list[tuple[float, float]],
    inner_pts: list[tuple[float, float]],
    color,
) -> None:
    if len(outer_pts) < 3 or len(inner_pts) != len(outer_pts):
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts = [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in outer_pts + inner_pts]
    n = len(outer_pts)
    indices: list[tuple[int, int, int]] = []
    for i in range(n):
        j = (i + 1) % n
        indices.append((i, j, n + j))
        indices.append((i, n + j, n + i))
    batch = batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_polyline_loop(
    pts: list[tuple[float, float]],
    color,
    line_width: float = 1.0,
    *,
    style: str = "solid",
    width_mm: float | None = None,
) -> None:
    if len(pts) < 2:
        return
    if str(style or "solid") != "solid" or width_mm is not None:
        _draw_styled_path_mm(
            pts,
            color,
            max(0.001, float(width_mm if width_mm is not None else line_width * 0.25)),
            style,
            closed=True,
        )
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    verts = [(mm_to_m(x), mm_to_m(y), 0.0) for x, y in pts] + [(mm_to_m(pts[0][0]), mm_to_m(pts[0][1]), 0.0)]
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": verts})
    shader.bind()
    shader.uniform_float("color", color)
    try:
        gpu.state.line_width_set(max(1.0, float(line_width)))
        batch.draw(shader)
    finally:
        gpu.state.line_width_set(1.0)


def _resolve_active_region(context):
    """draw_handler 用に WINDOW region と rv3d を確実に取得.

    Blender 5.x の POST_VIEW callback 内では ``context.region`` /
    ``context.region_data`` が None になるケースがあるため、
    context.area からスキャンして WINDOW region と rv3d を取得する fallback。
    """
    region = getattr(context, "region", None)
    rv3d = getattr(context, "region_data", None)
    if region is not None and rv3d is not None and getattr(region, "type", "") == "WINDOW":
        return region, rv3d
    area = getattr(context, "area", None)
    if area is None or area.type != "VIEW_3D":
        # 全 screen を巡回して最初の VIEW_3D area を探す (callback 中の area が
        # 別タイプだった場合のフォールバック)
        screen = getattr(context, "screen", None)
        if screen is not None:
            for a in screen.areas:
                if a.type == "VIEW_3D":
                    area = a
                    break
    if area is None:
        return None, None
    found_region = None
    for r in area.regions:
        if r.type == "WINDOW":
            found_region = r
            break
    if found_region is None:
        return None, None
    space = area.spaces.active
    found_rv3d = getattr(space, "region_3d", None)
    return found_region, found_rv3d


def _draw_text_in_rect(context, rect, entry_or_text, color=(0, 0, 0, 1)) -> None:
    """``rect`` (mm) の中にテキストレイヤーを blf で描画する."""
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from mathutils import Vector

    region, rv3d = _resolve_active_region(context)
    if region is None or rv3d is None:
        return
    font_id = _get_jp_font_id()

    if isinstance(entry_or_text, str):
        text = entry_or_text
        world = Vector((mm_to_m(rect.x + 1.0), mm_to_m(rect.y2 - 1.0), 0.0))
        coord = location_3d_to_region_2d(region, rv3d, world)
        if coord is None:
            return
        try:
            blf.size(font_id, 14.0)
        except Exception:  # noqa: BLE001
            pass
        try:
            blf.color(font_id, color[0], color[1], color[2], color[3])
        except Exception:  # noqa: BLE001
            pass
        try:
            _, th = blf.dimensions(font_id, text)
        except Exception:  # noqa: BLE001
            th = 14.0
        blf.position(font_id, float(coord.x), float(coord.y) - th, 0.0)
        blf.draw(font_id, text)
        return

    entry = entry_or_text
    padded = text_layout_bounds.text_inner_rect(rect)
    try:
        from ..typography import layout as text_layout, ruby as text_ruby

        result = text_layout.typeset(
            entry,
            padded.x,
            padded.y,
            padded.width,
            padded.height,
        )
        ruby_placements = text_ruby.compute_ruby_placements(
            result.placements,
            getattr(entry, "ruby_spans", []) or [],
            writing_mode=str(getattr(entry, "writing_mode", "vertical") or "vertical"),
        )
    except Exception:  # noqa: BLE001
        _logger.exception("text layout failed")
        return

    o0 = location_3d_to_region_2d(region, rv3d, Vector((0.0, 0.0, 0.0)))
    o1 = location_3d_to_region_2d(region, rv3d, Vector((mm_to_m(1.0), 0.0, 0.0)))
    if o0 is not None and o1 is not None:
        px_per_mm = abs(float(o1.x) - float(o0.x))
    else:
        px_per_mm = 3.78
    for glyph in result.placements:
        glyph_font_id = _get_font_id_for_path(text_style.font_for_index(entry, glyph.index))
        coord = location_3d_to_region_2d(
            region,
            rv3d,
            Vector((mm_to_m(glyph.x_mm), mm_to_m(glyph.y_mm), 0.0)),
        )
        if coord is None:
            continue
        size_px = glyph.size_pt * px_per_mm * 25.4 / 72.0
        try:
            blf.size(glyph_font_id, max(1, int(size_px)))
        except Exception:  # noqa: BLE001
            pass
        entry_color = text_style.color_for_index(entry, glyph.index)
        try:
            blf.color(
                glyph_font_id,
                float(entry_color[0]),
                float(entry_color[1]),
                float(entry_color[2]),
                float(entry_color[3]),
            )
        except Exception:  # noqa: BLE001
            pass
        x_px = float(coord.x)
        y_px = float(coord.y)
        stroke_width_px = 0.0
        if getattr(entry, "stroke_enabled", False):
            stroke_width_px = max(1.0, float(getattr(entry, "stroke_width_mm", 0.2)) * max(px_per_mm, 0.1))
            stroke_color = getattr(entry, "stroke_color", (1.0, 1.0, 1.0, 1.0))
            try:
                blf.color(
                    glyph_font_id,
                    float(stroke_color[0]),
                    float(stroke_color[1]),
                    float(stroke_color[2]),
                    float(stroke_color[3]),
                )
            except Exception:  # noqa: BLE001
                pass
            offsets = (
                (-stroke_width_px, 0.0),
                (stroke_width_px, 0.0),
                (0.0, -stroke_width_px),
                (0.0, stroke_width_px),
                (-stroke_width_px * 0.707, -stroke_width_px * 0.707),
                (stroke_width_px * 0.707, -stroke_width_px * 0.707),
                (-stroke_width_px * 0.707, stroke_width_px * 0.707),
                (stroke_width_px * 0.707, stroke_width_px * 0.707),
            )
            for ox, oy in offsets:
                blf.position(glyph_font_id, x_px + ox, y_px + oy, 0.0)
                blf.draw(glyph_font_id, glyph.ch)
        blf.position(glyph_font_id, x_px, y_px, 0.0)
        try:
            blf.color(
                glyph_font_id,
                float(entry_color[0]),
                float(entry_color[1]),
                float(entry_color[2]),
                float(entry_color[3]),
            )
        except Exception:  # noqa: BLE001
            pass
        blf.draw(glyph_font_id, glyph.ch)
        if text_style.bold_for_index(entry, glyph.index):
            blf.position(glyph_font_id, x_px + max(1.0, size_px * 0.035), y_px, 0.0)
            blf.draw(glyph_font_id, glyph.ch)
        if text_style.italic_for_index(entry, glyph.index):
            # BLF has no shear flag; draw a slight upper-right echo so the
            # setting has an immediate viewport-visible effect.
            blf.position(glyph_font_id, x_px + max(1.0, size_px * 0.055), y_px + max(1.0, size_px * 0.025), 0.0)
            blf.draw(glyph_font_id, glyph.ch)
    ruby_font_id = _get_font_id_for_path(str(getattr(entry, "font", "") or ""))
    ruby_color = getattr(entry, "color", (0.0, 0.0, 0.0, 1.0))
    for ruby_glyph in ruby_placements:
        coord = location_3d_to_region_2d(
            region,
            rv3d,
            Vector((mm_to_m(ruby_glyph.x_mm), mm_to_m(ruby_glyph.y_mm), 0.0)),
        )
        if coord is None:
            continue
        size_px = ruby_glyph.size_pt * px_per_mm * 25.4 / 72.0
        try:
            blf.size(ruby_font_id, max(1, int(size_px)))
        except Exception:  # noqa: BLE001
            pass
        try:
            blf.color(ruby_font_id, float(ruby_color[0]), float(ruby_color[1]), float(ruby_color[2]), float(ruby_color[3]))
        except Exception:  # noqa: BLE001
            pass
        blf.position(ruby_font_id, float(coord.x), float(coord.y), 0.0)
        blf.draw(ruby_font_id, ruby_glyph.ch)


def _draw_rect_fill_pixel(context, rect: Rect, color: tuple[float, float, float, float]) -> None:
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from mathutils import Vector

    region, rv3d = _resolve_active_region(context)
    if region is None or rv3d is None:
        return
    coords = []
    for x_mm, y_mm in (
        (rect.x, rect.y),
        (rect.x2, rect.y),
        (rect.x2, rect.y2),
        (rect.x, rect.y2),
    ):
        coord = location_3d_to_region_2d(
            region,
            rv3d,
            Vector((mm_to_m(x_mm), mm_to_m(y_mm), 0.0)),
        )
        if coord is None:
            return
        coords.append((float(coord.x), float(coord.y)))
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(
        shader,
        "TRIS",
        {"pos": coords},
        indices=[(0, 1, 2), (0, 2, 3)],
    )
    gpu.state.blend_set("ALPHA")
    try:
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)
    finally:
        gpu.state.blend_set("NONE")


def _edge_selection_targets_coma(work, page, wm) -> bool:
    if (
        wm is None
        or getattr(wm, "bname_edge_select_kind", "none")
        not in {"edge", "border", "vertex"}
    ):
        return False
    page_index = int(getattr(wm, "bname_edge_select_page", -1))
    coma_index = int(getattr(wm, "bname_edge_select_coma", -1))
    if not (0 <= page_index < len(work.pages)):
        return False
    selected_page = work.pages[page_index]
    if str(getattr(selected_page, "id", "") or "") != str(getattr(page, "id", "") or ""):
        return False
    return 0 <= coma_index < len(getattr(page, "comas", []))


def _draw_comas(
    work,
    page,
    ox_mm: float = 0.0,
    oy_mm: float = 0.0,
    *,
    skip_preview_stem: str = "",
) -> None:
    """ページ内のコマ枠・白フチを Z 順に従って描画.

    Z順序昇順 (背面→手前) で描画することで重なり時も正しく表示される。
    rect / polygon の両形状をサポート (枠線カット後は polygon になる)。
    自動くり抜きは Phase 2 段階では未実装。
    """
    active_stem = ""
    scene = getattr(bpy.context, "scene", None)
    active_kind = getattr(scene, "bname_active_layer_kind", "") if scene is not None else ""
    active_page_idx = int(getattr(work, "active_page_index", -1))
    active_page = work.pages[active_page_idx] if 0 <= active_page_idx < len(work.pages) else None
    wm = getattr(bpy.context, "window_manager", None)
    edge_selection_matches = _edge_selection_targets_coma(work, page, wm)
    is_active_page = (
        active_kind == "coma"
        and not edge_selection_matches
        and active_page is not None
        and str(getattr(active_page, "id", "") or "") == str(getattr(page, "id", "") or "")
    )
    if is_active_page:
        active_idx = int(getattr(page, "active_coma_index", -1))
        if 0 <= active_idx < len(page.comas):
            active_stem = str(getattr(page.comas[active_idx], "coma_id", "") or "")
    sorted_comas = sorted(page.comas, key=lambda p: p.z_order)
    for entry in sorted_comas:
        if not overlay_visibility.coma_visible(entry):
            continue
        # ポリゴン頂点リスト (mm) を取得 — rect なら 4 隅、polygon なら vertices
        if entry.shape_type == "rect":
            poly = [
                (entry.rect_x_mm, entry.rect_y_mm),
                (entry.rect_x_mm + entry.rect_width_mm, entry.rect_y_mm),
                (entry.rect_x_mm + entry.rect_width_mm,
                 entry.rect_y_mm + entry.rect_height_mm),
                (entry.rect_x_mm, entry.rect_y_mm + entry.rect_height_mm),
            ]
        elif entry.shape_type == "polygon" and len(entry.vertices) >= 3:
            poly = [(v.x_mm, v.y_mm) for v in entry.vertices]
        else:
            continue
        # ページオフセットを加算
        if ox_mm != 0.0 or oy_mm != 0.0:
            poly = [(x + ox_mm, y + oy_mm) for x, y in poly]
        # コマの背景・枠線・白フチは実体オブジェクト側で表示する。
        # オーバーレイ側は選択中コマの補助線だけを描き、実体表示と二重に
        # ならないようにする。
        is_active_coma = (
            bool(active_stem)
            and str(getattr(entry, "coma_id", "") or "") == active_stem
        )
        if is_active_coma:
            segs = [
                (poly[i], poly[(i + 1) % len(poly)])
                for i in range(len(poly))
            ]
            _draw_segments_mm(segs, viewport_colors.SELECTION_STRONG, width_mm=1.20)


def _translate_rect(r: Rect, ox_mm: float, oy_mm: float) -> Rect:
    """Rect を (ox_mm, oy_mm) だけ平行移動."""
    if ox_mm == 0.0 and oy_mm == 0.0:
        return r
    return Rect(r.x + ox_mm, r.y + oy_mm, r.width, r.height)


def _draw_canvas_fill_only(paper, rects, ox_mm: float, oy_mm: float) -> None:
    """キャンバス塗り (用紙背景の白) を POST_VIEW で描画する.

    `paper_color` は Blender COLOR プロパティ (scene-linear) なので UI 表示
    相当の sRGB に戻し、不透明 (alpha=1.0) で描く。

    深度テストは ``LESS_EQUAL`` を維持。raster 材質は DITHERED で depth を
    書込むため、ラスター画素では canvas (z=0) の depth (= 大きい値) が
    ラスター (z=0.005, depth = 小さい値) との LESS_EQUAL に失敗して却下
    される → ラスター画素は用紙塗りに上書きされない。
    """
    canvas_r = _translate_rect(rects.canvas, ox_mm, oy_mm)
    r, g, b = color_space.linear_to_srgb_rgb(paper.paper_color[:3])
    canvas_color = (
        r,
        g,
        b,
        1.0,
    )
    try:
        previous_depth = gpu.state.depth_test_get()
    except Exception:  # noqa: BLE001
        previous_depth = "NONE"
    try:
        gpu.state.depth_test_set("LESS_EQUAL")
        _draw_rect_fill(canvas_r, canvas_color)
    finally:
        try:
            gpu.state.depth_test_set(previous_depth)
        except Exception:  # noqa: BLE001
            pass


def _paint_mode_hides_paper_bg(context) -> bool:
    mode = str(getattr(context, "mode", "") or "")
    return mode in {
        "TEXTURE_PAINT",
        "PAINT_TEXTURE",
        "PAINT_GREASE_PENCIL",
        "EDIT_GREASE_PENCIL",
        "SCULPT_GREASE_PENCIL",
        "VERTEX_GREASE_PENCIL",
        "WEIGHT_GREASE_PENCIL",
    }


def _draw_page_overlay(
    context,
    work,
    paper,
    rects,
    page,
    mode: str,
    ox_mm: float = 0.0,
    oy_mm: float = 0.0,
    draw_image_layers: bool = True,
    is_left_half: bool = False,
    phase: str = "post",
) -> None:
    """1 ページ分のガイド/コマ枠を (ox_mm, oy_mm) オフセットで描画.

    ``is_left_half=True`` (見開きの左半分のページ) の場合、ノド/小口/
    inner_frame 横オフセットを左右反転して再計算する。

    ``phase`` (将来拡張用): 現在は ``"post"`` のみ使用。POST_VIEW で
    用紙塗り + 枠/トンボ/ガイドを順に描画する。ラスター材質を DITHERED
    にしたため depth_test_set("LESS_EQUAL") が機能し、用紙塗りはラスター
    画素を上書きしない。
    """
    if not overlay_visibility.page_visible(page):
        return
    # is_left_half が True の場合は per-page で rects を再計算 (左右反転対応)
    if is_left_half:
        rects = overlay_shared.compute_paper_rects(paper, is_left_half=True)

    # 通常時の用紙白背景は paper_bg_object.py の opaque な Mesh が表示する。
    # 描画モード中だけ paper_bg を raycast 回避で隠すため、同じ見た目を保つ
    # 代替として深度つきの GPU 塗りを入れる。
    if _paint_mode_hides_paper_bg(context):
        _draw_canvas_fill_only(paper, rects, ox_mm, oy_mm)

    # 用紙の基本枠 / 仕上がり・裁ち落とし枠 / セーフライン / トンボ /
    # セーフライン外の暗い表示は実体オブジェクトが描画するため、
    # オーバーレイ側では二重描画しない。

    # 画像レイヤー (アクティブページのみ — 全ページ一覧時は負荷とレイヤーの per-scene 制約で省略)
    if mode == MODE_PAGE and draw_image_layers:
        overlay_image.draw_image_layers(context.scene)
        _draw_shared_layers(work)

    # コマ枠 / フキダシ / テキスト。コマ編集モードでは参照表示として描く。
    if mode in (MODE_PAGE, MODE_COMA) and page is not None:
        skip_stem = ""
        if mode == MODE_COMA:
            skip_stem = getattr(context.scene, "bname_current_coma_id", "")
        _draw_comas(work, page, ox_mm=ox_mm, oy_mm=oy_mm, skip_preview_stem=skip_stem)
        active_text_guides = False
        if getattr(context.scene, "bname_active_layer_kind", "") == "text":
            active_idx = int(getattr(work, "active_page_index", -1))
            if 0 <= active_idx < len(work.pages):
                active_page = work.pages[active_idx]
                active_text_guides = (
                    active_page == page
                    or str(getattr(active_page, "id", "") or "")
                    == str(getattr(page, "id", "") or "")
                )
        overlay_text.draw_text_guides(
            page,
            context=context,
            ox_mm=ox_mm,
            oy_mm=oy_mm,
            active=active_text_guides,
            entry_visible=lambda entry: overlay_visibility.entry_in_visible_coma(page, entry),
            draw_rect_fill=_draw_rect_fill,
            draw_rect_outline=_draw_rect_outline,
        )

    # 作品情報とページ番号は実体のテキストオブジェクトで表示する。


def _resolve_page_index(work, ox_mm: float, oy_mm: float) -> int:
    """ox/oy オフセットからページ index を逆引き (overview 描画時の各ページ向け).

    ox=oy=0 ならアクティブページ index、それ以外なら overview の grid から逆引き。
    対応するページが見つからなければ -1。
    """
    if ox_mm == 0.0 and oy_mm == 0.0:
        return work.active_page_index
    paper = work.paper
    cw = paper.canvas_width_mm
    ch = paper.canvas_height_mm
    if cw <= 0 or ch <= 0:
        return work.active_page_index
    from ..utils.page_grid import page_grid_offset_mm as _pg_offset
    cols = max(1, int(getattr(bpy.context.scene, "bname_overview_cols", 4)))
    gap = float(getattr(bpy.context.scene, "bname_overview_gap_mm", 30.0))
    start_side = getattr(paper, "start_side", "right")
    read_direction = getattr(paper, "read_direction", "left")
    eps = 0.5  # mm 単位の許容誤差
    for i in range(len(work.pages)):
        ox_i, oy_i = _pg_offset(
            i, cols, gap, cw, ch, start_side, read_direction, work=work
        )
        ox_i, oy_i = _with_page_manual_offset(work, i, ox_i, oy_i)
        if abs(ox_i - ox_mm) < eps and abs(oy_i - oy_mm) < eps:
            return i
    return -1


def _with_page_manual_offset(work, page_index: int, ox_mm: float, oy_mm: float):
    try:
        from ..utils import page_grid

        page = work.pages[page_index] if 0 <= page_index < len(work.pages) else None
        add_x, add_y = page_grid.page_manual_offset_mm(page)
        return ox_mm + add_x, oy_mm + add_y
    except Exception:  # noqa: BLE001
        return ox_mm, oy_mm


def _page_overview_offset(
    context,
    work,
    page_index: int,
    cols: int,
    gap: float,
    cw: float,
    ch: float,
    start_side: str,
    read_direction: str,
    *,
    is_page_browser: bool,
) -> tuple[float, float]:
    if is_page_browser and page_browser.fit_enabled(context.scene):
        return page_browser.page_offset_mm(
            work,
            context.scene,
            getattr(context, "area", None),
            page_index,
        )
    from ..utils.page_grid import page_grid_offset_mm as _pg_offset

    ox, oy = _pg_offset(page_index, cols, gap, cw, ch, start_side, read_direction, work=work)
    return _with_page_manual_offset(work, page_index, ox, oy)


def _page_file_current_page_index(scene, work) -> int:
    """ページファイルで実体編集しているページ index を返す."""
    try:
        idx = page_file_scene.find_page_index(work, page_file_scene.current_page_id(scene))
        if idx >= 0:
            return idx
    except Exception:  # noqa: BLE001
        pass
    try:
        pages_len = len(getattr(work, "pages", []) or [])
        if pages_len <= 0:
            return -1
        return max(0, min(pages_len - 1, int(getattr(work, "active_page_index", 0))))
    except Exception:  # noqa: BLE001
        return -1


def _page_file_overview_indices(scene, work) -> set[int] | None:
    """ページファイルで補助表示を許可するページ index 群を返す.

    None は通常のページ一覧ファイルを表し、従来どおり全ページが対象。
    ページファイルでは、現在ページに加えて「ページ一覧表示」で指定された
    前後ページだけを対象にする。
    """
    try:
        if not page_file_scene.is_page_edit_scene(scene):
            return None
    except Exception:  # noqa: BLE001
        return None
    pages_len = len(getattr(work, "pages", []) or [])
    if pages_len <= 0:
        return set()
    current_index = _page_file_current_page_index(scene, work)
    indices: set[int] = set()
    if page_preview_object.preview_enabled(scene):
        indices.update(page_preview_object.preview_page_indices(scene, work))
    if current_index >= 0:
        indices.add(current_index)
    return {i for i in indices if 0 <= i < pages_len}


def _format_page_header_number(page_index: int, work=None) -> str:
    """作品の開始番号に従って、ページ番号を 001 形式にする。"""
    try:
        start = int(getattr(getattr(work, "work_info", None), "page_number_start", 1))
    except Exception:  # noqa: BLE001
        start = 1
    return f"{max(0, start + int(page_index)):03d}"


def _draw_bold_pixel_text(
    font_id: int,
    text: str,
    x_px: float,
    y_px: float,
    *,
    color: tuple[float, float, float, float],
    outline_color: tuple[float, float, float, float],
) -> None:
    """blf に太字指定が無い環境でも、重ね描きで太字風に表示する。"""
    outline_offsets = (
        (-2.0, -2.0), (-2.0, 0.0), (-2.0, 2.0),
        (0.0, -2.0), (0.0, 2.0),
        (2.0, -2.0), (2.0, 0.0), (2.0, 2.0),
    )
    try:
        blf.color(font_id, *outline_color)
    except Exception:  # noqa: BLE001
        pass
    for dx, dy in outline_offsets:
        blf.position(font_id, x_px + dx, y_px + dy, 0.0)
        blf.draw(font_id, text)

    bold_offsets = ((0.0, 0.0), (0.9, 0.0), (0.0, 0.9), (0.9, 0.9))
    try:
        blf.color(font_id, *color)
    except Exception:  # noqa: BLE001
        pass
    for dx, dy in bold_offsets:
        blf.position(font_id, x_px + dx, y_px + dy, 0.0)
        blf.draw(font_id, text)


def _draw_page_header_number_pixel(
    context,
    paper,
    page_index: int,
    ox_mm: float,
    oy_mm: float,
) -> None:
    """ページキャンバス上端の外側に 001 形式の大きな番号を描画する。"""
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    from mathutils import Vector

    region, rv3d = _resolve_active_region(context)
    if region is None or rv3d is None:
        return
    rects = overlay_shared.compute_paper_rects(paper)
    x_mm = rects.canvas.x + rects.canvas.width * 0.5 + ox_mm
    y_mm = rects.canvas.y2 + _PAGE_HEADER_GAP_MM + oy_mm
    coord = location_3d_to_region_2d(
        region,
        rv3d,
        Vector((mm_to_m(x_mm), mm_to_m(y_mm), 0.0)),
    )
    if coord is None:
        return
    if not (-300 < coord.x < region.width + 300 and -300 < coord.y < region.height + 300):
        return

    text = _format_page_header_number(page_index, get_work(context))
    font_id = _get_jp_font_id()
    try:
        blf.size(font_id, _PAGE_HEADER_FONT_SIZE_PX)
    except Exception:  # noqa: BLE001
        pass
    try:
        tw, th = blf.dimensions(font_id, text)
    except Exception:  # noqa: BLE001
        tw, th = 0.0, float(_PAGE_HEADER_FONT_SIZE_PX)
    sx = float(coord.x) - tw * 0.5
    sy = float(coord.y) - th * 0.5
    _draw_bold_pixel_text(
        font_id,
        text,
        sx,
        sy,
        color=_PAGE_HEADER_COLOR,
        outline_color=_PAGE_HEADER_OUTLINE_COLOR,
    )


def _should_highlight_active_page(context) -> bool:
    scene = getattr(context, "scene", None)
    if scene is None or not hasattr(scene, "bname_active_layer_kind"):
        return True
    return getattr(scene, "bname_active_layer_kind", "") == "page"


def _page_highlight_rect(rects, ox_mm: float, oy_mm: float) -> Rect:
    canvas_r = _translate_rect(rects.canvas, ox_mm, oy_mm)
    return canvas_r.inset(-5.0)


def _draw_page_highlight(rect: Rect | None) -> None:
    if rect is None:
        return
    previous_depth = None
    try:
        previous_depth = gpu.state.depth_test_get()
    except Exception:  # noqa: BLE001
        previous_depth = None
    try:
        gpu.state.depth_test_set("NONE")
        _draw_rect_outline(rect, viewport_colors.SELECTION, width_mm=1.00)
    finally:
        try:
            gpu.state.depth_test_set(previous_depth or "LESS_EQUAL")
        except Exception:  # noqa: BLE001
            pass


def _draw_callback(phase: str = "post") -> None:
    context = bpy.context
    work = get_work(context)
    if work is None or not work.loaded:
        return
    # B-Name オーバーレイ全体の表示切替 (Phase 3c: Object 表示モード時は OFF)
    scene_root = context.scene
    if scene_root is not None and not bool(
        getattr(scene_root, "bname_overlay_enabled", True)
    ):
        return
    mode = get_mode(context)
    is_page_browser = page_browser.is_page_browser_area(context)
    if mode == MODE_COMA and not is_page_browser:
        return
    paper = work.paper
    rects = overlay_shared.compute_paper_rects(paper)
    scene = context.scene
    region, rv3d = _resolve_active_region(context)

    gpu.state.blend_set("ALPHA")
    # depth_test を有効にして、3D Mesh (raster plane など) との前後関係を
    # オーバーレイ描画でも考慮する。これがないと用紙背景が常に最前面に
    # 描画され、Mesh に paint した内容が見えなくなる。
    gpu.state.depth_test_set("LESS_EQUAL")
    try:
        if (
            (
                mode == MODE_PAGE
                and getattr(scene, "bname_overview_mode", False)
            )
            or is_page_browser
        ) and len(work.pages) > 0:
            # 全ページ一覧モード.
            # 日本の漫画は右→左に読むため、ページ 0001 を右端に置き、追加した
            # ページ (0002, 0003...) を左方向に展開する。オフセットは負の X。
            from ..utils.page_grid import (
                is_left_half_page as _is_left_half,
            )

            cols = max(1, int(getattr(scene, "bname_overview_cols", 4)))
            gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
            cw = paper.canvas_width_mm
            ch = paper.canvas_height_mm
            start_side = getattr(paper, "start_side", "right")
            read_direction = getattr(paper, "read_direction", "left")
            active_idx = work.active_page_index
            highlight_active_page = _should_highlight_active_page(context)
            active_highlight_rect = None
            page_file_indices = None if is_page_browser else _page_file_overview_indices(scene, work)
            page_file_current_index = (
                _page_file_current_page_index(scene, work)
                if page_file_indices is not None
                else -1
            )
            for i, page in enumerate(work.pages):
                if page_file_indices is not None and i not in page_file_indices:
                    continue
                if not overlay_visibility.page_visible(page):
                    continue
                # 見開き判定込みの式は page_grid 側に集約
                ox, oy = _page_overview_offset(
                    context, work, i, cols, gap, cw, ch,
                    start_side, read_direction, is_page_browser=is_page_browser,
                )
                if not overlay_visibility.rect_may_be_visible_in_region(
                    _translate_rect(rects.canvas, ox, oy), region, rv3d,
                ):
                    continue
                if page_file_indices is not None and i != page_file_current_index:
                    continue
                left_half = _is_left_half(i, start_side, read_direction, work=work)
                _draw_page_overlay(
                    context, work, paper, rects, page, mode,
                    ox_mm=ox, oy_mm=oy, draw_image_layers=False,
                    is_left_half=left_half, phase=phase,
                )
                # アクティブページにハイライト枠 (ズーム連動)
                if highlight_active_page and i == active_idx:
                    active_highlight_rect = _page_highlight_rect(rects, ox, oy)
            _draw_page_highlight(active_highlight_rect)
        elif mode == MODE_COMA and len(work.pages) > 0:
            from ..utils.page_grid import (
                is_left_half_page as _is_left_half,
            )

            cols = max(2, int(getattr(scene, "bname_overview_cols", 4)))
            gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
            cw = paper.canvas_width_mm
            ch = paper.canvas_height_mm
            start_side = getattr(paper, "start_side", "left")
            read_direction = getattr(paper, "read_direction", "left")
            active_idx = work.active_page_index
            highlight_active_page = _should_highlight_active_page(context)
            active_highlight_rect = None
            page_file_indices = None if is_page_browser else _page_file_overview_indices(scene, work)
            page_file_current_index = (
                _page_file_current_page_index(scene, work)
                if page_file_indices is not None
                else -1
            )
            for i, page in enumerate(work.pages):
                if page_file_indices is not None and i not in page_file_indices:
                    continue
                if not overlay_visibility.page_visible(page):
                    continue
                ox, oy = _page_overview_offset(
                    context, work, i, cols, gap, cw, ch,
                    start_side, read_direction, is_page_browser=is_page_browser,
                )
                if not overlay_visibility.rect_may_be_visible_in_region(
                    _translate_rect(rects.canvas, ox, oy), region, rv3d,
                ):
                    continue
                if page_file_indices is not None and i != page_file_current_index:
                    continue
                left_half = _is_left_half(i, start_side, read_direction, work=work)
                _draw_page_overlay(
                    context, work, paper, rects, page, mode,
                    ox_mm=ox, oy_mm=oy, draw_image_layers=False,
                    is_left_half=left_half, phase=phase,
                )
                if highlight_active_page and i == active_idx:
                    active_highlight_rect = _page_highlight_rect(rects, ox, oy)
            _draw_page_highlight(active_highlight_rect)
        else:
            from ..utils.page_grid import (
                is_left_half_page as _is_left_half,
                page_grid_offset_mm as _pg_offset,
            )
            page = get_active_page(context)
            if page is not None and not overlay_visibility.page_visible(page):
                return
            cols = max(2, int(getattr(scene, "bname_overview_cols", 4)))
            gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
            cw = paper.canvas_width_mm
            ch = paper.canvas_height_mm
            start_side = getattr(paper, "start_side", "right")
            read_direction = getattr(paper, "read_direction", "left")
            idx = work.active_page_index
            # 単ページモードでも active page の内容は grid 位置にあるため、
            # overlay も同じ (ox, oy) で描画して内容と一致させる。
            ox, oy = _pg_offset(
                max(0, idx), cols, gap, cw, ch, start_side, read_direction, work=work
            )
            ox, oy = _with_page_manual_offset(work, max(0, idx), ox, oy)
            left_half = _is_left_half(max(0, idx), start_side, read_direction, work=work)
            _draw_page_overlay(
                context, work, paper, rects, page, mode,
                ox_mm=ox, oy_mm=oy, draw_image_layers=True,
                is_left_half=left_half, phase=phase,
            )
        try:
            gpu.state.depth_test_set("NONE")
        except Exception:  # noqa: BLE001
            pass
        overlay_effect_line.draw_active_effect_line_bounds(
            context,
            draw_rect_fill=_draw_rect_fill,
            draw_rect_outline=_draw_rect_outline,
            draw_segments_mm=_draw_segments_mm,
            logger=_logger,
        )
        overlay_creation_range.draw(
            draw_rect_fill=_draw_rect_fill,
            draw_rect_outline=_draw_rect_outline,
        )
        _draw_object_tool_layer_bounds(context)
    finally:
        gpu.state.blend_set("NONE")
        # depth_test を元に戻す (他の draw_handler への副作用を避ける)
        try:
            gpu.state.depth_test_set("NONE")
        except Exception:  # noqa: BLE001
            pass


def apply_bname_shading_mode(context=None) -> int:
    """全ウィンドウの全 VIEW_3D を B-Name のモード別シェーディングに切替.

    B-Name 作品 UI の見え方を統一する目的:
    - 紙面編集 (ページ一覧) も コマ編集 もどちらも shading.type = "RENDERED"。
      フキダシの画像マスクや、 コマ枠のぼかし枠線、 コマ平面のテクスチャ表示が
      すべて同じシェーダーパスで見えるようにする。
    - 旧来の ページ一覧 = SOLID 設定では、 フキダシの画像マスクが効かず、
      コマ枠の外までフキダシが見えてしまっていたため、 RENDERED に統一する。
    work_new / work_open / load_post から呼ぶ。戻り値は変更したエリア数。
    """
    ctx = context or bpy.context
    wm = ctx.window_manager
    if wm is None:
        return 0
    count = 0
    for window in wm.windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            if page_browser.is_page_browser_area_for_window(window, area):
                page_browser.apply_page_browser_view_settings(area)
                continue
            space = area.spaces.active
            if space is None:
                continue
            shading = getattr(space, "shading", None)
            if shading is None:
                continue
            try:
                if getattr(shading, "type", None) != "RENDERED":
                    shading.type = "RENDERED"
                    count += 1
            except Exception:  # noqa: BLE001
                _logger.exception("apply_bname_shading_mode: set failed")
    return count


def set_viewport_overlays_enabled(context=None, *, enabled: bool) -> int:
    """全ウィンドウの全 VIEW_3D で Blender 標準オーバーレイ表示を切り替える."""
    ctx = context or bpy.context
    wm = getattr(ctx, "window_manager", None)
    if wm is None:
        return 0
    count = 0
    for window in wm.windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            if page_browser.is_page_browser_area_for_window(window, area):
                page_browser.apply_page_browser_view_settings(area)
                continue
            for space in getattr(area, "spaces", []):
                if space.type != "VIEW_3D":
                    continue
                overlay = getattr(space, "overlay", None)
                if overlay is None:
                    continue
                try:
                    if bool(getattr(overlay, "show_overlays", True)) != bool(enabled):
                        overlay.show_overlays = bool(enabled)
                        count += 1
                except Exception:  # noqa: BLE001
                    _logger.exception("set_viewport_overlays_enabled: set failed")
            try:
                area.tag_redraw()
            except Exception:  # noqa: BLE001
                pass
    return count


def schedule_viewport_overlays_enabled(*, enabled: bool, retries: int = 6, interval: float = 0.1) -> None:
    """load_post 直後の UI 再構築をまたいでオーバーレイ表示を再適用する."""
    state = {"left": max(1, int(retries))}

    def _tick():
        try:
            set_viewport_overlays_enabled(bpy.context, enabled=enabled)
        except Exception:  # noqa: BLE001
            pass
        state["left"] -= 1
        return interval if state["left"] > 0 else None

    try:
        bpy.app.timers.register(_tick, first_interval=interval)
    except Exception:  # noqa: BLE001
        pass


def reset_viewport_background_to_theme(context=None) -> int:
    """全ウィンドウの全 VIEW_3D の solid shading 背景をテーマ色 (Blender 既定) に戻す.

    旧実装 (apply_paper_background_color) は Blender 自身の solid 背景色を
    paper_color (白) に書き換えていたため、用紙の外側まで真っ白になり
    「ビューポート全体が白」状態を招いていた。現行では用紙領域だけを
    POST_VIEW の最初に不透明塗りし (``_draw_canvas_fill_only``)、
    ビューポート背景はテーマ既定の灰色に保つ。

    過去に白く書き換えられて .blend に保存されているファイルも、ロード時に
    この関数を呼べば自動で灰色 (テーマ既定) に戻る。
    """
    ctx = context or bpy.context
    wm = ctx.window_manager
    if wm is None:
        return 0
    count = 0
    for window in wm.windows:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            if space is None:
                continue
            shading = getattr(space, "shading", None)
            if shading is None:
                continue
            try:
                if getattr(shading, "background_type", None) != "THEME":
                    shading.background_type = "THEME"
                    count += 1
            except Exception:  # noqa: BLE001
                _logger.exception("reset_viewport_background_to_theme: set failed")
    return count


# ---------- register / unregister ----------


def _draw_callback_pixel() -> None:
    """POST_PIXEL: blf テキスト描画 (ページ識別番号・テキスト本文).

    blf は POST_VIEW では view/projection matrix の影響で screen 座標が
    world 座標扱いになり画面外に飛ぶ。POST_PIXEL では Blender が pixel
    空間に matrix を切り替えて呼び出すので blf.draw が期待通り動く。
    """
    context = bpy.context
    work = get_work(context)
    if work is None or not work.loaded:
        return
    # オーバーレイ表示切替 (Phase 3c)
    scene_root = context.scene
    if scene_root is not None and not bool(
        getattr(scene_root, "bname_overlay_enabled", True)
    ):
        return
    paper = work.paper
    rects = overlay_shared.compute_paper_rects(paper)
    mode = get_mode(context)
    scene = context.scene
    is_page_browser = page_browser.is_page_browser_area(context)
    region, rv3d = _resolve_active_region(context)

    if mode != MODE_PAGE and not is_page_browser:
        return

    if (
        (
            getattr(scene, "bname_overview_mode", False)
            and mode == MODE_PAGE
        )
        or is_page_browser
    ) and len(work.pages) > 0:
        from ..utils.page_grid import (
            is_left_half_page as _is_left_half,
        )
        cols = max(2, int(getattr(scene, "bname_overview_cols", 4)))
        gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
        cw = paper.canvas_width_mm
        ch = paper.canvas_height_mm
        start_side = getattr(paper, "start_side", "right")
        read_direction = getattr(paper, "read_direction", "left")
        page_file_indices = None if is_page_browser else _page_file_overview_indices(scene, work)
        page_file_current_index = (
            _page_file_current_page_index(scene, work)
            if page_file_indices is not None
            else -1
        )
        for i, page in enumerate(work.pages):
            if page_file_indices is not None and i not in page_file_indices:
                continue
            if not overlay_visibility.page_visible(page):
                continue
            ox, oy = _page_overview_offset(
                context, work, i, cols, gap, cw, ch,
                start_side, read_direction, is_page_browser=is_page_browser,
            )
            if not overlay_visibility.rect_may_be_visible_in_region(
                _translate_rect(rects.canvas, ox, oy), region, rv3d,
            ):
                continue
            left_half = _is_left_half(i, start_side, read_direction, work=work)
            inner = bleed_rect(paper)
            page = work.pages[i] if 0 <= i < len(work.pages) else None
            if (
                page is not None
                and (page_file_indices is None or i == page_file_current_index)
            ):
                overlay_text.draw_text_pixels(
                    context,
                    page,
                    ox_mm=ox,
                    oy_mm=oy,
                    entry_visible=lambda entry: overlay_visibility.entry_in_visible_coma(page, entry),
                    draw_text_in_rect=_draw_text_in_rect,
                    draw_rect_fill_pixel=_draw_rect_fill_pixel,
                )
            _draw_page_header_number_pixel(context, paper, i, ox, oy)
    else:
        from ..utils.page_grid import (
            is_left_half_page as _is_left_half,
            page_grid_offset_mm as _pg_offset,
        )
        cols = max(2, int(getattr(scene, "bname_overview_cols", 4)))
        gap = float(getattr(scene, "bname_overview_gap_mm", 30.0))
        cw = paper.canvas_width_mm
        ch = paper.canvas_height_mm
        start_side = getattr(paper, "start_side", "right")
        read_direction = getattr(paper, "read_direction", "left")
        idx = max(0, work.active_page_index) if len(work.pages) > 0 else 0
        ox, oy = _pg_offset(idx, cols, gap, cw, ch, start_side, read_direction, work=work)
        ox, oy = _with_page_manual_offset(work, idx, ox, oy)
        left_half = _is_left_half(idx, start_side, read_direction, work=work)
        inner = bleed_rect(paper)
        page = get_active_page(context)
        if page is not None and overlay_visibility.page_visible(page):
            overlay_text.draw_text_pixels(
                context,
                page,
                ox_mm=ox,
                oy_mm=oy,
                entry_visible=lambda entry: overlay_visibility.entry_in_visible_coma(page, entry),
                draw_text_in_rect=_draw_text_in_rect,
                draw_rect_fill_pixel=_draw_rect_fill_pixel,
            )
            _draw_page_header_number_pixel(context, paper, idx, ox, oy)
    overlay_coma_selection.draw(context, work, region, rv3d)
def register() -> None:
    global _handle, _handle_pixel, _handle_pre
    _handle_pre = None  # PRE_VIEW は使用しない (EEVEE Next で視認できないため)
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback, ("post",), "WINDOW", "POST_VIEW"
        )
    if _handle_pixel is None:
        _handle_pixel = bpy.types.SpaceView3D.draw_handler_add(
            _draw_callback_pixel, (), "WINDOW", "POST_PIXEL"
        )
    _logger.debug("overlay draw_handlers registered (POST_VIEW + POST_PIXEL)")


def unregister() -> None:
    global _handle, _handle_pixel, _handle_pre
    _handle_pre = None
    if _handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        except (ValueError, RuntimeError):
            pass
        _handle = None
    if _handle_pixel is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handle_pixel, "WINDOW")
        except (ValueError, RuntimeError):
            pass
        _handle_pixel = None
    _logger.debug("overlay draw_handlers removed")
