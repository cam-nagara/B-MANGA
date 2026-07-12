"""中心点スナップ: 集中線系レイヤーの中心点同士をスナップする."""

from __future__ import annotations

SNAP_TOLERANCE_MM = 1.5

# スナップ発動しきい値: ドラッグ開始直後の微小ジッタ(1px程度)で
# スナップが即座に発火して意図しない移動が確定するのを防ぐ。
# 生の移動量がこの値(mm)を一度でも超えるまでスナップを有効化しない。
SNAP_ACTIVATION_MM = 1.0


def balloon_center(entry) -> tuple[float, float] | None:
    """フキダシの中心点を取得する。集中線系スタイルのみ。"""
    from ..utils import balloon_shapes

    if not balloon_shapes.is_flash_line_style(getattr(entry, "line_style", "")):
        return None
    cx = float(getattr(entry, "x_mm", 0)) + float(getattr(entry, "width_mm", 0)) * 0.5
    cy = float(getattr(entry, "y_mm", 0)) + float(getattr(entry, "height_mm", 0)) * 0.5
    cx += float(getattr(entry, "center_offset_x_mm", 0) or 0)
    cy += float(getattr(entry, "center_offset_y_mm", 0) or 0)
    return (cx, cy)


def effect_center(obj, layer) -> tuple[float, float] | None:
    """効果線の中心点を取得する。"""
    from ..operators import effect_line_op

    return effect_line_op.effect_layer_center(obj, layer)


def collect_page_center_points(
    context,
    page,
    *,
    exclude_balloon_ids: set | None = None,
    exclude_effect_layer_names: set | None = None,
) -> list[tuple[float, float]]:
    """ページ上の全レイヤーの中心点を収集する。"""
    from ..utils import layer_stack as layer_stack_utils
    from ..utils import gp_layer_parenting as gp_parent

    centers: list[tuple[float, float]] = []
    exclude_balloon_ids = exclude_balloon_ids or set()
    exclude_effect_layer_names = exclude_effect_layer_names or set()
    page_id = str(getattr(page, "id", "") or "")

    for entry in getattr(page, "balloons", []):
        bid = str(getattr(entry, "id", "") or "")
        if bid in exclude_balloon_ids:
            continue
        c = balloon_center(entry)
        if c is not None:
            centers.append(c)

    for obj in layer_stack_utils._iter_effect_objects():
        layers = getattr(getattr(obj, "data", None), "layers", None)
        if layers is None:
            continue
        for layer in layers:
            layer_name = str(getattr(layer, "name", "") or "")
            if layer_name in exclude_effect_layer_names:
                continue
            parent_key = gp_parent.parent_key(layer)
            if not parent_key:
                from ..utils import object_naming as _on
                parent_key = str(obj.get(_on.PROP_PARENT_KEY, "") or "")
            if page_id and not parent_key.startswith(page_id):
                continue
            c = effect_center(obj, layer)
            if c is not None:
                centers.append(c)

    legacy_obj = layer_stack_utils.get_effect_gp_object()
    if legacy_obj is not None:
        layers = getattr(getattr(legacy_obj, "data", None), "layers", None)
        if layers is not None:
            for layer in layers:
                layer_name = str(getattr(layer, "name", "") or "")
                if layer_name in exclude_effect_layer_names:
                    continue
                parent_key = gp_parent.parent_key(layer)
                if not parent_key or (page_id and not parent_key.startswith(page_id)):
                    continue
                c = effect_center(legacy_obj, layer)
                if c is not None:
                    centers.append(c)

    return centers


def snap_center(
    original_center: tuple[float, float],
    dx: float,
    dy: float,
    targets: list[tuple[float, float]],
    tolerance: float = SNAP_TOLERANCE_MM,
) -> tuple[float, float]:
    """中心点を最寄りのターゲット中心点にスナップし、調整済みの(dx, dy)を返す。

    各軸独立でスナップする（X軸とY軸は別々にスナップ可能）。
    """
    proposed_x = original_center[0] + dx
    proposed_y = original_center[1] + dy
    best_snap_x = dx
    best_snap_x_dist = tolerance + 1.0
    best_snap_y = dy
    best_snap_y_dist = tolerance + 1.0

    for tx, ty in targets:
        gap_x = abs(tx - proposed_x)
        gap_y = abs(ty - proposed_y)
        if gap_x <= tolerance and gap_x < best_snap_x_dist:
            best_snap_x = tx - original_center[0]
            best_snap_x_dist = gap_x
        if gap_y <= tolerance and gap_y < best_snap_y_dist:
            best_snap_y = ty - original_center[1]
            best_snap_y_dist = gap_y

    return (best_snap_x, best_snap_y)
