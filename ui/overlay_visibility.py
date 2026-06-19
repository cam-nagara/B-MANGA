"""Viewport overlay visibility predicates.

Blender 標準 Object と整合させるため、entry/panel の visible フラグだけでなく
**Outliner の Collection 表示状態** (LayerCollection.hide_viewport / exclude)
も見る。これによりユーザーが Outliner でページ/コマ Collection の目アイコン
を切ると、オーバーレイ描画も連動して非表示になる。
"""

from __future__ import annotations

import bpy

from ..utils import page_range
from ..utils.geom import Rect, mm_to_m
from ..utils.layer_hierarchy import entry_center, coma_containing_point


def rect_may_be_visible_in_region(
    rect: Rect,
    region,
    rv3d,
    *,
    margin_px: float = 240.0,
) -> bool:
    """画面外のページ補助描画を省くため、キャンバス矩形の可視性をざっくり判定する."""
    if region is None or rv3d is None:
        return True
    try:
        from bpy_extras import view3d_utils
    except Exception:  # noqa: BLE001
        return True
    coords = []
    for x_mm, y_mm in (
        (rect.x, rect.y),
        (rect.x2, rect.y),
        (rect.x2, rect.y2),
        (rect.x, rect.y2),
    ):
        try:
            coord = view3d_utils.location_3d_to_region_2d(
                region,
                rv3d,
                (mm_to_m(x_mm), mm_to_m(y_mm), 0.0),
            )
        except Exception:  # noqa: BLE001
            return True
        if coord is not None:
            coords.append(coord)
    if not coords:
        return True
    min_x = min(float(coord.x) for coord in coords)
    max_x = max(float(coord.x) for coord in coords)
    min_y = min(float(coord.y) for coord in coords)
    max_y = max(float(coord.y) for coord in coords)
    return not (
        max_x < -margin_px
        or min_x > float(getattr(region, "width", 0)) + margin_px
        or max_y < -margin_px
        or min_y > float(getattr(region, "height", 0)) + margin_px
    )


def _walk_layer_collection(layer_coll, bmanga_id: str):
    """LayerCollection ツリーから ``bmanga_id`` を持つ LayerCollection を探す."""
    if layer_coll is None or not bmanga_id:
        return None
    coll = getattr(layer_coll, "collection", None)
    if coll is not None and str(coll.get("bmanga_id") or "") == bmanga_id:
        return layer_coll
    for child in layer_coll.children:
        found = _walk_layer_collection(child, bmanga_id)
        if found is not None:
            return found
    return None


def _layer_collection_visible(bmanga_id: str) -> bool:
    """``bmanga_id`` の Collection が現在の view_layer で表示状態にあるか.

    LayerCollection.exclude (チェックボックス) または hide_viewport (目アイコン)
    が立っていたら非表示扱い。Collection が見つからない / scene 取得不可は
    True (表示) で fallback。
    """
    if not bmanga_id:
        return True
    try:
        scene = bpy.context.scene
        if scene is None:
            return True
        view_layer = bpy.context.view_layer
        if view_layer is None:
            return True
        lc = _walk_layer_collection(view_layer.layer_collection, bmanga_id)
        if lc is None:
            return True
        if bool(getattr(lc, "exclude", False)):
            return False
        if bool(getattr(lc, "hide_viewport", False)):
            return False
        # Collection 自身の hide_viewport (per-data) も見る
        coll = getattr(lc, "collection", None)
        if coll is not None and bool(getattr(coll, "hide_viewport", False)):
            return False
        return True
    except Exception:  # noqa: BLE001
        return True


def page_visible(page) -> bool:
    if not page_range.page_visible_in_work(page):
        return False
    page_id = str(getattr(page, "id", "") or "")
    if not _layer_collection_visible(page_id):
        return False
    return True


def coma_visible(panel, *, page=None) -> bool:
    if not bool(getattr(panel, "visible", True)):
        return False
    coma_id = str(getattr(panel, "id", "") or "")
    if not coma_id:
        return True
    # コマ Collection の bmanga_id は "<page_id>:<coma_id>" 形式
    page_id = ""
    if page is not None:
        page_id = str(getattr(page, "id", "") or "")
    else:
        # page 不明: 全 page を走査して panel を含むページを探す
        try:
            scene = bpy.context.scene
            work = getattr(scene, "bmanga_work", None) if scene is not None else None
            if work is not None:
                for p in getattr(work, "pages", []):
                    for c in getattr(p, "comas", []):
                        if c is panel:
                            page_id = str(getattr(p, "id", "") or "")
                            break
                    if page_id:
                        break
        except Exception:  # noqa: BLE001
            pass
    if page_id:
        bmanga_id = f"{page_id}:{coma_id}"
        if not _layer_collection_visible(bmanga_id):
            return False
    return True


def entry_in_visible_coma(page, entry) -> bool:
    # エントリ自身が「表示=False」なら描画しない (balloon / text で使用)
    if not bool(getattr(entry, "visible", True)):
        return False
    try:
        panel = coma_containing_point(page, *entry_center(entry))
    except Exception:  # noqa: BLE001
        panel = None
    return panel is None or coma_visible(panel, page=page)


def _coma_matches_parent_key(page, coma, coma_id: str) -> bool:
    coma_id = str(coma_id or "")
    if not coma_id:
        return False
    ids = {
        str(getattr(coma, "id", "") or ""),
        str(getattr(coma, "coma_id", "") or ""),
    }
    page_id = str(getattr(page, "id", "") or "")
    ids.update({f"{page_id}:{item}" for item in list(ids) if item})
    return coma_id in ids


def _polygon_for_coma(coma) -> list[tuple[float, float]]:
    if getattr(coma, "shape_type", "") == "rect":
        x = float(getattr(coma, "rect_x_mm", 0.0))
        y = float(getattr(coma, "rect_y_mm", 0.0))
        w = float(getattr(coma, "rect_width_mm", 0.0))
        h = float(getattr(coma, "rect_height_mm", 0.0))
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    verts = getattr(coma, "vertices", []) or []
    return [(float(v.x_mm), float(v.y_mm)) for v in verts]


def _point_in_polygon(px: float, py: float, polygon: list[tuple[float, float]]) -> bool:
    if not polygon or len(polygon) < 3:
        return False
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def entry_bbox_within_parent_coma(page, entry) -> bool:
    """parent_kind="coma" の entry について、 bbox 4 隅が親コマ polygon 内か.

    text overlay で使う厳格判定。 parent_kind が page / outside / 空の場合は
    True を返して従来挙動を維持する (= page 全体に描画される)。
    親コマが見つからない or polygon が取れない場合も True (= マスクなし)。
    """
    parent_kind = str(getattr(entry, "parent_kind", "") or "")
    parent_key = str(getattr(entry, "parent_key", "") or "")
    if parent_kind != "coma" or ":" not in parent_key:
        return True
    coma_id = parent_key.split(":", 1)[1]
    target_coma = None
    for c in getattr(page, "comas", []) or []:
        if _coma_matches_parent_key(page, c, coma_id):
            target_coma = c
            break
    if target_coma is None:
        return True
    polygon = _polygon_for_coma(target_coma)
    if len(polygon) < 3:
        return True
    x = float(getattr(entry, "x_mm", 0.0))
    y = float(getattr(entry, "y_mm", 0.0))
    w = float(getattr(entry, "width_mm", 0.0))
    h = float(getattr(entry, "height_mm", 0.0))
    corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return all(_point_in_polygon(cx, cy, polygon) for cx, cy in corners)
