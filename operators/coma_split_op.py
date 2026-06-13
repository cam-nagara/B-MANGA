"""コマ比率分割 Operator.

選択中のコマを指定比率で分割する。
- 垂直/水平モード: 原稿用紙に対して真っ直ぐな線で分割
- 縦/横モード: コマの辺に沿った線で分割 (斜めコマ対応)
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

import bpy
from bpy.props import EnumProperty, FloatProperty
from bpy.types import Operator

from ..core.work import get_active_page, get_work
from ..io import coma_io, page_io
from ..utils import (
    layer_stack as layer_stack_utils,
    log,
    object_selection,
    page_file_scene,
)

_logger = log.get_logger(__name__)


def _coma_polygon(panel) -> list[tuple[float, float]]:
    if panel.shape_type == "rect":
        x, y = panel.rect_x_mm, panel.rect_y_mm
        w, h = panel.rect_width_mm, panel.rect_height_mm
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    if panel.shape_type == "polygon":
        return [(v.x_mm, v.y_mm) for v in panel.vertices]
    return []


def _set_coma_polygon(panel, poly: Sequence[tuple[float, float]]) -> None:
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


def _lerp(a: tuple[float, float], b: tuple[float, float], t: float) -> tuple[float, float]:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _edge_angle(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.atan2(b[1] - a[1], b[0] - a[0])


def _classify_quad_edges(
    poly: list[tuple[float, float]],
) -> tuple[list[int], list[int]]:
    """4頂点の多角形の辺を「横方向（上下辺）」と「縦方向（左右辺）」に分類.

    Returns: (horizontal_edge_indices, vertical_edge_indices)
    horizontal = よりX方向に長い辺のペア (上辺・下辺)
    vertical = よりY方向に長い辺のペア (左辺・右辺)
    """
    n = len(poly)
    angles = []
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        ang = abs(_edge_angle(a, b))
        angles.append((i, ang))

    # 辺の角度の絶対値: 0 or πに近い → 横, π/2に近い → 縦
    def h_score(ang: float) -> float:
        return min(abs(ang), abs(ang - math.pi), abs(ang + math.pi))

    sorted_edges = sorted(angles, key=lambda x: h_score(x[1]))
    horiz = [sorted_edges[0][0], sorted_edges[1][0]]
    vert = [sorted_edges[2][0], sorted_edges[3][0]]
    return horiz, vert


def _split_line_page_axis(
    poly: list[tuple[float, float]],
    direction: str,
    ratio: float,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """垂直/水平モード: ページ軸に平行な分割線を生成."""
    if not poly:
        return None
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    if direction == "H":
        y = y_min + (y_max - y_min) * ratio
        return ((x_min - 10.0, y), (x_max + 10.0, y))
    else:
        x = x_min + (x_max - x_min) * ratio
        return ((x, y_min - 10.0), (x, y_max + 10.0))


def _split_line_along_edges(
    poly: list[tuple[float, float]],
    direction: str,
    ratio: float,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """縦/横モード: コマの辺に沿った分割線を生成 (斜め対応).

    横方向分割 (H): 左右の縦辺を ratio で分割した点同士を結ぶ
    縦方向分割 (V): 上下の横辺を ratio で分割した点同士を結ぶ
    """
    n = len(poly)
    if n < 3:
        return None
    if n != 4:
        return _split_line_page_axis(poly, direction, ratio)

    horiz_edges, vert_edges = _classify_quad_edges(poly)

    if direction == "H":
        edges = vert_edges
    else:
        edges = horiz_edges

    e0 = edges[0]
    e1 = edges[1]
    a0 = poly[e0]
    b0 = poly[(e0 + 1) % n]
    a1 = poly[e1]
    b1 = poly[(e1 + 1) % n]

    p0 = _lerp(a0, b0, ratio)
    p1 = _lerp(a1, b1, ratio)
    return (p0, p1)


def _do_split(
    context, work, page, coma_idx: int, work_dir: Path,
    A: tuple[float, float], B: tuple[float, float],
) -> bool:
    from .coma_knife_cut_op import (
        _split_convex_polygon_by_line,
        _ordered_split_polygons,
        _reassign_coma_z_order_by_reading_order,
        _effective_gap_mm,
    )
    from .coma_op import _copy_coma_entry, blank_generated_coma_title

    if not (0 <= coma_idx < len(page.comas)):
        return False
    panel = page.comas[coma_idx]
    poly = _coma_polygon(panel)
    if not poly:
        return False
    gap_mm = _effective_gap_mm(work, A, B, panel)
    result = _split_convex_polygon_by_line(poly, A, B, gap_mm=gap_mm)
    if result is None:
        return False
    read_dir = str(getattr(getattr(work, "paper", None), "read_direction", "left") or "left")
    first_poly, second_poly = _ordered_split_polygons(result[0], result[1], read_dir)

    new_stem = coma_io.allocate_new_coma_id(work_dir, page.id)
    try:
        coma_io.copy_coma_files(work_dir, page.id, page.id, panel.coma_id, new_stem)
    except Exception:  # noqa: BLE001
        _logger.warning("coma_split: copy_coma_files failed for %s", panel.coma_id)
    new_entry = page.comas.add()
    _copy_coma_entry(panel, new_entry)
    new_entry.coma_id = new_stem
    new_entry.id = new_stem
    new_entry.title = blank_generated_coma_title()

    from .coma_knife_cut_op import _coma_id_number
    if _coma_id_number(str(getattr(panel, "coma_id", "") or "")) <= _coma_id_number(new_stem):
        _set_coma_polygon(panel, first_poly)
        _set_coma_polygon(new_entry, second_poly)
    else:
        _set_coma_polygon(panel, second_poly)
        _set_coma_polygon(new_entry, first_poly)
    _reassign_coma_z_order_by_reading_order(page, read_dir)
    try:
        coma_io.save_coma_meta(work_dir, page.id, panel)
        coma_io.save_coma_meta(work_dir, page.id, new_entry)
    except Exception:  # noqa: BLE001
        _logger.exception("coma_split: save_coma_meta failed")
    page.coma_count = len(page.comas)
    try:
        page_io.save_page_json(work_dir, page)
    except Exception:  # noqa: BLE001
        _logger.exception("coma_split: save_page_json failed")
    return True


def _sync_after_split(context) -> None:
    try:
        layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
    except Exception:  # noqa: BLE001
        _logger.exception("coma_split: layer stack sync failed")
    try:
        from ..core.work import get_work
        from ..utils import layer_object_sync as _los
        from ..utils import mask_object as _mask
        from ..utils import page_file_scene

        work = get_work(context)
        if work is not None:
            page_id = page_file_scene.current_page_id(context)
            if page_id:
                _los.rebuild_page_coma_objects(context, work, page_id)
                _mask.regenerate_all_coma_masks(context.scene, work, page_id)
    except Exception:  # noqa: BLE001
        _logger.exception("coma_split: object sync failed")


class BNAME_OT_coma_split_ratio(Operator):
    """選択中のコマを指定比率で分割する."""

    bl_idname = "bname.coma_split_ratio"
    bl_label = "コマを比率で分割"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(
        name="分割方式",
        items=[
            ("PAGE", "垂直/水平", "原稿用紙に対して真っ直ぐな線で分割"),
            ("EDGE", "縦/横", "コマの辺に沿って分割 (斜めコマ対応)"),
        ],
        default="PAGE",
    )
    direction: EnumProperty(
        name="方向",
        items=[
            ("H", "横方向", "横に分割 (上下に分かれる)"),
            ("V", "縦方向", "縦に分割 (左右に分かれる)"),
        ],
        default="H",
    )
    ratio: FloatProperty(
        name="比率 (%)",
        description="分割位置 (0%=始点側, 100%=終点側)",
        default=50.0,
        min=5.0,
        max=95.0,
        subtype="PERCENTAGE",
    )

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        if not (work and getattr(work, "loaded", False)):
            return False
        if not page_file_scene.is_page_edit_scene(context.scene):
            return False
        return object_selection.selected_coma_count(context) >= 1

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=280)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "mode")
        layout.prop(self, "direction")
        layout.prop(self, "ratio", slider=True)

    def execute(self, context):
        work = get_work(context)
        if work is None:
            self.report({"ERROR"}, "作品が読み込まれていません")
            return {"CANCELLED"}
        work_dir = Path(str(getattr(work, "work_dir", "") or ""))
        refs = object_selection.selected_coma_refs(context)
        if not refs:
            self.report({"ERROR"}, "コマが選択されていません")
            return {"CANCELLED"}

        t = self.ratio / 100.0
        split_count = 0
        for _page_idx, page, coma_idx, panel in reversed(refs):
            poly = _coma_polygon(panel)
            if not poly:
                continue
            if self.mode == "PAGE":
                line = _split_line_page_axis(poly, self.direction, t)
            else:
                line = _split_line_along_edges(poly, self.direction, t)
            if line is None:
                continue
            if _do_split(context, work, page, coma_idx, work_dir, line[0], line[1]):
                split_count += 1

        if split_count == 0:
            self.report({"WARNING"}, "分割できるコマがありませんでした")
            return {"CANCELLED"}

        _sync_after_split(context)
        self.report({"INFO"}, f"{split_count} コマを分割しました")
        return {"FINISHED"}


_CLASSES = (BNAME_OT_coma_split_ratio,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
