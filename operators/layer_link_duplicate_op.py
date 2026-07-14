"""レイヤーのリンク複製."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..utils import (
    balloon_curve_object,
    layer_links,
    layer_stack as layer_stack_utils,
    log,
)
from ..utils.layer_hierarchy import OUTSIDE_STACK_KEY, page_stack_key

_logger = log.get_logger(__name__)

_BALLOON_CENTER_FREE_ATTRS = (
    "center_offset_x_mm",
    "center_offset_y_mm",
    "rotation_deg",
    "free_transform_enabled",
    "free_transform_bottom_left",
    "free_transform_bottom_right",
    "free_transform_top_left",
    "free_transform_top_right",
    "free_transform_line_width_scale",
)

_BALLOON_RECT_ATTRS = (
    "x_mm",
    "y_mm",
    "width_mm",
    "height_mm",
)


def _active_stack_item(context):
    scene = getattr(context, "scene", None)
    if scene is None:
        return None
    stack = getattr(scene, "bmanga_layer_stack", None)
    idx = int(getattr(scene, "bmanga_active_layer_stack_index", -1) or -1)
    if stack is not None and 0 <= idx < len(stack):
        return stack[idx]
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None or not (0 <= idx < len(stack)):
        return None
    return stack[idx]


def _balloon_uid(page, entry) -> str:
    if entry is None:
        return ""
    page_key = OUTSIDE_STACK_KEY if page is None else page_stack_key(page)
    balloon_id = str(getattr(entry, "id", "") or "")
    if not page_key or not balloon_id:
        return ""
    return layer_stack_utils.target_uid("balloon", f"{page_key}:{balloon_id}")


def _copy_center_free(src, dst) -> bool:
    changed = False
    for attr in _BALLOON_CENTER_FREE_ATTRS:
        if not hasattr(src, attr) or not hasattr(dst, attr):
            continue
        value = getattr(src, attr)
        try:
            if attr in {
                "free_transform_bottom_left",
                "free_transform_bottom_right",
                "free_transform_top_left",
                "free_transform_top_right",
            }:
                value = (float(value[0]), float(value[1]))
            if getattr(dst, attr) != value:
                setattr(dst, attr, value)
                changed = True
        except Exception:  # noqa: BLE001
            try:
                setattr(dst, attr, value)
                changed = True
            except Exception:  # noqa: BLE001
                pass
    return changed


def _copy_balloon_rect(src, dst) -> tuple[float, float, float, float] | None:
    values: list[float] = []
    for attr in _BALLOON_RECT_ATTRS:
        if not hasattr(src, attr) or not hasattr(dst, attr):
            return None
        try:
            values.append(float(getattr(src, attr, 0.0) or 0.0))
        except Exception:  # noqa: BLE001
            return None
    return values[0], values[1], values[2], values[3]


def _linked_balloon_targets(context, page, entry):
    from ..core.work import get_work

    source_uid = _balloon_uid(page, entry)
    if not source_uid:
        return []
    linked_uids = set(layer_links.linked_uids_for_uid(context, source_uid))
    linked_uids.discard(source_uid)
    if not linked_uids:
        return []
    targets = []
    work = get_work(context)
    pages = getattr(work, "pages", []) if work is not None else []
    # リンク先が別ページにある場合に備え、リンク uid が指すページの詳細を
    # 読み込んでおく (詳細未読込のページではリンク先がメモリに存在しないため)
    try:
        from ..utils import page_detail

        for target_page in pages:
            if bool(getattr(target_page, "detail_loaded", True)):
                continue
            pk = page_stack_key(target_page)
            if pk and any(f"{pk}:" in uid for uid in linked_uids):
                page_detail.ensure_page_detail(work, target_page)
    except Exception:  # noqa: BLE001
        pass
    for target_page in pages:
        for target in getattr(target_page, "balloons", []) or []:
            if target is entry:
                continue
            if _balloon_uid(target_page, target) in linked_uids:
                targets.append((target_page, target))
    for target in getattr(work, "shared_balloons", []) or []:
        if target is entry:
            continue
        if _balloon_uid(None, target) in linked_uids:
            targets.append((None, target))
    return targets


def sync_balloon_transform_to_target(context, source_page, source, target_page, target) -> bool:
    """source の位置・サイズ・回転・中心点を target へ同値で反映する."""
    if source is None or target is None or source is target:
        return False
    try:
        from . import balloon_op

        changed = False
        rect = _copy_balloon_rect(source, target)
        if rect is not None:
            old_rect = _copy_balloon_rect(target, source)
            balloon_op._set_balloon_rect(
                target_page,
                target,
                rect[0],
                rect[1],
                rect[2],
                rect[3],
                propagate_link=False,
            )
            changed = old_rect != rect
        with balloon_curve_object.suspend_auto_sync():
            changed = _copy_center_free(source, target) or changed
        if changed:
            balloon_curve_object.on_balloon_entry_changed(target)
            balloon_op._sync_balloon_merge_display_if_needed(target_page, target)
        return changed
    except Exception:  # noqa: BLE001
        _logger.exception("linked balloon direct transform sync failed")
        return False


def propagate_linked_balloon_center_free(context, page, entry) -> int:
    """リンクされたフキダシへ中心点・回転・自由変形だけを反映する."""
    if entry is None:
        return 0
    changed = 0
    for target_page, target in _linked_balloon_targets(context, page, entry):
        with balloon_curve_object.suspend_auto_sync():
            copied = _copy_center_free(entry, target)
        if copied:
            changed += 1
            try:
                balloon_curve_object.on_balloon_entry_changed(target)
            except Exception:  # noqa: BLE001
                _logger.exception("linked balloon sync failed")
            try:
                from . import balloon_op

                balloon_op._sync_balloon_merge_display_if_needed(target_page, target)
            except Exception:  # noqa: BLE001
                pass
    return changed


def propagate_linked_balloon_transform_absolute(
    context,
    page,
    entry,
    *,
    skip_uids: set[str] | None = None,
) -> int:
    """リンクされたフキダシへ位置・サイズ・回転・中心点を同値で反映する."""
    if entry is None:
        return 0
    skip = set(skip_uids or ())
    source_uid = _balloon_uid(page, entry)
    if source_uid:
        skip.add(source_uid)
    changed = 0
    for target_page, target in _linked_balloon_targets(context, page, entry):
        uid = _balloon_uid(target_page, target)
        if uid in skip:
            continue
        if sync_balloon_transform_to_target(context, page, entry, target_page, target):
            changed += 1
    return changed


def propagate_linked_balloon_move_delta(
    context,
    page,
    entry,
    dx_mm: float,
    dy_mm: float,
    *,
    skip_uids: set[str] | None = None,
    updated_uids: set[str] | None = None,
) -> int:
    """リンクされたフキダシへ移動差分だけを反映する."""
    if entry is None:
        return 0
    dx = float(dx_mm)
    dy = float(dy_mm)
    if abs(dx) <= 1.0e-9 and abs(dy) <= 1.0e-9:
        return 0
    skip = set(skip_uids or ())
    source_uid = _balloon_uid(page, entry)
    if source_uid:
        skip.add(source_uid)
    changed = 0
    for target_page, target in _linked_balloon_targets(context, page, entry):
        uid = _balloon_uid(target_page, target)
        if uid in skip:
            continue
        if updated_uids is not None and uid in updated_uids:
            continue
        try:
            from . import balloon_op

            balloon_op._move_balloon_with_texts(
                target_page,
                target,
                float(getattr(target, "x_mm", 0.0) or 0.0) + dx,
                float(getattr(target, "y_mm", 0.0) or 0.0) + dy,
            )
        except Exception:  # noqa: BLE001
            _logger.exception("linked balloon move sync failed")
            continue
        if updated_uids is not None and uid:
            updated_uids.add(uid)
        changed += 1
    return changed


def _create_linked_balloon_duplicate(context, item) -> bool:
    from ..core.work import get_work
    from ..io import schema
    from .balloon_op import _allocate_balloon_id, _allocate_balloon_id_from_collection

    resolved = layer_stack_utils.resolve_stack_item(context, item)
    src = resolved.get("target") if resolved is not None else None
    page = resolved.get("page") if resolved is not None else None
    work = get_work(context)
    if src is None:
        return False
    collection = getattr(page, "balloons", None) if page is not None else getattr(work, "shared_balloons", None)
    if collection is None:
        return False
    source_uid = _balloon_uid(page, src)
    dst = collection.add()
    with balloon_curve_object.suspend_auto_sync():
        schema.balloon_entry_from_dict(dst, schema.balloon_entry_to_dict(src))
        if page is None:
            dst.id = _allocate_balloon_id_from_collection(collection, "shared_balloon")
            dst.parent_kind = "none"
            dst.parent_key = ""
        else:
            dst.id = _allocate_balloon_id(page, work)
    try:
        balloon_curve_object.on_balloon_entry_changed(dst)
    except Exception:  # noqa: BLE001
        _logger.exception("linked balloon duplicate display sync failed")
    if page is not None:
        page.active_balloon_index = len(collection) - 1
    context.scene.bmanga_active_layer_kind = "balloon"
    dest_uid = _balloon_uid(page, dst)
    if source_uid and dest_uid:
        layer_links.link_uids(context, [source_uid, dest_uid])
    layer_stack_utils.sync_layer_stack_after_data_change(context)
    if dest_uid:
        for index, row in enumerate(context.scene.bmanga_layer_stack):
            if layer_stack_utils.stack_item_uid(row) == dest_uid:
                layer_stack_utils.select_stack_index(context, index)
                break
    return True


class BMANGA_OT_layer_stack_link_duplicate(Operator):
    bl_idname = "bmanga.layer_stack_link_duplicate"
    bl_label = "リンク複製"
    bl_description = "選択中のフキダシまたは効果線をリンク複製します"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        item = layer_stack_utils.active_stack_item(context)
        return str(getattr(item, "kind", "") or "") in {"balloon", "effect"}

    def execute(self, context):
        item = _active_stack_item(context)
        kind = str(getattr(item, "kind", "") or "") if item is not None else ""
        if kind == "balloon":
            if _create_linked_balloon_duplicate(context, item):
                self.report({"INFO"}, "リンク複製しました")
                return {"FINISHED"}
            self.report({"ERROR"}, "リンク複製するフキダシが見つかりません")
            return {"CANCELLED"}
        if kind == "effect":
            return bpy.ops.bmanga.effect_line_create_linked("EXEC_DEFAULT")
        return {"CANCELLED"}


_CLASSES = (BMANGA_OT_layer_stack_link_duplicate,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
