"""統合レイヤーリストの選択・追加・並び替え・削除 Operator."""

from __future__ import annotations

import json
import time
from pathlib import Path

import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty
from bpy.types import Menu, Operator
from bpy_extras.io_utils import ImportHelper

from ..utils import layer_stack as layer_stack_utils
from ..utils import layer_folder as layer_folder_utils
from ..utils.layer_hierarchy import (
    PAGE_KIND,
    COMA_KIND,
    OUTSIDE_STACK_KEY,
    page_stack_key,
    coma_stack_key,
    outside_child_key,
    split_child_key,
)

_INLINE_RENAME_DOUBLE_CLICK_SEC = 0.45
_LAST_INLINE_RENAME_CLICK = {"index": -1, "uid": "", "time": 0.0}

_ADD_KIND_ITEMS = (
    ("page", "ページ", ""),
    ("coma", "コマ", ""),
    ("gp", "グリースペンシル", ""),
    ("image", "画像 (配置)", ""),
    ("image_path", "パターンカーブ", ""),
    ("raster", "ラスター (描画)", ""),
    ("fill", "塗り", ""),
    ("balloon", "フキダシ", ""),
    ("text", "テキスト", ""),
    ("effect", "効果線", ""),
    ("layer_folder", "フォルダ", ""),
)

_ADD_KIND_ICONS = {
    "page": "FILE_BLANK",
    "coma": "MOD_WIREFRAME",
    "gp": "OUTLINER_OB_GREASEPENCIL",
    "image": "IMAGE_DATA",
    "image_path": "CURVE_BEZCURVE",
    "raster": "BRUSH_DATA",
    "fill": "NODE_TEXTURE",
    "balloon_group": "FILE_FOLDER",
    "balloon": "MOD_FLUID",
    "text": "FONT_DATA",
    "effect": "STROKE",
    "layer_folder": "FILE_FOLDER",
}

def _active_stack_item(context):
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None:
        return None
    idx = int(getattr(context.scene, "bmanga_active_layer_stack_index", -1))
    if 0 <= idx < len(stack):
        return stack[idx]
    return None

def _active_stack_uid(context) -> str:
    item = _active_stack_item(context)
    return layer_stack_utils.stack_item_uid(item) if item is not None else ""

def _selected_stack_uids(context, stack=None) -> set[str]:
    stack = stack if stack is not None else layer_stack_utils.sync_layer_stack(
        context,
        preserve_active_index=True,
    )
    if stack is None:
        return set()
    return {
        layer_stack_utils.stack_item_uid(item)
        for item in stack
        if layer_stack_utils.is_item_selected(context, item)
    }

def _set_selected_stack_uids(context, uids: set[str], stack=None):
    stack = stack if stack is not None else layer_stack_utils.sync_layer_stack(
        context,
        preserve_active_index=True,
    )
    layer_stack_utils.clear_all_selection(context)
    if stack is None:
        return stack
    for item in stack:
        if layer_stack_utils.stack_item_uid(item) in uids:
            layer_stack_utils.set_item_selected(context, item, True)
    return stack

def _page_key_for_item(item, context=None) -> str:
    if item is None:
        return ""
    if item.kind == PAGE_KIND:
        return item.key
    if item.kind == "layer_folder":
        from ..core.work import get_work

        semantic_parent = layer_folder_utils.semantic_parent_key_for_folder(get_work(context or bpy.context), item.key)
        if semantic_parent == OUTSIDE_STACK_KEY:
            return ""
        page_key, _child = split_child_key(semantic_parent)
        return page_key
    if item.kind in {COMA_KIND, "balloon_group", "balloon", "text"}:
        page_key, _child = split_child_key(item.key)
        return page_key
    parent_key = str(getattr(item, "parent_key", "") or "")
    if parent_key and ":" not in parent_key:
        return parent_key
    if parent_key:
        page_key, _child = split_child_key(parent_key)
        return page_key
    return ""

def _placement_anchor_uid(context, kind: str) -> str:
    item = _active_stack_item(context)
    if item is None:
        return ""
    if kind == PAGE_KIND:
        page_key = _page_key_for_item(item, context)
        return layer_stack_utils.target_uid(PAGE_KIND, page_key) if page_key else ""
    if kind == COMA_KIND:
        if item.kind == COMA_KIND:
            return layer_stack_utils.stack_item_uid(item)
        parent_key = str(getattr(item, "parent_key", "") or "")
        if parent_key and ":" in parent_key:
            return layer_stack_utils.target_uid(COMA_KIND, parent_key)
        return ""
    return layer_stack_utils.stack_item_uid(item)

def _find_page(context, page_key: str):
    from ..core.work import get_work

    work = get_work(context)
    if work is None:
        return None, -1
    for i, page in enumerate(work.pages):
        if page_stack_key(page) == page_key:
            return page, i
    return None, -1

def _find_panel(context, coma_key: str):
    page_key, stem = split_child_key(coma_key)
    page, page_idx = _find_page(context, page_key)
    if page is None:
        return None, None, page_idx, -1
    for i, panel in enumerate(page.comas):
        if coma_stack_key(page, panel) == coma_key or getattr(panel, "coma_id", "") == stem:
            return page, panel, page_idx, i
    return page, None, page_idx, -1

def _active_or_anchor_page(context, anchor_uid: str):
    from ..core.work import get_active_page, get_work

    stack = getattr(context.scene, "bmanga_layer_stack", None)
    anchor = None
    if stack is not None and anchor_uid:
        for item in stack:
            if layer_stack_utils.stack_item_uid(item) == anchor_uid:
                anchor = item
                break
    page_key = _page_key_for_item(anchor, context)
    if page_key:
        page, page_idx = _find_page(context, page_key)
        if page is not None:
            work = get_work(context)
            if work is not None and 0 <= page_idx < len(work.pages):
                work.active_page_index = page_idx
            return work, page
    work = get_work(context)
    return work, get_active_page(context)

def _coma_bounds(panel) -> tuple[float, float, float, float] | None:
    from ..utils.layer_hierarchy import coma_polygon

    points = coma_polygon(panel)
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)

def _default_rect_for_parent(context, work, page, parent_key: str, width: float, height: float):
    if parent_key and ":" in parent_key:
        _page, panel, _page_idx, _coma_idx = _find_panel(context, parent_key)
        bounds = _coma_bounds(panel) if panel is not None else None
        if bounds is not None:
            left, bottom, right, top = bounds
            return (
                left + max(0.0, (right - left - width) * 0.5),
                bottom + max(0.0, (top - bottom - height) * 0.5),
            )
    paper = getattr(work, "paper", None)
    canvas_w = float(getattr(paper, "canvas_width_mm", 210.0))
    canvas_h = float(getattr(paper, "canvas_height_mm", 297.0))
    return max(0.0, canvas_w - width - 5.0), max(0.0, canvas_h - height - 5.0)

def _parent_key_for_new_item(context, anchor_uid: str, kind: str) -> str:
    """新規レイヤー追加時の親キーを、レイヤーリスト上で選択中の行から推定する.

    CSP / Photoshop のレイヤーパネルと同じ感覚にする:
    - Page / Coma 選択中: そのページ/コマの中に追加 (返り値 = 行の key)
    - フォルダ選択中: フォルダの中に追加 (返り値 = フォルダの key)
    - 他レイヤー選択中: その兄弟として追加 (返り値 = 行の parent_key)
    """
    stack = getattr(context.scene, "bmanga_layer_stack", None)
    if stack is None or not anchor_uid:
        return ""
    from ..core.work import get_work
    from ..utils import gp_layer_parenting as gp_parent

    work = get_work(context)
    logical_child_kinds = {"gp", "effect", "raster", "image", "image_path", "fill", "balloon", "text"}
    for item in stack:
        if layer_stack_utils.stack_item_uid(item) != anchor_uid:
            continue
        folder_key = _folder_key_for_anchor_item(context, item)
        if kind in {"image", "image_path", "raster", "fill", "balloon", "text"} and folder_key:
            semantic_parent = layer_folder_utils.semantic_parent_key_for_folder(work, folder_key)
            return "" if semantic_parent == OUTSIDE_STACK_KEY else semantic_parent
        # Page / Coma 行を選択中: そのコンテナの中へ
        if kind in logical_child_kinds and item.kind in {PAGE_KIND, COMA_KIND}:
            return item.key
        if kind in logical_child_kinds and gp_parent.parent_key_exists(
            work, str(getattr(item, "parent_key", "") or "")
        ):
            return str(getattr(item, "parent_key", "") or "")
        # 同種レイヤー選択中: 兄弟として
        if kind == "gp" and item.kind == "gp":
            return str(getattr(item, "parent_key", "") or "")
        if kind in {"effect", "raster", "fill", "balloon", "text"} and item.kind in {"effect", "raster", "fill", "balloon", "text"}:
            return str(getattr(item, "parent_key", "") or "")
        if kind == COMA_KIND and item.kind == COMA_KIND:
            return str(getattr(item, "parent_key", "") or "")
        return ""
    return ""

def _folder_key_for_anchor_item(context, item) -> str:
    if item is None:
        return ""
    if item.kind == "layer_folder":
        return str(getattr(item, "key", "") or "")
    parent_key = str(getattr(item, "parent_key", "") or "")
    if parent_key and layer_folder_utils.is_folder_key(context, parent_key):
        return parent_key
    return ""

def _folder_key_for_anchor(context, anchor_uid: str) -> str:
    stack = getattr(context.scene, "bmanga_layer_stack", None)
    if stack is None or not anchor_uid:
        return ""
    for item in stack:
        if layer_stack_utils.stack_item_uid(item) == anchor_uid:
            return _folder_key_for_anchor_item(context, item)
    return ""

def _parent_key_for_new_layer_folder(context, anchor_uid: str) -> str:
    stack = getattr(context.scene, "bmanga_layer_stack", None)
    if stack is None or not anchor_uid:
        return OUTSIDE_STACK_KEY
    for item in stack:
        if layer_stack_utils.stack_item_uid(item) != anchor_uid:
            continue
        if item.kind in {"outside_group", PAGE_KIND, COMA_KIND, "layer_folder"}:
            return str(getattr(item, "key", "") or OUTSIDE_STACK_KEY)
        parent_key = str(getattr(item, "parent_key", "") or "")
        return parent_key or OUTSIDE_STACK_KEY
    return OUTSIDE_STACK_KEY

def _place_new_item(context, new_uid: str, anchor_uid: str) -> bool:
    stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    if stack is None or not new_uid:
        return False
    new_idx = next(
        (i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == new_uid),
        -1,
    )
    if new_idx < 0:
        return False
    anchor_idx = next(
        (i for i, item in enumerate(stack) if layer_stack_utils.stack_item_uid(item) == anchor_uid),
        -1,
    )
    if anchor_idx >= 0 and anchor_idx != new_idx:
        target_idx = anchor_idx if new_idx > anchor_idx else max(0, anchor_idx - 1)
        if target_idx != new_idx:
            stack.move(new_idx, target_idx)
    layer_stack_utils.apply_stack_order(context)
    layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
    for i, item in enumerate(context.scene.bmanga_layer_stack):
        if layer_stack_utils.stack_item_uid(item) == new_uid:
            layer_stack_utils.select_stack_index(context, i)
            layer_stack_utils.remember_layer_stack_signature(context)
            return True
    return False

def _unique_name(existing: set[str], base: str) -> str:
    if base not in existing:
        return base
    i = 1
    while True:
        candidate = f"{base}.{i:03d}"
        if candidate not in existing:
            return candidate
        i += 1

def _unique_shared_id(coll, prefix: str) -> str:
    used = {str(getattr(entry, "id", "") or "") for entry in coll or []}
    i = 1
    while True:
        candidate = f"{prefix}_{i:04d}"
        if candidate not in used:
            return candidate
        i += 1

def _copy_image_entry(src, dst) -> None:
    for attr in (
        "title", "filepath", "x_mm", "y_mm", "width_mm", "height_mm",
        "rotation_deg", "flip_x", "flip_y", "visible", "locked", "opacity",
        "blend_mode", "brightness", "contrast", "binarize_enabled",
        "binarize_threshold", "tint_color", "parent_kind", "parent_key", "folder_key",
    ):
        try:
            setattr(dst, attr, getattr(src, attr))
        except Exception:  # noqa: BLE001
            pass

def _active_row_in_visible_subtree(stack, active_index: int, parent_index: int) -> bool:
    if active_index <= parent_index or not (0 <= active_index < len(stack)):
        return False
    parent_depth = int(getattr(stack[parent_index], "depth", 0))
    for i in range(parent_index + 1, len(stack)):
        depth = int(getattr(stack[i], "depth", 0))
        if depth <= parent_depth:
            return False
        if i == active_index:
            return True
    return False

def _select_stack_uid(context, uid: str) -> bool:
    stack = getattr(context.scene, "bmanga_layer_stack", None)
    if stack is None or not uid:
        return False
    for i, item in enumerate(stack):
        if layer_stack_utils.stack_item_uid(item) == uid:
            return layer_stack_utils.select_stack_index(context, i)
    return False

def _editable_name_prop_for_item(context, item) -> str | None:
    if item is None:
        return None
    kind = str(getattr(item, "kind", "") or "")
    if kind in {"page", "coma", "coma_preview", "outside_group"}:
        return None
    resolved = layer_stack_utils.resolve_stack_item(context, item)
    target = resolved.get("target") if resolved is not None else None
    if target is None:
        return None
    if kind in {"layer_folder", "image", "image_path", "raster", "fill", "balloon", "text"} and hasattr(
        target, "title"
    ):
        return "title"
    if kind in {"gp", "effect"} and hasattr(target, "name"):
        return "name"
    return None

def _remember_inline_rename_click(index: int, uid: str) -> bool:
    now = time.monotonic()
    previous_index = int(_LAST_INLINE_RENAME_CLICK.get("index", -1) or -1)
    previous_uid = str(_LAST_INLINE_RENAME_CLICK.get("uid", "") or "")
    previous_time = float(_LAST_INLINE_RENAME_CLICK.get("time", 0.0) or 0.0)
    _LAST_INLINE_RENAME_CLICK["index"] = int(index)
    _LAST_INLINE_RENAME_CLICK["uid"] = str(uid or "")
    _LAST_INLINE_RENAME_CLICK["time"] = now
    return (
        previous_index == int(index)
        and previous_uid == str(uid or "")
        and 0.0 <= now - previous_time <= _INLINE_RENAME_DOUBLE_CLICK_SEC
    )

def _should_begin_inline_rename(context, item, index: int, event) -> bool:
    if _editable_name_prop_for_item(context, item) is None:
        return False
    if (
        bool(getattr(event, "shift", False))
        or bool(getattr(event, "ctrl", False))
        or bool(getattr(event, "oskey", False))
    ):
        return False
    value = str(getattr(event, "value", "") or "")
    uid = layer_stack_utils.stack_item_uid(item)
    if value == "DOUBLE_CLICK":
        _remember_inline_rename_click(index, uid)
        return True
    if value != "PRESS":
        return False
    return _remember_inline_rename_click(index, uid)

def _begin_inline_rename(context, item, index: int) -> None:
    context.scene.bmanga_layer_stack_inline_edit_uid = layer_stack_utils.stack_item_uid(item)
    layer_stack_utils.clear_all_selection(context)
    layer_stack_utils.set_item_selected(context, item, True)
    layer_stack_utils.select_stack_index(context, index)
    layer_stack_utils.tag_view3d_redraw(context)

class BMANGA_OT_layer_stack_select(Operator):
    bl_idname = "bmanga.layer_stack_select"
    bl_label = "レイヤーを選択"
    bl_options = {"REGISTER"}

    index: IntProperty(default=-1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bmanga_layer_stack", None) is not None

    def execute(self, context):
        if not layer_stack_utils.select_stack_index(context, self.index):
            return {"CANCELLED"}
        return {"FINISHED"}


class BMANGA_OT_layer_stack_multi_select(Operator):
    """レイヤーリストの複数選択。Ctrl=トグル / Shift=範囲 / 通常=単独選択."""

    bl_idname = "bmanga.layer_stack_multi_select"
    bl_label = "レイヤーを複数選択"
    bl_options = {"REGISTER"}

    index: IntProperty(default=-1)  # type: ignore[valid-type]
    anchor_index: IntProperty(default=-1, options={"HIDDEN"})  # type: ignore[valid-type]
    mode: EnumProperty(  # type: ignore[valid-type]
        items=(
            ("SET", "単独", ""),
            ("TOGGLE", "トグル", ""),
            ("RANGE", "範囲", ""),
        ),
        default="SET",
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        stack = getattr(context.scene, "bmanga_layer_stack", None)
        return stack is not None and len(stack) > 0

    def invoke(self, context, event):
        stack = getattr(context.scene, "bmanga_layer_stack", None)
        if stack is not None and 0 <= self.index < len(stack):
            item = stack[self.index]
            if _should_begin_inline_rename(context, item, self.index, event):
                _begin_inline_rename(context, item, self.index)
                return {"FINISHED"}

        if bool(getattr(event, "shift", False)):
            self.mode = "RANGE"
        elif bool(getattr(event, "ctrl", False)) or bool(getattr(event, "oskey", False)):
            self.mode = "TOGGLE"
        else:
            self.mode = "SET"
        return self.execute(context)

    def execute(self, context):
        try:
            from ..core import layer_stack as core_layer_stack

            core_layer_stack.suppress_next_visible_index_select()
        except Exception:  # noqa: BLE001
            pass
        stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        if stack is None or not (0 <= self.index < len(stack)):
            return {"CANCELLED"}
        scene = context.scene
        scene.bmanga_layer_stack_inline_edit_uid = ""
        active_idx = int(getattr(scene, "bmanga_active_layer_stack_index", -1))
        anchor_idx = int(getattr(self, "anchor_index", -1))
        if not (0 <= anchor_idx < len(stack)):
            anchor_idx = active_idx

        if self.mode == "RANGE" and 0 <= anchor_idx < len(stack):
            from ..utils import layer_links

            layer_stack_utils.clear_all_selection(context)
            lo = min(anchor_idx, self.index)
            hi = max(anchor_idx, self.index)
            for i in range(lo, hi + 1):
                layer_stack_utils.set_item_selected(context, stack[i], True)
            layer_links.expand_linked_selection(context, stack=stack)
            layer_stack_utils.set_active_stack_index_silently(context, anchor_idx)
            layer_stack_utils.sync_object_selection_from_stack_selection(context, stack)
            # アクティブ行は変更せず、範囲の終端は選択フラグで表現する
            layer_stack_utils.tag_view3d_redraw(context)
            return {"FINISHED"}

        if self.mode == "TOGGLE":
            from ..utils import layer_links

            target = stack[self.index]
            target_uid = layer_stack_utils.stack_item_uid(target)
            target_uids = (
                set(layer_links.linked_uids_for_uid(context, target_uid))
                if layer_links.is_linkable_item(target)
                else {target_uid}
            )
            selected_uids = _selected_stack_uids(context, stack)
            currently = layer_stack_utils.is_item_selected(context, target)
            if currently:
                # アクティブ行を解除する場合は別の選択行にアクティブを移す
                selected_uids.difference_update(target_uids)
                if self.index == active_idx:
                    new_active = -1
                    for i, it in enumerate(stack):
                        if i == self.index:
                            continue
                        if layer_stack_utils.stack_item_uid(it) in selected_uids:
                            new_active = i
                            break
                    if new_active >= 0:
                        layer_stack_utils.select_stack_index(
                            context,
                            new_active,
                            sync_object_selection=False,
                        )
            else:
                selected_uids.update(target_uids)
                layer_stack_utils.select_stack_index(
                    context,
                    self.index,
                    sync_object_selection=False,
                )
            stack = _set_selected_stack_uids(context, selected_uids)
            layer_stack_utils.sync_object_selection_from_stack_selection(context, stack)
            layer_stack_utils.tag_view3d_redraw(context)
            return {"FINISHED"}

        # SET: 単独選択 — 他の selected をすべてクリアし、この行のみ選択
        from ..utils import layer_links

        target = stack[self.index]
        target_uid = layer_stack_utils.stack_item_uid(target)
        selected_uids = (
            set(layer_links.linked_uids_for_uid(context, target_uid))
            if layer_links.is_linkable_item(target)
            else {target_uid}
        )
        layer_stack_utils.select_stack_index(
            context,
            self.index,
            sync_object_selection=False,
        )
        stack = _set_selected_stack_uids(context, selected_uids)
        layer_stack_utils.sync_object_selection_from_stack_selection(context, stack)
        layer_stack_utils.tag_view3d_redraw(context)
        return {"FINISHED"}


class BMANGA_OT_layer_stack_move(Operator):
    bl_idname = "bmanga.layer_stack_move"
    bl_label = "レイヤー順を変更"
    bl_options = {"REGISTER", "UNDO"}

    direction: EnumProperty(  # type: ignore[valid-type]
        items=(
            ("FRONT", "最前面", ""),
            ("UP", "前面へ", ""),
            ("DOWN", "背面へ", ""),
            ("BACK", "最背面", ""),
        ),
        default="UP",
    )

    @classmethod
    def poll(cls, context):
        stack = getattr(context.scene, "bmanga_layer_stack", None)
        return stack is not None and len(stack) > 0

    def execute(self, context):
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        stack = context.scene.bmanga_layer_stack
        idx = int(getattr(context.scene, "bmanga_active_layer_stack_index", -1))
        if not (0 <= idx < len(stack)):
            return {"CANCELLED"}
        if not layer_stack_utils.move_stack_item(context, idx, direction=self.direction):
            return {"CANCELLED"}
        return {"FINISHED"}


class BMANGA_MT_layer_stack_add(Menu):
    bl_idname = "BMANGA_MT_layer_stack_add"
    bl_label = "レイヤーを追加"

    def draw(self, _context):
        layout = self.layout
        for kind, label, _desc in _ADD_KIND_ITEMS:
            if kind == "raster":
                layout.menu(
                    "BMANGA_MT_layer_stack_add_raster",
                    text=label,
                    icon=_ADD_KIND_ICONS.get(kind, "ADD"),
                )
                continue
            if kind == "fill":
                layout.menu(
                    "BMANGA_MT_layer_stack_add_fill",
                    text=label,
                    icon=_ADD_KIND_ICONS.get(kind, "ADD"),
                )
                continue
            op = layout.operator(
                "bmanga.layer_stack_add",
                text=label,
                icon=_ADD_KIND_ICONS.get(kind, "ADD"),
            )
            op.kind = kind


class BMANGA_MT_layer_stack_add_raster(Menu):
    bl_idname = "BMANGA_MT_layer_stack_add_raster"
    bl_label = "ラスターを追加"

    def draw(self, _context):
        layout = self.layout
        op = layout.operator(
            "bmanga.layer_stack_add",
            text="300dpi / グレー 8bit",
            icon="BRUSH_DATA",
        )
        op.kind = "raster"
        op.dpi = 300
        op.bit_depth = "gray8"
        op = layout.operator(
            "bmanga.layer_stack_add",
            text="150dpi / グレー 8bit",
            icon="BRUSH_DATA",
        )
        op.kind = "raster"
        op.dpi = 150
        op.bit_depth = "gray8"


class BMANGA_MT_layer_stack_add_fill(Menu):
    bl_idname = "BMANGA_MT_layer_stack_add_fill"
    bl_label = "塗りを追加"

    def draw(self, _context):
        layout = self.layout
        op = layout.operator(
            "bmanga.layer_stack_add",
            text="ベタ塗り",
            icon="NODE_TEXTURE",
        )
        op.kind = "fill"
        op.fill_type = "solid"
        op = layout.operator(
            "bmanga.layer_stack_add",
            text="線形グラデーション",
            icon="NODE_TEXTURE",
        )
        op.kind = "fill"
        op.fill_type = "gradient_linear"
        op = layout.operator(
            "bmanga.layer_stack_add",
            text="円形グラデーション",
            icon="NODE_TEXTURE",
        )
        op.kind = "fill"
        op.fill_type = "gradient_radial"


class BMANGA_OT_layer_stack_add(Operator, ImportHelper):
    bl_idname = "bmanga.layer_stack_add"
    bl_label = "レイヤーを追加"
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(items=_ADD_KIND_ITEMS, default="gp")  # type: ignore[valid-type]
    anchor_uid: StringProperty(default="", options={"HIDDEN"})  # type: ignore[valid-type]
    dpi: IntProperty(name="DPI", default=300, min=30, soft_max=1200)  # type: ignore[valid-type]
    bit_depth: EnumProperty(  # type: ignore[valid-type]
        name="階調",
        items=(("gray8", "グレー 8bit", ""), ("gray1", "1bit", "")),
        default="gray8",
    )
    fill_type: StringProperty(default="solid", options={"HIDDEN"})  # type: ignore[valid-type]
    filter_glob: StringProperty(  # type: ignore[valid-type]
        default="*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.psd;*.bmp",
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bmanga_layer_stack", None) is not None

    def invoke(self, context, _event):
        self.anchor_uid = _placement_anchor_uid(context, self.kind)
        if self.kind == "image":
            self.filepath = ""
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}
        return self.execute(context)

    def execute(self, context):
        anchor_uid = self.anchor_uid or _placement_anchor_uid(context, self.kind)
        try:
            new_uid = self._add_by_kind(context, anchor_uid)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"レイヤー追加失敗: {exc}")
            return {"CANCELLED"}
        if not new_uid:
            return {"CANCELLED"}
        _place_new_item(context, new_uid, anchor_uid)
        return {"FINISHED"}

    def _add_by_kind(self, context, anchor_uid: str) -> str:
        if self.kind == "page":
            return self._add_page(context)
        if self.kind == "coma":
            return self._add_panel(context, anchor_uid)
        if self.kind == "gp":
            return self._add_gp_layer(context, anchor_uid)
        if self.kind == "image":
            return self._add_image(context, anchor_uid)
        if self.kind == "image_path":
            return self._add_image_path(context, anchor_uid)
        if self.kind == "raster":
            return self._add_raster(context, anchor_uid)
        if self.kind == "fill":
            return self._add_fill(context, anchor_uid)
        if self.kind == "balloon":
            return self._add_balloon(context, anchor_uid)
        if self.kind == "text":
            return self._add_text(context, anchor_uid)
        if self.kind == "effect":
            return self._add_effect(context, anchor_uid)
        if self.kind == "layer_folder":
            return self._add_layer_folder(context, anchor_uid)
        return ""

    def _add_page(self, context) -> str:
        from ..core.work import get_work
        from ..io import page_io, work_io
        from ..utils import gp_object_layer, page_grid, page_range
        from .coma_op import create_basic_frame_coma

        work = get_work(context)
        if work is None or not work.loaded or not work.work_dir:
            self.report({"ERROR"}, "作品が開かれていません")
            return ""
        work_dir = Path(work.work_dir)
        entry = page_io.register_new_page(work)
        page_io.ensure_page_dir(work_dir, entry.id)
        create_basic_frame_coma(work, entry, work_dir)
        gp_object_layer.ensure_default_page_layer(context.scene, entry.id)
        page_grid.apply_page_collection_transforms(context, work)
        page_io.save_pages_json(work_dir, work)
        page_range.sync_end_number_to_page_count(work)
        work_io.save_work_json(work_dir, work)
        context.scene.bmanga_active_layer_kind = PAGE_KIND
        layer_stack_utils.sync_layer_stack_after_data_change(context, align_page_order=True)
        return layer_stack_utils.target_uid(PAGE_KIND, page_stack_key(entry))

    def _add_panel(self, context, anchor_uid: str) -> str:
        from ..io import page_io
        from .coma_op import create_basic_frame_coma

        work, page = _active_or_anchor_page(context, anchor_uid)
        if work is None or page is None or not work.work_dir:
            self.report({"ERROR"}, "ページが選択されていません")
            return ""
        entry = create_basic_frame_coma(work, page, Path(work.work_dir))
        page_io.save_pages_json(Path(work.work_dir), work)
        context.scene.bmanga_active_layer_kind = COMA_KIND
        layer_stack_utils.sync_layer_stack_after_data_change(context, align_coma_order=True)
        return layer_stack_utils.target_uid(COMA_KIND, coma_stack_key(page, entry))

    def _add_gp_layer(self, context, anchor_uid: str) -> str:
        from ..utils import gp_object_layer
        from ..utils import layer_object_model

        parent_key = _parent_key_for_new_item(context, anchor_uid, "gp")
        folder_key = _folder_key_for_anchor(context, anchor_uid)
        existing = {
            layer_object_model.display_title(obj)
            for obj in layer_object_model.iter_layer_objects("gp")
        }
        title = _unique_name(existing, "レイヤー")
        z_order = max(
            (
                layer_object_model.z_index(obj)
                for obj in layer_object_model.iter_layer_objects("gp")
                if layer_object_model.parent_key(obj) == parent_key
            ),
            default=200,
        ) + 10
        bmanga_id = layer_object_model.make_stable_id("gp")
        obj = gp_object_layer.create_layer_gp_object(
            scene=context.scene,
            bmanga_id=bmanga_id,
            title=title,
            z_index=z_order,
            parent_kind="coma" if ":" in parent_key else ("page" if parent_key else "outside"),
            parent_key=parent_key,
            folder_id=folder_key,
        )
        if obj is None:
            return ""
        layer = layer_object_model.content_layer(obj)
        if layer is not None:
            try:
                from ..utils import gpencil as gp_utils

                obj.data.layers.active = layer
                gp_utils.ensure_active_frame(layer)
                gp_utils.ensure_layer_material(obj, layer, activate=True, assign_existing=True)
            except Exception:  # noqa: BLE001
                pass
        try:
            context.view_layer.objects.active = obj
            obj.select_set(True)
        except Exception:  # noqa: BLE001
            pass
        context.scene.bmanga_active_layer_kind = "gp"
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return layer_stack_utils.target_uid("gp", bmanga_id)

    def _add_layer_folder(self, context, anchor_uid: str) -> str:
        from ..core.work import get_work

        work = get_work(context)
        folders = getattr(work, "layer_folders", None) if work is not None else None
        if folders is None:
            self.report({"ERROR"}, "作品データが未初期化です")
            return ""
        entry = folders.add()
        entry.id = layer_folder_utils.ensure_unique_folder_id(work)
        entry.title = "フォルダ"
        entry.parent_key = _parent_key_for_new_layer_folder(context, anchor_uid)
        entry.expanded = True
        context.scene.bmanga_active_layer_kind = "layer_folder"
        if hasattr(context.scene, "bmanga_active_layer_folder_key"):
            context.scene.bmanga_active_layer_folder_key = entry.id
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return layer_stack_utils.target_uid("layer_folder", entry.id)

    def _add_image(self, context, anchor_uid: str) -> str:
        path = Path(self.filepath)
        if not path.is_file():
            self.report({"ERROR"}, f"ファイルが見つかりません: {path}")
            return ""
        coll = getattr(context.scene, "bmanga_image_layers", None)
        if coll is None:
            self.report({"ERROR"}, "画像レイヤーが未初期化です")
            return ""
        used = {entry.id for entry in coll}
        i = 1
        while f"image_{i:04d}" in used:
            i += 1
        entry = coll.add()
        entry.id = f"image_{i:04d}"
        entry.title = path.stem
        entry.filepath = str(path)
        parent_key = _parent_key_for_new_item(context, anchor_uid, "image")
        if parent_key:
            entry.parent_kind = "coma" if ":" in parent_key else "page"
            entry.parent_key = parent_key
        folder_key = _folder_key_for_anchor(context, anchor_uid)
        if folder_key:
            entry.folder_key = folder_key
        try:
            img = bpy.data.images.load(str(path), check_existing=True)
            entry.width_mm = max(1.0, img.size[0] / 6.0)
            entry.height_mm = max(1.0, img.size[1] / 6.0)
        except Exception:  # noqa: BLE001
            pass
        context.scene.bmanga_active_image_layer_index = len(coll) - 1
        context.scene.bmanga_active_layer_kind = "image"
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return layer_stack_utils.target_uid("image", entry.id)

    def _add_image_path(self, context, anchor_uid: str) -> str:
        from ..core.work import get_work

        coll = getattr(context.scene, "bmanga_image_path_layers", None)
        if coll is None:
            self.report({"ERROR"}, "パターンカーブが未初期化です")
            return ""
        used = {entry.id for entry in coll}
        i = 1
        while f"image_path_{i:04d}" in used:
            i += 1
        work = get_work(context)
        paper = getattr(work, "paper", None) if work is not None else None
        cx = float(getattr(paper, "canvas_width_mm", 182.0) or 182.0) * 0.5
        cy = float(getattr(paper, "canvas_height_mm", 257.0) or 257.0) * 0.5
        entry = coll.add()
        entry.id = f"image_path_{i:04d}"
        entry.title = f"パターンカーブ {i}"
        entry.path_points_json = json.dumps([(cx - 30.0, cy), (cx + 30.0, cy)])
        parent_key = _parent_key_for_new_item(context, anchor_uid, "image_path")
        if parent_key:
            entry.parent_kind = "coma" if ":" in parent_key else "page"
            entry.parent_key = parent_key
        elif work is not None and getattr(work, "pages", None):
            page_index = int(getattr(work, "active_page_index", 0) or 0)
            if 0 <= page_index < len(work.pages):
                entry.parent_kind = "page"
                entry.parent_key = str(getattr(work.pages[page_index], "id", "") or "")
        folder_key = _folder_key_for_anchor(context, anchor_uid)
        if folder_key:
            entry.folder_key = folder_key
        try:
            from . import preset_op

            preset_op.apply_image_path_preset_to_entry(context, entry)
        except Exception:  # noqa: BLE001
            pass
        context.scene.bmanga_active_image_path_layer_index = len(coll) - 1
        context.scene.bmanga_active_layer_kind = "image_path"
        try:
            from ..utils import image_path_object

            page = image_path_object.page_for_entry(context.scene, work, entry) if work is not None else None
            image_path_object.ensure_image_path_object(scene=context.scene, entry=entry, page=page)
        except Exception:  # noqa: BLE001
            pass
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return layer_stack_utils.target_uid("image_path", entry.id)

    def _add_raster(self, context, anchor_uid: str) -> str:
        before = {
            getattr(entry, "id", "")
            for entry in (getattr(context.scene, "bmanga_raster_layers", None) or [])
        }
        result = bpy.ops.bmanga.raster_layer_add(
            "EXEC_DEFAULT",
            dpi_preset="custom",
            dpi=int(getattr(self, "dpi", 300)),
            bit_depth=str(getattr(self, "bit_depth", "gray8") or "gray8"),
        )
        if "FINISHED" not in result:
            return ""
        coll = getattr(context.scene, "bmanga_raster_layers", None)
        if coll is None:
            return ""
        for entry in coll:
            if getattr(entry, "id", "") not in before:
                parent_key = _parent_key_for_new_item(context, anchor_uid, "raster")
                folder_key = _folder_key_for_anchor(context, anchor_uid)
                if parent_key:
                    entry.scope = "page"
                    entry.parent_kind = "coma" if ":" in parent_key else "page"
                    entry.parent_key = parent_key
                elif folder_key and layer_folder_utils.semantic_parent_key_for_folder(
                    getattr(context.scene, "bmanga_work", None),
                    folder_key,
                ) == OUTSIDE_STACK_KEY:
                    entry.scope = "master"
                    entry.parent_kind = "none"
                    entry.parent_key = ""
                if folder_key:
                    entry.folder_key = folder_key
                return layer_stack_utils.target_uid("raster", entry.id)
        idx = int(getattr(context.scene, "bmanga_active_raster_layer_index", -1))
        if 0 <= idx < len(coll):
            return layer_stack_utils.target_uid("raster", coll[idx].id)
        return ""

    def _add_fill(self, context, anchor_uid: str) -> str:
        coll = getattr(context.scene, "bmanga_fill_layers", None)
        if coll is None:
            self.report({"ERROR"}, "塗りレイヤーが未初期化です")
            return ""
        used = {entry.id for entry in coll}
        i = 1
        while f"fill_{i:04d}" in used:
            i += 1
        entry = coll.add()
        entry.id = f"fill_{i:04d}"
        ft = str(getattr(self, "fill_type", "solid") or "solid")
        if ft == "gradient_linear":
            entry.fill_type = "gradient"
            entry.gradient_type = "linear"
            entry.title = f"グラデーション {i}"
        elif ft == "gradient_radial":
            entry.fill_type = "gradient"
            entry.gradient_type = "radial"
            entry.title = f"円形グラデーション {i}"
        else:
            entry.fill_type = "solid"
            entry.title = f"ベタ塗り {i}"
        parent_key = _parent_key_for_new_item(context, anchor_uid, "fill")
        if parent_key:
            entry.parent_kind = "coma" if ":" in parent_key else "page"
            entry.parent_key = parent_key
        folder_key = _folder_key_for_anchor(context, anchor_uid)
        if folder_key:
            entry.folder_key = folder_key
        context.scene.bmanga_active_fill_layer_index = len(coll) - 1
        context.scene.bmanga_active_layer_kind = "fill"
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return layer_stack_utils.target_uid("fill", entry.id)

    def _add_balloon(self, context, anchor_uid: str) -> str:
        from .balloon_op import _allocate_balloon_id, _creation_violates_layer_scope

        work, page = _active_or_anchor_page(context, anchor_uid)
        folder_key = _folder_key_for_anchor(context, anchor_uid)
        folder_parent = layer_folder_utils.semantic_parent_key_for_folder(work, folder_key) if folder_key else ""
        if work is not None and folder_key and folder_parent == OUTSIDE_STACK_KEY:
            entry = work.shared_balloons.add()
            entry.id = _unique_shared_id(work.shared_balloons, "shared_balloon")
            entry.shape = "rect"
            entry.x_mm = 10.0
            entry.y_mm = 10.0
            entry.width_mm = 40.0
            entry.height_mm = 20.0
            entry.rounded_corner_enabled = True
            entry.corner_type = "rounded"
            entry.corner_type_initialized = True
            entry.parent_kind = "none"
            entry.parent_key = ""
            entry.folder_key = folder_key
            context.scene.bmanga_active_layer_kind = "balloon"
            layer_stack_utils.sync_layer_stack_after_data_change(context)
            return layer_stack_utils.target_uid("balloon", outside_child_key(entry.id))
        if work is None or page is None:
            self.report({"ERROR"}, "ページが選択されていません")
            return ""
        width, height = 40.0, 20.0
        parent_key = _parent_key_for_new_item(context, anchor_uid, "balloon")
        x_mm, y_mm = _default_rect_for_parent(context, work, page, parent_key, width, height)
        if _creation_violates_layer_scope(context, page, x_mm, y_mm, width, height):
            self.report({"ERROR"}, "このモードではその位置にフキダシを作成できません")
            return ""
        entry = page.balloons.add()
        entry.id = _allocate_balloon_id(page)
        entry.shape = "rect"
        entry.x_mm = x_mm
        entry.y_mm = y_mm
        entry.width_mm = width
        entry.height_mm = height
        entry.rounded_corner_enabled = True
        entry.corner_type = "rounded"
        entry.corner_type_initialized = True
        entry.parent_kind = "coma" if ":" in parent_key else "page"
        entry.parent_key = parent_key or page_stack_key(page)
        if folder_key:
            entry.folder_key = folder_key
        page.active_balloon_index = len(page.balloons) - 1
        context.scene.bmanga_active_layer_kind = "balloon"
        layer_stack_utils.sync_layer_stack_after_data_change(context)
        return layer_stack_utils.target_uid("balloon", f"{page_stack_key(page)}:{entry.id}")

    def _add_text(self, context, anchor_uid: str) -> str:
        from .text_op import _create_text_entry, _creation_blocked

        work, page = _active_or_anchor_page(context, anchor_uid)
        folder_key = _folder_key_for_anchor(context, anchor_uid)
        folder_parent = layer_folder_utils.semantic_parent_key_for_folder(work, folder_key) if folder_key else ""
        if work is not None and folder_key and folder_parent == OUTSIDE_STACK_KEY:
            entry = work.shared_texts.add()
            entry.id = _unique_shared_id(work.shared_texts, "shared_text")
            entry.body = "テキスト"
            entry.x_mm = 10.0
            entry.y_mm = 10.0
            entry.width_mm = 30.0
            entry.height_mm = 15.0
            entry.parent_kind = "none"
            entry.parent_key = ""
            entry.folder_key = folder_key
            context.scene.bmanga_active_layer_kind = "text"
            layer_stack_utils.sync_layer_stack_after_data_change(context)
            return layer_stack_utils.target_uid("text", outside_child_key(entry.id))
        if work is None or page is None:
            self.report({"ERROR"}, "ページが選択されていません")
            return ""
        width, height = 30.0, 15.0
        parent_key = _parent_key_for_new_item(context, anchor_uid, "text")
        x_mm, y_mm = _default_rect_for_parent(context, work, page, parent_key, width, height)
        if _creation_blocked(context, page, x_mm, y_mm, width, height):
            self.report({"ERROR"}, "このモードではその位置にテキストを作成できません")
            return ""
        entry, _missing = _create_text_entry(
            context,
            page,
            body="テキスト",
            x_mm=x_mm,
            y_mm=y_mm,
            width_mm=width,
            height_mm=height,
        )
        if parent_key:
            entry.parent_kind = "coma" if ":" in parent_key else "page"
            entry.parent_key = parent_key
        if folder_key:
            entry.folder_key = folder_key
        return layer_stack_utils.target_uid("text", f"{page_stack_key(page)}:{entry.id}")

    def _add_effect(self, context, anchor_uid: str) -> str:
        from .effect_line_op import _create_effect_layer
        from ..utils import layer_object_model

        parent_key = _parent_key_for_new_item(context, anchor_uid, "effect")
        obj, _layer = _create_effect_layer(context, parent_key=parent_key)
        stable_id = layer_object_model.stable_id(obj)
        return layer_stack_utils.target_uid("effect", stable_id) if stable_id else ""


class BMANGA_OT_layer_stack_duplicate(Operator):
    bl_idname = "bmanga.layer_stack_duplicate"
    bl_label = "レイヤーを複製"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        stack = getattr(context.scene, "bmanga_layer_stack", None)
        idx = int(getattr(context.scene, "bmanga_active_layer_stack_index", -1))
        return stack is not None and 0 <= idx < len(stack)

    def execute(self, context):
        stack = layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        idx = int(getattr(context.scene, "bmanga_active_layer_stack_index", -1))
        if stack is None or not (0 <= idx < len(stack)):
            return {"CANCELLED"}
        anchor_uid = layer_stack_utils.stack_item_uid(stack[idx])
        before = {layer_stack_utils.stack_item_uid(item) for item in stack}
        if not self._duplicate_item(context, stack[idx]):
            return {"CANCELLED"}
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        new_uid = self._new_uid_after_duplicate(context, before)
        if new_uid:
            _place_new_item(context, new_uid, anchor_uid)
        return {"FINISHED"}

    def _new_uid_after_duplicate(self, context, before: set[str]) -> str:
        stack = getattr(context.scene, "bmanga_layer_stack", None)
        if stack is None:
            return ""
        for item in stack:
            uid = layer_stack_utils.stack_item_uid(item)
            if uid not in before:
                return uid
        return _active_stack_uid(context)

    def _duplicate_item(self, context, item) -> bool:
        if item.kind in {PAGE_KIND, COMA_KIND}:
            if not layer_stack_utils.select_stack_index(
                context,
                int(getattr(context.scene, "bmanga_active_layer_stack_index", -1)),
            ):
                return False
            op_name = "page_duplicate" if item.kind == PAGE_KIND else "coma_duplicate"
            return "FINISHED" in getattr(bpy.ops.bmanga, op_name)("EXEC_DEFAULT")
        if item.kind in {"gp", "effect"}:
            return self._duplicate_gp_layer(context, item)
        if item.kind == "layer_folder":
            return self._duplicate_layer_folder(context, item)
        if item.kind == "image":
            return self._duplicate_image(context, item)
        if item.kind == "raster":
            from .layer_clipboard_op import duplicate_raster_item

            return duplicate_raster_item(context, item)
        if item.kind == "balloon":
            return self._duplicate_balloon(context, item)
        if item.kind == "text":
            return self._duplicate_text(context, item)
        return False

    def _duplicate_gp_layer(self, context, item) -> bool:
        if not layer_stack_utils.select_stack_index(
            context,
            int(getattr(context.scene, "bmanga_active_layer_stack_index", -1)),
        ):
            return False
        try:
            parent_key = str(getattr(item, "parent_key", "") or "")
            source_obj = None
            source_layer = None
            if item.kind == "effect":
                resolved = layer_stack_utils.resolve_stack_item(context, item)
                source_obj = resolved.get("object") if resolved is not None else None
                source_layer = resolved.get("target") if resolved is not None else None
                from . import effect_line_link_op

                _dest_obj, dest_layer = effect_line_link_op.duplicate_effect_entry(
                    context,
                    source_obj,
                    source_layer,
                    linked=False,
                    ui_parent_key=parent_key,
                )
                return dest_layer is not None
            resolved = layer_stack_utils.resolve_stack_item(context, item)
            source_obj = resolved.get("object") if resolved is not None else None
            if source_obj is None:
                return False
            from ..utils import layer_object_model

            title = _unique_name(
                {
                    layer_object_model.display_title(obj)
                    for obj in layer_object_model.iter_layer_objects("gp")
                },
                f"{layer_object_model.display_title(source_obj)} 複製",
            )
            duplicate = layer_object_model.duplicate_gp_object(
                source_obj,
                bmanga_id=layer_object_model.make_stable_id("gp"),
                title=title,
                z_order=layer_object_model.z_index(source_obj) + 1,
            )
            if duplicate is None:
                return False
            try:
                context.view_layer.objects.active = duplicate
                duplicate.select_set(True)
            except Exception:  # noqa: BLE001
                pass
            return True
        except Exception:  # noqa: BLE001
            return False

    def _duplicate_layer_folder(self, context, item) -> bool:
        from ..core.work import get_work

        work = get_work(context)
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        src = resolved.get("target") if resolved is not None else None
        folders = getattr(work, "layer_folders", None) if work is not None else None
        if src is None or folders is None:
            return False
        dst = folders.add()
        dst.id = layer_folder_utils.ensure_unique_folder_id(work)
        dst.title = _unique_name(
            {str(getattr(folder, "title", "") or "") for folder in folders if folder is not dst},
            f"{getattr(src, 'title', '') or 'フォルダ'} 複製",
        )
        dst.parent_key = str(getattr(src, "parent_key", "") or OUTSIDE_STACK_KEY)
        dst.expanded = bool(getattr(src, "expanded", True))
        dst.visible = bool(getattr(src, "visible", True))
        dst.locked = bool(getattr(src, "locked", False))
        context.scene.bmanga_active_layer_kind = "layer_folder"
        if hasattr(context.scene, "bmanga_active_layer_folder_key"):
            context.scene.bmanga_active_layer_folder_key = dst.id
        return True

    def _duplicate_image(self, context, item) -> bool:
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        src = resolved.get("target") if resolved is not None else None
        coll = getattr(context.scene, "bmanga_image_layers", None)
        if src is None or coll is None:
            return False
        used = {entry.id for entry in coll}
        i = 1
        while f"image_{i:04d}" in used:
            i += 1
        dst = coll.add()
        dst.id = f"image_{i:04d}"
        _copy_image_entry(src, dst)
        dst.title = _unique_name({entry.title for entry in coll if entry is not dst}, f"{src.title} 複製")
        context.scene.bmanga_active_image_layer_index = len(coll) - 1
        context.scene.bmanga_active_layer_kind = "image"
        return True

    def _duplicate_balloon(self, context, item) -> bool:
        from ..io import schema
        from .balloon_op import _allocate_balloon_id

        resolved = layer_stack_utils.resolve_stack_item(context, item)
        src = resolved.get("target") if resolved is not None else None
        page = resolved.get("page") if resolved is not None else None
        if src is None or page is None:
            return False
        dst = page.balloons.add()
        schema.balloon_entry_from_dict(dst, schema.balloon_entry_to_dict(src))
        dst.id = _allocate_balloon_id(page)
        page.active_balloon_index = len(page.balloons) - 1
        context.scene.bmanga_active_layer_kind = "balloon"
        return True

    def _duplicate_text(self, context, item) -> bool:
        from ..io import schema
        from .text_op import _allocate_text_id

        resolved = layer_stack_utils.resolve_stack_item(context, item)
        src = resolved.get("target") if resolved is not None else None
        page = resolved.get("page") if resolved is not None else None
        if src is None or page is None:
            return False
        dst = page.texts.add()
        schema.text_entry_from_dict(dst, schema.text_entry_to_dict(src))
        dst.id = _allocate_text_id(page)
        dst.x_mm += 5.0
        dst.y_mm -= 5.0
        page.active_text_index = len(page.texts) - 1
        context.scene.bmanga_active_layer_kind = "text"
        return True


class BMANGA_OT_layer_stack_toggle_visibility(Operator):
    bl_idname = "bmanga.layer_stack_toggle_visibility"
    bl_label = "レイヤー表示を切替"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bmanga_layer_stack", None) is not None

    def execute(self, context):
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        stack = getattr(context.scene, "bmanga_layer_stack", None)
        if stack is None or not (0 <= self.index < len(stack)):
            return {"CANCELLED"}
        item = stack[self.index]
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        target = resolved.get("target") if resolved is not None else None
        if target is None:
            return {"CANCELLED"}
        if item.kind == "balloon_group":
            page = resolved.get("page") if resolved is not None else None
            group_id = str(resolved.get("group_id", "") or "") if resolved is not None else ""
            members = [
                entry
                for entry in getattr(page, "balloons", []) or []
                if str(getattr(entry, "merge_group_id", "") or "") == group_id
            ]
            if not members:
                return {"CANCELLED"}
            new_visible = not any(bool(getattr(entry, "visible", True)) for entry in members)
            try:
                from ..core.work import get_work
                from ..utils import balloon_curve_object, balloon_merge_object

                with balloon_curve_object.suspend_auto_sync():
                    for entry in members:
                        entry.visible = new_visible
                for entry in members:
                    balloon_curve_object.on_balloon_entry_changed(entry)
                balloon_merge_object.sync_group_for_entry(context.scene, get_work(context), page, members[0])
            except Exception:  # noqa: BLE001
                for entry in members:
                    entry.visible = new_visible
        elif item.kind == layer_stack_utils.COMA_PREVIEW_KIND and hasattr(target, "paper_visible"):
            target.paper_visible = not bool(target.paper_visible)
            obj = resolved.get("object") if resolved is not None else None
            if obj is not None:
                hidden = not bool(target.paper_visible)
                try:
                    obj.hide_viewport = hidden
                    obj.hide_render = hidden
                except Exception:  # noqa: BLE001
                    pass
        elif item.kind in {PAGE_KIND, COMA_KIND} and hasattr(target, "visible"):
            target.visible = not bool(target.visible)
        elif item.kind in {"layer_folder", "image", "image_path", "raster", "fill"} and hasattr(target, "visible"):
            target.visible = not bool(target.visible)
        elif item.kind in {"balloon", "text"} and hasattr(target, "visible"):
            target.visible = not bool(target.visible)
        elif item.kind in {"gp", "effect"}:
            from ..utils import layer_object_model

            obj = resolved.get("object") if resolved is not None else None
            if not layer_object_model.set_user_visible(
                obj,
                not layer_object_model.user_visible(obj),
            ):
                return {"CANCELLED"}
        else:
            return {"CANCELLED"}
        layer_stack_utils.tag_view3d_redraw(context)
        return {"FINISHED"}


class BMANGA_OT_layer_stack_toggle_expanded(Operator):
    bl_idname = "bmanga.layer_stack_toggle_expanded"
    bl_label = "レイヤー階層を開閉"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bmanga_layer_stack", None) is not None

    def execute(self, context):
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        stack = getattr(context.scene, "bmanga_layer_stack", None)
        if stack is None or not (0 <= self.index < len(stack)):
            return {"CANCELLED"}
        item = stack[self.index]
        resolved = layer_stack_utils.resolve_stack_item(context, item)
        target = resolved.get("target") if resolved is not None else None
        if target is None:
            return {"CANCELLED"}
        parent_uid = layer_stack_utils.stack_item_uid(item)
        active_will_be_hidden = _active_row_in_visible_subtree(
            stack,
            int(getattr(context.scene, "bmanga_active_layer_stack_index", -1)),
            self.index,
        )
        if item.kind == PAGE_KIND and hasattr(target, "stack_expanded"):
            was_expanded = bool(target.stack_expanded)
            target.stack_expanded = not was_expanded
            active_will_be_hidden = active_will_be_hidden and was_expanded
        elif item.kind == "balloon_group":
            from ..utils import layer_stack_visible

            was_expanded = not layer_stack_visible.is_balloon_group_collapsed(context, item.key)
            layer_stack_visible.set_balloon_group_collapsed(context, item.key, was_expanded)
            active_will_be_hidden = active_will_be_hidden and was_expanded
        elif item.kind == "layer_folder" and hasattr(target, "expanded"):
            was_expanded = bool(target.expanded)
            target.expanded = not was_expanded
            active_will_be_hidden = active_will_be_hidden and was_expanded
        else:
            return {"CANCELLED"}
        layer_stack_utils.sync_layer_stack(context, preserve_active_index=True)
        if active_will_be_hidden:
            _select_stack_uid(context, parent_uid)
        layer_stack_utils.remember_layer_stack_signature(context)
        layer_stack_utils.tag_view3d_redraw(context)
        return {"FINISHED"}


class BMANGA_OT_layer_stack_delete(Operator):
    bl_idname = "bmanga.layer_stack_delete"
    bl_label = "レイヤーを削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        stack = getattr(context.scene, "bmanga_layer_stack", None)
        idx = int(getattr(context.scene, "bmanga_active_layer_stack_index", -1))
        return stack is not None and 0 <= idx < len(stack)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        idx = int(getattr(context.scene, "bmanga_active_layer_stack_index", -1))
        if not layer_stack_utils.delete_stack_index(context, idx):
            return {"CANCELLED"}
        return {"FINISHED"}


class BMANGA_OT_layer_stack_enter_coma(Operator):
    bl_idname = "bmanga.layer_stack_enter_coma"
    bl_label = "コマ編集へ"
    bl_options = {"REGISTER"}

    stack_index: IntProperty(default=-1)  # type: ignore[valid-type]

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "bmanga_layer_stack", None) is not None

    def execute(self, context):
        if not layer_stack_utils.select_stack_index(context, self.stack_index):
            return {"CANCELLED"}
        item = layer_stack_utils.active_stack_item(context)
        if item is None or item.kind != "coma":
            return {"CANCELLED"}
        return bpy.ops.bmanga.enter_coma_mode("EXEC_DEFAULT")


_CLASSES = (
    BMANGA_OT_layer_stack_select,
    BMANGA_OT_layer_stack_multi_select,
    BMANGA_OT_layer_stack_move,
    BMANGA_MT_layer_stack_add,
    BMANGA_MT_layer_stack_add_raster,
    BMANGA_MT_layer_stack_add_fill,
    BMANGA_OT_layer_stack_add,
    BMANGA_OT_layer_stack_duplicate,
    BMANGA_OT_layer_stack_toggle_visibility,
    BMANGA_OT_layer_stack_toggle_expanded,
    BMANGA_OT_layer_stack_delete,
    BMANGA_OT_layer_stack_enter_coma,
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
